"""STAC search and scene selection (Microsoft Planetary Computer)."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Iterable, List, Optional, Tuple

import planetary_computer
import pystac_client
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .config import YearSpec

log = logging.getLogger(__name__)

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def open_catalog() -> pystac_client.Client:
    # No sign_inplace modifier: hrefs are signed lazily at read time so a
    # long run never uses an expired SAS token.
    return pystac_client.Client.open(STAC_URL)


def signed_href(item, key: str) -> str:
    return planetary_computer.sign(item.assets[key].href)


def matches_satellite(item, sat_num: int, instrument: str) -> bool:
    platform = str(item.properties.get("platform", "")).lower()
    instruments = [str(i).lower() for i in (item.properties.get("instruments") or [])]
    platform_ok = str(sat_num) in re.findall(r"\d+", platform)
    # Substring match is deliberate ("oli" also matches "oli-2"); the platform
    # check above is what separates Landsat 8 from 9 and 5 from 7.
    instrument_ok = any(instrument.lower() in i for i in instruments) if instruments else True
    return platform_ok and instrument_ok


def cloud_cover(item) -> float:
    value = item.properties.get("eo:cloud_cover")
    return 100.0 if value is None else float(value)


def tile_key(item):
    path = item.properties.get("landsat:wrs_path")
    row = item.properties.get("landsat:wrs_row")
    if path is not None and row is not None:
        return (path, row)
    if getattr(item, "bbox", None):
        return (round((item.bbox[0] + item.bbox[2]) / 2, 1), round((item.bbox[1] + item.bbox[3]) / 2, 1))
    return item.id


def search_year(
    catalog: pystac_client.Client,
    spec: YearSpec,
    bbox: Tuple[float, float, float, float],
    aoi_geom_ll: BaseGeometry,
) -> List:
    """All scenes of the right satellite/sensor that intersect the AOI and carry every needed band."""
    search = catalog.search(
        collections=[spec.collection],
        bbox=list(bbox),
        datetime=f"{spec.year}-01-01/{spec.year}-12-31",
    )
    all_items = list(search.items())
    log.info("  %d scene(s) in %s before satellite filtering", len(all_items), spec.collection)

    matched = [it for it in all_items if matches_satellite(it, spec.sat_num, spec.instrument)]
    if not matched and all_items:
        s = all_items[0].properties
        log.warning(
            "  No scene matched sat_num=%s instrument=%s (example scene: platform=%s, instruments=%s)",
            spec.sat_num, spec.instrument, s.get("platform"), s.get("instruments"),
        )

    complete = [it for it in matched if all(b in it.assets for b in spec.bands)]
    if len(complete) < len(matched):
        log.info("  Dropped %d scene(s) missing a required band", len(matched) - len(complete))

    return [
        it for it in complete
        if it.geometry and shape(it.geometry).intersects(aoi_geom_ll)
    ]


def select_scenes(
    items: Iterable,
    scenes_per_tile: int = 1,
    max_cloud_cover: Optional[float] = None,
) -> List:
    """Rank scenes for compositing.

    Per WRS-2 tile keep the `scenes_per_tile` least-cloudy scenes. The result is
    ordered best-first across rank levels: every tile's best scene comes before
    any tile's second-best, so lower-ranked scenes only ever fill gaps.
    """
    pool = list(items)
    if max_cloud_cover is not None:
        pool = [it for it in pool if cloud_cover(it) <= max_cloud_cover]

    groups = defaultdict(list)
    for it in pool:
        groups[tile_key(it)].append(it)

    ranked = []
    for group in groups.values():
        group.sort(key=lambda i: (cloud_cover(i), i.id))
        for rank, it in enumerate(group[:scenes_per_tile]):
            ranked.append((rank, cloud_cover(it), it.id, it))
    ranked.sort(key=lambda t: t[:3])
    return [t[3] for t in ranked]
