from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from baseline.leaderboard import build_leaderboard
from baseline.run_baseline import run as run_baseline
from benchmark.reporter import Reporter
from benchmark.runner import BenchmarkRunner
from reports.generator import PDFGenerator
from telemetry.alerts import alert_manager
from telemetry.metrics import (
    get_metrics_output,
    record_api_error,
    record_episode_end,
    record_episode_start,
    record_request,
)

from .approval import (
    approve_request as _approve_request,
)
from .approval import (
    get_pending_requests as _get_pending_requests,
)
from .approval import (
    get_request_history,
    submit_approval_request,
)
from .approval import (
    get_request_status as _get_request_status,
)
from .approval import (
    reject_request as _reject_request,
)
from .config import get_settings
from .dashboard_api import dashboard_router
from .db import migrate_db
from .environment import ExecutiveEmailEnv
from .grader import evaluate_trajectory
from .learning.example_extractor import example_extractor
from .learning.trajectory_store import feedback_store, trajectory_store
from .logging_config import configure_logging, get_request_id, set_request_id
from .models import (
    Action,
    ActionResult,
    ApprovalRequest,
    ApprovalResponse,
    BaselineRequest,
    BaselineResponse,
    EpisodeHistory,
    GraderRequest,
    GraderResponse,
    LeaderboardRequest,
    LeaderboardResponse,
    Observation,
    ResetRequest,
    StateSnapshot,
    TasksResponse,
)
from .repositories import EpisodeRepository, TeamSettingsRepository, UserPreferenceRepository
from .security import is_valid_identifier, rate_limiter, resolve_auth
from .tasks import list_tasks

configure_logging()
logger = logging.getLogger(__name__)

migrate_db()
repo = EpisodeRepository()
preference_repo = UserPreferenceRepository()
team_settings_repo = TeamSettingsRepository()

# In-memory episode history storage
episode_history_store: dict[str, EpisodeHistory] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup. uvicorn translates SIGTERM into lifespan shutdown, so cleanup
    # here runs on graceful termination.
    logger.info(
        "Starting Autonomous Executive Email Copilot API (log_level=%s)", get_settings().log_level
    )
    yield
    logger.info("Shutting down Autonomous Executive Email Copilot API")


# Single-source the API version from the package (env/__init__.py, which tracks
# pyproject's version) so /version and the OpenAPI version never drift.
from env import __version__ as API_VERSION  # noqa: E402

