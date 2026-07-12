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
import logging
import re
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# Estimativa de tokens (espelha o preview de custo do frontend em disciplina.html).
CHARS_PER_TOKEN = 3.8
SYSTEM_OVERHEAD_TOKENS = 55
OUTPUT_TOKENS_POR_TIPO = {'sucinto': 700, 'completo': 2000}

SYSTEM_PROMPT = (
    'Você é um tutor especialista em questões de concurso público. '
    'Responda sempre em português, de forma didática e em Markdown.'
)

# Marcadores usados para dividir a resposta combinada nos dois comentários.
MARCA_COMPLETO = '===COMENTARIO COMPLETO==='
MARCA_REVISAO = '===REVISAO==='

# Saída estimada da chamada combinada (medida no Sonnet 5, thinking incluso).
OUTPUT_TOKENS_PAR = 3000

# Prompt único que gera os dois comentários numa só chamada: a entrada
# (enunciado + imagens + instruções) é paga uma vez, e o app separa a saída
# pelos marcadores para montar os PDFs de explicações e de revisão.
PROMPT_COMBINADO = f"""\
Produza DOIS comentários sobre esta questão, na mesma resposta, separados \
exatamente pelos marcadores abaixo, cada um sozinho em sua própria linha:

{MARCA_COMPLETO}
(primeiro comentário)
{MARCA_REVISAO}
(segundo comentário)

No primeiro comentário, explique a questão como um professor experiente da \
matéria, em cerca de uma página, usando Markdown com exatamente estes títulos:

## Tema
Uma linha: o assunto cobrado e onde ele se encaixa dentro da disciplina.

## O essencial da matéria
Em 2 a 4 parágrafos, ensine a teoria necessária para resolver a questão: \
conceito, regra geral, exceções relevantes e a base normativa (artigos de lei, \
dispositivos da CF/88, súmulas e jurisprudência consolidada). Destaque em \
**negrito** os termos que costumam decidir a questão.

## Alternativas
Analise cada alternativa na ordem (A, B, C…). Comece cada uma com **Correta** \
ou **Incorreta** e explique o porquê em 1 a 3 frases, apontando o fundamento e, \
nas incorretas, o erro específico. Em questões de Certo/Errado, analise a \
assertiva única.

## Gabarito
Uma frase confirmando a alternativa correta e o raciocínio-chave.

## Como isso cai em prova
1 ou 2 dicas objetivas: variações que as bancas cobram e armadilhas a evitar.

No segundo comentário, escreva um único parágrafo de revisão (4 a 7 linhas) — \
como anotação de caderno para reler na véspera da prova: a regra central, a \
exceção mais importante, por que a alternativa do gabarito é a correta, \
fechando com o fundamento entre parênteses. Comece direto pela primeira \
palavra do parágrafo: sem título, sem listas, sem negrito.

Regras gerais: fundamente com precisão e cite o dispositivo quando existir; \
não invente jurisprudência nem número de artigo. Ignore pequenos defeitos de \
digitação vindos da extração do PDF. Se o gabarito informado parecer \
equivocado, siga-o, mas registre a divergência em nota ao final do primeiro \
comentário.
"""

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


# Tolerante a variações que o modelo produz: '===X===', '## X', '=== X'…
RE_MARCA_COMPLETO = re.compile(r'^\s*[=#]+\s*COMENT[ÁA]RIO[_ ]COMPLETO\s*=*\s*$', re.I | re.M)
RE_MARCA_REVISAO = re.compile(r'^\s*[=#]+\s*REVIS[ÃA]O\s*=*\s*$', re.I | re.M)


def dividir_comentarios(texto):
    """Separa a resposta combinada em (completo, revisão) pelos marcadores.

    Tolerante a variações de acento/espaçamento; sem o marcador de revisão,
    tudo vai para o completo e a revisão volta vazia.
    """
    partes = RE_MARCA_REVISAO.split(texto or '', maxsplit=1)
    completo = RE_MARCA_COMPLETO.sub('', partes[0]).strip()
    revisao = partes[1].strip() if len(partes) > 1 else ''
    return completo, revisao


def estimar_tokens_par(questoes):
    """Estimativa de tokens da geração combinada (entrada única + duas saídas)."""
    prompt_tokens = len(PROMPT_COMBINADO) / CHARS_PER_TOKEN
    total = 0
    for q in questoes:
        q_tokens = len(q.enunciado_md or '') / CHARS_PER_TOKEN
        total += SYSTEM_OVERHEAD_TOKENS + prompt_tokens + q_tokens + OUTPUT_TOKENS_PAR
    return int(total)


def _params_par(questao, cache_prompt=False):
    """Parâmetros da chamada combinada (com ou sem cache do prefixo p/ lotes)."""
    if cache_prompt:
        system = [
            {'type': 'text', 'text': SYSTEM_PROMPT},
            {'type': 'text', 'text': PROMPT_COMBINADO, 'cache_control': {'type': 'ephemeral'}},
        ]
        messages = montar_mensagens(questao)
    else:
        system = SYSTEM_PROMPT
        messages = montar_mensagens(questao, PROMPT_COMBINADO)
    params = {
        'model': getattr(settings, 'AI_MODEL', 'claude-sonnet-5'),
        'max_tokens': getattr(settings, 'AI_MAX_TOKENS', 16000),
        'system': system,
        'messages': messages,
    }
    if not cache_prompt and _suporta_adaptive(params['model']):
        params['thinking'] = {'type': 'adaptive'}
        params['output_config'] = {'effort': getattr(settings, 'AI_EFFORT', 'medium')}
    return params


