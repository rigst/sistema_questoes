from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.testing import SENHA_TESTE
from ai.models import ResultadoPrompt, TextoTopico
from exams.models import Disciplina, Prova
from prompts.models import Prompt
from questions.models import Questao, Topico

from .models import Relatorio
from .services import gerar_relatorio, gerar_relatorio_topicos

User = get_user_model()


class RelatorioTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ana", password=SENHA_TESTE)
        self.prova = Prova.objects.create(user=self.u, nome="Concurso")
        self.disc = Disciplina.objects.create(prova=self.prova, nome="Direito")
        self.prompt = Prompt.objects.create(user=self.u, nome="Explicar", texto="Explique.")
        self.q = Questao.objects.create(
            disciplina=self.disc,
            numero=1,
            enunciado_md="Enunciado",
            gabarito="A",
            status=Questao.Status.CONCLUIDA,
        )
        ResultadoPrompt.objects.create(
            questao=self.q,
            prompt=self.prompt,
            status=ResultadoPrompt.Status.CONCLUIDO,
            resultado_md="**Resposta:** A.",
        )

    def test_gera_pdf_com_texto(self):
        rel = gerar_relatorio(
            self.u, self.prompt, disciplina=self.disc, com_texto=True, formato="pdf"
        )
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_pdf.size > 0)

    def test_gera_pdf_sem_texto(self):
        rel = gerar_relatorio(
            self.u, self.prompt, disciplina=self.disc, com_texto=False, formato="pdf"
        )
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_pdf.size > 0)

    def test_gera_markdown(self):
        rel = gerar_relatorio(self.u, self.prompt, disciplina=self.disc, formato="md")
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_md.size > 0)
        self.assertFalse(rel.arquivo_pdf)

    def test_escopo_sem_resultados_retorna_zero(self):
        outro = Prompt.objects.create(user=self.u, nome="Outro", texto="x")
        rel = gerar_relatorio(self.u, outro, disciplina=self.disc)
        self.assertEqual(rel.num_questoes, 0)


class RelatorioTopicosTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ana", password=SENHA_TESTE)
        self.prova = Prova.objects.create(user=self.u, nome="Concurso")
        self.disc = Disciplina.objects.create(prova=self.prova, nome="Direito")
        self.q1 = Questao.objects.create(
            disciplina=self.disc,
            numero=1,
            enunciado_md="Enunciado 1",
            gabarito="A",
            status=Questao.Status.CONCLUIDA,
        )
        self.q2 = Questao.objects.create(
            disciplina=self.disc,
            numero=2,
            enunciado_md="Enunciado 2",
            gabarito="B",
            status=Questao.Status.CONCLUIDA,
        )
        self.topico = Topico.objects.create(
            disciplina=self.disc,
            nome="Princípios constitucionais",
            descricao="Base teórica",
            ordem=1,
        )
        self.q1.topico = self.topico
        self.q1.save()
        self.q2.topico = self.topico
        self.q2.save()
        TextoTopico.objects.create(
            topico=self.topico,
            status=TextoTopico.Status.CONCLUIDO,
            texto_md="## Resumo\n\nTexto de estudo **completo** do tópico.",
        )

    def test_gera_pdf_topicos(self):
        rel = gerar_relatorio_topicos(self.u, disciplina=self.disc, formato="pdf")
        self.assertEqual(rel.tipo, rel.Tipo.TOPICOS)
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_pdf.size > 0)

    def test_gera_markdown_topicos(self):
        rel = gerar_relatorio_topicos(self.u, disciplina=self.disc, formato="md")
        self.assertEqual(rel.num_questoes, 1)
        conteudo = rel.arquivo_md.read().decode("utf-8")
        self.assertIn("Princípios constitucionais", conteudo)
        self.assertIn("Texto de estudo", conteudo)
        self.assertIn("Questões:** 1, 2", conteudo)

    def test_topico_sem_texto_pronto_fica_fora(self):
        outro_topico = Topico.objects.create(disciplina=self.disc, nome="Sem texto", ordem=2)
        TextoTopico.objects.create(topico=outro_topico, status=TextoTopico.Status.PROCESSANDO)
        rel = gerar_relatorio_topicos(self.u, disciplina=self.disc, formato="md")
        self.assertEqual(rel.num_questoes, 1)

    def test_escopo_sem_topicos_retorna_zero(self):
        outra_disc = Disciplina.objects.create(prova=self.prova, nome="Português")
        rel = gerar_relatorio_topicos(self.u, disciplina=outra_disc, formato="md")
        self.assertEqual(rel.num_questoes, 0)


