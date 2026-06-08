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
    erro = models.TextField('mensagem de erro', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'importação de PDF'
        verbose_name_plural = 'importações de PDF'
        ordering = ['-criado_em']

    def __str__(self):
        return f'Importação #{self.pk} ({self.disciplina.nome})'


class Questao(models.Model):
    """Uma questão extraída de um PDF."""

    class Status(models.TextChoices):
        EM_REVISAO = 'em_revisao', 'Em revisão'
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
    numero = models.PositiveIntegerField('número', default=0)
    enunciado_md = models.TextField('enunciado (markdown)', blank=True)
    gabarito = models.CharField('gabarito', max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.EM_REVISAO)
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
