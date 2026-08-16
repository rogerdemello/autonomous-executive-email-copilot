from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func

from .db import Episode, TeamSettings, UserPreference, get_session


class EpisodeRepository:
    def __init__(self) -> None:
        self.db = get_session()

    def save_episode(self, episode_data: dict[str, Any]) -> Episode:
        with get_session() as session:
            existing = (
                session.query(Episode)
                .filter(Episode.episode_id == episode_data["episode_id"])
                .first()
            )
            if existing:
                existing.task_id = episode_data.get("task_id", existing.task_id)
                existing.seed = episode_data.get("seed", existing.seed)
                existing.persona = episode_data.get("persona", existing.persona)
                existing.steps = episode_data.get("steps", existing.steps)
                existing.score = episode_data.get("score", existing.score)
                existing.total_reward = episode_data.get("total_reward", existing.total_reward)
                existing.decisions_json = json.dumps(episode_data.get("decisions", []))
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(existing)
                return existing
            episode = Episode(
                episode_id=episode_data["episode_id"],
                task_id=episode_data.get("task_id", ""),
                seed=episode_data.get("seed", 0),
                persona=episode_data.get("persona", "balanced"),
                steps=episode_data.get("steps", 0),
                score=episode_data.get("score", 0.0),
                total_reward=episode_data.get("total_reward", 0.0),
                decisions_json=json.dumps(episode_data.get("decisions", [])),
            )
            session.add(episode)
            session.commit()
            session.refresh(episode)
            return episode

    def get_episode(self, episode_id: str | None = None, id: int | None = None) -> Episode | None:
        with get_session() as session:
            if episode_id:
                return session.query(Episode).filter(Episode.episode_id == episode_id).first()
            if id:
                return session.query(Episode).filter(Episode.id == id).first()
            return None

    def list_episodes(
        self,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        with get_session() as session:
            query = session.query(Episode)
            if filters:
                if "task_id" in filters and filters["task_id"]:
                    query = query.filter(Episode.task_id == filters["task_id"])
                if "persona" in filters and filters["persona"]:
                    query = query.filter(Episode.persona == filters["persona"])
                if "min_score" in filters and filters["min_score"] is not None:
                    query = query.filter(Episode.score >= filters["min_score"])
                if "max_score" in filters and filters["max_score"] is not None:
                    query = query.filter(Episode.score <= filters["max_score"])
                if "start_date" in filters and filters["start_date"]:
                    query = query.filter(Episode.created_at >= filters["start_date"])
                if "end_date" in filters and filters["end_date"]:
                    query = query.filter(Episode.created_at <= filters["end_date"])
            total = query.count()
            offset = (page - 1) * limit
            episodes = query.order_by(desc(Episode.created_at)).offset(offset).limit(limit).all()
            return {
                "episodes": [e.to_dict() for e in episodes],
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit,
            }

    def get_episodes_by_task(self, task_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        return self.list_episodes(filters={"task_id": task_id}, page=page, limit=limit)

    def get_episodes_by_persona(
        self, persona: str, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        return self.list_episodes(filters={"persona": persona}, page=page, limit=limit)

    def get_episodes_by_score_range(
        self, min_score: float, max_score: float, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        return self.list_episodes(
            filters={"min_score": min_score, "max_score": max_score}, page=page, limit=limit
        )

    def get_episodes_by_date_range(
        self, start_date: str, end_date: str, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        return self.list_episodes(
            filters={"start_date": start_date, "end_date": end_date}, page=page, limit=limit
        )

    def get_stats(self) -> dict[str, Any]:
        with get_session() as session:
            total_episodes = session.query(Episode).count()
            avg_score = session.query(func.avg(Episode.score)).scalar() or 0.0
            avg_steps = session.query(func.avg(Episode.steps)).scalar() or 0.0
            avg_reward = session.query(func.avg(Episode.total_reward)).scalar() or 0.0
            task_counts = (
                session.query(Episode.task_id, func.count(Episode.id))
                .group_by(Episode.task_id)
                .all()
            )
            persona_counts = (
                session.query(Episode.persona, func.count(Episode.id))
                .group_by(Episode.persona)
                .all()
            )
            score_distribution = {
                "min": session.query(func.min(Episode.score)).scalar() or 0.0,
                "max": session.query(func.max(Episode.score)).scalar() or 0.0,
                "avg": float(avg_score),
            }
            return {
                "total_episodes": total_episodes,
                "avg_score": float(avg_score),
                "avg_steps": float(avg_steps),
                "avg_reward": float(avg_reward),
                "by_task": dict(task_counts),
                "by_persona": dict(persona_counts),
                "score_distribution": score_distribution,
            }


class UserPreferenceRepository:
    """Repository for managing user preferences."""

    def __init__(self) -> None:
        self.db = get_session()

    def save_user_preference(self, preference_data: dict[str, Any]) -> UserPreference:
        """Save or update user preference."""

        with get_session() as session:
            existing = (
                session.query(UserPreference)
                .filter(UserPreference.user_id == preference_data["user_id"])
                .first()
            )
            if existing:
                existing.default_persona = preference_data.get(
                    "default_persona", existing.default_persona
                )
                existing.notification_email = preference_data.get(
                    "notification_email", existing.notification_email
                )
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(existing)
                return existing
            preference = UserPreference(
                user_id=preference_data["user_id"],
                default_persona=preference_data.get("default_persona", "balanced"),
                notification_email=preference_data.get("notification_email"),
            )
            session.add(preference)
            session.commit()
            session.refresh(preference)
            return preference

    def get_user_preference(self, user_id: str) -> UserPreference | None:
        """Get user preference by user_id."""
        with get_session() as session:
            return session.query(UserPreference).filter(UserPreference.user_id == user_id).first()

    def list_user_preferences(self, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """List all user preferences with pagination."""
        with get_session() as session:
            total = session.query(UserPreference).count()
            offset = (page - 1) * limit
            preferences = (
                session.query(UserPreference)
                .order_by(desc(UserPreference.created_at))
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {
                "preferences": [p.to_dict() for p in preferences],
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit,
            }


class TeamSettingsRepository:
    """Repository for managing team settings."""

    def __init__(self) -> None:
        self.db = get_session()

    def save_team_settings(self, settings_data: dict[str, Any]) -> TeamSettings:
        """Save or update team settings."""
        import json

        with get_session() as session:
            existing = (
                session.query(TeamSettings)
                .filter(TeamSettings.team_id == settings_data["team_id"])
                .first()
            )
            if existing:
                existing.approval_rules = json.dumps(settings_data.get("approval_rules", []))
                existing.escalation_targets = json.dumps(
                    settings_data.get("escalation_targets", [])
                )
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(existing)
                return existing
            settings = TeamSettings(
                team_id=settings_data["team_id"],
                approval_rules=json.dumps(settings_data.get("approval_rules", [])),
                escalation_targets=json.dumps(settings_data.get("escalation_targets", [])),
            )
            session.add(settings)
            session.commit()
            session.refresh(settings)
            return settings

    def get_team_settings(self, team_id: str) -> TeamSettings | None:
        """Get team settings by team_id."""
        with get_session() as session:
            return session.query(TeamSettings).filter(TeamSettings.team_id == team_id).first()

    def list_team_settings(self, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """List all team settings with pagination."""
        with get_session() as session:
            total = session.query(TeamSettings).count()
            offset = (page - 1) * limit
            settings = (
                session.query(TeamSettings)
                .order_by(desc(TeamSettings.created_at))
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {
                "settings": [s.to_dict() for s in settings],
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": (total + limit - 1) // limit,
            }
