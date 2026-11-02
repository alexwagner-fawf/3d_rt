"""PostgreSQL source: TMS-4 data, plot locations and the ERA5 reference (schema: docs/db/schema.sql).

`voxsol db` reads the database in a read-only session and writes a snapshot to work/<site>/db/ in the
formats the rest of the pipeline reads (`inputs.tms` / `inputs.loggers` default to these files):

  tms_long.parquet  logger_id, x, y, timestamp (UTC), value, height, sensor_name, serial_number, deployment_id
  loggers.csv       id, x, y, z, xy_sd, reference + plot, treatment, site and ERA5 point of each logger
  met_points.csv    ERA5 points (externe_daten.met_points) linked to the loggers' regeneration circles
  met_data.parquet  met_point_id, variable (t2m, ghi, precip), time (UTC), value
  db_export.json    provenance and checks (dropped deployments, heights, sensors, SRIDs, coverage)

Data model (link confirmed by the data owner; `voxsol db --check` counts the matches):
tms_deployments.locality_id is the verjkr_id of plots.be_verjuengungskreise_pt, whose point geometry
is the logger position; valid data come from the
view daten.tms_data_valid (logger and sensor end dates); the installation day(s) are dropped. A logger
id is the locality id; deployments that overlap in time at one locality get `<locality>_<serial>`.
Connection: libpq service from secrets/pg_service.conf, password in secrets/pgpass (scripts/db.sh).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from voxsol import REPO_ROOT

LOCAL_TZ = "Europe/Berlin"  # tms_deployments / tms_sensor_end dates are local days
SECRETS = REPO_ROOT / "secrets"
CHUNK = 200_000
SITE_COLS = ["versuchsflaeche_id", "versuchsflaeche_name_short", "versuchsflaeche_name_safe",
             "versuchsflaeche_name_clean"]
RELATIONS = ["daten.tms_data_valid", "daten.tms_deployments", "daten.tms_lut_height", "daten.tms_lut_sensor_name",
             "plots.be_verjuengungskreise_pt", "plots.be_plots", "plots.be_flaechen", "externe_daten.met_points",
             "externe_daten.met_point_verjkr", "externe_daten.met_data"]
# Names only; the schema dump grants geouser no SELECT on these, then ids are used instead.
SITE_NAMES_SQL = """
SELECT versuchsflaeche_id, versuchsflaeche_name_short, versuchsflaeche_name_safe, versuchsflaeche_name_clean
FROM lut.versuchsflaechen"""
TREATMENT_NAMES_SQL = """
SELECT behandlungseinheit_id, be_name_short_eng AS treatment, be_name_short_de AS treatment_de
FROM lut.behandlungseinheiten"""

DEPLOYMENTS_SQL = """
SELECT d.deployment_id, d.serial_number, d.locality_id, d.locality_id_quality, d.start_date, d.collect_date,
       d.true_end_date, d.logger_intact, d.logger_error,
       v.verjkr_id, v.pkr_id, v.aktiv,
       ST_X(v.geometry) AS gx, ST_Y(v.geometry) AS gy, ST_Z(v.geometry) AS gz, ST_SRID(v.geometry) AS srid,
       fl.fl_id, fl.versuchsflaeche_id, fl.behandlungseinheit_id
