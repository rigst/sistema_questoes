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
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

# Estimativa de tokens (espelha o preview de custo do frontend em disciplina.html).
CHARS_PER_TOKEN = 3.8
SYSTEM_OVERHEAD_TOKENS = 55
OUTPUT_TOKENS_POR_TIPO = {"sucinto": 350, "completo": 900}

SYSTEM_PROMPT = (
    "Você é um tutor especialista em questões de concurso público. "
    "Responda sempre em português, de forma didática e em Markdown."
)

# Schema para separação de questões (refino por IA).
SCHEMA_QUESTOES = {
    "type": "object",
    "properties": {
        "questoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "numero": {"type": "integer"},
                    "enunciado": {"type": "string"},
                    "gabarito": {"type": "string"},
                },
                "required": ["numero", "enunciado", "gabarito"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questoes"],
    "additionalProperties": False,
}


# Schema para classificação das questões em tópicos.
SCHEMA_TOPICOS = {
    "type": "object",
    "properties": {
        "topicos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "descricao": {"type": "string"},
                    "questoes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["nome", "descricao", "questoes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topicos"],
    "additionalProperties": False,
}

# Passe 1 da classificação: só os nomes dos tópicos, sem IDs.
SCHEMA_TOPICOS_NOMES = {
    "type": "object",
    "properties": {
        "topicos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "descricao": {"type": "string"},
                },
                "required": ["nome", "descricao"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topicos"],
    "additionalProperties": False,
}

# Passe 3: fusão de tópicos redundantes ou pequenos demais.
SCHEMA_FUSOES = {
    "type": "object",
    "properties": {
        "fusoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "de": {"type": "integer"},
                    "para": {"type": "integer"},
                },
                "required": ["de", "para"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fusoes"],
    "additionalProperties": False,
}

# Passe 2: cada questão recebe o índice do tópico a que pertence.
SCHEMA_ATRIBUICOES = {
    "type": "object",
    "properties": {
        "atribuicoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "topico": {"type": "integer"},
                },
                "required": ["id", "topico"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["atribuicoes"],
    "additionalProperties": False,
}


class IAError(Exception):
    pass


# Famílias de modelo que aceitam thinking adaptativo + output_config.effort
# (4.6+; Sonnet 4.5/Haiku 4.5 rejeitam ambos com 400).
_FAMILIAS_ADAPTIVE = (
    "opus-4-6",
    "opus-4-7",
    "opus-4-8",
    "sonnet-4-6",
    "sonnet-5",
    "fable",
    "mythos",
)


def _suporta_adaptive(modelo):
    return any(f in modelo for f in _FAMILIAS_ADAPTIVE)


def _params_aplicacao(questao, prompt):
    """Parâmetros completos do envio síncrono, ajustados ao modelo configurado."""
    params = _params_mensagem(questao, prompt)
    if _suporta_adaptive(params["model"]):
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": getattr(settings, "AI_EFFORT", "medium")}
    return params


def get_client():
    api_key = getattr(settings, "ANTHROPIC_API_KEY", "")
    if not api_key:
        raise IAError("ANTHROPIC_API_KEY não configurada.")
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _criar_mensagem(client, params):
    """Equivalente a client.messages.create(**params), mas via streaming.

    Chamadas com max_tokens alto (tópicos/disciplinas grandes, thinking
    adaptativo) podem passar de 10 minutos, e o SDK exige streaming nesse
    caso — ver https://github.com/anthropics/anthropic-sdk-python#long-requests.
    stream.get_final_message() devolve o mesmo objeto Message de sempre.
    """
    with client.messages.stream(**params) as stream:
        return stream.get_final_message()


def estimar_tokens(questoes, prompt):
    """Estimativa (entrada + saída) do custo em tokens de aplicar `prompt` às questões."""
    out_tokens = OUTPUT_TOKENS_POR_TIPO.get(prompt.tipo, 900)
    prompt_tokens = len(prompt.texto or "") / CHARS_PER_TOKEN
    total = 0
    for q in questoes:
        q_tokens = len(q.enunciado_md or "") / CHARS_PER_TOKEN
        total += SYSTEM_OVERHEAD_TOKENS + prompt_tokens + q_tokens + out_tokens
    return int(total)


# Teto de gastos de uma operação (análise em lote, tópicos): a previsão
# mostrada ao usuário antes de confirmar, com 50% de margem. Ultrapassado
# isso, o processamento para e entrega o que já foi concluído — os itens
# restantes ficam marcados como erro (e não presos "gerando…" para sempre).
MARGEM_TETO_OPERACAO = 1.5


# A Batches API cobra metade do preço; a usage devolvida vem em tokens
# brutos, então o desconto precisa ser aplicado ao converter em dinheiro.
DESCONTO_BATCH = Decimal("0.5")


def custo_usd(input_tokens, output_tokens, lote=False):
    pin = Decimal(str(getattr(settings, "AI_PRICE_INPUT_PER_MTOK", 3.0)))
    pout = Decimal(str(getattr(settings, "AI_PRICE_OUTPUT_PER_MTOK", 15.0)))
    bruto = (Decimal(input_tokens) / 1_000_000 * pin) + (Decimal(output_tokens) / 1_000_000 * pout)
    return bruto * DESCONTO_BATCH if lote else bruto


def formatar_custo_usd(valor):
    """Formata um valor em USD no mesmo padrão usado nas prévias de custo da UI."""
    if valor < Decimal("0.0005"):
        return "< $0.001"
    casas = 4 if valor < Decimal("0.01") else 3
    return f"${valor:.{casas}f}"


def _texto_questao(questao, prompt_texto=None):
    partes = []
    if prompt_texto:
        partes += [prompt_texto.strip(), ""]
    partes += ["--- QUESTÃO ---", questao.enunciado_md or ""]
    if questao.gabarito:
        partes += ["", f"Gabarito informado: {questao.gabarito}"]
    return "\n".join(partes)


def montar_mensagens(questao, prompt_texto=None):
    """Monta a mensagem de uma questão (+ prompt inline). Só texto: os recortes
    de imagem da extração eram a página inteira/marca-d'água e inflavam a
    entrada sem agregar conteúdo."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _texto_questao(questao, prompt_texto)},
            ],
        }
    ]


def _params_mensagem(questao, prompt, cache_prompt=False):
    """Parâmetros para messages.create / batches (compartilhado).

    Com `cache_prompt`, o texto do prompt migra da mensagem do usuário para o
    bloco system marcado com cache_control: em lotes, o prefixo estável
    (system + prompt) é idêntico em todas as requisições e pode ser cacheado
    (efetivo quando o prompt é longo o bastante para o mínimo cacheável).
    """
    if cache_prompt:
        system = [
            {"type": "text", "text": SYSTEM_PROMPT},
            {
                "type": "text",
                "text": "Instruções do usuário:\n" + (prompt.texto or "").strip(),
                "cache_control": {"type": "ephemeral"},
            },
        ]
        messages = montar_mensagens(questao)
    else:
        system = SYSTEM_PROMPT
        messages = montar_mensagens(questao, prompt.texto)
    return {
        "model": getattr(settings, "AI_MODEL", "claude-sonnet-5"),
        "max_tokens": getattr(settings, "AI_MAX_TOKENS", 16000),
        "system": system,
        "messages": messages,
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
    resultado.modelo = getattr(settings, "AI_MODEL", "claude-sonnet-5")
    resultado.save(update_fields=["status", "modelo", "atualizado_em"])

    try:
        client = get_client()
        resp = _criar_mensagem(client, _params_aplicacao(questao, prompt))

        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
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
        questao.save(update_fields=["status", "atualizado_em"])
        return resultado
    except Exception as exc:
        resultado.status = ResultadoPrompt.Status.ERRO
        resultado.erro = str(exc)[:2000]
        resultado.save(update_fields=["status", "erro", "atualizado_em"])
        # Tira a questão da fila para o polling/badges não ficarem presos.
        questao.status = questao.Status.ERRO
        questao.save(update_fields=["status", "atualizado_em"])
        raise


# ---------------------------------------------------------------------------
# Separação de questões via IA (refino da extração)
# ---------------------------------------------------------------------------


def separar_questoes_via_ia(texto, profile=None):
    """Usa structured outputs para separar questões do texto bruto."""
    client = get_client()
    instrucao = (
        "Separe as questões do texto a seguir. Para cada questão, retorne o "
        "número, o enunciado completo (com alternativas) e o gabarito (letra) "
        "quando houver. O texto vem de extração de PDF e pode ter defeitos: "
        'palavras com ligaduras perdidas (ex.: "signicado" → "significado", '
        '"deagração" → "deflagração") e quebras de linha no meio de frases — '
        "corrija-os ao transcrever, sem alterar o conteúdo. Texto:\n\n" + texto[:120000]
    )
    resp = _criar_mensagem(
        client,
        {
            "model": getattr(settings, "AI_MODEL", "claude-sonnet-5"),
            "max_tokens": getattr(settings, "AI_MAX_TOKENS", 16000),
            "system": "Você extrai questões de provas de concurso de forma estruturada.",
            "messages": [{"role": "user", "content": instrucao}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA_QUESTOES}},
        },
    )
    texto_json = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if profile is not None:
        profile.registrar_uso(
            resp.usage.input_tokens,
            resp.usage.output_tokens,
            custo_usd(resp.usage.input_tokens, resp.usage.output_tokens),
        )
    try:
        data = json.loads(texto_json)
        return data.get("questoes", [])
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
        requests.append({"custom_id": f"res-{r.pk}", "params": params})

    batch = client.messages.batches.create(requests=requests)
    ResultadoPrompt.objects.filter(pk__in=[r.pk for r in resultados]).update(
        status=ResultadoPrompt.Status.PROCESSANDO,
        batch_id=batch.id,
        modelo=getattr(settings, "AI_MODEL", "claude-sonnet-5"),
    )
    return batch.id


def coletar_batch(batch_id):
    """Coleta os resultados de um batch concluído. Retorna True se finalizado."""
    from .models import ResultadoPrompt

    client = get_client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return False

    for item in client.messages.batches.results(batch_id):
        try:
            res_id = int(item.custom_id.split("-", 1)[1])
            resultado = ResultadoPrompt.objects.select_related(
                "questao", "questao__disciplina__prova__user"
            ).get(pk=res_id)
        except (ValueError, ResultadoPrompt.DoesNotExist):
            continue

        if item.result.type == "succeeded":
            msg = item.result.message
            texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            it = msg.usage.input_tokens
            ot = msg.usage.output_tokens
            resultado.resultado_md = texto
            resultado.input_tokens = it
            resultado.output_tokens = ot
            resultado.custo_estimado = custo_usd(it, ot, lote=True)
            resultado.status = ResultadoPrompt.Status.CONCLUIDO
            resultado.save()
            profile = getattr(resultado.questao.disciplina.prova.user, "profile", None)
            if profile is not None:
                profile.registrar_uso(it, ot, resultado.custo_estimado)
            resultado.questao.status = resultado.questao.Status.CONCLUIDA
            resultado.questao.save(update_fields=["status", "atualizado_em"])
        else:
            resultado.status = ResultadoPrompt.Status.ERRO
            resultado.erro = f"Batch: {item.result.type}"
            resultado.save(update_fields=["status", "erro", "atualizado_em"])
            resultado.questao.status = resultado.questao.Status.ERRO
            resultado.questao.save(update_fields=["status", "atualizado_em"])
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

# Passe 1 só precisa reconhecer os TEMAS presentes, não classificar cada
# questão — um trecho curto de cada enunciado basta e reduz muito o custo.
CLASSIFICACAO_CHARS_NOMES = 400

# Questões por chamada no passe 2. Pedir a uma única chamada que
# particione centenas de IDs faz o modelo perder ~10% deles pelo caminho
# (auditoria: 48 de 452 caíram em "Outros temas", incluindo o tema inteiro
# de tutela provisória). Em blocos pequenos a atribuição é confiável.
CLASSIFICACAO_CHUNK = 50

# Abaixo disso o tópico é candidato a ser fundido no vizinho temático no
# passe 3 (não é regra absoluta: um tema autônomo pode continuar sozinho).
CLASSIFICACAO_MIN_QUESTOES = 3

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


def _chamar_json(client, system, instrucao, schema, max_tokens, profile=None, etapa=""):
    """Faz uma chamada de structured output e devolve o JSON já decodificado."""
    modelo = getattr(settings, "AI_MODEL", "claude-sonnet-5")
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    params = {
        "model": modelo,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": instrucao}],
        "output_config": output_config,
    }
    if _suporta_adaptive(modelo):
        params["thinking"] = {"type": "adaptive"}
        output_config["effort"] = getattr(settings, "AI_EFFORT", "medium")
    resp = _criar_mensagem(client, params)
    texto_json = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if not texto_json:
        raise IAError(
            f"A IA não retornou texto{etapa} (stop_reason={getattr(resp, 'stop_reason', '?')})."
        )
    if profile is not None:
        profile.registrar_uso(
            resp.usage.input_tokens,
            resp.usage.output_tokens,
            custo_usd(resp.usage.input_tokens, resp.usage.output_tokens),
        )
    return json.loads(texto_json)


def levantar_topicos_via_ia(questoes, profile=None):
    """Passe 1 da classificação: descobre os TEMAS presentes na disciplina,
    sem atribuir questões. Retorna `[{nome, descricao}, ...]` em ordem
    didática. Como não precisa emitir centenas de IDs, é confiável mesmo
    com disciplinas grandes."""
    linhas = [f"- {(q.enunciado_md or '').strip()[:CLASSIFICACAO_CHARS_NOMES]}" for q in questoes]
    instrucao = (
        "Abaixo estão as questões de concurso de uma mesma disciplina. "
        "Identifique os TÓPICOS DE ESTUDO que elas cobrem e devolva a lista "
        "de tópicos — apenas os tópicos, sem associar questões a eles.\n\n"
        "Regras:\n"
        "- Cada tópico tem um nome curto (2 a 6 palavras) e uma descrição de "
        "uma frase.\n"
        "- Crie tópicos que cubram TODO o conteúdo presente: se um tema "
        "aparece em várias questões, ele merece tópico próprio. Tipicamente "
        "entre 10 e 40 tópicos, conforme a variedade do material.\n"
        "- Prefira tópicos da dogmática da disciplina (institutos, fases, "
        "procedimentos) a rótulos genéricos. NÃO crie tópicos vagos como "
        '"Temas diversos", "Outros assuntos" ou "Questões variadas".\n'
        "- Se houver institutos paralelos que costumam ser confundidos, "
        "trate-os no mesmo tópico com um nome que cubra ambos.\n"
        "- Ordene na sequência didática natural da disciplina.\n\n"
        "Questões:\n\n" + "\n".join(linhas)
    )
    data = _chamar_json(
        get_client(),
        "Você organiza questões de provas de concurso em tópicos de estudo.",
        instrucao,
        SCHEMA_TOPICOS_NOMES,
        max_tokens=max(getattr(settings, "AI_MAX_TOKENS", 16000), 12000),
        profile=profile,
        etapa=" ao levantar os tópicos",
    )
    return [t for t in data.get("topicos", []) if (t.get("nome") or "").strip()]


def atribuir_questoes_aos_topicos(questoes, topicos, profile=None, progresso=None):
    """Passe 2 da classificação: distribui as questões entre os tópicos já
    definidos, em blocos de CLASSIFICACAO_CHUNK. Retorna `{questao_pk:
    indice_do_topico}` — IDs que a IA deixar de fora simplesmente não
    aparecem no dicionário e viram sobra.

    `progresso(feitos, total)` é chamado a cada bloco para alimentar a barra
    de andamento da página."""
    client = get_client()
    n_blocos = max(1, -(-len(questoes) // CLASSIFICACAO_CHUNK))
    catalogo = "\n".join(
        f"{i}. {t['nome']}" + (f" — {t.get('descricao', '')}" if t.get("descricao") else "")
        for i, t in enumerate(topicos)
    )
    atribuicoes = {}
    for n, ini in enumerate(range(0, len(questoes), CLASSIFICACAO_CHUNK)):
        if progresso is not None:
            progresso(n, n_blocos)
        bloco = questoes[ini : ini + CLASSIFICACAO_CHUNK]
        linhas = [
            f"[ID {q.pk}] {(q.enunciado_md or '').strip()[:CLASSIFICACAO_CHARS_POR_QUESTAO]}"
            for q in bloco
        ]
        instrucao = (
            "Classifique cada questão abaixo no tópico de estudo mais "
            "adequado, escolhendo pelo NÚMERO do tópico nesta lista:\n\n"
            f"{catalogo}\n\n"
            "Regras:\n"
            f"- Devolva EXATAMENTE {len(bloco)} atribuições, uma para cada ID "
            "listado. Nenhum ID pode ficar de fora e nenhum pode se repetir.\n"
            "- Classifique pelo tema PRINCIPAL que a questão testa, não por "
            "temas que ela apenas mencione de passagem.\n"
            "- Use somente números de tópico que existam na lista acima.\n\n"
            "Questões:\n\n" + "\n\n".join(linhas)
        )
        data = _chamar_json(
            client,
            "Você classifica questões de provas de concurso em tópicos de estudo.",
            instrucao,
            SCHEMA_ATRIBUICOES,
            max_tokens=max(8000, 2000 + len(bloco) * 60),
            profile=profile,
            etapa=" ao classificar as questões",
        )
        validos = {q.pk for q in bloco}
        for a in data.get("atribuicoes", []):
            qid, idx = a.get("id"), a.get("topico")
            if qid in validos and isinstance(idx, int) and 0 <= idx < len(topicos):
                atribuicoes.setdefault(qid, idx)
    if progresso is not None:
        progresso(n_blocos, n_blocos)
    return atribuicoes


def _aplicar_fusoes(grupos, fusoes):
    """Aplica `[{de, para}]` sobre os grupos, resolvendo cadeias (A→B→C) e
    tolerando ciclos (A→B→A resolve para o primeiro visitado)."""
    destino = {}
    for f in fusoes:
        de, para = f.get("de"), f.get("para")
        if not (isinstance(de, int) and isinstance(para, int)):
            continue
        if not (0 <= de < len(grupos) and 0 <= para < len(grupos)) or de == para:
            continue
        destino[de] = para

    def raiz(i):
        vistos = set()
        while i in destino and i not in vistos:
            vistos.add(i)
            i = destino[i]
        return i

    final, mapa = [], {}
    for i, g in enumerate(grupos):
        if raiz(i) == i:
            mapa[i] = len(final)
            final.append({**g, "questoes": list(g["questoes"])})
    for i, g in enumerate(grupos):
        r = raiz(i)
        if r != i:
            final[mapa[r]]["questoes"].extend(g["questoes"])
    return final


def consolidar_topicos(grupos, profile=None):
    """Passe 3: funde tópicos redundantes (dois recortes do mesmo instituto)
    e acomoda os pequenos demais no vizinho temático.

    Sem esta etapa a taxonomia fica granular demais: no reprocessamento de
    Direito Processual Civil, 11 dos 55 tópicos ficaram com ≤2 questões e
    havia pares como "Ação rescisória" / "Ação rescisória e coisa julgada
    nas relações continuativas".
    """
    if len(grupos) < 2:
        return grupos
    catalogo = "\n".join(
        f"{i}. {g['nome']} ({len(g['questoes'])} "
        f"{'questão' if len(g['questoes']) == 1 else 'questões'})"
        + (f" — {g['descricao']}" if g.get("descricao") else "")
        for i, g in enumerate(grupos)
    )
    instrucao = (
        "Abaixo está a lista de tópicos de estudo de uma disciplina, com "
        "quantas questões cada um recebeu. Indique quais tópicos são "
        "REDUNDANTES e devem ser fundidos em outros.\n\n"
        f"{catalogo}\n\n"
        "CRITÉRIO ÚNICO — só funda A em B se o conteúdo de A já estiver "
        "naturalmente coberto pelo NOME de B, isto é, se um estudante "
        "procurasse o assunto de A e esperasse encontrá-lo dentro de B. "
        "Na prática isso acontece quando A é um recorte, uma fase ou um "
        'aspecto do MESMO instituto de B — por exemplo "X na execução" '
        'dentro de "X", ou "estabilização de Y" dentro de "Y".\n\n'
        "NUNCA funda apenas porque o tópico é pequeno. Um tópico com uma "
        "única questão sobre um instituto autônomo deve permanecer sozinho. "
        "Uma fusão errada é MUITO pior do que um tópico pequeno: ela esconde "
        "o assunto num lugar onde ninguém vai procurar. Em caso de dúvida, "
        "NÃO funda.\n\n"
        "Antes de propor cada fusão, verifique explicitamente: os dois "
        "tópicos tratam do mesmo instituto jurídico, ou A é uma etapa/fase "
        "do procedimento tratado em B? Se são institutos autônomos — ainda "
        'que da mesma área, ou ambos "procedimentos especiais", ou ambos '
        "ligados a recursos —, NÃO funda.\n\n"
        f"Só proponha fusões cuja ORIGEM tenha menos de "
        f"{CLASSIFICACAO_MIN_QUESTOES} questões: tópicos maiores que isso já "
        "se sustentam sozinhos e não devem ser absorvidos, mesmo que haja "
        "algum parentesco temático.\n\n"
        "Sempre funda o MENOR no MAIOR. Não encadeie fusões: se A vai para "
        "B, B não pode ir para outro. Devolva lista vazia se nada precisar "
        "ser fundido."
    )
    data = _chamar_json(
        get_client(),
        "Você organiza taxonomias de tópicos de estudo, evitando redundância.",
        instrucao,
        SCHEMA_FUSOES,
        max_tokens=max(8000, 2000 + len(grupos) * 40),
        profile=profile,
        etapa=" ao consolidar os tópicos",
    )
    # Guarda dura: por mais que o prompt peça, o modelo tende a absorver
    # tópicos saudáveis e criar poucos tópicos gigantes (observado: fundir
    # um de 13 questões em outro de 25). Só origens pequenas são fundidas.
    fusoes = [
        f
        for f in data.get("fusoes", [])
        if isinstance(f.get("de"), int)
        and 0 <= f["de"] < len(grupos)
        and len(grupos[f["de"]]["questoes"]) < CLASSIFICACAO_MIN_QUESTOES
    ]
    return _aplicar_fusoes(grupos, fusoes)


def classificar_topicos_via_ia(questoes, profile=None, progresso=None):
    """Agrupa as questões em tópicos de estudo em TRÊS passes: levanta os
    temas da disciplina, classifica as questões em blocos pequenos e funde
    os tópicos redundantes ou pequenos demais.
    Retorna `[{nome, descricao, questoes: [ids]}, ...]`.

    `progresso(rotulo, feitos, total)` acompanha as etapas para a UI."""

    def _etapa(rotulo):
        def _cb(feitos, total):
            if progresso is not None:
                progresso(rotulo, feitos, total)

        return _cb

    _etapa("Identificando os temas da disciplina…")(0, 1)
    topicos = levantar_topicos_via_ia(questoes, profile=profile)
    if not topicos:
        raise IAError("A IA não retornou tópicos.")

    atribuicoes = atribuir_questoes_aos_topicos(
        questoes,
        topicos,
        profile=profile,
        progresso=_etapa("Classificando as questões por tema…"),
    )

    grupos = [
        {"nome": t.get("nome") or "Tópico", "descricao": t.get("descricao") or "", "questoes": []}
        for t in topicos
    ]
    for qid, idx in atribuicoes.items():
        grupos[idx]["questoes"].append(qid)
    grupos = [g for g in grupos if g["questoes"]]

    _etapa("Organizando os tópicos…")(0, 1)
    return consolidar_topicos(grupos, profile=profile)


def montar_conteudo_topico(topico):
    """Monta o material do tópico: enunciado, gabarito e análises concluídas
    de cada questão — a matéria-prima do texto coeso."""
    from .models import ResultadoPrompt

    partes = [f"TÓPICO: {topico.nome}"]
    if topico.descricao:
        partes.append(topico.descricao)
    for i, q in enumerate(topico.questoes.all(), start=1):
        partes += ["", f"=== QUESTÃO {i} ===", (q.enunciado_md or "").strip()]
        if q.gabarito:
            partes.append(f"Gabarito: {q.gabarito}")
        analises = (
            q.resultados.filter(status=ResultadoPrompt.Status.CONCLUIDO)
            .exclude(resultado_md="")
            .order_by("criado_em")
        )
        for r in analises:
            partes += [f"--- Análise ({r.prompt.nome}) ---", r.resultado_md.strip()]
    return "\n".join(partes)


def _params_sintese(topico, cache_prompt=False):
    """Parâmetros para a síntese do texto do tópico (sync ou batch).

    Com `cache_prompt`, as instruções vão para o bloco system com
    cache_control — o prefixo é idêntico em todos os tópicos do lote.
    """
    conteudo = montar_conteudo_topico(topico)
    if cache_prompt:
        system = [
            {"type": "text", "text": SYSTEM_PROMPT},
            {
                "type": "text",
                "text": "Instruções:\n" + PROMPT_SINTESE_TOPICO,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        messages = [{"role": "user", "content": conteudo}]
    else:
        system = SYSTEM_PROMPT
        messages = [{"role": "user", "content": PROMPT_SINTESE_TOPICO + "\n\n" + conteudo}]
    # Tópicos grandes precisam de mais espaço de saída: com o teto fixo, o
    # texto era cortado no meio da frase (ex.: "Mandado de segurança", 18
    # questões, bateu exatamente os 16k). O budget também é consumido pelo
    # thinking adaptativo, então escala com o tamanho do material de entrada.
    max_tokens = min(
        32000,
        max(
            getattr(settings, "AI_MAX_TOKENS", 16000),
            int(len(conteudo) / CHARS_PER_TOKEN * 0.9) + 8000,
        ),
    )
    return {
        "model": getattr(settings, "AI_MODEL", "claude-sonnet-5"),
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }


def sintetizar_texto_sincrono(texto, profile=None):
    """Executa um TextoTopico via messages.create e grava o resultado."""
    from .models import TextoTopico

    texto.status = TextoTopico.Status.PROCESSANDO
    texto.modelo = getattr(settings, "AI_MODEL", "claude-sonnet-5")
    texto.save(update_fields=["status", "modelo", "atualizado_em"])

    try:
        params = _params_sintese(texto.topico)
        if _suporta_adaptive(params["model"]):
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": getattr(settings, "AI_EFFORT", "medium")}
        client = get_client()
        resp = _criar_mensagem(client, params)

        corpo = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
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
    except Exception as exc:
        texto.status = TextoTopico.Status.ERRO
        texto.erro = str(exc)[:2000]
        texto.save(update_fields=["status", "erro", "atualizado_em"])
        raise


def submeter_batch_textos(textos):
    """Submete um lote de TextoTopico via Batches API. Retorna batch_id."""
    from .models import TextoTopico

    client = get_client()
    requests = []
    for t in textos:
        params = _params_sintese(t.topico, cache_prompt=True)
        requests.append({"custom_id": f"top-{t.pk}", "params": params})

    batch = client.messages.batches.create(requests=requests)
    TextoTopico.objects.filter(pk__in=[t.pk for t in textos]).update(
        status=TextoTopico.Status.PROCESSANDO,
        batch_id=batch.id,
        modelo=getattr(settings, "AI_MODEL", "claude-sonnet-5"),
    )
    return batch.id


def coletar_batch_textos(batch_id):
    """Coleta os textos de um batch concluído. Retorna True se finalizado."""
    from .models import TextoTopico

    client = get_client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return False

    for item in client.messages.batches.results(batch_id):
        try:
            texto_id = int(item.custom_id.split("-", 1)[1])
            texto = TextoTopico.objects.select_related("topico__disciplina__prova__user").get(
                pk=texto_id
            )
        except (ValueError, TextoTopico.DoesNotExist):
            continue

        if item.result.type == "succeeded":
            msg = item.result.message
            corpo = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            it = msg.usage.input_tokens
            ot = msg.usage.output_tokens
            texto.texto_md = corpo
            texto.input_tokens = it
            texto.output_tokens = ot
            texto.custo_estimado = custo_usd(it, ot, lote=True)
            texto.status = TextoTopico.Status.CONCLUIDO
            texto.save()
            profile = getattr(texto.topico.disciplina.prova.user, "profile", None)
            if profile is not None:
                profile.registrar_uso(it, ot, texto.custo_estimado)
        else:
            texto.status = TextoTopico.Status.ERRO
            texto.erro = f"Batch: {item.result.type}"
            texto.save(update_fields=["status", "erro", "atualizado_em"])
    return True


def _estimar_n_topicos(n_questoes):
    """Quantos tópicos a classificação deve produzir para `n_questoes`.

    O teto antigo de 30 subestimava desde que a classificação passou a ser
    em dois passes: 452 questões geraram 55 tópicos, e a estimativa (presa
    em 30) ficou 1,44x abaixo do consumo real — a operação quase foi cortada
    pelo teto de gastos. Sem o teto, n//8+1 dá 57 para esse caso, colando no
    valor observado.
    """
    return max(3, n_questoes // 8 + 1)


def _tokens_classificacao(questoes, n_topicos):
    """Tokens (entrada + saída) dos dois passes da classificação: passe 1
    levanta os nomes dos tópicos; passe 2 atribui as questões em blocos,
    repetindo o catálogo de tópicos a cada bloco."""
    n = len(questoes)
    chars_nomes = sum(min(len(q.enunciado_md or ""), CLASSIFICACAO_CHARS_NOMES) for q in questoes)
    chars_class = sum(
        min(len(q.enunciado_md or ""), CLASSIFICACAO_CHARS_POR_QUESTAO) for q in questoes
    )
    n_blocos = max(1, -(-n // CLASSIFICACAO_CHUNK))
    passe1 = chars_nomes / CHARS_PER_TOKEN + OUTPUT_TOKENS_CLASSIFICACAO
    passe2 = chars_class / CHARS_PER_TOKEN + n_blocos * n_topicos * 20 + n * 20
    return passe1 + passe2


def estimar_tokens_topicos(questoes):
    """Estimativa (entrada + saída) do fluxo completo de tópicos:
    classificação + síntese de todos os textos."""
    from .models import ResultadoPrompt

    n = len(questoes)
    if not n:
        return 0
    chars_q = sum(len(q.enunciado_md or "") for q in questoes)
    chars_analises = sum(
        len(md)
        for md in ResultadoPrompt.objects.filter(
            questao__in=[q.pk for q in questoes],
            status=ResultadoPrompt.Status.CONCLUIDO,
        ).values_list("resultado_md", flat=True)
    )
    n_topicos = _estimar_n_topicos(n)
    classificacao = _tokens_classificacao(questoes, n_topicos)
    sintese_in = (chars_q + chars_analises) / CHARS_PER_TOKEN + n_topicos * 400
    sintese_out = n_topicos * OUTPUT_TOKENS_SINTESE
    return int(classificacao + sintese_in + sintese_out)


def estimar_custo_topicos(questoes):
    """Estimativa de custo (USD) do fluxo completo de tópicos: a classificação
    são chamadas síncronas (preço cheio); a síntese dos textos sai pela
    Batches API (desconto de 50%) quando há mais de um tópico."""
    from .models import ResultadoPrompt

    n = len(questoes)
    if not n:
        return Decimal("0")
    chars_q = sum(len(q.enunciado_md or "") for q in questoes)
    chars_analises = sum(
        len(md)
        for md in ResultadoPrompt.objects.filter(
            questao__in=[q.pk for q in questoes],
            status=ResultadoPrompt.Status.CONCLUIDO,
        ).values_list("resultado_md", flat=True)
    )
    n_topicos = _estimar_n_topicos(n)

    # A classificação mistura entrada e saída; como a saída dos dois passes é
    # pequena perto da entrada, cobra-se o todo como entrada mais a saída
    # nominal do passe 1.
    class_tokens = _tokens_classificacao(questoes, n_topicos)
    sintese_in = (chars_q + chars_analises) / CHARS_PER_TOKEN + n_topicos * 400
    sintese_out = n_topicos * OUTPUT_TOKENS_SINTESE

    desconto_sintese = Decimal("0.5") if n_topicos > 1 else Decimal("1")
    return (
        custo_usd(int(class_tokens), OUTPUT_TOKENS_CLASSIFICACAO)
        + custo_usd(int(sintese_in), int(sintese_out)) * desconto_sintese
    )


def estimar_tokens_sintese(topicos):
    """Estimativa (entrada + saída) de sintetizar o texto de UM lote de
    tópicos já classificados — usada para o teto de gastos por lote."""
    total = 0
    for topico in topicos:
        chars = len(montar_conteudo_topico(topico))
        total += chars / CHARS_PER_TOKEN + 400 + OUTPUT_TOKENS_SINTESE
    return int(total)
