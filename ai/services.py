"""
Integração com a API da Anthropic (Claude).

- Aplicação de prompts sobre questões (texto + imagens multimodais).
- Separação de questões via structured outputs (refino da extração).
- Envio em lote via Batches API (50% mais barato).
- Contabilização de tokens/custo e débito de quota.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# Estimativa de tokens (espelha o preview de custo do frontend em disciplina.html).
CHARS_PER_TOKEN = 3.8
SYSTEM_OVERHEAD_TOKENS = 55
OUTPUT_TOKENS_POR_TIPO = {'sucinto': 350, 'completo': 900}

SYSTEM_PROMPT = (
    'Você é um tutor especialista em questões de concurso público. '
    'Responda sempre em português, de forma didática e em Markdown.'
)

# Schema para separação de questões (refino por IA).
SCHEMA_QUESTOES = {
    'type': 'object',
    'properties': {
        'questoes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'numero': {'type': 'integer'},
                    'enunciado': {'type': 'string'},
                    'gabarito': {'type': 'string'},
                },
                'required': ['numero', 'enunciado', 'gabarito'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['questoes'],
    'additionalProperties': False,
}


# Schema para classificação das questões em tópicos.
SCHEMA_TOPICOS = {
    'type': 'object',
    'properties': {
        'topicos': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'nome': {'type': 'string'},
                    'descricao': {'type': 'string'},
                    'questoes': {'type': 'array', 'items': {'type': 'integer'}},
                },
                'required': ['nome', 'descricao', 'questoes'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['topicos'],
    'additionalProperties': False,
}


class IAError(Exception):
    pass


# Famílias de modelo que aceitam thinking adaptativo + output_config.effort
# (4.6+; Sonnet 4.5/Haiku 4.5 rejeitam ambos com 400).
_FAMILIAS_ADAPTIVE = ('opus-4-6', 'opus-4-7', 'opus-4-8', 'sonnet-4-6', 'sonnet-5', 'fable', 'mythos')


def _suporta_adaptive(modelo):
    return any(f in modelo for f in _FAMILIAS_ADAPTIVE)


def _params_aplicacao(questao, prompt):
    """Parâmetros completos do envio síncrono, ajustados ao modelo configurado."""
    params = _params_mensagem(questao, prompt)
    if _suporta_adaptive(params['model']):
        params['thinking'] = {'type': 'adaptive'}
        params['output_config'] = {'effort': getattr(settings, 'AI_EFFORT', 'medium')}
    return params


def get_client():
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise IAError('ANTHROPIC_API_KEY não configurada.')
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def estimar_tokens(questoes, prompt):
    """Estimativa (entrada + saída) do custo em tokens de aplicar `prompt` às questões."""
    out_tokens = OUTPUT_TOKENS_POR_TIPO.get(prompt.tipo, 900)
    prompt_tokens = len(prompt.texto or '') / CHARS_PER_TOKEN
    total = 0
    for q in questoes:
        q_tokens = len(q.enunciado_md or '') / CHARS_PER_TOKEN
        total += SYSTEM_OVERHEAD_TOKENS + prompt_tokens + q_tokens + out_tokens
    return int(total)


def custo_usd(input_tokens, output_tokens):
    pin = Decimal(str(getattr(settings, 'AI_PRICE_INPUT_PER_MTOK', 3.0)))
    pout = Decimal(str(getattr(settings, 'AI_PRICE_OUTPUT_PER_MTOK', 15.0)))
    return (Decimal(input_tokens) / 1_000_000 * pin) + (Decimal(output_tokens) / 1_000_000 * pout)


def formatar_custo_usd(valor):
    """Formata um valor em USD no mesmo padrão usado nas prévias de custo da UI."""
    if valor < Decimal('0.0005'):
        return '< $0.001'
    casas = 4 if valor < Decimal('0.01') else 3
    return f'${valor:.{casas}f}'


def _texto_questao(questao, prompt_texto=None):
    partes = []
    if prompt_texto:
        partes += [prompt_texto.strip(), '']
    partes += ['--- QUESTÃO ---', questao.enunciado_md or '']
    if questao.gabarito:
        partes += ['', f'Gabarito informado: {questao.gabarito}']
    return '\n'.join(partes)


def montar_mensagens(questao, prompt_texto=None):
    """Monta a mensagem de uma questão (+ prompt inline). Só texto: os recortes
    de imagem da extração eram a página inteira/marca-d'água e inflavam a
    entrada sem agregar conteúdo."""
    return [{'role': 'user', 'content': [
        {'type': 'text', 'text': _texto_questao(questao, prompt_texto)},
    ]}]


def _params_mensagem(questao, prompt, cache_prompt=False):
    """Parâmetros para messages.create / batches (compartilhado).

    Com `cache_prompt`, o texto do prompt migra da mensagem do usuário para o
    bloco system marcado com cache_control: em lotes, o prefixo estável
    (system + prompt) é idêntico em todas as requisições e pode ser cacheado
    (efetivo quando o prompt é longo o bastante para o mínimo cacheável).
    """
    if cache_prompt:
        system = [
            {'type': 'text', 'text': SYSTEM_PROMPT},
            {
                'type': 'text',
                'text': 'Instruções do usuário:\n' + (prompt.texto or '').strip(),
                'cache_control': {'type': 'ephemeral'},
            },
        ]
        messages = montar_mensagens(questao)
    else:
        system = SYSTEM_PROMPT
        messages = montar_mensagens(questao, prompt.texto)
    return {
        'model': getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
        'max_tokens': getattr(settings, 'AI_MAX_TOKENS', 16000),
        'system': system,
        'messages': messages,
    }


# ---------------------------------------------------------------------------
# Aplicação síncrona (envio único)
# ---------------------------------------------------------------------------

def aplicar_resultado_sincrono(resultado, profile=None):
    """Executa um ResultadoPrompt via messages.create e grava o resultado."""
    from .models import ResultadoPrompt

    questao = resultado.questao
    prompt = resultado.prompt
    resultado.status = ResultadoPrompt.Status.PROCESSANDO
    resultado.modelo = getattr(settings, 'AI_MODEL', 'claude-sonnet-5')
    resultado.save(update_fields=['status', 'modelo', 'atualizado_em'])

    try:
        client = get_client()
        resp = client.messages.create(**_params_aplicacao(questao, prompt))

        texto = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        it = resp.usage.input_tokens
        ot = resp.usage.output_tokens
        resultado.resultado_md = texto
        resultado.input_tokens = it
        resultado.output_tokens = ot
        resultado.custo_estimado = custo_usd(it, ot)
        resultado.status = ResultadoPrompt.Status.CONCLUIDO
        resultado.save()

        if profile is not None:
            profile.registrar_uso(it, ot, resultado.custo_estimado)

        questao.status = questao.Status.CONCLUIDA
        questao.save(update_fields=['status', 'atualizado_em'])
        return resultado
    except Exception as exc:  # noqa: BLE001
        resultado.status = ResultadoPrompt.Status.ERRO
        resultado.erro = str(exc)[:2000]
        resultado.save(update_fields=['status', 'erro', 'atualizado_em'])
        # Tira a questão da fila para o polling/badges não ficarem presos.
        questao.status = questao.Status.ERRO
        questao.save(update_fields=['status', 'atualizado_em'])
        raise


# ---------------------------------------------------------------------------
# Separação de questões via IA (refino da extração)
# ---------------------------------------------------------------------------

def separar_questoes_via_ia(texto, profile=None):
    """Usa structured outputs para separar questões do texto bruto."""
    client = get_client()
    instrucao = (
        'Separe as questões do texto a seguir. Para cada questão, retorne o '
        'número, o enunciado completo (com alternativas) e o gabarito (letra) '
        'quando houver. O texto vem de extração de PDF e pode ter defeitos: '
        'palavras com ligaduras perdidas (ex.: "signicado" → "significado", '
        '"deagração" → "deflagração") e quebras de linha no meio de frases — '
        'corrija-os ao transcrever, sem alterar o conteúdo. Texto:\n\n' + texto[:120000]
    )
    resp = client.messages.create(
        model=getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
        max_tokens=getattr(settings, 'AI_MAX_TOKENS', 16000),
        system='Você extrai questões de provas de concurso de forma estruturada.',
        messages=[{'role': 'user', 'content': instrucao}],
        output_config={'format': {'type': 'json_schema', 'schema': SCHEMA_QUESTOES}},
    )
    texto_json = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
    if profile is not None:
        profile.registrar_uso(
            resp.usage.input_tokens, resp.usage.output_tokens,
            custo_usd(resp.usage.input_tokens, resp.usage.output_tokens),
        )
    try:
        data = json.loads(texto_json)
        return data.get('questoes', [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Lote (Batches API)
# ---------------------------------------------------------------------------

def submeter_batch(resultados):
    """Submete um lote de ResultadoPrompt via Batches API. Retorna batch_id."""
    from .models import ResultadoPrompt

    client = get_client()
    requests = []
    for r in resultados:
        params = _params_mensagem(r.questao, r.prompt, cache_prompt=True)
        requests.append({'custom_id': f'res-{r.pk}', 'params': params})

    batch = client.messages.batches.create(requests=requests)
    ResultadoPrompt.objects.filter(pk__in=[r.pk for r in resultados]).update(
        status=ResultadoPrompt.Status.PROCESSANDO, batch_id=batch.id,
    )
    return batch.id


def coletar_batch(batch_id):
    """Coleta os resultados de um batch concluído. Retorna True se finalizado."""
    from .models import ResultadoPrompt

    client = get_client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != 'ended':
        return False

    for item in client.messages.batches.results(batch_id):
        try:
            res_id = int(item.custom_id.split('-', 1)[1])
            resultado = ResultadoPrompt.objects.select_related(
                'questao', 'questao__disciplina__prova__user'
            ).get(pk=res_id)
        except (ValueError, ResultadoPrompt.DoesNotExist):
            continue

        if item.result.type == 'succeeded':
            msg = item.result.message
            texto = ''.join(b.text for b in msg.content if getattr(b, 'type', '') == 'text')
            it = msg.usage.input_tokens
            ot = msg.usage.output_tokens
            resultado.resultado_md = texto
            resultado.input_tokens = it
            resultado.output_tokens = ot
            resultado.custo_estimado = custo_usd(it, ot)
            resultado.status = ResultadoPrompt.Status.CONCLUIDO
            resultado.save()
            profile = getattr(resultado.questao.disciplina.prova.user, 'profile', None)
            if profile is not None:
                profile.registrar_uso(it, ot, resultado.custo_estimado)
            resultado.questao.status = resultado.questao.Status.CONCLUIDA
            resultado.questao.save(update_fields=['status', 'atualizado_em'])
        else:
            resultado.status = ResultadoPrompt.Status.ERRO
            resultado.erro = f'Batch: {item.result.type}'
            resultado.save(update_fields=['status', 'erro', 'atualizado_em'])
            resultado.questao.status = resultado.questao.Status.ERRO
            resultado.questao.save(update_fields=['status', 'atualizado_em'])
    return True


# ---------------------------------------------------------------------------
# Tópicos: classificação das questões + síntese de texto de estudo por tópico
# ---------------------------------------------------------------------------

# Estimativas de saída (tokens) usadas na quota.
OUTPUT_TOKENS_CLASSIFICACAO = 2500
OUTPUT_TOKENS_SINTESE = 2500

# Caracteres de cada enunciado enviados na classificação (o começo do
# enunciado basta para identificar o tema; limita o custo em PDFs grandes).
CLASSIFICACAO_CHARS_POR_QUESTAO = 1200

PROMPT_SINTESE_TOPICO = """\
Você receberá as questões de um mesmo tópico de estudo e as análises geradas \
para cada uma. Escreva um TEXTO DE ESTUDO COESO sobre o tópico, em Markdown, \
que substitua a leitura das análises individuais.

