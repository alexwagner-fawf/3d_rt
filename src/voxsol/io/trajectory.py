"""Scanner trajectory (DJI Terra sbet.out / sbet.txt): reading, time base and datum conversion."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer
from pyproj.crs import CompoundCRS
from pyproj.transformer import TransformerGroup

SBET_FIELDS = (
    "time", "lat", "lon", "h_ell", "vx", "vy", "vz", "roll", "pitch", "heading",
    "wander", "ax", "ay", "az", "wx", "wy", "wz",
)
SBET_RECORD_BYTES = 8 * len(SBET_FIELDS)
GPS_WEEK_S = 604800.0
ADJUSTED_GPS_OFFSET = 1e9  # LAS adjusted standard GPS time = GPS time - 1e9

_TXT_ALIASES = {
    "time": ("time", "gps_time", "gpstime", "t", "sow", "gps time"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "long", "longitude"),
    "h_ell": ("h_ell", "alt", "altitude", "height", "h", "ellipsoid height"),
}


def detect_format(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".out" or (path.stat().st_size % SBET_RECORD_BYTES == 0
                                         and path.suffix.lower() != ".txt"):
        return "sbet_out"
    return "sbet_txt"


def read_sbet_out(path: str | Path) -> pd.DataFrame:
    """Applanix-style SBET: 17 little-endian float64 per record, angles in radians."""
    raw = np.fromfile(path, dtype="<f8")
    if raw.size % len(SBET_FIELDS):
        raise ValueError(f"{path}: size is not a multiple of {SBET_RECORD_BYTES} bytes, not an SBET file")
    df = pd.DataFrame(raw.reshape(-1, len(SBET_FIELDS)), columns=SBET_FIELDS)
    for col in ("lat", "lon", "roll", "pitch", "heading", "wander"):
        df[col] = np.degrees(df[col])
    return df


def read_sbet_txt(path: str | Path) -> pd.DataFrame:
    """Text trajectory with a header row; columns are matched by common names."""
    df = pd.read_csv(path, sep=None, engine="python", comment="#")
    lower = {c.strip().lower(): c for c in df.columns}
    out = {}
    for target, aliases in _TXT_ALIASES.items():
        match = next((lower[a] for a in aliases if a in lower), None)
        if match is None:
            raise ValueError(f"{path}: no column for '{target}' (have: {list(df.columns)})")
        out[target] = df[match].astype(float)
    return pd.DataFrame(out)


def read_trajectory(path: str | Path, fmt: str = "auto") -> pd.DataFrame:
    fmt = detect_format(path) if fmt == "auto" else fmt
    df = read_sbet_out(path) if fmt == "sbet_out" else read_sbet_txt(path)
    return df.sort_values("time", kind="stable").reset_index(drop=True)


def las_time_to_seconds_of_week(t_las: np.ndarray, adjusted: bool) -> tuple[np.ndarray, np.ndarray]:
    """Return (gps_week, seconds_of_week) for LAS gps_time values.

    With adjusted standard GPS time, absolute GPS seconds are t + 1e9. Without it, LAS stores
    seconds of week already (week unknown, returned as -1).
    """
    t_las = np.asarray(t_las, dtype=np.float64)
    if not adjusted:
        return np.full(t_las.shape, -1, dtype=np.int64), t_las
    t_abs = t_las + ADJUSTED_GPS_OFFSET
    week = np.floor(t_abs / GPS_WEEK_S).astype(np.int64)
    return week, t_abs - week * GPS_WEEK_S


def las_time_in_trajectory_base(t_las: np.ndarray, adjusted: bool, traj_time: np.ndarray) -> tuple[np.ndarray, str]:
    """Express LAS gps_time in the time base of the trajectory.

    SBET files usually store GPS seconds of week; some exports use absolute GPS seconds.
    """
    t_las = np.asarray(t_las, dtype=np.float64)
    traj_absolute = float(np.nanmax(traj_time)) > GPS_WEEK_S
    if traj_absolute:
        if not adjusted:
            raise ValueError("Trajectory uses absolute GPS time but LAS stores seconds of week")
        return t_las + ADJUSTED_GPS_OFFSET, "absolute GPS seconds"
    _, sow = las_time_to_seconds_of_week(t_las, adjusted)
    return sow, "GPS seconds of week"


def projected_transformer(crs: str, vertical_crs: str | None) -> tuple[Transformer, dict]:
    """Transformer from WGS84 geographic 3D (lat, lon, h_ell) to the site CRS.

    Returns the transformer and a report noting whether a geoid grid was available. Without a
    vertical CRS heights stay ellipsoidal.
    """
    horizontal = CRS.from_user_input(crs)
    target = horizontal
    if vertical_crs:
        target = CompoundCRS(name="site", components=[horizontal, CRS.from_user_input(vertical_crs)])
    group = TransformerGroup("EPSG:4979", target, always_xy=True)
    report = {
        "target": target.name,
        "heights": "orthometric" if vertical_crs else "ellipsoidal",
        "best_available": bool(group.best_available),
        "unavailable_grids": sorted({g.short_name for op in group.unavailable_operations for g in op.grids
                                     if not g.available}),
    }
    if not group.transformers:
        raise RuntimeError(f"No transformation EPSG:4979 -> {target.name}; missing grids: "
                           f"{report['unavailable_grids']}")
    return group.transformers[0], report


def to_projected(traj: pd.DataFrame, crs: str, vertical_crs: str | None = None) -> tuple[pd.DataFrame, dict]:
    transformer, report = projected_transformer(crs, vertical_crs)
    x, y, z = transformer.transform(traj["lon"].to_numpy(), traj["lat"].to_numpy(), traj["h_ell"].to_numpy())
    out = traj[["time"]].copy()
    out["x"], out["y"], out["z"] = x, y, z
    return out, report


def interpolate_positions(traj_xyz: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    """Linear interpolation of scanner positions (N, 3) at seconds-of-week times `t`.

    Times outside the trajectory return NaN.
    """
    tt = traj_xyz["time"].to_numpy()
    pos = np.empty((t.size, 3))
    for k, col in enumerate(("x", "y", "z")):
        pos[:, k] = np.interp(t, tt, traj_xyz[col].to_numpy(), left=np.nan, right=np.nan)
    return pos
