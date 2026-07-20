from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

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
    from django.core.cache import cache
    from django.db.models import Count

    from ai.models import ResultadoPrompt, TextoTopico
    from ai.tasks import chave_topicos_classificando

    disc = _disciplina_do_user(request, pk)
    padrao = Prompt.objects.filter(user__isnull=True).first()

    base = disc.questoes.annotate(
        tem_analise=Exists(ResultadoPrompt.objects.filter(
            questao=OuterRef('pk'), prompt=padrao,
            status=ResultadoPrompt.Status.CONCLUIDO,
        )),
    )
    paginator = Paginator(base, 50)
    questoes = paginator.get_page(request.GET.get('page'))
    importacoes = disc.importacoes.all()[:10]
    pendentes = base.filter(tem_analise=False)
    contexto = {
        'disciplina': disc,
        'questoes': questoes,
        'page_obj': questoes,
        'total_questoes': paginator.count,
        'total_com_analise': base.filter(tem_analise=True).count(),
        'total_pendentes': pendentes.count(),
        'total_chars_pendentes': pendentes.aggregate(s=Sum(Length('enunciado_md')))['s'] or 0,
        'importacoes': importacoes,
        'importacao_form': ImportacaoForm(),
        'prompts': Prompt.visiveis_para(request.user),
        'prompt_chars': len(padrao.texto) if padrao else 0,
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

    topicos = (
        disc.topicos.select_related('texto')
        .prefetch_related('questoes')
        .annotate(n_questoes=Count('questoes'))
    )
    topicos_classificando = bool(cache.get(chave_topicos_classificando(disc.pk)))
    contexto.update({
        'topicos': topicos,
        'total_analisadas': disc.questoes.filter(Exists(
            ResultadoPrompt.objects.filter(
                questao=OuterRef('pk'), status=ResultadoPrompt.Status.CONCLUIDO,
            )
        )).count(),
        'topicos_classificando': topicos_classificando,
        'topicos_gerando': topicos_classificando or TextoTopico.objects.filter(
            topico__disciplina=disc,
            status__in=[TextoTopico.Status.PENDENTE, TextoTopico.Status.PROCESSANDO],
        ).exists(),
    })
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
def topicos_status(request, pk):
    from django.core.cache import cache

    from ai.models import TextoTopico
    from ai.tasks import chave_topicos_classificando, chave_topicos_erro

    disc = _disciplina_do_user(request, pk)
    classificando = bool(cache.get(chave_topicos_classificando(disc.pk)))
    erro_classificacao = cache.get(chave_topicos_erro(disc.pk)) or ''
    textos = TextoTopico.objects.filter(topico__disciplina=disc)
    total = textos.count()
    concluidos = textos.filter(status=TextoTopico.Status.CONCLUIDO).count()
    erros = textos.filter(status=TextoTopico.Status.ERRO).count()
    pendentes = total - concluidos - erros
    return JsonResponse({
        'classificando': classificando,
        'erro_classificacao': erro_classificacao,
        'total': total,
        'concluidos': concluidos,
        'erros': erros,
        'em_processamento': classificando or pendentes > 0,
    })


@login_required
def upload(request, pk):
    disc = _disciplina_do_user(request, pk)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == 'POST':
        form = ImportacaoForm(request.POST, request.FILES)
        if form.is_valid():
            imp = form.save(commit=False)
            imp.disciplina = disc
            imp.save()
            processar_importacao.delay(imp.pk)
            if is_ajax:
                return JsonResponse({
                    'ok': True,
                    'importacao_id': imp.pk,
                    'status': imp.status,
                    'status_url': reverse('questions:importacao_status', args=[imp.pk]),
                })
            messages.success(request, 'PDF enviado. As questões estão sendo extraídas.')
        else:
            erro = 'Arquivo inválido. Envie um PDF.'
            if is_ajax:
                return JsonResponse({'ok': False, 'erro': erro}, status=400)
            messages.error(request, erro)
    return redirect('questions:disciplina', pk=disc.pk)


@login_required
def importacao_status(request, pk):
    imp = get_object_or_404(ImportacaoPDF, pk=pk, disciplina__prova__user=request.user)
    return JsonResponse({
        'status': imp.status,
        'progresso': imp.progresso,
        'etapa': imp.etapa,
        'total_paginas': imp.total_paginas,
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
            form.save()
            messages.success(request, 'Questão atualizada.')
            return redirect('questions:disciplina', pk=questao.disciplina.pk)
    else:
        form = QuestaoForm(instance=questao)
    return render(request, 'questions/questao_form.html', {
        'form': form, 'questao': questao,
    })


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
    prompts = Prompt.visiveis_para(request.user)
    return render(request, 'questions/questao_detalhe.html', {
        'questao': questao,
        'resultados': resultados,
        'prompts': prompts,
        'prompts_aplicados': aplicados,
    })
