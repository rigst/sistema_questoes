"""
Integração com a API da Anthropic (Claude).

- Aplicação de prompts sobre questões (texto + imagens multimodais).
- Separação de questões via structured outputs (refino da extração).
- Envio em lote via Batches API (50% mais barato).
- Contabilização de tokens/custo e débito de quota.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

from django.conf import settings

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


def get_client():
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise IAError('ANTHROPIC_API_KEY não configurada.')
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def custo_usd(input_tokens, output_tokens):
    pin = Decimal(str(getattr(settings, 'AI_PRICE_INPUT_PER_MTOK', 3.0)))
    pout = Decimal(str(getattr(settings, 'AI_PRICE_OUTPUT_PER_MTOK', 15.0)))
    return (Decimal(input_tokens) / 1_000_000 * pin) + (Decimal(output_tokens) / 1_000_000 * pout)


def _blocos_imagens(questao):
    blocos = []
    for img in questao.imagens.all():
        try:
            with img.imagem.open('rb') as fh:
                data = base64.standard_b64encode(fh.read()).decode('utf-8')
            blocos.append({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': data},
            })
        except Exception:
            continue
    return blocos


def _texto_questao(questao, prompt_texto):
    partes = [prompt_texto.strip(), '', '--- QUESTÃO ---', questao.enunciado_md or '']
    if questao.gabarito:
        partes += ['', f'Gabarito informado: {questao.gabarito}']
    return '\n'.join(partes)


def montar_mensagens(questao, prompt_texto):
    """Monta a lista de mensagens (multimodal) para uma questão + prompt."""
    content = _blocos_imagens(questao)
    content.append({'type': 'text', 'text': _texto_questao(questao, prompt_texto)})
    return [{'role': 'user', 'content': content}]


def _params_mensagem(questao, prompt, cache_prompt=False):
    """Parâmetros para messages.create / batches (compartilhado)."""
    system = SYSTEM_PROMPT
    if cache_prompt:
        # Cacheia o prefixo estável (system) para lotes do mesmo prompt.
        system = [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}]
    return {
        'model': getattr(settings, 'AI_MODEL', 'claude-sonnet-4-6'),
        'max_tokens': getattr(settings, 'AI_MAX_TOKENS', 16000),
        'system': system,
        'messages': montar_mensagens(questao, prompt.texto),
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
    resultado.modelo = getattr(settings, 'AI_MODEL', 'claude-sonnet-4-6')
    resultado.save(update_fields=['status', 'modelo', 'atualizado_em'])

    try:
        client = get_client()
        params = _params_mensagem(questao, prompt)
        params['thinking'] = {'type': 'adaptive'}
        params['output_config'] = {'effort': getattr(settings, 'AI_EFFORT', 'medium')}
        resp = client.messages.create(**params)

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
        'quando houver. Texto:\n\n' + texto[:120000]
    )
    resp = client.messages.create(
        model=getattr(settings, 'AI_MODEL', 'claude-sonnet-4-6'),
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

    # Agrupa débito de quota por usuário (profile).
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
    return True
