"""End-to-end run: AOI -> STAC search -> per-year composites + provenance manifests."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import rasterio
from rasterio.enums import Resampling
from shapely.ops import unary_union

from . import __version__
from .composite import write_year
from .config import RunConfig
from .grid import Grid, load_aoi
from .preview import make_preview
from .stac import open_catalog, search_year, select_scenes

log = logging.getLogger(__name__)

GDAL_OPTIONS = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="3",
    VSI_CACHE="TRUE",
    GDAL_CACHEMAX=1024,  # MB; rasterio requires an int here
)


def _preview_target(cfg: RunConfig, tif_path: Path) -> Path:
    ext = "jpg" if cfg.preview == "jpg" else "png"
    return tif_path.with_name(f"{tif_path.stem}_preview.{ext}")


def _preview(cfg: RunConfig, tif_path: Path) -> Optional[Path]:
    """Create the PNG/JPEG preview; never let a preview problem abort the run."""
    if cfg.preview == "none":
        return None
    target = _preview_target(cfg, tif_path)
    try:
        return make_preview(tif_path, target, cfg.preview, cfg.preview_size)
    except Exception as exc:
        log.warning("  Preview failed (%s). If %s is open in a viewer, close it and re-run.",
                    exc, target.name)
        return None


def _restore(backup, out_path) -> None:
    """Put the previous output back if a re-run did not complete."""
    if backup is not None and backup.exists() and not out_path.exists():
        backup.replace(out_path)


def run(cfg: RunConfig) -> List[Path]:
    """Process every configured year. Returns the composites written in this run."""
    cfg.out_dir.mkdir(parents=True, exist_ok=True)

    aoi = load_aoi(cfg.aoi_path, cfg.aoi_filter)
    aoi_ll = aoi.to_crs(4326)
    bbox = tuple(float(v) for v in aoi_ll.total_bounds)
    aoi_geom_ll = unary_union(list(aoi_ll.geometry))
    aoi_proj = aoi.to_crs(cfg.crs)
    clip_geoms = list(aoi_proj.geometry) if cfg.clip else None
    snap = max(s.resolution_m for s in cfg.years)
    log.info("AOI: %d feature(s), bbox (lon/lat) = %s", len(aoi), tuple(round(v, 4) for v in bbox))

    catalog = open_catalog()
    written: List[Path] = []

    with rasterio.Env(**GDAL_OPTIONS):
        for spec in sorted(cfg.years, key=lambda s: s.year):
            log.info("=== %d: Landsat %d %s (%s) ===",
                     spec.year, spec.sat_num, spec.instrument.upper(), spec.collection)
            out_path = cfg.out_dir / f"landsat_{spec.year}.tif"
            backup = None
            if out_path.exists():
                if not cfg.overwrite:
                    log.info("  %s exists, skipping (use --overwrite to redo)", out_path.name)
                    if cfg.preview != "none" and not _preview_target(cfg, out_path).exists():
                        _preview(cfg, out_path)  # add a missing preview to an existing composite
                    continue
                # Move the old file aside *before* the long run: if another program (e.g. QGIS on
                # Windows) has it open this fails immediately instead of after all the work is done.
                backup = out_path.with_name(out_path.name + ".bak")
                try:
                    out_path.replace(backup)
                except PermissionError as exc:
                    raise PermissionError(
                        f"Cannot overwrite {out_path}: it is open in another program "
                        f"(e.g. a QGIS layer). Close or remove it and run again."
                    ) from exc

            try:
                items = search_year(catalog, spec, bbox, aoi_geom_ll)
                scenes = select_scenes(items, cfg.scenes_per_tile, cfg.max_cloud_cover)
            except BaseException:
                _restore(backup, out_path)
                raise
            if not scenes:
                log.warning("  No usable scenes for %d; skipping.", spec.year)
                _restore(backup, out_path)
                continue
            log.info("  %d candidate scene(s) -> %d selected", len(items), len(scenes))

            grid = Grid.from_bounds(tuple(aoi_proj.total_bounds), cfg.crs, spec.resolution_m, snap)
            gb = grid.width * grid.height * len(spec.bands) * 2 / 1e9
            log.info("  Output grid %d x %d px @ %.0f m (%.1f GB uncompressed)",
                     grid.width, grid.height, grid.resolution, gb)

            try:
                stats = write_year(
                    spec, scenes, grid, out_path,
                    resampling=Resampling[cfg.resampling],
                    mask_clouds=cfg.mask_clouds, clip_geoms=clip_geoms, block_size=cfg.block_size,
                )
            except BaseException:
                _restore(backup, out_path)
                raise
            if backup is not None:
                backup.unlink(missing_ok=True)
            log.info("  Wrote %s | coverage %.1f%% | %d scene(s) used | %d read failure(s)",
                     out_path.name, stats.coverage_pct, len(stats.used_scene_ids), stats.read_failures)
            if stats.read_failures:
                log.warning("  %d read(s) failed after retries; scenes skipped for the affected blocks.",
                            stats.read_failures)

            preview_path = _preview(cfg, out_path)

            manifest = {
                "tool_version": __version__,
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "year": spec.year,
                "platform": f"landsat-{spec.sat_num}",
                "instrument": spec.instrument,
                "collection": spec.collection,
                "bands": list(spec.bands),
                "crs": grid.crs.to_string(),
                "resolution_m": grid.resolution,
                "width": grid.width,
                "height": grid.height,
                "mask_clouds": cfg.mask_clouds,
                "clip": cfg.clip,
                "resampling": cfg.resampling,
                "scenes_per_tile": cfg.scenes_per_tile,
                "max_cloud_cover": cfg.max_cloud_cover,
                "candidate_scenes": len(scenes),
                "scene_ids_used": sorted(stats.used_scene_ids),
                "coverage_pct": round(stats.coverage_pct, 2),
                "read_failures": stats.read_failures,
                "preview": preview_path.name if preview_path else None,
            }
            out_path.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            written.append(out_path)

    log.info("Done. %d composite(s) written to %s", len(written), cfg.out_dir)
    return written
