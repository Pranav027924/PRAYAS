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


@dataclass(frozen=True, slots=True)
class RazorpayCredentials:
    """Razorpay API credentials and the webhook signing secret.

    Outbound only. `key_id`/`key_secret` authenticate calls *we* make.

    Inbound webhook signatures are a separate mechanism entirely and are not
    configured here: `webhook_secrets` holds a per-tenant `secret_ref`, and the
    material lives in `PRAYAS_WEBHOOK_SECRET_<REF>` so that several secrets can
    be valid at once during a rotation (ADR-014). Adding a single
    `RAZORPAY_WEBHOOK_SECRET` here would look equivalent and would quietly
    defeat rotation.

    Absent credentials are not an error. §44's `OBSERVE` and `SHADOW` stages
    fire nothing and need no provider, so a Tier 0 deployment runs without
    these; `configured` is how a caller asks rather than catching an exception.
    """

    key_id: str | None
    key_secret: str | None
    base_url: str = "https://api.razorpay.com/v1"

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def is_test_mode(self) -> bool:
        """Test keys carry an `rzp_test_` prefix; live keys `rzp_live_`.

        Surfaced so a deployment can *assert* which one it is holding. A live
        key reaching a staging environment is the mistake worth making
        impossible to overlook.
        """
        return bool(self.key_id and self.key_id.startswith("rzp_test_"))

    @classmethod
    def from_env(cls) -> RazorpayCredentials:
        return cls(
            key_id=os.environ.get("RAZORPAY_KEY_ID") or None,
            key_secret=os.environ.get("RAZORPAY_KEY_SECRET") or None,
            base_url=os.environ.get("RAZORPAY_BASE_URL", "https://api.razorpay.com/v1"),
        )
