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

access_check_location="$(sed -n '/^[[:space:]]*location = \/_campus_access_check {$/,/^[[:space:]]*}/p' "${template}")"
printf '%s\n' "${access_check_location}" | \
  grep -Fq 'proxy_set_header Cookie "campus_portal_session=$cookie_campus_portal_session";' || {
    echo "Campus access checks can still authenticate with a stock Dify cookie" >&2
    exit 1
  }

for upstream in campus_api campus_portal campus_web campus_plugin_daemon; do
  upstream_block="$(sed -n "/^upstream ${upstream} {$/,/^}$/p" "${template}")"
  [[ -n "${upstream_block}" ]] || {
    echo "Campus nginx does not define the ${upstream} connection pool" >&2
    exit 1
  }
  printf '%s\n' "${upstream_block}" | grep -Eq '^[[:space:]]+keepalive [1-9][0-9]*;' || {
    echo "Campus nginx ${upstream} does not reuse upstream connections" >&2
    exit 1
  }
done

if grep -Eq 'proxy_pass http://(api:5001|portal:8080|web:3000|plugin_daemon:5002)' "${template}"; then
  echo "Campus nginx still bypasses its bounded upstream connection pools" >&2
  exit 1
fi

grep -Fq 'proxy_pass http://campus_portal/;' "${template}" || {
  echo "Access portal is not routed through its pooled frontend upstream" >&2
  exit 1
}

grep -Eq '^[[:space:]]*location \^~ /campus-admin/ \{' "${template}" || {
  echo "Campus nginx does not expose the administration portal assets" >&2
  exit 1
}

grep -Fq 'root /campus-admin-html;' "${template}" || {
  echo "Campus administration portal does not own the administrator listener root" >&2
  exit 1
}

grep -Fq './campus/admin/index.html:/campus-admin-html/index.html:ro' "${compose_overlay}" || {
  echo "Campus Compose does not mount the administration portal page" >&2
  exit 1
}

grep -Fq './campus/admin/assets:/campus-admin-html/assets:ro' "${compose_overlay}" || {
  echo "Campus Compose does not mount the administration portal assets" >&2
  exit 1
}

grep -Fq '127.0.0.1:${CAMPUS_MANUAL_PUBLIC_PORT:-18083}:8082' "${compose_overlay}" || {
  echo "Campus Compose does not publish the isolated manual origin on loopback" >&2
  exit 1
}
manual_server="$(sed -n '/^# Original learning documents run on a separate browser origin/,$p' "${template}")"
printf '%s\n' "${manual_server}" | grep -Fq 'listen 8082;' || {
  echo "Campus nginx does not define the isolated manual origin" >&2
  exit 1
}
printf '%s\n' "${manual_server}" | grep -Fq 'proxy_pass http://campus_api;' || {
  echo "Campus manual origin does not stream documents from the Campus API" >&2
  exit 1
}
if printf '%s\n' "${manual_server}" | grep -Fq 'Content-Security-Policy'; then
  echo "Campus manual origin still changes uploaded HTML behaviour with CSP" >&2
  exit 1
fi
[[ "$(grep -Fc 'location ~ ^/console/api/campus/lab-manuals/documents/[0-9a-fA-F-]+/content$ {' "${template}")" == "3" ]] || {
  echo "Original HTML must be blocked on Portal/Admin origins and exposed only on the manual origin" >&2
  exit 1
}

root_location="$(sed -n '/^[[:space:]]*location = \/ {$/,/^[[:space:]]*}/p' "${template}")"
for directive in \
  'auth_request /_campus_access_check;' \
  'error_page 401 = @campus_portal_entry;' \
  'error_page 403 = @campus_portal_entry;' \
  'proxy_pass http://campus_web;'; do
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

logout_location="$(sed -n '/^[[:space:]]*location = \/console\/api\/logout {$/,/^[[:space:]]*}/p' "${template}")"
printf '%s\n' "${logout_location}" | \
  grep -Fq 'proxy_pass http://campus_api/console/api/campus/session/logout;' || {
    echo "Dify logout does not revoke the Campus portal session" >&2
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

# Administration routes must not be published on a campus interface: named
# administrators reach them through the host-loopback listener instead.
admin_api_block="$(sed -n '/^[[:space:]]*location \^~ \/console\/api\/campus\/admin\/ {$/,/^[[:space:]]*}$/p' "${template}")"
printf '%s\n' "${admin_api_block}" | grep -Fq 'return 404;' || {
  echo "Campus administration API is published on the campus listener" >&2
  exit 1
}
campus_listener="$(sed -n '1,/^# The stock Dify administration surface/p' "${template}")"
printf '%s\n' "${campus_listener}" | grep -Fq 'location ^~ /console/api/campus/admin/' || {
  echo "the administration API block is not on the campus listener" >&2
  exit 1
}
admin_listener="$(sed -n '/^# The stock Dify administration surface/,$p' "${template}")"
if printf '%s\n' "${admin_listener}" | grep -Fq '/console/api/campus/admin/'; then
  echo "the administration listener must keep the administration API reachable" >&2
  exit 1
fi

model_provider_location="$(sed -n '/^[[:space:]]*location \^~ \/console\/api\/workspaces\/current\/model-providers {$/,/^[[:space:]]*}$/p' "${template}")"
[[ -n "${model_provider_location}" ]] || {
  echo "Campus nginx does not guard the workspace model-provider routes" >&2
  exit 1
}
for directive in \
  'auth_request /_campus_access_check;' \
  'limit_except GET {' \
  'deny all;'; do
  printf '%s\n' "${model_provider_location}" | grep -Fq "${directive}" || {
    echo "students can still change their own model providers: missing ${directive}" >&2
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
