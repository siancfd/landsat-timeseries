# landsat-timeseries

Multi-year Landsat composites (MSS → OLI-2, 1972–present) for any area of interest,
read directly from the [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
STAC API. No scene downloads: output blocks are warped from cloud-optimised GeoTIFFs via
HTTP range requests, so only pixels inside your AOI are transferred.

## Install

```bash
pip install -e .
```

Python ≥ 3.9. Planetary Computer data is readable without an account (anonymous SAS tokens are
requested automatically).

## Usage

```bash
# one state out of a multi-feature boundary file, all default years, clipped to the polygon
landsat-timeseries my_aoi.geojson out/ --filter NAME_1=Rajasthan --clip

# just two years, projected CRS of your choice, up to 3 scenes per tile for gap filling
landsat-timeseries my_aoi.gpkg out/ --years 2017 2022 --crs EPSG:7755 --scenes-per-tile 3

landsat-timeseries --help
```

Or from Python:

```python
from landsat_timeseries import RunConfig, run
run(RunConfig(aoi_path="my_aoi.geojson", out_dir="out", clip=True))
```

Start with a **small AOI and one year** to check access and runtime before a large run.

## Output

Per year, in `OUT_DIR`:

| File | Content |
|---|---|
| `landsat_<year>.tif` | Multi-band `uint16` GeoTIFF, nodata = 0, band names set as descriptions, deflate-tiled |
| `landsat_<year>_preview.png` | Small contrast-stretched preview (longest side 2048 px, transparent where nodata) for quick viewing, sharing and README figures. `--preview jpg` for JPEG, `--preview none` to disable |
| `landsat_<year>.json` | Provenance: parameters, scene IDs actually used, AOI coverage %, read failures |

The preview uses true colour when blue/green/red are present, otherwise false colour (R=swir16, G=nir08, B=red), or colour infrared for MSS (R=nir08, G=red, B=green). It is for viewing only: it is downsampled, stretched per band and not georeferenced — use the GeoTIFF for analysis.

Existing years are skipped on re-run (`--overwrite` to redo). Files are written to `*.part` and
renamed on success, so an interrupted run never leaves a half-written GeoTIFF. The tool never
deletes anything it did not create.

## Default year table

| Year | Satellite | Sensor | Collection | Bands | Pixel |
|---|---|---|---|---|---|
| 1972 | Landsat 1 | MSS | L1 | green, red, nir08, nir09 | 60 m |
| 1977 | Landsat 2 | MSS | L1 | green, red, nir08, nir09 | 60 m |
| 1984 | Landsat 5 | TM | L1 | red, nir08, swir16, swir22 | 30 m |
| 1987–1997 | Landsat 5 | TM | L2 | red, nir08, swir16, swir22 | 30 m |
| 2002–2012 | Landsat 7 | ETM+ | L2 | red, nir08, swir16, swir22 | 30 m |
| 2017, 2022 | Landsat 8 | OLI | L2 | red, nir08, swir16, swir22 | 30 m |
| 2025 | Landsat 9 | OLI-2 | L2 | red, nir08, swir16, swir22 | 30 m |

Replace it with your own table via `--config` (see `examples/years.yaml`). Archive coverage
is regional: early years may have no scenes over your AOI (e.g. no usable Landsat 4 data over
India in 1982). Years without scenes are logged and skipped.

## How compositing works

1. Search the year for the configured platform/sensor; keep scenes that intersect the AOI and
   carry every required band.
2. Per WRS-2 tile keep the `--scenes-per-tile` least-cloudy scenes, ordered best-first.
3. The AOI bounding box is turned into a pixel grid in `--crs` at the sensor's native pixel size
   (edges snapped to 60 m so 30 m and 60 m years align).
4. Each block is filled scene by scene: a scene only fills pixels that are still empty, only where
   `QA_PIXEL` shows no fill / cloud / cirrus / shadow, and only where **all** bands are valid, so
   a pixel never mixes bands from different scenes.

## Limitations — read before using the data

- **Resampling:** the pixel *size* is native, but data are warped from each scene's UTM grid onto
  the output grid (nearest neighbour by default). Choose `--crs` deliberately; for area statistics
  use an equal-area or suitable projected CRS.
- **Digital numbers, not reflectance.** Values are stored as read. Level-2 assets carry
  scale/offset in the GeoTIFF metadata (`reflectance = DN × 0.0000275 − 0.2`) — apply it when
  analysing (`rioxarray`/GDAL-aware readers do so automatically if asked). 1972–1984 use Level-1
  DN, so those years are **not radiometrically comparable** with later years.
- **One acquisition date per tile per year** (plus fill scenes if `--scenes-per-tile > 1`). Mosaics
  mix dates and seasons across tiles; this is not a phenology-aware composite.
- **Landsat 7 after May 2003 (SLC-off)** has striping gaps (2007, 2012). Use `--scenes-per-tile 2`
  or more to fill them from other scenes.
- Cloud masking relies on the USGS `QA_PIXEL` band; thin cloud, haze and snow are not fully handled.
- Scenes that fail to read after retries are skipped for that block and counted in the manifest.

## Data and licensing

Landsat data are provided by USGS/NASA; see the Planetary Computer terms of use for access
conditions. **This repository contains no data and no AOI boundaries.** Check the licence of
whichever boundary dataset you use (e.g. GADM restricts redistribution and commercial use).

## Development

```bash
pip install -e ".[dev]" && pytest -q
```

## License

MIT — see `LICENSE`.
