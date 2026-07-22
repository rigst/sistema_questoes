from django.conf import settings
from django.db import models

from exams.models import Disciplina


class ImportacaoPDF(models.Model):
    """Um upload de PDF de questões + o resultado do processamento."""

    class Status(models.TextChoices):
        ENVIADO = 'enviado', 'Enviado'
        PROCESSANDO = 'processando', 'Processando'
        CONCLUIDO = 'concluido', 'Concluído'
        ERRO = 'erro', 'Erro'

    disciplina = models.ForeignKey(
        Disciplina, on_delete=models.CASCADE, related_name='importacoes'
    )
    arquivo = models.FileField('arquivo PDF', upload_to='importacoes/%Y/%m/')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ENVIADO)
    num_questoes = models.PositiveIntegerField('questões extraídas', default=0)
    confianca_media = models.FloatField('confiança média', default=0.0)
    usou_ia = models.BooleanField('refinado por IA', default=False)
    progresso = models.PositiveSmallIntegerField('progresso (%)', default=0)
    etapa = models.CharField('etapa atual', max_length=120, blank=True)
    total_paginas = models.PositiveIntegerField('total de páginas', default=0)
    erro = models.TextField('mensagem de erro', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'importação de PDF'
        verbose_name_plural = 'importações de PDF'
        ordering = ['-criado_em']

    def __str__(self):
        return f'Importação #{self.pk} ({self.disciplina.nome})'


# Tópico-balaio criado para as questões que a IA não classificou. Fica
# sempre no fim da listagem, depois dos temas de verdade.
NOME_TOPICO_SOBRAS = 'Outros temas'


class Topico(models.Model):
    """Tema de estudo identificado pela IA dentro de uma disciplina."""

    disciplina = models.ForeignKey(
        Disciplina, on_delete=models.CASCADE, related_name='topicos'
    )
    nome = models.CharField('nome', max_length=200)
    descricao = models.CharField('descrição', max_length=500, blank=True)
    ordem = models.PositiveIntegerField('ordem', default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'tópico'
        verbose_name_plural = 'tópicos'
        ordering = ['ordem', 'id']

    @property
    def e_sobras(self):
        return self.nome == NOME_TOPICO_SOBRAS

    def __str__(self):
        return f'{self.nome} ({self.disciplina.nome})'


class Questao(models.Model):
    """Uma questão extraída de um PDF."""

    class Status(models.TextChoices):
        DISPONIVEL = 'disponivel', 'Disponível'
        NA_FILA = 'na_fila', 'Na fila'
        PROCESSANDO = 'processando', 'Processando'
        CONCLUIDA = 'concluida', 'Concluída'
        ERRO = 'erro', 'Erro'

    disciplina = models.ForeignKey(
        Disciplina, on_delete=models.CASCADE, related_name='questoes'
    )
    importacao = models.ForeignKey(
        ImportacaoPDF, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questoes',
    )
    topico = models.ForeignKey(
        Topico, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='questoes',
    )
    numero = models.PositiveIntegerField('número', default=0)
    enunciado_md = models.TextField('enunciado (markdown)', blank=True)
    gabarito = models.CharField('gabarito', max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DISPONIVEL)
    confianca_extracao = models.FloatField('confiança da extração', default=0.0)
    ordem = models.PositiveIntegerField('ordem', default=0)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'questão'
        verbose_name_plural = 'questões'
        ordering = ['ordem', 'numero', 'id']

    def __str__(self):
        return f'Questão {self.numero} ({self.disciplina.nome})'

    @property
    def tem_imagens(self):
        return self.imagens.exists()

    def prompts_aplicados(self):
        """IDs de prompts que já possuem resultado salvo nesta questão."""
        return set(self.resultados.values_list('prompt_id', flat=True))


class QuestaoImagem(models.Model):
    """Recorte de imagem/figura associado a uma questão."""

    questao = models.ForeignKey(
        Questao, on_delete=models.CASCADE, related_name='imagens'
    )
    imagem = models.ImageField('imagem', upload_to='questoes/%Y/%m/')
    ordem = models.PositiveIntegerField('ordem', default=0)

    class Meta:
        verbose_name = 'imagem da questão'
        verbose_name_plural = 'imagens das questões'
        ordering = ['ordem', 'id']

    def __str__(self):
        return f'Imagem {self.ordem} da questão {self.questao_id}'


class LeituraTopico(models.Model):
    """Marca que um usuário terminou de ler o texto de um tópico.

    A leitura é por usuário: a mesma prova pode ser estudada por pessoas
    diferentes, e regerar os tópicos apaga as marcações junto (cascade),
    porque o texto lido deixou de existir.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leituras'
    )
    topico = models.ForeignKey(
        Topico, on_delete=models.CASCADE, related_name='leituras'
    )
    lido_em = models.DateTimeField('lido em', auto_now_add=True)

    class Meta:
        verbose_name = 'leitura de tópico'
        verbose_name_plural = 'leituras de tópicos'
        constraints = [
            models.UniqueConstraint(fields=['user', 'topico'], name='leitura_unica_por_topico'),
        ]

    def __str__(self):
        return f'{self.user} leu {self.topico.nome}'


class RespostaRevisao(models.Model):
    """Uma resposta do usuário a uma questão, durante a revisão.

    É um log de tentativas (sem unicidade): responder de novo cria outra
    linha, o que deixa medir a evolução do acerto ao longo do tempo. Some
    junto com a questão (cascade).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='respostas_revisao'
    )
    questao = models.ForeignKey(
        Questao, on_delete=models.CASCADE, related_name='respostas'
    )
    alternativa = models.CharField('alternativa escolhida', max_length=4)
    correta = models.BooleanField('acertou')
    respondido_em = models.DateTimeField('respondido em', auto_now_add=True)

    class Meta:
        verbose_name = 'resposta de revisão'
        verbose_name_plural = 'respostas de revisão'
        ordering = ['-respondido_em']

    def __str__(self):
        marca = 'acertou' if self.correta else 'errou'
        return f'{self.user} {marca} a questão {self.questao_id}'
