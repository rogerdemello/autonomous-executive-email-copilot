from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from research.sim.environment import ExecutiveEmailEnv

dashboard_router = APIRouter()

runtime_env = ExecutiveEmailEnv()

active_connections: list[WebSocket] = []


async def broadcast_state(state: dict[str, Any]) -> None:
    message = json.dumps({"type": "state_update", "data": state})
    for conn in active_connections[:]:
        try:
            await conn.send_text(message)
        except Exception:
            if conn in active_connections:
                active_connections.remove(conn)


@dashboard_router.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                msg_type = message.get("type")
                payload = message.get("data", {})

                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                elif msg_type == "reset":
                    obs = runtime_env.reset(
                        task_id=payload.get("task_id", "hard_full_management"),
                        seed=payload.get("seed", 42),
                        persona=payload.get("persona", "balanced"),
                    )
                    await websocket.send_text(
                        json.dumps({"type": "reset_complete", "data": obs.model_dump()})
                    )
                    await broadcast_state(runtime_env.state().model_dump())
                elif msg_type == "action":
                    action = payload.get("action")
                    result = runtime_env.step(action)
                    await websocket.send_text(
                        json.dumps({"type": "action_result", "data": result.model_dump()})
                    )
                    await broadcast_state(runtime_env.state().model_dump())
                elif msg_type == "get_state":
                    state = runtime_env.state().model_dump()
                    await websocket.send_text(json.dumps({"type": "state", "data": state}))
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
            except Exception as e:
                await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)


@dashboard_router.get("/dashboard/health")
async def dashboard_health() -> dict[str, str]:
    return {"status": "ok", "service": "dashboard_api"}


@dashboard_router.get("/dashboard/state")
async def get_state() -> dict[str, Any]:
    return runtime_env.state().model_dump()


@dashboard_router.post("/dashboard/state")
async def post_state() -> dict[str, Any]:
    return runtime_env.state().model_dump()


@dashboard_router.post("/dashboard/reset")
async def dashboard_reset(
    task_id: str = "hard_full_management",
    seed: int = 42,
    persona: str = "balanced",
) -> dict[str, Any]:
    obs = runtime_env.reset(task_id=task_id, seed=seed, persona=persona)
    state = runtime_env.state().model_dump()
    asyncio.create_task(broadcast_state(state))
    return obs.model_dump()


@dashboard_router.get("/dashboard/eval/results")
async def eval_results(
    task_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    from .core.db_async import AsyncEpisodeRepository

    repo = AsyncEpisodeRepository()
    episodes = await repo.list(task_id=task_id, limit=limit, offset=offset)
    return {
        "total": len(episodes),
        "episodes": [
            {
                "episode_id": e.episode_id,
                "task_id": e.task_id,
                "seed": e.seed,
                "persona": e.persona,
                "score": e.score,
                "total_reward": e.total_reward,
                "steps": e.steps,
                "created_at": str(e.created_at),
            }
            for e in episodes
        ],
    }


@dashboard_router.get("/dashboard/eval/summary")
async def eval_summary() -> dict[str, Any]:
    from app.core.db_async import AsyncEpisodeRepository

    repo = AsyncEpisodeRepository()
    episodes = await repo.list(limit=500)
    if not episodes:
        return {"summary": {}}

    scores = [e.score for e in episodes if e.score is not None]
    rewards = [e.total_reward for e in episodes if e.total_reward is not None]
    from statistics import mean, stdev

    return {
        "summary": {
            "total_episodes": len(episodes),
            "avg_score": mean(scores) if scores else None,
            "std_score": stdev(scores) if len(scores) > 1 else None,
            "avg_reward": mean(rewards) if rewards else None,
            "best_score": max(scores) if scores else None,
            "by_task": {
                task: sum(1 for e in episodes if e.task_id == task)
                for task in {e.task_id for e in episodes}
            },
        }
    }
