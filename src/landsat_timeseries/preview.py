"""Small, viewer-friendly PNG/JPEG previews of the GeoTIFF composites."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import rasterio
import rasterio.shutil
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile

log = logging.getLogger(__name__)

PREVIEW_FORMATS = ("png", "jpg", "none")
_DRIVER = {"png": "PNG", "jpg": "JPEG"}


def choose_bands(names: Sequence[str]) -> Tuple[List[int], str]:
    """Pick three bands (1-based indexes, R,G,B order) and describe the resulting colour scheme."""
    idx = {n: i for i, n in enumerate(names, start=1)}
    if all(b in idx for b in ("red", "green", "blue")):
        return [idx["red"], idx["green"], idx["blue"]], "true colour (R=red, G=green, B=blue)"
    if all(b in idx for b in ("swir16", "nir08", "red")):
        return [idx["swir16"], idx["nir08"], idx["red"]], "false colour (R=swir16, G=nir08, B=red)"
    if all(b in idx for b in ("nir08", "red", "green")):
        return [idx["nir08"], idx["red"], idx["green"]], "colour infrared (R=nir08, G=red, B=green)"
    if len(names) >= 3:
        return [1, 2, 3], f"first three bands ({', '.join(names[:3])})"
    return [1, 1, 1], f"greyscale ({names[0]})"


def _stretch(band: np.ndarray, valid: np.ndarray, lo_pct: float = 2, hi_pct: float = 98) -> np.ndarray:
    lo, hi = np.percentile(band[valid], (lo_pct, hi_pct))
    if hi <= lo:
        hi = lo + 1
    scaled = (band.astype("float32") - lo) / (hi - lo)
    return (np.clip(scaled, 0, 1) * 255).astype("uint8")


def make_preview(
    tif_path: Path,
    out_path: Path,
    fmt: str = "png",
    max_size: int = 2048,
    quality: int = 90,
) -> Optional[Path]:
    """Write a downsampled, contrast-stretched preview next to the GeoTIFF.

    Only a decimated read is done, so memory stays small even for very large composites.
    PNG keeps nodata / clipped-out pixels transparent; JPEG has no alpha, so they are black.
    """
    if fmt not in _DRIVER:
        raise ValueError(f"fmt must be one of {tuple(_DRIVER)}")

    with rasterio.open(tif_path) as src:
        names = [d or f"band{i}" for i, d in enumerate(src.descriptions, start=1)]
        indexes, label = choose_bands(names)
        scale = min(1.0, max_size / max(src.width, src.height))
        out_w, out_h = max(1, round(src.width * scale)), max(1, round(src.height * scale))
        data = src.read(indexes, out_shape=(len(indexes), out_h, out_w), resampling=Resampling.nearest)

    valid = (data != 0).all(axis=0)
    if not valid.any():
        log.warning("  Preview skipped: no valid pixels in %s", tif_path.name)
        return None

    rgb = np.stack([_stretch(data[i], valid) for i in range(3)])
    rgb[:, ~valid] = 0

    with MemoryFile() as mem:
        count = 4 if fmt == "png" else 3
        with mem.open(driver="GTiff", dtype="uint8", count=count, width=out_w, height=out_h) as tmp:
            tmp.write(rgb, indexes=[1, 2, 3])
            if fmt == "png":
                tmp.write((valid * 255).astype("uint8"), 4)
                tmp.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
            options = {"QUALITY": quality} if fmt == "jpg" else {}
            rasterio.shutil.copy(tmp, str(out_path), driver=_DRIVER[fmt], **options)

    # GDAL may leave a sidecar .aux.xml next to the preview; it is not needed
    Path(str(out_path) + ".aux.xml").unlink(missing_ok=True)
    log.info("  Preview: %s (%d x %d px, %s)", out_path.name, out_w, out_h, label)
    return out_path
