import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
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
    submeter_batch,
)
from .tasks import chave_topicos_erro, gerar_topicos, processar_lote

User = get_user_model()


def _resposta_fake(texto='**Análise.**', input_tokens=200, output_tokens=100):
    return SimpleNamespace(
        content=[SimpleNamespace(type='text', text=texto)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _fakes_classificacao(topicos, n_blocos=1):
    """Respostas dos DOIS passes da classificação: passe 1 devolve os nomes
    dos tópicos, passe 2 (um por bloco) devolve as atribuições. Recebe o
    formato consolidado `[{nome, descricao, questoes: [ids]}, ...]`."""
    nomes = _resposta_fake(texto=json.dumps({
        'topicos': [
            {'nome': t['nome'], 'descricao': t.get('descricao', '')} for t in topicos
        ],
    }))
    atribuicoes = [
        {'id': qid, 'topico': i}
        for i, t in enumerate(topicos) for qid in t.get('questoes', [])
    ]
    # Cada bloco filtra os IDs que lhe pertencem, então devolver a lista
    # inteira em todos os blocos é inofensivo.
    blocos = [
        _resposta_fake(texto=json.dumps({'atribuicoes': atribuicoes}))
        for _ in range(n_blocos)
    ]
    # Passe 3 (consolidação): por padrão nada a fundir — e, como no código
    # real, só acontece quando sobra mais de um tópico.
    if len(topicos) < 2:
        return [nomes, *blocos]
    return [nomes, *blocos, _resposta_fake(texto=json.dumps({'fusoes': []}))]


def _mock_stream(get_client_mock, resposta_ou_lista):
    """Configura get_client().messages.stream(...) — usado por _criar_mensagem
    no lugar de messages.create() — para devolver a(s) resposta(s) via
    get_final_message(), como create() devolvia diretamente antes."""
    def _context_manager(resposta):
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = resposta
        cm.__exit__.return_value = False
        return cm

    stream_mock = get_client_mock.return_value.messages.stream
    if isinstance(resposta_ou_lista, Exception):
        stream_mock.side_effect = resposta_ou_lista
    elif isinstance(resposta_ou_lista, list):
        stream_mock.side_effect = [_context_manager(r) for r in resposta_ou_lista]
    else:
        stream_mock.return_value = _context_manager(resposta_ou_lista)


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
        _mock_stream(get_client, _resposta_fake())

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
        _mock_stream(get_client, RuntimeError('api indisponível'))
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
        _mock_stream(get_client, _resposta_fake())
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
        _mock_stream(get_client, _resposta_fake(
            texto='A regra central é X (art. 1º).\n\nA alternativa (B) erra porque...',
            input_tokens=800, output_tokens=400,
        ))
        resp = self.client.post(reverse('ai:gerar_comentarios'), {'questao_ids': [self.questao.pk]})
        self.assertEqual(resp.status_code, 302)
        resultado = ResultadoPrompt.objects.get(questao=self.questao, prompt__user__isnull=True)
        self.assertEqual(resultado.status, ResultadoPrompt.Status.CONCLUIDO)
        self.assertIn('regra central', resultado.resultado_md)
        self.assertEqual(get_client.return_value.messages.stream.call_count, 1)
        self.questao.refresh_from_db()
        self.assertEqual(self.questao.status, Questao.Status.CONCLUIDA)

    @patch('ai.services.get_client')
    def test_gerar_analise_pula_ja_prontas(self, get_client):
        _mock_stream(get_client, _resposta_fake(texto='Análise.'))
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


class TetoDeGastosTests(BaseIATestCase):
    """Teto de gastos por operação (previsão + 50% de margem): itens além
    do teto não são enviados à IA e ficam marcados como erro."""

    @patch('ai.services.get_client')
    def test_sequencial_para_apos_estourar_o_teto(self, get_client):
        q2 = Questao.objects.create(disciplina=self.disc, numero=2, enunciado_md='Outra questão')
        q3 = Questao.objects.create(disciplina=self.disc, numero=3, enunciado_md='Mais uma questão')
        r1 = ResultadoPrompt.objects.create(questao=self.questao, prompt=self.prompt)
        r2 = ResultadoPrompt.objects.create(questao=q2, prompt=self.prompt)
        r3 = ResultadoPrompt.objects.create(questao=q3, prompt=self.prompt)

        # Resposta muito maior que qualquer estimativa razoável para questões
        # curtas — o gasto real já estoura o teto logo no primeiro item.
        _mock_stream(get_client, _resposta_fake(
            texto='Resposta.', input_tokens=100, output_tokens=50000,
        ))

        resultado_msg = processar_lote([r1.pk, r2.pk, r3.pk], usar_lote=False)

        r1.refresh_from_db()
        r2.refresh_from_db()
        r3.refresh_from_db()
        self.assertEqual(r1.status, ResultadoPrompt.Status.CONCLUIDO)
        self.assertEqual(r2.status, ResultadoPrompt.Status.ERRO)
        self.assertIn('teto de gastos', r2.erro)
        self.assertEqual(r3.status, ResultadoPrompt.Status.ERRO)
        self.assertEqual(get_client.return_value.messages.stream.call_count, 1)
        self.assertIn('pulado', resultado_msg)

        q2.refresh_from_db()
        self.assertEqual(q2.status, Questao.Status.ERRO)

    @patch('ai.tasks.coletar_batch.apply_async')
    @patch('ai.services.submeter_batch')
    @patch('ai.services.estimar_tokens')
    def test_lote_via_batch_para_de_submeter_chunks_apos_estourar_o_teto(
        self, mock_estimar, mock_submeter, mock_apply_async,
    ):
        mock_estimar.side_effect = [3000, 4000, 1000]  # teto (x1.5=4500), chunk1, chunk2
        mock_submeter.return_value = 'batch_x'

        ids = []
        for i in range(30):
            q = Questao.objects.create(disciplina=self.disc, numero=100 + i, enunciado_md=f'Q{i}')
            r = ResultadoPrompt.objects.create(questao=q, prompt=self.prompt)
            ids.append(r.pk)

        resultado_msg = processar_lote(ids, usar_lote=True)

        self.assertEqual(mock_submeter.call_count, 1)
        pulados = ResultadoPrompt.objects.filter(status=ResultadoPrompt.Status.ERRO)
        self.assertEqual(pulados.count(), 5)  # segundo chunk (30 - 25) não submetido
        for r in pulados:
            self.assertIn('teto de gastos', r.erro)
        self.assertIn('pulado', resultado_msg)

    @patch('ai.tasks.coletar_batch_textos.apply_async')
    @patch('ai.services.submeter_batch_textos')
    @patch('ai.services.estimar_tokens_sintese')
    @patch('ai.services.get_client')
    def test_sintese_de_topicos_via_batch_para_apos_estourar_o_teto(
        self, get_client, mock_estimar_sintese, mock_submeter, mock_apply_async,
    ):
        grupos = []
        for i in range(26):
            q = Questao.objects.create(disciplina=self.disc, numero=200 + i, enunciado_md=f'Questão extra {i}')
            ResultadoPrompt.objects.create(
                questao=q, prompt=self.prompt, status=ResultadoPrompt.Status.CONCLUIDO,
                resultado_md=f'Análise {i}.',
            )
            grupos.append({'nome': f'Tema {i}', 'descricao': '', 'questoes': [q.pk]})

        _mock_stream(get_client, _fakes_classificacao(grupos))
        mock_estimar_sintese.side_effect = [4000, 1000]  # chunk1 (25 tópicos), chunk2 (2 tópicos)
        mock_submeter.return_value = 'batch_y'

        resultado_msg = gerar_topicos(self.disc.pk, teto_tokens=4500)

        self.assertEqual(mock_submeter.call_count, 1)
        pulados = TextoTopico.objects.filter(status=TextoTopico.Status.ERRO)
        self.assertGreater(pulados.count(), 0)
        for t in pulados:
            self.assertIn('teto de gastos', t.erro)
        self.assertIn('pulado', resultado_msg)
        self.assertIn('Teto de gastos atingido', cache.get(chave_topicos_erro(self.disc.pk)))


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
        return _fakes_classificacao(topicos)

    @patch('ai.services.get_client')
    def test_fluxo_completo_com_um_topico_sintese_sincrona(self, get_client):
        classificacao = self._classificacao_fake([
            {'nome': 'Tema Único', 'descricao': 'Tudo.',
             'questoes': [self.questao.pk, self.q2.pk]},
        ])
        sintese = _resposta_fake(texto='## Texto do tópico', input_tokens=500, output_tokens=300)
        _mock_stream(get_client, [*classificacao, sintese])

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
    def test_classificacao_em_blocos_nao_perde_nenhum_id(self, get_client):
        """Regressão da auditoria: com uma chamada só, 48 de 452 IDs se
        perdiam. Em blocos, 300 questões saem 300 classificadas."""
        from django.test import override_settings

        questoes = [SimpleNamespace(pk=i, enunciado_md=f'Enunciado {i}') for i in range(1, 301)]
        respostas = [_resposta_fake(texto=json.dumps(
            {'topicos': [{'nome': 'Tema A', 'descricao': 'x'}, {'nome': 'Tema B', 'descricao': 'y'}]}
        ))]
        for ini in range(0, 300, 50):
            respostas.append(_resposta_fake(texto=json.dumps({'atribuicoes': [
                {'id': q.pk, 'topico': q.pk % 2} for q in questoes[ini:ini + 50]
            ]})))
        respostas.append(_resposta_fake(texto=json.dumps({'fusoes': []})))
        _mock_stream(get_client, respostas)

        with override_settings(AI_MODEL='claude-sonnet-5', AI_EFFORT='low'):
            grupos = classificar_topicos_via_ia(questoes)

        # 1 chamada de nomes + 6 blocos de 50 + 1 de consolidação
        self.assertEqual(get_client.return_value.messages.stream.call_count, 8)
        self.assertEqual(sum(len(g['questoes']) for g in grupos), 300)
        kwargs = get_client.return_value.messages.stream.call_args.kwargs
        self.assertEqual(kwargs['thinking'], {'type': 'adaptive'})
        self.assertEqual(kwargs['output_config']['effort'], 'low')

    def test_aplicar_fusoes_junta_questoes_e_remove_o_topico_de_origem(self):
        from ai.services import _aplicar_fusoes
        grupos = [
            {'nome': 'Ação rescisória', 'descricao': '', 'questoes': [1, 2]},
            {'nome': 'Rescisória e relações continuativas', 'descricao': '', 'questoes': [3]},
            {'nome': 'Outro tema', 'descricao': '', 'questoes': [4]},
        ]
        final = _aplicar_fusoes(grupos, [{'de': 1, 'para': 0}])
        self.assertEqual([g['nome'] for g in final], ['Ação rescisória', 'Outro tema'])
        self.assertEqual(sorted(final[0]['questoes']), [1, 2, 3])

    def test_aplicar_fusoes_resolve_cadeia_e_nao_trava_em_ciclo(self):
        from ai.services import _aplicar_fusoes
        grupos = [
            {'nome': 'A', 'descricao': '', 'questoes': [1]},
            {'nome': 'B', 'descricao': '', 'questoes': [2]},
            {'nome': 'C', 'descricao': '', 'questoes': [3]},
        ]
        # cadeia C -> B -> A: tudo deve terminar em A
        final = _aplicar_fusoes(grupos, [{'de': 2, 'para': 1}, {'de': 1, 'para': 0}])
        self.assertEqual([g['nome'] for g in final], ['A'])
        self.assertEqual(sorted(final[0]['questoes']), [1, 2, 3])
        # ciclo A -> B -> A: não pode entrar em loop nem perder questões
        final = _aplicar_fusoes(grupos, [{'de': 0, 'para': 1}, {'de': 1, 'para': 0}])
        self.assertEqual(sorted(q for g in final for q in g['questoes']), [1, 2, 3])

    def test_aplicar_fusoes_ignora_indices_invalidos(self):
        from ai.services import _aplicar_fusoes
        grupos = [{'nome': 'A', 'descricao': '', 'questoes': [1]},
                  {'nome': 'B', 'descricao': '', 'questoes': [2]}]
        final = _aplicar_fusoes(grupos, [
            {'de': 0, 'para': 0},    # para si mesmo
            {'de': 9, 'para': 0},    # origem inexistente
            {'de': 1, 'para': 42},   # destino inexistente
            {'de': None, 'para': 0},
        ])
        self.assertEqual(len(final), 2)
        self.assertEqual(sorted(q for g in final for q in g['questoes']), [1, 2])

    @patch('ai.services.get_client')
    def test_consolidacao_recusa_absorver_topico_saudavel(self, get_client):
        """Guarda dura: o modelo propôs fundir um tópico de 13 questões em
        outro de 25, criando um tópico gigante que ninguém pediu."""
        from ai.services import consolidar_topicos
        grupos = [
            {'nome': 'Jurisdição e competência', 'descricao': '', 'questoes': list(range(1, 26))},
            {'nome': 'Competência da Justiça Federal', 'descricao': '', 'questoes': list(range(26, 39))},
            {'nome': 'Recorte de X', 'descricao': '', 'questoes': [99]},
        ]
        _mock_stream(get_client, _resposta_fake(texto=json.dumps({'fusoes': [
            {'de': 1, 'para': 0},   # origem com 13 questões -> deve ser recusada
            {'de': 2, 'para': 0},   # origem com 1 questão -> permitida
        ]})))
        final = consolidar_topicos(grupos)
        nomes = [g['nome'] for g in final]
        self.assertIn('Competência da Justiça Federal', nomes)
        self.assertNotIn('Recorte de X', nomes)

    @patch('ai.services.get_client')
    def test_classificacao_funde_topico_redundante_no_terceiro_passe(self, get_client):
        questoes = [SimpleNamespace(pk=i, enunciado_md=f'E{i}') for i in range(1, 5)]
        _mock_stream(get_client, [
            _resposta_fake(texto=json.dumps({'topicos': [
                {'nome': 'Ação rescisória', 'descricao': ''},
                {'nome': 'Rescisória nas relações continuativas', 'descricao': ''},
            ]})),
            _resposta_fake(texto=json.dumps({'atribuicoes': [
                {'id': 1, 'topico': 0}, {'id': 2, 'topico': 0},
                {'id': 3, 'topico': 0}, {'id': 4, 'topico': 1},
            ]})),
            _resposta_fake(texto=json.dumps({'fusoes': [{'de': 1, 'para': 0}]})),
        ])
        grupos = classificar_topicos_via_ia(questoes)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(sorted(grupos[0]['questoes']), [1, 2, 3, 4])

    @patch('ai.services.get_client')
    def test_ids_omitidos_ou_invalidos_viram_sobra_sem_quebrar(self, get_client):
        questoes = [SimpleNamespace(pk=i, enunciado_md=f'E{i}') for i in range(1, 6)]
        _mock_stream(get_client, [
            _resposta_fake(texto=json.dumps({'topicos': [{'nome': 'Tema', 'descricao': ''}]})),
            _resposta_fake(texto=json.dumps({'atribuicoes': [
                {'id': 1, 'topico': 0},
                {'id': 2, 'topico': 99},    # índice inexistente -> ignorado
                {'id': 999, 'topico': 0},   # ID de fora do bloco -> ignorado
                # 3, 4 e 5 simplesmente omitidos pela IA
            ]})),
        ])
        grupos = classificar_topicos_via_ia(questoes)
        self.assertEqual(grupos, [{'nome': 'Tema', 'descricao': '', 'questoes': [1]}])

    @patch('ai.services.get_client')
    def test_classificacao_sem_texto_levanta_erro_claro(self, get_client):
        resposta_vazia = SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=100, output_tokens=0), stop_reason='max_tokens',
        )
        _mock_stream(get_client, resposta_vazia)
        with self.assertRaises(IAError) as ctx:
            classificar_topicos_via_ia([self.questao, self.q2])
        self.assertIn('max_tokens', str(ctx.exception))

    @patch('ai.services.get_client')
    def test_sobras_vao_para_outros_temas_e_lote_e_submetido(self, get_client):
        _mock_stream(get_client, self._classificacao_fake([
            {'nome': 'Tema A', 'descricao': '', 'questoes': [self.questao.pk]},
        ]))
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

        _mock_stream(get_client, [
            *self._classificacao_fake([
                {'nome': 'Novo', 'descricao': '',
                 'questoes': [self.questao.pk, self.q2.pk]},
            ]),
            _resposta_fake(texto='Texto novo.'),
        ])
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
        _mock_stream(get_client, [
            *self._classificacao_fake([
                {'nome': 'Tema', 'descricao': '',
                 'questoes': [self.questao.pk, self.q2.pk]},
            ]),
            _resposta_fake(texto='Texto.'),
        ])
        resp = self.client.post(reverse('ai:gerar_topicos', args=[self.disc.pk]), follow=True)

        q3.refresh_from_db()
        self.assertIsNone(q3.topico)
        self.assertEqual(Topico.objects.count(), 1)
        # A questão sem análise não vai nem para "Outros temas"
        self.assertFalse(Topico.objects.filter(nome='Outros temas').exists())
        # O enunciado dela não entra na chamada de classificação
        primeira_chamada = get_client.return_value.messages.stream.call_args_list[0]
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

    def test_sintese_de_topico_grande_ganha_max_tokens_maior(self):
        """Regressão: com teto fixo, o texto de tópicos grandes era cortado no
        meio da frase (auditoria: 'Mandado de segurança', 18 questões)."""
        from django.test import override_settings

        topico = Topico.objects.create(disciplina=self.disc, nome='Tema grande')
        self.disc.questoes.update(topico=topico)
        # Análise longa o bastante para o material do tópico passar do teto fixo.
        ResultadoPrompt.objects.create(
            questao=self.questao, prompt=self.prompt,
            status=ResultadoPrompt.Status.CONCLUIDO, resultado_md='Conteúdo extenso. ' * 6000,
        )
        with override_settings(AI_MAX_TOKENS=16000):
            params = _params_sintese(topico)
        self.assertGreater(params['max_tokens'], 16000)
        self.assertLessEqual(params['max_tokens'], 32000)

    def test_sintese_de_topico_pequeno_mantem_o_teto_padrao(self):
        from django.test import override_settings

        topico = Topico.objects.create(disciplina=self.disc, nome='Tema pequeno')
        self.disc.questoes.update(topico=topico)
        with override_settings(AI_MAX_TOKENS=16000):
            params = _params_sintese(topico)
        self.assertEqual(params['max_tokens'], 16000)

    @patch('ai.services.get_client')
    def test_submeter_batch_de_analises_grava_o_modelo(self, get_client):
        """Regressão: as 452 análises da auditoria ficaram com modelo=''
        porque só o batch de textos gravava o campo."""
        from django.test import override_settings

        get_client.return_value.messages.batches.create.return_value = SimpleNamespace(id='b1')
        r = ResultadoPrompt.objects.create(questao=self.questao, prompt=self.prompt)
        with override_settings(AI_MODEL='claude-sonnet-5'):
            submeter_batch([r])
        r.refresh_from_db()
        self.assertEqual(r.modelo, 'claude-sonnet-5')

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

    def test_custo_de_lote_aplica_o_desconto_da_batches_api(self):
        """Regressão: custo_estimado gravava preço cheio nos itens de lote,
        inflando custo_acumulado em 2x (auditoria: $7,06 gravado vs $3,53
        realmente cobrado nos 55 textos)."""
        from ai.services import custo_usd
        cheio = custo_usd(100_000, 50_000)
        lote = custo_usd(100_000, 50_000, lote=True)
        self.assertEqual(lote, cheio / 2)

    def test_estimativa_de_topicos_acompanha_disciplinas_grandes(self):
        """Regressão: o teto de 30 tópicos fazia a estimativa ficar 1,44x
        abaixo do real em 452 questões (55 tópicos), e o teto de gastos
        quase cortou a síntese."""
        from ai.services import _estimar_n_topicos
        self.assertGreaterEqual(_estimar_n_topicos(452), 50)
        self.assertEqual(_estimar_n_topicos(0), 3)

    def test_estimativa_de_tokens_cobre_o_consumo_real_de_452_questoes(self):
        """Com 452 questões o consumo medido da síntese foi ~1,06M tokens;
        a estimativa (base do teto de gastos) não pode ficar abaixo disso."""
        enunciado = 'Enunciado de questão de concurso. ' * 40
        questoes = [
            Questao.objects.create(disciplina=self.disc, numero=1000 + i, enunciado_md=enunciado)
            for i in range(60)
        ]
        for q in questoes:
            ResultadoPrompt.objects.create(
                questao=q, prompt=self.prompt, status=ResultadoPrompt.Status.CONCLUIDO,
                resultado_md='Análise da questão. ' * 120,
            )
        estimado = estimar_tokens_topicos(questoes)
        # material bruto que a síntese precisa reprocessar (entrada mínima)
        material = sum(len(q.enunciado_md) for q in questoes) / 3.8 * 2
        self.assertGreater(estimado, material)

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


class MentoriaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('mel', password='x')
        prova = Prova.objects.create(user=self.user, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=prova, nome='Direito Processual Civil')
        t = Topico.objects.create(disciplina=self.disc, nome='Recursos', ordem=0)
        Questao.objects.create(disciplina=self.disc, numero=1, topico=t, gabarito='A')
        self.client.force_login(self.user)

    @patch('ai.services.get_client')
    def test_gera_salva_e_debita_quota(self, get_client):
        _mock_stream(get_client, _resposta_fake(texto='## Como estudar\n- Comece pelos recursos.'))
        resp = self.client.post(
            reverse('ai:mentoria', args=[self.disc.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('Como estudar', data['texto_md'])
        from ai.models import MentoriaDisciplina
        m = MentoriaDisciplina.objects.get(user=self.user, disciplina=self.disc)
        self.assertIn('recursos', m.texto_md.lower())
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.tokens_usados_mes, 300)

    @patch('ai.services.get_client')
    def test_regerar_atualiza_a_mesma_linha(self, get_client):
        _mock_stream(get_client, [_resposta_fake(texto='primeira'), _resposta_fake(texto='segunda')])
        url = reverse('ai:mentoria', args=[self.disc.pk])
        self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        from ai.models import MentoriaDisciplina
        self.assertEqual(MentoriaDisciplina.objects.filter(disciplina=self.disc).count(), 1)
        self.assertEqual(MentoriaDisciplina.objects.get(disciplina=self.disc).texto_md, 'segunda')

    def test_bloqueia_sem_quota(self):
        p = self.user.profile
        p.quota_tokens_mes = 5
        p.save()
        resp = self.client.post(
            reverse('ai:mentoria', args=[self.disc.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_nao_gera_para_disciplina_de_outro(self):
        outra_prova = Prova.objects.create(
            user=User.objects.create_user('leo', password='x'), nome='P')
        alheia = Disciplina.objects.create(prova=outra_prova, nome='D')
        resp = self.client.post(reverse('ai:mentoria', args=[alheia.pk]))
        self.assertEqual(resp.status_code, 404)
