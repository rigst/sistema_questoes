"""Cabeçalhos de segurança que o SecurityMiddleware do Django não manda.

Permissions-Policy e Content-Security-Policy, no padrão dos outros apps da
frota (sistema_orcamentos): valores em settings, sobrescrevíveis por env.
"""

import secrets

from django.conf import settings


class PermissionsPolicyMiddleware:
    """Desliga as APIs do navegador que o app não usa (câmera, microfone etc.).

    Nenhuma tela grava áudio nem vídeo, pede localização ou pagamento: com o
    cabeçalho, um script injetado não consegue pedir essas permissões em nome
    do domínio.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        politica = getattr(settings, "PERMISSIONS_POLICY", "")
        if politica:
            response.setdefault("Permissions-Policy", politica)
        return response


class ContentSecurityPolicyMiddleware:
    """Aplica a CSP, com uma política própria (e mais frouxa) para o /admin/.

    O app público fica na política estrita: script só do próprio domínio ou
    com o nonce da requisição, que os `<script>` inline dos templates trazem em
    `nonce="{{ request.csp_nonce }}"`. O admin não cabe nela porque o tema
    (django-unfold) usa Alpine.js, que avalia `x-data`/`x-init` com
    `new Function()` e portanto exige 'unsafe-eval'. A exceção fica presa ao
    prefixo do admin, que só `is_staff` alcança.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _politica(self, request):
        prefixo = getattr(settings, "ADMIN_PATH_PREFIX", "/admin/")
        if request.path.startswith(prefixo):
            return (
                getattr(settings, "CONTENT_SECURITY_POLICY_ADMIN", "")
                or settings.CONTENT_SECURITY_POLICY
            )
        return settings.CONTENT_SECURITY_POLICY

    def __call__(self, request):
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce
        response = self.get_response(request)
        if getattr(settings, "ENABLE_CSP", False) and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._politica(request).format(nonce=nonce)
        return response
