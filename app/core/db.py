from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Float, Integer, String, Text, create_engine, func, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings
from .paths import DATA_ROOT

# Database path - store in project root for persistence
DB_PATH = DATA_ROOT / "episodes.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Default zero-config database: a local SQLite file requiring no extra deps.
DEFAULT_SQLITE_URL = f"sqlite:///{DB_PATH}"

# Pool tuning for server-backed databases (e.g. Postgres). These are ignored for
# SQLite, which uses a file/in-memory connection rather than a network pool.
DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_RECYCLE_SECONDS = 1800


def resolve_database_url() -> str:
    """Resolve the active database URL.

    Honors ``DATABASE_URL`` (via :class:`app.core.config.Settings`) when set, falling
    back to the zero-config local SQLite database otherwise.

    Postgres URLs are normalized to the psycopg3 driver we actually ship:
    managed platforms hand out ``postgres://`` (which SQLAlchemy 2 rejects
    outright — Render's ``connectionString`` is the motivating case) and the
    plain ``postgresql://`` scheme selects psycopg2 (not installed). Explicit
    ``postgresql+<driver>://`` URLs are respected untouched.
    """
    configured = get_settings().database_url
    if not (configured and configured.strip()):
        return DEFAULT_SQLITE_URL
    url = configured.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def build_engine_kwargs(database_url: str) -> dict:
    """Compute ``create_engine`` keyword args appropriate for ``database_url``.

    SQLite keeps its current behavior (``check_same_thread=False`` so the
    file-backed connection can be shared across threads, as FastAPI does). Any
    non-SQLite backend (e.g. Postgres) gets connection-pool tuning:
    ``pool_pre_ping`` (drop dead connections), ``pool_recycle`` (recycle stale
    ones), plus ``pool_size``/``max_overflow``. This is a pure function so it can
    be unit-tested without a live database.
    """
    backend = make_url(database_url).get_backend_name()
    if backend == "sqlite":
        return {"echo": False, "connect_args": {"check_same_thread": False}}
    return {
        "echo": False,
        "pool_size": DEFAULT_POOL_SIZE,
        "max_overflow": DEFAULT_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": DEFAULT_POOL_RECYCLE_SECONDS,
    }


# SQLAlchemy setup. ``DATABASE_URL`` is resolved once at import time; setting the
# ``DATABASE_URL`` env var before import switches the app to Postgres while
# SQLite remains the default. The Postgres driver (``psycopg``) is imported by
# SQLAlchemy only when a Postgres URL is actually used, so it stays an optional
# dependency.
DATABASE_URL = resolve_database_url()
engine = create_engine(DATABASE_URL, **build_engine_kwargs(DATABASE_URL))
# expire_on_commit=False keeps attributes readable on objects returned from a
# closed session (get_session commits then closes), so repository callers can
# safely read/serialize ORM instances after the context manager exits.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
# Typed as Any so mypy accepts ``class Model(Base)`` subclassing and treats mapped
# ``Column`` attributes as dynamic (the SQLAlchemy 1.x-style declarative base is not
# a static type). This is the pragmatic alternative to the sqlalchemy mypy plugin.
Base: Any = declarative_base()


