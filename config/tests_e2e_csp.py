"""CSP no navegador: as telas principais abrem sem nenhuma violação.

O test client confere que o cabeçalho sai; só o navegador diz se a política
bloqueia algo que a página precisa (script inline sem nonce, handler `on*=`,
`eval` do Alpine no admin, fonte ou imagem de outra origem). Cada tela é
aberta logada, e o teste falha com a lista de violações encontradas.

Roda só no job `e2e` (`pytest -m e2e`, `DJANGO_ALLOW_ASYNC_UNSAFE=true`).
"""

import pytest

from accounts.testing import SENHA_TESTE

TEXTO_MD = "## Seção\n\nTexto com **negrito** e `código`.\n\n- item 1\n- item 2"


def _semear(usuario):
    from ai.models import ResultadoPrompt, TextoTopico
    from exams.models import Disciplina, Prova
    from prompts.models import Prompt
    from questions.models import Questao, Topico
    from reports.models import Relatorio

    prova = Prova.objects.create(user=usuario, nome="Prova CSP")
    disciplina = Disciplina.objects.create(prova=prova, nome="Direito CSP")
    topico = Topico.objects.create(disciplina=disciplina, nome="Tópico CSP")
    TextoTopico.objects.create(topico=topico, status="concluido", texto_md=TEXTO_MD)
    questao = Questao.objects.create(
        disciplina=disciplina,
        topico=topico,
        numero=1,
        enunciado_md="Enunciado.\n\nA) um\nB) dois\nC) três\nD) quatro\nE) cinco",
        gabarito="B",
    )
    prompt = Prompt.objects.create(user=usuario, nome="Prompt CSP", texto="Comente.")
    ResultadoPrompt.objects.create(
        questao=questao, prompt=prompt, status="concluido", resultado_md=TEXTO_MD
    )
    Relatorio.objects.create(user=usuario, titulo="Relatório CSP", disciplina=disciplina)
    return disciplina, topico, questao


def _publicar_documentos_legais():
    from legal.models import DocumentoLegal, TipoDocumento

    for tipo in (TipoDocumento.TERMOS, TipoDocumento.PRIVACIDADE):
        DocumentoLegal.objects.create(
            tipo=tipo, versao="1.0", titulo=f"Doc ({tipo})", corpo_md="Conteúdo."
        ).publicar()


def _vigiar_csp(page):
    violacoes = []

    def _registrar(msg):
        if "Content Security Policy" in msg.text:
            violacoes.append(msg.text)

    page.on("console", _registrar)
    # O console não pega toda violação (ex.: `eval` bloqueado dentro de um
    # try/catch); o evento do documento pega.
    page.add_init_script(
        "document.addEventListener('securitypolicyviolation', (e) => {"
        "  console.error('Content Security Policy: ' + e.violatedDirective"
        "    + ' bloqueou ' + (e.blockedURI || 'inline') + ' em ' + e.sourceFile"
        "    + ':' + e.lineNumber);"
        "});"
    )
    return violacoes


def _login(page, base, usuario):
    page.goto(f"{base}/login/")
    page.fill("form.auth-form input[name=username]", usuario)
    page.fill("form.auth-form input[type=password]", SENHA_TESTE)
    page.locator("button.auth-submit").click()
    page.wait_for_load_state("load")


def _abrir_todas(page, base, caminhos):
    vistos = {}
    for caminho in caminhos:
        resposta = page.goto(f"{base}{caminho}")
        page.wait_for_load_state("networkidle")
        # Sessão perdida devolve 200 na tela de login: a varredura mediria a
        # página errada sem acusar nada.
        assert "login" not in page.url, f"{caminho} caiu no login ({page.url})"
        vistos[caminho] = resposta.status if resposta else None
    return vistos


@pytest.mark.e2e
def test_telas_do_app_sem_violacao_de_csp(live_server, page):
    from django.contrib.auth.models import User

    _publicar_documentos_legais()
    usuario = User.objects.create_user("csp", password=SENHA_TESTE)
    disciplina, topico, questao = _semear(usuario)

    violacoes = _vigiar_csp(page)
    _login(page, live_server.url, "csp")
    # Conta sem aceite registrado cai no re-aceite: a tela também entra na
    # varredura, e aceitar libera as demais.
    assert "/legal/reaceite/" in page.url, page.url
    page.locator("form.legal-acao input[type=checkbox]").check()
    page.locator("form.legal-acao button[type=submit]").click()
    page.wait_for_load_state("load")
    vistos = _abrir_todas(
        page,
        live_server.url,
        [
            "/",
            f"/questoes/disciplina/{disciplina.pk}/",
            f"/questoes/topico/{topico.pk}/",
            f"/questoes/{questao.pk}/",
            f"/questoes/{questao.pk}/editar/",
            f"/questoes/revisao/?questoes={questao.pk}",
            "/prompts/",
            "/prompts/novo/",
            "/relatorios/",
            "/termos/",
            "/privacidade/",
            "/legal/meus-aceites/",
        ],
    )
    # Prova de que o script inline com nonce executa, e não só de que não
    # gerou erro: o marked.min.js transforma o markdown do tópico em HTML.
    page.goto(f"{live_server.url}/questoes/topico/{topico.pk}/")
    page.wait_for_load_state("networkidle")
    assert page.locator(".js-md-topico strong").count() >= 1

    assert all(status == 200 for status in vistos.values()), vistos
    assert violacoes == [], "\n".join(violacoes)


@pytest.mark.e2e
def test_admin_sem_violacao_de_csp(live_server, page):
    from django.contrib.auth.models import User

    User.objects.create_superuser("admin-csp", "a@example.com", SENHA_TESTE)
    violacoes = _vigiar_csp(page)

    page.goto(f"{live_server.url}/admin/login/")
    page.fill("input[name=username]", "admin-csp")
    page.fill("input[name=password]", SENHA_TESTE)
    page.locator("[type=submit]").click()
    page.wait_for_load_state("load")
    vistos = _abrir_todas(
        page,
        live_server.url,
        [
            "/admin/",
            "/admin/questions/questao/",
            "/admin/questions/questao/add/",
            "/admin/legal/documentolegal/",
            "/admin/auth/user/",
        ],
    )
    # O Alpine do unfold carregou e avaliou expressões (o que exige o
    # 'unsafe-eval'): o modal de busca, `x-show="openModal"`, ficou oculto.
    assert page.evaluate("typeof window.Alpine") == "object"
    assert not page.locator("[x-show='openModal']").first.is_visible()

    assert all(status == 200 for status in vistos.values()), vistos
    assert violacoes == [], "\n".join(violacoes)
