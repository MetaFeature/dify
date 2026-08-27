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
grep -Fq 'validate_compose_credentials.py' "${manager}" || {
  echo "Campus validation does not enforce Redis/Celery credential consistency" >&2
  exit 1
}

redirect_function="$(sed -n '/^assert_portal_root_redirect() {$/,/^}/p' "${manager}")"
printf '%s\n' "${redirect_function}" | grep -Fq -- "--write-out '%{http_code} %{redirect_url}\\n'" || {
  echo "Portal redirect verification must terminate curl output for Bash read" >&2
  exit 1
}

verify_function="$(sed -n '/^verify() {$/,/^}/p' "${manager}")"
wait_line="$(printf '%s\n' "${verify_function}" | grep -n -m1 'wait_for_campus_health')"
health_line="$(printf '%s\n' "${verify_function}" | grep -n -m1 'assert_service_healthy')"
[[ -n "${wait_line}" && -n "${health_line}" && "${wait_line%%:*}" -lt "${health_line%%:*}" ]] || {
  echo "Campus verification must wait for API health before asserting container health" >&2
  exit 1
}

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
grep -Eq '^[[:space:]]*verify-demo-accounts\)' "${manager}" || {
  echo "Campus manager does not expose redacted demo-account verification" >&2
  exit 1
}
promote_function="$(sed -n '/^promote() {$/,/^}/p' "${manager}")"
printf '%s\n' "${promote_function}" | grep -Fq 'verify_demo_accounts' || {
  echo "Campus promotion does not enforce the demo-account gate" >&2
  exit 1
}
printf '%s\n' "${verify_function}" | grep -Fq 'verify_public_firewall' || {
  echo "Promoted-state verification does not recheck the firewall boundary" >&2
  exit 1
}
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
