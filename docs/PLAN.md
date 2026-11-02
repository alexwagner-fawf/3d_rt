# Plan: `voxsol`: ULS-based below-canopy insolation as a microclimate covariate

## Context

Disturbed forest sites (1–3 sites, < 20 ha each, mountainous Central Europe) have sparse adult trees and a dense layer of vegetation mostly < 1 m. Goal: an insolation covariate that captures the spatial differences between microclimates, calibrated against TMS-4 loggers (T1 −6 cm, T2 +2 cm, T3 +15 cm, soil moisture).

Inputs:
- DJI M350 RTK + Zenmuse L2 LAZ from DJI Terra (≥ 500 pts/m², GPS time, returns, scan angle, RGB-colored points).
- RGB orthomosaic.
- RTK logger positions with varying uncertainty.

The project directory `/home/alex/Dokumente/3d_rt` is empty, so this is a new build.

Decisions agreed with the user:
- **Python core**: heavy compute and a command-line tool.
- **R**: calibration and statistics.
- **Radiation forcing**: CAMS reanalysis/satellite, with ERA5 as fallback.
- **Reference temperatures**: ERA5-Land by default, with the option to plug in own reference loggers.
- **Two-layer light model**: 3D voxel ray tracing for structures above `h_cut` (≈ 1 m); a calibrated per-pixel understory model below it. This avoids relying on the L2 to resolve low vegetation, where its close returns merge and dense grass is not penetrated.
- **Built-in calibration module** covering T3, T2, T1, with soil moisture as a moderator.

**Why voxels:** each voxel holds a "turbid medium" (a light-extinction coefficient λ estimated from the laser pulses) rather than treating the canopy as a solid surface. Alternatives considered:
- A DSM shadow model can't represent partly see-through crowns or snags.
- Synthetic hemispherical photos rendered from points (CanRad-style) are point-size dependent and hard to calibrate.

The trajectory and per-pulse GPS time make beam-based λ estimation possible (as in AMAPVox), which corrects for occlusion.

**Key efficiency idea:** geometry and weather are decoupled. Each target point gets the transmittance toward a fixed set of sun-path and sky directions once. Daily sums for any weather series are then a linear combination of those values, using precomputed weights per direction bin. Weather or period changes therefore never require re-tracing.

## Repository containment (hard rule)

Everything is written inside `/home/alex/Dokumente/3d_rt`. Outside the folder, only these agreed exceptions apply:
- **Base interpreter:** `/home/alex/.local/share/r-miniconda/bin/python3` (3.12.9) is used read-only to create `.venv/`. Nothing is installed into r-miniconda.
- **Input data is read in place:** the config holds external paths to LAZ, sbet, ortho, TMS-4 files and the logger table. These are only opened read-only, never written. I ask for the paths and confirm before first access.
- **Network downloads land in the repo:** PyPI, CRAN, CAMS/SoDa, CDS (ERA5/ERA5-Land), DEM.
- **The plan file** in `~/.claude/plans/` is required by plan mode. At implementation start it is copied to `docs/PLAN.md`. No memory files are written outside; persistent project notes go into the repo's `CLAUDE.md`.

How this is enforced:
- **Python:** `.venv/` created with `python3 -m venv`, pip only (no conda, no PDAL, so every dependency is a wheel). Installs use `PIP_CACHE_DIR=.cache/pip`.
- **R:** project `.Rprofile` puts `r-lib/` first in `.libPaths()`, and new packages are installed there. No renv, so there is no global cache.
- **`env.sh`** (sourced before every run) plus `.Renviron` point every cache and temp directory into `.cache/`: `TMPDIR`, `PIP_CACHE_DIR`, `NUMBA_CACHE_DIR`, `XDG_CACHE_HOME`, `PROJ_USER_WRITABLE_DIRECTORY` (with `PROJ_NETWORK=OFF`). The CLI also sets them at startup.
- **PROJ:** pyproj's bundled PROJ data lives inside `.venv`. The GCG2016 geoid grid is stored in `data/external/proj/` and registered with `pyproj.datadir.append_data_dir`.
- **Credentials:** `secrets/credentials.yaml` (gitignored) holds the SoDa email and CDS key, passed explicitly via `cdsapi.Client(url=..., key=...)`. `~/.cdsapirc` is never written.
- **Repo `CLAUDE.md`** records this rule for future sessions.

