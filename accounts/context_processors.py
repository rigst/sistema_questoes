from django.conf import settings


def profile_context(request):
    """Expõe o perfil, dados de quota e flags globais para todos os templates."""
    contexto = {
        'allow_public_signup': getattr(settings, 'ALLOW_PUBLIC_SIGNUP', False),
    }
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return contexto
    profile = getattr(user, 'profile', None)
    if profile is None:
        contexto['profile'] = None
        return contexto
    contexto.update({
        'profile': profile,
        'quota_restante': profile.tokens_restantes,
        'quota_total': profile.quota_tokens_mes,
    })
    return contexto
