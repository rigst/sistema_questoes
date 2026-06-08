from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .services import criar_visitante

User = get_user_model()


class ProfileQuotaTests(TestCase):
    def test_profile_criado_no_signal(self):
        u = User.objects.create_user('joao', password='x')
        self.assertTrue(hasattr(u, 'profile'))
        self.assertGreater(u.profile.quota_tokens_mes, 0)

    def test_registrar_uso_debita_quota(self):
        u = User.objects.create_user('maria', password='x')
        p = u.profile
        p.quota_tokens_mes = 1000
        p.save()
        p.registrar_uso(100, 50, Decimal('0.01'))
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
