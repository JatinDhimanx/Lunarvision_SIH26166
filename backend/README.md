# LunarVision Backend REST Service Layer
**ISRO Problem Statement SIH26166: Multi-Modal, Sun-Angle and Scale Invariant Planetary Correspondence Suite**

```
• BACKEND: Python + FastAPI(async) REST service layer
```

---

## 1. Architectural Overview

The **LunarVision Backend** is a production-grade, asynchronous REST service layer powered by **FastAPI** and **Python 3.11**. It bridges the mathematical photogrammetric registration core ([`core/`](file:///c:/Users/jatin/OneDrive/Desktop/SIH_26166/core)) to web dashboards, scientific GIS clients, and cloud object stores.

```
backend/
├── README.md                      # Backend Architecture & REST API Specification
├── __init__.py                    # Module export (app, settings)
├── config.py                      # Production settings, upload size limits & security guards
├── app.py                         # FastAPI application factory, CORS, and ASGI server
│
├── routers/                       # Modular REST API Routers
│   ├── __init__.py
│   ├── registration.py            # Ingestion & registration endpoints (async non-blocking)
│   ├── spatial.py                 # Leaflet GeoJSON & PostGIS SQL table generator
│   ├── storage.py                 # MinIO S3 & SQLite/PostGIS storage telemetry & health check
│   └── files.py                   # Secure asset delivery & registered bundle downloads
│
└── services/                      # Business Logic & Pipeline Bridges
    ├── __init__.py
    └── registration_service.py    # Pipeline execution, deliverable generator & task registry
```

---

## 2. API Endpoints Catalog

### A. Planetary Registration & Processing
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/upload-and-register` | Accepts planetary datasets (`.xml`, `.img`, `.tif`), runs full pipeline, and generates deliverables. |
| `GET` | `/api/task/{task_id}` | Pollable execution status endpoint for asynchronous background tasks. |

### B. Geospatial & PostGIS Storage
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/spatial/tiepoints/{task_id}` | Returns a standardized **GeoJSON `FeatureCollection`** of tie-points for Leaflet GIS. |
| `GET` | `/api/spatial/postgis-dump/{task_id}` | Generates a complete **PostgreSQL + PostGIS SQL script** (`ST_SetSRID`, `ST_MakePoint`). |

### C. System Telemetry & Health
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Real-time health check reporting active algorithms, MAGSAC++, LoFTR, and version. |
| `GET` | `/api/storage-status` | Connectivity and disk telemetry for **MinIO S3** and **PostGIS Spatial DB**. |

### D. Deliverables & File Downloads
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/file/{task_id}/{filename}` | Streamlined preview endpoint for warped imagery, difference heatmaps, and phase congruency edges. |
| `GET` | `/api/download/{task_id}/{file_type}` | 1-click downloads for `bundle` (.zip), `geotiff` (.tif), `gcp_csv` (.csv), or `report` (.json). |

---

## 3. Interactive Documentation

FastAPI automatically generates interactive OpenAPI documentation:
- **Swagger UI**: [`http://localhost:8000/docs`](http://localhost:8000/docs)
- **ReDoc**: [`http://localhost:8000/redoc`](http://localhost:8000/redoc)

---

## 4. Security & Hardening Features

1. **Path Containment Verification (`is_safe_path`)**:
   Strict `os.path.commonpath` validation prevents path-traversal attacks (`../`).
2. **Filename Sanitization (`sanitize_filename`)**:
   Regex cleaning strips special characters and path delimiters before writing to filesystem.
3. **Upload Size Limit (`MAX_UPLOAD_SIZE = 500MB`)**:
   Prevents denial-of-service from oversized uncompressed planetary rasters.
4. **RFC 8259 Compliant JSON Sanitization (`sanitize_for_json`)**:
   Guarantees zero serialization crashes by recursively converting `NaN` and `±Inf` to `null`.
5. **CORS Enabled**:
   Full Cross-Origin Resource Sharing enabled for local GIS clients, React apps, and Streamlit laboratories.

---

## 5. Execution Commands

From the project root:
```powershell
# Production ASGI Uvicorn Server with Live Reload
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```
Or directly from the backend module:
```powershell
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```
