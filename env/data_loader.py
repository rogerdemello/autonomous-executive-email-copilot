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
