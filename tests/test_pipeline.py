import json

import geopandas as gpd
import rasterio
from affine import Affine
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from shapely.geometry import box
from test_core import UTM, _local_hrefs, _scene  # noqa: F401  (fixture re-used)

from landsat_timeseries import cli, pipeline
from landsat_timeseries.config import RunConfig, YearSpec


def _setup(tmp_path):
    size = 64
    t = Affine(30.0, 0, 500_000.0, 0, -30.0, 3_000_000.0)
    scene = _scene(tmp_path, "s1", 5, t, size, 120)
    x0, y0, x1, y1 = array_bounds(size, size, t)
    w, s, e, n = transform_bounds(UTM, "EPSG:4326", x0, y0, x1, y1)
    dx, dy = (e - w) * 0.2, (n - s) * 0.2
    aoi = gpd.GeoDataFrame({"NAME_1": ["Inside"]}, geometry=[box(w + dx, s + dy, e - dx, n - dy)], crs=4326)
    aoi_path = tmp_path / "aoi.geojson"
    aoi.to_file(aoi_path, driver="GeoJSON")
    spec = YearSpec(2000, 5, "tm", "landsat-c2-l2", ("red", "nir08"))
    return aoi_path, spec, scene


def test_run_end_to_end_with_manifest_and_resume(tmp_path, monkeypatch):
    aoi_path, spec, scene = _setup(tmp_path)
    monkeypatch.setattr(pipeline, "open_catalog", lambda: object())
    monkeypatch.setattr(pipeline, "search_year", lambda *a, **k: [scene])
    out = tmp_path / "out"
    cfg = RunConfig(aoi_path=aoi_path, out_dir=out, years=[spec], clip=True, block_size=512)

    written = pipeline.run(cfg)
    assert [p.name for p in written] == ["landsat_2000.tif"]
    manifest = json.loads((out / "landsat_2000.json").read_text())
    assert manifest["scene_ids_used"] == ["s1"] and manifest["coverage_pct"] == 100.0
    with rasterio.open(out / "landsat_2000.tif") as src:
        data = src.read(1)
        assert src.crs.to_string() == "EPSG:6933" and 0 < (data == 120).sum() == (data != 0).sum()

    assert pipeline.run(cfg) == []  # resume: existing output is skipped
    cfg.overwrite = True
    assert len(pipeline.run(cfg)) == 1


def test_cli_rejects_unknown_year_and_bad_filter(tmp_path, capsys):
    aoi_path, _, _ = _setup(tmp_path)
    assert cli.main([str(aoi_path), str(tmp_path / "o"), "--years", "1999"]) == 2
    args = [str(aoi_path), str(tmp_path / "o"), "--years", "2002", "--filter", "NAME_1=Nowhere"]
    assert cli.main(args) == 2
    assert "Available" in capsys.readouterr().err


def test_overwrite_restores_old_output_if_rerun_fails(tmp_path, monkeypatch):
    aoi_path, spec, scene = _setup(tmp_path)
    monkeypatch.setattr(pipeline, "open_catalog", lambda: object())
    monkeypatch.setattr(pipeline, "search_year", lambda *a, **k: [scene])
    out = tmp_path / "out"
    cfg = RunConfig(aoi_path=aoi_path, out_dir=out, years=[spec], block_size=512)
    pipeline.run(cfg)
    before = (out / "landsat_2000.tif").read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "write_year", boom)
    cfg.overwrite = True
    import pytest
    with pytest.raises(RuntimeError):
        pipeline.run(cfg)
    assert (out / "landsat_2000.tif").read_bytes() == before      # old result preserved
    assert not (out / "landsat_2000.tif.bak").exists()


def test_overwrite_success_removes_backup(tmp_path, monkeypatch):
    aoi_path, spec, scene = _setup(tmp_path)
    monkeypatch.setattr(pipeline, "open_catalog", lambda: object())
    monkeypatch.setattr(pipeline, "search_year", lambda *a, **k: [scene])
    out = tmp_path / "out"
    cfg = RunConfig(aoi_path=aoi_path, out_dir=out, years=[spec], block_size=512, overwrite=True)
    pipeline.run(cfg)
    pipeline.run(cfg)
    assert (out / "landsat_2000.tif").exists() and not (out / "landsat_2000.tif.bak").exists()


def test_locked_output_fails_fast_with_clear_message(tmp_path, monkeypatch):
    import pathlib

    import pytest
    aoi_path, spec, scene = _setup(tmp_path)
    monkeypatch.setattr(pipeline, "open_catalog", lambda: object())
    monkeypatch.setattr(pipeline, "search_year", lambda *a, **k: [scene])
    out = tmp_path / "out"
    cfg = RunConfig(aoi_path=aoi_path, out_dir=out, years=[spec], block_size=512)
    pipeline.run(cfg)

    real_replace = pathlib.Path.replace

    def locked(self, target):
        if self.name == "landsat_2000.tif":
            raise PermissionError("WinError 5")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", locked)
    cfg.overwrite = True
    with pytest.raises(PermissionError, match="open in another program"):
        pipeline.run(cfg)
    assert (out / "landsat_2000.tif").exists()


def test_run_writes_preview_and_backfills_missing_one(tmp_path, monkeypatch):
    aoi_path, spec, scene = _setup(tmp_path)
    monkeypatch.setattr(pipeline, "open_catalog", lambda: object())
    monkeypatch.setattr(pipeline, "search_year", lambda *a, **k: [scene])
    out = tmp_path / "out"
    cfg = RunConfig(aoi_path=aoi_path, out_dir=out, years=[spec], clip=True, block_size=512)
    pipeline.run(cfg)
    png = out / "landsat_2000_preview.png"
    assert png.exists()
    assert json.loads((out / "landsat_2000.json").read_text())["preview"] == "landsat_2000_preview.png"

    png.unlink()
    assert pipeline.run(cfg) == []          # composite skipped ...
    assert png.exists()                     # ... but the missing preview is regenerated

    cfg2 = RunConfig(aoi_path=aoi_path, out_dir=tmp_path / "o2", years=[spec], block_size=512, preview="none")
    pipeline.run(cfg2)
    assert not list((tmp_path / "o2").glob("*_preview.*"))
