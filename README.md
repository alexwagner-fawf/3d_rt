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
```bash
git clone <repo> voxsol && cd voxsol
scripts/proxy.sh set http://proxy.example.com:8080 --no-proxy "localhost,127.0.0.1" [--ca corp-root-ca.pem]  # if behind a proxy
./setup.sh [python3.12]   # .venv (pip), R packages into r-lib/, machine-specific .Renviron
source env.sh             # every new shell: caches/tmp inside the repo, proxy, activates .venv
pytest                    # 26 tests (~30 s)
```
R uses existing packages from your library; new ones go to `r-lib/`. Credentials for CAMS/ERA5:
copy `config/credentials.example.yaml` to `secrets/credentials.yaml`.

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

## Workflow for a real site
```bash
source env.sh
cp config/site_example.yaml config/siteA.yaml      # edit paths, CRS, period
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
| LAZ (DJI Terra) | needs `gps_time`; RGB used for green/dead canopy and understory greenness |
| `sbet.out` / `sbet.txt` | enable trajectory export in DJI Terra; without it beams are assumed vertical |
| orthomosaic | RGB indices (ExG, GCC, GLI, VARI, Otsu green cover); `ortho_time` for `validate` |
| TMS long table | `x, y, timestamp, value, height` with heights `15, 2, -8, -0-10cm` (mapping in `calibration.sensors`) |
| logger table (optional) | `x, y[, z, xy_sd, reference]`: RTK uncertainty per logger, RTK ground height (DTM check), open-area reference flags |
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
- CAMS/ERA5/ERA5-Land download code is untested here (no credentials). ERA5-Land grid elevation for
  the lapse-rate correction must be set manually (`forcing.reference_grid_elevation`).
- Not modeled: ground-reflected radiation, multiple scattering (only an optional √(1−σ) factor), sky
  anisotropy beyond Perez circumsolar.
- Map SE ignores covariance between days (approximate). Map predictions use one effective cos(z) per
  day for the understory beam term (error at loggers is reported in `understory_daily_approximation.csv`).
