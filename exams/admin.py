from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Disciplina, Prova


class DisciplinaInline(TabularInline):
    model = Disciplina
    extra = 0


@admin.register(Prova)
class ProvaAdmin(ModelAdmin):
    list_display = ("nome", "user", "total_disciplinas", "criado_em")
    search_fields = ("nome", "user__username")
    inlines = [DisciplinaInline]


@admin.register(Disciplina)
class DisciplinaAdmin(ModelAdmin):
    list_display = ("nome", "prova", "ordem", "total_questoes")
    list_filter = ("prova",)
    search_fields = ("nome",)
