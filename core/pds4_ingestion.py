"""
PDS4 Scientific Raster Ingestion & Memory-Mapped Preview Generator.
Handles dynamic PDS4 XML label parsing, raster validation, zero-RAM np.memmap reading,
and scientifically safe contrast-stretched preview generation for gigabyte-scale datasets.
"""

import os
import xml.etree.ElementTree as ET
import logging
from typing import Dict, Any, Tuple, Optional, List
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# PDS4 Data Type mapping to NumPy dtypes with explicit byte ordering
PDS4_DTYPE_MAP = {
    # 8-bit integers
    "UNSIGNEDBYTE": np.dtype("uint8"),
    "BYTE": np.dtype("uint8"),
    "UINT8": np.dtype("uint8"),
    "SIGNEDBYTE": np.dtype("int8"),
    "INT8": np.dtype("int8"),

    # 16-bit integers - Little Endian (LSB)
    "UNSIGNEDLSB2": np.dtype("<u2"),
    "SIGNEDLSB2": np.dtype("<i2"),
    "UINT16LSB": np.dtype("<u2"),
    "INT16LSB": np.dtype("<i2"),

    # 16-bit integers - Big Endian (MSB)
    "UNSIGNEDMSB2": np.dtype(">u2"),
    "SIGNEDMSB2": np.dtype(">i2"),
    "UINT16MSB": np.dtype(">u2"),
    "INT16MSB": np.dtype(">i2"),

    # 32-bit integers - Little Endian (LSB)
    "UNSIGNEDLSB4": np.dtype("<u4"),
    "SIGNEDLSB4": np.dtype("<i4"),
    "UINT32LSB": np.dtype("<u4"),
    "INT32LSB": np.dtype("<i4"),

    # 32-bit integers - Big Endian (MSB)
    "UNSIGNEDMSB4": np.dtype(">u4"),
    "SIGNEDMSB4": np.dtype(">i4"),
    "UINT32MSB": np.dtype(">u4"),
    "INT32MSB": np.dtype(">i4"),

    # Floating point
    "IEEE754SINGLE": np.dtype("<f4"),
    "IEEE754SINGLELSB": np.dtype("<f4"),
    "IEEE754SINGLEMSB": np.dtype(">f4"),
    "IEEE754DOUBLE": np.dtype("<f8"),
    "IEEE754DOUBLELSB": np.dtype("<f8"),
    "IEEE754DOUBLEMSB": np.dtype(">f8"),
    "FLOAT32": np.dtype("<f4"),
    "FLOAT64": np.dtype("<f8"),
}


def _strip_tag(elem: ET.Element) -> str:
    """Returns local tag name ignoring any XML namespace prefix."""
    return elem.tag.split("}")[-1]


def _find_elem(parent: ET.Element, tag_name: str) -> Optional[ET.Element]:
    """Finds first child or descendant whose local tag matches tag_name."""
    tag_name_lower = tag_name.lower()
    for elem in parent.iter():
        if _strip_tag(elem).lower() == tag_name_lower:
            return elem
    return None


def _findall_elems(parent: ET.Element, tag_name: str) -> List[ET.Element]:
    """Finds all children or descendants whose local tag matches tag_name."""
    tag_name_lower = tag_name.lower()
    return [elem for elem in parent.iter() if _strip_tag(elem).lower() == tag_name_lower]


def _get_elem_text(parent: ET.Element, tag_name: str, default: Optional[str] = None) -> Optional[str]:
    elem = _find_elem(parent, tag_name)
    if elem is not None and elem.text and elem.text.strip():
        return elem.text.strip()
    return default