Regras de cobertura (as mais importantes):
- Incorpore TODAS as informações relevantes de TODAS as análises e questões: \
regras, conceitos, bases normativas (artigos/súmulas), exceções, prazos, \
competências, erros comuns e pegadinhas. Nenhum ponto que apareça em apenas \
uma questão pode ficar de fora do texto.
- Qualificadores normativos são informação relevante e devem ser preservados \
com exatidão: exceções, formas alternativas de conduta (ex.: "dolosa OU por \
grave omissão"), prazos, restrições setoriais e ressalvas.
- Se as análises tratarem de institutos, regimes ou dispositivos DISTINTOS — \
ainda que semelhantes ou paralelos, como as versões federal e estadual de um \
mesmo instituto —, apresente cada um em seção própria, com seus atores e \
dispositivos corretos, e destaque as diferenças. NUNCA funda institutos \
diferentes num só, mesmo que o nome do tópico sugira apenas um deles.
- Informação repetida em várias questões entra UMA única vez, sem redundância.
- Antes de finalizar, confira questão por questão se algum ponto exclusivo \
ficou de fora ou foi atribuído ao instituto errado; se sim, corrija.

Estrutura:
- Organize por subtemas com títulos "##"/"###" quando ajudar; comece pela \
regra geral do tema, depois os detalhes e as exceções.
- Termine com a seção "## Erros comuns e pegadinhas", consolidando as \
armadilhas de prova identificadas em todas as questões.
- Destaque os termos decisivos em **negrito**.

