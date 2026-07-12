"""Análise abstrata: nota de estudo sobre a matéria, sem referenciar a questão."""

from django.db import migrations

NOME_ANALISE = 'Análise da questão'

TEXTO_ANALISE = """\
A questão fornecida é apenas contexto: identifique o tema e o ponto testado \
e escreva uma nota de estudo ABSTRATA sobre a matéria, que faça sentido \
sozinha — sem mencionar a questão, o enunciado, as alternativas, letras ou \
o gabarito.

Escreva DOIS parágrafos de Markdown, sem títulos nem listas:

1º parágrafo — a matéria: a regra central do tema, como funciona na prática \
e a base normativa (artigo/súmula), incluindo a exceção relevante quando \
houver, com os termos decisivos em **negrito**.

2º parágrafo — os erros comuns e as pegadinhas: as confusões que as bancas \
exploram nesse tema (conceitos parecidos trocados, exceções ignoradas, \
prazos e competências semelhantes), formuladas de forma geral ("é comum \
confundir…", "não se deve…"), fechando com a pegadinha típica de prova.

Regras: cite dispositivos com precisão e não invente jurisprudência nem \
número de artigo; nunca escreva "a questão", "o enunciado", "a alternativa", \
"o gabarito" nem letras de alternativas; ignore defeitos de digitação da \
extração.
"""


def atualizar(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    Prompt.objects.filter(user__isnull=True, nome=NOME_ANALISE).update(texto=TEXTO_ANALISE)


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0003_analise_unica'),
    ]

    operations = [
        migrations.RunPython(atualizar, migrations.RunPython.noop),
    ]
