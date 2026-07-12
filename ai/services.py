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

