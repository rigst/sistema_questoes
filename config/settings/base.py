"""
Configurações base do Django — Sistema de Estudos por Questões.
Compartilhadas entre development e production.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Apps do projeto
    'accounts',
    'exams',
    'questions',
    'prompts',
    'ai',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.VisitorExpiryMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.profile_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Auth redirects
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'


# ==============================================================================
# Upload Configuration
# ==============================================================================

MAX_UPLOAD_SIZE_MB = int(os.getenv('MAX_UPLOAD_SIZE_MB', '50'))
MAX_UPLOAD_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_TEMP_DIR = str(BASE_DIR / 'media' / 'tmp')


# ==============================================================================
# Celery Configuration
# ==============================================================================

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-visitors': {
        'task': 'accounts.tasks.cleanup_expired_visitors',
        'schedule': int(os.getenv('CLEANUP_INTERVAL_MINUTES', '60')) * 60,
    },
}


# ==============================================================================
# IA (Anthropic / Claude) Configuration
# ==============================================================================

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
# Modelo padrão — Sonnet (multimodal, 1M contexto, 64K saída)
AI_MODEL = os.getenv('AI_MODEL', 'claude-sonnet-4-6')
AI_EFFORT = os.getenv('AI_EFFORT', 'medium')  # low | medium | high
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '16000'))

# Preços por 1M tokens (USD) — Sonnet 4.6: $3 input / $15 output. Ajuste por env.
AI_PRICE_INPUT_PER_MTOK = float(os.getenv('AI_PRICE_INPUT_PER_MTOK', '3.0'))
AI_PRICE_OUTPUT_PER_MTOK = float(os.getenv('AI_PRICE_OUTPUT_PER_MTOK', '15.0'))

# Quotas mensais (tokens) — usuário comum e visitante
QUOTA_TOKENS_DEFAULT = int(os.getenv('QUOTA_TOKENS_DEFAULT', '2000000'))
QUOTA_TOKENS_VISITOR = int(os.getenv('QUOTA_TOKENS_VISITOR', '100000'))

# Expiração do visitante (horas de inatividade)
VISITOR_EXPIRY_HOURS = int(os.getenv('VISITOR_EXPIRY_HOURS', '48'))
