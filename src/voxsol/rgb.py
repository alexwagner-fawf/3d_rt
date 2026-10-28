"""RGB vegetation proxies: orthomosaic indices and green fraction of colored canopy points."""

from __future__ import annotations

import warnings

import numpy as np

from voxsol.grid import Raster, VoxelGrid


def chromatic_indices(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = (np.asarray(c, dtype=np.float64) for c in (r, g, b))
    s = r + g + b + 1e-9
    rn, gn, bn = r / s, g / s, b / s
    with np.errstate(divide="ignore", invalid="ignore"):
        vari = np.where(np.abs(g + r - b) > 1e-6, (g - r) / (g + r - b), 0.0)
        gli = np.where(np.abs(2 * g + r + b) > 1e-6, (2 * g - r - b) / (2 * g + r + b), 0.0)
    return {"exg": 2 * gn - rn - bn, "gcc": gn, "gli": gli, "vari": np.clip(vari, -1, 1)}


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=bins)
    centers = 0.5 * (edges[1:] + edges[:-1])
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


def ortho_indices(path: str, tpl: Raster) -> dict[str, np.ndarray]:
    """Index means and green cover (Otsu threshold on ExG) aggregated to the template raster."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.transform import from_origin

    with rasterio.open(path) as src:
        bands = src.read([1, 2, 3]).astype(np.float64)
        mask = src.read_masks(1) > 0
        src_transform, src_crs = src.transform, src.crs
    idx = chromatic_indices(*bands)
    idx["green_cover"] = (idx["exg"] > otsu_threshold(idx["exg"][mask])).astype(np.float64)
    dst_transform = from_origin(tpl.x0, tpl.y1, tpl.res, tpl.res)
    out = {}
    for name, arr in idx.items():
        arr = np.where(mask, arr, np.nan)
        dst = np.full((tpl.ny, tpl.nx), np.nan)
        reproject(arr, dst, src_transform=src_transform, src_crs=src_crs, dst_transform=dst_transform,
                  dst_crs=tpl.crs or src_crs, resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
        out[f"rgb_{name}"] = np.flipud(dst)
    return out


def voxel_green_fraction(xyz: np.ndarray, hag: np.ndarray, rgb: np.ndarray, grid: VoxelGrid,
                         exg_threshold: float = 0.05) -> np.ndarray:
    """Share of green (ExG > threshold) returns per canopy voxel; NaN where there are no returns.

    L2 colors are projected from nadir images, so the value is most reliable for upper surfaces.
    """
    ix = ((xyz[:, 0] - grid.x0) / grid.dx).astype(np.int64)
    iy = ((xyz[:, 1] - grid.y0) / grid.dx).astype(np.int64)
    k = np.floor((hag - grid.h0) / grid.dz).astype(np.int64)
    ok = (ix >= 0) & (ix < grid.nx) & (iy >= 0) & (iy < grid.ny) & (k >= 0) & (k < grid.nz)
    flat = (iy[ok] * grid.nx + ix[ok]) * grid.nz + k[ok]
    exg = chromatic_indices(*rgb[ok].T)["exg"]
    n = grid.nx * grid.ny * grid.nz
    tot = np.bincount(flat, minlength=n)
    green = np.bincount(flat[exg > exg_threshold], minlength=n)
    frac = np.divide(green, tot, out=np.full(n, np.nan), where=tot > 0)
    return frac.reshape(grid.ny, grid.nx, grid.nz).astype(np.float32)


def green_canopy_metrics(grid: VoxelGrid, green_frac: np.ndarray, g_leaf: float = 0.5) -> dict[str, np.ndarray]:
    """Green (transpiring) and non-green (dead/woody) plant area per column.

    Voxels without colored returns (occluded interior) inherit the column's mean green share.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # columns without colored returns
        col_mean = np.nanmean(np.where(grid.lam > 0, green_frac, np.nan), axis=2)
    col_mean = np.nan_to_num(col_mean, nan=0.0)
    gf = np.where(np.isnan(green_frac), col_mean[:, :, None], green_frac)
    green_pai = (grid.lam * gf).sum(axis=2) * grid.dz / g_leaf
    total = grid.lam.sum(axis=2) * grid.dz / g_leaf
    return {"pai_green": green_pai, "pai_nongreen": total - green_pai}
