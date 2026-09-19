"""
Real PS execution: runs the LunarVisionPipeline on:
  1. Chandrayaan-1 benchmark sample images (tmc_stereo_aft/fore) — square 640×640 crops
  2. Chandrayaan-1 real TMC NCA/NCF browse strip (with CLAHE contrast enhancement)
Results saved to results/
"""
import sys, os, json, logging, time
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(ROOT, "results", "run_realdata.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("real_run")

from core.pipeline import LunarVisionPipeline
from core.quality import sanitize_for_json, evaluate_quality

pipeline = LunarVisionPipeline()
all_reports = {}

# ─────────────────────────────────────────────────────────────────────────────
def run_case(label, img_src, img_ref, meta_src, meta_ref, backend="sift", steep=True):
    log.info("")
    log.info("=" * 65)
    log.info(f"  CASE: {label}")
    log.info(f"  src={img_src.shape}  ref={img_ref.shape}  backend={backend}")
    log.info("=" * 65)
    t0 = time.time()
    result = pipeline.register(
        img_src=img_src,
        img_ref=img_ref,
        pds4_meta_src=meta_src,
        pds4_meta_ref=meta_ref,
        matcher_backend=backend,
        is_steep_relief=steep,
        enable_scale_cascade=False,
        enable_subpixel=True,
        subpixel_method="ic_lk",
        enable_tiling=False,
    )
    elapsed = time.time() - t0

    status = result.get("status", "UNKNOWN")
    metrics = result.get("metrics", {})
    gates   = evaluate_quality(metrics)
    all_pass = all(gates.values())

    log.info(f"  STATUS  : {status}   ({elapsed:.1f}s)")
    log.info(f"  Inliers : {metrics.get('inlier_count','N/A')}  ratio={metrics.get('inlier_ratio_pct','N/A')}%")
    log.info(f"  Sub-px RMSE     : {metrics.get('subpixel_rmse_px','N/A')} px")
    log.info(f"  Checkpoint RMSE : {metrics.get('checkpoint_rmse_px','N/A')} px")
    log.info(f"  NCC / SSIM / NMI: {metrics.get('ncc','N/A')} / {metrics.get('ssim','N/A')} / {metrics.get('nmi','N/A')}")
    log.info(f"  Grid coverage   : {metrics.get('grid_coverage_pct','N/A')}%")
    log.info(f"  Overlap         : {metrics.get('overlap_pct','N/A')}%")
    log.info(f"  Confidence      : {metrics.get('observable_confidence','N/A')}")
    log.info("  Quality Gates:")
    for gate, passed in gates.items():
        log.info(f"    [{'PASS' if passed else 'FAIL'}] {gate}")
    log.info(f"  >> {'ALL GATES PASS' if all_pass else 'SOME GATES FAILED'}")

    # Save warped image
    warped = result.get("warped_image")
    slug = label.replace(" ", "_").replace("/", "_")
    if warped is not None:
        cv2.imwrite(os.path.join(ROOT, "results", f"{slug}_warped.png"), warped)
        h = min(warped.shape[0], img_ref.shape[0])
        w = min(warped.shape[1], img_ref.shape[1])
        side = np.hstack([img_ref[:h, :w], warped[:h, :w]])
        cv2.imwrite(os.path.join(ROOT, "results", f"{slug}_comparison.png"), side)
        # Checkerboard
        TILE = 32
        checker = img_ref[:h, :w].copy()
        for ty in range(0, h, TILE):
            for tx in range(0, w, TILE):
                if ((ty // TILE) + (tx // TILE)) % 2 == 0:
                    checker[ty:ty+TILE, tx:tx+TILE] = warped[ty:ty+TILE, tx:tx+TILE]
        cv2.imwrite(os.path.join(ROOT, "results", f"{slug}_checkerboard.png"), checker)

    return {
        "status": status,
        "elapsed_s": round(elapsed, 2),
        "metrics": sanitize_for_json(metrics),
        "quality_gates": gates,
        "all_gates_pass": all_pass,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CASE 1: Benchmark TMC stereo aft/fore (640×640, well-conditioned square crop)
# ─────────────────────────────────────────────────────────────────────────────
log.info("Loading benchmark sample images …")
aft_bench  = cv2.imread(os.path.join(ROOT, "data/sample_benchmarks/tmc_stereo_aft.png"),  cv2.IMREAD_GRAYSCALE)
fore_bench = cv2.imread(os.path.join(ROOT, "data/sample_benchmarks/tmc_stereo_fore.png"), cv2.IMREAD_GRAYSCALE)
META_BENCH = {"sun_azimuth_deg": 168.7, "sun_elevation_deg": 11.6, "gsd_m": 10.66}

all_reports["benchmark_sift"] = run_case(
    "Benchmark TMC stereo SIFT",
    aft_bench, fore_bench, META_BENCH, META_BENCH,
    backend="sift"
)
all_reports["benchmark_pc"] = run_case(
    "Benchmark TMC stereo Phase-Congruency",
    aft_bench, fore_bench, META_BENCH, META_BENCH,
    backend="phase_congruency"
)

# ─────────────────────────────────────────────────────────────────────────────
# CASE 2: OHRC fine crop vs TMC-2 coarse map (multimodal, different GSD)
# ─────────────────────────────────────────────────────────────────────────────
ohrc_crop = cv2.imread(os.path.join(ROOT, "data/sample_benchmarks/ohrc_fine_crop.png"),   cv2.IMREAD_GRAYSCALE)
tmc2_map  = cv2.imread(os.path.join(ROOT, "data/sample_benchmarks/tmc2_coarse_map.png"),  cv2.IMREAD_GRAYSCALE)
META_OHRC  = {"sun_azimuth_deg": 90.0, "sun_elevation_deg": 45.0, "gsd_m": 0.25}
META_TMC2  = {"sun_azimuth_deg": 90.0, "sun_elevation_deg": 45.0, "gsd_m": 5.0}

all_reports["ohrc_tmc2_sift"] = run_case(
    "OHRC vs TMC-2 SIFT",
    ohrc_crop, tmc2_map, META_OHRC, META_TMC2,
    backend="sift"
)

# ─────────────────────────────────────────────────────────────────────────────
# CASE 3: Real NCA/NCF strip — CLAHE enhanced, best 2048-row segment
# ─────────────────────────────────────────────────────────────────────────────
log.info("Loading real TMC NCA/NCF browse strips …")
nca_raw = cv2.imread(os.path.join(ROOT,
    "realdata/ch1_tmc_nca_20090529T0853239926_d_img_d18/browse/calibrated/20090529",
    "ch1_tmc_nca_20090529T0853239926_b_brw_d18.png"), cv2.IMREAD_GRAYSCALE)
ncf_raw = cv2.imread(os.path.join(ROOT,
    "realdata/ch1_tmc_ncf_20090529T0853239926_d_img_d18/browse/calibrated/20090529",
    "ch1_tmc_ncf_20090529T0853239926_b_brw_d18.png"), cv2.IMREAD_GRAYSCALE)

CROP_H = 2048
src_row = (nca_raw.shape[0] - CROP_H) // 2
ref_row = (ncf_raw.shape[0] - CROP_H) // 2
src_strip = nca_raw[src_row:src_row + CROP_H, :]
ref_strip = ncf_raw[ref_row:ref_row + CROP_H, :]

# CLAHE: Contrast Limited Adaptive Histogram Equalization
# Essential for very dark lunar shadow terrain (mean DN=30)
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
src_enhanced = clahe.apply(src_strip)
ref_enhanced = clahe.apply(ref_strip)

META_NCA = {"sun_azimuth_deg": 168.719, "sun_elevation_deg": 11.632, "gsd_m": 10.66,
            "sensor": "TMC-NCA", "mission": "Chandrayaan-1", "date": "2009-05-29"}
META_NCF = {"sun_azimuth_deg": 168.719, "sun_elevation_deg": 11.632, "gsd_m": 10.66,
            "sensor": "TMC-NCF", "mission": "Chandrayaan-1", "date": "2009-05-29"}

all_reports["real_nca_ncf_sift"] = run_case(
    "Real NCA-NCF strip SIFT",
    src_enhanced, ref_enhanced, META_NCA, META_NCF,
    backend="sift"
)
all_reports["real_nca_ncf_orb"] = run_case(
    "Real NCA-NCF strip ORB",
    src_enhanced, ref_enhanced, META_NCA, META_NCF,
    backend="orb"
)

# For the strip: try Phase Congruency (illumination-invariant, handles shadow-heavy lunar strip)
all_reports["real_nca_ncf_pc"] = run_case(
    "Real NCA-NCF strip Phase-Congruency",
    src_enhanced, ref_enhanced, META_NCA, META_NCF,
    backend="phase_congruency"
)

# ─────────────────────────────────────────────────────────────────────────────
# Save combined report
# ─────────────────────────────────────────────────────────────────────────────
json_path = os.path.join(ROOT, "results", "realdata_registration_report.json")
with open(json_path, "w", encoding="utf-8") as fh:
    json.dump(all_reports, fh, indent=2)
log.info(f"\nFull report saved: {json_path}")

# Summary table
log.info("\n" + "=" * 65)
log.info("  FINAL SUMMARY")
log.info("=" * 65)
any_pass = False
for name, rep in all_reports.items():
    icon = "PASS" if rep["all_gates_pass"] else "FAIL"
    inl  = rep["metrics"].get("inlier_count", "?")
    rmse = rep["metrics"].get("subpixel_rmse_px", "?")
    log.info(f"  [{icon}] {name:<42} inliers={inl}  RMSE={rmse} px")
    if rep["all_gates_pass"]:
        any_pass = True
log.info("=" * 65)
sys.exit(0 if any_pass else 2)
