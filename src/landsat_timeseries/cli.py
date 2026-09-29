"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import DEFAULT_YEARS, PREVIEW_CHOICES, RESAMPLING_CHOICES, RunConfig, load_years
from .pipeline import run


def _parse_filter(text: str):
    if "=" not in text:
        raise argparse.ArgumentTypeError("expected FIELD=VALUE")
    field, value = text.split("=", 1)
    return field, value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="landsat-timeseries",
        description="Multi-year Landsat composites for an AOI, read directly from the Planetary Computer.",
    )
    p.add_argument("aoi", type=Path, help="AOI polygon file (GeoJSON, Shapefile, GeoPackage...)")
    p.add_argument("out_dir", type=Path, help="output directory")
    p.add_argument("--filter", type=_parse_filter, metavar="FIELD=VALUE",
                   help="keep only AOI features where FIELD equals VALUE (e.g. NAME_1=Rajasthan)")
    p.add_argument("--config", type=Path, help="YAML year table replacing the built-in one")
    p.add_argument("--years", type=int, nargs="+", help="process only these years")
    p.add_argument("--start-year", type=int, help="process only years >= this")
    p.add_argument("--crs", default="EPSG:6933", help="output CRS (default: %(default)s, global equal-area)")
    p.add_argument("--clip", action="store_true", help="mask pixels outside the AOI polygon")
    p.add_argument("--no-mask-clouds", action="store_true", help="skip QA_PIXEL cloud/shadow masking")
    p.add_argument("--scenes-per-tile", type=int, default=1,
                   help="scenes per WRS-2 tile available for gap filling (default: %(default)s)")
    p.add_argument("--max-cloud-cover", type=float, help="drop scenes above this cloud %%")
    p.add_argument("--block-size", type=int, default=4096, help="processing block, px (multiple of 512)")
    p.add_argument("--resampling", choices=RESAMPLING_CHOICES, default="nearest")
    p.add_argument("--preview", choices=PREVIEW_CHOICES, default="png",
                   help="also write a small preview image next to each GeoTIFF (default: %(default)s)")
    p.add_argument("--preview-size", type=int, default=2048, help="longest side of the preview in px")
    p.add_argument("--overwrite", action="store_true", help="redo years whose output already exists")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    table = load_years(args.config) if args.config else list(DEFAULT_YEARS)
    years = table
    if args.years:
        known = {s.year for s in table}
        unknown = sorted(set(args.years) - known)
        if unknown:
            print(f"Unknown year(s) {unknown}; available: {sorted(known)}", file=sys.stderr)
            return 2
        years = [s for s in years if s.year in set(args.years)]
    if args.start_year:
        years = [s for s in years if s.year >= args.start_year]
    if not years:
        print("No years selected.", file=sys.stderr)
        return 2

    try:
        cfg = RunConfig(
            aoi_path=args.aoi, out_dir=args.out_dir, years=years, aoi_filter=args.filter,
            crs=args.crs, clip=args.clip, mask_clouds=not args.no_mask_clouds,
            scenes_per_tile=args.scenes_per_tile, max_cloud_cover=args.max_cloud_cover,
            block_size=args.block_size, resampling=args.resampling, overwrite=args.overwrite,
            preview=args.preview, preview_size=args.preview_size,
        )
        run(cfg)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
