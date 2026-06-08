from django.contrib import admin

from .models import Disciplina, Prova


class DisciplinaInline(admin.TabularInline):
    model = Disciplina
    extra = 0


@admin.register(Prova)
class ProvaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'user', 'total_disciplinas', 'criado_em')
    search_fields = ('nome', 'user__username')
    inlines = [DisciplinaInline]


@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'prova', 'ordem', 'total_questoes')
    list_filter = ('prova',)
    search_fields = ('nome',)
