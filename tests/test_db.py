"""Database export against the real schema (docs/db/schema.sql) in a throwaway PostGIS cluster.

The cluster lives in the pytest temp dir inside the repo (unix socket only, no TCP port) and is
stopped afterwards. The test role `geouser` gets exactly the grants of the schema dump (plus schema
USAGE), so the export is tested with the read-only role's rights. Skipped without PostgreSQL server
binaries or PostGIS.
"""

import glob
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from voxsol import REPO_ROOT

PG_BIN = next(iter(sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)), None)
if shutil.which("initdb"):
    PG_BIN = str(Path(shutil.which("initdb")).parent)
pytestmark = pytest.mark.skipif(PG_BIN is None or not Path(PG_BIN, "initdb").exists(),
                                reason="PostgreSQL server binaries not installed")
psycopg = pytest.importorskip("psycopg")

X0, Y0 = 400_000.0, 5_500_000.0  # EPSG:25832, Rhineland-Palatinate
LAPSE = -0.0065
PERIOD = ("2025-06-01", "2025-06-18")
DEPLOYMENTS = [  # id, serial, locality, quality, start, collect, true_end, intact
    (1, "S1", "SA101_1", "sicher", "2025-06-01", "2025-06-10", "2025-06-10", True),
    (2, "S9", "SA101_1", "sicher", "2025-06-10", "2025-06-20", "2025-06-20", True),  # swap on 06-10
    (3, "S2", "SA101_2", "unsicher", "2025-06-01", "2025-06-20", "2025-06-15", False),
    (4, "S3", "SA102_1", "sicher", "2025-06-01", "2025-06-20", "2025-06-20", True),
    (5, "S4", "SB201_1", "sicher", "2025-06-01", "2025-06-20", "2025-06-20", True),   # other site
    (6, "S5", "XX_UNKNOWN", None, "2025-06-01", "2025-06-20", "2025-06-20", True),   # no plot location
]
MET_ELEV = {1: 400.0, 2: 420.0, 3: 410.0}


def site_lonlat(x=X0, y=Y0):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True).transform(x, y)


def base_t2m(t: pd.DatetimeIndex) -> np.ndarray:
    return 15.0 + 5.0 * np.sin(2 * np.pi * (t.hour - 9) / 24)


def hourly_clearsky_end_label(t0, t1) -> pd.Series:
    from pvlib.location import Location

    lon, lat = site_lonlat()
    minutes = pd.date_range(t0, t1, freq="1min", tz="UTC", inclusive="left")
    cs = Location(lat, lon, altitude=410).get_clearsky(minutes, model="ineichen")["ghi"]
    return cs.resample("1h", label="right", closed="right").mean()  # Open-Meteo: mean of the preceding hour


