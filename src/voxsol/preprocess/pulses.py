"""Pulse reconstruction: group returns by GPS time and attach the scanner position."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np

VERTICAL_ORIGIN_HEIGHT = 500.0  # m above the last return for the no-trajectory fallback


@dataclass
class Pulses:
    origin: np.ndarray       # (P, 3) scanner position
    direc: np.ndarray        # (P, 3) unit vector towards the last return
    echo_ptr: np.ndarray     # (P + 1,) CSR offsets into the echo arrays
    echo_s: np.ndarray       # (E,) distance from the origin along direc
    echo_w: np.ndarray       # (E,) beam fraction carried by the echo
    echo_canopy: np.ndarray  # (E,) echo counts as a canopy interception (hag >= h_cut, not ground)
    last_ground: np.ndarray  # (P,) last echo is a ground return

    def __len__(self) -> int:
        return self.origin.shape[0]

    def save(self, path: str | Path) -> None:
        np.savez(path, **{f.name: getattr(self, f.name) for f in fields(self)})

    @classmethod
    def load(cls, path: str | Path) -> "Pulses":
        data = np.load(path)
        return cls(**{f.name: data[f.name] for f in fields(cls)})

    def last_xyz(self) -> np.ndarray:
        s = self.echo_s[self.echo_ptr[1:] - 1]
        return self.origin + s[:, None] * self.direc


def build_pulses(xyz: np.ndarray, gps_time: np.ndarray, return_number: np.ndarray,
                 ground: np.ndarray, hag: np.ndarray, h_cut: float,
                 scanner_at=None) -> tuple[Pulses, dict]:
    """Group points with identical gps_time into pulses.

    `scanner_at(t)` returns scanner positions (n, 3) for LAS gps times (NaN outside the
    trajectory). Without it, beams are assumed vertical (fallback). Each echo carries the beam
    fraction 1/n, n = number of echoes present in the pulse.
    """
    order = np.lexsort((return_number, gps_time))
    t = gps_time[order]
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    sizes = np.diff(np.r_[starts, t.size])
    last = starts + sizes - 1
    p_last = xyz[order[last]]
    if scanner_at is not None:
        origin = scanner_at(t[starts])
    else:
        origin = p_last + np.array([0.0, 0.0, VERTICAL_ORIGIN_HEIGHT])
    valid = np.isfinite(origin).all(axis=1)
    vec = p_last - origin
    rng = np.linalg.norm(vec, axis=1)
    valid &= rng > 1e-3
    report = {"pulses_total": int(starts.size), "pulses_without_scanner": int((~valid).sum()),
              "mode": "trajectory" if scanner_at is not None else "vertical fallback"}
    starts, sizes, origin, vec, rng = starts[valid], sizes[valid], origin[valid], vec[valid], rng[valid]
    direc = vec / rng[:, None]
    echo_idx = np.repeat(starts, sizes) + (np.arange(sizes.sum()) - np.repeat(np.cumsum(sizes) - sizes, sizes))
    pts = order[echo_idx]
    pulse_of_echo = np.repeat(np.arange(starts.size), sizes)
    echo_s = np.einsum("ij,ij->i", xyz[pts] - origin[pulse_of_echo], direc[pulse_of_echo])
    echo_ptr = np.r_[0, np.cumsum(sizes)]
    # echoes must be ordered along the beam (return numbers can disagree with range for noisy data)
    reorder = np.lexsort((echo_s, pulse_of_echo))
    pts, echo_s = pts[reorder], echo_s[reorder]
    pulses = Pulses(
        origin=origin, direc=direc, echo_ptr=echo_ptr.astype(np.int64), echo_s=echo_s,
        echo_w=np.repeat(1.0 / sizes, sizes).astype(np.float32),
        echo_canopy=(hag[pts] >= h_cut) & ~ground[pts],
        last_ground=ground[pts[echo_ptr[1:] - 1]],
    )
    report["pulses_used"] = len(pulses)
    report["echoes"] = int(echo_s.size)
    return pulses, report
