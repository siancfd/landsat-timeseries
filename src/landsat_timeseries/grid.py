"""AOI loading and the output pixel grid."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

import geopandas as gpd
from affine import Affine
from rasterio.crs import CRS
from rasterio.windows import Window


def load_aoi(path: Path, field_filter: Optional[Tuple[str, str]] = None) -> gpd.GeoDataFrame:
    """Read an AOI (GeoJSON, shapefile, GeoPackage...) and optionally filter by attribute."""
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"AOI '{path}' has no CRS; assign one before use.")
    if field_filter:
        name, value = field_filter
        if name not in gdf.columns:
            raise ValueError(f"Field '{name}' not in AOI; columns: {list(gdf.columns)}")
        available = sorted(gdf[name].astype(str).unique())
        gdf = gdf[gdf[name].astype(str) == value]
        if gdf.empty:
            raise ValueError(f"No feature with {name}={value!r}. Available: {available[:50]}")
    gdf = gdf[~(gdf.geometry.isna() | gdf.geometry.is_empty)].copy()
    if gdf.empty:
        raise ValueError("AOI contains no usable geometries.")
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf.reset_index(drop=True)


@dataclass(frozen=True)
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int
    resolution: float

    @classmethod
    def from_bounds(
        cls,
        bounds: Tuple[float, float, float, float],
        crs: str,
        resolution: float,
        snap: Optional[float] = None,
    ) -> "Grid":
        """Grid covering `bounds`, with edges snapped to a multiple of `snap` metres.

        Snapping to the coarsest resolution in the run keeps 30 m and 60 m years
        pixel-aligned with each other.
        """
        snap = snap or resolution
        if snap % resolution:
            snap = resolution
        minx, miny, maxx, maxy = bounds
        minx = math.floor(minx / snap) * snap
        miny = math.floor(miny / snap) * snap
        maxx = math.ceil(maxx / snap) * snap
        maxy = math.ceil(maxy / snap) * snap
        width = int(round((maxx - minx) / resolution))
        height = int(round((maxy - miny) / resolution))
        transform = Affine(resolution, 0.0, minx, 0.0, -resolution, maxy)
        return cls(CRS.from_user_input(crs), transform, width, height, resolution)


def iter_windows(width: int, height: int, block: int) -> Iterator[Window]:
    for row in range(0, height, block):
        for col in range(0, width, block):
            yield Window(col, row, min(block, width - col), min(block, height - row))
