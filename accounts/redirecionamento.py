"""Destino de redirecionamento vindo do parâmetro `next`."""

from django.utils.http import url_has_allowed_host_and_scheme


def destino_seguro(request, padrao):
    """A URL de `next`, se ela apontar para este mesmo site; senão, `padrao`.

    Sem esta checagem o `next` é um redirecionamento aberto: quem monta o
    formulário escolhe para onde a vítima vai parar depois da ação, e uma
    página de phishing servida no domínio do atacante ganha a credibilidade de
    ter sido alcançada a partir daqui. O scanner acusa como `S5146`, e com
    razão — o valor vem inteiro do POST.
    """
    destino = request.POST.get("next") or request.GET.get("next") or ""
    if destino and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return destino
    return padrao
