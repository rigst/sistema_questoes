from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from questions.models import Questao

from .forms import DisciplinaForm, ProvaForm
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
    lista = (
        Prova.objects.filter(user=request.user)
        .annotate(n_disciplinas=Count('disciplinas'))
    )
    return render(request, 'exams/prova_lista.html', {'provas': lista})


@login_required
def prova_form(request, pk=None):
    instancia = get_object_or_404(Prova, pk=pk, user=request.user) if pk else None
    if request.method == 'POST':
        form = ProvaForm(request.POST, instance=instancia)
        if form.is_valid():
            prova = form.save(commit=False)
            prova.user = request.user
            prova.save()
            messages.success(request, 'Prova salva.')
            return redirect('exams:prova_detalhe', pk=prova.pk)
    else:
        form = ProvaForm(instance=instancia)
    titulo = 'Editar prova' if instancia else 'Nova prova'
    return render(request, 'exams/prova_form.html', {'form': form, 'titulo': titulo})


@login_required
def prova_detalhe(request, pk):
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    disciplinas = prova.disciplinas.annotate(n_questoes=Count('questoes'))
    return render(
        request,
        'exams/prova_detalhe.html',
        {'prova': prova, 'disciplinas': disciplinas},
    )


@login_required
def prova_excluir(request, pk):
    prova = get_object_or_404(Prova, pk=pk, user=request.user)
    if request.method == 'POST':
        prova.delete()
        messages.success(request, 'Prova excluída.')
        return redirect('exams:provas')
    return render(request, 'exams/prova_excluir.html', {'prova': prova})


@login_required
def disciplina_form(request, prova_pk=None, pk=None):
    instancia = get_object_or_404(Disciplina, pk=pk, prova__user=request.user) if pk else None
    prova = instancia.prova if instancia else get_object_or_404(Prova, pk=prova_pk, user=request.user)
    if request.method == 'POST':
        form = DisciplinaForm(request.POST, instance=instancia)
        if form.is_valid():
            disciplina = form.save(commit=False)
            disciplina.prova = prova
            disciplina.save()
            messages.success(request, 'Disciplina salva.')
            return redirect('exams:prova_detalhe', pk=prova.pk)
    else:
        form = DisciplinaForm(instance=instancia)
    titulo = 'Editar disciplina' if instancia else 'Nova disciplina'
    return render(
        request,
        'exams/disciplina_form.html',
        {'form': form, 'titulo': titulo, 'prova': prova},
    )


@login_required
def disciplina_excluir(request, pk):
    disciplina = get_object_or_404(Disciplina, pk=pk, prova__user=request.user)
    prova_pk = disciplina.prova.pk
    if request.method == 'POST':
        disciplina.delete()
        messages.success(request, 'Disciplina excluída.')
        return redirect('exams:prova_detalhe', pk=prova_pk)
    return render(request, 'exams/disciplina_excluir.html', {'disciplina': disciplina})
