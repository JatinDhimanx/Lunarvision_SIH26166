"""
FastAPI Router for Storage Telemetry & Engine Health:
- GET /api/health: Operational capabilities and algorithm health check.
- GET /api/storage-status: Real-time MinIO S3 and PostGIS/SQLite spatial telemetry.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.quality import sanitize_for_json
from backend.services.registration_service import object_storage, spatial_storage

router = APIRouter(tags=["System & Storage"])


@router.get("/api/health")
async def health_check():
    """Returns engine capabilities, version, and active algorithmic subsystems."""
    return {
        "status": "ONLINE",
        "engine": "LunarVision Production Suite",
        "version": "3.0.0",
        "architecture": "Python + FastAPI (async) REST Service Layer",
        "capabilities": {
            "hybrid_matchers": ["phase_congruency_rift", "sift", "orb", "loftr (optional)"],
            "geometric_estimators": ["magsac_plus_plus", "ransac"],
            "subpixel_refinement": ["ic_lk", "phase_correlation"],
            "scale_cascade": True,
            "scale_ladder_hierarchy": True,
            "multimodal_pca": True,
            "photogrammetric_verification": True,
            "dem_topographic_photometry": True,
            "rpc_spice_sensor_model": True,
            "bounded_tps": True,
            "postgis_spatial_db": True,
            "minio_s3_storage": True
        }
    }


@router.get("/api/storage-status")
async def get_storage_status():
    """Returns MinIO and SQLite/PostGIS connectivity and storage telemetry."""
    data = {
        "object_storage": object_storage.get_storage_status(),
        "spatial_db": {
            "type": "PostGIS-Compatible SQLite Spatial Engine",
            "db_path": spatial_storage.db_path,
            "geojson_export_ready": True
        }
    }
    return JSONResponse(content=sanitize_for_json(data))
