from django.contrib import admin

from .models import ResultadoPrompt


@admin.register(ResultadoPrompt)
class ResultadoPromptAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'questao', 'prompt', 'status',
        'input_tokens', 'output_tokens', 'custo_estimado', 'criado_em',
    )
    list_filter = ('status', 'modelo')
    search_fields = ('resultado_md',)
