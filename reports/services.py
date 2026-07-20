"""Geração de relatórios em PDF ou Markdown."""

from __future__ import annotations

from pathlib import Path

from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.utils import timezone

from ai.models import ResultadoPrompt, TextoTopico
from questions.models import Topico

from .models import Relatorio

# Fontes auto-hospedadas do tema "Bancada" (ver static/fonts/fonts.css),
# reaproveitadas no PDF para manter a mesma identidade visual do app.
FONT_SPECS = [
    ('Fraunces', 650, 'fonts/fraunces-normal-650-latin.woff2'),
    ('Fraunces', 550, 'fonts/fraunces-normal-550-latin.woff2'),
    ('Inter', 400, 'fonts/inter-normal-400-latin.woff2'),
    ('Inter', 500, 'fonts/inter-normal-500-latin.woff2'),
    ('Inter', 600, 'fonts/inter-normal-600-latin.woff2'),
    ('IBM Plex Mono', 400, 'fonts/ibm-plex-mono-normal-400-latin.woff2'),
    ('IBM Plex Mono', 500, 'fonts/ibm-plex-mono-normal-500-latin.woff2'),
]

CSS_BASE = """
@page {
    size: A4;
    margin: 2.4cm 2cm 2.2cm;
    @bottom-left { content: "Estudo por Questões"; font-family: 'Inter', sans-serif; font-size: 8.5px; color: #9AA3B5; }
    @bottom-right { content: counter(page) " / " counter(pages); font-family: 'Inter', sans-serif; font-size: 8.5px; color: #9AA3B5; }
}
* { box-sizing: border-box; }
body { font-family: 'Inter', 'Helvetica', sans-serif; color: #1D2534; font-size: 10.5px; line-height: 1.6; }

.cabecalho { border-bottom: 1px solid #E2E5EC; padding-bottom: 12px; margin-bottom: 22px; }
h1 { font-family: 'Fraunces', 'Georgia', serif; font-weight: 650; font-size: 22px; color: #1D2534; margin: 0 0 6px; letter-spacing: -0.01em; }
.meta { color: #69758E; font-size: 10px; margin: 0; }

.disciplina-heading {
    font-family: 'Fraunces', 'Georgia', serif; font-weight: 550; font-size: 14.5px; color: #1D2534;
    margin: 26px 0 12px; padding-bottom: 5px; border-bottom: 2px solid #F2B33D;
}

.item { margin: 0 0 20px; padding-bottom: 16px; border-bottom: 1px solid #E2E5EC; page-break-inside: avoid; }
.item:last-child { border-bottom: none; }
.item-titulo {
    font-family: 'Fraunces', 'Georgia', serif; font-weight: 550; font-size: 13px; color: #1D2534;
    margin: 0 0 8px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
}
.item-chip {
    font-family: 'IBM Plex Mono', monospace; font-weight: 500; font-size: 9px;
    background: rgba(226, 168, 43, 0.16); color: #93650A; padding: 2px 8px; border-radius: 999px;
}
.item-desc { color: #69758E; font-size: 10px; font-style: italic; margin: 0 0 10px; }
.enunciado {
    background: #F4F5F8; border-left: 3px solid #F2B33D; border-radius: 0 6px 6px 0;
    padding: 10px 14px; margin: 0 0 10px; white-space: pre-wrap; color: #45526A; font-size: 10px;
}
.gabarito {
    display: inline-block; font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 9.5px;
    background: rgba(30, 122, 70, 0.1); color: #1E7A46; padding: 2px 9px; border-radius: 999px; margin: 0 0 10px;
}
.item-questoes { color: #69758E; font-size: 9.5px; margin-top: 10px; }

.corpo { font-size: 10.5px; }
.corpo h1, .corpo h2, .corpo h3, .corpo h4 { font-family: 'Fraunces', 'Georgia', serif; font-weight: 550; color: #1D2534; margin: 14px 0 6px; }
.corpo h1 { font-size: 13px; }
.corpo h2 { font-size: 12px; }
.corpo h3, .corpo h4 { font-size: 11px; }
.corpo p { margin: 0 0 8px; }
.corpo ul, .corpo ol { margin: 0 0 10px; padding-left: 20px; }
.corpo li { margin-bottom: 3px; }
.corpo strong { font-weight: 600; color: #1D2534; }
.corpo code { font-family: 'IBM Plex Mono', monospace; font-size: 9.3px; background: #F4F5F8; padding: 1px 5px; border-radius: 4px; color: #93650A; }
.corpo pre { background: #F4F5F8; border: 1px solid #E2E5EC; border-radius: 6px; padding: 10px 12px; overflow-x: auto; margin: 0 0 10px; }
.corpo pre code { background: none; padding: 0; color: #1D2534; }
.corpo blockquote { border-left: 3px solid #CFD5E0; margin: 0 0 10px; padding: 2px 12px; color: #69758E; font-style: italic; }
.corpo table { border-collapse: collapse; width: 100%; margin: 0 0 12px; font-size: 9.8px; }
.corpo th, .corpo td { border: 1px solid #E2E5EC; padding: 5px 8px; text-align: left; }
.corpo th { background: #F4F5F8; font-weight: 600; }
.corpo hr { border: none; border-top: 1px solid #E2E5EC; margin: 14px 0; }
.corpo a { color: #2B57B4; text-decoration: none; }
"""


