#!/usr/bin/env bash
set -Eeuo pipefail

IPTABLES_BIN="${CAMPUS_IPTABLES_BIN:-iptables}"
IP_BIN="${CAMPUS_IP_BIN:-ip}"
LOOPBACK_INTERFACE="${CAMPUS_WSL_LOOPBACK_INTERFACE:-loopback0}"
LOOPBACK_PORTS="${CAMPUS_WSL_LOOPBACK_PORTS:-13000,18080,18081,18082,18444}"
RULE_COMMENT="njit-campus-wsl-host-loopback"

fail() {
  echo "campus-loopback-routing: $*" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" == "0" ]] || fail "root privileges are required"
}

require_runtime() {
  command -v "${IPTABLES_BIN}" >/dev/null || fail "iptables is required"
  command -v "${IP_BIN}" >/dev/null || fail "ip is required"
  "${IP_BIN}" link show "${LOOPBACK_INTERFACE}" >/dev/null 2>&1 || \
    fail "WSL mirrored loopback interface ${LOOPBACK_INTERFACE} is unavailable"
  "${IPTABLES_BIN}" -t raw -S PREROUTING >/dev/null 2>&1 || \
    fail "iptables raw PREROUTING chain is unavailable"
  "${IPTABLES_BIN}" -t nat -S DOCKER >/dev/null 2>&1 || \
    fail "Docker iptables chain is unavailable"
}

raw_rule=(
  -i "${LOOPBACK_INTERFACE}"
  -s 127.0.0.0/8
  -d 127.0.0.1/32
  -p tcp
  -m multiport --dports "${LOOPBACK_PORTS}"
  -m comment --comment "${RULE_COMMENT}"
  -j ACCEPT
)

nat_rule=(
  -i "${LOOPBACK_INTERFACE}"
  -s 127.0.0.0/8
  -d 127.0.0.1/32
  -p tcp
  -m multiport --dports "${LOOPBACK_PORTS}"
  -m comment --comment "${RULE_COMMENT}"
  -j RETURN
)

remove_rule_copies() {
  local table="$1" chain="$2"
  shift 2
  while "${IPTABLES_BIN}" -t "${table}" -C "${chain}" "$@" 2>/dev/null; do
    "${IPTABLES_BIN}" -t "${table}" -D "${chain}" "$@"
  done
}

apply_rules() {
  require_runtime
  remove_rule_copies raw PREROUTING "${raw_rule[@]}"
  remove_rule_copies nat DOCKER "${nat_rule[@]}"
  "${IPTABLES_BIN}" -t raw -I PREROUTING 1 "${raw_rule[@]}"
  "${IPTABLES_BIN}" -t nat -I DOCKER 1 "${nat_rule[@]}"
  verify_rules
}

assert_first_rule() {
  local table="$1" chain="$2" first_rule
  first_rule="$("${IPTABLES_BIN}" -t "${table}" -S "${chain}" | awk '$1 == "-A" { print; exit }')"
  [[ "${first_rule}" == *"--comment ${RULE_COMMENT}"* ]] || \
    fail "${table}/${chain} loopback exception is not first"
}

verify_rules() {
  require_runtime
  "${IPTABLES_BIN}" -t raw -C PREROUTING "${raw_rule[@]}" 2>/dev/null || \
    fail "raw loopback exception is missing"
  "${IPTABLES_BIN}" -t nat -C DOCKER "${nat_rule[@]}" 2>/dev/null || \
    fail "Docker NAT loopback exception is missing"
  assert_first_rule raw PREROUTING
  assert_first_rule nat DOCKER
  echo "Campus WSL loopback routing verified for TCP ${LOOPBACK_PORTS}."
}

remove_rules() {
  if "${IPTABLES_BIN}" -t raw -S PREROUTING >/dev/null 2>&1; then
    remove_rule_copies raw PREROUTING "${raw_rule[@]}"
  fi
  if "${IPTABLES_BIN}" -t nat -S DOCKER >/dev/null 2>&1; then
    remove_rule_copies nat DOCKER "${nat_rule[@]}"
  fi
}

require_root
case "${1:-}" in
  apply) apply_rules ;;
  verify) verify_rules ;;
  remove) remove_rules ;;
  *) fail "usage: $0 {apply|verify|remove}" ;;
esac
