from django.conf import settings
from django.db import models


class Prova(models.Model):
    """Uma prova/concurso do usuário, que agrupa disciplinas."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='provas',
    )
    nome = models.CharField('nome', max_length=200)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'prova'
        verbose_name_plural = 'provas'
        ordering = ['-criado_em']

    def __str__(self):
        return self.nome

    @property
    def total_disciplinas(self):
        return self.disciplinas.count()


class Disciplina(models.Model):
    """Uma disciplina dentro de uma prova."""

    prova = models.ForeignKey(
        Prova,
        on_delete=models.CASCADE,
        related_name='disciplinas',
    )
    nome = models.CharField('nome', max_length=200)
    ordem = models.PositiveIntegerField('ordem', default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'disciplina'
        verbose_name_plural = 'disciplinas'
        ordering = ['ordem', 'nome']

    def __str__(self):
        return f'{self.nome} ({self.prova.nome})'

    @property
    def total_questoes(self):
        return self.questoes.count()
