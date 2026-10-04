"""Entry point for `uv run python -m pragma` — starts the PRAGMA API server.

Equivalent to `uvicorn pragma.api:app --host 0.0.0.0 --port 8000`.
Honours the PORT environment variable (used by the network benchmark).
"""

from __future__ import annotations

import os

import uvicorn

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))


def main() -> None:
    uvicorn.run("pragma.api:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()