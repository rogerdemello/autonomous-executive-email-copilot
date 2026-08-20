"""Tests for repository layer (app/core/repositories.py).

Tests EpisodeRepository, UserPreferenceRepository, and TeamSettingsRepository
against the live SQLite database (data/episodes.db).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core import db
from app.core.repositories import (
    EpisodeRepository,
    TeamSettingsRepository,
    UserPreferenceRepository,
)


def _uid(prefix: str = "t") -> str:
    """Return a unique identifier for test isolation."""
    return f"{prefix}-{datetime.now(timezone.utc).timestamp()}-{id({})}"


# ---------------------------------------------------------------------------
# EpisodeRepository
# ---------------------------------------------------------------------------


class TestEpisodeRepository:
    """Tests for EpisodeRepository."""

    def setup_method(self) -> None:
        db.init_db()
        self.repo = EpisodeRepository()

    def test_save_episode_creates_new(self) -> None:
        ep_id = _uid("ep-create")
        ep = self.repo.save_episode(
            {
                "episode_id": ep_id,
                "task_id": "task-1",
                "seed": 42,
                "persona": "analytical",
                "steps": 5,
                "score": 0.85,
                "total_reward": 2.0,
                "decisions": [{"step": 1, "action": "reply"}],
            }
        )
        assert ep.episode_id == ep_id
        assert ep.task_id == "task-1"
        assert ep.seed == 42
        assert ep.persona == "analytical"
        assert ep.steps == 5
        assert ep.score == 0.85
        assert ep.total_reward == 2.0
        assert ep.id is not None

    def test_save_episode_updates_existing(self) -> None:
        ep_id = _uid("ep-update")
        self.repo.save_episode(
            {"episode_id": ep_id, "task_id": "task-old", "score": 0.5, "steps": 2}
        )
        updated = self.repo.save_episode(
            {
                "episode_id": ep_id,
                "task_id": "task-new",
                "score": 0.9,
                "steps": 10,
                "total_reward": 5.0,
            }
        )
        assert updated.episode_id == ep_id
        assert updated.task_id == "task-new"
        assert updated.score == 0.9
        assert updated.steps == 10
        assert updated.total_reward == 5.0

    def test_get_episode_by_episode_id(self) -> None:
        ep_id = _uid("ep-get-eid")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.75})
        fetched = self.repo.get_episode(episode_id=ep_id)
        assert fetched is not None
        assert fetched.episode_id == ep_id
        assert fetched.task_id == "t"

    def test_get_episode_by_id(self) -> None:
        ep_id = _uid("ep-get-id")
        saved = self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.6})
        fetched = self.repo.get_episode(id=saved.id)
        assert fetched is not None
        assert fetched.id == saved.id
        assert fetched.episode_id == ep_id

    def test_get_episode_returns_none_for_unknown_id(self) -> None:
        assert self.repo.get_episode(episode_id="nonexistent-episode-id") is None
        assert self.repo.get_episode(id=999_999_999) is None

    def test_list_episodes_no_filters(self) -> None:
        ep_id = _uid("ep-list-all")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.5})
        result = self.repo.list_episodes()
        assert "episodes" in result
        assert result["total"] >= 1
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_list_episodes_task_filter(self) -> None:
        task = _uid("task-f")
        ep_id = _uid("ep-taskf")
        self.repo.save_episode({"episode_id": ep_id, "task_id": task, "score": 0.5})
        result = self.repo.list_episodes(filters={"task_id": task})
        assert any(e["episode_id"] == ep_id for e in result["episodes"])
        # should *not* appear for a different task
        other = self.repo.list_episodes(filters={"task_id": _uid("other")})
        assert not any(e["episode_id"] == ep_id for e in other["episodes"])

    def test_list_episodes_persona_filter(self) -> None:
        persona = "aggressive"
        ep_id = _uid("ep-persf")
        self.repo.save_episode(
            {"episode_id": ep_id, "task_id": "t", "persona": persona, "score": 0.5}
        )
        result = self.repo.list_episodes(filters={"persona": persona})
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_list_episodes_score_range(self) -> None:
        ep_id = _uid("ep-scorer")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.5})
        result = self.repo.list_episodes(filters={"min_score": 0.4, "max_score": 0.6})
        assert any(e["episode_id"] == ep_id for e in result["episodes"])
        out = self.repo.list_episodes(filters={"min_score": 0.9, "max_score": 1.0})
        assert not any(e["episode_id"] == ep_id for e in out["episodes"])

    def test_list_episodes_date_range(self) -> None:
        ep_id = _uid("ep-dater")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.5})
        result = self.repo.list_episodes(
            filters={
                "start_date": "2020-01-01T00:00:00",
                "end_date": "2030-01-01T00:00:00",
            }
        )
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_list_episodes_pagination(self) -> None:
        ids = [_uid("ep-pag") for _ in range(3)]
        for eid in ids:
            self.repo.save_episode({"episode_id": eid, "task_id": "pag", "score": 0.5})
        result = self.repo.list_episodes(page=1, limit=2)
        assert len(result["episodes"]) <= 2
        assert result["page"] == 1
        assert result["limit"] == 2
        assert result["total"] >= 3
        assert result["total_pages"] >= 2

    def test_get_episodes_by_task(self) -> None:
        task = _uid("by-task")
        ep_id = _uid("ep-bt")
        self.repo.save_episode({"episode_id": ep_id, "task_id": task, "score": 0.5})
        result = self.repo.get_episodes_by_task(task)
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_get_episodes_by_persona(self) -> None:
        persona = "creative"
        ep_id = _uid("ep-bp")
        self.repo.save_episode(
            {"episode_id": ep_id, "task_id": "t", "persona": persona, "score": 0.5}
        )
        result = self.repo.get_episodes_by_persona(persona)
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_get_episodes_by_score_range(self) -> None:
        ep_id = _uid("ep-bsr")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.5})
        result = self.repo.get_episodes_by_score_range(0.4, 0.6)
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_get_episodes_by_date_range(self) -> None:
        ep_id = _uid("ep-bdr")
        self.repo.save_episode({"episode_id": ep_id, "task_id": "t", "score": 0.5})
        result = self.repo.get_episodes_by_date_range("2020-01-01T00:00:00", "2030-01-01T00:00:00")
        assert any(e["episode_id"] == ep_id for e in result["episodes"])

    def test_get_stats(self) -> None:
        task = _uid("stats-task")
        persona = "stats-persona"
        ep_id = _uid("ep-stats")
        self.repo.save_episode(
            {
                "episode_id": ep_id,
                "task_id": task,
                "persona": persona,
                "score": 0.8,
                "steps": 10,
                "total_reward": 3.0,
            }
        )
        stats = self.repo.get_stats()
        assert stats["total_episodes"] >= 1
        assert isinstance(stats["avg_score"], float)
        assert isinstance(stats["avg_steps"], float)
        assert isinstance(stats["avg_reward"], float)
        assert isinstance(stats["by_task"], dict)
        assert isinstance(stats["by_persona"], dict)
        assert isinstance(stats["score_distribution"], dict)
        assert "min" in stats["score_distribution"]
        assert "max" in stats["score_distribution"]
        assert "avg" in stats["score_distribution"]


# ---------------------------------------------------------------------------
# UserPreferenceRepository
# ---------------------------------------------------------------------------


class TestUserPreferenceRepository:
    """Tests for UserPreferenceRepository."""

    def setup_method(self) -> None:
        db.init_db()
        self.repo = UserPreferenceRepository()

    def test_save_user_preference_creates_new(self) -> None:
        uid = _uid("up-create")
        pref = self.repo.save_user_preference(
            {
                "user_id": uid,
                "default_persona": "analytical",
                "notification_email": "test@example.com",
            }
        )
        assert pref.user_id == uid
        assert pref.default_persona == "analytical"
        assert pref.notification_email == "test@example.com"
        assert pref.id is not None

    def test_save_user_preference_updates_existing(self) -> None:
        uid = _uid("up-update")
        self.repo.save_user_preference(
            {"user_id": uid, "default_persona": "balanced", "notification_email": "old@example.com"}
        )
        updated = self.repo.save_user_preference(
            {
                "user_id": uid,
                "default_persona": "aggressive",
                "notification_email": "new@example.com",
            }
        )
        assert updated.user_id == uid
        assert updated.default_persona == "aggressive"
        assert updated.notification_email == "new@example.com"

    def test_get_user_preference_returns_correct(self) -> None:
        uid = _uid("up-get")
        self.repo.save_user_preference({"user_id": uid, "default_persona": "creative"})
        fetched = self.repo.get_user_preference(uid)
        assert fetched is not None
        assert fetched.user_id == uid
        assert fetched.default_persona == "creative"

    def test_get_user_preference_returns_none_for_unknown(self) -> None:
        assert self.repo.get_user_preference("nonexistent-user-id") is None

    def test_list_user_preferences_pagination(self) -> None:
        uids = [_uid("up-list") for _ in range(3)]
        for uid in uids:
            self.repo.save_user_preference({"user_id": uid, "default_persona": "balanced"})
        result = self.repo.list_user_preferences(page=1, limit=2)
        assert "preferences" in result
        assert len(result["preferences"]) <= 2
        assert result["page"] == 1
        assert result["limit"] == 2
        assert result["total"] >= 3
        assert result["total_pages"] >= 2


# ---------------------------------------------------------------------------
# TeamSettingsRepository
# ---------------------------------------------------------------------------


class TestTeamSettingsRepository:
    """Tests for TeamSettingsRepository."""

    def setup_method(self) -> None:
        db.init_db()
        self.repo = TeamSettingsRepository()

    def test_save_team_settings_creates_new(self) -> None:
        tid = _uid("ts-create")
        settings = self.repo.save_team_settings(
            {
                "team_id": tid,
                "approval_rules": [{"type": "manager_approval"}],
                "escalation_targets": [{"email": "manager@example.com"}],
            }
        )
        assert settings.team_id == tid
        assert settings.id is not None

    def test_save_team_settings_updates_existing(self) -> None:
        tid = _uid("ts-update")
        self.repo.save_team_settings(
            {
                "team_id": tid,
                "approval_rules": [{"type": "auto"}],
                "escalation_targets": [],
            }
        )
        updated = self.repo.save_team_settings(
            {
                "team_id": tid,
                "approval_rules": [{"type": "manual_review"}],
                "escalation_targets": [{"email": "review@example.com"}],
            }
        )
        assert updated.team_id == tid

    def test_get_team_settings_returns_correct(self) -> None:
        tid = _uid("ts-get")
        self.repo.save_team_settings(
            {"team_id": tid, "approval_rules": [], "escalation_targets": []}
        )
        fetched = self.repo.get_team_settings(tid)
        assert fetched is not None
        assert fetched.team_id == tid

    def test_get_team_settings_returns_none_for_unknown(self) -> None:
        assert self.repo.get_team_settings("nonexistent-team-id") is None

    def test_list_team_settings_pagination(self) -> None:
        tids = [_uid("ts-list") for _ in range(3)]
        for tid in tids:
            self.repo.save_team_settings(
                {"team_id": tid, "approval_rules": [], "escalation_targets": []}
            )
        result = self.repo.list_team_settings(page=1, limit=2)
        assert "settings" in result
        assert len(result["settings"]) <= 2
        assert result["page"] == 1
        assert result["limit"] == 2
        assert result["total"] >= 3
        assert result["total_pages"] >= 2
