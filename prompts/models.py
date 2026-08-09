from django.conf import settings
from django.db import models


class Prompt(models.Model):
    """Prompt reutilizável aplicado sobre as questões via IA.

    Com ``user=None`` é um prompt padrão do sistema: aparece para todos os
    usuários (existentes e novos), sempre, e não pode ser editado/excluído.
    """

    class Tipo(models.TextChoices):
        COMPLETO = "completo", "Completo"
        SUCINTO = "sucinto", "Sucinto (revisão)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prompts",
        null=True,
        blank=True,
    )
    nome = models.CharField("nome", max_length=200)
    tipo = models.CharField("tipo", max_length=20, choices=Tipo.choices, default=Tipo.COMPLETO)
    texto = models.TextField(
        "texto do prompt",
        help_text="Instruções enviadas à IA. O enunciado e o gabarito da questão são anexados automaticamente.",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "prompt"
        verbose_name_plural = "prompts"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def padrao(self):
        return self.user_id is None

    @classmethod
    def visiveis_para(cls, user):
        """Prompts do usuário + padrões do sistema (padrões primeiro)."""
        return cls.objects.filter(models.Q(user=user) | models.Q(user__isnull=True)).order_by(
            models.F("user_id").asc(nulls_first=True), "nome"
        )
