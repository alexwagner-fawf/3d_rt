"""Orthomosaic indices read block by block vs. a brute-force full-resolution computation."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from voxsol.grid import Raster
from voxsol.rgb import chromatic_indices, ortho_blocks, ortho_brightness, ortho_indices, otsu_threshold

RES, X0, Y1 = 0.01, 500_000.0, 5_500_015.0  # 20 m x 15 m at 1 cm
NX, NY = 2000, 1500


@pytest.fixture(scope="module")
def ortho(tmp_path_factory):
    """RGBA ortho: green patches on brown soil, an invalid (alpha 0) strip at x 8-9 m."""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:NY, 0:NX]
    veg = (np.sin(xx / 70.0) + np.cos(yy / 45.0) + rng.normal(0, 0.3, (NY, NX))) > 0.4
    rgb = np.where(veg, np.array([60, 140, 50])[:, None, None], np.array([150, 120, 90])[:, None, None])
    rgb = np.clip(rgb + rng.normal(0, 12, (3, NY, NX)), 0, 255).astype(np.uint8)
    alpha = np.full((NY, NX), 255, np.uint8)
    alpha[:, 800:900] = 0
    path = tmp_path_factory.mktemp("ortho") / "ortho.tif"
    with rasterio.open(path, "w", driver="GTiff", width=NX, height=NY, count=4, dtype="uint8", crs="EPSG:25832",
                       transform=from_origin(X0, Y1, RES, RES), photometric="RGB", alpha="YES", tiled=True) as dst:
        dst.write(np.concatenate([rgb, alpha[None]]))
    return path, rgb, alpha > 0


@pytest.fixture
def tpl():
    # 0.5 m map that extends 2 m beyond the ortho to the south and east and 1 m to the north
    return Raster(np.zeros((36, 44), np.float32), X0 + 2.0, Y1 - 15.0 - 2.0, 0.5, "EPSG:25832")


def brute_force(rgb, valid, tpl):
    idx = chromatic_indices(*rgb.astype(np.float64))
    idx["green_cover"] = (idx["exg"] > otsu_threshold(idx["exg"][valid])).astype(float)
    xs = X0 + (np.arange(NX) + 0.5) * RES
    ys = Y1 - (np.arange(NY) + 0.5) * RES
    ix, iy = np.floor((xs - tpl.x0) / tpl.res).astype(int), np.floor((ys - tpl.y0) / tpl.res).astype(int)
    ok = valid & ((ix >= 0) & (ix < tpl.nx))[None, :] & ((iy >= 0) & (iy < tpl.ny))[:, None]
    flat = (iy[:, None] * tpl.nx + ix[None, :])[ok]
    n = tpl.nx * tpl.ny
    cnt = np.bincount(flat, minlength=n)
    with np.errstate(invalid="ignore"):
        return {f"rgb_{k}": np.where(cnt > 0, np.bincount(flat, v[ok], n) / cnt, np.nan).reshape(tpl.ny, tpl.nx)
                for k, v in idx.items()}


def test_blocks_match_full_resolution(ortho, tpl):
    path, rgb, valid = ortho
    messages = []
    got = ortho_indices(str(path), tpl, block_px=50_000, log=messages.append)  # native resolution
    ref = brute_force(rgb, valid, tpl)
    assert "in 56 blocks" in messages[0]  # 1500 rows in blocks of 50000 // 1800 cols = 27 rows
    for k in ("rgb_exg", "rgb_gcc", "rgb_gli", "rgb_vari"):
        assert np.array_equal(np.isnan(got[k]), np.isnan(ref[k]))
        assert np.nanmax(np.abs(got[k] - ref[k])) < 1e-6, k
    # threshold from ~20 sampled blocks instead of all pixels
    assert np.nanmean(np.abs(got["rgb_green_cover"] - ref["rgb_green_cover"])) < 0.01
    # outside the ortho and under the alpha strip (x 8-9 m -> map columns 12-13) there is no value
    assert np.isnan(got["rgb_exg"][:4]).all() and np.isnan(got["rgb_exg"][:, -4:]).all()
    assert np.isnan(got["rgb_exg"][4:-2, 12:14]).all() and np.isfinite(got["rgb_exg"][4:-2, :12]).all()


def test_blocks_are_bounded(ortho, tpl):
    path, _, _ = ortho
    sizes = [flat.size for _, flat in ortho_blocks(str(path), tpl, block_px=50_000)]
    assert len(sizes) > 10 and max(sizes) <= 50_000


def test_coarser_read_approximates(ortho, tpl):
    path, rgb, valid = ortho
    ref = brute_force(rgb, valid, tpl)
    got = ortho_indices(str(path), tpl, work_res=0.05, block_px=50_000)
    inner = np.s_[5:-3, 1:11]  # cells away from the masked strip and the ortho border
    diff = got["rgb_exg"][inner] - ref["rgb_exg"][inner]
    # averaged RGB weights bright soil more than dark vegetation: ExG slightly lower, never higher
    assert diff.max() < 0.005 and diff.min() > -0.06
    assert np.corrcoef(got["rgb_exg"][inner].ravel(), ref["rgb_exg"][inner].ravel())[0, 1] > 0.99
    assert np.abs(got["rgb_green_cover"][inner] - ref["rgb_green_cover"][inner]).mean() < 0.1


def test_reprojected_ortho(ortho, tpl, tmp_path):
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    path, _, _ = ortho
    other = tmp_path / "ortho_3035.tif"
    with rasterio.open(path) as src:
        transform, w, h = calculate_default_transform(src.crs, "EPSG:3035", src.width, src.height, *src.bounds,
                                                      resolution=RES)
        profile = src.profile | {"crs": "EPSG:3035", "transform": transform, "width": w, "height": h}
        with rasterio.open(other, "w", **profile) as dst:
            for b in range(1, 5):
                reproject(rasterio.band(src, b), rasterio.band(dst, b), resampling=Resampling.nearest)
    a = ortho_brightness(str(path), tpl)
    b = ortho_brightness(str(other), tpl)
    inner = np.s_[5:-3, 1:11]
    assert np.isfinite(b[inner]).all() and np.abs(a[inner] - b[inner]).mean() < 3.0  # brightness 0-255
