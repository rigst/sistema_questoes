"""
Extração híbrida de questões a partir de um PDF de concurso.

Pipeline:
1. Texto + posições com pdfplumber; render de páginas com PyMuPDF (fitz).
2. Segmentação por regras (numeração das questões + detecção de gabarito).
3. Cálculo de confiança; refino opcional via IA quando a confiança é baixa.
4. Recorte das imagens/figuras e associação à questão correspondente.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pdfplumber

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


# Marcador de início de questão no começo de uma linha.
RE_QUESTAO = re.compile(
    r'^\s*(?:quest[ãa]o\s+)?(\d{1,3})[\).\-º°:]\s+',
    re.IGNORECASE,
)
RE_GABARITO_HEADER = re.compile(r'gabarito', re.IGNORECASE)
# Pares "1-A", "1) A", "1. C", "1 D" dentro da seção de gabarito.
RE_GABARITO_PAR = re.compile(r'(\d{1,3})\s*[\).\-:]?\s*([A-Ea-e])\b')
# Gabarito inline dentro do próprio enunciado.
RE_GABARITO_INLINE = re.compile(
    r'gabarito\s*[:\-]?\s*([A-Ea-e])\b', re.IGNORECASE
)
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


def _detectar_gabarito_geral(texto_paginas):
    """Procura uma seção 'GABARITO' e retorna {numero: letra}."""
    texto_total = '\n'.join(texto_paginas)
    idx = None
    for m in re.finditer(RE_GABARITO_HEADER, texto_total):
        idx = m.start()
    if idx is None:
        return {}
    trecho = texto_total[idx:]
    mapa = {}
    for num, letra in RE_GABARITO_PAR.findall(trecho):
        mapa[int(num)] = letra.upper()
    # Evita falsos positivos: só usa se houver ao menos dois pares plausíveis.
    return mapa if len(mapa) >= 2 else {}


def _segmentar_por_regras(paginas_palavras, gabarito_map):
    """
    paginas_palavras: lista por página de dicts pdfplumber (extract_words),
    cada um com 'text','top','x0','x1','bottom'.
    Retorna lista de QuestaoExtraida (sem imagens ainda).
    """
    # Reconstrói linhas por página preservando o 'top' do início da linha.
    marcadores = []  # (pagina, top, numero, char_offset_global)
    linhas_por_pagina = []
    for pag_idx, palavras in enumerate(paginas_palavras):
        linhas = _agrupar_linhas(palavras)
        linhas_por_pagina.append(linhas)
        for linha in linhas:
            m = RE_QUESTAO.match(linha['texto'])
            if m:
                marcadores.append({
                    'pagina': pag_idx,
                    'top': linha['top'],
                    'numero': int(m.group(1)),
                })

    # Mantém apenas marcadores com numeração não-decrescente e coerente.
    marcadores = _filtrar_marcadores_sequenciais(marcadores)

    questoes = []
    for i, marc in enumerate(marcadores):
        prox = marcadores[i + 1] if i + 1 < len(marcadores) else None
        texto = _texto_entre(linhas_por_pagina, marc, prox)
        numero = marc['numero']
        gab = gabarito_map.get(numero, '')
        if not gab:
            mi = RE_GABARITO_INLINE.search(texto)
            if mi:
                gab = mi.group(1).upper()
        conf = _confianca_questao(texto, gab)
        questoes.append(QuestaoExtraida(
            numero=numero,
            enunciado=texto.strip(),
            gabarito=gab,
            confianca=conf,
            pagina=marc['pagina'],
            top=marc['top'],
        ))
    return questoes


def _agrupar_linhas(palavras, tol=3.0):
    """Agrupa palavras em linhas por proximidade vertical (top)."""
    if not palavras:
        return []
    palavras = sorted(palavras, key=lambda w: (round(w['top']), w['x0']))
    linhas = []
    atual = []
    top_ref = None
    for w in palavras:
        if top_ref is None or abs(w['top'] - top_ref) <= tol:
            atual.append(w)
            top_ref = w['top'] if top_ref is None else top_ref
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
        'texto': ' '.join(w['text'] for w in palavras),
        'top': min(w['top'] for w in palavras),
        'bottom': max(w['bottom'] for w in palavras),
    }


def _filtrar_marcadores_sequenciais(marcadores):
    """Remove marcadores cujo número quebra a sequência (provável falso positivo)."""
    if not marcadores:
        return []
    filtrados = [marcadores[0]]
    for m in marcadores[1:]:
        ultimo = filtrados[-1]['numero']
        # aceita se for maior que o último e não pular demais
        if m['numero'] > ultimo and (m['numero'] - ultimo) <= 5:
            filtrados.append(m)
        elif m['numero'] == ultimo + 1:
            filtrados.append(m)
    return filtrados


def _texto_entre(linhas_por_pagina, marc, prox):
    partes = []
    pag_ini = marc['pagina']
    pag_fim = prox['pagina'] if prox else len(linhas_por_pagina) - 1
    for pag in range(pag_ini, pag_fim + 1):
        for linha in linhas_por_pagina[pag]:
            if pag == pag_ini and linha['top'] < marc['top'] - 0.5:
                continue
            if prox and pag == prox['pagina'] and linha['top'] >= prox['top'] - 0.5:
                continue
            partes.append(linha['texto'])
    return '\n'.join(partes)


def _confianca_questao(texto, gabarito):
    conf = 0.4
    if gabarito:
        conf += 0.4
    # presença de alternativas A) B) C) D) E)
    alternativas = len(re.findall(r'(?m)^\s*[A-Ea-e][\).\-]\s+', texto))
    if alternativas >= 3:
        conf += 0.2
    if len(texto.strip()) < 20:
        conf -= 0.3
    return max(0.0, min(1.0, conf))


def _recortar_imagens(pdf_bytes, questoes):
    """Recorta as imagens do PDF e associa à questão acima de cada figura."""
    if fitz is None:
        return
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception:
        return

    # Indexa questões por página ordenadas por top.
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
                # questão dona = última cujo top <= top da imagem
                dona = None
                for q in por_pagina[pag_idx]:
                    if q.top <= top + 2:
                        dona = q
                    else:
                        break
                if dona is None:
                    por_pagina[pag_idx][0] if por_pagina[pag_idx] else None
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


def extrair(pdf_bytes, usar_ia=True, profile=None):
    """Ponto de entrada: extrai questões de um PDF (bytes). Retorna ResultadoExtracao."""
    texto_paginas = []
    paginas_palavras = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            texto_paginas.append(page.extract_text() or '')
            paginas_palavras.append(page.extract_words() or [])

    gabarito_map = _detectar_gabarito_geral(texto_paginas)
    questoes = _segmentar_por_regras(paginas_palavras, gabarito_map)

    confianca_media = (
        sum(q.confianca for q in questoes) / len(questoes) if questoes else 0.0
    )

    usou_ia = False
    if usar_ia and (confianca_media < CONFIANCA_MINIMA_IA or not questoes):
        refinadas = _refinar_com_ia(texto_paginas, profile)
        if refinadas:
            # Mantém posições/páginas das questões por regra quando possível.
            questoes = _mesclar_refino(questoes, refinadas)
            confianca_media = (
                sum(q.confianca for q in questoes) / len(questoes) if questoes else 0.0
            )
            usou_ia = True

    _recortar_imagens(pdf_bytes, questoes)

    return ResultadoExtracao(
        questoes=questoes,
        confianca_media=confianca_media,
        usou_ia=usou_ia,
    )


def _refinar_com_ia(texto_paginas, profile=None):
    """Usa o Claude (structured outputs) para separar as questões do texto."""
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
    """Casa as questões refinadas pela IA com posição/página detectadas por regra."""
    pos_por_numero = {q.numero: (q.pagina, q.top, q.imagens) for q in por_regra}
    for q in refinadas:
        if q.numero in pos_por_numero:
            pag, top, imgs = pos_por_numero[q.numero]
            q.pagina, q.top, q.imagens = pag, top, imgs
    return refinadas
