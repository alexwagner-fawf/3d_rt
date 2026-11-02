# voxsol: below-canopy insolation from ULS point clouds as a microclimate covariate

Simulates shortwave insolation in disturbed forest sites from UAV laser scanning (DJI L2, ≥ 500 pts/m²)
and calibrates it against TMS-4 temperatures. Design and rationale: [`docs/PLAN.md`](docs/PLAN.md).

**Two-layer light model**
- *Canopy layer (≥ `h_cut`, default 1 m)*: terrain-following voxel grid; per-voxel extinction λ is
  estimated by tracing each laser pulse from the scanner (trajectory) to its last return
  (beam-fraction weighted free-path MLE). Sun and sky rays are traced through λ, terrain, and a
  far horizon from an external DEM.
- *Understory (< `h_cut`)*: the L2 merges returns of vegetation < ~1 m with the ground echo, so this
  layer is described per pixel by several descriptors: LiDAR point fractions, `ground_lift` (how far
  the CSF ground sits above the local lower envelope), `ground_roughness`, and RGB indices. Its light
  extinction is fitted to the T2/T3 loggers. The calibration picks the descriptor that best predicts
  held-out loggers.

Geometry and weather are decoupled: transmittance toward sun-path bins and sky directions is traced
once, so any forcing series (CAMS, ERA5, clear sky) gives daily maps by a matrix product.

## Setup on a new machine
Requirements (target system: Ubuntu 20.04, see below):
- **System**: Linux x86_64 with bash, git, curl. All Python wheels install on glibc ≥ 2.28 (Ubuntu 20.04: 2.31).
- **Python 3.11 or 3.12**: `setup.sh` takes the first of python3.12, python3.11, python3 that fits, or
  downloads one (`--standalone-python`). 3.13 has no binary wheel for `cloth-simulation-filter`.
- **R ≥ 4.0** (nanoparquet; developed with 4.5.2, target has 4.3.2): missing packages (data.table,
  terra, yaml, nanoparquet) are installed into `r-lib/`; mgcv ships with R. On Linux CRAN packages are compiled; `terra` then needs
  the GDAL, GEOS and PROJ development packages, unless it is already installed or precompiled
  packages are used (`VOXSOL_CRAN`, see below).
- **No PostgreSQL installation**: the client library comes with the `psycopg` wheel. A local server
  with PostGIS is only used by the database tests, which are skipped without one (and when the repo
  path is too long for a unix socket, > ~80 characters).

The repository has no remote yet. To copy it: `git bundle create voxsol.bundle --all` on this machine,
then `git clone voxsol.bundle voxsol` on the target (only committed work is included).
```bash
git clone <repo or voxsol.bundle> voxsol && cd voxsol
scripts/proxy.sh set http://proxy.example.com:8080 --no-proxy "localhost,127.0.0.1" [--ca corp-root-ca.pem]  # if behind a proxy
./setup.sh [python3.12]   # .venv (pip), R packages into r-lib/, machine-specific .Renviron
scripts/db.sh set db.example.local forschung your_user   # PostgreSQL connection (asks for the password)
source env.sh             # every new shell: caches/tmp inside the repo, proxy, DB service, activates .venv
pytest                    # 35 tests (~30 s; the database tests need a local PostgreSQL + PostGIS, else skipped)
scripts/proxy.sh check    # PyPI, CRAN, CAMS, CDS, PROJ, GitHub reachable?   scripts/db.sh check: database
```
R uses existing packages from your library; new ones go to `r-lib/`. Credentials for CAMS/ERA5 (only
needed for `radiation: cams|era5` or `reference: era5land`): copy `config/credentials.example.yaml` to
`secrets/credentials.yaml`.

### Ubuntu 20.04
Target system: Ubuntu 20.04 with R 4.3.2 and terra already installed, admin rights available. So only
Python needs attention: 20.04 ships Python 3.8, and the deadsnakes PPA no longer builds Python for it.
On a fresh 20.04, R (3.6) would also be too old.
- **Python**: `./setup.sh --standalone-python` downloads a pinned Python 3.12 build
  (python-build-standalone, SHA-256 checked) into `.python/` and builds `.venv` on it. No admin rights
  are needed; the proxy must let `github.com` and its download host (`release-assets.githubusercontent.com`)
  through. `setup.sh` without the flag stops with this hint if it finds no suitable Python.
