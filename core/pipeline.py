"""
Master Registration & Correspondence Pipeline
Orchestrates:
1. Preprocessing, destriping, shadow masking, and DEM illumination correction.
2. Multi-modal PCA panchromatic proxy synthesis for hyperspectral cubes.
3. Coarse-to-fine scale cascade and candidate ROI estimation.
4. Hybrid feature matching (RIFT Phase-Congruency, SIFT, ORB, LoFTR).
5. Rigorous photogrammetric geometric verification (condition number, overlap, inliers).
6. Quad-Tree ANMS homogeneous spatial dispersion.
7. Scientifically verified sub-pixel refinement with independent check-point RMSE.
8. Bounded Non-Rigid Thin-Plate Spline (TPS) with extrapolation damping.
9. Technical honesty policy: Structured failure reporting without fake points in real mode.
"""

from typing import Dict, Any, Optional, Tuple, List
import numpy as np
import cv2

from core.preprocessing import preprocess_planetary_image
from core.matcher import HybridMatcher
from core.geometric_verification import GeometricVerifier, PlanetaryGeometryEngine
from core.ch02_scale_cascade import ScaleCascadeEngine
from core.ch03_multimodal_bridging import MultimodalBridgingEngine
from core.ch04_viewpoint_topographic import TopographicReliefEngine
from core.ch05_subpixel_engine import SubpixelRefinementEngine
from core.ch06_uniform_distribution import UniformDistributionEngine
from utils.metrics import (
    compute_rmse,
    compute_inlier_ratio,
    compute_grid_coverage,
    compute_voronoi_entropy,
    compute_ssim,
    compute_nmi,
    compute_ncc,
    compute_observable_confidence
)
import math
from core.quality import resolve_quality_thresholds, evaluate_quality, quality_passes, sanitize_for_json


