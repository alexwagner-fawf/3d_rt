"""Transmittance at targets (logger uncertainty disks, map pixels) towards sky and sun directions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter

from voxsol.directions import GOLDEN_ANGLE
from voxsol.grid import Raster, VoxelGrid
from voxsol.raymarch import transmittance_kernel

MACRO_CELL_VOX = 8


@dataclass
class Horizon:
    """Far-terrain horizon elevation [deg] per azimuth bin on a coarse grid (row 0 = south)."""

    data: np.ndarray  # (ny, nx, naz) float32
    x0: float
    y0: float
    res: float
    az_step: float

    @classmethod
    def none(cls) -> "Horizon":
        return cls(np.full((1, 1, 1), -90.0, np.float32), 0.0, 0.0, 1.0, 360.0)

    def save(self, path) -> None:
        np.savez(path, data=self.data, meta=np.array([self.x0, self.y0, self.res, self.az_step]))

    @classmethod
    def load(cls, path) -> "Horizon":
        f = np.load(path)
        x0, y0, res, az_step = f["meta"]
        return cls(f["data"], x0, y0, res, az_step)


@dataclass
class MacroTop:
    top: np.ndarray  # (mny, mnx) highest absolute z of canopy or terrain in/around each cell
    cs: float
    global_top: float


def macro_top(grid: VoxelGrid, cell_vox: int = MACRO_CELL_VOX) -> MacroTop:
    """Conservative empty-space-skipping grid (dilated by one cell)."""
    cs = grid.dx * cell_vox
    mnx = int(np.ceil(grid.nx / cell_vox))
    mny = int(np.ceil(grid.ny / cell_vox))
    occupied = grid.lam > 0
    any_occ = occupied.any(axis=2)
    ktop = grid.nz - np.argmax(occupied[:, :, ::-1], axis=2)
    top_hag = np.where(any_occ, grid.h0 + ktop * grid.dz, 0.0)
    pad = np.zeros((mny * cell_vox, mnx * cell_vox))
    pad[: grid.ny, : grid.nx] = top_hag
    canopy = pad.reshape(mny, cell_vox, mnx, cell_vox).max(axis=(1, 3))
    # terrain: max DTM node value per macro cell (bilinear interpolation never exceeds its nodes)
    xs, ys = grid.dtm.centers()
    cx = np.clip(((xs - grid.x0) / cs).astype(np.int64), 0, mnx - 1)
    cy = np.clip(((ys - grid.y0) / cs).astype(np.int64), 0, mny - 1)
    terrain = np.full((mny, mnx), -np.inf)
    np.maximum.at(terrain, (cy.ravel(), cx.ravel()), grid.dtm.data.ravel())
    terrain[~np.isfinite(terrain)] = np.nanmax(grid.dtm.data)
    top = maximum_filter(maximum_filter(terrain, size=3) + canopy, size=3) + grid.dz
    return MacroTop(top.astype(np.float64), cs, float(top.max()) + 1.0)


def effective_lambda(grid: VoxelGrid, sigma_leaf: float) -> np.ndarray:
    """Shortwave extinction; sqrt(1 - sigma) approximates leaf scattering (sigma = 0: geometric)."""
    lam = grid.lam.astype(np.float32, copy=False)
    return lam if sigma_leaf == 0 else lam * np.float32(np.sqrt(1.0 - sigma_leaf))


def trace(grid: VoxelGrid, xyz: np.ndarray, dirs: np.ndarray, *, macro: MacroTop | None = None,
          horizon: Horizon | None = None, sigma_leaf: float = 0.0, t_min: float = 1e-3,
          ds: float | None = None, lam: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Transmittance (n_targets, n_dirs) and edge flags (ray left the grid below the canopy top)."""
    macro = macro or macro_top(grid)
    lam = effective_lambda(grid, sigma_leaf) if lam is None else lam
    horizon = horizon or Horizon.none()
    ds = ds or 0.5 * min(grid.dx, grid.dz)
    xyz = np.ascontiguousarray(xyz, dtype=np.float64)
    return transmittance_kernel(
        xyz[:, 0], xyz[:, 1], xyz[:, 2], np.ascontiguousarray(dirs, dtype=np.float64), lam,
        grid.x0, grid.y0, grid.dx, grid.h0, grid.dz,
        grid.dtm.data.astype(np.float64, copy=False), grid.dtm.x0, grid.dtm.y0, grid.dtm.res,
        macro.top, macro.cs, macro.global_top, ds, -np.log(t_min),
        horizon.data.astype(np.float32, copy=False), horizon.x0, horizon.y0, horizon.res,
        horizon.az_step, horizon.data.shape[2] > 1 or horizon.data.max() > -90.0)


def disk_points(x: float, y: float, radius: float, n: int) -> np.ndarray:
    """Vogel spiral: n points evenly covering a disk (equal area per point)."""
    k = np.arange(n)
    r = radius * np.sqrt((k + 0.5) / n)
    th = k * GOLDEN_ANGLE
    return np.column_stack([x + r * np.cos(th), y + r * np.sin(th)])


def target_heights(grid: VoxelGrid, xy: np.ndarray, height: float | None = None) -> np.ndarray:
    """xyz targets at `height` above ground (default: the canopy-layer base h0)."""
    h = grid.h0 if height is None else height
    z = grid.dtm.sample(xy[:, 0], xy[:, 1]) + h + 1e-6
    return np.column_stack([xy, z])


def map_pixels(grid: VoxelGrid, res: float, buffer: float = 0.0) -> Raster:
    """Empty map raster over the grid extent minus `buffer` (values are filled by the caller)."""
    x0 = grid.x0 + buffer
    y0 = grid.y0 + buffer
    nx = int(np.floor((grid.nx * grid.dx - 2 * buffer) / res))
    ny = int(np.floor((grid.ny * grid.dx - 2 * buffer) / res))
    if nx <= 0 or ny <= 0:
        raise ValueError("Map extent is empty: buffer too large for the grid")
    return Raster(np.zeros((ny, nx), np.float32), x0, y0, res, grid.dtm.crs)


def surface_normals(dtm: Raster, x: np.ndarray, y: np.ndarray, smooth_m: float = 1.0) -> np.ndarray:
    """Unit normals of the smoothed DTM at (x, y), shape (n, 3)."""
    z = gaussian_filter(dtm.data, sigma=smooth_m / dtm.res, mode="nearest")
    gy, gx = np.gradient(z, dtm.res)  # row 0 = south, so axis 0 is +north
    sm = Raster(gx, dtm.x0, dtm.y0, dtm.res)
    dzdx = sm.sample(x, y)
    sm.data = gy
    dzdy = sm.sample(x, y)
    n = np.column_stack([-dzdx, -dzdy, np.ones_like(dzdx)])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def slope_aspect(normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Slope [deg] and aspect [deg clockwise from north, direction the slope faces (downslope)]."""
    slope = np.degrees(np.arccos(np.clip(normals[:, 2], -1, 1)))
    aspect = np.degrees(np.arctan2(normals[:, 0], normals[:, 1])) % 360
    return slope, aspect
