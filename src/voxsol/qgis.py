"""Portable QGIS export of a site's results (`voxsol qgis <config>`).

Writes work/<site>/<site>_qgis/ (and a zip of it) to copy to any PC with QGIS >= 3.22:

  <site>.qgs                 QGIS project: grouped, named and styled layers, relative paths
  loggers.geojson            logger points with ids, metadata, static covariates, mean insolation and
                             per-logger calibration results (observed / cross-validated offsets)
  logger_disks.geojson       position uncertainty disks (radius 2 * xy_sd) used for the logger tracing
  result/                    calibrated temperature offset maps (dT_<sensor>_<stat>_<YYYY-MM>.tif)
  insolation/                monthly and period mean SW / transmittance (see voxsol.summaries)
  canopy/ understory/ ortho/ terrain/ quality/ other/   static layers from maps/ and dtm.tif
  OUTPUTS.md, README.txt     meaning of every layer

Rasters are copied as compressed GeoTIFFs with overviews; styles (colour ramps, ranges, legends) are
embedded in the project. The project XML is written directly (no PyQGIS needed on the target).
"""

from __future__ import annotations

import json
import re
import shutil
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, indent, tostring

import numpy as np
import pandas as pd

from voxsol import REPO_ROOT

QGIS_VERSION = "3.22.0-Białowieża"  # oldest QGIS the project format targets (opens in all later ones)
MARKER = ".voxsol-qgis-export"

PALETTES = {
    "inferno": ["#000004", "#420a68", "#932667", "#dd513a", "#fca50a", "#fcffa4"],
    "viridis": ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"],
    "greens": ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"],
    "ylgn": ["#ffffe5", "#d9f0a3", "#78c679", "#238443", "#004529"],
    "ylorbr": ["#ffffe5", "#fee391", "#fe9929", "#cc4c02", "#662506"],
    "reds": ["#fff5f0", "#fcbba1", "#fb6a4a", "#cb181d", "#67000d"],
    "diverging": ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"],
    "aspect": ["#3b4cc0", "#f2d16b", "#d73027", "#4daf4a", "#3b4cc0"],  # N, E, S, W, N
    "terrain": ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#a6611a"],
}


@dataclass
class MapSpec:
    group: str
    title: str
    palette: str
    vrange: tuple[float, float] | None  # fixed colour range, else 2-98 % of the data
    abstract: str


G_CANOPY, G_US, G_RGB, G_TERRAIN, G_QUALITY = ("Sky view and canopy (above h_cut)", "Understory (below h_cut)",
                                               "Orthophoto indices", "Terrain", "Quality")
SUBDIR = {G_CANOPY: "canopy", G_US: "understory", G_RGB: "ortho", G_TERRAIN: "terrain", G_QUALITY: "quality",
          "Other maps": "other"}
