#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
public_overlay="${DOCKER_DIR}/docker-compose.campus-public.yaml"
upstream_overlay="${SCRIPT_DIR}/upstream-loopback.yaml"
manager="${SCRIPT_DIR}/manage.sh"
firewall_script="${SCRIPT_DIR}/windows/configure-intranet-firewall.ps1"
approved_plugin_file="${SCRIPT_DIR}/approved-provider-plugin.txt"
credential_validator="${SCRIPT_DIR}/validate_compose_credentials.py"
credential_validator_test="${SCRIPT_DIR}/tests/test_compose_credentials.py"
provider_config_test="${SCRIPT_DIR}/test-provider-config.sh"
provider_config_migration_test="${SCRIPT_DIR}/test-provider-config-migration.sh"
workspace_isolation_sql="${SCRIPT_DIR}/verify-workspace-isolation.sql"
baseline_runner="${SCRIPT_DIR}/nonbillable_baseline.py"
baseline_runner_test="${SCRIPT_DIR}/tests/test_nonbillable_baseline.py"
campus_env_example="${DOCKER_DIR}/envs/campus.env.example"
approved_openai_plugin="$(sed -n '1p' "${approved_plugin_file}")"

[[ -n "${approved_openai_plugin}" ]] || {
  echo "Approved OpenAI provider plugin identity is missing" >&2
  exit 1
}
grep -Fq 'APPROVED_PROVIDER_PLUGIN_FILE' "${manager}" || {
  echo "Campus validation does not consume the approved provider plugin file" >&2
  exit 1
}
if grep -Fq "${approved_openai_plugin}" "${manager}"; then
  echo "Campus manager duplicates the approved provider plugin identity" >&2
  exit 1
fi
[[ -f "${credential_validator}" && -f "${credential_validator_test}" ]] || {
  echo "Campus Compose credential validator or its tests are missing" >&2
  exit 1
}
python3 "${credential_validator_test}"
[[ -x "${provider_config_test}" ]] || {
  echo "Campus provider configuration test is missing or not executable" >&2
  exit 1
}
[[ -x "${provider_config_migration_test}" ]] || {
  echo "Campus provider configuration migration test is missing or not executable" >&2
  exit 1
}
grep -Fq 'test-provider-config.sh' "${manager}" || {
  echo "Campus validation does not exercise the provider configuration contract" >&2
  exit 1
}
grep -Fq 'test-provider-config-migration.sh' "${manager}" || {
  echo "Campus validation does not exercise the provider migration contract" >&2
  exit 1
}
grep -Fq 'validate_compose_credentials.py' "${manager}" || {
  echo "Campus validation does not enforce Redis/Celery credential consistency" >&2
  exit 1
}
[[ -f "${workspace_isolation_sql}" ]] || {
  echo "Campus workspace isolation verifier is missing" >&2
  exit 1
}
grep -Fq 'verify-workspace-isolation.sql' "${manager}" || {
  echo "Campus manager does not consume the workspace isolation verifier" >&2
  exit 1
}
grep -Fq 'service_principal_email' "${workspace_isolation_sql}" || {
  echo "Campus workspace isolation does not identify the configured service principal" >&2
  exit 1
}
workspace_isolation_function="$(sed -n '/^verify_workspace_isolation() {$/,/^}/p' "${manager}")"
printf '%s\n' "${workspace_isolation_function}" | grep -Fq 'CAMPUS_SERVICE_PRINCIPAL_EMAIL' || {
  echo "Campus workspace verification does not pass the configured service principal" >&2
  exit 1
}
[[ -f "${baseline_runner}" ]] || {
  echo "Campus non-billable baseline runner is missing" >&2
  exit 1
}
[[ -f "${baseline_runner_test}" ]] || {
  echo "Campus non-billable baseline tests are missing" >&2
  exit 1
}
python3 "${baseline_runner_test}"

redirect_function="$(sed -n '/^assert_portal_root_redirect() {$/,/^}/p' "${manager}")"
printf '%s\n' "${redirect_function}" | grep -Fq -- "--write-out '%{http_code} %{redirect_url}\\n'" || {
  echo "Portal redirect verification must terminate curl output for Bash read" >&2
  exit 1
}

