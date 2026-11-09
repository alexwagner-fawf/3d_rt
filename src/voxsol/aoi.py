"""Area of interest: restrict processing to a polygon or multipolygon (config section `aoi`).

`prepare` reads the polygon file (read-only; any OGR format via fiona: GeoPackage, Shapefile,
GeoJSON, ...), transforms it to the site CRS and stores it as work/<site>/aoi.geojson. All later
steps use that copy:

- points are kept inside the AOI's bounding box widened by `buffer_m`, because trees and terrain
  around the AOI still shade it; the DTM and the voxel grid shrink to that box;
- loggers outside the AOI are dropped;
- map pixels (grid B) are computed only inside the AOI, plus a one-pixel rim and the pixels around the
  logger uncertainty disks (so bilinear sampling never reaches an empty pixel); elsewhere they are
  NaN and are not traced.

Changing the AOI (file, layer, filter or file contents) needs a new run from `prepare`; later steps
stop with a message if the config no longer matches aoi.geojson.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

AOI_FILE = "aoi.geojson"


def source_signature(params) -> dict:
    path = Path(params.path).expanduser().resolve()
    return {"path": str(path), "layer": params.layer, "where": params.where}


def _rings(geom: dict):
    """All rings (exterior and holes) of a Polygon / MultiPolygon as (n, 2) arrays."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        for ring in poly:
            yield np.asarray(ring, dtype=np.float64)[:, :2]


def _ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def area(fc: dict) -> float:
    """Area [map units^2]; holes subtracted, overlapping features counted twice."""
    total = 0.0
    for f in fc["features"]:
        g = f["geometry"]
        for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
            rings = [np.asarray(r, dtype=np.float64)[:, :2] for r in poly]
            total += _ring_area(rings[0]) - sum(_ring_area(r) for r in rings[1:])
    return total


