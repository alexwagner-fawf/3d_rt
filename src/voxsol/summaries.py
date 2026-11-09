"""Monthly and period summaries of the daily SW maps as GeoTIFFs (work/<site>/insolation/).

Read from maps_daily.nc one month at a time (memory ~ days in a month x map pixels), so the period
summary is built from the monthly sums and never needs the whole NetCDF in memory.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from voxsol.grid import Raster

MJ_DAY = "MJ m-2 d-1"
VARIABLES = {
    "sw_tou_h": ("Mean daily shortwave at the top of the understory (DTM + h_cut), horizontal", MJ_DAY),
    "sw_dir_h": ("Mean daily direct shortwave (beam + circumsolar) at the top of the understory, horizontal", MJ_DAY),
    "sw_dif_h": ("Mean daily diffuse shortwave at the top of the understory, horizontal", MJ_DAY),
    "sw_tou_s": ("Mean daily shortwave at the top of the understory, on the slope plane", MJ_DAY),
    "trans_h": ("Transmittance to the top of the understory: sum of SW_tou (horizontal) / sum of open-sky GHI", "1"),
}
NC_VARS = ("sw_dir_h", "sw_dif_h", "sw_dir_s", "sw_dif_s")


def _labels(times: pd.DatetimeIndex) -> list[tuple[str, np.ndarray]]:
    months = times.strftime("%Y-%m")
    return [(m, np.flatnonzero(months == m)) for m in sorted(set(months))]


def insolation_summaries(cfg) -> list[Path]:
    """Write insolation/<variable>_<YYYY-MM|period>.tif for every variable in VARIABLES."""
    import xarray as xr

    work = cfg.workdir
    site = pd.read_csv(work / "site_daily.csv", index_col="date")
    site.index = pd.to_datetime(site.index, utc=True).tz_localize(None).normalize()
    out_dir = work / "insolation"
    out_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    with xr.open_dataset(work / "maps_daily.nc") as ds:
        times = pd.DatetimeIndex(ds["time"].values).normalize()
        xs, ys = ds["x"].values, ds["y"].values  # cell centers, y north-up (descending)
        res = float(xs[1] - xs[0]) if xs.size > 1 else float(ys[0] - ys[1])
        x0, y0 = float(xs[0]) - res / 2, float(ys[-1]) - res / 2
        ghi = site["ghi"].reindex(times).to_numpy(float)
        total = {v: 0.0 for v in NC_VARS}
        total_days, total_ghi = 0, 0.0

        def write(name: str, label: str, arr: np.ndarray, days: int, first, last) -> None:
            desc, unit = VARIABLES[name]
            path = out_dir / f"{name}_{label}.tif"
            Raster(np.flipud(arr).astype(np.float32), x0, y0, res, cfg.crs).write(
                path, description=desc, unit=unit, days=days, first_day=first, last_day=last)
            written.append(path)

        def write_all(label: str, sums: dict, days: int, ghi_sum: float, first, last) -> None:
            tou_h = sums["sw_dir_h"] + sums["sw_dif_h"]
            write("sw_tou_h", label, tou_h / days, days, first, last)
            write("sw_dir_h", label, sums["sw_dir_h"] / days, days, first, last)
            write("sw_dif_h", label, sums["sw_dif_h"] / days, days, first, last)
            write("sw_tou_s", label, (sums["sw_dir_s"] + sums["sw_dif_s"]) / days, days, first, last)
            trans = tou_h / ghi_sum if ghi_sum > 0 else np.full_like(tou_h, np.nan)
            write("trans_h", label, trans, days, first, last)

        for label, idx in _labels(times):
            sums = {v: ds[v].isel(time=idx).sum("time", dtype=np.float64).to_numpy() for v in NC_VARS}
            ghi_sum = float(np.nansum(ghi[idx]))
            write_all(label, sums, idx.size, ghi_sum, times[idx[0]].date(), times[idx[-1]].date())
            for v in NC_VARS:
                total[v] = total[v] + sums[v]
            total_days += idx.size
            total_ghi += ghi_sum
        write_all("period", total, total_days, total_ghi, times[0].date(), times[-1].date())
    return written
