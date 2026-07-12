import logging

from celery import shared_task
from django.conf import settings

from . import services
from .models import ResultadoPrompt

logger = logging.getLogger(__name__)


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


@shared_task
def processar_lote(resultado_ids, usar_lote=True):
    """Processa vários resultados — via Batches API em chunks de 25, ou em sequência."""
    resultados = list(
        ResultadoPrompt.objects.select_related(
            'questao', 'prompt', 'questao__disciplina__prova__user'
        ).filter(pk__in=resultado_ids)
    )
    if not resultados:
        return 'nenhum resultado'

    if usar_lote and len(resultados) > 1:
        # Divide em batches de 25 questões (otimizado para 30-45 min)
        batch_size = 25
        total_batches = 0

        for i in range(0, len(resultados), batch_size):
            chunk = resultados[i:i + batch_size]
            batch_id = services.submeter_batch(chunk)
            coletar_batch.apply_async(args=[batch_id], countdown=30)
            total_batches += 1

        return f'{total_batches} batch(es) submetido(s) com total de {len(resultados)} itens'

    for r in resultados:
        profile = getattr(r.questao.disciplina.prova.user, 'profile', None)
        try:
            services.aplicar_resultado_sincrono(r, profile=profile)
        except Exception:
            # O resultado e a questão já foram marcados como ERRO no service.
            logger.exception('Falha ao aplicar prompt (resultado %s)', r.pk)
            continue
    return f'{len(resultados)} resultado(s) processado(s)'


@shared_task
def processar_pares(par_ids, usar_lote=True):
    """Gera os comentários (completo + revisão) de várias questões.

    ``par_ids`` é uma lista de tuplas (pk_completo, pk_revisao). Com
    ``usar_lote``, envia via Batches API em chunks de 25 questões.
    """
    pares = []
    for pk_c, pk_r in par_ids:
        try:
            rc = ResultadoPrompt.objects.select_related(
                'questao', 'questao__disciplina__prova__user'
            ).get(pk=pk_c)
            rr = ResultadoPrompt.objects.get(pk=pk_r)
        except ResultadoPrompt.DoesNotExist:
            continue
        pares.append((rc, rr))
    if not pares:
        return 'nenhum par'

    if usar_lote and len(pares) > 1:
        batch_size = 25
        total_batches = 0
        for i in range(0, len(pares), batch_size):
            chunk = pares[i:i + batch_size]
            batch_id = services.submeter_batch_pares(chunk)
            coletar_batch.apply_async(args=[batch_id], countdown=30)
            total_batches += 1
        return f'{total_batches} batch(es) com {len(pares)} questão(ões)'

    for rc, rr in pares:
        profile = getattr(rc.questao.disciplina.prova.user, 'profile', None)
        try:
            services.aplicar_par_sincrono(rc, rr, profile=profile)
        except Exception:
            logger.exception('Falha ao gerar comentários (par %s/%s)', rc.pk, rr.pk)
            continue
    return f'{len(pares)} questão(ões) processada(s)'


@shared_task(bind=True, max_retries=240)
def coletar_batch(self, batch_id):
    """Coleta os resultados de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'
