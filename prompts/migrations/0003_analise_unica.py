"""Unifica os prompts padrão em uma única "Análise da questão" (dois parágrafos)."""

from django.db import migrations

NOME_ANALISE = 'Análise da questão'
NOME_COMPLETO_ANTIGO = 'Explicação completa da questão'
NOME_SUCINTO_ANTIGO = 'Revisão em um parágrafo'

TEXTO_ANALISE = """\
Analise a questão em DOIS parágrafos de Markdown, sem títulos nem listas:

1º parágrafo — a matéria e a resposta: explique a regra central do tema \
cobrado, com a base normativa (artigo/súmula), e por que a alternativa do \
gabarito é a correta.

2º parágrafo — os erros e as pegadinhas: percorra as alternativas erradas \
apontando o erro específico de cada uma (com a letra entre parênteses) e \
feche com a pegadinha típica da banca nesse tema.

Regras: cite dispositivos com precisão e não invente jurisprudência nem \
número de artigo; ignore defeitos de digitação da extração; se o gabarito \
parecer equivocado, siga-o e registre a divergência em nota final.
"""


def unificar(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    # O prompt "completo" vira a análise única (preserva resultados existentes).
    Prompt.objects.filter(user__isnull=True, nome=NOME_COMPLETO_ANTIGO).update(
        nome=NOME_ANALISE, tipo='completo', texto=TEXTO_ANALISE,
    )
    Prompt.objects.get_or_create(
        user=None, nome=NOME_ANALISE,
        defaults={'tipo': 'completo', 'texto': TEXTO_ANALISE},
    )
    # Remove o padrão de revisão (cascateia os resultados desse prompt).
    Prompt.objects.filter(user__isnull=True, nome=NOME_SUCINTO_ANTIGO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0002_prompts_padrao'),
    ]

    operations = [
        migrations.RunPython(unificar, migrations.RunPython.noop),
    ]
