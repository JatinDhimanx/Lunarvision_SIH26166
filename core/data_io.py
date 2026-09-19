"""
Production Planetary Data I/O Manager for Chandrayaan-2 & Lunar Datasets
Fully supports:
1. PDS4 Standards:
   - XML parsing with namespace stripping
   - Binary byte offset extraction (<offset> tag)
   - Endianness handling (MSB big-endian, LSB little-endian)
   - Multiple data types (UnsignedByte, SignedMSB2, UnsignedMSB2, IEEE754Single, IEEE754Double)
   - Extracted SPICE geometry: Sub-solar azimuth, elevation, incidence, emission, phase angle
2. GeoTIFF / TIFF Standards:
   - 8-bit, 16-bit, and 32-bit floating point rasters
   - World file generation (.tfw) with planetary pixel scales
   - Optional Rasterio / PyProj integration with clean standard fallback
3. Deliverable Exports:
   - Registered Georeferenced Product (.tif + .tfw)
   - Ground Control Point (GCP) Tie-Points Table with real confidence (.csv)
   - Mathematically Honest Quality Certification Report (.json & .md) with dynamic PASS/FAIL
   - Full ZIP Archive bundle
"""

import os
import io
import json
import csv
import zipfile
import base64
import logging
from typing import Tuple, Optional, Dict, Any, List
from core.quality import resolve_quality_thresholds, evaluate_quality, sanitize_for_json
import xml.etree.ElementTree as ET
import numpy as np
import cv2
from PIL import Image

logger = logging.getLogger(__name__)

# Optional Rasterio/PyProj integration check
HAS_RASTERIO = False
try:
    import rasterio
    from rasterio.transform import Affine
    import pyproj
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False


