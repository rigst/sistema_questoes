from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from exams.models import Disciplina, Prova
from prompts.models import Prompt

from .models import Relatorio
from .services import gerar_relatorio


@login_required
def lista(request):
    relatorios = Relatorio.objects.filter(user=request.user)
    contexto = {
        'relatorios': relatorios,
        'provas': Prova.objects.filter(user=request.user),
        'disciplinas': Disciplina.objects.filter(prova__user=request.user),
        'prompts': Prompt.objects.filter(user=request.user),
    }
    return render(request, 'reports/lista.html', contexto)


@login_required
def gerar(request):
    if request.method != 'POST':
        return redirect('reports:lista')

    prompt = get_object_or_404(Prompt, pk=request.POST.get('prompt_id'), user=request.user)
    com_texto = request.POST.get('com_texto') == '1'

    disciplina = None
    prova = None
    disc_id = request.POST.get('disciplina_id')
    prova_id = request.POST.get('prova_id')
    if disc_id:
        disciplina = get_object_or_404(Disciplina, pk=disc_id, prova__user=request.user)
    elif prova_id:
        prova = get_object_or_404(Prova, pk=prova_id, user=request.user)

    relatorio = gerar_relatorio(
        request.user, prompt, prova=prova, disciplina=disciplina, com_texto=com_texto
    )
    if relatorio.num_questoes == 0:
        messages.warning(request, 'Nenhuma questão com resultado salvo desse prompt no escopo escolhido.')
    else:
        messages.success(request, f'Relatório gerado com {relatorio.num_questoes} questão(ões).')
    return redirect('reports:lista')


@login_required
def excluir(request, pk):
    relatorio = get_object_or_404(Relatorio, pk=pk, user=request.user)
    if request.method == 'POST':
        relatorio.delete()
        messages.success(request, 'Relatório removido.')
    return redirect('reports:lista')
