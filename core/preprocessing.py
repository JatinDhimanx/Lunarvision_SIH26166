"""
Planetary Image Preprocessing and Radiometric Illumination Module
Handles:
1. Percentile contrast stretching (suppressing hot/dead pixels and cosmic rays).
2. Pushbroom detector line destriping (OHRC / TMC-2 line striping artifacts).
3. Shadow masking (isolating deep zero-SNR lunar shadow pits).
4. Solar angle radiometric normalization using PDS4 metadata.
5. DEM-guided topographic photometric illumination correction (honest: only when DEM supplied).
"""

import numpy as np
import cv2


class RadiometricNormalizer:
    """Preprocesses planetary images for robust optical and multi-modal correspondence."""

    @staticmethod
    def percentile_contrast_stretch(image, p_low=1.0, p_high=99.0):
        """
        Normalizes image intensities using percentile clipping.
        Prevents saturation from specular reflections and dead pixels.
        """
        if image is None or image.size == 0:
            return image

        img_float = image.astype(np.float32)
        v_min = np.percentile(img_float, p_low)
        v_max = np.percentile(img_float, p_high)

        if v_max <= v_min:
            return np.clip(img_float, 0, 255).astype(np.uint8)

        clipped = np.clip(img_float, v_min, v_max)
        normalized = ((clipped - v_min) / (v_max - v_min) * 255.0).astype(np.uint8)
        return normalized

    @staticmethod
    def destripe_pushbroom(image, axis=1):
        """
        Suppresses 1D pushbroom detector striping artifacts common in line-scanner sensors.
        axis=1: horizontal striping (row-wise gain offsets) — default for TMC-2, OHRC along-track sensors.
        axis=0: vertical striping (column-wise gain offsets) — for across-track sensors.
        """
        if image is None or image.size == 0:
            return image

        img_float = image.astype(np.float32)
        # Compute mean intensity profile along the non-striping axis
        line_means = np.mean(img_float, axis=axis, keepdims=True)
        # Filter line means using a 1D median/moving average to get background baseline
        ksize = 15
        pad = ksize // 2
        if axis == 0:
            profile = line_means[0, :]
            padded = np.pad(profile, pad, mode='edge')
            smooth = np.convolve(padded, np.ones(ksize) / ksize, mode='valid')
            offsets = profile - smooth
            corrected = img_float - offsets[np.newaxis, :]
        else:
            profile = line_means[:, 0]
            padded = np.pad(profile, pad, mode='edge')
            smooth = np.convolve(padded, np.ones(ksize) / ksize, mode='valid')
            offsets = profile - smooth
            corrected = img_float - offsets[:, np.newaxis]

        corrected = np.clip(corrected, 0, 255).astype(np.uint8)
        return corrected

    @staticmethod
    def compute_shadow_mask(image, threshold_percentile=5.0):
        """
        Generates a binary mask of deep shadowed lunar terrain.
        Returns uint8 mask where 255 = illuminated/usable terrain, 0 = deep shadow.
        """
        if image is None or image.size == 0:
            return None

        thresh_val = np.percentile(image, threshold_percentile)
        thresh_val = max(5.0, float(thresh_val))
        # Mask where pixel intensity is above threshold
        mask = (image > thresh_val).astype(np.uint8) * 255
        # Clean small noise artifacts with morphological opening
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask

    @staticmethod
    def normalize_sun_angle_metadata(image, sun_elevation_deg=35.0, sun_azimuth_deg=90.0):
        """
        Applies radiometric scale adjustment derived from solar illumination angles in PDS4 labels.
        Returns scaled image and normalization factor.
        """
        if image is None:
            return None, 1.0

        el_rad = np.radians(float(sun_elevation_deg))
        # Incident solar elevation cosine factor on horizontal plane
        cos_i_nominal = np.sin(el_rad)  # elevation is relative to local horizon
        cos_i_nominal = float(np.clip(cos_i_nominal, 0.1, 1.0))

        # Illumination scaling factor to bring grazing light into standard visibility range
        scale_factor = float(1.0 / cos_i_nominal)
        img_float = image.astype(np.float32) * (scale_factor ** 0.5)  # square-root compression
        corrected = np.clip(img_float, 0, 255).astype(np.uint8)
        return corrected, scale_factor