MAPS = {
    "svf_h": MapSpec(G_CANOPY, "Sky view, horizontal [0-1]", "inferno", (0, 1),
                     "Share of isotropic diffuse sky light reaching the top of the understory (DTM + h_cut) "
                     "through canopy and terrain, horizontal plane. 1 = open sky. Weather-independent."),
    "svf_s": MapSpec(G_CANOPY, "Sky view, slope plane [0-1]", "inferno", (0, 1),
                     "As the horizontal sky view, for the slope-parallel plane; an unobstructed slope sees "
                     "(1 + cos slope) / 2 of the sky."),
    "canopy_pai": MapSpec(G_CANOPY, "Plant area index above h_cut [m2/m2]", "greens", None,
                          "Leaves + wood above h_cut from the voxel extinction (sum lambda * dz / 0.5). 0.25 m grid, "
                          "full scan extent."),
    "canopy_pai_green": MapSpec(G_CANOPY, "Plant area index, green [m2/m2]", "greens", None,
                                "Part of the plant area index from green (living) material, from RGB-colored "
                                "points; reliable mainly for upper surfaces."),
    "canopy_pai_nongreen": MapSpec(G_CANOPY, "Plant area index, non-green [m2/m2]", "ylorbr", None,
                                   "Part of the plant area index from non-green material: snags, stems, dead crowns."),
    "canopy_gap_vertical": MapSpec(G_CANOPY, "Vertical gap fraction above h_cut [0-1]", "inferno", (0, 1),
                                   "Probability that a vertical beam passes the layer above h_cut. 1 = open."),
    "canopy_canopy_height": MapSpec(G_CANOPY, "Canopy height [m]", "viridis", None,
                                    "Top of the highest voxel with lambda > 0.05 1/m, above ground; 0 = nothing "
                                    "above h_cut."),
    "us_index_15cm": MapSpec(G_US, "Understory index above 15 cm [-]", "ylgn", None,
                             "-ln(share of low returns below 15 cm): vegetation between the T3 sensor height and "
                             "h_cut. 0 = nothing above the sensor; capped at 3.9."),
    "us_index_2cm": MapSpec(G_US, "Understory index above 2 cm [-]", "ylgn", None,
                            "-ln(share of low returns below 2 cm): vegetation above the T2 sensor height. 0 = "
                            "nothing above the sensor; capped at 3.9."),
    "us_height": MapSpec(G_US, "Understory height p95 [m]", "ylgn", None,
                         "95th percentile height of low vegetation returns (> 5 cm, < h_cut); 0 = none detected."),
    "us_green_frac": MapSpec(G_US, "Green share of low vegetation returns [0-1]", "ylgn", (0, 1),
                             "Share of low vegetation returns with ExG > 0.05 (LAS colors)."),
    "penetration": MapSpec(G_US, "Pulse penetration to the ground [0-1]", "inferno", (0, 1),
                           "Share of pulses whose last return in the pixel is a ground return. 1 = open."),
    "ground_lift": MapSpec(G_US, "Ground lift [m]", "ylorbr", None,
                           "DTM above the local lower envelope of ground returns (4 m window): the 'ground' sits "
                           "on dense vegetation where this is > ~0.1 m."),
    "ground_roughness": MapSpec(G_US, "Ground-return roughness [m]", "ylorbr", None,
                                "Robust vertical spread (IQR / 1.349) of ground returns: high in dense herbs."),
    "rgb_green_cover": MapSpec(G_RGB, "Green cover [0-1]", "greens", (0, 1),
                               "Share of orthophoto pixels classified as green (ExG above the site's Otsu "
                               "threshold). Default model covariate. Under crowns it shows the crowns."),
    "rgb_exg": MapSpec(G_RGB, "Excess green ExG [-]", "greens", None,
                       "2g - r - b of chromatic coordinates, mean over the ortho pixels. > ~0.1 green vegetation."),
    "rgb_gcc": MapSpec(G_RGB, "Green chromatic coordinate [0-1]", "greens", None, "g / (r + g + b); 0.33 = grey."),
    "rgb_gli": MapSpec(G_RGB, "Green leaf index [-]", "greens", None, "(2G - R - B) / (2G + R + B)."),
    "rgb_vari": MapSpec(G_RGB, "VARI [-]", "greens", None, "(G - R) / (G + R - B); sensitive to illumination."),
    "slope_deg": MapSpec(G_TERRAIN, "Slope [deg]", "viridis", None, "Slope of the 1 m-smoothed DTM."),
    "aspect_deg": MapSpec(G_TERRAIN, "Aspect, direction faced [deg]", "aspect", (0, 360),
                          "Direction the slope faces, clockwise from north (180 = south-facing); arbitrary on "
                          "flat ground."),
    "edge_fraction": MapSpec(G_QUALITY, "Edge fraction [0-1]", "reds", None,
                             "Share of sky directions whose ray left the scanned volume sideways; beyond the scan "
                             "counts as open, so values > ~0.05 overestimate light."),
    "n_low": MapSpec(G_QUALITY, "Low returns per pixel [count]", "viridis", None,
                     "Returns below h_cut per pixel; understory metrics are noisy where this is small."),
    "shadow_model_ortho_time": MapSpec(G_QUALITY, "Modeled direct transmittance at ortho time [0-1]", "inferno",
                                       (0, 1), "voxsol validate: direct transmittance towards the sun at the "
                                       "orthophoto acquisition time; compare with the shadows in the ortho."),
}
INSOLATION = {  # variable: (group title, palette, fixed range)
    "sw_tou_h": ("SW total, horizontal [MJ/m2/d]", "inferno", None),
    "trans_h": ("Transmittance to the understory top [0-1]", "inferno", (0, 1)),
    "sw_dir_h": ("SW direct, horizontal [MJ/m2/d]", "inferno", None),
    "sw_dif_h": ("SW diffuse, horizontal [MJ/m2/d]", "inferno", None),
    "sw_tou_s": ("SW total, slope plane [MJ/m2/d]", "inferno", None),
}
SENSORS = {"T3": "T3 air 15 cm", "T2": "T2 surface 2 cm", "T1": "T1 soil -8 cm"}


