#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
public_overlay="${DOCKER_DIR}/docker-compose.campus-public.yaml"
upstream_overlay="${SCRIPT_DIR}/upstream-loopback.yaml"
manager="${SCRIPT_DIR}/manage.sh"
firewall_script="${SCRIPT_DIR}/windows/configure-intranet-firewall.ps1"
approved_openai_plugin='langgenius/openai:1.0.4@3b49ff900a77c9b2cfba21e3cd1180fbfd7edf5ec008bc20564541c7a3914295'

grep -Fq "${approved_openai_plugin}" "${manager}" || {
  echo "Campus validation does not pin the approved OpenAI provider plugin" >&2
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
