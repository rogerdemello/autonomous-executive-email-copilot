from __future__ import annotations

import copy
import random
import threading
from pathlib import Path
from typing import Any

import yaml

_lock = threading.Lock()
_cache: dict[Path, tuple[int, int, Any]] = {}


# =============================================================================
# Synthetic Scenario Combinators
# =============================================================================

# Adversarial email wording patterns for subtle manipulation
ADVERSARIAL_PATTERNS: dict[str, list[dict[str, str]]] = {
    "urgency_manipulation": [
        {
            "pattern": "Need it ASAP",
            "replacement": "Need it by end of day",
            "subtle_variant": "Need it when you get a chance",
        },
        {"pattern": "URGENT", "replacement": "Important", "subtle_variant": "Time-sensitive"},
        {
            "pattern": "Immediate attention required",
            "replacement": "Please review when available",
            "subtle_variant": "Kindly review at your convenience",
        },
    ],
    "social_proof": [
        {
            "pattern": "Everyone is waiting",
            "replacement": "Several team members are ready",
            "subtle_variant": "The team is prepared to proceed",
        },
        {
            "pattern": "As discussed",
            "replacement": "Following up on",
            "subtle_variant": "Building on our previous conversation",
        },
    ],
    "artificial_deadline": [
        {
            "pattern": "Must respond today",
            "replacement": "Please respond when possible",
            "subtle_variant": "Response appreciated this week",
        },
        {
            "pattern": "Deadline is tomorrow",
            "replacement": "Target response: end of week",
            "subtle_variant": "Ideally would appreciate response by Friday",
        },
    ],
    "guilt_trip": [
        {
            "pattern": "I've been waiting",
            "replacement": "Following up",
            "subtle_variant": "Checking in on this",
        },
        {
            "pattern": "No one else can do this",
            "replacement": "Your expertise would be valuable",
            "subtle_variant": "Your input would be helpful",
        },
    ],
    "false_authority": [
        {
            "pattern": "The CEO wants",
            "replacement": "Leadership is interested",
            "subtle_variant": "Senior leadership has requested",
        },
        {
            "pattern": "Legal requires",
            "replacement": "Compliance recommends",
            "subtle_variant": "We should consider",
        },
    ],
}

# Conflicting deadline scenarios
CONFLICTING_DEADLINE_TEMPLATES: list[dict[str, Any]] = [
    {
        "description": "Two urgent items with overlapping deadlines",
        "email_a": {"deadline_minutes": 45, "priority_hint": "high", "business_value": 0.9},
        "email_b": {"deadline_minutes": 30, "priority_hint": "high", "business_value": 0.85},
    },
    {
        "description": "Critical deadline vs important deadline",
        "email_a": {"deadline_minutes": 60, "priority_hint": "high", "business_value": 0.98},
        "email_b": {"deadline_minutes": 90, "priority_hint": "medium", "business_value": 0.7},
    },
    {
        "description": "Multiple items due within same hour",
        "emails": [
            {"deadline_minutes": 45, "priority_hint": "high"},
            {"deadline_minutes": 50, "priority_hint": "high"},
            {"deadline_minutes": 55, "priority_hint": "medium"},
        ],
    },
]


def apply_adversarial_pattern(
    text: str,
    pattern_type: str,
    rng: random.Random,
    apply_subtle: bool = False,
) -> str:
    """Apply adversarial wording pattern to text."""
    patterns = ADVERSARIAL_PATTERNS.get(pattern_type, [])
    if not patterns:
        return text

    entry = rng.choice(patterns)
    if apply_subtle and entry.get("subtle_variant"):
        replacement = entry["subtle_variant"]
    else:
        replacement = entry.get("replacement", entry["pattern"])

    return text.replace(entry["pattern"], replacement)


