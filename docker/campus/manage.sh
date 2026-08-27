#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CAMPUS_ENV_FILE="${CAMPUS_ENV_FILE:-${DOCKER_DIR}/envs/campus.env}"
BACKUP_ROOT="${CAMPUS_BACKUP_ROOT:-${SCRIPT_DIR}/backups}"
PUBLIC_COMPOSE_FILE="${DOCKER_DIR}/docker-compose.campus-public.yaml"
UPSTREAM_LOOPBACK_FILE="${SCRIPT_DIR}/upstream-loopback.yaml"

env_value() {
  local key="$1"
  [[ -f "${CAMPUS_ENV_FILE}" ]] || return 0
  awk -v key="${key}" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' "${CAMPUS_ENV_FILE}"
}

configure_compose() {
  COMPOSE=(
    docker compose
    --env-file "${DOCKER_DIR}/.env"
    --env-file "${CAMPUS_ENV_FILE}"
    --profile postgresql
    -f docker-compose.yaml
    -f docker-compose.campus.yaml
  )
  if [[ "$(env_value CAMPUS_PUBLIC_ENTRY_ENABLED)" == "true" ]]; then
    COMPOSE+=(-f "${PUBLIC_COMPOSE_FILE}")
  fi
}

configure_compose

UPSTREAM_DOCKER_DIR="$(env_value CAMPUS_UPSTREAM_DOCKER_DIR)"
UPSTREAM_DOCKER_DIR="${UPSTREAM_DOCKER_DIR:-/mnt/d/Projects/dify-main/docker}"
UPSTREAM_PROJECT_NAME="$(env_value CAMPUS_UPSTREAM_PROJECT_NAME)"
UPSTREAM_PROJECT_NAME="${UPSTREAM_PROJECT_NAME:-dify-mian-20260725}"
UPSTREAM_HTTP_PORT="$(env_value CAMPUS_UPSTREAM_HTTP_PORT)"
UPSTREAM_HTTP_PORT="${UPSTREAM_HTTP_PORT:-18082}"
UPSTREAM_HTTPS_PORT="$(env_value CAMPUS_UPSTREAM_HTTPS_PORT)"
UPSTREAM_HTTPS_PORT="${UPSTREAM_HTTPS_PORT:-18444}"
UPSTREAM_COMPOSE=(
  docker compose
  --env-file "${UPSTREAM_DOCKER_DIR}/.env"
  --project-name "${UPSTREAM_PROJECT_NAME}"
  -f "${UPSTREAM_DOCKER_DIR}/docker-compose.yaml"
)
UPSTREAM_LOOPBACK_COMPOSE=(
  env
  "CAMPUS_UPSTREAM_HTTP_PORT=${UPSTREAM_HTTP_PORT}"
  "CAMPUS_UPSTREAM_HTTPS_PORT=${UPSTREAM_HTTPS_PORT}"
  "${UPSTREAM_COMPOSE[@]}"
  -f "${UPSTREAM_LOOPBACK_FILE}"
)

cd "${DOCKER_DIR}"

fail() {
  echo "campus-manage: $*" >&2
  exit 1
}

set_env_value() {
  local key="$1" value="$2" env_tmp
  [[ "${key}" =~ ^[A-Z0-9_]+$ ]] || fail "invalid environment key"
  env_tmp="$(mktemp "${CAMPUS_ENV_FILE}.XXXXXX")"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "${CAMPUS_ENV_FILE}" >"${env_tmp}"
  chmod --reference="${CAMPUS_ENV_FILE}" "${env_tmp}"
  mv "${env_tmp}" "${CAMPUS_ENV_FILE}"
}

