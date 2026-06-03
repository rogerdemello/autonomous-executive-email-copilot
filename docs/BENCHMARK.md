# Benchmark Methodology

A research-grade description of how the Autonomous Executive Email Copilot is
evaluated: the task suite, the persona axis, the scoring model, the determinism
guarantees, the agent set, and how to reproduce the numbers. Every claim below is
derived from the code; the relevant file and (where useful) line is cited inline.

For the system map see [ARCHITECTURE.md](ARCHITECTURE.md); for exhaustive API
detail see [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md).

## 1. Task suite

The benchmark fixes three tasks, declared in [`data/tasks.yaml`](../data/tasks.yaml)
and validated into `TaskDefinition` objects by `list_tasks()`
([`env/tasks.py`](../env/tasks.py)). The grader hard-codes these three ids and
raises `ValueError` on any other id (`env/grader.py`, `_compute_score` and the
breakdown branch in `evaluate_trajectory`), so the task set is closed by design.

| Task id | Name | Difficulty | What it measures |
|---------|------|------------|------------------|
| `easy_classification` | Email Classification | easy | Per-email label correctness (`spam` / `normal` / `urgent`). |
| `medium_prioritization` | Inbox Prioritization | medium | Ranking quality of an inbox under deadlines and business value. |
| `hard_full_management` | Full Email Management | hard | End-to-end inbox handling: classify, choose an action, draft replies, manage risk and time. |

### What each task scores

The final task score is computed by `_compute_score(metrics, task_id)` in
[`env/grader.py`](../env/grader.py) from the environment's `metrics()` dict
([`env/environment.py`](../env/environment.py), lines 149-187):

- **`easy_classification`** -> `metrics["classification_accuracy"]`.
  `classification_accuracy` is the fraction of emails whose `predicted_label`
  matches `expected_label` over all emails in the inbox
  (`environment.py` lines 150-153).

- **`medium_prioritization`** -> `metrics["prioritization"]`.
  `prioritization` is the *best* `ranking_similarity` observed across the episode
  (`environment.py` `_handle_prioritize`, line 256 tracks
  `_best_priority_similarity`). Similarity is Kendall's tau between the agent's
  `priority_order` and the gold order, remapped from `[-1, 1]` to `[0, 1]`
  (`env/utils.py` `ranking_similarity`, using `scipy.stats.kendalltau`). The gold
  order is produced by `compute_gold_priority_order` (`env/utils.py`, lines 72-93),
  a deterministic weighting of urgency (priority hint + deadline), business value,
  and sender-role importance.

- **`hard_full_management`** -> a fixed convex combination of three metrics
  (`env/grader.py`, lines 31-36):

  ```
  score = 0.30 * classification_accuracy
        + 0.30 * action_correctness
        + 0.40 * response_quality
  ```

  - `action_correctness`: over emails whose `expected_action != "ignore"`, the
    fraction whose `handled_action == expected_action`
    (`environment.py` lines 155-159).
  - `response_quality` (keyed `response_quality` in the metrics dict, sourced from
    `reply_quality`): mean keyword-coverage of replies over emails whose
    `expected_action == "reply"`; an unanswered reply target contributes `0.0`
    (`environment.py` lines 161-172; `reply_keyword_score` in `env/utils.py`).

`hard_full_management` also reports `prioritization` and `resolved_ratio` in its
breakdown (see Section 3.3) but does not weight them into the headline score.

### Reward vs. score

`metrics()`/score is the *outcome* measure. Separately, the environment emits a
per-step **reward** in `step()` (`environment.py` lines 95-147), shaped by action
correctness, deadline penalties (`_apply_deadline_penalties`), and an end-of-episode
`_terminal_penalty` for unresolved urgent/critical emails. The cumulative
`total_reward` is reported by the grader after a monotonic squash (Section 3.4).
Reward shaping is persona-sensitive; score is not (Section 2).

## 2. Persona axis

Three personas form the second evaluation axis, declared as
`PersonaType = Literal["strict_ceo", "balanced", "chill_manager"]`
([`env/models.py`](../env/models.py), line 12) and parameterized in
[`data/settings.yaml`](../data/settings.yaml) under `persona_profiles`. Each persona
maps to a `PersonaProfile` of four multipliers loaded by `get_persona_profile`
(`env/utils.py`, lines 17-23 and 65-69):

