from django import forms

from .models import Disciplina, Prova


class ProvaForm(forms.ModelForm):
    class Meta:
        model = Prova
        fields = ['nome']


class DisciplinaForm(forms.ModelForm):
    class Meta:
        model = Disciplina
        fields = ['nome']
