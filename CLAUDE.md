# voxsol: ULS-based below-canopy insolation as a microclimate covariate

Full design: `docs/PLAN.md`.

## Containment rule (hard)
Write **only** inside the repository root (here `/home/alex/Dokumente/3d_rt`). Ask the user before
touching anything outside. Agreed exceptions:
- The base interpreter given to `setup.sh` (here `/home/alex/.local/share/r-miniconda/bin/python3`) is
  used read-only to create `.venv/`.
- Input data (LAZ, sbet, ortho, TMS-4, logger table) is read in place, read-only, via paths in the site config.
- Already-installed R packages (personal/system library) are only read; new ones go to `r-lib/`.
- `.Rprofile` re-reads `~/.Renviron` (read-only) so the user's personal R settings are kept.

How: `source env.sh` before any shell work (sets TMPDIR, pip/numba/PROJ/XDG caches into `.cache/`,
proxy from `secrets/proxy.env`, activates `.venv`). R picks up `.Renviron` (machine-specific, written by
`setup.sh`, not versioned) + `.Rprofile` when started from the repo root. Credentials live in
`secrets/` (gitignored) and are passed explicitly, never via `~/.cdsapirc`. Downloads go to
`data/external/`, intermediate results to `work/<site>/`. Git settings only via `git config --local`.
Containment check: `./check_containment.sh` (empty output = nothing written outside).

## Corporate proxy
Runs behind a corporate proxy on the target machine. Settings: `secrets/proxy.env` (template
`config/proxy.example.env`, helper `scripts/proxy.sh set|show|check|unset`). Read by `env.sh`,
`.Rprofile`, `setup.sh` and `voxsol.netconfig.apply_proxy_env()` (called in `ensure_local_env`).
Any new network access must use requests/libcurl so these settings apply; never hard-code proxies.

## Database
TMS-4 data, logger positions (`plots.be_verjuengungskreise_pt`) and ERA5 via Open-Meteo
(`externe_daten`) come from PostgreSQL/PostGIS (schema dump `docs/db/schema.sql`; the original export
`rstudio-export/` is local and gitignored, it already exists on the live system). Only `voxsol db` connects (`src/voxsol/io/db.py`, read-only session) and writes the
snapshot `work/<site>/db/`; everything else reads the snapshot. Never write to the database. Connection:
libpq service `secrets/pg_service.conf` + `secrets/pgpass` (`scripts/db.sh`). Confirmed by the user
(2026-11-02): `tms_deployments.locality_id` = `verjkr_id`; SELECT on the `lut` name tables will be granted.

## Layout
- `src/voxsol/`: Python core (numba ray marching, voxelization, tracing, forcing, `io/db.py`), CLI `voxsol`.
  Steps live in `pipeline.py` (`STEPS`), the numba kernels in `raymarch.py`, test/demo scenes in `synthetic.py`.
- `R/`: calibration (`run_calibration.R` → `calibrate.R`, `predict_maps.R`), data.table + mgcv + terra.
- `tests/`: pytest with analytic and synthetic scenes (`source env.sh && pytest`, ~30 s).
- Demo: `voxsol synth && voxsol run config/synthetic.yaml && Rscript R/run_calibration.R config/synthetic.yaml`.

## Target system
Ubuntu 20.04 behind the corporate proxy, R 4.3.2 with terra installed, admin rights available (user,
2026-11-02). System Python is 3.8: `./setup.sh --standalone-python` puts a pinned, checksum-verified
Python 3.12 into `.python/`. `VOXSOL_CRAN` (in `.Renviron`) can switch R installs to Posit Package
Manager binaries. Keep the R code compatible with R 4.3: no base functions from 4.4+ (`%||%` is defined
in `R/io_voxsol.R`), no new dependencies that need R ≥ 4.4 (current CRAN mgcv/Matrix do; use the bundled ones).
Wheels must stay installable on glibc 2.31 (check new dependencies with `pip install --dry-run
--only-binary=:all: --platform manylinux_2_31_x86_64 ...`; pip needs the older manylinux tags listed too).

## Deployment
`origin` is the GitHub repository; the target clones it and updates with `git pull` (commits must be
pushed to reach it). `dist/` bundles are only an offline fallback.

## Status
- First real site run on the target (2026-11-03): `prepare` and `voxelize` succeeded (34.5 M points,
  680 M voxels); `understory` ran out of memory reading the 5 Gpx ortho at once, fixed by streaming it
  (`rgb.ortho_blocks`). Geoid grids go to `data/external/proj/`.
- First real calibration attempt (2026-11-09): all TMS values were dropped because the database height
  labels (`air 15 cm`, `air 2 cm`, `soil 8 cm`, `soil 0-15 cm`) did not match `calibration.sensors`. Labels
  are now normalized (`height_key` in `io/loggers.py` and `R/loggers.R`, same rules, tested for parity).
  Soil-moisture units in the database are unknown (raw counts are skipped with a warning). Output
  meanings: `docs/OUTPUTS.md`; QGIS export: `voxsol qgis` (`src/voxsol/qgis.py`, layer titles/styles in
  `MAPS`). When adding or changing an output, update both. QGIS runs on another PC (results are copied).
  PyQGIS is installed on the development machine only: run it with HOME/XDG redirected into `.cache/`.
- The target machine has less RAM than the development machine: never read a whole orthomosaic, DEM
  or other large raster into memory; stream it in windows (`rgb.ortho_blocks`, `horizon.load_dem`).
- TMS-4 data: PostgreSQL (`voxsol db`, tested against the schema dump only) or a long-format file
  x, y, timestamp, value, height (15, 2, -8, "-0-10cm"), EPSG:25832.

## Conventions
- All timestamps UTC. Coordinates in the site's projected CRS (e.g. EPSG:25832), heights orthometric.
- Two-layer light model: 3D voxels above `h_cut` (default 1 m), per-pixel understory model below.