def _montar_css():
    regras = []
    for familia, peso, rel_path in FONT_SPECS:
        caminho = finders.find(rel_path)
        if not caminho:
            continue
        uri = Path(caminho).resolve().as_uri()
        regras.append(
            "@font-face { font-family: '%s'; font-style: normal; font-weight: %d; "
            "font-display: swap; src: url('%s') format('woff2'); }" % (familia, peso, uri)
        )
    return '\n'.join(regras) + '\n' + CSS_BASE


def _plural(n, singular, plural):
    return singular if n == 1 else plural


def gerar_relatorio(user, prompt, prova=None, disciplina=None, com_texto=True, formato='md'):
    """Gera e persiste um Relatorio em Markdown ou PDF a partir dos comentários das questões."""
    qs = ResultadoPrompt.objects.filter(
        status=ResultadoPrompt.Status.CONCLUIDO,
        prompt=prompt,
        questao__disciplina__prova__user=user,
    ).select_related('questao', 'questao__disciplina').order_by(
        'questao__disciplina__nome', 'questao__numero'
    )
    if disciplina is not None:
        qs = qs.filter(questao__disciplina=disciplina)
    elif prova is not None:
        qs = qs.filter(questao__disciplina__prova=prova)

    resultados = list(qs)
    escopo = disciplina.nome if disciplina else (prova.nome if prova else 'Geral')
    titulo = f'{prompt.nome} — {escopo}'

    relatorio = Relatorio(
        user=user, titulo=titulo, tipo=Relatorio.Tipo.COMENTARIOS, prova=prova, disciplina=disciplina,
        prompt=prompt, com_texto=com_texto, num_questoes=len(resultados),
    )

    ts = timezone.now().strftime('%Y%m%d_%H%M%S')
    if formato == 'pdf':
        import markdown as md
        html = _montar_html(titulo, prompt, resultados, com_texto, md)
        pdf_bytes = _render_pdf(html)
        relatorio.arquivo_pdf.save(f'relatorio_{ts}.pdf', ContentFile(pdf_bytes), save=False)
    else:
        conteudo_md = _montar_markdown(titulo, prompt, resultados, com_texto)
        relatorio.arquivo_md.save(f'relatorio_{ts}.md', ContentFile(conteudo_md.encode('utf-8')), save=False)

    relatorio.save()
    return relatorio


def gerar_relatorio_topicos(user, prova=None, disciplina=None, formato='md'):
    """Gera e persiste um Relatorio em Markdown ou PDF a partir dos tópicos de estudo já sintetizados."""
    qs = Topico.objects.filter(
        disciplina__prova__user=user,
        texto__status=TextoTopico.Status.CONCLUIDO,
    ).select_related('disciplina', 'texto').prefetch_related('questoes').order_by(
        'disciplina__nome', 'ordem', 'id'
    )
    if disciplina is not None:
        qs = qs.filter(disciplina=disciplina)
    elif prova is not None:
        qs = qs.filter(disciplina__prova=prova)

    topicos = list(qs)
    escopo = disciplina.nome if disciplina else (prova.nome if prova else 'Geral')
    titulo = f'Tópicos de estudo — {escopo}'

    relatorio = Relatorio(
        user=user, titulo=titulo, tipo=Relatorio.Tipo.TOPICOS, prova=prova, disciplina=disciplina,
        com_texto=True, num_questoes=len(topicos),
    )

    ts = timezone.now().strftime('%Y%m%d_%H%M%S')
    if formato == 'pdf':
        import markdown as md
        html = _montar_html_topicos(titulo, topicos, md)
        pdf_bytes = _render_pdf(html)
        relatorio.arquivo_pdf.save(f'relatorio_topicos_{ts}.pdf', ContentFile(pdf_bytes), save=False)
    else:
        conteudo_md = _montar_markdown_topicos(titulo, topicos)
        relatorio.arquivo_md.save(f'relatorio_topicos_{ts}.md', ContentFile(conteudo_md.encode('utf-8')), save=False)

    relatorio.save()
    return relatorio


