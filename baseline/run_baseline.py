from __future__ import annotations

import argparse
import json
import random

from env.environment import ExecutiveEmailEnv
from env.grader import evaluate_trajectory
from env.llm_policy import LLMPolicy
from env.models import Action, AIDecisionTrace
from env.policy import BaselinePolicy


def _next_wrong_label(label: str) -> str:
    labels = ["spam", "normal", "urgent"]
    for candidate in labels:
        if candidate != label:
            return candidate
    return "normal"


def _apply_stress(action: Action, rng: random.Random, stress_rate: float) -> Action:
    if stress_rate <= 0.0 or rng.random() >= stress_rate:
        return action

    if action.action_type == "classify" and action.label is not None:
        return action.model_copy(update={"label": _next_wrong_label(action.label)})

    if action.action_type == "prioritize" and action.priority_order:
        return action.model_copy(update={"priority_order": list(reversed(action.priority_order))})

    if action.action_type == "reply" and action.email_id is not None:
        return Action(action_type="defer", email_id=action.email_id)

    if action.action_type == "escalate" and action.email_id is not None:
        return action.model_copy(update={"escalate_to": "finance_lead"})

    return action


def run(
    task_id: str,
    seed: int,
    max_steps: int,
    persona: str,
    mode: str = "baseline",
    stress_rate: float = 0.0,
) -> dict[str, object]:
    if mode not in {"baseline", "stress", "llm"}:
        raise ValueError("mode must be baseline, stress, or llm")

    clamped_stress = max(0.0, min(1.0, stress_rate if mode == "stress" else 0.0))
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    observation = env.reset(task_id=task_id, seed=seed, persona=persona)

    if mode == "llm":
        policy = LLMPolicy()
    else:
        policy = BaselinePolicy()
    trace = []
    decision_traces: list[dict] = []  # Store decision traces for llm mode
    rng = random.Random(seed + 9001)

    for _ in range(max(1, max_steps)):
        action = policy.next_action(observation)
        if action is None:
            break
        if mode == "stress":
            action = _apply_stress(action, rng=rng, stress_rate=clamped_stress)
        
        if mode == "llm":
            from env.llm_agent import get_action as llm_get_action
            ai_response = llm_get_action(observation)
            
            email_context = None
            if action.email_id and observation.emails:
                for email in observation.emails:
                    if email.id == action.email_id:
                        email_context = {
                            "id": email.id,
                            "sender": email.sender,
                            "sender_role": email.sender_role,
                            "subject": email.subject,
                            "priority_hint": email.priority_hint,
                            "deadline_minutes": email.deadline_minutes,
                            "business_value": email.business_value,
                            "risk_tag": email.risk_tag,
                        }
                        break
            
            action_info = action.model_dump() if action else None
            
            decision_traces.append({
                "step": len(trace) + 1,
                "email_context": email_context,
                "action": action_info,
                "reason": ai_response.trace.reason,
                "confidence": ai_response.trace.confidence,
                "alternatives_considered": ai_response.trace.alternatives_considered,
                "why_not": ai_response.trace.why_not,
                "latency_ms": ai_response.trace.latency_ms,
                "model_name": ai_response.trace.model_name,
                "status": ai_response.trace.status,
                "token_count": ai_response.trace.token_count,
            })
        
        trace.append(action)
        result = env.step(action)
        observation = result.observation
        if result.done:
            break

    graded = evaluate_trajectory(task_id=task_id, seed=seed, actions=trace, persona=persona)
    output = {
        "task_id": task_id,
        "seed": seed,
        "persona": persona,
        "mode": mode,
        "stress_rate": clamped_stress,
        "steps": len(trace),
        "score": graded.score,
        "total_reward": graded.total_reward,
        "breakdown": graded.breakdown,
        "actions": [a.model_dump() for a in trace],
    }
    
    # Add decision traces for llm mode
    if mode == "llm":
        output["decision_traces"] = decision_traces
    
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline policy")
    parser.add_argument("--task", default="hard_full_management")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--persona", default="balanced", choices=["strict_ceo", "balanced", "chill_manager"])
    parser.add_argument("--mode", default="baseline", choices=["baseline", "stress", "llm"])
    parser.add_argument("--stress-rate", type=float, default=0.0)
    args = parser.parse_args()

    result = run(
        task_id=args.task,
        seed=args.seed,
        max_steps=args.max_steps,
        persona=args.persona,
        mode=args.mode,
        stress_rate=args.stress_rate,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
