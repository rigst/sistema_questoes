import fitz
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from django.core.cache import cache

from ai.models import ResultadoPrompt
from ai.tasks import chave_topicos_erro
from exams.models import Disciplina, Prova
from prompts.models import Prompt

from . import extraction
from .models import LeituraTopico, Questao, Topico

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


class TopicosErroVisivelTests(TestCase):
    """Uma falha (ou o teto de gastos) na geração de tópicos precisa
    continuar visível numa visita normal à página, não só enquanto o
    polling ao vivo está rodando — senão o usuário só vê silêncio."""

    def test_erro_persistido_aparece_no_carregamento_normal_da_pagina(self):
        user = User.objects.create_user('ana2', password='x')
        prova = Prova.objects.create(user=user, nome='Concurso')
        disc = Disciplina.objects.create(prova=prova, nome='Direito Processual Civil')
        Questao.objects.create(disciplina=disc, numero=1, enunciado_md='Enunciado')
        cache.set(
            chave_topicos_erro(disc.pk),
            'Teto de gastos atingido: 3 de 8 tópico(s) não foram sintetizados.',
            86400,
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('questions:disciplina', args=[disc.pk]))
        self.assertContains(resp, 'Teto de gastos atingido')
        self.assertContains(resp, 'disc-banner-error')


class ProgressoComEtaTests(TestCase):
    """A estimativa de tempo dos progressos de IA."""

    def _eta(self, segundos_atras, feitos, restantes):
        from datetime import timedelta

        from django.utils import timezone

        from .views import _progresso_com_eta
        inicio = timezone.now() - timedelta(seconds=segundos_atras)
        return _progresso_com_eta(inicio, feitos, restantes)

    def test_sem_inicio_nao_estima(self):
        from .views import _progresso_com_eta
        self.assertEqual(_progresso_com_eta(None, 0, 0), {'decorrido': None, 'eta': None})

    def test_estimativa_proporcional_ao_ritmo_observado(self):
        # 10 feitos em 100s => 10s cada => 5 restantes ~ 50s
        r = self._eta(100, 10, 5)
        self.assertAlmostEqual(r['decorrido'], 100, delta=2)
        self.assertAlmostEqual(r['eta'], 50, delta=3)

    def test_nao_estima_antes_de_ter_amostra(self):
        self.assertIsNone(self._eta(120, 0, 10)['eta'])   # nada concluído ainda
        self.assertIsNone(self._eta(5, 1, 10)['eta'])     # cedo demais
        self.assertIsNone(self._eta(120, 10, 0)['eta'])   # nada restante

    def test_estimativa_tem_teto_para_nao_projetar_horas(self):
        # 1 item levou 1h; 100 restantes projetariam ~100h
        r = self._eta(3600, 1, 100)
        self.assertEqual(r['eta'], 4 * 3600)