local_curl_function="$(sed -n '/^local_curl() {$/,/^}/p' "${manager}")"
printf '%s\n' "${local_curl_function}" | grep -Fq -- "--noproxy '*'" || {
  echo "Campus local HTTP verification does not bypass inherited proxies" >&2
  exit 1
}
wait_for_http_function="$(sed -n '/^wait_for_http() {$/,/^}/p' "${manager}")"
printf '%s\n' "${wait_for_http_function}" | grep -Fq 'local_curl' || {
  echo "Campus readiness checks can still send loopback traffic through a proxy" >&2
  exit 1
}
printf '%s\n' "${redirect_function}" | grep -Fq 'local_curl' || {
  echo "Campus redirect checks can still send loopback traffic through a proxy" >&2
  exit 1
}

grep -Fq 'BRANDING_LOGO_MANIFEST=' "${manager}" || {
  echo "Campus validation does not consume the approved Dify branding manifest" >&2
  exit 1
}
branding_source_function="$(sed -n '/^validate_branding_logo_source() {$/,/^}/p' "${manager}")"
for contract in sha256sum BRANDING_LOGO_MANIFEST branding_logo_sha256; do
  printf '%s\n' "${branding_source_function}" | grep -Fq "${contract}" || {
    echo "Campus validation does not verify the Dify branding source contract: ${contract}" >&2
    exit 1
  }
done
branding_response_function="$(sed -n '/^assert_branding_logo() {$/,/^}/p' "${manager}")"
for contract in local_curl '%{content_type}' sha256sum branding_logo_sha256; do
  printf '%s\n' "${branding_response_function}" | grep -Fq "${contract}" || {
    echo "Campus runtime verification does not enforce Dify logo contract: ${contract}" >&2
    exit 1
  }
done