| Multiplier | strict_ceo | balanced | chill_manager | Applied in (`environment.py`) |
|------------|-----------:|---------:|--------------:|-------------------------------|
| `deadline_penalty_multiplier` | 1.35 | 1.00 | 0.65 | `_apply_deadline_penalties` (missed urgent deadline) |
| `terminal_penalty_multiplier` | 1.35 | 1.00 | 0.70 | `_terminal_penalty` (unresolved urgent/critical at episode end) |
| `urgent_defer_penalty_multiplier` | 1.20 | 1.00 | 0.85 | `_handle_defer` (deferring an urgent email) |
| `redundant_penalty_multiplier` | 1.10 | 1.00 | 0.90 | redundant/invalid actions across handlers |

An unknown persona falls back to `balanced` (`get_persona_profile`, lines 67-68).

**Important scope of the persona axis.** Personas modulate *reward shaping and
penalties only*. The headline task **score** is computed from outcome `metrics()`
(label/action/reply/ranking correctness), none of which read the persona profile.
Consequently persona changes move `total_reward` but, for an otherwise identical
trajectory, leave `score` unchanged. The persona therefore tests how strongly an
agent is punished for the same mistakes, not how the mistakes are tallied. (The
persona is still threaded through `build_scenario` and stored on the `Scenario`
for traceability, `env/tasks.py` lines 63-100.)

## 3. Scoring model

All scores and breakdown metrics are bounded into the **open** interval `(0, 1)` to
satisfy strict OpenEnv validators that reject exact `0.0`/`1.0`. The contract and
its rationale are documented in the `env/grader.py` module docstring (lines 1-15).

### 3.1 `strict_unit_interval`: the open-interval map

`strict_unit_interval(value, epsilon=1e-6)` (`env/utils.py`, lines 60-62) first
clips to `[0, 1]` via `clip_score` (non-finite inputs map to the low bound), then
pulls the endpoints inward by `epsilon`:

```
bounded = clip_score(value)                      # -> [0, 1]
return min(1.0 - epsilon, max(epsilon, bounded)) # -> [epsilon, 1 - epsilon]
```

With `epsilon = 1e-6` the reachable range is `[1e-6, 0.999999]`. This is applied to:

- every metric in `environment.metrics()` (lines 181-187),
- the final task `score` (`grader.py` line 139),
- every breakdown entry (`grader.py` line 140).

### 3.2 Per-task metric weights (from `grader.py`)

The exact weighting used to collapse metrics into the headline score, taken
verbatim from `_compute_score` (`env/grader.py`, lines 26-37):

| Task | classification_accuracy | action_correctness | response_quality | prioritization |
|------|------------------------:|-------------------:|-----------------:|---------------:|
| `easy_classification` | 1.00 | - | - | - |
| `medium_prioritization` | - | - | - | 1.00 |
| `hard_full_management` | 0.30 | 0.30 | 0.40 | - |

These weights are the *only* place the three component metrics are combined; there
are no hidden coefficients. `resolved_ratio` is reported but never weighted.

### 3.3 Breakdown payloads

`evaluate_trajectory` returns a per-task breakdown (each value passed through
`strict_unit_interval` and rounded to 6 dp), `env/grader.py` lines 119-148:

- `easy_classification`: `{classification_accuracy}`
- `medium_prioritization`: `{prioritization, resolved_ratio}`
- `hard_full_management`: `{classification_accuracy, action_correctness,
  response_quality, prioritization, resolved_ratio}`

It also returns a `step_breakdown` (`StepScoreBreakdown` per action) recording the
per-step `score_delta` (the change in `_compute_score` across the step) and a
human-readable `reason` from `_generate_reason` (lines 46-74).

### 3.4 The atan reward squash (`total_reward`)

`total_reward` is an unbounded cumulative sum of per-step rewards, so it cannot use
the clip-based map directly. `_normalize_reward` (`env/grader.py`, lines 40-43)
squashes it with a strictly increasing arctangent map before the same open-interval
clamp:

```
mapped = 0.5 + (atan(value) / pi)   # strictly increasing R -> (0, 1)
return strict_unit_interval(mapped) # -> [epsilon, 1 - epsilon]
```

Because `atan` is monotonic, ordering between trajectories is preserved: a higher
cumulative reward always yields a higher normalized `total_reward`. Only the
absolute scale is bounded. A cumulative reward of `0` maps to exactly `0.5`;
positive rewards map above `0.5`, negative below. The reported `total_reward` is
this squashed value rounded to 6 dp (`grader.py` line 149).

### 3.5 Per-step reward shaping (reference)

