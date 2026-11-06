"""Cabeçalhos de segurança aplicados por config/security_headers.py.

Separado do base.py para os valores e as justificativas ficarem num só lugar;
o base.py só os reexporta. Todos sobrescrevíveis por env.
"""

import os

# Valor padrão da frota. Nenhuma tela usa câmera, microfone, localização,
# pagamento nem USB; `interest-cohort=()` tira o site do FLoC.
PERMISSIONS_POLICY = os.getenv(
    "PERMISSIONS_POLICY",
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
)

# Ligada em todos os ambientes, não só em produção: assim o e2e (que roda com
# os settings de desenvolvimento) enxerga as mesmas violações que o navegador
# do usuário veria. Desligável por env para diagnóstico.
ENABLE_CSP = os.getenv("ENABLE_CSP", "True").lower() in ("true", "1", "yes")

# Tudo é servido do próprio domínio: fontes auto-hospedadas (static/fonts),
# marked.min.js local, PDFs e relatórios em /media/. Os `<script>` inline dos
# templates levam o nonce da requisição. `style-src 'unsafe-inline'` fica
# porque os templates e o JS usam `style=`/`el.style` em vários pontos.
# `img-src data: blob:` cobre SVG embutido e prévias geradas no navegador.
CONTENT_SECURITY_POLICY = os.getenv(
    "CONTENT_SECURITY_POLICY",
    "default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
)

ADMIN_PATH_PREFIX = "/admin/"

# Política exclusiva do admin. O 'unsafe-eval' é exigido pelo Alpine.js que o
# django-unfold usa: ele compila as expressões de `x-data`/`x-init` com
# `new Function()`. Sem isso o painel carrega sem menu, abas nem tema. A
# concessão fica presa ao /admin/, que só `is_staff` alcança.
CONTENT_SECURITY_POLICY_ADMIN = os.getenv(
    "CONTENT_SECURITY_POLICY_ADMIN",
    "default-src 'self'; img-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-eval' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
)