app = FastAPI(
    title="Autonomous Executive Email Copilot",
    version=API_VERSION,
    description=(
        "Deterministic, RL-style executive inbox simulation for evaluating "
        "agents that triage and manage high-stakes email. Endpoints are stable "
        "within a major version; breaking changes will be introduced under a "
        "versioned path. See /docs for the full schema."
    ),
    license_info={"name": "MIT"},
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _V1PathRewriteMiddleware:
    """Accept ``/v1/<path>`` as a back-compat alias for the unversioned routes.

    The benchmark contract requires the unversioned ``/reset|/step|/state`` paths to
    stay stable, so rather than move routes under ``/v1`` we strip a leading ``/v1``
    from the request path before routing. ``/v1/reset`` and ``/reset`` therefore hit
    the same handler and return identical responses.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path == "/v1" or path.startswith("/v1/"):
                rewritten = path[len("/v1") :] or "/"
                scope = dict(scope)
                scope["path"] = rewritten
                if scope.get("raw_path"):
                    scope["raw_path"] = rewritten.encode("utf-8")
        await self.app(scope, receive, send)


app.add_middleware(_V1PathRewriteMiddleware)
app.include_router(dashboard_router)
runtime_env = ExecutiveEmailEnv()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def _gateway_middleware(request, call_next):
    """Per-request gateway: request id, opt-in rate limiting, opt-in auth, and
    latency/count/error telemetry. Echoes X-Request-ID for log correlation."""
    request_id = set_request_id(request.headers.get("X-Request-ID"))
    settings = get_settings()
    start = time.perf_counter()
    path = request.url.path  # refined to the route template after routing

    # Resolve auth + tenant up front so rate limiting can key on the tenant when
    # one is known. With neither API_AUTH_TOKEN nor API_TENANTS set, ``tenant`` is
    # None and the rate-limit key falls back to the client IP -- byte-identical to
    # the previous IP-only behavior. (Tenant tagging does NOT enforce per-tenant
    # DB isolation; that is a deliberate follow-up.)
    authorized, tenant = resolve_auth(
        request.method,
        settings.api_auth_token,
        settings.tenant_token_map,
        request.headers.get("Authorization"),
        request.headers.get("X-API-Key"),
    )

    # Opt-in rate limiting (disabled when RATE_LIMIT_PER_MINUTE <= 0). Key per
    # tenant when resolved, else per client IP.
    rate_key = f"tenant:{tenant}" if tenant else _client_key(request)
    if not rate_limiter.allow(rate_key, settings.rate_limit_per_minute):
        record_request(0.0, {"path": path, "method": request.method, "status": "429"})
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded", "request_id": request_id},
            headers={"X-Request-ID": request_id, "Retry-After": "60"},
        )

    # Opt-in auth (open when neither API_AUTH_TOKEN nor API_TENANTS is set; only
    # gates mutating methods).
    if not authorized:
        record_request(0.0, {"path": path, "method": request.method, "status": "401"})
        record_api_error("unauthorized")
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid API token", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    # Tag the request with the resolved tenant for downstream handlers/logging.
    if tenant is not None:
        request.state.tenant = tenant

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000.0
        record_request(
            duration_ms, {"path": _metric_path(request), "method": request.method, "status": "500"}
        )
        record_api_error("unhandled_exception")
        logger.exception("Unhandled error handling %s %s", request.method, request.url.path)
        raise
    duration_ms = (time.perf_counter() - start) * 1000.0
    # Prefer the matched route template (e.g. /episodes/{episode_id}) over the
    # concrete path to keep metric label cardinality bounded.
    record_request(
        duration_ms,
        {
            "path": _metric_path(request),
            "method": request.method,
            "status": str(response.status_code),
        },
    )
    if response.status_code >= 500:
        record_api_error(str(response.status_code))
    response.headers["X-Request-ID"] = request_id
    if tenant is not None:
        response.headers["X-Tenant"] = tenant
    return response


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a consistent JSON error without leaking stack traces."""
    logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": get_request_id()},
    )


