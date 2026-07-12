from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from prompts.models import Prompt
from questions.models import Questao

from .models import ResultadoPrompt
from .services import estimar_tokens
from .tasks import aplicar_resultado, processar_lote


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
def resultado_excluir(request, pk):
    resultado = get_object_or_404(
        ResultadoPrompt, pk=pk, questao__disciplina__prova__user=request.user
    )
    resultado.delete()
    messages.success(request, 'Resultado removido.')
    return _redir(request)
