from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Relatorio


@admin.register(Relatorio)
class RelatorioAdmin(ModelAdmin):
    list_display = ('titulo', 'tipo', 'user', 'prompt', 'com_texto', 'num_questoes', 'criado_em')
    list_filter = ('tipo', 'com_texto')
    search_fields = ('titulo',)
