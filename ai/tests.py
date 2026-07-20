from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from exams.models import Disciplina, Prova
from prompts.models import Prompt
from questions.models import Questao, Topico

from .models import ResultadoPrompt, TextoTopico
from .services import (
    IAError,
    _params_mensagem,
    _params_sintese,
    classificar_topicos_via_ia,
    estimar_custo_topicos,
    estimar_tokens,
    estimar_tokens_topicos,
    formatar_custo_usd,
    montar_conteudo_topico,
)
from .tasks import processar_lote

User = get_user_model()


def _resposta_fake(texto='**Análise.**', input_tokens=200, output_tokens=100):
    return SimpleNamespace(
        content=[SimpleNamespace(type='text', text=texto)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class BaseIATestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x')
        self.prova = Prova.objects.create(user=self.user, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=self.prova, nome='Direito')
        self.prompt = Prompt.objects.create(user=self.user, nome='Explicar', texto='Explique a questão.')
        self.questao = Questao.objects.create(
            disciplina=self.disc, numero=1, enunciado_md='Enunciado da questão', gabarito='A',
        )
        self.client.force_login(self.user)


class AplicarPromptTests(BaseIATestCase):
    @patch('ai.services.get_client')
    def test_aplicar_conclui_resultado_e_debita_quota(self, get_client):
        get_client.return_value.messages.create.return_value = _resposta_fake()

        resp = self.client.post(
            reverse('ai:aplicar', args=[self.questao.pk]),
            {'prompt_id': self.prompt.pk},
        )
        self.assertEqual(resp.status_code, 302)

        resultado = ResultadoPrompt.objects.get(questao=self.questao)
        self.assertEqual(resultado.status, ResultadoPrompt.Status.CONCLUIDO)
        self.assertEqual(resultado.resultado_md, '**Análise.**')
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.status, Questao.Status.CONCLUIDA)

        profile = self.user.profile
        profile.refresh_from_db()
        self.assertEqual(profile.tokens_usados_mes, 300)

    def test_aplicar_bloqueia_sem_quota_para_custo_estimado(self):
        profile = self.user.profile
        profile.quota_tokens_mes = 10  # abaixo do custo estimado de 1 questão
        profile.save()

        resp = self.client.post(
            reverse('ai:aplicar', args=[self.questao.pk]),
            {'prompt_id': self.prompt.pk},
            follow=True,
        )
        self.assertFalse(ResultadoPrompt.objects.exists())
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Quota de IA insuficiente' in m for m in mensagens))

    @patch('ai.services.get_client')
    def test_lote_sequencial_marca_erro_sem_travar_questao(self, get_client):
        get_client.return_value.messages.create.side_effect = RuntimeError('api indisponível')
        resultado = ResultadoPrompt.objects.create(questao=self.questao, prompt=self.prompt)
        self.questao.status = Questao.Status.NA_FILA
        self.questao.save(update_fields=['status'])

        processar_lote(resultado_ids=[resultado.pk], usar_lote=False)

        resultado.refresh_from_db()
        self.questao.refresh_from_db()
        self.assertEqual(resultado.status, ResultadoPrompt.Status.ERRO)
        self.assertIn('api indisponível', resultado.erro)
        self.assertEqual(self.questao.status, Questao.Status.ERRO)

    @patch('ai.services.get_client')
    def test_aplicar_funciona_com_prompt_padrao(self, get_client):
        get_client.return_value.messages.create.return_value = _resposta_fake()
        padrao = Prompt.objects.get(user__isnull=True)
        resp = self.client.post(
            reverse('ai:aplicar', args=[self.questao.pk]),
            {'prompt_id': padrao.pk},
        )
        self.assertEqual(resp.status_code, 302)
        resultado = ResultadoPrompt.objects.get(questao=self.questao, prompt=padrao)
        self.assertEqual(resultado.status, ResultadoPrompt.Status.CONCLUIDO)

    def test_aplicar_nao_acessa_questao_de_outro_usuario(self):
        outro = User.objects.create_user('bia', password='x')
        self.client.force_login(outro)
        prompt_outro = Prompt.objects.create(user=outro, nome='P', texto='x')
        resp = self.client.post(
            reverse('ai:aplicar', args=[self.questao.pk]),
            {'prompt_id': prompt_outro.pk},
        )
        self.assertEqual(resp.status_code, 404)


class ServicosIATests(BaseIATestCase):
    def test_estimar_tokens_cresce_com_questoes(self):
        um = estimar_tokens([self.questao], self.prompt)
        dois = estimar_tokens([self.questao, self.questao], self.prompt)
        self.assertGreater(um, 0)
        self.assertEqual(dois, um * 2)

    def test_params_com_cache_move_prompt_para_system(self):
        params = _params_mensagem(self.questao, self.prompt, cache_prompt=True)
        system = params['system']
        self.assertIsInstance(system, list)
        self.assertEqual(system[-1]['cache_control'], {'type': 'ephemeral'})
        self.assertIn(self.prompt.texto, system[-1]['text'])
        texto_user = params['messages'][0]['content'][-1]['text']
        self.assertNotIn(self.prompt.texto, texto_user)

    def test_params_sem_cache_mantem_prompt_na_mensagem(self):
        params = _params_mensagem(self.questao, self.prompt, cache_prompt=False)
        texto_user = params['messages'][0]['content'][-1]['text']
        self.assertIn(self.prompt.texto, texto_user)


class ParametrosModeloTests(BaseIATestCase):
    def test_haiku_nao_recebe_thinking_nem_effort(self):
        from django.test import override_settings
        from ai.services import _params_aplicacao
        with override_settings(AI_MODEL='claude-haiku-4-5'):
            params = _params_aplicacao(self.questao, self.prompt)
        self.assertNotIn('thinking', params)
        self.assertNotIn('output_config', params)

    def test_sonnet46_recebe_thinking_adaptativo(self):
        from django.test import override_settings
        from ai.services import _params_aplicacao
        with override_settings(AI_MODEL='claude-sonnet-4-6'):
            params = _params_aplicacao(self.questao, self.prompt)
        self.assertEqual(params['thinking'], {'type': 'adaptive'})
        self.assertIn('effort', params['output_config'])



class AnaliseUnicaTests(BaseIATestCase):
    @patch('ai.services.get_client')
    def test_gerar_analise_salva_resultado_unico(self, get_client):
        get_client.return_value.messages.create.return_value = _resposta_fake(
            texto='A regra central é X (art. 1º).\n\nA alternativa (B) erra porque...',
            input_tokens=800, output_tokens=400,
        )
        resp = self.client.post(reverse('ai:gerar_comentarios'), {'questao_ids': [self.questao.pk]})
        self.assertEqual(resp.status_code, 302)
        resultado = ResultadoPrompt.objects.get(questao=self.questao, prompt__user__isnull=True)
        self.assertEqual(resultado.status, ResultadoPrompt.Status.CONCLUIDO)
        self.assertIn('regra central', resultado.resultado_md)
        self.assertEqual(get_client.return_value.messages.create.call_count, 1)
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.status, Questao.Status.CONCLUIDA)

    @patch('ai.services.get_client')
    def test_gerar_analise_pula_ja_prontas(self, get_client):
        get_client.return_value.messages.create.return_value = _resposta_fake(texto='Análise.')
        self.client.post(reverse('ai:gerar_comentarios'), {'questao_ids': [self.questao.pk]})
        antes = ResultadoPrompt.objects.count()
        resp = self.client.post(reverse('ai:gerar_comentarios'), {'questao_ids': [self.questao.pk]}, follow=True)
        self.assertEqual(ResultadoPrompt.objects.count(), antes)
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('já têm análise' in m for m in mensagens))

    def test_mensagens_sem_imagens(self):
        from ai.services import montar_mensagens
        msgs = montar_mensagens(self.questao, 'instrução')
        self.assertEqual([b['type'] for b in msgs[0]['content']], ['text'])

    @patch('ai.tasks.processar_lote.delay')
    def test_todas_paginas_abrange_disciplina_inteira_sem_questao_ids(self, processar_lote_delay):
        outra = Questao.objects.create(
            disciplina=self.disc, numero=2, enunciado_md='Outra questão', gabarito='B',
        )
        resp = self.client.post(reverse('ai:gerar_comentarios'), {
            'usar_lote': '1', 'todas_paginas': '1', 'disciplina_id': self.disc.pk,
        })
        self.assertEqual(resp.status_code, 302)
        padrao = Prompt.objects.filter(user__isnull=True).first()
        self.assertEqual(
            ResultadoPrompt.objects.filter(prompt=padrao, questao__in=[self.questao, outra]).count(), 2,
        )
        processar_lote_delay.assert_called_once()

    @patch('ai.tasks.processar_lote.delay')
    def test_todas_paginas_pula_questoes_de_outra_disciplina(self, processar_lote_delay):
        outra_disc = Disciplina.objects.create(prova=self.prova, nome='Português')
        Questao.objects.create(disciplina=outra_disc, numero=1, enunciado_md='Outra disciplina')

        self.client.post(reverse('ai:gerar_comentarios'), {
            'usar_lote': '1', 'todas_paginas': '1', 'disciplina_id': self.disc.pk,
        })
        padrao = Prompt.objects.filter(user__isnull=True).first()
        self.assertEqual(ResultadoPrompt.objects.filter(prompt=padrao).count(), 1)
        self.assertEqual(ResultadoPrompt.objects.get(prompt=padrao).questao, self.questao)

    def test_todas_paginas_sem_disciplina_id_exige_selecao(self):
        resp = self.client.post(
            reverse('ai:gerar_comentarios'), {'usar_lote': '1', 'todas_paginas': '1'}, follow=True,
        )
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Selecione ao menos uma questão' in m for m in mensagens))


