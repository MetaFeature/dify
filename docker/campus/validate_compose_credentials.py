#!/usr/bin/env python3
"""Validate rendered Redis/Celery wiring without emitting credential values."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit


class CredentialConfigurationError(ValueError):
    """Raised when rendered Compose services disagree on protected Redis wiring."""


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
    """Require every Dify Celery client to use the rendered Redis endpoint and password."""

    root = _mapping(config, "root configuration")
    services = _mapping(root.get("services"), "services")
    redis_service = _mapping(services.get("redis"), "redis service")
    redis_environment = _mapping(redis_service.get("environment"), "redis environment")
    redis_password = _required_text(redis_environment, "REDISCLI_AUTH", "Redis credential")

    for service_name in ("api", "worker", "worker_beat"):
        service = _mapping(services.get(service_name), f"{service_name} service")
        environment = _mapping(service.get("environment"), f"{service_name} environment")
        broker_url = _required_text(environment, "CELERY_BROKER_URL", f"{service_name} Celery broker URL")
        redis_host = _required_text(environment, "REDIS_HOST", f"{service_name} Redis host")
        redis_port = _required_text(environment, "REDIS_PORT", f"{service_name} Redis port")

        parsed = urlsplit(broker_url)
        try:
            parsed_port = parsed.port
        except ValueError as error:
            raise CredentialConfigurationError(f"{service_name} broker endpoint is invalid") from error
        if parsed.scheme not in {"redis", "rediss"} or parsed.hostname != redis_host or str(parsed_port) != redis_port:
            raise CredentialConfigurationError(f"{service_name} broker endpoint does not match Redis")
        if parsed.path != "/1":
            raise CredentialConfigurationError(f"{service_name} broker must use the Celery Redis database")
        if unquote(parsed.password or "") != redis_password:
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
