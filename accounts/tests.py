from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .services import criar_visitante

User = get_user_model()


class ProfileQuotaTests(TestCase):
    def test_profile_criado_no_signal(self):
        u = User.objects.create_user("joao", password="x")
        self.assertTrue(hasattr(u, "profile"))
        self.assertGreater(u.profile.quota_tokens_mes, 0)

    def test_registrar_uso_debita_quota(self):
        u = User.objects.create_user("maria", password="x")
        p = u.profile
        p.quota_tokens_mes = 1000
        p.save()
        p.registrar_uso(100, 50, Decimal("0.01"))
        self.assertEqual(p.tokens_usados_mes, 150)
        self.assertEqual(p.tokens_restantes, 850)
        self.assertTrue(p.tem_quota(800))
        self.assertFalse(p.tem_quota(900))

    def test_visitante_tem_quota_reduzida_e_expiracao(self):
        u, senha = criar_visitante()
        self.assertTrue(u.profile.is_visitor)
        self.assertIsNotNone(u.profile.expires_at)
        self.assertGreater(u.profile.expires_at, timezone.now())
        self.assertTrue(senha)


class AuthViewsTests(TestCase):
    def test_login_mostra_visitante_e_esconde_cadastro_por_padrao(self):
        resp = self.client.get("/login/")
        self.assertContains(resp, "Entrar como visitante")
        self.assertNotContains(resp, "Criar conta")

    @override_settings(ALLOW_PUBLIC_SIGNUP=True)
    def test_login_mostra_cadastro_com_flag_ativa(self):
        resp = self.client.get("/login/")
        self.assertContains(resp, "Criar conta")

    def test_cadastro_desativado_retorna_404(self):
        resp = self.client.get("/accounts/cadastro/")
        self.assertEqual(resp.status_code, 404)

    def test_entrar_como_visitante_autentica(self):
        # O aceite dos termos passou a ser condição para criar o visitante.
        resp = self.client.post("/accounts/visitante/", {"aceite_legal": "on"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        user = resp.context["user"]
        self.assertTrue(user.is_authenticated)
        self.assertTrue(user.profile.is_visitor)

    def test_entrar_como_visitante_exige_post(self):
        resp = self.client.get("/accounts/visitante/")
        self.assertEqual(resp.status_code, 405)

    @override_settings(ALLOW_PUBLIC_SIGNUP=True)
    def test_cadastro_cria_usuario_e_loga(self):
        resp = self.client.post(
            "/accounts/cadastro/",
            {
                "username": "novo_usuario",
                "first_name": "Novo",
                "email": "novo@example.com",
                "password1": "senha-bem-forte-123",
                "password2": "senha-bem-forte-123",
                "aceite_legal": "on",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(User.objects.filter(username="novo_usuario").exists())
        self.assertTrue(resp.context["user"].is_authenticated)

    @override_settings(ALLOW_PUBLIC_SIGNUP=True)
    def test_cadastro_exige_email(self):
        resp = self.client.post(
            "/accounts/cadastro/",
            {
                "username": "sem_email",
                "password1": "senha-bem-forte-123",
                "password2": "senha-bem-forte-123",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="sem_email").exists())

    @override_settings(ALLOW_PUBLIC_SIGNUP=True)
    def test_cadastro_senhas_diferentes_mostra_erro(self):
        resp = self.client.post(
            "/accounts/cadastro/",
            {
                "username": "outro",
                "email": "outro@example.com",
                "password1": "senha-bem-forte-123",
                "password2": "diferente-456",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="outro").exists())

    def test_reset_de_senha_envia_email(self):
        from django.core import mail

        user = User.objects.create_user("ana", password="x", email="ana@example.com")
        resp = self.client.post("/senha/", {"email": user.email}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Redefinição de senha", mail.outbox[0].subject)
        self.assertIn("/senha/redefinir/", mail.outbox[0].body)
