from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from baseline.leaderboard import build_leaderboard
from baseline.run_baseline import run as run_baseline
from .environment import ExecutiveEmailEnv
from .grader import evaluate_trajectory
from .models import (
    Action,
    ActionResult,
    BaselineRequest,
    BaselineResponse,
    GraderRequest,
    GraderResponse,
    LeaderboardRequest,
    LeaderboardResponse,
    Observation,
    ResetRequest,
    StateSnapshot,
    TasksResponse,
)
from .tasks import list_tasks

app = FastAPI(title="Autonomous Executive Email Copilot", version="0.1.0")
runtime_env = ExecutiveEmailEnv()


@app.get("/", response_class=HTMLResponse)
def root() -> str:
        return """
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
        <div class=\"muted\">Server is running.</div>
        <p>Available endpoints:</p>
        <ul>
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


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
        return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.post("/baseline", response_model=BaselineResponse)
def baseline(request: BaselineRequest) -> BaselineResponse:
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace = [Action.model_validate(item) for item in result["actions"]]
    decision_traces = result.get("decision_traces", [])
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
