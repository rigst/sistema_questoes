from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Exists, IntegerField, OuterRef, Sum, Value, When
from django.db.models.functions import Length
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from exams.models import Disciplina
from prompts.models import Prompt

from .forms import ImportacaoForm, QuestaoForm
from .models import (
    NOME_TOPICO_SOBRAS,
    ImportacaoPDF,
    LeituraTopico,
    Questao,
    RespostaRevisao,
    Topico,
)
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
    from ai.services import estimar_custo_topicos, formatar_custo_usd
    from ai.tasks import chave_topicos_classificando, chave_topicos_erro

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

    lidos = set(
        LeituraTopico.objects.filter(user=request.user, topico__disciplina=disc)
        .values_list('topico_id', flat=True)
    )
    topicos = list(_topicos_ordenados(disc))
    for t in topicos:
        t.lido = t.pk in lidos
    topicos_classificando = bool(cache.get(chave_topicos_classificando(disc.pk)))
    analisadas = list(disc.questoes.filter(Exists(
        ResultadoPrompt.objects.filter(
            questao=OuterRef('pk'), status=ResultadoPrompt.Status.CONCLUIDO,
        )
    )))
    custo_topicos = estimar_custo_topicos(analisadas) if analisadas else None

    # Estatística "tópicos que mais caem": ranking dos temas de verdade (sem o
    # balaio de sobras) por nº de questões, com o peso relativo à prova.
    total_q = paginator.count
    reais = [t for t in topicos if not t.e_sobras and t.n_questoes]
    top_topicos = []
    for t in reais[:8]:
        top_topicos.append({
            'nome': t.nome,
            'n_questoes': t.n_questoes,
            'pct': round(t.n_questoes / total_q * 100) if total_q else 0,
        })
    soma_top5 = sum(t.n_questoes for t in reais[:5])
    concentracao_top5 = round(soma_top5 / total_q * 100) if total_q else 0

    contexto.update({
        'topicos': topicos,
        'total_topicos': len(topicos),
        'total_lidos': len(lidos),
        'top_topicos': top_topicos,
        'concentracao_top5': concentracao_top5,
        'n_top_concentracao': min(5, len(reais)),
        # denominador da barra de peso: o tema mais cobrado da disciplina
        'maior_topico': max((t.n_questoes for t in topicos), default=1) or 1,
        'total_analisadas': len(analisadas),
        'custo_estimado_topicos': formatar_custo_usd(custo_topicos) if custo_topicos is not None else None,
        'topicos_classificando': topicos_classificando,
        'topicos_gerando': topicos_classificando or TextoTopico.objects.filter(
            topico__disciplina=disc,
            status__in=[TextoTopico.Status.PENDENTE, TextoTopico.Status.PROCESSANDO],
        ).exists(),
        'topicos_erro': cache.get(chave_topicos_erro(disc.pk)) or '',
    })
    return render(request, 'questions/disciplina.html', contexto)