def _montar_markdown(titulo, prompt, resultados, com_texto):
    total = len(resultados)
    multiplas = len({r.questao.disciplina_id for r in resultados}) > 1
    partes = [f'# {titulo}', '', f'> Prompt: {prompt.nome} · {total} {_plural(total, "questão", "questões")}', '']
    disciplina_atual = None
    for r in resultados:
        q = r.questao
        if multiplas and q.disciplina_id != disciplina_atual:
            partes += [f'## {q.disciplina.nome}', '']
            disciplina_atual = q.disciplina_id
        cabecalho = '###' if multiplas else '##'
        partes += [f'{cabecalho} Questão {q.numero}', '']
        if com_texto and q.enunciado_md:
            partes += [q.enunciado_md, '']
            if q.gabarito:
                partes += [f'**Gabarito:** {q.gabarito}', '']
        partes += [r.resultado_md or '', '', '---', '']
    return '\n'.join(partes)


def _montar_markdown_topicos(titulo, topicos):
    total = len(topicos)
    multiplas = len({t.disciplina_id for t in topicos}) > 1
    partes = [f'# {titulo}', '', f'> {total} {_plural(total, "tópico", "tópicos")} de estudo', '']
    disciplina_atual = None
    for t in topicos:
        if multiplas and t.disciplina_id != disciplina_atual:
            partes += [f'## {t.disciplina.nome}', '']
            disciplina_atual = t.disciplina_id
        cabecalho = '###' if multiplas else '##'
        n_q = t.questoes.count()
        partes += [f'{cabecalho} {t.nome} ({n_q} {_plural(n_q, "questão", "questões")})', '']
        if t.descricao:
            partes += [f'_{t.descricao}_', '']
        partes += [t.texto.texto_md or '', '']
        numeros = ', '.join(str(q.numero) for q in t.questoes.all())
        if numeros:
            partes += [f'**Questões:** {numeros}', '']
        partes += ['---', '']
    return '\n'.join(partes)


def _montar_html(titulo, prompt, resultados, com_texto, md):
    total = len(resultados)
    multiplas = len({r.questao.disciplina_id for r in resultados}) > 1
    partes = [
        '<html><head><meta charset="utf-8"></head><body>',
        '<div class="cabecalho">',
        f'<h1>{_esc(titulo)}</h1>',
        f'<p class="meta">Prompt: {_esc(prompt.nome)} · {total} {_plural(total, "questão", "questões")}</p>',
        '</div>',
    ]
    disciplina_atual = None
    for r in resultados:
        q = r.questao
        if multiplas and q.disciplina_id != disciplina_atual:
            partes.append(f'<div class="disciplina-heading">{_esc(q.disciplina.nome)}</div>')
            disciplina_atual = q.disciplina_id
        partes.append('<div class="item">')
        partes.append(f'<h2 class="item-titulo">Questão {q.numero}</h2>')
        if com_texto and q.enunciado_md:
            partes.append(f'<div class="enunciado">{_esc(q.enunciado_md)}</div>')
            if q.gabarito:
                partes.append(f'<p class="gabarito">Gabarito: {_esc(q.gabarito)}</p>')
        partes.append(f'<div class="corpo">{md.markdown(r.resultado_md or "", extensions=["extra"])}</div>')
        partes.append('</div>')
    partes.append('</body></html>')
    return '\n'.join(partes)


def _montar_html_topicos(titulo, topicos, md):
    total = len(topicos)
    multiplas = len({t.disciplina_id for t in topicos}) > 1
    partes = [
        '<html><head><meta charset="utf-8"></head><body>',
        '<div class="cabecalho">',
        f'<h1>{_esc(titulo)}</h1>',
        f'<p class="meta">{total} {_plural(total, "tópico", "tópicos")} de estudo</p>',
        '</div>',
    ]
    disciplina_atual = None
    for t in topicos:
        if multiplas and t.disciplina_id != disciplina_atual:
            partes.append(f'<div class="disciplina-heading">{_esc(t.disciplina.nome)}</div>')
            disciplina_atual = t.disciplina_id
        n_q = t.questoes.count()
        partes.append('<div class="item">')
        partes.append(
            f'<h2 class="item-titulo">{_esc(t.nome)} '
            f'<span class="item-chip">{n_q} {_plural(n_q, "questão", "questões")}</span></h2>'
        )
        if t.descricao:
            partes.append(f'<p class="item-desc">{_esc(t.descricao)}</p>')
        partes.append(f'<div class="corpo">{md.markdown(t.texto.texto_md or "", extensions=["extra"])}</div>')
        numeros = ', '.join(str(q.numero) for q in t.questoes.all())
        if numeros:
            partes.append(f'<p class="item-questoes">Questões: {_esc(numeros)}</p>')
        partes.append('</div>')
    partes.append('</body></html>')
    return '\n'.join(partes)


def _render_pdf(html):
    from weasyprint import CSS, HTML
    return HTML(string=html).write_pdf(stylesheets=[CSS(string=_montar_css())])


def _esc(texto):
    return str(texto).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
