"""Direction sets: diffuse sky directions and sun-path bins. Vectors are (east, north, up).

Azimuth is clockwise from north in degrees (pvlib convention), elevation above the horizontal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GOLDEN_ANGLE = np.pi * (3.0 - np.sqrt(5.0))


def unit_vector(azimuth_deg, elevation_deg) -> np.ndarray:
    az = np.radians(np.asarray(azimuth_deg, dtype=np.float64))
    el = np.radians(np.asarray(elevation_deg, dtype=np.float64))
    return np.stack([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)], axis=-1)


def sky_directions(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Fibonacci hemisphere, equal solid angle 2*pi/n per direction.

    Returns directions (n, 3) and weights for isotropic diffuse irradiance on a horizontal
    surface, w_i = cos(zenith_i) * (2*pi/n) / pi, which sum to exactly 1 (sky view factor).
    """
    i = np.arange(n)
    uz = 1.0 - (i + 0.5) / n
    r = np.sqrt(1.0 - uz**2)
    phi = i * GOLDEN_ANGLE
    dirs = np.column_stack([r * np.sin(phi), r * np.cos(phi), uz])
    return dirs, 2.0 * uz / n


def slope_weights(dirs: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Isotropic-sky weights for tilted surfaces: w_ij = max(0, n_i . d_j) * 2/n.

    `normals` is (k, 3); for an unobstructed surface of tilt b the weights sum to (1 + cos b)/2.
    """
    n = dirs.shape[0]
    return np.clip(np.atleast_2d(normals) @ dirs.T, 0.0, None) * (2.0 / n)


def sun_positions(times: pd.DatetimeIndex, lat: float, lon: float, altitude: float = 0.0) -> pd.DataFrame:
    """Apparent sun position (pvlib NREL SPA): columns azimuth, elevation, zenith [deg]."""
    from pvlib.solarposition import get_solarposition

    sp = get_solarposition(times, lat, lon, altitude=altitude)
    return pd.DataFrame({"azimuth": sp["azimuth"], "elevation": sp["apparent_elevation"],
                         "zenith": sp["apparent_zenith"]}, index=times)


class SunBins:
    """Regular azimuth/elevation cells visited by the sun; the cell center is the trace direction."""

    def __init__(self, azimuth: np.ndarray, elevation: np.ndarray, bin_deg: float):
        az = np.asarray(azimuth, dtype=np.float64) % 360.0
        el = np.asarray(elevation, dtype=np.float64)
        day = el > 0.0
        n_az = int(round(360.0 / bin_deg))
        ia = np.floor(az / bin_deg).astype(np.int64) % n_az
        ie = np.floor(np.clip(el, 0, 90 - 1e-9) / bin_deg).astype(np.int64)
        key = ie * n_az + ia
        keys = np.unique(key[day])
        self.bin_deg = bin_deg
        self.keys = keys
        self.index = np.full(az.shape, -1, dtype=np.int64)
        self.index[day] = np.searchsorted(keys, key[day])
        self.azimuth = ((keys % n_az) + 0.5) * bin_deg
        self.elevation = ((keys // n_az) + 0.5) * bin_deg
        self.dirs = unit_vector(self.azimuth, self.elevation)

    def __len__(self) -> int:
        return self.keys.size
