from celery import shared_task
from django.conf import settings

from . import services
from .models import ResultadoPrompt


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
            continue
    return f'{len(resultados)} resultado(s) processado(s)'


@shared_task(bind=True, max_retries=240)
def coletar_batch(self, batch_id):
    """Coleta os resultados de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'
