from __future__ import annotations

import time

from env.environment import ExecutiveEmailEnv
from env.grader import evaluate_trajectory
from env.policy import BaselinePolicy


class BenchmarkMetrics:
    def __init__(
        self,
        score: float,
        time_ms: int,
        tokens: int,
        cost_usd: float,
    ):
        self.score = score
        self.time_ms = time_ms
        self.tokens = tokens
        self.cost_usd = cost_usd

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "time_ms": self.time_ms,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
        }


class BaseBenchmarkAgent:
    name: str = "BaseBenchmarkAgent"

    def run(
        self,
        task_id: str,
        seed: int,
        persona: str,
        max_steps: int = 100,
    ) -> BenchmarkMetrics:
        raise NotImplementedError


class BaselineAgent(BaseBenchmarkAgent):
    name: str = "baseline"

    def run(
        self,
        task_id: str,
        seed: int,
        persona: str,
        max_steps: int = 100,
    ) -> BenchmarkMetrics:
        env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
        observation = env.reset(task_id=task_id, seed=seed, persona=persona)
        policy = BaselinePolicy()
        trace = []

        start_time = time.time()

        for _ in range(max(1, max_steps)):
            action = policy.next_action(observation)
            if action is None:
                break
            trace.append(action)
            result = env.step(action)
            observation = result.observation
            if result.done:
                break

        elapsed_ms = int((time.time() - start_time) * 1000)
        graded = evaluate_trajectory(task_id=task_id, seed=seed, actions=trace, persona=persona)

        return BenchmarkMetrics(
            score=graded.score,
            time_ms=elapsed_ms,
            tokens=0,
            cost_usd=0.0,
        )


class LLMAgent(BaseBenchmarkAgent):
    name: str = "llm"

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            from env.llm_agent import LLMAgent as _LLM

            self._agent = _LLM(model=self.model)
        return self._agent

    def run(
        self,
        task_id: str,
        seed: int,
        persona: str,
        max_steps: int = 100,
    ) -> BenchmarkMetrics:
        env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
        observation = env.reset(task_id=task_id, seed=seed, persona=persona)
        agent = self._get_agent()
        agent._did_prioritize = False
        trace = []

        start_time = time.time()
        total_tokens = 0

        for _ in range(max(1, max_steps)):
            from env.llm_agent import get_action as llm_get_action

            ai_response = llm_get_action(observation)
            action = ai_response.action
            if action is None:
                break

            if ai_response.trace and ai_response.trace.token_usage:
                total_tokens += ai_response.trace.token_usage.total_tokens

            trace.append(action)
            result = env.step(action)
            observation = result.observation
            if result.done:
                break

        elapsed_ms = int((time.time() - start_time) * 1000)
        graded = evaluate_trajectory(task_id=task_id, seed=seed, actions=trace, persona=persona)

        cost_usd = 0.0
        if total_tokens > 0:
            from env.llm_agent import MODEL_PRICING

            pricing = MODEL_PRICING.get(self.model, {"prompt": 0.15, "completion": 0.60})
            cost_usd = (total_tokens / 1_000_000) * pricing["completion"]

        return BenchmarkMetrics(
            score=graded.score,
            time_ms=elapsed_ms,
            tokens=total_tokens,
            cost_usd=cost_usd,
        )


class MultiAgent(BaseBenchmarkAgent):
    name: str = "multiagent"

    def run(
        self,
        task_id: str,
        seed: int,
        persona: str,
        max_steps: int = 100,
    ) -> BenchmarkMetrics:
        from env.agents.coordinator import CoordinatorAgent

        env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
        observation = env.reset(task_id=task_id, seed=seed, persona=persona)
        coordinator = CoordinatorAgent()
        trace = []

        start_time = time.time()

        for _ in range(max(1, max_steps)):
            action = coordinator.execute(observation)
            if action is None:
                break
            trace.append(action)
            result = env.step(action)
            observation = result.observation
            if result.done:
                break

        elapsed_ms = int((time.time() - start_time) * 1000)
        graded = evaluate_trajectory(task_id=task_id, seed=seed, actions=trace, persona=persona)

        return BenchmarkMetrics(
            score=graded.score,
            time_ms=elapsed_ms,
            tokens=0,
            cost_usd=0.0,
        )
