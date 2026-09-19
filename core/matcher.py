"""
Production Hybrid Planetary Matcher Interface
Supports:
1. Phase-Congruency / RIFT Matcher (CH-01: Radiation & illumination invariant).
2. SIFT Matcher (Scale-Invariant Feature Transform baseline).
3. ORB Matcher (Fast binary feature baseline).
4. LoFTR Matcher (Optional Deep Detector-Free Matcher with graceful fallback).
"""

from dataclasses import dataclass, field
import numpy as np
import cv2

from core.ch01_sun_angle_invariance import SunAngleInvarianceEngine


@dataclass
class MatchResult:
    """Standardized result container for planetary image matching."""
    pts_src: np.ndarray
    pts_ref: np.ndarray
    descriptor_scores: np.ndarray = field(default_factory=lambda: np.array([]))
    match_distances: np.ndarray = field(default_factory=lambda: np.array([]))
    inlier_mask: np.ndarray = field(default_factory=lambda: np.array([]))
    geometric_residuals: np.ndarray = field(default_factory=lambda: np.array([]))
    backend_name: str = "unknown"
    metadata: dict = field(default_factory=dict)

    @property
    def inlier_count(self) -> int:
        if len(self.inlier_mask) == 0:
            return 0
        return int(np.sum(self.inlier_mask))

    @property
    def inlier_ratio(self) -> float:
        if len(self.pts_src) == 0:
            return 0.0
        return float(self.inlier_count / len(self.pts_src) * 100.0)


class BasePlanetaryMatcher:
    """Abstract base class for all lunar image matchers."""

    def match(self, img_src: np.ndarray, img_ref: np.ndarray, roi=None) -> MatchResult:
        raise NotImplementedError


class PhaseCongruencyMatcher(BasePlanetaryMatcher):
    """
    Log-Gabor Phase Congruency & RIFT feature matching.
    Highest invariance against extreme sun-angle differences and shadow inversions.
    """

    def __init__(self, n_scales=4, n_orientations=6, ratio_thresh=0.80, max_points=350):
        self.engine = SunAngleInvarianceEngine(n_scales=n_scales, n_orientations=n_orientations)
        self.ratio_thresh = ratio_thresh
        self.max_points = max_points

    def match(self, img_src: np.ndarray, img_ref: np.ndarray, roi=None) -> MatchResult:
        h, w = img_src.shape[:2]
        kps1, desc1, pc1 = self.engine.extract_features(img_src, max_points=self.max_points)
        kps2, desc2, pc2 = self.engine.extract_features(img_ref, max_points=self.max_points)

        if len(desc1) == 0 or len(desc2) == 0:
            return MatchResult(
                pts_src=np.empty((0, 2), dtype=np.float32),
                pts_ref=np.empty((0, 2), dtype=np.float32),
                backend_name="phase_congruency_rift",
                metadata={"pc_src": pc1, "pc_ref": pc2, "status": "No descriptors extracted"}
            )

        # L2 Distance ratio matching
        bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        matches = bf.knnMatch(desc1, desc2, k=2)

        good_pts1 = []
        good_pts2 = []
        distances = []
        scores = []

        for m_pair in matches:
            if len(m_pair) == 2:
                m, n = m_pair
                if m.distance < self.ratio_thresh * n.distance:
                    good_pts1.append(kps1[m.queryIdx].pt)
                    good_pts2.append(kps2[m.trainIdx].pt)
                    distances.append(m.distance)
                    # Score derived from ratio confidence
                    ratio_score = 1.0 - (m.distance / max(n.distance, 1e-6))
                    scores.append(float(np.clip(ratio_score, 0.0, 1.0)))

        if len(good_pts1) < 4:
            return MatchResult(
                pts_src=np.array(good_pts1, dtype=np.float32).reshape(-1, 2) if good_pts1 else np.empty((0, 2), dtype=np.float32),
                pts_ref=np.array(good_pts2, dtype=np.float32).reshape(-1, 2) if good_pts2 else np.empty((0, 2), dtype=np.float32),
                backend_name="phase_congruency_rift",
                metadata={"pc_src": pc1, "pc_ref": pc2, "status": "Insufficient matches (< 4)"}
            )

        pts1 = np.array(good_pts1, dtype=np.float32)
        pts2 = np.array(good_pts2, dtype=np.float32)

        # Initial RANSAC homography for geometric inlier detection
        H, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 4.0)
        mask_bool = inlier_mask.ravel().astype(bool) if inlier_mask is not None else np.zeros(len(pts1), dtype=bool)

        # Compute geometric reprojection residuals for each pair
        residuals = np.zeros(len(pts1), dtype=np.float32)
        if H is not None and np.sum(mask_bool) >= 4:
            pts1_h = np.hstack([pts1, np.ones((len(pts1), 1), dtype=np.float32)])
            proj = (H @ pts1_h.T).T
            proj = proj[:, :2] / np.maximum(proj[:, 2:], 1e-7)
            residuals = np.linalg.norm(proj - pts2, axis=1)

        return MatchResult(
            pts_src=pts1,
            pts_ref=pts2,
            descriptor_scores=np.array(scores, dtype=np.float32),
            match_distances=np.array(distances, dtype=np.float32),
            inlier_mask=mask_bool,
            geometric_residuals=residuals,
            backend_name="phase_congruency_rift",
            metadata={"pc_src": pc1, "pc_ref": pc2, "homography": H}
        )


