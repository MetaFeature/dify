#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CAMPUS_ENV_FILE="${CAMPUS_ENV_FILE:-${DOCKER_DIR}/envs/campus.env}"
BACKUP_ROOT="${CAMPUS_BACKUP_ROOT:-${SCRIPT_DIR}/backups}"
PUBLIC_COMPOSE_FILE="${DOCKER_DIR}/docker-compose.campus-public.yaml"
UPSTREAM_LOOPBACK_FILE="${SCRIPT_DIR}/upstream-loopback.yaml"
APPROVED_PROVIDER_PLUGIN_FILE="${SCRIPT_DIR}/approved-provider-plugin.txt"
BRANDING_DIR="${SCRIPT_DIR}/branding"
BRANDING_LOGO_FILE="${BRANDING_DIR}/njit-logo.png"
BRANDING_LOGO_MANIFEST="${BRANDING_DIR}/SHA256SUMS"
WORKSPACE_ISOLATION_SQL="${SCRIPT_DIR}/verify-workspace-isolation.sql"
APPROVED_MODEL_PROVIDER="langgenius/openai_api_compatible/openai_api_compatible"
APPROVED_MODEL_CREDENTIAL_SCOPE="model"
APPROVED_MODEL_API_KEY_FIELD="api_key"
APPROVED_MODEL_BASE_URL_FIELD="endpoint_url"

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

local_curl() {
  command curl --noproxy '*' "$@"
}

branding_logo_sha256() {
  awk '$2 == "njit-logo.png" && length($1) == 64 { print $1; exit }' \
    "${BRANDING_LOGO_MANIFEST}"
}

validate_branding_logo_source() {
  local expected_sha256 actual_sha256
  command -v sha256sum >/dev/null || fail "sha256sum is required"
  [[ -f "${BRANDING_LOGO_FILE}" ]] || fail "missing Campus Dify branding logo"
  [[ -f "${BRANDING_LOGO_MANIFEST}" ]] || fail "missing Campus Dify branding manifest"
  expected_sha256="$(branding_logo_sha256)"
  [[ "${expected_sha256}" =~ ^[0-9a-f]{64}$ ]] || fail "invalid Campus Dify branding manifest"
  actual_sha256="$(sha256sum "${BRANDING_LOGO_FILE}" | awk '{print $1}')"
  [[ "${actual_sha256}" == "${expected_sha256}" ]] || \
    fail "Campus Dify branding logo does not match the approved asset"
}

assert_branding_logo() {
  local url="$1" content_type expected_sha256 actual_sha256
  content_type="$(local_curl --fail --silent --show-error --max-time 10 \
    --output /dev/null --write-out '%{content_type}' "${url}")"
  [[ "${content_type}" == "image/png" ]] || fail "${url} does not serve the Campus Dify logo as PNG"
  expected_sha256="$(branding_logo_sha256)"
  actual_sha256="$(local_curl --fail --silent --show-error --max-time 10 "${url}" | \
    sha256sum | awk '{print $1}')"
  [[ "${actual_sha256}" == "${expected_sha256}" ]] || \
    fail "${url} does not serve the approved Campus Dify logo"
}

set_env_value() {
  local key="$1" value="$2" env_tmp source_mode
  [[ "${key}" =~ ^[A-Z0-9_]+$ ]] || fail "invalid environment key"
  env_tmp="$(mktemp "${CAMPUS_ENV_FILE}.XXXXXX")"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "${CAMPUS_ENV_FILE}" >"${env_tmp}"
  source_mode="$(stat -c '%a' "${CAMPUS_ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${CAMPUS_ENV_FILE}")"
  [[ "${source_mode}" =~ ^[0-7]{3,4}$ ]] || fail "could not preserve Campus environment file mode"
  chmod "${source_mode}" "${env_tmp}"
  mv "${env_tmp}" "${CAMPUS_ENV_FILE}"
}

validate_gateway_build_images() {
  local key expected value approved_provider_plugin
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
  value="$(env_value CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER)"
  [[ -f "${APPROVED_PROVIDER_PLUGIN_FILE}" ]] || fail "missing approved provider plugin identity"
  approved_provider_plugin="$(sed -n '1p' "${APPROVED_PROVIDER_PLUGIN_FILE}")"
  [[ -n "${approved_provider_plugin}" ]] || fail "approved provider plugin identity is empty"
  [[ "${value}" == "${approved_provider_plugin}" ]] || \
    fail "CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER must retain the approved package identity"
  [[ "$(env_value CAMPUS_MODEL_PROVIDER)" == "${APPROVED_MODEL_PROVIDER}" ]] || \
    fail "CAMPUS_MODEL_PROVIDER must match the approved OpenAI-compatible model credential schema"
  [[ "$(env_value CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE)" == "${APPROVED_MODEL_CREDENTIAL_SCOPE}" ]] || \
    fail "CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE must match the approved OpenAI-compatible model credential schema"
  [[ "$(env_value CAMPUS_MODEL_PROVIDER_API_KEY_FIELD)" == "${APPROVED_MODEL_API_KEY_FIELD}" ]] || \
    fail "CAMPUS_MODEL_PROVIDER_API_KEY_FIELD must match the approved OpenAI-compatible model credential schema"
  [[ "$(env_value CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD)" == "${APPROVED_MODEL_BASE_URL_FIELD}" ]] || \
    fail "CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD must match the approved OpenAI-compatible model credential schema"
  value="$(env_value CAMPUS_GATEWAY_GO_PROXY)"
  case "${value}" in
    ""|https://proxy.golang.org,direct|https://goproxy.cn,direct) ;;
    *) fail "CAMPUS_GATEWAY_GO_PROXY must use an approved HTTPS Go module proxy" ;;
  esac
}

