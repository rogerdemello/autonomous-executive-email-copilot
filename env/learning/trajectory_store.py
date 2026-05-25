"""Trajectory store for high-scoring episodes (score > 0.7)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Database path
TRAJECTORY_DB_PATH = Path(__file__).parent.parent / "data" / "trajectories.db"
TRAJECTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

TRAJECTORY_DATABASE_URL = f"sqlite:///{TRAJECTORY_DB_PATH}"
trajectory_engine = create_engine(TRAJECTORY_DATABASE_URL, echo=False)
TrajectorySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=trajectory_engine)
TrajectoryBase = declarative_base()


class SuccessfulTrajectory(TrajectoryBase):
    """High-scoring trajectory for few-shot learning."""

    __tablename__ = "successful_trajectories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(255), unique=True, nullable=False, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    seed = Column(Integer, nullable=False)
    persona = Column(String(50), nullable=False, index=True)
    score = Column(Float, nullable=False)
    steps = Column(Integer, nullable=False)
    # Serialized trajectory: observation -> action -> reward -> next observation
    trajectory_json = Column(Text, nullable=False)
    # Key patterns extracted from trajectory
    patterns_json = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "seed": self.seed,
            "persona": self.persona,
            "score": self.score,
            "steps": self.steps,
            "trajectory": json.loads(self.trajectory_json) if self.trajectory_json else [],
            "patterns": json.loads(self.patterns_json) if self.patterns_json else [],
            "created_at": self.created_at,
        }


class UserFeedback(TrajectoryBase):
    """User feedback on responses (good/bad)."""

    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(255), nullable=True, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    seed = Column(Integer, nullable=False)
    persona = Column(String(50), nullable=False)
    step_index = Column(Integer, nullable=True)
    action_type = Column(String(50), nullable=True)
    email_id = Column(String(100), nullable=True)
    feedback = Column(String(10), nullable=False)  # "good" or "bad"
    comment = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "seed": self.seed,
            "persona": self.persona,
            "step_index": self.step_index,
            "action_type": self.action_type,
            "email_id": self.email_id,
            "feedback": self.feedback,
            "comment": self.comment,
            "created_at": self.created_at,
        }


def _init_trajectory_db() -> None:
    """Initialize trajectory database tables."""
    TrajectoryBase.metadata.create_all(bind=trajectory_engine)


@contextmanager
def _get_trajectory_session():
    """Context manager for trajectory database sessions."""
    session = TrajectorySessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Initialize database
_init_trajectory_db()


class TrajectoryStore:
    """Store and retrieve high-scoring trajectories."""

    SCORE_THRESHOLD = 0.7

    def __init__(self) -> None:
        pass

    def save_trajectory(
        self,
        episode_id: str,
        task_id: str,
        seed: int,
        persona: str,
        score: float,
        steps: int,
        trajectory_data: list[dict[str, Any]],
        patterns: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Save trajectory if score exceeds threshold.

        Args:
            episode_id: Unique episode identifier
            task_id: Task identifier
            seed: Random seed
            persona: Persona type
            score: Episode score (0-1)
            steps: Number of steps taken
            trajectory_data: List of {observation, action, reward} dicts
            patterns: Optional extracted patterns for quick matching

        Returns:
            Dict representation if saved, None if score below threshold
        """
        if score < self.SCORE_THRESHOLD:
            return None

        with _get_trajectory_session() as session:
            existing = session.query(SuccessfulTrajectory).filter(
                SuccessfulTrajectory.episode_id == episode_id
            ).first()

            trajectory_json = json.dumps(trajectory_data)
            patterns_json = json.dumps(patterns or [])

            if existing:
                existing.score = score
                existing.steps = steps
                existing.trajectory_json = trajectory_json
                existing.patterns_json = patterns_json
                existing.created_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(existing)
                return existing.to_dict()

            trajectory = SuccessfulTrajectory(
                episode_id=episode_id,
                task_id=task_id,
                seed=seed,
                persona=persona,
                score=score,
                steps=steps,
                trajectory_json=trajectory_json,
                patterns_json=patterns_json,
            )
            session.add(trajectory)
            session.commit()
            session.refresh(trajectory)
            return trajectory.to_dict()

    def get_trajectories(
        self,
        task_id: str | None = None,
        persona: str | None = None,
        min_score: float | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Retrieve high-scoring trajectories matching filters."""
        with _get_trajectory_session() as session:
            query = session.query(SuccessfulTrajectory)

            if task_id:
                query = query.filter(SuccessfulTrajectory.task_id == task_id)
            if persona:
                query = query.filter(SuccessfulTrajectory.persona == persona)
            if min_score is not None:
                query = query.filter(SuccessfulTrajectory.score >= min_score)

            trajectories = query.order_by(SuccessfulTrajectory.score.desc()).limit(limit).all()
            return [t.to_dict() for t in trajectories]

    def get_similar_trajectories(
        self,
        task_id: str,
        persona: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Get similar trajectories for few-shot examples."""
        return self.get_trajectories(task_id=task_id, persona=persona, limit=limit)

    def get_all_count(self) -> int:
        """Get total count of stored trajectories."""
        with _get_trajectory_session() as session:
            return session.query(SuccessfulTrajectory).count()

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about stored trajectories."""
        with _get_trajectory_session() as session:
            from sqlalchemy import func

            total = session.query(SuccessfulTrajectory).count()
            avg_score = session.query(func.avg(SuccessfulTrajectory.score)).scalar() or 0.0
            avg_steps = session.query(func.avg(SuccessfulTrajectory.steps)).scalar() or 0.0

            task_counts = (
                session.query(SuccessfulTrajectory.task_id, func.count(SuccessfulTrajectory.id))
                .group_by(SuccessfulTrajectory.task_id)
                .all()
            )

            persona_counts = (
                session.query(SuccessfulTrajectory.persona, func.count(SuccessfulTrajectory.id))
                .group_by(SuccessfulTrajectory.persona)
                .all()
            )

            return {
                "total_trajectories": total,
                "avg_score": float(avg_score),
                "avg_steps": float(avg_steps),
                "by_task": dict(task_counts),
                "by_persona": dict(persona_counts),
                "score_threshold": self.SCORE_THRESHOLD,
            }


class FeedbackStore:
    """Store and retrieve user feedback on responses."""

    def add_feedback(
        self,
        episode_id: str | None,
        task_id: str,
        seed: int,
        persona: str,
        step_index: int | None,
        action_type: str | None,
        email_id: str | None,
        feedback: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Add user feedback (good/bad) for a response."""
        with _get_trajectory_session() as session:
            feedback_record = UserFeedback(
                episode_id=episode_id,
                task_id=task_id,
                seed=seed,
                persona=persona,
                step_index=step_index,
                action_type=action_type,
                email_id=email_id,
                feedback=feedback,
                comment=comment,
            )
            session.add(feedback_record)
            session.commit()
            session.refresh(feedback_record)
            result = feedback_record.to_dict()
            return result

    def get_feedback(
        self,
        task_id: str | None = None,
        feedback: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get feedback records matching filters."""
        with _get_trajectory_session() as session:
            query = session.query(UserFeedback)

            if task_id:
                query = query.filter(UserFeedback.task_id == task_id)
            if feedback:
                query = query.filter(UserFeedback.feedback == feedback)

            records = query.order_by(UserFeedback.created_at.desc()).limit(limit).all()
            return [r.to_dict() for r in records]

    def get_good_actions(
        self,
        task_id: str | None = None,
        persona: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get actions marked as 'good' by users."""
        with _get_trajectory_session() as session:
            query = session.query(UserFeedback).filter(UserFeedback.feedback == "good")

            if task_id:
                query = query.filter(UserFeedback.task_id == task_id)
            if persona:
                query = query.filter(UserFeedback.persona == persona)

            records = query.all()
            return [r.to_dict() for r in records]

    def get_bad_actions(
        self,
        task_id: str | None = None,
        persona: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get actions marked as 'bad' by users."""
        with _get_trajectory_session() as session:
            query = session.query(UserFeedback).filter(UserFeedback.feedback == "bad")

            if task_id:
                query = query.filter(UserFeedback.task_id == task_id)
            if persona:
                query = query.filter(UserFeedback.persona == persona)

            records = query.all()
            return [r.to_dict() for r in records]

    def get_stats(self) -> dict[str, Any]:
        """Get feedback statistics."""
        with _get_trajectory_session() as session:
            from sqlalchemy import func

            total = session.query(UserFeedback).count()
            good_count = session.query(func.count(UserFeedback.id)).filter(
                UserFeedback.feedback == "good"
            ).scalar() or 0
            bad_count = session.query(func.count(UserFeedback.id)).filter(
                UserFeedback.feedback == "bad"
            ).scalar() or 0

            return {
                "total_feedback": total,
                "good_count": good_count,
                "bad_count": bad_count,
                "good_ratio": good_count / total if total > 0 else 0.0,
            }


# Default instances
trajectory_store = TrajectoryStore()
feedback_store = FeedbackStore()