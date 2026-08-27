from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from urllib.parse import quote


MODULE_PATH = Path(__file__).parents[1] / "validate_compose_credentials.py"
SPEC = importlib.util.spec_from_file_location("validate_compose_credentials", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load Compose credential validator")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ComposeConfigFixture = dict[str, dict[str, dict[str, dict[str, str]]]]


def compose_config(redis_password: str, broker_password: str | None = None) -> ComposeConfigFixture:
    encoded_password = quote(broker_password if broker_password is not None else redis_password, safe="")
    broker_url = f"redis://:{encoded_password}@redis:6379/1"
    shared_environment = {
        "CELERY_BROKER_URL": broker_url,
        "REDIS_HOST": "redis",
        "REDIS_PORT": "6379",
    }
    return {
        "services": {
            "redis": {"environment": {"REDISCLI_AUTH": redis_password}},
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


if __name__ == "__main__":
    unittest.main()
