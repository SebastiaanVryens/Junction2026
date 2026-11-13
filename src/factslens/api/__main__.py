"""Run the web app:  python -m factslens.api [--host 0.0.0.0] [--port 8000]

Started this way (rather than with the `uvicorn` command) so the same entry point works on
Windows, where the database driver needs a different event loop than uvicorn's default.
"""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

from factslens.common import db
from factslens.common.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="FactsLens web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    configure_logging()
    db.use_compatible_event_loop()
    config = uvicorn.Config(
        "factslens.api.app:app",
        host=args.host,
        port=args.port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",  # only Caddy can reach this port
    )
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
