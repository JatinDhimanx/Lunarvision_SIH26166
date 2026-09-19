"""
Command Line Interface (CLI) for LunarVision Planetary Registration Suite
Executes end-to-end registration on real files (PDS4 XML/IMG, GeoTIFF, PNG/JPG)
and exports all required deliverables:
1. Registered Georeferenced Product (.tif + .tfw).
2. Ground Control Points (GCP) Tie-Points Table (.csv).
3. ISRO Quality Certification Report (.json + .md).
4. Visual Verification Blends (Checkerboard, Difference Heatmap, Vectors).

Usage (from project root):
    python scripts/run_registration.py --src <source> --ref <reference> [--out results/<run_name>]
"""

import os
import sys
import argparse
import numpy as np
import cv2

# Ensure project root (one level up from scripts/) is on Python path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.data_io import PlanetaryDataManager
from core.pipeline import LunarVisionPipeline
from core.quality import resolve_quality_thresholds

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def parse_args():
    parser = argparse.ArgumentParser(
        description="LunarVision: Chandrayaan-2 Automated Planetary Registration Suite (ISRO SIH 26166)"
    )
    parser.add_argument("--src", "--source", dest="src", type=str, required=True,
                        help="Path to source image file or PDS4 XML label (.xml, .tif, .png, .jpg)")
    parser.add_argument("--ref", "--reference", dest="ref", type=str, required=True,
                        help="Path to reference image file or PDS4 XML label (.xml, .tif, .png, .jpg)")
    parser.add_argument("--anchor", type=str, default=None,
                        help="Optional intermediate scale anchor image (e.g. TMC-2 between IIRS and OHRC)")
    parser.add_argument("--dem", type=str, default=None,
                        help="Optional DEM raster file (.tif, .png, .npy) for LOLA/Kaguya photometric correction")
    parser.add_argument("--out", "--output-dir", dest="out", type=str, default="results",
                        help="Output directory for exported registered deliverables (default: results/)")
    parser.add_argument("--multimodal", action="store_true",
                        help="Set flag if source is hyperspectral infrared (IIRS)")
    parser.add_argument("--cascade", action="store_true", default=True,
                        help="Enable hierarchical coarse-to-fine scale cascade")
    parser.add_argument("--no-cascade", dest="cascade", action="store_false",
                        help="Disable hierarchical coarse-to-fine scale cascade")
    parser.add_argument("--steep-relief", "--tps", dest="steep_relief", action="store_true", default=True,
                        help="Apply non-rigid Thin-Plate Spline or Piecewise Affine for steep crater relief parallax")
    parser.add_argument("--tile", "--enable-tiling", dest="enable_tiling", action="store_true", default=None,
                        help="Force sliding-window tiled processing for large rasters")
    parser.add_argument("--matcher", type=str, default="phase_congruency",
                        choices=["phase_congruency", "sift", "orb", "loftr"],
                        help="Feature matching backend algorithm")
    parser.add_argument("--subpixel-method", type=str, default="ic_lk",
                        choices=["ic_lk", "phase_correlation", "quadratic_ncc", "parabolic"],
                        help="Sub-pixel refinement algorithm: Inverse-Compositional Lucas-Kanade, Fourier Phase Correlation, or 2D Quadratic/Hessian NCC")
    return parser.parse_args()

