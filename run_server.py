#!/usr/bin/env python3
"""
Convenience entry point for LunarVision FastAPI Server.
Redirects execution to scripts/run_server.py.
"""
import os
import sys

if __name__ == "__main__":
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "run_server.py")
    with open(script_path, "r", encoding="utf-8") as f:
        code = compile(f.read(), script_path, "exec")
        exec(code, {"__name__": "__main__"})
