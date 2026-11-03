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


def ortho_blocks(path: str, tpl: Raster, work_res: float | None = None, block_px: int = 2_000_000,
                 sample: int = 0, log=None):
    """Stream the orthomosaic over the template extent in row blocks.

    Each block is read at native resolution, or at about `work_res` if that is coarser (average
    resampling of RGB), and reprojected on the fly if the ortho CRS differs from the template CRS. Yields
    (rgb (3, n) float32, flat template pixel index (n,)) for the valid pixels inside the template, so
    memory depends on `block_px` (~150 bytes per working pixel), not on the ortho size. With
    `sample` > 0 only about that many blocks, evenly spread, are read (for global thresholds).
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window, from_bounds
    from rasterio.windows import transform as window_transform

    # one sequential pass: a large GDAL block cache (default 5% of RAM) only costs memory
    with rasterio.Env(GDAL_CACHEMAX=128), rasterio.open(path) as src:
        reproject = src.crs is not None and tpl.crs is not None and CRS.from_user_input(src.crs) != CRS.from_user_input(tpl.crs)
        ds = WarpedVRT(src, crs=tpl.crs, resampling=Resampling.bilinear) if reproject else src
        try:
            full = Window(0, 0, ds.width, ds.height)
            try:
                win = from_bounds(tpl.x0, tpl.y0, tpl.x1, tpl.y1, ds.transform).round_offsets().round_lengths()
                win = win.intersection(full)
            except rasterio.errors.WindowError:  # no overlap
                return
            native = abs(ds.transform.a)
            factor = max(1.0, (work_res or native) / native)
            out_w = max(1, int(round(win.width / factor)))
            rows = max(1, int(round(max(1, block_px // out_w) * factor)))  # source rows per block
            offsets = list(range(int(win.row_off), int(win.row_off + win.height), rows))
            if sample:
                offsets = offsets[::max(1, len(offsets) // sample)]
            elif log:
                log(f"ortho {ds.width}x{ds.height} px at {native:.4f} m, read at {native * factor:.3f} m in "
                    f"{len(offsets)} blocks{' (reprojected)' if reproject else ''}")
            for row_off in offsets:
                bw = Window(win.col_off, row_off, win.width, min(rows, win.row_off + win.height - row_off))
                out_h = max(1, int(round(bw.height / factor)))
                rgb = ds.read([1, 2, 3], window=bw, out_shape=(3, out_h, out_w), resampling=Resampling.average)
                valid = ds.read_masks(1, window=bw, out_shape=(out_h, out_w)) > 0
                bt = window_transform(bw, ds.transform)
                xs = bt.c + (np.arange(out_w) + 0.5) * bt.a * bw.width / out_w
                ys = bt.f + (np.arange(out_h) + 0.5) * bt.e * bw.height / out_h
                ix = np.floor((xs - tpl.x0) / tpl.res).astype(np.int64)
                iy = np.floor((ys - tpl.y0) / tpl.res).astype(np.int64)
                valid &= ((ix >= 0) & (ix < tpl.nx))[None, :] & ((iy >= 0) & (iy < tpl.ny))[:, None]
                if valid.any():
                    flat = iy[:, None] * tpl.nx + ix[None, :]
                    yield rgb[:, valid].astype(np.float32), flat[valid]
        finally:
            if reproject:
                ds.close()


def ortho_indices(path: str, tpl: Raster, work_res: float | None = None, block_px: int = 2_000_000,
                  log=None) -> dict[str, np.ndarray]:
    """Index means and green cover (Otsu threshold on ExG) per template pixel, from the ortho read
    block by block. The Otsu threshold comes from a pre-pass over ~20 spread blocks.

    At native resolution (default) the indices are computed per ortho pixel and averaged. With a
    coarser `work_res`, RGB is averaged first: faster, but ratio indices then weight bright pixels
    (soil) more than dark ones (vegetation), e.g. ExG up to ~0.05 lower in mixed cells.
    """
    n = tpl.nx * tpl.ny
    sample = []
    for rgb, _ in ortho_blocks(path, tpl, work_res, block_px, sample=20):
        exg = chromatic_indices(*rgb)["exg"]
        sample.append(exg[::max(1, exg.size // 500_000)])  # <= ~10 M values in total
    if not sample:
        raise ValueError(f"{path} does not overlap the map extent")
    threshold = otsu_threshold(np.concatenate(sample))
    names = ("exg", "gcc", "gli", "vari", "green_cover")
    sums = {k: np.zeros(n) for k in names}
    count = np.zeros(n)
    for rgb, flat in ortho_blocks(path, tpl, work_res, block_px, log=log):
        idx = chromatic_indices(*rgb)
        idx["green_cover"] = (idx["exg"] > threshold).astype(np.float64)
        for k in names:
            sums[k] += np.bincount(flat, weights=idx[k], minlength=n)
        count += np.bincount(flat, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {f"rgb_{k}": np.where(count > 0, sums[k] / count, np.nan).reshape(tpl.ny, tpl.nx) for k in names}


def ortho_brightness(path: str, tpl: Raster, work_res: float | None = None,
                     block_px: int = 2_000_000) -> np.ndarray:
    """Mean RGB brightness per template pixel (NaN outside the ortho)."""
    n = tpl.nx * tpl.ny
    total, count = np.zeros(n), np.zeros(n)
    for rgb, flat in ortho_blocks(path, tpl, work_res, block_px):
        total += np.bincount(flat, weights=rgb.mean(axis=0), minlength=n)
        count += np.bincount(flat, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan).reshape(tpl.ny, tpl.nx)


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
