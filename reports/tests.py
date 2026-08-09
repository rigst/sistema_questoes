from django.contrib.auth import get_user_model
from django.test import TestCase

from ai.models import ResultadoPrompt, TextoTopico
from exams.models import Disciplina, Prova
from prompts.models import Prompt
from questions.models import Questao, Topico

from .services import gerar_relatorio, gerar_relatorio_topicos

User = get_user_model()


class RelatorioTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("ana", password="x")
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
        self.u = User.objects.create_user("ana", password="x")
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