class Episode(Base):
    """Episode database model for SQLite storage."""

    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(255), unique=True, nullable=False, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    seed = Column(Integer, nullable=False)
    persona = Column(String(50), nullable=False, index=True)
    steps = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    total_reward = Column(Float, nullable=False)
    decisions_json = Column(Text, nullable=True)  # JSON serialized decisions
    created_at = Column(
        String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at = Column(
        String(50),
        nullable=False,
        default=lambda: datetime.now(timezone.utc).isoformat(),
        onupdate=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        import json

        try:
            decisions = json.loads(self.decisions_json) if self.decisions_json else []  # type: ignore[arg-type]
        except (TypeError, ValueError):
            decisions = []

        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "seed": self.seed,
            "persona": self.persona,
            "steps": self.steps,
            "score": self.score,
            "total_reward": self.total_reward,
            "decisions": decisions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DecisionRecord(Base):
    """Individual decision record for detailed tracking."""

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(255), nullable=False, index=True)
    step = Column(Integer, nullable=False)
    action_type = Column(String(50), nullable=True)
    email_id = Column(String(100), nullable=True)
    label = Column(String(50), nullable=True)
    content = Column(Text, nullable=True)
    reward = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(
        String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat()
    )


class UserPreference(Base):
    """User preference settings for personalization."""

    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), unique=True, nullable=False, index=True)
    default_persona = Column(String(50), nullable=False, default="balanced")
    notification_email = Column(String(255), nullable=True)
    created_at = Column(
        String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at = Column(
        String(50),
        nullable=False,
        default=lambda: datetime.now(timezone.utc).isoformat(),
        onupdate=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "default_persona": self.default_persona,
            "notification_email": self.notification_email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TeamSettings(Base):
    """Team settings for approval workflows and escalation targets."""

    __tablename__ = "team_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String(255), unique=True, nullable=False, index=True)
    approval_rules = Column(Text, nullable=True)  # JSON serialized approval rules
    escalation_targets = Column(Text, nullable=True)  # JSON serialized escalation targets
    created_at = Column(
        String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at = Column(
        String(50),
        nullable=False,
        default=lambda: datetime.now(timezone.utc).isoformat(),
        onupdate=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        import json

        approval_rules_str = self.approval_rules
        escalation_targets_str = self.escalation_targets

        return {
            "id": self.id,
            "team_id": self.team_id,
            "approval_rules": json.loads(approval_rules_str) if approval_rules_str else [],  # type: ignore[arg-type]
            "escalation_targets": json.loads(escalation_targets_str)  # type: ignore[arg-type]
            if escalation_targets_str
            else [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_SCHEMA_VERSION = 6


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version = Column(Integer, primary_key=True)
    applied_at = Column(
        String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat()
    )


def _register_saas_models() -> None:
    """Import the SaaS ORM models so their tables join ``Base.metadata``.

    Done lazily (not at module import) because ``app.saas.models_db`` imports
    ``Base`` from this module — a top-level import would be circular. Import is
    cheap and idempotent, so calling it before every ``create_all`` is safe.
    """
    try:
        import app.saas.models_db  # noqa: F401
    except Exception:  # pragma: no cover - defensive; SaaS layer is additive
        pass


def init_db() -> None:
    """Initialize database tables (core + SaaS layer)."""
    _register_saas_models()
    Base.metadata.create_all(bind=engine)


def get_db() -> sessionmaker:
    """Get database session factory."""
    return SessionLocal


@contextmanager
def get_session():
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> None:
    """Additively add a column, skipping it when it is already there.

    ``create_all`` creates missing *tables* but never alters existing ones, so a
    new column on a table that already exists in a deployed database is
    invisible to it. ``ADD COLUMN`` is the one schema change both SQLite and
    PostgreSQL accept cheaply and without a table rewrite; the inspector check
    stands in for ``IF NOT EXISTS``, which SQLite does not support here.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return  # create_all will build it complete
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _run_migration(version: int) -> None:
    """Execute a single migration step for the given version number."""
    if version == 1:
        return
    if version == 2:
        # Carry the message's real provider timestamp and display name through
        # to the UI. Without these every message in the inbox renders with the
        # sync time, so a mailbox looks like it arrived in one instant.
        _add_column_if_missing("saas_processed_messages", "received_at", "VARCHAR(50)")
        _add_column_if_missing("saas_processed_messages", "sender_name", "VARCHAR(255)")
        return
    if version == 3:
        # Record where a draft's *prose* came from and why the action was chosen.
        # Without draft_source the UI cannot distinguish model-written text from
        # the policy's generic sentence; without a persisted rationale the
        # approvals queue has to show a bare draft with no reasoning attached.
        _add_column_if_missing("saas_proposed_actions", "draft_source", "VARCHAR(16)")
        _add_column_if_missing("saas_proposed_actions", "draft_confidence", "FLOAT")
        _add_column_if_missing("saas_proposed_actions", "rationale", "TEXT")
        return
    if version == 4:
        # A reviewer may now amend a draft before approving; the proposed text
        # is kept so (original, edited) pairs can teach the drafter.
        _add_column_if_missing("saas_proposed_actions", "original_content", "TEXT")
        return
    if version == 5:
        # Draft-then-verify: each held draft is checked against its source
        # message before it queues; the verdict rides on the action.
        _add_column_if_missing("saas_proposed_actions", "verification_status", "VARCHAR(16)")
        _add_column_if_missing("saas_proposed_actions", "verification_notes", "TEXT")
        return
    if version == 6:
        # The whole message body. Only a 500-character preview was stored, so
        # the reader pane physically could not show a full email — you could
        # not read your mail in this mail product.
        _add_column_if_missing("saas_processed_messages", "body", "TEXT")
        return


def migrate_db() -> None:
    """Run database migrations with schema version tracking."""
    init_db()

    inspector = inspect(engine)
    if "schema_version" not in inspector.get_table_names():
        SchemaVersion.__table__.create(bind=engine)
        with get_session() as session:
            session.add(SchemaVersion(version=_SCHEMA_VERSION))
        return

    with get_session() as session:
        current = session.query(func.max(SchemaVersion.version)).scalar() or 0
        for v in range(current + 1, _SCHEMA_VERSION + 1):
            _run_migration(v)
            session.add(SchemaVersion(version=v))


def schema_is_current() -> bool:
    """True when the database is reachable and migrated to ``_SCHEMA_VERSION``.

    Used by the readiness probe. This asks the database rather than trusting a
    process-local flag, so it stays correct however the app was started — a
    partially-applied migration (schema_version behind the code) reports not
    ready instead of failing later, mid-request, on a missing column.
    """
    try:
        inspector = inspect(engine)
        if "schema_version" not in inspector.get_table_names():
            return False
        with get_session() as session:
            current = session.query(func.max(SchemaVersion.version)).scalar() or 0
        return int(current) >= _SCHEMA_VERSION
    except Exception:  # noqa: BLE001 - unreachable DB is "not ready", not an error
        return False
