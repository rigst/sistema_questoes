from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from questions.models import Questao

from .models import Disciplina, Prova


@login_required
def dashboard(request):
    provas = (
        Prova.objects.filter(user=request.user)
        .annotate(n_disciplinas=Count('disciplinas', distinct=True))
        .prefetch_related('disciplinas')
    )
    total_questoes = Questao.objects.filter(
        disciplina__prova__user=request.user
    ).count()
    total_concluidas = Questao.objects.filter(
        disciplina__prova__user=request.user, status=Questao.Status.CONCLUIDA
    ).count()
    contexto = {
        'provas': provas,
        'total_provas': provas.count(),
        'total_disciplinas': Disciplina.objects.filter(prova__user=request.user).count(),
        'total_questoes': total_questoes,
        'total_concluidas': total_concluidas,
        'em_revisao': Questao.objects.filter(
            disciplina__prova__user=request.user, status=Questao.Status.EM_REVISAO
        ).count(),
        'na_fila': Questao.objects.filter(
            disciplina__prova__user=request.user,
            status__in=[Questao.Status.NA_FILA, Questao.Status.PROCESSANDO]
        ).count(),
    }
    return render(request, 'exams/dashboard.html', contexto)


@login_required
def provas(request):
    return redirect('dashboard')


@login_required
def prova_form(request, pk=None):
    return redirect('dashboard')


@login_required
def prova_detalhe(request, pk):
    return redirect('dashboard')


@login_required
def prova_excluir(request, pk):
    return redirect('dashboard')


@login_required
def disciplina_form(request, prova_pk=None, pk=None):
    return redirect('dashboard')


@login_required
def disciplina_excluir(request, pk):
    return redirect('dashboard')


@login_required
def prova_excluir_inline(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    prova.delete()
    return JsonResponse({'ok': True})


@login_required
def disciplina_excluir_inline(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    disciplina = get_object_or_404(Disciplina, pk=pk, prova__user=request.user)
    disciplina.delete()
    return JsonResponse({'ok': True})


@login_required
def prova_renomear_inline(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    nome = request.POST.get('nome', '').strip()
    if not nome:
        return JsonResponse({'error': 'Nome obrigatório.'}, status=400)
    prova.nome = nome
    prova.save(update_fields=['nome'])
    return JsonResponse({'pk': prova.pk, 'nome': prova.nome})


@login_required
def disciplina_renomear_inline(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    disciplina = get_object_or_404(Disciplina, pk=pk, prova__user=request.user)
    nome = request.POST.get('nome', '').strip()
    if not nome:
        return JsonResponse({'error': 'Nome obrigatório.'}, status=400)
    disciplina.nome = nome
    disciplina.save(update_fields=['nome'])
    return JsonResponse({'pk': disciplina.pk, 'nome': disciplina.nome})


@login_required
def prova_criar_inline(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    nome = request.POST.get('nome', '').strip()
    if not nome:
        return JsonResponse({'error': 'Nome obrigatório.'}, status=400)
    prova = Prova(user=request.user, nome=nome)
    prova.save()
    return JsonResponse({'pk': prova.pk, 'nome': prova.nome})


@login_required
def disciplina_criar_inline(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    nome = request.POST.get('nome', '').strip()
    prova_pk = request.POST.get('prova_pk')
    if not nome:
        return JsonResponse({'error': 'Nome obrigatório.'}, status=400)
    prova = get_object_or_404(Prova, pk=prova_pk, user=request.user)
    disc = Disciplina(prova=prova, nome=nome)
    disc.save()
    from questions.models import Questao as Q
    return JsonResponse({
        'pk': disc.pk,
        'nome': disc.nome,
        'url': f'/questions/disciplina/{disc.pk}/',
    })
