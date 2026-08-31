# Sistema de Estudos por Questões

Aplicativo web (Django) para estudar **por questões** de concurso. O usuário cria **provas**
e **disciplinas**, envia **PDFs de questões com gabarito**, o sistema **separa as questões
uma a uma** (texto + gabarito + imagens), e permite **aplicar prompts de IA (Claude)** sobre
elas — individualmente ou em lote — gerando **relatórios em PDF**.

## Recursos
- Login + **perfil de visitante temporário** (uso de IA limitado, dados expiram por inatividade).
- Importação de PDF com **extração híbrida**: regras (pdfplumber) + refino por IA quando a
  confiança é baixa; as questões extraídas ficam disponíveis direto (edição/exclusão avulsa).
- Preservação de **imagens/figuras** das questões (recorte via PyMuPDF) e envio multimodal.
- **Prompts** reutilizáveis (completo / sucinto).
- Aplicação de prompts via **Claude** (`claude-sonnet-5`): envio único ou **em lote
  (Batches API, 50% mais barato)**, com **prompt caching** em lotes (efetivo para
  prompts longos) e **quota por usuário** validada pelo custo estimado da operação.
- **Relatórios em PDF** (WeasyPrint), com ou sem o texto da questão, por disciplina/prova/prompt.

## Stack
Django 6 · Celery + Redis · pdfplumber + PyMuPDF · Anthropic SDK · WeasyPrint · PostgreSQL (prod).
UI: design system "Stölben" (CSS próprio, sem build step).

## Desenvolvimento
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # ajuste ANTHROPIC_API_KEY etc.
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver       # usa config.settings.development (Celery eager, SQLite)
```
Abra http://localhost:8000/ — entre, ou use **"Entrar como visitante"**.

> Em desenvolvimento o Celery roda *eager* (inline), sem Redis. Sem `ANTHROPIC_API_KEY`, a
> extração funciona só por regras e a aplicação de prompts grava status de erro (não quebra).

## Testes
```bash
python manage.py test
```

## Produção
- `DJANGO_SETTINGS_MODULE=config.settings.production` (PostgreSQL + Redis).
- `deploy/` traz Gunicorn, systemd (`questoes.service`, `questoes_celery.service`) e Nginx.
- Worker + Beat: `celery -A config worker --beat -l info` (Beat limpa visitantes expirados).
- `python manage.py collectstatic`.

## Deploy contínuo

O merge de um PR em `main` que passar no CI é implantado sozinho em produção via
`.github/workflows/deploy.yml` + `deploy/cd-deploy.sh` — o workflow
reutilizável `deploy-django.yml` do `rigst/ci` dispara o script por SSH.
A branch `main` tem proteção ativa (checks obrigatórios, sem push direto nem
pra admin); mudanças sempre entram por PR, sem exigir aprovação de terceiros.
Procedimento completo, geração de chave e rollback manual: RUNBOOK.md do
`rigst/ci`, seção 7.

## Conformidade legal (LGPD / Marco Civil)

O app `legal` versiona os Termos de Uso e a Política de Privacidade e registra cada aceite
com data, hora, IP, navegador e o `sha256` do texto exato aceito. O checkbox nasce
desmarcado e é obrigatório no servidor, tanto no cadastro quanto no acesso visitante;
publicar uma versão com mudança material obriga todos a aceitarem de novo antes de
continuar usando o sistema.

Os registros de acesso do nginx são mantidos por **6 meses**, como exige o art. 15 do
Marco Civil (`deploy/logrotate/stolben-acesso` e `deploy/nginx_acesso.py`).

O procedimento completo está em [docs/CONFORMIDADE.md](docs/CONFORMIDADE.md).

```bash
./venv/bin/python manage.py importar_documentos_legais --publicar  # seed inicial
./venv/bin/python manage.py exportar_documentos_legais             # espelho em git
```

## Licença

Este projeto é distribuído sob a **GNU Affero General Public License v3.0** (ver [LICENSE](LICENSE)).

O sistema utiliza [PyMuPDF](https://github.com/pymupdf/PyMuPDF), licenciado sob AGPL-3.0 pela Artifex Software. Em conformidade com a cláusula de rede da AGPL (§13), o código-fonte completo deste sistema está disponível em <https://github.com/rigst/sistema_questoes>.

O inventário das bibliotecas de terceiros está em [docs/LICENCAS-TERCEIROS.md](docs/LICENCAS-TERCEIROS.md), regenerável com `./venv/bin/python scripts/licencas_terceiros.py`.
