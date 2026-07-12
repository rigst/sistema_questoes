from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import CadastroForm
from .services import criar_visitante


def cadastro(request):
    """Cria uma conta e autentica a sessão (se o cadastro público estiver ativo)."""
    if not getattr(settings, 'ALLOW_PUBLIC_SIGNUP', False):
        raise Http404
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = CadastroForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Conta criada. Bons estudos!')
            return redirect('dashboard')
    else:
        form = CadastroForm()
    return render(request, 'registration/cadastro.html', {'form': form})


@require_POST
def entrar_como_visitante(request):
    """Cria um visitante temporário e autentica a sessão."""
    user, _senha = criar_visitante()
    login(request, user)
    messages.info(
        request,
        'Você entrou como visitante. Os dados são temporários e expiram por inatividade.',
    )
    return redirect('dashboard')
