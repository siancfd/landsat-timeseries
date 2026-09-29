"""Block-wise compositing straight from cloud-optimised GeoTIFFs.

Nothing is downloaded to disk: each output block is warped from the source
COGs via HTTP range requests, so only the pixels inside the AOI are read.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np
import rasterio
from rasterio import windows
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.features import rasterize
from rasterio.warp import reproject, transform_bounds
from shapely.geometry import box, shape

from .config import BLOCK_TILE, YearSpec
from .grid import Grid, iter_windows
from .stac import signed_href

log = logging.getLogger(__name__)

# QA_PIXEL bits: 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow.
# Bit 2 is unused (0) on sensors without a cirrus band, so testing it is harmless.
BAD_QA_MASK = 0b11111
QA_ASSET = "qa_pixel"
QA_FILL = 1  # value used where a scene does not cover the block -> flagged as fill


@dataclass
class YearStats:
    used_scene_ids: Set[str] = field(default_factory=set)
    read_failures: int = 0
    filled_pixels: int = 0
    total_pixels: int = 0

    @property
    def coverage_pct(self) -> float:
        return 100.0 * self.filled_pixels / self.total_pixels if self.total_pixels else 0.0


def _warp_asset(
    item,
    key: str,
    grid_crs,
    transform,
    shape_hw: Tuple[int, int],
    resampling: Resampling,
    *,
    fill: int,
    src_nodata: Optional[int],
    threads: int,
    retries: int = 4,
    base_delay: float = 2.0,
) -> Optional[np.ndarray]:
    """Warp one asset onto a block grid. Re-signs and retries on transient I/O errors."""
    for attempt in range(1, retries + 1):
        try:
            dst = np.full(shape_hw, fill, dtype=np.uint16)
            with rasterio.open(signed_href(item, key)) as src:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    dst_transform=transform,
                    dst_crs=grid_crs,
                    src_nodata=src_nodata,
                    dst_nodata=fill,
                    resampling=resampling,
                    num_threads=threads,
                )
            return dst
        except (RasterioError, OSError) as exc:
            if attempt == retries:
                log.warning("    giving up on %s/%s: %s", item.id, key, exc)
                return None
            delay = base_delay * 2 ** (attempt - 1)
            log.debug("    %s/%s failed (%s); retry %d/%d in %.0fs",
                      item.id, key, exc, attempt, retries, delay)
            time.sleep(delay)
    return None


def composite_block(
    scenes: Sequence,
    spec: YearSpec,
    grid: Grid,
    window: windows.Window,
    *,
    resampling: Resampling,
    mask_clouds: bool,
    clip_geoms: Optional[Sequence],
    stats: YearStats,
    threads: int = 1,
):
    """Composite one block.

    Returns (data, inside, remaining), or None if the block lies outside the clip polygon.

    Scenes are consumed best-first; a scene only fills pixels that are still
    empty, and a pixel is only filled if *all* bands are valid there, so no
    pixel ever mixes bands from different scenes.
    """
    height, width = int(window.height), int(window.width)
    transform = windows.transform(window, grid.transform)

    if clip_geoms is not None:
        inside = rasterize(
            [(g, 1) for g in clip_geoms], out_shape=(height, width),
            transform=transform, fill=0, dtype="uint8",
        ).astype(bool)
        if not inside.any():
            return None
    else:
        inside = np.ones((height, width), dtype=bool)

    out = np.zeros((len(spec.bands), height, width), dtype=np.uint16)
    remaining = inside.copy()
    block_ll = box(*transform_bounds(grid.crs, "EPSG:4326", *windows.bounds(window, grid.transform)))

    for item in scenes:
        if not remaining.any():
            break
        if not shape(item.geometry).intersects(block_ll):
            continue

        good = remaining
        if mask_clouds and QA_ASSET in item.assets:
            qa = _warp_asset(item, QA_ASSET, grid.crs, transform, (height, width),
                             Resampling.nearest, fill=QA_FILL, src_nodata=None, threads=threads)
            if qa is None:  # never fall back to unmasked data silently
                stats.read_failures += 1
                continue
            good = remaining & ((qa & BAD_QA_MASK) == 0)
        if not good.any():
            continue

        arrays: List[np.ndarray] = []
        for band in spec.bands:
            arr = _warp_asset(item, band, grid.crs, transform, (height, width),
                              resampling, fill=0, src_nodata=0, threads=threads)
            if arr is None:
                stats.read_failures += 1
                break
            arrays.append(arr)
        if len(arrays) != len(spec.bands):
            continue

        stack = np.stack(arrays)
        valid = good & (stack != 0).all(axis=0)
        if valid.any():
            out[:, valid] = stack[:, valid]
            remaining &= ~valid
            stats.used_scene_ids.add(item.id)

    return out, inside, remaining


def _scale_offset(item, key: str) -> Tuple[float, float]:
    rb = (item.assets[key].extra_fields or {}).get("raster:bands") or [{}]
    return float(rb[0].get("scale", 1.0)), float(rb[0].get("offset", 0.0))


def write_year(
    spec: YearSpec,
    scenes: Sequence,
    grid: Grid,
    out_path: Path,
    *,
    resampling: Resampling,
    mask_clouds: bool,
    clip_geoms: Optional[Sequence],
    block_size: int,
) -> YearStats:
    """Write one year's composite. Output appears atomically: a crash never leaves a half-written .tif."""
    stats = YearStats()
    threads = max(1, min(8, os.cpu_count() or 1))
    scales, offsets = zip(*(_scale_offset(scenes[0], b) for b in spec.bands))

    profile = dict(
        driver="GTiff", dtype="uint16", count=len(spec.bands),
        width=grid.width, height=grid.height, crs=grid.crs, transform=grid.transform,
        nodata=0, compress="deflate", predictor=2,
        tiled=True, blockxsize=BLOCK_TILE, blockysize=BLOCK_TILE, BIGTIFF="IF_SAFER",
    )
    blocks = list(iter_windows(grid.width, grid.height, block_size))
    tmp_path = out_path.with_name(out_path.name + ".part")

    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            for i, band in enumerate(spec.bands, start=1):
                dst.set_band_description(i, band)
            if any(s != 1.0 for s in scales) or any(o != 0.0 for o in offsets):
                dst.scales = scales
                dst.offsets = offsets
            dst.update_tags(
                YEAR=spec.year, PLATFORM=f"landsat-{spec.sat_num}",
                INSTRUMENT=spec.instrument, COLLECTION=spec.collection,
            )
            for n, window in enumerate(blocks, start=1):
                log.info("  block %d/%d", n, len(blocks))
                result = composite_block(
                    scenes, spec, grid, window, resampling=resampling,
                    mask_clouds=mask_clouds, clip_geoms=clip_geoms, stats=stats, threads=threads,
                )
                if result is None:
                    continue
                data, inside, remaining = result
                stats.total_pixels += int(inside.sum())
                stats.filled_pixels += int((inside & ~remaining).sum())
                if data.any():
                    dst.write(data, window=window)
        tmp_path.replace(out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return stats