- **R 4.3.2** works: the R code uses no base functions newer than 4.3 (`%||%` is defined in the repo),
  and `setup.sh` only adds data.table, yaml and nanoparquet if missing. They have no dependencies but are
  compiled, so build tools are needed (`sudo apt install r-base-dev`). mgcv is the version bundled with
  R (current CRAN mgcv needs R ≥ 4.4).
- **R on a fresh 20.04**: if `R --version` shows 3.6, an admin installs a current R from CRAN's Ubuntu repository
  (`focal-cran40`, R 4.5.x): https://cloud.r-project.org/bin/linux/ubuntu/. Behind the proxy, apt needs
  its own setting, e.g. `/etc/apt/apt.conf.d/95proxy` with `Acquire::https::Proxy "http://proxy.example.com:8080/";`.
- **terra** (already installed on the target; otherwise): either compile it (`sudo apt install libgdal-dev libgeos-dev libproj-dev`;
  focal has GDAL 3.0.4, enough for terra), or use precompiled packages from Posit Package Manager, which
  only need the runtime libraries `libgdal26 libgeos-c1v5 libproj15`:
  `VOXSOL_CRAN=https://packagemanager.posit.co/cran/__linux__/focal/latest ./setup.sh`
  (stored in `.Renviron`, used by `.Rprofile` for all later installs; the proxy must allow `packagemanager.posit.co`).

Typical sequence on the target:
```bash
git clone voxsol.bundle voxsol && cd voxsol      # path short enough, e.g. ~/voxsol
scripts/proxy.sh set http://proxy.example.com:8080 --no-proxy "localhost,127.0.0.1,.example.local" [--ca corp-root-ca.pem]
./setup.sh --standalone-python                  # Python 3.12 into .python/, venv, R packages
scripts/db.sh set db.example.local forschung your_user
source env.sh && pytest && scripts/proxy.sh check && scripts/db.sh check
voxsol synth && voxsol run config/synthetic.yaml && Rscript R/run_calibration.R config/synthetic.yaml --no-maps
```
The last line runs the whole chain on the synthetic site (a few minutes). It is the first end-to-end
run with the standalone Python and R 4.3.2, which were not available on the development machine.

### External data and network access
| what | needed when | source, location |
|---|---|---|
| Python wheels, R packages | `setup.sh` (once) | PyPI (`pypi.org`, `files.pythonhosted.org`), CRAN (`cloud.r-project.org`) or Posit Package Manager (`VOXSOL_CRAN`), through the proxy |
| Python 3.12 build | `setup.sh --standalone-python` (once, Ubuntu 20.04) | `github.com` (python-build-standalone, pinned release and SHA-256), into `.python/` |
| PostgreSQL | `voxsol db` | internal database server, port 5432, direct connection (not through the proxy) |
| radiation forcing | `forcing` step | `radiation: db`: the database, no internet. `cams`: SoDa/CAMS API (`api.soda-solardata.com`, free account, e-mail in `secrets/credentials.yaml`). `era5`: CDS (`cds_key`) |
| reference temperature | `forcing` step | `reference: db`: the database. `era5land`: CDS (`cds_key`). `file`: local CSV |
| geoid grid `de_bkg_gcg2016.tif` | only with `vertical_crs` (LAS heights in DHHN2016) | `cdn.proj.org`, into `data/external/proj/` (`prepare` stops and prints the `curl` command if it is missing) |
| external DEM | optional `horizon.dem` (distant ridges, mainly in valleys) | local file, read in place. It must cover the site ± `horizon.max_dist_m` (20 km); 10–30 m resolution is enough (Copernicus GLO-30, or the state DGM resampled; DGM1 tiles work as a VRT but are slow to read) |
| clear-sky model (Linke turbidity) | `radiation: db`, `clearsky` | bundled with pvlib, offline |

Downloads land in `data/external/` (gitignored). With `radiation: db` and `reference: db`, runs need no
internet access after `setup.sh`, apart from the one-time geoid grid when a vertical CRS is set.

