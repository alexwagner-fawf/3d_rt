"""Synthetic disturbed-forest scene, ULS (DJI-L2-like) simulation and data writers.

The true scene is a terrain-following extinction field from the ground up (h0 = 0, fine dz) with a
material code per voxel. Pulses are split into sub-beams with exponential free paths; sub-beam hits
closer than `sep` along the range merge into one echo (mimicking weak return separation in low
vegetation). Used by the tests and the end-to-end demo (`voxsol synth`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import laspy
import numpy as np
import pandas as pd
from numba import njit, prange
from pyproj import CRS as ProjCRS
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

from voxsol.grid import Raster, VoxelGrid
from voxsol.io.trajectory import ADJUSTED_GPS_OFFSET, GPS_WEEK_S, SBET_FIELDS
from voxsol.raymarch import bilinear

CRS = "EPSG:25832"
X0, Y0, Z0 = 600_000.0, 5_600_000.0, 500.0
GPS_WEEK = 2400

NONE, GREEN, DEAD, HERB, DRY = 0, 1, 2, 3, 4
GROUND = 255
COLORS = {NONE: (0, 0, 0), GREEN: (55, 105, 45), DEAD: (135, 128, 120), HERB: (95, 150, 60),
          DRY: (165, 150, 100), GROUND: (110, 90, 70)}


@dataclass
class Scene:
    truth: VoxelGrid          # true extinction, h0 = 0
    material: np.ndarray      # (ny, nx, nz) uint8 material codes
    us_height: np.ndarray     # (ny, nx) true understory height [m]
    us_lambda: np.ndarray     # (ny, nx) true understory extinction [1/m]


def _smooth_field(rng, shape, sigma):
    f = gaussian_filter(rng.normal(size=shape), sigma=sigma, mode="wrap")
    return (f - f.mean()) / (f.std() + 1e-12)


def make_scene(size: float = 100.0, dx: float = 0.25, dz: float = 0.1, zmax: float = 30.0,
               slope_deg: float = 12.0, n_trees: int = 10, n_snags: int = 5, n_saplings: int = 25,
               seed: int = 1) -> Scene:
    rng = np.random.default_rng(seed)
    n = int(round(size / dx))
    dtm = Raster(np.zeros((n, n)), X0, Y0, dx, CRS)
    xs, ys = dtm.centers()
    dtm.data = (Z0 + (ys - Y0) * np.tan(np.radians(slope_deg))
                + 1.5 * np.sin(2 * np.pi * (xs - X0) / 40.0) * np.sin(2 * np.pi * (ys - Y0) / 55.0))
    truth = VoxelGrid.over_dtm(dtm, dx, 0.0, dz, zmax)
    lam = truth.empty()
    mat = np.zeros(lam.shape, np.uint8)
    hz = truth.layer_heights()
    cx, cy = truth.column_centers()

    def paint(x, y, radius, fn):
        i0 = max(int((y - Y0 - radius) / dx), 0)
        i1 = min(int((y - Y0 + radius) / dx) + 1, truth.ny)
        j0 = max(int((x - X0 - radius) / dx), 0)
        j1 = min(int((x - X0 + radius) / dx) + 1, truth.nx)
        d = np.hypot(cx[i0:i1, j0:j1] - x, cy[i0:i1, j0:j1] - y)[:, :, None]
        fn(lam[i0:i1, j0:j1], mat[i0:i1, j0:j1], d, hz[None, None, :])

    margin = 8.0
    for _ in range(n_trees):  # spruce-like cones with an opaque trunk
        x, y = X0 + rng.uniform(margin, size - margin), Y0 + rng.uniform(margin, size - margin)
        height, base, radius, dens = rng.uniform(16, 26), rng.uniform(3, 6), rng.uniform(2.0, 3.2), rng.uniform(0.8, 1.3)

        def tree(L, M, d, h, height=height, base=base, radius=radius, dens=dens):
            crown = (h >= base) & (h < height) & (d < radius * (height - h) / (height - base))
            trunk = (d < 0.2) & (h < height)
            L[crown] = np.maximum(L[crown], dens)
            M[crown] = GREEN
            L[trunk] = 20.0
            M[trunk] = DEAD

        paint(x, y, radius + 0.5, tree)
    for _ in range(n_snags):  # dead standing trees: trunk plus sparse dead branches
        x, y = X0 + rng.uniform(margin, size - margin), Y0 + rng.uniform(margin, size - margin)
        height = rng.uniform(10, 20)

        def snag(L, M, d, h, height=height):
            branches = (d < 1.5) & (h > 5) & (h < height)
            trunk = (d < 0.2) & (h < height)
            L[branches] = np.maximum(L[branches], 0.15)
            M[branches & (M == NONE)] = DEAD
            L[trunk] = 20.0
            M[trunk] = DEAD

        paint(x, y, 2.0, snag)
    for _ in range(n_saplings):  # regrowth 1.5-3.5 m
        x, y = X0 + rng.uniform(2, size - 2), Y0 + rng.uniform(2, size - 2)
        height, radius = rng.uniform(1.5, 3.5), rng.uniform(0.6, 1.2)

        def sapling(L, M, d, h, height=height, radius=radius):
            crown = (h >= 0.3) & (h < height) & (d < radius * (height - h) / (height - 0.3))
            L[crown] = np.maximum(L[crown], 1.5)
            M[crown] = GREEN

        paint(x, y, radius + 0.5, sapling)
    # understory: smooth height field 0..0.95 m with bare patches, dense herbs
    hfield = _smooth_field(rng, (truth.ny, truth.nx), 6.0 / dx)
    us_h = np.clip(0.55 + 0.3 * hfield, 0.0, 0.95)
    us_h[us_h < 0.15] = 0.0
    us_lam = np.clip(3.5 + 1.2 * _smooth_field(rng, (truth.ny, truth.nx), 4.0 / dx), 1.0, 7.0)
    dry = _smooth_field(rng, (truth.ny, truth.nx), 5.0 / dx) > 0.8
    herb = hz[None, None, :] < us_h[:, :, None]
    upd = herb & (mat == NONE)
    lam[upd] = np.broadcast_to(us_lam[:, :, None], lam.shape)[upd]
    mat[upd] = np.where(np.broadcast_to(dry[:, :, None], lam.shape)[upd], DRY, HERB)
    truth.lam = lam
    return Scene(truth, mat, us_h, us_lam)


# --------------------------------------------------------------------------------------------
# ULS simulation
# --------------------------------------------------------------------------------------------

@dataclass
class FlightPlan:
    altitude: float = 80.0       # m above the mean terrain
    speed: float = 6.0           # m/s
    line_spacing: float = 25.0   # m
    pulse_rate: float = 20_000.0 # Hz (scaled down from the L2 for test speed)
    fov_deg: float = 35.0        # +- across track
    t0_sow: float = 300_000.0    # GPS seconds of week at the first pulse
    overshoot: float = 15.0      # m beyond the scene at each line end
    turn_s: float = 10.0         # s between lines


@njit(cache=True, inline="always")
def _rand01(seed, p, k):
    """splitmix64 hash -> uniform (0, 1); deterministic per (seed, pulse, sub-beam)."""
    z = np.uint64(seed) * np.uint64(0x9E3779B97F4A7C15) + np.uint64(p) * np.uint64(0xBF58476D1CE4E5B9) \
        + np.uint64(k + 1) * np.uint64(0x94D049BB133111EB)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    z = z ^ (z >> np.uint64(31))
    return (float(z >> np.uint64(11)) + 0.5) / 9007199254740992.0


@njit(cache=True, parallel=True)
def _simulate(origin, direc, lam, mat, gx0, gy0, dx, dz, dtm, tx0, ty0, tres, z_top,
              n_sub, sep, seed, ds, max_echo):
    P = origin.shape[0]
    ny, nx, nz = lam.shape
    out_s = np.full((P, max_echo), np.nan)
    out_w = np.zeros((P, max_echo))
    out_m = np.zeros((P, max_echo), np.uint8)
    out_n = np.zeros(P, np.int64)
    xmax = gx0 + nx * dx
    ymax = gy0 + ny * dx
    for p in prange(P):
        ox, oy, oz = origin[p, 0], origin[p, 1], origin[p, 2]
        ux, uy, uz = direc[p, 0], direc[p, 1], direc[p, 2]
        thr = np.empty(n_sub)
        for k in range(n_sub):
            thr[k] = -math.log(_rand01(seed, p, k))
        thr.sort()
        hit_s = np.empty(n_sub)
        hit_m = np.zeros(n_sub, np.uint8)
        nh = 0
        s = max((oz - z_top) / -uz, 0.0)
        tau = 0.0
        ground_s = -1.0
        while nh < n_sub:
            sm = s + 0.5 * ds
            x = ox + sm * ux
            y = oy + sm * uy
            if x < gx0 or x >= xmax or y < gy0 or y >= ymax:
                break
            hag = oz + sm * uz - bilinear(dtm, tx0, ty0, tres, x, y)
            if hag < 0.0:
                ground_s = s
                break
            k = int(hag / dz)
            if k < nz:
                ix = int((x - gx0) / dx)
                iy = int((y - gy0) / dx)
                lv = lam[iy, ix, k]
                if lv > 0.0:
                    tau += lv * ds
                    while nh < n_sub and thr[nh] <= tau:
                        hit_s[nh] = sm
                        hit_m[nh] = mat[iy, ix, k]
                        nh += 1
            s += ds
        if nh == 0 and ground_s < 0.0:
            continue  # beam left the scene
        n_all = nh
        if ground_s >= 0.0:
            for k in range(nh, n_sub):
                hit_s[k] = ground_s
                hit_m[k] = 255
            n_all = n_sub
        # merge sub-beam hits closer than `sep` along the range into echoes
        ne = 0
        g0 = 0
        while g0 < n_all and ne < max_echo:
            g1 = g0 + 1
            while g1 < n_all and hit_s[g1] - hit_s[g0] < sep:
                g1 += 1
            ssum = 0.0
            nground = 0
            for k in range(g0, g1):
                ssum += hit_s[k]
                if hit_m[k] == 255:
                    nground += 1
            out_s[p, ne] = ssum / (g1 - g0)
            out_w[p, ne] = (g1 - g0) / n_sub
            out_m[p, ne] = 255 if 2 * nground >= (g1 - g0) else hit_m[g0]
            ne += 1
            g0 = g1
        out_n[p] = ne
    return out_s, out_w, out_m, out_n


def flight_pulses(scene: Scene, plan: FlightPlan) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """Pulse times (seconds of week), origins, directions and the trajectory (sow, x, y, z)."""
    g = scene.truth
    size_x, size_y = g.nx * g.dx, g.ny * g.dx
    z_fly = float(np.mean(g.dtm.data)) + plan.altitude
    lines = np.arange(plan.line_spacing / 2, size_y, plan.line_spacing)
    t, org, dirs, traj = [], [], [], []
    t_line = plan.t0_sow
    for i, ly in enumerate(lines):
        length = size_x + 2 * plan.overshoot
        dur = length / plan.speed
        tt = t_line + np.arange(int(dur * plan.pulse_rate)) / plan.pulse_rate
        sign = 1.0 if i % 2 == 0 else -1.0
        xstart = X0 - plan.overshoot if sign > 0 else X0 + size_x + plan.overshoot
        x = xstart + sign * plan.speed * (tt - t_line)
        o = np.column_stack([x, np.full_like(x, Y0 + ly), np.full_like(x, z_fly)])
        # rotating-mirror-like across-track sweep (deterministic, dense)
        ang = np.radians(plan.fov_deg) * np.sin(2 * np.pi * 37.0 * (tt - t_line) + 0.3 * np.arange(tt.size))
        d = np.column_stack([np.zeros_like(ang), np.sin(ang) * sign, -np.cos(ang)])
        t.append(tt)
        org.append(o)
        dirs.append(d)
        tj = np.arange(t_line - 1.0, t_line + dur + 1.0, 1.0 / 200.0)
        xj = xstart + sign * plan.speed * (tj - t_line)
        traj.append(pd.DataFrame({"time": tj, "x": xj, "y": Y0 + ly, "z": z_fly}))
        t_line += dur + plan.turn_s
    return (np.concatenate(t), np.vstack(org), np.vstack(dirs), pd.concat(traj, ignore_index=True))


def simulate_uls(scene: Scene, plan: FlightPlan = FlightPlan(), n_sub: int = 8, sep: float = 1.0,
                 seed: int = 0, range_noise: float = 0.02, max_echo: int = 5) -> dict:
    """Simulated point cloud (LAS-ready arrays) and trajectory."""
    t, origin, direc, traj = flight_pulses(scene, plan)
    g = scene.truth
    z_top = float(g.dtm.data.max()) + g.htop
    s, w, m, ne = _simulate(origin, direc, g.lam, scene.material, g.x0, g.y0, g.dx, g.dz,
                            g.dtm.data, g.dtm.x0, g.dtm.y0, g.dtm.res, z_top, n_sub, sep, seed,
                            0.5 * g.dz, max_echo)
    rows, cols = np.nonzero(np.arange(max_echo)[None, :] < ne[:, None])
    rng = np.random.default_rng(seed + 1)
    rr = s[rows, cols] + rng.normal(0, range_noise, rows.size)
    xyz = origin[rows] + rr[:, None] * direc[rows]
    mats = m[rows, cols]
    lut = np.zeros((256, 3))
    for code, col in COLORS.items():
        lut[code] = col
    color = np.clip(lut[mats] * rng.normal(1.0, 0.06, (rows.size, 1)), 0, 255) * 256
    return {
        "xyz": xyz, "gps_time_sow": t[rows], "return_number": cols + 1, "number_of_returns": ne[rows],
        "classification": np.where(mats == GROUND, 2, 1).astype(np.uint8), "rgb": color.astype(np.uint16),
        "scan_angle": np.degrees(np.arcsin(direc[rows, 1])), "material": mats, "echo_weight": w[rows, cols],
        "trajectory": traj, "pulses_emitted": int(t.size), "pulses_returned": int((ne > 0).sum()),
    }


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------

def write_las(path: Path, sim: dict) -> None:
    header = laspy.LasHeader(point_format=7, version="1.4")
    header.scales = [0.001, 0.001, 0.001]
    header.offsets = [X0, Y0, Z0]
    header.global_encoding.gps_time_type = laspy.header.GpsTimeType.STANDARD
    header.add_crs(ProjCRS.from_user_input(CRS))
    las = laspy.LasData(header)
    xyz = sim["xyz"]
    las.x, las.y, las.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    las.gps_time = GPS_WEEK * GPS_WEEK_S + sim["gps_time_sow"] - ADJUSTED_GPS_OFFSET
    las.return_number = sim["return_number"]
    las.number_of_returns = sim["number_of_returns"]
    las.scan_angle = np.round(sim["scan_angle"] / 0.006).astype(np.int16)
    las.classification = sim["classification"]
    las.red, las.green, las.blue = sim["rgb"][:, 0], sim["rgb"][:, 1], sim["rgb"][:, 2]
    las.write(path)


def write_sbet(path: Path, traj: pd.DataFrame) -> None:
    to_geo = Transformer.from_crs(CRS, "EPSG:4979", always_xy=True)
    lon, lat, h = to_geo.transform(traj["x"].to_numpy(), traj["y"].to_numpy(), traj["z"].to_numpy())
    rec = np.zeros((len(traj), len(SBET_FIELDS)))
    rec[:, 0], rec[:, 1], rec[:, 2], rec[:, 3] = traj["time"], np.radians(lat), np.radians(lon), h
    rec.astype("<f8").tofile(path)


def top_surface(scene: Scene, threshold: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
    """Height above ground and material of the topmost visible voxel per column (0/GROUND if none)."""
    occ = scene.truth.lam > threshold
    any_occ = occ.any(axis=2)
    k = scene.truth.nz - 1 - np.argmax(occ[:, :, ::-1], axis=2)
    top_h = np.where(any_occ, (k + 1) * scene.truth.dz, 0.0)
    top_m = np.where(any_occ, np.take_along_axis(scene.material, k[:, :, None], axis=2)[:, :, 0], GROUND)
    return top_h, top_m.astype(np.uint8)


def render_ortho(path: Path, scene: Scene, sun_azimuth: float, sun_elevation: float) -> None:
    """Nadir RGB orthomosaic at the grid resolution, with cast shadows for the given sun position."""
    from voxsol.directions import unit_vector
    from voxsol.trace import trace

    g = scene.truth
    top_h, top_m = top_surface(scene)
    xs, ys = g.column_centers()
    xyz = np.column_stack([xs.ravel(), ys.ravel(), (g.dtm.sample(xs.ravel(), ys.ravel()) + top_h.ravel() + 0.05)])
    T, _ = trace(g, xyz, unit_vector([sun_azimuth], [sun_elevation]))
    shade = (0.35 + 0.65 * T[:, 0]).reshape(top_h.shape)
    lut = np.zeros((256, 3))
    for code, col in COLORS.items():
        lut[code] = col
    color = lut[top_m]
    herb = (top_m == HERB) | (top_m == DRY)
    gap = np.exp(-0.5 * scene.us_lambda * scene.us_height)[:, :, None]  # vertical gap, G = 0.5
    color = np.where(herb[:, :, None], gap * lut[GROUND] + (1 - gap) * color, color)
    rgb = np.clip(color * shade[:, :, None], 0, 255).astype(np.uint8)
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", height=g.ny, width=g.nx, count=3, dtype="uint8", crs=CRS,
                       transform=from_origin(g.x0, g.y0 + g.ny * g.dx, g.dx, g.dx)) as dst:
        for b in range(3):
            dst.write(np.flipud(rgb[:, :, b]), b + 1)


# --------------------------------------------------------------------------------------------
# Demo site: point cloud, trajectory, ortho, TMS-4 long table, reference temperatures, config
# --------------------------------------------------------------------------------------------

def _ema(x: np.ndarray, step_s: float, tau_s: float) -> np.ndarray:
    a = 1.0 - np.exp(-step_s / tau_s)
    out = np.empty_like(x)
    acc = x[..., 0]
    for i in range(x.shape[-1]):
        acc = acc + a * (x[..., i] - acc)
        out[..., i] = acc
    return out


def _ar1(rng, shape, sigma, rho):
    e = rng.normal(0, sigma * np.sqrt(1 - rho**2), shape)
    out = np.empty(shape)
    out[..., 0] = rng.normal(0, sigma, shape[:-1])
    for i in range(1, shape[-1]):
        out[..., i] = rho * out[..., i - 1] + e[..., i]
    return out


def true_sensor_irradiance(scene: Scene, sky: pd.DataFrame, xy: np.ndarray, height: float) -> np.ndarray:
    """Horizontal shortwave at `height` above ground through the full true scene (incl. herbs)."""
    from voxsol.directions import sky_directions, unit_vector
    from voxsol.trace import target_heights, trace

    g = scene.truth
    xyz = target_heights(g, xy, height)
    day = sky["elevation"].to_numpy() > 0
    T_sun = np.zeros((len(xy), len(sky)), np.float32)
    T_sun[:, day], _ = trace(g, xyz, unit_vector(sky["azimuth"].to_numpy()[day], sky["elevation"].to_numpy()[day]))
    dirs, w = sky_directions(400)
    T_sky, _ = trace(g, xyz, dirs)
    return (T_sun * (sky["beam_n"] * sky["cosz"]).to_numpy()[None, :]
            + (T_sky @ w)[:, None] * sky["iso"].to_numpy()[None, :])


def make_demo_site(out: Path, config_path: Path, size: float = 100.0, n_loggers: int = 24, seed: int = 1,
                   start: str = "2025-05-01", end: str = "2025-09-30", time_step: str = "15min",
                   cloudiness: float = 0.3, pulse_rate: float = 20_000.0) -> dict:
    """Write a complete synthetic site and its YAML config. The temperature model is a toy that
    links logger temperatures to the *true* radiation at sensor height (k_true via the herb layer)."""
    import yaml

    from voxsol.directions import sun_positions
    from voxsol.forcing import clearsky
    from voxsol.irradiance import sky_components

    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    scene = make_scene(size=size, seed=seed)
    sim = simulate_uls(scene, FlightPlan(pulse_rate=pulse_rate), seed=seed)
    write_las(out / "cloud.laz", sim)
    write_sbet(out / "sbet.out", sim["trajectory"])
    g = scene.truth
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(X0 + size / 2, Y0 + size / 2)
    alt = float(g.dtm.data.mean())
    ortho_time = pd.Timestamp("2025-07-15 09:30", tz="UTC")
    sp = sun_positions(pd.DatetimeIndex([ortho_time]), lat, lon, alt)
    render_ortho(out / "ortho.tif", scene, float(sp["azimuth"].iloc[0]), float(sp["elevation"].iloc[0]))

    # logger positions: random interior points plus two open-area reference loggers
    buffer = 15.0
    top_h, _ = top_surface(scene)
    cand = rng.uniform(buffer + 2, size - buffer - 2, (4000, 2))
    ci = (cand / g.dx).astype(int)
    open_mask = (top_h[ci[:, 1], ci[:, 0]] < 0.3)
    refs = cand[open_mask][:2]
    others = cand[~np.isin(np.arange(len(cand)), np.flatnonzero(open_mask)[:2])][: n_loggers - 2]
    true_xy = np.vstack([refs, others]) + np.array([X0, Y0])
    xy_sd = np.r_[[0.02, 0.02], rng.uniform(0.02, 0.6, len(others))]
    meas_xy = true_xy + rng.normal(0, 1, true_xy.shape) * xy_sd[:, None] / np.sqrt(2)

    forcing = clearsky(start, end, lat, lon, alt, time_step, cloudiness, seed)
    sky = sky_components(forcing, lat, lon, alt)
    step = forcing.attrs["step_s"]
    sw15 = true_sensor_irradiance(scene, sky, true_xy, 0.15)
    sw02 = true_sensor_irradiance(scene, sky, true_xy, 0.02)

    # macroclimate reference (toy): seasonal + diurnal cycle damped by clouds + weather noise
    t = sky.index + pd.Timedelta(seconds=step / 2)
    doy = t.day_of_year.to_numpy()
    hour = (t.hour + t.minute / 60.0).to_numpy() + 1.0  # ~local solar time
    cs = clearsky(start, end, lat, lon, alt, time_step, 0.0, seed)["ghi"].to_numpy()
    clear_frac = pd.Series(np.where(cs > 50, forcing["ghi"] / np.maximum(cs, 1), np.nan), index=t) \
        .resample("D").mean().reindex(t.normalize()).fillna(0.7).to_numpy()
    weather = gaussian_filter(rng.normal(0, 1, len(t)), 96 * 2) * 25
    t2m = 13 + 5 * np.sin(2 * np.pi * (doy - 110) / 365) + (2 + 4 * clear_frac) * np.sin(2 * np.pi * (hour - 9) / 24) + weather
    skt = t2m + 0.004 * forcing["ghi"].to_numpy()
    stl1 = _ema(t2m, step, 12 * 3600.0)
    rain = (gaussian_filter(rng.random(len(t)) < 0.002, 8).astype(float) * 40).clip(0, 1)
    vwc_t = 0.22 + 0.1 * _ema(rain, step, 4 * 86400.0) - 0.04 * (doy - doy[0]) / (doy[-1] - doy[0] + 1)
    vwc_base = rng.uniform(-0.06, 0.12, len(true_xy))
    vwc = np.clip(vwc_t[None, :] + vwc_base[:, None], 0.05, 0.5)

    n = len(true_xy)
    dT3 = 0.010 * _ema(sw15, step, 1200.0) * (1 - 1.2 * (vwc - 0.2)) + _ar1(rng, (n, len(t)), 0.25, 0.9)
    dT2 = 0.020 * _ema(sw02, step, 1800.0) * (1 - 1.5 * (vwc - 0.2)) + _ar1(rng, (n, len(t)), 0.35, 0.9)
    dT1 = 0.006 * _ema(sw02, step, 6 * 3600.0) + _ar1(rng, (n, len(t)), 0.15, 0.98)
    series = {"15": t2m[None, :] + dT3, "2": t2m[None, :] + dT2, "-8": stl1[None, :] + dT1, "-0-10cm": vwc}
    ts = sky.index.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")
    frames = []
    for height, arr in series.items():
        frames.append(pd.DataFrame({
            "x": np.repeat(np.round(meas_xy[:, 0], 3), len(ts)), "y": np.repeat(np.round(meas_xy[:, 1], 3), len(ts)),
            "timestamp": np.tile(ts, n), "value": np.round(arr.ravel(), 3 if height == "-0-10cm" else 2),
            "height": height}))
    pd.concat(frames, ignore_index=True).to_csv(out / "tms_long.csv", index=False)
    ref = pd.DataFrame({"time": t - pd.Timedelta(seconds=step / 2), "t2m": t2m, "skt": skt, "stl1": stl1})
    ref = ref.set_index("time").resample("1h").mean().reset_index()
    ref["time"] = ref["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    ref.round(3).to_csv(out / "reference.csv", index=False)
    pd.DataFrame({"x": meas_xy[:, 0], "y": meas_xy[:, 1], "xy_sd": xy_sd,
                  "reference": [True, True] + [False] * (n - 2)}).to_csv(out / "loggers_meta.csv", index=False)
    pd.DataFrame({"x": true_xy[:, 0], "y": true_xy[:, 1], "x_meas": meas_xy[:, 0], "y_meas": meas_xy[:, 1],
                  "sw15_mean": sw15.mean(1), "sw02_mean": sw02.mean(1), "vwc_base": vwc_base}) \
        .to_csv(out / "truth_loggers.csv", index=False)

    cfg = {
        "site": "synthetic", "crs": CRS, "vertical_crs": None,
        "inputs": {"laz": [str(out / "cloud.laz")], "trajectory": str(out / "sbet.out"),
                   "ortho": str(out / "ortho.tif"), "ortho_time": ortho_time.isoformat(),
                   "loggers": str(out / "loggers_meta.csv"), "tms": str(out / "tms_long.csv")},
        "period": {"start": start, "end": end}, "buffer_m": buffer,
        "grid": {"dx": 0.25, "dz": 0.25, "zmax": 30.0, "h_cut": 1.0, "dtm_res": 0.25},
        "prepare": {"ground": "csf", "sor": True, "csf_rigidness": 2},
        "forcing": {"radiation": "clearsky", "cloudiness": cloudiness, "seed": seed, "time_step": time_step,
                    "reference": "file", "reference_file": str(out / "reference.csv")},
    }
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as fh:
        fh.write("# Synthetic demo site generated by `voxsol synth` (all paths inside the repo)\n")
        yaml.safe_dump(cfg, fh, sort_keys=False)
    return {"points": int(len(sim["xyz"])), "pulses": sim["pulses_emitted"], "loggers": n,
            "config": str(config_path), "out": str(out)}