def _metric_path(request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


# Dashboard static files setup
dashboard_dist = Path(__file__).parent.parent / "dashboard" / "dist"
if dashboard_dist.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_dist), html=True), name="dashboard")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> Response:
    # When the dashboard build is present (the deployed/container case), the app's
    # starting page is the dashboard. Otherwise (API-only / no build) fall back to
    # a small landing page that points at the API surface.
    if dashboard_dist.exists():
        return RedirectResponse(url="/dashboard/", status_code=307)
    return HTMLResponse(
        """
<!doctype html>
<html lang=\"en\">
    <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>Autonomous Executive Email Copilot</title>
        <style>
            body { font-family: Segoe UI, Arial, sans-serif; margin: 2rem; line-height: 1.5; }
            h1 { margin-bottom: 0.25rem; }
            .muted { color: #555; margin-bottom: 1.25rem; }
            ul { padding-left: 1.25rem; }
            code { background: #f3f3f3; padding: 0.15rem 0.35rem; border-radius: 4px; }
        </style>
    </head>
    <body>
        <h1>Autonomous Executive Email Copilot</h1>
        <div class=\"muted\">Server is running. The dashboard build is not present.</div>
        <p>Available endpoints:</p>
        <ul>
            <li><a href=\"/dashboard/\">/dashboard/</a> - operations dashboard (when built)</li>
            <li><a href=\"/docs\">/docs</a> - interactive API docs</li>
            <li><a href=\"/health\">/health</a> - health check</li>
            <li><a href=\"/tasks\">/tasks</a> - task metadata and schemas</li>
            <li><code>POST /grader</code> - trajectory scoring</li>
            <li><code>POST /baseline</code> - baseline execution</li>
            <li><code>POST /leaderboard</code> - aggregate benchmarks</li>
        </ul>
    </body>
</html>
"""
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    """Service name and version (semver). Useful for clients and deploys."""
    return {"name": "autonomous-executive-email-copilot", "version": API_VERSION}


@app.get("/health/live")
def liveness() -> dict[str, str]:
    """Liveness: the process is up and serving. Cheap, no dependencies."""
    return {"status": "alive"}


@app.get("/health/ready")
def readiness() -> Response:
    """Readiness: dependencies (the database) are reachable."""
    try:
        # A trivial lookup exercises the DB connection without side effects.
        repo.get_episode(episode_id="__readiness_probe__")
    except Exception as exc:  # noqa: BLE001 - report not-ready rather than 500
        logger.warning("Readiness probe failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(status_code=200, content={"status": "ready"})


@app.get("/tasks", response_model=TasksResponse)
def tasks() -> TasksResponse:
    return TasksResponse(
        tasks=list_tasks(),
        action_schema=Action.model_json_schema(),
        observation_schema=Observation.model_json_schema(),
    )


@app.post("/reset", response_model=Observation)
def reset(request: ResetRequest | None = Body(default=None)) -> Observation:
    payload = request or ResetRequest()
    return runtime_env.reset(task_id=payload.task_id, seed=payload.seed, persona=payload.persona)


@app.post("/step", response_model=ActionResult)
def step(action: Action) -> ActionResult:
    return runtime_env.step(action)


@app.get("/state", response_model=StateSnapshot)
def state() -> StateSnapshot:
    return runtime_env.state()


@app.post("/state", response_model=StateSnapshot)
def state_post() -> StateSnapshot:
    # Keep POST variant for compatibility with method-style runtime checks.
    return runtime_env.state()


@app.post("/grader", response_model=GraderResponse)
def grader(request: GraderRequest) -> GraderResponse:
    try:
        return evaluate_trajectory(
            task_id=request.task_id,
            seed=request.seed,
            persona=request.persona,
            actions=request.actions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _persist_baseline_episode(
    episode_id: str,
    request: BaselineRequest,
    result: dict,
    decision_traces: list,
) -> None:
    """Best-effort persistence of a finished baseline run to the episode DB and
    the learning trajectory store. Failures are logged, never raised, so a
    storage problem can't break the run response."""
    score = float(result["score"])
    steps = int(result["steps"])
    try:
        repo.save_episode(
            {
                "episode_id": episode_id,
                "task_id": request.task_id,
                "seed": request.seed,
                "persona": request.persona,
                "steps": steps,
                "score": score,
                "total_reward": float(result["total_reward"]),
                "decisions": decision_traces,
            }
        )
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        logger.warning("Failed to persist episode %s: %s", episode_id, exc)

    try:
        # save_trajectory self-gates on its score threshold and returns None below it.
        trajectory_data = [{"action": action} for action in result.get("actions", [])]
        trajectory_store.save_trajectory(
            episode_id=episode_id,
            task_id=request.task_id,
            seed=request.seed,
            persona=request.persona,
            score=score,
            steps=steps,
            trajectory_data=trajectory_data,
        )
    except Exception as exc:  # noqa: BLE001 - learning capture is best-effort
        logger.warning("Failed to store trajectory for %s: %s", episode_id, exc)


@app.post("/baseline", response_model=BaselineResponse)
def baseline(request: BaselineRequest) -> BaselineResponse:
    record_episode_start()
    try:
        result = run_baseline(
            task_id=request.task_id,
            seed=request.seed,
            max_steps=max(1, request.max_steps),
            persona=request.persona,
            mode=request.mode,
            stress_rate=request.stress_rate,
        )
    except ValueError as exc:
        record_episode_end(success=False)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace = [Action.model_validate(item) for item in result["actions"]]
    decision_traces = result.get("decision_traces", [])

    episode_id = f"{request.task_id}_{request.seed}_{request.persona}"
    episode_history_store[episode_id] = EpisodeHistory(
        episode_id=episode_id,
        task_id=request.task_id,
        seed=request.seed,
        persona=request.persona,
        steps=int(result["steps"]),
        score=float(result["score"]),
        total_reward=float(result["total_reward"]),
        decisions=decision_traces,
    )

    _persist_baseline_episode(episode_id, request, result, decision_traces)
    record_episode_end(success=True)

    return BaselineResponse(
        task_id=request.task_id,
        seed=request.seed,
        persona=request.persona,
        mode=request.mode,
        stress_rate=request.stress_rate,
        score=float(result["score"]),
        total_reward=float(result["total_reward"]),
        steps=int(result["steps"]),
        breakdown=result["breakdown"],
        action_trace=trace,
        decision_trace=decision_traces,
    )


@app.post("/leaderboard", response_model=LeaderboardResponse)
def leaderboard(request: LeaderboardRequest) -> LeaderboardResponse:
    data = build_leaderboard(
        tasks=request.tasks,
        personas=request.personas,
        seeds=request.seeds,
        max_steps=max(1, request.max_steps),
        mode=request.mode,
        stress_rate=request.stress_rate,
        csv_out=request.csv_out,
    )
    return LeaderboardResponse(**data)


@app.get("/replay/{episode_id}", response_model=EpisodeHistory)
def replay(episode_id: str) -> EpisodeHistory:
    if not is_valid_identifier(episode_id):
        raise HTTPException(status_code=400, detail="Invalid episode_id")
    if episode_id in episode_history_store:
        return episode_history_store[episode_id]
    # Fall back to the persisted episode so replay survives a restart.
    episode = repo.get_episode(episode_id=episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")
    data = episode.to_dict()
    return EpisodeHistory(
        episode_id=data["episode_id"],
        task_id=data["task_id"],
        seed=data["seed"],
        persona=data["persona"],
        steps=data["steps"],
        score=data["score"],
        total_reward=data["total_reward"],
        decisions=data["decisions"],
    )


class ApprovalRequestInput(BaseModel):
    action_type: str
    email_id: str
    content: str | None = None
    escalate_to: str | None = None


class ApprovalResponseInput(BaseModel):
    approver_id: str
    comment: str | None = None


@app.post("/approval/request", response_model=ApprovalRequest)
def approval_request(payload: ApprovalRequestInput) -> ApprovalRequest:
    return submit_approval_request(
        action_type=payload.action_type,
        email_id=payload.email_id,
        content=payload.content,
        escalate_to=payload.escalate_to,
    )


@app.post("/approval/{request_id}/approve", response_model=ApprovalResponse)
def approval_approve(request_id: str, payload: ApprovalResponseInput) -> ApprovalResponse:
    response = _approve_request(
        request_id=request_id,
        approver_id=payload.approver_id,
        comment=payload.comment,
    )
    if response is None:
        raise HTTPException(
            status_code=404, detail=f"Approval request {request_id} not found or already processed"
        )
    return response


@app.post("/approval/{request_id}/reject", response_model=ApprovalResponse)
def approval_reject(request_id: str, payload: ApprovalResponseInput) -> ApprovalResponse:
    response = _reject_request(
        request_id=request_id,
        approver_id=payload.approver_id,
        comment=payload.comment,
    )
    if response is None:
        raise HTTPException(
            status_code=404, detail=f"Approval request {request_id} not found or already processed"
        )
    return response


# Static sub-paths MUST be declared before the parameterized /approval/{request_id}
# route, otherwise FastAPI's first-match-wins routing captures "pending"/"history"
# as a request_id and these endpoints 404.
@app.get("/approval/pending", response_model=list[ApprovalRequest])
def approval_pending() -> list[ApprovalRequest]:
    return _get_pending_requests()


@app.get("/approval/history", response_model=list[ApprovalRequest])
def approval_history(limit: int = 50) -> list[ApprovalRequest]:
    return get_request_history(limit=limit)


@app.get("/approval/{request_id}", response_model=ApprovalRequest)
def approval_status(request_id: str) -> ApprovalRequest:
    request = _get_request_status(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Approval request {request_id} not found")
    return request


# Episode endpoints
class EpisodeFilters(BaseModel):
    task_id: str | None = None
    persona: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    start_date: str | None = None
    end_date: str | None = None


@app.get("/episodes")
def list_episodes(
    task_id: str | None = None,
    persona: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> dict:
    page = max(1, page)
    limit = max(1, min(limit, 100))
    filters = {}
    if task_id:
        filters["task_id"] = task_id
    if persona:
        filters["persona"] = persona
    if min_score is not None:
        filters["min_score"] = min_score
    if max_score is not None:
        filters["max_score"] = max_score
    if start_date:
        filters["start_date"] = start_date
    if end_date:
        filters["end_date"] = end_date
    return repo.list_episodes(filters=filters if filters else None, page=page, limit=limit)


# /episodes/stats must precede /episodes/{episode_id} so "stats" is not captured
# as an episode_id by the parameterized route.
@app.get("/episodes/stats")
def episode_stats() -> dict:
    return repo.get_stats()


@app.get("/episodes/{episode_id}")
def get_episode(episode_id: str) -> dict:
    if not is_valid_identifier(episode_id):
        raise HTTPException(status_code=400, detail="Invalid episode_id")
    episode = repo.get_episode(episode_id=episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")
    return episode.to_dict()


class UserPreferenceInput(BaseModel):
    default_persona: str | None = "balanced"
    notification_email: str | None = None


@app.get("/preferences/user/{user_id}")
def get_user_preference(user_id: str) -> dict:
    preference = preference_repo.get_user_preference(user_id)
    if preference is None:
        return {
            "user_id": user_id,
            "default_persona": "balanced",
            "notification_email": None,
            "created_at": None,
            "updated_at": None,
        }
    return preference.to_dict()


@app.put("/preferences/user/{user_id}")
def save_user_preference(user_id: str, payload: UserPreferenceInput) -> dict:
    preference_data = {
        "user_id": user_id,
        "default_persona": payload.default_persona,
        "notification_email": payload.notification_email,
    }
    preference = preference_repo.save_user_preference(preference_data)
    return preference.to_dict()


@app.get("/preferences/users")
def list_user_preferences(page: int = 1, limit: int = 20) -> dict:
    return preference_repo.list_user_preferences(page=page, limit=limit)


class TeamSettingsInput(BaseModel):
    approval_rules: list[dict[str, Any]] | None = None
    escalation_targets: list[dict[str, Any]] | None = None


@app.get("/preferences/team/{team_id}")
def get_team_settings(team_id: str) -> dict:
    settings = team_settings_repo.get_team_settings(team_id)
    if settings is None:
        return {
            "team_id": team_id,
            "approval_rules": [],
            "escalation_targets": [],
            "created_at": None,
            "updated_at": None,
        }
    return settings.to_dict()


@app.put("/preferences/team/{team_id}")
def save_team_settings(team_id: str, payload: TeamSettingsInput) -> dict:
    settings_data = {
        "team_id": team_id,
        "approval_rules": payload.approval_rules or [],
        "escalation_targets": payload.escalation_targets or [],
    }
    settings = team_settings_repo.save_team_settings(settings_data)
    return settings.to_dict()


@app.get("/preferences/teams")
def list_team_settings(page: int = 1, limit: int = 20) -> dict:
    return team_settings_repo.list_team_settings(page=page, limit=limit)


class FeedbackInput(BaseModel):
    episode_id: str | None = None
    task_id: str
    seed: int
    persona: str
    step_index: int | None = None
    action_type: str | None = None
    email_id: str | None = None
    feedback: str
    comment: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    episode_id: str | None
    task_id: str
    seed: int
    persona: str
    step_index: int | None
    action_type: str | None
    email_id: str | None
    feedback: str
    comment: str | None
    created_at: str


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackInput) -> FeedbackResponse:
    if payload.feedback not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="feedback must be 'good' or 'bad'")
    record = feedback_store.add_feedback(
        episode_id=payload.episode_id,
        task_id=payload.task_id,
        seed=payload.seed,
        persona=payload.persona,
        step_index=payload.step_index,
        action_type=payload.action_type,
        email_id=payload.email_id,
        feedback=payload.feedback,
        comment=payload.comment,
    )
    return FeedbackResponse(**record)


@app.get("/feedback", response_model=list[FeedbackResponse])
def list_feedback(
    task_id: str | None = None,
    feedback: str | None = None,
    limit: int = 50,
) -> list[FeedbackResponse]:
    records = feedback_store.get_feedback(task_id=task_id, feedback=feedback, limit=limit)
    return [FeedbackResponse(**r) for r in records]


@app.get("/learning/stats")
def learning_stats() -> dict:
    traj_stats = trajectory_store.get_stats()
    fb_stats = feedback_store.get_stats()
    return {
        "trajectories": traj_stats,
        "feedback": fb_stats,
    }


@app.get("/learning/examples/{task_id}/{persona}")
def get_learning_examples(task_id: str, persona: str) -> dict:
    examples = example_extractor.extract_all_examples(task_id, persona)
    return {
        "task_id": task_id,
        "persona": persona,
        "has_examples": any(examples.values()),
        "examples": examples,
    }


class BenchmarkRequest(BaseModel):
    tasks: list[str] | None = None
    personas: list[str] | None = None
    seeds: list[int] | None = None
    max_steps: int = 100


class BenchmarkResponse(BaseModel):
    summary: list[dict]
    results: list[dict]


class BenchmarkHTMLResponse(BaseModel):
    html: str


@app.post("/benchmark/run", response_model=BenchmarkResponse)
def run_benchmark(request: BenchmarkRequest) -> BenchmarkResponse:
    runner = BenchmarkRunner(
        tasks=request.tasks,
        personas=request.personas,
        seeds=request.seeds,
        max_steps=request.max_steps,
    )
    results = runner.run_all()
    reporter = Reporter(runner)
    json_data = reporter.generate_json(results)
    import json

    data = json.loads(json_data)
    return BenchmarkResponse(summary=data["summary"], results=data["results"])


@app.post("/benchmark/run_html", response_model=BenchmarkHTMLResponse)
def run_benchmark_html(request: BenchmarkRequest) -> BenchmarkHTMLResponse:
    runner = BenchmarkRunner(
        tasks=request.tasks,
        personas=request.personas,
        seeds=request.seeds,
        max_steps=request.max_steps,
    )
    results = runner.run_all()
    reporter = Reporter(runner)
    html = reporter.generate_html(results)
    return BenchmarkHTMLResponse(html=html)


pdf_generator = PDFGenerator()


class ReportGenerateRequest(BaseModel):
    episode_data: dict[str, Any]


@app.get("/reports/episode/{episode_id}")
def download_episode_report(episode_id: str):
    if not is_valid_identifier(episode_id):
        raise HTTPException(status_code=400, detail="Invalid episode_id")
    try:
        pdf_bytes = pdf_generator.generate(episode_id)
        from fastapi.responses import Response

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{episode_id}.pdf"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/reports/generate")
def generate_report_from_data(payload: ReportGenerateRequest):
    pdf_bytes = pdf_generator.generate_summary(payload.episode_data)
    from fastapi.responses import Response

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=report.pdf"},
    )


@app.get("/metrics")
def metrics_endpoint() -> Response:
    output = get_metrics_output()
    return Response(content=output, media_type="text/plain")


class WebhookInput(BaseModel):
    url: str
    rule_name: str | None = None


@app.post("/alerts/webhook")
def add_webhook(payload: WebhookInput) -> dict:
    rule = None
    if payload.rule_name:
        for r in alert_manager._rules:
            if r.name == payload.rule_name:
                rule = r
                break
    if rule:
        rule.webhook = payload.url
        return {"status": "ok", "message": f"Webhook added to rule {payload.rule_name}"}
    return {"status": "error", "message": f"Rule {payload.rule_name} not found"}


@app.get("/alerts")
def alerts_endpoint() -> dict:
    metrics_dict = _parse_metrics_to_dict()
    alert_manager.set_metrics(metrics_dict)
    triggered = alert_manager.check_rules()
    return {
        "active_alerts": [
            {"rule_name": a.rule_name, "message": a.message, "timestamp": a.timestamp}
            for a in triggered
        ],
        "all_alerts": [
            {"rule_name": a.rule_name, "message": a.message, "timestamp": a.timestamp}
            for a in alert_manager.get_alerts()
        ],
    }


def _parse_metrics_to_dict() -> dict:
    output = get_metrics_output()
    result = {}
    for line in output.strip().split("\n"):
        if line:
            parts = line.split("{")
            name = parts[0]
            if len(parts) > 1:
                label_part = parts[1].rstrip("}")
                labels = {}
                for label in label_part.split(","):
                    if "=" in label:
                        k, v = label.split("=", 1)
                        labels[k] = v.strip('"')
                key = name + "_" + "_".join(f"{k}={v}" for k, v in sorted(labels.items()))
            else:
                key = name
            value_part = line.split()[-1]
            try:
                result[key] = float(value_part)
            except ValueError:
                pass
    return result
