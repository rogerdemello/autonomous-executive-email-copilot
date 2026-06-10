# Autonomous Executive Email Copilot — A Reproducible Benchmark for Inbox-Triage Agents

> Positioning & methods note. For the formal scoring spec see
> [BENCHMARK.md](BENCHMARK.md); for the system design see
> [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. The problem

"Agentic" email assistants are easy to demo and hard to *measure*. A screenshot of a
model drafting a reply tells you nothing about whether it triages a real executive
inbox well: does it escalate the legal risk, reply to the high-value client before the
deadline, defer the noise, and do so **reproducibly**? Most agent demos are
non-deterministic, un-scored, and un-comparable.

This project is the measurement layer. It is a **deterministic, RL-style
environment** — a `reset → step → state` loop with bounded, numerically stable scoring —
plus the tooling to run policies against it, score them, and compare them. It is a
benchmark first and a product second.

## 2. What it measures

An executive mailbox is simulated with deadlines, business value, risk tags, thread
history, and mid-episode interruptions. An agent chooses among `classify`, `prioritize`,
`reply`, `escalate`, `defer`. Three tasks of increasing difficulty:

| Task | What it isolates |
|------|------------------|
| `easy_classification` | Labeling (spam / normal / urgent) |
| `medium_prioritization`| Ranking under deadlines + business value |
| `hard_full_management` | The realistic job: classify + act + draft + manage risk/time |

Scores are mapped into the **open interval `(0,1)`** (`strict_unit_interval` + an `atan`
reward squash) so they stay numerically stable while preserving ordering. A given
`(task, seed, persona)` always produces the same trajectory and score — determinism is a
tested invariant, not an aspiration.

## 3. Results (real, measured)

Run over a 3×3×3 grid (tasks × personas × seeds); the `llm` agent is **real Azure
OpenAI `gpt-4o`**. Deterministic agents have ≈0 variance.

| Task | Baseline (heuristic) | Multi-agent (task-aware) | LLM — `gpt-4o` |
|------|:---:|:---:|:---:|
| `easy_classification` | **1.00** | 0.80 | 0.17 |
| `medium_prioritization` | **1.00** | **1.00** | **1.00** |
| `hard_full_management` | **0.67** | 0.09 | 0.62 |

The LLM averaged ~3k tokens / **≈ $0.009 per episode** (~$0.23 for the full sweep).

## 4. What the numbers actually say (honest findings)

- **The benchmark discriminates.** A strong hand-written heuristic, a naive multi-agent
  crew, and a frontier LLM separate clearly — and *differently per task*. A benchmark
  where everything scores the same measures nothing.
- **Frontier models shine on the realistic task.** On `hard_full_management`, the LLM
  (0.62) is competitive with a carefully hand-tuned baseline (0.67) and dwarfs the naive
  multi-agent crew (0.09). This is the task that looks like the real job.
- **Narrow tasks punish task-blind agents.** The LLM scores only 0.17 on pure
  classification because its safety guardrails (prioritize-first, auto-escalate risk,
  prefer replies) trade label coverage for caution. That is an **agent-design** finding,
  not a model-capability one — and surfacing it is the point.
- **Personas are currently a no-op for the headline score** (they shape per-step reward
  shaping, not the metric). A documented limitation, not hidden.

These were found by *running the benchmark*, not by tuning for a story — including two
bugs the benchmark exposed (a task-blind coordinator that scored 0, and an LLM agent that
re-acted on the same email forever). Both are documented and fixed.

## 5. How it differs from a typical agent demo

| Typical demo | This benchmark |
|--------------|----------------|
| One happy-path screenshot | 81-cell grid, scored |
| Non-deterministic | Deterministic per `(task, seed, persona)`, drift-guarded by tests |
| No score | Bounded `(0,1)` score with documented weights |
| One model | Heuristic / multi-agent / hybrid / any OpenAI-compatible LLM |
| Claims | Every claim backed by a passing test (285 tests, ~80% coverage) |

## 6. Reproducibility & cost

The deterministic agents run **offline at $0**. The LLM column works with **any
OpenAI-compatible endpoint** — Azure, OpenAI, or a free provider (Groq free tier, local
Ollama) — so the headline table can be reproduced at no cost (see
[BENCHMARK.md §6](BENCHMARK.md)). One command emits `results.json/csv/html` aggregated
with 95% confidence intervals.

## 7. Beyond the benchmark

The same environment is wrapped in a production-style stack — FastAPI + React,
human-in-the-loop approvals, opt-in auth / multi-tenancy / rate limiting, Prometheus
metrics + Grafana panels, a reproducible Docker image, and CI that gates lint, types,
security, coverage, the frontend build, and a container smoke test. The benchmark is the
core; the product is proof the core is built to last.

## 8. Limitations & roadmap

Honest gaps (all documented): persona-invariance of the headline score; one Azure
deployment per base URL (so the small→large model fallback is a no-op on Azure); scenario
breadth (three base scenarios + gated variants); and full per-tenant DB row isolation
(authentication is multi-tenant; storage isolation is a planned migration). These are
tracked, not papered over — which is the whole ethos: **make every claim true and tested.**
