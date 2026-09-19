"""
FastAPI Router for Geospatial Database Operations:
- GET /api/spatial/tiepoints/{task_id}: Returns GeoJSON FeatureCollection for Leaflet GIS.
- GET /api/spatial/postgis-dump/{task_id}: Generates PostgreSQL + PostGIS SQL table dump.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from core.quality import sanitize_for_json
from backend.services.registration_service import spatial_storage, task_registry

router = APIRouter(prefix="/api/spatial", tags=["Spatial GIS"])


@router.get("/tiepoints/{task_id}")
async def get_spatial_tiepoints(task_id: str):
    """Returns GeoJSON FeatureCollection of tie-points for Leaflet GIS rendering."""
    if task_id not in task_registry:
        raise HTTPException(status_code=404, detail="Task ID not found")
    data = spatial_storage.query_geojson_tiepoints(task_id)
    return JSONResponse(content=sanitize_for_json(data))


@router.get("/postgis-dump/{task_id}")
async def get_postgis_dump(task_id: str):
    """Generates and returns PostgreSQL + PostGIS SQL dump for the task's GCPs."""
    if task_id not in task_registry:
        raise HTTPException(status_code=404, detail="Task ID not found")
    task_res = task_registry[task_id].get("response", {})
    gcps = task_res.get("gcp_geo_points", [])
    sql_text = spatial_storage.generate_postgis_sql_dump(task_id, gcps)
    return HTMLResponse(content=f"<pre>{sql_text}</pre>", media_type="text/plain")
