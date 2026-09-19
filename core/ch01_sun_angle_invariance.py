"""
Challenge 01: Solar Illumination & Sun-Angle Invariance Engine
Algorithm: Phase Congruency (PC) & RIFT (Radiation-variation Insensitive Feature Transform)
Key Innovation: 
Unlike intensity gradients (which invert and flip 180° when the sun shifts), Phase Congruency 
measures frequency-domain phase alignment of Log-Gabor wavelets. It isolates the TRUE physical 
crater rim geometry regardless of whether shadows fall to the left, right, or are pitch black.
"""

import numpy as np
import cv2
from scipy.fft import fft2, ifft2, fftshift

class SunAngleInvarianceEngine:
    """Computes illumination-invariant structural representations and RIFT descriptors."""

    def __init__(self, n_scales=4, n_orientations=6, min_wavelength=3, mult=2.1, sigma_onf=0.55):
        self.n_scales = n_scales
        self.n_orientations = n_orientations
        self.min_wavelength = min_wavelength
        self.mult = mult
        self.sigma_onf = sigma_onf

    def _construct_log_gabor_filters(self, rows, cols):
        """Constructs 2D Log-Gabor filter bank in frequency domain."""
        y = np.linspace(-0.5, 0.5, rows)
        x = np.linspace(-0.5, 0.5, cols)
        X, Y = np.meshgrid(x, y)
        radius = np.sqrt(X**2 + Y**2)
        radius[rows // 2, cols // 2] = 1.0  # Avoid log(0) at DC center

        theta = np.arctan2(-Y, X)
        sintheta = np.sin(theta)
        costheta = np.cos(theta)

        filters = []
        for o in range(self.n_orientations):
            angle = o * np.pi / self.n_orientations
            # Angular spread filter
            ds = sintheta * np.cos(angle) - costheta * np.sin(angle)
            dc = costheta * np.cos(angle) + sintheta * np.sin(angle)
            dtheta = np.abs(np.arctan2(ds, dc))
            spread = np.exp(-0.5 * (dtheta / (np.pi / self.n_orientations * 0.7))**2)

            for s in range(self.n_scales):
                wavelength = self.min_wavelength * (self.mult**s)
                fo = 1.0 / wavelength
                # Radial Log-Gabor filter
                radial = np.exp(-0.5 * (np.log(radius / fo) / self.sigma_onf)**2)
                radial[rows // 2, cols // 2] = 0.0  # Zero DC component

                lg = fftshift(radial * spread)
                filters.append((lg, o, s))

        return filters

    def compute_phase_congruency(self, image):
        """
        Computes Maximum Moment of Phase Congruency (PC_max).
        PC_max is strictly invariant to monotonic contrast changes and shadow reversals.
        """
        if image.ndim == 3:
            img = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
        else:
            img = image.astype(np.float32)
        rows, cols = img.shape
        img_fft = fft2(img)

        filters = self._construct_log_gabor_filters(rows, cols)

        sum_e = np.zeros((rows, cols), dtype=np.float32)
        sum_o = np.zeros((rows, cols), dtype=np.float32)
        sum_an = np.zeros((rows, cols), dtype=np.float32)

        pc_orients = np.zeros((self.n_orientations, rows, cols), dtype=np.float32)

        for lg, o, s in filters:
            # Complex response in spatial domain
            c_resp = ifft2(img_fft * lg)
            e = np.real(c_resp)
            o_resp = np.imag(c_resp)
            an = np.sqrt(e**2 + o_resp**2)

            sum_e += e
            sum_o += o_resp
            sum_an += an
            pc_orients[o] += an

        # Phase congruency formulation (Kovesi model)
        phase_deviation = np.sqrt(sum_e**2 + sum_o**2)
        pc_energy = np.maximum(phase_deviation - 0.1 * np.mean(sum_an), 0.0)
        pc_max = pc_energy / (sum_an + 1e-4)

        # Normalize to [0, 1]
        pc_max = cv2.normalize(pc_max, None, 0.0, 1.0, cv2.NORM_MINMAX)
        return pc_max, pc_orients

    def compute_maximum_index_map(self, pc_orients):
        """
        Computes Maximum Index Map (MIM) from multi-orientation Log-Gabor responses.
        MIM records the orientation layer with maximum response, providing robust structural indices.
        """
        mim = np.argmax(pc_orients, axis=0).astype(np.uint8)
        return mim

    def extract_features(self, image, max_points=300):
        """
        Extracts keypoints and RIFT descriptors:
        1. Keypoints detected on the illumination-invariant PC_max map (crater rims and central peaks).
        2. RIFT descriptor computed from orientation histogram of the Maximum Index Map (MIM).
        """
        pc_max, pc_orients = self.compute_phase_congruency(image)
        mim = self.compute_maximum_index_map(pc_orients)

        # Detect salient points on PC_max map using FAST / Harris
        pc_uint8 = (pc_max * 255).astype(np.uint8)
        # Threshold 12 (was 25): captures keypoints on low-contrast lunar maria plains
        fast = cv2.FastFeatureDetector_create(threshold=12, nonmaxSuppression=True)
        raw_kps = fast.detect(pc_uint8, None)

        if len(raw_kps) == 0:
            # Adaptive fallback: progressively loosen FAST then switch to Harris corners
            for thresh in [8, 5]:
                fast_loose = cv2.FastFeatureDetector_create(threshold=thresh, nonmaxSuppression=True)
                raw_kps = fast_loose.detect(pc_uint8, None)
                if len(raw_kps) > 0:
                    break

        if len(raw_kps) == 0:
            # Final fallback: GoodFeaturesToTrack with permissive quality level
            pts = cv2.goodFeaturesToTrack(pc_uint8, maxCorners=max_points, qualityLevel=0.02, minDistance=8)
            if pts is not None:
                raw_kps = [cv2.KeyPoint(float(p[0, 0]), float(p[0, 1]), 15) for p in pts]

        # Sort and prune to max_points
        raw_kps = sorted(raw_kps, key=lambda k: k.response if hasattr(k, 'response') else 0, reverse=True)[:max_points]

        # Compute patch RIFT descriptors (histogram of MIM orientations in 4x4 spatial cells)
        patch_size = 32
        cell_size = 8
        half = patch_size // 2

        valid_kps = []
        descriptors = []

        h, w = image.shape
        for kp in raw_kps:
            x, y = int(round(kp.pt[0])), int(round(kp.pt[1]))
            if x - half < 0 or x + half >= w or y - half < 0 or y + half >= h:
                continue

            patch_mim = mim[y - half:y + half, x - half:x + half]
            # Build 4x4 spatial cells histogram
            desc_cells = []
            for cy in range(0, patch_size, cell_size):
                for cx in range(0, patch_size, cell_size):
                    cell = patch_mim[cy:cy + cell_size, cx:cx + cell_size]
                    hist, _ = np.histogram(cell, bins=self.n_orientations, range=(0, self.n_orientations))
                    desc_cells.append(hist.astype(np.float32))

            desc_vec = np.concatenate(desc_cells)
            norm = np.linalg.norm(desc_vec) + 1e-6
            desc_vec /= norm

            valid_kps.append(kp)
            descriptors.append(desc_vec)

        if len(descriptors) == 0:
            return [], np.empty((0, 96), dtype=np.float32), pc_max

        return valid_kps, np.array(descriptors, dtype=np.float32), pc_max

    def match_images(self, img1, img2, ratio_thresh=0.85):
        """
        Matches two images with severe sun-angle difference / inverted shadows.
        Returns: pts1, pts2, inlier_mask, pc1, pc2
        """
        kps1, desc1, pc1 = self.extract_features(img1)
        kps2, desc2, pc2 = self.extract_features(img2)

        if len(desc1) == 0 or len(desc2) == 0:
            return np.empty((0, 2)), np.empty((0, 2)), np.array([]), pc1, pc2

        # Nearest neighbor matching with distance ratio
        bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        matches = bf.knnMatch(desc1, desc2, k=2)

        good_pts1 = []
        good_pts2 = []
        for m_pair in matches:
            if len(m_pair) == 2:
                m, n = m_pair
                if m.distance < ratio_thresh * n.distance:
                    good_pts1.append(kps1[m.queryIdx].pt)
                    good_pts2.append(kps2[m.trainIdx].pt)

        if len(good_pts1) < 4:
            return np.empty((0, 2)), np.empty((0, 2)), np.array([]), pc1, pc2

        pts1 = np.array(good_pts1, dtype=np.float32)
        pts2 = np.array(good_pts2, dtype=np.float32)

        # RANSAC outlier rejection
        _, inlier_mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 4.0)
        inlier_mask = inlier_mask.ravel().astype(bool) if inlier_mask is not None else np.zeros(len(pts1), dtype=bool)

        return pts1, pts2, inlier_mask, pc1, pc2
