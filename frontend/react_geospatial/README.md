# LunarVision Production Geospatial UI (React 18 + Leaflet.js)
**ISRO Problem Statement SIH26166: Multi-Modal, Sun-Angle and Scale Invariant Planetary Correspondence Suite**

---

## Overview
The **React + Leaflet.js Production Geospatial UI** is the official mission control dashboard for LunarVision. It features a space-grade dark glassmorphism interface engineered for flight operations, mission quality auditing, and interactive geospatial analysis.

## Key Capabilities
- **NASA Moon Trek & Leaflet.js Integration**:
  - Interactive 2D planetary GIS map with NASA Moon Trek WMS tiles (LRO WAC Basemap).
  - Ground Control Point (GCP) markers plotted with high-precision lunar latitude & longitude.
  - Interactive opacity slider, swath coverage polygons, and georeferencing status indicators.
- **Visual Comparison Suite**:
  - **Split Curtain Slider**: Live draggable split divider comparing Source vs Registered Warped product.
  - **Seamless Dynamic Checkerboard**: Alternating tiles to verify crater rim alignment across boundaries.
  - **Tie-Point Vector Field**: Green vector rays connecting sub-pixel correspondences across the image pair.
  - **Residual Error Difference Heatmap**: Absolute error visualization colored via `Magma` colormap.
  - **Frequency Phase Congruency (PC_max)**: Illumination-invariant structural feature map visualization.
- **Interactive Drag-and-Drop Ingestion**:
  - Dual drag-and-drop zones for Source (OHRC/TMC-2/IIRS) and Reference (LRO NAC/SELENE).
  - PDS4 companion auto-pairing (`.xml` + `.img` or `.tif` + `.tfw`).
  - DEM elevation and intermediate scale anchor dropzones.
- **Official Deliverable Export Center**:
  - Full Registration Bundle (`.zip`)
  - Registered Product GeoTIFF (`.tif` + `.tfw`)
  - Ground Control Point Table (`.csv`)
  - Quality Certification Report (`.json` & `.md`)
  - PostGIS Spatial DDL (`.sql` dump for direct database ingestion)

---

## Launch Instructions

The React geospatial dashboard is served automatically by the FastAPI backend server:
```powershell
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at:
`http://localhost:8000`
