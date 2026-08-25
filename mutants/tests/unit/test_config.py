"""Configuration loading (Execution Playbook, Phase 0: env injection, never files)."""

from __future__ import annotations

import pytest

from prayas.config import ConfigError, Settings, owner_database_url


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAYAS_ENV", "staging")
    monkeypatch.setenv("PRAYAS_LOG_LEVEL", "debug")
    monkeypatch.setenv("PRAYAS_DATABASE_URL_APP", "postgresql+asyncpg://u:p@h/db")

    settings = Settings.from_env()

    assert settings.env == "staging"
    assert settings.log_level == "DEBUG", "log level should be normalised to upper case"
    assert settings.database_url_app == "postgresql+asyncpg://u:p@h/db"
    assert settings.is_local is False


def test_defaults_apply_when_optional_vars_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAYAS_ENV", raising=False)
    monkeypatch.delenv("PRAYAS_LOG_LEVEL", raising=False)
    monkeypatch.setenv("PRAYAS_DATABASE_URL_APP", "postgresql+asyncpg://u:p@h/db")

    settings = Settings.from_env()

    assert settings.env == "local"
    assert settings.log_level == "INFO"
    assert settings.is_local is True


def test_missing_database_url_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail at startup, never at fire time (§17.4)."""
    monkeypatch.delenv("PRAYAS_DATABASE_URL_APP", raising=False)

    with pytest.raises(ConfigError, match="PRAYAS_DATABASE_URL_APP"):
        Settings.from_env()


def test_empty_string_is_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported-but-blank variable is a misconfiguration, not a value."""
    monkeypatch.setenv("PRAYAS_DATABASE_URL_APP", "")

    with pytest.raises(ConfigError):
        Settings.from_env()


def test_settings_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config that can change under a running decision breaks replay (§32)."""
    monkeypatch.setenv("PRAYAS_DATABASE_URL_APP", "postgresql+asyncpg://u:p@h/db")
    settings = Settings.from_env()

    with pytest.raises(AttributeError):
        settings.env = "tampered"  # type: ignore[misc]


def test_owner_url_is_separate_from_app_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-004: two roles, two URLs. The app must never hold DDL rights."""
    monkeypatch.setenv("PRAYAS_DATABASE_URL_OWNER", "postgresql+asyncpg://owner:p@h/db")
    assert owner_database_url() == "postgresql+asyncpg://owner:p@h/db"

    monkeypatch.delenv("PRAYAS_DATABASE_URL_OWNER", raising=False)
    with pytest.raises(ConfigError, match="PRAYAS_DATABASE_URL_OWNER"):
        owner_database_url()