For completeness, the raw step rewards (before squashing) come from
`environment.step()` and its handlers. Each step's reward is clamped to `[-1, 1]`
(`environment.py` line 128) and accumulated. Salient terms:

- correct `classify` `+0.2`; classifying as `spam` resolves the email
  (`_handle_classify`).
- `prioritize` `+0.3 * similarity` (`_handle_prioritize`).
- `reply` `+0.5 * keyword_quality`, minus `0.2` (or `1.0` if `critical`) when the
  email did not want a reply (`_handle_reply`).
- correct `escalate` `+0.4` (`+0.1` if `escalate_to` matches the recommended
  target); wrong escalation of a critical reply target `-0.3` (`_handle_escalate`).
- correct `defer` `+0.1`; deferring an urgent email
  `-0.7 * urgent_defer_penalty_multiplier` (`_handle_defer`).
- per-email missed urgent deadline `-0.7 * deadline_penalty_multiplier`, charged
  once per email (`_apply_deadline_penalties`).
- terminal: `-0.5` per unresolved urgent and `-0.4` per unresolved
  critical legal/security email, scaled by `terminal_penalty_multiplier`
  (`_terminal_penalty`).

Redundant or malformed actions incur `-0.1 * redundant_penalty_multiplier`.

## 4. Determinism guarantees

A benchmark cell is the triple **(task, seed, persona)** plus an agent. Given a
fixed triple, scenario construction is deterministic:

- `build_scenario(task_id, seed, persona)` seeds a private
  `random.Random(seed)` and uses it for synthetic scenario generation, email
  shuffling, and interruption trigger-minute sampling
  (`env/tasks.py`, lines 63-100). No global RNG is touched.
- The environment copies scenario state on `reset()` and replays actions
  deterministically; `metrics()` and the grader are pure functions of state
  (`env/environment.py`).
- The grader instantiates its *own* environment from `(task_id, seed, persona)`
  and replays the supplied action list, so grading is reproducible independent of
  how the trajectory was generated (`env/grader.py`, `evaluate_trajectory`,
  lines 77-83).

This determinism is asserted by the existing suite (e.g.
`tests/test_grading_rigor.py`, referenced in
[ARCHITECTURE.md](ARCHITECTURE.md) invariants): a given `(task, seed, persona)`
always produces the same baseline trajectory and score.

The default evaluation grid (`benchmark/runner.py`, lines 11-23) is:

- tasks: `easy_classification`, `medium_prioritization`, `hard_full_management`
- personas: `strict_ceo`, `balanced`, `chill_manager`
- seeds: `42`, `43`, `44`

This yields `3 x 3 x 3 = 27` cells per agent. Seeds are the only source of
variation within a (task, persona) pair; the 3-seed sample is what the Results
table summarizes (mean and CI). Stochasticity in scenario layout therefore enters
only through the seed; the LLM and multi-agent decision processes may add their own
sampling variance at inference time (see Section 5).

## 5. Agent set

The runner evaluates three agents, constructed in `BenchmarkRunner.__init__`
(`benchmark/runner.py`, lines 66-68) and listed in `run_all` (lines 72-76). All
three share the same loop shape: reset the env, step until `done` or `max_steps`,
then grade the collected trajectory with `evaluate_trajectory`
(`benchmark/agents.py`).

| Agent (`name`) | Implementation | Decision source | Tokens / cost reported |
|----------------|----------------|-----------------|------------------------|
| `baseline` | `BaselineAgent` | `env.policy.BaselinePolicy` (deterministic heuristic) | `tokens=0`, `cost_usd=0.0` |
| `llm` | `LLMAgent` | `env.llm_agent` (`get_action`, default model `gpt-4o-mini`) | real token sum; cost via `MODEL_PRICING` |
| `multiagent` | `MultiAgent` | `env.agents.coordinator.CoordinatorAgent` | `tokens=0`, `cost_usd=0.0` |

Notes derived from `benchmark/agents.py`:

- **Baseline heuristic.** Pure rule-based policy; no model calls. It is the
  determinism anchor and the zero-cost reference (`BaselineAgent.run`, lines 45-80).
