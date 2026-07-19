import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from . import services
from .models import ResultadoPrompt, TextoTopico

logger = logging.getLogger(__name__)


def chave_topicos_classificando(disciplina_id):
    return f'topicos_classificando_{disciplina_id}'


def chave_topicos_erro(disciplina_id):
    return f'topicos_erro_{disciplina_id}'


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


@shared_task(bind=True, max_retries=240)
def coletar_batch(self, batch_id):
    """Coleta os resultados de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'


@shared_task
def gerar_topicos(disciplina_id):
    """Classifica as questões analisadas da disciplina em tópicos e submete as sínteses.

    1. Uma chamada de classificação (structured outputs) agrupa as questões.
    2. Cria os Topico e associa as questões; sobras vão para "Outros temas".
    3. Cria um TextoTopico por tópico e submete as sínteses via Batches API
       (chunks de 25), ou sincronamente quando há um único tópico.
    """
    from exams.models import Disciplina
    from questions.models import Topico

    try:
        disc = Disciplina.objects.select_related('prova__user').get(pk=disciplina_id)
    except Disciplina.DoesNotExist:
        cache.delete(chave_topicos_classificando(disciplina_id))
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
        return 'sem questões analisadas'

    try:
        grupos = services.classificar_topicos_via_ia(questoes, profile=profile)
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
                disciplina=disc, nome='Outros temas',
                descricao='Questões não classificadas nos demais tópicos.',
                ordem=len(grupos),
            )
            disc.questoes.filter(pk__in=sobras).update(topico=topico)
            topicos.append(topico)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Falha ao classificar tópicos (disciplina %s)', disciplina_id)
        cache.set(chave_topicos_erro(disciplina_id), str(exc)[:500], 600)
        cache.delete(chave_topicos_classificando(disciplina_id))
        return 'erro na classificação'

    # Tópicos criados: o polling passa a acompanhar os TextoTopico.
    textos = [TextoTopico.objects.create(topico=t) for t in topicos]
    cache.delete(chave_topicos_classificando(disciplina_id))

    if len(textos) == 1:
        try:
            services.sintetizar_texto_sincrono(textos[0], profile=profile)
        except Exception:
            logger.exception('Falha ao sintetizar texto do tópico %s', textos[0].topico_id)
        return f'{len(topicos)} tópico criado (síntese síncrona)'

    batch_size = 25
    total_batches = 0
    for i in range(0, len(textos), batch_size):
        chunk = textos[i:i + batch_size]
        batch_id = services.submeter_batch_textos(chunk)
        coletar_batch_textos.apply_async(args=[batch_id], countdown=30)
        total_batches += 1
    return f'{len(topicos)} tópicos criados; {total_batches} batch(es) de síntese'


@shared_task(bind=True, max_retries=240)
def coletar_batch_textos(self, batch_id):
    """Coleta os textos de tópicos de um batch; re-agenda enquanto não finaliza."""
    finalizado = services.coletar_batch_textos(batch_id)
    if not finalizado and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        raise self.retry(countdown=60)
    return f'batch {batch_id}: {"ok" if finalizado else "pendente"}'
