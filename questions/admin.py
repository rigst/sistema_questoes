from django.contrib import admin

from .models import ImportacaoPDF, Questao, QuestaoImagem


class QuestaoImagemInline(admin.TabularInline):
    model = QuestaoImagem
    extra = 0


@admin.register(ImportacaoPDF)
class ImportacaoPDFAdmin(admin.ModelAdmin):
    list_display = ('id', 'disciplina', 'status', 'num_questoes', 'confianca_media', 'usou_ia', 'criado_em')
    list_filter = ('status', 'usou_ia')


@admin.register(Questao)
class QuestaoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'disciplina', 'status', 'gabarito', 'confianca_extracao')
    list_filter = ('status', 'disciplina')
    search_fields = ('enunciado_md',)
    inlines = [QuestaoImagemInline]
