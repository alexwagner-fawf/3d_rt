"""Pipeline steps. Each step reads its inputs from work/<site>/ and writes its outputs there.

prepare -> voxelize -> understory -> horizon -> forcing -> trace -> irradiance
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from voxsol.config import SiteConfig
from voxsol.grid import Raster, VoxelGrid

STATIC_DIR = "maps"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _write_json(path: Path, obj: dict) -> None:
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))


def _maps_dir(cfg: SiteConfig) -> Path:
    d = cfg.workdir / STATIC_DIR
    d.mkdir(exist_ok=True)
    return d


def site_location(cfg: SiteConfig, dtm: Raster) -> tuple[float, float, float]:
    """Latitude, longitude and mean elevation of the DTM center."""
    lon, lat = Transformer.from_crs(cfg.crs, "EPSG:4326", always_xy=True).transform(
        (dtm.x0 + dtm.x1) / 2, (dtm.y0 + dtm.y1) / 2)
    return float(lat), float(lon), float(np.nanmean(dtm.data))


def load_grid(cfg: SiteConfig) -> VoxelGrid:
    dtm = Raster.read(cfg.workdir / "dtm.tif")
    return VoxelGrid.load(cfg.workdir / "voxels.npz", dtm)


# ------------------------------------------------------------------------------------ prepare

def read_points(cfg: SiteConfig, region: tuple[float, float, float, float] | None = None) -> dict:
    """All LAZ points, or only those inside `region` (xmin, ymin, xmax, ymax), read in chunks so that
    only the kept points are held in memory."""
    from voxsol.io.las import header_info, iter_chunks, scan_angle_deg

    files = cfg.inputs.laz_files()
    if not files:
        raise FileNotFoundError(f"No LAZ files match {cfg.inputs.laz}")
    parts = []
    adjusted = None
    n_total = 0
    for path in files:
        info = header_info(path)
        adjusted = info.adjusted_gps_time if adjusted is None else adjusted
        if info.adjusted_gps_time != adjusted:
            raise ValueError("LAZ files mix GPS time encodings")
        if region is not None and (info.maxs[0] < region[0] or info.mins[0] > region[2]
                                   or info.maxs[1] < region[1] or info.mins[1] > region[3]):
            n_total += info.point_count
            _log(f"skip {path} (outside the AOI context box)")
            continue
        n_file = n_kept = 0
        for pts in iter_chunks(path):
            x, y = np.asarray(pts.x, np.float64), np.asarray(pts.y, np.float64)
            n_file += len(x)
            keep = np.ones(len(x), bool) if region is None else (
                (x >= region[0]) & (x <= region[2]) & (y >= region[1]) & (y <= region[3]))
            if not keep.any():
                continue
            sa = scan_angle_deg(pts)
            parts.append({
                "xyz": np.column_stack([x[keep], y[keep], np.asarray(pts.z, np.float64)[keep]]),
                "gps_time": np.asarray(pts.gps_time, np.float64)[keep] if info.has_gps_time else None,
                "return_number": np.asarray(pts.return_number, np.int16)[keep],
                "number_of_returns": np.asarray(pts.number_of_returns, np.int16)[keep],
                "classification": np.asarray(pts.classification, np.uint8)[keep],
                "rgb": np.column_stack([pts.red, pts.green, pts.blue])[keep] if info.has_rgb else None,
                "scan_angle": sa[keep] if sa is not None else None})
            n_kept += int(keep.sum())
        n_total += n_file
        _log(f"read {path} ({n_file:,} points" + (f", {n_kept:,} in the AOI context box)" if region else ")"))
    if not parts:
        raise ValueError("no LAZ points inside the AOI context box (check aoi and the LAZ CRS)")
    out = {}
    for key in parts[0]:
        vals = [p[key] for p in parts]
        out[key] = None if any(v is None for v in vals) else np.concatenate(vals)
    out["adjusted_gps_time"] = adjusted
    out["points_in_files"] = n_total
    return out


def _scanner_function(cfg: SiteConfig, adjusted: bool):
    from voxsol.io.trajectory import (interpolate_positions, las_time_in_trajectory_base, read_trajectory,
                                      to_projected)

    traj = read_trajectory(cfg.inputs.trajectory, cfg.inputs.trajectory_format)
    xyz, proj = to_projected(traj, cfg.crs, cfg.vertical_crs)

    def scanner_at(t_las):
        t, _ = las_time_in_trajectory_base(t_las, adjusted, xyz["time"].to_numpy())
        return interpolate_positions(xyz, t)

    return scanner_at, proj


def step_prepare(cfg: SiteConfig) -> dict:
    from voxsol.io.loggers import logger_table
    from voxsol.preprocess.ground import (classify_ground_csf, dtm_check, dtm_extent, rasterize_dtm,
                                          sor_filter)
    from voxsol.preprocess.pulses import build_pulses

    from voxsol.aoi import context_box, points_in, read_aoi, summary, write_aoi

    p = cfg.prepare
    if cfg.database is not None and not Path(cfg.inputs.tms).exists():
        raise FileNotFoundError(f"{cfg.inputs.tms} missing: run `voxsol db <config>` first (database snapshot)")
    aoi = read_aoi(cfg.aoi, cfg.crs) if cfg.aoi is not None else None
    write_aoi(cfg.workdir, aoi)  # later steps read this copy (and remove a stale one when aoi is unset)
    pts = read_points(cfg, context_box(aoi, cfg.buffer_m) if aoi else None)
    xyz = pts["xyz"]
    report: dict = {"points_read": int(len(xyz))}
    if aoi:
        report["aoi"] = summary(aoi, cfg.buffer_m) | {"points_in_files": int(pts["points_in_files"])}
        _log(f"AOI {report['aoi']['area_ha']} ha; {len(xyz):,} of {pts['points_in_files']:,} points in its "
             f"context box ({report['aoi']['context_box_ha']} ha)")
    keep = sor_filter(xyz) if p.sor else np.ones(len(xyz), bool)
    report["points_noise"] = int((~keep).sum())
    for key in ("xyz", "gps_time", "return_number", "number_of_returns", "classification", "rgb", "scan_angle"):
        if pts[key] is not None:
            pts[key] = pts[key][keep]
    xyz = pts["xyz"]
    if p.ground == "las_class":
        ground = pts["classification"] == 2
    else:
        last = pts["return_number"] == pts["number_of_returns"]
        ground = np.zeros(len(xyz), bool)
        ground[np.flatnonzero(last)] = classify_ground_csf(xyz[last], p.csf_cloth_resolution, p.csf_rigidness,
                                                            p.csf_class_threshold)
    report["ground_points"] = int(ground.sum())
    _log(f"ground: {ground.sum():,} of {len(xyz):,} points")
    x0, y0, nx, ny = dtm_extent(*xyz[:, :2].min(0), *xyz[:, :2].max(0), cfg.grid.dtm_res)
    dtm, has_ground = rasterize_dtm(xyz[ground], x0, y0, nx, ny, cfg.grid.dtm_res, cfg.crs)
    report["dtm_cells_with_ground"] = float(has_ground.mean())
    dtm.write(cfg.workdir / "dtm.tif")
    hag = xyz[:, 2] - dtm.sample(xyz[:, 0], xyz[:, 1])
    if pts["gps_time"] is None:
        raise ValueError("LAZ files have no gps_time; pulses cannot be reconstructed")
    scanner_at, proj = (None, None)
    if cfg.inputs.trajectory:
        scanner_at, proj = _scanner_function(cfg, pts["adjusted_gps_time"])
        report["trajectory_projection"] = proj
    pulses, prep = build_pulses(xyz, pts["gps_time"], pts["return_number"], ground, hag, cfg.grid.h_cut,
                                scanner_at=scanner_at)
    report["pulses"] = prep
    pulses.save(cfg.workdir / "pulses.npz")
    low = (hag < cfg.grid.h_cut) | ground
    rgb = pts["rgb"]
    np.savez(cfg.workdir / "points_low.npz", xyz=xyz[low], hag=hag[low], ground=ground[low],
             rgb=rgb[low] if rgb is not None else np.zeros((0, 3)))
    if rgb is not None:
        canopy = ~low
        np.savez(cfg.workdir / "points_canopy.npz", xyz=xyz[canopy], hag=hag[canopy], rgb=rgb[canopy])
    if cfg.inputs.tms or cfg.inputs.loggers:
        loggers = logger_table(cfg.inputs.tms, cfg.inputs.loggers, p.logger_xy_sd,
                               cfg.forcing.reference_loggers, cfg.inputs.tms_columns)
        inside = loggers["x"].between(dtm.x0, dtm.x1) & loggers["y"].between(dtm.y0, dtm.y1)
        if not inside.all():
            report["loggers_outside_scan"] = loggers.loc[~inside, "id"].tolist()
            _log(f"{(~inside).sum()} loggers outside the scanned area dropped: {report['loggers_outside_scan']}")
        if aoi is not None:
            out_aoi = inside & ~points_in(aoi, loggers["x"].to_numpy(), loggers["y"].to_numpy())
            if out_aoi.any():
                report["loggers_outside_aoi"] = loggers.loc[out_aoi, "id"].tolist()
                _log(f"{out_aoi.sum()} loggers outside the AOI dropped: {report['loggers_outside_aoi']}")
            inside &= ~out_aoi
        loggers = loggers[inside]
        loggers.to_csv(cfg.workdir / "loggers.csv", index=False)
        report["loggers"] = int(len(loggers))
        if "z" in loggers.columns and loggers["z"].notna().any():
            sel = loggers["z"].notna()
            report["dtm_check_vs_logger_z"] = dtm_check(dtm, loggers.loc[sel, "x"].to_numpy(),
                                                        loggers.loc[sel, "y"].to_numpy(),
                                                        loggers.loc[sel, "z"].to_numpy())
    _write_json(cfg.workdir / "prepare.json", report)
    return report


# ------------------------------------------------------------------------------------ voxelize

def step_voxelize(cfg: SiteConfig) -> dict:
    from voxsol.preprocess.pulses import Pulses
    from voxsol.rgb import green_canopy_metrics, voxel_green_fraction
    from voxsol.voxelize import column_metrics, voxelize

    dtm = Raster.read(cfg.workdir / "dtm.tif")
    grid = VoxelGrid.over_dtm(dtm, cfg.grid.dx, cfg.grid.h_cut, cfg.grid.dz, cfg.grid.zmax)
    pulses = Pulses.load(cfg.workdir / "pulses.npz")
    _log(f"voxelize {grid.nx}x{grid.ny}x{grid.nz} voxels from {len(pulses):,} pulses")
    out = voxelize(pulses, grid, cfg.voxel.pulse_min)
    grid.save(cfg.workdir / "voxels.npz")
    layers = column_metrics(grid, cfg.voxel.g_leaf)
    canopy_pts = cfg.workdir / "points_canopy.npz"
    if canopy_pts.exists():
        c = np.load(canopy_pts)
        green = voxel_green_fraction(c["xyz"], c["hag"], c["rgb"], grid)
        np.save(cfg.workdir / "voxels_green.npy", green)
        layers.update(green_canopy_metrics(grid, green, cfg.voxel.g_leaf))
    for name, arr in layers.items():
        Raster(arr.astype(np.float32), grid.x0, grid.y0, grid.dx, cfg.crs).write(_maps_dir(cfg) / f"canopy_{name}.tif")
    _write_json(cfg.workdir / "voxelize.json", out["report"])
    return out["report"]


# ------------------------------------------------------------------------------------ understory

def map_template(cfg: SiteConfig, grid: VoxelGrid) -> Raster:
    from voxsol.trace import map_pixels

    return map_pixels(grid, cfg.trace.map_res, cfg.buffer_m)


def map_mask(cfg: SiteConfig, tpl: Raster) -> np.ndarray | None:
    """Map pixels to compute when an AOI is set (row 0 = south), else None (all pixels)."""
    from voxsol.aoi import load_aoi, pixel_mask

    fc = load_aoi(cfg)
    if fc is None:
        return None
    disk = None
    path = cfg.workdir / "loggers.csv"
    if path.exists():
        loggers = pd.read_csv(path)
        if len(loggers):
            disk, _ = logger_points(cfg, loggers)
    return pixel_mask(fc, tpl, disk)


def _full(values: np.ndarray, pix: np.ndarray | None, tpl: Raster) -> np.ndarray:
    """Values of the computed pixels as a full (ny, nx) map, NaN elsewhere."""
    if pix is None:
        return values.reshape(tpl.ny, tpl.nx).astype(np.float32)
    out = np.full(tpl.ny * tpl.nx, np.nan, np.float32)
    out[pix] = values
    return out.reshape(tpl.ny, tpl.nx)


def step_understory(cfg: SiteConfig) -> dict:
    from voxsol.preprocess.pulses import Pulses
    from voxsol.rgb import ortho_indices
    from voxsol.understory import ground_surface_metrics, understory_metrics

    grid = load_grid(cfg)
    tpl = map_template(cfg, grid)
    low = np.load(cfg.workdir / "points_low.npz")
    pulses = Pulses.load(cfg.workdir / "pulses.npz")
    rgb = low["rgb"] if low["rgb"].shape[0] else None
    layers = understory_metrics(low["xyz"], low["hag"], low["ground"], tpl, cfg.grid.h_cut,
                                tuple(cfg.trace.sensor_heights), rgb=rgb,
                                last_xy=pulses.last_xyz()[:, :2], last_ground=pulses.last_ground)
    layers.update(ground_surface_metrics(low["xyz"][low["ground"]], grid.dtm, tpl))
    if cfg.inputs.ortho:
        layers.update(ortho_indices(cfg.inputs.ortho, tpl, cfg.understory.ortho_res, cfg.understory.ortho_block_px,
                                    log=_log))
    mask = map_mask(cfg, tpl)
    for name, arr in layers.items():
        arr = arr.astype(np.float32)
        if mask is not None:
            arr[~mask] = np.nan
        Raster(arr, tpl.x0, tpl.y0, tpl.res, cfg.crs).write(_maps_dir(cfg) / f"{name}.tif")
    report = {"layers": sorted(layers), "pixels": int(tpl.nx * tpl.ny)}
    if mask is not None:
        report["pixels_in_aoi"] = int(mask.sum())
    _write_json(cfg.workdir / "understory.json", report)
    return report


# ------------------------------------------------------------------------------------ horizon

def step_horizon(cfg: SiteConfig) -> dict:
    from voxsol.horizon import compute_horizon, load_dem

    target = cfg.workdir / "horizon.npz"
    if not cfg.horizon.dem:
        target.unlink(missing_ok=True)
        return {"horizon": "none (no external DEM configured)"}
    dtm = Raster.read(cfg.workdir / "dtm.tif")
    pad = cfg.horizon.max_dist_m
    dem = load_dem(cfg.horizon.dem, cfg.crs, (dtm.x0 - pad, dtm.y0 - pad, dtm.x1 + pad, dtm.y1 + pad),
                   cfg.horizon.dem_res)
    hor = compute_horizon(dem, (dtm.x0, dtm.y0, dtm.x1, dtm.y1), cfg.horizon.grid_res_m, cfg.horizon.az_step_deg,
                          cfg.horizon.min_dist_m, cfg.horizon.max_dist_m, observer_height=cfg.grid.h_cut)
    hor.save(target)
    report = {"horizon_max_deg": float(hor.data.max()), "horizon_mean_deg": float(hor.data.mean())}
    _write_json(cfg.workdir / "horizon.json", report)
    return report


# ------------------------------------------------------------------------------------ forcing

def _period(cfg: SiteConfig):
    if cfg.period is None:
        raise ValueError("config.period (start, end) is required for forcing/trace/irradiance")
    return cfg.period.start, cfg.period.end


def step_forcing(cfg: SiteConfig) -> dict:
    from voxsol import forcing as fz

    dtm = Raster.read(cfg.workdir / "dtm.tif")
    lat, lon, alt = site_location(cfg, dtm)
    start, end = _period(cfg)
    f = cfg.forcing
    if f.radiation == "cams":
        rad = fz.cams(start, end, lat, lon, alt, f.time_step)
    elif f.radiation == "era5":
        rad = fz.era5_radiation(start, end, lat, lon)
    elif f.radiation == "db":
        rad, label_check = fz.db_radiation(cfg.db_dir, start, end, lat, lon, alt, f.time_step,
                                           cfg.database.ghi_label)
    else:
        rad = fz.clearsky(start, end, lat, lon, alt, f.time_step, f.cloudiness, f.seed)
    rad.index.name = "time"
    rad.to_parquet(cfg.workdir / "forcing.parquet")
    report = {"lat": lat, "lon": lon, "altitude": alt, **rad.attrs, "intervals": int(len(rad))}
    if f.radiation == "db":
        report.update(label_check)
    if f.reference == "era5land":
        ref = fz.era5land_reference(start, end, lat, lon, alt, f.reference_grid_elevation, f.lapse_rate)
    elif f.reference == "db":
        ref = fz.db_reference(cfg.db_dir, alt, f.lapse_rate)
    elif f.reference == "file":
        ref = fz.reference_from_file(f.reference_file)
    else:
        ref = None  # computed from the reference loggers in R
    if ref is not None:
        ref.index.name = "time"
        ref.to_parquet(cfg.workdir / "reference.parquet")
        report["reference"] = ref.attrs.get("source")
    _write_json(cfg.workdir / "forcing.json", report)
    return report


def load_forcing(cfg: SiteConfig) -> pd.DataFrame:
    from voxsol.forcing import interval_index

    meta = json.loads((cfg.workdir / "forcing.json").read_text())
    rad = pd.read_parquet(cfg.workdir / "forcing.parquet")
    idx = interval_index(*_period(cfg), cfg.forcing.time_step)
    missing = (~idx.isin(rad.index)).sum()
    if missing:
        _log(f"warning: {missing} forcing intervals missing, set to 0")
    rad = rad.reindex(idx).fillna(0.0)
    rad.attrs.update(step_s=meta["step_s"], source=meta["source"])
    return rad


# ------------------------------------------------------------------------------------ trace

def _horizon(cfg: SiteConfig):
    from voxsol.trace import Horizon

    path = cfg.workdir / "horizon.npz"
    return Horizon.load(path) if path.exists() else None


def logger_points(cfg: SiteConfig, loggers: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Uncertainty-disk sample points (radius 2 sd) and the logger index of every point."""
    from voxsol.trace import disk_points

    pts, owner = [], []
    for i, row in enumerate(loggers.itertuples()):
        n = cfg.trace.logger_disk_n if row.xy_sd > 0 else 1
        pts.append(disk_points(row.x, row.y, 2.0 * row.xy_sd, n))
        owner.append(np.full(n, i))
    return np.vstack(pts), np.concatenate(owner)


