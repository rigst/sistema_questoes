from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from legal.forms import AceiteForm
from legal.models import OrigemAceite
from legal.services import documentos_vigentes, registrar_aceite

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
    form_aceite = AceiteForm(request.POST)
    if not form_aceite.is_valid():
        # Volta para a própria tela de aceite com o erro, e não para o login:
        # o checkbox não existe mais lá.
        return render(
            request,
            'legal/aceite.html',
            {
                'form': form_aceite,
                'documentos': list(documentos_vigentes().values()),
                'action': reverse('accounts:entrar_visitante'),
                'campos_extras': {},
            },
        )

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
