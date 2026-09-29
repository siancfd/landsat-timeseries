from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from shapely.geometry import box, mapping

from landsat_timeseries import composite
from landsat_timeseries.config import DEFAULT_YEARS, RunConfig, YearSpec
from landsat_timeseries.grid import Grid, iter_windows
from landsat_timeseries.stac import matches_satellite, select_scenes, tile_key


def _item(id, cloud=10.0, path=None, row=None, **props):
    p = {"eo:cloud_cover": cloud, **props}
    if path is not None:
        p["landsat:wrs_path"], p["landsat:wrs_row"] = path, row
    return SimpleNamespace(id=id, properties=p, bbox=None, assets={})


# ---------- selection -------------------------------------------------------

def test_matches_satellite_separates_platforms():
    l5 = _item("a", platform="landsat-5", instruments=["tm"])
    l7 = _item("b", platform="landsat-7", instruments=["etm+"])
    l8 = _item("c", platform="landsat-8", instruments=["oli", "tirs"])
    l9 = _item("d", platform="landsat-9", instruments=["oli-2", "tirs-2"])
    assert matches_satellite(l5, 5, "tm") and not matches_satellite(l7, 5, "tm")
    assert matches_satellite(l7, 7, "etm+")
    assert matches_satellite(l8, 8, "oli") and not matches_satellite(l9, 8, "oli")
    assert matches_satellite(l9, 9, "oli-2")


def test_platform_digit_match_is_exact():
    assert not matches_satellite(_item("x", platform="landsat-11", instruments=["mss"]), 1, "mss")


def test_tile_key_falls_back_to_bbox():
    it = _item("x")
    it.bbox = [10.0, 20.0, 12.0, 22.0]
    assert tile_key(it) == (11.0, 21.0)


def test_select_scenes_best_first_and_rank_levels():
    items = [
        _item("A1", 5, 147, 41), _item("A2", 20, 147, 41), _item("A3", 40, 147, 41),
        _item("B1", 30, 148, 41), _item("B2", 2, 148, 41),
    ]
    one = select_scenes(items, scenes_per_tile=1)
    assert [i.id for i in one] == ["B2", "A1"]
    two = select_scenes(items, scenes_per_tile=2)
    # every tile's best scene precedes any tile's runner-up
    assert [i.id for i in two] == ["B2", "A1", "A2", "B1"]


def test_select_scenes_cloud_filter_and_missing_cloud_is_worst():
    items = [_item("ok", 10, 1, 1), _item("bad", 90, 2, 2)]
    assert [i.id for i in select_scenes(items, max_cloud_cover=50)] == ["ok"]
    no_cc = SimpleNamespace(id="n", properties={}, bbox=None, assets={})
    assert select_scenes([no_cc], max_cloud_cover=50) == []


# ---------- grid / config ---------------------------------------------------

def test_grid_snaps_and_aligns_30_and_60m():
    b = (100_013.0, 200_007.0, 190_100.0, 260_090.0)
    g30 = Grid.from_bounds(b, "EPSG:32643", 30.0, snap=60.0)
    g60 = Grid.from_bounds(b, "EPSG:32643", 60.0, snap=60.0)
    assert g30.transform.c == g60.transform.c and g30.transform.f == g60.transform.f
    assert g30.width == 2 * g60.width and g30.height == 2 * g60.height
    x0, y0, x1, y1 = array_bounds(g30.height, g30.width, g30.transform)
    assert x0 <= b[0] and y0 <= b[1] and x1 >= b[2] and y1 >= b[3]


def test_iter_windows_covers_grid_exactly():
    wins = list(iter_windows(1000, 700, 512))
    assert sum(w.width * w.height for w in wins) == 1000 * 700


def test_default_table_resolutions():
    res = {s.year: s.resolution_m for s in DEFAULT_YEARS}
    assert res[1972] == res[1977] == 60.0
    assert all(v == 30.0 for y, v in res.items() if y >= 1984)


def test_runconfig_validation(tmp_path):
    kw = dict(aoi_path=tmp_path / "a.geojson", out_dir=tmp_path)
    with pytest.raises(ValueError):
        RunConfig(block_size=1000, **kw)
    with pytest.raises(ValueError):
        RunConfig(scenes_per_tile=0, **kw)
    with pytest.raises(ValueError, match="invalid CRS"):
        RunConfig(crs="EPSG:not-a-code", **kw)
    with pytest.raises(ValueError, match="invalid CRS"):
        RunConfig(crs="nonsense", **kw)


# ---------- compositing on synthetic COG-like scenes ------------------------

UTM = CRS.from_epsg(32643)
CLEAR = 64        # QA_PIXEL "clear" bit, no bad bits
CLOUD = 64 | 8    # clear bit + cloud bit


def _write(path, arr, transform, nodata):
    with rasterio.open(path, "w", driver="GTiff", dtype="uint16", count=1,
                       width=arr.shape[1], height=arr.shape[0], crs=UTM,
                       transform=transform, nodata=nodata) as dst:
        dst.write(arr, 1)


