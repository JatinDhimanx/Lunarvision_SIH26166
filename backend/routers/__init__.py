"""
FastAPI APIRouters for LunarVision Backend REST API.
"""
from backend.routers.registration import router as registration_router
from backend.routers.spatial import router as spatial_router
from backend.routers.storage import router as storage_router
from backend.routers.files import router as files_router

__all__ = [
    "registration_router",
    "spatial_router",
    "storage_router",
    "files_router"
]
