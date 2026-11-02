"""Radiation forcing (CAMS, ERA5, offline clear-sky) and macroclimate reference (ERA5-Land, files).

Standard radiation table: index = interval start (UTC), columns ghi, dni, dhi = mean irradiance
over the interval [W/m2]; `df.attrs["step_s"]` holds the interval length. Downloads are cached in
data/external/ (inside the repo); credentials come from secrets/credentials.yaml.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from voxsol import REPO_ROOT

EXTERNAL = REPO_ROOT / "data" / "external"
CREDENTIALS = REPO_ROOT / "secrets" / "credentials.yaml"
CDS_URL = "https://cds.climate.copernicus.eu/api"
STEP_S = {"15min": 900, "1h": 3600}


def load_credentials(path: Path = CREDENTIALS) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it with e.g.\n  soda_email: you@example.org\n  cds_key: <personal access token>")
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def interval_index(start, end, time_step: str) -> pd.DatetimeIndex:
    """Interval starts covering [start, end] (dates inclusive), UTC."""
    return pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1),
                         freq=time_step, inclusive="left")


def midpoints(index: pd.DatetimeIndex, step_s: int) -> pd.DatetimeIndex:
    return index + pd.Timedelta(seconds=step_s / 2)


def _standard(df: pd.DataFrame, step_s: int, source: str) -> pd.DataFrame:
    out = df[["ghi", "dni", "dhi"]].astype(float).clip(lower=0.0)
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC") if out.index.tz else out.index.tz_localize("UTC")
    out.attrs.update(step_s=step_s, source=source)
    return out


# ---------------------------------------------------------------------------- offline clear-sky

def clearsky(start, end, lat: float, lon: float, altitude: float, time_step: str = "15min",
             cloudiness: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """Ineichen clear sky; with cloudiness > 0 a smooth random cloud modulation of GHI is applied
    and split into direct/diffuse with Erbs. For offline runs and tests."""
    from pvlib.irradiance import erbs
    from pvlib.location import Location

    step_s = STEP_S[time_step]
    idx = interval_index(start, end, time_step)
    mid = midpoints(idx, step_s)
    loc = Location(lat, lon, altitude=altitude)
    cs = loc.get_clearsky(mid, model="ineichen")
    if cloudiness <= 0:
        df = cs[["ghi", "dni", "dhi"]]
    else:
        rng = np.random.default_rng(seed)
        n_days = int(np.ceil(len(idx) * step_s / 86400)) + 1
        daily = np.clip(1.0 - cloudiness * rng.beta(1.2, 1.8, n_days), 0.15, 1.0)
        day = ((mid - mid[0]).total_seconds() // 86400).astype(int)
        flicker = np.clip(1.0 - 0.3 * cloudiness * rng.random(len(idx)), 0.4, 1.0)
        ghi = cs["ghi"].to_numpy() * daily[day] * flicker
        zen = loc.get_solarposition(mid)["apparent_zenith"]
        parts = erbs(ghi, zen, mid)
        df = pd.DataFrame({"ghi": ghi, "dni": parts["dni"], "dhi": parts["dhi"]}, index=mid)
    df.index = idx
    return _standard(df, step_s, "clearsky" if cloudiness <= 0 else f"clearsky*clouds({cloudiness})")


# ---------------------------------------------------------------------------- CAMS radiation

def cams(start, end, lat: float, lon: float, altitude: float, time_step: str = "15min",
         email: str | None = None, cache: Path | None = None) -> pd.DataFrame:
    """CAMS radiation service (SoDa) via pvlib: all-sky GHI, DHI and DNI (BNI)."""
    from pvlib.iotools import get_cams

    step_s = STEP_S[time_step]
    cache = cache or EXTERNAL / f"cams_{lat:.4f}_{lon:.4f}_{start}_{end}_{time_step}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        email = email or load_credentials().get("soda_email")
        if not email:
            raise ValueError("CAMS needs a registered SoDa e-mail (soda_email in secrets/credentials.yaml)")
        data, _ = get_cams(lat, lon, pd.Timestamp(start), pd.Timestamp(end), email,
                           identifier="cams_radiation", altitude=altitude, time_step=time_step,
                           time_ref="UT", integrated=False, label="left", map_variables=True)
        df = data[["ghi", "dni", "dhi"]]
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache)
    return _standard(df, step_s, "cams_radiation")


# ---------------------------------------------------------------------------- ERA5 / ERA5-Land

def _cds_client():
    import cdsapi

    creds = load_credentials()
    if not creds.get("cds_key"):
        raise ValueError("ERA5 needs cds_key in secrets/credentials.yaml (CDS personal access token)")
    return cdsapi.Client(url=creds.get("cds_url", CDS_URL), key=creds["cds_key"], quiet=True)


def _cds_monthly(dataset: str, variables: list[str], start, end, lat: float, lon: float, tag: str,
                 extra: dict | None = None) -> list[Path]:
    """Download monthly NetCDF files for a 0.2 deg box around the site (cached)."""
    files = []
    client = None
    for month in pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M"):
        target = EXTERNAL / f"{tag}_{lat:.3f}_{lon:.3f}_{month}.nc"
        if not target.exists():
            client = client or _cds_client()
            target.parent.mkdir(parents=True, exist_ok=True)
            request = {
                "variable": variables, "year": [str(month.year)], "month": [f"{month.month:02d}"],
                "day": [f"{d:02d}" for d in range(1, month.days_in_month + 1)],
                "time": [f"{h:02d}:00" for h in range(24)],
                "area": [lat + 0.1, lon - 0.1, lat - 0.1, lon + 0.1],
                "data_format": "netcdf", "download_format": "unarchived", **(extra or {}),
            }
            client.retrieve(dataset, request, str(target))
        files.append(target)
    return files


def _open_point(files: list[Path], lat: float, lon: float) -> pd.DataFrame:
    import xarray as xr

    ds = xr.open_mfdataset([str(f) for f in files], combine="by_coords")
    time_dim = "valid_time" if "valid_time" in ds.dims else "time"
    pt = ds.interp(latitude=lat, longitude=lon, method="linear")
    df = pt.to_dataframe()
    df = df[[v for v in ds.data_vars if v in df.columns]]
    df.index = pd.DatetimeIndex(df.index.get_level_values(time_dim)).tz_localize("UTC")
    return df[~df.index.duplicated()].sort_index()


def era5_radiation(start, end, lat: float, lon: float) -> pd.DataFrame:
    """ERA5 hourly ssrd/fdir (accumulated over the hour ending at valid_time) -> GHI, DNI, DHI."""
    from pvlib.solarposition import get_solarposition

    files = _cds_monthly("reanalysis-era5-single-levels",
                         ["surface_solar_radiation_downwards", "total_sky_direct_solar_radiation_at_surface"],
                         start, end, lat, lon, "era5rad", {"product_type": ["reanalysis"]})
    df = _open_point(files, lat, lon)
    ghi = df["ssrd"] / 3600.0
    bhi = df["fdir"] / 3600.0
    idx = df.index - pd.Timedelta(hours=1)  # interval start
    cosz = np.cos(np.radians(get_solarposition(idx + pd.Timedelta(minutes=30), lat, lon)["apparent_zenith"]))
    dni = np.where(cosz > 0.03, bhi.to_numpy() / np.maximum(cosz.to_numpy(), 0.03), 0.0)
    out = pd.DataFrame({"ghi": ghi.to_numpy(), "dni": dni, "dhi": (ghi - bhi).clip(lower=0).to_numpy()}, index=idx)
    out = out.loc[(out.index >= pd.Timestamp(start, tz="UTC"))
                  & (out.index < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))]
    return _standard(out, 3600, "era5")


def era5land_reference(start, end, lat: float, lon: float, site_elevation: float | None = None,
                       grid_elevation: float | None = None, lapse_rate: float = -0.0065) -> pd.DataFrame:
    """Hourly ERA5-Land reference: t2m, skt, stl1 [deg C], swvl1 [m3/m3].

    With both elevations given, temperatures are lapse-rate corrected to the site elevation.
    """
    files = _cds_monthly("reanalysis-era5-land",
                         ["2m_temperature", "skin_temperature", "soil_temperature_level_1",
                          "volumetric_soil_water_layer_1"], start, end, lat, lon, "era5land")
    df = _open_point(files, lat, lon)
    out = pd.DataFrame(index=df.index)
    for var in ("t2m", "skt", "stl1"):
        out[var] = df[var] - 273.15
    out["swvl1"] = df["swvl1"]
    if site_elevation is not None and grid_elevation is not None:
        for var in ("t2m", "skt", "stl1"):
            out[var] += lapse_rate * (site_elevation - grid_elevation)
    out.attrs.update(source="era5land", lapse_corrected=site_elevation is not None and grid_elevation is not None)
    return out


def reference_from_file(path: str | Path) -> pd.DataFrame:
    """Own reference data: CSV with a `time` column (UTC) and any of t2m, skt, stl1 [deg C]."""
    df = pd.read_csv(path, parse_dates=["time"]).set_index("time")
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    df.attrs.update(source=f"file:{Path(path).name}")
    return df


# ---------------------------------------------------------------------------- database snapshot (voxsol db)

# Hourly GHI time stamp -> middle of its averaging hour. Open-Meteo labels the mean of the preceding hour.
GHI_LABEL_OFFSET_MIN = {"end": -30, "instant": 0, "start": 30}


def read_db_met(db_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ERA5 points and series from the `voxsol db` snapshot."""
    points_csv, data_pq = Path(db_dir) / "met_points.csv", Path(db_dir) / "met_data.parquet"
    if not points_csv.exists():
        raise FileNotFoundError(f"{points_csv} missing: run `voxsol db <config>` first")
    points = pd.read_csv(points_csv)
    if points.empty or not data_pq.exists():
        raise ValueError(f"{db_dir}: no ERA5 points/series are linked to the loggers (see db_export.json)")
    return points, pd.read_parquet(data_pq)


