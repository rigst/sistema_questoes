# Licenças de terceiros — Sistema de Estudos por Questões

Gerado por `scripts/licencas_terceiros.py` em 2026-07-24 a partir dos pacotes instalados no venv de produção.
Para regenerar: `./venv/bin/python scripts/licencas_terceiros.py`.

O código deste projeto é licenciado sob **AGPL-3.0** (ver `LICENSE`). As bibliotecas abaixo permanecem sob suas licenças originais.

## Dependências diretas

| Pacote | Versão | Licença |
|---|---|---|
| anthropic | 0.109.0 | MIT License |
| celery | 5.6.3 | BSD-3-Clause |
| Django | 6.0.6 | BSD-3-Clause |
| django-redis | 6.0.0 | BSD License |
| gunicorn | 26.0.0 | MIT |
| Markdown | 3.10.2 | BSD-3-Clause |
| pdfplumber | 0.11.9 | MIT License |
| pillow | 12.2.0 | MIT-CMU |
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| PyMuPDF | 1.27.2.3 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License |
| pyspellchecker | 0.9.0 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| redis | 8.0.0 | MIT |
| weasyprint | 69.0 | BSD License |

## Dependências transitivas

| Pacote | Versão | Licença |
|---|---|---|
| amqp | 5.3.1 | BSD License |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.13.0 | MIT |
| asgiref | 3.11.1 | BSD License |
| billiard | 4.2.4 | BSD License |
| brotli | 1.2.0 | MIT |
| certifi | 2026.5.20 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.0.0 | MIT |
| charset-normalizer | 3.4.7 | MIT |
| click | 8.4.1 | BSD-3-Clause |
| click-didyoumean | 0.3.1 | MIT License |
| click-plugins | 1.1.1.2 | BSD License |
| click-repl | 0.3.0 | MIT |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |
| cssselect2 | 0.9.0 | BSD License |
| distro | 1.9.0 | Apache Software License |
| docstring_parser | 0.18.0 | MIT License |
| fonttools | 4.63.0 | MIT |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.18 | BSD-3-Clause |
| jiter | 0.15.0 | MIT |
| kombu | 5.6.2 | BSD-3-Clause |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20251230 | MIT |
| prompt_toolkit | 3.0.52 | BSD License |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| pydyf | 0.12.1 | BSD License |
| pypdfium2 | 5.9.0 | BSD-3-Clause, Apache-2.0, dependency licenses |
| pyphen | 0.17.2 | GNU General Public License v2 or later (GPLv2+) / GNU Lesser General Public License v2 or later (LGPLv2+) / Mozilla Public License 1.1 (MPL 1.1) |
| python-dateutil | 2.9.0.post0 | BSD License / Apache Software License |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| sqlparse | 0.5.5 | BSD License |
| tinycss2 | 1.5.1 | BSD License |
| tinyhtml5 | 2.1.0 | MIT License |
| typing_extensions | 4.15.0 | PSF-2.0 |
| typing-inspection | 0.4.2 | MIT |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.3.1 | MIT License |
| vine | 5.1.0 | BSD License |
| wcwidth | 0.8.1 | MIT |
| webencodings | 0.5.1 | BSD License |
| zopfli | 0.4.2 | Apache Software License |

## Componentes com licença recíproca (copyleft)

Listados para conferência ao redistribuir o código ou ao combinar com componentes fechados. O uso como biblioteca, sem modificação e sem distribuição do binário, não propaga obrigações de abertura.

| Pacote | Versão | Licença |
|---|---|---|
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| PyMuPDF | 1.27.2.3 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License |
| certifi | 2026.5.20 | Mozilla Public License 2.0 (MPL 2.0) |
| pyphen | 0.17.2 | GNU General Public License v2 or later (GPLv2+) / GNU Lesser General Public License v2 or later (LGPLv2+) / Mozilla Public License 1.1 (MPL 1.1) |

## Notas de manutenção

- **Redis**: o servidor em uso é a série 7.0 (BSD-3-Clause). As versões 7.4 a 7.9 passaram a ser RSALv2/SSPL, que não são licenças livres segundo a OSI. Ao atualizar o servidor, reveja esta seção e a página de licenças do site.
- **WeasyPrint** usa Pango, cairo e HarfBuzz do sistema (LGPL) por ligação dinâmica via cffi, forma de uso compatível com a LGPL sem obrigação de abertura.
