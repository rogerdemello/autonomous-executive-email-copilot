from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from telemetry.otel import configure_otel, in_span

    _OTEL_CONFIGURED = False
except ImportError:
    _OTEL_CONFIGURED = True  # skip configure

    def in_span(name, attributes=None, kind=None):
        from contextlib import nullcontext

        return nullcontext()

    def configure_otel(**kwargs):
        pass


from reports.generator import PDFGenerator
from research.baseline.leaderboard import build_leaderboard
from research.baseline.run_baseline import run as run_baseline
from research.benchmark.reporter import Reporter
from research.benchmark.runner import BenchmarkRunner
from research.sim.environment import ExecutiveEmailEnv
from research.sim.grader import evaluate_trajectory
from research.sim.learning.example_extractor import example_extractor
from research.sim.learning.trajectory_store import feedback_store, trajectory_store
from research.sim.tasks import list_tasks
from telemetry.alerts import alert_manager
from telemetry.metrics import (
    get_metrics_output,
    record_api_error,
    record_episode_end,
    record_episode_start,
    record_request,
)

from .core.approval import (
    approve_request as _approve_request,
)
from .core.approval import (
    get_pending_requests as _get_pending_requests,
)
from .core.approval import (
    get_request_history,
    submit_approval_request,
)
from .core.approval import (
    get_request_status as _get_request_status,
)
from .core.approval import (
    reject_request as _reject_request,
)
from .core.config import get_settings
from .core.db import migrate_db, schema_is_current
from .core.logging_config import configure_logging, get_request_id, set_request_id
from .core.models import (
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
from .core.paths import STATIC_DIR
from .core.repositories import EpisodeRepository, TeamSettingsRepository, UserPreferenceRepository
from .core.security import is_sensitive_read, is_valid_identifier, rate_limiter, resolve_auth
from .live_api import dashboard_router

configure_logging()
logger = logging.getLogger(__name__)

# NOTE: the schema migration deliberately does NOT run here. Running it at
# import time made an unreachable database a hard import crash — the process
# died before FastAPI existed, so /health/ready could never report the degraded
# state it was written to report, and the container crash-looped with no
# diagnosable endpoint. It now runs in the lifespan (see below), and readiness
# asks the database directly via schema_is_current().
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
    if get_settings().auth_secret_is_dev:
        # ENVIRONMENT=production promises a hard failure rather than a silent
        # insecure default: booting a real deployment with the well-known dev
        # secret would let anyone forge session tokens and license keys.
        if get_settings().is_production:
            raise RuntimeError(
                "AUTH_SECRET_KEY must be set when ENVIRONMENT=production. It signs "
                "session tokens and license keys; the development fallback is a "
                "publicly-known constant. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        logger.warning(
            "AUTH_SECRET_KEY is not set — using an insecure development signing "
            "secret. Set AUTH_SECRET_KEY to a long random value in production; "
            "all session tokens and license keys are signed with it."
        )
    # Bring the schema up to date. A failure here is logged and leaves the
    # service running but NOT ready: /health/ready returns 503, so an
    # orchestrator withholds traffic and an operator gets a reachable process
    # with a readable log instead of a crash loop.
    try:
        migrate_db()
    except Exception:  # noqa: BLE001 - report not-ready rather than crash at boot
        logger.exception("Schema migration failed at startup; service will report not-ready")
    global _OTEL_CONFIGURED
    if not _OTEL_CONFIGURED:
        settings = get_settings()
        otlp_endpoint = getattr(settings, "otel_exporter_otlp_endpoint", None) or None
        configure_otel(
            service_name="exec-email-copilot",
            otlp_endpoint=otlp_endpoint,
            enable_console=False,
        )
        _OTEL_CONFIGURED = True
    if get_settings().demo_seed_on_startup:
        # Deployments without a shell (Render) boot straight into a presentable
        # demo. Best-effort: a seed failure is logged, never fatal — the service
        # must come up either way.
        try:
            from app.saas.demo_seed import seed_demo

            seed_demo()
        except Exception:  # noqa: BLE001 - seeding must not block startup
            logger.exception("Demo seed on startup failed; continuing without it")
    sync_worker = None
    if get_settings().sync_worker_enabled:
        from app.saas.sync_worker import BackgroundSyncWorker

        sync_worker = BackgroundSyncWorker()
        sync_worker.start()
    yield
    if sync_worker is not None:
        await sync_worker.stop()
    logger.info("Shutting down Autonomous Executive Email Copilot API")


# Single-source the API version from the package (app/__init__.py, which tracks
# pyproject's version) so /version and the OpenAPI version never drift.
from app import __version__ as API_VERSION  # noqa: E402

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
# Credentials only ride when origins are pinned: "*" + allow_credentials is a
# combination browsers reject and that would otherwise hand any origin the
# session cookie on a misconfigured deployment.
_cors_origins = get_settings().cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
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

# Baseline security headers on every response (HTML, JSON, static). Added after
# CORS/rewrite so it wraps them (outermost of the three); TrustedHost outermost
# of all so a forged Host header is rejected before any routing happens.
from .web.middleware import SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(SecurityHeadersMiddleware)

_allowed_hosts = get_settings().allowed_host_list
if _allowed_hosts != ["*"]:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

app.include_router(dashboard_router)

# Commercial SaaS layer: accounts, organizations (tenants), RBAC, sales-led
# licensing. Additive — it does not touch the benchmark/scoring routes.
from .saas.mailbox_routes import mailbox_router  # noqa: E402
from .saas.marketing import marketing_router  # noqa: E402
from .saas.operator_routes import operator_router  # noqa: E402
from .saas.processing_routes import inbox_router  # noqa: E402
from .saas.routes import (  # noqa: E402
    SAAS_SELF_AUTH_PREFIXES,
    auth_router,
    billing_router,
    org_router,
)

app.include_router(auth_router)
app.include_router(org_router)
app.include_router(billing_router)
app.include_router(mailbox_router)
app.include_router(inbox_router)
app.include_router(marketing_router)
app.include_router(operator_router)

from .web.routes import (  # noqa: E402
    _LoginRedirect,
    login_redirect_handler,
    web_http_error_handler,
    web_router,
)

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
    path = request.url.path

    span_attrs = {
        "http.method": request.method,
        "http.target": path,
        "net.peer.ip": request.client.host if request.client else "unknown",
        "enduser.id": request.headers.get("X-User-ID", ""),
    }
    with in_span("gateway.request", attributes=span_attrs):
        authorized, tenant = resolve_auth(
            request.method,
            settings.api_auth_token,
            settings.tenant_token_map,
            request.headers.get("Authorization"),
            request.headers.get("X-API-Key"),
            # With a token configured, reads of the benchmark surface (pending
            # approvals, episodes, preferences, live sim state) need it too —
            # /approval/pending would otherwise be an anonymous read on a
            # locked-down deployment.
            enforce_reads=is_sensitive_read(path),
        )

        rate_key = f"tenant:{tenant}" if tenant else _client_key(request)
        if not rate_limiter.allow(rate_key, settings.rate_limit_per_minute):
            record_request(0.0, {"path": path, "method": request.method, "status": "429"})
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "request_id": request_id},
                headers={"X-Request-ID": request_id, "Retry-After": "60"},
            )

        # The SaaS/product API self-authenticates with per-user session tokens, so
        # it must bypass the operator API_AUTH_TOKEN gate (it enforces its own auth
        # via route dependencies). Public auth endpoints are a subset of these.
        # Segment-aware on purpose: a raw startswith let /approval/* ride the
        # /app prefix straight past the operator token gate.
        if not authorized and any(
            path == p or path.startswith(p + "/") for p in SAAS_SELF_AUTH_PREFIXES
        ):
            authorized = True

        if not authorized:
            record_request(0.0, {"path": path, "method": request.method, "status": "401"})
            record_api_error("unauthorized")
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API token", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )

        if tenant is not None:
            request.state.tenant = tenant

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            record_request(
                duration_ms,
                {"path": _metric_path(request), "method": request.method, "status": "500"},
            )
            record_api_error("unhandled_exception")
            logger.exception("Unhandled error handling %s %s", request.method, request.url.path)
            raise
        duration_ms = (time.perf_counter() - start) * 1000.0
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


