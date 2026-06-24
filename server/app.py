from __future__ import annotations

import os

import uvicorn

# Re-export the FastAPI app so `server.app:app` resolves for uvicorn / ASGI.
from env.api import app

__all__ = ["app", "main"]


def main() -> None:
    """CLI entrypoint that launches the API server (used by the container).

    Honors $PORT when the host injects one (Render, Cloud Run, Fly.io, …),
    defaulting to 7860 for local runs.
    """
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run("server.app:app", host="0.0.0.0", port=port)  # nosec B104 - container service binds all interfaces by design


if __name__ == "__main__":
    main()