migrate_provider_config() {
  [[ -f "${CAMPUS_ENV_FILE}" ]] || fail "missing ${CAMPUS_ENV_FILE}"
  [[ -f "${APPROVED_PROVIDER_PLUGIN_FILE}" ]] || fail "missing approved provider plugin identity"
  local approved_plugin current_plugin current_scope stamp destination
  approved_plugin="$(sed -n '1p' "${APPROVED_PROVIDER_PLUGIN_FILE}")"
  current_plugin="$(env_value CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER)"
  current_scope="$(env_value CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE)"

  if [[ "$(env_value CAMPUS_MODEL_PROVIDER)" == "${APPROVED_MODEL_PROVIDER}" && \
        "${current_plugin}" == "${approved_plugin}" && \
        "${current_scope}" == "${APPROVED_MODEL_CREDENTIAL_SCOPE}" && \
        "$(env_value CAMPUS_MODEL_PROVIDER_API_KEY_FIELD)" == "${APPROVED_MODEL_API_KEY_FIELD}" && \
        "$(env_value CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD)" == "${APPROVED_MODEL_BASE_URL_FIELD}" ]]; then
    echo "Campus provider configuration already uses the approved OpenAI-compatible schema."
    return
  fi

  [[ "$(env_value CAMPUS_MODEL_PROVIDER)" == "langgenius/openai/openai" && \
        "${current_plugin}" == langgenius/openai:* && \
        ( -z "${current_scope}" || "${current_scope}" == "provider" ) && \
        "$(env_value CAMPUS_MODEL_PROVIDER_API_KEY_FIELD)" == "openai_api_key" && \
        "$(env_value CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD)" == "openai_api_base" ]] || \
    fail "provider configuration is neither the approved schema nor the supported legacy OpenAI schema"

  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="${BACKUP_ROOT}/provider-config-${stamp}"
  install -d -m 700 "${destination}"
  cp -p "${CAMPUS_ENV_FILE}" "${destination}/campus.env"
  chmod 600 "${destination}/campus.env"

  if ! (
    set_env_value CAMPUS_MODEL_PROVIDER "${APPROVED_MODEL_PROVIDER}"
    set_env_value CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER "${approved_plugin}"
    set_env_value CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE "${APPROVED_MODEL_CREDENTIAL_SCOPE}"
    set_env_value CAMPUS_MODEL_PROVIDER_API_KEY_FIELD "${APPROVED_MODEL_API_KEY_FIELD}"
    set_env_value CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD "${APPROVED_MODEL_BASE_URL_FIELD}"
    validate_gateway_build_images
  ); then
    cp -p "${destination}/campus.env" "${CAMPUS_ENV_FILE}"
    fail "provider configuration migration failed; the protected environment was restored"
  fi
  echo "Campus provider configuration migrated; rollback copy: ${destination}/campus.env"
}

