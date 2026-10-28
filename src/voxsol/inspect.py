"""`voxsol inspect`: report the data facts every later step depends on (read-only)."""

from __future__ import annotations

import json

import numpy as np

from voxsol.config import SiteConfig
from voxsol.io.las import header_info, pulse_grouping_stats, read_sample, scan_angle_deg
from voxsol.io.loggers import read_loggers
from voxsol.io.trajectory import (interpolate_positions, las_time_in_trajectory_base, read_trajectory,
                                  to_projected)

NADIR_DEG = 5.0


def _hist(values: np.ndarray) -> dict[str, int]:
    uniq, counts = np.unique(values, return_counts=True)
    return {str(int(u)): int(c) for u, c in zip(uniq, counts)}


def _pct(values: np.ndarray, qs=(0, 5, 50, 95, 100)) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {}
    return {f"p{q}": float(np.percentile(values, q)) for q in qs}


def inspect_file(path, sample_n: int) -> tuple[dict, object]:
    info = header_info(path)
    pts = read_sample(path, sample_n)
    dims = set(info.dimensions)
    area = (info.maxs[0] - info.mins[0]) * (info.maxs[1] - info.mins[1])
    out = {
        "header": info.as_dict(),
        "sample_points": int(len(pts)),
        "density_bbox_pts_m2": info.point_count / area if area > 0 else float("nan"),
        "return_number": _hist(np.asarray(pts.return_number)),
        "number_of_returns": _hist(np.asarray(pts.number_of_returns)),
        "classification": _hist(np.asarray(pts.classification)),
    }
    if info.has_gps_time:
        t = np.asarray(pts.gps_time)
        out["gps_time_range"] = [float(t.min()), float(t.max())]
        out["pulses"] = pulse_grouping_stats(t, np.asarray(pts.return_number), np.asarray(pts.number_of_returns))
    sa = scan_angle_deg(pts)
    if sa is not None:
        out["scan_angle_deg"] = _pct(sa)
    if info.has_rgb:
        out["rgb_max"] = int(max(np.max(pts.red), np.max(pts.green), np.max(pts.blue)))
        out["rgb_bits"] = 16 if out["rgb_max"] > 255 else 8
    if "intensity" in dims:
        out["intensity"] = _pct(np.asarray(pts.intensity, dtype=float))
    return out, pts


def inspect_trajectory(cfg: SiteConfig, pts, adjusted: bool, n_dist: int) -> dict:
    traj = read_trajectory(cfg.inputs.trajectory, cfg.inputs.trajectory_format)
    dt = np.diff(traj["time"].to_numpy())
    out = {
        "records": int(len(traj)),
        "time_range": [float(traj["time"].min()), float(traj["time"].max())],
        "rate_hz": float(1.0 / np.median(dt)) if dt.size else float("nan"),
        "lat_range": [float(traj["lat"].min()), float(traj["lat"].max())],
        "lon_range": [float(traj["lon"].min()), float(traj["lon"].max())],
        "h_ell_range": [float(traj["h_ell"].min()), float(traj["h_ell"].max())],
    }
    traj_xyz, proj = to_projected(traj, cfg.crs, cfg.vertical_crs)
    out["projection"] = proj
    if pts is None or "gps_time" not in set(pts.point_format.dimension_names):
        return out
    t_pts, base = las_time_in_trajectory_base(np.asarray(pts.gps_time), adjusted, traj["time"].to_numpy())
    out["time_base"] = base
    inside = (t_pts >= out["time_range"][0]) & (t_pts <= out["time_range"][1])
    out["sample_time_overlap"] = float(inside.mean())
    if not inside.any():
        return out
    rng = np.random.default_rng(0)
    idx = rng.choice(np.flatnonzero(inside), size=min(n_dist, int(inside.sum())), replace=False)
    xyz = np.column_stack([np.asarray(pts.x)[idx], np.asarray(pts.y)[idx], np.asarray(pts.z)[idx]])
    scanner = interpolate_positions(traj_xyz, t_pts[idx])
    out["range_m"] = _pct(np.linalg.norm(xyz - scanner, axis=1))
    sa = scan_angle_deg(pts)
    if sa is not None:
        nadir = np.abs(sa[idx]) < NADIR_DEG
        if nadir.any():
            out["nadir_scanner_height_above_points_m"] = _pct(scanner[nadir, 2] - xyz[nadir, 2])
    return out


