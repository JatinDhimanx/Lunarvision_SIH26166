# LunarVision Streamlit Rapid Prototyping Laboratory
**ISRO Problem Statement SIH26166: Multi-Modal, Sun-Angle and Scale Invariant Planetary Correspondence Suite**

---

## Overview
The **Streamlit Rapid Prototyping Laboratory** provides an interactive workbench for mission scientists, algorithm researchers, and photogrammetrists. It enables real-time parameter tuning, algorithm benchmarking, and instant visual verification without needing a compiled React toolchain.

## Key Capabilities
- **Matcher Selection**: Interactive switching between **Phase Congruency (RIFT)**, **SIFT**, **ORB**, and **LoFTR**.
- **Sub-Pixel Engines**: Direct comparison between **IC-LK (Gradient Descent)** and **Frequency Phase Correlation (FFT Cross-Power Spectrum)**.
- **Topographic Relief**: Toggle Bounded Non-Rigid Thin-Plate Spline (TPS) with elevation parallax handling.
- **Scale Cascade**: Toggle 3-stage intermediate scale pyramid (IIRS $\leftrightarrow$ TMC-2 $\leftrightarrow$ OHRC).
- **Quality Gates**: Real-time slider controls for inlier count and maximum reprojection RMSE thresholds.
- **Visual Inspection**: 3-column synchronized visual comparison (Source, Reference, Warped Registered Product).
- **Tie-Points Table**: Instant tabulated Ground Control Points (GCPs) with sub-pixel image coordinates.

---

## Launch Instructions

From the project root:
```powershell
streamlit run frontend/streamlit/app.py
```
Or simply:
```powershell
streamlit run streamlit_app.py
```

The Streamlit laboratory will automatically open at `http://localhost:8501`.