# The web UI. Static assets first, then the page router — mounting /static as a
# real StaticFiles app means the stylesheet is served with correct caching and
# content types without a handler of our own.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(web_router)

# An anonymous visitor hitting a page behind the session gets the login form
# with their destination preserved, rather than a 401 they cannot act on.
app.add_exception_handler(_LoginRedirect, login_redirect_handler)

# And a signed-in visitor who double-clicks Approve or races a disconnect gets
# an error page with a way back, not raw JSON. API paths keep the JSON contract.
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402

app.add_exception_handler(_StarletteHTTPException, web_http_error_handler)


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
    """Readiness: the schema is migrated and the database is reachable."""
    if not schema_is_current():
        # The startup migration failed or the DB is behind the code. Serving
        # traffic against an unmigrated schema produces confusing column errors
        # deep inside a request; failing the probe keeps traffic away instead.
        logger.warning("Readiness probe failed: schema is missing or behind")
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "schema"})
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


@app.post("/step/stream")
async def step_stream(request: BaselineRequest) -> StreamingResponse:
    """SSE endpoint that streams each environment step as a server-sent event.

    Events:
      - ``step``: a single step result (action + observation + reward)
      - ``done``: final result with score
      - ``error``: an error message

    Usage::

        curl -N -X POST http://localhost:8000/step/stream \\
          -H "Content-Type: application/json" \\
          -d '{"task_id":"hard_full_management","seed":42,"persona":"balanced","mode":"baseline","max_steps":10}'
    """

    async def event_generator():
        env = ExecutiveEmailEnv()
        env.reset(
            task_id=request.task_id,
            seed=request.seed,
            persona=request.persona,
        )

        total_reward = 0.0
        steps = 0
        decision_traces: list[dict[str, Any]] = []
        action_trace: list[Action] = []

        for step_idx in range(request.max_steps):
            if env._is_done():
                break

            # Compute action using the appropriate policy
            from .copilot.policy import BaselinePolicy, HybridPolicy

            if request.mode == "baseline":
                policy = BaselinePolicy()
                action: Action | None = policy.next_action(env._build_observation())
            elif request.mode in ("llm", "hybrid"):
                policy = HybridPolicy()
                action = policy.next_action(env._build_observation())
            else:
                action = None

            if action is None:
                break

            result = env.step(action)
            total_reward = env._total_reward
            steps += 1
            action_trace.append(action)

            # Build decision trace
            trace_entry = {
                "step": step_idx,
                "action": action.model_dump() if action else None,
                "reward": result.reward,
                "done": result.done,
                "observation": result.observation.model_dump() if result.observation else None,
            }
            decision_traces.append(trace_entry)

            # Yield SSE event
            yield f"data: {json.dumps(trace_entry)}\n\n"

            if result.done:
                break

        # Final score
        from research.sim.grader import evaluate_trajectory

        try:
            grade = evaluate_trajectory(
                task_id=request.task_id,
                seed=request.seed,
                persona=request.persona,
                actions=action_trace,
            )
        except Exception:
            grade = None

        final = {
            "done": True,
            "steps": steps,
            "total_reward": total_reward,
            "score": grade.score if grade else None,
            "breakdown": grade.breakdown if grade else None,
            "action_trace": [a.model_dump() for a in action_trace],
        }
        yield f"data: {json.dumps(final)}\n\n"

    import json

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


