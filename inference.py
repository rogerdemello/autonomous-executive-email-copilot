#!/usr/bin/env python3
"""Inference script for Executive Email Copilot."""

import os
import sys

from openai import OpenAI

from env.environment import ExecutiveEmailEnv
from env.models import Action


def main(task: str = "hard_full_management", max_steps: int = 100):
    """Run inference on the Executive Email environment.
    
    Args:
        task: Task ID (easy_classification, medium_prioritization, hard_full_management)
        max_steps: Maximum number of steps to run
    """
    # Configuration from environment variables
    api_base_url = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
    model_name = os.environ.get("MODEL_NAME", "gpt-4o-mini")
    hf_token = os.environ.get("HF_TOKEN", os.environ.get("OPENAI_API_KEY"))
    
    # Initialize environment
    env = ExecutiveEmailEnv(task_id=task, seed=42, persona="balanced")
    observation = env.reset()
    
    print(f"[START] task={task} env=local model={model_name}")
    
    # Initialize OpenAI client
    if not hf_token:
        print("[END] success=False steps=0 score=0.0 rewards=0.0", file=sys.stderr)
        print("Error: No API key found. Set HF_TOKEN or OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)
    
    client = OpenAI(
        base_url=api_base_url,
        api_key=hf_token,
    )
    
    step_count = 0
    total_reward = 0.0
    
    while step_count < max_steps:
        # Check if done
        state = env.state()
        if env._is_done():
            break
        
        # Get current observation
        obs = env._build_observation()
        
        # Build prompt for the LLM
        prompt = _build_prompt(obs)
        
        # Call LLM to get action
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are an executive email assistant. Analyze the inbox and take appropriate actions."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500,
            )
            action_text = response.choices[0].message.content
            if action_text is None:
                action_text = ""
            action = _parse_action(action_text, obs)
        except Exception as e:
            # Log error and continue
            print(f"[STEP] step={step_count} action=none reward=0.0 done=False error={str(e)}")
            step_count += 1
            continue
        
        # Execute action
        result = env.step(action)
        
        # Log step
        action_str = f"{action.action_type}"
        if action.email_id:
            action_str += f":{action.email_id}"
        error_str = result.info.get("error", "none")
        
        print(f"[STEP] step={step_count} action={action_str} reward={result.reward} done={result.done} error={error_str}")
        
        step_count += 1
        total_reward += result.reward
        
        if result.done:
            break
    
    # Calculate final score
    metrics = env.metrics()
    score = (
        0.3 * metrics.get("classification_accuracy", 0.0) +
        0.3 * metrics.get("action_correctness", 0.0) +
        0.4 * metrics.get("response_quality", 0.0)
    )
    
    # Log end
    success = score >= 0.5
    print(f"[END] success={success} steps={step_count} score={score:.4f} rewards={total_reward:.4f}")
    
    return score


def _build_prompt(observation) -> str:
    """Build a prompt from the current observation."""
    emails = observation.emails
    time_remaining = observation.time_remaining
    pending = observation.pending_actions
    
    prompt = f"Time remaining: {time_remaining} minutes\n\n"
    prompt += f"Pending actions: {', '.join(pending) if pending else 'none'}\n\n"
    prompt += "Emails:\n"
    
    for email in emails:
        prompt += f"- ID: {email.id}\n"
        prompt += f"  From: {email.sender} ({email.sender_role})\n"
        prompt += f"  Subject: {email.subject}\n"
        prompt += f"  Priority: {email.priority_hint}\n"
        prompt += f"  Deadline: {email.deadline_minutes} min\n"
        prompt += f"  Business value: {email.business_value}\n"
        prompt += f"  Risk: {email.risk_tag}\n"
        if email.thread_history:
            prompt += f"  Thread: {len(email.thread_history)} messages\n"
        prompt += "\n"
    
    prompt += "Available actions:\n"
    prompt += "- classify: Classify an email as spam, normal, or urgent\n"
    prompt += "- prioritize: Set priority order for emails\n"
    prompt += "- reply: Send a reply to an email\n"
    prompt += "- escalate: Escalate an email to a team\n"
    prompt += "- defer: Defer an email for later\n"
    
    prompt += "\nRespond with your action in JSON format:\n"
    prompt += '{"action_type": "classify|reply|defer|escalate|prioritize", "email_id": "...", ...}'
    
    return prompt


def _parse_action(action_text: str, observation) -> Action:
    """Parse action from LLM response."""
    import json
    
    try:
        # Try to parse as JSON
        data = json.loads(action_text)
        action_type = data.get("action_type", "classify")
        email_id = data.get("email_id")
        label = data.get("label")
        content = data.get("content")
        priority_order = data.get("priority_order", [])
        escalate_to = data.get("escalate_to")
        
        return Action(
            action_type=action_type,
            email_id=email_id,
            label=label,
            content=content,
            priority_order=priority_order,
            escalate_to=escalate_to,
        )
    except (json.JSONDecodeError, KeyError):
        # Default action if parsing fails
        obs_emails = observation.emails
        email_id = obs_emails[0].id if obs_emails else None
        
        return Action(
            action_type="classify",
            email_id=email_id,
            label="normal",
        )


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run inference on Executive Email Copilot")
    parser.add_argument("--task", type=str, default="hard_full_management",
                        choices=["easy_classification", "medium_prioritization", "hard_full_management"],
                        help="Task to run")
    parser.add_argument("--max-steps", type=int, default=100,
                        help="Maximum number of steps")
    
    args = parser.parse_args()
    
    main(task=args.task, max_steps=args.max_steps)