- **LLM agent.** Calls `env.llm_agent.get_action(observation)` each step and sums
  `trace.token_usage.total_tokens` (`LLMAgent.run`, lines 97-145). Cost is computed
  as `(total_tokens / 1_000_000) * pricing["completion"]` using
  `MODEL_PRICING` (`env/llm_agent.py`, lines 167-170; `gpt-4o-mini` ->
  `{prompt: 0.15, completion: 0.60}` USD per 1M tokens). **Caveat:** the benchmark's
  cost estimate multiplies *all* tokens by the *completion* rate rather than
  splitting prompt vs. completion (the env's own `_estimate_cost` in
  `env/llm_agent.py` lines 118-121 does split them); treat benchmark `cost_usd` as
  an upper-bound proxy.
- **Multi-agent.** A `CoordinatorAgent` orchestrates sub-agents
  (`MultiAgent.run`, lines 148-185). It currently reports `tokens=0` /
  `cost_usd=0.0`; if its sub-agents make billable calls, token/cost accounting is
  not yet surfaced here.

#### Finding: task-blind coordination floored the multi-agent on `easy_classification`

An early benchmark run surfaced a coordination bug worth recording. The
`CoordinatorAgent` resolved conflicts with a *static* priority order
(`EscalatorAgent > ClassifierAgent > ResponderAgent`). On `easy_classification` the
synthetic inbox legitimately contains risk-tagged emails (e.g. `legal`/`security`
tags), so `EscalatorAgent.can_handle` returns `True` for those emails. The static
order therefore made the coordinator *escalate* on a task whose headline score is
purely `classification_accuracy` (Section 3.2) — producing no `classify` actions and
flooring the score at the open-interval epsilon (~`1e-6`), while the single-agent
`baseline` scored ~`1.0` on the same cells. The multi-agent was being penalised not
for bad classification but for solving the wrong sub-problem.

The coordinator is now **task-aware**: it is told which benchmark task it is solving
(`CoordinatorAgent(task_id=...)`, threaded from `MultiAgent.run`) and biases conflict
resolution per task — preferring `ClassifierAgent` on `easy_classification` /
`medium_prioritization`, and emitting an opening `prioritize` ranking on
`medium_prioritization` (which no content specialist would otherwise produce). On
`hard_full_management` it retains the original **risk-first** ordering, so genuine
legal/security risk is still escalated. This threading does **not** change the env
API or the validator-facing `Observation` schema (which still carries no `task_id`);
the task is passed to the coordinator out-of-band. A secondary fix gives the
coordinator a memory of the `(action_type, email_id)` pairs it has already emitted so
stateless specialists no longer re-propose the same email every step. Covered by
`tests/test_multiagent_taskaware.py`.

`max_steps` defaults to `100` in the runner (`runner.py` line 60) and `120` in the
API-level `LeaderboardRequest` (`env/models.py` line 220); each agent caps its loop
at `max(1, max_steps)` steps.

## 6. Reproducing results

The full matrix is driven by `BenchmarkRunner.run_all()`, which iterates
tasks x personas x seeds x agents and returns one `BenchmarkResult` per cell
(`benchmark/runner.py`, lines 70-97). `BenchmarkResult.to_dict()` exposes
`task_id, persona, seed, agent_name, score, time_ms, tokens, cost_usd`
(lines 41-51). The `Reporter` aggregates these into JSON or HTML, computing
per-agent averages of score/time/tokens/cost (`benchmark/reporter.py`).

### Default full run

```python
from benchmark.runner import BenchmarkRunner
from benchmark.reporter import Reporter

runner = BenchmarkRunner()          # 3 tasks x 3 personas x 3 seeds x 3 agents = 81 runs
results = runner.run_all()

reporter = Reporter(runner)
open("benchmark_results.json", "w").write(reporter.generate_json(results))
open("benchmark_results.html", "w").write(reporter.generate_html(results))
```

### A single agent or a narrowed matrix

```python
# One agent across the default matrix:
results = BenchmarkRunner().run_agent("baseline")   # "baseline" | "llm" | "multiagent"

# Custom grid (e.g. smoke test):
runner = BenchmarkRunner(
    tasks=["easy_classification"],
    personas=["balanced"],
    seeds=[42],
)
results = runner.run_all()          # 1 x 1 x 1 x 3 agents = 3 runs
```

### Notes for live (Azure OpenAI) runs

- The `llm` agent requires provider credentials; configure the OpenAI/Azure client
  used by `env/llm_agent.py` before running. The baseline and multi-agent agents
  run offline.
- The default agent model is `gpt-4o-mini` (`data/settings.yaml` `agent_config`,
  and `LLMAgent` default in `benchmark/agents.py` line 86). To price `gpt-4o`, the
  rate `{prompt: 2.50, completion: 10.00}` is already in `MODEL_PRICING`.
