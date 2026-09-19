"""
LunarVision Streamlit Rapid Prototyping Entry Point:
Runs the Streamlit laboratory from the repository root.

Usage:
    streamlit run streamlit_app.py
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

STREAMLIT_SCRIPT = os.path.join(ROOT_DIR, "frontend", "streamlit", "app.py")

if __name__ == "__main__" or "streamlit" in sys.modules:
    if os.path.exists(STREAMLIT_SCRIPT):
        with open(STREAMLIT_SCRIPT, "r", encoding="utf-8") as f:
            code = compile(f.read(), STREAMLIT_SCRIPT, "exec")
            exec(code, globals())
    else:
        raise FileNotFoundError(f"Streamlit application file not found at {STREAMLIT_SCRIPT}")