def _topicos_ordenados(disc):
    """Do tópico mais cobrado ao menos cobrado, com o balaio de sobras sempre
    por último — é a ordem em que faz sentido estudar."""
    from django.db.models import Count

    return (
        disc.topicos.select_related('texto')
        .annotate(
            n_questoes=Count('questoes'),
            e_sobra=Case(
                When(nome=NOME_TOPICO_SOBRAS, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by('e_sobra', '-n_questoes', 'nome')
    )


@login_required
def topico_detalhe(request, pk):
    """Página de leitura de um tópico, com navegação para o anterior/próximo."""
    topico = get_object_or_404(
        Topico.objects.select_related('texto', 'disciplina__prova'),
        pk=pk, disciplina__prova__user=request.user,
    )
    disc = topico.disciplina
    ordenados = list(_topicos_ordenados(disc))
    ids = [t.pk for t in ordenados]
    i = ids.index(topico.pk) if topico.pk in ids else 0
    lidos = set(
        LeituraTopico.objects.filter(user=request.user, topico__disciplina=disc)
        .values_list('topico_id', flat=True)
    )
    # Abrir o tópico é o sinal de "estou lendo isto": alimenta o atalho de
    # continuar leitura na dashboard.
    perfil = getattr(request.user, 'profile', None)
    if perfil is not None and perfil.ultimo_topico_id != topico.pk:
        perfil.ultimo_topico = topico
        perfil.save(update_fields=['ultimo_topico', 'atualizado_em'])

    return render(request, 'questions/topico.html', {
        'disciplina': disc,
        'topico': topico,
        'questoes': topico.questoes.order_by('numero'),
        'lido': topico.pk in lidos,
        'posicao': i + 1,
        'total_topicos': len(ordenados),
        'anterior': ordenados[i - 1] if i > 0 else None,
        'proximo': ordenados[i + 1] if i + 1 < len(ordenados) else None,
        'total_lidos': len(lidos),
    })


@login_required
@require_POST
def topico_leitura(request, pk):
    """Marca/desmarca o tópico como lido. Responde JSON no fetch da página."""
    topico = get_object_or_404(Topico, pk=pk, disciplina__prova__user=request.user)
    if request.POST.get('lido') == '0':
        LeituraTopico.objects.filter(user=request.user, topico=topico).delete()
        lido = False
    else:
        LeituraTopico.objects.get_or_create(user=request.user, topico=topico)
        lido = True

    total_lidos = LeituraTopico.objects.filter(
        user=request.user, topico__disciplina=topico.disciplina,
    ).count()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'lido': lido,
            'total_lidos': total_lidos,
            'total': topico.disciplina.topicos.count(),
        })
    return redirect(request.POST.get('next') or 'questions:topico_detalhe', pk=topico.pk)


def _progresso_com_eta(inicio, feitos, restantes):
    """Decorrido + estimativa de conclusão a partir da taxa observada.

    A estimativa só aparece depois de algum item concluir e de alguns
    segundos de operação — antes disso a taxa é ruído. Como é recalculada a
    cada consulta, ela se corrige sozinha conforme o ritmo real muda (os
    lotes da Batches API concluem em degraus, não continuamente).
    """
    if inicio is None:
        return {'decorrido': None, 'eta': None}
    decorrido = max(0, int((timezone.now() - inicio).total_seconds()))
    eta = None
    if feitos > 0 and restantes > 0 and decorrido >= 15:
        eta = int(decorrido / feitos * restantes)
        # Sem teto, uma primeira conclusão lenta projeta horas de espera.
        eta = min(eta, 4 * 3600)
    return {'decorrido': decorrido, 'eta': eta}


@login_required
def ia_status(request, pk):
    from django.db.models import Min

    from ai.models import ResultadoPrompt

    disc = _disciplina_do_user(request, pk)
    qs = disc.questoes
    total = qs.count()
    na_fila = qs.filter(status__in=[Questao.Status.NA_FILA, Questao.Status.PROCESSANDO]).count()
    concluidas = qs.filter(status=Questao.Status.CONCLUIDA).count()

    # A rodada em andamento é o conjunto criado a partir do resultado
    # pendente mais antigo — o total da disciplina não serve de denominador
    # (gerar 7 análises numa disciplina de 452 mostraria 7/452).
    pendentes = ResultadoPrompt.objects.filter(
        questao__disciplina=disc,
        status__in=[ResultadoPrompt.Status.PENDENTE, ResultadoPrompt.Status.PROCESSANDO],
    )
    inicio = pendentes.aggregate(m=Min('criado_em'))['m']
    lote_total = lote_restantes = 0
    if inicio is not None:
        lote_total = ResultadoPrompt.objects.filter(
            questao__disciplina=disc, criado_em__gte=inicio,
        ).count()
        lote_restantes = pendentes.count()

    return JsonResponse({
        'em_processamento': na_fila > 0,
        'total': total,
        'na_fila': na_fila,
        'concluidas': concluidas,
        'lote_total': lote_total,
        'lote_feitos': lote_total - lote_restantes,
        **_progresso_com_eta(inicio, lote_total - lote_restantes, lote_restantes),
    })


@login_required
def topicos_status(request, pk):
    from django.core.cache import cache
    from django.db.models import Min

    from ai.models import TextoTopico
    from ai.tasks import (
        chave_topicos_classificando,
        chave_topicos_erro,
        chave_topicos_fase,
    )

    disc = _disciplina_do_user(request, pk)
    classificando = bool(cache.get(chave_topicos_classificando(disc.pk)))
    erro_classificacao = cache.get(chave_topicos_erro(disc.pk)) or ''
    fase = cache.get(chave_topicos_fase(disc.pk)) or {}
    textos = TextoTopico.objects.filter(topico__disciplina=disc)
    total = textos.count()
    concluidos = textos.filter(status=TextoTopico.Status.CONCLUIDO).count()
    erros = textos.filter(status=TextoTopico.Status.ERRO).count()
    pendentes = total - concluidos - erros

    if classificando:
        # Na classificação não há itens no banco para medir: a própria task
        # publica o andamento dos blocos em cache.
        inicio = fase.get('inicio')
        if inicio:
            inicio = timezone.datetime.fromisoformat(inicio)
        progresso = _progresso_com_eta(
            inicio, fase.get('feitos', 0) or 0, fase.get('restantes', 0) or 0,
        )
    else:
        inicio = textos.exclude(
            status=TextoTopico.Status.CONCLUIDO,
        ).aggregate(m=Min('criado_em'))['m']
        progresso = _progresso_com_eta(inicio, concluidos, pendentes)

    return JsonResponse({
        'classificando': classificando,
        'erro_classificacao': erro_classificacao,
        'total': total,
        'concluidos': concluidos,
        'erros': erros,
        'em_processamento': classificando or pendentes > 0,
        'fase_rotulo': fase.get('rotulo', ''),
        'fase_feitos': fase.get('feitos', 0) or 0,
        'fase_total': (fase.get('feitos', 0) or 0) + (fase.get('restantes', 0) or 0),
        **progresso,
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


def _gabarito_letra(questao):
    """Extrai a letra do gabarito (A–E) do campo, que às vezes vem como
    'A' e às vezes como 'A) ...' ou 'Letra A'."""
    import re

    m = re.search(r'[A-E]', (questao.gabarito or '').upper())
    return m.group(0) if m else ''


@login_required
def revisao(request):
    """Autoteste com as questões dos tópicos que o usuário já leu.

    Escopo opcional por ?topico= ou ?disciplina=. Só entram questões de
    tópicos marcados como lidos e que tenham gabarito (A–E) para conferir.
    """
    import random

    lidos = LeituraTopico.objects.filter(
        user=request.user, topico__disciplina__prova__user=request.user,
    ).values('topico')
    qs = Questao.objects.filter(
        disciplina__prova__user=request.user, topico__in=lidos,
    ).select_related('topico', 'disciplina')

    escopo = None
    topico_id = request.GET.get('topico')
    disciplina_id = request.GET.get('disciplina')
    if topico_id:
        topico = get_object_or_404(Topico, pk=topico_id, disciplina__prova__user=request.user)
        qs = qs.filter(topico=topico)
        escopo = topico.nome
    elif disciplina_id:
        disc = _disciplina_do_user(request, disciplina_id)
        qs = qs.filter(disciplina=disc)
        escopo = disc.nome

    # Só vale revisar o que dá para corrigir: precisa de gabarito A–E.
    questoes = [q for q in qs if _gabarito_letra(q)]
    random.shuffle(questoes)
    questoes = questoes[:30]

    dados = [{
        'pk': q.pk,
        'topico': q.topico.nome if q.topico else '',
        'enunciado': q.enunciado_md,
    } for q in questoes]

    return render(request, 'questions/revisao.html', {
        'dados': dados,
        'total': len(dados),
        'escopo': escopo,
    })


@login_required
@require_POST
def revisao_responder(request, pk):
    """Registra a resposta do usuário e devolve se acertou + o gabarito."""
    questao = _questao_do_user(request, pk)
    escolha = (request.POST.get('alternativa') or '').strip().upper()[:4]
    gabarito = _gabarito_letra(questao)
    correta = bool(escolha) and escolha == gabarito
    RespostaRevisao.objects.create(
        user=request.user, questao=questao, alternativa=escolha, correta=correta,
    )
    return JsonResponse({'correta': correta, 'gabarito': gabarito})


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
