"""
FastAPI Router for LunarVision Chandrayaan PDS4 XML + IMG Ingestion & Scientific Processing.
Provides:
- POST /api/lunarvision/upload: Ingests XML + IMG via multipart, chunking, or local disk reference.
- POST /api/lunarvision/process: Dispatches asynchronous PDS4 parsing and zero-RAM preview generation.
- GET  /api/lunarvision/status/{job_id}: Returns real-time processing status and progress percentage.
- GET  /api/lunarvision/preview/{job_id}: Serves the generated high-contrast scientific preview PNG.
- GET  /api/lunarvision/metadata/{job_id}: Returns parsed PDS4 metadata and raster properties.
- GET  /api/lunarvision/local-datasets: Discovers pre-extracted mission rasters on disk.
"""

import os
import uuid
import json
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, status
from fastapi.responses import JSONResponse, FileResponse
from starlette.concurrency import run_in_threadpool

from backend.config import settings, sanitize_filename
from core.pds4_ingestion import (
    parse_pds4_metadata,
    validate_raster_file,
    generate_scientific_preview
)
from core.quality import sanitize_for_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lunarvision", tags=["LunarVision PDS4 Ingestion Engine"])

# Job registry storing async progress and results
jobs: Dict[str, Dict[str, Any]] = {}


def _get_job_dir(job_id: str) -> str:
    jdir = os.path.abspath(os.path.join(settings.OUTPUT_DIR, "lunarvision_jobs", job_id))
    os.makedirs(jdir, exist_ok=True)
    return jdir


def _execute_processing_pipeline(job_id: str):
    """
    Background worker that parses PDS4 XML, validates IMG against label metadata,
    reads binary raster via np.memmap, and generates contrast-stretched preview.
    """
    job = jobs.get(job_id)
    if not job:
        return

    job_dir = _get_job_dir(job_id)
    xml_path = job.get("xml_path")
    img_path = job.get("img_path")

    try:
        # Step 1: Parse PDS4 XML
        job["status"] = "processing"
        job["progress"] = 25
        job["step"] = "Parsing PDS4 XML label metadata..."

        if not xml_path or not os.path.exists(xml_path):
            raise FileNotFoundError(f"PDS4 XML file missing at path: {xml_path}")

        meta = parse_pds4_metadata(xml_path=xml_path)
        job["metadata"] = meta
        job["progress"] = 45
        job["step"] = f"PDS4 label verified: {meta['lines']:,} lines × {meta['samples']:,} samples ({meta['data_type']})."

        # Step 2: Validate Raster IMG
        if not img_path or not os.path.exists(img_path):
            raise FileNotFoundError(f"Scientific IMG raster missing at path: {img_path}")

        job["progress"] = 60
        job["step"] = "Validating binary raster structure against PDS4 label..."
        is_valid, val_msg = validate_raster_file(meta, img_path)
        if not is_valid:
            raise ValueError(val_msg)

        # Step 3: Zero-RAM Preview Generation with np.memmap
        job["progress"] = 80
        job["step"] = "Reading memory-mapped raster & generating scientific preview..."
        preview_path = os.path.join(job_dir, "preview.png")
        prev_info = generate_scientific_preview(meta, img_path, preview_path, max_dim=1600)

        job["preview_info"] = prev_info
        job["preview_path"] = preview_path
        job["preview_url"] = f"/api/lunarvision/preview/{job_id}"
        job["status"] = "ready"
        job["progress"] = 100
        job["step"] = "Processing complete! Scientific preview and metadata ready."

        # Save state snapshot to disk
        meta_save = sanitize_for_json(dict(job))
        meta_file = os.path.join(job_dir, "job_metadata.json")
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta_save, f, indent=2)

    except Exception as e:
        logger.error("LunarVision processing failed for job %s: %s", job_id, e, exc_info=True)
        job["status"] = "error"
        job["error"] = str(e)
        job["step"] = f"Error: {str(e)}"


@router.get("/local-datasets")
async def list_local_datasets():
    """
    Detects any pre-extracted Chandrayaan-2 TMC-2 datasets located on disk.
    Allows 1-click instant ingestion without uploading 1.56 GB through the browser.
    """
    datasets = []
    base_search = [
        os.path.join(settings.BASE_DIR, "data", "extracted_bundles", "tmc_fore"),
        os.path.join(settings.BASE_DIR, "data", "extracted_bundles", "tmc_aft"),
        os.path.join(settings.BASE_DIR, "data", "extracted_bundles"),
        os.path.join(settings.BASE_DIR, "data")
    ]

    for b in base_search:
        if os.path.exists(b):
            for root, _, files in os.walk(b):
                for f in files:
                    if f.lower().endswith(".xml"):
                        xml_full = os.path.join(root, f)
                        base_name, _ = os.path.splitext(f)
                        img_cand = os.path.join(root, base_name + ".img")
                        if os.path.exists(img_cand):
                            fsize = os.path.getsize(img_cand)
                            datasets.append({
                                "name": "Chandrayaan TMC Calibrated Swath",
                                "xml_filename": f,
                                "img_filename": base_name + ".img",
                                "xml_path": xml_full,
                                "img_path": img_cand,
                                "size_bytes": fsize,
                                "size_gb": round(fsize / (1024**3), 2)
                            })
    return {"datasets": datasets}


