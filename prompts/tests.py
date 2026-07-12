from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Prompt

User = get_user_model()


class PromptViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x')
        self.client.force_login(self.user)

    def test_lista_renderiza_somente_prompts_do_usuario(self):
        Prompt.objects.create(user=self.user, nome='Meu', texto='x')
        Prompt.objects.create(
            user=User.objects.create_user('bia', password='x'), nome='Da Bia', texto='y'
        )
        resp = self.client.get(reverse('prompts:lista'))
        self.assertContains(resp, 'Meu')
        self.assertNotContains(resp, 'Da Bia')

    def test_criar_prompt_com_tipo_sucinto(self):
        resp = self.client.post(reverse('prompts:novo'), {
            'nome': 'Resumo',
            'tipo': Prompt.Tipo.SUCINTO,
            'texto': 'Resuma a questão.',
        })
        self.assertEqual(resp.status_code, 302)
        prompt = Prompt.objects.get(user=self.user, nome='Resumo')
        self.assertEqual(prompt.tipo, Prompt.Tipo.SUCINTO)

    def test_editar_prompt(self):
        prompt = Prompt.objects.create(user=self.user, nome='Antigo', texto='x')
        resp = self.client.post(reverse('prompts:editar', args=[prompt.pk]), {
            'nome': 'Novo nome',
            'tipo': prompt.tipo,
            'texto': 'Texto novo.',
        })
        self.assertEqual(resp.status_code, 302)
        prompt.refresh_from_db()
        self.assertEqual(prompt.nome, 'Novo nome')

    def test_excluir_exige_post(self):
        prompt = Prompt.objects.create(user=self.user, nome='Meu', texto='x')
        resp = self.client.get(reverse('prompts:excluir', args=[prompt.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Prompt.objects.filter(pk=prompt.pk).exists())

        self.client.post(reverse('prompts:excluir', args=[prompt.pk]))
        self.assertFalse(Prompt.objects.filter(pk=prompt.pk).exists())

    def test_prompt_padrao_aparece_para_todos(self):
        resp = self.client.get(reverse('prompts:lista'))
        self.assertContains(resp, 'Análise da questão')
        self.assertContains(resp, 'padrão')

    def test_prompt_padrao_nao_pode_ser_editado_nem_excluido(self):
        padrao = Prompt.objects.get(user__isnull=True)
        resp = self.client.get(reverse('prompts:editar', args=[padrao.pk]))
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(reverse('prompts:excluir', args=[padrao.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Prompt.objects.filter(pk=padrao.pk).exists())

    def test_visiveis_para_ordena_padrao_primeiro(self):
        Prompt.objects.create(user=self.user, nome='AAA meu prompt', texto='x')
        visiveis = list(Prompt.visiveis_para(self.user))
        self.assertTrue(visiveis[0].padrao)
        self.assertEqual(visiveis[-1].nome, 'AAA meu prompt')

    def test_nao_exclui_prompt_de_outro_usuario(self):
        prompt = Prompt.objects.create(
            user=User.objects.create_user('bia', password='x'), nome='Da Bia', texto='y'
        )
        resp = self.client.post(reverse('prompts:excluir', args=[prompt.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Prompt.objects.filter(pk=prompt.pk).exists())
