"""
varco_sa.migration
===================
``AlembicMigrator`` — the SQLAlchemy/Postgres migration backend implementing
``varco_core.migration.AbstractMigrator``, plus the packaged ``varco``
Alembic branch (framework-owned tables) and its supporting lock/env/ops
helpers.

Requires the ``migrations`` extra::

    pip install "varco-sa[migrations]"

Importing this package without ``alembic`` installed raises
``varco_core.migration.MigrationBackendUnavailable`` naming that exact
install line.
"""

from __future__ import annotations

try:
    import alembic  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    from varco_core.migration.errors import MigrationBackendUnavailable

    raise MigrationBackendUnavailable(
        "varco_sa.migration requires the 'migrations' extra. "
        'Install it with: pip install "varco-sa[migrations]"'
    ) from exc

from varco_sa.migration.env_template import configure_kwargs, include_object
from varco_sa.migration.lock import migration_lock
from varco_sa.migration.migrator import AlembicMigrator
from varco_sa.migration.ops import rls_downgrade, rls_upgrade

__all__ = [
    "AlembicMigrator",
    "configure_kwargs",
    "include_object",
    "migration_lock",
    "rls_downgrade",
    "rls_upgrade",
]
