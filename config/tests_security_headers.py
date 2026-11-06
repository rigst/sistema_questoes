"""Permissions-Policy e CSP em toda resposta (config/security_headers.py)."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from accounts.testing import SENHA_TESTE

PERMISSIONS_POLICY_FROTA = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
)
CSP = "Content-Security-Policy"
URL_LOGIN = "/login/"
URL_ADMIN_LOGIN = "/admin/login/"


class PermissionsPolicyTests(TestCase):
    def test_rota_publica_tem_permissions_policy(self):
        resposta = self.client.get(URL_LOGIN)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Permissions-Policy"], PERMISSIONS_POLICY_FROTA)

    def test_admin_tem_permissions_policy(self):
        resposta = self.client.get(URL_ADMIN_LOGIN)
        self.assertEqual(resposta["Permissions-Policy"], PERMISSIONS_POLICY_FROTA)

    def test_healthz_tem_permissions_policy(self):
        resposta = self.client.get("/healthz/")
        self.assertIn("Permissions-Policy", resposta)

    @override_settings(PERMISSIONS_POLICY="camera=(self)")
    def test_valor_vem_do_settings(self):
        resposta = self.client.get(URL_LOGIN)
        self.assertEqual(resposta["Permissions-Policy"], "camera=(self)")

    @override_settings(PERMISSIONS_POLICY="")
    def test_vazio_desliga_o_cabecalho(self):
        resposta = self.client.get(URL_LOGIN)
        self.assertNotIn("Permissions-Policy", resposta)


class ContentSecurityPolicyTests(TestCase):
    def _nonce(self, politica):
        achado = re.search(r"'nonce-([^']+)'", politica)
        if achado is None:
            self.fail(f"política sem nonce: {politica}")
        return achado.group(1)

    def test_rota_publica_tem_politica_estrita(self):
        resposta = self.client.get(URL_LOGIN)
        politica = resposta[CSP]
        self.assertNotIn("unsafe-eval", politica)
        self.assertNotIn("'unsafe-inline'", politica.split("script-src", 1)[1].split(";")[0])
        for diretiva in (
            "default-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ):
            self.assertIn(diretiva, politica)

    def test_admin_tem_unsafe_eval_so_no_prefixo(self):
        resposta = self.client.get(URL_ADMIN_LOGIN)
        self.assertIn("'unsafe-eval'", resposta[CSP])
        self.assertIn("frame-ancestors 'none'", resposta[CSP])

    def test_nonce_muda_a_cada_requisicao(self):
        primeiro = self._nonce(self.client.get(URL_LOGIN)[CSP])
        segundo = self._nonce(self.client.get(URL_LOGIN)[CSP])
        self.assertNotEqual(primeiro, segundo)

    def test_scripts_inline_levam_o_nonce_da_resposta(self):
        user = User.objects.create_user("ana", password=SENHA_TESTE)
        self.client.force_login(user)
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        nonce = self._nonce(resposta[CSP])
        html = resposta.content.decode()
        inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)
        self.assertTrue(inline, "o dashboard deveria ter script inline")
        for atributos in inline:
            self.assertIn(f'nonce="{nonce}"', atributos)

    @override_settings(ENABLE_CSP=False)
    def test_desligavel_por_settings(self):
        resposta = self.client.get(URL_LOGIN)
        self.assertNotIn(CSP, resposta)


class TemplatesSemScriptInlineSemNonceTests(TestCase):
    """Varre os templates: script inline sem nonce e handler `on*=` quebram sob a CSP."""

    def _templates(self):
        raiz = Path(settings.BASE_DIR)
        pastas = [raiz / "templates", *raiz.glob("*/templates")]
        for pasta in pastas:
            yield from pasta.rglob("*.html")

    def test_todo_script_inline_tem_nonce(self):
        sem_nonce = []
        for caminho in self._templates():
            for atributos in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", caminho.read_text()):
                if "csp_nonce" not in atributos and "application/json" not in atributos:
                    sem_nonce.append(str(caminho))
        self.assertEqual(sem_nonce, [])

    def test_nenhum_handler_inline(self):
        com_handler = [
            str(caminho)
            for caminho in self._templates()
            if re.search(r"<[^>]+\son[a-z]+\s*=", caminho.read_text())
        ]
        self.assertEqual(com_handler, [])