def map_spec(stem: str) -> MapSpec:
    if stem in MAPS:
        return MAPS[stem]
    m = re.fullmatch(r"frac_([\d.]+)_([\d.]+)", stem)
    if m:
        return MapSpec(G_US, f"Vegetation share {m[1]}-{m[2]} m [0-1]", "ylgn", None,
                       f"Share of low returns that are vegetation between {m[1]} and {m[2]} m above ground.")
    return MapSpec("Other maps", stem, "viridis", None, "")


# ------------------------------------------------------------------------------------ data

def _copy_raster(src_path: Path, dst_path: Path) -> np.ndarray:
    """Compressed, tiled GeoTIFF with overviews; returns band 1 (for the colour range)."""
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(src_path) as src:
        data = src.read()
        profile = src.profile.copy()
        descriptions, units, tags = src.descriptions, src.units, src.tags()
    floating = np.issubdtype(data.dtype, np.floating)
    profile.update(driver="GTiff", compress="deflate", predictor=3 if floating else 2, tiled=True,
                   blockxsize=256, blockysize=256)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data)
        for i, (d, u) in enumerate(zip(descriptions, units), start=1):
            if d:
                dst.set_band_description(i, d)
            if u:
                dst.set_band_unit(i, u)
        dst.update_tags(**tags)
        factors = [f for f in (2, 4, 8, 16, 32) if max(dst.width, dst.height) / f >= 256]
        if factors:
            dst.build_overviews(factors, Resampling.average)
            dst.update_tags(ns="rio_overviews", resampling="average")
    band = data[0].astype(np.float64)
    if profile.get("nodata") is not None and not np.isnan(profile["nodata"]):
        band[band == profile["nodata"]] = np.nan
    return band