verify_function="$(sed -n '/^verify() {$/,/^}/p' "${manager}")"
printf '%s\n' "${verify_function}" | grep -Fq 'verify_workspace_isolation' || {
  echo "Campus runtime verification does not enforce every workspace binding" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'assert_api_concurrency_capacity' || {
  echo "Campus runtime verification does not enforce API concurrency capacity" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'assert_code_execution_ready' || {
  echo "Campus runtime verification does not exercise the sandbox authentication path" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'docker port "${container_id}" 80/tcp' || {
  echo "Campus verification does not inspect every nginx host binding" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'actual_bindings' || {
  echo "Campus verification does not reject additional nginx host bindings" >&2
  exit 1
}
wait_line="$(printf '%s\n' "${verify_function}" | grep -n -m1 'wait_for_campus_health')"
health_line="$(printf '%s\n' "${verify_function}" | grep -n -m1 'assert_service_healthy')"
[[ -n "${wait_line}" && -n "${health_line}" && "${wait_line%%:*}" -lt "${health_line%%:*}" ]] || {
  echo "Campus verification must wait for API health before asserting container health" >&2
  exit 1
}
for logo_url in \
  'http://127.0.0.1:${campus_port}/logo/logo.svg' \
  'http://127.0.0.1:${admin_port}/logo/logo.svg'; do
  printf '%s\n' "${verify_function}" | grep -Fq "${logo_url}" || {
    echo "Campus verification does not check Dify branding at ${logo_url}" >&2
    exit 1
  }
done
if [[ "${verify_function}" != *'assert_portal_root_redirect'*\
'http://127.0.0.1:${campus_port}/signin?redirect_url=%2F'* ]]; then
  echo "Campus verification does not exercise the post-logout student redirect" >&2
  exit 1
fi
printf '%s\n' "${verify_function}" | \
  grep -Fq 'http://127.0.0.1:${admin_port}/signin' || {
  echo "Campus verification does not exercise the administrator sign-in page" >&2
  exit 1
}
if [[ "${verify_function}" != *'for blocked_route in /signin/check-code /console/api/login'*\
'http://127.0.0.1:${campus_port}${blocked_route}'* ]]; then
  echo "Campus verification does not exercise blocked student authentication routes" >&2
  exit 1
fi

deploy_function="$(sed -n '/^deploy() {$/,/^}/p' "${manager}")"
build_line="$(printf '%s\n' "${deploy_function}" | grep -n -m1 'up -d --build' || true)"
nginx_recreate_line="$(printf '%s\n' "${deploy_function}" | grep -n -m1 -- '--force-recreate nginx' || true)"
deploy_verify_line="$(printf '%s\n' "${deploy_function}" | grep -n -m1 'verify' || true)"
[[ -n "${build_line}" && -n "${nginx_recreate_line}" && -n "${deploy_verify_line}" && \
   "${build_line%%:*}" -lt "${nginx_recreate_line%%:*}" && \
   "${nginx_recreate_line%%:*}" -lt "${deploy_verify_line%%:*}" ]] || {
  echo "Campus deployment must recreate nginx after application containers and before verification" >&2
  exit 1
}

grep -Eq '^[[:space:]]*deploy-branding\)' "${manager}" || {
  echo "Campus manager does not expose scoped Dify branding deployment" >&2
  exit 1
}
deploy_branding_function="$(sed -n '/^deploy_branding() {$/,/^}/p' "${manager}")"
branding_backup_line="$(printf '%s\n' "${deploy_branding_function}" | grep -n -m1 'backup' || true)"
branding_recreate_line="$(printf '%s\n' "${deploy_branding_function}" | grep -n -m1 -- '--force-recreate nginx' || true)"
branding_verify_line="$(printf '%s\n' "${deploy_branding_function}" | grep -n -m1 'verify' || true)"
[[ -n "${branding_backup_line}" && -n "${branding_recreate_line}" && -n "${branding_verify_line}" && \
   "${branding_backup_line%%:*}" -lt "${branding_recreate_line%%:*}" && \
   "${branding_recreate_line%%:*}" -lt "${branding_verify_line%%:*}" ]] || {
  echo "Campus branding deployment must back up, recreate only nginx, then verify" >&2
  exit 1
}

[[ -f "${public_overlay}" ]] || {
  echo "Campus public-entry Compose overlay is missing" >&2
  exit 1
}
[[ -f "${upstream_overlay}" ]] || {
  echo "Upstream loopback Compose overlay is missing" >&2
  exit 1
}

grep -Fq '${CAMPUS_PUBLIC_BIND_ADDRESS:-10.20.10.193}:${CAMPUS_PUBLIC_HTTP_PORT:-80}:${NGINX_PORT:-80}' \
  "${public_overlay}" || {
  echo "Campus public entry is not bound to the protected campus address" >&2
  exit 1
}
grep -Fq '127.0.0.1:${EXPOSE_NGINX_PORT:-18080}:${NGINX_PORT:-80}' "${public_overlay}" || {
  echo "Campus public entry does not retain its loopback verification route" >&2
  exit 1
}
grep -Fq '127.0.0.1:${CAMPUS_ADMIN_PORT:-18081}:8081' "${public_overlay}" || {
  echo "Campus administrator route is not loopback-only after promotion" >&2
  exit 1
}
grep -Fq '127.0.0.1:${CAMPUS_UPSTREAM_HTTP_PORT:-18082}:${NGINX_PORT:-80}' "${upstream_overlay}" || {
  echo "Upstream rollback route is not loopback-only" >&2
  exit 1
}

grep -Eq '^[[:space:]]*promote\)' "${manager}" || {
  echo "Campus manager does not expose deterministic promotion" >&2
  exit 1
}
grep -Eq '^[[:space:]]*rollback-promotion\)' "${manager}" || {
  echo "Campus manager does not expose deterministic promotion rollback" >&2
  exit 1
}
grep -Eq '^[[:space:]]*baseline\)' "${manager}" || {
  echo "Campus manager does not expose the 100-user baseline" >&2
  exit 1
}
baseline_function="$(sed -n '/^run_baseline() {$/,/^}/p' "${manager}")"
for contract in \
  'verify_workspace_isolation' \
  'assert_api_concurrency_capacity' \
  'nonbillable_baseline.py' \
  'RestartCount' \
  'Cannot assign requested address'; do
  printf '%s\n' "${baseline_function}" | grep -Fq "${contract}" || {
    echo "Campus baseline omits required safety contract: ${contract}" >&2
    exit 1
  }
done
api_service="$(sed -n '/^[[:space:]]\{2\}api:$/,/^[[:space:]]\{2\}[a-zA-Z0-9_-]*:$/p' \
  "${DOCKER_DIR}/docker-compose.campus.yaml")"
for setting in \
  'SERVER_WORKER_AMOUNT: ${CAMPUS_API_WORKER_AMOUNT:-2}' \
  'SERVER_WORKER_CLASS: ${CAMPUS_API_WORKER_CLASS:-gevent}' \
  'SERVER_WORKER_CONNECTIONS: ${CAMPUS_API_WORKER_CONNECTIONS:-200}'; do
  printf '%s\n' "${api_service}" | grep -Fq "${setting}" || {
    echo "Campus API omits concurrency setting: ${setting}" >&2
    exit 1
  }
done
for setting in \
  'CAMPUS_API_WORKER_AMOUNT=2' \
  'CAMPUS_API_WORKER_CLASS=gevent' \
  'CAMPUS_API_WORKER_CONNECTIONS=200' \
  'CAMPUS_BASELINE_CONCURRENCY=100' \
  'CAMPUS_BASELINE_REQUESTS_PER_USER=1' \
  'CAMPUS_BASELINE_DURATION_SECONDS=300' \
  'CAMPUS_BASELINE_MAX_P99_MS=100'; do
  grep -Fq "${setting}" "${campus_env_example}" || {
    echo "Campus environment example omits baseline setting: ${setting}" >&2
    exit 1
  }
done
grep -Eq '^[[:space:]]*verify-demo-accounts\)' "${manager}" || {
  echo "Campus manager does not expose redacted demo-account verification" >&2
  exit 1
}
verify_demo_function="$(sed -n '/^verify_demo_accounts() {$/,/^}/p' "${manager}")"
printf '%s\n' "${verify_demo_function}" | grep -Fq 'verify_workspace_isolation' || {
  echo "Campus demo verification does not enforce workspace isolation" >&2
  exit 1
}
promote_function="$(sed -n '/^promote() {$/,/^}/p' "${manager}")"
printf '%s\n' "${promote_function}" | grep -Fq 'verify_demo_accounts' || {
  echo "Campus promotion does not enforce the demo-account gate" >&2
  exit 1
}
loopback_recreate_line="$(printf '%s\n' "${promote_function}" | grep -n -m1 'UPSTREAM_LOOPBACK_COMPOSE.*up -d' || true)"
loopback_wait_line="$(printf '%s\n' "${promote_function}" | grep -n -m1 'wait_for_http.*upstream' || true)"
[[ -n "${loopback_recreate_line}" && -n "${loopback_wait_line}" && \
   "${loopback_recreate_line%%:*}" -lt "${loopback_wait_line%%:*}" ]] || {
  echo "Campus promotion must wait for the recreated upstream rollback route" >&2
  exit 1
}
port_owner_function="$(sed -n '/^assert_port_owner() {$/,/^}/p' "${manager}")"
printf '%s\n' "${port_owner_function}" | grep -Fq 'docker ps --no-trunc --quiet' || {
  echo "Campus promotion compares a truncated Docker ID with the exact port owner" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'verify_public_firewall' || {
  echo "Promoted-state verification does not recheck the firewall boundary" >&2
  exit 1
}
if printf '%s\n' "${verify_function}" | grep -Fq 'http://${public_bind}:${public_port}'; then
  echo "Campus verification incorrectly hairpins the Windows LAN address from WSL" >&2
  exit 1
fi
grep -Fq 'last_login_at IS NOT NULL' "${manager}" || {
  echo "Campus demo verification does not prove both administrator logins" >&2
  exit 1
}
grep -Fq 'COUNT(DISTINCT lower(name))' "${manager}" || {
  echo "Campus demo verification does not require two distinct administrator names" >&2
  exit 1
}
grep -Fq 'campus_portal_sessions' "${manager}" || {
  echo "Campus demo verification does not prove both student portal logins" >&2
  exit 1
}
[[ "$(grep -F -o 'cohort=\$\$demo\$\$' "${manager}" | wc -l | tr -d ' ')" -ge 3 ]] || {
  echo "Campus demo verification must scope database checks to the demo cohort" >&2
  exit 1
}
[[ "$(grep -F 'row.get("cohort") == "demo"' "${manager}" | wc -l | tr -d ' ')" -ge 2 ]] || {
  echo "Campus demo verification must scope protected identities to the demo cohort" >&2
  exit 1
}
if grep -Fq 'print("\\n".join' "${manager}"; then
  echo "Campus demo verification uses a literal backslash-n instead of a line separator" >&2
  exit 1
fi
grep -Fq 'print("\n".join' "${manager}" || {
  echo "Campus demo verification must compare one configured identity per line" >&2
  exit 1
}
grep -Fq -- '-Action Verify' "${manager}" || {
  echo "Campus promotion does not enforce the Windows and Hyper-V firewall gate" >&2
  exit 1
}
grep -Fq -- '-ExecutionPolicy Bypass' "${manager}" || {
  echo "Campus firewall verification cannot run the reviewed WSL-hosted PowerShell script" >&2
  exit 1
}
for service in worker worker_beat model-gateway; do
  grep -Fq "${service}" "${manager}" || {
    echo "Campus verification does not cover ${service}" >&2
    exit 1
  }
done
grep -Fq 'RestartCount' "${manager}" || {
  echo "Campus verification does not reject worker restart loops" >&2
  exit 1
}
grep -Fq 'assert_current_slot_load_signal' "${manager}" || {
  echo "Campus verification does not prove the current-slot load signal" >&2
  exit 1
}
grep -Fq 'CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU' "${DOCKER_DIR}/docker-compose.campus.yaml" || {
  echo "Campus API does not receive the current-slot load threshold" >&2
  exit 1
}
grep -Fq 'CAMPUS_CURRENT_SLOT_MAX_LOAD_PER_CPU=' "${campus_env_example}" || {
  echo "Campus environment example omits the current-slot load threshold" >&2
  exit 1
}
grep -Fq '/api/status' "${manager}" || {
  echo "Campus verification does not check the model-gateway status endpoint" >&2
  exit 1
}

grep -Fq 'Dify HTTP 80' "${firewall_script}" || {
  echo "Campus firewall promotion does not account for the legacy public HTTP rule" >&2
  exit 1
}
grep -Fq 'Set-NetFirewallRule -Enabled False' "${firewall_script}" || {
  echo "Campus firewall promotion does not disable legacy broad port-80 access" >&2
  exit 1
}
for snapshot in PreviousWindowsRule PreviousHyperVRule; do
  grep -Fq "${snapshot}" "${firewall_script}" || {
    echo "Campus firewall rollback does not preserve ${snapshot}" >&2
    exit 1
  }
done

grep -Fq 'PreviousWslConfig' "${firewall_script}" || {
  echo "Campus public-entry rollback does not preserve the previous WSL configuration" >&2
  exit 1
}
grep -Fq 'hostAddressLoopback=true' "${firewall_script}" || {
  echo "Campus public entry does not enable mirrored host-address loopback" >&2
  exit 1
}
grep -Fq 'Test-CampusHostAddressLoopback' "${firewall_script}" || {
  echo "Campus public-entry verification does not test the Windows LAN address" >&2
  exit 1
}
grep -Fq 'wsl-docker-boot' "${firewall_script}" || {
  echo "Campus public-entry verification does not require the WSL keepalive task" >&2
  exit 1
}
grep -Fq 'Test-WslKeepaliveTask' "${firewall_script}" || {
  echo "Campus public-entry verification does not validate the WSL runtime anchor" >&2
  exit 1
}
firewall_verify_function="$(sed -n '/^verify_public_firewall() {$/,/^}/p' "${manager}")"
printf '%s\n' "${firewall_verify_function}" | grep -Fq -- '-ListenAddress "${public_bind}"' || {
  echo "Campus verification does not test the configured campus address from Windows" >&2
  exit 1
}
printf '%s\n' "${firewall_verify_function}" | grep -Fq 'System32/WindowsPowerShell/v1.0/powershell.exe' || {
  echo "Campus firewall verification cannot locate Windows PowerShell under sudo" >&2
  exit 1
}
host_loopback_function="$(sed -n '/^function Test-CampusHostAddressLoopback {$/,/^}/p' "${firewall_script}")"
printf '%s\n' "${host_loopback_function}" | grep -Fq 'portal/' || {
  echo "Windows public-entry verification does not load the Portal page" >&2
  exit 1
}
