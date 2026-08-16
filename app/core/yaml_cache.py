"""An mtime-and-size-keyed YAML loader shared by config and scenario loading.

Re-reads a file only when it actually changes on disk, and hands back a deep
copy so a caller mutating the result cannot poison the cache for everyone else.
That hot-reload behaviour is relied on by the tuning settings in
``app.core.utils`` and by scenario loading in ``research.sim.data_loader``.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import yaml

_lock = threading.Lock()
_cache: dict[Path, tuple[int, int, Any]] = {}


def load_yaml(path: Path) -> Any:
    if not path.exists():
        # The usual cause is a non-editable wheel install: `data/` sits beside
        # the package in the repository and is not packaged, so point at the
        # override rather than leaving a bare path.
        raise RuntimeError(
            f"Missing YAML file: {path}. Project data lives in the repository's "
            "data/ directory and is not bundled into the wheel — run from a "
            "checkout, or set DATA_DIR to where the data directory is mounted."
        )

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
