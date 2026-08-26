"""
varco_core.config
==================
Base settings class for all varco configuration objects.

All backend-specific configuration classes (``EventBusSettings``,
``CacheSettings``, ``RedisEventBusSettings``, etc.) extend ``VarcoSettings``
to gain uniform env-var loading via Pydantic Settings.

Environment variable conventions
---------------------------------
Each settings subclass defines its own ``env_prefix`` via ``SettingsConfigDict``.
Field names are uppercased and concatenated with the prefix::

    class RedisEventBusSettings(EventBusSettings):
        model_config = SettingsConfigDict(env_prefix="VARCO_REDIS_", frozen=True)
        url: str = "redis://localhost:6379/0"

    # Reads from env var: VARCO_REDIS_URL

Construction patterns::

    # From env vars (production — reads VARCO_REDIS_URL etc.)
    config = RedisEventBusSettings.from_env()

    # From keyword args (tests, explicit configuration)
    config = RedisEventBusSettings(url="redis://test-host:6379/0")

    # From a dictionary (DI wiring, dynamic config)
    config = RedisEventBusSettings.from_dict({"url": "redis://test-host:6379/0"})

DESIGN: Pydantic BaseSettings over plain dataclass
    ✅ Reads from env vars automatically — no manual ``os.environ`` wiring.
    ✅ Validates types at load time — ``VARCO_REDIS_MAX_CONNECTIONS=abc`` fails fast.
    ✅ ``from_env()`` / ``from_dict()`` are self-documenting constructor aliases.
    ✅ ``frozen=True`` (via subclass config) makes settings immutable after construction.
    ❌ Adds ``pydantic-settings`` as a hard dep on ``varco_core``.
       Acceptable — pydantic is already required; pydantic-settings is a small
       official companion package.
    ❌ Dicts and complex types (e.g. ``dict[str, Any]``) cannot be read from
       a single env var without JSON parsing.  Set these via keyword args or
       ``from_dict()`` instead of env vars.

Thread safety:  ✅ Immutable after construction when subclass uses ``frozen=True``.
Async safety:   ✅ No mutable state.

📚 Docs
- 🔍 https://docs.pydantic.dev/latest/concepts/pydantic_settings/
  Pydantic Settings — BaseSettings, env_prefix, SettingsConfigDict
- 🐍 https://docs.python.org/3/library/typing.html#typing.Self
  Self — return type for classmethods that return an instance of the subclass
"""

from __future__ import annotations

from typing import Any, Self

from pydantic_settings import BaseSettings, SettingsConfigDict

# ── VarcoSettings ─────────────────────────────────────────────────────────────


class VarcoSettings(BaseSettings):
    """
    Base class for all varco backend configuration objects.

    Subclasses must override ``model_config`` to set their own ``env_prefix``::

        class MySettings(VarcoSettings):
            model_config = SettingsConfigDict(env_prefix="MY_SVC_", frozen=True)
            db_url: str = "postgresql+asyncpg://localhost/dev"

    All fields should have sensible defaults so the class can be constructed
    without any env vars for local development.

    Thread safety:  ✅ Immutable after construction (subclasses use frozen=True).
    Async safety:   ✅ No mutable state.

    Edge cases:
        - Subclasses that forget to set ``env_prefix`` will read their fields
          from the global environment namespace (no prefix).  This is unlikely
          to conflict but is worth documenting.
        - ``dict[str, Any]`` fields cannot be populated via env vars without
          custom ``model_validator`` logic.  Use ``from_dict()`` for complex fields.
    """

    # Base config — subclasses override this with their own env_prefix.
    # frozen=False here; individual subclasses should set frozen=True.
    # DESIGN: not frozen at the base level because some tests need mutable
    # settings objects.  Production subclasses enforce immutability.
    model_config = SettingsConfigDict()

    @classmethod
    def from_env(cls) -> Self:
        """
        Construct this settings object by reading values from environment variables.

        Equivalent to ``cls()`` — Pydantic BaseSettings reads env vars
        automatically on construction.  This is a named alias that makes the
        intent explicit at the call site.

        Returns:
            A fully populated settings instance.

        Raises:
            ValidationError: If a required field is missing from the environment
                             or a value fails type validation.

        Example::

            # Reads VARCO_REDIS_URL, VARCO_REDIS_CHANNEL_PREFIX, etc.
            config = RedisEventBusSettings.from_env()
        """
        # BaseSettings() triggers env var reading automatically.
        # This method is purely a named alias for clarity.
        return cls()

    @classmethod
    def env_prefix(cls) -> str:
        """
        Return the env-var prefix configured for this settings class.

        Reads from ``model_config["env_prefix"]``.  Subclasses that set
        ``SettingsConfigDict(env_prefix="VARCO_REDIS_", ...)`` return
        ``"VARCO_REDIS_"``; the base ``VarcoSettings`` returns ``""``.

        Useful when callers need to introspect the prefix at runtime — e.g.
        to build a ``SSLConfig.from_env(prefix=MySettings.env_prefix())``.

        Returns:
            The configured env-var prefix string, or ``""`` if not set.

        Example::

            class RedisSettings(VarcoSettings):
                model_config = SettingsConfigDict(env_prefix="VARCO_REDIS_")

            assert RedisSettings.env_prefix() == "VARCO_REDIS_"
        """
        return cls.model_config.get("env_prefix", "")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        Construct this settings object from a plain dictionary.

        Useful in unit tests where you want to provide values without
        setting real environment variables.

        Args:
            data: Dictionary of ``{field_name: value}`` mappings.
                  Field names must match the class's field names exactly.

        Returns:
            A populated settings instance with values from ``data``.

        Raises:
            ValidationError: If ``data`` contains invalid field names or values.

        Example::

            config = RedisEventBusSettings.from_dict({
                "url": "redis://test:6379",
                "channel_prefix": "test:",
            })
        """
        # model_validate bypasses env var reading — values come only from ``data``.
        return cls.model_validate(data)


__all__ = [
    "VarcoSettings",
]
