from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from exams.models import Disciplina, Prova
from prompts.models import Prompt
from questions.models import Questao

from .models import ResultadoPrompt
from .services import _params_mensagem, estimar_tokens
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
