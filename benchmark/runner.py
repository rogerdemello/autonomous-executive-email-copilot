from __future__ import annotations

from .agents import (
    BaseBenchmarkAgent,
    BaselineAgent,
    BenchmarkMetrics,
    LLMAgent,
    MultiAgent,
)


DEFAULT_TASKS = [
    "easy_classification",
    "medium_prioritization",
    "hard_full_management",
]

DEFAULT_PERSONAS = [
    "strict_ceo",
    "balanced",
    "chill_manager",
]

DEFAULT_SEEDS = [42, 43, 44]


class BenchmarkResult:
    def __init__(
        self,
        task_id: str,
        persona: str,
        seed: int,
        agent_name: str,
        metrics: BenchmarkMetrics,
    ):
        self.task_id = task_id
        self.persona = persona
        self.seed = seed
        self.agent_name = agent_name
        self.metrics = metrics

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "persona": self.persona,
            "seed": self.seed,
            "agent_name": self.agent_name,
            "score": self.metrics.score,
            "time_ms": self.metrics.time_ms,
            "tokens": self.metrics.tokens,
            "cost_usd": self.metrics.cost_usd,
        }


class BenchmarkRunner:
    def __init__(
        self,
        tasks: list[str] | None = None,
        personas: list[str] | None = None,
        seeds: list[int] | None = None,
        max_steps: int = 100,
    ):
        self.tasks = tasks or DEFAULT_TASKS
        self.personas = personas or DEFAULT_PERSONAS
        self.seeds = seeds or DEFAULT_SEEDS
        self.max_steps = max_steps
        self.baseline_agent = BaselineAgent()
        self.llm_agent = LLMAgent()
        self.multiagent = MultiAgent()

    def run_all(self) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        agents: list[BaseBenchmarkAgent] = [
            self.baseline_agent,
            self.llm_agent,
            self.multiagent,
        ]

        for task_id in self.tasks:
            for persona in self.personas:
                for seed in self.seeds:
                    for agent in agents:
                        metrics = agent.run(
                            task_id=task_id,
                            seed=seed,
                            persona=persona,
                            max_steps=self.max_steps,
                        )
                        result = BenchmarkResult(
                            task_id=task_id,
                            persona=persona,
                            seed=seed,
                            agent_name=agent.name,
                            metrics=metrics,
                        )
                        results.append(result)

        return results

    def run_agent(
        self,
        agent_name: str,
    ) -> list[BenchmarkResult]:
        if agent_name == "baseline":
            agent = self.baseline_agent
        elif agent_name == "llm":
            agent = self.llm_agent
        elif agent_name == "multiagent":
            agent = self.multiagent
        else:
            raise ValueError(f"Unknown agent: {agent_name}")

        results: list[BenchmarkResult] = []

        for task_id in self.tasks:
            for persona in self.personas:
                for seed in self.seeds:
                    metrics = agent.run(
                        task_id=task_id,
                        seed=seed,
                        persona=persona,
                        max_steps=self.max_steps,
                    )
                    result = BenchmarkResult(
                        task_id=task_id,
                        persona=persona,
                        seed=seed,
                        agent_name=agent_name,
                        metrics=metrics,
                    )
                    results.append(result)

        return results