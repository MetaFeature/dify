#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
template="${SCRIPT_DIR}/nginx/default.conf.template"
pattern="$(sed -n 's/^[[:space:]]*location ~ \(.*\) {$/\1/p' "${template}")"

[[ -n "${pattern}" ]] || {
  echo "missing Campus nginx bootstrap allowlist" >&2
  exit 1
}

for endpoint in \
  init login refresh-token setup parameters system-features features \
  account/profile workspaces/current version activate/check; do
  route="/console/api/${endpoint}"
  printf '%s\n' "${route}" | grep -Eq "${pattern}" || {
    echo "bootstrap route is blocked: ${route}" >&2
    exit 1
  }
done

for route in \
  /console/api/apps \
  /console/api/account/password \
  /console/api/workspaces/current/model-providers \
  /console/api/campus/admin/students; do
  if printf '%s\n' "${route}" | grep -Eq "${pattern}"; then
    echo "protected route entered bootstrap allowlist: ${route}" >&2
    exit 1
  fi
done