def step_trace(cfg: SiteConfig, chunk: int = 2048) -> dict:
    from voxsol.directions import SunBins, sky_directions, slope_weights, sun_positions, unit_vector
    from voxsol.forcing import STEP_S, interval_index, midpoints
    from voxsol.trace import effective_lambda, macro_top, surface_normals, target_heights, trace

    grid = load_grid(cfg)
    macro = macro_top(grid)
    lam = effective_lambda(grid, cfg.voxel.sigma_leaf)
    hor = _horizon(cfg)
    lat, lon, alt = site_location(cfg, grid.dtm)
    idx = interval_index(*_period(cfg), cfg.forcing.time_step)
    sun = sun_positions(midpoints(idx, STEP_S[cfg.forcing.time_step]), lat, lon, alt)
    sky, w_h = sky_directions(cfg.trace.n_sky)
    kw = dict(macro=macro, horizon=hor, lam=lam, t_min=cfg.trace.t_min)
    report: dict = {"sky_dirs": int(len(sky))}

    loggers_csv = cfg.workdir / "loggers.csv"
    loggers = pd.read_csv(loggers_csv) if loggers_csv.exists() else None
    if loggers is None or loggers.empty:
        (cfg.workdir / "logger_trace.npz").unlink(missing_ok=True)  # no stale logger results
    else:
        xy, owner = logger_points(cfg, loggers)
        xyz = target_heights(grid, xy)
        normals = surface_normals(grid.dtm, xy[:, 0], xy[:, 1])
        day = sun["elevation"].to_numpy() > 0
        _log(f"trace loggers: {len(xy)} points x {day.sum()} sun positions")
        T_sun, _ = trace(grid, xyz, unit_vector(sun["azimuth"].to_numpy()[day], sun["elevation"].to_numpy()[day]), **kw)
        T_sky, edge = trace(grid, xyz, sky, **kw)
        np.savez(cfg.workdir / "logger_trace.npz", T_sun=T_sun.astype(np.float32), day=day,
                 T_dif_h=T_sky @ w_h, T_dif_s=(slope_weights(sky, normals) * T_sky).sum(1),
                 normals=normals, owner=owner, xy=xy, edge=(edge * w_h).sum(1))
        report["logger_points"] = int(len(xy))

    tpl = map_template(cfg, grid)
    mask = map_mask(cfg, tpl)
    pix = np.flatnonzero(mask.ravel()) if mask is not None else None
    xs, ys = tpl.centers()
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    if pix is not None:
        xy = xy[pix]
    xyz = target_heights(grid, xy)
    normals = surface_normals(grid.dtm, xy[:, 0], xy[:, 1])
    bins = SunBins(sun["azimuth"].to_numpy(), sun["elevation"].to_numpy(), cfg.trace.sun_bin_deg)
    n_pix = len(xy)
    _log(f"trace map: {n_pix:,} pixels x ({len(bins)} sun bins + {len(sky)} sky directions)")
    T_bins = np.lib.format.open_memmap(cfg.workdir / "map_Tbins.npy", mode="w+", dtype=np.uint8,
                                       shape=(n_pix, len(bins)))
    svf_h = np.empty(n_pix, np.float32)
    svf_s = np.empty(n_pix, np.float32)
    edge_frac = np.empty(n_pix, np.float32)
    for a in range(0, n_pix, chunk):
        b = min(a + chunk, n_pix)
        T, _ = trace(grid, xyz[a:b], bins.dirs, **kw)
        T_bins[a:b] = np.round(T * 255).astype(np.uint8)
        T, edge = trace(grid, xyz[a:b], sky, **kw)
        svf_h[a:b] = T @ w_h
        svf_s[a:b] = (slope_weights(sky, normals[a:b]) * T).sum(1)
        edge_frac[a:b] = (edge * w_h).sum(1)
    T_bins.flush()
    np.savez(cfg.workdir / "map_trace.npz", bin_index=bins.index, bin_az=bins.azimuth, bin_el=bins.elevation,
             normals=normals, svf_h=svf_h, svf_s=svf_s, edge=edge_frac,
             tpl=np.array([tpl.x0, tpl.y0, tpl.res, tpl.nx, tpl.ny]),
             pixels=pix if pix is not None else np.zeros(0, np.int64), all_pixels=pix is None)
    for name, arr in (("svf_h", svf_h), ("svf_s", svf_s), ("edge_fraction", edge_frac)):
        Raster(_full(arr, pix, tpl), tpl.x0, tpl.y0, tpl.res, cfg.crs).write(_maps_dir(cfg) / f"{name}.tif")
    write_terrain_maps(cfg, normals, tpl, pix)
    report.update(map_pixels=int(n_pix), sun_bins=int(len(bins)), edge_fraction_mean=float(edge_frac.mean()))
    _write_json(cfg.workdir / "trace.json", report)
    return report


