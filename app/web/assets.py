"""Fingerprinted URLs for the three static files a page cannot work without.

Starlette serves ``/static`` with an ``ETag`` and a ``Last-Modified`` but no
``Cache-Control``, which leaves the freshness to the browser's heuristic: ten
percent of the file's age. A stylesheet last touched a month ago is therefore
"fresh" in a returning visitor's cache for about three days — while the HTML
that refers to it is dynamic and always new. After a deploy that is new markup
with old CSS, for days, for exactly the people who already know the site: the
owner checking their own deploy, and a recruiter who looked once last week.

The fix is the standard one. A page asks for ``/static/app.css?v=<digest of the
file>``; the URL changes when — and only when — the file does, so it can be
cached forever (:class:`~app.web.middleware.SecurityHeadersMiddleware` says so),
and an unversioned request is told to revalidate every time.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

from app.core.paths import STATIC_DIR


@lru_cache(maxsize=256)
def _digest(path: str, mtime_ns: int, size: int) -> str:
    # mtime and size are part of the key, so editing a file under a running
    # development server gets a new digest without a restart; in a container
    # nothing changes and every call after the first is a dictionary hit.
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12]


def asset_url(path: str) -> str:
    """``/static/<path>?v=<digest>``, or the bare URL if the file is missing.

    A missing file is not an error here: the page should still render, and the
    request for it will 404 on its own, visibly, like any other broken asset.
    """
    try:
        stat = (STATIC_DIR / path).stat()
        return f"/static/{path}?v={_digest(path, stat.st_mtime_ns, stat.st_size)}"
    except OSError:
        return f"/static/{path}"


def register(templates: Any) -> Any:
    """Expose :func:`asset_url` to every template served by ``templates``."""
    templates.env.globals["asset_url"] = asset_url
    return templates
