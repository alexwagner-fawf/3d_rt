"""Trajectory height datum: an exact geoid conversion or a clear stop (no silent ~50 m error)."""

import subprocess
import sys

import pandas as pd
import pytest

from voxsol import REPO_ROOT
from voxsol.io.trajectory import PROJ_GRID_DIR, to_projected

GRID = PROJ_GRID_DIR / "de_bkg_gcg2016.tif"
TRAJ = pd.DataFrame({"time": [0.0], "lat": [49.64], "lon": [7.62], "h_ell": [500.0]})


def test_missing_geoid_grid_stops_with_download_command(tmp_path):
    # fresh interpreter: PROJ search paths are process-global once a grid directory was registered
    code = f"""
import pandas as pd
import voxsol.io.trajectory as tr
tr.PROJ_GRID_DIR = __import__("pathlib").Path({str(tmp_path)!r})
traj = pd.DataFrame({{"time": [0.0], "lat": [49.64], "lon": [7.62], "h_ell": [500.0]}})
print(tr.to_projected(traj, "EPSG:25832", "EPSG:7837", strict=False)[1]["best_available"])
try:
    tr.to_projected(traj, "EPSG:25832", "EPSG:7837")
except RuntimeError as exc:
    print(exc)
"""
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=REPO_ROOT, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "False"  # inspect: reported, not raised
    assert "de_bkg_gcg2016.tif" in out.stdout and "curl -fL" in out.stdout


def test_no_vertical_crs_keeps_ellipsoidal_heights():
    xyz, report = to_projected(TRAJ, "EPSG:25832")
    assert report["heights"] == "ellipsoidal" and xyz["z"].iloc[0] == 500.0


@pytest.mark.skipif(not GRID.exists(), reason="geoid grid not downloaded into data/external/proj/")
def test_geoid_grid_from_repo_folder_is_used():
    xyz, report = to_projected(TRAJ, "EPSG:25832", "EPSG:7837")
    assert report["best_available"]
    assert 45.0 < 500.0 - xyz["z"].iloc[0] < 52.0  # GCG2016 undulation in Rhineland-Palatinate
