from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.redirecionamento import destino_seguro
from exams.models import Disciplina
from prompts.models import Prompt
from questions.models import Questao

from .models import ResultadoPrompt
from .services import MARGEM_TETO_OPERACAO, estimar_tokens, estimar_tokens_topicos
from .tasks import (
    aplicar_resultado,
    chave_topicos_classificando,
    chave_topicos_erro,
    processar_lote,
)
from .tasks import (
    gerar_topicos as gerar_topicos_task,
)


def _redir(request, fallback="dashboard"):
    return redirect(destino_seguro(request, fallback))


def _sem_quota(request, questoes, prompt):
    """Bloqueia se a quota restante não cobre o custo estimado da operação."""
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return False
    estimados = estimar_tokens(questoes, prompt)
    if not profile.tem_quota(estimados):
        messages.error(
            request,
            f"Quota de IA insuficiente: a operação precisa de ~{estimados:,} tokens "
            f"e restam {profile.tokens_restantes:,} neste mês.".replace(",", "."),
        )
        return True
    return False


@login_required
@require_POST
def aplicar(request, questao_pk):
    """Aplica um prompt a uma única questão."""
    questao = get_object_or_404(Questao, pk=questao_pk, disciplina__prova__user=request.user)
    prompt = get_object_or_404(Prompt.visiveis_para(request.user), pk=request.POST.get("prompt_id"))
    if _sem_quota(request, [questao], prompt):
        return _redir(request)

    resultado = ResultadoPrompt.objects.create(questao=questao, prompt=prompt)
    questao.status = Questao.Status.NA_FILA
    questao.save(update_fields=["status", "atualizado_em"])
    aplicar_resultado.delay(resultado.pk)
    messages.success(request, f'Prompt "{prompt.nome}" enviado para a questão {questao.numero}.')
    return _redir(request)


@login_required
@require_POST
def aplicar_lote(request):
    """Aplica o mesmo prompt a várias questões selecionadas."""
    ids = request.POST.getlist("questao_ids")
    prompt = get_object_or_404(Prompt.visiveis_para(request.user), pk=request.POST.get("prompt_id"))
    usar_lote = request.POST.get("usar_lote") == "1"
    if not ids:
        messages.error(request, "Selecione ao menos uma questão.")
        return _redir(request)

    questoes = Questao.objects.filter(pk__in=ids, disciplina__prova__user=request.user)
    if _sem_quota(request, questoes, prompt):
        return _redir(request)
    resultado_ids = []
    for questao in questoes:
        resultado = ResultadoPrompt.objects.create(questao=questao, prompt=prompt)
        resultado_ids.append(resultado.pk)
        questao.status = Questao.Status.NA_FILA
        questao.save(update_fields=["status", "atualizado_em"])

    processar_lote.delay(resultado_ids, usar_lote)
    modo = "em lote (Batches)" if usar_lote and len(resultado_ids) > 1 else "individualmente"
    messages.success(
        request,
        f'Prompt "{prompt.nome}" enviado para {len(resultado_ids)} questão(ões) {modo}.',
    )
    return _redir(request)


@login_required
@require_POST
def gerar_comentarios(request):
    """Gera a análise (dois parágrafos) das questões selecionadas.

    Um único prompt padrão do sistema; questões que já têm análise concluída
    são puladas para não pagar duas vezes.
    """
    usar_lote = request.POST.get("usar_lote") == "1"
    todas_paginas = request.POST.get("todas_paginas") == "1"
    disciplina_id = request.POST.get("disciplina_id")

    if todas_paginas and disciplina_id:
        disciplina = get_object_or_404(Disciplina, pk=disciplina_id, prova__user=request.user)
        questoes = list(disciplina.questoes.all())
    else:
        ids = request.POST.getlist("questao_ids")
        if not ids:
            messages.error(request, "Selecione ao menos uma questão.")
            return _redir(request)
        questoes = list(Questao.objects.filter(pk__in=ids, disciplina__prova__user=request.user))

    if not questoes:
        messages.error(request, "Selecione ao menos uma questão.")
        return _redir(request)

    padrao = Prompt.objects.filter(user__isnull=True).first()
    if not padrao:
        messages.error(request, "Prompt padrão do sistema não encontrado.")
        return _redir(request)

    pendentes = [
        q
        for q in questoes
        if not q.resultados.filter(prompt=padrao, status=ResultadoPrompt.Status.CONCLUIDO).exists()
    ]
    ja_prontas = len(questoes) - len(pendentes)
    if not pendentes:
        messages.info(request, "As questões selecionadas já têm análise gerada.")
        return _redir(request)

    if _sem_quota(request, pendentes, padrao):
        return _redir(request)

    single_ids = []
    for questao in pendentes:
        single_ids.append(ResultadoPrompt.objects.create(questao=questao, prompt=padrao).pk)
        questao.status = Questao.Status.NA_FILA
        questao.save(update_fields=["status", "atualizado_em"])

    processar_lote.delay(single_ids, usar_lote)
    aviso = f" ({ja_prontas} já tinham análise e foram puladas)" if ja_prontas else ""
    modo = " em lote" if usar_lote and len(single_ids) > 1 else ""
    messages.success(request, f"Gerando análise de {len(single_ids)} questão(ões){modo}{aviso}.")
    return _redir(request)


@login_required
@require_POST
def gerar_topicos(request, disciplina_pk):
    """Agrupa as questões ANALISADAS da disciplina em tópicos e gera o texto
    de estudo de cada um. Questões sem análise concluída ficam de fora — as
    análises são a matéria-prima dos textos. Regerar apaga os anteriores."""
    from django.db.models import Exists, OuterRef

    disc = get_object_or_404(Disciplina, pk=disciplina_pk, prova__user=request.user)
    total = disc.questoes.count()
    analisadas = list(
        disc.questoes.filter(
            Exists(
                ResultadoPrompt.objects.filter(
                    questao=OuterRef("pk"),
                    status=ResultadoPrompt.Status.CONCLUIDO,
                )
            )
        )
    )
    if not analisadas:
        messages.error(
            request,
            "Nenhuma questão tem análise concluída. Gere as análises antes — "
            "os tópicos e textos são construídos a partir delas.",
        )
        return _redir(request)

    profile = getattr(request.user, "profile", None)
    estimados = estimar_tokens_topicos(analisadas)
    if profile is not None and not profile.tem_quota(estimados):
        messages.error(
            request,
            f"Quota de IA insuficiente: a operação precisa de ~{estimados:,} tokens "
            f"e restam {profile.tokens_restantes:,} neste mês.".replace(",", "."),
        )
        return _redir(request)

    disc.topicos.all().delete()
    cache.delete(chave_topicos_erro(disc.pk))
    cache.set(chave_topicos_classificando(disc.pk), 1, 3600)
    teto_tokens = int(estimados * MARGEM_TETO_OPERACAO)
    gerar_topicos_task.delay(disc.pk, teto_tokens=teto_tokens)

    fora = total - len(analisadas)
    aviso = f" {fora} questão(ões) sem análise ficaram de fora." if fora else ""
    messages.success(
        request,
        f"Gerando tópicos e textos com {len(analisadas)} questão(ões) analisada(s).{aviso}",
    )
    return _redir(request)


@login_required
@require_POST
def resultado_excluir(request, pk):
    resultado = get_object_or_404(
        ResultadoPrompt, pk=pk, questao__disciplina__prova__user=request.user
    )
    resultado.delete()
    messages.success(request, "Resultado removido.")
    return _redir(request)
