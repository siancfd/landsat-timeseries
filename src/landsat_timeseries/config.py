"""Configuration objects and the default year table."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import yaml
from rasterio.crs import CRS
from rasterio.errors import CRSError

BLOCK_TILE = 512  # internal GeoTIFF tile size; processing blocks must be multiples of it
RESAMPLING_CHOICES = ("nearest", "bilinear", "cubic")
PREVIEW_CHOICES = ("png", "jpg", "none")

_SENSOR_RESOLUTION_M = {"mss": 60.0}
_DEFAULT_RESOLUTION_M = 30.0


@dataclass(frozen=True)
class YearSpec:
    """One row of the year -> satellite -> sensor -> product -> bands table."""

    year: int
    sat_num: int
    instrument: str
    collection: str
    bands: Tuple[str, ...]
    resolution: Optional[float] = None  # metres; None = sensor default (MSS 60, others 30)

    @property
    def resolution_m(self) -> float:
        if self.resolution is not None:
            return float(self.resolution)
        return _SENSOR_RESOLUTION_M.get(self.instrument.lower(), _DEFAULT_RESOLUTION_M)


_MSS = ("green", "red", "nir08", "nir09")
_OPTICAL = ("red", "nir08", "swir16", "swir22")

# Bands are Planetary Computer common asset names.
# 1972-1984 use Collection 2 Level-1 (DN); later years use Level-2 (surface reflectance).
DEFAULT_YEARS: List[YearSpec] = [
    YearSpec(1972, 1, "mss", "landsat-c2-l1", _MSS),
    YearSpec(1977, 2, "mss", "landsat-c2-l1", _MSS),
    YearSpec(1984, 5, "tm", "landsat-c2-l1", _OPTICAL),
    YearSpec(1987, 5, "tm", "landsat-c2-l2", _OPTICAL),
    YearSpec(1992, 5, "tm", "landsat-c2-l2", _OPTICAL),
    YearSpec(1997, 5, "tm", "landsat-c2-l2", _OPTICAL),
    YearSpec(2002, 7, "etm+", "landsat-c2-l2", _OPTICAL),
    YearSpec(2007, 7, "etm+", "landsat-c2-l2", _OPTICAL),
    YearSpec(2012, 7, "etm+", "landsat-c2-l2", _OPTICAL),
    YearSpec(2017, 8, "oli", "landsat-c2-l2", _OPTICAL),
    YearSpec(2022, 8, "oli", "landsat-c2-l2", _OPTICAL),
    YearSpec(2025, 9, "oli-2", "landsat-c2-l2", _OPTICAL),
]


def load_years(path: Path) -> List[YearSpec]:
    """Load a custom year table from YAML (see examples/years.yaml)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "years" not in data:
        raise ValueError(f"{path}: expected a top-level 'years:' list")
    required = ("year", "sat_num", "instrument", "collection", "bands")
    specs = []
    for i, row in enumerate(data["years"]):
        missing = [k for k in required if k not in row]
        if missing:
            raise ValueError(f"{path}: years[{i}] is missing {missing}")
        specs.append(
            YearSpec(
                year=int(row["year"]),
                sat_num=int(row["sat_num"]),
                instrument=str(row["instrument"]),
                collection=str(row["collection"]),
                bands=tuple(row["bands"]),
                resolution=float(row["resolution"]) if row.get("resolution") else None,
            )
        )
    return specs


@dataclass
class RunConfig:
    aoi_path: Path
    out_dir: Path
    years: Sequence[YearSpec] = field(default_factory=lambda: list(DEFAULT_YEARS))
    aoi_filter: Optional[Tuple[str, str]] = None  # (attribute field, value)
    crs: str = "EPSG:6933"  # global equal-area; pick a regional projected CRS if you can
    clip: bool = False  # mask pixels outside the AOI polygon (extent is always the AOI bbox)
    mask_clouds: bool = True  # use QA_PIXEL to drop cloud / shadow / cirrus / fill pixels
    scenes_per_tile: int = 1  # >1 lets lower-ranked scenes fill gaps (cloud, Landsat 7 SLC-off)
    max_cloud_cover: Optional[float] = None  # scene-level filter, percent
    block_size: int = 4096  # processing block in output pixels (memory vs. HTTP overhead)
    resampling: str = "nearest"
    overwrite: bool = False
    preview: str = "png"  # small viewer-friendly image next to each GeoTIFF
    preview_size: int = 2048  # longest side of the preview, px

    def __post_init__(self) -> None:
        self.aoi_path = Path(self.aoi_path)
        self.out_dir = Path(self.out_dir)
        try:
            CRS.from_user_input(self.crs)  # fail early on a bad CRS
        except (CRSError, ValueError) as exc:
            raise ValueError(f"invalid CRS {self.crs!r}: {exc}") from exc
        if self.block_size % BLOCK_TILE:
            raise ValueError(f"block_size must be a multiple of {BLOCK_TILE}")
        if self.scenes_per_tile < 1:
            raise ValueError("scenes_per_tile must be >= 1")
        if self.resampling not in RESAMPLING_CHOICES:
            raise ValueError(f"resampling must be one of {RESAMPLING_CHOICES}")
        if self.preview not in PREVIEW_CHOICES:
            raise ValueError(f"preview must be one of {PREVIEW_CHOICES}")
        if self.preview_size < 64:
            raise ValueError("preview_size must be >= 64")
        if not self.years:
            raise ValueError("no years selected")
