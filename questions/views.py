from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from exams.models import Disciplina
from prompts.models import Prompt

from .forms import ImportacaoForm, QuestaoForm
from .models import ImportacaoPDF, Questao
from .tasks import processar_importacao


def _disciplina_do_user(request, pk):
    return get_object_or_404(Disciplina, pk=pk, prova__user=request.user)


def _questao_do_user(request, pk):
    return get_object_or_404(Questao, pk=pk, disciplina__prova__user=request.user)


@login_required
def disciplina(request, pk):
    disc = _disciplina_do_user(request, pk)
    questoes = disc.questoes.prefetch_related('imagens', 'resultados')
    importacoes = disc.importacoes.all()[:10]
    contexto = {
        'disciplina': disc,
        'questoes': questoes,
        'importacoes': importacoes,
        'importacao_form': ImportacaoForm(),
        'prompts': Prompt.objects.filter(user=request.user),
        'em_processamento': disc.importacoes.filter(
            status__in=[ImportacaoPDF.Status.ENVIADO, ImportacaoPDF.Status.PROCESSANDO]
        ).exists(),
        'ia_em_processamento': disc.questoes.filter(
            status__in=[Questao.Status.NA_FILA, Questao.Status.PROCESSANDO]
        ).exists(),
        'StatusQuestao': Questao.Status,
        'ai_price_input': float(getattr(settings, 'AI_PRICE_INPUT_PER_MTOK', 3.0)),
        'ai_price_output': float(getattr(settings, 'AI_PRICE_OUTPUT_PER_MTOK', 15.0)),
    }
    return render(request, 'questions/disciplina.html', contexto)


@login_required
def ia_status(request, pk):
    disc = _disciplina_do_user(request, pk)
    qs = disc.questoes
    total = qs.count()
    na_fila = qs.filter(status__in=[Questao.Status.NA_FILA, Questao.Status.PROCESSANDO]).count()
    concluidas = qs.filter(status=Questao.Status.CONCLUIDA).count()
    return JsonResponse({
        'em_processamento': na_fila > 0,
        'total': total,
        'na_fila': na_fila,
        'concluidas': concluidas,
    })


@login_required
def upload(request, pk):
    disc = _disciplina_do_user(request, pk)
    if request.method == 'POST':
        form = ImportacaoForm(request.POST, request.FILES)
        if form.is_valid():
            imp = form.save(commit=False)
            imp.disciplina = disc
            imp.save()
            processar_importacao.delay(imp.pk)
            messages.success(request, 'PDF enviado. As questões estão sendo extraídas.')
        else:
            messages.error(request, 'Arquivo inválido. Envie um PDF.')
    return redirect('questions:disciplina', pk=disc.pk)


@login_required
def importacao_status(request, pk):
    imp = get_object_or_404(ImportacaoPDF, pk=pk, disciplina__prova__user=request.user)
    return JsonResponse({
        'status': imp.status,
        'num_questoes': imp.num_questoes,
        'confianca_media': round(imp.confianca_media, 2),
        'usou_ia': imp.usou_ia,
        'erro': imp.erro,
    })


@login_required
def questao_editar(request, pk):
    questao = _questao_do_user(request, pk)
    if request.method == 'POST':
        form = QuestaoForm(request.POST, instance=questao)
        if form.is_valid():
            q = form.save(commit=False)
            if q.status == Questao.Status.EM_REVISAO:
                q.status = Questao.Status.DISPONIVEL
            q.save()
            messages.success(request, 'Questão atualizada.')
            return redirect('questions:disciplina', pk=questao.disciplina.pk)
    else:
        form = QuestaoForm(instance=questao)
    return render(request, 'questions/questao_form.html', {
        'form': form, 'questao': questao,
    })


@login_required
def questao_confirmar(request, pk):
    """Marca uma questão como disponível (revisão concluída)."""
    questao = _questao_do_user(request, pk)
    if request.method == 'POST':
        if questao.status == Questao.Status.EM_REVISAO:
            questao.status = Questao.Status.DISPONIVEL
            questao.save(update_fields=['status', 'atualizado_em'])
    return redirect('questions:disciplina', pk=questao.disciplina.pk)


@login_required
def questao_excluir(request, pk):
    questao = _questao_do_user(request, pk)
    disc_pk = questao.disciplina.pk
    if request.method == 'POST':
        questao.delete()
        messages.success(request, 'Questão excluída.')
    return redirect('questions:disciplina', pk=disc_pk)


@login_required
def questao_detalhe(request, pk):
    questao = _questao_do_user(request, pk)
    resultados = questao.resultados.select_related('prompt').order_by('-criado_em')
    aplicados = {r.prompt_id for r in resultados}
    prompts = Prompt.objects.filter(user=request.user)
    return render(request, 'questions/questao_detalhe.html', {
        'questao': questao,
        'resultados': resultados,
        'prompts': prompts,
        'prompts_aplicados': aplicados,
    })
