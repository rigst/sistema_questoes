"""Geração de relatórios em PDF (Markdown → HTML → PDF via WeasyPrint)."""

from __future__ import annotations

import markdown as md
from django.core.files.base import ContentFile
from django.utils import timezone

from ai.models import ResultadoPrompt

from .models import Relatorio

CSS_RELATORIO = """
@page { size: A4; margin: 2cm; }
body { font-family: 'Helvetica', 'Arial', sans-serif; color: #111827; font-size: 12px; line-height: 1.5; }
h1 { font-size: 20px; color: #1d4ed8; }
h2 { font-size: 15px; margin-top: 1.4em; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
.questao { margin-bottom: 1.5em; page-break-inside: avoid; }
.enunciado { background: #f7f8fa; padding: 8px 12px; border-radius: 8px; white-space: pre-wrap; }
.gabarito { font-weight: 600; color: #2f8f57; }
.meta { color: #6b7280; font-size: 11px; }
"""


def gerar_relatorio(user, prompt, prova=None, disciplina=None, com_texto=True):
    """Gera e persiste um Relatorio com os resultados concluídos do prompt."""
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

    escopo = disciplina.nome if disciplina else (prova.nome if prova else 'Geral')
    titulo = f'{prompt.nome} — {escopo}'

    html = _montar_html(titulo, prompt, qs, com_texto)
    pdf_bytes = _render_pdf(html)

    relatorio = Relatorio(
        user=user, titulo=titulo, prova=prova, disciplina=disciplina,
        prompt=prompt, com_texto=com_texto, num_questoes=qs.count(),
    )
    nome = f'relatorio_{timezone.now():%Y%m%d_%H%M%S}.pdf'
    relatorio.arquivo_pdf.save(nome, ContentFile(pdf_bytes), save=False)
    relatorio.save()
    return relatorio


def _montar_html(titulo, prompt, resultados, com_texto):
    partes = [
        '<html><head><meta charset="utf-8"></head><body>',
        f'<h1>{_escape(titulo)}</h1>',
        f'<p class="meta">Prompt: {_escape(prompt.nome)} · {resultados.count()} questão(ões)</p>',
    ]
    for r in resultados:
        q = r.questao
        partes.append('<div class="questao">')
        partes.append(f'<h2>Questão {q.numero} — {_escape(q.disciplina.nome)}</h2>')
        if com_texto and q.enunciado_md:
            partes.append(f'<div class="enunciado">{_escape(q.enunciado_md)}</div>')
            if q.gabarito:
                partes.append(f'<p class="gabarito">Gabarito: {_escape(q.gabarito)}</p>')
        partes.append(md.markdown(r.resultado_md or '', extensions=['extra']))
        partes.append('</div>')
    partes.append('</body></html>')
    return '\n'.join(partes)


def _render_pdf(html):
    from weasyprint import CSS, HTML
    return HTML(string=html).write_pdf(stylesheets=[CSS(string=CSS_RELATORIO)])


def _escape(texto):
    return (
        str(texto)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )
