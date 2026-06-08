from django.contrib import admin

from .models import Prompt


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ('nome', 'tipo', 'user', 'atualizado_em')
    list_filter = ('tipo',)
    search_fields = ('nome', 'texto')
