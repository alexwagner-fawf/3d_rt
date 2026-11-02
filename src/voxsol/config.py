"""Site configuration (YAML, validated with pydantic).

Input paths are read in place and never written. All outputs go to `work/<site>/` inside the repo.
"""

from __future__ import annotations

import glob
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from voxsol import REPO_ROOT


class Inputs(BaseModel):
    laz: list[str] = Field(description="LAZ/LAS files or glob patterns (read-only)")
    trajectory: str | None = Field(None, description="DJI Terra sbet.out / sbet.txt")
    trajectory_format: Literal["auto", "sbet_out", "sbet_txt"] = "auto"
    ortho: str | None = Field(None, description="RGB orthomosaic GeoTIFF")
    ortho_time: str | None = Field(None, description="Ortho acquisition time (UTC, ISO 8601), for the shadow check")
    loggers: str | None = Field(None, description="Optional logger metadata CSV: x, y [, id, z, xy_sd, reference]")
    tms: str | None = Field(None, description="TMS-4 data in long format (CSV or Parquet)")
    tms_columns: dict[str, str] = Field(
        default_factory=lambda: {"x": "x", "y": "y", "time": "timestamp", "value": "value", "height": "height"},
        description="Column names in the TMS long table; optional `id` = logger id column (else ids by position)")
    tms_timezone: str = "UTC"

    @field_validator("laz", mode="before")
    @classmethod
    def _as_list(cls, v):
        return [v] if isinstance(v, str) else v

    def laz_files(self) -> list[Path]:
        files: list[Path] = []
        for pattern in self.laz:
            hits = sorted(glob.glob(str(Path(pattern).expanduser())))
            files.extend(Path(h) for h in hits)
        return files


class Period(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self):
        if self.end < self.start:
            raise ValueError("period.end must not be before period.start")
        return self


class PrepareParams(BaseModel):
    ground: Literal["csf", "las_class"] = Field("csf", description="Ground from CSF or LAS class 2")
    sor: bool = Field(True, description="Statistical outlier removal before ground classification")
    csf_cloth_resolution: float = 0.5
    csf_rigidness: int = Field(2, ge=1, le=3, description="1 = steep terrain, 3 = flat")
    csf_class_threshold: float = 0.3
    logger_xy_sd: float = Field(0.05, ge=0, description="Default logger position sd [m] if not given")


class GridParams(BaseModel):
    dx: float = Field(0.25, gt=0, description="Horizontal voxel size [m]")
    dz: float = Field(0.25, gt=0, description="Vertical voxel size [m] (height above ground)")
    zmax: float = Field(40.0, gt=0, description="Top of the terrain-following grid above ground [m]")
    h_cut: float = Field(1.0, ge=0, description="Split height between understory and 3D canopy layer [m]")
    dtm_res: float = Field(0.25, gt=0, description="DTM resolution [m]")


class VoxelParams(BaseModel):
    pulse_min: int = Field(5, ge=1, description="Minimum entering pulses for a valid voxel estimate")
    g_leaf: float = Field(0.5, gt=0, le=1, description="Leaf projection function G (0.5 = spherical)")
    sigma_leaf: float = Field(0.0, ge=0, lt=1, description="Leaf scattering coefficient (0 = geometric)")


class TraceParams(BaseModel):
    map_res: float = Field(0.5, gt=0, description="Map pixel size [m]")
    sun_bin_deg: float = Field(1.0, gt=0, description="Sun-path direction bin size [deg]")
    n_sky: int = Field(400, ge=50, description="Number of diffuse sky directions (upper hemisphere)")
    logger_disk_n: int = Field(25, ge=1, description="Sample points per logger uncertainty disk")
    t_min: float = Field(1e-3, gt=0, description="Stop marching below this transmittance")
    understory_k: float = Field(0.5, gt=0, description="First-guess understory extinction for uncalibrated maps")
    sensor_heights: list[float] = Field(default_factory=lambda: [0.02, 0.15], description="TMS T2/T3 heights [m]")


class DatabaseParams(BaseModel):
    """PostgreSQL source (`voxsol db`) for TMS-4 data, plot locations and the ERA5 reference.

    Schema: docs/db/schema.sql. The connection comes from a libpq service in secrets/pg_service.conf
    (password in secrets/pgpass, see scripts/db.sh). `voxsol db` writes a snapshot to work/<site>/db/.
    """

    service: str = Field("voxsol", description="libpq service name (secrets/pg_service.conf)")
    versuchsflaeche: str | int | None = Field(None, description="Site: versuchsflaeche id, short, safe or clean name "
                                                          "(may be omitted if only one site has deployments)")
    behandlungseinheiten: list[str] = Field(default_factory=list,
                                            description="Optional treatment filter (short names, eng or de)")
    met_source: str | None = Field(None, description="externe_daten.met_points.source to use "
                                                     "(may be omitted if the loggers link to only one source)")
    skip_days_after_start: int = Field(1, ge=0, description="Drop the installation day(s) of every deployment")
    exclude_locality_quality: list[str] = Field(default_factory=list,
                                                description="Drop deployments with these locality_id_quality values")
    xy_sd_by_quality: dict[str, float] = Field(default_factory=dict,
                                               description="Logger position sd [m] per locality_id_quality "
                                                           "(default: prepare.logger_xy_sd)")
    ghi_label: Literal["auto", "end", "start", "instant"] = Field(
        "auto", description="Time stamp convention of the hourly GHI (Open-Meteo: end of the averaging hour); "
                            "auto = best match with clear-sky timing")


