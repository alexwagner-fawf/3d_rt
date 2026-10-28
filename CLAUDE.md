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

## Layout
- `src/voxsol/`: Python core (numba ray marching, voxelization, tracing, forcing), CLI `voxsol`.
  Steps live in `pipeline.py` (`STEPS`), the numba kernels in `raymarch.py`, test/demo scenes in `synthetic.py`.
- `R/`: calibration (`run_calibration.R` → `calibrate.R`, `predict_maps.R`), data.table + mgcv + terra.
- `tests/`: pytest with analytic and synthetic scenes (`source env.sh && pytest`, ~30 s).
- Demo: `voxsol synth && voxsol run config/synthetic.yaml && Rscript R/run_calibration.R config/synthetic.yaml`.

## Status
- Built and verified on synthetic data only (as of 2026-10-28); no real data has been inspected yet.
  Next step with real data: `voxsol inspect` (pulse grouping, sbet time base/datum).
- TMS-4 data comes in long format: x, y, timestamp, value, height (15, 2, -8, "-0-10cm"), EPSG:25832.

## Conventions
- All timestamps UTC. Coordinates in the site's projected CRS (e.g. EPSG:25832), heights orthometric.
- Two-layer light model: 3D voxels above `h_cut` (default 1 m), per-pixel understory model below.
