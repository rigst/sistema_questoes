# Sistema de Estudos por Questões

Aplicativo web (Django) para estudar **por questões** de concurso. O usuário cria **provas**
e **disciplinas**, envia **PDFs de questões com gabarito**, o sistema **separa as questões
uma a uma** (texto + gabarito + imagens), e permite **aplicar prompts de IA (Claude)** sobre
elas — individualmente ou em lote — gerando **relatórios em PDF**.

## Recursos
- Login + **perfil de visitante temporário** (uso de IA limitado, dados expiram por inatividade).
- Importação de PDF com **extração híbrida**: regras (pdfplumber) + refino por IA quando a
  confiança é baixa, com **tela de revisão** (modal AJAX).
- Preservação de **imagens/figuras** das questões (recorte via PyMuPDF) e envio multimodal.
- **Prompts** reutilizáveis (completo / sucinto).
- Aplicação de prompts via **Claude** (`claude-sonnet-4-6`): envio único ou **em lote
  (Batches API, 50% mais barato)**, com **prompt caching** e **quota por usuário**.
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
