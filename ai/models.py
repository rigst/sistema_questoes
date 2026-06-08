from django.db import models

from prompts.models import Prompt
from questions.models import Questao


class ResultadoPrompt(models.Model):
    """Resultado da aplicação de um prompt sobre uma questão (N por questão)."""

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        PROCESSANDO = 'processando', 'Processando'
        CONCLUIDO = 'concluido', 'Concluído'
        ERRO = 'erro', 'Erro'

    questao = models.ForeignKey(
        Questao, on_delete=models.CASCADE, related_name='resultados'
    )
    prompt = models.ForeignKey(
        Prompt, on_delete=models.CASCADE, related_name='resultados'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    resultado_md = models.TextField('resultado (markdown)', blank=True)
    modelo = models.CharField('modelo', max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    custo_estimado = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    batch_id = models.CharField('batch id', max_length=120, blank=True)
    erro = models.TextField('erro', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'resultado de prompt'
        verbose_name_plural = 'resultados de prompts'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.prompt.nome} → Q{self.questao.numero}'

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens
