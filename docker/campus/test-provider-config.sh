#!/usr/bin/env bash
# Exercise the deployment guard for the exact OpenAI-compatible credential
# schema. A plugin id alone is insufficient: provider-vs-model scope and field
# names decide whether Dify can persist the managed key.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE_ENV="$(mktemp)"
trap 'rm -f "${FIXTURE_ENV}"' EXIT

CAMPUS_ENV_FILE="${FIXTURE_ENV}"
APPROVED_PROVIDER_PLUGIN_FILE="${SCRIPT_DIR}/approved-provider-plugin.txt"
APPROVED_MODEL_PROVIDER="langgenius/openai_api_compatible/openai_api_compatible"
APPROVED_MODEL_CREDENTIAL_SCOPE="model"
APPROVED_MODEL_API_KEY_FIELD="api_key"
APPROVED_MODEL_BASE_URL_FIELD="endpoint_url"

fail() {
  echo "$*" >&2
  exit 1
}

env_value() {
  local key="$1"
  awk -v key="${key}" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' "${CAMPUS_ENV_FILE}"
}

# Exercise the real function rather than duplicating its deployment rules.
function_source="$(sed -n '/^validate_gateway_build_images() {$/,/^}$/p' "${SCRIPT_DIR}/manage.sh")"
eval "${function_source}"
declare -F validate_gateway_build_images >/dev/null || \
  fail "could not load validate_gateway_build_images from manage.sh"

approved_plugin="$(sed -n '1p' "${APPROVED_PROVIDER_PLUGIN_FILE}")"

write_config() {
  local provider="$1" scope="$2" api_key_field="$3" base_url_field="$4" plugin="${5:-${approved_plugin}}"
  printf '%s\n' \
    "CAMPUS_MODEL_PROVIDER=${provider}" \
    "CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE=${scope}" \
    "CAMPUS_MODEL_PROVIDER_API_KEY_FIELD=${api_key_field}" \
    "CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD=${base_url_field}" \
    "CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER=${plugin}" >"${CAMPUS_ENV_FILE}"
}

failures=0
expect() {
  local label="$1" want="$2" got
  if ( validate_gateway_build_images ) >/dev/null 2>&1; then got=accepted; else got=rejected; fi
  if [[ "${got}" != "${want}" ]]; then
    echo "provider config ${label}: ${got}, expected ${want}" >&2
    failures=$((failures + 1))
  fi
}

write_config "langgenius/openai_api_compatible/openai_api_compatible" model api_key endpoint_url
expect "approved schema" accepted

write_config "langgenius/openai/openai" model api_key endpoint_url
expect "native OpenAI provider" rejected

write_config "langgenius/openai_api_compatible/openai_api_compatible" provider api_key endpoint_url
expect "provider-level credential" rejected

write_config "langgenius/openai_api_compatible/openai_api_compatible" model openai_api_key endpoint_url
expect "native OpenAI API key field" rejected

write_config "langgenius/openai_api_compatible/openai_api_compatible" model api_key openai_api_base
expect "native OpenAI base URL field" rejected

[[ "${failures}" -eq 0 ]] || fail "${failures} provider configuration check(s) behaved unexpectedly"
