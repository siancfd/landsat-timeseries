import numpy as np
import pytest
import rasterio
from affine import Affine

from landsat_timeseries.preview import choose_bands, make_preview


def _tif(path, names, width=300, height=200):
    rng = np.random.default_rng(0)
    data = rng.integers(2000, 20000, size=(len(names), height, width)).astype("uint16")
    data[:, :20, :] = 0  # nodata strip (e.g. clipped-out area)
    with rasterio.open(path, "w", driver="GTiff", dtype="uint16", count=len(names), width=width,
                       height=height, crs="EPSG:32631", transform=Affine(30, 0, 500000, 0, -30, 5400000),
                       nodata=0) as dst:
        dst.write(data)
        for i, n in enumerate(names, 1):
            dst.set_band_description(i, n)
    return path


def test_choose_bands_schemes():
    assert choose_bands(["red", "green", "blue", "nir08"])[0] == [1, 2, 3]
    assert choose_bands(["red", "nir08", "swir16", "swir22"])[0] == [3, 2, 1]   # R=swir16 G=nir08 B=red
    assert choose_bands(["green", "red", "nir08", "nir09"])[0] == [3, 2, 1]     # CIR: R=nir08 G=red B=green
    assert choose_bands(["a", "b", "c", "d"])[0] == [1, 2, 3]
    assert choose_bands(["only"])[0] == [1, 1, 1]


def test_png_preview_is_downsampled_with_transparent_nodata(tmp_path):
    tif = _tif(tmp_path / "landsat_2000.tif", ["red", "nir08", "swir16", "swir22"])
    out = make_preview(tif, tmp_path / "p.png", "png", max_size=100)
    assert out.exists()
    with rasterio.open(out) as src:
        assert max(src.width, src.height) == 100 and src.count == 4
        alpha = src.read(4)
        assert alpha[0].max() == 0 and alpha[-1].min() == 255   # top strip transparent, rest opaque
    assert not (tmp_path / "p.png.aux.xml").exists()
    assert out.stat().st_size < tif.stat().st_size


def test_jpg_preview(tmp_path):
    tif = _tif(tmp_path / "landsat_2000.tif", ["red", "nir08", "swir16", "swir22"])
    out = make_preview(tif, tmp_path / "p.jpg", "jpg", max_size=150)
    with rasterio.open(out) as src:
        assert src.count == 3 and max(src.width, src.height) == 150


def test_no_upscaling_and_all_nodata(tmp_path):
    tif = _tif(tmp_path / "a.tif", ["red", "nir08", "swir16", "swir22"], width=80, height=60)
    out = make_preview(tif, tmp_path / "small.png", "png", max_size=2048)
    with rasterio.open(out) as src:
        assert (src.width, src.height) == (80, 60)
    empty = tmp_path / "empty.tif"
    with rasterio.open(empty, "w", driver="GTiff", dtype="uint16", count=3,
                       width=10, height=10, nodata=0) as d:
        d.write(np.zeros((3, 10, 10), dtype="uint16"))
    assert make_preview(empty, tmp_path / "e.png") is None


def test_bad_format(tmp_path):
    with pytest.raises(ValueError):
        make_preview(tmp_path / "x.tif", tmp_path / "x.gif", "gif")
