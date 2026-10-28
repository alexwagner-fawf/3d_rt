"""End-to-end run on a small synthetic site, checked against the known truth."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from voxsol import synthetic as syn
from voxsol.config import load_config
from voxsol.directions import sky_directions, unit_vector
from voxsol.grid import Raster
from voxsol.pipeline import STEPS, load_forcing, load_grid, site_location
from voxsol.trace import macro_top, target_heights, trace
from voxsol.validate import shadow_check

SIZE, SEED = 60.0, 1


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    mp = pytest.MonkeyPatch()
    mp.setattr("voxsol.config.REPO_ROOT", root)
    cfg_path = root / "config" / "synthetic.yaml"
    syn.make_demo_site(root / "data", cfg_path, size=SIZE, n_loggers=8, seed=SEED,
                       start="2025-06-01", end="2025-06-07", pulse_rate=30_000.0)
    cfg = load_config(cfg_path)
    cfg.buffer_m = 10.0
    for step in STEPS.values():
        step(cfg)
    yield cfg, syn.make_scene(size=SIZE, seed=SEED)
    mp.undo()


def test_dtm_close_to_truth_on_bare_ground(site):
    cfg, scene = site
    dtm = Raster.read(cfg.workdir / "dtm.tif")
    xs, ys = scene.truth.column_centers()
    err = (dtm.sample(xs.ravel(), ys.ravel()) - scene.truth.dtm.sample(xs.ravel(), ys.ravel())).reshape(xs.shape)
    inner = (slice(20, -20), slice(20, -20))
    bare = scene.us_height[inner] == 0
    assert abs(np.median(err[inner][bare])) < 0.1
    # merged echoes in dense herbs lift the DTM (the L2 effect the split model accounts for)
    assert np.median(err[inner][scene.us_height[inner] > 0.6]) > np.median(err[inner][bare])


def test_sky_view_matches_truth(site):
    cfg, scene = site
    grid = load_grid(cfg)
    mt = np.load(cfg.workdir / "map_trace.npz")
    x0, y0, res, nx, ny = mt["tpl"]
    tpl = Raster(np.zeros((int(ny), int(nx))), x0, y0, res)
    xs, ys = tpl.centers()
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    dirs, w = sky_directions(400)
    T, _ = trace(scene.truth, target_heights(scene.truth, xy, cfg.grid.h_cut), dirs)
    svf_true = T @ w
    assert np.mean(np.abs(mt["svf_h"] - svf_true)) < 0.05
    assert np.corrcoef(mt["svf_h"], svf_true)[0, 1] > 0.9


def test_sun_bins_match_exact_tracing(site):
    cfg, _ = site
    from voxsol.irradiance import sky_components

    grid = load_grid(cfg)
    lat, lon, alt = site_location(cfg, grid.dtm)
    sky = sky_components(load_forcing(cfg), lat, lon, alt)
    mt = np.load(cfg.workdir / "map_trace.npz")
    x0, y0, res, nx, ny = mt["tpl"]
    tpl = Raster(np.zeros((int(ny), int(nx))), x0, y0, res)
    xs, ys = tpl.centers()
    rng = np.random.default_rng(0)
    pick = rng.choice(xs.size, 60, replace=False)
    xy = np.column_stack([xs.ravel()[pick], ys.ravel()[pick]])
    day = sky["elevation"].to_numpy() > 0
    T, _ = trace(grid, target_heights(grid, xy), unit_vector(sky["azimuth"][day], sky["elevation"][day]),
                 macro=macro_top(grid))
    e = (sky["beam_n"] * sky["cosz"]).to_numpy()[day] * sky.attrs["step_s"] * 1e-6
    d_idx = sky.index.normalize()[day]
    exact = pd.DataFrame(T * e[None, :]).T.groupby(np.asarray(d_idx)).sum().to_numpy()  # (days, pixels)
    with xr.open_dataset(cfg.workdir / "maps_daily.nc") as ds:
        binned = ds["sw_dir_h"].values[:, ::-1, :].reshape(len(ds.time), -1)[:, pick]
    assert abs(binned.sum() / exact.sum() - 1) < 0.02
    rel = np.abs(binned - exact) / np.maximum(exact, 0.5)
    assert np.median(rel) < 0.05


def test_logger_series_against_truth(site):
    cfg, scene = site
    from voxsol.irradiance import sky_components

    truth = pd.read_csv(cfg.workdir.parents[1] / "data" / "truth_loggers.csv")
    loggers = pd.read_csv(cfg.workdir / "loggers.csv")
    lat, lon, alt = site_location(cfg, scene.truth.dtm)
    sky = sky_components(load_forcing(cfg), lat, lon, alt)
    # truth radiation at h_cut (top of understory) at the true logger positions
    order = [int(np.argmin(np.hypot(truth["x_meas"] - r.x, truth["y_meas"] - r.y))) for r in loggers.itertuples()]
    true_xy = truth[["x", "y"]].to_numpy()[order]
    sw_true = syn.true_sensor_irradiance(scene, sky, true_xy, cfg.grid.h_cut).sum(1)
    series = pd.read_parquet(cfg.workdir / "logger_irradiance.parquet")
    sw_est = series.groupby("logger_id")["sw_tou_h"].sum().reindex(loggers["id"]).to_numpy()
    assert np.corrcoef(sw_est, sw_true)[0, 1] > 0.9
    assert np.median(np.abs(sw_est / sw_true - 1)) < 0.1
    static = pd.read_csv(cfg.workdir / "logger_static.csv")
    assert {"us_index_15cm", "svf_h_point", "rgb_exg", "canopy_pai"} <= set(static.columns)


def test_shadow_check_detects_shadows(site):
    cfg, _ = site
    rep = shadow_check(cfg)
    assert rep["kappa"] > 0.3 and rep["spearman_T_brightness"] > 0.2
