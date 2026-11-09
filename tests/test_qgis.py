"""Monthly/period insolation summaries and the portable QGIS export, on the synthetic pipeline run."""

import json
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree

import numpy as np
import pandas as pd
import pytest
import rasterio

from voxsol.grid import Raster
from voxsol.qgis import export_qgis

from test_pipeline_synthetic import site  # noqa: F401  (module fixture: full synthetic pipeline)


def test_insolation_summaries(site):  # noqa: F811
    import xarray as xr

    cfg, _ = site
    ins = cfg.workdir / "insolation"
    assert (ins / "sw_tou_h_period.tif").exists() and (ins / "sw_tou_h_2025-06.tif").exists()
    with xr.open_dataset(cfg.workdir / "maps_daily.nc") as ds:
        tou = (ds["sw_dir_h"] + ds["sw_dif_h"]).to_numpy()  # (days, y north-up, x)
    got = np.flipud(Raster.read(ins / "sw_tou_h_period.tif").data)
    assert np.allclose(got, tou.mean(0), rtol=1e-5, atol=1e-5)
    site_daily = pd.read_csv(cfg.workdir / "site_daily.csv")
    trans = np.flipud(Raster.read(ins / "trans_h_period.tif").data)
    assert np.allclose(trans, tou.sum(0) / site_daily["ghi"].sum(), rtol=1e-5)
    assert 0 < np.nanmin(trans) and np.nanmax(trans) <= 1.02
    with rasterio.open(ins / "sw_tou_h_period.tif") as r:
        assert r.units[0] == "MJ m-2 d-1" and "top of the understory" in r.descriptions[0]
        assert r.tags()["days"] == str(tou.shape[0])


@pytest.fixture(scope="module")
def export(site):  # noqa: F811
    cfg, _ = site
    return cfg, export_qgis(cfg, make_zip=True, log=lambda *_: None)


def test_export_is_self_contained(export, tmp_path):
    cfg, report = export
    with zipfile.ZipFile(report["zip"]) as zf:
        zf.extractall(tmp_path)
    folder = tmp_path / f"{cfg.site}_qgis"
    project = folder / f"{cfg.site}.qgs"
    root = ElementTree.parse(project).getroot()
    sources = [ml.findtext("datasource") for ml in root.iter("maplayer")]
    assert len(sources) == sum(report["layers"].values()) > 30
    assert all(s.startswith("./") and (folder / s).exists() for s in sources)  # relative, nothing missing
    assert root.find("projectCrs/spatialrefsys/authid").text == "EPSG:25832"
    names = [ml.findtext("layername") for ml in root.iter("maplayer")]
    assert {"Loggers", "sw_tou_h period", "trans_h period", "Hillshade", "Sky view, horizontal [0-1]"} <= set(names)
    visible = {n.get("name") for n in root.iter("layer-tree-layer") if n.get("checked") == "Qt::Checked"}
    assert visible == {"Loggers", "sw_tou_h period", "Hillshade"}
    loggers = json.loads((folder / "loggers.geojson").read_text())
    assert len(loggers["features"]) == len(pd.read_csv(cfg.workdir / "loggers.csv"))
    props = loggers["features"][0]["properties"]
    assert {"id", "svf_h_point", "sw_tou_h_mean_MJ", "trans_h_mean"} <= set(props)
    assert (folder / "OUTPUTS.md").exists() and (folder / "README.txt").exists()


def _pyqgis():
    py = shutil.which("python3", path="/usr/bin:/bin")
    if py is None:
        return None
    ok = subprocess.run([py, "-c", "import qgis.core"], capture_output=True, env={"PATH": "/usr/bin:/bin"})
    return py if ok.returncode == 0 else None


@pytest.mark.skipif(_pyqgis() is None, reason="QGIS (PyQGIS) not installed")
def test_project_loads_in_qgis(export, tmp_path):
    cfg, report = export
    home = tmp_path / "home"  # QGIS writes its profile under HOME/XDG: keep it in the test folder
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_DATA_HOME": str(home / "data"), "XDG_CACHE_HOME": str(home / "cache"),
           "XDG_RUNTIME_DIR": str(home / "run"), "TMPDIR": str(tmp_path), "QT_QPA_PLATFORM": "offscreen"}
    (home / "run").mkdir(parents=True, mode=0o700)
    code = """
import sys
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False); app.initQgis()
prj = QgsProject.instance()
assert prj.read(sys.argv[1])
bad = [l.name() for l in prj.mapLayers().values() if not l.isValid()]
print(prj.crs().authid(), len(prj.mapLayers()), "BAD:" + ";".join(bad))
app.exitQgis()
"""
    out = subprocess.run([_pyqgis(), "-c", code, report["project"]], env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    crs, n, bad = out.stdout.strip().splitlines()[-1].split(" ", 2)
    assert crs == "EPSG:25832" and int(n) == sum(report["layers"].values()) and bad == "BAD:", out.stdout