def seed_sql() -> str:
    lon, lat = site_lonlat(X0 + 50, Y0 + 40)
    deps = ",\n".join(
        f"({i}, '{s}', '{loc}', {f'$${q}$$' if q else 'NULL'}, '{a}', '{c}', '{e}', {str(ok).lower()})"
        for i, s, loc, q, a, c, e, ok in DEPLOYMENTS)
    plon, plat = site_lonlat()
    points = ",\n".join(f"({i}, '{src}', 'p{i}', {plat}, {plon}, 49.5, 7.5, {MET_ELEV[i]})"
                        for i, src in ((1, "open-meteo_era5"), (2, "open-meteo_era5"), (3, "open-meteo_era5_land")))
    return f"""
    GRANT USAGE ON SCHEMA daten, plots, lut, externe_daten TO geoadmin, fsadmin;  -- owners (FK checks)
    INSERT INTO lut.versuchsflaechen VALUES (1, 'site_a', 'Site A', 'SA'), (2, 'site_b', 'Site B', 'SB');
    INSERT INTO lut.behandlungseinheiten VALUES (1, 'CON', 'KON', 'control', 'kontrolle', 'Control', 'Kontrolle'),
                                                (2, 'SAL', 'RAE', 'salvage', 'raeumung', 'Salvage', 'Raeumung');
    INSERT INTO plots.be_flaechen (fl_id, flaeche_ha, versuchsflaeche_id, behandlungseinheit_id)
        VALUES (10, 1, 1, 1), (11, 1, 1, 2), (20, 1, 2, 1);
    INSERT INTO plots.be_plots (pkr_id, pkr_name, fl_id) VALUES (101, 'p101', 10), (102, 'p102', 11), (201, 'p201', 20);
    INSERT INTO plots.be_verjuengungskreise_pt VALUES
        ('SA101_1', 101, true, NULL, ST_GeomFromEWKT('SRID=25832;POINT Z ({X0 + 10} {Y0 + 10} 350.2)')),
        ('SA101_2', 101, true, NULL, ST_GeomFromEWKT('SRID=25832;POINT ({X0 + 30} {Y0 + 15})')),
        ('SA102_1', 102, true, NULL, ST_GeomFromEWKT('SRID=4326;POINT ({lon} {lat})')),
        ('SB201_1', 201, true, NULL, ST_GeomFromEWKT('SRID=25832;POINT ({X0 + 9000} {Y0 + 9000})'));
    INSERT INTO daten.tms_lut_height VALUES (1, '15'), (2, '2'), (3, '-8'), (4, '-0-10cm');
    INSERT INTO daten.tms_lut_sensor_name VALUES (1, 'TMS_T3'), (2, 'TMS_T2'), (3, 'TMS_T1'), (4, 'TMS_moist');
    INSERT INTO daten.tms_deployments (deployment_id, serial_number, locality_id, locality_id_quality, start_date,
                                       collect_date, true_end_date, logger_intact)
        OVERRIDING SYSTEM VALUE VALUES {deps};
    INSERT INTO daten.tms_sensor_end (deployment_id, sensor_name_id, true_end_date, reason)
        VALUES (4, 3, '2025-06-12', 'T1 broken');
    INSERT INTO externe_daten.met_points (met_point_id, source, ext_id, lat, lon, grid_lat, grid_lon, elevation)
        OVERRIDING SYSTEM VALUE VALUES {points};
    INSERT INTO externe_daten.met_point_verjkr VALUES ('SA101_1', 1), ('SA101_2', 1), ('SA102_1', 2), ('SA101_1', 3),
                                                      ('SB201_1', 1);
    GRANT USAGE ON SCHEMA daten, plots, lut TO geouser;  -- schema grants are not part of the dump
    """


def copy_tms(conn) -> None:
    """15-min values for every deployment window (inserted through the deployment trigger)."""
    with conn.cursor() as cur, cur.copy(
            "COPY daten.tms_data (serial_number, height_id, sensor_name_id, datetime, value) FROM STDIN") as cp:
        for dep_id, serial, _, _, start, collect, _, _ in DEPLOYMENTS:
            t0 = pd.Timestamp(start, tz="Europe/Berlin")
            t1 = pd.Timestamp(collect, tz="Europe/Berlin") + pd.Timedelta(days=1)
            for t in pd.date_range(t0, t1, freq="15min", inclusive="left"):
                for sensor in (1, 2, 3, 4):
                    cp.write_row((serial, sensor, sensor, t.to_pydatetime(), 10.0 * dep_id + sensor))


