from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from prompts.models import Prompt
from questions.models import Questao

from .models import ResultadoPrompt
from .services import estimar_tokens, estimar_tokens_par
from .tasks import aplicar_resultado, processar_lote, processar_pares


def _redir(request, fallback='dashboard'):
    return redirect(request.POST.get('next') or fallback)


def _sem_quota(request, questoes, prompt):
    """Bloqueia se a quota restante não cobre o custo estimado da operação."""
    profile = getattr(request.user, 'profile', None)
    if profile is None:
        return False
    estimados = estimar_tokens(questoes, prompt)
    if not profile.tem_quota(estimados):
        messages.error(
            request,
            f'Quota de IA insuficiente: a operação precisa de ~{estimados:,} tokens '
            f'e restam {profile.tokens_restantes:,} neste mês.'.replace(',', '.'),
        )
        return True
    return False


@login_required
@require_POST
def aplicar(request, questao_pk):
    """Aplica um prompt a uma única questão."""
    questao = get_object_or_404(Questao, pk=questao_pk, disciplina__prova__user=request.user)
    prompt = get_object_or_404(Prompt.visiveis_para(request.user), pk=request.POST.get('prompt_id'))
    if _sem_quota(request, [questao], prompt):
        return _redir(request)

    resultado = ResultadoPrompt.objects.create(questao=questao, prompt=prompt)
    questao.status = Questao.Status.NA_FILA
    questao.save(update_fields=['status', 'atualizado_em'])
    aplicar_resultado.delay(resultado.pk)
    messages.success(request, f'Prompt "{prompt.nome}" enviado para a questão {questao.numero}.')
    return _redir(request)


@login_required
@require_POST
def aplicar_lote(request):
    """Aplica o mesmo prompt a várias questões selecionadas."""
    ids = request.POST.getlist('questao_ids')
    prompt = get_object_or_404(Prompt.visiveis_para(request.user), pk=request.POST.get('prompt_id'))
    usar_lote = request.POST.get('usar_lote') == '1'
    if not ids:
        messages.error(request, 'Selecione ao menos uma questão.')
        return _redir(request)

    questoes = Questao.objects.filter(pk__in=ids, disciplina__prova__user=request.user)
    if _sem_quota(request, questoes, prompt):
        return _redir(request)
    resultado_ids = []
    for questao in questoes:
        resultado = ResultadoPrompt.objects.create(questao=questao, prompt=prompt)
        resultado_ids.append(resultado.pk)
        questao.status = Questao.Status.NA_FILA
        questao.save(update_fields=['status', 'atualizado_em'])

    processar_lote.delay(resultado_ids, usar_lote)
    modo = 'em lote (Batches)' if usar_lote and len(resultado_ids) > 1 else 'individualmente'
    messages.success(
        request,
        f'Prompt "{prompt.nome}" enviado para {len(resultado_ids)} questão(ões) {modo}.',
    )
    return _redir(request)