validate() {
  command -v docker >/dev/null || fail "docker is required"
  command -v curl >/dev/null || fail "curl is required"
  command -v python3 >/dev/null || fail "python3 is required"
  [[ -f "${DOCKER_DIR}/.env" ]] || fail "missing ${DOCKER_DIR}/.env"
  [[ -f "${CAMPUS_ENV_FILE}" ]] || fail "missing ${CAMPUS_ENV_FILE}"
  if grep -Ev '^[[:space:]]*#' "${CAMPUS_ENV_FILE}" | grep -Fq 'change-me'; then
    fail "replace all change-me placeholders in the Campus environment file"
  fi
  validate_branding_logo_source
  validate_gateway_build_images
  "${SCRIPT_DIR}/test-provider-config.sh"
  "${SCRIPT_DIR}/test-provider-config-migration.sh"
  validate_campus_model_list
  "${SCRIPT_DIR}/test-model-list.sh"
  "${SCRIPT_DIR}/test-nginx-routes.sh"
  "${SCRIPT_DIR}/test-public-entry.sh"
  "${COMPOSE[@]}" config --quiet
  "${COMPOSE[@]}" config --format json | \
    python3 "${SCRIPT_DIR}/validate_compose_credentials.py"
}

validate_campus_model_list() {
  local models entry model_type name seen_llm=false
  models="$(env_value CAMPUS_MODEL_PROVIDER_MODELS)"
  [[ -n "${models}" ]] || fail "set CAMPUS_MODEL_PROVIDER_MODELS"
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] || continue
    model_type="${entry%%:*}"
    name="${entry#*:}"
    [[ "${entry}" == *:* && -n "${name}" ]] || \
      fail "CAMPUS_MODEL_PROVIDER_MODELS entry must be type:name, got '${entry}'"
    case "${model_type}" in
      llm) seen_llm=true ;;
      text-embedding|rerank|speech2text|tts) ;;
      *) fail "CAMPUS_MODEL_PROVIDER_MODELS has an unknown model type: '${entry}'" ;;
    esac
  done < <(printf '%s\n' "${models}" | tr ',' '\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  [[ "${seen_llm}" == "true" ]] || \
    fail "CAMPUS_MODEL_PROVIDER_MODELS must name at least one llm to validate credentials against"
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
  local service="$1" container_id health attempt
  container_id="$("${COMPOSE[@]}" ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "${service} container is missing"
  for attempt in {1..30}; do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}")"
    [[ "${health}" == "healthy" ]] && return
    sleep 2
  done
  fail "${service} did not become healthy within 60 seconds"
}

assert_service_never_restarted() {
  local service="$1" container_id restart_count
  container_id="$("${COMPOSE[@]}" ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "${service} container is missing"
  restart_count="$(docker inspect --format '{{.RestartCount}}' "${container_id}")"
  [[ "${restart_count}" == "0" ]] || fail "${service} restarted ${restart_count} times"
}

wait_for_http() {
  local url="$1" label="$2" deadline remaining max_wait
  deadline="$((SECONDS + 60))"
  while (( (remaining = deadline - SECONDS) > 0 )); do
    max_wait="$((remaining < 5 ? remaining : 5))"
    if local_curl --fail --silent --show-error --max-time "${max_wait}" "${url}" >/dev/null 2>&1; then
      return 0
    fi
    remaining="$((deadline - SECONDS))"
    ((remaining > 0)) && sleep "$((remaining < 2 ? remaining : 2))"
  done
  printf 'campus-manage: %s did not become ready within 60 seconds\n' "${label}" >&2
  return 1
}

wait_for_campus_health() {
  local campus_port="$1"
  wait_for_http "http://127.0.0.1:${campus_port}/health" "Campus API" && return 0
  fail "Campus API did not become healthy within 60 seconds"
}

assert_current_slot_load_signal() {
  "${COMPOSE[@]}" exec -T api python -c '
import math
import os

from configs import dify_config

one_minute_load = os.getloadavg()[0]
cpu_count = os.cpu_count()
threshold = dify_config.CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU
assert cpu_count is not None and cpu_count > 0
assert math.isfinite(one_minute_load) and one_minute_load >= 0
assert math.isfinite(threshold) and threshold > 0
print(f"current_slot_load_per_cpu={one_minute_load / cpu_count:.3f} threshold={threshold:.3f} signal=ready")
'
}