def write_terrain_maps(cfg: SiteConfig, normals: np.ndarray, tpl: Raster, pix: np.ndarray | None = None) -> None:
    """slope_deg / aspect_deg from the smoothed-DTM normals of the map pixels (also refreshed by irradiance)."""
    from voxsol.trace import slope_aspect

    slope, aspect = slope_aspect(normals)
    for name, arr in (("slope_deg", slope), ("aspect_deg", aspect)):
        Raster(_full(arr, pix, tpl), tpl.x0, tpl.y0, tpl.res, cfg.crs).write(_maps_dir(cfg) / f"{name}.tif")


def traced_pixels(mt) -> np.ndarray | None:
    """Flat indices of the traced map pixels (None = all; map_trace.npz files before the AOI option)."""
    if "pixels" not in mt.files or bool(mt["all_pixels"]):
        return None
    return mt["pixels"]


# ------------------------------------------------------------------------------------ irradiance

def logger_static(cfg: SiteConfig, loggers: pd.DataFrame, lt) -> pd.DataFrame:
    """Static covariates per logger: mean of every map layer over the uncertainty-disk points."""
    rows = {"logger_id": loggers["id"].to_numpy(), "x": loggers["x"].to_numpy(), "y": loggers["y"].to_numpy(),
            "xy_sd": loggers["xy_sd"].to_numpy(), "reference": loggers["reference"].to_numpy()}
    owner, xy = lt["owner"], lt["xy"]
    for tif in sorted(_maps_dir(cfg).glob("*.tif")):
        r = Raster.read(tif)
        v = r.sample(xy[:, 0], xy[:, 1])
        ok = np.isfinite(v)
        num = np.bincount(owner, weights=np.where(ok, v, 0.0), minlength=len(loggers))
        den = np.bincount(owner, weights=ok.astype(float), minlength=len(loggers))
        rows[tif.stem] = np.divide(num, den, out=np.full(len(loggers), np.nan), where=den > 0)
    rows["svf_h_point"] = np.bincount(owner, weights=lt["T_dif_h"]) / np.bincount(owner)
    rows["svf_s_point"] = np.bincount(owner, weights=lt["T_dif_s"]) / np.bincount(owner)
    rows["edge_fraction_point"] = np.bincount(owner, weights=lt["edge"]) / np.bincount(owner)
    return pd.DataFrame(rows)