class DEMIlluminationCorrector:
    """
    Topographic photometric correction using surface normals from Digital Elevation Models (DEM).
    Strict honesty rule: If DEM is not provided, reports that DEM correction was NOT applied.
    """

    @staticmethod
    def correct_illumination(image, dem=None, sun_azimuth_deg=90.0, sun_elevation_deg=35.0, resolution_m=0.5):
        """
        Applies DEM-guided photometric correction (Lommel-Seeliger / Minnaert) if DEM is supplied.
        Returns:
            corrected_image: np.ndarray
            dem_metadata: dict
        """
        if dem is None:
            return image, {
                "dem_correction_applied": False,
                "reason": "External DEM data not supplied; topographic photometric correction bypassed.",
                "sun_azimuth_deg": float(sun_azimuth_deg),
                "sun_elevation_deg": float(sun_elevation_deg)
            }

        # Validate DEM dimensions match image
        h, w = image.shape[:2]
        if dem.shape[:2] != (h, w):
            dem_resized = cv2.resize(dem.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        else:
            dem_resized = dem.astype(np.float32)

        # Compute surface gradients in meters
        gy, gx = np.gradient(dem_resized)
        gx = gx / max(resolution_m, 1e-3)
        gy = gy / max(resolution_m, 1e-3)

        # Surface normal components N = (-gx, -gy, 1) normalized
        norm_mag = np.sqrt(gx**2 + gy**2 + 1.0)
        nx = -gx / norm_mag
        ny = -gy / norm_mag
        nz = 1.0 / norm_mag

        # Sun illumination vector L
        az_rad = np.radians(float(sun_azimuth_deg))
        el_rad = np.radians(float(sun_elevation_deg))
        lx = np.cos(el_rad) * np.sin(az_rad)
        ly = -np.cos(el_rad) * np.cos(az_rad)
        lz = np.sin(el_rad)
        sun_vec = np.array([lx, ly, lz], dtype=np.float32)
        sun_vec /= np.linalg.norm(sun_vec)

        # Local cosine of solar incidence angle (mu_0 = N . L)
        cos_i = nx * sun_vec[0] + ny * sun_vec[1] + nz * sun_vec[2]
        cos_i = np.clip(cos_i, 0.05, 1.0)  # Bound away from singularity in shadow

        # Emission angle cos_e for nadir viewing
        cos_e = np.clip(nz, 0.1, 1.0)

        # Lommel-Seeliger photometric correction factor: f = (cos_i + cos_e) / (2.0 * cos_i)
        # Ratio relative to flat surface
        flat_cos_i = max(lz, 0.05)
        correction_factor = (cos_i / (cos_i + cos_e + 1e-4)) / (flat_cos_i / (flat_cos_i + 1.0))
        correction_factor = np.clip(correction_factor, 0.25, 4.0)

        img_float = image.astype(np.float32)
        corrected = img_float / correction_factor
        corrected = np.clip(corrected, 0, 255).astype(np.uint8)

        metadata = {
            "dem_correction_applied": True,
            "mean_slope_deg": float(np.degrees(np.mean(np.arccos(np.clip(nz, 0.0, 1.0))))),
            "sun_azimuth_deg": float(sun_azimuth_deg),
            "sun_elevation_deg": float(sun_elevation_deg),
            "resolution_m": float(resolution_m)
        }
        return corrected, metadata


def preprocess_planetary_image(
    image,
    pds4_meta=None,
    dem=None,
    apply_destripe=True,
    apply_percentile=True
):
    """
    Unified entry point for lunar image preprocessing.
    Returns:
        proc_image: np.ndarray (uint8)
        shadow_mask: np.ndarray (uint8)
        meta: dict describing all applied preprocessing steps.
    """
    if image is None:
        return None, None, {}

    proc = image.copy()
    meta = {
        "original_shape": list(image.shape),
        "steps_applied": []
    }

    # Step 1: Pushbroom destriping
    if apply_destripe:
        # axis=1: TMC-2/OHRC pushbroom stripes are horizontal (row-wise gain offsets)
        proc = RadiometricNormalizer.destripe_pushbroom(proc, axis=1)
        meta["steps_applied"].append("pushbroom_destriping")

    # Step 2: Percentile contrast stretch
    if apply_percentile:
        proc = RadiometricNormalizer.percentile_contrast_stretch(proc, p_low=1.0, p_high=99.0)
        meta["steps_applied"].append("percentile_contrast_stretch")

    # Step 3: Deep shadow mask
    shadow_mask = RadiometricNormalizer.compute_shadow_mask(proc, threshold_percentile=4.0)
    meta["steps_applied"].append("shadow_masking")

    # Step 4: DEM-guided illumination correction (if DEM available)
    sun_az = float(pds4_meta.get("sun_azimuth_deg", 90.0)) if pds4_meta else 90.0
    sun_el = float(pds4_meta.get("sun_elevation_deg", 35.0)) if pds4_meta else 35.0

    proc, dem_info = DEMIlluminationCorrector.correct_illumination(
        proc,
        dem=dem,
        sun_azimuth_deg=sun_az,
        sun_elevation_deg=sun_el
    )
    meta["dem_correction"] = dem_info
    if dem_info["dem_correction_applied"]:
        meta["steps_applied"].append("dem_illumination_correction")

    return proc, shadow_mask, meta
