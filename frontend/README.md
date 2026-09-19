# LunarVision Dual Frontend Architecture
**ISRO Problem Statement SIH26166: Multi-Modal, Sun-Angle and Scale Invariant Image Correspondence Suite**

```
• FRONTEND: Streamlit (rapid prototyping) / React + Leaflet.js (production geospatial UI)
```

---

## Architecture Overview

The LunarVision frontend is architected as a **dual-tier user interface system** aligned with the project technical specification:

```
frontend/
├── README.md                          # Master Frontend Architecture Guide
│
├── streamlit/                         # Tier 1: Rapid Prototyping Laboratory
│   ├── app.py                         # Streamlit interactive experimentation laboratory
│   └── README.md                      # Streamlit documentation & usage guide
│
└── react_geospatial/                  # Tier 2: Production Planetary Geospatial UI
    ├── index.html                     # HTML5 React 18 mount
    ├── styles.css                     # Space-grade glassmorphism design system
    ├── app.jsx                        # React 18 component suite & Leaflet.js Moon Trek GIS
    └── README.md                      # Production UI documentation & GIS capabilities
```

---

## Comparison Matrix

| Capability | Streamlit Rapid Prototyping Lab | React + Leaflet.js Production UI |
| :--- | :--- | :--- |
| **Primary Audience** | Algorithm researchers, photogrammetry scientists | Mission operations, flight verification, mission control |
| **Technology Stack** | Streamlit, Matplotlib, OpenCV | React 18, Leaflet.js, HTML5 Canvas, CSS Glassmorphism |
| **Interactivity** | Real-time parameter sliders (min inliers, max RMSE) | Split curtain slider, checkerboard, vector field, error heatmap |
| **Planetary GIS** | Tabular GCP coordinate display | Interactive NASA Moon Trek WMS globe overlay with GCP pins |
| **Data Ingestion** | Standard file uploaders | Drag-and-drop with automatic PDS4 companion pairing |
| **Deliverables Export** | GCP CSV export | 1-click downloads for GeoTIFF, GCP CSV, JSON Audit, PostGIS SQL, ZIP |
| **Execution Port** | `http://localhost:8501` | `http://localhost:8000` |

---

## Quick Start

### 1. Launch Production Geospatial UI (React + Leaflet.js)
```powershell
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```
Navigate to: **`http://localhost:8000`**

### 2. Launch Rapid Prototyping Laboratory (Streamlit)
```powershell
streamlit run frontend/streamlit/app.py
```
Navigate to: **`http://localhost:8501`**
