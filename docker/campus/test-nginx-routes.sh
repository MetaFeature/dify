#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
template="${SCRIPT_DIR}/nginx/default.conf.template"
compose_overlay="${SCRIPT_DIR}/../docker-compose.campus.yaml"
branding_dir="${SCRIPT_DIR}/branding"
branding_locations="${branding_dir}/logo-locations.conf"
branding_response="${branding_dir}/logo-response.conf"
branding_logo="${branding_dir}/njit-logo.png"
branding_manifest="${branding_dir}/SHA256SUMS"
grep -Eq '^[[:space:]]+volumes: !override$' "${compose_overlay}" || {
  echo "Campus nginx volumes must replace, not merge with, the upstream conf.d directory mount" >&2
  exit 1
}

[[ -f "${branding_locations}" && -f "${branding_response}" && -f "${branding_logo}" && \
   -f "${branding_manifest}" ]] || {
  echo "Campus Dify branding assets are missing" >&2
  exit 1
}
(cd "${branding_dir}" && sha256sum --check --status "$(basename -- "${branding_manifest}")") || {
  echo "Campus Dify branding logo does not match the approved user asset" >&2
  exit 1
}
grep -Fq './campus/branding:/etc/nginx/campus-branding:ro' "${compose_overlay}" || {
  echo "Campus nginx does not mount the branding overlay read-only" >&2
  exit 1
}
[[ "$(grep -Fc 'include /etc/nginx/campus-branding/logo-locations.conf;' "${template}")" == "2" ]] || {
  echo "Campus and administrator listeners must both include the Dify logo overlay" >&2
  exit 1
}
for logo_path in \
  /logo/logo.svg \
  /logo/logo-monochrome-white.svg \
  /logo/logo-site.png \
  /logo/logo-site-dark.png \
  /logo/logo-embedded-chat-avatar.png \
  /logo/logo-embedded-chat-header.png \
  /logo/logo-embedded-chat-header@2x.png \
  /logo/logo-embedded-chat-header@3x.png; do
  grep -Fq "location = ${logo_path} {" "${branding_locations}" || {
    echo "Campus Dify branding overlay omits ${logo_path}" >&2
    exit 1
  }
done
[[ "$(grep -Fc 'include /etc/nginx/campus-branding/logo-response.conf;' "${branding_locations}")" == "8" ]] || {
  echo "Campus Dify logo routes do not share the reviewed branding response" >&2
  exit 1
}
grep -Fq 'alias /etc/nginx/campus-branding/njit-logo.png;' "${branding_response}" || {
  echo "Campus Dify logo routes do not serve the approved branding asset" >&2
  exit 1
}
grep -Fq 'default_type image/png;' "${branding_response}" || {
  echo "Campus Dify logo routes do not declare the PNG media type" >&2
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
for directive in \
  'auth_request /_campus_access_check;' \
  'error_page 401 = @campus_portal_entry;' \
  'error_page 403 = @campus_portal_entry;' \
  'proxy_pass http://web:3000;'; do
  printf '%s\n' "${root_location}" | grep -Fq "${directive}" || {
    echo "Campus root does not preserve authenticated Dify home access: ${directive}" >&2
    exit 1
  }
done

portal_entry_location="$(sed -n '/^[[:space:]]*location @campus_portal_entry {$/,/^[[:space:]]*}/p' "${template}")"
printf '%s\n' "${portal_entry_location}" | grep -Fq 'return 302 /portal/;' || {
  echo "Unauthenticated Campus root does not enter the Access portal" >&2
  exit 1
}

signin_location="$(sed -n '/^[[:space:]]*location = \/signin {$/,/^[[:space:]]*}/p' "${template}")"
printf '%s\n' "${signin_location}" | grep -Fq 'return 302 /portal/;' || {
  echo "Post-logout Campus sign-in does not return students to the Access portal" >&2
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

blocked_auth_patterns="$(awk '
  /^[[:space:]]*location ~ / {
    pattern = $0
    sub(/^[[:space:]]*location ~ /, "", pattern)
    sub(/[[:space:]]*\{[[:space:]]*$/, "", pattern)
    in_location = 1
    next
  }
  in_location && /return 404;/ { print pattern }
  in_location && /^[[:space:]]*}/ { in_location = 0 }
' "${template}")"
[[ -n "${blocked_auth_patterns}" ]] || {
  echo "Campus nginx does not define blocked student authentication routes" >&2
  exit 1
}

for route in \
  /signin/check-code \
  /signup \
  /console/api/login \
  /console/api/email-code-login \
  /console/api/oauth/login/github; do
  route_is_blocked=false
  while IFS= read -r blocked_auth_pattern; do
    if printf '%s\n' "${route}" | grep -Eq "${blocked_auth_pattern}"; then
      route_is_blocked=true
      break
    fi
  done <<<"${blocked_auth_patterns}"
  [[ "${route_is_blocked}" == "true" ]] || {
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