## Architecture

```
3d_rt/
  CLAUDE.md                  # containment rule + project conventions
  env.sh, .Renviron, .Rprofile  # repo-local caches/tmp/R library (see above)
  .gitignore                 # .venv/ r-lib/ .cache/ work/ data/external/ secrets/
  requirements.txt           # pip wheels: numpy, numba, laspy[lazrs], cloth-simulation-filter, pyproj,
                             # rasterio, xarray, netcdf4, pvlib, cdsapi, pandas, pyarrow, scipy,
                             # pydantic, pyyaml, typer, pytest
  pyproject.toml             # package "voxsol" (pip install -e . into .venv), entry point `voxsol`
  docs/PLAN.md               # copy of this plan
  data/external/             # downloads: CAMS, ERA5(-Land), DEM, proj grids (inputs stay in place)
  secrets/credentials.yaml   # gitignored
  config/site_example.yaml   # all paths/params per site (pydantic-validated)
  src/voxsol/
    config.py                # pydantic schema + YAML load
    cli.py                   # typer: inspect | prepare | voxelize | horizon | forcing | trace | run
    io/las.py                # chunked LAZ reading, attribute detection, gps_time encoding check
    io/trajectory.py         # sbet.out (17 float64/record) or sbet.txt; time-base + datum conversion
    io/loggers.py            # logger table: id, x, y, z, xy_sd (per-logger RTK uncertainty), site
    preprocess/ground.py     # SOR noise filter (scipy cKDTree), CSF ground (cloth-simulation-filter),
                             # DTM 0.25 m, normalization
    preprocess/pulses.py     # group returns into pulses, scanner position per pulse (trajectory interp.)
    grid.py                  # terrain-following voxel grid (index k = bin(z − DTM(x,y))), CHM max-pyramid
    raymarch.py              # numba core: terrain-following ray marching, shared by voxelize + trace
    voxelize.py              # pulse tracing → per-voxel path length / hits / pulses → λ
    rgb.py                   # per-voxel green fraction from colored points; ortho → RGB index rasters
    understory.py            # per-pixel < h_cut metrics (height, layer fractions, penetration, greenness)
    directions.py            # sun-path direction bins + diffuse sky directions with solid-angle weights
    horizon.py               # far horizon from external DEM (Copernicus GLO-30 or state DGM1)
    forcing.py               # CAMS (pvlib.iotools.get_cams, 'cams_radiation', 15 min); ERA5 fdir/ssrd;
                             # ERA5-Land t2m/skt/stl1/swvl1 + orography for lapse-rate correction
    trace.py                 # transmittance at targets (logger disks exact; map grid via bins)
    irradiance.py            # assembles SW components, weight tensors, daily maps, logger series
    outputs.py               # NetCDF (daily maps), GeoTIFF (static layers), Parquet (logger series)
  R/
    io_voxsol.R              # read Parquet/NetCDF/GeoTIFF outputs (arrow, terra)
    loggers.R                # myClim: read TOMST TMS-4, clean, VWC, QC flags, aggregate hourly/daily
    reference.R              # ERA5-Land (default) or own reference loggers/station file → ΔT
    calibrate.R              # mgcv::bam models, understory-k profiling, CV, baselines
    predict_maps.R           # terra::predict → ΔT maps + uncertainty
    run_calibration.R        # driver script per config
  tests/                     # pytest, synthetic scenes (see Verification)
  work/<site>/               # cached intermediate outputs per step
```

## Pipeline and methods

### 1. `voxsol inspect` (run first on real data)
Reports the facts every later step depends on:
- LAS point format and the global-encoding bit (adjusted standard GPS time = GPS − 1e9).
- Whether returns of one pulse share a GPS time.
- Return statistics and scan angle range.
- RGB presence and CRS.
- Trajectory time overlap and the point-to-trajectory distance distribution.