assert_portal_root_redirect() {
  local url="$1" status redirect_url
  read -r status redirect_url < <(local_curl --silent --output /dev/null \
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

assert_static_surface_is_consistent() {
  # A served page and its served script must agree on required element ids.
  # Their pages are single-file bind mounts, so a stale container can serve an
  # old page beside a new script: the script then throws during init and every
  # listener is lost, including the one that keeps credentials out of the URL.
  local page_url="$1" script_url="$2" page script missing id
  page="$(local_curl --fail --silent --show-error --max-time 10 "${page_url}")"
  script="$(local_curl --fail --silent --show-error --max-time 10 "${script_url}")"
  missing=""
  while read -r id; do
    [[ -z "${id}" ]] && continue
    grep -Fq "id=\"${id}\"" <<<"${page}" || missing+=" #${id}"
  done < <(grep -oE "requiredElement\('#[a-z-]+'" <<<"${script}" | sed -E "s/.*'#([a-z-]+)'/\1/" | sort -u)
  [[ -z "${missing}" ]] || \
    fail "${page_url} serves a page missing required elements:${missing} (stale static container?)"
}

verify_workspace_isolation() {
  local summary binding_count student_count account_count tenant_count
  local invalid_topology_count invalid_student_membership_count distinct_owner_count
  local administrator_membership_count orphan_binding_count service_principal_count
  local unexpected_owner_count expected_owner_count service_principal_email
  [[ -f "${WORKSPACE_ISOLATION_SQL}" ]] || fail "missing Campus workspace isolation verifier"
  service_principal_email="$(env_value CAMPUS_SERVICE_PRINCIPAL_EMAIL)"
  [[ -n "${service_principal_email}" ]] || fail "Campus service principal email is not configured"
  summary="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -qAt -F "|" -v ON_ERROR_STOP=1 \
      -v service_principal_email="$1" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    _ "${service_principal_email}" \
    <"${WORKSPACE_ISOLATION_SQL}")"
  IFS='|' read -r binding_count student_count account_count tenant_count \
    invalid_topology_count invalid_student_membership_count distinct_owner_count \
    administrator_membership_count orphan_binding_count service_principal_count \
    unexpected_owner_count <<<"${summary}"
  for value in \
    "${binding_count}" "${student_count}" "${account_count}" "${tenant_count}" \
    "${invalid_topology_count}" "${invalid_student_membership_count}" "${distinct_owner_count}" \
    "${administrator_membership_count}" "${orphan_binding_count}" "${service_principal_count}" \
    "${unexpected_owner_count}"; do
    [[ "${value}" =~ ^[0-9]+$ ]] || fail "workspace isolation verifier returned an invalid summary"
  done
  [[ "${binding_count}" == "${student_count}" && \
     "${binding_count}" == "${account_count}" && \
     "${binding_count}" == "${tenant_count}" ]] || \
    fail "Campus students do not have one-to-one account and workspace bindings"
  [[ "${invalid_topology_count}" == "0" ]] || \
    fail "Campus workspace membership topology is not isolated"
  [[ "${invalid_student_membership_count}" == "0" ]] || \
    fail "a Campus student Dify account belongs to more than one workspace"
  [[ "${administrator_membership_count}" == "0" ]] || \
    fail "a named administrator is a member of a student workspace"
  [[ "${orphan_binding_count}" == "0" ]] || fail "a Campus workspace binding has no student identity"
  [[ "${service_principal_count}" == "1" ]] || \
    fail "the configured Campus service principal is missing or inactive"
  [[ "${unexpected_owner_count}" == "0" ]] || \
    fail "a Campus workspace is not owned by the configured service principal"
  expected_owner_count=0
  [[ "${binding_count}" == "0" ]] || expected_owner_count=1
  [[ "${distinct_owner_count}" == "${expected_owner_count}" ]] || \
    fail "Campus workspaces do not share exactly one service-principal owner"
  printf 'Workspace isolation: bindings=%s distinct_accounts=%s distinct_tenants=%s topology=valid\n' \
    "${binding_count}" "${account_count}" "${tenant_count}"
}

assert_api_concurrency_capacity() {
  local required_concurrency="${1:-100}" container_id environment worker_amount worker_class worker_connections
  local configured_capacity
  container_id="$("${COMPOSE[@]}" ps -q api)"
  [[ -n "${container_id}" ]] || fail "Campus API container is missing"
  environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container_id}")"
  worker_amount="$(awk -F= '$1 == "SERVER_WORKER_AMOUNT" { print $2; exit }' <<<"${environment}")"
  worker_class="$(awk -F= '$1 == "SERVER_WORKER_CLASS" { print $2; exit }' <<<"${environment}")"
  worker_connections="$(awk -F= '$1 == "SERVER_WORKER_CONNECTIONS" { print $2; exit }' <<<"${environment}")"
  [[ "${required_concurrency}" =~ ^[1-9][0-9]*$ && \
     "${worker_amount}" =~ ^[1-9][0-9]*$ && \
     "${worker_connections}" =~ ^[1-9][0-9]*$ ]] || \
    fail "Campus API concurrency configuration is invalid"
  [[ "${worker_class}" == "gevent" ]] || fail "Campus API concurrency requires the gevent worker class"
  configured_capacity=$((worker_amount * worker_connections))
  [[ "${configured_capacity}" -ge "${required_concurrency}" ]] || \
    fail "Campus API concurrency capacity is below ${required_concurrency}"
  printf 'API concurrency: workers=%s class=%s worker_connections=%s capacity=%s\n' \
    "${worker_amount}" "${worker_class}" "${worker_connections}" "${configured_capacity}"
}

verify() {
  validate
  local campus_bind campus_port admin_port gateway_port baseline_url status published container_id nginx_config nginx_location portal_networks
  local actual_bindings blocked_route expected_bindings
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
  assert_branding_logo "http://127.0.0.1:${campus_port}/logo/logo.svg"
  assert_branding_logo "http://127.0.0.1:${admin_port}/logo/logo.svg"
  for service in api portal model-gateway; do
    assert_service_healthy "${service}"
  done
  assert_api_concurrency_capacity "$(env_value CAMPUS_BASELINE_CONCURRENCY || true)"
  verify_workspace_isolation
  assert_current_slot_load_signal
  for service in worker worker_beat; do
    assert_service_never_restarted "${service}"
  done
  if "${COMPOSE[@]}" ps --all --services | grep -qx api_websocket; then
    require_running_service api_websocket
    assert_service_never_restarted api_websocket
  fi
  container_id="$("${COMPOSE[@]}" ps -q nginx)"
  [[ -n "${container_id}" ]] || fail "Campus nginx container is missing"
  published="$(docker port "${container_id}" 80/tcp)"
  if [[ "${public_enabled}" == "true" ]]; then
    actual_bindings="$(printf '%s\n' "${published}" | sed '/^$/d' | sort)"
    expected_bindings="$(printf '%s\n' "${public_bind}:${public_port}" "127.0.0.1:${campus_port}" | sort)"
    [[ "${actual_bindings}" == "${expected_bindings}" ]] || \
      fail "Campus public nginx bindings differ from the exact public and loopback set"
  else
    [[ "${published}" == "${campus_bind}:${campus_port}" ]] || \
      fail "Campus Dify bind differs from protected configuration"
  fi
  published="$(docker port "${container_id}" 8081/tcp)"
  [[ "${published}" == "127.0.0.1:${admin_port}" ]] || fail "Campus administration listener is not loopback-only"
  nginx_location="$(sed -n '/^[[:space:]]*location ~ /{s/^[[:space:]]*//;p;q;}' \
    "${SCRIPT_DIR}/nginx/default.conf.template")"
  nginx_config="$("${COMPOSE[@]}" exec -T nginx nginx -T 2>&1)"
  printf '%s\n' "${nginx_config}" | grep -Fq "${nginx_location}" || \
    fail "running nginx does not contain the Campus bootstrap route boundary"
  printf '%s\n' "${nginx_config}" | grep -Fq 'return 302 /portal/;' || \
    fail "running nginx does not make the Access portal the default entry"
  assert_portal_root_redirect "http://127.0.0.1:${campus_port}/"

  status="$(local_curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/portal/")"
  [[ "${status}" == "200" ]] || fail "Campus Access portal is unavailable (HTTP ${status})"

  if [[ "${public_enabled}" == "true" ]]; then
    verify_public_firewall "${public_port}"
    published="$("${UPSTREAM_LOOPBACK_COMPOSE[@]}" port nginx 80)"
    [[ "${published}" == "127.0.0.1:${upstream_port}" ]] || \
      fail "upstream Dify rollback route is not loopback-only"
  fi

  assert_portal_root_redirect \
    "http://127.0.0.1:${campus_port}/signin?redirect_url=%2F"

  status="$(local_curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${admin_port}/signin")"
  [[ "${status}" == "200" ]] || fail "Campus administrator sign-in is unavailable (HTTP ${status})"

  local_curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${admin_port}/" | \
    grep -Fq '校园平台管理后台' || fail "Campus administration portal does not own the administrator listener root"

  assert_static_surface_is_consistent \
    "http://127.0.0.1:${campus_port}/portal/" \
    "http://127.0.0.1:${campus_port}/portal/assets/portal.js"
  assert_static_surface_is_consistent \
    "http://127.0.0.1:${admin_port}/" \
    "http://127.0.0.1:${admin_port}/campus-admin/assets/admin.js"

  for blocked_route in /signin/check-code /console/api/login; do
    status="$(local_curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
      "http://127.0.0.1:${campus_port}${blocked_route}")"
    [[ "${status}" == "404" ]] || \
      fail "student Dify authentication route is not blocked: ${blocked_route} (HTTP ${status})"
  done

  container_id="$("${COMPOSE[@]}" ps -q portal)"
  [[ -n "${container_id}" && -z "$(docker port "${container_id}" 2>/dev/null || true)" ]] || \
    fail "Campus Access portal publishes a host port"
  portal_networks="$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' "${container_id}")"
  [[ "$(printf '%s\n' "${portal_networks}" | sed '/^$/d' | wc -l | tr -d ' ')" == "1" ]] || \
    fail "Campus Access portal is attached to more than one network"
  printf '%s\n' "${portal_networks}" | grep -q 'campus_portal$' || \
    fail "Campus Access portal is not attached to its isolated network"

  status="$(local_curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${campus_port}/console/api/apps")"
  [[ "${status}" == "401" ]] || fail "direct workspace API was not rejected (HTTP ${status})"

  published="$("${COMPOSE[@]}" port model-gateway 3000)"
  [[ "${published}" == "127.0.0.1:${gateway_port}" ]] || fail "model gateway is not loopback-only"
  local_curl --fail --silent --show-error --max-time 10 \
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
  verify_gateway_pricing "${gateway_port}"
  local_curl --fail --silent --show-error --max-time 10 "${baseline_url}" >/dev/null
}

