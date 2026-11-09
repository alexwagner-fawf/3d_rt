"""Area of interest: polygon handling, and the synthetic pipeline restricted to an L-shaped AOI."""

import json

import fiona
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pyproj import Transformer

from voxsol import synthetic as syn
from voxsol.aoi import area, load_aoi, pixel_mask, points_in, read_aoi
from voxsol.config import AoiParams, SiteConfig, load_config
from voxsol.grid import Raster
from voxsol.pipeline import STEPS

SQUARE_WITH_HOLE = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                                       [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
FAR_SQUARE = {"type": "MultiPolygon", "coordinates": [[[[20, 0], [22, 0], [22, 2], [20, 2], [20, 0]]]]}
FC = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": g}
                                                for g in (SQUARE_WITH_HOLE, FAR_SQUARE)]}
L_LOCAL = [(15, 15), (45, 15), (45, 30), (30, 30), (30, 45), (15, 45), (15, 15)]  # m from the scene origin


def test_points_in_holes_and_parts():
    x = np.array([1, 5, 9.5, 21, 15, -1])
    y = np.array([1, 5, 9.5, 1, 1, 5])
    assert points_in(FC, x, y).tolist() == [True, False, True, True, False, False]
    assert area(FC) == pytest.approx(100 - 4 + 4)


def test_pixel_mask_covers_polygon_with_rim():
    tpl = Raster(np.zeros((12, 30), np.float32), -2.0, -1.0, 1.0)
    m = pixel_mask(FC, tpl)
    xs, ys = tpl.centers()
    inside = points_in(FC, xs.ravel(), ys.ravel()).reshape(m.shape)
    assert m[inside].all()
    # square x 0..10 = columns 2..11: one-pixel rim (column 1), nothing beyond it or between the parts
    assert m[1, 1] and not m[1, 0] and not m[1, 15] and m[1, 21] and m[1, 24] and not m[1, 25]
    assert m.sum() < m.size / 2


def _write_gpkg(path, rings_local, crs_out="EPSG:4326", name="A", other=True):
    to_ll = Transformer.from_crs("EPSG:25832", crs_out, always_xy=True)
    schema = {"geometry": "Polygon", "properties": {"name": "str"}}
    with fiona.open(path, "w", driver="GPKG", crs=crs_out, schema=schema, layer="plots") as dst:
        for nm, ring in [(name, rings_local)] + ([("B", [(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)])] if other else []):
            pts = [to_ll.transform(syn.X0 + x, syn.Y0 + y) for x, y in ring]
            dst.write({"geometry": {"type": "Polygon", "coordinates": [pts]}, "properties": {"name": nm}})


def test_read_aoi_transforms_and_filters(tmp_path):
    gpkg = tmp_path / "plots.gpkg"
    _write_gpkg(gpkg, L_LOCAL)
    fc = read_aoi(AoiParams(path=str(gpkg), where="name = 'A'"), "EPSG:25832")
    assert len(fc["features"]) == 1 and fc["features"][0]["properties"]["name"] == "A"
    ring = np.asarray(fc["features"][0]["geometry"]["coordinates"][0])
    assert np.allclose(ring - [syn.X0, syn.Y0], L_LOCAL, atol=1e-3)
    assert area(fc) == pytest.approx(30 * 15 + 15 * 15, rel=1e-4)
    assert len(read_aoi(AoiParams(path=str(gpkg)), "EPSG:25832")["features"]) == 2
    with pytest.raises(ValueError, match="no polygon"):
        read_aoi(AoiParams(path=str(gpkg), where="name = 'X'"), "EPSG:25832")
    line = tmp_path / "line.geojson"
    line.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}]}))
    with pytest.raises(ValueError, match="only Polygon"):
        read_aoi(AoiParams(path=str(line)), "EPSG:25832")


def test_config_accepts_a_plain_path():
    cfg = SiteConfig.model_validate({"site": "s", "crs": "EPSG:25832", "inputs": {"laz": "x.laz"}, "aoi": "a.gpkg"})
    assert cfg.aoi.path == "a.gpkg" and cfg.aoi.layer is None


