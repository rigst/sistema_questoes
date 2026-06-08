from django import forms

from .models import ImportacaoPDF, Questao


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