class TopicosTests(BaseIATestCase):
    def setUp(self):
        super().setUp()
        self.q2 = Questao.objects.create(
            disciplina=self.disc, numero=2, enunciado_md='Outro enunciado', gabarito='B',
        )
        # Os tópicos são gerados só com questões que têm análise concluída.
        for q in (self.questao, self.q2):
            ResultadoPrompt.objects.create(
                questao=q, prompt=self.prompt,
                status=ResultadoPrompt.Status.CONCLUIDO,
                resultado_md=f'Análise da questão {q.numero}.',
            )

    def _classificacao_fake(self, topicos):
        import json
        return _resposta_fake(texto=json.dumps({'topicos': topicos}))

    @patch('ai.services.get_client')
    def test_fluxo_completo_com_um_topico_sintese_sincrona(self, get_client):
        classificacao = self._classificacao_fake([
            {'nome': 'Tema Único', 'descricao': 'Tudo.',
             'questoes': [self.questao.pk, self.q2.pk]},
        ])
        sintese = _resposta_fake(texto='## Texto do tópico', input_tokens=500, output_tokens=300)
        get_client.return_value.messages.create.side_effect = [classificacao, sintese]

        resp = self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]))
        self.assertEqual(resp.status_code, 302)

        topico = Topico.objects.get(disciplina=self.disc)
        self.assertEqual(topico.nome, 'Tema Único')
        self.assertEqual(
            set(topico.questoes.values_list('pk', flat=True)),
            {self.questao.pk, self.q2.pk},
        )
        self.assertEqual(topico.texto.status, TextoTopico.Status.CONCLUIDO)
        self.assertIn('Texto do tópico', topico.texto.texto_md)

        profile = self.user.profile
        profile.refresh_from_db()
        self.assertGreater(profile.tokens_usados_mes, 0)

    @patch('ai.services.get_client')
    def test_classificacao_usa_effort_e_max_tokens_maior_em_disciplina_grande(self, get_client):
        from django.test import override_settings

        get_client.return_value.messages.create.return_value = self._classificacao_fake(
            [{'nome': 'Tema', 'descricao': 'x', 'questoes': list(range(1, 301))}]
        )
        questoes_grandes = [SimpleNamespace(pk=i, enunciado_md=f'Enunciado {i}') for i in range(1, 301)]
        with override_settings(AI_MODEL='claude-sonnet-5', AI_MAX_TOKENS=16000, AI_EFFORT='low'):
            classificar_topicos_via_ia(questoes_grandes)

        kwargs = get_client.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs['thinking'], {'type': 'adaptive'})
        self.assertEqual(kwargs['output_config']['effort'], 'low')
        self.assertGreater(kwargs['max_tokens'], 16000)

    @patch('ai.services.get_client')
    def test_classificacao_sem_texto_levanta_erro_claro(self, get_client):
        resposta_vazia = SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=100, output_tokens=0), stop_reason='max_tokens',
        )
        get_client.return_value.messages.create.return_value = resposta_vazia
        with self.assertRaises(IAError) as ctx:
            classificar_topicos_via_ia([self.questao, self.q2])
        self.assertIn('max_tokens', str(ctx.exception))

    @patch('ai.services.get_client')
    def test_sobras_vao_para_outros_temas_e_lote_e_submetido(self, get_client):
        get_client.return_value.messages.create.side_effect = [
            self._classificacao_fake([
                {'nome': 'Tema A', 'descricao': '', 'questoes': [self.questao.pk]},
            ]),
        ]
        get_client.return_value.messages.batches.create.return_value = SimpleNamespace(id='batch_teste')
        get_client.return_value.messages.batches.retrieve.return_value = SimpleNamespace(
            processing_status='in_progress',
        )

        self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]))

        nomes = set(Topico.objects.values_list('nome', flat=True))
        self.assertEqual(nomes, {'Tema A', 'Outros temas'})
        self.q2.refresh_from_db()
        self.assertEqual(self.q2.topico.nome, 'Outros temas')

        textos = TextoTopico.objects.all()
        self.assertEqual(textos.count(), 2)
        for t in textos:
            self.assertEqual(t.status, TextoTopico.Status.PROCESSANDO)
            self.assertEqual(t.batch_id, 'batch_teste')

    @patch('ai.services.get_client')
    def test_regerar_apaga_topicos_e_textos_anteriores(self, get_client):
        antigo = Topico.objects.create(disciplina=self.disc, nome='Velho')
        TextoTopico.objects.create(topico=antigo, status=TextoTopico.Status.CONCLUIDO)
        self.questao.topico = antigo
        self.questao.save(update_fields=['topico'])

        get_client.return_value.messages.create.side_effect = [
            self._classificacao_fake([
                {'nome': 'Novo', 'descricao': '',
                 'questoes': [self.questao.pk, self.q2.pk]},
            ]),
            _resposta_fake(texto='Texto novo.'),
        ]
        self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]))

        self.assertFalse(Topico.objects.filter(nome='Velho').exists())
        self.assertEqual(Topico.objects.count(), 1)
        self.assertEqual(TextoTopico.objects.count(), 1)
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.topico.nome, 'Novo')

    @patch('ai.services.get_client')
    def test_questoes_sem_analise_ficam_fora(self, get_client):
        q3 = Questao.objects.create(
            disciplina=self.disc, numero=3, enunciado_md='Questão sem análise',
        )
        get_client.return_value.messages.create.side_effect = [
            self._classificacao_fake([
                {'nome': 'Tema', 'descricao': '',
                 'questoes': [self.questao.pk, self.q2.pk]},
            ]),
            _resposta_fake(texto='Texto.'),
        ]
        resp = self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]), follow=True)

        q3.refresh_from_db()
        self.assertIsNone(q3.topico)
        self.assertEqual(Topico.objects.count(), 1)
        # A questão sem análise não vai nem para "Outros temas"
        self.assertFalse(Topico.objects.filter(nome='Outros temas').exists())
        # O enunciado dela não entra na chamada de classificação
        primeira_chamada = get_client.return_value.messages.create.call_args_list[0]
        self.assertNotIn('Questão sem análise', primeira_chamada.kwargs['messages'][0]['content'])
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('1 questão(ões) sem análise ficaram de fora' in m for m in mensagens))

    def test_bloqueia_quando_nenhuma_questao_tem_analise(self):
        ResultadoPrompt.objects.all().delete()
        resp = self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]), follow=True)
        self.assertFalse(Topico.objects.exists())
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Nenhuma questão tem análise concluída' in m for m in mensagens))

    def test_gerar_topicos_bloqueia_sem_quota(self):
        profile = self.user.profile
        profile.quota_tokens_mes = 10
        profile.save()
        resp = self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]), follow=True)
        self.assertFalse(Topico.objects.exists())
        mensagens = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('Quota de IA insuficiente' in m for m in mensagens))

    def test_conteudo_topico_inclui_enunciados_gabaritos_e_analises(self):
        topico = Topico.objects.create(disciplina=self.disc, nome='Tema')
        self.disc.questoes.update(topico=topico)
        ResultadoPrompt.objects.create(
            questao=self.questao, prompt=self.prompt,
            status=ResultadoPrompt.Status.CONCLUIDO, resultado_md='Ponto exclusivo da Q1.',
        )
        ResultadoPrompt.objects.create(
            questao=self.q2, prompt=self.prompt,
            status=ResultadoPrompt.Status.ERRO, resultado_md='Não deve entrar.',
        )
        conteudo = montar_conteudo_topico(topico)
        self.assertIn('Enunciado da questão', conteudo)
        self.assertIn('Outro enunciado', conteudo)
        self.assertIn('Gabarito: A', conteudo)
        self.assertIn('Ponto exclusivo da Q1.', conteudo)
        self.assertNotIn('Não deve entrar.', conteudo)

    def test_params_sintese_com_cache_move_instrucoes_para_system(self):
        topico = Topico.objects.create(disciplina=self.disc, nome='Tema')
        self.disc.questoes.update(topico=topico)
        params = _params_sintese(topico, cache_prompt=True)
        system = params['system']
        self.assertIsInstance(system, list)
        self.assertEqual(system[-1]['cache_control'], {'type': 'ephemeral'})
        self.assertIn('TODAS as informações relevantes', system[-1]['text'])
        self.assertIn('Enunciado da questão', params['messages'][0]['content'])

    def test_estimar_tokens_topicos_positivo(self):
        self.assertGreater(estimar_tokens_topicos([self.questao, self.q2]), 0)
        self.assertEqual(estimar_tokens_topicos([]), 0)

    def test_estimar_custo_topicos_positivo_e_zero_sem_questoes(self):
        from decimal import Decimal
        custo = estimar_custo_topicos([self.questao, self.q2])
        self.assertGreater(custo, Decimal('0'))
        self.assertEqual(estimar_custo_topicos([]), Decimal('0'))

    def test_estimar_custo_topicos_cresce_com_mais_conteudo(self):
        q3 = Questao.objects.create(
            disciplina=self.disc, numero=3, enunciado_md='Enunciado bem maior ' * 50, gabarito='C',
        )
        ResultadoPrompt.objects.create(
            questao=q3, prompt=self.prompt, status=ResultadoPrompt.Status.CONCLUIDO,
            resultado_md='Análise bem mais longa. ' * 50,
        )
        custo_pequeno = estimar_custo_topicos([self.questao, self.q2])
        custo_grande = estimar_custo_topicos([self.questao, self.q2, q3])
        self.assertGreater(custo_grande, custo_pequeno)

    def test_formatar_custo_usd(self):
        from decimal import Decimal
        self.assertEqual(formatar_custo_usd(Decimal('0.0001')), '< $0.001')
        self.assertEqual(formatar_custo_usd(Decimal('0.0032')), '$0.0032')
        self.assertEqual(formatar_custo_usd(Decimal('0.123')), '$0.123')

    def test_status_endpoint(self):
        topico = Topico.objects.create(disciplina=self.disc, nome='Tema')
        TextoTopico.objects.create(topico=topico, status=TextoTopico.Status.CONCLUIDO)
        resp = self.client.get(reverse('questions:topicos_status', args=[self.disc.pk]))
        data = resp.json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['concluidos'], 1)
        self.assertFalse(data['em_processamento'])
