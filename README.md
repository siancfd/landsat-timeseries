# landsat-timeseries

Multi-year Landsat composites (MSS → OLI-2, 1972–present) for any area of interest,
read directly from the [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
STAC API. No scene downloads: output blocks are warped from cloud-optimised GeoTIFFs via
HTTP range requests, so only pixels inside your AOI are transferred.

## Quick start

Written for Windows PowerShell; macOS/Linux differences are noted. You need internet access but
**no account**: Planetary Computer data is read with anonymous tokens.

### 1. Install the prerequisites (once)

- **Python 3.9 or newer** from [python.org](https://www.python.org/downloads/). On Windows tick
  *"Add Python to PATH"* in the installer.
- **Git** from [git-scm.com](https://git-scm.com/downloads). Alternatively skip Git: on the GitHub
  page click **Code → Download ZIP** and unzip it.

Check in a new terminal:

```powershell
python --version
git --version
```

### 2. Get the code

```powershell
git clone https://github.com/siancfd/landsat-timeseries.git
cd landsat-timeseries
```

### 3. Create an isolated environment and install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

macOS/Linux: activate with `source .venv/bin/activate`.

If PowerShell says *"running scripts is disabled on this system"*, run this once in the same
window and activate again (it only affects that window):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Your prompt should now start with `(.venv)`. Activate it again in every new terminal.

### 4. First run: the included Paris example

```powershell
landsat-timeseries examples\paris_eiffel_10km.geojson out --years 2022 --clip --crs EPSG:32631 --scenes-per-tile 3
```

`examples/paris_eiffel_10km.geojson` is a 10 × 10 km square around the Eiffel Tower, so this
finishes in a minute or two. Add `-v` for detailed logging. A healthy run logs, per year:

```
=== 2022: Landsat 8 OLI (landsat-c2-l2) ===
  ... candidate scene(s) -> ... selected
  block 1/1
  Wrote landsat_2022.tif | coverage ~100% | ... scene(s) used | 0 read failure(s)
```

### 5. Look at the result

Everything is written to the `out` folder:

- `landsat_2022_preview.png`: open it in any image viewer for a quick look. It should be square
  and show the Seine and the Bois de Boulogne.
- `landsat_2022.tif`: the real data. In [QGIS](https://qgis.org) drag it in, then *Layer
  Properties → Symbology → Render type: Multiband color* with **Red = band 3, Green = band 2,
  Blue = band 1** (swir16 / nir08 / red for the default year table) and a 2–98 % contrast stretch.
- `landsat_2022.json`: which scenes were used, coverage and parameters (for reproducibility).

**Close the `.tif` in QGIS (remove the layer) before re-running with `--overwrite`**; Windows
will not let the tool replace a file that is open in another program.

### 6. Use your own area

Any polygon file works (GeoJSON, Shapefile, GeoPackage). Start with a **small AOI and one year**
to check access and runtime before a large run, then drop `--years` to process every year.
**Choose `--crs` for your area** (next section).

## Choosing `--crs`

Output pixels are laid out in the CRS you give with `--crs`, at the sensor's native pixel size.
Use the **UTM zone of your area** (metric, close to the sensor's own grid):

| Area | UTM zone | `--crs` |
|---|---|---|
| Paris | 31N | `EPSG:32631` |
| Munich | 32N | `EPSG:32632` |
| Rajasthan | 43N | `EPSG:32643` |
| Abu Dhabi | 40N | `EPSG:32640` |
| Sydney | 56S | `EPSG:32756` |

Rule of thumb: zone = ⌊(longitude + 180) / 6⌋ + 1; northern hemisphere = `EPSG:326zz`,
southern = `EPSG:327zz`. Or let Python tell you:

```python
import geopandas as gpd
print(gpd.read_file("my_aoi.geojson").estimate_utm_crs().to_epsg())
```

If your AOI spans several UTM zones, use a regional projected CRS instead (for example
`EPSG:7755` for India).

> **Do not rely on the default.** The default is `EPSG:6933`, a global equal-area projection. It
> is valid everywhere but visibly stretches pixels at mid and high latitudes (about 1.7:1 at
> Paris), so "30 m" pixels are not 30 × 30 m. Always pass `--crs`.

## More examples

```bash
# one state out of a multi-feature boundary file, all default years, clipped to the polygon
landsat-timeseries my_aoi.geojson out/ --filter NAME_1=Rajasthan --clip --crs EPSG:32643

# two years, up to 3 scenes per tile for gap filling (cloud, Landsat 7 SLC-off)
landsat-timeseries my_aoi.gpkg out/ --years 2017 2022 --crs EPSG:32643 --scenes-per-tile 3

# your own year table
landsat-timeseries my_aoi.geojson out/ --config examples/years.yaml --crs EPSG:32643

landsat-timeseries --help
```

Or from Python:

```python
from landsat_timeseries import RunConfig, run
run(RunConfig(aoi_path="my_aoi.geojson", out_dir="out", clip=True, crs="EPSG:32643"))
```

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
  the output grid (nearest neighbour by default). Choose `--crs` deliberately (see above); a
  matching UTM zone keeps this close to a plain copy.
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

## Troubleshooting

| Symptom | Fix |
|---|---|
| `running scripts is disabled on this system` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate the venv again |
| `landsat-timeseries` is not recognised | The venv is not active (prompt lacks `(.venv)`), or `pip install -e .` was not run in it |
| `Cannot overwrite ... open in another program` | Remove the layer in QGIS / close the viewer, then re-run |
| `No usable scenes for <year>` | The archive has no matching scene over your AOI that year (early MSS/TM coverage is regional). Try another year or a different table |
| Image looks stretched / not square | You used the default CRS. Re-run with the UTM `--crs` for your area and `--overwrite` |
| Speckled nodata in cloudy areas | Increase `--scenes-per-tile` (e.g. 3) so other scenes can fill masked pixels |
| Run is slow | Large AOIs are read over HTTP. Start small, use `--years` to limit, or increase `--block-size` (multiple of 512) if you have RAM |
| `git` is not recognised | Install Git, then open a **new** terminal |

## Updating

```powershell
cd landsat-timeseries
git pull
.venv\Scripts\activate
pip install -e .
```

Existing outputs are left alone; use `--overwrite` to recompute a year with the new version.

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