class LeituraTopicoTests(TestCase):
    """Marcação de tópicos lidos e sua contagem."""

    def setUp(self):
        self.user = User.objects.create_user('leitor', password='x')
        prova = Prova.objects.create(user=self.user, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=prova, nome='Direito')
        self.t1 = Topico.objects.create(disciplina=self.disc, nome='Tema A', ordem=0)
        self.t2 = Topico.objects.create(disciplina=self.disc, nome='Tema B', ordem=1)
        self.client.force_login(self.user)

    def test_marca_e_desmarca_como_lido(self):
        url = reverse('questions:topico_leitura', args=[self.t1.pk])
        r = self.client.post(url, {'lido': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(r.json()['lido'], True)
        self.assertEqual(r.json()['total_lidos'], 1)
        self.assertTrue(LeituraTopico.objects.filter(user=self.user, topico=self.t1).exists())

        r = self.client.post(url, {'lido': '0'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(r.json()['lido'], False)
        self.assertEqual(r.json()['total_lidos'], 0)
        self.assertFalse(LeituraTopico.objects.filter(user=self.user, topico=self.t1).exists())

    def test_marcar_duas_vezes_nao_duplica(self):
        url = reverse('questions:topico_leitura', args=[self.t1.pk])
        self.client.post(url, {'lido': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.client.post(url, {'lido': '1'}, headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(LeituraTopico.objects.filter(topico=self.t1).count(), 1)

    def test_leitura_e_por_usuario(self):
        LeituraTopico.objects.create(user=self.user, topico=self.t1)
        outro = User.objects.create_user('outro', password='x')
        self.client.force_login(outro)
        # o tópico é de outro usuário: nem acessível
        r = self.client.post(reverse('questions:topico_leitura', args=[self.t1.pk]))
        self.assertEqual(r.status_code, 404)

    def test_nao_marca_topico_de_outro_usuario(self):
        outra_prova = Prova.objects.create(user=User.objects.create_user('bia', password='x'), nome='P')
        outra_disc = Disciplina.objects.create(prova=outra_prova, nome='D')
        alheio = Topico.objects.create(disciplina=outra_disc, nome='Alheio')
        r = self.client.post(reverse('questions:topico_leitura', args=[alheio.pk]))
        self.assertEqual(r.status_code, 404)
        self.assertFalse(LeituraTopico.objects.exists())

    def test_regerar_topicos_apaga_as_marcacoes(self):
        LeituraTopico.objects.create(user=self.user, topico=self.t1)
        self.disc.topicos.all().delete()
        self.assertFalse(LeituraTopico.objects.exists())

    def test_pagina_do_topico_traz_navegacao_e_estado(self):
        LeituraTopico.objects.create(user=self.user, topico=self.t1)
        r = self.client.get(reverse('questions:topico_detalhe', args=[self.t1.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['lido'])
        self.assertEqual(r.context['total_lidos'], 1)
        self.assertEqual(r.context['total_topicos'], 2)
        self.assertIsNone(r.context['anterior'])
        self.assertEqual(r.context['proximo'], self.t2)

    def test_disciplina_marca_quais_topicos_estao_lidos(self):
        LeituraTopico.objects.create(user=self.user, topico=self.t2)
        r = self.client.get(reverse('questions:disciplina', args=[self.disc.pk]))
        lidos = {t.nome: t.lido for t in r.context['topicos']}
        self.assertEqual(lidos, {'Tema A': False, 'Tema B': True})
        self.assertEqual(r.context['total_lidos'], 1)


class OrdemTopicosTests(TestCase):
    def test_mais_questoes_primeiro_e_sobras_por_ultimo(self):
        user = User.objects.create_user('ana3', password='x')
        prova = Prova.objects.create(user=user, nome='P')
        disc = Disciplina.objects.create(prova=prova, nome='D')
        from .models import NOME_TOPICO_SOBRAS
        sobras = Topico.objects.create(disciplina=disc, nome=NOME_TOPICO_SOBRAS)
        pequeno = Topico.objects.create(disciplina=disc, nome='Pequeno')
        grande = Topico.objects.create(disciplina=disc, nome='Grande')
        for i, t in enumerate([sobras] * 9 + [pequeno] * 2 + [grande] * 5):
            Questao.objects.create(disciplina=disc, numero=i + 1, topico=t)

        from .views import _topicos_ordenados
        nomes = [t.nome for t in _topicos_ordenados(disc)]
        # sobras tem MAIS questões que todos, mas vai para o fim mesmo assim
        self.assertEqual(nomes, ['Grande', 'Pequeno', NOME_TOPICO_SOBRAS])


class ContinuarLendoTests(TestCase):
    """Atalho da dashboard para o último tópico aberto."""

    def setUp(self):
        self.user = User.objects.create_user('leitor2', password='x')
        prova = Prova.objects.create(user=self.user, nome='Concurso')
        self.disc = Disciplina.objects.create(prova=prova, nome='Direito')
        self.t1 = Topico.objects.create(disciplina=self.disc, nome='Tema A', ordem=0)
        self.t2 = Topico.objects.create(disciplina=self.disc, nome='Tema B', ordem=1)
        self.client.force_login(self.user)

    def test_abrir_topico_registra_como_ultimo(self):
        self.client.get(reverse('questions:topico_detalhe', args=[self.t2.pk]))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.ultimo_topico, self.t2)

    def test_dashboard_oferece_continuar_de_onde_parou(self):
        self.client.get(reverse('questions:topico_detalhe', args=[self.t1.pk]))
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['continuar'], self.t1)

    def test_topico_ja_lido_nao_e_oferecido(self):
        self.client.get(reverse('questions:topico_detalhe', args=[self.t1.pk]))
        LeituraTopico.objects.create(user=self.user, topico=self.t1)
        r = self.client.get(reverse('dashboard'))
        self.assertIsNone(r.context['continuar'])

    def test_regerar_topicos_nao_quebra_o_atalho(self):
        self.client.get(reverse('questions:topico_detalhe', args=[self.t1.pk]))
        self.disc.topicos.all().delete()
        self.user.profile.refresh_from_db()
        self.assertIsNone(self.user.profile.ultimo_topico)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['continuar'])

    def test_dashboard_conta_topicos_lidos_e_a_ler(self):
        LeituraTopico.objects.create(user=self.user, topico=self.t1)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['total_topicos'], 2)
        self.assertEqual(r.context['total_lidos'], 1)
        self.assertEqual(r.context['total_a_ler'], 1)
        self.assertEqual(r.context['pct_lidos'], 50)


class ParserAlternativasTests(TestCase):
    """O separador de alternativas da página da questão vive em JS no
    template. Este teste roda o arquivo Node que extrai o regex de lá e o
    exercita — cobre o caso que quebrou 124 das 1008 questões, em que uma
    linha de enunciado ("A inobservância...") virava a alternativa A."""

    def test_regex_de_alternativa_nao_pega_enunciado(self):
        import shutil
        import subprocess
        from pathlib import Path

        node = shutil.which('node')
        if not node:
            self.skipTest('node não disponível')
        base = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [node, 'questions/tests_parser_alternativas.js'],
            cwd=base, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
