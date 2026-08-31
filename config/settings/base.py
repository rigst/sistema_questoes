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
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]


# Application definition

INSTALLED_APPS = [
    # O unfold precisa vir antes do admin: é assim que os templates dele
    # sobrescrevem os do django.contrib.admin.
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps do projeto
    "accounts",
    "exams",
    "questions",
    "prompts",
    "ai",
    "reports",
    "legal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Depois do Authentication (precisa de request.user) e antes do
    # VisitorExpiryMiddleware: nova versão dos termos bloqueia o uso até ser
    # aceita, inclusive para visitantes.
    "legal.middleware.AceiteObrigatorioMiddleware",
    "accounts.middleware.VisitorExpiryMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

UNFOLD = {
    "SITE_TITLE": "Estudo por Questões",
    "SITE_HEADER": "Estudo por Questões",
    "SITE_SUBHEADER": "Administração",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        # Rampa laranja deslocada um degrau a partir do 600. O unfold usa
        # `primary-600` como fundo de botão com texto branco e como cor de link
        # no cabeçalho: o 600 original (234 88 12) dá 3,56:1, abaixo dos 4,5:1
        # do WCAG AA. O 700 do mesmo laranja (194 65 12) dá 5,18:1. A metade
        # clara fica como estava — ela nunca serve de fundo para texto branco.
        "primary": {
            "50": "255 247 237",
            "100": "255 237 213",
            "200": "254 215 170",
            "300": "253 186 116",
            "400": "251 146 60",
            "500": "249 115 22",
            "600": "194 65 12",
            "700": "154 52 18",
            "800": "124 45 18",
            "900": "67 20 7",
            "950": "43 13 4",
        },
    },
}

# Destino após o aceite nas telas do app `legal`.
LEGAL_REDIRECT_URL = "dashboard"

# Para onde a tela de aceite de visitante posta. Aqui a criação do visitante
# tem rota própria e não precisa de campos extras.
LEGAL_VISITOR_ACTION = "accounts:entrar_visitante"
LEGAL_VISITOR_EXTRA: dict[str, str] = {}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.profile_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files (uploads)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Auth redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# ==============================================================================
# Upload Configuration
# ==============================================================================

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
# Configurável porque o Django exige que o diretório exista — `check` falha com
# files.E001 se não existir — e media/tmp não vem versionado.
FILE_UPLOAD_TEMP_DIR = os.getenv("FILE_UPLOAD_TEMP_DIR", str(BASE_DIR / "media" / "tmp"))


# ==============================================================================
# Celery Configuration
# ==============================================================================

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-visitors": {
        "task": "accounts.tasks.cleanup_expired_visitors",
        "schedule": int(os.getenv("CLEANUP_INTERVAL_MINUTES", "60")) * 60,
    },
}


# ==============================================================================
# IA (Anthropic / Claude) Configuration
# ==============================================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Modelo padrão — Sonnet 5 (multimodal, 1M contexto, 128K saída)
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5")
AI_EFFORT = os.getenv("AI_EFFORT", "medium")  # low | medium | high
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "16000"))

# Preços por 1M tokens (USD) — Sonnet 5: $3 input / $15 output. Ajuste por env.
AI_PRICE_INPUT_PER_MTOK = float(os.getenv("AI_PRICE_INPUT_PER_MTOK", "3.0"))
AI_PRICE_OUTPUT_PER_MTOK = float(os.getenv("AI_PRICE_OUTPUT_PER_MTOK", "15.0"))

# Quotas mensais (tokens) — usuário comum e visitante
QUOTA_TOKENS_DEFAULT = int(os.getenv("QUOTA_TOKENS_DEFAULT", "2000000"))
QUOTA_TOKENS_VISITOR = int(os.getenv("QUOTA_TOKENS_VISITOR", "100000"))

# Expiração do visitante (horas de inatividade)
VISITOR_EXPIRY_HOURS = int(os.getenv("VISITOR_EXPIRY_HOURS", "48"))

# Cadastro público de usuários (desativado por padrão; ligue com ALLOW_PUBLIC_SIGNUP=True)
ALLOW_PUBLIC_SIGNUP = os.getenv("ALLOW_PUBLIC_SIGNUP", "False").lower() in ("true", "1", "yes")


# ==============================================================================
# E-mail (recuperação de senha)
# ==============================================================================

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "True").lower() in ("true", "1", "yes")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "False").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "webmaster@localhost")
EMAIL_TIMEOUT = 15
