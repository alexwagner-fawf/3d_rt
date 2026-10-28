"""Noise filtering, ground classification (CSF) and DTM rasterization."""

from __future__ import annotations

import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import median_filter
from scipy.spatial import cKDTree

from voxsol.grid import Raster


def sor_filter(xyz: np.ndarray, k: int = 8, n_std: float = 3.0, tile: float = 50.0) -> np.ndarray:
    """Statistical outlier removal, tile by tile (with overlap). Returns a keep mask."""
    keep = np.ones(len(xyz), dtype=bool)
    if len(xyz) <= k:
        return keep
    halo = 2.0
    x0, y0 = xyz[:, 0].min(), xyz[:, 1].min()
    tx = ((xyz[:, 0] - x0) // tile).astype(np.int64)
    ty = ((xyz[:, 1] - y0) // tile).astype(np.int64)
    for i in range(tx.max() + 1):
        for j in range(ty.max() + 1):
            core = (tx == i) & (ty == j)
            if not core.any():
                continue
            xa, ya = x0 + i * tile - halo, y0 + j * tile - halo
            sel = ((xyz[:, 0] >= xa) & (xyz[:, 0] < xa + tile + 2 * halo)
                   & (xyz[:, 1] >= ya) & (xyz[:, 1] < ya + tile + 2 * halo))
            pts = xyz[sel]
            if len(pts) <= k:
                continue
            d, _ = cKDTree(pts).query(xyz[core], k=k + 1)
            mean_d = d[:, 1:].mean(axis=1)
            all_d, _ = cKDTree(pts).query(pts, k=k + 1)
            ref = all_d[:, 1:].mean(axis=1)
            keep[np.flatnonzero(core)] = mean_d <= ref.mean() + n_std * ref.std()
    return keep


def classify_ground_csf(xyz: np.ndarray, cloth_resolution: float = 0.5, rigidness: int = 2,
                        class_threshold: float = 0.3, slope_smooth: bool = True,
                        iterations: int = 500) -> np.ndarray:
    """Cloth simulation filter (Zhang et al. 2016). Returns a ground mask."""
    import CSF

    csf = CSF.CSF()
    csf.params.bSloopSmooth = slope_smooth
    csf.params.cloth_resolution = cloth_resolution
    csf.params.rigidness = rigidness
    csf.params.class_threshold = class_threshold
    csf.params.time_step = 0.65
    csf.params.interations = iterations  # (sic) attribute name in the CSF bindings
    shift = xyz.min(axis=0)
    csf.setPointCloud(np.ascontiguousarray(xyz - shift))
    ground, other = CSF.VecInt(), CSF.VecInt()
    csf.do_filtering(ground, other, False)
    mask = np.zeros(len(xyz), dtype=bool)
    mask[np.asarray(ground, dtype=np.int64)] = True
    return mask


def dtm_extent(xmin: float, ymin: float, xmax: float, ymax: float, res: float) -> tuple[float, float, int, int]:
    """Grid origin snapped to multiples of `res` and the cell counts covering the bounds."""
    x0 = np.floor(xmin / res) * res
    y0 = np.floor(ymin / res) * res
    nx = int(np.ceil((xmax - x0) / res))
    ny = int(np.ceil((ymax - y0) / res))
    return x0, y0, nx, ny


def rasterize_dtm(ground_xyz: np.ndarray, x0: float, y0: float, nx: int, ny: int, res: float,
                  crs: str | None = None, smooth: int = 3) -> tuple[Raster, np.ndarray]:
    """Mean ground height per cell, gaps filled by linear/nearest interpolation, light median smoothing.

    Returns the DTM and a mask of cells that contained ground points.
    """
    ix = np.clip(((ground_xyz[:, 0] - x0) / res).astype(np.int64), 0, nx - 1)
    iy = np.clip(((ground_xyz[:, 1] - y0) / res).astype(np.int64), 0, ny - 1)
    flat = iy * nx + ix
    cnt = np.bincount(flat, minlength=nx * ny)
    zsum = np.bincount(flat, weights=ground_xyz[:, 2], minlength=nx * ny)
    z = np.full(nx * ny, np.nan)
    has = cnt > 0
    z[has] = zsum[has] / cnt[has]
    z = z.reshape(ny, nx)
    has = has.reshape(ny, nx)
    if not has.all():
        yy, xx = np.mgrid[0:ny, 0:nx]
        pts = np.column_stack([xx[has], yy[has]])
        filled = griddata(pts, z[has], (xx, yy), method="linear")
        gaps = np.isnan(filled)
        if gaps.any():
            filled[gaps] = griddata(pts, z[has], (xx[gaps], yy[gaps]), method="nearest")
        z = filled
    if smooth > 1:
        z = median_filter(z, size=smooth, mode="nearest")
    return Raster(z, x0, y0, res, crs), has


def dtm_check(dtm: Raster, x: np.ndarray, y: np.ndarray, z_ref: np.ndarray) -> dict:
    """DTM minus reference heights (e.g. RTK logger ground heights)."""
    diff = dtm.sample(x, y) - z_ref
    diff = diff[np.isfinite(diff)]
    if diff.size == 0:
        return {}
    return {"n": int(diff.size), "bias_m": float(diff.mean()), "rmse_m": float(np.sqrt((diff**2).mean())),
            "max_abs_m": float(np.abs(diff).max())}
