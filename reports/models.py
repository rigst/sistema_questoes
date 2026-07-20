from django.conf import settings
from django.db import models

from exams.models import Disciplina, Prova
from prompts.models import Prompt


class Relatorio(models.Model):
    """Relatório PDF gerado a partir dos resultados de prompts ou dos tópicos de estudo."""

    class Tipo(models.TextChoices):
        COMENTARIOS = 'comentarios', 'Comentários das questões'
        TOPICOS = 'topicos', 'Tópicos de estudo'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='relatorios'
    )
    titulo = models.CharField('título', max_length=255)
    tipo = models.CharField('tipo', max_length=20, choices=Tipo.choices, default=Tipo.COMENTARIOS)
    prova = models.ForeignKey(Prova, on_delete=models.SET_NULL, null=True, blank=True)
    disciplina = models.ForeignKey(Disciplina, on_delete=models.SET_NULL, null=True, blank=True)
    prompt = models.ForeignKey(Prompt, on_delete=models.SET_NULL, null=True, blank=True)
    com_texto = models.BooleanField('inclui texto da questão', default=True)
    arquivo_pdf = models.FileField('PDF', upload_to='relatorios/%Y/%m/', blank=True, null=True)
    arquivo_md = models.FileField('Markdown', upload_to='relatorios/%Y/%m/', blank=True, null=True)
    num_questoes = models.PositiveIntegerField('nº de itens (questões ou tópicos)', default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'relatório'
        verbose_name_plural = 'relatórios'
        ordering = ['-criado_em']

    def __str__(self):
        return self.titulo
