"""
Configurações de desenvolvimento.
Usa SQLite e sessões em banco de dados.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# Celery roda eager (síncrono inline) em dev, sem depender de Redis ativo.
CELERY_TASK_ALWAYS_EAGER = True
# Não propaga exceções da task para a view em dev: o status de erro já é
# persistido no próprio registro (ResultadoPrompt/ImportacaoPDF).
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_RESULT_BACKEND = 'cache'
CELERY_CACHE_BACKEND = 'memory'
