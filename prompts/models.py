from django.conf import settings
from django.db import models


class Prompt(models.Model):
    """Prompt reutilizável que o usuário aplica sobre as questões via IA."""

    class Tipo(models.TextChoices):
        COMPLETO = 'completo', 'Completo'
        SUCINTO = 'sucinto', 'Sucinto (revisão)'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='prompts'
    )
    nome = models.CharField('nome', max_length=200)
    tipo = models.CharField('tipo', max_length=20, choices=Tipo.choices, default=Tipo.COMPLETO)
    texto = models.TextField(
        'texto do prompt',
        help_text='Instruções enviadas à IA. O enunciado e o gabarito da questão são anexados automaticamente.',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'prompt'
        verbose_name_plural = 'prompts'
        ordering = ['nome']

    def __str__(self):
        return self.nome