class RelatorioViewsTests(TestCase):
    """As views não tinham teste nenhum — 33 das 47 linhas de reports/views.py
    estavam descobertas, e é o que segura o Quality Gate do main."""

    def setUp(self):
        self.u = User.objects.create_user("ana", password=SENHA_TESTE)
        self.outro = User.objects.create_user("bob", password=SENHA_TESTE)
        self.prova = Prova.objects.create(user=self.u, nome="Concurso")
        self.disc = Disciplina.objects.create(prova=self.prova, nome="Direito")
        # O relatório de comentários usa o prompt padrão do sistema, que a view
        # busca com get_object_or_404(user__isnull=True) — no singular. Uma
        # migration já semeia exatamente um; criar outro aqui faria a view
        # estourar MultipleObjectsReturned, que é justamente o contrato.
        self.prompt = Prompt.objects.get(user__isnull=True)
        self.q = Questao.objects.create(
            disciplina=self.disc,
            numero=1,
            enunciado_md="Enunciado",
            gabarito="A",
            status=Questao.Status.CONCLUIDA,
        )
        ResultadoPrompt.objects.create(
            questao=self.q,
            prompt=self.prompt,
            status=ResultadoPrompt.Status.CONCLUIDO,
            resultado_md="**Resposta:** A.",
        )
        self.client.force_login(self.u)

    def test_lista_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse("reports:lista"))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("login"), resposta["Location"])

    def test_lista_mostra_so_os_relatorios_do_usuario(self):
        meu = Relatorio.objects.create(user=self.u, titulo="Meu relatório")
        Relatorio.objects.create(user=self.outro, titulo="Relatório alheio")

        resposta = self.client.get(reverse("reports:lista"))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(list(resposta.context["relatorios"]), [meu])
        self.assertIn(self.prova, resposta.context["provas"])
        self.assertIn(self.disc, resposta.context["disciplinas"])

    def test_gerar_por_get_so_redireciona(self):
        resposta = self.client.get(reverse("reports:gerar"))
        self.assertRedirects(resposta, reverse("reports:lista"))
        self.assertEqual(Relatorio.objects.count(), 0)

    def test_gerar_comentarios_por_disciplina(self):
        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.COMENTARIOS, "disciplina_id": self.disc.pk, "formato": "md"},
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        relatorio = Relatorio.objects.get()
        self.assertEqual(relatorio.num_questoes, 1)
        self.assertEqual([m.level_tag for m in resposta.wsgi_request._messages], ["success"])

    def test_gerar_por_prova_quando_nao_veio_disciplina(self):
        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.COMENTARIOS, "prova_id": self.prova.pk, "formato": "md"},
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        self.assertEqual(Relatorio.objects.get().prova, self.prova)

    def test_gerar_avisa_quando_o_escopo_sai_vazio(self):
        outra_disc = Disciplina.objects.create(prova=self.prova, nome="Português")

        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.COMENTARIOS, "disciplina_id": outra_disc.pk, "formato": "md"},
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        self.assertEqual(Relatorio.objects.get().num_questoes, 0)
        self.assertEqual([m.level_tag for m in resposta.wsgi_request._messages], ["warning"])

    def test_gerar_topicos_sem_texto_pronto_avisa(self):
        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.TOPICOS, "disciplina_id": self.disc.pk, "formato": "md"},
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        relatorio = Relatorio.objects.get()
        self.assertEqual(relatorio.tipo, Relatorio.Tipo.TOPICOS)
        self.assertEqual(relatorio.num_questoes, 0)
        self.assertEqual([m.level_tag for m in resposta.wsgi_request._messages], ["warning"])

    def test_gerar_topicos_com_texto_pronto(self):
        topico = Topico.objects.create(disciplina=self.disc, nome="Princípios", ordem=1)
        self.q.topico = topico
        self.q.save()
        TextoTopico.objects.create(
            topico=topico,
            status=TextoTopico.Status.CONCLUIDO,
            texto_md="## Resumo\n\nTexto pronto.",
        )

        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.TOPICOS, "disciplina_id": self.disc.pk, "formato": "md"},
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        relatorio = Relatorio.objects.get()
        self.assertEqual(relatorio.tipo, Relatorio.Tipo.TOPICOS)
        self.assertEqual(relatorio.num_questoes, 1)
        self.assertEqual([m.level_tag for m in resposta.wsgi_request._messages], ["success"])

    def test_gerar_sem_escopo_pega_tudo_do_usuario(self):
        """Nem disciplina_id nem prova_id: o relatório sai sobre todo o acervo
        de quem pediu, sem filtro."""
        resposta = self.client.post(
            reverse("reports:gerar"), {"tipo": Relatorio.Tipo.COMENTARIOS, "formato": "md"}
        )

        self.assertRedirects(resposta, reverse("reports:lista"))
        relatorio = Relatorio.objects.get()
        self.assertIsNone(relatorio.prova)
        self.assertIsNone(relatorio.disciplina)
        self.assertEqual(relatorio.num_questoes, 1)

    def test_gerar_recusa_disciplina_de_outro_usuario(self):
        alheia = Disciplina.objects.create(
            prova=Prova.objects.create(user=self.outro, nome="Outra"), nome="Alheia"
        )
        resposta = self.client.post(
            reverse("reports:gerar"),
            {"tipo": Relatorio.Tipo.COMENTARIOS, "disciplina_id": alheia.pk},
        )
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Relatorio.objects.count(), 0)

    def test_excluir_por_post_remove(self):
        relatorio = Relatorio.objects.create(user=self.u, titulo="Some")

        resposta = self.client.post(reverse("reports:excluir", args=[relatorio.pk]))

        self.assertRedirects(resposta, reverse("reports:lista"))
        self.assertFalse(Relatorio.objects.filter(pk=relatorio.pk).exists())

    def test_excluir_por_get_nao_remove(self):
        """GET não pode apagar: link visitado por crawler ou prefetch do
        navegador destruiria relatório."""
        relatorio = Relatorio.objects.create(user=self.u, titulo="Fica")

        resposta = self.client.get(reverse("reports:excluir", args=[relatorio.pk]))

        self.assertRedirects(resposta, reverse("reports:lista"))
        self.assertTrue(Relatorio.objects.filter(pk=relatorio.pk).exists())

    def test_excluir_relatorio_de_outro_usuario_da_404(self):
        alheio = Relatorio.objects.create(user=self.outro, titulo="Alheio")

        resposta = self.client.post(reverse("reports:excluir", args=[alheio.pk]))

        self.assertEqual(resposta.status_code, 404)
        self.assertTrue(Relatorio.objects.filter(pk=alheio.pk).exists())