@router.post("/upload")
async def upload_dataset(
    background_tasks: BackgroundTasks,
    xml_file: Optional[UploadFile] = File(None),
    img_file: Optional[UploadFile] = File(None),
    local_xml_path: Optional[str] = Form(None),
    local_img_path: Optional[str] = Form(None),
    dataset_name: Optional[str] = Form("TMC-2")
):
    """
    Ingests XML and IMG file pair:
    - Supports direct upload for small test datasets.
    - Supports local path reference for 1.56 GB files already on disk (zero upload time).
    """
    job_id = f"lv_{uuid.uuid4().hex[:10]}"
    job_dir = _get_job_dir(job_id)

    target_xml_path = None
    target_img_path = None
    xml_fname = None
    img_fname = None
    img_size = 0

    # 1. Check if local paths were provided
    if local_xml_path and os.path.exists(local_xml_path) and local_img_path and os.path.exists(local_img_path):
        target_xml_path = os.path.abspath(local_xml_path)
        target_img_path = os.path.abspath(local_img_path)
        xml_fname = os.path.basename(target_xml_path)
        img_fname = os.path.basename(target_img_path)
        img_size = os.path.getsize(target_img_path)

    # 2. Check if files were uploaded via multipart form
    elif xml_file is not None:
        xml_fname = sanitize_filename(xml_file.filename)
        target_xml_path = os.path.join(job_dir, xml_fname)
        with open(target_xml_path, "wb") as f:
            content = await xml_file.read()
            f.write(content)

        if img_file is not None:
            img_fname = sanitize_filename(img_file.filename)
            target_img_path = os.path.join(job_dir, img_fname)
            with open(target_img_path, "wb") as f:
                while chunk := await img_file.read(16 * 1024 * 1024):
                    f.write(chunk)
            img_size = os.path.getsize(target_img_path)

    if not target_xml_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDS4 XML label must be provided (either via xml_file or local_xml_path)."
        )

    # Initialize Job Entry
    job = {
        "jobId": job_id,
        "status": "uploaded",
        "progress": 10,
        "step": "Files uploaded and staged successfully.",
        "dataset_name": dataset_name,
        "xml_path": target_xml_path,
        "img_path": target_img_path,
        "xml_filename": xml_fname,
        "img_filename": img_fname,
        "img_size_bytes": img_size,
        "img_size_formatted": f"{img_size / (1024**3):.2f} GB" if img_size > 1024**3 else f"{img_size / (1024**2):.1f} MB",
        "metadata": None,
        "preview_url": None,
        "error": None
    }
    jobs[job_id] = job

    # Auto-dispatch processing in background thread
    background_tasks.add_task(_execute_processing_pipeline, job_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "jobId": job_id,
            "status": "processing",
            "message": "Dataset uploaded successfully. Processing started in background.",
            "dataset_name": dataset_name,
            "img_filename": img_fname,
            "img_size": job["img_size_formatted"]
        }
    )


@router.post("/process")
async def trigger_process(
    background_tasks: BackgroundTasks,
    job_id: str = Form(...)
):
    """
    Explicitly triggers processing for a staged jobId.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] not in ("processing", "ready"):
        job["status"] = "processing"
        job["progress"] = 15
        job["step"] = "Processing triggered..."
        background_tasks.add_task(_execute_processing_pipeline, job_id)

    return {"jobId": job_id, "status": job["status"]}


@router.get("/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Polls the processing status of a large IMG dataset.
    Returns status: 'uploading' | 'processing' | 'ready' | 'error'.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    return {
        "jobId": job_id,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "step": job.get("step", ""),
        "dataset_name": job.get("dataset_name", "TMC-2"),
        "img_filename": job.get("img_filename"),
        "img_size": job.get("img_size_formatted"),
        "preview_url": job.get("preview_url"),
        "error": job.get("error")
    }


@router.get("/preview/{job_id}")
async def get_job_preview(job_id: str):
    """
    Returns the lightweight scientific preview image.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    preview_path = job.get("preview_path")
    if not preview_path or not os.path.exists(preview_path):
        raise HTTPException(status_code=404, detail="Preview image is not ready yet.")

    return FileResponse(
        preview_path,
        media_type="image/png",
        filename=f"{job_id}_preview.png"
    )


@router.get("/metadata/{job_id}")
async def get_job_metadata(job_id: str):
    """
    Returns extracted PDS4 metadata and raster properties for the dataset.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if not job.get("metadata"):
        raise HTTPException(status_code=400, detail="Metadata parsing has not completed yet.")

    return JSONResponse(
        content=sanitize_for_json({
            "jobId": job_id,
            "status": job["status"],
            "dataset_name": job.get("dataset_name", "TMC-2"),
            "img_filename": job.get("img_filename"),
            "img_size": job.get("img_size_formatted"),
            "preview_url": job.get("preview_url"),
            "metadata": job["metadata"],
            "preview_info": job.get("preview_info")
        })
    )
