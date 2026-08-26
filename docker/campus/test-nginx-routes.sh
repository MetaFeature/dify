#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
template="${SCRIPT_DIR}/nginx/default.conf.template"
compose_overlay="${SCRIPT_DIR}/../docker-compose.campus.yaml"
grep -Eq '^[[:space:]]+volumes: !override$' "${compose_overlay}" || {
  echo "Campus nginx volumes must replace, not merge with, the upstream conf.d directory mount" >&2
  exit 1
}

grep -Eq '^[[:space:]]*location \^~ /portal/ \{' "${template}" || {
  echo "Campus nginx does not expose the Access portal" >&2
  exit 1
}

grep -Fq 'proxy_pass http://portal:8080/;' "${template}" || {
  echo "Access portal is not routed to its frontend container" >&2
  exit 1
}

root_location="$(sed -n '/^[[:space:]]*location = \/ {$/,/^[[:space:]]*}/p' "${template}")"
printf '%s\n' "${root_location}" | grep -Fq 'return 302 /portal/;' || {
  echo "Campus root does not enter the Access portal" >&2
  exit 1
}

grep -Eq '^[[:space:]]{2}portal:$' "${compose_overlay}" || {
  echo "Campus Compose does not define the Access portal container" >&2
  exit 1
}

grep -Eq '^[[:space:]]{2}campus_portal:$' "${compose_overlay}" || {
  echo "Campus Compose does not define the isolated portal network" >&2
  exit 1
}

portal_service="$(sed -n '/^[[:space:]]\{2\}portal:$/,/^[[:space:]]\{2\}[a-zA-Z0-9_-]*:$/p' "${compose_overlay}")"
printf '%s\n' "${portal_service}" | grep -Eq '^[[:space:]]+- campus_portal$' || {
  echo "Access portal is not attached to its isolated network" >&2
  exit 1
}
if printf '%s\n' "${portal_service}" | grep -Eq '^[[:space:]]+ports:'; then
  echo "Access portal must not publish a host port" >&2
  exit 1
fi
portal_networks="$(printf '%s\n' "${portal_service}" | sed -n '/^[[:space:]]\{4\}networks:$/,/^[[:space:]]\{2\}[a-zA-Z0-9_-]*:$/p' | grep -E '^[[:space:]]+- ' || true)"
if [[ "${portal_networks}" != "      - campus_portal" ]]; then
  echo "Access portal must join only the isolated portal network" >&2
  exit 1
fi

nginx_service="$(sed -n '/^[[:space:]]\{2\}nginx:$/,/^[[:space:]]\{2\}[a-zA-Z0-9_-]*:$/p' "${compose_overlay}")"
for network in default campus_portal; do
  printf '%s\n' "${nginx_service}" | grep -Eq "^[[:space:]]+- ${network}$" || {
    echo "Campus nginx is not attached to ${network}" >&2
    exit 1
  }
done

sed -n '/^[[:space:]]*location \/console\/api {$/,/^[[:space:]]*}/p' "${template}" | \
  grep -Fq 'auth_request /_campus_access_check;' || {
    echo "Campus console API is not protected by the reservation gate" >&2
    exit 1
  }

for route in \
  /signin \
  /signup \
  /console/api/login \
  /console/api/email-code-login \
  /console/api/oauth/login/github; do
  printf '%s\n' "${route}" | grep -Eq '^/(activate|forgot-password|reset-password|signin|signup)(/|$)|^/console/api/(activate|email-code-login|forgot-password|login|oauth/(authorize|login)|reset-password)(/|$)' || {
    echo "student Dify authentication route is not blocked: ${route}" >&2
    exit 1
  }
done

grep -Fq 'listen 8081;' "${template}" || {
  echo "Campus nginx does not define the loopback administration listener" >&2
  exit 1
}
grep -Fq '127.0.0.1:${CAMPUS_ADMIN_PORT:-18081}:8081' "${compose_overlay}" || {
  echo "Campus administration listener is not bound to host loopback" >&2
  exit 1
}

for service in api_websocket worker worker_beat; do
  service_config="$(sed -n "/^[[:space:]]\\{2\\}${service}:$/,/^[[:space:]]\\{2\\}[a-zA-Z0-9_-]*:$/p" "${compose_overlay}")"
  printf '%s\n' "${service_config}" | grep -Fq 'MIGRATION_ENABLED: "false"' || {
    echo "${service} must not race the API database migration" >&2
    exit 1
  }
done
