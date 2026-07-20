import fitz
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ai.models import ResultadoPrompt
from exams.models import Disciplina, Prova
from prompts.models import Prompt

from . import extraction
from .models import Questao

User = get_user_model()

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


class LimpezaTextoTests(TestCase):
    def test_remove_nul_do_texto(self):
        self.assertEqual(extraction._limpar_texto('abc\x00def'), 'abcdef')

    def test_normaliza_codepoints_de_ligadura(self):
        self.assertEqual(extraction._limpar_texto('ﬁm do ﬂagrante'), 'fim do flagrante')

    def test_repara_ligaduras_engolidas_pela_fonte(self):
        texto = 'o signicado da deagração foi vericado e quali cou'
        reparado = extraction._limpar_texto(texto)
        self.assertIn('significado', reparado)
        self.assertIn('deflagração', reparado)
        self.assertIn('verificado', reparado)

    def test_nao_altera_palavras_corretas_nem_siglas(self):
        texto = 'O STF julgou o financeiro conforme a CRFB'
        self.assertEqual(extraction._limpar_texto(texto), texto)


class NormalizarEnunciadoTests(TestCase):
    def test_formato_fgv_letra_espaco_com_continuacao(self):
        from .forms import _normalizar_enunciado
        texto = (
            'Inúmeras demandas vinham sendo ajuizadas em face do Estado Alfa.\n'
            'À luz da sistemática vigente, assinale a afirmativa correta.\n'
            'A Como os atos de Alfa se estendem por 12 meses, não é cabível.\n'
            'B A decretação pressupõe requisição pelo Tribunal de Justiça.\n'
            'C A decretação, por estar presente um requisito, pode ocorrer na modalidade\n'
            'espontânea.\n'
            'D A decretação pressupõe o provimento de ação direta interventiva.\n'
            'E A decretação pressupõe iniciativa privativa do PGR.'
        )
        out = _normalizar_enunciado(texto)
        paras = out.split('\n\n')
        self.assertEqual(len(paras), 6)  # enunciado + 5 alternativas
        self.assertTrue(paras[1].startswith('A) Como os atos'))
        self.assertIn('C) A decretação, por estar presente um requisito, pode ocorrer na modalidade espontânea.', paras)
        self.assertTrue(paras[5].startswith('E) A decretação'))

    def test_linha_do_enunciado_comecando_com_A_nao_vira_alternativa(self):
        from .forms import _normalizar_enunciado
        texto = (
            'A sociedade empresária Alfa foi contratada pela Administração.\n'
            'Considerando a situação, assinale a afirmativa correta.\n'
            'A O Tribunal de Justiça deve julgar o mandado de segurança.\n'
            'B O mandado deverá ser impetrado perante um Juiz de Direito.\n'
            'C A divergência deve ser julgada por um Juiz Federal.\n'
            'D A divergência deve ser julgada por um TRF.\n'
            'E A União deve ser intimada da existência do feito.'
        )
        out = _normalizar_enunciado(texto)
        paras = out.split('\n\n')
        self.assertEqual(len(paras), 6)
        self.assertIn('A sociedade empresária', paras[0])
        self.assertTrue(paras[1].startswith('A) O Tribunal'))

    def test_formatos_pontuados_continuam_funcionando(self):
        from .forms import _normalizar_enunciado
        texto = 'Enunciado da questão.\nA) primeira\nB) segunda\nC) terceira\nD) quarta'
        out = _normalizar_enunciado(texto)
        self.assertEqual(len(out.split('\n\n')), 5)

    def test_certo_errado(self):
        from .forms import _normalizar_enunciado
        texto = 'Julgue o item a seguir: a União é ente federativo.\nCerto\nErrado'
        out = _normalizar_enunciado(texto)
        paras = out.split('\n\n')
        self.assertEqual(paras[1], 'C) Certo')
        self.assertEqual(paras[2], 'E) Errado')

    def test_sem_alternativas_mantem_paragrafos(self):
        from .forms import _normalizar_enunciado
        texto = 'Primeiro parágrafo.\n\nSegundo parágrafo.'
        self.assertEqual(_normalizar_enunciado(texto), 'Primeiro parágrafo.\n\nSegundo parágrafo.')

    def test_cid_63_e_64_viram_ligaduras(self):
        self.assertEqual(extraction._limpar_texto('a(cid:63)rmado e in(cid:64)uência'), 'afirmado e influência')

    def test_alternativas_minusculas_continuando_o_enunciado(self):
        from .forms import _normalizar_enunciado
        texto = (
            'A sociedade Alfa ajuizou demanda em face do Estado.\n'
            'Ao fim dos estudos, concluiu-se corretamente que:\n'
            'A é cabível a decretação da intervenção provocada, a que pressupõe o provimento de representação\n'
            'ajuizada pelo procurador-geral;\n'
            'B é cabível a decretação da intervenção espontânea, desde que a ausência de repasse\n'
            'tenha se estendido;\n'
            'C é cabível a decretação da intervenção espontânea, devendo o decreto especificar a amplitude;\n'
            'D é cabível a decretação da intervenção provocada, o que pressupõe requerimento;\n'
            'E não é cabível a decretação da intervenção pela União.'
        )
        out = _normalizar_enunciado(texto)
        paras = out.split('\n\n')
        self.assertEqual(len(paras), 6)
        self.assertIn('A sociedade Alfa', paras[0])
        self.assertTrue(paras[1].startswith('A) é cabível a decretação da intervenção provocada'))
        self.assertIn('ajuizada pelo procurador-geral;', paras[1])

    def test_remove_grade_de_respostas_e_ids(self):
        from .forms import _normalizar_enunciado
        texto = (
            'Enunciado final do caderno.\n'
            'A não afrontaria a ordem constitucional;\n'
            'B afrontaria a ordem constitucional;\n'
            'C não afrontaria, pois as desigualdades foram superadas;\n'
            '4000823362\n'
            'Respostas:\n'
            '1 C 2 A 3 A 4 E 5 A 6 A 7 C 8 B\n'
            '9 B 10 D 11 C 12 E 13 A 14 A 15 E 16 E'
        )
        out = _normalizar_enunciado(texto)
        self.assertNotIn('Respostas', out)
        self.assertNotIn('4000823362', out)
        self.assertNotIn('9 B 10 D', out)
        self.assertEqual(len(out.split('\n\n')), 4)


class DisciplinaCustoTopicosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', password='x')
        self.prova = Prova.objects.create(user=self.user, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=self.prova, nome='Direito')
        self.prompt = Prompt.objects.create(user=self.user, nome='Explicar', texto='Explique.')
        self.client.force_login(self.user)

    def test_sem_analise_nao_estima_custo(self):
        Questao.objects.create(disciplina=self.disc, numero=1, enunciado_md='Enunciado sem análise')
        resp = self.client.get(reverse('questions:disciplina', args=[self.disc.pk]))
        self.assertIsNone(resp.context['custo_estimado_topicos'])

    def test_com_analise_mostra_custo_estimado_ao_lado_do_botao(self):
        q = Questao.objects.create(disciplina=self.disc, numero=1, enunciado_md='Enunciado sobre X ' * 20)
        ResultadoPrompt.objects.create(
            questao=q, prompt=self.prompt, status=ResultadoPrompt.Status.CONCLUIDO,
            resultado_md='Análise concluída sobre o tema.',
        )
        resp = self.client.get(reverse('questions:disciplina', args=[self.disc.pk]))
        custo = resp.context['custo_estimado_topicos']
        self.assertIsNotNone(custo)
        self.assertTrue(custo.startswith('$') or custo.startswith('< $'))
        self.assertContains(resp, custo)
