"""LAS/LAZ reading (read-only, chunked) and attribute inspection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import laspy
import numpy as np

# LAS 1.4 point formats >= 6 store the scan angle in 0.006 degree units.
SCAN_ANGLE_SCALE_PF6 = 0.006


@dataclass
class LasHeaderInfo:
    path: str
    version: str
    point_format: int
    point_count: int
    dimensions: list[str]
    mins: list[float]
    maxs: list[float]
    scales: list[float]
    adjusted_gps_time: bool
    crs_wkt: str | None
    has_rgb: bool
    has_gps_time: bool
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def header_info(path: str | Path) -> LasHeaderInfo:
    with laspy.open(path) as reader:
        h = reader.header
        dims = list(h.point_format.dimension_names)
        try:
            crs = h.parse_crs()
            crs_wkt = crs.to_wkt() if crs is not None else None
        except Exception:  # malformed VLRs should not stop inspection
            crs_wkt = None
        return LasHeaderInfo(
            path=str(path),
            version=f"{h.version.major}.{h.version.minor}",
            point_format=h.point_format.id,
            point_count=int(h.point_count),
            dimensions=dims,
            mins=[float(v) for v in h.mins],
            maxs=[float(v) for v in h.maxs],
            scales=[float(v) for v in h.scales],
            adjusted_gps_time=bool(h.global_encoding.gps_time_type),
            crs_wkt=crs_wkt,
            has_rgb={"red", "green", "blue"} <= set(dims),
            has_gps_time="gps_time" in dims,
        )


def iter_chunks(path: str | Path, chunk_size: int = 5_000_000) -> Iterator[laspy.ScaleAwarePointRecord]:
    with laspy.open(path) as reader:
        yield from reader.chunk_iterator(chunk_size)


def read_sample(path: str | Path, n: int) -> laspy.ScaleAwarePointRecord:
    """First `n` points of the file (attribute statistics only; spatially biased)."""
    with laspy.open(path) as reader:
        return reader.read_points(min(n, reader.header.point_count))


def scan_angle_deg(points: laspy.ScaleAwarePointRecord) -> np.ndarray | None:
    dims = set(points.point_format.dimension_names)
    if points.point_format.id >= 6 and "scan_angle" in dims:
        return np.asarray(points.scan_angle, dtype=np.float64) * SCAN_ANGLE_SCALE_PF6
    if "scan_angle_rank" in dims:
        return np.asarray(points.scan_angle_rank, dtype=np.float64)
    return None


def pulse_grouping_stats(gps_time: np.ndarray, return_number: np.ndarray,
                         number_of_returns: np.ndarray) -> dict:
    """Check whether returns of one pulse share a GPS time stamp.

    A group of points with identical gps_time is a consistent pulse if its size equals the
    (common) number_of_returns and its return numbers are exactly 1..n.
    """
    order = np.lexsort((return_number, gps_time))
    t = gps_time[order]
    rn = return_number[order].astype(np.int64)
    nr = number_of_returns[order].astype(np.int64)
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    sizes = np.diff(np.r_[starts, t.size])
    first_nr = nr[starts]
    # Return numbers within a group must be 1..size and number_of_returns constant.
    expected_rn = np.arange(t.size) - np.repeat(starts, sizes) + 1
    rn_ok = np.add.reduceat((rn == expected_rn).astype(np.int64), starts) == sizes
    nr_ok = np.add.reduceat((nr == np.repeat(first_nr, sizes)).astype(np.int64), starts) == sizes
    consistent = rn_ok & nr_ok & (sizes == first_nr)
    size_hist = np.bincount(np.minimum(sizes, 8))
    return {
        "n_points": int(t.size),
        "n_time_groups": int(starts.size),
        "group_size_hist": {str(i): int(c) for i, c in enumerate(size_hist) if c},
        "fraction_groups_consistent": float(consistent.mean()) if starts.size else float("nan"),
        "fraction_points_in_consistent_groups": float(sizes[consistent].sum() / t.size) if t.size else float("nan"),
        "min_positive_dt": float(np.min(np.diff(t[starts]))) if starts.size > 1 else float("nan"),
    }
