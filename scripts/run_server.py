"""
LunarVision Server Runner:
Convenience script to start the FastAPI server directly.

Usage (from project root):
    python scripts/run_server.py
"""

import os
import sys
import socket
import uvicorn

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from server import app


def find_free_port(start=8000, attempts=5):
    """
    Finds an available TCP port starting from `start`.

    On Windows, killed server processes leave sockets in TIME_WAIT state for
    ~2 minutes.  The test socket must NOT use SO_REUSEADDR because uvicorn
    binds without it — using SO_REUSEADDR here would make the test pass for
    TIME_WAIT ports that uvicorn will still fail to bind.
    """
    for port in range(start, start + attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # No SO_REUSEADDR: mirrors uvicorn's bind behavior exactly
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            continue
    raise RuntimeError(
        f"All ports {start}–{start + attempts - 1} are busy (Windows TIME_WAIT).\n"
        "Wait ~2 minutes for the OS to release them, then try again."
    )


if __name__ == "__main__":
    port = find_free_port()

    print(f"\n{'=' * 60}")
    print(f"  LunarVision Server  ->  http://127.0.0.1:{port}/")
    print(f"  API Docs            ->  http://127.0.0.1:{port}/docs")
    print(f"{'=' * 60}\n")

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,             # Keep off: file watcher competes with GB-scale disk I/O
        timeout_keep_alive=300,   # 5 min: prevents drops mid-chunk on slow uploads
        access_log=False,         # Suppress per-chunk request spam in logs
        loop="asyncio",
        http="h11",
    )
