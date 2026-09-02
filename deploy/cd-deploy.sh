#!/usr/bin/env bash
set -euo pipefail

# Disparado via SSH pelo usuário "deploy" (authorized_keys com command=
# forçado — ver rigst/ci RUNBOOK.md seção 7). Roda inteiro como "deploy";
# só o reload/restart no fim precisa de sudo (sudoers próprio de "deploy",
# nunca o de "rod").

APP_DIR=/var/www/sistema_questoes/current
FETCH_URL=https://github.com/rigst/sistema_questoes.git   # HTTPS anônimo — repo público, sem credencial
VENV=/var/www/sistema_questoes/venv
ENV_FILE=/var/www/sistema_questoes/shared/.env
WEB_SERVICE=sistema_questoes.service   # reload (SIGHUP): zero downtime, socket nunca cai
OTHER_SERVICES=(sistema_questoes_celery.service)
HEALTH_URL="https://questoes.stolben.com/"   # sem /healthz/ neste app; home redireciona pro login (302)
HEALTH_HEADER=""
BACKUP_SCRIPT=/var/www/sistema_questoes/shared/scripts/backup_postgres.sh
EXTRA_ENV=""
LOCK_FILE=/tmp/sistema_questoes_cd_deploy.lock

# Versão realmente instalada no venv, ou "ausente". Consultar a metadata em vez
# de `gunicorn --version` não depende do formato de saída do CLI.
versao_instalada() {
  "$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('$1'))" 2>/dev/null \
    || echo ausente
}

# O mestre do gunicorn carrega o pacote uma vez, no boot. Se o pacote instalado
# é mais novo que o início do serviço, o que está no ar é código velho — sobra
# de um deploy anterior que instalou sem reiniciar. SIGHUP não corrige isso.
gunicorn_mais_novo_que_o_mestre() {
  local pkg inicio t_pkg t_svc
  pkg="$(ls -d "$VENV"/lib/python*/site-packages/gunicorn 2>/dev/null | head -1)"
  [[ -d "$pkg" ]] || return 1
  inicio="$(systemctl show -p ActiveEnterTimestamp --value "$WEB_SERVICE")"
  [[ -n "$inicio" ]] || return 1
  t_pkg="$(stat -c %Y "$pkg")" || return 1
  t_svc="$(date -d "$inicio" +%s 2>/dev/null)" || return 1
  (( t_pkg > t_svc ))
}

main() {
  local sha
  sha="$(printf '%s' "${SSH_ORIGINAL_COMMAND:-}" | awk '{print $2}')"
  [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || { echo "SHA inválido: '$sha'"; exit 1; }

  cd "$APP_DIR"
  git fetch "$FETCH_URL" main
  git merge-base --is-ancestor "$sha" FETCH_HEAD \
    || { echo "SHA não é ancestral do main remoto: $sha"; exit 1; }

  local antes; antes="$(git rev-parse HEAD)"

  local tem_migracao
  tem_migracao="$(git diff --name-only "HEAD..$sha" -- '*/migrations/*')"

  if [[ -n "$tem_migracao" && -n "$BACKUP_SCRIPT" ]]; then
    "$BACKUP_SCRIPT"
  fi

  git merge --ff-only "$sha"

  # O pip roda sempre, e não sob um `git diff` contra HEAD: o merge acima já
  # moveu o HEAD, então numa reexecução depois de uma falha o diff sai vazio e
  # o install seria pulado — deploy verde sem ter instalado nada. Rodar sempre
  # custa poucos segundos quando não há o que fazer, e torna o script
  # idempotente sob retry.
  local gunicorn_antes gunicorn_depois
  gunicorn_antes="$(versao_instalada gunicorn)"
  "$VENV/bin/pip" install -r requirements.txt
  gunicorn_depois="$(versao_instalada gunicorn)"

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  [[ -n "$EXTRA_ENV" ]] && eval "export $EXTRA_ENV"
  set +a

  [[ -n "${DJANGO_HEALTHZ_TOKEN:-}" ]] && HEALTH_HEADER="X-Healthz-Token: $DJANGO_HEALTHZ_TOKEN"

  "$VENV/bin/python" manage.py check --deploy --fail-level ERROR
  "$VENV/bin/python" manage.py migrate --check || "$VENV/bin/python" manage.py migrate
  "$VENV/bin/python" manage.py collectstatic --noinput

  # SIGHUP recicla os workers mas não reexecuta o mestre: um gunicorn novo fica
  # no venv sem entrar em vigor. Duas perguntas à realidade do servidor, e não
  # ao diff do git — ver rigst/ci RUNBOOK.md seção 7.1.2.
  if [[ "$gunicorn_antes" != "$gunicorn_depois" ]] || gunicorn_mais_novo_que_o_mestre; then
    sudo systemctl restart "$WEB_SERVICE"
  else
    sudo systemctl reload "$WEB_SERVICE"
  fi
  for unidade in "${OTHER_SERVICES[@]}"; do
    sudo systemctl restart "$unidade"
  done

  if [[ -n "$HEALTH_URL" ]]; then
    local codigo
    for _ in 1 2 3 4 5; do
      codigo="$(curl -s -o /dev/null -w '%{http_code}' ${HEALTH_HEADER:+-H "$HEALTH_HEADER"} "$HEALTH_URL")"
      [[ "$codigo" =~ ^[23][0-9][0-9]$ ]] && break
      sleep 2
    done
    if [[ ! "$codigo" =~ ^[23][0-9][0-9]$ ]]; then
      echo "Smoke-test falhou ($codigo). Rollback manual: git -C $APP_DIR reset --hard $antes"
      exit 1
    fi
  fi

  echo "Deploy de $sha concluído (era $antes)."
}

(
  flock -n 9 || { echo "Deploy já em andamento, saindo."; exit 1; }
  main "$@"
) 9>"$LOCK_FILE"