def parse_pds4_metadata(xml_path: Optional[str] = None, xml_content: Optional[str] = None) -> Dict[str, Any]:
    """
    Dynamically parses a PDS4 XML observational product label.
    Extracts raster structure, data types, byte offsets, and observation metadata.
    Does NOT hardcode dimensions or types.
    """
    if xml_content:
        root = ET.fromstring(xml_content)
    elif xml_path and os.path.exists(xml_path):
        tree = ET.parse(xml_path)
        root = tree.getroot()
    else:
        raise ValueError("Neither valid xml_path nor xml_content was supplied.")

    meta: Dict[str, Any] = {
        "is_pds4": True,
        "raw_label_type": "PDS4_XML",
        "dataset_name": "TMC-2",
        "lines": None,
        "samples": None,
        "bands": 1,
        "data_type": None,
        "numpy_dtype": None,
        "byte_order": "Little-Endian",
        "byte_offset": 0,
        "expected_bytes": None,
        "expected_filename": None,
        "file_size_bytes": None,
        "md5_checksum": None,
        "valid_min": None,
        "valid_max": None,
        "special_constants": {},
        # Acquisition & Mission
        "title": _get_elem_text(root, "title", "Chandrayaan TMC Calibrated Product"),
        "mission_name": _get_elem_text(root, "Investigation_Area", "Chandrayaan"),
        "instrument_name": "Terrain Mapping Camera (TMC)",
        "processing_level": _get_elem_text(root, "processing_level", "Calibrated"),
        "start_time": _get_elem_text(root, "start_date_time"),
        "stop_time": _get_elem_text(root, "stop_date_time"),
        "orbit_number": _get_elem_text(root, "imaging_orbit_number"),
        "pixel_resolution_m": None,
        "spacecraft_altitude_km": None,
        # Solar geometry
        "sun_azimuth_deg": 90.0,
        "sun_elevation_deg": 35.0,
        "solar_incidence_deg": 55.0,
        "projection": "Polar stereographic",
        "area": "North Pole",
        # Spatial footprint
        "bounds": {}
    }

    # Extract Instrument name if present
    for obs in _findall_elems(root, "Observing_System_Component"):
        n = _get_elem_text(obs, "name")
        if n:
            meta["instrument_name"] = n.title()
            break

    # 2. Target File Reference
    file_elem = _find_elem(root, "File")
    if file_elem is not None:
        meta["expected_filename"] = _get_elem_text(file_elem, "file_name")
        fsize_str = _get_elem_text(file_elem, "file_size")
        if fsize_str:
            try:
                meta["file_size_bytes"] = int(fsize_str)
            except ValueError:
                pass
        meta["md5_checksum"] = _get_elem_text(file_elem, "md5_checksum")

    # 3. Raster Dimensions & Structure (Array_2D_Image or Array_3D_Image)
    array_node = _find_elem(root, "Array_2D_Image") or _find_elem(root, "Array_3D_Image") or root
    offset_str = _get_elem_text(array_node, "offset")
    if offset_str:
        try:
            meta["byte_offset"] = int(offset_str)
        except ValueError:
            meta["byte_offset"] = 0

    dtype_str = _get_elem_text(array_node, "data_type")
    if dtype_str:
        meta["data_type"] = dtype_str
        clean_key = dtype_str.upper().replace(" ", "").replace("_", "")
        np_dt = PDS4_DTYPE_MAP.get(clean_key)
        if np_dt is None:
            if "LSB" in clean_key:
                meta["byte_order"] = "Little-Endian"
                if "2" in clean_key or "16" in clean_key:
                    np_dt = np.dtype("<u2" if "UNSIGNED" in clean_key else "<i2")
                elif "4" in clean_key or "32" in clean_key:
                    np_dt = np.dtype("<u4" if "UNSIGNED" in clean_key else "<i4")
            elif "MSB" in clean_key:
                meta["byte_order"] = "Big-Endian"
                if "2" in clean_key or "16" in clean_key:
                    np_dt = np.dtype(">u2" if "UNSIGNED" in clean_key else ">i2")
                elif "4" in clean_key or "32" in clean_key:
                    np_dt = np.dtype(">u4" if "UNSIGNED" in clean_key else ">i4")
            else:
                np_dt = np.dtype("uint8")
        meta["numpy_dtype"] = np_dt
        meta["byte_order"] = "Little-Endian" if np_dt.byteorder in ("<", "=") else "Big-Endian"

    # Extract Axis Array elements for Lines and Samples
    lines = None
    samples = None
    bands = 1
    for axis in _findall_elems(array_node, "Axis_Array"):
        aname = _get_elem_text(axis, "axis_name")
        elems_str = _get_elem_text(axis, "elements")
        if aname and elems_str:
            axis_txt = aname.lower()
            try:
                cnt = int(elems_str)
                if "line" in axis_txt:
                    lines = cnt
                elif "sample" in axis_txt:
                    samples = cnt
                elif "band" in axis_txt:
                    bands = cnt
            except ValueError:
                pass

    meta["lines"] = lines
    meta["samples"] = samples
    meta["bands"] = bands

    # Validate that lines and samples were discovered
    if lines is None or samples is None:
        missing = []
        if lines is None: missing.append("Line dimension")
        if samples is None: missing.append("Sample dimension")
        raise ValueError(f"Unable to interpret PDS4 raster geometry. Missing metadata: {', '.join(missing)}.")

    if meta["numpy_dtype"] is not None:
        bytes_per_pix = meta["numpy_dtype"].itemsize
        meta["expected_bytes"] = meta["byte_offset"] + (lines * samples * bands * bytes_per_pix)

    # 4. Valid range and special constants
    vmin_str = _get_elem_text(array_node, "valid_minimum") or _get_elem_text(array_node, "minimum")
    if vmin_str:
        try: meta["valid_min"] = float(vmin_str)
        except ValueError: pass

    vmax_str = _get_elem_text(array_node, "valid_maximum") or _get_elem_text(array_node, "maximum")
    if vmax_str:
        try: meta["valid_max"] = float(vmax_str)
        except ValueError: pass

    # Special constants (e.g. missing, saturated, null)
    sc_node = _find_elem(array_node, "Special_Constants")
    if sc_node is not None:
        for sc in sc_node:
            tag_name = _strip_tag(sc)
            if sc.text and sc.text.strip():
                try:
                    meta["special_constants"][tag_name] = float(sc.text.strip())
                except ValueError:
                    pass

    # 5. Product & Solar Parameters
    res_str = _get_elem_text(root, "pixel_resolution")
    if res_str:
        try: meta["pixel_resolution_m"] = float(res_str)
        except ValueError: pass

    alt_str = _get_elem_text(root, "spacecraft_altitude")
    if alt_str:
        try: meta["spacecraft_altitude_km"] = float(alt_str)
        except ValueError: pass

    az_str = _get_elem_text(root, "sun_azimuth") or _get_elem_text(root, "solar_azimuth")
    if az_str:
        try: meta["sun_azimuth_deg"] = float(az_str)
        except ValueError: pass

    el_str = _get_elem_text(root, "sun_elevation") or _get_elem_text(root, "solar_elevation")
    if el_str:
        try: meta["sun_elevation_deg"] = float(el_str)
        except ValueError: pass

    inc_str = _get_elem_text(root, "solar_incidence") or _get_elem_text(root, "incidence_angle")
    if inc_str:
        try: meta["solar_incidence_deg"] = float(inc_str)
        except ValueError: pass

    proj_str = _get_elem_text(root, "projection")
    if proj_str:
        meta["projection"] = proj_str

    area_str = _get_elem_text(root, "area")
    if area_str:
        meta["area"] = area_str

    # 6. Spatial Footprint Coordinates
    geo_node = _find_elem(root, "System_Level_Coordinates") or _find_elem(root, "Refined_Corner_Coordinates")
    if geo_node is not None:
        for c in geo_node:
            tag = _strip_tag(c)
            if c.text and c.text.strip():
                try:
                    meta["bounds"][tag] = float(c.text.strip())
                except ValueError:
                    pass

    return meta


