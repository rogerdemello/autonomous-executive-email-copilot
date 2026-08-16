"""Mail providers: the read/write seam every backend implements.

``demo`` and ``fake`` need no credentials; ``gmail`` and ``graph`` need OAuth.
"""

from __future__ import annotations

__all__ = ["base", "demo", "fake", "gmail", "graph"]
