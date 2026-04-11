from __future__ import annotations

import uvicorn

from env.api import app


def main() -> None:
    """CLI entrypoint used by OpenEnv multi-mode validation."""
    uvicorn.run("server.app:app", host="0.0.0.0", port=7860)


if __name__ == "__main__":
    main()
