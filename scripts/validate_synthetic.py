"""Compare pipeline outputs of the synthetic demo site (config/synthetic.yaml) with the known truth."""

import sys

import numpy as np
import pandas as pd

from voxsol import ensure_local_env

ensure_local_env()

from voxsol import synthetic as syn  # noqa: E402
from voxsol.config import load_config  # noqa: E402
from voxsol.directions import sky_directions  # noqa: E402
from voxsol.grid import Raster  # noqa: E402
from voxsol.irradiance import sky_components  # noqa: E402
from voxsol.pipeline import load_forcing, site_location  # noqa: E402
from voxsol.trace import target_heights, trace  # noqa: E402
from voxsol.validate import shadow_check  # noqa: E402

cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config/synthetic.yaml")
scene = syn.make_scene(size=100.0, seed=1)
dtm = Raster.read(cfg.workdir / "dtm.tif")
xs, ys = scene.truth.column_centers()
err = dtm.sample(xs.ravel(), ys.ravel()).reshape(xs.shape) - scene.truth.dtm.data
inner = (slice(40, -40), slice(40, -40))
for label, m in (("bare", scene.us_height[inner] == 0), ("herbs > 0.6 m", scene.us_height[inner] > 0.6)):
    print(f"DTM error {label:14s}: median {np.median(err[inner][m]):+.3f} m, MAD {np.median(np.abs(err[inner][m])):.3f} m")

mt = np.load(cfg.workdir / "map_trace.npz")
x0, y0, res, nx, ny = mt["tpl"]
tpl = Raster(np.zeros((int(ny), int(nx))), x0, y0, res)
gx, gy = tpl.centers()
xy = np.column_stack([gx.ravel(), gy.ravel()])
dirs, w = sky_directions(400)
T, _ = trace(scene.truth, target_heights(scene.truth, xy, cfg.grid.h_cut), dirs)
svf_true = T @ w
print(f"Sky view (h_cut) vs truth : MAE {np.mean(np.abs(mt['svf_h'] - svf_true)):.3f}, "
      f"r {np.corrcoef(mt['svf_h'], svf_true)[0, 1]:.3f}, mean est {mt['svf_h'].mean():.3f} / true {svf_true.mean():.3f}")
print(f"Edge fraction (mean)      : {mt['edge'].mean():.3f}")

truth = pd.read_csv("data/synthetic/truth_loggers.csv")
loggers = pd.read_csv(cfg.workdir / "loggers.csv")
lat, lon, alt = site_location(cfg, dtm)
sky = sky_components(load_forcing(cfg), lat, lon, alt)
order = [int(np.argmin(np.hypot(truth["x_meas"] - r.x, truth["y_meas"] - r.y))) for r in loggers.itertuples()]
sw_true = syn.true_sensor_irradiance(scene, sky, truth[["x", "y"]].to_numpy()[order], cfg.grid.h_cut).sum(1)
series = pd.read_parquet(cfg.workdir / "logger_irradiance.parquet")
sw_est = series.groupby("logger_id")["sw_tou_h"].sum().reindex(loggers["id"]).to_numpy()
print(f"Logger SW_tou season sum  : r {np.corrcoef(sw_est, sw_true)[0, 1]:.3f}, "
      f"median |rel err| {np.median(np.abs(sw_est / sw_true - 1)):.3f}")
print("Shadow check (ortho)      :", {k: round(v, 3) for k, v in shadow_check(cfg).items()})