If no sbet is found, it prints how to reprocess the mission in DJI Terra with trajectory output (sbet.out) enabled.

### 2. `prepare`: ground and pulses
- **Noise filter:** statistical outlier removal with a scipy cKDTree.
- **Ground:** CSF ground classification (`cloth-simulation-filter`; `lidR::classify_ground(csf())` as a cross-check in R), then a 0.25 m DTM.
- **DTM check:** compare the DTM with the logger RTK heights (bias/RMSE). This tests whether the DTM is sitting on top of dense grass.
- **Trajectory:** reproject from WGS84 ellipsoidal to the LAS CRS (e.g. EPSG:25832) and the orthometric height datum (GCG2016 geoid grid via PROJ).
- **Pulse table:** pulse id, scanner xyz, ordered return xyz, ground flag.
- **Fallback without trajectory:** beam direction from scan angle and flight-line heading.

### 3. `voxelize`: canopy layer (≥ h_cut, default 1.0 m)
- **Grid:** terrain-following, horizontal 0.25 m, vertical 0.25 m up to 40 m above ground. That is about 2–5 GB in RAM for 20 ha; no tiling needed.
- **Pulse tracing:** each pulse is followed from the scanner to its last return, accumulating per voxel: entering path length, hits weighted by echo rank (1/n per return, AMAPVox-style), and the number of entering pulses.
  - Ground returns and returns below `h_cut` end the beam without counting as canopy hits.
- **λ estimate:** bias-corrected maximum-likelihood estimate λ = hits / path length (Pimont et al. 2018).
  - Voxels with fewer than `pulse_min` entering pulses are masked as occluded and filled with the local mean at the same height.
- **Light extinction:** spherical leaf angles (G = 0.5), so λ_light = λ_lidar. Optional leaf-scattering factor √(1−σ), default σ = 0 (purely geometric).
- **RGB per voxel:** green fraction from colored points (ExG/GCC threshold), giving separate green (transpiring) and non-green (snags/wood) λ.
  - Caveat: L2 colors are projected from nadir images, so this is reliable mainly on upper-surface voxels. It is written as a quality-flagged layer.
- **Static outputs:** PAI (vertically integrated canopy density), canopy height, gap fraction, and green canopy cover.

### 4. `understory`: < h_cut layer per pixel (0.25 m, aggregated to map resolution)
- **Height:** low-vegetation height (p95 of normalized z in 0.05–h_cut).
- **Layer fractions:** point fractions in 0–0.15 / 0.15–0.5 / 0.5–h_cut m, giving the vegetation above each sensor height: above 0.02 m for T2, above 0.15 m for T3.
- **Penetration index:** ground returns / pulses in the column.
- **Greenness:** ortho RGB indices (ExG, GLI, VARI, GCC) and green cover (Otsu threshold on ExG), plus the green fraction of low colored points.
- **Understory index:** a combination of these metrics (`US`, with versions for T2 and T3 sensor heights).
- **First-guess transmittance:** T_us = exp(−k·US / cos Z) with a default k, so uncalibrated maps exist before calibration. The calibration step fits k.

### 5. `horizon`: far terrain (mountainous sites)
- Horizon elevation per 1° azimuth from an external DEM, out to 20 km with earth-curvature correction, computed on a 25 m grid over the AOI.
- Direct light is blocked when sun elevation < horizon(az); diffuse directions below the horizon get zero weight.
- Near terrain is handled inside the ray marcher: a ray below DTM(x,y) is blocked.

### 6. `forcing`
- **Radiation:** CAMS radiation service at the site centroid and altitude, 15 min, giving GHI, DHI and DNI plus clear-sky values. Needs a free SoDa account email.
- **ERA5 alternative:** hourly ssrd/fdir via `cdsapi`, key passed explicitly from `secrets/credentials.yaml`.
- **ERA5-Land reference:** t2m, skt, stl1, swvl1, with a lapse-rate correction from the grid-cell orography to the logger elevation.
- **Own reference data:** `reference.source: era5land | loggers | file` in the config.
- All timestamps in UTC.

