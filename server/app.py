from __future__ import annotations

import uvicorn

# Re-export the FastAPI app so `server.app:app` resolves for uvicorn / ASGI.
from env.api import app

__all__ = ["app", "main"]


def main() -> None:
    """CLI entrypoint used by OpenEnv multi-mode validation."""
    uvicorn.run("server.app:app", host="0.0.0.0", port=7860)  # nosec B104 - container service binds all interfaces by design


if __name__ == "__main__":
    main()