gateway_sql() {
  "${COMPOSE[@]}" exec -T model-gateway-db sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -qtAX -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"' "$1"
}

# An unpriced model does not fail loudly: the gateway falls back to a ratio of
# 37.5 (about $75 per million tokens) and, with self-use mode on, bills it
# silently. These assertions are what keep that from reaching a student.
verify_gateway_pricing() {
  local gateway_port="$1"
  local unpriced self_use probe_model probe_id probe_secret status log_ratio sql

  sql="$(
    cat <<'SQL'
select a.model
from (select distinct model from abilities where enabled) a
where not exists (select 1 from options o where o.key = 'ModelRatio' and o.value::jsonb ? a.model)
  and not exists (select 1 from options o where o.key = 'ModelPrice' and o.value::jsonb ? a.model)
order by a.model
SQL
  )"
  unpriced="$(gateway_sql "${sql}")"
  [[ -z "${unpriced}" ]] || \
    fail "gateway routes models with no price: $(printf '%s' "${unpriced}" | tr '\n' ' ')"

  self_use="$(gateway_sql "select value from options where key = 'SelfUseModeEnabled'")"
  [[ -z "${self_use}" || "${self_use}" == "false" ]] || \
    fail "gateway self-use mode is on, so an unpriced model would bill at the fallback ratio"

  probe_model="$(campus_probe_model)"
  [[ -n "${probe_model}" ]] || fail "CAMPUS_MODEL_PROVIDER_MODELS names no llm to probe"

  # A probe token proves the student path end to end: group routing, upstream
  # reachability, and that metering lands with a sane ratio. Deleting it is a
  # soft delete, so the row stays but the credential stops authenticating —
  # verified to return 401 afterwards.
  gateway_admin_post "${gateway_port}" /api/token/ \
    "{\"name\":\"campus-verify-probe\",\"remain_quota\":500000,\"expired_time\":-1,\"unlimited_quota\":false,\"model_limits_enabled\":false,\"group\":\"campus\"}" \
    | grep -q '"success":true' || fail "could not create the gateway probe token"
  probe_id="$(gateway_sql "select id from tokens where name = 'campus-verify-probe' and deleted_at is null order by id desc limit 1")"
  [[ -n "${probe_id}" ]] || fail "gateway probe token was not stored"
  probe_secret="$(gateway_sql "select key from tokens where id = ${probe_id}")"
  [[ -n "${probe_secret}" ]] || fail "gateway probe token has no secret"

  status="$(local_curl --silent --output /dev/null --write-out '%{http_code}' --max-time 30 \
    -H "Authorization: Bearer sk-${probe_secret}" -H 'Content-Type: application/json' \
    -d "{\"model\":\"${probe_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"1+1=?\"}],\"max_tokens\":8}" \
    "http://127.0.0.1:${gateway_port}/v1/chat/completions")"
  log_ratio="$(gateway_sql "select other::jsonb->>'model_ratio' from logs where type = 2 and token_id = ${probe_id} order by id desc limit 1")"
  gateway_admin_delete "${gateway_port}" "/api/token/${probe_id}" >/dev/null || true

  [[ "${status}" == "200" ]] || fail "gateway probe call failed (HTTP ${status})"
  [[ -n "${log_ratio}" ]] || fail "gateway probe call was not metered"
  awk -v ratio="${log_ratio}" 'BEGIN { exit !(ratio + 0 < 1.0) }' || \
    fail "gateway metered the probe at ratio ${log_ratio}, which is the unpriced fallback"
}

