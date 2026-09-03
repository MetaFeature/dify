#!/usr/bin/env bash
# Prove the one-time provider migration preserves protected values, creates a
# rollback copy, and is idempotent once the OpenAI-compatible schema is active.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE_ROOT="$(mktemp -d)"
trap 'find "${FIXTURE_ROOT}" -depth -delete' EXIT

fixture_env="${FIXTURE_ROOT}/campus.env"
backup_root="${FIXTURE_ROOT}/backups"
approved_plugin="$(sed -n '1p' "${SCRIPT_DIR}/approved-provider-plugin.txt")"

printf '%s\n' \
  'CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN=must-remain-untouched' \
  'CAMPUS_MODEL_PROVIDER=langgenius/openai/openai' \
  'CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER=langgenius/openai:1.0.4@legacy' \
  'CAMPUS_MODEL_PROVIDER_CREDENTIAL_NAME=Campus managed' \
  'CAMPUS_MODEL_PROVIDER_API_KEY_FIELD=openai_api_key' \
  'CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD=openai_api_base' >"${fixture_env}"
chmod 600 "${fixture_env}"

CAMPUS_ENV_FILE="${fixture_env}" CAMPUS_BACKUP_ROOT="${backup_root}" \
  "${SCRIPT_DIR}/manage.sh" migrate-provider-config >/dev/null

grep -Fxq 'CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN=must-remain-untouched' "${fixture_env}"
grep -Fxq 'CAMPUS_MODEL_PROVIDER=langgenius/openai_api_compatible/openai_api_compatible' "${fixture_env}"
grep -Fxq "CAMPUS_MODEL_PROVIDER_PLUGIN_UNIQUE_IDENTIFIER=${approved_plugin}" "${fixture_env}"
grep -Fxq 'CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE=model' "${fixture_env}"
grep -Fxq 'CAMPUS_MODEL_PROVIDER_API_KEY_FIELD=api_key' "${fixture_env}"
grep -Fxq 'CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD=endpoint_url' "${fixture_env}"
[[ "$(stat -c '%a' "${fixture_env}" 2>/dev/null || stat -f '%Lp' "${fixture_env}")" == "600" ]]

backup_count="$(find "${backup_root}" -type f -name campus.env -print | wc -l | tr -d ' ')"
backup_file="$(find "${backup_root}" -type f -name campus.env -print -quit)"
[[ "${backup_count}" -eq 1 ]]
grep -Fxq 'CAMPUS_MODEL_PROVIDER=langgenius/openai/openai' "${backup_file}"
grep -Fxq 'CAMPUS_NEWAPI_ADMIN_ACCESS_TOKEN=must-remain-untouched' "${backup_file}"

CAMPUS_ENV_FILE="${fixture_env}" CAMPUS_BACKUP_ROOT="${backup_root}" \
  "${SCRIPT_DIR}/manage.sh" migrate-provider-config >/dev/null
backup_count_after_retry="$(find "${backup_root}" -type f -name campus.env -print | wc -l | tr -d ' ')"
[[ "${backup_count_after_retry}" -eq 1 ]]