SIZE, SEED = 60.0, 1


@pytest.fixture(scope="module")
def aoi_site(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    mp = pytest.MonkeyPatch()
    mp.setattr("voxsol.config.REPO_ROOT", root)
    cfg_path = root / "config" / "synthetic.yaml"
    syn.make_demo_site(root / "data", cfg_path, size=SIZE, n_loggers=16, seed=SEED,
                       start="2025-06-01", end="2025-06-07", pulse_rate=30_000.0)
    gpkg = root / "data" / "aoi.gpkg"
    _write_gpkg(gpkg, L_LOCAL)
    cfg = load_config(cfg_path)
    cfg.buffer_m = 10.0
    cfg.aoi = AoiParams(path=str(gpkg), layer="plots", where="name = 'A'")
    reports = {name: step(cfg) for name, step in STEPS.items()}
    yield cfg, reports
    mp.undo()


def test_prepare_keeps_the_context_box(aoi_site):
    cfg, rep = aoi_site
    pr = rep["prepare"]
    assert pr["aoi"]["area_ha"] == pytest.approx(0.0675, rel=1e-3)
    assert pr["points_read"] < 0.8 * pr["aoi"]["points_in_files"]
    dtm = Raster.read(cfg.workdir / "dtm.tif")
    x0, y0, x1, y1 = pr["aoi"]["context_box"]
    assert dtm.x0 >= x0 - 0.5 and dtm.x1 <= x1 + 0.5 and dtm.y0 >= y0 - 0.5 and dtm.y1 <= y1 + 0.5
    loggers = pd.read_csv(cfg.workdir / "loggers.csv")
    fc = load_aoi(cfg)
    assert len(loggers) > 0 and points_in(fc, loggers["x"], loggers["y"]).all()
    assert len(pr["loggers_outside_aoi"]) > 0


def test_maps_only_inside_the_aoi(aoi_site):
    cfg, rep = aoi_site
    fc = load_aoi(cfg)
    svf = Raster.read(cfg.workdir / "maps" / "svf_h.tif")
    xs, ys = svf.centers()
    inside = points_in(fc, xs.ravel(), ys.ravel()).reshape(svf.data.shape)
    computed = np.isfinite(svf.data)
    assert computed[inside].all() and rep["trace"]["map_pixels"] == computed.sum() < 0.85 * computed.size
    notch = Raster.read(cfg.workdir / "maps" / "us_index_15cm.tif").sample(
        np.array([syn.X0 + 38.0]), np.array([syn.Y0 + 38.0]))
    assert np.isnan(notch[0])  # inner corner of the L: outside the AOI
    with xr.open_dataset(cfg.workdir / "maps_daily.nc") as ds:
        day = ds["sw_dif_h"].isel(time=0).to_numpy()[::-1]  # north-up -> row 0 = south
    assert np.array_equal(np.isfinite(day), computed)
    trans = Raster.read(cfg.workdir / "insolation" / "trans_h_period.tif").data
    assert np.array_equal(np.isfinite(trans), computed) and np.nanmax(trans) <= 1.02
    static = pd.read_csv(cfg.workdir / "logger_static.csv")
    assert static[["svf_h", "us_index_15cm", "slope_deg"]].notna().all().all()


def test_changed_aoi_needs_prepare(aoi_site):
    cfg, _ = aoi_site
    other = cfg.model_copy(update={"aoi": AoiParams(path=cfg.aoi.path, layer="plots", where="name = 'B'")})
    with pytest.raises(ValueError, match="re-run the pipeline from prepare"):
        load_aoi(other)
    with pytest.raises(ValueError, match="re-run the pipeline from prepare"):
        load_aoi(cfg.model_copy(update={"aoi": None}))


def test_qgis_export_shows_the_aoi(aoi_site):
    from voxsol.qgis import export_qgis

    cfg, _ = aoi_site
    report = export_qgis(cfg, make_zip=False, log=lambda *_: None)
    assert report["layers"]["Area of interest"] == 1
    assert (cfg.workdir / f"{cfg.site}_qgis" / "aoi.geojson").exists()