### Corporate proxy
All proxy settings live in one gitignored file, `secrets/proxy.env` (template: `config/proxy.example.env`;
`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, optional `CA_BUNDLE`). It is applied by:

| consumer | covers |
|---|---|
| `env.sh` | shell, pip, git, curl, PROJ (exports upper- and lower-case variables) |
| `.Rprofile` | R sessions started in the repo root: `install.packages`, `download.file` (libcurl) |
| `voxsol` CLI | CAMS (pvlib) and CDS/ERA5 (cdsapi) downloads, also without sourcing `env.sh` |
| `scripts/proxy.sh set` | additionally `http.proxy` / `http.sslCAInfo` in this repository's `.git/config` |

For proxies with TLS inspection, `CA_BUNDLE` (corporate root CA, PEM) is appended to the system CA
bundle in `.cache/ca-bundle.pem`. Python, pip, R, git and curl are pointed at that bundle.
`scripts/proxy.sh show` prints the settings with credentials masked. `scripts/proxy.sh check` tests
PyPI, CRAN, CAMS, CDS and the PROJ CDN via curl, Python and R. `voxsol proxy --check` runs the Python part only.
The file's values override proxy variables inherited from the system.

### Database (TMS-4 data, plot locations, ERA5)
TMS-4 data, logger positions and the ERA5 reference come from the PostgreSQL database (schema:
[`docs/db/schema.sql`](docs/db/schema.sql)). `voxsol db` reads it in a read-only session and writes a
snapshot to `work/<site>/db/`. All later steps read only that snapshot, so runs are reproducible and
work offline. The connection is a libpq service: `secrets/pg_service.conf` (template
`config/pg_service.example.conf`) plus the password in `secrets/pgpass`, both written by `scripts/db.sh set`.
`env.sh` and `.Rprofile` export them, so psql and R use the same connection:
`DBI::dbConnect(RPostgres::Postgres(), service = "voxsol")`. PostgreSQL does not go through the HTTP proxy.

| database | becomes |
|---|---|
| `daten.tms_data_valid` + height/sensor lookup tables | `tms_long.parquet`: values in the period, logger/sensor end dates applied by the view, installation day dropped (`skip_days_after_start`) |
| `tms_deployments.locality_id` → `plots.be_verjuengungskreise_pt` | logger id (= `verjkr_id`) and position (point geometry in the site CRS; a Z value is used for the DTM check). Logger swaps at one locality are joined into one series |
| `be_plots` → `be_flaechen` → `lut.*` | site filter `database.versuchsflaeche`, optional treatment filter; treatment and site columns in `loggers.csv` |
| `externe_daten.met_point_verjkr` → `met_points`, `met_data` | `forcing.reference: db`: hourly t2m, lapse-rate corrected from each point's elevation to the site. `forcing.radiation: db`: hourly GHI → 15 min → direct/diffuse (Erbs) |

`voxsol db --check` shows what the export relies on: readable tables, sites, how many `locality_id`s
match a `verjkr_id`, the height labels (must match `calibration.sensors`), geometry SRIDs, and ERA5 sources.
Notes:
- The database role needs SELECT on the tables above, including `lut.versuchsflaechen` and
  `lut.behandlungseinheiten` for site and treatment names. Without the `lut` grants the export still
  works, but the site must be given by its numeric id.
- The database has no soil temperature, so with `reference: db` T1 is compared with t2m. This affects the
  day-to-day fit of the T1 model, not the spatial pattern used for the maps (see `docs/PLAN.md`).
- The time stamp convention of the hourly GHI is checked against clear-sky timing (`forcing.json`:
  `ghi_label`, `ghi_label_corr`).

## Workflow for a real site
```bash
source env.sh
cp config/site_example.yaml config/siteA.yaml      # edit paths, CRS, period, database.versuchsflaeche
voxsol db --check                                  # database access and the facts the export relies on
voxsol db config/siteA.yaml                        # TMS, logger positions, ERA5 -> work/siteA/db/
voxsol inspect config/siteA.yaml                   # check GPS time / pulses / trajectory / datum first
voxsol run config/siteA.yaml                       # prepare → voxelize → understory → horizon → forcing → trace → irradiance
voxsol validate config/siteA.yaml                  # modeled shadows vs. orthomosaic shadows
Rscript R/run_calibration.R config/siteA.yaml      # k profiling, CV vs. baselines, monthly ΔT maps
```
Single steps: `voxsol prepare|voxelize|understory|horizon|forcing|trace|irradiance config/siteA.yaml`.
To start from a later step: `voxsol run config/siteA.yaml --start trace`.

### Inputs
| input | notes |
|---|---|
| LAZ (DJI Terra) | needs `gps_time`; RGB used for green/dead canopy and understory greenness. Height datum → `vertical_crs`: `null` if the LAS heights are ellipsoidal (DJI Terra without a geoid model; the sbet is always ellipsoidal), `EPSG:7837` if they are DHHN2016. `voxsol inspect` flags a mix (scanner ~48 m off) |
| `sbet.out` / `sbet.txt` | enable trajectory export in DJI Terra; without it beams are assumed vertical |
| orthomosaic | RGB indices (ExG, GCC, GLI, VARI, Otsu green cover); `ortho_time` for `validate` |
| PostgreSQL (`database` section) | TMS-4 data, logger positions and ERA5 via `voxsol db` (see above); replaces the two rows below |
| TMS long table | `x, y, timestamp, value, height[, id]` with heights `15, 2, -8, -0-10cm` (mapping in `calibration.sensors`) |
| logger table (optional) | `x, y[, id, z, xy_sd, reference]`: RTK uncertainty per logger, RTK ground height (DTM check), open-area reference flags |
| external DEM (optional) | far horizon for mountain sites |

### Outputs (`work/<site>/`)
- `dtm.tif`, `voxels.npz`, `maps/*.tif`: DTM, extinction grid, static layers (canopy PAI green/non-green,
  canopy height, sky view horizontal/slope, understory descriptors, RGB indices, slope/aspect, edge fraction)
- `logger_irradiance.parquet`: 15-min SW at the top of the understory per logger (direct/diffuse,
  horizontal/slope; mean and sd over the RTK uncertainty disk)
- `logger_static.csv`: all static layers averaged over each logger's uncertainty disk
- `maps_daily.nc`: daily SW maps [MJ m⁻² d⁻¹] plus uncalibrated first-guess SW at 2/15 cm
- `calibration/`: `cv_summary.csv` (full model vs. no radiation / open GHI / sky view / canopy-only SW),
  `chosen_parameters.csv`, `k_profile.csv`, model RDS + summaries, diagnostic PNGs,
  `maps/dT_<sensor>_<stat>_<YYYY-MM>.tif` (mean offset and approximate SE)

## Synthetic demo
```bash
voxsol synth                                   # 1 ha disturbed stand, simulated L2 flight, 24 TMS loggers
voxsol run config/synthetic.yaml
python scripts/validate_synthetic.py           # pipeline vs. known truth
Rscript R/run_calibration.R config/synthetic.yaml
```
The synthetic temperatures are a toy model driven by the *true* 3D radiation at sensor height. They
test the chain end to end but say nothing about the strength of effects at your sites.

## Known limitations / to check with real data
- **DTM in dense herbs**: merged echoes lift the CSF ground (synthetic: +0.5 m under 0.6–0.95 m herbs).
  Provide RTK `z` of the loggers so `prepare` reports the DTM bias at each logger.
- Runtime/memory at 20 ha × 500 pts/m² is extrapolated, not measured (SOR and CSF on ~100 M points
  are the likely bottlenecks).
- Database export: tested against the schema dump with synthetic rows, not yet against the real
  database. Run `voxsol db --check` first: height labels, geometry SRIDs, ERA5 sources.
- CAMS/ERA5/ERA5-Land download code is untested here (no credentials). ERA5-Land grid elevation for
  the lapse-rate correction must be set manually (`forcing.reference_grid_elevation`).
- Not modeled: ground-reflected radiation, multiple scattering (only an optional √(1−σ) factor), sky
  anisotropy beyond Perez circumsolar.
- Map SE ignores covariance between days (approximate). Map predictions use one effective cos(z) per
  day for the understory beam term (error at loggers is reported in `understory_daily_approximation.csv`).