### 7. `trace`: transmittance (numba, parallel over targets, 16 cores)
**Ray marcher:**
- Terrain-following lookup.
- Adaptive step; skips ahead when the ray is above the local CHM maximum.
- Stops early when T < 1e-3, or when the ray hits terrain or reaches the canopy top.

**Targets:**
- *Loggers:* about 25 points in a disk of radius 2·xy_sd (per-logger uncertainty) at z = DTM + h_cut. Exact sun position per 15-min step, plus about 400 sky directions stored per point. Outputs mean, sd and quantiles over the disk.
- *Map grid:* 0.5 m pixels (configurable) at DTM + h_cut.
  - Direct light uses sun-path bins (1° az × 1° el cells visited in the study period, about 3–5k). Each pixel's ray results are contracted immediately with a weight tensor `W[bin, day, xyz]` = Σ DNI·(sun unit vector).
  - Because incidence cos i = n·s is linear, one pass gives daily direct sums on both the horizontal and the slope plane (normal from a smoothed DTM). There is no need to store T for every pixel and bin.
  - Diffuse: isotropic, cos-weighted over about 400 Fibonacci-hemisphere directions, for both the horizontal and the slope normal. The circumsolar part (Perez) is treated as direct.

**Edge handling:** a quality layer flags rays that leave the grid sideways below canopy height. The recommended AOI is the scan extent minus a 30–50 m buffer.

**Expected runtime:** minutes for loggers; roughly 10–30 min per 20 ha site for 0.5 m maps.

### 8. `irradiance` + outputs
- **Logger series (Parquet, 15 min):** SW on top of the understory (SW_tou) as direct/diffuse, horizontal/slope; GHI open; sun zenith and azimuth; relative transmittance; disk sd.
- **Daily maps (NetCDF, CF/CRS):** SW_tou direct and diffuse (horizontal and slope), plus a daily energy-weighted effective cos Z used for the understory correction.
- **Static GeoTIFFs:** sky-view/diffuse transmittance, PAI, green and non-green canopy cover, understory metrics/US, RGB indices, DTM/slope/aspect/TWI, quality flags.

### 9. R calibration (`R/`)
- **Logger data:** read with `myClim` (TOMST format): cleaning, `mc_calc_vwc`, QC (installation/lifting checks), hourly and daily aggregation.
- **Responses:** ΔT3, ΔT2, ΔT1 = logger − reference, as daily Tmax/Tmean/Tmin and optionally hourly.
- **Understory k profiling:** grid over k. For each k, SW at sensor height = SW_tou_dir·exp(−k·US_h/cos Z) + SW_tou_dif·exp(−k·US_h·c_d), computed exactly from the 15-min logger series. Choose k by CV, separately per sensor height.
- **Models:** `mgcv::bam(discrete = TRUE)`, e.g. `ΔT3max ~ s(SW_ground) + te(SW_ground, US) + s(green_cover) + s(PAI) + s(doy) + s(logger, bs = "re") + s(site, bs = "re")`, with AR(1) via `rho`.
  - T1 uses an exponentially weighted cumulative SW (time constant profiled).
  - Two tiers: *explanatory*, which adds `s(VWC)` and its interaction with SW (evaporative cooling), and *predictive*, which uses only predictors available as maps.
- **Validation:** leave-one-logger-out and spatial-block CV. Baselines: no radiation, open GHI only, sky-view only, full model. This shows what the 3D insolation adds.
- **Maps:** `terra::predict` → monthly/seasonal ΔT maps per response, with SE from `predict(se.fit = TRUE)`.
- **New R packages:** myClim, arrow, ncdf4, gratia, installed into the repo-local `r-lib/` (mgcv/terra/sf/data.table/lidR are already installed in the user library and only read from there).

