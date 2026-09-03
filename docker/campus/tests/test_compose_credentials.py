from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote


MODULE_PATH = Path(__file__).parents[1] / "validate_compose_credentials.py"
SPEC = importlib.util.spec_from_file_location("validate_compose_credentials", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load Compose credential validator")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class EnvironmentFixture(TypedDict, total=False):
    API_KEY: str
    CELERY_BROKER_URL: str
    CODE_EXECUTION_API_KEY: str
    REDISCLI_AUTH: str
    REDIS_HOST: str
    REDIS_PORT: str


class ServiceFixture(TypedDict):
    environment: EnvironmentFixture


class ServicesFixture(TypedDict):
    redis: ServiceFixture
    sandbox: ServiceFixture
    api: ServiceFixture
    worker: ServiceFixture
    worker_beat: ServiceFixture


class ComposeConfigFixture(TypedDict):
    services: ServicesFixture


def compose_config(redis_password: str, broker_password: str | None = None) -> ComposeConfigFixture:
    encoded_password = quote(broker_password if broker_password is not None else redis_password, safe="")
    broker_url = f"redis://:{encoded_password}@redis:6379/1"
    shared_environment = {
        "CELERY_BROKER_URL": broker_url,
        "CODE_EXECUTION_API_KEY": "sandbox-key",
        "REDIS_HOST": "redis",
        "REDIS_PORT": "6379",
    }
    return {
        "services": {
            "redis": {"environment": {"REDISCLI_AUTH": redis_password}},
            "sandbox": {"environment": {"API_KEY": "sandbox-key"}},
            "api": {"environment": dict(shared_environment)},
            "worker": {"environment": dict(shared_environment)},
            "worker_beat": {"environment": dict(shared_environment)},
        }
    }


class ComposeCredentialValidationTest(unittest.TestCase):
    def test_accepts_matching_percent_encoded_redis_credentials(self) -> None:
        MODULE.validate_compose_credentials(compose_config("campus #pass@word/with spaces"))

    def test_rejects_stale_celery_password_without_disclosing_it(self) -> None:
        stale_password = "old-password-must-not-leak"

        with self.assertRaisesRegex(MODULE.CredentialConfigurationError, "credential does not match") as caught:
            MODULE.validate_compose_credentials(compose_config("current-password", stale_password))

        self.assertNotIn(stale_password, str(caught.exception))

    def test_rejects_wrong_broker_endpoint(self) -> None:
        config = compose_config("current-password")
        config["services"]["worker"]["environment"]["CELERY_BROKER_URL"] = (
            "redis://:current-password@other-redis:6380/0"
        )

        with self.assertRaisesRegex(MODULE.CredentialConfigurationError, "worker broker endpoint"):
            MODULE.validate_compose_credentials(config)

    def test_rejects_stale_code_execution_key_without_disclosing_it(self) -> None:
        stale_key = "old-sandbox-key-must-not-leak"
        config = compose_config("current-password")
        config["services"]["worker"]["environment"]["CODE_EXECUTION_API_KEY"] = stale_key

        with self.assertRaisesRegex(MODULE.CredentialConfigurationError, "sandbox credential does not match") as caught:
            MODULE.validate_compose_credentials(config)

        self.assertNotIn(stale_key, str(caught.exception))

    def test_rejects_transport_and_acl_forms_not_used_by_internal_redis(self) -> None:
        for broker_url in (
            "rediss://:current-password@redis:6379/1",
            "redis://campus:current-password@redis:6379/1",
        ):
            with self.subTest(broker_url=broker_url):
                config = compose_config("current-password")
                config["services"]["worker"]["environment"]["CELERY_BROKER_URL"] = broker_url

                with self.assertRaisesRegex(MODULE.CredentialConfigurationError, "worker broker"):
                    MODULE.validate_compose_credentials(config)

    def test_cli_redacts_malformed_broker_netloc(self) -> None:
        secret = "malformed-secret-must-not-leak"
        config = compose_config("current-password")
        config["services"]["worker"]["environment"]["CELERY_BROKER_URL"] = (
            f"redis://:{secret}@／redis:6379/1"
        )

        result = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=json.dumps(config),
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("campus-compose:", result.stderr)


if __name__ == "__main__":
    unittest.main()
