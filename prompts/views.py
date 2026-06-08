from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import PromptForm
from .models import Prompt


@login_required
def lista(request):
    prompts = Prompt.objects.filter(user=request.user)
    return render(request, 'prompts/lista.html', {'prompts': prompts})


@login_required
def form(request, pk=None):
    instancia = get_object_or_404(Prompt, pk=pk, user=request.user) if pk else None
    if request.method == 'POST':
        form = PromptForm(request.POST, instance=instancia)
        if form.is_valid():
            prompt = form.save(commit=False)
            prompt.user = request.user
            prompt.save()
            messages.success(request, 'Prompt salvo.')
            return redirect('prompts:lista')
    else:
        form = PromptForm(instance=instancia)
    titulo = 'Editar prompt' if instancia else 'Novo prompt'
    return render(request, 'prompts/form.html', {'form': form, 'titulo': titulo})


@login_required
def excluir(request, pk):
    prompt = get_object_or_404(Prompt, pk=pk, user=request.user)
    if request.method == 'POST':
        prompt.delete()
        messages.success(request, 'Prompt excluído.')
        return redirect('prompts:lista')
    return render(request, 'prompts/excluir.html', {'prompt': prompt})
