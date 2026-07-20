import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import services
from .models import ResultadoPrompt, TextoTopico

logger = logging.getLogger(__name__)


def chave_topicos_classificando(disciplina_id):
    return f'topicos_classificando_{disciplina_id}'


def chave_topicos_erro(disciplina_id):
    return f'topicos_erro_{disciplina_id}'


def chave_topicos_fase(disciplina_id):
    return f'topicos_fase_{disciplina_id}'


def publicar_fase(disciplina_id, rotulo, feitos, restantes, inicio):
    """Publica o andamento da classificação para o polling da página.

    Diferente da síntese, a classificação não tem itens no banco para o
    status contar — sem isso a barra fica indeterminada e sem estimativa.
    """
    cache.set(chave_topicos_fase(disciplina_id), {
        'rotulo': rotulo,
        'feitos': feitos,
        'restantes': restantes,
        'inicio': inicio.isoformat(),
    }, 7200)


@shared_task
def aplicar_resultado(resultado_id):
    """Aplica um prompt a uma questão (envio único síncrono)."""
    try:
        resultado = ResultadoPrompt.objects.select_related(
            'questao', 'prompt', 'questao__disciplina__prova__user'
        ).get(pk=resultado_id)
    except ResultadoPrompt.DoesNotExist:
        return 'resultado inexistente'
    profile = getattr(resultado.questao.disciplina.prova.user, 'profile', None)
    services.aplicar_resultado_sincrono(resultado, profile=profile)
    return f'resultado {resultado_id} concluído'


def _marcar_teto_atingido(resultados, teto):
    """Marca resultados/questões pulados por terem estourado o teto de
    gastos da operação — ficam visíveis como erro em vez de presos
    'gerando…' para sempre; um novo envio os pega de novo."""
    mensagem = f'Não processada: teto de gastos da operação atingido (~{teto:,} tokens).'.replace(',', '.')
    for r in resultados:
        r.status = ResultadoPrompt.Status.ERRO
        r.erro = mensagem
        r.save(update_fields=['status', 'erro', 'atualizado_em'])
        r.questao.status = r.questao.Status.ERRO
        r.questao.save(update_fields=['status', 'atualizado_em'])


@shared_task
def processar_lote(resultado_ids, usar_lote=True):
    """Processa vários resultados — via Batches API em chunks de 25, ou em sequência.

    Respeita um teto de gastos da operação (previsão inicial + 50% de
    margem — services.MARGEM_TETO_OPERACAO). Ultrapassado o teto, os itens
    restantes não são enviados à IA e ficam marcados como erro — o
    primeiro item/lote sempre é processado, mesmo que sozinho já beire o
    teto, para a operação nunca terminar sem entregar nada.
    """
    resultados = list(
        ResultadoPrompt.objects.select_related(
            'questao', 'prompt', 'questao__disciplina__prova__user'
        ).filter(pk__in=resultado_ids).order_by('questao__numero')
    )
    if not resultados:
        return 'nenhum resultado'

    teto = int(
        services.estimar_tokens([r.questao for r in resultados], resultados[0].prompt)
        * services.MARGEM_TETO_OPERACAO
    )

    if usar_lote and len(resultados) > 1:
        # Divide em batches de 25 questões (otimizado para 30-45 min)
        batch_size = 25
        total_batches = 0
        gasto_estimado = 0
        pulados = 0

        for i in range(0, len(resultados), batch_size):
            chunk = resultados[i:i + batch_size]
            estimativa_chunk = services.estimar_tokens([r.questao for r in chunk], chunk[0].prompt)
            if total_batches > 0 and gasto_estimado + estimativa_chunk > teto:
                _marcar_teto_atingido(chunk, teto)
                pulados += len(chunk)
                continue
            gasto_estimado += estimativa_chunk
            batch_id = services.submeter_batch(chunk)
            coletar_batch.apply_async(args=[batch_id], countdown=30)
            total_batches += 1

        aviso = f'; {pulados} pulado(s) por teto de gastos' if pulados else ''
        return f'{total_batches} batch(es) submetido(s) com total de {len(resultados) - pulados} itens{aviso}'

    gasto = 0
    pulados = 0
    for idx, r in enumerate(resultados):
        if idx > 0 and gasto >= teto:
            _marcar_teto_atingido([r], teto)
            pulados += 1
            continue
        profile = getattr(r.questao.disciplina.prova.user, 'profile', None)
        try:
            resultado = services.aplicar_resultado_sincrono(r, profile=profile)
            gasto += resultado.input_tokens + resultado.output_tokens
        except Exception:
            # O resultado e a questão já foram marcados como ERRO no service.
            logger.exception('Falha ao aplicar prompt (resultado %s)', r.pk)
            continue
    aviso = f'; {pulados} pulado(s) por teto de gastos' if pulados else ''
    return f'{len(resultados) - pulados} resultado(s) processado(s){aviso}'