def _gravar_par(res_completo, res_revisao, texto, it, ot, profile):
    """Divide e persiste o par; usage/custo ficam no completo (evita dupla conta)."""
    from .models import ResultadoPrompt

    completo, revisao = dividir_comentarios(texto)
    res_completo.resultado_md = completo
    res_completo.input_tokens = it
    res_completo.output_tokens = ot
    res_completo.custo_estimado = custo_usd(it, ot)
    res_completo.status = ResultadoPrompt.Status.CONCLUIDO
    res_completo.save()

    if revisao:
        res_revisao.resultado_md = revisao
        res_revisao.status = ResultadoPrompt.Status.CONCLUIDO
    else:
        res_revisao.status = ResultadoPrompt.Status.ERRO
        res_revisao.erro = 'Resposta veio sem o marcador de revisão.'
    res_revisao.save()

    if profile is not None:
        profile.registrar_uso(it, ot, res_completo.custo_estimado)

    questao = res_completo.questao
    questao.status = questao.Status.CONCLUIDA
    questao.save(update_fields=['status', 'atualizado_em'])


def aplicar_par_sincrono(res_completo, res_revisao, profile=None):
    """Gera os dois comentários de uma questão numa única chamada."""
    from .models import ResultadoPrompt

    modelo = getattr(settings, 'AI_MODEL', 'claude-sonnet-5')
    for r in (res_completo, res_revisao):
        r.status = ResultadoPrompt.Status.PROCESSANDO
        r.modelo = modelo
        r.save(update_fields=['status', 'modelo', 'atualizado_em'])

    try:
        client = get_client()
        resp = client.messages.create(**_params_par(res_completo.questao))
        texto = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        _gravar_par(res_completo, res_revisao, texto,
                    resp.usage.input_tokens, resp.usage.output_tokens, profile)
    except Exception as exc:  # noqa: BLE001
        for r in (res_completo, res_revisao):
            r.status = ResultadoPrompt.Status.ERRO
            r.erro = str(exc)[:2000]
            r.save(update_fields=['status', 'erro', 'atualizado_em'])
        questao = res_completo.questao
        questao.status = questao.Status.ERRO
        questao.save(update_fields=['status', 'atualizado_em'])
        raise


def submeter_batch_pares(pares):
    """Submete pares (completo, revisão) via Batches API. Retorna batch_id."""
    from .models import ResultadoPrompt

    client = get_client()
    requests = [
        {'custom_id': f'par-{rc.pk}-{rr.pk}', 'params': _params_par(rc.questao, cache_prompt=True)}
        for rc, rr in pares
    ]
    batch = client.messages.batches.create(requests=requests)
    todos = [r.pk for par in pares for r in par]
    ResultadoPrompt.objects.filter(pk__in=todos).update(
        status=ResultadoPrompt.Status.PROCESSANDO, batch_id=batch.id,
    )
    return batch.id


def estimar_tokens(questoes, prompt):
    """Estimativa (entrada + saída) do custo em tokens de aplicar `prompt` às questões."""
    out_tokens = OUTPUT_TOKENS_POR_TIPO.get(prompt.tipo, 2000)
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


def _texto_questao(questao, prompt_texto=None):
    partes = []
    if prompt_texto:
        partes += [prompt_texto.strip(), '']
    partes += ['--- QUESTÃO ---', questao.enunciado_md or '']
    if questao.gabarito:
        partes += ['', f'Gabarito informado: {questao.gabarito}']
    return '\n'.join(partes)


def montar_mensagens(questao, prompt_texto=None):
    """Monta a lista de mensagens (multimodal) para uma questão (+ prompt inline)."""
    content = _blocos_imagens(questao)
    content.append({'type': 'text', 'text': _texto_questao(questao, prompt_texto)})
    return [{'role': 'user', 'content': content}]


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
        if item.custom_id.startswith('par-'):
            _coletar_item_par(item)
            continue
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


def _coletar_item_par(item):
    """Processa um item de batch do fluxo combinado (custom_id 'par-<c>-<r>')."""
    from .models import ResultadoPrompt

    try:
        _, pk_c, pk_r = item.custom_id.split('-')
        res_completo = ResultadoPrompt.objects.select_related(
            'questao', 'questao__disciplina__prova__user'
        ).get(pk=int(pk_c))
        res_revisao = ResultadoPrompt.objects.get(pk=int(pk_r))
    except (ValueError, ResultadoPrompt.DoesNotExist):
        return

    if item.result.type == 'succeeded':
        msg = item.result.message
        texto = ''.join(b.text for b in msg.content if getattr(b, 'type', '') == 'text')
        profile = getattr(res_completo.questao.disciplina.prova.user, 'profile', None)
        _gravar_par(res_completo, res_revisao, texto,
                    msg.usage.input_tokens, msg.usage.output_tokens, profile)
    else:
        for r in (res_completo, res_revisao):
            r.status = ResultadoPrompt.Status.ERRO
            r.erro = f'Batch: {item.result.type}'
            r.save(update_fields=['status', 'erro', 'atualizado_em'])
        res_completo.questao.status = res_completo.questao.Status.ERRO
        res_completo.questao.save(update_fields=['status', 'atualizado_em'])
