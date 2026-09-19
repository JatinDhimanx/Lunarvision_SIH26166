"""
Evaluation Metrics for Lunar Image Correspondence & Registration
Implements mathematical verification metrics demanded by ISRO:
1. Sub-pixel RMSE (Root Mean Square Error).
2. Inlier Match Ratio (IR %).
3. Grid Coverage Ratio (Spatial Uniformity).
4. Voronoi Area Entropy (Spatial Uniformity Certification).
5. Structural Similarity Index (SSIM).
6. Normalized Mutual Information (NMI).
7. Normalized Cross Correlation (NCC).
8. Real Observable Confidence Scoring (no synthetic formulas).
"""

import numpy as np
from scipy.spatial import Voronoi
import cv2


def compute_rmse(pts_pred: np.ndarray, pts_gt: np.ndarray) -> float:
    """
    Computes Root Mean Square Error between predicted and ground truth coordinates.
    pts_pred, pts_gt: shape (N, 2)
    Returns scalar RMSE in pixels. Returns float('nan') if points are empty.
    """
    if len(pts_pred) == 0 or len(pts_gt) == 0:
        return float('nan')
    diff = pts_pred - pts_gt
    sq_dist = np.sum(diff**2, axis=1)
    return float(np.sqrt(np.mean(sq_dist)))


def compute_inlier_ratio(n_inliers: int, n_total: int) -> float:
    """Computes inlier ratio percentage [0, 100]."""
    if n_total <= 0:
        return 0.0
    return float((n_inliers / n_total) * 100.0)


def compute_grid_coverage(pts: np.ndarray, image_shape, grid_bins=(12, 12)) -> float:
    """
    Measures percentage of grid bins that contain at least 1 verified tie-point.
    ISRO requirement: prevent clustering on crater rims alone.
    """
    if len(pts) == 0:
        return 0.0
    h, w = image_shape[:2]
    ny, nx = grid_bins

    bin_x = np.clip((pts[:, 0] / max(w, 1) * nx).astype(int), 0, nx - 1)
    bin_y = np.clip((pts[:, 1] / max(h, 1) * ny).astype(int), 0, ny - 1)

    occupied = np.zeros((ny, nx), dtype=bool)
    occupied[bin_y, bin_x] = True

    coverage = np.sum(occupied) / (nx * ny) * 100.0
    return float(coverage)


def compute_voronoi_entropy(pts: np.ndarray, image_shape) -> float:
    """
    Computes normalized Voronoi polygon area entropy.
    H_norm = 1.0 represents perfectly uniform spatial point distribution.
    Clustered points have low entropy (< 0.6).
    """
    if len(pts) < 5:
        return 0.0

    h, w = image_shape[:2]
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float32)
    all_pts = np.vstack([pts, corners])

    try:
        vor = Voronoi(all_pts)
        areas = []
        for region_idx in vor.point_region[:len(pts)]:
            region = vor.regions[region_idx]
            if not region or -1 in region:
                continue
            poly = np.array([vor.vertices[i] for i in region])
            poly[:, 0] = np.clip(poly[:, 0], 0, w)
            poly[:, 1] = np.clip(poly[:, 1], 0, h)
            x = poly[:, 0]
            y = poly[:, 1]
            area = 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
            if area > 1.0:
                areas.append(area)

        if len(areas) < 3:
            return 0.0

        areas = np.array(areas)
        p = areas / np.sum(areas)
        p = p[p > 0]
        entropy = -np.sum(p * np.log2(p))
        max_entropy = np.log2(len(areas))
        h_norm = entropy / (max_entropy + 1e-6)
        return float(np.clip(h_norm, 0.0, 1.0))
    except Exception:
        return 0.0


