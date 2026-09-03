import pytest

from configs.extra.campus_config import CampusConfig


def test_campus_model_defaults_match_openai_compatible_model_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "CAMPUS_MODEL_PROVIDER",
        "CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE",
        "CAMPUS_MODEL_PROVIDER_API_KEY_FIELD",
        "CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD",
    ):
        monkeypatch.delenv(key, raising=False)

    config = CampusConfig(_env_file=None)

    assert config.CAMPUS_MODEL_PROVIDER == "langgenius/openai_api_compatible/openai_api_compatible"
    assert config.CAMPUS_MODEL_PROVIDER_CREDENTIAL_SCOPE == "model"
    assert config.CAMPUS_MODEL_PROVIDER_API_KEY_FIELD == "api_key"
    assert config.CAMPUS_MODEL_PROVIDER_BASE_URL_FIELD == "endpoint_url"
