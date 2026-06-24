"""Error-proof test harness.

A single home for the invariant contract every advancement must preserve:

* INV-1 open interval (0,1) for all grader-facing scores
* INV-2 determinism per (task, seed, persona)
* INV-3 persona-invariant headline score / breakdown
* INV-4 bounded per-step reward in [-1, 1]
* INV-5 closed scenario schema (gold fields never reach an Observation)

See ``tests/harness/invariants.py`` for the reusable assertions.
"""