class PlanetaryDataManager:
    """Handles production planetary data ingestion, metadata parsing, and product export."""

    @staticmethod
    @staticmethod
    def stretch_contrast_percentile(arr: np.ndarray, p_low: float = 1.0, p_high: float = 99.0) -> np.ndarray:
        """
        Robust 1st-99th percentile radiometric contrast stretching for planetary sensors.
        Mitigates extreme illumination disparities, deep shadows, and hot pixels.
        Returns clean float32 array normalized to [0.0, 1.0].
        """
        arr_f = arr.astype(np.float32)
        valid = arr_f[np.isfinite(arr_f)]
        if len(valid) == 0:
            return np.zeros_like(arr_f, dtype=np.float32)

        vmin = float(np.percentile(valid, p_low))
        vmax = float(np.percentile(valid, p_high))
        if vmax <= vmin:
            vmin = float(np.min(valid))
            vmax = float(np.max(valid))

        if vmax > vmin:
            stretched = np.clip((arr_f - vmin) / (vmax - vmin), 0.0, 1.0)
        else:
            stretched = np.zeros_like(arr_f, dtype=np.float32)
        return stretched

    @staticmethod
    def load_pds4(xml_path, data_path=None, as_float: bool = False, max_dim: Optional[int] = None):
        """
        Parses PDS4 XML or PDS3/PDS4 LBL label and reads the associated binary raw raster.
        Extracts axes, bit depth, byte-ordering, valid data ranges, and solar illumination geometry.
        Supports high-performance zero-copy memory mapping (np.memmap) with optional max_dim strided downsampling
        to ensure multi-GB rasters never cause memory exhaustion.
        Applies 1st-99th percentile contrast stretching for 12/16-bit uncalibrated digital numbers.
        """
        if not os.path.exists(xml_path):
            raise FileNotFoundError(f"PDS label not found: {xml_path}")

        metadata = {
            "source_xml": os.path.abspath(xml_path),
            "mission": "Chandrayaan-2",
            "target": "Moon",
            "format": "PDS4",
            "has_real_georeferencing": False
        }

        # Check if file is XML or ODL/text (.lbl)
        is_xml = True
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for elem in root.iter():
                if '}' in elem.tag:
                    elem.tag = elem.tag.split('}', 1)[1]
        except Exception:
            is_xml = False

        if not is_xml:
            # Parse text/ODL label (.lbl)
            lines = 384
            samples = 384
            bands = 1
            data_type_str = "UnsignedMSB2"
            byte_offset = 0
            with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
                lbl_text = f.read()

            import re
            m_lines = re.search(r'(?:LINES|FILE_RECORDS)\s*=\s*(\d+)', lbl_text, re.I)
            if m_lines:
                lines = int(m_lines.group(1))
            m_samples = re.search(r'(?:LINE_SAMPLES|RECORD_BYTES|ELEMENTS)\s*=\s*(\d+)', lbl_text, re.I)
            if m_samples:
                samples = int(m_samples.group(1))
            m_dtype = re.search(r'(?:DATA_TYPE|SAMPLE_TYPE)\s*=\s*([A-Za-z0-9_]+)', lbl_text, re.I)
            if m_dtype:
                data_type_str = m_dtype.group(1)
            m_offset = re.search(r'(?:OFFSET|\^IMAGE)\s*=\s*(?:\([^\)]*,)?\s*(\d+)', lbl_text, re.I)
            if m_offset:
                byte_offset = int(m_offset.group(1))

            def _find_lbl_angle(pattern, default_val):
                m = re.search(pattern, lbl_text, re.I)
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
                return default_val

            metadata["instrument"] = "TMC-2 / OHRC (Chandrayaan-2)"
            metadata["sun_azimuth_deg"] = _find_lbl_angle(r'(?:SOLAR_AZIMUTH|SUB_SOLAR_AZIMUTH)\s*=\s*([0-9.]+)', 90.0)
            metadata["sun_elevation_deg"] = _find_lbl_angle(r'(?:SOLAR_ELEVATION|SUB_SOLAR_ELEVATION)\s*=\s*([0-9.]+)', 35.0)
            metadata["incidence_angle_deg"] = _find_lbl_angle(r'(?:INCIDENCE_ANGLE|SOLAR_INCIDENCE_ANGLE)\s*=\s*([0-9.]+)', 55.0)
            metadata["emission_angle_deg"] = _find_lbl_angle(r'EMISSION_ANGLE\s*=\s*([0-9.]+)', 0.0)
            metadata["phase_angle_deg"] = _find_lbl_angle(r'PHASE_ANGLE\s*=\s*([0-9.]+)', 55.0)

            metadata["lines"] = lines
            metadata["samples"] = samples
            metadata["bands"] = bands
            metadata["data_type"] = data_type_str
            metadata["byte_offset"] = byte_offset
        else:
            # XML element parsing
            # Extract instrument & observation info
            inst = root.find(".//Observing_System_Component/name") or root.find(".//instrument_name")
            if inst is not None and inst.text:
                metadata["instrument"] = inst.text.strip()
            else:
                metadata["instrument"] = "OHRC (Chandrayaan-2)"

            # Helper to query across multiple possible PDS4 tag naming conventions
            def _find_angle(tag_candidates, default_val=None):
                for t in tag_candidates:
                    elem = root.find(f".//{t}")
                    if elem is not None and elem.text and elem.text.strip():
                        try:
                            return float(elem.text.strip())
                        except ValueError:
                            pass
                return default_val

            # Solar and orbital geometry
            metadata["sun_azimuth_deg"] = _find_angle(
                ["sub_solar_azimuth", "solar_azimuth_angle", "subsolar_azimuth_angle", "sun_azimuth_angle"], 90.0
            )
            metadata["sun_elevation_deg"] = _find_angle(
                ["sub_solar_elevation", "solar_elevation_angle", "subsolar_elevation_angle", "sun_elevation_angle"], 35.0
            )
            metadata["incidence_angle_deg"] = _find_angle(
                ["incidence_angle", "solar_incidence_angle", "mean_incidence_angle"], 55.0
            )
            metadata["emission_angle_deg"] = _find_angle(
                ["emission_angle", "mean_emission_angle"], 0.0
            )
            metadata["phase_angle_deg"] = _find_angle(
                ["phase_angle", "mean_phase_angle"], 55.0
            )

        # Array geometry & data type from Array_2D_Image or root
        arr_node = root.find(".//Array_2D_Image") or root.find(".//Array_3D_Image") or root
        lines = None
        samples = None
        bands = 1

        for axis in arr_node.findall(".//Axis_Array"):
            aname = axis.find("axis_name")
            elems = axis.find("elements")
            if aname is not None and elems is not None and elems.text:
                txt = aname.text.strip().lower()
                if "line" in txt:
                    lines = int(elems.text.strip())
                elif "sample" in txt:
                    samples = int(elems.text.strip())
                elif "band" in txt:
                    bands = int(elems.text.strip())

        if lines is None:
            l_elem = root.find(".//lines")
            lines = int(l_elem.text.strip()) if (l_elem is not None and l_elem.text) else 512
        if samples is None:
            s_elem = root.find(".//samples")
            samples = int(s_elem.text.strip()) if (s_elem is not None and s_elem.text) else 512

        dtype_elem = arr_node.find(".//data_type") or root.find(".//data_type")
        offset_elem = arr_node.find(".//offset") or root.find(".//offset")

        # Valid data ranges
        min_elem = arr_node.find(".//valid_maximum") or arr_node.find(".//minimum")
        max_elem = arr_node.find(".//valid_minimum") or arr_node.find(".//maximum")
        if min_elem is not None and min_elem.text:
            try:
                metadata["valid_min"] = float(min_elem.text.strip())
            except ValueError:
                pass
        if max_elem is not None and max_elem.text:
            try:
                metadata["valid_max"] = float(max_elem.text.strip())
            except ValueError:
                pass

        data_type_str = dtype_elem.text.strip() if (dtype_elem is not None and dtype_elem.text) else "UnsignedByte"
        byte_offset = int(offset_elem.text.strip()) if (offset_elem is not None and offset_elem.text) else 0

        metadata["lines"] = lines
        metadata["samples"] = samples
        metadata["bands"] = bands
        metadata["data_type"] = data_type_str
        metadata["byte_offset"] = byte_offset

        # Check for geographic bounding box in PDS4
        lat_min_elem = root.find(".//minimum_latitude")
        lat_max_elem = root.find(".//maximum_latitude")
        lon_min_elem = root.find(".//minimum_longitude")
        lon_max_elem = root.find(".//maximum_longitude")
        if lat_min_elem is not None and lat_max_elem is not None:
            try:
                metadata["lat_min"] = float(lat_min_elem.text)
                metadata["lat_max"] = float(lat_max_elem.text)
                metadata["lon_min"] = float(lon_min_elem.text)
                metadata["lon_max"] = float(lon_max_elem.text)
                metadata["center_lat"] = (metadata["lat_min"] + metadata["lat_max"]) / 2.0
                metadata["center_lon"] = (metadata["lon_min"] + metadata["lon_max"]) / 2.0
                metadata["has_real_georeferencing"] = True
            except Exception as e:
                logger.debug("Failed to parse PDS4 lat/lon bounding elements: %s", e)

        # Resolve binary file path
        if data_path is None:
            file_name_elem = root.find(".//File/file_name")
            if file_name_elem is not None and file_name_elem.text:
                cand_name = os.path.join(os.path.dirname(xml_path), file_name_elem.text.strip())
                if os.path.exists(cand_name):
                    data_path = cand_name

        if data_path is None:
            base, _ = os.path.splitext(xml_path)
            for ext in [".img", ".raw", ".dat", ".bin", ".tif"]:
                cand = base + ext
                if os.path.exists(cand):
                    data_path = cand
                    break

        if data_path is None or not os.path.exists(data_path):
            image_array = np.zeros((lines, samples), dtype=np.float32 if as_float else np.uint8)
            return image_array, metadata

        metadata["data_file"] = os.path.abspath(data_path)

        normalized_dtype = data_type_str.strip().upper().replace(" ", "")

        if "SIGNEDLSB4" in normalized_dtype or ("INT32" in normalized_dtype and "LSB" in normalized_dtype):
            np_dtype = np.dtype("<i4")
        elif "UNSIGNEDLSB4" in normalized_dtype or ("UINT32" in normalized_dtype and "LSB" in normalized_dtype):
            np_dtype = np.dtype("<u4")
        elif "SIGNEDMSB4" in normalized_dtype or ("INT32" in normalized_dtype and "MSB" in normalized_dtype):
            np_dtype = np.dtype(">i4")
        elif "UNSIGNEDMSB4" in normalized_dtype or ("UINT32" in normalized_dtype and "MSB" in normalized_dtype):
            np_dtype = np.dtype(">u4")
        elif "INT32" in normalized_dtype:
            np_dtype = np.dtype(">i4")
        elif "UINT32" in normalized_dtype:
            np_dtype = np.dtype(">u4")
        elif "SIGNEDLSB2" in normalized_dtype or ("INT16" in normalized_dtype and "LSB" in normalized_dtype):
            np_dtype = np.dtype("<i2")
        elif "UNSIGNEDLSB2" in normalized_dtype or ("UINT16" in normalized_dtype and "LSB" in normalized_dtype):
            np_dtype = np.dtype("<u2")
        elif "SIGNEDMSB2" in normalized_dtype or ("INT16" in normalized_dtype and "MSB" in normalized_dtype):
            np_dtype = np.dtype(">i2")
        elif "UNSIGNEDMSB2" in normalized_dtype or ("UINT16" in normalized_dtype and "MSB" in normalized_dtype):
            np_dtype = np.dtype(">u2")
        elif "INT16" in normalized_dtype:
            np_dtype = np.dtype(">i2")
        elif "UINT16" in normalized_dtype:
            np_dtype = np.dtype(">u2")
        elif "IEEE754DOUBLE" in normalized_dtype or "FLOAT64" in normalized_dtype or "DOUBLE" in normalized_dtype:
            np_dtype = np.dtype("<f8" if "LSB" in normalized_dtype else ">f8")
        elif "IEEE754SINGLE" in normalized_dtype or "FLOAT32" in normalized_dtype:
            np_dtype = np.dtype("<f4" if "LSB" in normalized_dtype else ">f4")
        elif "UNSIGNEDBYTE" in normalized_dtype or "UINT8" in normalized_dtype:
            np_dtype = np.dtype("uint8")
        elif "SIGNEDBYTE" in normalized_dtype or "INT8" in normalized_dtype:
            np_dtype = np.dtype("int8")
        else:
            np_dtype = np.dtype("uint8")

        if data_path.lower().endswith((".tif", ".tiff")):
            raw_raster = cv2.imread(data_path, cv2.IMREAD_UNCHANGED)
        else:
            file_size = os.path.getsize(data_path) if os.path.exists(data_path) else 0
            expected_pixels = lines * samples * bands
            bytes_per_pixel = np_dtype.itemsize
            expected_bytes = expected_pixels * bytes_per_pixel

            shape = (lines, samples) if bands == 1 else (lines, samples, bands)

            # Use np.memmap for zero-copy, low-RAM streaming of large binary rasters
            if file_size >= byte_offset + expected_bytes and lines > 0 and samples > 0:
                try:
                    mm = np.memmap(data_path, dtype=np_dtype, mode='r', offset=byte_offset, shape=shape)
                    if max_dim and max(lines, samples) > max_dim:
                        step_y = max(1, int(np.ceil(lines / max_dim)))
                        step_x = max(1, int(np.ceil(samples / max_dim)))
                        raw_raster = np.array(mm[::step_y, ::step_x])
                    else:
                        raw_raster = np.array(mm)
                except Exception as mm_err:
                    logger.warning("np.memmap failed (%s): %s. Falling back to buffered read.", data_path, mm_err)
                    with open(data_path, "rb") as f:
                        f.seek(byte_offset)
                        raw_bytes = f.read(expected_bytes)
                    raw_raster = np.frombuffer(raw_bytes, dtype=np_dtype)
                    if len(raw_raster) >= expected_pixels:
                        raw_raster = raw_raster[:expected_pixels]
                        raw_raster = raw_raster.reshape(shape)
            else:
                with open(data_path, "rb") as f:
                    f.seek(byte_offset)
                    raw_bytes = f.read()

                expected_pixels = lines * samples * bands
                raw_raster = np.frombuffer(raw_bytes, dtype=np_dtype)
                if len(raw_raster) >= expected_pixels:
                    raw_raster = raw_raster[:expected_pixels]
                    if bands > 1:
                        raw_raster = raw_raster.reshape((lines, samples, bands))
                    else:
                        raw_raster = raw_raster.reshape((lines, samples))

        # Enforce native system byteorder
        if raw_raster.dtype.byteorder not in ('=', '|'):
            raw_raster = raw_raster.astype(raw_raster.dtype.newbyteorder('='), copy=False)

        # 1st-99th percentile dynamic contrast stretching
        float32_norm = PlanetaryDataManager.stretch_contrast_percentile(raw_raster, 1.0, 99.0)
        metadata["raw_min"] = float(np.min(raw_raster)) if raw_raster.size > 0 else 0.0
        metadata["raw_max"] = float(np.max(raw_raster)) if raw_raster.size > 0 else 1.0

        if as_float:
            return float32_norm, metadata

        uint8_disp = (float32_norm * 255.0).astype(np.uint8)
        return uint8_disp, metadata

    @staticmethod
    def generate_low_res_preview(
        xml_path: str,
        data_path: Optional[str] = None,
        target_size: int = 1024
    ) -> Tuple[np.ndarray, dict, str]:
        """
        Generates a fast, low-memory thumbnail/preview of a PDS4 image product using np.memmap.
        Does not load the full multi-GB image into memory. Returns (preview_array, metadata, base64_data_url).
        """
        disp, meta = PlanetaryDataManager.load_pds4(
            xml_path=xml_path,
            data_path=data_path,
            as_float=False,
            max_dim=target_size
        )
        success, encoded = cv2.imencode('.png', disp)
        if success:
            b64_str = base64.b64encode(encoded).decode('utf-8')
            data_url = f"data:image/png;base64,{b64_str}"
        else:
            data_url = ""
        return disp, meta, data_url

    @staticmethod
    def extract_chip(
        image: np.ndarray,
        metadata: dict,
        chip_size: int = 1024,
        center: bool = True,
        offset_x: int = 0,
        offset_y: int = 0
    ) -> Tuple[np.ndarray, dict]:
        """
        Safely crops a centered or localized chip of size (chip_size, chip_size)
        from gigapixel planetary swathes to prevent memory exhaustion, while maintaining
        georeferencing offsets (chip_x0, chip_y0, and updated ul_x, ul_y in world file).
        """
        h, w = image.shape[:2]
        if h <= chip_size and w <= chip_size:
            return image, metadata

        if center:
            x0 = max(0, (w - chip_size) // 2)
            y0 = max(0, (h - chip_size) // 2)
        else:
            x0 = max(0, min(offset_x, max(0, w - chip_size)))
            y0 = max(0, min(offset_y, max(0, h - chip_size)))

        x1 = min(w, x0 + chip_size)
        y1 = min(h, y0 + chip_size)

        chip = image[y0:y1, x0:x1].copy()
        chip_meta = dict(metadata)
        chip_meta["chip_x0"] = int(x0)
        chip_meta["chip_y0"] = int(y0)
        chip_meta["chip_width"] = int(x1 - x0)
        chip_meta["chip_height"] = int(y1 - y0)
        chip_meta["original_shape"] = (h, w)

        # Update world file coordinates if present
        if "geo_transform" in chip_meta:
            gt = dict(chip_meta["geo_transform"])
            gt["ul_x"] = gt.get("ul_x", 0.0) + x0 * gt.get("pixel_size_x", 1.0)
            gt["ul_y"] = gt.get("ul_y", 0.0) + y0 * gt.get("pixel_size_y", -1.0)
            chip_meta["geo_transform"] = gt

        return chip, chip_meta

    @staticmethod
    def load_image(file_path, as_float: bool = False, max_chip_dim: Optional[int] = None):
        """
        Universal reader supporting PDS4 XML, PDS3/4 LBL, GeoTIFF, standard images, and NumPy arrays.
        Supports 1st-99th percentile radiometric contrast stretching, float32 return mode,
        and automatic centered chip cropping for massive swathes.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        metadata = {
            "file_path": os.path.abspath(file_path),
            "format": ext[1:].upper(),
            "has_real_georeferencing": False
        }

        # Check for accompanying world file (.tfw / .pgw / .wld)
        base_path = os.path.splitext(file_path)[0]
        for w_ext in [".tfw", ".pgw", ".wld"]:
            w_cand = base_path + w_ext
            if os.path.exists(w_cand):
                try:
                    with open(w_cand, "r", encoding="utf-8") as wf:
                        lines = [float(l.strip()) for l in wf.readlines() if l.strip()]
                        if len(lines) >= 6:
                            metadata["geo_transform"] = {
                                "pixel_size_x": lines[0],
                                "rotation_y": lines[1],
                                "rotation_x": lines[2],
                                "pixel_size_y": lines[3],
                                "ul_x": lines[4],
                                "ul_y": lines[5]
                            }
                            metadata["has_real_georeferencing"] = True
                except Exception as e:
                    logger.debug("Failed to parse world file %s: %s", w_cand, e)
                break

        if ext in [".xml", ".lbl"]:
            img, meta = PlanetaryDataManager.load_pds4(file_path, as_float=as_float)
            if max_chip_dim and (img.shape[0] > max_chip_dim or img.shape[1] > max_chip_dim):
                img, meta = PlanetaryDataManager.extract_chip(img, meta, chip_size=max_chip_dim, center=True)
            return img, meta

        elif ext in [".img", ".raw", ".dat", ".bin"]:
            # Check for matching PDS4 XML or LBL label in same directory
            base_dir = os.path.dirname(file_path)
            stem = os.path.splitext(os.path.basename(file_path))[0]
            xml_candidate = None
            for cand_name in [stem + ".xml", stem + ".XML", stem + ".lbl", stem + ".LBL"]:
                full_cand = os.path.join(base_dir, cand_name)
                if os.path.exists(full_cand):
                    xml_candidate = full_cand
                    break

            if not xml_candidate:
                # Search directory for any xml file referencing this image
                for f in os.listdir(base_dir):
                    if f.lower().endswith((".xml", ".lbl")):
                        xml_candidate = os.path.join(base_dir, f)
                        break

            if xml_candidate:
                img, meta = PlanetaryDataManager.load_pds4(xml_candidate, data_path=file_path, as_float=as_float)
                if max_chip_dim and (img.shape[0] > max_chip_dim or img.shape[1] > max_chip_dim):
                    img, meta = PlanetaryDataManager.extract_chip(img, meta, chip_size=max_chip_dim, center=True)
                return img, meta

        elif ext in [".tif", ".tiff"] and HAS_RASTERIO:
            try:
                with rasterio.open(file_path) as src:
                    arr = src.read(1)
                    metadata["height"] = arr.shape[0]
                    metadata["width"] = arr.shape[1]
                    if src.crs:
                        metadata["crs"] = str(src.crs)
                        metadata["has_real_georeferencing"] = True
                    if src.transform:
                        metadata["geo_transform"] = {
                            "pixel_size_x": src.transform.a,
                            "rotation_y": src.transform.b,
                            "ul_x": src.transform.c,
                            "rotation_x": src.transform.d,
                            "pixel_size_y": src.transform.e,
                            "ul_y": src.transform.f
                        }
                        metadata["has_real_georeferencing"] = True

                    float32_norm = PlanetaryDataManager.stretch_contrast_percentile(arr, 1.0, 99.0)
                    if as_float:
                        return float32_norm, metadata
                    uint8_disp = (float32_norm * 255.0).astype(np.uint8) if arr.dtype != np.uint8 else arr
                    return uint8_disp, metadata
            except Exception as e:
                logger.warning("Rasterio failed reading %s: %s. Falling back to standard reader.", file_path, e)

        elif ext == ".zip":
            metadata["format"] = "PDS4_ZIP"
            with zipfile.ZipFile(file_path, 'r') as z:
                names = z.namelist()
                xml_names = [n for n in names if n.lower().endswith('.xml')]
                for xn in xml_names:
                    try:
                        xtree = ET.fromstring(z.read(xn))
                        for elem in xtree.iter():
                            if '}' in elem.tag:
                                elem.tag = elem.tag.split('}', 1)[1]
                        title = xtree.find('.//title')
                        if title is not None and title.text:
                            metadata["mission"] = title.text.strip()
                        inst = xtree.find(".//Observing_System_Component/name")
                        if inst is not None and inst.text:
                            metadata["instrument"] = inst.text.strip()
                        az = xtree.find(".//subsolar_azimuth_angle")
                        if az is not None and az.text:
                            metadata["sun_azimuth_deg"] = float(az.text.strip())
                        el = xtree.find(".//solar_elevation_angle")
                        if el is not None and el.text:
                            metadata["sun_elevation_deg"] = float(el.text.strip())
                        inc = xtree.find(".//incidence_angle")
                        if inc is not None and inc.text:
                            metadata["incidence_angle_deg"] = float(inc.text.strip())
                        break
                    except Exception as e:
                        logger.debug("Failed parsing XML inside zip %s: %s", xn, e)

                img_candidates = [n for n in names if n.lower().endswith(('.png', '.jpg', '.jpeg', '.tif', '.tiff'))]
                preferred = [n for n in img_candidates if 'browse' in n.lower() or 'calibrated' in n.lower()]
                target_name = preferred[0] if preferred else (img_candidates[0] if img_candidates else None)

                if target_name:
                    pil_img = Image.open(io.BytesIO(z.read(target_name)))
                    arr = np.array(pil_img)
                else:
                    raise ValueError(f"No compatible image raster found inside {os.path.basename(file_path)}")

                if arr.ndim == 3 and arr.shape[2] >= 3:
                    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                elif arr.ndim == 3 and arr.shape[2] == 1:
                    arr = arr[:, :, 0]

                float32_norm = PlanetaryDataManager.stretch_contrast_percentile(arr, 1.0, 99.0)
                metadata["height"] = arr.shape[0]
                metadata["width"] = arr.shape[1]

                if as_float:
                    return float32_norm, metadata
                uint8_disp = (float32_norm * 255.0).astype(np.uint8) if arr.dtype != np.uint8 else arr
                return uint8_disp, metadata

        elif ext in [".npy", ".npz"]:
            data = np.load(file_path)
            arr = data[list(data.keys())[0]] if ext == ".npz" else data
            metadata["shape"] = list(arr.shape)
            metadata["dtype"] = str(arr.dtype)
            float32_norm = PlanetaryDataManager.stretch_contrast_percentile(arr, 1.0, 99.0)
            if as_float:
                return float32_norm, metadata
            return (float32_norm * 255.0).astype(np.uint8), metadata

        else:
            img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                pil_img = Image.open(file_path)
                img = np.array(pil_img)

            if len(img.shape) == 3 and img.shape[2] == 3:
                img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                img_gray = img

            metadata["height"] = img_gray.shape[0]
            metadata["width"] = img_gray.shape[1]

            float32_norm = PlanetaryDataManager.stretch_contrast_percentile(img_gray, 1.0, 99.0)
            if as_float:
                return float32_norm, metadata
            uint8_disp = (float32_norm * 255.0).astype(np.uint8) if img_gray.dtype != np.uint8 else img_gray
            return uint8_disp, metadata

    @staticmethod
    def export_registered_product(image, output_path, geo_transform=None):
        """
        Exports registered product as GeoTIFF with accompanying ESRI world file (.tfw).
        Utilizes Rasterio when available to write GeoTIFF tags and CRS directly.
        """
        if image is None:
            return None

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()

        written_with_rasterio = False
        if HAS_RASTERIO and ext in [".tif", ".tiff"]:
            try:
                px = geo_transform.get("pixel_size_x", 0.5) if geo_transform else 0.5
                py = geo_transform.get("pixel_size_y", -0.5) if geo_transform else -0.5
                ux = geo_transform.get("ul_x", 0.0) if geo_transform else 0.0
                uy = geo_transform.get("ul_y", 0.0) if geo_transform else 0.0
                rx = geo_transform.get("rotation_x", 0.0) if geo_transform else 0.0
                ry = geo_transform.get("rotation_y", 0.0) if geo_transform else 0.0
                transform = Affine(px, ry, ux, rx, py, uy)
                h, w = image.shape[:2]
                count = 1 if image.ndim == 2 else image.shape[2]
                with rasterio.open(
                    output_path,
                    'w',
                    driver='GTiff',
                    height=h,
                    width=w,
                    count=count,
                    dtype=image.dtype,
                    transform=transform,
                    crs="EPSG:4326"
                ) as dst:
                    if image.ndim == 2:
                        dst.write(image, 1)
                    else:
                        for b in range(count):
                            dst.write(image[:, :, b], b + 1)
                written_with_rasterio = True
            except Exception as e:
                logger.warning("Rasterio export failed (%s): %s. Falling back to OpenCV.", output_path, e)

        if not written_with_rasterio:
            cv2.imwrite(output_path, image)

        world_ext = ".tfw" if ext in [".tif", ".tiff"] else ".pgw"
        world_path = os.path.splitext(output_path)[0] + world_ext

        px = geo_transform.get("pixel_size_x", 0.5) if geo_transform else 0.5
        py = geo_transform.get("pixel_size_y", -0.5) if geo_transform else -0.5
        ux = geo_transform.get("ul_x", 0.0) if geo_transform else 0.0
        uy = geo_transform.get("ul_y", 0.0) if geo_transform else 0.0

        with open(world_path, "w", encoding="utf-8") as f:
            f.write(f"{px:.6f}\n0.000000\n0.000000\n{py:.6f}\n{ux:.6f}\n{uy:.6f}\n")

        return os.path.abspath(output_path)

    @staticmethod
    def export_tie_points(
        pts_src,
        pts_ref,
        output_path,
        residuals=None,
        confidences=None,
        subpixel_residuals=None
    ):
        """Exports Ground Control Points (GCP) table with observable confidences in CSV format."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        num_points = len(pts_src)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Point_ID",
                "Source_X_px",
                "Source_Y_px",
                "Reference_X_px",
                "Reference_Y_px",
                "Delta_X_px",
                "Delta_Y_px",
                "Reprojection_Residual_px",
                "Subpixel_Residual_px",
                "Observable_Confidence",
                "Certified_Subpixel"
            ])

            for i in range(num_points):
                sx, sy = pts_src[i]
                rx, ry = pts_ref[i]
                dx = rx - sx
                dy = ry - sy
                res_val = float(residuals[i]) if (residuals is not None and i < len(residuals)) else float(np.sqrt(dx**2 + dy**2))
                if subpixel_residuals is None or i >= len(subpixel_residuals):
                    sub_res = ""
                else:
                    sub_res = float(subpixel_residuals[i])
                if confidences is None or i >= len(confidences):
                    conf_val = ""
                else:
                    conf_val = float(confidences[i])
                is_subpixel = "YES (<0.3px)" if res_val < 0.30 else "NO"

                writer.writerow([
                    f"GCP_{i + 1:04d}",
                    f"{sx:.3f}",
                    f"{sy:.3f}",
                    f"{rx:.3f}",
                    f"{ry:.3f}",
                    f"{dx:.3f}",
                    f"{dy:.3f}",
                    f"{res_val:.4f}",
                    f"{sub_res:.4f}" if sub_res != "" else "",
                    f"{conf_val:.3f}" if conf_val != "" else "",
                    is_subpixel
                ])

        return os.path.abspath(output_path)

    @staticmethod
    def export_certification_report(metrics, output_path, mission_info=None, quality_gates=None):
        """
        Generates mathematically honest JSON & Markdown quality certification reports.
        Evaluates every gate dynamically. Never hardcodes PASSED.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        gates = resolve_quality_thresholds(quality_gates)

        inlier_cnt = metrics.get("inlier_count", 0)
        inlier_ratio = metrics.get("inlier_ratio_pct", 0.0)
        rmse = metrics.get("subpixel_rmse_px")
        checkpoint_rmse = metrics.get("checkpoint_rmse_px")
        grid_cov = metrics.get("grid_coverage_pct")
        entropy = metrics.get("voronoi_entropy")
        overlap = metrics.get("overlap_pct")
        verdicts = evaluate_quality(metrics, quality_gates)
        all_mandatory_passed = all(verdicts.values())

        overall_verdict = "PASSED" if all_mandatory_passed else "FAILED"
        verdict_summary = (
            "PASSED (ISRO Photogrammetry Standard Fulfilled)"
            if all_mandatory_passed else
            "FAILED (Registration Quality Gates Not Satisfied)"
        )

        report_data = {
            "title": "ISRO Problem Statement 26166 - Registration Quality Certification",
            "organization": "Indian Space Research Organisation (ISRO)",
            "software": "LunarVision Automated Planetary Registration Engine",
            "overall_status": overall_verdict,
            "overall_verdict": verdict_summary,
            "metrics": metrics,
            "quality_gates_thresholds": gates,
            "gate_evaluations": {
                **verdicts
            }
        }

        if mission_info:
            report_data["mission_metadata"] = mission_info

        report_data = sanitize_for_json(report_data)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=4)

        # Generate Dynamic Markdown Report
        md_path = os.path.splitext(output_path)[0] + ".md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# ISRO Chandrayaan-2 Registration Certification Report\n\n")
            f.write("**Problem Statement:** SIH 26166 | **Theme:** Space Technology\n\n")
            f.write(f"### Overall Verdict: **{overall_verdict}**\n\n")
            f.write(f"> {verdict_summary}\n\n")
            f.write("### Quantitative Validation Scorecard\n\n")
            f.write("| Metric | Measured Value | ISRO Benchmark | Verdict |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            f.write(f"| **Sub-Pixel Accuracy (RMSE)** | `{rmse if rmse is not None else 'N/A'}` | `<= {gates['max_subpixel_rmse_px']:.2f} px` | **{'PASS' if verdicts['subpixel_rmse_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Independent Check-Point RMSE** | `{checkpoint_rmse if checkpoint_rmse is not None else 'N/A'}` | `<= {gates['max_checkpoint_rmse_px']:.2f} px` | **{'PASS' if verdicts['checkpoint_rmse_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Inlier Tie-Point Count** | `{inlier_cnt}` | `>= {gates['min_inliers']}` | **{'PASS' if verdicts['inlier_count_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Inlier Correspondence Ratio** | `{inlier_ratio}` | `>= {gates['min_inlier_ratio_pct']:.0f}%` | **{'PASS' if verdicts['inlier_ratio_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Grid Spatial Coverage Index** | `{grid_cov}` | `>= {gates['min_grid_coverage_pct']:.0f}%` | **{'PASS' if verdicts['spatial_grid_coverage_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Image Overlap Percentage** | `{overlap}` | `>= {gates['min_overlap_pct']:.0f}%` | **{'PASS' if verdicts['image_overlap_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Voronoi Area Entropy (H)** | `{entropy}` | `>= {gates['min_voronoi_entropy']:.2f}` | **{'PASS' if verdicts['voronoi_entropy_pass'] else 'FAIL'}** |\n")
            f.write(f"| **Transform Geometric Validity** | `{'VALID' if verdicts['geometric_transform_valid_pass'] else 'INVALID'}` | `Valid Matrix` | **{'PASS' if verdicts['geometric_transform_valid_pass'] else 'FAIL'}** |\n\n")

            if all_mandatory_passed:
                f.write("### Final Verdict\n")
                f.write("> **CERTIFIED**: Source image successfully registered to canonical reference basemap with verified sub-pixel precision and homogeneous spatial tie-point distribution.\n")
            else:
                f.write("### Final Verdict\n")
                f.write("> **REJECTED**: Registration did not satisfy mandatory ISRO photogrammetric quality thresholds.\n")

        return os.path.abspath(output_path)

    @staticmethod
    def create_zip_bundle(directory_path, zip_output_path):
        """Packages all exported deliverables into a clean ZIP archive for download."""
        os.makedirs(os.path.dirname(os.path.abspath(zip_output_path)), exist_ok=True)
        with zipfile.ZipFile(zip_output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(directory_path):
                for file in files:
                    if file.endswith((".tif", ".tfw", ".csv", ".json", ".md", ".png")):
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, directory_path)
                        zf.write(full_p, rel_p)
        return os.path.abspath(zip_output_path)