def compute_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """Computes Mean Structural Similarity Index between two registered grayscale images."""
    if img1 is None or img2 is None or img1.shape != img2.shape:
        return 0.0
    i1 = img1.astype(np.float64)
    i2 = img2.astype(np.float64)

    c1 = (0.01 * 255)**2
    c2 = (0.03 * 255)**2

    kernel = cv2.getGaussianKernel(11, 1.5)
    window = np.outer(kernel, kernel.transpose())

    mu1 = cv2.filter2D(i1, -1, window)[5:-5, 5:-5]
    mu2 = cv2.filter2D(i2, -1, window)[5:-5, 5:-5]

    mu1_sq = mu1**2
    mu2_sq = mu2**2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.filter2D(i1**2, -1, window)[5:-5, 5:-5] - mu1_sq
    sigma2_sq = cv2.filter2D(i2**2, -1, window)[5:-5, 5:-5] - mu2_sq
    sigma12 = cv2.filter2D(i1 * i2, -1, window)[5:-5, 5:-5] - mu1_mu2

    denom = (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    valid_mask = denom > 1e-6
    ssim_map = np.zeros_like(denom)
    ssim_map[valid_mask] = ((2 * mu1_mu2[valid_mask] + c1) * (2 * sigma12[valid_mask] + c2)) / denom[valid_mask]
    return float(np.clip(np.mean(ssim_map), 0.0, 1.0))


def compute_nmi(img1: np.ndarray, img2: np.ndarray, bins: int = 64) -> float:
    """Computes Normalized Mutual Information between two registered images."""
    if img1 is None or img2 is None or img1.shape != img2.shape:
        return 0.0
    hist_2d, _, _ = np.histogram2d(img1.ravel(), img2.ravel(), bins=bins)
    pxy = hist_2d / float(np.sum(hist_2d))
    px = np.sum(pxy, axis=1)
    py = np.sum(pxy, axis=0)

    px = px[px > 0]
    py = py[py > 0]
    pxy = pxy[pxy > 0]

    hx = -np.sum(px * np.log2(px))
    hy = -np.sum(py * np.log2(py))
    hxy = -np.sum(pxy * np.log2(pxy))

    nmi = 2.0 * (hx + hy - hxy) / (hx + hy + 1e-6)
    return float(np.clip(nmi, 0.0, 1.0))


def compute_ncc(img1: np.ndarray, img2: np.ndarray) -> float:
    """Computes Normalized Cross Correlation across overlapping image pixels."""
    if img1 is None or img2 is None or img1.shape != img2.shape:
        return 0.0
    f1 = img1.astype(np.float32)
    f2 = img2.astype(np.float32)
    f1_mean = np.mean(f1)
    f2_mean = np.mean(f2)
    f1_std = np.std(f1) + 1e-6
    f2_std = np.std(f2) + 1e-6
    ncc = float(np.mean((f1 - f1_mean) * (f2 - f2_mean)) / (f1_std * f2_std))
    return float(np.clip(ncc, -1.0, 1.0))


def compute_observable_confidence(
    descriptor_score: float,
    ncc_quality: float,
    subpixel_step_px: float,
    ransac_residual_px: float
) -> float:
    """
    Computes rigorous point confidence purely from observable physical quantities:
    - descriptor_score: feature matching ratio/confidence [0, 1]
    - ncc_quality: patch normalized cross correlation after IC-LK convergence [0, 1]
    - subpixel_step_px: optimizer step magnitude (smaller = higher confidence)
    - ransac_residual_px: global reprojection error (smaller = higher confidence)
    Never uses hardcoded random constants.
    """
    s_desc = float(np.clip(descriptor_score, 0.0, 1.0))
    s_ncc = float(np.clip(ncc_quality, 0.0, 1.0))

    # Reprojection penalty: drops to 0 at 3.0 px error
    s_reproj = float(np.clip(1.0 - (ransac_residual_px / 3.0), 0.0, 1.0))

    # Subpixel displacement penalty: drops to 0 at 1.0 px step
    s_step = float(np.clip(1.0 - (subpixel_step_px / 1.0), 0.0, 1.0))

    # Weighted combination: 30% descriptor, 30% NCC patch match, 25% geometric reprojection, 15% step convergence
    conf = 0.30 * s_desc + 0.30 * s_ncc + 0.25 * s_reproj + 0.15 * s_step
    return float(np.clip(conf, 0.0, 1.0))