def _scene(tmp_path, id, cloud, transform, size, value, *, gap_cols=0, cloud_rows=0):
    band = np.full((size, size), value, dtype=np.uint16)
    qa = np.full((size, size), CLEAR, dtype=np.uint16)
    if gap_cols:
        band[:, :gap_cols] = 0            # e.g. SLC-off stripe / no data
    if cloud_rows:
        qa[:cloud_rows, :] = CLOUD
    assets = {}
    for key, arr, nd in (("red", band, 0), ("nir08", band, 0), (composite.QA_ASSET, qa, 1)):
        p = tmp_path / f"{id}_{key}.tif"
        _write(p, arr, transform, nd)
        assets[key] = SimpleNamespace(href=str(p), extra_fields={})
    x0, y0, x1, y1 = array_bounds(size, size, transform)
    ll = transform_bounds(UTM, "EPSG:4326", x0, y0, x1, y1)
    return SimpleNamespace(id=id, properties={"eo:cloud_cover": cloud}, bbox=None,
                           geometry=mapping(box(*ll)), assets=assets)


@pytest.fixture(autouse=True)
def _local_hrefs(monkeypatch):
    monkeypatch.setattr(composite, "signed_href", lambda item, key: item.assets[key].href)


def test_composite_fills_gaps_and_masks_clouds(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    best = _scene(tmp_path, "best", 5, t, size, 100, gap_cols=16, cloud_rows=8)
    backup = _scene(tmp_path, "backup", 30, t, size, 200)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    grid = Grid(UTM, t, size, size, 30.0)
    stats = composite.YearStats()

    data, inside, remaining = composite.composite_block(
        [best, backup], spec, grid, Window(0, 0, size, size),
        resampling=Resampling.nearest, mask_clouds=True, clip_geoms=None, stats=stats,
    )
    assert not remaining.any()
    assert (data[0] == data[1]).all()                       # bands never mixed across scenes
    assert (data[0][:, :16] == 200).all()                   # gap filled from backup
    assert (data[0][:8, :] == 200).all()                    # cloud filled from backup
    assert (data[0][8:, 16:] == 100).all()                  # clean area keeps the best scene
    assert stats.used_scene_ids == {"best", "backup"}


def test_without_backup_cloud_pixels_stay_empty(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    only = _scene(tmp_path, "only", 5, t, size, 100, cloud_rows=8)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    data, inside, remaining = composite.composite_block(
        [only], spec, Grid(UTM, t, size, size, 30.0), Window(0, 0, size, size),
        resampling=Resampling.nearest, mask_clouds=True, clip_geoms=None, stats=composite.YearStats(),
    )
    assert (data[:, :8, :] == 0).all() and remaining[:8].all()
    assert (data[:, 8:, :] == 100).all()


def test_reprojection_to_equal_area_grid(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    scene = _scene(tmp_path, "s", 5, t, size, 150)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    # small grid well inside the scene footprint, in a different CRS
    x0, y0, x1, y1 = array_bounds(size, size, t)
    cx, cy = transform_bounds(UTM, "EPSG:6933", x0, y0, x1, y1)[0:2]
    grid = Grid.from_bounds((cx + 300, cy + 300, cx + 1200, cy + 1200), "EPSG:6933", 30.0, snap=60.0)
    data, inside, remaining = composite.composite_block(
        [scene], spec, grid, Window(0, 0, grid.width, grid.height),
        resampling=Resampling.nearest, mask_clouds=True, clip_geoms=None, stats=composite.YearStats(),
    )
    assert set(np.unique(data)) == {150}
    assert not remaining.any()


def test_clip_skips_blocks_outside_polygon(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    scene = _scene(tmp_path, "s", 5, t, size, 100)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    grid = Grid(UTM, t, size, size, 30.0)
    far_away = box(0, 0, 10, 10)
    res = composite.composite_block(
        [scene], spec, grid, Window(0, 0, size, size), resampling=Resampling.nearest,
        mask_clouds=True, clip_geoms=[far_away], stats=composite.YearStats(),
    )
    assert res is None


def test_write_year_is_atomic_and_tagged(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    scene = _scene(tmp_path, "s", 5, t, size, 100)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    out = tmp_path / "landsat_2000.tif"
    stats = composite.write_year(
        spec, [scene], Grid(UTM, t, size, size, 30.0), out, resampling=Resampling.nearest,
        mask_clouds=True, clip_geoms=None, block_size=512,
    )
    assert out.exists() and not (tmp_path / "landsat_2000.tif.part").exists()
    assert stats.coverage_pct == pytest.approx(100.0)
    with rasterio.open(out) as src:
        assert src.count == 2 and src.descriptions == ("red", "nir08")
        assert src.nodata == 0 and src.tags()["YEAR"] == "2000"
        assert (src.read(1) == 100).all()


def test_write_year_cleans_up_on_failure(tmp_path, monkeypatch):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    scene = _scene(tmp_path, "s", 5, t, size, 100)
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(composite, "composite_block", boom)
    out = tmp_path / "landsat_2000.tif"
    with pytest.raises(RuntimeError):
        composite.write_year(spec, [scene], Grid(UTM, t, size, size, 30.0), out,
                             resampling=Resampling.nearest, mask_clouds=True, clip_geoms=None, block_size=512)
    assert not out.exists() and not (tmp_path / "landsat_2000.tif.part").exists()
