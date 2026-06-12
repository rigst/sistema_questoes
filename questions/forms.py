import re

from django import forms

from .models import ImportacaoPDF, Questao


def _normalizar_enunciado(texto: str) -> str:
    """Formata enunciado_md: parágrafos do enunciado + um parágrafo por alternativa (A–E)."""
    if not texto:
        return texto
    # Reconhece: A) A. A- A: (A) ou A com 2+ espaços — maiúsculo ou minúsculo
    ALT_RE = re.compile(r'^\s*(?:\([A-E]\)\s*|[A-E]\s*[).\-:]\s+|[A-E]\s{2,})', re.IGNORECASE)
    linhas = texto.splitlines()
    secoes: list[list[str]] = []
    secao_atual: list[str] = []
    encontrou_alternativa = False

    for linha in linhas:
        linha = linha.rstrip()
        if ALT_RE.match(linha):
            if secao_atual:
                secoes.append(secao_atual)
            secao_atual = [linha]
            encontrou_alternativa = True
        elif linha:
            secao_atual.append(linha)
        elif not encontrou_alternativa and secao_atual:
            # Linha em branco antes das alternativas → quebra de parágrafo no enunciado
            secoes.append(secao_atual)
            secao_atual = []

    if secao_atual:
        secoes.append(secao_atual)
    return '\n\n'.join(' '.join(s) for s in secoes if s)


class ImportacaoForm(forms.ModelForm):
    class Meta:
        model = ImportacaoPDF
        fields = ['arquivo']

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        nome = (arquivo.name or '').lower()
        if not nome.endswith('.pdf'):
            raise forms.ValidationError('Envie um arquivo PDF.')
        return arquivo


class QuestaoForm(forms.ModelForm):
    class Meta:
        model = Questao
        fields = ['numero', 'enunciado_md', 'gabarito']
        widgets = {
            'enunciado_md': forms.Textarea(attrs={'rows': 10}),
        }

    def clean_enunciado_md(self):
        return _normalizar_enunciado(self.cleaned_data.get('enunciado_md', ''))