def _db_series(data: pd.DataFrame, variable: str) -> pd.DataFrame:
    """time x met_point_id table of one variable."""
    sel = data[data["variable"] == variable]
    if sel.empty:
        raise ValueError(f"the database snapshot has no '{variable}' series")
    wide = sel.pivot_table(index="time", columns="met_point_id", values="value")
    wide.index = pd.DatetimeIndex(wide.index).tz_convert("UTC")
    return wide.sort_index()


def db_reference(db_dir: Path, site_elevation: float, lapse_rate: float = -0.0065) -> pd.DataFrame:
    """Hourly reference t2m [deg C] (and precip) from the ERA5 points linked to the loggers.

    Each point is lapse-rate corrected from its own elevation (the height Open-Meteo downscaled it to)
    to the site elevation, then the points are averaged.
    """
    points, data = read_db_met(db_dir)
    t2m = _db_series(data, "t2m")
    elev = points.set_index("met_point_id")["elevation"].reindex(t2m.columns).to_numpy(float)
    shift = np.where(np.isfinite(elev), lapse_rate * (site_elevation - elev), 0.0)
    out = pd.DataFrame({"t2m": (t2m + shift).mean(axis=1)})
    if (data["variable"] == "precip").any():
        out["precip"] = _db_series(data, "precip").mean(axis=1).reindex(out.index)
    out.attrs.update(source=f"db:{points['source'].iloc[0]}", points=int(t2m.shape[1]),
                     lapse_corrected=bool(np.isfinite(elev).all()))
    return out


