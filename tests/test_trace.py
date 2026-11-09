import numpy as np
import pytest

from voxsol.directions import SunBins, sky_directions, slope_weights, unit_vector
from voxsol.grid import Raster, VoxelGrid
from voxsol.trace import Horizon, disk_points, slope_aspect, surface_normals, target_heights, trace

DX, DZ, H0, ZMAX = 0.25, 0.25, 1.0, 20.0


def make_grid(size=60.0, slope_deg=0.0, dtm_res=0.5):
    n = int(size / dtm_res)
    dtm = Raster(np.zeros((n, n)), 0.0, 0.0, dtm_res, "EPSG:25832")
    _, ys = dtm.centers()
    dtm.data = ys * np.tan(np.radians(slope_deg))  # rises to the north: south-facing slope
    grid = VoxelGrid.over_dtm(dtm, DX, H0, DZ, ZMAX)
    grid.lam = grid.empty()
    return grid


def center_target(grid):
    c = grid.nx * grid.dx / 2
    return target_heights(grid, np.array([[c, c]]))


def test_empty_scene_is_fully_open():
    grid = make_grid(size=30)
    dirs, w = sky_directions(400)
    T, edge = trace(grid, center_target(grid), dirs)
    assert np.allclose(T, 1.0)
    assert w.sum() == pytest.approx(1.0)
    assert (T[0] * w).sum() == pytest.approx(1.0)


@pytest.mark.parametrize("zenith", [0.0, 30.0, 60.0])
def test_homogeneous_slab_beer_lambert(zenith):
    grid = make_grid(size=80)
    lam, h_lo, h_hi = 0.2, 2.0, 12.0
    k = grid.layer_heights()
    grid.lam[:, :, (k > h_lo) & (k < h_hi)] = lam
    d = unit_vector([180.0], [90.0 - zenith])
    T, _ = trace(grid, center_target(grid), d)
    expected = np.exp(-lam * (h_hi - h_lo) / np.cos(np.radians(zenith)))
    assert T[0, 0] == pytest.approx(expected, rel=0.03)


def test_opaque_cylinder_shadow_position():
    grid = make_grid(size=40)
    cx = cy = 20.0
    xs, ys = grid.column_centers()
    inside = (xs - cx) ** 2 + (ys - cy) ** 2 < 1.0
    k = grid.layer_heights()
    grid.lam[inside[:, :, None] & ((k > 3) & (k < 8))[None, None, :]] = 50.0
    sun = unit_vector([180.0], [45.0])  # sun in the south: shadow falls to the north
    north = np.array([0.5, 2.5, 4.0, 6.5, 10.0])
    xyz = target_heights(grid, np.column_stack([np.full(north.size, cx), cy + north]))
    T, _ = trace(grid, xyz, sun)
    # ray rises 1 m per metre: crosses the axis at hag = 1 + d, blocked for 3 < 1 + d < 8 (+-1 m radius)
    assert T[0, 0] == pytest.approx(1.0)
    assert T[1, 0] < 1e-3 and T[2, 0] < 1e-3 and T[3, 0] < 1e-3
    assert T[4, 0] == pytest.approx(1.0)


def test_slope_terrain_blocking_and_sky_view():
    beta = 20.0
    grid = make_grid(size=120, slope_deg=beta)
    xyz = center_target(grid)
    uphill_low = unit_vector([0.0, 0.0, 0.0, 180.0], [5.0, 10.0, 30.0, 5.0])
    T, _ = trace(grid, xyz, uphill_low)
    assert list(T[0] > 0.5) == [False, False, True, True]
    normal = surface_normals(grid.dtm, xyz[:, 0], xyz[:, 1])
    assert normal[0] == pytest.approx([0.0, -np.sin(np.radians(beta)), np.cos(np.radians(beta))], abs=1e-6)
    dirs, _ = sky_directions(4000)
    T, _ = trace(grid, xyz, dirs)
    svf_slope = (slope_weights(dirs, normal)[0] * T[0]).sum()
    assert svf_slope == pytest.approx((1 + np.cos(np.radians(beta))) / 2, rel=0.01)


def test_far_horizon_blocks_low_sun():
    grid = make_grid(size=20)
    hor = Horizon(np.full((1, 1, 360), -90.0, np.float32), 0.0, 0.0, 1000.0, 1.0)
    hor.data[0, 0, 90:270] = 10.0
    sun = unit_vector([180.0, 180.0, 0.0], [5.0, 15.0, 5.0])
    T, _ = trace(grid, center_target(grid), sun, horizon=hor)
    assert list(T[0]) == [0.0, 1.0, 1.0]


def test_disk_points_cover_radius():
    pts = disk_points(10.0, 20.0, 2.0, 50)
    r = np.hypot(pts[:, 0] - 10.0, pts[:, 1] - 20.0)
    assert r.max() <= 2.0 and r.max() > 1.9 and abs(pts[:, 0].mean() - 10.0) < 0.1


def test_sun_bins_cover_daylight_only():
    az = np.array([90.0, 90.4, 180.2, 270.0, 0.0])
    el = np.array([10.2, 10.7, 60.1, -5.0, 0.3])
    bins = SunBins(az, el, 1.0)
    assert len(bins) == 3
    assert bins.index[0] == bins.index[1] and bins.index[3] == -1
    assert bins.elevation.min() == pytest.approx(0.5)


@pytest.mark.parametrize("rise_x, rise_y, facing", [(0.0, 0.2, 180.0), (0.0, -0.2, 0.0), (-0.2, 0.0, 90.0),
                                                    (0.2, 0.0, 270.0)])
def test_aspect_is_the_downslope_direction(rise_x, rise_y, facing):
    """A plane rising to the north faces south (180 deg); rising to the west faces east (90 deg)."""
    yy, xx = np.mgrid[0:40, 0:40] * 0.5 + 0.25  # row 0 = south
    dtm = Raster((rise_x * xx + rise_y * yy).astype(np.float32), 0.0, 0.0, 0.5, "EPSG:25832")
    slope, aspect = slope_aspect(surface_normals(dtm, np.array([10.0]), np.array([10.0])))
    assert abs(slope[0] - np.degrees(np.arctan(0.2))) < 0.01
    assert abs((aspect[0] - facing + 180) % 360 - 180) < 0.01
