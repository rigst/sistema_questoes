from django.contrib.auth import get_user_model
from django.test import TestCase

from ai.models import ResultadoPrompt
from exams.models import Disciplina, Prova
from prompts.models import Prompt
from questions.models import Questao

from .services import gerar_relatorio

User = get_user_model()


class RelatorioTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('ana', password='x')
        self.prova = Prova.objects.create(user=self.u, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=self.prova, nome='Direito')
        self.prompt = Prompt.objects.create(user=self.u, nome='Explicar', texto='Explique.')
        self.q = Questao.objects.create(
            disciplina=self.disc, numero=1, enunciado_md='Enunciado', gabarito='A',
            status=Questao.Status.CONCLUIDA,
        )
        ResultadoPrompt.objects.create(
            questao=self.q, prompt=self.prompt,
            status=ResultadoPrompt.Status.CONCLUIDO,
            resultado_md='**Resposta:** A.',
        )

    def test_gera_pdf_com_texto(self):
        rel = gerar_relatorio(self.u, self.prompt, disciplina=self.disc, com_texto=True)
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_pdf.size > 0)

    def test_gera_pdf_sem_texto(self):
        rel = gerar_relatorio(self.u, self.prompt, disciplina=self.disc, com_texto=False)
        self.assertEqual(rel.num_questoes, 1)
        self.assertTrue(rel.arquivo_pdf.size > 0)

    def test_escopo_sem_resultados_retorna_zero(self):
        outro = Prompt.objects.create(user=self.u, nome='Outro', texto='x')
        rel = gerar_relatorio(self.u, outro, disciplina=self.disc)
        self.assertEqual(rel.num_questoes, 0)
