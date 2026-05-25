from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .environment import ExecutiveEmailEnv

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