## Implementation order
0. Containment setup: `CLAUDE.md`, `env.sh`, `.Renviron`, `.Rprofile`, `.gitignore`, `docs/PLAN.md`. Check with `find ~ -newer <stamp>` (excluding the repo and the read-only data paths) that nothing was written outside.
1. `.venv` + `r-lib/`, skeleton, config schema, `inspect`. Ask for the external data paths, then run read-only on the real data and confirm assumptions (GPS-time-per-pulse, sbet time base and datum).
2. `prepare` (ground, DTM check against RTK, pulse table).
3. `raymarch` + `voxelize` + `rgb` + `understory`, with synthetic tests.
4. `directions`, `horizon`, `trace`, with synthetic tests.
5. `forcing` + `irradiance` + outputs.
6. R calibration module.
7. End-to-end run on one site; README with the workflow.

## Verification
**Synthetic pytest scenes:**
- Homogeneous slab of known λ gives T = exp(−λL/cos θ).
- Opaque cylinder "tree": shadow position matches analytic geometry.
- Sloped plane: correct cos i and terrain self-shading.
- Pulses simulated through a known λ field are recovered without bias by `voxelize`.
- Empty scene gives sky-view = 1, with direction weights summing to π.
- Bin-weight daily sums are within 2% of brute-force per-time-step tracing.

**Real-data checks:**
- DTM vs RTK logger heights.
- Open-area logger: T ≈ 1.
- Modeled direct-shadow map at the ortho acquisition time vs the shadow mask from ortho brightness. This is an independent geometric check.
- λ/PAI vs the AMAPVox R package on a 1 ha subset (same trajectory).
- Understory approximation (daily effective cos Z) vs exact 15-min series at the loggers.

**Calibration:** CV skill of the full model vs the baselines; residual maps checked for spatial structure.

## Implementation notes (2026-10-28, built and tested on synthetic data)

Deviations from the plan above, and why:
- **Parquet in R**: `nanoparquet` instead of `arrow` (arrow builds a large C++ library from source on Linux).
  NetCDF is read with `terra`, so `ncdf4` is not needed.
- **TMS-4 input** arrives in long format (x, y, timestamp, value, height), so it is read with `data.table`.
  `myClim` is installed for raw TOMST files. Python derives the canonical logger table (`loggers.csv`)
  from the unique coordinates, and R joins measurements to it by nearest coordinates (≤ 0.5 m).
- **Map tracing** stores T per pixel and sun bin as uint8 (`map_Tbins.npy`) instead of contracting with
  the forcing during tracing. Forcing and period changes need no re-tracing.
- **Understory descriptors**: the simulated L2 (echoes < 1 m apart merge) showed that LiDAR point
  fractions carry almost no information below 1 m. Added `ground_lift` (CSF ground above the
  grey-opening lower envelope of ground returns) and `ground_roughness` (IQR of ground returns).
  Calibration tries all candidates (scaled by map 5–95% quantiles) and chooses descriptor and k by
  grouped CV. In the synthetic site `ground_lift` won for all sensors.
- **Estimator**: exposure-based free-path MLE (Pimont et al. 2018) with an optional classical (N−1)/N
  factor. Pimont's specific bias correction is not implemented.
- **Forcing**: an offline `clearsky` source (Ineichen + synthetic clouds + Erbs split) for tests and demos.
- **Explanatory model**: the VWC interaction is parametric (`sw_ground:vwc`). A tensor interaction
  extrapolated wildly for held-out loggers.
- **Validation**: `voxsol validate` runs the ortho shadow check (synthetic: 97.7% agreement, κ = 0.79).
- **R threading**: `run_calibration.R` re-launches itself with `OPENBLAS_NUM_THREADS=1`. OpenBLAS
  busy-waiting otherwise multiplied system CPU time about 10×.

## Implementation notes (2026-11-02, PostgreSQL source)