class SIFTMatcher(BasePlanetaryMatcher):
    """SIFT feature detector and descriptor baseline."""

    def __init__(self, n_features=600, ratio_thresh=0.75):
        self.n_features = n_features
        self.ratio_thresh = ratio_thresh

    def match(self, img_src: np.ndarray, img_ref: np.ndarray, roi=None) -> MatchResult:
        sift = cv2.SIFT_create(nfeatures=self.n_features)
        kp1, des1 = sift.detectAndCompute(img_src, None)
        kp2, des2 = sift.detectAndCompute(img_ref, None)

        if des1 is None or des2 is None or len(des1) < 4 or len(des2) < 4:
            return MatchResult(
                pts_src=np.empty((0, 2), dtype=np.float32),
                pts_ref=np.empty((0, 2), dtype=np.float32),
                backend_name="sift",
                metadata={"status": "Insufficient SIFT keypoints"}
            )

        bf = cv2.BFMatcher(cv2.NORM_L2)
        matches = bf.knnMatch(des1, des2, k=2)

        good_pts1 = []
        good_pts2 = []
        distances = []
        scores = []

        for m_pair in matches:
            if len(m_pair) == 2:
                m, n = m_pair
                if m.distance < self.ratio_thresh * n.distance:
                    good_pts1.append(kp1[m.queryIdx].pt)
                    good_pts2.append(kp2[m.trainIdx].pt)
                    distances.append(m.distance)
                    scores.append(float(1.0 - (m.distance / max(n.distance, 1e-6))))

        if len(good_pts1) < 4:
            return MatchResult(
                pts_src=np.array(good_pts1, dtype=np.float32).reshape(-1, 2) if good_pts1 else np.empty((0, 2), dtype=np.float32),
                pts_ref=np.array(good_pts2, dtype=np.float32).reshape(-1, 2) if good_pts2 else np.empty((0, 2), dtype=np.float32),
                backend_name="sift",
                metadata={"status": "Insufficient good matches (< 4)"}
            )

        pts1 = np.array(good_pts1, dtype=np.float32)
        pts2 = np.array(good_pts2, dtype=np.float32)

        H, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 3.5)
        mask_bool = inlier_mask.ravel().astype(bool) if inlier_mask is not None else np.zeros(len(pts1), dtype=bool)

        residuals = np.zeros(len(pts1), dtype=np.float32)
        if H is not None and np.sum(mask_bool) >= 4:
            pts1_h = np.hstack([pts1, np.ones((len(pts1), 1), dtype=np.float32)])
            proj = (H @ pts1_h.T).T
            proj = proj[:, :2] / np.maximum(proj[:, 2:], 1e-7)
            residuals = np.linalg.norm(proj - pts2, axis=1)

        return MatchResult(
            pts_src=pts1,
            pts_ref=pts2,
            descriptor_scores=np.array(scores, dtype=np.float32),
            match_distances=np.array(distances, dtype=np.float32),
            inlier_mask=mask_bool,
            geometric_residuals=residuals,
            backend_name="sift",
            metadata={"homography": H}
        )