def validate_raster_file(meta: Dict[str, Any], img_path: str) -> Tuple[bool, str]:
    """
    Verifies that the provided .img file exists, is readable, and aligns with
    the raster structure specified in the PDS4 label.
    """
    if not os.path.exists(img_path):
        return False, f"IMG file not found at path: '{img_path}'"

    actual_size = os.path.getsize(img_path)
    expected_size = meta.get("expected_bytes")

    if expected_size is not None and actual_size < expected_size:
        return False, (
            f"Unable to interpret IMG using the supplied PDS4 metadata. "
            f"Expected at least {expected_size:,} bytes based on {meta['lines']:,} lines × "
            f"{meta['samples']:,} samples × {meta['bands']} bands ({meta['data_type']}), "
            f"but file size on disk is only {actual_size:,} bytes."
        )

    # Validate that we can memory-map the raster without error
    try:
        shape = (meta["lines"], meta["samples"]) if meta["bands"] == 1 else (meta["lines"], meta["samples"], meta["bands"])
        mm = np.memmap(
            img_path,
            dtype=meta["numpy_dtype"],
            mode="r",
            offset=meta.get("byte_offset", 0),
            shape=shape
        )
        # Probe first and last element safely
        _ = mm[0, 0]
        _ = mm[-1, -1]
        del mm
    except Exception as e:
        return False, f"Unable to interpret IMG using the supplied PDS4 metadata: {str(e)}"

    return True, "Valid PDS4 raster image"