validate_gateway_build_images() {
  local key expected value
  while read -r key expected; do
    value="$(env_value "${key}")"
    [[ -z "${value}" || "${value}" == *@sha256:"${expected}" ]] || \
      fail "${key} must retain the approved sha256 digest"
  done <<'EOF'
CAMPUS_GATEWAY_BUN_IMAGE 0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7
CAMPUS_GATEWAY_GO_IMAGE 2389ebfa5b7f43eeafbd6be0c3700cc46690ef842ad962f6c5bd6be49ed82039
CAMPUS_GATEWAY_RUNTIME_IMAGE f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a
EOF
  value="$(env_value CAMPUS_DIFY_API_BASE_IMAGE)"
  [[ -z "${value}" || "${value}" == *@sha256:bd3e8b15cfc47e89dc7a0d17431e6f3289244f4b442b96e2372bd0f0646f3d58 ]] || \
    fail "CAMPUS_DIFY_API_BASE_IMAGE must retain the approved Dify 1.16.0 sha256 digest"
  value="$(env_value CAMPUS_GATEWAY_GO_PROXY)"
  case "${value}" in
    ""|https://proxy.golang.org,direct|https://goproxy.cn,direct) ;;
    *) fail "CAMPUS_GATEWAY_GO_PROXY must use an approved HTTPS Go module proxy" ;;
  esac
}

validate() {
  command -v docker >/dev/null || fail "docker is required"
  command -v curl >/dev/null || fail "curl is required"
  [[ -f "${DOCKER_DIR}/.env" ]] || fail "missing ${DOCKER_DIR}/.env"
  [[ -f "${CAMPUS_ENV_FILE}" ]] || fail "missing ${CAMPUS_ENV_FILE}"
  if grep -Ev '^[[:space:]]*#' "${CAMPUS_ENV_FILE}" | grep -Fq 'change-me'; then
    fail "replace all change-me placeholders in the Campus environment file"
  fi
  validate_gateway_build_images
  "${SCRIPT_DIR}/test-nginx-routes.sh"
  "${SCRIPT_DIR}/test-public-entry.sh"
  "${COMPOSE[@]}" config --quiet
}

validate_gateway_bootstrap() {
  command -v docker >/dev/null || fail "docker is required"
  [[ -f "${DOCKER_DIR}/.env" ]] || fail "missing ${DOCKER_DIR}/.env"
  [[ -f "${CAMPUS_ENV_FILE}" ]] || fail "missing ${CAMPUS_ENV_FILE}"
  validate_gateway_build_images
  local key value
  for key in CAMPUS_GATEWAY_DB_USER CAMPUS_GATEWAY_DB_PASSWORD CAMPUS_GATEWAY_DB_DSN \
    CAMPUS_GATEWAY_REDIS_PASSWORD CAMPUS_GATEWAY_REDIS_CONN_STRING CAMPUS_GATEWAY_SESSION_SECRET; do
    value="$(env_value "${key}")"
    [[ -n "${value}" && "${value}" != *change-me* ]] || fail "set ${key} before gateway bootstrap"
  done
}

checksum_manifest() {
  local directory="$1"
  local manifest_tmp
  manifest_tmp="$(mktemp)"
  if command -v sha256sum >/dev/null; then
    (cd "${directory}" && sha256sum -- *) >"${manifest_tmp}"
  else
    (cd "${directory}" && shasum -a 256 -- *) >"${manifest_tmp}"
  fi
  mv "${manifest_tmp}" "${directory}/SHA256SUMS"
}

service_running() {
  local service="$1"
  "${COMPOSE[@]}" ps --status running --services | grep -qx "${service}"
}

require_running_service() {
  local service="$1"
  service_running "${service}" || fail "${service} is not running"
}

assert_service_healthy() {
  local service="$1" container_id health
  container_id="$("${COMPOSE[@]}" ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "${service} container is missing"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}")"
  [[ "${health}" == "healthy" ]] || fail "${service} is not healthy"
}

assert_service_never_restarted() {
  local service="$1" container_id restart_count
  container_id="$("${COMPOSE[@]}" ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "${service} container is missing"
  restart_count="$(docker inspect --format '{{.RestartCount}}' "${container_id}")"
  [[ "${restart_count}" == "0" ]] || fail "${service} restarted ${restart_count} times"
}

wait_for_campus_health() {
  local campus_port="$1" attempt
  for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 5 \
      "http://127.0.0.1:${campus_port}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  fail "Campus API did not become healthy within 60 seconds"
}

assert_portal_root_redirect() {
  local url="$1" status redirect_url
  read -r status redirect_url < <(curl --silent --output /dev/null \
    --write-out '%{http_code} %{redirect_url}\n' --max-time 10 "${url}")
  [[ "${status}" == "302" && "${redirect_url}" == */portal/ ]] || \
    fail "${url} does not redirect to the Access portal"
}

