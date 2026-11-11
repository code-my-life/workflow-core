"""Start the core's service: `python -m workflow_core`. The shared secret comes from WORKFLOW_CORE_SECRET."""

from __future__ import annotations

import argparse
import os
import sys

SECRET_VARIABLE = "WORKFLOW_CORE_SECRET"
HOST = "127.0.0.1"
PORT = 8447


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workflow_core", description="Serve the core's read-only API.")
    parser.add_argument("--host", default=HOST, help=f"listening address (default {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"listening port (default {PORT})")
    args = parser.parse_args(argv)

    secret = os.environ.get(SECRET_VARIABLE, "")
    if not secret:
        print(
            f"workflow-core: {SECRET_VARIABLE} is not set; the service does not start without its shared secret "
            "(put it in ~/.config/workflow-core/.env).",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from workflow_core.adapters import runtime_adapter
    from workflow_core.api.app import create_app
    from workflow_core.config import load_workflows

    app = create_app(
        secret=secret,
        workflows=load_workflows(),
        boards=runtime_adapter().make_board_reader(),
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