FROM daten.tms_deployments d
LEFT JOIN plots.be_verjuengungskreise_pt v ON v.verjkr_id = d.locality_id
LEFT JOIN plots.be_plots p ON p.pkr_id = v.pkr_id
LEFT JOIN plots.be_flaechen fl ON fl.fl_id = p.fl_id
WHERE d.start_date <= %(end)s AND COALESCE(d.true_end_date, d.collect_date) >= %(start)s
ORDER BY d.locality_id, d.start_date
"""

TMS_SQL = """
SELECT t.deployment_id, EXTRACT(EPOCH FROM t.datetime)::float8 AS epoch, h.height, s.sensor_name, t.value
FROM daten.tms_data_valid t
LEFT JOIN daten.tms_lut_height h ON h.height_id = t.height_id
LEFT JOIN daten.tms_lut_sensor_name s ON s.sensor_name_id = t.sensor_name_id
WHERE t.deployment_id = ANY(%(ids)s) AND t.datetime >= %(t0)s AND t.datetime < %(t1)s
"""

MET_POINTS_SQL = """
SELECT mv.verjkr_id, mp.met_point_id, mp.source, mp.ext_id, mp.lat, mp.lon, mp.grid_lat, mp.grid_lon, mp.elevation
FROM externe_daten.met_point_verjkr mv
JOIN externe_daten.met_points mp ON mp.met_point_id = mv.met_point_id
WHERE mv.verjkr_id = ANY(%(ids)s)
"""

MET_DATA_SQL = """
SELECT met_point_id, variable, EXTRACT(EPOCH FROM datetime)::float8 AS epoch, value
FROM externe_daten.met_data
WHERE met_point_id = ANY(%(ids)s) AND datetime >= %(t0)s AND datetime < %(t1)s
"""

OVERVIEW_SQL = {
    "sites (versuchsflaechen with TMS deployments)": """
        SELECT fl.versuchsflaeche_id, count(DISTINCT d.deployment_id) AS deployments,
               count(DISTINCT d.locality_id) AS localities, min(d.start_date) AS first_start,
               max(COALESCE(d.true_end_date, d.collect_date)) AS last_end
        FROM daten.tms_deployments d
        JOIN plots.be_verjuengungskreise_pt v ON v.verjkr_id = d.locality_id
        JOIN plots.be_plots p ON p.pkr_id = v.pkr_id
        JOIN plots.be_flaechen fl ON fl.fl_id = p.fl_id
        GROUP BY 1 ORDER BY 1""",
    "locality_id join (expected: all deployments match a verjkr_id)": """
        SELECT count(*) AS deployments,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM plots.be_verjuengungskreise_pt v
                                              WHERE v.verjkr_id = d.locality_id)) AS match_verjkr_id,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM plots.be_plots p
                                              WHERE p.pkr_id::text = d.locality_id)) AS match_pkr_id,
               count(*) FILTER (WHERE d.locality_id IS NULL) AS no_locality
        FROM daten.tms_deployments d""",
    "locality_id_quality": """
        SELECT COALESCE(locality_id_quality, '(null)') AS quality, count(*) AS deployments
        FROM daten.tms_deployments GROUP BY 1 ORDER BY 2 DESC""",
    "heights (calibration.sensors maps these to T3/T2/T1/VWC)":
        "SELECT height_id, height FROM daten.tms_lut_height ORDER BY 1",
    "sensor names": "SELECT sensor_name_id, sensor_name FROM daten.tms_lut_sensor_name ORDER BY 1",
    "plot geometry (be_verjuengungskreise_pt)": """
        SELECT ST_SRID(geometry) AS srid, GeometryType(geometry) AS type, ST_NDims(geometry) AS dims,
               count(*) AS n
        FROM plots.be_verjuengungskreise_pt GROUP BY 1, 2, 3 ORDER BY 4 DESC""",
    "ERA5 points per source": """
        SELECT mp.source, count(DISTINCT mp.met_point_id) AS points, count(DISTINCT mv.verjkr_id) AS circles,
               min(mp.elevation) AS elev_min, max(mp.elevation) AS elev_max
        FROM externe_daten.met_points mp
        LEFT JOIN externe_daten.met_point_verjkr mv ON mv.met_point_id = mp.met_point_id
        GROUP BY 1 ORDER BY 1""",
    "ERA5 series": """
        SELECT mp.source, md.variable, min(md.datetime) AS first, max(md.datetime) AS last, count(*) AS n
        FROM externe_daten.met_data md JOIN externe_daten.met_points mp ON mp.met_point_id = md.met_point_id
        GROUP BY 1, 2 ORDER BY 1, 2""",
}


def apply_pg_env() -> dict:
    """PGSERVICEFILE / PGPASSFILE from secrets/ (as in env.sh and .Rprofile; the repo files win)."""
    applied = {}
    for key, name in (("PGSERVICEFILE", "pg_service.conf"), ("PGPASSFILE", "pgpass")):
        path = SECRETS / name
        if path.exists():
            os.environ[key] = applied[key] = str(path)
    return applied


def connect(service: str):
    """Connection via a libpq service. Every transaction is READ ONLY, so voxsol can never write."""
    import psycopg

    apply_pg_env()
    conn = psycopg.connect(service=service, application_name="voxsol", connect_timeout=20, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.autocommit = False
    conn.read_only = True
    return conn


def _frame(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def _optional_frame(conn, sql: str) -> tuple[pd.DataFrame | None, str | None]:
    """Query that may fail for lack of privileges: (None, reason) instead of an error."""
    import psycopg

    try:
        return _frame(conn, sql), None
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable) as exc:
        conn.rollback()
        return None, exc.diag.message_primary


def _with_names(conn, dep: pd.DataFrame, treatments: bool = True) -> tuple[pd.DataFrame, list[str]]:
    """Add site (and treatment) names from the lut tables where readable."""
    notes = []
    lookups = [(SITE_NAMES_SQL, "versuchsflaeche_id", SITE_COLS[1:])]
    if treatments:
        lookups.append((TREATMENT_NAMES_SQL, "behandlungseinheit_id", ["treatment", "treatment_de"]))
    for sql, key, cols in lookups:
        names, why = _optional_frame(conn, sql)
        if names is None:
            notes.append(f"names not readable ({why}): use numeric ids")
            dep = dep.assign(**{c: None for c in cols})
        else:
            dep = dep.merge(names, on=key, how="left")
    return dep, notes


def _utc(epoch: pd.Series) -> pd.Series:
    return pd.to_datetime(np.round(epoch.to_numpy(float) * 1e3).astype("int64"), unit="ms", utc=True)


def _local_midnight_utc(dates: pd.Series) -> pd.Series:
    return pd.to_datetime(dates).dt.tz_localize(LOCAL_TZ).dt.tz_convert("UTC")


def overview(conn) -> dict:
    """What `voxsol db --check` prints: access to the relations and the facts the export relies on."""
    import psycopg

    out: dict = {"server": conn.execute("SELECT version()").fetchone()[0].split(",")[0],
                 "user": conn.execute("SELECT current_user").fetchone()[0],
                 "read_only": conn.execute("SHOW transaction_read_only").fetchone()[0]}
    problems = []
    for rel in RELATIONS:
        try:
            conn.execute(f"SELECT 1 FROM {rel} LIMIT 1")
        except psycopg.Error as exc:
            conn.rollback()
            problems.append(f"{rel}: {exc.diag.message_primary}")
    out["relations"] = problems or "all readable"
    if not problems:
        for title, sql in OVERVIEW_SQL.items():
            out[title] = _frame(conn, sql)
        sites_key = next(iter(OVERVIEW_SQL))
        sites, notes = _with_names(conn, out[sites_key], treatments=False)
        out[sites_key] = sites.drop(columns="versuchsflaeche_name_safe")
        if notes:
            out["lut tables"] = notes
    conn.rollback()
    return out


# ------------------------------------------------------------------------------------ export

def _norm(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        value = int(value)
    return str(value).strip().lower()


def _site_list(dep: pd.DataFrame) -> str:
    sites = dep.dropna(subset=["versuchsflaeche_id"]).drop_duplicates("versuchsflaeche_id")
    return "; ".join(f"{int(r.versuchsflaeche_id)}" + (f" = {r.versuchsflaeche_name_short} ({r.versuchsflaeche_name_clean})"
                                                       if isinstance(r.versuchsflaeche_name_short, str) else "")
                     for r in sites.itertuples()) or "none"


def select_site(dep: pd.DataFrame, want) -> np.ndarray:
    """Rows of the requested versuchsflaeche (id or short/safe/clean name, case-insensitive)."""
    if want is None:
        ids = dep["versuchsflaeche_id"].dropna().unique()
        if len(ids) != 1:
            raise ValueError("set database.versuchsflaeche; deployments in the period belong to: " + _site_list(dep))
        want = ids[0]
    key = _norm(want)
    hit = np.column_stack([dep[c].map(_norm).eq(key).to_numpy() for c in SITE_COLS]).any(axis=1)
    if not hit.any():
        raise ValueError(f"versuchsflaeche {want!r} has no deployments in the period; available: {_site_list(dep)}")
    return hit


def assign_logger_ids(dep: pd.DataFrame) -> pd.Series:
    """Locality id, or `<locality>_<serial>` where deployments at one locality overlap in time."""
    ids = dep["locality_id"].astype(str).copy()
    for loc, g in dep.groupby("locality_id"):
        g = g.sort_values("first_valid")
        last_before = g["last_valid"].cummax().shift()
        if (g["first_valid"] < last_before).any():  # last_valid is exclusive (midnight after the last day)
            ids[g.index] = loc + "_" + g["serial_number"].astype(str)
    return ids


def to_site_crs(dep: pd.DataFrame, crs: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    from pyproj import CRS, Transformer

    x, y = dep["gx"].to_numpy(float).copy(), dep["gy"].to_numpy(float).copy()
    notes = []
    target = CRS.from_user_input(crs)
    srid = dep["srid"].fillna(0).astype(int).to_numpy()
    for code in np.unique(srid):
        sel = srid == code
        if code == 0:
            notes.append(f"{sel.sum()} plot geometries without SRID: taken as {crs}")
        elif CRS.from_epsg(int(code)) != target:
            tr = Transformer.from_crs(CRS.from_epsg(int(code)), target, always_xy=True)
            x[sel], y[sel] = tr.transform(x[sel], y[sel])
            notes.append(f"{sel.sum()} plot geometries transformed from EPSG:{code} to {crs}")
    return x, y, notes


def select_deployments(dep: pd.DataFrame, cfg) -> tuple[pd.DataFrame, dict]:
    """Site/treatment/quality filters, positions in the site CRS, data windows and logger ids."""
    db = cfg.database
    report: dict = {"deployments_in_period": int(len(dep)), "sites_in_period": _site_list(dep)}
    unmatched = dep["verjkr_id"].isna()
    report["dropped_no_plot_location"] = sorted(dep.loc[unmatched, "locality_id"].astype(str).unique())
    dep = dep[~unmatched]
    dep = dep[select_site(dep, db.versuchsflaeche)]
    report["site"] = _site_list(dep)
    if db.behandlungseinheiten:
        keys = {k.lower() for k in db.behandlungseinheiten}
        keep = dep["treatment"].str.lower().isin(keys) | dep["treatment_de"].str.lower().isin(keys)
        report["dropped_treatment"] = int((~keep).sum())
        dep = dep[keep]
    if db.exclude_locality_quality:
        bad = dep["locality_id_quality"].isin(db.exclude_locality_quality)
        report["dropped_locality_quality"] = dep.loc[bad, "locality_id"].astype(str).tolist()
        dep = dep[~bad]
    no_geom = dep["gx"].isna() | dep["gy"].isna()
    report["dropped_no_geometry"] = dep.loc[no_geom, "locality_id"].astype(str).tolist()
    dep = dep[~no_geom].copy()
    if dep.empty:
        raise ValueError(f"no deployments left after the filters: {json.dumps(report, default=str)}")
    dep["x"], dep["y"], report["coordinates"] = to_site_crs(dep, cfg.crs)
    dep["z"] = dep["gz"].astype(float)
    dep["first_valid"] = _local_midnight_utc(dep["start_date"]) + pd.Timedelta(days=db.skip_days_after_start)
    dep["last_valid"] = _local_midnight_utc(dep["true_end_date"].fillna(dep["collect_date"])) + pd.Timedelta(days=1)
    dep["logger_id"] = assign_logger_ids(dep)
    report["deployments_selected"] = int(len(dep))
    return dep.reset_index(drop=True), report


def logger_rows(dep: pd.DataFrame, cfg) -> pd.DataFrame:
    """One row per logger id: position, uncertainty and descriptive metadata."""
    db = cfg.database
    sd = dep["locality_id_quality"].map(db.xy_sd_by_quality).astype(float).fillna(cfg.prepare.logger_xy_sd)

    def joined(s):
        return ";".join(sorted({str(v) for v in s.dropna()}))

    g = dep.assign(xy_sd=sd).groupby("logger_id", sort=True)
    out = g.agg(x=("x", "first"), y=("y", "first"), z=("z", "first"), xy_sd=("xy_sd", "max"),
                locality_id=("locality_id", "first"), locality_id_quality=("locality_id_quality", joined),
                pkr_id=("pkr_id", "first"), fl_id=("fl_id", "first"), treatment=("treatment", "first"),
                versuchsflaeche=("versuchsflaeche_name_short", "first"), aktiv=("aktiv", "first"),
                serial_numbers=("serial_number", joined), deployment_ids=("deployment_id", joined),
                logger_errors=("logger_error", joined)).reset_index().rename(columns={"logger_id": "id"})
    out.insert(5, "reference", out["id"].isin(cfg.forcing.reference_loggers))
    return out


def fetch_tms(conn, dep: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    parts = []
    with conn.cursor(name="voxsol_tms") as cur:
        cur.itersize = CHUNK
        cur.execute(TMS_SQL, {"ids": dep["deployment_id"].astype(int).tolist(),
                              "t0": t0.to_pydatetime(), "t1": t1.to_pydatetime()})
        while rows := cur.fetchmany(CHUNK):
            parts.append(pd.DataFrame(rows, columns=["deployment_id", "epoch", "height", "sensor_name", "value"]))
    cols = ["deployment_id", "epoch", "height", "sensor_name", "value"]
    tms = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    d = dep.set_index("deployment_id")
    first = (d["first_valid"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    early = tms["epoch"].to_numpy(float) < tms["deployment_id"].map(first).to_numpy(float)
    report = {"rows_installation_days_dropped": int(early.sum())}
    tms = tms[~early]
    out = pd.DataFrame({
        "logger_id": tms["deployment_id"].map(d["logger_id"]).to_numpy(),
        "x": tms["deployment_id"].map(d["x"]).to_numpy(float),
        "y": tms["deployment_id"].map(d["y"]).to_numpy(float),
        "timestamp": _utc(tms["epoch"]).astype("datetime64[us, UTC]"),
        "value": tms["value"].astype(float).to_numpy(),
        "height": tms["height"].fillna("").astype(str).str.strip().to_numpy(),
        "sensor_name": tms["sensor_name"].fillna("").astype(str).to_numpy(),
        "serial_number": tms["deployment_id"].map(d["serial_number"]).astype(str).to_numpy(),
        "deployment_id": tms["deployment_id"].astype("int64").to_numpy(),
    })
    dup = out.duplicated(["logger_id", "timestamp", "height"])
    report["rows_duplicate_dropped"] = int(dup.sum())
    out = out[~dup].sort_values(["logger_id", "height", "timestamp"]).reset_index(drop=True)
    report["rows"] = int(len(out))
    if len(out):
        report["time_range_utc"] = [str(out["timestamp"].min()), str(out["timestamp"].max())]
        report["values_per_height"] = (out.groupby(["height", "sensor_name"]).size().rename("n").reset_index()
                                       .to_dict(orient="records"))
    return out, report


def fetch_met(conn, loggers: pd.DataFrame, source: str | None, t0, t1) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    links = _frame(conn, MET_POINTS_SQL, {"ids": loggers["locality_id"].astype(str).unique().tolist()})
    if links.empty:
        loggers["met_point_id"] = pd.NA
        return (pd.DataFrame(columns=[*links.columns.drop("verjkr_id"), "verjkr_ids"]),
                pd.DataFrame(columns=["met_point_id", "variable", "time", "value"]),
                {"note": "no ERA5 points linked to the selected loggers"})
    sources = sorted(links["source"].unique())
    if source is None:
        if len(sources) > 1:
            raise ValueError(f"set database.met_source; the loggers link to ERA5 points of: {sources}")
        source = sources[0]
    elif source not in sources:
        raise ValueError(f"met_source {source!r} not linked to the selected loggers; available: {sources}")
    links = links[links["source"] == source]
    report = {"source": source}
    per_circle = links.groupby("verjkr_id")["met_point_id"].nunique()
    if (per_circle > 1).any():
        report["circles_with_several_points"] = per_circle[per_circle > 1].index.tolist()
    points = (links.groupby([c for c in links.columns if c != "verjkr_id"], dropna=False)["verjkr_id"]
              .agg(lambda s: ";".join(sorted(s))).rename("verjkr_ids").reset_index())
    met = _frame(conn, MET_DATA_SQL, {"ids": points["met_point_id"].astype(int).tolist(),
                                      "t0": t0.to_pydatetime(), "t1": t1.to_pydatetime()})
    met = pd.DataFrame({"met_point_id": met["met_point_id"].astype("int64"), "variable": met["variable"],
                        "time": _utc(met["epoch"]).astype("datetime64[us, UTC]"),
                        "value": met["value"].astype(float)}) if len(met) else met
    report["points"] = int(len(points))
    if len(met):
        report["series"] = (met.groupby("variable")["time"].agg(["min", "max", "count"]).astype(str)
                            .to_dict(orient="index"))
    first_point = links.sort_values("met_point_id").drop_duplicates("verjkr_id").set_index("verjkr_id")["met_point_id"]
    loggers["met_point_id"] = loggers["locality_id"].astype(str).map(first_point).astype("Int64")
    return points, met, report


def export_site(cfg) -> dict:
    """Query the database for the configured site and period and write the snapshot (work/<site>/db/)."""
    db = cfg.database
    if db is None:
        raise ValueError("the config has no `database` section")
    if cfg.period is None:
        raise ValueError("config.period (start, end) is required for the database export")
    t0 = pd.Timestamp(cfg.period.start, tz="UTC")
    t1 = pd.Timestamp(cfg.period.end, tz="UTC") + pd.Timedelta(days=1)
    pad = pd.Timedelta(days=1)  # reference series reach beyond the period for interpolation
    report: dict = {"service": db.service, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "period_utc": [str(t0), str(t1)]}
    with connect(db.service) as conn:
        report["server"] = conn.execute("SELECT version()").fetchone()[0].split(",")[0]
        dep = _frame(conn, DEPLOYMENTS_SQL, {"start": cfg.period.start, "end": cfg.period.end})
        if dep.empty:
            raise ValueError(f"no TMS deployments overlap the period {cfg.period.start} .. {cfg.period.end}")
        dep, name_notes = _with_names(conn, dep)
        dep, report["deployments"] = select_deployments(dep, cfg)
        if name_notes:
            report["deployments"]["lut_names"] = name_notes
        loggers = logger_rows(dep, cfg)
        tms, report["tms"] = fetch_tms(conn, dep, t0, t1)
        points, met, report["met"] = fetch_met(conn, loggers, db.met_source, t0 - pad, t1 + pad)
    mapped = set(map(str, cfg.calibration.sensors.values()))
    unmapped = sorted(set(tms["height"]) - mapped)
    if unmapped:
        report["tms"]["heights_not_in_calibration_sensors"] = unmapped
    report["loggers"] = {"n": int(len(loggers)), "with_z": int(loggers["z"].notna().sum()),
                         "ids": loggers["id"].tolist()}
    out = cfg.db_dir
    out.mkdir(parents=True, exist_ok=True)
    tms.to_parquet(out / "tms_long.parquet", index=False)
    loggers.to_csv(out / "loggers.csv", index=False)
    points.to_csv(out / "met_points.csv", index=False)
    if len(met):
        met.to_parquet(out / "met_data.parquet", index=False)
    else:
        (out / "met_data.parquet").unlink(missing_ok=True)
    with open(out / "db_export.json", "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    return report