Regras de conteúdo:
- Texto autônomo de estudo: não mencione "a questão", números de questões, \
alternativas nem gabaritos.
- Cite dispositivos legais com precisão e não invente jurisprudência nem \
número de artigo — use apenas o que estiver nas análises ou for de seu \
conhecimento seguro.
- Ignore defeitos de digitação vindos da extração do PDF.
"""


def classificar_topicos_via_ia(questoes, profile=None):
    """Agrupa as questões em tópicos de estudo (uma única chamada, structured
    outputs). Retorna a lista `[{nome, descricao, questoes: [ids]}, ...]`."""
    client = get_client()
    linhas = []
    for q in questoes:
        enunciado = (q.enunciado_md or '').strip()[:CLASSIFICACAO_CHARS_POR_QUESTAO]
        linhas.append(f'[ID {q.pk}] {enunciado}')
    instrucao = (
        'Agrupe as questões de concurso abaixo em tópicos de estudo da mesma '
        'disciplina. Para cada tópico, retorne um nome curto (2 a 6 palavras), '
        'uma descrição de uma frase e a lista de IDs das questões que testam '
        'aquele tema. Crie quantos tópicos forem necessários (tipicamente '
        'entre 5 e 30): questões que testam o mesmo tema ficam juntas, e cada '
        'ID deve aparecer em exatamente um tópico — nenhum ID pode ficar de '
        'fora. O nome do tópico deve abranger TODAS as questões do grupo: se '
        'agrupar institutos paralelos (ex.: intervenção federal e estadual), '
        'use um nome que cubra ambos, sem privilegiar um deles. Ordene os '
        'tópicos na sequência didática natural da disciplina.'
        '\n\nQuestões:\n\n' + '\n\n'.join(linhas)
    )
    resp = client.messages.create(
        model=getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
        max_tokens=getattr(settings, 'AI_MAX_TOKENS', 16000),
        system='Você organiza questões de provas de concurso em tópicos de estudo.',
        messages=[{'role': 'user', 'content': instrucao}],
        output_config={'format': {'type': 'json_schema', 'schema': SCHEMA_TOPICOS}},
    )
    texto_json = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
    if profile is not None:
        profile.registrar_uso(
            resp.usage.input_tokens, resp.usage.output_tokens,
            custo_usd(resp.usage.input_tokens, resp.usage.output_tokens),
        )
    data = json.loads(texto_json)
    return data.get('topicos', [])


def montar_conteudo_topico(topico):
    """Monta o material do tópico: enunciado, gabarito e análises concluídas
    de cada questão — a matéria-prima do texto coeso."""
    from .models import ResultadoPrompt

    partes = [f'TÓPICO: {topico.nome}']
    if topico.descricao:
        partes.append(topico.descricao)
    for i, q in enumerate(topico.questoes.all(), start=1):
        partes += ['', f'=== QUESTÃO {i} ===', (q.enunciado_md or '').strip()]
        if q.gabarito:
            partes.append(f'Gabarito: {q.gabarito}')
        analises = q.resultados.filter(
            status=ResultadoPrompt.Status.CONCLUIDO
        ).exclude(resultado_md='').order_by('criado_em')
        for r in analises:
            partes += [f'--- Análise ({r.prompt.nome}) ---', r.resultado_md.strip()]
    return '\n'.join(partes)


def _params_sintese(topico, cache_prompt=False):
    """Parâmetros para a síntese do texto do tópico (sync ou batch).

    Com `cache_prompt`, as instruções vão para o bloco system com
    cache_control — o prefixo é idêntico em todos os tópicos do lote.
    """
    conteudo = montar_conteudo_topico(topico)
    if cache_prompt:
        system = [
            {'type': 'text', 'text': SYSTEM_PROMPT},
            {
                'type': 'text',
                'text': 'Instruções:\n' + PROMPT_SINTESE_TOPICO,
                'cache_control': {'type': 'ephemeral'},
            },
        ]
        messages = [{'role': 'user', 'content': conteudo}]
    else:
        system = SYSTEM_PROMPT
        messages = [{'role': 'user', 'content': PROMPT_SINTESE_TOPICO + '\n\n' + conteudo}]
    return {
        'model': getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
        'max_tokens': getattr(settings, 'AI_MAX_TOKENS', 16000),
        'system': system,
        'messages': messages,
    }


def sintetizar_texto_sincrono(texto, profile=None):
    """Executa um TextoTopico via messages.create e grava o resultado."""
    from .models import TextoTopico

    texto.status = TextoTopico.Status.PROCESSANDO
    texto.modelo = getattr(settings, 'AI_MODEL', 'claude-sonnet-5')
    texto.save(update_fields=['status', 'modelo', 'atualizado_em'])

    try:
        params = _params_sintese(texto.topico)
        if _suporta_adaptive(params['model']):
            params['thinking'] = {'type': 'adaptive'}
            params['output_config'] = {'effort': getattr(settings, 'AI_EFFORT', 'medium')}
        client = get_client()
        resp = client.messages.create(**params)

        corpo = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        it = resp.usage.input_tokens
        ot = resp.usage.output_tokens
        texto.texto_md = corpo
        texto.input_tokens = it
        texto.output_tokens = ot
        texto.custo_estimado = custo_usd(it, ot)
        texto.status = TextoTopico.Status.CONCLUIDO
        texto.save()

        if profile is not None:
            profile.registrar_uso(it, ot, texto.custo_estimado)
        return texto
    except Exception as exc:  # noqa: BLE001
        texto.status = TextoTopico.Status.ERRO
        texto.erro = str(exc)[:2000]
        texto.save(update_fields=['status', 'erro', 'atualizado_em'])
        raise


def submeter_batch_textos(textos):
    """Submete um lote de TextoTopico via Batches API. Retorna batch_id."""
    from .models import TextoTopico

    client = get_client()
    requests = []
    for t in textos:
        params = _params_sintese(t.topico, cache_prompt=True)
        requests.append({'custom_id': f'top-{t.pk}', 'params': params})

    batch = client.messages.batches.create(requests=requests)
    TextoTopico.objects.filter(pk__in=[t.pk for t in textos]).update(
        status=TextoTopico.Status.PROCESSANDO, batch_id=batch.id,
        modelo=getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
    )
    return batch.id


def coletar_batch_textos(batch_id):
    """Coleta os textos de um batch concluído. Retorna True se finalizado."""
    from .models import TextoTopico

    client = get_client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != 'ended':
        return False

    for item in client.messages.batches.results(batch_id):
        try:
            texto_id = int(item.custom_id.split('-', 1)[1])
            texto = TextoTopico.objects.select_related(
                'topico__disciplina__prova__user'
            ).get(pk=texto_id)
        except (ValueError, TextoTopico.DoesNotExist):
            continue

        if item.result.type == 'succeeded':
            msg = item.result.message
            corpo = ''.join(b.text for b in msg.content if getattr(b, 'type', '') == 'text')
            it = msg.usage.input_tokens
            ot = msg.usage.output_tokens
            texto.texto_md = corpo
            texto.input_tokens = it
            texto.output_tokens = ot
            texto.custo_estimado = custo_usd(it, ot)
            texto.status = TextoTopico.Status.CONCLUIDO
            texto.save()
            profile = getattr(texto.topico.disciplina.prova.user, 'profile', None)
            if profile is not None:
                profile.registrar_uso(it, ot, texto.custo_estimado)
        else:
            texto.status = TextoTopico.Status.ERRO
            texto.erro = f'Batch: {item.result.type}'
            texto.save(update_fields=['status', 'erro', 'atualizado_em'])
    return True


def estimar_tokens_topicos(questoes):
    """Estimativa (entrada + saída) do fluxo completo de tópicos:
    classificação + síntese de todos os textos."""
    from .models import ResultadoPrompt

    n = len(questoes)
    if not n:
        return 0
    chars_q = sum(len(q.enunciado_md or '') for q in questoes)
    chars_class = sum(
        min(len(q.enunciado_md or ''), CLASSIFICACAO_CHARS_POR_QUESTAO) for q in questoes
    )
    chars_analises = sum(
        len(md) for md in ResultadoPrompt.objects.filter(
            questao__in=[q.pk for q in questoes],
            status=ResultadoPrompt.Status.CONCLUIDO,
        ).values_list('resultado_md', flat=True)
    )
    n_topicos = max(3, min(30, n // 8 + 1))
    classificacao = chars_class / CHARS_PER_TOKEN + OUTPUT_TOKENS_CLASSIFICACAO
    sintese_in = (chars_q + chars_analises) / CHARS_PER_TOKEN + n_topicos * 400
    sintese_out = n_topicos * OUTPUT_TOKENS_SINTESE
    return int(classificacao + sintese_in + sintese_out)


def estimar_custo_topicos(questoes):
    """Estimativa de custo (USD) do fluxo completo de tópicos: a classificação
    é uma chamada síncrona (preço cheio); a síntese dos textos sai pela
    Batches API (desconto de 50%) quando há mais de um tópico."""
    from .models import ResultadoPrompt

    n = len(questoes)
    if not n:
        return Decimal('0')
    chars_q = sum(len(q.enunciado_md or '') for q in questoes)
    chars_class = sum(
        min(len(q.enunciado_md or ''), CLASSIFICACAO_CHARS_POR_QUESTAO) for q in questoes
    )
    chars_analises = sum(
        len(md) for md in ResultadoPrompt.objects.filter(
            questao__in=[q.pk for q in questoes],
            status=ResultadoPrompt.Status.CONCLUIDO,
        ).values_list('resultado_md', flat=True)
    )
    n_topicos = max(3, min(30, n // 8 + 1))

    class_in = chars_class / CHARS_PER_TOKEN
    sintese_in = (chars_q + chars_analises) / CHARS_PER_TOKEN + n_topicos * 400
    sintese_out = n_topicos * OUTPUT_TOKENS_SINTESE

    desconto_sintese = Decimal('0.5') if n_topicos > 1 else Decimal('1')
    return (
        custo_usd(int(class_in), OUTPUT_TOKENS_CLASSIFICACAO)
        + custo_usd(int(sintese_in), int(sintese_out)) * desconto_sintese
    )