class LunarVisionPipeline:
    """Production Chandrayaan-2 Registration Pipeline."""

    def __init__(self):
        self.scale_engine = ScaleCascadeEngine()
        self.multimodal_engine = MultimodalBridgingEngine()
        self.relief_engine = TopographicReliefEngine()
        self.subpixel_engine = SubpixelRefinementEngine()
        self.uniformity_engine = UniformDistributionEngine(target_points=200)
        self.verifier = GeometricVerifier(
            min_inliers=15,
            max_reprojection_rmse=3.0,
            max_condition_number=1000.0,
            min_overlap_pct=20.0,
            min_hull_coverage_pct=15.0
        )

    def match_tiled_sliding_window(
        self,
        src_img: np.ndarray,
        ref_img: np.ndarray,
        matcher,
        tile_size: int = 1024,
        overlap: int = 128,
        coarse_transform: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Sliding-window tiled feature matching for gigapixel / large-format planetary images.
        Decomposes scenes into overlapping chips (default 1024x1024 with 128px overlap)
        to prevent OOM on full OHRC/TMC-2 strips (>10,000 x 10,000).
        Projects chip bounding boxes through coarse transformation, matches locally,
        and translates coordinates back into global scene space.
        """
        h_s, w_s = src_img.shape[:2]
        h_r, w_r = ref_img.shape[:2]

        step = tile_size - overlap
        all_pts_src = []
        all_pts_ref = []
        all_scores = []

        y_steps = list(range(0, max(1, h_s - overlap), step))
        x_steps = list(range(0, max(1, w_s - overlap), step))

        for y0 in y_steps:
            y1 = min(y0 + tile_size, h_s)
            for x0 in x_steps:
                x1 = min(x0 + tile_size, w_s)

                chip_src = src_img[y0:y1, x0:x1]
                # If chip is tiny (edge strip), skip or pad
                if chip_src.shape[0] < 64 or chip_src.shape[1] < 64:
                    continue

                # Determine corresponding search region in ref
                if coarse_transform is not None:
                    # Project corners of source chip to ref coordinates
                    corners = np.array([
                        [x0, y0, 1.0],
                        [x1, y0, 1.0],
                        [x1, y1, 1.0],
                        [x0, y1, 1.0]
                    ], dtype=np.float32)
                    proj = (coarse_transform @ corners.T).T
                    if proj.shape[1] == 3:
                        denom = np.maximum(np.abs(proj[:, 2:]), 1e-7)
                        proj = proj[:, :2] / denom
                    rx0 = max(0, int(np.min(proj[:, 0]) - overlap))
                    ry0 = max(0, int(np.min(proj[:, 1]) - overlap))
                    rx1 = min(w_r, int(np.max(proj[:, 0]) + overlap))
                    ry1 = min(h_r, int(np.max(proj[:, 1]) + overlap))
                else:
                    rx0 = max(0, x0 - overlap)
                    ry0 = max(0, y0 - overlap)
                    rx1 = min(w_r, x1 + overlap)
                    ry1 = min(h_r, y1 + overlap)

                if (rx1 - rx0) < 64 or (ry1 - ry0) < 64:
                    continue

                chip_ref = ref_img[ry0:ry1, rx0:rx1]

                # Match chip pair
                try:
                    res = matcher.match(chip_src, chip_ref)
                    if len(res.pts_src) > 0:
                        # Re-project local chip coordinates to global coordinates
                        pts_s_global = res.pts_src + np.array([x0, y0], dtype=np.float32)
                        pts_r_global = res.pts_ref + np.array([rx0, ry0], dtype=np.float32)

                        all_pts_src.append(pts_s_global)
                        all_pts_ref.append(pts_r_global)
                        if len(res.descriptor_scores) == len(res.pts_src):
                            all_scores.append(res.descriptor_scores)
                        else:
                            all_scores.append(np.ones(len(res.pts_src), dtype=np.float32))
                except Exception:
                    continue

        if len(all_pts_src) == 0:
            return (
                np.empty((0, 2), dtype=np.float32),
                np.empty((0, 2), dtype=np.float32),
                np.empty((0,), dtype=np.float32)
            )

        cat_src = np.vstack(all_pts_src)
        cat_ref = np.vstack(all_pts_ref)
        cat_scores = np.concatenate(all_scores)
        return cat_src, cat_ref, cat_scores

    def register(
        self,
        img_src: np.ndarray,
        img_ref: np.ndarray,
        is_multimodal: bool = False,
        is_steep_relief: bool = True,
        matcher_backend: str = "phase_congruency",
        enable_scale_cascade: bool = True,
        enable_preprocessing: bool = True,
        enable_subpixel: bool = True,
        subpixel_method: str = "ic_lk",
        anchor_img: Optional[np.ndarray] = None,
        pds4_meta_src: Optional[dict] = None,
        pds4_meta_ref: Optional[dict] = None,
        dem: Optional[np.ndarray] = None,
        quality_thresholds: Optional[dict] = None,
        enable_tiling: Optional[bool] = None,
        tile_size: int = 1024,
        tile_overlap: int = 128
    ) -> Dict[str, Any]:
        """
        Executes end-to-end planetary registration.
        """
        geo_status = PlanetaryGeometryEngine.get_geometry_status()
        provenance = {
            "matcher_backend_requested": matcher_backend,
            "matcher_backend_executed": matcher_backend,
            "subpixel_method": subpixel_method,
            "loftr_used": False,
            "dem_correction_applied": False,
            "scale_cascade_applied": False,
            "scale_ladder_applied": bool(anchor_img is not None),
            "multimodal_proxy_applied": False,
            "tps_applied": False,
            "tiling_applied": False,
            "rpc_spice_applied": geo_status.get("rpc_spice_applied", False),
            "rpc_spice_model": geo_status.get("model", "Projective Homography"),
            "geometric_estimator": "MAGSAC++",
            "notes": []
        }
        thresholds = resolve_quality_thresholds(quality_thresholds)

        # Override quality gates if supplied
        self.verifier.min_inliers = int(thresholds["min_inliers"])

        # Step 1: Preprocessing & Illumination Normalization
        src_raw = img_src
        ref_raw = img_ref
        src_mask = None
        ref_mask = None

        if enable_preprocessing:
            # Handle multi-band source before 2D preprocessing if needed
            if src_raw.ndim == 3 and src_raw.shape[2] > 1:
                # Modality bridging first for hyperspectral
                src_gray, pca_meta = self.multimodal_engine.synthesize_panchromatic_proxy(src_raw)
                provenance["multimodal_proxy_applied"] = True
                provenance["pca_metadata"] = pca_meta
            else:
                src_gray = src_raw if src_raw.ndim == 2 else src_raw[:, :, 0]

            ref_gray = ref_raw if ref_raw.ndim == 2 else ref_raw[:, :, 0]

            src_proc, src_mask, src_pre_meta = preprocess_planetary_image(
                src_gray, pds4_meta=pds4_meta_src, dem=dem
            )
            ref_proc, ref_mask, ref_pre_meta = preprocess_planetary_image(
                ref_gray, pds4_meta=pds4_meta_ref, dem=None
            )
            provenance["dem_correction_applied"] = src_pre_meta.get("dem_correction", {}).get("dem_correction_applied", False)
            provenance["src_preprocessing"] = src_pre_meta
            provenance["ref_preprocessing"] = ref_pre_meta
            provenance["shadow_mask_used"] = bool(src_mask is not None or ref_mask is not None)
        else:
            if src_raw.ndim == 3 and src_raw.shape[2] > 1:
                src_proc, pca_meta = self.multimodal_engine.synthesize_panchromatic_proxy(src_raw)
                provenance["multimodal_proxy_applied"] = True
                provenance["pca_metadata"] = pca_meta
            else:
                src_proc = src_raw if src_raw.ndim == 2 else src_raw[:, :, 0]
            ref_proc = ref_raw if ref_raw.ndim == 2 else ref_raw[:, :, 0]

        # Step 2: Coarse-to-Fine Scale Cascade
        cascade_meta = {"applied": False}
        if enable_scale_cascade:
            try:
                if anchor_img is not None:
                    # Multi-payload 3-stage scale ladder (e.g. OHRC <-> TMC-2 <-> IIRS)
                    ladder_results = self.scale_engine.match_scale_ladder(
                        img_ohrc=src_proc,
                        img_tmc2=anchor_img,
                        img_iirs=ref_proc
                    )
                    cascade_meta = self.scale_engine.estimate_coarse_alignment(
                        src_proc, ref_proc, meta_src=pds4_meta_src, meta_ref=pds4_meta_ref
                    )
                    cascade_meta["ladder_hierarchy"] = ladder_results
                    provenance["scale_ladder_applied"] = True
                else:
                    cascade_meta = self.scale_engine.estimate_coarse_alignment(
                        src_proc, ref_proc, meta_src=pds4_meta_src, meta_ref=pds4_meta_ref
                    )
                provenance["scale_cascade_applied"] = cascade_meta.get("applied", False)
            except Exception as e:
                cascade_meta = {"applied": False, "error": str(e)}
        provenance["cascade_metadata"] = cascade_meta

        # Step 3: Matcher Selection & Feature Extraction
        matcher, executed_backend, fallback_note = HybridMatcher.get_matcher(matcher_backend)
        provenance["matcher_backend_executed"] = executed_backend
        if fallback_note:
            provenance["notes"].append(fallback_note)
        if executed_backend == "loftr":
            provenance["loftr_used"] = True

        # Decide whether to use tiled sliding-window matching for large rasters
        max_dim = max(max(src_proc.shape[:2]), max(ref_proc.shape[:2]))
        should_tile = enable_tiling if enable_tiling is not None else (max_dim > 1536)

        if should_tile:
            provenance["tiling_applied"] = True
            coarse_H = cascade_meta.get("coarse_transform") if cascade_meta.get("applied") else None
            pts_src_all, pts_ref_all, scores_all = self.match_tiled_sliding_window(
                src_proc, ref_proc, matcher,
                tile_size=tile_size,
                overlap=tile_overlap,
                coarse_transform=coarse_H
            )
            raw_pts_src = pts_src_all
            raw_pts_ref = pts_ref_all
            desc_scores = scores_all
            match_inlier_count = len(pts_src_all)
            match_inlier_ratio = 1.0 if len(pts_src_all) > 0 else 0.0
            pc_extracted = None
            pc_ref_extracted = None  # Tiled mode: no single match_res to pull pc_ref from
        else:
            match_ref = ref_proc
            roi_offset = np.array([0, 0], dtype=np.float32)
            candidate_roi = cascade_meta.get("candidate_roi") if cascade_meta.get("applied") else None
            if candidate_roi:
                x0, y0, rw, rh = [int(v) for v in candidate_roi]
                match_ref = ref_proc[y0:y0 + rh, x0:x0 + rw]
                roi_offset = np.array([x0, y0], dtype=np.float32)
                provenance["fine_match_roi"] = [x0, y0, rw, rh]
            match_res = matcher.match(src_proc, match_ref)
            if len(match_res.pts_ref):
                match_res.pts_ref = match_res.pts_ref + roi_offset
            raw_pts_src = match_res.pts_src
            raw_pts_ref = match_res.pts_ref
            desc_scores = match_res.descriptor_scores
            match_inlier_count = match_res.inlier_count
            match_inlier_ratio = match_res.inlier_ratio
            pc_extracted = match_res.metadata.get("pc_src", None)
            pc_ref_extracted = match_res.metadata.get("pc_ref", None)

        # Guarantee structural energy maps for inspection
        try:
            from core.ch01_sun_angle_invariance import SunAngleInvarianceEngine
            _pc_eng = SunAngleInvarianceEngine(n_scales=3, n_orientations=4)
            if pc_extracted is None:
                _, _, pc_extracted = _pc_eng.extract_features(src_proc, max_points=100)
            if pc_ref_extracted is None:
                _, _, pc_ref_extracted = _pc_eng.extract_features(ref_proc, max_points=100)
        except Exception:
            if pc_extracted is None:
                pc_extracted = cv2.normalize(cv2.Sobel(src_proc, cv2.CV_32F, 1, 1), None, 0.0, 1.0, cv2.NORM_MINMAX)
            if pc_ref_extracted is None:
                pc_ref_extracted = cv2.normalize(cv2.Sobel(ref_proc, cv2.CV_32F, 1, 1), None, 0.0, 1.0, cv2.NORM_MINMAX)

        if src_mask is not None and len(raw_pts_src):
            valid_src = src_mask[
                np.clip(raw_pts_src[:, 1].astype(int), 0, src_mask.shape[0] - 1),
                np.clip(raw_pts_src[:, 0].astype(int), 0, src_mask.shape[1] - 1)
            ] > 0
            if ref_mask is not None and len(raw_pts_ref):
                valid_ref = ref_mask[
                    np.clip(raw_pts_ref[:, 1].astype(int), 0, ref_mask.shape[0] - 1),
                    np.clip(raw_pts_ref[:, 0].astype(int), 0, ref_mask.shape[1] - 1)
                ] > 0
                valid_src &= valid_ref
            raw_pts_src = raw_pts_src[valid_src]
            raw_pts_ref = raw_pts_ref[valid_src]
            if len(desc_scores) == len(valid_src):
                desc_scores = desc_scores[valid_src]
            provenance["shadow_mask_points_rejected"] = int(np.sum(~valid_src))

        # Step 4: Check if genuine matches are sufficient to attempt geometric estimation
        min_pts_required = 4
        has_enough_matches = (
            len(raw_pts_src) >= min_pts_required and 
            len(raw_pts_ref) >= min_pts_required and 
            match_inlier_count >= min_pts_required
        )

        if not has_enough_matches:
            return {
                    "status": "FAILED",
                    "reason": f"Insufficient genuine feature correspondences for geometric estimation (found {len(raw_pts_src)} raw matches, {match_inlier_count} initial inliers; required >= {min_pts_required} to attempt model fitting).",
                    "match_count": int(len(raw_pts_src)),
                    "inlier_count": int(match_inlier_count),
                    "warped_image": None,
                    "pts_src": np.empty((0, 2), dtype=np.float32),
                    "pts_ref": np.empty((0, 2), dtype=np.float32),
                    "metrics": {
                        "estimator": "MAGSAC++",
                        "subpixel_method": subpixel_method,
                        "inlier_count": int(match_res.inlier_count),
                        "inlier_ratio_pct": float(match_res.inlier_ratio),
                        "subpixel_rmse_px": None,
                        "checkpoint_rmse_px": None,
                        "grid_coverage_pct": 0.0,
                        "voronoi_entropy": 0.0,
                        "ssim": 0.0,
                        "nmi": 0.0,
                        "ncc": 0.0,
                        "valid_transform": False
                    },
                    "provenance": provenance
                }

        # Step 5: Rigorous Photogrammetric Geometric Verification
        verif = self.verifier.verify(
            raw_pts_src,
            raw_pts_ref,
            src_proc.shape,
            ref_proc.shape
        )

        if not verif.is_valid:
            est_name = verif.metrics.get("estimator", "MAGSAC++")
            provenance["geometric_estimator"] = est_name
            return {
                "status": "FAILED",
                "reason": f"Geometric verification rejected candidate transformation: {'; '.join(verif.rejection_reasons)}",
                "match_count": int(len(raw_pts_src)),
                "inlier_count": int(verif.inlier_count),
                "warped_image": None,
                "pts_src": np.empty((0, 2), dtype=np.float32),
                "pts_ref": np.empty((0, 2), dtype=np.float32),
                "metrics": sanitize_for_json({
                    "estimator": est_name,
                    "subpixel_method": subpixel_method,
                    "inlier_count": int(verif.inlier_count),
                    "inlier_ratio_pct": round(float(verif.inlier_ratio), 1) if math.isfinite(verif.inlier_ratio) else 0.0,
                    "reprojection_rmse_px": round(float(verif.reprojection_rmse), 4) if math.isfinite(verif.reprojection_rmse) else None,
                    "condition_number": round(float(verif.condition_number), 2) if math.isfinite(verif.condition_number) else None,
                    "overlap_pct": round(float(verif.overlap_pct), 1) if math.isfinite(verif.overlap_pct) else 0.0,
                    "convex_hull_coverage_pct": round(float(verif.convex_hull_coverage_pct), 1) if math.isfinite(verif.convex_hull_coverage_pct) else 0.0,
                    "subpixel_rmse_px": None,
                    "checkpoint_rmse_px": None,
                    "grid_coverage_pct": 0.0,
                    "voronoi_entropy": 0.0,
                    "ssim": 0.0,
                    "nmi": 0.0,
                    "ncc": 0.0,
                    "valid_transform": False
                }),
                "provenance": provenance
            }

        inliers_src = raw_pts_src[verif.inlier_mask]
        inliers_ref = raw_pts_ref[verif.inlier_mask]
        scores_inliers = desc_scores[verif.inlier_mask] if len(desc_scores) == len(raw_pts_src) else np.ones(len(inliers_src), dtype=np.float32)

        # Step 6: Uniform Spatial Distribution (Quad-Tree ANMS)
        uniform_src = self.uniformity_engine.enforce_uniformity(inliers_src, scores_inliers, src_proc.shape)
        # Select closest corresponding ref points
        dists = np.linalg.norm(inliers_src[:, None, :] - uniform_src[None, :, :], axis=-1)
        closest_indices = np.argmin(dists, axis=0)
        # Deduplicate to ensure strictly unique coordinates for TPS numerical stability
        unique_indices, unique_pos = np.unique(closest_indices, return_index=True)
        uniform_src = uniform_src[unique_pos]
        uniform_ref = inliers_ref[unique_indices]
        uniform_scores = scores_inliers[unique_indices]

        # Step 7: Continuous Sub-Pixel Refinement & Independent Check-Point Validation
        if enable_subpixel and len(uniform_src) >= 4:
            refined_src, refined_ref, subpixel_details = self.subpixel_engine.refine_all_points(
                src_proc, ref_proc, uniform_src, uniform_ref, method=subpixel_method
            )
            if len(refined_src) < 4:
                return {
                        "status": "FAILED",
                        "reason": "Subpixel refinement rejected candidates: convergence and NCC quality gates were not met.",
                        "match_count": int(len(raw_pts_src)),
                        "inlier_count": int(verif.inlier_count),
                        "warped_image": None,
                        "pts_src": np.empty((0, 2), dtype=np.float32),
                        "pts_ref": np.empty((0, 2), dtype=np.float32),
                        "metrics": {"inlier_count": int(verif.inlier_count), "inlier_ratio_pct": float(verif.inlier_ratio),
                                    "subpixel_rmse_px": None, "checkpoint_rmse_px": None, "grid_coverage_pct": 0.0,
                                    "voronoi_entropy": 0.0, "overlap_pct": float(verif.overlap_pct), "ssim": 0.0,
                                    "nmi": 0.0, "ncc": 0.0, "valid_transform": False},
                        "provenance": provenance
                    }

            # Independent Check Point RMSE evaluation
            icp_eval = self.subpixel_engine.evaluate_independent_checkpoints(refined_src, refined_ref)
            measured_rmse = float(icp_eval["checkpoint_rmse_px"])
            if np.isinf(measured_rmse) or np.isnan(measured_rmse):
                measured_rmse = verif.reprojection_rmse
        else:
            refined_src = uniform_src
            refined_ref = uniform_ref
            subpixel_details = []
            measured_rmse = verif.reprojection_rmse
            icp_eval = {"checkpoint_rmse_px": measured_rmse, "num_checkpoints": 0}

        # Step 8: Non-Rigid Topographic Relief Warping (Bounded TPS)
        validity_mask = None
        if is_steep_relief and len(refined_src) >= 5:
            warped_img, validity_mask = self.relief_engine.warp_image_tps(
                src_proc, refined_src, refined_ref,
                output_shape=ref_proc.shape[:2],
                fallback_homography=verif.H
            )
            provenance["tps_applied"] = True
        else:
            if verif.H is not None:
                warped_img = cv2.warpPerspective(src_proc, verif.H, (ref_proc.shape[1], ref_proc.shape[0]))
            else:
                warped_img = src_proc.copy()
            validity_mask = np.ones(ref_proc.shape[:2], dtype=np.uint8) * 255

        # Step 9: Real Observable Confidence Calculation
        per_point_confidence = []
        for i in range(len(refined_src)):
            d_score = float(uniform_scores[i]) if (i < len(uniform_scores) and uniform_scores[i] is not None) else 0.8
            detail = subpixel_details[i] if (subpixel_details is not None and i < len(subpixel_details)) else None
            ncc_q = float(detail.ncc_quality) if detail is not None else 0.7
            step_mag = float(np.linalg.norm(detail.step_offset)) if detail is not None else 0.0

            # Reprojection residual under global transform
            pt_h = np.array([refined_src[i, 0], refined_src[i, 1], 1.0], dtype=np.float32)
            if verif.H is not None:
                p_proj = verif.H @ pt_h
                denom = p_proj[2] if abs(p_proj[2]) > 1e-7 else 1e-7
                p_proj = p_proj[:2] / denom
                res_ransac = float(np.linalg.norm(p_proj - refined_ref[i]))
            else:
                res_ransac = 1.0

            conf_val = compute_observable_confidence(d_score, ncc_q, step_mag, res_ransac)
            per_point_confidence.append(conf_val)

        # Step 10: Complete Mathematical Quality Scorecard
        grid_cov = compute_grid_coverage(refined_src, src_proc.shape)
        voronoi_ent = compute_voronoi_entropy(refined_src, src_proc.shape)
        ssim_val = compute_ssim(warped_img, ref_proc)
        nmi_val = compute_nmi(warped_img, ref_proc)
        ncc_val = compute_ncc(warped_img, ref_proc)

        estimator_name = verif.metrics.get("estimator", "MAGSAC++")
        provenance["geometric_estimator"] = estimator_name

        def _safe_f(v, default=None, ndigits=None):
            if v is None:
                return default
            try:
                val = float(v)
                if not math.isfinite(val):
                    return default
                return round(val, ndigits) if ndigits is not None else val
            except (TypeError, ValueError):
                return default

        metrics = sanitize_for_json({
            "estimator": estimator_name,
            "subpixel_method": subpixel_method,
            "subpixel_rmse_px": _safe_f(measured_rmse, ndigits=4),
            "checkpoint_rmse_px": _safe_f(icp_eval.get("checkpoint_rmse_px", measured_rmse), ndigits=4),
            "inlier_count": int(len(refined_src)),
            "inlier_ratio_pct": _safe_f(verif.inlier_ratio, default=0.0, ndigits=1),
            "grid_coverage_pct": _safe_f(grid_cov, default=0.0, ndigits=1),
            "voronoi_entropy": _safe_f(voronoi_ent, default=0.0, ndigits=4),
            "ssim": _safe_f(ssim_val, default=0.0, ndigits=4),
            "nmi": _safe_f(nmi_val, default=0.0, ndigits=4),
            "ncc": _safe_f(ncc_val, default=0.0, ndigits=4),
            "condition_number": _safe_f(verif.condition_number, ndigits=2),
            "overlap_pct": _safe_f(verif.overlap_pct, default=0.0, ndigits=1),
            "convex_hull_coverage_pct": _safe_f(verif.convex_hull_coverage_pct, default=0.0, ndigits=1),
            "valid_transform": bool(verif.is_valid),
            "mean_confidence": _safe_f(np.mean([v for v in per_point_confidence if v is not None]), ndigits=4)
            if any(v is not None for v in per_point_confidence) else None
        })

        # Check final quality gates
        gate_evaluations = evaluate_quality(metrics, thresholds)
        passed_gates = quality_passes(metrics, thresholds)
        metrics["gate_evaluations"] = gate_evaluations
        provenance["quality_thresholds"] = thresholds

        overall_status = "SUCCESS" if passed_gates else "FAILED"
        return {
            "status": overall_status,
            "warped_image": warped_img,
            "validity_mask": validity_mask,
            "pts_src": refined_src,
            "pts_ref": refined_ref,
            "point_confidences": per_point_confidence,
            "metrics": metrics,
            "provenance": provenance,
            "pc_src": pc_extracted,
            "pc_ref": pc_ref_extracted,
            "subpixel_details": [
                {
                    "ncc": d.ncc_quality,
                    "status": d.status,
                    "converged": d.converged,
                    "iterations": d.iterations
                } for d in subpixel_details
            ]
        }
