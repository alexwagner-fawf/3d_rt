"""Per-pixel metrics of the layer below h_cut (understory), where 3D voxels are unreliable."""

from __future__ import annotations

import numpy as np

from voxsol.grid import Raster

LAYERS = ((0.0, 0.15), (0.15, 0.5), (0.5, None))  # upper bound None = h_cut
GAP_MIN = 0.02  # caps the contact-frequency index at -ln(0.02) ~ 3.9


def _pixel_index(x, y, tpl: Raster):
    ix = ((x - tpl.x0) / tpl.res).astype(np.int64)
    iy = ((y - tpl.y0) / tpl.res).astype(np.int64)
    ok = (ix >= 0) & (ix < tpl.nx) & (iy >= 0) & (iy < tpl.ny)
    return iy * tpl.nx + ix, ok


def _percentile_per_pixel(flat: np.ndarray, values: np.ndarray, n: int, q: float) -> np.ndarray:
    out = np.zeros(n)
    if flat.size == 0:
        return out
    order = np.lexsort((values, flat))
    f, v = flat[order], values[order]
    starts = np.flatnonzero(np.r_[True, f[1:] != f[:-1]])
    sizes = np.diff(np.r_[starts, f.size])
    pick = starts + np.minimum((q / 100.0 * (sizes - 1)).round().astype(np.int64), sizes - 1)
    out[f[starts]] = v[pick]
    return out


def understory_metrics(xyz: np.ndarray, hag: np.ndarray, ground: np.ndarray, tpl: Raster, h_cut: float,
                       sensor_heights=(0.02, 0.15), rgb: np.ndarray | None = None,
                       last_xy: np.ndarray | None = None, last_ground: np.ndarray | None = None,
                       exg_threshold: float = 0.05) -> dict[str, np.ndarray]:
    """Metrics from returns below h_cut (ground returns count as reaching hag = 0).

    us_index_<h>: contact-frequency proxy -ln(gap) with gap = share of low returns below sensor
    height h, i.e. how much vegetation lies between h and h_cut above that sensor.
    """
    n = tpl.nx * tpl.ny
    low = hag < h_cut
    flat, ok = _pixel_index(xyz[:, 0], xyz[:, 1], tpl)
    sel = low & ok
    f = flat[sel]
    h = np.where(ground[sel], 0.0, np.maximum(hag[sel], 0.0))
    veg = ~ground[sel] & (h > 0.05)
    n_low = np.bincount(f, minlength=n).astype(float)
    out = {"n_low": n_low}
    out["us_height"] = _percentile_per_pixel(f[veg], h[veg], n, 95.0)
    for lo, hi in LAYERS:
        hi = h_cut if hi is None else hi
        cnt = np.bincount(f[(h >= lo) & (h < hi) & veg], minlength=n)
        out[f"frac_{lo:g}_{hi:g}"] = np.divide(cnt, n_low, out=np.zeros(n), where=n_low > 0)
    for hs in sensor_heights:
        below = np.bincount(f[h < hs], minlength=n)
        gap = np.divide(below, n_low, out=np.ones(n), where=n_low > 0)
        out[f"us_index_{int(round(hs * 100))}cm"] = -np.log(np.clip(gap, GAP_MIN, 1.0))
    if rgb is not None:
        r, g, b = (rgb[sel].astype(float).T + 1e-9)
        s = r + g + b
        exg = (2 * g - r - b) / s
        green = veg & (exg > exg_threshold)
        nveg = np.bincount(f[veg], minlength=n)
        out["us_green_frac"] = np.divide(np.bincount(f[green], minlength=n), nveg, out=np.zeros(n), where=nveg > 0)
    if last_xy is not None and last_ground is not None:
        pf, pok = _pixel_index(last_xy[:, 0], last_xy[:, 1], tpl)
        tot = np.bincount(pf[pok], minlength=n)
        grd = np.bincount(pf[pok & last_ground], minlength=n)
        out["penetration"] = np.divide(grd, tot, out=np.full(n, np.nan), where=tot > 0)
    return {k: v.reshape(tpl.ny, tpl.nx) for k, v in out.items()}


def ground_surface_metrics(ground_xyz: np.ndarray, dtm: Raster, tpl: Raster, window_m: float = 4.0) -> dict[str, np.ndarray]:
    """Descriptors of merged vegetation/ground echoes (weak return separation in dense herbs).

    ground_lift: DTM above the local lower envelope (grey opening of the per-cell minimum of
    ground returns), i.e. how far the "ground" sits on top of the vegetation.
    ground_roughness: robust vertical spread (IQR / 1.349) of ground returns per map pixel.
    """
    from scipy.ndimage import grey_opening

    ix = np.clip(((ground_xyz[:, 0] - dtm.x0) / dtm.res).astype(np.int64), 0, dtm.nx - 1)
    iy = np.clip(((ground_xyz[:, 1] - dtm.y0) / dtm.res).astype(np.int64), 0, dtm.ny - 1)
    zmin = np.full(dtm.data.shape, np.inf)
    np.minimum.at(zmin, (iy, ix), ground_xyz[:, 2])
    zmin = np.where(np.isfinite(zmin), zmin, dtm.data)
    size = max(int(round(window_m / dtm.res)), 3)
    envelope = np.minimum(grey_opening(zmin, size=(size, size), mode="nearest"), dtm.data)
    lift = Raster(np.clip(dtm.data - envelope, 0.0, None), dtm.x0, dtm.y0, dtm.res)
    xs, ys = tpl.centers()
    out = {"ground_lift": lift.sample(xs.ravel(), ys.ravel()).reshape(tpl.ny, tpl.nx)}
    hag = ground_xyz[:, 2] - dtm.sample(ground_xyz[:, 0], ground_xyz[:, 1])
    flat, ok = _pixel_index(ground_xyz[:, 0], ground_xyz[:, 1], tpl)
    n = tpl.nx * tpl.ny
    q25 = _percentile_per_pixel(flat[ok], hag[ok], n, 25.0)
    q75 = _percentile_per_pixel(flat[ok], hag[ok], n, 75.0)
    out["ground_roughness"] = ((q75 - q25) / 1.349).reshape(tpl.ny, tpl.nx)
    return out


def understory_transmittance(us_index: np.ndarray, cosz: np.ndarray | float, k: float,
                             diffuse_factor: float = 1.66) -> tuple[np.ndarray, np.ndarray]:
    """Beer-Lambert transmittance of the understory for beam (path ~ 1/cos z) and diffuse light.

    `diffuse_factor` ~ 1/cos(53 deg) is the usual effective path for hemispherical diffuse light.
    """
    beam = np.exp(-k * us_index / np.maximum(cosz, 0.05))
    diffuse = np.exp(-k * us_index * diffuse_factor)
    return beam, diffuse