class ORBMatcher(BasePlanetaryMatcher):
    """ORB (Oriented FAST and Rotated BRIEF) fast binary matcher."""

    def __init__(self, n_features=800):
        self.n_features = n_features

    def match(self, img_src: np.ndarray, img_ref: np.ndarray, roi=None) -> MatchResult:
        orb = cv2.ORB_create(nfeatures=self.n_features)
        kp1, des1 = orb.detectAndCompute(img_src, None)
        kp2, des2 = orb.detectAndCompute(img_ref, None)

        if des1 is None or des2 is None or len(des1) < 4 or len(des2) < 4:
            return MatchResult(
                pts_src=np.empty((0, 2), dtype=np.float32),
                pts_ref=np.empty((0, 2), dtype=np.float32),
                backend_name="orb",
                metadata={"status": "Insufficient ORB keypoints"}
            )

        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)
        matches = sorted(matches, key=lambda x: x.distance)

        pts1 = np.float32([kp1[m.queryIdx].pt for m in matches])
        pts2 = np.float32([kp2[m.trainIdx].pt for m in matches])
        distances = np.float32([m.distance for m in matches])
        scores = np.float32([max(0.0, 1.0 - m.distance / 256.0) for m in matches])

        if len(pts1) < 4:
            return MatchResult(
                pts_src=pts1,
                pts_ref=pts2,
                backend_name="orb",
                metadata={"status": "Insufficient matches (< 4)"}
            )

        H, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 4.0)
        mask_bool = inlier_mask.ravel().astype(bool) if inlier_mask is not None else np.zeros(len(pts1), dtype=bool)

        residuals = np.zeros(len(pts1), dtype=np.float32)
        if H is not None and np.sum(mask_bool) >= 4:
            pts1_h = np.hstack([pts1, np.ones((len(pts1), 1), dtype=np.float32)])
            proj = (H @ pts1_h.T).T
            proj = proj[:, :2] / np.maximum(proj[:, 2:], 1e-7)
            residuals = np.linalg.norm(proj - pts2, axis=1)

        return MatchResult(
            pts_src=pts1,
            pts_ref=pts2,
            descriptor_scores=scores,
            match_distances=distances,
            inlier_mask=mask_bool,
            geometric_residuals=residuals,
            backend_name="orb",
            metadata={"homography": H}
        )


class LoFTRMatcher(BasePlanetaryMatcher):
    """
    Optional Deep Detector-Free Matcher (LoFTR).
    Strict honesty rule: If PyTorch or Kornia is not installed, does not fake execution;
    returns explicit status and delegates to PhaseCongruencyMatcher fallback.
    """

    def __init__(self):
        self._is_available = False
        try:
            import torch
            import kornia
            self._is_available = True
        except ImportError:
            self._is_available = False

    @property
    def is_available(self) -> bool:
        return self._is_available

    def match(self, img_src: np.ndarray, img_ref: np.ndarray, roi=None) -> MatchResult:
        if not self._is_available:
            raise RuntimeError(
                "LoFTR backend requires PyTorch and Kornia which are not installed in the environment."
            )
        # Deep matching logic if libraries are present (kept clean and guarded)
        import torch
        from kornia.feature import LoFTR
        matcher = LoFTR(pretrained='outdoor')
        t_src = torch.from_numpy(img_src).float()[None, None] / 255.0
        t_ref = torch.from_numpy(img_ref).float()[None, None] / 255.0
        with torch.no_grad():
            corr = matcher({'image0': t_src, 'image1': t_ref})
        pts1 = corr['keypoints0'].cpu().numpy()
        pts2 = corr['keypoints1'].cpu().numpy()
        conf = corr['confidence'].cpu().numpy()

        H, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 3.0)
        mask_bool = inlier_mask.ravel().astype(bool) if inlier_mask is not None else np.zeros(len(pts1), dtype=bool)

        return MatchResult(
            pts_src=pts1,
            pts_ref=pts2,
            descriptor_scores=conf,
            inlier_mask=mask_bool,
            backend_name="loftr",
            metadata={"loftr_executed": True}
        )


class HybridMatcher:
    """
    Factory and orchestrator for selecting matchers.
    Gracefully handles optional dependencies and records what actually executed.
    """

    @staticmethod
    def get_matcher(backend: str = "phase_congruency"):
        backend = (backend or "phase_congruency").lower()
        if backend in ["rift", "phase_congruency", "pc"]:
            return PhaseCongruencyMatcher(), "phase_congruency_rift", None
        elif backend == "sift":
            return SIFTMatcher(), "sift", None
        elif backend == "orb":
            return ORBMatcher(), "orb", None
        elif backend == "loftr":
            loftr = LoFTRMatcher()
            if loftr.is_available:
                return loftr, "loftr", None
            else:
                fallback_note = (
                    "LoFTR requested but PyTorch/Kornia dependencies are not installed; "
                    "fell back honestly to Phase-Congruency/RIFT baseline."
                )
                return PhaseCongruencyMatcher(), "phase_congruency_rift", fallback_note
        else:
            return PhaseCongruencyMatcher(), "phase_congruency_rift", f"Unknown backend '{backend}'; defaulted to phase_congruency."
