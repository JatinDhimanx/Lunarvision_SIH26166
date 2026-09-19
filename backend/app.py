"""
FastAPI Application Factory & ASGI Server Instance:
- Technical Honesty: Real registration failure reporting with no fake fallbacks in real mode.
- Production-only execution: all registration inputs come from uploaded mission data.
- Security: Filename sanitization, path containment verification, file type validation, and upload size limits.
- Georeferencing: Coordinates are reported only when real mission metadata is available.
- Real Observable Confidence: No synthetic formulas.
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from backend.config import settings
from backend.routers.registration import router as registration_router
from backend.routers.spatial import router as spatial_router
from backend.routers.storage import router as storage_router
from backend.routers.files import router as files_router
from backend.routers.chunk_upload import router as chunk_upload_router
from backend.routers.lunarvision import router as lunarvision_router


def create_app() -> FastAPI:
    """Instantiates and configures the production FastAPI (async) REST application."""
    application = FastAPI(
        title=settings.TITLE,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # Cross-Origin Resource Sharing (CORS) for external GIS clients and tools
    # Complies with W3C CORS specification (explicit origins when allow_credentials=True)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register modular REST routers
    application.include_router(registration_router)
    application.include_router(spatial_router)
    application.include_router(storage_router)
    application.include_router(files_router)
    application.include_router(chunk_upload_router)
    application.include_router(lunarvision_router)

    # Serve Production Geospatial UI (React 18 + Leaflet.js)
    @application.get("/")
    def serve_dashboard():
        index_file = os.path.join(settings.WEB_DIR, "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse("<h1>LunarVision Web Dashboard Loading...</h1>")

    # Static assets mount (styles.css, app.jsx, icons)
    if os.path.exists(settings.WEB_DIR):
        application.mount("/", StaticFiles(directory=settings.WEB_DIR, html=True), name="static")

    return application


app = create_app()
