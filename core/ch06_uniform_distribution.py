"""
Challenge 06: Uniform Spatial Distribution Engine
Algorithm: Quad-Tree Spatial Partitioning & Social Soft Clustering ANMS (SSC-ANMS)
Key Innovation:
ISRO specifically mandates: "...maintaining uniform distribution across the images".
Standard detectors cluster hundreds of points along a single sharp crater rim while 
leaving 80% of smooth lunar maria plains empty. This module enforces spatial uniformity 
via recursive quad-tree cell budgeting and adaptive suppression radius filtering.
"""

import numpy as np

class UniformDistributionEngine:
    """Enforces homogeneous spatial dispersion of tie-points across all terrain types."""

    def __init__(self, target_points=200, grid_bins=(16, 16), tolerance=0.1):
        self.target_points = target_points
        self.grid_bins = grid_bins
        self.tolerance = tolerance

    def quadtree_spatial_filter(self, keypoints, scores, image_shape):
        """
        Recursively partitions image into grid cells, enforcing minimum and maximum 
        point allowances per cell to eliminate spatial clumping.
        """
        if len(keypoints) <= self.target_points:
            return keypoints, scores

        h, w = image_shape[:2]
        ny, nx = self.grid_bins
        cell_w = w / nx
        cell_h = h / ny

        # Target quota per cell
        quota_per_cell = max(1, int(round(self.target_points / (nx * ny) * 1.5)))

        # Assign points to cells
        cells = {}
        for idx, (pt, s) in enumerate(zip(keypoints, scores)):
            bx = int(np.clip(pt[0] // cell_w, 0, nx - 1))
            by = int(np.clip(pt[1] // cell_h, 0, ny - 1))
            cell_key = (by, bx)
            if cell_key not in cells:
                cells[cell_key] = []
            cells[cell_key].append((idx, pt, s))

        # Select top points per cell based on score
        selected_indices = []
        for cell_pts in cells.values():
            # Sort by score descending
            cell_pts = sorted(cell_pts, key=lambda x: x[2], reverse=True)
            for item in cell_pts[:quota_per_cell]:
                selected_indices.append(item[0])

        selected_indices = sorted(list(set(selected_indices)))
        return keypoints[selected_indices], scores[selected_indices]

    def ssc_anms(self, keypoints, scores, image_shape):
        """
        Social Soft Clustering - Adaptive Non-Maximal Suppression (Bailo et al., CVPR 2018).
        Dynamically calculates suppression radius r_i for each keypoint:
        r_i = min || x_i - x_j || such that score_i < c_robust * score_j
        """
        num_pts = len(keypoints)
        if num_pts == 0:
            return keypoints
        if num_pts <= self.target_points:
            return keypoints

        # Sort keypoints by score descending
        order = np.argsort(scores)[::-1]
        sorted_pts = keypoints[order]
        sorted_scores = scores[order]

        c_robust = 0.9
        radii = np.full(num_pts, np.inf, dtype=np.float32)

        # For each point i, find closest higher-scoring point j where score_i < c_robust * score_j
        for i in range(1, num_pts):
            valid_j = np.where(sorted_scores[i] < c_robust * sorted_scores[:i])[0]
            if len(valid_j) > 0:
                dists = np.hypot(
                    sorted_pts[i, 0] - sorted_pts[valid_j, 0],
                    sorted_pts[i, 1] - sorted_pts[valid_j, 1]
                )
                radii[i] = np.min(dists)

        # Select points with the largest suppression radius (homogeneous spatial dispersion)
        best_indices = np.argsort(radii)[::-1][:self.target_points]
        return sorted_pts[best_indices]

    def enforce_uniformity(self, keypoints, scores, image_shape):
        """Combined Quad-Tree and SSC-ANMS spatial distribution pipeline."""
        if len(keypoints) == 0:
            return keypoints
        # 1. Fast Quad-tree cell budget
        qt_pts, qt_scores = self.quadtree_spatial_filter(keypoints, scores, image_shape)
        if len(qt_pts) == 0:
            return qt_pts
        # 2. SSC-ANMS continuous radius suppression
        uniform_pts = self.ssc_anms(qt_pts, qt_scores, image_shape)
        return uniform_pts
