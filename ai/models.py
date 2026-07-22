from django.conf import settings
from django.db import models

from exams.models import Disciplina
from prompts.models import Prompt
from questions.models import Questao, Topico


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


class TextoTopico(models.Model):
    """Texto de estudo coeso do tópico, sintetizado pela IA a partir das
    questões e análises do tópico (1 por tópico)."""

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        PROCESSANDO = 'processando', 'Processando'
        CONCLUIDO = 'concluido', 'Concluído'
        ERRO = 'erro', 'Erro'

    topico = models.OneToOneField(
        Topico, on_delete=models.CASCADE, related_name='texto'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    texto_md = models.TextField('texto (markdown)', blank=True)
    modelo = models.CharField('modelo', max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    custo_estimado = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    batch_id = models.CharField('batch id', max_length=120, blank=True)
    erro = models.TextField('erro', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'texto de tópico'
        verbose_name_plural = 'textos de tópicos'
        ordering = ['topico__ordem', 'topico_id']

    def __str__(self):
        return f'Texto de {self.topico.nome}'

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


class MentoriaDisciplina(models.Model):
    """Dicas de estudo geradas pela IA para uma disciplina, por usuário.

    Diferente do texto de tópico (o QUE estudar), a mentoria é sobre COMO
    estudar a matéria: é personalizada com o que mais cai e com o
    desempenho do aluno na revisão, então há uma por usuário.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mentorias'
    )
    disciplina = models.ForeignKey(
        Disciplina, on_delete=models.CASCADE, related_name='mentorias'
    )
    texto_md = models.TextField('texto (markdown)', blank=True)
    modelo = models.CharField('modelo', max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    custo_estimado = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'mentoria de disciplina'
        verbose_name_plural = 'mentorias de disciplinas'
        constraints = [
            models.UniqueConstraint(fields=['user', 'disciplina'], name='mentoria_unica_por_disciplina'),
        ]

    def __str__(self):
        return f'Mentoria de {self.disciplina.nome} para {self.user}'
