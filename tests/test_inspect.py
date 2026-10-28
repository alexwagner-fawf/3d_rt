import numpy as np
import pandas as pd
import pytest

from voxsol import synthetic as syn
from voxsol.config import SiteConfig
from voxsol.inspect import run_inspect
from voxsol.io.las import pulse_grouping_stats
from voxsol.io.trajectory import read_trajectory


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    d = tmp_path_factory.mktemp("site")
    scene = syn.make_scene(size=40.0, n_trees=2, n_snags=1, n_saplings=3)
    plan = syn.FlightPlan(pulse_rate=4000.0)
    sim = syn.simulate_uls(scene, plan)
    syn.write_las(d / "cloud.laz", sim)
    syn.write_sbet(d / "sbet.out", sim["trajectory"])
    pd.DataFrame({"id": ["L1", "L2"], "x": [syn.X0 + 10, syn.X0 + 999], "y": [syn.Y0 + 10] * 2,
                  "xy_sd": [0.03, None]}).to_csv(d / "loggers.csv", index=False)
    return d, plan, sim


def test_sbet_roundtrip(site):
    d, plan, sim = site
    traj = read_trajectory(d / "sbet.out")
    assert traj["time"].iloc[0] == pytest.approx(plan.t0_sow - 1.0)
    assert 49 < traj["lat"].mean() < 52 and 9 < traj["lon"].mean() < 11


def test_pulse_grouping_detects_shared_times():
    t = np.array([1.0, 1.0, 2.0, 3.0, 3.0, 3.0])
    rn = np.array([1, 2, 1, 1, 2, 3])
    nr = np.array([2, 2, 1, 3, 3, 3])
    stats = pulse_grouping_stats(t, rn, nr)
    assert stats["n_time_groups"] == 3
    assert stats["fraction_points_in_consistent_groups"] == 1.0
    # a missing return breaks consistency of that pulse only
    stats = pulse_grouping_stats(t[:-1], rn[:-1], nr[:-1])
    assert stats["fraction_points_in_consistent_groups"] == pytest.approx(3 / 5)


def test_run_inspect(site, tmp_path, monkeypatch):
    d, plan, _ = site
    monkeypatch.setattr("voxsol.config.REPO_ROOT", tmp_path)
    cfg = SiteConfig.model_validate({
        "site": "synthetic", "crs": syn.CRS,
        "inputs": {"laz": str(d / "*.laz"), "trajectory": str(d / "sbet.out"), "loggers": str(d / "loggers.csv")},
    })
    report = run_inspect(cfg, sample_n=10_000_000)
    f = report["files"][0]
    assert f["header"]["adjusted_gps_time"]
    assert f["pulses"]["fraction_points_in_consistent_groups"] == 1.0
    assert f["rgb_bits"] == 16
    traj = report["trajectory"]
    assert traj["time_base"] == "GPS seconds of week"
    assert traj["sample_time_overlap"] == 1.0
    assert traj["projection"]["heights"] == "ellipsoidal"
    # flight at mean terrain + 80 m over a sloped scene with crowns
    assert 60 < traj["nadir_scanner_height_above_points_m"]["p50"] < 90
    assert report["loggers"]["outside_ids"] == ["L2"]
    assert (tmp_path / "work" / "synthetic" / "inspect.json").exists()
