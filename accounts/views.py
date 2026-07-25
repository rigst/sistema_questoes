from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from legal.forms import AceiteForm
from legal.models import OrigemAceite
from legal.services import registrar_aceite

from .forms import CadastroForm
from .services import criar_visitante


class LoginComAceiteView(LoginView):
    """LoginView padrão + o formulário de aceite usado pelo botão de visitante.

    O login normal não pede aceite — quem já tem conta já aceitou, e mudança de
    versão é tratada pelo middleware. O form existe só para o bloco do visitante,
    que cria uma conta nova.
    """

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.setdefault('form_aceite', AceiteForm())
        return contexto


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
            # Depois do login: o aceite fica vinculado à conta recém-criada.
            registrar_aceite(request, usuario=user, origem=OrigemAceite.CADASTRO)
            messages.success(request, 'Conta criada. Bons estudos!')
            return redirect('dashboard')
    else:
        form = CadastroForm()
    return render(request, 'registration/cadastro.html', {'form': form})


@require_POST
def entrar_como_visitante(request):
    """Cria um visitante temporário e autentica a sessão."""
    # O aceite é condição para criar a conta: valida antes de qualquer escrita,
    # para não deixar visitante órfão sem prova de aceite.
    if not AceiteForm(request.POST).is_valid():
        messages.error(
            request,
            'É preciso aceitar os Termos de Uso e a Política de Privacidade '
            'para entrar como visitante.',
        )
        return redirect('login')

    user, _senha = criar_visitante()
    login(request, user)
    registrar_aceite(
        request, usuario=user, origem=OrigemAceite.VISITANTE, e_visitante=True
    )
    messages.info(
        request,
        'Você entrou como visitante. Os dados são temporários e expiram por inatividade.',
    )
    return redirect('dashboard')
