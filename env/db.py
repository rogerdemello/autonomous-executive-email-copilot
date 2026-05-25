from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Database path - store in project root for persistence
DB_PATH = Path(__file__).parent.parent / "data" / "episodes.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# SQLAlchemy setup
DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(DATABASE_URL, echo=False)
# expire_on_commit=False keeps attributes readable on objects returned from a
# closed session (get_session commits then closes), so repository callers can
# safely read/serialize ORM instances after the context manager exits.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
Base = declarative_base()


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
            decisions = json.loads(self.decisions_json) if self.decisions_json else []
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
            "approval_rules": json.loads(approval_rules_str) if approval_rules_str else [],
            "escalation_targets": json.loads(escalation_targets_str)
            if escalation_targets_str
            else [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def init_db() -> None:
    """Initialize database tables."""
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


def migrate_db() -> None:
    """Run database migrations."""
    init_db()
