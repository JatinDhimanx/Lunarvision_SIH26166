#!/usr/bin/env python3
"""
Convenience entry point for LunarVision registration CLI.
Redirects execution to scripts/run_registration.py.
"""
import os
import sys

if __name__ == "__main__":
    from scripts.run_registration import main
    main()