def parse_metrics_text(output: str) -> dict[str, float]:
    """Flatten Prometheus exposition text into a dict for alert rules.

    Every series is keyed twice:

    - by its full identity (``requests_total_method=GET_path=/health_status=200``)
    - by its bare metric name (``requests_total``), summed across all label sets

    Two bugs made every default rule in :mod:`telemetry.alerts` unable to fire,
    whatever the service did, and both are fixed here:

    1. An *unlabelled* line is ``name value`` separated by a space, but the name
       was taken as everything before the first ``{`` — so ``episodes_failed_total 3``
       was stored under the key ``"episodes_failed_total 3"``. Rules reading
       ``episodes_failed_total`` found nothing. Fixed by splitting off the value.
    2. A *labelled* line was stored only under its composite key. Since
       ``record_request`` always passes labels, ``requests_total`` never existed
       as a key at all. Fixed by also accumulating the label-free total.

    Pure function of its input so it can be tested deterministically — the
    metrics registry is process-global, so a test that scrapes it is at the
    mercy of every other test that ran first.
    """
    result: dict[str, float] = {}
    for raw in output.strip().split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):  # skip HELP/TYPE comment lines
            continue
        try:
            value = float(line.split()[-1])
        except ValueError:
            continue
        head = line.split("{", 1)
        # The metric name is the first whitespace-delimited token, which strips
        # the trailing value off an unlabelled line.
        name = head[0].split()[0]
        key = name
        if len(head) > 1:
            labels = {}
            for label in head[1].split("}")[0].split(","):
                if "=" in label:
                    k, v = label.split("=", 1)
                    labels[k] = v.strip('"')
            if labels:
                key = name + "_" + "_".join(f"{k}={v}" for k, v in sorted(labels.items()))
        result[key] = value
        if key != name:
            # Sum the label dimensions away so `requests_total` means what a
            # rule author expects: every request, regardless of path or status.
            result[name] = result.get(name, 0.0) + value
    return result


def _parse_metrics_to_dict() -> dict:
    return parse_metrics_text(get_metrics_output())


def main() -> None:
    """Run the app with uvicorn, binding the port the platform hands us.

    Container platforms (Render, Fly, Cloud Run, Heroku) inject ``$PORT`` and
    expect the process to bind it on all interfaces. This is the
    ``[project.scripts] server`` console entrypoint.
    """
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # nosec B104 - container service binds all interfaces by design
        port=port,
        # Same proxy posture as the Dockerfile CMD: real client IPs behind a
        # platform proxy; spoofable only if the process is exposed directly.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
