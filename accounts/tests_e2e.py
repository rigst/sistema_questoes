"""Testes de ponta a ponta num navegador de verdade.

Rodam só no job `e2e` do CI (`pytest -m e2e`) e ficam de fora da suíte comum,
que não tem Playwright instalado. Para rodar na mão:

    pip install pytest-playwright && playwright install chromium
    pytest -m e2e

O que estes testes cobrem e os outros não: que a página chega ao navegador
inteira. Um `assertContains` do test client passa mesmo com o CSS quebrado,
com o JS levantando exceção ou com o `{% static %}` apontando para um arquivo
que não existe — nada disso é executado. Aqui é.

Deliberadamente poucos. Suíte e2e grande envelhece mal, e a primeira falha
intermitente ensina a equipe a ignorar o vermelho.
"""

import pytest

from accounts.testing import SENHA_ERRADA


@pytest.mark.e2e
def test_login_renderiza_no_navegador(live_server, page):
    """A tela de login carrega, aplica o CSS e traz o formulário utilizável."""
    page.goto(f"{live_server.url}/login/")

    formulario = page.locator("form.auth-form")
    assert formulario.count() == 1
    assert formulario.locator("input[type=password]").is_visible()

    # Sem o CSS carregado o botão fica com o fundo transparente do agente de
    # usuário. Conferir a cor computada é o que separa "o HTML chegou" de "a
    # página chegou": um {% static %} quebrado passa em qualquer teste do test
    # client, porque lá o CSS nunca é buscado.
    fundo = page.locator("button.auth-submit").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    assert fundo not in ("rgba(0, 0, 0, 0)", "transparent"), (
        f"o botão saiu sem estilo ({fundo}) — estáticos não carregaram"
    )


@pytest.mark.e2e
def test_credencial_errada_nao_derruba_a_pagina(live_server, page):
    """Erro de autenticação volta como formulário, não como 500 nem tela branca."""
    page.goto(f"{live_server.url}/login/")
    page.fill("form.auth-form input[name=username]", "usuario-que-nao-existe")
    page.fill("form.auth-form input[type=password]", SENHA_ERRADA)
    page.locator("button.auth-submit").click()

    page.wait_for_load_state("load")
    assert page.locator("form.auth-form").count() == 1
    assert "/login/" in page.url


@pytest.mark.e2e
def test_paginas_legais_nao_exigem_login(live_server, page):
    """Termos e privacidade precisam abrir sem sessão — são exigência legal."""
    from legal.models import DocumentoLegal, TipoDocumento

    # Sem documento publicado a view levanta Http404 de propósito, então o
    # cenário precisa ser semeado aqui. O `live_server` já traz o
    # `transactional_db`, que é o que permite gravar e o servidor enxergar:
    # ele roda noutra thread, e uma transação de teste comum não seria visível.
    for tipo in (TipoDocumento.TERMOS, TipoDocumento.PRIVACIDADE):
        documento = DocumentoLegal.objects.create(
            tipo=tipo,
            versao="1.0",
            titulo=f"Documento de teste ({tipo})",
            corpo_md="Conteúdo mínimo para a página renderizar.",
        )
        # `publicar()` gera o corpo_html sanitizado e põe o documento em vigor;
        # criar com status=publicado deixaria a página em branco.
        documento.publicar()

    for caminho in ("/termos/", "/privacidade/"):
        resposta = page.goto(f"{live_server.url}{caminho}")
        assert resposta is not None and resposta.status == 200, caminho
        assert "/login/" not in page.url, f"{caminho} redirecionou para o login"
