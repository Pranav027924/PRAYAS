"""Runtime configuration.

Secrets arrive by environment injection only, never from files
(Execution Playbook, Phase 0). Nothing here reads a `.env` at runtime; that
file is a developer convenience loaded by the shell or by Compose, and it is
gitignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

REQUIRED_APP_VARS: Final = ("PRAYAS_DATABASE_URL_APP",)
REQUIRED_MIGRATION_VARS: Final = ("PRAYAS_DATABASE_URL_OWNER",)


class ConfigError(RuntimeError):
    """Configuration is missing or malformed. Fail at startup, never at fire time."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"Required environment variable {name} is not set. "
            f"Secrets are injected from the environment, never read from files."
        )
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    Frozen because configuration that can change under a running decision is a
    source of non-reproducible behaviour, and §32 requires decisions be replayable.
    """

    env: str
    log_level: str
    database_url_app: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            env=os.environ.get("PRAYAS_ENV", "local"),
            log_level=os.environ.get("PRAYAS_LOG_LEVEL", "INFO").upper(),
            database_url_app=_require("PRAYAS_DATABASE_URL_APP"),
        )

    @property
    def is_local(self) -> bool:
        return self.env == "local"


def owner_database_url() -> str:
    """DDL connection string. Used by Alembic only.

    The application must never call this. Per ADR-004 the owner role holds DDL
    rights and bypasses RLS by ownership; the app role owns nothing.
    """
    return _require("PRAYAS_DATABASE_URL_OWNER")
