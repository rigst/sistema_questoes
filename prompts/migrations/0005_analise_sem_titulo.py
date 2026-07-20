"""Reforça no prompt as duas regras que o modelo mais desobedecia.

Auditoria de 452 análises (Direito Processual Civil/ENAM): 58% saíram com
título Markdown e 3,8% abriam com "A questão trata de…", apesar de o prompt
já proibir ambos. As proibições estavam diluídas no fim do texto — aqui elas
sobem para o topo, com exemplo do formato esperado.
"""

from django.db import migrations

NOME_ANALISE = 'Análise da questão'

TEXTO_ANALISE = """\
A questão fornecida é apenas contexto: identifique o tema e o ponto testado \
e escreva uma nota de estudo ABSTRATA sobre a matéria, que faça sentido \
sozinha — sem mencionar a questão, o enunciado, as alternativas, letras ou \
o gabarito.

FORMATO OBRIGATÓRIO — exatamente DOIS parágrafos de texto corrido, nada mais:
- NÃO escreva título, cabeçalho ou linha iniciada por "#". Comece direto pela \
primeira palavra da matéria.
- NÃO use listas, tópicos, marcadores nem numeração.
- NÃO abra com fórmulas como "A questão trata de…", "O tema abordado é…" ou \
"Este item exige…". Comece pelo próprio conteúdo jurídico.

1º parágrafo — a matéria: a regra central do tema, como funciona na prática \
e a base normativa (artigo/súmula), incluindo a exceção relevante quando \
houver, com os termos decisivos em **negrito**.

2º parágrafo — os erros comuns e as pegadinhas: as confusões que as bancas \
exploram nesse tema (conceitos parecidos trocados, exceções ignoradas, \
prazos e competências semelhantes), formuladas de forma geral ("é comum \
confundir…", "não se deve…"), fechando com a pegadinha típica de prova.

Regras: escreva em português do Brasil, sem palavras em outro idioma; cite \
dispositivos com precisão e não invente jurisprudência nem número de artigo; \
nunca escreva "a questão", "o enunciado", "a alternativa", "o gabarito", \
"a assertiva" nem letras de alternativas; ignore defeitos de digitação da \
extração.
"""

TEXTO_ANTERIOR = """\
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


def reverter(apps, schema_editor):
    Prompt = apps.get_model('prompts', 'Prompt')
    Prompt.objects.filter(user__isnull=True, nome=NOME_ANALISE).update(texto=TEXTO_ANTERIOR)


class Migration(migrations.Migration):

    dependencies = [
        ('prompts', '0004_analise_abstrata'),
    ]

    operations = [
        migrations.RunPython(atualizar, reverter),
    ]