def _robust_range(values: list[np.ndarray], fixed=None, symmetric=False) -> tuple[float, float]:
    if fixed is not None:
        return float(fixed[0]) + 0.0, float(fixed[1]) + 0.0
    v = np.concatenate([a[np.isfinite(a)][:: max(1, a.size // 200_000)] for a in values]) if values else np.array([])
    if v.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(v, [2, 98])
    if symmetric:
        m = max(abs(lo), abs(hi), 1e-6)
        return -float(m), float(m)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    return float(lo) + 0.0, float(hi) + 0.0  # + 0.0 turns -0.0 into 0.0


def _json_value(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else round(float(v), 4)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v if isinstance(v, (int, str, bool)) else str(v)


def _geojson(path: Path, features: list[dict], epsg: int | None) -> None:
    fc = {"type": "FeatureCollection", "name": path.stem, "features": features}
    if epsg:
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}
    path.write_text(json.dumps(fc, ensure_ascii=False))


def logger_attributes(cfg) -> pd.DataFrame | None:
    """Loggers with metadata, static covariates, mean insolation and calibration results per logger."""
    work = cfg.workdir
    if not (work / "loggers.csv").exists():
        return None
    lg = pd.read_csv(work / "loggers.csv")
    lg["id"] = lg["id"].astype(str)
    static_path = work / "logger_static.csv"
    if static_path.exists():
        st = pd.read_csv(static_path).rename(columns={"logger_id": "id"})
        st["id"] = st["id"].astype(str)
        lg = lg.merge(st.drop(columns=[c for c in ("x", "y", "xy_sd", "reference") if c in st.columns]),
                      on="id", how="left")
    irr_path = work / "logger_irradiance.parquet"
    if irr_path.exists():
        step = json.loads((work / "forcing.json").read_text()).get("step_s", 900)
        irr = pd.read_parquet(irr_path, columns=["logger_id", "time", "ghi", "sw_tou_h"])
        irr["day"] = pd.to_datetime(irr["time"]).dt.normalize()
        g = irr.groupby(irr["logger_id"].astype(str))
        sw = pd.DataFrame({"sw_tou_h_mean_MJ": g["sw_tou_h"].sum() * step * 1e-6 / g["day"].nunique(),
                           "trans_h_mean": g["sw_tou_h"].sum() / g["ghi"].sum()})
        lg = lg.merge(sw, left_on="id", right_index=True, how="left")
    for f in sorted((work / "calibration").glob("model_data_*.csv")):
        tag = f.stem.removeprefix("model_data_")
        d = pd.read_csv(f, usecols=lambda c: c in ("logger_id", "delta", "pred_cv"))
        g = d.groupby(d["logger_id"].astype(str))
        res = pd.DataFrame({f"{tag}_obs": g["delta"].mean(), f"{tag}_cv": g["pred_cv"].mean(),
                            f"{tag}_days": g.size()})
        res[f"{tag}_resid"] = res[f"{tag}_obs"] - res[f"{tag}_cv"]
        lg = lg.merge(res, left_on="id", right_index=True, how="left")
    return lg


# ------------------------------------------------------------------------------------ project XML

class Project:
    def __init__(self, title: str, crs: str):
        from pyproj import CRS

        c = CRS.from_user_input(crs)
        auth = c.to_authority()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # proj4 string only as a fallback next to the WKT
            proj4 = c.to_proj4() or ""
        self.epsg = int(auth[1]) if auth and auth[0] == "EPSG" else None
        self.crs = {"wkt": c.to_wkt("WKT2_2019"), "proj4": proj4, "authid": f"{auth[0]}:{auth[1]}" if auth else "",
                    "srid": str(self.epsg or ""), "description": c.name, "geographicflag": "false" if c.is_projected else "true"}
        self.root = Element("qgis", projectname=title, version=QGIS_VERSION)
        SubElement(self.root, "homePath", path="")
        SubElement(self.root, "title").text = title
        self._srs(SubElement(self.root, "projectCrs"))
        self.tree = SubElement(self.root, "layer-tree-group")
        SubElement(self.tree, "customproperties")
        self.layers = SubElement(self.root, "projectlayers")
        self.n = 0

    def _srs(self, parent):
        s = SubElement(parent, "spatialrefsys", nativeFormat="Wkt")
        for k in ("wkt", "proj4", "srid", "authid", "description"):
            SubElement(s, k).text = self.crs[k]
        SubElement(s, "geographicflag").text = self.crs["geographicflag"]
        return s

    def group(self, parent, name: str, expanded: bool = False):
        g = SubElement(parent if parent is not None else self.tree, "layer-tree-group", name=name,
                       checked="Qt::Checked", expanded="1" if expanded else "0")
        SubElement(g, "customproperties")
        return g

    def _layer(self, parent, kind: str, name: str, source: str, provider: str, visible: bool, abstract: str,
               **attrs):
        self.n += 1
        lid = f"{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')[:40]}_{self.n:03d}"
        node = SubElement(parent, "layer-tree-layer", id=lid, name=name, source=source, providerKey=provider,
                          checked="Qt::Checked" if visible else "Qt::Unchecked", expanded="0")
        SubElement(node, "customproperties")
        ml = SubElement(self.layers, "maplayer", type=kind, autoRefreshEnabled="0", refreshOnNotifyEnabled="0",
                        hasScaleBasedVisibilityFlag="0", maxScale="0", minScale="1e+08",
                        styleCategories="AllStyleCategories", **attrs)
        SubElement(ml, "id").text = lid
        SubElement(ml, "datasource").text = source
        SubElement(ml, "layername").text = name
        self._srs(SubElement(ml, "srs"))
        md = SubElement(ml, "resourceMetadata")
        SubElement(md, "identifier")
        SubElement(md, "parentidentifier")
        SubElement(md, "language")
        SubElement(md, "type").text = "dataset"
        SubElement(md, "title").text = name
        SubElement(md, "abstract").text = abstract
        return ml

    def raster(self, parent, name: str, source: str, palette: str, vrange: tuple[float, float],
               visible: bool = False, abstract: str = "", band: int = 1, opacity: float = 1.0):
        ml = self._layer(parent, "raster", name, source, "gdal", visible, abstract)
        SubElement(ml, "provider").text = "gdal"
        pipe = SubElement(ml, "pipe")
        lo, hi = vrange
        rr = SubElement(pipe, "rasterrenderer", type="singlebandpseudocolor", band=str(band), opacity=str(opacity),
                        alphaBand="-1", classificationMin=repr(lo), classificationMax=repr(hi), nodataColor="")
        SubElement(rr, "rasterTransparency")
        self._minmax(rr)
        shader = SubElement(SubElement(rr, "rastershader"), "colorrampshader", colorRampType="INTERPOLATED",
                            classificationMode="1", clip="0", minimumValue=repr(lo), maximumValue=repr(hi),
                            labelPrecision=str(_precision(lo, hi)))
        colors = PALETTES[palette]
        ramp = SubElement(shader, "colorramp", type="gradient", name="[source]")
        rgb = [_rgb(c) for c in colors]
        SubElement(ramp, "prop", k="color1", v=f"{rgb[0]},255")
        SubElement(ramp, "prop", k="color2", v=f"{rgb[-1]},255")
        SubElement(ramp, "prop", k="discrete", v="0")
        SubElement(ramp, "prop", k="rampType", v="gradient")
        inner = [f"{i / (len(rgb) - 1):.4f};{c},255" for i, c in enumerate(rgb)][1:-1]
        if inner:
            SubElement(ramp, "prop", k="stops", v=":".join(inner))
        prec = _precision(lo, hi)
        for i, c in enumerate(colors):
            v = lo + (hi - lo) * i / (len(colors) - 1)
            SubElement(shader, "item", alpha="255", value=repr(v), label=f"{v:.{prec}f}", color=c)
        self._pipe_tail(pipe)
        SubElement(ml, "blendMode").text = "0"
        return ml

    def hillshade(self, parent, name: str, source: str, visible: bool = True, abstract: str = ""):
        ml = self._layer(parent, "raster", name, source, "gdal", visible, abstract)
        SubElement(ml, "provider").text = "gdal"
        pipe = SubElement(ml, "pipe")
        rr = SubElement(pipe, "rasterrenderer", type="hillshade", band="1", opacity="1", alphaBand="-1",
                        zfactor="1", azimuth="315", angle="45", multidirection="1", nodataColor="")
        SubElement(rr, "rasterTransparency")
        self._minmax(rr)
        self._pipe_tail(pipe)
        SubElement(ml, "blendMode").text = "0"
        return ml

    @staticmethod
    def _minmax(rr):
        mm = SubElement(rr, "minMaxOrigin")
        for k, v in (("limits", "None"), ("extent", "WholeRaster"), ("statAccuracy", "Estimated"),
                     ("cumulativeCutLower", "0.02"), ("cumulativeCutUpper", "0.98"), ("stdDevFactor", "2")):
            SubElement(mm, k).text = v

    @staticmethod
    def _pipe_tail(pipe):
        SubElement(pipe, "brightnesscontrast", brightness="0", contrast="0", gamma="1")
        SubElement(pipe, "huesaturation", saturation="0", grayscaleMode="0", colorizeOn="0", colorizeRed="255",
                   colorizeGreen="128", colorizeBlue="128", colorizeStrength="100", invertColors="0")
        SubElement(pipe, "rasterresampler", maxOversampling="2")
        SubElement(pipe, "resamplingStage").text = "resamplingFilter"

    def vector(self, parent, name: str, source: str, geometry: str, visible: bool, abstract: str = "",
               label_field: str | None = None, outline: tuple[str, str] = ("20,20,20,255", "0.3")):
        wkb = {"Point": "Point", "Polygon": "Polygon"}[geometry]
        ml = self._layer(parent, "vector", name, source, "ogr", visible, abstract, geometry=geometry, wkbType=wkb,
                         labelsEnabled="1" if label_field else "0", readOnly="0", simplifyDrawingHints="0",
                         simplifyDrawingTol="1", simplifyLocal="1", simplifyAlgorithm="0", simplifyMaxScale="1")
        SubElement(ml, "provider", encoding="UTF-8").text = "ogr"
        r = SubElement(ml, "renderer-v2", type="singleSymbol", symbollevels="0", enableorderby="0", forceraster="0")
        sym = SubElement(SubElement(r, "symbols"), "symbol", type="marker" if geometry == "Point" else "fill",
                         name="0", alpha="1", clip_to_extent="1", force_rhr="0")
        if geometry == "Point":
            sl = SubElement(sym, "layer", **{"class": "SimpleMarker", "pass": "0", "enabled": "1", "locked": "0"})
            props = {"name": "circle", "color": "255,255,255,255", "outline_color": "20,20,20,255",
                     "outline_style": "solid", "outline_width": "0.5", "outline_width_unit": "MM", "size": "2.8",
                     "size_unit": "MM", "angle": "0", "joinstyle": "bevel", "scale_method": "diameter"}
        else:
            sl = SubElement(sym, "layer", **{"class": "SimpleFill", "pass": "0", "enabled": "1", "locked": "0"})
            props = {"color": "0,0,0,0", "style": "no", "outline_color": outline[0], "outline_style": "solid",
                     "outline_width": outline[1], "outline_width_unit": "MM", "joinstyle": "bevel"}
        for k, v in props.items():
            SubElement(sl, "prop", k=k, v=v)
        SubElement(r, "rotation")
        SubElement(r, "sizescale")
        if label_field:
            lab = SubElement(ml, "labeling", type="simple")
            st = SubElement(lab, "settings", calloutType="simple")
            ts = SubElement(st, "text-style", fieldName=label_field, isExpression="0", fontSize="8",
                            fontSizeUnit="Point", textColor="0,0,0,255", textOpacity="1", fontWeight="50",
                            fontItalic="0", fontFamily="Sans Serif", namedStyle="Regular", multilineHeight="1",
                            blendMode="0", previewBkgrdColor="255,255,255,255", allowHtml="0", capitalization="0")
            SubElement(ts, "text-buffer", bufferDraw="1", bufferSize="0.8", bufferSizeUnits="MM",
                       bufferColor="255,255,255,255", bufferOpacity="1", bufferNoFill="1", bufferJoinStyle="128",
                       bufferBlendMode="0")
            SubElement(st, "placement", placement="0", dist="1", distUnits="MM", quadOffset="4", priority="5",
                       offsetType="0", xOffset="0", yOffset="0")
            SubElement(st, "rendering", scaleVisibility="0", obstacle="1", drawLabels="1", displayAll="0",
                       upsidedownLabels="0", limitNumLabels="0", fontLimitPixelSize="0")
        SubElement(ml, "blendMode").text = "0"
        SubElement(ml, "featureBlendMode").text = "0"
        SubElement(ml, "layerOpacity").text = "1"
        return ml

    def write(self, path: Path, extent: tuple[float, float, float, float]) -> None:
        canvas = SubElement(self.root, "mapcanvas", name="theMapCanvas", annotationsVisible="1")
        SubElement(canvas, "units").text = "meters" if self.crs["geographicflag"] == "false" else "degrees"
        ext = SubElement(canvas, "extent")
        for k, v in zip(("xmin", "ymin", "xmax", "ymax"), extent):
            SubElement(ext, k).text = repr(float(v))
        SubElement(canvas, "rotation").text = "0"
        self._srs(SubElement(canvas, "destinationsrs"))
        props = SubElement(self.root, "properties")
        SubElement(SubElement(props, "Paths"), "Absolute", type="bool").text = "false"
        # without this flag QGIS ignores <projectCrs>
        SubElement(SubElement(props, "SpatialRefSys"), "ProjectionsEnabled", type="int").text = "1"
        indent(self.root)
        xml = tostring(self.root, encoding="unicode")
        path.write_text("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n" + xml + "\n", encoding="utf-8")


def _rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def _precision(lo: float, hi: float) -> int:
    span = abs(hi - lo)
    return 0 if span >= 50 else 1 if span >= 5 else 2 if span >= 0.5 else 3


# ------------------------------------------------------------------------------------ export

def export_qgis(cfg, make_zip: bool = True, log=print) -> dict:
    """Build work/<site>/<site>_qgis/ (and <site>_qgis.zip): data, styles and a QGIS project."""
    from voxsol.summaries import insolation_summaries

    work = cfg.workdir
    maps_dir = work / "maps"
    if not maps_dir.is_dir():
        raise FileNotFoundError(f"{maps_dir} missing: run the pipeline first (voxsol run config/<site>.yaml)")
    name = f"{cfg.site}_qgis"
    out = work / name
    if out.exists():
        if not (out / MARKER).exists():
            raise FileExistsError(f"{out} exists and was not written by voxsol qgis; move it away first")
        shutil.rmtree(out)
    out.mkdir()
    (out / MARKER).write_text("generated by voxsol qgis; the folder is replaced on every export\n")

    nc = work / "maps_daily.nc"
    ins_dir = work / "insolation"
    if nc.exists() and (not list(ins_dir.glob("*_period.tif"))
                        or min(p.stat().st_mtime for p in ins_dir.glob("*.tif")) < nc.stat().st_mtime):
        log("monthly/period insolation maps from maps_daily.nc")
        insolation_summaries(cfg)

    prj = Project(cfg.site, cfg.crs)
    report: dict = {"folder": str(out), "layers": {}}

    def count(group: str) -> None:
        report["layers"][group] = report["layers"].get(group, 0) + 1

    # loggers ---------------------------------------------------------------------------------
    lg = logger_attributes(cfg)
    if lg is not None and len(lg):
        g = prj.group(None, "Loggers", expanded=True)
        feats, disks = [], []
        for row in lg.to_dict(orient="records"):
            props = {k: _json_value(v) for k, v in row.items()}
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "Point", "coordinates": [round(row["x"], 3), round(row["y"], 3)]}})
            r = 2.0 * float(row.get("xy_sd") or 0.0)
            if r > 0:
                a = np.linspace(0, 2 * np.pi, 33)
                ring = [[round(row["x"] + r * np.sin(t), 3), round(row["y"] + r * np.cos(t), 3)] for t in a]
                disks.append({"type": "Feature", "properties": {"id": props["id"], "radius_m": round(r, 3)},
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})
        _geojson(out / "loggers.geojson", feats, prj.epsg)
        prj.vector(g, "Loggers", "./loggers.geojson", "Point", True,
                   "TMS-4 loggers. Attributes: metadata, static covariates averaged over the uncertainty disk, "
                   "mean daily SW at the top of the understory (sw_tou_h_mean_MJ), its transmittance, and per "
                   "calibrated sensor/statistic the mean observed offset (_obs), the held-out prediction (_cv), "
                   "their difference (_resid) and the number of days.", label_field="id")
        count("Loggers")
        if disks:
            _geojson(out / "logger_disks.geojson", disks, prj.epsg)
            prj.vector(g, "Position uncertainty (2 sd)", "./logger_disks.geojson", "Polygon", False,
                       "Disk of radius 2 * xy_sd around each logger, sampled for the logger tracing.")
            count("Loggers")

    aoi_path = work / "aoi.geojson"
    if aoi_path.exists():
        g = prj.group(None, "Area of interest", expanded=True)
        shutil.copy(aoi_path, out / "aoi.geojson")
        prj.vector(g, "Area of interest", "./aoi.geojson", "Polygon", True,
                   "The polygon(s) processing was restricted to (config aoi). Maps are computed inside it plus "
                   "a one-pixel rim; trees within buffer_m around it are part of the shading.",
                   outline=("215,25,28,255", "0.7"))
        count("Area of interest")

    # calibrated temperature offsets ----------------------------------------------------------
    dts = sorted((work / "calibration" / "maps").glob("dT_*_*_*.tif"))
    if dts:
        top = prj.group(None, "Temperature offset dT vs. reference [K] (calibrated)", expanded=True)
        by_model: dict[tuple[str, str], list[Path]] = {}
        for p in dts:
            m = re.fullmatch(r"dT_(\w+?)_(max|mean|min)_(\d{4}-\d{2})", p.stem)
            if m:
                by_model.setdefault((m[1], m[2]), []).append(p)
        for (sensor, stat), files in sorted(by_model.items(), key=lambda kv: (list(SENSORS).index(kv[0][0])
                                                                                 if kv[0][0] in SENSORS else 9, kv[0][1])):
            bands = [_copy_raster(p, out / "result" / p.name) for p in files]
            vr = _robust_range(bands, symmetric=True)
            sg = prj.group(top, f"{SENSORS.get(sensor, sensor)}, daily {stat}")
            for p in files:
                month = p.stem.rsplit("_", 1)[1]
                prj.raster(sg, f"dT {sensor} {stat} {month}", f"./result/{p.name}", "diverging", vr,
                           abstract=f"Predicted monthly mean of the daily {stat} offset of {SENSORS.get(sensor, sensor)}"
                                    f" against the reference (logger - ERA5 t2m) in {month} [K]. Band 1 dT_mean, band "
                                    f"2 se_approx (standard error of the fitted mean). Population-level model.")
                count("dT")

    # insolation ------------------------------------------------------------------------------
    ins = sorted(ins_dir.glob("*.tif")) if ins_dir.is_dir() else []
    if ins:
        top = prj.group(None, "Insolation at the top of the understory (uncalibrated)", expanded=True)
        for var, (title, palette, fixed) in INSOLATION.items():
            files = [p for p in ins if p.stem.startswith(var + "_")]
            if not files:
                continue
            files.sort(key=lambda p: (not p.stem.endswith("_period"), p.stem))
            bands = [_copy_raster(p, out / "insolation" / p.name) for p in files]
            vr = _robust_range(bands, fixed)
            sg = prj.group(top, title, expanded=var == "sw_tou_h")
            for p in files:
                label = p.stem.removeprefix(var + "_")
                prj.raster(sg, f"{var} {label}", f"./insolation/{p.name}", palette, vr,
                           visible=var == "sw_tou_h" and label == "period",
                           abstract=f"{title}, {'whole period' if label == 'period' else label}: mean of the daily "
                                    f"values (trans_h: ratio of sums). See OUTPUTS.md, section irradiance.")
                count("insolation")

    # static maps -----------------------------------------------------------------------------
    groups: dict[str, list[tuple[str, MapSpec, Path]]] = {}
    for p in sorted(maps_dir.glob("*.tif")):
        spec = map_spec(p.stem)
        groups.setdefault(spec.group, []).append((p.stem, spec, p))
    order = [G_CANOPY, G_US, G_RGB, G_TERRAIN, G_QUALITY, "Other maps"]
    dtm = work / "dtm.tif"
    for gname in order:
        items = groups.get(gname, [])
        if gname == G_TERRAIN and dtm.exists():
            items = items + [("dtm", MapSpec(G_TERRAIN, "DTM [m]", "terrain", None,
                                             "Digital terrain model from the laser ground points, 0.25 m."), dtm)]
        if not items:
            continue
        keys = list(MAPS)
        items.sort(key=lambda t: keys.index(t[0]) if t[0] in keys else len(keys))
        g = prj.group(None, gname)
        sub = SUBDIR[gname]
        for stem, spec, path in items:
            band = _copy_raster(path, out / sub / path.name)
            src = f"./{sub}/{path.name}"
            prj.raster(g, spec.title, src, spec.palette, _robust_range([band], spec.vrange),
                       abstract=spec.abstract)
            count(gname)
            if stem == "dtm":
                prj.hillshade(g, "Hillshade", src, visible=True, abstract="Hillshade of the DTM (basemap).")
                count(gname)

    # extent: the map grid (grid B) if present, else the DTM
    import rasterio

    ref = next(iter(sorted(ins_dir.glob("*_period.tif"))), None) if ins_dir.is_dir() else None
    with rasterio.open(ref or (maps_dir / "svf_h.tif" if (maps_dir / "svf_h.tif").exists() else dtm)) as r:
        extent = tuple(r.bounds)
    prj.write(out / f"{cfg.site}.qgs", extent)

    if (REPO_ROOT / "docs" / "OUTPUTS.md").exists():
        shutil.copy(REPO_ROOT / "docs" / "OUTPUTS.md", out / "OUTPUTS.md")
    period = f"{cfg.period.start} to {cfg.period.end}" if cfg.period else "n/a"
    (out / "README.txt").write_text(
        f"voxsol results for site {cfg.site} (exported {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)\n\n"
        f"Open {cfg.site}.qgs in QGIS 3.22 or newer. Keep the folder structure: all paths are relative.\n"
        f"Meaning, units and caveats of every layer: OUTPUTS.md (short version in each layer's\n"
        f"Properties > Metadata). CRS: {cfg.crs}. Period: {period}. Rasters: float32 GeoTIFF, nodata = NaN.\n"
        f"Daily maps are not included (work/{cfg.site}/maps_daily.nc on the processing machine).\n",
        encoding="utf-8")
    if make_zip:
        zpath = work / f"{name}.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in sorted(out.rglob("*")):
                if f.is_file() and f.name != MARKER:
                    kind = zipfile.ZIP_STORED if f.suffix == ".tif" else zipfile.ZIP_DEFLATED
                    zf.write(f, Path(name) / f.relative_to(out), compress_type=kind)
        report["zip"] = str(zpath)
        report["zip_mb"] = round(zpath.stat().st_size / 1e6, 1)
    report["project"] = str(out / f"{cfg.site}.qgs")
    return report
