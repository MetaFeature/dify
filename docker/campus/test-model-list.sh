#!/usr/bin/env bash
# Checks validate_campus_model_list against inputs a deployment could really
# hold. The model list decides which gateway models land in every student
# workspace, and a typo in it otherwise surfaces only when a student signs in.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE_ENV="$(mktemp)"
trap 'rm -f "${FIXTURE_ENV}"' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

CAMPUS_ENV_FILE="${FIXTURE_ENV}"
env_value() {
  local key="$1"
  [[ -f "${CAMPUS_ENV_FILE}" ]] || return 0
  awk -v key="${key}" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' "${CAMPUS_ENV_FILE}"
}

# Exercise the real function rather than a copy of it.
function_source="$(sed -n '/^validate_campus_model_list() {$/,/^}$/p' "${SCRIPT_DIR}/manage.sh")"
eval "${function_source}"
declare -F validate_campus_model_list >/dev/null || \
  fail "could not load validate_campus_model_list from manage.sh"

failures=0
expect() {
  local label="$1" value="$2" want="$3" got
  printf 'CAMPUS_MODEL_PROVIDER_MODELS=%s\n' "${value}" >"${CAMPUS_ENV_FILE}"
  # A subshell, because a rejection calls fail, and fail exits.
  if ( validate_campus_model_list ) >/dev/null 2>&1; then got=accepted; else got=rejected; fi
  if [[ "${got}" != "${want}" ]]; then
    echo "model list ${label}: ${got}, expected ${want} (value: ${value})" >&2
    failures=$((failures + 1))
  fi
}

expect "default deployment" \
  "llm:deepseek-v4-flash,llm:deepseek-v4-flash-0817,llm:glm-5.3-flash,text-embedding:bge-m3,rerank:bge-reranker-v2-m3" \
  accepted
expect "surrounding whitespace" "llm:a , text-embedding:b " accepted
expect "every supported type" \
  "llm:a,text-embedding:b,rerank:c,speech2text:d,tts:e" accepted

expect "unset" "" rejected
expect "a type Dify has no slot for" "image:doubao-seedream-5.0-pro" rejected
expect "a type the approved provider does not support" "llm:a,moderation:text-moderation-latest" rejected
expect "an entry with no type" "deepseek-v4-flash" rejected
expect "an entry with no name" "llm:" rejected
expect "no llm to validate credentials against" "text-embedding:bge-m3" rejected

# A trailing entry is the one a line-oriented reader drops most easily, and
# dropping it would wave a mispriced or unroutable model straight through.
expect "a bad type in the last position" "llm:deepseek-v4-flash,image:doubao-seedream-5.0-pro" rejected
expect "a missing type in the last position" "llm:deepseek-v4-flash,bge-m3" rejected

[[ "${failures}" -eq 0 ]] || fail "${failures} model list check(s) behaved unexpectedly"