def main():
    args = parse_args()

    print("========================================================================")
    print(" [LUNARVISION] AUTOMATED PLANETARY REGISTRATION SUITE (ISRO 26166)")
    print("========================================================================")
    print(" [MODE] PRODUCTION MODE: Strict technical honesty enforced.")

    # -------------------------------------------------------------------------
    # STEP 1: DATA INGESTION (IMPORT)
    # -------------------------------------------------------------------------
    print("\n[1/4] INGESTING DATASETS...")
    print(f"  -> Source:    {args.src}")
    img_src, meta_src = PlanetaryDataManager.load_image(args.src)
    print(f"     Format:    {meta_src.get('format', 'UNKNOWN')} | Shape: {img_src.shape} | Dtype: {img_src.dtype}")
    if "sun_azimuth_deg" in meta_src:
        print(f"     Sun Geom:  Azimuth={meta_src['sun_azimuth_deg']}°, Elevation={meta_src['sun_elevation_deg']}°")

    print(f"  -> Reference: {args.ref}")
    img_ref, meta_ref = PlanetaryDataManager.load_image(args.ref)
    print(f"     Format:    {meta_ref.get('format', 'UNKNOWN')} | Shape: {img_ref.shape} | Dtype: {img_ref.dtype}")
    if "sun_azimuth_deg" in meta_ref:
        print(f"     Sun Geom:  Azimuth={meta_ref['sun_azimuth_deg']}°, Elevation={meta_ref['sun_elevation_deg']}°")

    # Ingest optional DEM
    dem_img = None
    if args.dem:
        print(f"  -> DEM Model: {args.dem}")
        dem_img, _ = PlanetaryDataManager.load_image(args.dem)
        print(f"     Shape:     {dem_img.shape} | Dtype: {dem_img.dtype}")

    # Ingest optional Anchor
    anchor_img = None
    if args.anchor:
        print(f"  -> Scale Anchor: {args.anchor}")
        anchor_img, _ = PlanetaryDataManager.load_image(args.anchor)
        print(f"     Shape:        {anchor_img.shape}")

    # -------------------------------------------------------------------------
    # STEP 2: REGISTRATION PIPELINE EXECUTION
    # -------------------------------------------------------------------------
    print("\n[2/4] EXECUTING CORRESPONDENCE & REGISTRATION PIPELINE...")
    pipeline = LunarVisionPipeline()
    result = pipeline.register(
        img_src,
        img_ref,
        is_multimodal=args.multimodal,
        is_steep_relief=args.steep_relief,
        matcher_backend=args.matcher,
        enable_scale_cascade=args.cascade,
        subpixel_method=args.subpixel_method,
        anchor_img=anchor_img,
        dem=dem_img,
        pds4_meta_src=meta_src,
        pds4_meta_ref=meta_ref,
        enable_tiling=args.enable_tiling
    )

    status = result.get("status", "SUCCESS")
    metrics = result.get("metrics", {})
    provenance = result.get("provenance", {})
    thresholds = provenance.get("quality_thresholds", resolve_quality_thresholds())

    os.makedirs(args.out, exist_ok=True)
    mission_info = {"source_metadata": meta_src, "reference_metadata": meta_ref}

    if status == "FAILED":
        reason = result.get("reason", "Unknown failure reason.")
        print(f"\n  [FAILURE] REGISTRATION FAILED: {reason}")
        print(f"  -> Matches found:     {result.get('match_count', 0)}")
        print(f"  -> Inliers:           {result.get('inlier_count', 0)}")
        print(f"\n[3/4] EXPORTING FAILURE AUDIT REPORT TO: {args.out}")

        cert_path = os.path.join(args.out, "quality_certification_report.json")
        PlanetaryDataManager.export_certification_report(metrics, cert_path, mission_info=mission_info)
        print(f"  [OK] Saved Failure Audit Report: {cert_path}")
        print(f"  [OK] Saved Markdown Audit Report: {os.path.splitext(cert_path)[0] + '.md'}")

        print("\n[4/4] DELIVERABLES WITHHELD")
        print("  [!] Registered product and visual overlays withheld due to failed certification gates.")
        print("\n========================================================================")
        print(" [RESULT] REGISTRATION REJECTED (TECHNICAL HONESTY ENFORCED)")
        print("========================================================================\n")
        sys.exit(1)

    warped_product = result["warped_image"]
    pts_src = result["pts_src"]
    pts_ref = result["pts_ref"]

    print(f"  -> Tie-points identified:    {len(pts_src)}")
    print(f"  -> Sub-pixel RMSE:           {metrics.get('subpixel_rmse_px', float('nan')):.3f} px (Target: <= {thresholds['max_subpixel_rmse_px']:.2f} px)")
    print(f"  -> Inlier Match Ratio:       {metrics.get('inlier_ratio_pct', 0.0):.1f}% (Target: >= {thresholds['min_inlier_ratio_pct']:.0f}%)")
    print(f"  -> Grid Spatial Coverage:    {metrics.get('grid_coverage_pct', 0.0):.1f}% (Target: >= {thresholds['min_grid_coverage_pct']:.0f}%)")
    print(f"  -> Voronoi Area Entropy:     {metrics.get('voronoi_entropy', 0.0):.3f} (Target: >= {thresholds['min_voronoi_entropy']:.2f})")
    print(f"  -> Structural Similarity:    {metrics.get('ssim', 0.0):.3f}")
    print(f"  -> Normalized Mutual Info:   {metrics.get('nmi', 0.0):.3f}")

    # -------------------------------------------------------------------------
    # STEP 3: EXPORTING DELIVERABLES
    # -------------------------------------------------------------------------
    print(f"\n[3/4] EXPORTING DELIVERABLE PRODUCTS TO: {args.out}")

    # 1. Georeferenced Registered Product (.tif + .tfw)
    prod_path = os.path.join(args.out, "registered_product.tif")
    PlanetaryDataManager.export_registered_product(warped_product, prod_path)
    print(f"  [OK] Saved: {prod_path} (with ESRI world file .tfw)")

    # 2. Ground Control Points (GCP) Tie-Points Table (.csv)
    gcp_path = os.path.join(args.out, "tie_points_gcp.csv")
    PlanetaryDataManager.export_tie_points(pts_src, pts_ref, gcp_path)
    print(f"  [OK] Saved: {gcp_path} (USGS ISIS3 & QGIS compatible)")

    # 3. ISRO Quality Certification Report (.json and .md)
    cert_path = os.path.join(args.out, "quality_certification_report.json")
    PlanetaryDataManager.export_certification_report(metrics, cert_path, mission_info=mission_info)
    print(f"  [OK] Saved: {cert_path}")
    print(f"  [OK] Saved: {os.path.splitext(cert_path)[0] + '.md'}")

    # -------------------------------------------------------------------------
    # STEP 4: VISUAL VERIFICATION EXPORT
    # -------------------------------------------------------------------------
    print("\n[4/4] GENERATING VISUAL VERIFICATION BLENDS...")

    # A. Seamless Checkerboard Blend
    cb = np.zeros_like(img_ref)
    sq = max(16, min(img_ref.shape[:2]) // 8)
    for y in range(0, img_ref.shape[0], sq):
        for x in range(0, img_ref.shape[1], sq):
            if ((x // sq) + (y // sq)) % 2 == 0:
                cb[y:y + sq, x:x + sq] = warped_product[y:y + sq, x:x + sq]
            else:
                cb[y:y + sq, x:x + sq] = img_ref[y:y + sq, x:x + sq]

    cb_path = os.path.join(args.out, "checkerboard_overlay.png")
    cv2.imwrite(cb_path, cb)
    print(f"  [OK] Saved: {cb_path}")

    # B. Standard Distinct Artifacts (warped.png, diff.png, phase.png)
    warped_path = os.path.join(args.out, "warped.png")
    cv2.imwrite(warped_path, warped_product)
    print(f"  [OK] Saved: {warped_path}")

    diff = cv2.absdiff(warped_product, img_ref)
    diff_colored = cv2.applyColorMap(diff, cv2.COLORMAP_INFERNO)
    diff_path = os.path.join(args.out, "diff.png")
    cv2.imwrite(diff_path, diff_colored)
    print(f"  [OK] Saved: {diff_path}")
    diff_hm_path = os.path.join(args.out, "difference_heatmap.png")
    cv2.imwrite(diff_hm_path, diff_colored)

    pc_src = result.get("pc_src", None)
    if pc_src is not None:
        pc_disp = (pc_src * 255).astype(np.uint8)
    else:
        src_disp = img_src if img_src.ndim == 2 else img_src[:, :, 0]
        pc_disp = cv2.normalize(cv2.Sobel(src_disp, cv2.CV_32F, 1, 1), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    phase_colored = cv2.applyColorMap(pc_disp, cv2.COLORMAP_VIRIDIS)
    phase_path = os.path.join(args.out, "phase.png")
    cv2.imwrite(phase_path, phase_colored)
    print(f"  [OK] Saved: {phase_path}")

    # C. Tie-Point Vector Overlay
    canvas = np.hstack([img_src, img_ref])
    if len(canvas.shape) == 2:
        canvas_bgr = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    else:
        canvas_bgr = canvas.copy()

    shift_x = img_src.shape[1]
    for i in range(min(50, len(pts_src))):
        p1 = (int(round(pts_src[i][0])), int(round(pts_src[i][1])))
        p2 = (int(round(pts_ref[i][0])) + shift_x, int(round(pts_ref[i][1])))
        cv2.line(canvas_bgr, p1, p2, (0, 230, 118), 1, cv2.LINE_AA)
        cv2.circle(canvas_bgr, p1, 3, (255, 229, 0), -1)
        cv2.circle(canvas_bgr, p2, 3, (255, 229, 0), -1)

    vec_path = os.path.join(args.out, "correspondence_vectors.png")
    cv2.imwrite(vec_path, canvas_bgr)
    print(f"  [OK] Saved: {vec_path}")

    print("\n========================================================================")
    print(" [DONE] REGISTRATION COMPLETED SUCCESSFULLY! ALL DELIVERABLES READY.")
    print("========================================================================\n")

if __name__ == "__main__":
    main()
