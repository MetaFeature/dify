#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CAMPUS_ENV_FILE="${CAMPUS_ENV_FILE:-${DOCKER_DIR}/envs/campus.env}"
BACKUP_ROOT="${CAMPUS_BACKUP_ROOT:-${SCRIPT_DIR}/backups}"
COMPOSE=(
  docker compose
  --env-file "${DOCKER_DIR}/.env"
  --env-file "${CAMPUS_ENV_FILE}"
  --profile postgresql
  -f docker-compose.yaml
  -f docker-compose.campus.yaml
)

cd "${DOCKER_DIR}"

fail() {
  echo "campus-manage: $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  awk -v key="${key}" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' "${CAMPUS_ENV_FILE}"
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
  local campus_port gateway_port baseline_url status published
  campus_port="$(env_value EXPOSE_NGINX_PORT)"
  gateway_port="$(env_value CAMPUS_GATEWAY_ADMIN_PORT)"
  baseline_url="$(env_value CAMPUS_BASELINE_URL)"
  campus_port="${campus_port:-18080}"
  gateway_port="${gateway_port:-13000}"
  baseline_url="${baseline_url:-http://127.0.0.1/}"

  "${COMPOSE[@]}" ps --status running --services | grep -qx api || fail "Campus API is not running"
  "${COMPOSE[@]}" ps --status running --services | grep -qx model-gateway || fail "model gateway is not running"
  curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${campus_port}/health" >/dev/null

  status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/console/api/apps")"
  [[ "${status}" == "401" ]] || fail "direct workspace API was not rejected (HTTP ${status})"

  published="$("${COMPOSE[@]}" port model-gateway 3000)"
  [[ "${published}" == "127.0.0.1:${gateway_port}" ]] || fail "model gateway is not loopback-only"
  [[ -z "$("${COMPOSE[@]}" port model-gateway-db 5432 2>/dev/null || true)" ]] || fail "gateway database is published"
  [[ -z "$("${COMPOSE[@]}" port model-gateway-redis 6379 2>/dev/null || true)" ]] || fail "gateway Redis is published"
  curl --fail --silent --show-error --max-time 10 "${baseline_url}" >/dev/null
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
  echo "usage: $0 {gateway-up|validate|backup|deploy|verify|stop}" >&2
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
  stop)
    validate
    "${COMPOSE[@]}" down
    ;;
  *) usage ;;
esac
