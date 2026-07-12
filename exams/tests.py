from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Disciplina, Prova

User = get_user_model()


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x')
        self.client.force_login(self.user)

    def test_dashboard_renderiza(self):
        Prova.objects.create(user=self.user, nome='Concurso')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Concurso')

    def test_dashboard_exige_login(self):
        self.client.logout()
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)


class CrudInlineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x')
        self.client.force_login(self.user)

    def test_criar_prova_inline(self):
        resp = self.client.post(reverse('exams:prova_criar_inline'), {'nome': 'Nova Prova'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Prova.objects.filter(user=self.user, nome='Nova Prova').exists())

    def test_criar_prova_sem_nome_retorna_erro(self):
        resp = self.client.post(reverse('exams:prova_criar_inline'), {'nome': '  '})
        self.assertEqual(resp.status_code, 400)

    def test_renomear_e_excluir_prova_inline(self):
        prova = Prova.objects.create(user=self.user, nome='Antiga')
        resp = self.client.post(
            reverse('exams:prova_renomear_inline', args=[prova.pk]), {'nome': 'Renomeada'}
        )
        self.assertEqual(resp.status_code, 200)
        prova.refresh_from_db()
        self.assertEqual(prova.nome, 'Renomeada')

        resp = self.client.post(reverse('exams:prova_excluir_inline', args=[prova.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Prova.objects.filter(pk=prova.pk).exists())

    def test_criar_disciplina_inline(self):
        prova = Prova.objects.create(user=self.user, nome='Concurso')
        resp = self.client.post(
            reverse('exams:disciplina_criar_inline'),
            {'nome': 'Português', 'prova_pk': prova.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Disciplina.objects.filter(prova=prova, nome='Português').exists())

    def test_nao_altera_prova_de_outro_usuario(self):
        outra = Prova.objects.create(
            user=User.objects.create_user('bia', password='x'), nome='Da Bia'
        )
        resp = self.client.post(
            reverse('exams:prova_renomear_inline', args=[outra.pk]), {'nome': 'Hackeada'}
        )
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(reverse('exams:prova_excluir_inline', args=[outra.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Prova.objects.filter(pk=outra.pk).exists())

    def test_inline_exige_post(self):
        prova = Prova.objects.create(user=self.user, nome='Concurso')
        resp = self.client.get(reverse('exams:prova_excluir_inline', args=[prova.pk]))
        self.assertEqual(resp.status_code, 405)