def copy_met(conn) -> None:
    ghi = hourly_clearsky_end_label("2025-05-30", "2025-06-21")
    with conn.cursor() as cur, cur.copy(
            "COPY externe_daten.met_data (met_point_id, variable, datetime, value) FROM STDIN") as cp:
        for pid, elev in MET_ELEV.items():
            t2m = base_t2m(ghi.index) + LAPSE * (elev - 400.0)
            for t, g, tt in zip(ghi.index, ghi.to_numpy(), t2m):
                cp.write_row((pid, "ghi", t.to_pydatetime(), float(g)))
                cp.write_row((pid, "t2m", t.to_pydatetime(), float(tt)))
                cp.write_row((pid, "precip", t.to_pydatetime(), 0.0))


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    base = tmp_path_factory.mktemp("pg")
    data, sock, secrets = base / "data", base / "s", base / "secrets"
    if len(str(sock / ".s.PGSQL.5433")) > 107:
        pytest.skip(f"repository path too long for a PostgreSQL unix socket (max 107 bytes): {sock}")
    sock.mkdir(mode=0o700)  # trust auth: only the user running the tests may reach the socket
    secrets.mkdir()

    def run(*args):
        return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True)

    run(f"{PG_BIN}/initdb", "-D", data, "-U", "owner", "--auth=trust", "-E", "UTF8", "--locale=C")
    with open(data / "postgresql.conf", "a") as fh:
        fh.write(f"listen_addresses = ''\nunix_socket_directories = '{sock}'\nunix_socket_permissions = 0700\n"
                 "port = 5433\nfsync = off\n")
    run(f"{PG_BIN}/pg_ctl", "-D", data, "-l", base / "server.log", "-w", "start")
    try:
        admin = psycopg.connect(host=str(sock), port=5433, dbname="postgres", user="owner", autocommit=True)
        admin.execute("CREATE DATABASE tms")
        for role in ("geoadmin", "fsadmin"):
            admin.execute(f"CREATE ROLE {role}")
        admin.execute("CREATE ROLE geouser LOGIN")
        admin.close()
        conn = psycopg.connect(host=str(sock), port=5433, dbname="tms", user="owner", autocommit=True)
        try:
            conn.execute("CREATE EXTENSION postgis")
        except psycopg.Error as exc:
            pytest.skip(f"PostGIS not available: {exc}")
        # the dump is from pg_dump 17; transaction_timeout does not exist on older servers
        schema = "\n".join(line for line in (REPO_ROOT / "docs/db/schema.sql").read_text().splitlines()
                           if not line.startswith("SET transaction_timeout"))
        (base / "schema.sql").write_text(schema)
        run(f"{PG_BIN}/psql", "-h", sock, "-p", "5433", "-U", "owner", "-d", "tms", "-q", "-v", "ON_ERROR_STOP=1",
            "-f", base / "schema.sql")
        conn.execute("SET search_path = public")
        conn.execute(seed_sql())
        copy_tms(conn)
        copy_met(conn)
        conn.close()
        (secrets / "pg_service.conf").write_text(
            f"[voxsol_test]\nhost={sock}\nport=5433\ndbname=tms\nuser=geouser\n\n"
            f"[voxsol_owner]\nhost={sock}\nport=5433\ndbname=tms\nuser=owner\n")
        yield {"secrets": secrets}
    finally:
        run(f"{PG_BIN}/pg_ctl", "-D", data, "-m", "immediate", "stop")


def site_config(root: Path, **database):
    from voxsol.config import SiteConfig

    raw = {"site": "dbtest", "crs": "EPSG:25832", "inputs": {"laz": ["unused.laz"]},
           "period": {"start": PERIOD[0], "end": PERIOD[1]},
           "prepare": {"logger_xy_sd": 0.05},
           "forcing": {"radiation": "db", "reference": "db", "reference_loggers": ["SA101_2"]},
           "calibration": {"reference_vars": {"T3": "t2m", "T2": "t2m", "T1": "t2m"}},
           "database": {"service": "voxsol_test", "met_source": "open-meteo_era5",
                        "xy_sd_by_quality": {"unsicher": 0.5}, **database}}
    return SiteConfig.model_validate(raw)


@pytest.fixture(scope="module")
def snapshot(pg, tmp_path_factory):
    """Export as the restricted role (no SELECT on the lut name tables, as in the dump)."""
    from voxsol import config as cfgmod
    from voxsol.io import db

    root = tmp_path_factory.mktemp("repo")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "SECRETS", pg["secrets"])
        mp.setattr(cfgmod, "REPO_ROOT", root)
        cfg = site_config(root, versuchsflaeche=1)
        report = db.export_site(cfg)
        yield cfg, report


