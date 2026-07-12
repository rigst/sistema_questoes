"""Prompt.user opcional + criação dos dois prompts padrão do sistema."""

from django.conf import settings
from django.db import migrations, models

NOME_COMPLETO = 'Explicação completa da questão'
NOME_SUCINTO = 'Revisão em um parágrafo'

TEXTO_COMPLETO = """\
Explique a questão em Markdown com exatamente estes títulos:

## Tema
Uma linha: o assunto cobrado.

## O essencial da matéria
1 a 2 parágrafos objetivos: regra geral, exceção relevante e base normativa \
(artigo/súmula), com os termos decisivos em **negrito**.

## Alternativas
Cada alternativa na ordem, iniciando com **Correta** ou **Incorreta** e 1 a 2 \
frases com o fundamento (nas incorretas, o erro específico). Em Certo/Errado, \
analise a assertiva única.

## Gabarito
Uma frase: a alternativa correta e o raciocínio-chave.

## Dica de prova
Uma dica: variação que a banca cobra ou armadilha a evitar.

Regras: cite dispositivos com precisão e não invente jurisprudência nem número \
de artigo; ignore defeitos de digitação da extração; se o gabarito parecer \
equivocado, siga-o e registre a divergência em nota final.
"""

TEXTO_SUCINTO = """\
Escreva um único parágrafo de revisão (3 a 5 linhas) sobre o ponto que a \
questão cobra — anotação de véspera de prova: a regra central, por que a \
alternativa do gabarito é a correta, fechando com o fundamento entre \
parênteses. Comece direto pela primeira palavra: sem título, sem "#", sem \
listas, sem negrito. Não analise as alternativas uma a uma. Não invente \
jurisprudência; ignore defeitos de digitação da extração.
"""


def criar_prompts_padrao(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    Prompt.objects.get_or_create(
        user=None, nome=NOME_COMPLETO,
        defaults={'tipo': 'completo', 'texto': TEXTO_COMPLETO},
    )
    Prompt.objects.get_or_create(
        user=None, nome=NOME_SUCINTO,
        defaults={'tipo': 'sucinto', 'texto': TEXTO_SUCINTO},
    )


def remover_prompts_padrao(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    Prompt.objects.filter(user__isnull=True, nome__in=[NOME_COMPLETO, NOME_SUCINTO]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='prompt',
            name='user',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.CASCADE,
                related_name='prompts', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(criar_prompts_padrao, remover_prompts_padrao),
    ]
