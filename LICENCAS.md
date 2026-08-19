# Licenças de terceiros

O código original deste projeto é licenciado sob [AGPL-3.0](LICENSE). Este
arquivo registra os componentes de terceiros usados pela release atual; não
substitui os textos oficiais de cada projeto.

## PyMuPDF: a escolha que restringe o relicenciamento

**Este é o item que exige decisão consciente antes de qualquer mudança de
licença do projeto.**

O `pymupdf` é distribuído sob licença dupla: **AGPL-3.0 ou uma licença
comercial da Artifex**. Ele não é opcional aqui — `questions/extraction.py`
depende dele para rasterizar páginas de PDF (`fitz.Matrix`, `get_pixmap`), que
é o passo que produz os recortes de imagem das questões.

Optamos pelo ramo **AGPL-3.0**, compatível porque o projeto já é AGPL-3.0.
A consequência é de mão única:

> Enquanto o `pymupdf` estiver embarcado, este projeto **não pode** ser
> relicenciado para nada mais permissivo — nem MIT, nem BSD, nem Apache —
> sem antes comprar a licença comercial da Artifex ou substituir a biblioteca.

Se um dia isso for necessário, o substituto natural é o `pypdfium2`, que já
está na árvore como dependência do `pdfplumber` e é permissivo. A troca não é
mecânica: a API de renderização é diferente.

## Dependências diretas

| Componente | Versão | Licença declarada |
|---|---:|---|
| Django | 6.0.8 | BSD-3-Clause |
| celery | 5.6.3 | BSD-3-Clause |
| redis | 8.1.0 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| pdfplumber | 0.11.10 | MIT |
| **pymupdf** | **1.28.2** | **AGPL-3.0 ou comercial (Artifex)** |
| pillow | 12.3.0 | MIT-CMU |
| anthropic | 0.121.0 | MIT |
| weasyprint | 69.0 | BSD |
| Markdown | 3.10.3 | BSD-3-Clause |
| gunicorn | 26.0.0 | MIT |
| psycopg2-binary | 2.9.12 | LGPL |
| django-redis | 6.0.0 | BSD |
| pyspellchecker | 0.9.0 | MIT |
| nh3 | 0.3.6 | MIT |
| django-unfold | 0.104.0 | MIT |

Notas de compatibilidade:

- `psycopg2-binary` é LGPL, compatível com a AGPL-3.0 — é usado como
  biblioteca, sem modificação, e permanece sob a própria licença.
- `pypdfium2` (transitiva, via `pdfplumber`) declara
  `BSD-3-Clause, Apache-2.0, dependency licenses` num campo de texto livre. As
  duas licenças nomeadas são permissivas, e "dependency licenses" refere-se ao
  PDFium, que é BSD-3-Clause. Nada aí restringe a distribuição sob AGPL.

## Como isto é verificado

A política de licenças fica em
[`configs/liccheck.ini` do rigst/ci](https://github.com/rigst/ci/blob/v1/configs/liccheck.ini)
e é aplicada em dois lugares:

- no CI, pelo job `licencas` (`liccheck`, nível CAUTIOUS), sobre o que o
  repositório declara;
- no servidor, semanalmente, sobre o que está **instalado** em
  `/var/www/*/venv` — os dois divergem, e é a divergência que interessa.

As duas strings de licença acima (`pymupdf` e `pypdfium2`) estão autorizadas
como literal na política, com a justificativa em comentário: nenhuma das duas
declara classifier ou expressão SPDX, só um campo `License` em texto corrido
que nenhuma normalização converte.
