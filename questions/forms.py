import re

from django import forms

from .models import ImportacaoPDF, Questao

# Marcador de alternativa no início da linha. Formas com pontuação — "A)",
# "(A)", "A.", "A -", "A:" — são fortes por si sós. A forma "A texto" (letra
# maiúscula + espaço, comum em PDFs de bancas) é ambígua em português
# ("A sociedade…", "E pelos…"): a desambiguação fica a cargo da seleção de
# cadeia (sequência A→B→C… mais compacta), em _melhor_cadeia.
RE_ALT_PONTUADA = re.compile(r"^\s*(?:\(([A-Ea-e])\)|([A-Ea-e])\s*[).\-:–])\s*(.*)$")
RE_ALT_ESPACO = re.compile(r"^\s*([A-E])\s+(\S.*)$")
RE_CERTO_ERRADO = re.compile(r"^\s*(?:\(\s*\)\s*)?(Certo|Errado)\s*\.?\s*$", re.IGNORECASE)

# Ruído que costuma vazar para dentro do bloco da questão em PDFs compilados:
# ids numéricos soltos, os cabeçalhos "Respostas:"/"GABARITO" e a grade.
RE_LINHA_ID = re.compile(r"^\s*\d{6,}\s*$")
RE_RESPOSTAS = re.compile(r"^\s*Respostas\s*:?\s*$", re.IGNORECASE)
# Linha inteira feita de pares "número letra". Um par só também conta: a
# última linha da grade é parcial quando o total de questões não é múltiplo
# da largura da linha, e antes ficava como lixo no fim do último enunciado.
RE_GRADE_GABARITO = re.compile(r"^\s*\d+\s+[A-E](?:\s+\d+\s+[A-E])*\s*$")
# Só a linha inteira: a palavra aparece legitimamente dentro de enunciados
# ("suas respostas estavam de acordo com o gabarito fornecido pela banca").
RE_CABECALHO_GABARITO = re.compile(r"^\s*GABARITO\s*:?\s*$", re.IGNORECASE)


def _remover_ruido(linhas):
    limpas = []
    for ln in linhas:
        if RE_RESPOSTAS.match(ln):
            break  # daqui em diante é a grade de respostas do caderno
        if RE_LINHA_ID.match(ln) or RE_GRADE_GABARITO.match(ln) or RE_CABECALHO_GABARITO.match(ln):
            continue
        limpas.append(ln)
    return limpas


_ORDEM = "ABCDE"


def _candidatos_alternativa(linhas):
    """(indice_da_linha, letra, resto) para cada possível marcador de alternativa."""
    cands = []
    for i, linha in enumerate(linhas):
        m = RE_ALT_PONTUADA.match(linha)
        if m:
            letra = (m.group(1) or m.group(2)).upper()
            if letra in _ORDEM:
                cands.append((i, letra, m.group(3).strip()))
            continue
        m = RE_ALT_ESPACO.match(linha)
        if m:
            cands.append((i, m.group(1), m.group(2).strip()))
    return cands


def _melhor_cadeia(cands):
    """Escolhe a sequência A→B→C(→D→E) mais longa e compacta entre os candidatos.

    A exigência de ordem elimina falsos positivos isolados; entre empates
    (ex.: um "A …" do enunciado competindo com o "A …" real), vence a cadeia
    de menor extensão em linhas — as alternativas são um bloco contíguo.
    """
    melhores = []
    for inicio, (idx, letra, _resto) in enumerate(cands):
        if letra != "A":
            continue
        cadeia = [inicio]
        proxima = 1  # índice em _ORDEM da próxima letra esperada
        for j in range(inicio + 1, len(cands)):
            if proxima >= len(_ORDEM):
                break
            if cands[j][1] == _ORDEM[proxima]:
                cadeia.append(j)
                proxima += 1
        if len(cadeia) >= 2:
            span = cands[cadeia[-1]][0] - cands[cadeia[0]][0]
            melhores.append((len(cadeia), -span, cands[inicio][0], cadeia))
    if not melhores:
        return None
    melhores.sort(reverse=True)
    return [cands[j] for j in melhores[0][3]]


def _normalizar_enunciado(texto: str) -> str:
    """Formata enunciado_md: parágrafos do enunciado + um parágrafo por alternativa.

    Suporta alternativas A–E (com ou sem pontuação após a letra, incluindo o
    formato "A Texto…" de bancas como a FGV) e o par Certo/Errado. Linhas de
    continuação são anexadas à alternativa anterior.
    """
    if not texto:
        return texto
    linhas = _remover_ruido([ln.rstrip() for ln in texto.splitlines()])

    cadeia = _melhor_cadeia(_candidatos_alternativa(linhas))

    # Certo/Errado (sem letras): vira um par de alternativas C) / E)
    if cadeia is None:
        ce = [
            (i, m.group(1).capitalize())
            for i, ln in enumerate(linhas)
            if (m := RE_CERTO_ERRADO.match(ln))
        ]
        if len(ce) == 2 and {c[1] for c in ce} == {"Certo", "Errado"}:
            corte = ce[0][0]
            enunciado = _paragrafos(linhas[:corte])
            alts = [f"{rotulo[0]}) {rotulo}" for _i, rotulo in ce]
            return "\n\n".join([*enunciado, *alts])
        # Sem alternativas reconhecíveis: só formata os parágrafos.
        return "\n\n".join(_paragrafos(linhas))

    indices = [i for i, _l, _r in cadeia]
    enunciado = _paragrafos(linhas[: indices[0]])

    alternativas = []
    for pos, (i, letra, resto) in enumerate(cadeia):
        fim = indices[pos + 1] if pos + 1 < len(indices) else len(linhas)
        pedacos = [resto] + [ln.strip() for ln in linhas[i + 1 : fim] if ln.strip()]
        alternativas.append(f"{letra}) " + " ".join(p for p in pedacos if p))

    return "\n\n".join([*enunciado, *alternativas])


def _paragrafos(linhas):
    """Junta linhas quebradas em parágrafos, separando em linhas em branco."""
    paras: list[list[str]] = []
    atual: list[str] = []
    for ln in linhas:
        if ln.strip():
            atual.append(ln.strip())
        elif atual:
            paras.append(atual)
            atual = []
    if atual:
        paras.append(atual)
    return [" ".join(p) for p in paras]


class ImportacaoForm(forms.ModelForm):
    class Meta:
        model = ImportacaoPDF
        fields = ["arquivo"]

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        nome = (arquivo.name or "").lower()
        if not nome.endswith(".pdf"):
            raise forms.ValidationError("Envie um arquivo PDF.")
        return arquivo


class QuestaoForm(forms.ModelForm):
    class Meta:
        model = Questao
        fields = ["numero", "enunciado_md", "gabarito"]
        widgets = {
            "enunciado_md": forms.Textarea(attrs={"rows": 10}),
        }

    def clean_enunciado_md(self):
        return _normalizar_enunciado(self.cleaned_data.get("enunciado_md", ""))
