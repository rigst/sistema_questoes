from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Prompt


@admin.register(Prompt)
class PromptAdmin(ModelAdmin):
    list_display = ('nome', 'tipo', 'user', 'atualizado_em')
    list_filter = ('tipo',)
    search_fields = ('nome', 'texto')