class ForcingParams(BaseModel):
    radiation: Literal["cams", "era5", "clearsky", "db"] = "cams"
    cloudiness: float = Field(0.0, ge=0, le=1, description="clearsky only: synthetic cloud modulation")
    seed: int = 0
    time_step: Literal["15min", "1h"] = "15min"
    reference: Literal["era5land", "loggers", "file", "db"] = "era5land"
    reference_loggers: list[str] = Field(default_factory=list)
    reference_file: str | None = None
    lapse_rate: float = Field(-0.0065, description="Air temperature lapse rate [K/m]")
    reference_grid_elevation: float | None = Field(None, description="ERA5-Land cell elevation [m] for lapse correction")


class HorizonParams(BaseModel):
    dem: str | None = Field(None, description="External DEM for the far horizon (read-only)")
    dem_res: float = Field(10.0, gt=0, description="Resampling resolution of the external DEM [m]")
    min_dist_m: float = 50.0
    max_dist_m: float = 20000.0
    az_step_deg: float = 1.0
    grid_res_m: float = 25.0


class CalibrationParams(BaseModel):
    """Read by the R calibration (R/run_calibration.R)."""

    sensors: dict[str, str] = Field(
        default_factory=lambda: {"T3": "15", "T2": "2", "T1": "-8", "VWC": "-0-10cm"},
        description="Sensor name -> value of the TMS `height` column")
    reference_vars: dict[str, str] = Field(
        default_factory=lambda: {"T3": "t2m", "T2": "t2m", "T1": "stl1"},
        description="Reference variable per temperature sensor (ERA5-Land / file columns)")
    us_candidates: list[str] = Field(
        default_factory=lambda: ["us_index_15cm", "us_index_2cm", "ground_lift", "ground_roughness", "rgb_exg"],
        description="Understory descriptors (map layers) tried in the k profiling; each is scaled by its "
                    "95th percentile over the map, the best one per sensor is chosen by grouped CV")
    responses: list[str] = Field(default_factory=lambda: ["T3", "T2", "T1"])
    stats: list[Literal["max", "mean", "min"]] = Field(default_factory=lambda: ["max", "mean"])
    k_grid: list[float] = Field(default_factory=lambda: [round(0.5 * i, 1) for i in range(17)])
    tau_days_grid: list[float] = Field(default_factory=lambda: [0.0, 1.0, 2.0, 4.0],
                                       description="T1: time constants of the cumulative SW tried")
    static_covariates: list[str] = Field(default_factory=lambda: ["rgb_green_cover"],
                                         description="Map layers (maps/<name>.tif) used as linear covariates")
    cv_folds: int = Field(0, ge=0, description="0 = leave-one-logger-out, else grouped k-fold")
    min_day_coverage: float = Field(0.9, gt=0, le=1)


class SiteConfig(BaseModel):
    site: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    crs: str = Field(description="Projected CRS of LAS and logger coordinates, e.g. EPSG:25832")
    vertical_crs: str | None = Field(None, description="Vertical CRS of LAS heights, e.g. EPSG:7837")
    inputs: Inputs
    period: Period | None = None
    prepare: PrepareParams = PrepareParams()
    buffer_m: float = Field(40.0, ge=0, description="Edge buffer excluded from maps [m]")
    grid: GridParams = GridParams()
    voxel: VoxelParams = VoxelParams()
    trace: TraceParams = TraceParams()
    forcing: ForcingParams = ForcingParams()
    horizon: HorizonParams = HorizonParams()
    calibration: CalibrationParams = CalibrationParams()
    database: DatabaseParams | None = None

    @model_validator(mode="after")
    def _sources(self):
        f = self.forcing
        if self.database is None and "db" in (f.radiation, f.reference):
            raise ValueError("forcing.radiation/reference = db needs a `database` section")
        if f.radiation == "era5" and f.time_step != "1h":
            raise ValueError("forcing.radiation = era5 is hourly: set forcing.time_step: 1h")
        if f.reference == "db":
            other = sorted({v for v in self.calibration.reference_vars.values() if v != "t2m"})
            if other:
                raise ValueError(f"the database reference only has t2m; set calibration.reference_vars to t2m "
                                 f"for all sensors (got {other})")
        if self.database is not None:
            snapshot = self.db_dir
            if self.inputs.tms is None:
                self.inputs.tms = str(snapshot / "tms_long.parquet")
                self.inputs.tms_columns.setdefault("id", "logger_id")
            if self.inputs.loggers is None:
                self.inputs.loggers = str(snapshot / "loggers.csv")
        return self

    @property
    def workdir(self) -> Path:
        path = REPO_ROOT / "work" / self.site
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_dir(self) -> Path:
        """Snapshot written by `voxsol db` (TMS long table, logger table, ERA5 points and series)."""
        return REPO_ROOT / "work" / self.site / "db"


def load_config(path: str | Path) -> SiteConfig:
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    return SiteConfig.model_validate(raw)
