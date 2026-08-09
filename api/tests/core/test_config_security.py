from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "secret_key": "j" * 32,
        "meili_master_key": "production-meili-key",
        "eurooffice_jwt_secret": "o" * 32,
        "eurooffice_file_token_secret": "f" * 32,
        "s3_access_key": "production-storage-access",
        "s3_secret_key": "production-storage-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("secret_key", "short", "SECRET_KEY must contain at least 32 bytes"),
        (
            "eurooffice_jwt_secret",
            "short",
            "EUROOFFICE_JWT_SECRET must contain at least 32 bytes",
        ),
        (
            "eurooffice_file_token_secret",
            "short",
            "EUROOFFICE_FILE_TOKEN_SECRET must contain at least 32 bytes",
        ),
        ("s3_access_key", "minioadmin", "development credentials are forbidden"),
        ("s3_secret_key", "minioadmin", "development credentials are forbidden"),
        ("webhook_secret", "short", "WEBHOOK_SECRET must contain at least 32 bytes"),
    ],
)
def test_production_rejects_weak_or_development_secrets(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _production_settings(**{field: value})


def test_production_worker_delivery_requires_strong_hmac_secret() -> None:
    with pytest.raises(ValidationError, match="WORKER_ZIP_HMAC_SECRET must contain"):
        _production_settings(
            worker_zip_url="https://downloads.example.test",
            worker_zip_hmac_secret="short",
        )


def test_production_accepts_independent_strong_secrets() -> None:
    settings = _production_settings(
        worker_zip_url="https://downloads.example.test",
        worker_zip_hmac_secret="w" * 32,
        webhook_secret="h" * 32,
    )
    assert settings.environment == "production"
