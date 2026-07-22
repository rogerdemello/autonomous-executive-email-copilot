from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .db import Episode, TeamSettings, UserPreference

logger = logging.getLogger(__name__)

ASYNC_DATABASE_URL = os.environ.get(
    "ASYNC_DATABASE_URL",
    "sqlite+aiosqlite:///data/episodes.db",
)

_async_engine = None
_async_session_factory = None


def _get_async_engine():
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            ASYNC_DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
        )
    return _async_engine


def _get_async_session_factory():
    global _async_session_factory
    if _async_session_factory is None:
        engine = _get_async_engine()
        _async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    session = _get_async_session_factory()
    async with session() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()


class AsyncEpisodeRepository:
    """Async repository for episodes."""

    async def get(self, episode_id: str) -> Episode | None:
        async with get_async_session() as session:
            result = await session.execute(select(Episode).where(Episode.episode_id == episode_id))
            return result.scalar_one_or_none()

    async def list(
        self,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Episode]:
        async with get_async_session() as session:
            stmt = select(Episode).order_by(Episode.created_at.desc()).offset(offset).limit(limit)
            if task_id:
                stmt = stmt.where(Episode.task_id == task_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def save(self, episode: Episode) -> Episode:
        async with get_async_session() as session:
            session.add(episode)
            await session.flush()
            return episode


class AsyncUserPreferenceRepository:
    async def get(self, user_id: str) -> UserPreference | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def upsert(self, preference: UserPreference) -> UserPreference:
        async with get_async_session() as session:
            existing = await session.execute(
                select(UserPreference).where(UserPreference.user_id == preference.user_id)
            )
            found = existing.scalar_one_or_none()
            if found:
                found.default_persona = preference.default_persona
                found.notification_email = preference.notification_email
            else:
                session.add(preference)
            await session.flush()
            return preference


class AsyncTeamSettingsRepository:
    async def get(self, team_id: str) -> TeamSettings | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(TeamSettings).where(TeamSettings.team_id == team_id)
            )
            return result.scalar_one_or_none()

    async def upsert(self, settings: TeamSettings) -> TeamSettings:
        async with get_async_session() as session:
            existing = await session.execute(
                select(TeamSettings).where(TeamSettings.team_id == settings.team_id)
            )
            found = existing.scalar_one_or_none()
            if found:
                found.approval_rules = settings.approval_rules
                found.escalation_targets = settings.escalation_targets
            else:
                session.add(settings)
            await session.flush()
            return settings
