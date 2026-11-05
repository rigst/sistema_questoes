"""Testes da sonda /healthz/ usada pelo smoke-test do CD."""

from unittest import mock

from django.contrib.auth.models import User
from django.db.utils import OperationalError
from django.test import TestCase, override_settings

from accounts.testing import SENHA_TESTE

URL = "/healthz/"
CONEXAO_CELERY = "config.celery.app.connection_for_write"
# Fora do modo eager, como em produção: só assim a sonda consulta o broker.
COM_BROKER = override_settings(
    CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="redis://broker-de-teste:6379/0"
)


class HealthzTests(TestCase):
    def test_tudo_ok_responde_200(self):
        resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.content, b"ok\n")
        self.assertIn("no-cache", resposta["Cache-Control"])

    def test_banco_fora_responde_503(self):
        with (
            mock.patch("config.healthz.connection.cursor", side_effect=OperationalError("fora")),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.content, b"indisponivel\n")
        self.assertIn("banco", logs.output[0])

    def test_cache_fora_responde_503(self):
        with (
            mock.patch("config.healthz.cache.set", side_effect=ConnectionError("redis fora")),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("cache", logs.output[0])

    def test_cache_que_nao_devolve_o_valor_responde_503(self):
        with (
            mock.patch("config.healthz.cache.get", return_value=None),
            self.assertLogs("config.healthz", level="ERROR"),
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)

    @COM_BROKER
    def test_broker_fora_responde_503(self):
        with (
            mock.patch(CONEXAO_CELERY, side_effect=OSError("broker fora")),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("broker", logs.output[0])

    @COM_BROKER
    def test_broker_ok_responde_200(self):
        with mock.patch(CONEXAO_CELERY) as conexao:
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        conexao.return_value.__enter__.return_value.ensure_connection.assert_called_once()

    @override_settings(CELERY_BROKER_URL="")
    def test_sem_broker_configurado_nao_consulta_o_celery(self):
        with mock.patch(CONEXAO_CELERY) as conexao:
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        conexao.assert_not_called()

    def test_post_nao_e_aceito(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_usuario_com_termos_pendentes_nao_e_redirecionado(self):
        # O aceite obrigatório redireciona tudo para /legal/reaceite/; a sonda
        # precisa responder 200 mesmo assim (302 reprova o smoke-test).
        usuario = User.objects.create_user("sonda", password=SENHA_TESTE)
        self.client.force_login(usuario)
        with mock.patch("legal.middleware.precisa_reaceitar", return_value=True):
            self.assertEqual(self.client.get("/").status_code, 302)
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