@login_required
@require_POST
def gerar_comentarios(request):
    """Gera os dois comentários (completo + revisão) das questões selecionadas.

    Uma chamada de IA por questão; a resposta é dividida e salva como dois
    resultados, ancorados nos prompts padrão (para os PDFs por tipo).
    """
    ids = request.POST.getlist('questao_ids')
    usar_lote = request.POST.get('usar_lote') == '1'
    if not ids:
        messages.error(request, 'Selecione ao menos uma questão.')
        return _redir(request)

    padrao_completo = Prompt.objects.filter(user__isnull=True, tipo=Prompt.Tipo.COMPLETO).first()
    padrao_sucinto = Prompt.objects.filter(user__isnull=True, tipo=Prompt.Tipo.SUCINTO).first()
    if not padrao_completo or not padrao_sucinto:
        messages.error(request, 'Prompts padrão do sistema não encontrados.')
        return _redir(request)

    questoes = list(Questao.objects.filter(pk__in=ids, disciplina__prova__user=request.user))

    # Classifica cada questão: par completo, ou só a metade que falta —
    # evita regenerar (e pagar) comentários que já existem.
    pares, so_completo, so_revisao = [], [], []
    for q in questoes:
        tem_c = q.resultados.filter(prompt=padrao_completo, status=ResultadoPrompt.Status.CONCLUIDO).exists()
        tem_r = q.resultados.filter(prompt=padrao_sucinto, status=ResultadoPrompt.Status.CONCLUIDO).exists()
        if tem_c and tem_r:
            continue
        if tem_r:
            so_completo.append(q)
        elif tem_c:
            so_revisao.append(q)
        else:
            pares.append(q)
    total = len(pares) + len(so_completo) + len(so_revisao)
    ja_prontas = len(questoes) - total
    if not total:
        messages.info(request, 'As questões selecionadas já têm comentários gerados.')
        return _redir(request)

    profile = getattr(request.user, 'profile', None)
    if profile is not None:
        estimados = (
            estimar_tokens_par(pares)
            + estimar_tokens(so_completo, padrao_completo)
            + estimar_tokens(so_revisao, padrao_sucinto)
        )
        if not profile.tem_quota(estimados):
            messages.error(
                request,
                f'Quota de IA insuficiente: a operação precisa de ~{estimados:,} tokens '
                f'e restam {profile.tokens_restantes:,} neste mês.'.replace(',', '.'),
            )
            return _redir(request)

    par_ids, single_ids = [], []
    for questao in pares:
        rc = ResultadoPrompt.objects.create(questao=questao, prompt=padrao_completo)
        rr = ResultadoPrompt.objects.create(questao=questao, prompt=padrao_sucinto)
        par_ids.append((rc.pk, rr.pk))
    for questao, prompt in [(q, padrao_completo) for q in so_completo] + [(q, padrao_sucinto) for q in so_revisao]:
        single_ids.append(ResultadoPrompt.objects.create(questao=questao, prompt=prompt).pk)
    for questao in pares + so_completo + so_revisao:
        questao.status = Questao.Status.NA_FILA
        questao.save(update_fields=['status', 'atualizado_em'])

    if par_ids:
        processar_pares.delay(par_ids, usar_lote)
    if single_ids:
        processar_lote.delay(single_ids, usar_lote)
    aviso = f' ({ja_prontas} já tinham comentários e foram puladas)' if ja_prontas else ''
    modo = 'em lote' if usar_lote and total > 1 else ''
    messages.success(
        request,
        f'Gerando comentários de {total} questão(ões) {modo}{aviso}.'.replace('  ', ' '),
    )
    return _redir(request)


@login_required
@require_POST
def gerar_revisoes(request):
    """Gera apenas o parágrafo de revisão das questões selecionadas (modo econômico)."""
    ids = request.POST.getlist('questao_ids')
    usar_lote = request.POST.get('usar_lote') == '1'
    if not ids:
        messages.error(request, 'Selecione ao menos uma questão.')
        return _redir(request)

    padrao_sucinto = Prompt.objects.filter(user__isnull=True, tipo=Prompt.Tipo.SUCINTO).first()
    if not padrao_sucinto:
        messages.error(request, 'Prompt padrão de revisão não encontrado.')
        return _redir(request)

    questoes = list(Questao.objects.filter(pk__in=ids, disciplina__prova__user=request.user))
    pendentes = [
        q for q in questoes
        if not q.resultados.filter(prompt=padrao_sucinto, status=ResultadoPrompt.Status.CONCLUIDO).exists()
    ]
    ja_prontas = len(questoes) - len(pendentes)
    if not pendentes:
        messages.info(request, 'As questões selecionadas já têm revisão gerada.')
        return _redir(request)

    profile = getattr(request.user, 'profile', None)
    if profile is not None:
        estimados = estimar_tokens(pendentes, padrao_sucinto)
        if not profile.tem_quota(estimados):
            messages.error(
                request,
                f'Quota de IA insuficiente: a operação precisa de ~{estimados:,} tokens '
                f'e restam {profile.tokens_restantes:,} neste mês.'.replace(',', '.'),
            )
            return _redir(request)

    single_ids = []
    for questao in pendentes:
        single_ids.append(ResultadoPrompt.objects.create(questao=questao, prompt=padrao_sucinto).pk)
        questao.status = Questao.Status.NA_FILA
        questao.save(update_fields=['status', 'atualizado_em'])

    processar_lote.delay(single_ids, usar_lote)
    aviso = f' ({ja_prontas} já tinham revisão e foram puladas)' if ja_prontas else ''
    messages.success(request, f'Gerando revisão de {len(single_ids)} questão(ões){aviso}.')
    return _redir(request)


@login_required
@require_POST
def resultado_excluir(request, pk):
    resultado = get_object_or_404(
        ResultadoPrompt, pk=pk, questao__disciplina__prova__user=request.user
    )
    resultado.delete()
    messages.success(request, 'Resultado removido.')
    return _redir(request)
