import fitz
from django.test import TestCase

from . import extraction

PDF_TEXTO = """Prova

1) Primeira questao sobre algo.
A) a
B) b
C) c
D) d
E) e

2) Segunda questao sobre outra coisa.
A) a
B) b
C) c
D) d
E) e

GABARITO
1-A 2-D
"""


def _pdf_bytes(texto):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), texto, fontsize=11)
    return doc.tobytes()


class ExtracaoTests(TestCase):
    def test_separa_questoes_e_gabarito_por_regras(self):
        res = extraction.extrair(_pdf_bytes(PDF_TEXTO), usar_ia=False)
        self.assertEqual(len(res.questoes), 2)
        numeros = sorted(q.numero for q in res.questoes)
        self.assertEqual(numeros, [1, 2])
        gabaritos = {q.numero: q.gabarito for q in res.questoes}
        self.assertEqual(gabaritos[1], 'A')
        self.assertEqual(gabaritos[2], 'D')
        self.assertGreaterEqual(res.confianca_media, 0.8)

    def test_pdf_sem_questoes_nao_quebra(self):
        res = extraction.extrair(_pdf_bytes('Texto qualquer sem numeracao.'), usar_ia=False)
        self.assertEqual(res.questoes, [])

    def test_formato_questao_N_com_grade_de_gabarito(self):
        # Formato "Questão N <disciplina>", alternativas "A ..." e grade final
        # "1 C 2 A 3 E" — sem a palavra GABARITO (estilo curso/FGV).
        texto = (
            'Direito Processual Civil FGV\n'
            'Fulano - 024.308.130-84\n'
            'Questão 1 Direito Processual Civil\n'
            'Primeiro enunciado sobre competencia.\n'
            'A alternativa a\nB alternativa b\nC alternativa c\nD alternativa d\nE alternativa e\n'
            'Essa questao possui comentario do professor no site 123\n'
            'Questão 2 Direito Processual Civil\n'
            'Segundo enunciado sobre recursos.\n'
            'A alternativa a\nB alternativa b\nC alternativa c\nD alternativa d\nE alternativa e\n'
            'Essa questao possui comentario do professor no site 456\n'
            'Questão 3 Direito Processual Civil\n'
            'Terceiro enunciado sobre execucao.\n'
            'A alternativa a\nB alternativa b\nC alternativa c\nD alternativa d\nE alternativa e\n'
            '1 C 2 A 3 E\n'
        )
        res = extraction.extrair(_pdf_bytes(texto), usar_ia=False)
        self.assertEqual(len(res.questoes), 3)
        gab = {q.numero: q.gabarito for q in res.questoes}
        self.assertEqual(gab, {1: 'C', 2: 'A', 3: 'E'})
        # cabeçalho/CPF e rodapé de comentário não vazam para o enunciado
        q1 = res.questoes[0]
        self.assertNotIn('024.308', q1.enunciado)
        self.assertNotIn('comentario do professor', q1.enunciado)
        self.assertIn('competencia', q1.enunciado)
