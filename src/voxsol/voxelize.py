"""Beam-based extinction estimate per voxel (canopy layer above h_cut)."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter

from voxsol.grid import VoxelGrid
from voxsol.preprocess.pulses import Pulses
from voxsol.raymarch import accumulate_pulses, assign_tiles, pulse_start_params

LAMBDA_MAX = 20.0  # 1/m, effectively opaque at 0.25 m voxels


def accumulate(pulses: Pulses, grid: VoxelGrid, tile_vox: int = 64, ds: float | None = None):
    """Free path (beam-fraction weighted), intercepted weight and entering pulse counts per voxel."""
    ds = ds or 0.5 * min(grid.dx, grid.dz)
    dtm = grid.dtm.data.astype(np.float64, copy=False)
    s_end = pulses.echo_s[pulses.echo_ptr[1:] - 1]
    s_start = pulse_start_params(pulses.origin, pulses.direc, s_end, dtm, grid.dtm.x0, grid.dtm.y0,
                                 grid.dtm.res, grid.htop, 1.0)
    ntx = int(np.ceil(grid.nx / tile_vox))
    nty = int(np.ceil(grid.ny / tile_vox))
    ptr, items = assign_tiles(pulses.origin, pulses.direc, s_start, s_end, grid.x0, grid.y0,
                              tile_vox * grid.dx, ntx, nty)
    expo = grid.empty(np.float32)
    hits = grid.empty(np.float32)
    nin = grid.empty(np.uint16)
    accumulate_pulses(ptr, items, ntx, tile_vox, pulses.origin, pulses.direc, s_start, s_end,
                      pulses.echo_ptr, pulses.echo_s, pulses.echo_w, pulses.echo_canopy,
                      grid.x0, grid.y0, grid.dx, grid.h0, grid.dz, dtm, grid.dtm.x0, grid.dtm.y0,
                      grid.dtm.res, ds, expo, hits, nin)
    return expo, hits, nin


def estimate_lambda(expo: np.ndarray, hits: np.ndarray, nin: np.ndarray, pulse_min: int = 5,
                    small_sample_correction: bool = False) -> np.ndarray:
    """Exposure-based maximum likelihood estimate lambda = intercepted / free path.

    The free-path MLE of Pimont et al. (2018). `small_sample_correction` applies the classical
    (N-1)/N factor for exponential rates. Voxels with fewer than `pulse_min` entering pulses are
    NaN (occluded or not sampled).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = hits / expo
    if small_sample_correction:
        n = np.maximum(hits, 1.0)
        lam = lam * (n - 1.0) / n
    lam = np.where(expo > 0, lam, 0.0)
    lam = np.minimum(lam, LAMBDA_MAX).astype(np.float32)
    lam[nin < pulse_min] = np.nan
    return lam


def fill_unsampled(lam: np.ndarray, iterations: int = 4, size: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Fill NaN voxels with the mean of valid 3D neighbours (iteratively); leftovers become 0.

    Returns the filled array and the mask of voxels that were filled.
    """
    filled_mask = np.isnan(lam)
    out = lam.copy()
    for _ in range(iterations):
        bad = np.isnan(out)
        if not bad.any():
            break
        valid = (~bad).astype(np.float32)
        vals = np.where(bad, 0.0, out).astype(np.float32)
        num = uniform_filter(vals, size=size, mode="nearest")
        den = uniform_filter(valid, size=size, mode="nearest")
        upd = bad & (den > 1e-6)
        out[upd] = num[upd] / den[upd]
    out[np.isnan(out)] = 0.0
    return out, filled_mask


def voxelize(pulses: Pulses, grid: VoxelGrid, pulse_min: int = 5, tile_vox: int = 64) -> dict:
    expo, hits, nin = accumulate(pulses, grid, tile_vox=tile_vox)
    lam_raw = estimate_lambda(expo, hits, nin, pulse_min)
    lam, filled = fill_unsampled(lam_raw)
    grid.lam = lam
    return {"expo": expo, "hits": hits, "nin": nin, "filled": filled,
            "report": {"voxels": int(lam.size), "voxels_filled": int(filled.sum()),
                       "fraction_filled": float(filled.mean()), "lambda_max": float(lam.max())}}


def column_metrics(grid: VoxelGrid, g_leaf: float = 0.5) -> dict[str, np.ndarray]:
    """Per-column canopy metrics: PAI, vertical gap fraction, canopy top height above ground."""
    lam = grid.lam
    tau = lam.sum(axis=2) * grid.dz
    occupied = lam > 0.05
    ktop = grid.nz - np.argmax(occupied[:, :, ::-1], axis=2)
    top = np.where(occupied.any(axis=2), grid.h0 + ktop * grid.dz, 0.0)
    return {"pai": tau / g_leaf, "gap_vertical": np.exp(-tau), "canopy_height": top}
