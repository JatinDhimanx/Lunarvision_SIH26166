"""
FastAPI Router for Visual Deliverables & Product Downloads:
- GET /api/file/{task_id}/{file_name}: Serves preview assets with strict path-containment validation.
- GET /api/download/{task_id}/{file_type}: Downloads registered deliverable bundles.
"""

import os
import re
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.config import settings, is_safe_path
from backend.services.registration_service import task_registry

router = APIRouter(tags=["Files & Downloads"])


@router.get("/api/file/{task_id}/{file_name}")
async def get_task_file(task_id: str, file_name: str):
    """Serves task deliverable files with strict path-containment validation."""
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        raise HTTPException(status_code=404, detail="Task ID not found")
    clean_task_id = task_id
    clean_file_name = os.path.basename(file_name)

    target_path = os.path.join(settings.OUTPUT_DIR, clean_task_id, clean_file_name)

    # Strict path traversal check
    if not is_safe_path(target_path, settings.OUTPUT_DIR):
        raise HTTPException(status_code=403, detail="Forbidden: Path traversal detected.")

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target_path)


@router.get("/api/download/{task_id}/{file_type}")
async def download_deliverable(task_id: str, file_type: str):
    """Downloads registered deliverable bundles. Fails if no valid deliverable exists."""
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        raise HTTPException(status_code=404, detail="Task ID not found")
    clean_task_id = task_id
    task_dir = os.path.join(settings.OUTPUT_DIR, clean_task_id)

    if not is_safe_path(task_dir, settings.OUTPUT_DIR):
        raise HTTPException(status_code=403, detail="Forbidden: Path traversal detected.")

    if not os.path.isdir(task_dir):
        raise HTTPException(status_code=404, detail="Task ID not found")

    file_mapping = {
        "bundle": ("LunarVision_Registration_Bundle.zip", "application/zip"),
        "geotiff": ("registered_product.tif", "image/tiff"),
        "gcp_csv": ("tie_points_gcp.csv", "text/csv"),
        "report": ("quality_certification_report.json", "application/json")
    }

    if file_type not in file_mapping:
        raise HTTPException(status_code=400, detail=f"Invalid deliverable type '{file_type}'.")

    target_name, media_type = file_mapping[file_type]
    file_path = os.path.join(task_dir, target_name)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail=f"Deliverable '{target_name}' not available for this run (registration may have failed)."
        )

    return FileResponse(file_path, media_type=media_type, filename=target_name)
