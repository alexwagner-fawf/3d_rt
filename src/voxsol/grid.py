"""Rasters and the terrain-following voxel grid.

Internal array convention: row 0 is the *southern* edge (y increases with the row index), cell
centers at x0 + (i + 0.5) * res. GeoTIFFs are written north-up (rows flipped on IO).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


@dataclass
class Raster:
    data: np.ndarray  # (ny, nx), row 0 = south
    x0: float
    y0: float
    res: float
    crs: str | None = None

    @property
    def ny(self) -> int:
        return self.data.shape[0]

    @property
    def nx(self) -> int:
        return self.data.shape[1]

    @property
    def x1(self) -> float:
        return self.x0 + self.nx * self.res

    @property
    def y1(self) -> float:
        return self.y0 + self.ny * self.res

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.res
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.res
        return np.meshgrid(xs, ys)

    def sample(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Bilinear interpolation (clamped at the edges)."""
        from voxsol.raymarch import bilinear_many

        return bilinear_many(self.data.astype(np.float64), self.x0, self.y0, self.res,
                             np.asarray(x, np.float64), np.asarray(y, np.float64))

    def write(self, path: str | Path, nodata: float | None = np.nan, **tags) -> None:
        data = np.flipud(self.data)
        profile = dict(driver="GTiff", height=self.ny, width=self.nx, count=1, dtype=data.dtype,
                       transform=from_origin(self.x0, self.y1, self.res, self.res), crs=self.crs,
                       compress="deflate", tiled=True)
        if nodata is not None and np.issubdtype(data.dtype, np.floating):
            profile["nodata"] = nodata
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)
            if tags:
                dst.update_tags(**{k: str(v) for k, v in tags.items()})

    @classmethod
    def read(cls, path: str | Path, band: int = 1) -> "Raster":
        with rasterio.open(path) as src:
            t = src.transform
            if abs(abs(t.a) - abs(t.e)) > 1e-9 or t.b or t.d:
                raise ValueError(f"{path}: only square, unrotated pixels are supported")
            data = src.read(band).astype(np.float64)
            if src.nodata is not None:
                data[data == src.nodata] = np.nan
            crs = src.crs.to_string() if src.crs else None
            return cls(np.flipud(data), t.c, t.f - src.height * abs(t.e), abs(t.a), crs)


@dataclass
class VoxelGrid:
    """Terrain-following grid: voxel (iy, ix, k) spans height above ground [h0 + k dz, h0 + (k+1) dz)."""

    x0: float
    y0: float
    dx: float
    nx: int
    ny: int
    h0: float
    dz: float
    nz: int
    dtm: Raster
    lam: np.ndarray | None = None  # (ny, nx, nz) float32 extinction coefficient [1/m]

    @classmethod
    def over_dtm(cls, dtm: Raster, dx: float, h0: float, dz: float, zmax: float) -> "VoxelGrid":
        nx = int(np.floor((dtm.x1 - dtm.x0) / dx))
        ny = int(np.floor((dtm.y1 - dtm.y0) / dx))
        nz = int(np.ceil((zmax - h0) / dz))
        return cls(dtm.x0, dtm.y0, dx, nx, ny, h0, dz, nz, dtm)

    @property
    def htop(self) -> float:
        return self.h0 + self.nz * self.dz

    def empty(self, dtype=np.float32) -> np.ndarray:
        return np.zeros((self.ny, self.nx, self.nz), dtype=dtype)

    def column_centers(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.dx
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.dx
        return np.meshgrid(xs, ys)

    def layer_heights(self) -> np.ndarray:
        return self.h0 + (np.arange(self.nz) + 0.5) * self.dz

    def save(self, path: str | Path) -> None:
        np.savez(path, lam=self.lam, meta=np.array([self.x0, self.y0, self.dx, self.nx, self.ny,
                                                    self.h0, self.dz, self.nz]))

    @classmethod
    def load(cls, path: str | Path, dtm: Raster) -> "VoxelGrid":
        f = np.load(path)
        x0, y0, dx, nx, ny, h0, dz, nz = f["meta"]
        return cls(x0, y0, dx, int(nx), int(ny), h0, dz, int(nz), dtm, f["lam"])