# The first llm in the configured model list is what a student's workspace is
# validated against, so it is the right model to probe.
campus_probe_model() {
  env_value CAMPUS_MODEL_PROVIDER_MODELS | tr ',' '\n' | awk -F: '$1 == "llm" { print $2; exit }'
}

gateway_admin_post() {
  local gateway_port="$1" path="$2" body="$3"
  local_curl --silent --show-error --max-time 20 -X POST \
    -H "Authorization: Bearer $(env_value CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN)" \
    -H "New-API-User: $(env_value CAMPUS_NEWAPI_ADMIN_USER_ID)" \
    -H 'Content-Type: application/json' -d "${body}" \
    "http://127.0.0.1:${gateway_port}${path}"
}

gateway_admin_delete() {
  local gateway_port="$1" path="$2"
  local_curl --silent --show-error --max-time 20 -X DELETE \
    -H "Authorization: Bearer $(env_value CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN)" \
    -H "New-API-User: $(env_value CAMPUS_NEWAPI_ADMIN_USER_ID)" \
    "http://127.0.0.1:${gateway_port}${path}"
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

  setup_step="$(local_curl --fail --silent --show-error --max-time 10 \
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
  init_status="$(local_curl --fail --silent --show-error --max-time 10 \
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
  local public_port="$1" remote_address public_bind powershell_bin windows_script
  powershell_bin="$(command -v powershell.exe || true)"
  if [[ -z "${powershell_bin}" && -x /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe ]]; then
    powershell_bin=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
  fi
  [[ -n "${powershell_bin}" ]] || fail "powershell.exe is required for public-entry firewall verification"
  command -v wslpath >/dev/null || fail "wslpath is required for public-entry firewall verification"
  remote_address="$(env_value CAMPUS_PUBLIC_REMOTE_ADDRESS)"
  remote_address="${remote_address:-10.0.0.0/255.0.0.0}"
  public_bind="$(env_value CAMPUS_PUBLIC_BIND_ADDRESS)"
  public_bind="${public_bind:-10.20.10.193}"
  windows_script="$(wslpath -w "${SCRIPT_DIR}/windows/configure-intranet-firewall.ps1")"
  "${powershell_bin}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "${windows_script}" \
    -Action Verify -Port "${public_port}" -RemoteAddress "${remote_address}" \
    -ListenAddress "${public_bind}"
}

