from django.contrib import admin

from .models import Relatorio


@admin.register(Relatorio)
class RelatorioAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'user', 'prompt', 'com_texto', 'num_questoes', 'criado_em')
    list_filter = ('com_texto',)
    search_fields = ('titulo',)
