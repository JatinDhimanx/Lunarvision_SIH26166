# LunarVision Technical Architecture & Implementation Guide
**ISRO Problem Statement 26166: Multi-Modal, Sub-Pixel Accurate Planetary Image Registration Suite**

---

## 1. System Implementation Status & Capability Matrix

| Challenge Module | Implementation Status | Implementation Architecture |
| :--- | :--- | :--- |
| **CH-01: Illumination Invariance** | **Fully Implemented** | Log-Gabor Phase Congruency ($PC_{max}$) + Maximum Index Map (MIM) + RIFT descriptors |
| **CH-02: Extreme Scale Cascade** | **Fully Implemented** | Multi-Payload Scale Ladder (IIRS $\rightarrow$ TMC-2 $\rightarrow$ OHRC) + Octave Gaussian pyramid candidate ROI |
| **CH-03: Multi-Modal Bridging** | **Fully Implemented** | N-band hyperspectral PCA panchromatic proxy with polarity check & explained variance tracking |
| **CH-04: Viewpoint & Relief Parallax** | **Fully Implemented** | Bounded Thin-Plate Spline (TPS) with convex hull extrapolation damping + leave-one-out validation |
| **CH-05: Sub-Pixel Refinement** | **Fully Implemented** | Dual Engine: Inverse-Compositional Lucas-Kanade (IC-LK) + Fourier Phase Correlation (DFT) |
| **CH-06: Uniform Distribution** | **Fully Implemented** | Quad-Tree cell budgeting + Social Soft Clustering Adaptive Non-Maximal Suppression (SSC-ANMS) |
| **Robust Geometric Verification** | **Fully Implemented** | Native **MAGSAC++** (`cv2.USAC_MAGSAC`) with RANSAC fallback, condition number $\kappa(H) < 1000$ |
| **DEM Topographic Photometry** | **Fully Implemented** | Minnaert / Lommel-Seeliger photometric correction from LOLA / Kaguya DEMs with UI/CLI upload |
| **RPC & NASA SPICE Rig** | **Fully Implemented** | 20-term cubic Rational Polynomial Coefficients camera model + PDS4/SPICE solar ephemeris |
| **Spatial Database & Storage** | **Fully Implemented** | PostGIS DDL/Insert generation + SQLite Spatial tie-point tables + GeoJSON FeatureCollections |
| **Object Storage (MinIO / S3)** | **Fully Implemented** | Production MinIO S3-compatible client for raster tiles, chips, and bundles with local volume fallback |
| **Frontend Options** | **Fully Implemented** | React 18 + Leaflet.js (NASA Moon Trek WMS) & Streamlit Rapid Prototyping Laboratory (`streamlit_app.py`) |
| **MLOps & Containerization** | **Fully Implemented** | Multi-stage `Dockerfile`, `docker-compose.yml` (API + PostGIS + MinIO), and GitHub Actions CI/CD |

---

## 2. Technical Honesty & Failure Handling Policy

1. **No Synthetic Fallback**:
   - Production execution uses only genuine feature correspondences from the supplied mission datasets.
   - If genuine feature matches or inliers fall below the mandatory threshold ($\ge 15$), the pipeline halts and returns a structured `{"status": "FAILED", "reason": ...}` response.
2. **Dynamic Quality Certification Gates**:
   - Every metric verdict is calculated from actual mathematical measurements against strict ISRO benchmarks.
   - `overall_verdict` is set to `"PASSED"` ONLY if all mandatory gates pass.
   - Reports (both Markdown and JSON) show dynamic PASS/FAIL evaluations per metric.
3. **Independent Check Points (ICP)**:
   - Certification RMSE is evaluated on hold-out check points (or leave-one-out cross-validation), preventing optimizer step size from being reported as certification accuracy.
4. **Observable Confidence Scoring**:
   - Per-point confidence is computed from descriptor ratio, patch cross-correlation, subpixel displacement, and geometric reprojection residual. No synthetic random formulas are used.

---

## 3. Installation and Startup

### Prerequisites
- Python 3.9+
- OS: Windows / Linux / macOS

### Installation
```bash
# Install core dependencies
pip install -r requirements.txt
```

### Running the Backend & Web Dashboard
```bash
# Start FastAPI production server
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```
Open browser at `http://127.0.0.1:8000/`.
Runtime deliverables are written outside the source tree under the system
temporary directory. Set `LUNARVISION_OUTPUT_DIR` to a managed persistent
directory in deployment.

### Running the Command Line Interface (CLI)
```bash
python run_registration.py --src <source-mission-file> --ref <reference-mission-file> --out <output-directory>
```