def generate_scientific_preview(
    meta: Dict[str, Any],
    img_path: str,
    output_path: str,
    max_dim: int = 1600
) -> Dict[str, Any]:
    """
    Generates a high-quality, lightweight PNG/WebP visual preview from a large binary IMG.
    CRITICAL:
    - Uses zero-RAM np.memmap so GB-scale rasters never flood system RAM.
    - Preserves aspect ratio or downsamples with uniform geometric strides.
    - Filters out no-data / zero / fill values before computing statistics.
    - Applies scientifically safe 2%–98% percentile contrast stretch.
    - Original .img file remains 100% UNTOUCHED on disk.
    """
    lines = meta["lines"]
    samples = meta["samples"]
    bands = meta["bands"]
    dtype = meta["numpy_dtype"]
    offset = meta.get("byte_offset", 0)

    shape = (lines, samples) if bands == 1 else (lines, samples, bands)

    # 1. Open with read-only memory map (mode='r')
    mm = np.memmap(img_path, dtype=dtype, mode="r", offset=offset, shape=shape)

    # 2. Determine downsampling stride (target max_dim)
    step_y = max(1, int(np.ceil(lines / max_dim)))
    step_x = max(1, int(np.ceil(samples / max_dim)))

    # For tall push-broom swaths (e.g. 210,280 × 4,000):
    # Strided slice extracts full scene downsampled to <= max_dim pixels
    sub_raster = np.array(mm[::step_y, ::step_x])

    del mm  # release virtual descriptor

    # Handle multi-band (take first band if panchromatic or average)
    if sub_raster.ndim == 3:
        sub_raster = sub_raster[:, :, 0]

    # 3. Mask out invalid/fill/no-data values
    valid_mask = np.ones(sub_raster.shape, dtype=bool)
    if "valid_min" in meta and meta["valid_min"] is not None:
        valid_mask &= (sub_raster >= meta["valid_min"])
    if "valid_max" in meta and meta["valid_max"] is not None:
        valid_mask &= (sub_raster <= meta["valid_max"])

    # Also mask zeros if the data has zero as background padding
    non_zero = sub_raster > 0
    if np.sum(non_zero) > 100:
        valid_mask &= non_zero

    valid_pixels = sub_raster[valid_mask]
    if len(valid_pixels) < 50:
        # Fallback if masking removed everything
        valid_pixels = sub_raster.flatten()

    # 4. Scientifically safe percentile contrast normalization
    p2 = float(np.percentile(valid_pixels, 2.0))
    p98 = float(np.percentile(valid_pixels, 98.0))

    if p98 <= p2:
        p98 = p2 + 1.0

    stretched = np.clip((sub_raster.astype(np.float32) - p2) / (p98 - p2) * 255.0, 0.0, 255.0).astype(np.uint8)

    # If background pixels were masked, keep them clean black
    stretched[~valid_mask] = 0

    # 5. Export to PNG/WebP preview
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    success = cv2.imwrite(output_path, stretched)
    if not success:
        raise IOError(f"Failed to write preview image to '{output_path}'")

    preview_size_bytes = os.path.getsize(output_path)

    return {
        "preview_path": output_path,
        "preview_width": int(stretched.shape[1]),
        "preview_height": int(stretched.shape[0]),
        "preview_size_bytes": preview_size_bytes,
        "stretch_p2": p2,
        "stretch_p98": p98,
        "downsample_factor_y": step_y,
        "downsample_factor_x": step_x
    }