def bounds(fc: dict) -> tuple[float, float, float, float]:
    pts = np.vstack([r for f in fc["features"] for r in _rings(f["geometry"])])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def points_in(fc: dict, x, y) -> np.ndarray:
    """Point-in-AOI test (even-odd rule over all rings: holes and multipolygons work)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    inside_any = np.zeros(x.shape, bool)
    for f in fc["features"]:
        g = f["geometry"]
        for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
            inside = np.zeros(x.shape, bool)
            for ring in poly:
                r = np.asarray(ring, dtype=np.float64)[:, :2]
                x1, y1 = r[:-1, 0], r[:-1, 1]
                x2, y2 = r[1:, 0], r[1:, 1]
                for a, b, c, d in zip(x1, y1, x2, y2):
                    crosses = (b > y) != (d > y)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        xi = a + (y - b) * (c - a) / (d - b)
                    inside ^= crosses & (x < xi)
            inside_any |= inside
    return inside_any


def read_aoi(params, crs: str) -> dict:
    """Polygons of the AOI file in the site CRS, as a GeoJSON FeatureCollection with provenance."""
    import fiona
    from fiona.model import to_dict
    from fiona.transform import transform_geom

    path = Path(params.path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"aoi.path {path} not found")
    notes = []
    with fiona.open(path, layer=params.layer) as src:
        src_crs = src.crs.to_wkt() if src.crs else None
        if src_crs is None:
            notes.append(f"{path.name} has no CRS: assumed to be the site CRS {crs}")
        feats = src.filter(where=params.where) if params.where else iter(src)
        features = []
        for f in feats:
            if f.geometry is None:
                continue
            geom = to_dict(f.geometry)
            if geom["type"] not in ("Polygon", "MultiPolygon"):
                raise ValueError(f"aoi: {path.name} contains a {geom['type']}; only Polygon/MultiPolygon are supported")
            if src_crs:
                geom = to_dict(transform_geom(src_crs, crs, geom))
            props = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
                     for k, v in dict(f.properties).items()}
            features.append({"type": "Feature", "properties": props, "geometry": geom})
        layers = fiona.listlayers(path) if params.layer is None else [params.layer]
    if not features:
        raise ValueError(f"aoi: no polygon in {path}" + (f" matching where = {params.where!r}" if params.where else ""))
    if params.layer is None and len(layers) > 1:
        notes.append(f"{path.name} has several layers {layers}; used the first one (set aoi.layer)")
    fc = {"type": "FeatureCollection", "name": "aoi", "features": features,
          "voxsol": {"source": source_signature(params), "mtime": path.stat().st_mtime, "crs": crs,
                     "notes": notes}}
    from pyproj import CRS

    auth = CRS.from_user_input(crs).to_authority()
    if auth:
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:{auth[0]}::{auth[1]}"}}
    return fc


def write_aoi(workdir: Path, fc: dict | None) -> None:
    path = workdir / AOI_FILE
    if fc is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps(fc))


def load_aoi(cfg) -> dict | None:
    """The AOI used by `prepare` (work/<site>/aoi.geojson), checked against the config."""
    path = cfg.workdir / AOI_FILE
    fc = json.loads(path.read_text()) if path.exists() else None
    used = fc["voxsol"]["source"] if fc else None
    wanted = source_signature(cfg.aoi) if cfg.aoi is not None else None
    if used != wanted:
        raise ValueError(f"the config's aoi ({wanted}) differs from the one prepare used ({used}): "
                         "re-run the pipeline from prepare")
    if fc is not None:
        src = Path(used["path"])
        if src.exists() and src.stat().st_mtime != fc["voxsol"]["mtime"]:
            raise ValueError(f"{src} changed after prepare: re-run the pipeline from prepare")
    return fc


def context_box(fc: dict, buffer: float) -> tuple[float, float, float, float]:
    """AOI bounding box widened by `buffer` (the region whose points are kept)."""
    x0, y0, x1, y1 = bounds(fc)
    return x0 - buffer, y0 - buffer, x1 + buffer, y1 + buffer


def summary(fc: dict, buffer: float) -> dict:
    a = area(fc)
    x0, y0, x1, y1 = context_box(fc, buffer)
    box = (x1 - x0) * (y1 - y0)
    out = {"source": fc["voxsol"]["source"], "features": len(fc["features"]), "area_ha": round(a / 1e4, 4),
           "context_box": [x0, y0, x1, y1], "context_box_ha": round(box / 1e4, 3),
           "aoi_share_of_box": round(a / box, 3) if box > 0 else None}
    if fc["voxsol"]["notes"]:
        out["notes"] = fc["voxsol"]["notes"]
    if out["aoi_share_of_box"] is not None and out["aoi_share_of_box"] < 0.25:
        out["warning"] = ("the AOI covers < 25 % of its context box (e.g. parts far apart): the voxel grid spans "
                          "the whole box; consider one run per part (aoi.where)")
    return out


def pixel_mask(fc: dict, tpl, disk_xy: np.ndarray | None = None) -> np.ndarray:
    """Map pixels to compute (row 0 = south, like Raster.data): pixels touching the AOI, widened by one
    pixel, plus the 3 x 3 pixels around every logger disk point."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy.ndimage import binary_dilation

    shapes = [(f["geometry"], 1) for f in fc["features"]]
    m = rasterize(shapes, out_shape=(tpl.ny, tpl.nx), transform=from_origin(tpl.x0, tpl.y1, tpl.res, tpl.res),
                  all_touched=True, fill=0, dtype="uint8")
    m = binary_dilation(np.flipud(m).astype(bool), structure=np.ones((3, 3), bool))
    if disk_xy is not None and len(disk_xy):
        ix = np.floor((disk_xy[:, 0] - tpl.x0) / tpl.res).astype(np.int64)
        iy = np.floor((disk_xy[:, 1] - tpl.y0) / tpl.res).astype(np.int64)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                jx, jy = ix + dx, iy + dy
                ok = (jx >= 0) & (jx < tpl.nx) & (jy >= 0) & (jy < tpl.ny)
                m[jy[ok], jx[ok]] = True
    return m