TMS-4 data, logger positions and the ERA5 reference live in a PostgreSQL/PostGIS database (schema dump
in `docs/db/schema.sql`, from the user's `rstudio-export/`). Design:
- **Snapshot, not live queries**: `voxsol db <config>` (`src/voxsol/io/db.py`) runs once in a read-only
  session (`BEGIN READ ONLY`) and writes `work/<site>/db/` in the formats the pipeline already reads.
  With a `database` section, `inputs.tms` / `inputs.loggers` default to that snapshot (Python and R).
  Runs stay reproducible and work offline; nothing is ever written to the database.
- **Logger identity** = `tms_deployments.locality_id` = `be_verjuengungskreise_pt.verjkr_id` (confirmed by
  the user; `voxsol db --check` counts the matches). Deployments at one locality (logger swaps) form one series.
  Only deployments that overlap in time become `<locality>_<serial>`. The logger table and the R
  calibration now join by id when the TMS table has one (`tms_columns.id`), otherwise by position.
- **Validity**: the view `daten.tms_data_valid` (logger and sensor end dates, local days) plus
  `skip_days_after_start` (default 1: installation day). Dates are Europe/Berlin days; all output is UTC.
- **Positions**: point geometry, transformed from its SRID to the site CRS (SRID 0 = site CRS); a Z value
  becomes the logger `z` (DTM check). `xy_sd` per `locality_id_quality` via `xy_sd_by_quality`.
- **Privileges**: the dump grants `geouser` SELECT on `daten.*`, `plots.*` and `externe_daten.*`, but not
  on `lut.versuchsflaechen` / `lut.behandlungseinheiten` (to be granted). Without them, names are left
  empty and the site is chosen by its numeric id.
- **T1 reference without soil temperature**: the reference only removes the weather signal shared by all
  loggers. In the synthetic site (true T1 driven by soil temperature), t2m instead of stl1 lowered the
  daily CV R² of T1 (max 0.79 → 0.66, mean 0.70 → 0.46). The spatial R² of the logger offsets, which is
  what the maps carry, was unchanged (max 0.88 → 0.87, mean 0.81 → 0.81). With t2m, ΔT1 reads as
  "soil at −8 cm minus air", the same quantity as for T2/T3.
- **Target Ubuntu 20.04** (2026-11-02): all 51 wheels resolve for glibc 2.31 with Python 3.11/3.12
  (highest requirement glibc 2.28). The system has Python 3.8 and deadsnakes no longer builds for focal,
  so `setup.sh --standalone-python` fetches python-build-standalone 3.12.15 (release 20261003, SHA-256
  pinned) into `.python/`. R 3.6 is too old (nanoparquet needs R ≥ 4.0, current mgcv ≥ 4.4 but mgcv
  ships with R); CRAN's focal-cran40 repo has R 4.5.x. `VOXSOL_CRAN` selects Posit Package Manager
  binaries to avoid compiling terra. The standalone interpreter itself was not executed on the
  development machine (archive checksum and layout verified).
- **Geoid grids**: `data/external/proj/` is registered with pyproj. With `vertical_crs` set, `prepare` stops
  if the exact conversion is unavailable (PROJ would otherwise use a ballpark conversion and leave heights
  ~48 m off). `inspect` only reports it.
- **ERA5 (Open-Meteo)**: `forcing.reference: db` averages the t2m of the linked points after a lapse-rate
  correction from each point's `elevation` (the height Open-Meteo downscaled it to) to the site elevation.
  There is no soil temperature, so T1 also uses t2m. `forcing.radiation: db` takes hourly GHI, finds its
  time-stamp convention by correlation with clear-sky GHI (Open-Meteo: mean of the preceding hour), keeps
  the hourly energy but uses the clear-sky shape within each hour (15 min), and splits with Erbs. CAMS
  remains the preferred radiation source (satellite clouds, measured direct/diffuse split).
- **Tests** load the real schema into a throwaway PostGIS cluster (unix socket in the pytest temp dir)
  with the dump's grants. Downstream check: the synthetic site converted into a snapshot reproduces
  the calibration (T3 max CV R² 0.657 vs 0.663, T2 max 0.817 vs 0.818).