def generate_conflicting_deadlines(
    scenario: dict[str, Any],
    difficulty: str,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate conflicting deadline scenarios based on difficulty."""
    if difficulty == "easy":
        return []

    conflicts = []
    num_conflicts = 1 if difficulty == "medium" else rng.randint(1, 2)

    for _ in range(num_conflicts):
        template = rng.choice(CONFLICTING_DEADLINE_TEMPLATES)
        conflict = {
            "description": template["description"],
            "type": "conflicting_deadline",
        }

        if "emails" in template:
            conflict["emails"] = template["emails"]
        else:
            conflict["email_a"] = template["email_a"].copy()
            conflict["email_b"] = template["email_b"].copy()

        conflicts.append(conflict)

    return conflicts


def generate_interruptions(
    base_scenario: dict[str, Any],
    difficulty: str,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate random mid-episode interruptions based on difficulty."""
    existing_interruptions = base_scenario.get("interruptions", [])

    if difficulty == "easy":
        # No additional interruptions for easy
        return existing_interruptions

    # Base interruption probability
    base_prob = 0.3 if difficulty == "medium" else 0.5

    # Generate additional interruptions
    additional = []
    max_additional = 0 if difficulty == "medium" else rng.randint(0, 2)

    for _ in range(max_additional):
        if rng.random() < base_prob:
            trigger_range = [
                rng.randint(30, 60) if difficulty == "medium" else rng.randint(15, 45),
                rng.randint(60, 90) if difficulty == "medium" else rng.randint(45, 75),
            ]
            additional.append(
                {
                    "trigger_minute_range": trigger_range,
                    "email": {
                        "id": f"int_{rng.randint(1000, 9999)}",
                        "sender": rng.choice(
                            [
                                "colleague@company.com",
                                "manager@company.com",
                                "vendor@external.com",
                                "client@partner.com",
                            ]
                        ),
                        "sender_role": rng.choice(["internal", "internal", "vendor", "client"]),
                        "subject": rng.choice(
                            [
                                "Quick question",
                                "Follow-up on earlier item",
                                "Status check",
                                "Request for input",
                            ]
                        ),
                        "body": rng.choice(
                            [
                                "Can you take a quick look?",
                                "Need your thoughts when available.",
                                "Checking on next steps.",
                                "Would appreciate your input.",
                            ]
                        ),
                        "priority_hint": rng.choice(["low", "medium", "high"]),
                        "deadline_minutes": rng.choice([30, 60, 120, 240]),
                        "business_value": round(rng.uniform(0.4, 0.8), 2),
                        "risk_tag": rng.choice(["none", "ops", "finance"]),
                    },
                }
            )

    return existing_interruptions + additional


def scale_difficulty(
    base_scenario: dict[str, Any],
    difficulty: str,
    rng: random.Random,
) -> dict[str, Any]:
    """Scale scenario complexity based on difficulty level."""
    scenario = copy.deepcopy(base_scenario)

    if difficulty == "hard":
        # Add adversarial patterns to existing emails
        emails = scenario.get("emails", [])
        for email in emails:
            if rng.random() < 0.3:  # 30% chance of adversarial pattern
                pattern_type = rng.choice(list(ADVERSARIAL_PATTERNS.keys()))
                # Apply to subject
                if "subject" in email:
                    email["subject"] = apply_adversarial_pattern(
                        email["subject"], pattern_type, rng, apply_subtle=True
                    )
                # Apply to body
                if "body" in email:
                    email["body"] = apply_adversarial_pattern(
                        email["body"], pattern_type, rng, apply_subtle=True
                    )

        # Reduce time budget slightly for hard
        current_budget = scenario.get("time_budget", 180)
        scenario["time_budget"] = int(current_budget * 0.85)

    elif difficulty == "medium":
        # Reduce time budget moderately
        current_budget = scenario.get("time_budget", 180)
        scenario["time_budget"] = int(current_budget * 0.92)

    # Add conflicting deadlines for medium/hard
    if difficulty in ("medium", "hard"):
        conflicts = generate_conflicting_deadlines(scenario, difficulty, rng)
        scenario["conflicting_deadlines"] = conflicts

    # Generate interruptions
    scenario["interruptions"] = generate_interruptions(scenario, difficulty, rng)

    return scenario


def resolve_scenario_path(
    base_task_id: str,
    seed: int,
    dir_path: Path,
) -> Path:
    """Resolve which scenario YAML to load for ``base_task_id``.

    Default behaviour (the :class:`~env.config.Settings` ``scenario_variants``
    flag is off) resolves exactly ``{base_task_id}.yaml`` -- byte-identical to
    historical behaviour. When the flag is on, all ``{base_task_id}*.yaml`` files
    (canonical + authored variants) are globbed, sorted deterministically, and
    one is chosen via ``random.Random(seed)`` so the selection is reproducible.
    """
    from .config import get_settings

    canonical = dir_path / f"{base_task_id}.yaml"

    if not get_settings().scenario_variants:
        return canonical

    candidates = sorted(
        p for p in dir_path.glob(f"{base_task_id}*.yaml") if p.is_file()
    )
    if not candidates:
        return canonical

    return random.Random(seed).choice(candidates)


def generate_synthetic_scenario(
    base_task_id: str,
    seed: int,
    difficulty: str = "medium",
    scenarios_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate a synthetic scenario with difficulty scaling."""
    rng = random.Random(seed)

    dir_path = scenarios_dir or SCENARIOS_DIR
    path = resolve_scenario_path(base_task_id, seed, dir_path)
    if not path.exists():
        raise ValueError(f"Missing scenario file: {dir_path / f'{base_task_id}.yaml'}")

    base_scenario = load_yaml(path)

    scaled = scale_difficulty(base_scenario, difficulty, rng)

    return scaled


SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"


def load_yaml(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"Missing YAML file: {path}")

    stat = path.stat()
    mtime_ns = stat.st_mtime_ns
    size = stat.st_size

    with _lock:
        cached = _cache.get(path)
        if cached and cached[0] == mtime_ns and cached[1] == size:
            return copy.deepcopy(cached[2])

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    with _lock:
        _cache[path] = (mtime_ns, size, data)

    return copy.deepcopy(data)


def clear_yaml_cache() -> None:
    with _lock:
        _cache.clear()
