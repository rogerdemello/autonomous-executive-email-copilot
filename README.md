---
title: Autonomous Executive Email Copilot
emoji: "📧"
colorFrom: blue
colorTo: green
sdk: streamlit
sdk_version: "1.35.0"
python_version: "3.10"
app_file: streamlit_app.py
pinned: false
---

# Autonomous Executive Email Copilot

Autonomous Executive Email Copilot is an OpenEnv-style environment that simulates executive inbox management under time pressure and business risk.

All task/scenario content is data-driven from YAML files under data/ (no scenario emails are hardcoded in Python source).

## Highlights

- Multi-step decisions: classify, prioritize, reply, escalate, defer
- Risk-aware scoring with penalties for costly mistakes
- Persona-aware reward shaping: strict_ceo, balanced, chill_manager
- Mid-episode interruptions with new incoming emails
- Deterministic tasks for reproducible evaluation
- API endpoints for tasks, grading, and baseline runs

## Observation

```json
{
  "emails": [
    {
      "id": "e1",
      "sender": "client@company.com",
      "sender_role": "client",
      "subject": "URGENT: Contract issue",
      "body": "Need legal review before signing.",
      "priority_hint": "high",
      "deadline_minutes": 120,
      "business_value": 0.9,
      "risk_tag": "legal",
      "thread_history": []
    }
  ],
  "time_remaining": 180,
  "pending_actions": ["e1"],
  "risk_level": "medium",
  "current_minute": 0,
  "persona": "balanced",
  "remaining_interruptions": 1
}
```

## Action

```json
{
  "action_type": "classify | reply | defer | escalate | prioritize",
  "email_id": "e1",
  "label": "spam | normal | urgent",
  "content": "Optional response",
  "priority_order": ["e1", "e2"],
  "escalate_to": "legal_team"
}
```

## Reward Signals

- Correct classification: +0.2
- Prioritization quality: up to +0.3
- Reply quality: up to +0.5
- Smart escalation: +0.4 (+0.1 if escalation target matches)
- Missed urgent deadline: -0.7
- Wrong reply on critical email: -1.0
- Redundant action: -0.1

Persona profiles scale penalties differently:

- strict_ceo: strongest deadline and terminal penalties
- balanced: default policy
- chill_manager: softer delay penalties

## Tasks

- easy_classification
- medium_prioritization
- hard_full_management

Task definitions and scenario payloads live in:

- data/tasks.yaml
- data/scenarios/easy_classification.yaml
- data/scenarios/medium_prioritization.yaml
- data/scenarios/hard_full_management.yaml

Runtime tuning values also live in data/settings.yaml:

- action costs
- persona penalty multipliers
- classifier keyword lists

YAML hot reload is enabled by default. If you edit any file under data/, subsequent resets/steps/requests will use updated values without restarting the server.

Hard-task score formula:

```python
score = (
    0.3 * classification_accuracy +
    0.3 * action_correctness +
    0.4 * response_quality
)
```

## API

- GET /tasks
- POST /grader
- POST /baseline
- POST /leaderboard

Both /grader and /baseline accept persona in request JSON:

```json
{
  "task_id": "hard_full_management",
  "seed": 42,
  "persona": "strict_ceo"
}
```

Baseline also supports:

- mode: baseline, stress, or llm
- stress_rate: 0.0 to 1.0 (used only in stress mode)

## Local Setup

### 1) Create and use .venv

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Start server

```bash
uvicorn env.api:app --host 0.0.0.0 --port 8000 --reload
```

### 2b) Start Streamlit dashboard

```bash
python -m streamlit run streamlit_app.py --server.port 8501 --server.headless true
```

Then open: http://localhost:8501

### 3) Run baseline directly

```bash
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced
```

Stress baseline run:

```bash
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona strict_ceo --mode stress --stress-rate 0.5
```

### 4) Run AI mode (LLM-powered decisions)

Prerequisites:

```bash
# Set your OpenAI API key (or use HF_TOKEN for Hugging Face endpoints)
$env:OPENAI_API_KEY = "sk-..."  # Windows PowerShell
export OPENAI_API_KEY="sk-..."   # Linux/Mac
```

Or use the hackathon-required environment variables:

```bash
export API_BASE_URL="https://api.openai.com/v1"
export MODEL_NAME="gpt-4o-mini"
export HF_TOKEN="sk-..."  # Or use OPENAI_API_KEY as fallback
```

Hugging Face Spaces (Azure OpenAI) recommended secrets:

```bash
API_BASE_URL="https://<resource>.openai.azure.com/openai/deployments/<deployment>?api-version=2024-02-15-preview"
MODEL_NAME="gpt-4o"
HF_TOKEN="<azure-api-key>"
APP_API_BASE_URL="http://127.0.0.1:8000"
```

Notes:

- API_BASE_URL must include `/openai/deployments/<deployment>` for Azure with the OpenAI client.
- If `api-version` is omitted, code auto-adds `2024-02-15-preview` (or `AZURE_API_VERSION` if set).
- APP_API_BASE_URL is used by the Streamlit dashboard to call this app's FastAPI endpoints (`/health`, `/tasks`, `/baseline`, etc.).
- Do not set APP_API_BASE_URL to Azure/OpenAI endpoints.
- Keep temperature low (0.1 to 0.2) for stable hackathon runs.

Run AI baseline:

```bash
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced --mode llm
```

### 5) Build leaderboard across seeds/personas

```bash
python -m baseline.leaderboard --tasks easy_classification,medium_prioritization,hard_full_management --personas strict_ceo,balanced,chill_manager --seeds 42,43,44
```

Leaderboard with stress mode and CSV export:

```bash
python -m baseline.leaderboard --tasks hard_full_management --personas strict_ceo,balanced,chill_manager --seeds 42,43,44 --mode stress --stress-rate 0.5 --csv-out artifacts/leaderboard_stress.csv
```

## Tests

Run the test suite:

```bash
python -m pytest -q
```

## Inference Script (Hackathon Submission)

Run the inference script with required environment variables:

```bash
export API_BASE_URL="https://api.openai.com/v1"
export MODEL_NAME="gpt-4o-mini"
export HF_TOKEN="sk-..."

python inference.py
```

The inference script outputs logs in the exact format required:
- `[START] task={task} env={env} model={model}`
- `[STEP] step={step} action={action} reward={reward} done={done} error={error}`
- `[END] success={success} steps={steps} score={score} rewards={rewards}`

## Docker

```bash
docker build -t exec-email-copilot .
docker run -p 8000:8000 exec-email-copilot
```