def test_overview_checks_the_assumptions(pg, monkeypatch):
    from voxsol.io import db

    monkeypatch.setattr(db, "SECRETS", pg["secrets"])
    with db.connect("voxsol_test") as conn:
        ov = db.overview(conn)
    assert ov["relations"] == "all readable" and ov["read_only"] == "on"
    join = ov["locality_id join (expected: all deployments match a verjkr_id)"].iloc[0]
    assert (join["deployments"], join["match_verjkr_id"]) == (6, 5)
    assert ov["heights (calibration.sensors maps these to T3/T2/T1/VWC)"]["height"].tolist() == ["15", "2", "-8", "-0-10cm"]
    assert set(ov["plot geometry (be_verjuengungskreise_pt)"]["srid"]) == {25832, 4326}
    assert set(ov["ERA5 points per source"]["source"]) == {"open-meteo_era5", "open-meteo_era5_land"}
    assert "permission denied" in ov["lut tables"][0]  # geouser cannot read lut.* (as in the dump)


def test_export_snapshot(snapshot):
    cfg, report = snapshot
    dep = report["deployments"]
    assert dep["dropped_no_plot_location"] == ["XX_UNKNOWN"]
    assert dep["deployments_selected"] == 4 and any("names not readable" in n for n in dep["lut_names"])
    assert any("EPSG:4326" in n for n in dep["coordinates"])

    loggers = pd.read_csv(cfg.db_dir / "loggers.csv")
    assert loggers["id"].tolist() == ["SA101_1", "SA101_2", "SA102_1"]  # swap on 06-10 is no overlap
    lg = loggers.set_index("id")
    assert np.allclose(lg.loc["SA102_1", ["x", "y"]].to_numpy(float), [X0 + 50, Y0 + 40], atol=1e-3)
    assert lg.loc["SA101_1", "z"] == 350.2 and np.isnan(lg.loc["SA101_2", "z"])
    assert lg.loc["SA101_2", "xy_sd"] == 0.5 and lg.loc["SA101_1", "xy_sd"] == 0.05
    assert lg.loc["SA101_2", "reference"] and not lg.loc["SA101_1", "reference"]
    assert lg.loc["SA101_1", "serial_numbers"] == "S1;S9"
    assert lg["met_point_id"].tolist() == [1, 1, 2]

    tms = pd.read_parquet(cfg.db_dir / "tms_long.parquet")
    assert tms["timestamp"].dt.tz is not None and not tms.duplicated(["logger_id", "timestamp", "height"]).any()
    assert tms["timestamp"].max() < pd.Timestamp("2025-06-19", tz="UTC")  # period end
    t3 = tms[(tms["logger_id"] == "SA101_1") & (tms["height"] == "15")].set_index("timestamp")["value"]
    assert t3.index.min() == pd.Timestamp("2025-06-01 22:00", tz="UTC")  # installation day 06-01 (CEST) dropped
    assert (t3.index[1:] - t3.index[:-1] == pd.Timedelta("15min")).all()  # continuous across the logger swap
    swap = pd.Timestamp("2025-06-10 22:00", tz="UTC")                     # S9 from 06-11 local (its day 1 dropped)
    assert (t3[t3.index < swap] == 11).all() and (t3[t3.index >= swap] == 21).all()  # 10 * deployment + sensor
    last = tms.groupby(["logger_id", "height"])["timestamp"].max()
    assert last["SA101_2", "15"] == pd.Timestamp("2025-06-15 21:45", tz="UTC")   # logger true_end_date
    assert last["SA102_1", "-8"] == pd.Timestamp("2025-06-12 21:45", tz="UTC")   # sensor end (T1 only)
    assert last["SA102_1", "15"] == pd.Timestamp("2025-06-18 23:45", tz="UTC")
    assert "heights_not_in_calibration_sensors" not in report["tms"]

    met = pd.read_parquet(cfg.db_dir / "met_data.parquet")
    assert set(met["met_point_id"]) == {1, 2} and set(met["variable"]) == {"t2m", "ghi", "precip"}
    assert met["time"].min() == pd.Timestamp("2025-05-31", tz="UTC")  # one day padding for interpolation