assert_port_owner() {
  local port="$1" expected_container_id="$2"
  local owners=()
  mapfile -t owners < <(docker ps --no-trunc --quiet --filter "publish=${port}")
  [[ "${#owners[@]}" == "1" && "${owners[0]}" == "${expected_container_id}" ]] || \
    fail "TCP ${port} is not owned exclusively by the expected nginx container"
}

restore_upstream_public_entry() {
  "${UPSTREAM_COMPOSE[@]}" up -d --no-deps --force-recreate nginx
}

verify_demo_accounts() {
  validate
  verify_workspace_isolation
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
    "SELECT COUNT(*) FROM campus_students WHERE status=\$\$active\$\$ AND cohort=\$\$demo\$\$;"')"
  student_login_count="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT COUNT(DISTINCT student.id) FROM campus_students student JOIN campus_portal_sessions portal_session ON portal_session.student_id=student.id WHERE student.status=\$\$active\$\$ AND student.cohort=\$\$demo\$\$ AND portal_session.revoked_at IS NULL AND portal_session.expires_at > CURRENT_TIMESTAMP;"')"
  virtual_count="$("${COMPOSE[@]}" exec -T api python -c \
    'import json, os; rows=json.loads(os.environ["CAMPUS_VIRTUAL_IDENTITIES_JSON"]); print(sum(row.get("cohort") == "demo" for row in rows))')"
  [[ "${admin_count}" == "2" && "${admin_name_count}" == "2" ]] || \
    fail "expected two distinct active administrators with successful logins"
  [[ "${student_count}" == "2" ]] || fail "expected two active demo student identities"
  [[ "${student_login_count}" == "2" ]] || fail "expected successful current portal login for both demo students"
  [[ "${virtual_count}" == "2" ]] || fail "expected two configured virtual demonstration identities"

  student_names="$("${COMPOSE[@]}" exec -T db_postgres sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT student_number || chr(32) || display_name FROM campus_students WHERE status=\$\$active\$\$ AND cohort=\$\$demo\$\$ ORDER BY student_number;"')"
  virtual_names="$("${COMPOSE[@]}" exec -T api python -c \
    'import json, os; rows=json.loads(os.environ["CAMPUS_VIRTUAL_IDENTITIES_JSON"]); print("\n".join(sorted("{0} {1}".format(row.get("student_number"), row.get("display_name")) for row in rows if row.get("cohort") == "demo")))')"
  [[ "${student_names}" == "${virtual_names}" ]] || fail "active students do not match the protected virtual demo roster"
  printf 'Named administrators:\n%s\nDemo students:\n%s\n' "${admin_names}" "${student_names}"
}

