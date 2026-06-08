from django import forms

from .models import Disciplina, Prova


class ProvaForm(forms.ModelForm):
    class Meta:
        model = Prova
        fields = ['nome', 'descricao']
        widgets = {
            'descricao': forms.Textarea(attrs={'rows': 3}),
        }


class DisciplinaForm(forms.ModelForm):
    class Meta:
        model = Disciplina
        fields = ['nome', 'ordem']
