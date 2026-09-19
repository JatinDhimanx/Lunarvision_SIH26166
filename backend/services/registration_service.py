"""
Registration Service Engine: Handles pipeline orchestration, visual deliverable generation,
spatial DB persistence, and MinIO/S3 object storage uploads.
"""

import os
from typing import Dict, Any, Optional
import numpy as np
import cv2

from core.data_io import PlanetaryDataManager
from core.pipeline import LunarVisionPipeline
from core.quality import resolve_quality_thresholds, sanitize_for_json
from core.db_storage import SpatialStorageEngine
from core.object_storage import ObjectStorageManager

# Shared Singletons
pipeline = LunarVisionPipeline()
spatial_storage = SpatialStorageEngine()
object_storage = ObjectStorageManager()

# In-memory Task Registry for asynchronous task polling
task_registry: Dict[str, Dict[str, Any]] = {}


def clean_metadata_dict(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts only lightweight serializable scalar metadata fields."""
    if not isinstance(meta, dict):
        return {}
    cleaned = {}
    for k, v in meta.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            cleaned[k] = v
        elif isinstance(v, dict):
            cleaned[k] = clean_metadata_dict(v)
        elif isinstance(v, (list, tuple)) and len(v) <= 50:
            cleaned[k] = [x for x in v if isinstance(x, (int, float, str, bool))]
    return cleaned


def process_registration_and_export(
    src_img: np.ndarray,
    ref_img: np.ndarray,
    task_id: str,
    task_dir: str,
    is_multimodal: bool,
    enable_tps: bool,
    matcher_backend: str = "phase_congruency",
    enable_scale_cascade: bool = True,
    enable_preprocessing: bool = True,
    enable_subpixel: bool = True,
    subpixel_method: str = "ic_lk",
    anchor_img: Optional[np.ndarray] = None,
    meta_src: Optional[dict] = None,
    meta_ref: Optional[dict] = None,
    dem: Optional[np.ndarray] = None,
    quality_thresholds: Optional[dict] = None
) -> Dict[str, Any]:
    """Executes registration pipeline and produces all visual and geospatial deliverables."""
    task_registry[task_id] = {"status": "PROCESSING", "task_id": task_id}

    result = pipeline.register(
        src_img,
        ref_img,
        is_multimodal=is_multimodal,
        is_steep_relief=enable_tps,
        matcher_backend=matcher_backend,
        enable_scale_cascade=enable_scale_cascade,
        enable_preprocessing=enable_preprocessing,
        enable_subpixel=enable_subpixel,
        subpixel_method=subpixel_method,
        anchor_img=anchor_img,
        pds4_meta_src=meta_src,
        pds4_meta_ref=meta_ref,
        dem=dem,
        quality_thresholds=quality_thresholds
    )

    status = result["status"]
    metrics = result["metrics"]
    pts_src = result.get("pts_src", np.empty((0, 2)))
    pts_ref = result.get("pts_ref", np.empty((0, 2)))
    provenance = result.get("provenance", {})

    # Save visual assets (source and reference are always saved)
    src_disp = src_img if src_img.ndim == 2 else src_img[:, :, 0]
    cv2.imwrite(os.path.join(task_dir, "src.png"), src_disp)
    cv2.imwrite(os.path.join(task_dir, "ref.png"), ref_img)

    if status == "FAILED":
        # Create distinct visual assets even on failure for diagnostic analysis
        fail_disp = src_disp.copy()
        cv2.putText(fail_disp, "UNWARPED (REJECTED)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imwrite(os.path.join(task_dir, "warped.png"), fail_disp)

        # Difference between raw source and reference
        src_resized = cv2.resize(src_disp, (ref_img.shape[1], ref_img.shape[0]))
        diff_raw = cv2.absdiff(src_resized, ref_img)
        diff_colored = cv2.applyColorMap(diff_raw, cv2.COLORMAP_INFERNO)
        cv2.imwrite(os.path.join(task_dir, "diff.png"), diff_colored)

        # Structural phase energy map
        pc_src = result.get("pc_src", None)
        if pc_src is not None:
            pc_disp = (pc_src * 255).astype(np.uint8)
        else:
            pc_disp = cv2.normalize(cv2.Sobel(src_disp, cv2.CV_32F, 1, 1), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        pc_colored = cv2.applyColorMap(pc_disp, cv2.COLORMAP_VIRIDIS)
        cv2.imwrite(os.path.join(task_dir, "phase.png"), pc_colored)

        # Export mathematically honest certification report showing FAIL verdict
        json_path = os.path.join(task_dir, "quality_certification_report.json")
        mission_info = {
            "source_metadata": clean_metadata_dict(meta_src),
            "reference_metadata": clean_metadata_dict(meta_ref)
        }
        PlanetaryDataManager.export_certification_report(
            metrics, json_path, mission_info=mission_info, quality_gates=quality_thresholds
        )

        task_response = sanitize_for_json({
            "status": "FAILED",
            "task_id": task_id,
            "reason": result.get("reason", "Registration quality thresholds not satisfied."),
            "src_url": f"/api/file/{task_id}/src.png",
            "ref_url": f"/api/file/{task_id}/ref.png",
            "warped_url": f"/api/file/{task_id}/warped.png",
            "diff_url": f"/api/file/{task_id}/diff.png",
            "phase_url": f"/api/file/{task_id}/phase.png",
            "metrics": metrics,
            "pts_src": [],
            "pts_ref": [],
            "geo_bounds": None,
            "gcp_geo_points": [],
            "provenance": provenance,
            "quality_thresholds": provenance.get("quality_thresholds", resolve_quality_thresholds(quality_thresholds)),
            "metadata_source": clean_metadata_dict(meta_src),
            "metadata_reference": clean_metadata_dict(meta_ref),
            "deliverables_available": False
        })
        task_registry[task_id] = {"status": "FAILED", "response": task_response}
        return task_response

    # SUCCESS PATH: Export deliverables
    warped = result["warped_image"]
    pc_src = result.get("pc_src", None)
    confidences = result.get("point_confidences")
    if confidences is None or len(confidences) != len(pts_src):
        confidences = [1.0] * len(pts_src)

    cv2.imwrite(os.path.join(task_dir, "warped.png"), warped)
    diff = cv2.absdiff(warped, ref_img)
    diff_colored = cv2.applyColorMap(diff, cv2.COLORMAP_INFERNO)
    cv2.imwrite(os.path.join(task_dir, "diff.png"), diff_colored)

    if pc_src is not None:
        pc_disp = (pc_src * 255).astype(np.uint8)
    else:
        pc_disp = cv2.normalize(cv2.Sobel(src_disp, cv2.CV_32F, 1, 1), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    pc_colored = cv2.applyColorMap(pc_disp, cv2.COLORMAP_VIRIDIS)
    cv2.imwrite(os.path.join(task_dir, "phase.png"), pc_colored)

    # Deliverable files: .tif, .tfw, .csv, .json, .md, .zip
    tif_path = os.path.join(task_dir, "registered_product.tif")
    PlanetaryDataManager.export_registered_product(warped, tif_path)

    csv_path = os.path.join(task_dir, "tie_points_gcp.csv")
    PlanetaryDataManager.export_tie_points(pts_src, pts_ref, csv_path, confidences=confidences)

    json_path = os.path.join(task_dir, "quality_certification_report.json")
    mission_info = {"source_metadata": meta_src or {}, "reference_metadata": meta_ref or {}}
    PlanetaryDataManager.export_certification_report(
        metrics, json_path, mission_info=mission_info, quality_gates=quality_thresholds
    )

    zip_path = os.path.join(task_dir, "LunarVision_Registration_Bundle.zip")
    PlanetaryDataManager.create_zip_bundle(task_dir, zip_path)

    # Real vs Estimated Georeferencing
    has_real_geo = bool(meta_src and meta_src.get("has_real_georeferencing", False))
    if meta_src and "center_lat" in meta_src:
        center_lat = float(meta_src["center_lat"])
        center_lon = float(meta_src["center_lon"])
        georeferencing_status = "REAL_MISSION_METADATA_PDS4"
    else:
        geo_bounds = None
        gcp_geo_points = []
        georeferencing_status = "UNAVAILABLE"

    if georeferencing_status != "UNAVAILABLE":
        span_deg = 0.45
        geo_bounds = {
            "lat_min": round(center_lat - span_deg / 2, 4),
            "lat_max": round(center_lat + span_deg / 2, 4),
            "lon_min": round(center_lon - span_deg / 2, 4),
            "lon_max": round(center_lon + span_deg / 2, 4),
            "center": [round(center_lat, 4), round(center_lon, 4)],
            "georeferencing_status": georeferencing_status,
            "is_real_georeferenced": has_real_geo
        }
        h, w = ref_img.shape[:2]
        gcp_geo_points = []
        for idx, (p_src, p_ref) in enumerate(zip(pts_src, pts_ref)):
            px, py = p_ref
            lat = geo_bounds["lat_max"] - (py / max(h, 1)) * span_deg
            lon = geo_bounds["lon_min"] + (px / max(w, 1)) * span_deg
            gcp_geo_points.append({
                "id": f"GCP-{idx+1:02d}", "pixel_x": round(float(px), 1), "pixel_y": round(float(py), 1),
                "lat": round(float(lat), 4), "lon": round(float(lon), 4),
                "residual_px": metrics.get("subpixel_rmse_px"),
                "confidence": round(float(confidences[idx]), 3)
            })

    # Upload bundle to MinIO / S3 Object Storage
    bundle_path = os.path.join(task_dir, "LunarVision_Registration_Bundle.zip")
    storage_info = object_storage.upload_file(bundle_path, f"{task_id}/LunarVision_Registration_Bundle.zip")

    # Persist run to SQLite / PostGIS spatial storage
    spatial_storage.save_task_run(
        task_id=task_id,
        status="SUCCESS",
        metrics=metrics,
        geo_bounds=geo_bounds,
        gcp_list=gcp_geo_points
    )

    task_response = sanitize_for_json({
        "status": "SUCCESS",
        "task_id": task_id,
        "src_url": f"/api/file/{task_id}/src.png",
        "ref_url": f"/api/file/{task_id}/ref.png",
        "warped_url": f"/api/file/{task_id}/warped.png",
        "diff_url": f"/api/file/{task_id}/diff.png",
        "phase_url": f"/api/file/{task_id}/phase.png",
        "metrics": metrics,
        "pts_src": pts_src[:500].tolist() if len(pts_src) > 500 else pts_src.tolist(),
        "pts_ref": pts_ref[:500].tolist() if len(pts_ref) > 500 else pts_ref.tolist(),
        "geo_bounds": geo_bounds,
        "gcp_geo_points": gcp_geo_points[:150] if len(gcp_geo_points) > 150 else gcp_geo_points,
        "provenance": provenance,
        "quality_thresholds": provenance.get("quality_thresholds", resolve_quality_thresholds(quality_thresholds)),
        "metadata_source": clean_metadata_dict(meta_src),
        "metadata_reference": clean_metadata_dict(meta_ref),
        "storage": storage_info,
        "deliverables_available": True
    })
    task_registry[task_id] = {"status": "SUCCESS", "response": task_response}
    return task_response
