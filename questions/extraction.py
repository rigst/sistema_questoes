"""
Extração híbrida de questões a partir de um PDF de concurso.

Pipeline:
1. Texto + posições com pdfplumber; render de páginas com PyMuPDF (fitz).
2. Segmentação por regras (marcador de questão + detecção de gabarito).
3. Cálculo de confiança; refino opcional via IA quando a confiança é baixa.
4. Recorte das imagens/figuras e associação à questão correspondente.

Formatos de marcador suportados:
- "Questão 12 ..."  (palavra Questão/Questao + número) — usado por cursos/FGV.
- "12) ...", "12. ...", "12 - ..." (número + delimitador) — provas numeradas.

Gabarito:
- Seção com a palavra "GABARITO".
- Grade final no formato "1 D 2 A 3 C ..." (pares número+letra), sem cabeçalho.
- "Gabarito: X" inline no próprio enunciado.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


# Marcador forte: a palavra "Questão"/"Questao" seguida do número.
RE_QUESTAO_FORTE = re.compile(r'^\s*quest[ãa]o\s+(\d{1,3})\b', re.IGNORECASE)
# Marcador numérico: número no início da linha seguido de delimitador.
RE_QUESTAO_NUM = re.compile(r'^\s*(\d{1,3})\s*[\).\-º°]\s+')
# Alternativas: "A ...", "A) ...", "A. ...".
RE_ALTERNATIVA = re.compile(r'(?m)^\s*[A-Ea-e][\).\-]?\s+\S')
# Pares "número letra" da grade de gabarito (separador opcional: espaço, -, ., ), :).
RE_GABARITO_PAR = re.compile(r'\b(\d{1,3})\s*[.\)\-:]?\s*([A-Ea-e])\b')
RE_GABARITO_HEADER = re.compile(r'gabarito', re.IGNORECASE)
RE_GABARITO_INLINE = re.compile(r'gabarito\s*[:\-]?\s*([A-Ea-e])\b', re.IGNORECASE)
# Ruído conhecido a remover dos enunciados.
RE_RUIDO = re.compile(
    r'^(?:essa quest[ãa]o possui coment[áa]rio.*|acessar lista|p[áa]gina\s*\d+.*)$',
    re.IGNORECASE,
)
RE_CPF = re.compile(r'\d{3}\.\d{3}\.\d{3}-\d{2}')
RE_CID = re.compile(r'\(cid:\d+\)')

CONFIANCA_MINIMA_IA = 0.55


@dataclass
class QuestaoExtraida:
    numero: int
    enunciado: str
    gabarito: str = ''
    confianca: float = 0.0
    pagina: int = 0
    top: float = 0.0
    imagens: list = field(default_factory=list)  # bytes PNG


@dataclass
class ResultadoExtracao:
    questoes: list  # list[QuestaoExtraida]
    confianca_media: float = 0.0
    usou_ia: bool = False


# ---------------------------------------------------------------------------
# Limpeza de texto / ruído
# ---------------------------------------------------------------------------

def _limpar_texto(texto):
    texto = texto.replace('(cid:58)', 'fi')  # ligadura fi comum nesses PDFs
    texto = RE_CID.sub('', texto)
    return texto


def _linhas_ruido(texto_paginas):
    """Linhas de cabeçalho/rodapé repetidas na maioria das páginas."""
    n = len(texto_paginas)
    if n < 5:
        return set()
    contagem = Counter()
    for tp in texto_paginas:
        vistas = {ln.strip() for ln in tp.split('\n') if ln.strip()}
        for ln in vistas:
            contagem[ln] += 1
    limite = max(3, int(n * 0.25))
    return {ln for ln, freq in contagem.items() if len(ln) <= 80 and freq >= limite}


def _eh_ruido(linha, ruido):
    s = linha.strip()
    if not s:
        return True
    if s in ruido:
        return True
    if RE_RUIDO.match(s):
        return True
    if RE_CPF.search(s) and len(s) <= 80:
        return True
    return False


# ---------------------------------------------------------------------------
# Gabarito
# ---------------------------------------------------------------------------

def _detectar_gabarito(texto_paginas):
    """Detecta o gabarito por grade ('1 D 2 A ...') ou por seção 'GABARITO'."""
    mapa = {}
    for tp in texto_paginas:
        for linha in tp.split('\n'):
            pares = RE_GABARITO_PAR.findall(linha)
            if len(pares) >= 3:
                resto = RE_GABARITO_PAR.sub('', linha).strip()
                if len(resto) <= len(linha) * 0.35:
                    for num, letra in pares:
                        mapa[int(num)] = letra.upper()
    if mapa:
        return mapa

    # Fallback: seção textual após a palavra GABARITO.
    texto_total = '\n'.join(texto_paginas)
    idx = None
    for m in re.finditer(RE_GABARITO_HEADER, texto_total):
        idx = m.start()
    if idx is None:
        return {}
    for num, letra in RE_GABARITO_PAR.findall(texto_total[idx:]):
        mapa[int(num)] = letra.upper()
    return mapa if len(mapa) >= 2 else {}


# ---------------------------------------------------------------------------
# Linhas (com posição) a partir das palavras do pdfplumber
# ---------------------------------------------------------------------------

def _agrupar_linhas(palavras, tol=3.0):
    if not palavras:
        return []
    palavras = sorted(palavras, key=lambda w: (round(w['top']), w['x0']))
    linhas = []
    atual = []
    top_ref = None
    for w in palavras:
        if top_ref is None or abs(w['top'] - top_ref) <= tol:
            atual.append(w)
            if top_ref is None:
                top_ref = w['top']
        else:
            linhas.append(_linha_dict(atual))
            atual = [w]
            top_ref = w['top']
    if atual:
        linhas.append(_linha_dict(atual))
    return linhas


def _linha_dict(palavras):
    palavras = sorted(palavras, key=lambda w: w['x0'])
    return {
        'texto': _limpar_texto(' '.join(w['text'] for w in palavras)),
        'top': min(w['top'] for w in palavras),
        'bottom': max(w['bottom'] for w in palavras),
    }


# ---------------------------------------------------------------------------
# Segmentação
# ---------------------------------------------------------------------------

def _escolher_regex(linhas_por_pagina):
    fortes = sum(
        1 for linhas in linhas_por_pagina for ln in linhas
        if RE_QUESTAO_FORTE.match(ln['texto'])
    )
    if fortes >= 3:
        return RE_QUESTAO_FORTE, True
    return RE_QUESTAO_NUM, False


def _segmentar(linhas_por_pagina, gabarito_map, ruido):
    regex, forte = _escolher_regex(linhas_por_pagina)

    marcadores = []
    for pag_idx, linhas in enumerate(linhas_por_pagina):
        for linha in linhas:
            m = regex.match(linha['texto'])
            if m:
                marcadores.append({
                    'pagina': pag_idx,
                    'top': linha['top'],
                    'numero': int(m.group(1)),
                })

    marcadores = _filtrar_marcadores(marcadores, forte)

    questoes = []
    for i, marc in enumerate(marcadores):
        prox = marcadores[i + 1] if i + 1 < len(marcadores) else None
        texto = _texto_entre(linhas_por_pagina, marc, prox, ruido)
        numero = marc['numero']
        gab = gabarito_map.get(numero, '')
        if not gab:
            mi = RE_GABARITO_INLINE.search(texto)
            if mi:
                gab = mi.group(1).upper()
        conf = _confianca_questao(texto, gab, forte)
        questoes.append(QuestaoExtraida(
            numero=numero,
            enunciado=texto.strip(),
            gabarito=gab,
            confianca=conf,
            pagina=marc['pagina'],
            top=marc['top'],
        ))
    return questoes


def _filtrar_marcadores(marcadores, forte):
    """Mantém apenas marcadores em ordem crescente (descarta falsos positivos)."""
    filtrados = []
    ultimo = 0
    for m in marcadores:
        if m['numero'] > ultimo:
            if forte:
                # Marcador forte ("Questão N") é confiável: aceita qualquer avanço.
                filtrados.append(m)
                ultimo = m['numero']
            elif m['numero'] - ultimo <= 5 or not filtrados:
                filtrados.append(m)
                ultimo = m['numero']
    return filtrados


def _texto_entre(linhas_por_pagina, marc, prox, ruido):
    partes = []
    pag_ini = marc['pagina']
    pag_fim = prox['pagina'] if prox else len(linhas_por_pagina) - 1
    for pag in range(pag_ini, pag_fim + 1):
        for linha in linhas_por_pagina[pag]:
            # pula a própria linha do marcador "Questão N <disciplina>"
            if pag == pag_ini and linha['top'] <= marc['top'] + 0.5:
                continue
            if prox and pag == prox['pagina'] and linha['top'] >= prox['top'] - 0.5:
                continue
            if _eh_ruido(linha['texto'], ruido):
                continue
            partes.append(linha['texto'])
    return '\n'.join(partes)


def _confianca_questao(texto, gabarito, forte):
    conf = 0.5 if forte else 0.35
    if gabarito:
        conf += 0.3
    if len(RE_ALTERNATIVA.findall(texto)) >= 3:
        conf += 0.2
    if len(texto.strip()) < 20:
        conf -= 0.4
    return max(0.0, min(1.0, conf))


# ---------------------------------------------------------------------------
# Imagens
# ---------------------------------------------------------------------------

def _recortar_imagens(pdf_bytes, questoes):
    if fitz is None:
        return
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception:
        return

    por_pagina = {}
    for q in questoes:
        por_pagina.setdefault(q.pagina, []).append(q)
    for lista in por_pagina.values():
        lista.sort(key=lambda q: q.top)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pag_idx, page in enumerate(pdf.pages):
            imagens = page.images or []
            if not imagens or pag_idx not in por_pagina:
                continue
            try:
                fitz_page = doc[pag_idx]
            except Exception:
                continue
            for im in imagens:
                top = float(im.get('top', 0))
                dona = None
                for q in por_pagina[pag_idx]:
                    if q.top <= top + 2:
                        dona = q
                    else:
                        break
                if dona is None:
                    dona = por_pagina[pag_idx][0]
                rect = fitz.Rect(
                    float(im['x0']), float(im['top']),
                    float(im['x1']), float(im['bottom']),
                )
                if rect.is_empty or rect.width < 8 or rect.height < 8:
                    continue
                try:
                    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect)
                    dona.imagens.append(pix.tobytes('png'))
                except Exception:
                    continue
    doc.close()


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def extrair(pdf_bytes, usar_ia=True, profile=None, progresso=None):
    """Extrai questões de um PDF (bytes). Retorna ResultadoExtracao.

    ``progresso`` é um callback opcional ``fn(pct, etapa, total_paginas=None)``
    chamado ao longo do pipeline para reportar o andamento (0–100).
    """
    def _reportar(pct, etapa, **extra):
        if progresso:
            try:
                progresso(pct, etapa, **extra)
            except Exception:  # noqa: BLE001 — progresso nunca pode quebrar a extração
                pass

    # Fase 1: leitura do PDF (5% → 60%), proporcional ao nº de páginas.
    texto_paginas = []
    paginas_palavras = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total_paginas = len(pdf.pages)
        _reportar(5, 'Lendo o PDF…', total_paginas=total_paginas)
        for i, page in enumerate(pdf.pages, start=1):
            texto_paginas.append(_limpar_texto(page.extract_text() or ''))
            paginas_palavras.append(page.extract_words() or [])
            pct = 5 + int(55 * i / total_paginas) if total_paginas else 60
            _reportar(pct, f'Lendo página {i} de {total_paginas}…')

    # Fase 2: segmentação das questões (60% → 70%).
    _reportar(65, 'Identificando as questões…')
    linhas_por_pagina = [_agrupar_linhas(p) for p in paginas_palavras]
    ruido = _linhas_ruido(texto_paginas)
    gabarito_map = _detectar_gabarito(texto_paginas)
    questoes = _segmentar(linhas_por_pagina, gabarito_map, ruido)

    confianca_media = (
        sum(q.confianca for q in questoes) / len(questoes) if questoes else 0.0
    )

    # Fase 3: refino opcional via IA (70% → 90%).
    usou_ia = False
    if usar_ia and (confianca_media < CONFIANCA_MINIMA_IA or not questoes):
        _reportar(75, 'Refinando as questões com IA…')
        refinadas = _refinar_com_ia(texto_paginas, profile)
        if refinadas:
            questoes = _mesclar_refino(questoes, refinadas)
            confianca_media = (
                sum(q.confianca for q in questoes) / len(questoes) if questoes else 0.0
            )
            usou_ia = True

    # Fase 4: recorte das imagens/figuras (90% → 100%).
    _reportar(92, 'Extraindo imagens e figuras…')
    _recortar_imagens(pdf_bytes, questoes)
    _reportar(100, 'Finalizando…')

    return ResultadoExtracao(
        questoes=questoes,
        confianca_media=confianca_media,
        usou_ia=usou_ia,
    )


def _refinar_com_ia(texto_paginas, profile=None):
    from ai.services import separar_questoes_via_ia  # import tardio (evita ciclo)

    texto = '\n\n'.join(texto_paginas)
    try:
        itens = separar_questoes_via_ia(texto, profile=profile)
    except Exception:
        return []
    refinadas = []
    for it in itens:
        refinadas.append(QuestaoExtraida(
            numero=int(it.get('numero', 0) or 0),
            enunciado=(it.get('enunciado') or '').strip(),
            gabarito=(it.get('gabarito') or '').strip(),
            confianca=0.9,
        ))
    return refinadas


def _mesclar_refino(por_regra, refinadas):
    pos_por_numero = {q.numero: (q.pagina, q.top, q.imagens) for q in por_regra}
    for q in refinadas:
        if q.numero in pos_por_numero:
            pag, top, imgs = pos_por_numero[q.numero]
            q.pagina, q.top, q.imagens = pag, top, imgs
    return refinadas
