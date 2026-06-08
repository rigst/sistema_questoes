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
