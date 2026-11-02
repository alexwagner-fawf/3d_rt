"""Logger positions: derived from the TMS-4 long table, optionally enriched with a metadata table."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

MATCH_TOL_M = 0.5


def sniff_separator(path: str | Path) -> str:
    with open(path, newline="") as fh:
        header = fh.readline()
    return max((";", ",", "\t"), key=header.count)


def _read_table(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in (".parquet", ".pq"):
        return pd.read_parquet(path, columns=columns)
    return pd.read_csv(path, usecols=columns, sep=sniff_separator(path))


def read_loggers(path: str | Path) -> pd.DataFrame:
    """Logger metadata table: x, y [, id, z, xy_sd, reference]."""
    df = _read_table(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in ("x", "y") if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    if "id" not in df.columns:
        df["id"] = [f"L{i + 1:03d}" for i in range(len(df))]
    df["id"] = df["id"].astype(str)
    if df["id"].duplicated().any():
        raise ValueError(f"{path}: duplicate logger ids {df.loc[df['id'].duplicated(), 'id'].tolist()}")
    if "xy_sd" not in df.columns:
        df["xy_sd"] = np.nan
    if "reference" not in df.columns:
        df["reference"] = False
    df["reference"] = df["reference"].astype(str).str.lower().isin(("1", "true", "yes"))
    return df


def loggers_from_tms(path: str | Path, x_col: str = "x", y_col: str = "y", id_col: str | None = None) -> pd.DataFrame:
    """Logger positions in a TMS long table: per logger id if the table has one, else unique positions
    (rounded to 1 cm) with ids by x then y."""
    if id_col:
        df = _read_table(path, columns=[id_col, x_col, y_col])
        pos = df.groupby(df[id_col].astype(str), sort=True)[[x_col, y_col]].first()
        return pd.DataFrame({"id": pos.index.to_numpy(), "x": pos[x_col].to_numpy(float),
                             "y": pos[y_col].to_numpy(float)})
    df = _read_table(path, columns=[x_col, y_col])
    xy = np.unique(np.round(df[[x_col, y_col]].to_numpy(float), 2), axis=0)
    out = pd.DataFrame({"id": [f"L{i + 1:03d}" for i in range(len(xy))], "x": xy[:, 0], "y": xy[:, 1]})
    return out


def logger_table(tms: str | Path | None, meta: str | Path | None, default_xy_sd: float,
                 reference_ids: list[str] = (), tms_columns: dict | None = None) -> pd.DataFrame:
    """Canonical logger table (id, x, y, xy_sd, reference[, z, ...]) written to work/<site>/loggers.csv.

    Positions come from the TMS table if given. Metadata rows are matched by id when the TMS table
    has an id column (`tms_columns.id`, e.g. the database snapshot), otherwise by nearest position
    (within 0.5 m); their extra columns are carried over. The R calibration joins TMS measurements
    to this table by id or by coordinates.
    """
    cols = tms_columns or {}
    id_col = cols.get("id")
    if tms is not None:
        base = loggers_from_tms(tms, cols.get("x", "x"), cols.get("y", "y"), id_col)
    elif meta is not None:
        base = read_loggers(meta)[["id", "x", "y"]]
    else:
        raise ValueError("Need inputs.tms or inputs.loggers to define logger positions")
    base["xy_sd"] = default_xy_sd
    base["reference"] = False
    if meta is not None:
        m = read_loggers(meta)
        if id_col or tms is None:
            pos = pd.Index(m["id"]).get_indexer(base["id"])
        else:
            d, j = cKDTree(m[["x", "y"]].to_numpy()).query(base[["x", "y"]].to_numpy())
            pos = np.where(d <= MATCH_TOL_M, j, -1)
        hit = pos >= 0
        mm = m.iloc[pos[hit]].reset_index(drop=True)
        sd = mm["xy_sd"].to_numpy(float)
        base.loc[hit, "xy_sd"] = np.where(np.isfinite(sd), sd, default_xy_sd)
        base.loc[hit, "reference"] = mm["reference"].to_numpy()
        for col in mm.columns.drop(["id", "x", "y", "xy_sd", "reference"]):
            base.loc[hit, col] = mm[col].to_numpy()
    base.loc[base["id"].isin(list(reference_ids)), "reference"] = True
    return base
