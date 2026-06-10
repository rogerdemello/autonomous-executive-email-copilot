"""Report-only evaluation-depth metrics (calibration, etc.).

Nothing here feeds the grader's headline score; these are additional lenses on a
set of decisions/trajectories.
"""

from .calibration import brier_score, expected_calibration_error

__all__ = ["brier_score", "expected_calibration_error"]
