"""Prompt.user opcional + criação dos dois prompts padrão do sistema."""

from django.conf import settings
from django.db import migrations, models

NOME_COMPLETO = 'Explicação completa da questão'
NOME_SUCINTO = 'Revisão em um parágrafo'

TEXTO_COMPLETO = """\
Explique esta questão de concurso como um professor experiente da matéria, em cerca de uma página, usando Markdown com exatamente os títulos abaixo.

## Tema
Uma linha: o assunto cobrado e onde ele se encaixa dentro da disciplina.

## O essencial da matéria
Em 2 a 4 parágrafos, ensine a teoria necessária para resolver a questão: conceito, regra geral, exceções relevantes e a base normativa (artigos de lei, dispositivos da CF/88, súmulas e jurisprudência consolidada). Destaque em **negrito** os termos que costumam decidir a questão.

## Alternativas
Analise cada alternativa na ordem (A, B, C…). Comece cada uma com **Correta** ou **Incorreta** e explique o porquê em 1 a 3 frases, apontando o fundamento e, nas incorretas, o erro específico (troca de conceito, exceção ignorada, prazo errado etc.). Em questões de Certo/Errado, analise a assertiva única.

## Gabarito
Uma frase confirmando a alternativa correta e o raciocínio-chave para chegar a ela com segurança.

## Como isso cai em prova
Feche com 1 ou 2 dicas objetivas: variações do tema que as bancas cobram e as armadilhas a evitar.

Regras: fundamente com precisão e cite o dispositivo quando existir; não invente jurisprudência nem número de artigo — na dúvida, diga que o fundamento é a regra geral da matéria. Ignore pequenos defeitos de digitação vindos da extração do PDF. Se o gabarito informado parecer equivocado, siga-o na resposta, mas registre a divergência em uma nota final.
"""

TEXTO_SUCINTO = """\
Escreva um único parágrafo de revisão (4 a 7 linhas) sobre o ponto da matéria que esta questão cobra — como uma anotação de caderno para reler na véspera da prova. Vá direto à regra central e à exceção mais importante, termine indicando por que a alternativa do gabarito é a correta e feche com o fundamento entre parênteses, quando houver (ex.: art. 34, VII, da CF/88). Comece a resposta diretamente pela primeira palavra do parágrafo: sem título, sem cabeçalho "#", sem listas e sem negrito — apenas o parágrafo corrido. Não analise as alternativas uma a uma.
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
