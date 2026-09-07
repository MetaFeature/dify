#!/usr/bin/env bash
# Exercises the deployment boundary that assigns different approved models to
# different NewAPI channels. A shared model list would make one valid channel
# advertise models that its upstream cannot serve.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE_ENV="$(mktemp)"
trap 'unlink "${FIXTURE_ENV}" 2>/dev/null || true' EXIT

fail() {
  echo "$*" >&2
  exit 1
}

CAMPUS_ENV_FILE="${FIXTURE_ENV}"
env_value() {
  local key="$1"
  awk -v key="${key}" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); print; exit }' \
    "${CAMPUS_ENV_FILE}"
}
env_has_key() {
  local key="$1"
  grep -q "^${key}=" "${CAMPUS_ENV_FILE}"
}

function_source="$(
  sed -n '/^gateway_channel_model_rows() {$/,/^validate_campus_model_list() {$/p' \
    "${SCRIPT_DIR}/manage.sh" | sed '$d'
)"
eval "${function_source}"
for function_name in gateway_channel_model_rows validate_gateway_channel_models; do
  declare -F "${function_name}" >/dev/null || fail "could not load ${function_name} from manage.sh"
done

write_valid_catalog() {
  cat >"${CAMPUS_ENV_FILE}" <<'EOF'
CAMPUS_MODEL_PROVIDER_MODELS=llm:deepseek-v4-flash,llm:deepseek-v4-flash-0817,llm:glm-5.3-flash,text-embedding:bge-m3,rerank:bge-reranker-v2-m3
CAMPUS_NEWAPI_CHANNEL_MODELS_JSON={"1":{"channel_type":43,"base_url":"","models":["deepseek-v4-flash"]},"2":{"channel_type":1,"base_url":"https://ai.ctaigw.cn","models":["deepseek-v4-flash-0817","glm-5.3-flash","bge-m3","bge-reranker-v2-m3"]}}
CAMPUS_NEWAPI_RETIRED_CHANNEL_IDS=
EOF
}

write_valid_catalog
expected_rows=$'1\trouting\t{"models":["deepseek-v4-flash"],"channel_type":43,"base_url":""}\n2\trouting\t{"models":["deepseek-v4-flash-0817","glm-5.3-flash","bge-m3","bge-reranker-v2-m3"],"channel_type":1,"base_url":"https://ai.ctaigw.cn"}'
actual_rows="$(gateway_channel_model_rows)"
[[ "${actual_rows}" == "${expected_rows}" ]] || fail "per-channel model catalog was not parsed deterministically"
validate_gateway_channel_models

cat >"${CAMPUS_ENV_FILE}" <<'EOF'
CAMPUS_MODEL_PROVIDER_MODELS=llm:deepseek-v4-flash,llm:missing-model
CAMPUS_NEWAPI_CHANNEL_MODELS_JSON={"1":{"channel_type":43,"base_url":"","models":["deepseek-v4-flash"]}}
CAMPUS_NEWAPI_RETIRED_CHANNEL_IDS=
EOF
if (validate_gateway_channel_models) >/dev/null 2>&1; then
  fail "catalog accepted a configured Dify model with no gateway channel"
fi

cat >>"${CAMPUS_ENV_FILE}" <<'EOF'
CAMPUS_NEWAPI_PRESERVE_CHANNEL_MODELS=true
EOF
validate_gateway_channel_models

write_valid_catalog
sed -i.bak 's/CAMPUS_NEWAPI_RETIRED_CHANNEL_IDS=$/CAMPUS_NEWAPI_RETIRED_CHANNEL_IDS=2/' "${CAMPUS_ENV_FILE}"
rm -f "${CAMPUS_ENV_FILE}.bak"
if (validate_gateway_channel_models) >/dev/null 2>&1; then
  fail "catalog accepted a channel as both active and retired"
fi

cat >"${CAMPUS_ENV_FILE}" <<'EOF'
CAMPUS_MODEL_PROVIDER_MODELS=llm:deepseek-v4-flash
CAMPUS_NEWAPI_REQUIRED_CHANNEL_IDS=1
CAMPUS_NEWAPI_REQUIRED_CHANNEL_MODELS=deepseek-v4-flash
CAMPUS_NEWAPI_RETIRED_CHANNEL_IDS=2
EOF
[[ "$(gateway_channel_model_rows)" == $'1\tmodels\t{"models":["deepseek-v4-flash"]}' ]] || \
  fail "legacy shared channel model configuration is no longer compatible"
validate_gateway_channel_models

catalog_function_source="$(
  sed -n '/^gateway_catalog_model_spec() {$/,/^sync_gateway_upstream_models_if_due() {$/p' \
    "${SCRIPT_DIR}/manage.sh" | sed '$d'
)"
eval "${catalog_function_source}"
gateway_admin_get() {
  cat <<'JSON'
{"success":true,"data":{"revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","models":[{"name":"qwen3.8-flash","model_type":"llm"},{"name":"bge-m3","model_type":"text-embedding"}],"excluded":[]}}
JSON
}
[[ "$(gateway_catalog_model_spec 13000)" == \
  "llm:qwen3.8-flash,text-embedding:bge-m3" ]] || \
  fail "gateway catalog was not converted to a Dify model spec"

echo "Campus gateway model routing checks passed."