- `time_ms` is wall-clock and therefore environment-dependent; it is reported but
  not part of the score.

### Verifying the harness

```powershell
python -m pytest tests/test_benchmark.py -q
```

`tests/test_benchmark.py` pins the default tasks/personas/seeds, the agent names,
the metrics shape, and the reporter JSON/HTML output.

## 7. Results

Measured run over a 3×3×3 grid (3 tasks × 3 personas × 3 seeds = 27 cells/agent).
The `llm` agent is **real Azure OpenAI `gpt-4o`** (deployment `gpt-4o`, API version
`2024-12-01-preview`, `temperature=0.2`); `baseline` and `multiagent` are deterministic.
Each cell aggregates the 3 seeds into `mean_score`, a 95% CI half-width (`ci95`),
`mean_tokens`, and `mean_cost_usd`.

**Headline (mean over personas):** `baseline` 1.00 / 1.00 / 0.67, `multiagent`
0.80 / 1.00 / 0.09, `llm` 0.17 / 1.00 / 0.62 on easy / medium / hard. The LLM is
competitive with the heuristic baseline on the realistic full-management task and far
ahead of the naive multi-agent there, but its task-blind safety guardrails
(prioritize-first, auto-escalate risk, prefer replies) cost it coverage on the narrow
classification task. Scores are persona-invariant (personas affect per-step reward
shaping, not the headline metric — Section 4). The full LLM sweep cost ≈ $0.23.

| task | persona | agent | mean_score | ci95 | mean_tokens | mean_cost_usd |
|------|---------|-------|-----------:|-----:|------------:|--------------:|
| easy_classification | strict_ceo | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| easy_classification | strict_ceo | llm | 0.167 | ±0.000 | 2080 | $0.00614 |
| easy_classification | strict_ceo | multiagent | 0.800 | ±0.000 | 0 | $0.00000 |
| easy_classification | balanced | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| easy_classification | balanced | llm | 0.167 | ±0.000 | 2072 | $0.00612 |
| easy_classification | balanced | multiagent | 0.800 | ±0.000 | 0 | $0.00000 |
| easy_classification | chill_manager | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| easy_classification | chill_manager | llm | 0.167 | ±0.000 | 2076 | $0.00613 |
| easy_classification | chill_manager | multiagent | 0.800 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | strict_ceo | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | strict_ceo | llm | 1.000 | ±0.000 | 2264 | $0.00660 |
| medium_prioritization | strict_ceo | multiagent | 1.000 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | balanced | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | balanced | llm | 1.000 | ±0.000 | 2261 | $0.00662 |
| medium_prioritization | balanced | multiagent | 1.000 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | chill_manager | baseline | 1.000 | ±0.000 | 0 | $0.00000 |
| medium_prioritization | chill_manager | llm | 1.000 | ±0.000 | 2264 | $0.00662 |
| medium_prioritization | chill_manager | multiagent | 1.000 | ±0.000 | 0 | $0.00000 |
| hard_full_management | strict_ceo | baseline | 0.669 | ±0.067 | 0 | $0.00000 |
| hard_full_management | strict_ceo | llm | 0.588 | ±0.149 | 4565 | $0.01333 |
| hard_full_management | strict_ceo | multiagent | 0.086 | ±0.000 | 0 | $0.00000 |
| hard_full_management | balanced | baseline | 0.669 | ±0.067 | 0 | $0.00000 |
| hard_full_management | balanced | llm | 0.657 | ±0.086 | 4553 | $0.01332 |
| hard_full_management | balanced | multiagent | 0.086 | ±0.000 | 0 | $0.00000 |
| hard_full_management | chill_manager | baseline | 0.669 | ±0.067 | 0 | $0.00000 |
| hard_full_management | chill_manager | llm | 0.601 | ±0.061 | 4551 | $0.01325 |
| hard_full_management | chill_manager | multiagent | 0.086 | ±0.000 | 0 | $0.00000 |

Column definitions:

- **task / persona / agent**: the benchmark cell (Sections 1, 2, 5).
- **mean_score**: mean of the open-interval `(0,1)` task `score` (Section 3) over
  the seed sample for that cell.
- **ci95**: half-width of the 95% confidence interval of `mean_score` over seeds.
- **mean_tokens**: mean `tokens` reported by the agent (0 for `baseline` /
  `multiagent`).
- **mean_cost_usd**: mean `cost_usd` (Section 5 caveat applies to the `llm` agent).