def inspect_loggers(cfg: SiteConfig, bounds: tuple[list[float], list[float]]) -> dict:
    df = read_loggers(cfg.inputs.loggers)
    (xmin, ymin, _), (xmax, ymax, _) = bounds
    inside = df["x"].between(xmin, xmax) & df["y"].between(ymin, ymax)
    return {
        "n": int(len(df)),
        "n_reference": int(df["reference"].sum()),
        "xy_sd_m": _pct(df["xy_sd"].to_numpy(float)),
        "inside_las_bounds": int(inside.sum()),
        "outside_ids": df.loc[~inside, "id"].tolist(),
        "has_z": "z" in df.columns,
    }


def assess(report: dict) -> list[str]:
    """Plain-language conclusions for the next pipeline steps."""
    notes = []
    for f in report["files"]:
        name = f["header"]["path"]
        p = f.get("pulses")
        if p is None:
            notes.append(f"{name}: no gps_time, so beam-based voxelization is impossible; use the vertical-beam fallback.")
        else:
            frac = p["fraction_points_in_consistent_groups"]
            verdict = "OK for pulse reconstruction" if frac > 0.9 else "pulses need a different grouping rule"
            notes.append(f"{name}: {frac:.1%} of points in consistent gps_time groups ({verdict}).")
        if "scan_angle_deg" not in f:
            notes.append(f"{name}: no scan angle field.")
        if not f["header"]["crs_wkt"]:
            notes.append(f"{name}: no CRS in header; the config CRS will be assumed.")
    traj = report.get("trajectory")
    if traj is None:
        notes.append("No trajectory: reprocess the mission in DJI Terra with trajectory output (sbet.out) "
                     "enabled for beam-based voxelization. Fallback: beams from scan angle.")
    elif "error" in traj:
        notes.append(f"Trajectory could not be used: {traj['error']}")
    else:
        if traj["projection"]["unavailable_grids"]:
            notes.append(f"Missing PROJ grids {traj['projection']['unavailable_grids']}: download into "
                         "data/external/proj/ for an exact height datum conversion.")
        ov = traj.get("sample_time_overlap")
        if ov is not None:
            notes.append(f"Trajectory covers {ov:.1%} of sampled points ({traj.get('time_base')}).")
        h = traj.get("nadir_scanner_height_above_points_m", {}).get("p50")
        if h is not None:
            notes.append(f"Median scanner height above nadir points: {h:.1f} m. Compare with the planned "
                         "flight height above ground; an offset of ~45-50 m means ellipsoidal vs. "
                         "orthometric heights are mixed (set vertical_crs).")
    return notes


def run_inspect(cfg: SiteConfig, sample_n: int = 3_000_000, n_dist: int = 20_000) -> dict:
    files = cfg.inputs.laz_files()
    if not files:
        raise FileNotFoundError(f"No LAZ files match {cfg.inputs.laz}")
    report: dict = {"site": cfg.site, "files": []}
    first_pts, first_info = None, None
    for path in files:
        info, pts = inspect_file(path, sample_n)
        report["files"].append(info)
        if first_pts is None:
            first_pts, first_info = pts, info["header"]
        else:
            del pts
    if cfg.inputs.trajectory:
        try:
            report["trajectory"] = inspect_trajectory(cfg, first_pts, first_info["adjusted_gps_time"], n_dist)
        except Exception as exc:  # report and continue; inspection must not abort on one bad input
            report["trajectory"] = {"error": f"{type(exc).__name__}: {exc}"}
    if cfg.inputs.loggers:
        mins = [min(f["header"]["mins"][i] for f in report["files"]) for i in range(3)]
        maxs = [max(f["header"]["maxs"][i] for f in report["files"]) for i in range(3)]
        report["loggers"] = inspect_loggers(cfg, (mins, maxs))
    report["assessment"] = assess(report)
    with open(cfg.workdir / "inspect.json", "w") as fh:
        json.dump(report, fh, indent=2)
    return report