def write_daily_netcdf(path: Path, maps: dict, days: pd.DatetimeIndex, tpl: Raster, crs: str) -> None:
    import xarray as xr
    from pyproj import CRS

    ny, nx = tpl.ny, tpl.nx
    xs = tpl.x0 + (np.arange(nx) + 0.5) * tpl.res
    ys = (tpl.y0 + (np.arange(ny) + 0.5) * tpl.res)[::-1]  # north-up
    data = {k: (("time", "y", "x"), v.reshape(len(days), ny, nx)[:, ::-1, :],
                {"units": "MJ m-2 d-1" if k.startswith("sw_") else "1", "grid_mapping": "spatial_ref"})
            for k, v in maps.items()}
    wkt = CRS.from_user_input(crs).to_wkt()
    ds = xr.Dataset(data, coords={"time": days.tz_localize(None), "y": ys, "x": xs})
    ds["spatial_ref"] = xr.DataArray(0, attrs={"crs_wkt": wkt, "spatial_ref": wkt,
                                               "GeoTransform": f"{tpl.x0} {tpl.res} 0 {tpl.y1} 0 {-tpl.res}"})
    ds.attrs.update(title="voxsol daily shortwave on top of the understory", Conventions="CF-1.8")
    enc = {k: {"zlib": True, "complevel": 4, "dtype": "float32"} for k in maps}
    ds.to_netcdf(path, encoding=enc)


