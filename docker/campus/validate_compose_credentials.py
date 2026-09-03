#!/usr/bin/env python3
"""Validate rendered service credentials without emitting their values."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit


class CredentialConfigurationError(ValueError):
    """Raised when rendered Compose services disagree on protected credentials."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CredentialConfigurationError(f"rendered Compose is missing {label}")
    return value


def _required_text(values: Mapping[str, object], key: str, label: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise CredentialConfigurationError(f"rendered Compose is missing {label}")
    return value


def validate_compose_credentials(config: object) -> None:
    """Require Dify clients to use the rendered Redis and sandbox credentials."""

    root = _mapping(config, "root configuration")
    services = _mapping(root.get("services"), "services")
    redis_service = _mapping(services.get("redis"), "redis service")
    redis_environment = _mapping(redis_service.get("environment"), "redis environment")
    redis_password = _required_text(redis_environment, "REDISCLI_AUTH", "Redis credential")
    sandbox_service = _mapping(services.get("sandbox"), "sandbox service")
    sandbox_environment = _mapping(sandbox_service.get("environment"), "sandbox environment")
    sandbox_api_key = _required_text(sandbox_environment, "API_KEY", "sandbox credential")

    for service_name in ("api", "worker", "worker_beat"):
        service = _mapping(services.get(service_name), f"{service_name} service")
        environment = _mapping(service.get("environment"), f"{service_name} environment")
        broker_url = _required_text(environment, "CELERY_BROKER_URL", f"{service_name} Celery broker URL")
        redis_host = _required_text(environment, "REDIS_HOST", f"{service_name} Redis host")
        redis_port = _required_text(environment, "REDIS_PORT", f"{service_name} Redis port")

        if service_name in {"api", "worker"}:
            code_execution_api_key = _required_text(
                environment,
                "CODE_EXECUTION_API_KEY",
                f"{service_name} code execution credential",
            )
            if code_execution_api_key != sandbox_api_key:
                raise CredentialConfigurationError(f"{service_name} sandbox credential does not match")

        try:
            parsed = urlsplit(broker_url)
            parsed_scheme = parsed.scheme
            parsed_hostname = parsed.hostname
            parsed_port = parsed.port
            parsed_username = unquote(parsed.username or "")
            parsed_password = unquote(parsed.password or "")
            parsed_path = parsed.path
        except (UnicodeError, ValueError):
            raise CredentialConfigurationError(f"{service_name} broker endpoint is invalid") from None
        if parsed_scheme != "redis" or parsed_username:
            raise CredentialConfigurationError(f"{service_name} broker transport does not match Redis")
        if parsed_hostname != redis_host or str(parsed_port) != redis_port:
            raise CredentialConfigurationError(f"{service_name} broker endpoint does not match Redis")
        if parsed_path != "/1":
            raise CredentialConfigurationError(f"{service_name} broker must use the Celery Redis database")
        if parsed_password != redis_password:
            raise CredentialConfigurationError(f"{service_name} broker credential does not match Redis")


def main() -> int:
    try:
        validate_compose_credentials(json.load(sys.stdin))
    except (CredentialConfigurationError, json.JSONDecodeError) as error:
        print(f"campus-compose: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
