"""Shared quality thresholds and deterministic certification evaluation."""

from typing import Any, Dict, Mapping
import math
import numpy as np


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively converts arbitrary structures to RFC-8259 compliant JSON primitives.
    Replaces NaN, +Infinity, and -Infinity with None (JSON null).
    Converts numpy scalar and array types to Python native types.
    """
    if obj is None:
        return None
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.dtype, type)):
        return str(obj)
    if isinstance(obj, np.ndarray):
        if obj.size > 2000:
            return f"<ndarray shape={list(obj.shape)} dtype={str(obj.dtype)}>"
        return [sanitize_for_json(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(x) for x in obj]
    return obj


QUALITY_THRESHOLDS: Dict[str, float] = {
    "min_inliers": 15,
    "min_inlier_ratio_pct": 15.0,
    "max_subpixel_rmse_px": 2.0,
    "max_checkpoint_rmse_px": 2.0,
    "min_grid_coverage_pct": 15.0,
    "min_voronoi_entropy": 0.40,
    "min_overlap_pct": 10.0,
    "min_ncc": 0.30,
    "min_ssim": 0.20,
    "min_nmi": 0.20,
}


def resolve_quality_thresholds(overrides: Mapping[str, Any] | None = None) -> Dict[str, float]:
    thresholds = dict(QUALITY_THRESHOLDS)
    if overrides:
        for key, value in overrides.items():
            if key in thresholds:
                thresholds[key] = float(value)
    return thresholds


def evaluate_quality(metrics: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> Dict[str, bool]:
    t = resolve_quality_thresholds(overrides)

    def finite(name: str) -> float | None:
        value = metrics.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    rmse = finite("subpixel_rmse_px")
    checkpoint = finite("checkpoint_rmse_px")
    return {
        "inlier_count_pass": finite("inlier_count") is not None and finite("inlier_count") >= t["min_inliers"],
        "inlier_ratio_pass": finite("inlier_ratio_pct") is not None and finite("inlier_ratio_pct") >= t["min_inlier_ratio_pct"],
        "subpixel_rmse_pass": rmse is not None and rmse <= t["max_subpixel_rmse_px"],
        "checkpoint_rmse_pass": checkpoint is not None and checkpoint <= t["max_checkpoint_rmse_px"],
        "spatial_grid_coverage_pass": finite("grid_coverage_pct") is not None and finite("grid_coverage_pct") >= t["min_grid_coverage_pct"],
        "voronoi_entropy_pass": finite("voronoi_entropy") is not None and finite("voronoi_entropy") >= t["min_voronoi_entropy"],
        "image_overlap_pass": finite("overlap_pct") is not None and finite("overlap_pct") >= t["min_overlap_pct"],
        # These are conditional gates: callers that compute an image-level
        # similarity metric must pass it; sparse audit fixtures may omit it.
        "ncc_pass": "ncc" not in metrics or (finite("ncc") is not None and finite("ncc") >= t["min_ncc"]),
        "ssim_pass": "ssim" not in metrics or (finite("ssim") is not None and finite("ssim") >= t["min_ssim"]),
        "nmi_pass": "nmi" not in metrics or (finite("nmi") is not None and finite("nmi") >= t["min_nmi"]),
        "geometric_transform_valid_pass": bool(metrics.get("valid_transform", False)),
    }


def quality_passes(metrics: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> bool:
    return all(evaluate_quality(metrics, overrides).values())