run_baseline() {
  validate
  local campus_port concurrency requests_per_user duration_seconds max_p99_ms
  local started_at nginx_container_id restart_snapshot_before restart_snapshot_after baseline_failed
  campus_port="$(env_value EXPOSE_NGINX_PORT)"
  concurrency="$(env_value CAMPUS_BASELINE_CONCURRENCY)"
  requests_per_user="$(env_value CAMPUS_BASELINE_REQUESTS_PER_USER)"
  duration_seconds="$(env_value CAMPUS_BASELINE_DURATION_SECONDS)"
  max_p99_ms="$(env_value CAMPUS_BASELINE_MAX_P99_MS)"
  campus_port="${campus_port:-18080}"
  concurrency="${concurrency:-100}"
  requests_per_user="${requests_per_user:-1}"
  duration_seconds="${duration_seconds:-300}"
  max_p99_ms="${max_p99_ms:-100}"

  for service in api portal nginx db_postgres; do
    require_running_service "${service}"
  done
  wait_for_campus_health "${campus_port}"
  assert_api_concurrency_capacity "${concurrency}"
  verify_workspace_isolation
  nginx_container_id="$("${COMPOSE[@]}" ps -q nginx)"
  [[ -n "${nginx_container_id}" ]] || fail "Campus nginx container is missing"
  restart_snapshot_before="$(docker inspect --format '{{.Name}}={{.RestartCount}}' \
    $("${COMPOSE[@]}" ps -q) | sort)"
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  baseline_failed=false
  python3 "${SCRIPT_DIR}/nonbillable_baseline.py" \
    --base-url "http://127.0.0.1:${campus_port}" \
    --concurrency "${concurrency}" \
    --requests-per-user "${requests_per_user}" \
    --duration-seconds "${duration_seconds}" \
    --max-p99-ms "${max_p99_ms}" || baseline_failed=true

  local_curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${campus_port}/health" >/dev/null || \
    fail "Campus health failed after the concurrency baseline"
  restart_snapshot_after="$(docker inspect --format '{{.Name}}={{.RestartCount}}' \
    $("${COMPOSE[@]}" ps -q) | sort)"
  [[ "${restart_snapshot_before}" == "${restart_snapshot_after}" ]] || \
    fail "a Campus container RestartCount changed during the concurrency baseline"
  if docker logs --since "${started_at}" "${nginx_container_id}" 2>&1 | \
    grep -Fq 'Cannot assign requested address'; then
    fail "Campus nginx exhausted an upstream address during the concurrency baseline"
  fi
  [[ "${baseline_failed}" == "false" ]] || fail "Campus non-billable concurrency baseline failed"
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
    ! wait_for_http "http://127.0.0.1:${upstream_port}/" "upstream rollback route"; then
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
  # Recreate both static surfaces: their pages are single-file bind mounts, so a
  # source update that replaces the file's inode leaves a running container
  # serving the old page while its mounted asset directory serves new scripts.
  "${COMPOSE[@]}" up -d --no-deps --force-recreate portal
  "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx
  verify
}

deploy_branding() {
  local backup_destination
  validate
  project_has_state || fail "branding deployment requires an existing Campus project"
  backup_destination="$(backup)"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate nginx
  verify
  echo "Campus Dify branding deployed; rollback backup: ${backup_destination}"
}

usage() {
  echo "usage: $0 {gateway-up|migrate-provider-config|validate|backup|deploy|deploy-branding|verify|verify-demo-accounts|baseline|open-bootstrap|promote|rollback-promotion|stop}" >&2
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
  migrate-provider-config) migrate_provider_config ;;
  validate) validate ;;
  backup) backup ;;
  deploy) deploy ;;
  deploy-branding) deploy_branding ;;
  verify) verify ;;
  verify-demo-accounts) verify_demo_accounts ;;
  baseline) run_baseline ;;
  open-bootstrap) open_bootstrap ;;
  promote) promote ;;
  rollback-promotion) rollback_promotion ;;
  stop)
    validate
    "${COMPOSE[@]}" down
    ;;
  *) usage ;;
esac
