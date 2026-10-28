"""Combine transmittances with forcing: logger time series and daily maps.

Sky model: Perez (1990) circumsolar diffuse is treated as beam (traced towards the sun), the rest
of the diffuse is isotropic. All irradiances refer to the top of the understory (SW_tou), on the
horizontal plane (_h) and on the local slope plane (_s).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from voxsol.directions import sun_positions, unit_vector
from voxsol.forcing import midpoints

MJ = 1e-6


def sky_components(forcing: pd.DataFrame, lat: float, lon: float, altitude: float = 0.0) -> pd.DataFrame:
    """Sun geometry at interval midpoints plus beam (incl. circumsolar, normal plane) and isotropic diffuse."""
    from pvlib.atmosphere import get_relative_airmass
    from pvlib.irradiance import get_extra_radiation, perez

    step_s = forcing.attrs["step_s"]
    mid = midpoints(forcing.index, step_s)
    sp = sun_positions(mid, lat, lon, altitude)
    zen = sp["zenith"].to_numpy()
    day = sp["elevation"].to_numpy() > 0.0
    cosz = np.where(day, np.cos(np.radians(zen)), 0.0)
    dni = forcing["dni"].to_numpy()
    dhi = forcing["dhi"].to_numpy()
    comps = perez(0.0, 180.0, pd.Series(dhi, mid), pd.Series(dni, mid), get_extra_radiation(mid),
                  sp["zenith"], sp["azimuth"], get_relative_airmass(sp["zenith"]).fillna(38.0),
                  return_components=True)
    circ = np.clip(np.nan_to_num(np.asarray(comps["poa_circumsolar"], dtype=float)), 0.0, dhi)
    circ = np.where(day, circ, 0.0)
    beam_n = np.where(cosz > 0.01, dni + circ / np.maximum(cosz, 0.01), 0.0)
    vec = unit_vector(sp["azimuth"].to_numpy(), sp["elevation"].to_numpy())
    out = pd.DataFrame({
        "ghi": forcing["ghi"].to_numpy(), "dni": dni, "dhi": dhi, "zenith": zen,
        "azimuth": sp["azimuth"].to_numpy(), "elevation": sp["elevation"].to_numpy(), "cosz": cosz,
        "sx": vec[:, 0], "sy": vec[:, 1], "sz": vec[:, 2], "beam_n": beam_n,
        "iso": np.where(day, dhi - circ, dhi),
    }, index=forcing.index)
    out.attrs.update(forcing.attrs)
    return out


def logger_irradiance(sky: pd.DataFrame, T_sun: np.ndarray, T_dif_h: np.ndarray, T_dif_s: np.ndarray,
                      normals: np.ndarray, point_logger: np.ndarray, logger_ids: list[str]) -> pd.DataFrame:
    """Per-logger series (mean over its uncertainty-disk points; sd of SW_tou_h across the disk).

    T_sun is (n_points, n_times) towards the exact sun position of every forcing interval (0 at night).
    """
    beam = sky["beam_n"].to_numpy()
    s = sky[["sx", "sy", "sz"]].to_numpy()
    iso = sky["iso"].to_numpy()
    dir_h = T_sun * (beam * sky["cosz"].to_numpy())[None, :]
    dir_s = T_sun * beam[None, :] * np.clip(normals @ s.T, 0.0, None)
    dif_h = T_dif_h[:, None] * iso[None, :]
    dif_s = T_dif_s[:, None] * iso[None, :]
    frames = []
    for i, lid in enumerate(logger_ids):
        sel = point_logger == i
        tou_h = dir_h[sel] + dif_h[sel]
        f = pd.DataFrame({
            "logger_id": lid, "time": sky.index, "ghi": sky["ghi"].to_numpy(),
            "sw_dir_h": dir_h[sel].mean(0), "sw_dif_h": dif_h[sel].mean(0),
            "sw_dir_s": dir_s[sel].mean(0), "sw_dif_s": dif_s[sel].mean(0),
            "sw_tou_h_sd": tou_h.std(0), "zenith": sky["zenith"].to_numpy(), "cosz": sky["cosz"].to_numpy(),
            "beam_n": beam, "iso": iso,
        })
        frames.append(f)
    out = pd.concat(frames, ignore_index=True)
    out["sw_tou_h"] = out["sw_dir_h"] + out["sw_dif_h"]
    out["sw_tou_s"] = out["sw_dir_s"] + out["sw_dif_s"]
    with np.errstate(divide="ignore", invalid="ignore"):
        out["trans_h"] = np.where(out["ghi"] > 10, out["sw_tou_h"] / out["ghi"], np.nan)
    return out


def day_index(index: pd.DatetimeIndex) -> tuple[np.ndarray, pd.DatetimeIndex]:
    days = index.normalize()
    uniq = days.unique()
    return np.searchsorted(uniq, days), uniq


def sun_weights(sky: pd.DataFrame, bin_index: np.ndarray, n_bins: int) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """W[bin, day, xyz] = sum over intervals of beam_n * dt * sun vector [J/m2].

    The daily direct sum on a surface with unit normal n is sum_b T_b * (n . W[b, day]).
    """
    d_idx, days = day_index(sky.index)
    e = sky["beam_n"].to_numpy() * sky.attrs["step_s"]
    vec = sky[["sx", "sy", "sz"]].to_numpy()
    ok = (bin_index >= 0) & (e > 0)
    W = np.zeros((n_bins, days.size, 3))
    np.add.at(W, (bin_index[ok], d_idx[ok]), e[ok, None] * vec[ok])
    return W, days


def daily_site(sky: pd.DataFrame) -> pd.DataFrame:
    """Site-wide daily sums [MJ/m2] and the beam-energy-weighted effective cos(zenith)."""
    step = sky.attrs["step_s"]
    d_idx, days = day_index(sky.index)
    beam_h = sky["beam_n"] * sky["cosz"] * step
    df = pd.DataFrame({
        "ghi": np.bincount(d_idx, sky["ghi"] * step) * MJ,
        "iso": np.bincount(d_idx, sky["iso"] * step) * MJ,
        "beam_h": np.bincount(d_idx, beam_h) * MJ,
    }, index=days)
    num = np.bincount(d_idx, beam_h * sky["cosz"])
    den = np.bincount(d_idx, beam_h)
    df["cosz_eff"] = np.where(den > 0, num / np.maximum(den, 1e-12), np.nan)
    return df


def daily_maps(T_bins: np.ndarray, W: np.ndarray, normals: np.ndarray, T_dif_h: np.ndarray,
               T_dif_s: np.ndarray, iso_daily: np.ndarray, chunk: int = 8192) -> dict[str, np.ndarray]:
    """Daily SW_tou components (n_days, n_pix) [MJ/m2]; T_bins may be uint8 (T * 255)."""
    n_pix, n_bins = T_bins.shape
    n_days = W.shape[1]
    W2 = W.reshape(n_bins, n_days * 3).astype(np.float32)
    scale = np.float32(1 / 255) if T_bins.dtype == np.uint8 else np.float32(1)
    dir_h = np.empty((n_days, n_pix), np.float32)
    dir_s = np.empty((n_days, n_pix), np.float32)
    for a in range(0, n_pix, chunk):
        b = min(a + chunk, n_pix)
        R = (np.asarray(T_bins[a:b], dtype=np.float32) * scale) @ W2
        R = R.reshape(b - a, n_days, 3)
        dir_h[:, a:b] = R[:, :, 2].T * MJ
        dir_s[:, a:b] = np.clip(np.einsum("ck,cdk->dc", normals[a:b].astype(np.float32), R), 0, None) * MJ
    return {"sw_dir_h": dir_h, "sw_dir_s": dir_s,
            "sw_dif_h": (iso_daily[:, None] * T_dif_h[None, :]).astype(np.float32),
            "sw_dif_s": (iso_daily[:, None] * T_dif_s[None, :]).astype(np.float32)}
