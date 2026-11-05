"""
Configurações da suíte de testes no CI: as de desenvolvimento, com PostgreSQL.

Produção roda PostgreSQL e a suíte rodava em SQLite, que aceita o que o
Postgres recusa (comprimento de varchar, comparação de texto sem
diferenciar maiúsculas, ordem sem ORDER BY, transações). Só o banco muda
aqui; o resto (Celery eager, e-mail no console) segue o development.

Os valores vêm das variáveis DB_* — no CI, o `django-env` do ci.yml aponta
para o serviço PostgreSQL do job. Localmente, sem Postgres, rode a suíte com
o pytest.ini padrão (config.settings.development, SQLite).
"""

import os

from . import development

# Copia as configurações (o Django só lê nomes em maiúsculas) em vez de
# `from .development import *`: importação curinga é issue no Sonar (S2208).
globals().update({nome: valor for nome, valor in vars(development).items() if nome.isupper()})

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "postgres"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}