@shared_task(bind=True, max_retries=240)
def coletar_batch(self, batch_id):
    """Coleta os resultados de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'


def _marcar_textos_teto_atingido(textos, teto):
    mensagem = f'Não sintetizado: teto de gastos da operação atingido (~{teto:,} tokens).'.replace(',', '.')
    for t in textos:
        t.status = TextoTopico.Status.ERRO
        t.erro = mensagem
        t.save(update_fields=['status', 'erro', 'atualizado_em'])


@shared_task
def gerar_topicos(disciplina_id, teto_tokens=None):
    """Classifica as questões analisadas da disciplina em tópicos e submete as sínteses.

    1. Uma chamada de classificação (structured outputs) agrupa as questões.
    2. Cria os Topico e associa as questões; sobras vão para "Outros temas".
    3. Cria um TextoTopico por tópico e submete as sínteses via Batches API
       (chunks de 25), ou sincronamente quando há um único tópico.

    `teto_tokens` é o teto de gastos da operação inteira (previsão + 50% de
    margem, calculado no disparo). Ultrapassado ao submeter lotes de
    síntese, os tópicos restantes não são enviados e ficam marcados como
    erro — "Regerar tópicos" tenta de novo depois.
    """
    from exams.models import Disciplina
    from questions.models import NOME_TOPICO_SOBRAS, Topico

    try:
        disc = Disciplina.objects.select_related('prova__user').get(pk=disciplina_id)
    except Disciplina.DoesNotExist:
        cache.delete(chave_topicos_classificando(disciplina_id))
        cache.delete(chave_topicos_fase(disciplina_id))
        return 'disciplina inexistente'

    profile = getattr(disc.prova.user, 'profile', None)
    # Só questões com análise concluída: as análises são a matéria-prima
    # dos textos, então questões sem análise ficam fora dos tópicos.
    from django.db.models import Exists, OuterRef
    questoes = list(disc.questoes.filter(Exists(
        ResultadoPrompt.objects.filter(
            questao=OuterRef('pk'), status=ResultadoPrompt.Status.CONCLUIDO,
        )
    )))
    if not questoes:
        cache.delete(chave_topicos_classificando(disciplina_id))
        cache.delete(chave_topicos_fase(disciplina_id))
        return 'sem questões analisadas'

    if teto_tokens is None:
        teto_tokens = int(services.estimar_tokens_topicos(questoes) * services.MARGEM_TETO_OPERACAO)

    inicio = timezone.now()

    def _progresso(rotulo, feitos, total):
        publicar_fase(disciplina_id, rotulo, feitos, max(0, total - feitos), inicio)

    try:
        grupos = services.classificar_topicos_via_ia(
            questoes, profile=profile, progresso=_progresso,
        )
        if not grupos:
            raise services.IAError('A IA não retornou tópicos.')

        por_id = {q.pk: q for q in questoes}
        atribuidos = set()
        topicos = []
        for ordem, grupo in enumerate(grupos):
            ids = [i for i in grupo.get('questoes', []) if i in por_id and i not in atribuidos]
            if not ids:
                continue
            topico = Topico.objects.create(
                disciplina=disc,
                nome=(grupo.get('nome') or 'Tópico')[:200],
                descricao=(grupo.get('descricao') or '')[:500],
                ordem=ordem,
            )
            disc.questoes.filter(pk__in=ids).update(topico=topico)
            atribuidos.update(ids)
            topicos.append(topico)

        sobras = [pk for pk in por_id if pk not in atribuidos]
        if sobras:
            topico = Topico.objects.create(
                disciplina=disc, nome=NOME_TOPICO_SOBRAS,
                descricao='Questões não classificadas nos demais tópicos.',
                ordem=len(grupos),
            )
            disc.questoes.filter(pk__in=sobras).update(topico=topico)
            topicos.append(topico)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Falha ao classificar tópicos (disciplina %s)', disciplina_id)
        cache.set(chave_topicos_erro(disciplina_id), str(exc)[:500], 86400)
        cache.delete(chave_topicos_classificando(disciplina_id))
        cache.delete(chave_topicos_fase(disciplina_id))
        return 'erro na classificação'

    # Tópicos criados: o polling passa a acompanhar os TextoTopico.
    textos = [TextoTopico.objects.create(topico=t) for t in topicos]
    cache.delete(chave_topicos_classificando(disciplina_id))
    cache.delete(chave_topicos_fase(disciplina_id))

    if len(textos) == 1:
        try:
            services.sintetizar_texto_sincrono(textos[0], profile=profile)
        except Exception:
            logger.exception('Falha ao sintetizar texto do tópico %s', textos[0].topico_id)
        return f'{len(topicos)} tópico criado (síntese síncrona)'

    batch_size = 25
    total_batches = 0
    gasto_estimado = 0
    pulados = 0
    for i in range(0, len(textos), batch_size):
        chunk = textos[i:i + batch_size]
        estimativa_chunk = services.estimar_tokens_sintese([t.topico for t in chunk])
        if total_batches > 0 and gasto_estimado + estimativa_chunk > teto_tokens:
            _marcar_textos_teto_atingido(chunk, teto_tokens)
            pulados += len(chunk)
            continue
        gasto_estimado += estimativa_chunk
        batch_id = services.submeter_batch_textos(chunk)
        coletar_batch_textos.apply_async(args=[batch_id], countdown=30)
        total_batches += 1

    aviso = ''
    if pulados:
        cache.set(
            chave_topicos_erro(disciplina_id),
            f'Teto de gastos atingido: {pulados} de {len(topicos)} tópico(s) não foram sintetizados. '
            'Clique em "Regerar tópicos" para tentar concluir o restante.',
            86400,
        )
        aviso = f'; {pulados} tópico(s) pulado(s) por teto de gastos'
    return f'{len(topicos)} tópicos criados; {total_batches} batch(es) de síntese{aviso}'


@shared_task(bind=True, max_retries=240)
def coletar_batch_textos(self, batch_id):
    """Coleta os textos de tópicos de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch_textos(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'
