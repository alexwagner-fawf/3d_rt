import numpy as np
import pytest

from voxsol import synthetic as syn
from voxsol.directions import unit_vector
from voxsol.grid import Raster, VoxelGrid
from voxsol.io.trajectory import interpolate_positions
from voxsol.preprocess.pulses import build_pulses
from voxsol.trace import target_heights, trace
from voxsol.voxelize import voxelize

LAM, BOX = 0.5, (14.0, 26.0, 14.0, 26.0, 3.0, 8.0)  # x, y extent (local m) and height above ground


def box_scene(size=40.0):
    n = int(size / 0.25)
    dtm = Raster(np.full((n, n), syn.Z0), syn.X0, syn.Y0, 0.25, syn.CRS)
    truth = VoxelGrid.over_dtm(dtm, 0.25, 0.0, 0.1, 15.0)
    truth.lam = truth.empty()
    xs, ys = truth.column_centers()
    hz = truth.layer_heights()
    x0, x1, y0, y1, h0, h1 = BOX
    inside = ((xs - syn.X0 >= x0) & (xs - syn.X0 < x1) & (ys - syn.Y0 >= y0) & (ys - syn.Y0 < y1))
    truth.lam[inside[:, :, None] & ((hz >= h0) & (hz < h1))[None, None, :]] = LAM
    mat = np.where(truth.lam > 0, syn.GREEN, syn.NONE).astype(np.uint8)
    return syn.Scene(truth, mat, np.zeros((n, n)), np.zeros((n, n)))


def pulses_from_sim(scene, sim):
    traj = sim["trajectory"]
    xyz = sim["xyz"]
    ground = sim["classification"] == 2
    hag = xyz[:, 2] - scene.truth.dtm.sample(xyz[:, 0], xyz[:, 1])
    return build_pulses(xyz, sim["gps_time_sow"], sim["return_number"], ground, hag, 1.0,
                        scanner_at=lambda t: interpolate_positions(traj, t))


@pytest.fixture(scope="module")
def single_particle():
    scene = box_scene()
    sim = syn.simulate_uls(scene, syn.FlightPlan(pulse_rate=40_000.0, line_spacing=10.0), n_sub=1, range_noise=0.0)
    pulses, report = pulses_from_sim(scene, sim)
    grid = VoxelGrid.over_dtm(scene.truth.dtm, 0.5, 1.0, 0.5, 15.0)
    out = voxelize(pulses, grid, pulse_min=20)
    return scene, grid, out, report


def test_pulses_use_trajectory(single_particle):
    _, _, _, report = single_particle
    assert report["mode"] == "trajectory" and report["pulses_without_scanner"] == 0


def test_estimator_unbiased_for_single_particles(single_particle):
    scene, grid, out, _ = single_particle
    xs, ys = grid.column_centers()
    hz = grid.layer_heights()
    x0, x1, y0, y1, h0, h1 = BOX
    core = ((xs - syn.X0 > x0 + 1) & (xs - syn.X0 < x1 - 1) & (ys - syn.Y0 > y0 + 1) & (ys - syn.Y0 < y1 - 1))
    in_box = core[:, :, None] & ((hz > h0 + 0.5) & (hz < h1 - 0.5))[None, None, :]
    sampled = in_box & ~out["filled"]
    assert sampled.sum() > 500
    est = grid.lam[sampled]
    assert np.mean(est) == pytest.approx(LAM, rel=0.05)
    outside = ~(((xs - syn.X0 > x0 - 1) & (xs - syn.X0 < x1 + 1) & (ys - syn.Y0 > y0 - 1)
                 & (ys - syn.Y0 < y1 + 1))[:, :, None]) & ~out["filled"]
    assert grid.lam[outside].max() == 0.0


def test_estimated_grid_reproduces_transmittance(single_particle):
    scene, grid, _, _ = single_particle
    xy = np.array([[syn.X0 + 20.0, syn.Y0 + 20.0]])
    d = unit_vector([180.0, 90.0], [90.0, 60.0])
    T_true, _ = trace(scene.truth, target_heights(scene.truth, xy, 1.0), d)
    T_est, _ = trace(grid, target_heights(grid, xy), d)
    assert T_true[0, 0] == pytest.approx(np.exp(-LAM * 5.0), rel=0.05)
    assert np.allclose(T_est, T_true, atol=0.03)
