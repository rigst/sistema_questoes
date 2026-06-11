from django import forms

from .models import Prompt


class PromptForm(forms.ModelForm):
    class Meta:
        model = Prompt
        fields = ['nome', 'texto']
        widgets = {
            'texto': forms.Textarea(attrs={'rows': 8}),
        }