def step_irradiance(cfg: SiteConfig) -> dict:
    from voxsol.irradiance import daily_maps, daily_site, logger_irradiance, sky_components, sun_weights
    from voxsol.understory import understory_transmittance

    dtm = Raster.read(cfg.workdir / "dtm.tif")
    lat, lon, alt = site_location(cfg, dtm)
    sky = sky_components(load_forcing(cfg), lat, lon, alt)
    site_daily = daily_site(sky)
    site_daily.to_csv(cfg.workdir / "site_daily.csv", index_label="date")
    report: dict = {"days": int(len(site_daily))}
    mt = np.load(cfg.workdir / "map_trace.npz")
    x0, y0, res, nx, ny = mt["tpl"]
    tpl = Raster(np.zeros((int(ny), int(nx)), np.float32), x0, y0, res, cfg.crs)
    pix = traced_pixels(mt)
    write_terrain_maps(cfg, mt["normals"], tpl, pix)  # before logger_static samples maps/*.tif

    lt_path = cfg.workdir / "logger_trace.npz"
    if lt_path.exists():
        lt = np.load(lt_path)
        loggers = pd.read_csv(cfg.workdir / "loggers.csv")
        T_sun = np.zeros((lt["T_sun"].shape[0], len(sky)), np.float32)
        T_sun[:, lt["day"]] = lt["T_sun"]
        series = logger_irradiance(sky, T_sun, lt["T_dif_h"], lt["T_dif_s"], lt["normals"], lt["owner"],
                                   loggers["id"].astype(str).tolist())
        series.to_parquet(cfg.workdir / "logger_irradiance.parquet", index=False)
        logger_static(cfg, loggers, lt).to_csv(cfg.workdir / "logger_static.csv", index=False)
        report["logger_rows"] = int(len(series))
    else:
        for name in ("logger_irradiance.parquet", "logger_static.csv"):
            (cfg.workdir / name).unlink(missing_ok=True)

    T_bins = np.load(cfg.workdir / "map_Tbins.npy", mmap_mode="r")
    W, days = sun_weights(sky, mt["bin_index"], T_bins.shape[1])
    maps = daily_maps(T_bins, W, mt["normals"], mt["svf_h"], mt["svf_s"], site_daily["iso"].to_numpy())
    # uncalibrated first guess at the TMS sensor heights (k is fitted later in R)
    cosz = site_daily["cosz_eff"].fillna(0.5).to_numpy()[:, None]
    for h in cfg.trace.sensor_heights:
        tag = f"{int(round(h * 100))}cm"
        us_path = _maps_dir(cfg) / f"us_index_{tag}.tif"
        if us_path.exists():
            us = Raster.read(us_path).data.ravel()
            us = (us if pix is None else us[pix])[None, :]
            tb, td = understory_transmittance(us, cosz, cfg.trace.understory_k)
            maps[f"sw_ground_{tag}_firstguess"] = (maps["sw_dir_h"] * tb + maps["sw_dif_h"] * td).astype(np.float32)
    mean_tou = float((maps["sw_dir_h"] + maps["sw_dif_h"]).mean())
    if pix is not None:  # traced AOI pixels -> full maps, NaN outside
        for k in list(maps):
            full = np.full((maps[k].shape[0], tpl.ny * tpl.nx), np.nan, np.float32)
            full[:, pix] = maps[k]
            maps[k] = full
    write_daily_netcdf(cfg.workdir / "maps_daily.nc", maps, days, tpl, cfg.crs)
    del maps
    from voxsol.summaries import insolation_summaries

    (cfg.workdir / "sw_tou_h_mean_daily.tif").unlink(missing_ok=True)  # now insolation/sw_tou_h_period.tif
    summaries = insolation_summaries(cfg)
    report.update(map_days=int(len(days)), sw_tou_h_mean=mean_tou, ghi_mean=float(site_daily["ghi"].mean()),
                  insolation_maps=len(summaries))
    _write_json(cfg.workdir / "irradiance.json", report)
    return report


STEPS = {
    "prepare": step_prepare, "voxelize": step_voxelize, "understory": step_understory,
    "horizon": step_horizon, "forcing": step_forcing, "trace": step_trace, "irradiance": step_irradiance,
}
