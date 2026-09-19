"""
LunarVision Production ASGI Server Entry Point:
Provides the top-level 'app' instance for Uvicorn and direct execution.

Usage:
    python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
    or
    python server.py
"""

import os
import sys
import uvicorn

# Ensure the root directory is on the Python path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.app import app

__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
# Reload triggered: 2026-09-17 20:24