service_has_state() {
  local service="$1"
  if [[ -n "$("${COMPOSE[@]}" ps -aq "${service}" 2>/dev/null)" ]]; then
    return 0
  fi
  if [[ "${service}" == "db_postgres" ]]; then
    [[ -d volumes/db/data && -n "$(find volumes/db/data -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]
    return
  fi
  if [[ "${service}" == "model-gateway-db" ]]; then
    local project_name
    project_name="$(env_value COMPOSE_PROJECT_NAME)"
    project_name="${project_name:-njit-campus}"
    [[ -n "$(docker volume ls --quiet \
      --filter "label=com.docker.compose.project=${project_name}" \
      --filter "label=com.docker.compose.volume=campus_gateway_postgres")" ]]
    return
  fi
  return 1
}

project_has_state() {
  if [[ -n "$("${COMPOSE[@]}" ps -aq 2>/dev/null)" ]]; then
    return 0
  fi
  local project_name path
  project_name="$(env_value COMPOSE_PROJECT_NAME)"
  project_name="${project_name:-njit-campus}"
  if [[ -n "$(docker volume ls --quiet --filter "label=com.docker.compose.project=${project_name}")" ]]; then
    return 0
  fi
  for path in volumes/db/data volumes/app/storage volumes/plugin_daemon volumes/weaviate; do
    if [[ -d "${path}" && -n "$(find "${path}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      return 0
    fi
  done
  return 1
}

backup() {
  validate
  local stamp destination
  for database_service in db_postgres model-gateway-db; do
    if service_has_state "${database_service}" && ! service_running "${database_service}"; then
      fail "${database_service} has persistent state but is stopped; start it and run backup before deployment"
    fi
  done
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="${BACKUP_ROOT}/${stamp}"
  umask 077
  mkdir -p "${destination}"

  if service_running db_postgres; then
    "${COMPOSE[@]}" exec -T db_postgres sh -ec \
      'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall --clean --if-exists -U "$POSTGRES_USER"' \
      >"${destination}/dify-postgres.sql"
  fi
  if service_running model-gateway-db; then
    "${COMPOSE[@]}" exec -T model-gateway-db sh -ec \
      'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall --clean --if-exists -U "$POSTGRES_USER"' \
      >"${destination}/model-gateway-postgres.sql"
  fi

  local persistent_paths=()
  for path in app/storage plugin_daemon weaviate; do
    [[ -e "volumes/${path}" ]] && persistent_paths+=("${path}")
  done
  if ((${#persistent_paths[@]})); then
    tar -C volumes -czf "${destination}/dify-files.tgz" "${persistent_paths[@]}"
  fi
  tar -czf "${destination}/configuration.tgz" .env \
    -C "$(dirname -- "${CAMPUS_ENV_FILE}")" "$(basename -- "${CAMPUS_ENV_FILE}")"
  "${COMPOSE[@]}" config --images | sort -u >"${destination}/configured-images.txt"
  "${COMPOSE[@]}" ps --all --format json >"${destination}/containers.json"
  checksum_manifest "${destination}"
  echo "${destination}"
}

verify() {
  validate
  local campus_bind campus_port admin_port gateway_port baseline_url status published container_id nginx_config nginx_location portal_networks
  local public_enabled public_bind public_port upstream_port
  campus_bind="$(env_value CAMPUS_NGINX_BIND_ADDRESS)"
  campus_port="$(env_value EXPOSE_NGINX_PORT)"
  admin_port="$(env_value CAMPUS_ADMIN_PORT)"
  gateway_port="$(env_value CAMPUS_GATEWAY_ADMIN_PORT)"
  baseline_url="$(env_value CAMPUS_BASELINE_URL)"
  public_enabled="$(env_value CAMPUS_PUBLIC_ENTRY_ENABLED)"
  public_bind="$(env_value CAMPUS_PUBLIC_BIND_ADDRESS)"
  public_port="$(env_value CAMPUS_PUBLIC_HTTP_PORT)"
  upstream_port="$(env_value CAMPUS_UPSTREAM_HTTP_PORT)"
  campus_bind="${campus_bind:-127.0.0.1}"
  campus_port="${campus_port:-18080}"
  admin_port="${admin_port:-18081}"
  gateway_port="${gateway_port:-13000}"
  baseline_url="${baseline_url:-http://127.0.0.1/}"
  public_bind="${public_bind:-10.20.10.193}"
  public_port="${public_port:-80}"
  upstream_port="${upstream_port:-18082}"
  if [[ "${public_enabled}" == "true" ]]; then
    baseline_url="http://127.0.0.1:${upstream_port}/"
  fi

  for service in api portal model-gateway worker worker_beat nginx; do
    require_running_service "${service}"
  done
  wait_for_campus_health "${campus_port}"
  for service in api portal model-gateway; do
    assert_service_healthy "${service}"
  done
  for service in worker worker_beat; do
    assert_service_never_restarted "${service}"
  done
  if "${COMPOSE[@]}" ps --all --services | grep -qx api_websocket; then
    require_running_service api_websocket
    assert_service_never_restarted api_websocket
  fi
  published="$("${COMPOSE[@]}" port nginx 80)"
  if [[ "${public_enabled}" == "true" ]]; then
    printf '%s\n' "${published}" | grep -Fxq "${public_bind}:${public_port}" || \
      fail "Campus public entry is not bound to ${public_bind}:${public_port}"
    printf '%s\n' "${published}" | grep -Fxq "127.0.0.1:${campus_port}" || \
      fail "Campus loopback verification route is missing"
  else
    [[ "${published}" == "${campus_bind}:${campus_port}" ]] || \
      fail "Campus Dify bind differs from protected configuration"
  fi
  published="$("${COMPOSE[@]}" port nginx 8081)"
  [[ "${published}" == "127.0.0.1:${admin_port}" ]] || fail "Campus administration listener is not loopback-only"
  nginx_location="$(sed -n '/^[[:space:]]*location ~ /{s/^[[:space:]]*//;p;q;}' \
    "${SCRIPT_DIR}/nginx/default.conf.template")"
  nginx_config="$("${COMPOSE[@]}" exec -T nginx nginx -T 2>&1)"
  printf '%s\n' "${nginx_config}" | grep -Fq "${nginx_location}" || \
    fail "running nginx does not contain the Campus bootstrap route boundary"
  printf '%s\n' "${nginx_config}" | grep -Fq 'return 302 /portal/;' || \
    fail "running nginx does not make the Access portal the default entry"
  assert_portal_root_redirect "http://127.0.0.1:${campus_port}/"

  status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/portal/")"
  [[ "${status}" == "200" ]] || fail "Campus Access portal is unavailable (HTTP ${status})"

  if [[ "${public_enabled}" == "true" ]]; then
    verify_public_firewall "${public_port}"
    assert_portal_root_redirect "http://${public_bind}:${public_port}/"
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
      "http://${public_bind}:${public_port}/portal/")"
    [[ "${status}" == "200" ]] || fail "public Campus Access portal is unavailable (HTTP ${status})"
    published="$("${UPSTREAM_LOOPBACK_COMPOSE[@]}" port nginx 80)"
    [[ "${published}" == "127.0.0.1:${upstream_port}" ]] || \
      fail "upstream Dify rollback route is not loopback-only"
  fi

  status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/signin")"
  [[ "${status}" == "404" ]] || fail "public Dify sign-in route was not blocked (HTTP ${status})"

  container_id="$("${COMPOSE[@]}" ps -q portal)"
  [[ -n "${container_id}" && -z "$(docker port "${container_id}" 2>/dev/null || true)" ]] || \
    fail "Campus Access portal publishes a host port"
  portal_networks="$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' "${container_id}")"
  [[ "$(printf '%s\n' "${portal_networks}" | sed '/^$/d' | wc -l | tr -d ' ')" == "1" ]] || \
    fail "Campus Access portal is attached to more than one network"
  printf '%s\n' "${portal_networks}" | grep -q 'campus_portal$' || \
    fail "Campus Access portal is not attached to its isolated network"

  status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/console/api/apps")"
  [[ "${status}" == "401" ]] || fail "direct workspace API was not rejected (HTTP ${status})"

  published="$("${COMPOSE[@]}" port model-gateway 3000)"
  [[ "${published}" == "127.0.0.1:${gateway_port}" ]] || fail "model gateway is not loopback-only"
  curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${gateway_port}/api/status" | grep -q '"success":true' || \
    fail "model gateway status endpoint is unhealthy"
  container_id="$("${COMPOSE[@]}" ps -q model-gateway-db)"
  [[ -n "${container_id}" && -z "$(docker port "${container_id}" 5432 2>/dev/null || true)" ]] || \
    fail "gateway database is published"
  container_id="$("${COMPOSE[@]}" ps -q model-gateway-redis)"
  [[ -n "${container_id}" && -z "$(docker port "${container_id}" 6379 2>/dev/null || true)" ]] || \
    fail "gateway Redis is published"
  container_id="$("${COMPOSE[@]}" ps -q plugin_daemon)"
  [[ -n "${container_id}" && -z "$(docker port "${container_id}" 2>/dev/null || true)" ]] || \
    fail "plugin daemon is published"
  curl --fail --silent --show-error --max-time 10 "${baseline_url}" >/dev/null
}

open_bootstrap() {
  validate
  local campus_bind campus_port admin_port setup_step init_status env_tmp published
  campus_bind="$(env_value CAMPUS_NGINX_BIND_ADDRESS)"
  campus_port="$(env_value EXPOSE_NGINX_PORT)"
  admin_port="$(env_value CAMPUS_ADMIN_PORT)"
  campus_bind="${campus_bind:-127.0.0.1}"
  campus_port="${campus_port:-18080}"
  admin_port="${admin_port:-18081}"
  [[ "${campus_bind}" == "127.0.0.1" ]] || fail "administrator bootstrap requires a loopback-only Campus bind"

  setup_step="$(curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${admin_port}/console/api/setup" | \
    sed -n 's/.*"step":"\([^"]*\)".*/\1/p')"
  [[ "${setup_step}" == "not_started" ]] || fail "Dify setup is not waiting for first-time bootstrap"

  backup >/dev/null
  env_tmp="$(mktemp "${DOCKER_DIR}/.env.bootstrap.XXXXXX")"
  awk '
    BEGIN { replaced = 0 }
    /^INIT_PASSWORD=/ { print "INIT_PASSWORD="; replaced = 1; next }
    { print }
    END { if (!replaced) print "INIT_PASSWORD=" }
  ' "${DOCKER_DIR}/.env" >"${env_tmp}"
  chmod --reference="${DOCKER_DIR}/.env" "${env_tmp}"
  mv "${env_tmp}" "${DOCKER_DIR}/.env"

  "${COMPOSE[@]}" up -d --no-deps --force-recreate api nginx
  published="$("${COMPOSE[@]}" port nginx 80)"
  [[ "${published}" == "127.0.0.1:${campus_port}" ]] || fail "administrator bootstrap is not loopback-only"
  published="$("${COMPOSE[@]}" port nginx 8081)"
  [[ "${published}" == "127.0.0.1:${admin_port}" ]] || fail "administrator listener is not loopback-only"
  wait_for_campus_health "${campus_port}"
  init_status="$(curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${admin_port}/console/api/init" | \
    sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
  [[ "${init_status}" == "finished" ]] || fail "Dify initialization gate did not open"
}

validate_upstream() {
  [[ -f "${UPSTREAM_DOCKER_DIR}/.env" ]] || fail "missing upstream environment file"
  [[ -f "${UPSTREAM_DOCKER_DIR}/docker-compose.yaml" ]] || fail "missing upstream Compose file"
  [[ -f "${UPSTREAM_LOOPBACK_FILE}" ]] || fail "missing upstream loopback override"
  "${UPSTREAM_COMPOSE[@]}" config --quiet
  "${UPSTREAM_LOOPBACK_COMPOSE[@]}" config --quiet
}

assert_public_bind_is_local() {
  local public_bind="$1"
  case "${public_bind}" in
    0.0.0.0|127.0.0.1|localhost|"" ) fail "public entry requires a specific campus interface address" ;;
  esac
  ip -4 -o address show | awk '{print $4}' | grep -Eq "^${public_bind//./\\.}/" || \
    fail "public Campus address ${public_bind} is not assigned to this host"
}

verify_public_firewall() {
  local public_port="$1" remote_address windows_script
  command -v powershell.exe >/dev/null || fail "powershell.exe is required for public-entry firewall verification"
  command -v wslpath >/dev/null || fail "wslpath is required for public-entry firewall verification"
  remote_address="$(env_value CAMPUS_PUBLIC_REMOTE_ADDRESS)"
  remote_address="${remote_address:-10.0.0.0/255.0.0.0}"
  windows_script="$(wslpath -w "${SCRIPT_DIR}/windows/configure-intranet-firewall.ps1")"
  powershell.exe -NoProfile -NonInteractive -File "${windows_script}" \
    -Action Verify -Port "${public_port}" -RemoteAddress "${remote_address}"
}

assert_port_owner() {
  local port="$1" expected_container_id="$2"
  local owners=()
  mapfile -t owners < <(docker ps --quiet --filter "publish=${port}")
  [[ "${#owners[@]}" == "1" && "${owners[0]}" == "${expected_container_id}" ]] || \
    fail "TCP ${port} is not owned exclusively by the expected nginx container"
}

restore_upstream_public_entry() {
  "${UPSTREAM_COMPOSE[@]}" up -d --no-deps --force-recreate nginx
}

verify_demo_accounts() {
  validate
  local admin_summary admin_count admin_name_count student_count student_login_count virtual_count admin_names student_names virtual_names line
  admin_summary="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "WITH eligible AS (SELECT btrim(a.name) AS name FROM campus_administrators ca JOIN accounts a ON a.id=ca.account_id WHERE ca.active AND a.status=\$\$active\$\$ AND a.password IS NOT NULL AND a.last_login_at IS NOT NULL AND length(btrim(a.name)) > 0), summary AS (SELECT 1 AS sort_key, \$\$count=\$\$ || COUNT(*)::text AS value FROM eligible UNION ALL SELECT 2, \$\$distinct=\$\$ || COUNT(DISTINCT lower(name))::text FROM eligible UNION ALL SELECT 3, \$\$name=\$\$ || name FROM eligible) SELECT value FROM summary ORDER BY sort_key, value;"')"
  admin_count=""
  admin_name_count=""
  admin_names=""
  while IFS= read -r line; do
    case "${line}" in
      count=*) admin_count="${line#count=}" ;;
      distinct=*) admin_name_count="${line#distinct=}" ;;
      name=*)
        [[ -z "${admin_names}" ]] || admin_names+=$'\n'
        admin_names+="${line#name=}"
        ;;
    esac
  done <<<"${admin_summary}"
  student_count="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT COUNT(*) FROM campus_students WHERE status=\$\$active\$\$;"')"
  student_login_count="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT COUNT(DISTINCT student.id) FROM campus_students student JOIN campus_portal_sessions portal_session ON portal_session.student_id=student.id WHERE student.status=\$\$active\$\$ AND portal_session.revoked_at IS NULL AND portal_session.expires_at > CURRENT_TIMESTAMP;"')"
  virtual_count="$("${COMPOSE[@]}" exec -T api python -c \
    'import json, os; rows=json.loads(os.environ["CAMPUS_VIRTUAL_IDENTITIES_JSON"]); print(len(rows))')"
  [[ "${admin_count}" == "2" && "${admin_name_count}" == "2" ]] || \
    fail "expected two distinct active administrators with successful logins"
  [[ "${student_count}" == "2" ]] || fail "expected two active demo student identities"
  [[ "${student_login_count}" == "2" ]] || fail "expected successful current portal login for both demo students"
  [[ "${virtual_count}" == "2" ]] || fail "expected two configured virtual demonstration identities"

  student_names="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT student_number || chr(32) || display_name FROM campus_students WHERE status=\$\$active\$\$ ORDER BY student_number;"')"
  virtual_names="$("${COMPOSE[@]}" exec -T api python -c \
    'import json, os; rows=json.loads(os.environ["CAMPUS_VIRTUAL_IDENTITIES_JSON"]); print("\\n".join(sorted("{0} {1}".format(row.get("student_number"), row.get("display_name")) for row in rows)))')"
  [[ "${student_names}" == "${virtual_names}" ]] || fail "active students do not match the protected virtual demo roster"
  printf 'Named administrators:\n%s\nDemo students:\n%s\n' "${admin_names}" "${student_names}"
}

promote() {
  validate
  validate_upstream
  if [[ "$(env_value CAMPUS_PUBLIC_ENTRY_ENABLED)" == "true" ]]; then
    verify
    echo "Campus Access portal is already the public entry."
    return
  fi

  local public_bind public_port upstream_port upstream_container_id backup_destination published
  public_bind="$(env_value CAMPUS_PUBLIC_BIND_ADDRESS)"
  public_port="$(env_value CAMPUS_PUBLIC_HTTP_PORT)"
  upstream_port="$(env_value CAMPUS_UPSTREAM_HTTP_PORT)"
  public_bind="${public_bind:-10.20.10.193}"
  public_port="${public_port:-80}"
  upstream_port="${upstream_port:-18082}"
  [[ "${public_port}" == "80" ]] || fail "the default Campus HTTP entry must use TCP 80"
  assert_public_bind_is_local "${public_bind}"
  verify_public_firewall "${public_port}"

  verify
  verify_demo_accounts
  upstream_container_id="$("${UPSTREAM_COMPOSE[@]}" ps -q nginx)"
  [[ -n "${upstream_container_id}" ]] || fail "upstream nginx is not running"
  assert_port_owner "${public_port}" "${upstream_container_id}"
  backup_destination="$(backup)"

  "${UPSTREAM_LOOPBACK_COMPOSE[@]}" up -d --no-deps --force-recreate nginx
  published="$("${UPSTREAM_LOOPBACK_COMPOSE[@]}" port nginx 80)"
  if [[ "${published}" != "127.0.0.1:${upstream_port}" ]] || \
    ! curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${upstream_port}/" >/dev/null; then
    restore_upstream_public_entry || true
    fail "upstream Dify could not be preserved on its loopback rollback route"
  fi

  set_env_value CAMPUS_PUBLIC_BIND_ADDRESS "${public_bind}"
  set_env_value CAMPUS_PUBLIC_HTTP_PORT "${public_port}"
  set_env_value CAMPUS_UPSTREAM_HTTP_PORT "${upstream_port}"
  set_env_value CAMPUS_PUBLIC_ENTRY_ENABLED true
  configure_compose
  if ! "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx; then
    set_env_value CAMPUS_PUBLIC_ENTRY_ENABLED false
    configure_compose
    restore_upstream_public_entry || true
    fail "Campus public nginx failed to start; upstream entry was restored"
  fi
  if ! (verify); then
    set_env_value CAMPUS_PUBLIC_ENTRY_ENABLED false
    configure_compose
    "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx || true
    restore_upstream_public_entry || true
    fail "Campus public verification failed; upstream entry was restored"
  fi
  echo "Campus Access portal promoted; rollback backup: ${backup_destination}"
}

rollback_promotion() {
  validate
  validate_upstream
  if [[ "$(env_value CAMPUS_PUBLIC_ENTRY_ENABLED)" != "true" ]]; then
    echo "Campus public-entry promotion is not active."
    return
  fi

  local backup_destination
  backup_destination="$(backup)"
  set_env_value CAMPUS_PUBLIC_ENTRY_ENABLED false
  configure_compose
  if ! "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx; then
    set_env_value CAMPUS_PUBLIC_ENTRY_ENABLED true
    configure_compose
    "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx || true
    fail "Campus nginx could not release public port 80"
  fi
  restore_upstream_public_entry
  verify
  echo "Campus promotion rolled back; rollback backup: ${backup_destination}"
}

deploy() {
  validate
  if project_has_state; then
    backup >/dev/null
  fi
  "${COMPOSE[@]}" up -d --build
  verify
}

usage() {
  echo "usage: $0 {gateway-up|validate|backup|deploy|verify|verify-demo-accounts|open-bootstrap|promote|rollback-promotion|stop}" >&2
  exit 2
}

case "${1:-}" in
  gateway-up)
    validate_gateway_bootstrap
    if project_has_state; then
      validate
      backup >/dev/null
    fi
    CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN=bootstrap \
      "${COMPOSE[@]}" up -d --build model-gateway-db model-gateway-redis model-gateway
    ;;
  validate) validate ;;
  backup) backup ;;
  deploy) deploy ;;
  verify) verify ;;
  verify-demo-accounts) verify_demo_accounts ;;
  open-bootstrap) open_bootstrap ;;
  promote) promote ;;
  rollback-promotion) rollback_promotion ;;
  stop)
    validate
    "${COMPOSE[@]}" down
    ;;
  *) usage ;;
esac