def test_site_selection_and_read_only_session(pg, tmp_path, monkeypatch):
    from voxsol import config as cfgmod
    from voxsol.io import db

    monkeypatch.setattr(db, "SECRETS", pg["secrets"])
    monkeypatch.setattr(cfgmod, "REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match="set database.versuchsflaeche"):
        db.export_site(site_config(tmp_path, service="voxsol_owner"))
    cfg = site_config(tmp_path, service="voxsol_owner", versuchsflaeche="site a", behandlungseinheiten=["rae"])
    report = db.export_site(cfg)
    assert report["loggers"]["ids"] == ["SA102_1"] and report["deployments"]["dropped_treatment"] == 3
    assert pd.read_csv(cfg.db_dir / "loggers.csv")["treatment"].tolist() == ["SAL"]
    with db.connect("voxsol_owner") as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("INSERT INTO lut.versuchsflaechen VALUES (9, 'x', 'x', 'x')")


def test_forcing_from_snapshot(snapshot):
    from voxsol import forcing as fz

    cfg, _ = snapshot
    ref = fz.db_reference(cfg.db_dir, site_elevation=410.0, lapse_rate=LAPSE)
    assert np.allclose(ref["t2m"], base_t2m(ref.index) + LAPSE * 10.0)  # both points moved to 410 m
    lon, lat = site_lonlat()
    rad, check = fz.db_radiation(cfg.db_dir, *PERIOD, lat, lon, 410.0, "15min")
    assert check["ghi_label"] == "end", check
    assert rad.attrs["step_s"] == 900 and len(rad) == 18 * 96 and (rad >= 0).all().all()
    hourly = hourly_clearsky_end_label(PERIOD[0], "2025-06-20")
    hourly.index = hourly.index - pd.Timedelta(hours=1)  # interval start
    assert np.allclose(rad["ghi"].resample("1h").mean(), hourly.reindex(rad.resample("1h").mean().index), atol=1e-6)
    from pvlib.solarposition import get_solarposition

    zen = get_solarposition(rad.index + pd.Timedelta(minutes=7.5), lat, lon)["apparent_zenith"].to_numpy()
    assert np.allclose(rad["dhi"] + rad["dni"] * np.cos(np.radians(zen)).clip(0), rad["ghi"], atol=1.0)


def test_logger_table_matches_by_id(snapshot):
    from voxsol.io.loggers import logger_table

    cfg, _ = snapshot
    lt = logger_table(cfg.inputs.tms, cfg.inputs.loggers, 0.05, cfg.forcing.reference_loggers, cfg.inputs.tms_columns)
    assert lt["id"].tolist() == ["SA101_1", "SA101_2", "SA102_1"]
    assert lt["xy_sd"].tolist() == [0.05, 0.5, 0.05] and lt["reference"].tolist() == [False, True, False]
    assert lt.loc[0, "z"] == 350.2 and lt.loc[2, "met_point_id"] == 2


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed")
def test_r_reads_snapshot(snapshot):
    cfg, _ = snapshot
    code = f"""
    suppressPackageStartupMessages(library(data.table))
    source("R/io_voxsol.R"); source("R/loggers.R")
    cfg <- list(inputs = list(tms = "{cfg.inputs.tms}", tms_timezone = "UTC",
                              tms_columns = list(x = "x", y = "y", time = "timestamp", value = "value",
                                                 height = "height", id = "logger_id")),
                calibration = list(sensors = list(T3 = "15", T2 = "2", T1 = "-8", VWC = "-0-10cm")))
    tms <- read_tms_long(cfg)
    tms <- suppressWarnings(attach_logger_ids(tms, data.table(id = c("SA101_1", "SA102_1"))))
    cat(inherits(tms$time, "POSIXct"), attr(tms$time, "tzone"), "\\n")
    print(tms[, .N, keyby = .(logger_id, sensor)])
    """
    out = subprocess.run(["Rscript", "-e", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0].split() == ["TRUE", "UTC"]
    body = " ".join(lines[1:])
    assert "SA101_2" not in body and all(s in body for s in ("T1", "T2", "T3", "VWC", "SA102_1"))
