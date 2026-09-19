"""
Configuration and Security Settings for LunarVision Backend REST Service Layer.
"""

import os
import re
import tempfile
from typing import Set


class Settings:
    """Production server configuration and security bounds."""
    TITLE: str = "LunarVision Planetary Registration API"
    VERSION: str = "3.0.0"
    DESCRIPTION: str = "Asynchronous REST service layer for ISRO Chandrayaan-2 planetary correspondence."

    # Directory Paths
    BASE_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    FRONTEND_REACT_DIR: str = os.path.join(BASE_DIR, "frontend", "react_geospatial")
    WEB_DIR: str = FRONTEND_REACT_DIR if os.path.exists(FRONTEND_REACT_DIR) else os.path.join(BASE_DIR, "web")
    OUTPUT_DIR: str = os.path.abspath(os.environ.get(
        "LUNARVISION_OUTPUT_DIR",
        os.path.join(tempfile.gettempdir(), "lunarvision", "web_tasks")
    ))
    CHUNK_STAGING_DIR: str = os.path.abspath(os.environ.get(
        "LUNARVISION_CHUNK_DIR",
        os.path.join(tempfile.gettempdir(), "lunarvision", "chunk_sessions")
    ))

    # File & Security Constraints
    ALLOWED_EXTENSIONS: Set[str] = {
        ".xml", ".img", ".raw", ".dat", ".bin", ".tif", ".tiff",
        ".png", ".jpg", ".jpeg", ".npy", ".npz", ".zip"
    }
    MAX_UPLOAD_SIZE: int = 500 * 1024 * 1024  # 500 MB (Direct monolithic upload limit)
    DEFAULT_CHUNK_SIZE: int = 16 * 1024 * 1024  # 16 MB per chunk
    MAX_CHUNKED_FILE_SIZE: int = 10 * 1024 * 1024 * 1024  # 10 GB for chunked streaming
    CORS_ORIGINS: list = [
        o.strip() for o in os.environ.get(
            "LUNARVISION_CORS_ORIGINS",
            "http://localhost:3000,http://localhost:8000,http://127.0.0.1:3000,http://127.0.0.1:8000,http://localhost:8501,http://127.0.0.1:8501"
        ).split(",") if o.strip()
    ]


settings = Settings()
os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
os.makedirs(settings.CHUNK_STAGING_DIR, exist_ok=True)


def is_safe_path(target_path: str, base_dir: str = settings.OUTPUT_DIR) -> bool:
    """Verifies that target_path is contained within base_dir to prevent path traversal attacks."""
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(target_path)
    try:
        return os.path.commonpath([abs_target, abs_base]) == abs_base and abs_target != abs_base
    except ValueError:
        return False


def sanitize_filename(filename: str) -> str:
    """Sanitizes user-provided filenames to prevent filesystem injection."""
    base = os.path.basename(filename)
    clean = re.sub(r'[^a-zA-Z0-9_.-]', '_', base)
    clean = clean.lstrip('. ')
    if not clean:
        clean = "upload_data.bin"
    return clean
