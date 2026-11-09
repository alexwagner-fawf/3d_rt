"""Independent checks: modeled shadows vs. shadows visible in the orthomosaic."""

from __future__ import annotations

import numpy as np
import pandas as pd

from voxsol.config import SiteConfig
from voxsol.grid import Raster


def shadow_check(cfg: SiteConfig, surface_height: float = 0.3) -> dict:
    """Compare the modeled direct transmittance at the ortho acquisition time with ortho darkness.

    Only open pixels (canopy height <= h_cut) are used, where the ortho shows the ground or the
    understory top. Darkness is an Otsu threshold on brightness; it also reacts to dark materials,
    so agreement well above chance (kappa) rather than perfection is expected.
    """
    from voxsol.directions import sun_positions, unit_vector
    from voxsol.pipeline import _maps_dir, load_grid, map_mask, map_template, site_location
    from voxsol.rgb import ortho_brightness, otsu_threshold
    from voxsol.trace import macro_top, target_heights, trace

    if not (cfg.inputs.ortho and cfg.inputs.ortho_time):
        raise ValueError("shadow check needs inputs.ortho and inputs.ortho_time")
    grid = load_grid(cfg)
    tpl = map_template(cfg, grid)
    lat, lon, alt = site_location(cfg, grid.dtm)
    t = pd.Timestamp(cfg.inputs.ortho_time)
    t = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    sp = sun_positions(pd.DatetimeIndex([t]), lat, lon, alt)
    xs, ys = tpl.centers()
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    T, _ = trace(grid, target_heights(grid, xy, surface_height),
                 unit_vector(sp["azimuth"].to_numpy(), sp["elevation"].to_numpy()), macro=macro_top(grid))
    T = T[:, 0].reshape(tpl.ny, tpl.nx).astype(np.float32)
    mask = map_mask(cfg, tpl)
    if mask is not None:
        T[~mask] = np.nan
    Raster(T.astype(np.float32), tpl.x0, tpl.y0, tpl.res, cfg.crs).write(_maps_dir(cfg) / "shadow_model_ortho_time.tif")
    bright = ortho_brightness(cfg.inputs.ortho, tpl, cfg.understory.ortho_res, cfg.understory.ortho_block_px)
    ch = Raster.read(_maps_dir(cfg) / "canopy_canopy_height.tif").sample(xy[:, 0], xy[:, 1]).reshape(tpl.ny, tpl.nx)
    use = (ch <= cfg.grid.h_cut) & np.isfinite(bright) & np.isfinite(T)
    dark = bright < otsu_threshold(bright[use])
    shaded = T < 0.5
    a, b = dark[use], shaded[use]
    agree = float((a == b).mean())
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    kappa = float((agree - pe) / (1 - pe)) if pe < 1 else float("nan")
    rank_corr = float(pd.Series(T[use]).corr(pd.Series(bright[use]), method="spearman"))
    return {"sun_elevation": float(sp["elevation"].iloc[0]), "pixels": int(use.sum()),
            "modeled_shaded_fraction": float(b.mean()), "dark_fraction": float(a.mean()),
            "agreement": agree, "kappa": kappa, "spearman_T_brightness": rank_corr}
