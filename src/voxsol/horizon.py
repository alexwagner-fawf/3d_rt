"""Far-terrain horizon from an external DEM (state DGM1, Copernicus GLO-30, ...)."""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

from voxsol.grid import Raster
from voxsol.raymarch import bilinear
from voxsol.trace import Horizon

EARTH_RADIUS = 6_371_000.0
REFRACTION_K = 0.13  # standard atmospheric refraction coefficient


@njit(cache=True, parallel=True)
def _horizon_kernel(dem, x0, y0, res, px, py, pz, n_az, az_step, min_dist, max_dist, r_eff):
    n = px.size
    ny, nx = dem.shape
    out = np.full((n, n_az), -90.0, dtype=np.float32)
    x1 = x0 + nx * res
    y1 = y0 + ny * res
    for i in prange(n):
        for a in range(n_az):
            az = math.radians((a + 0.5) * az_step)
            ux, uy = math.sin(az), math.cos(az)
            best = -90.0
            d = min_dist
            step = res
            while d < max_dist:
                x = px[i] + d * ux
                y = py[i] + d * uy
                if x < x0 or x >= x1 or y < y0 or y >= y1:
                    break
                z = bilinear(dem, x0, y0, res, x, y) - d * d / (2.0 * r_eff)
                ang = math.degrees(math.atan2(z - pz[i], d))
                if ang > best:
                    best = ang
                d += step
                step = min(step * 1.02, 4.0 * res)
            out[i, a] = best
    return out


def load_dem(path: str, crs: str, bounds: tuple[float, float, float, float], res: float) -> Raster:
    """Read (and if needed reproject) the DEM window covering `bounds` at resolution `res`."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import from_bounds

    xmin, ymin, xmax, ymax = bounds
    with rasterio.open(path) as src, WarpedVRT(src, crs=crs, resampling=Resampling.bilinear) as vrt:
        win = from_bounds(xmin, ymin, xmax, ymax, vrt.transform).intersection(
            rasterio.windows.Window(0, 0, vrt.width, vrt.height))
        b = rasterio.windows.bounds(win, vrt.transform)
        nx = max(int((b[2] - b[0]) / res), 2)
        ny = max(int((b[3] - b[1]) / res), 2)
        data = vrt.read(1, window=win, out_shape=(ny, nx), resampling=Resampling.bilinear).astype(np.float64)
        if vrt.nodata is not None:
            data[data == vrt.nodata] = np.nan
    if np.isnan(data).any():
        data[np.isnan(data)] = np.nanmin(data)
    return Raster(np.flipud(data), b[0], b[1], (b[2] - b[0]) / nx, crs)


def compute_horizon(dem: Raster, bounds: tuple[float, float, float, float], grid_res: float = 25.0,
                    az_step: float = 1.0, min_dist: float = 50.0, max_dist: float = 20_000.0,
                    observer_height: float = 1.0) -> Horizon:
    """Horizon elevation angles [deg] per azimuth bin on a coarse grid over `bounds`."""
    xmin, ymin, xmax, ymax = bounds
    nx = max(int(np.ceil((xmax - xmin) / grid_res)), 1)
    ny = max(int(np.ceil((ymax - ymin) / grid_res)), 1)
    xs = xmin + (np.arange(nx) + 0.5) * grid_res
    ys = ymin + (np.arange(ny) + 0.5) * grid_res
    gx, gy = np.meshgrid(xs, ys)
    pz = dem.sample(gx.ravel(), gy.ravel()) + observer_height
    n_az = int(round(360.0 / az_step))
    r_eff = EARTH_RADIUS / (1.0 - REFRACTION_K)
    h = _horizon_kernel(dem.data.astype(np.float64), dem.x0, dem.y0, dem.res, gx.ravel(), gy.ravel(), pz,
                        n_az, az_step, min_dist, max_dist, r_eff)
    return Horizon(h.reshape(ny, nx, n_az), xmin, ymin, grid_res, az_step)