def ghi_label_scores(ghi: pd.Series, lat: float, lon: float, altitude: float) -> dict[str, float]:
    """Correlation of hourly GHI with clear-sky GHI at the hour middle implied by each label convention."""
    from pvlib.location import Location

    loc = Location(lat, lon, altitude=altitude)
    scores = {}
    for label, off in GHI_LABEL_OFFSET_MIN.items():
        cs = loc.get_clearsky(ghi.index + pd.Timedelta(minutes=off), model="ineichen")["ghi"].to_numpy()
        scores[label] = round(float(np.corrcoef(ghi.to_numpy(), cs)[0, 1]), 4)
    return scores


def db_radiation(db_dir: Path, start, end, lat: float, lon: float, altitude: float, time_step: str = "15min",
                 label: str = "auto") -> tuple[pd.DataFrame, dict]:
    """ERA5 hourly GHI from the database -> 15-min GHI (clear-sky shape within each hour, hourly energy
    kept) -> DNI/DHI with Erbs. Returns the standard radiation table and the label check."""
    from pvlib.irradiance import erbs
    from pvlib.location import Location

    _, data = read_db_met(db_dir)
    ghi_h = _db_series(data, "ghi").mean(axis=1).dropna().clip(lower=0.0)
    scores = ghi_label_scores(ghi_h, lat, lon, altitude)
    if label == "auto":
        label = max(scores, key=scores.get)
    hour_start = ghi_h.index + pd.Timedelta(minutes=GHI_LABEL_OFFSET_MIN[label] - 30)
    n, sub_s = 4, 900
    starts = hour_start.repeat(n) + pd.to_timedelta(np.tile(np.arange(n) * sub_s, len(hour_start)), unit="s")
    mid = starts + pd.Timedelta(seconds=sub_s / 2)
    loc = Location(lat, lon, altitude=altitude)
    cs = loc.get_clearsky(mid, model="ineichen")["ghi"].to_numpy().reshape(-1, n)
    cs_mean = cs.mean(axis=1, keepdims=True)
    shape = np.where(cs_mean > 1.0, cs / np.maximum(cs_mean, 1.0), 1.0)  # mean 1 within each hour
    ghi = (ghi_h.to_numpy()[:, None] * shape).ravel()
    parts = erbs(ghi, loc.get_solarposition(mid)["apparent_zenith"].to_numpy(), mid)
    df = pd.DataFrame({"ghi": ghi, "dni": np.asarray(parts["dni"]), "dhi": np.asarray(parts["dhi"])}, index=starts)
    if time_step == "1h":
        df = df.resample("1h").mean()
    df = df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))]
    return _standard(df, STEP_S[time_step], f"db:ghi+erbs ({label})"), {"ghi_label": label, "ghi_label_corr": scores}
