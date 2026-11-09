# voxsol outputs: what every file and map means

All results of a site go to `work/<site>/` (never into the input folders). This page explains each
file and map layer: what it shows, its units and range, how it is derived, and what to watch out for.
Start with [Which map for what](#which-map-for-what); the rest is a reference by pipeline step.

## The model in one paragraph

The light reaching a TMS-4 logger is computed in two layers. **Above `h_cut`** (default 1 m), trees,
snags and shrubs are a 3D voxel grid of light extinction (λ per 0.25 m voxel, from the laser pulses).
Rays are traced from every map pixel and logger at **DTM + h_cut** ("top of the understory", *tou*)
towards the sun and the sky, through the voxels and over the terrain (and the far horizon, if a DEM is
given). This gives **SW_tou**, the shortwave radiation arriving on top of the understory. **Below `h_cut`**
the laser cannot resolve dense herbs and grass, so the understory is described per pixel by descriptors
(e.g. `us_index_15cm`, `ground_lift`). The calibration in R fits how strongly they attenuate light down to
the sensor:

    SW_ground = SW_dir · exp(−k · US / cos z) + SW_dif · exp(−k · US · 1.66)

Then it relates SW_ground to the measured temperature offsets (logger − ERA5 reference) and predicts
**ΔT maps**.

```
LAZ + trajectory ─ prepare ─ voxelize ─ understory ─ trace ─ irradiance ─┬─ R calibration ─ ΔT maps
TMS-4 / ERA5 (db) ───────────────────────── forcing ─────────────────────┘
```

## Conventions

| | |
|---|---|
| CRS | the site CRS from the config (e.g. EPSG:25832); heights orthometric |
| raster format | GeoTIFF, float32, nodata = NaN |
| grid A: 0.25 m (`grid.dx`) | full scanned extent: `dtm.tif`, `maps/canopy_*.tif` |
| grid B: 0.5 m (`trace.map_res`) | scanned extent minus `buffer_m` (40 m) on every side: all other `maps/*.tif`, `maps_daily.nc`, `sw_tou_h_mean_daily.tif`, `calibration/maps/*.tif` |
| time | UTC everywhere; "daily" = UTC day |
| radiation | daily sums in MJ m⁻² d⁻¹; 15-min series as mean irradiance in W m⁻² |
| `_h` / `_s` | on the horizontal plane / on the local slope plane (normal of the 1 m-smoothed DTM) |
| TMS-4 sensors | T3 air +15 cm, T2 surface +2 cm, T1 soil −8 cm, VWC soil moisture 0–15 cm |

## Which map for what

| question | file | notes |
|---|---|---|
| **How much warmer/cooler is a spot than the reference?** (the final microclimate covariate) | `calibration/maps/dT_<sensor>_<stat>_<YYYY-MM>.tif` | band 1 `dT_mean` [K], band 2 `se_approx` [K]; one file per sensor (T3/T2/T1), daily statistic (max/mean) and month |
| How much sun reaches the top of the understory? (insolation covariate, uncalibrated) | `sw_tou_h_mean_daily.tif` | mean daily SW_tou over the period [MJ m⁻² d⁻¹]; compare with the open-sky mean in `site_daily.csv` (`ghi`) |
| ... per day, direct vs. diffuse, horizontal vs. slope | `maps_daily.nc` | one band per day; see [irradiance](#irradiance) |
| How open is the sky (canopy + terrain)? | `maps/svf_h.tif` | 0–1, share of diffuse light passing; independent of weather |
| How dense is the tree/shrub layer above 1 m? | `maps/canopy_pai.tif`, `canopy_gap_vertical.tif`, `canopy_canopy_height.tif` | 0.25 m grid |
| Living vs. dead canopy | `maps/canopy_pai_green.tif`, `canopy_pai_nongreen.tif` | from RGB-colored points; reliable mainly for upper surfaces |
| How dense is the herb layer above a sensor? | `maps/us_index_15cm.tif`, `us_index_2cm.tif`, `us_height.tif` | the calibration picks the best understory descriptor (`chosen_parameters.csv`) |
| How green is the ground cover? | `maps/rgb_green_cover.tif`, `rgb_exg.tif` | from the orthomosaic; `rgb_green_cover` is a model covariate by default |
| Where are results less reliable? | `maps/edge_fraction.tif`, `maps/n_low.tif` | edge rays leave the scan; few returns mean noisy understory metrics |
| How good is the model? | `calibration/cv_summary.csv`, `calibration_*.png` | held-out-logger skill vs. simpler baselines |

## Reference by step

### db: database snapshot (`work/<site>/db/`, written by `voxsol db`)

| file | content |
|---|---|
| `tms_long.parquet` | TMS-4 values: `logger_id, x, y, timestamp` (UTC), `value, height, sensor_name, serial_number, deployment_id`. Only valid data (`tms_data_valid`), installation day dropped |
| `loggers.csv` | one row per logger: `id, x, y, z, xy_sd, reference` + plot, treatment, site, serial numbers, ERA5 point |
| `met_points.csv` | ERA5 (Open-Meteo) grid points linked to the loggers, with their elevation |
| `met_data.parquet` | ERA5 series: `met_point_id, variable` (t2m, ghi, precip), `time` (UTC), `value` |
| `db_export.json` | provenance and checks: dropped deployments and why, `height_to_sensor` (how each height label maps to T3/T2/T1/VWC), `values_per_height` (n, min, median, max per label: shows units, e.g. raw moisture counts), coverage |

Logger ids are the `locality_id` (= `verjkr_id`); overlapping deployments at one locality become `<locality>_<serial>`.

### prepare

| file | content |
|---|---|
| `dtm.tif` | digital terrain model [m, orthometric], 0.25 m, from CSF ground points (or LAS class 2) |
| `pulses.npz` | laser pulses: scanner position, ordered returns, ground flag (input for `voxelize`) |
| `points_low.npz` | returns below `h_cut` and ground returns, with height above ground and RGB (input for `understory`) |
| `points_canopy.npz` | colored returns above `h_cut` (input for green/non-green canopy) |
| `loggers.csv` | loggers inside the scan: `id, x, y, z, xy_sd, reference, ...` |
| `prepare.json` | `points_read`, `points_noise` (SOR), `ground_points`, `dtm_cells_with_ground` (share of DTM cells with a ground return; low = interpolated DTM), `trajectory_projection`, `pulses` (pulse statistics), `loggers_outside_scan`, `dtm_check_vs_logger_z` (DTM − RTK ground height at the loggers: bias/RMSE; positive bias = DTM on top of dense vegetation) |

### voxelize

| file | content |
|---|---|
| `voxels.npz` | `lam`: extinction coefficient λ [m⁻¹] per voxel (ny, nx, nz), terrain-following layers of `dz` from `h_cut` to `zmax` above ground; `meta` = grid geometry. Voxels with fewer than `pulse_min` entering pulses are filled from their neighbours |
| `voxels_green.npy` | share of green (ExG > 0.05) colored returns per voxel (NaN = no colored return) |
| `voxelize.json` | `voxels`, `voxels_filled`, `fraction_filled` (occluded/unsampled share), `lambda_max` |

Map layers (grid A, 0.25 m, full scan extent):

| layer | unit / range | meaning |
|---|---|---|
| `canopy_pai` | m² m⁻², ≥ 0 | plant area index (leaves + wood) above `h_cut`: Σλ·dz / G with G = 0.5 (spherical leaf angles). Open ground 0, closed beech canopy ~4–8 |
| `canopy_gap_vertical` | 0–1 | probability that a vertical beam passes the layer above `h_cut`: exp(−Σλ·dz). 1 = open |
| `canopy_canopy_height` | m above ground | top of the highest voxel with λ > 0.05 m⁻¹; 0 where nothing is above `h_cut`; steps of `dz` |
| `canopy_pai_green` | m² m⁻² | part of `canopy_pai` from green (living) material |
| `canopy_pai_nongreen` | m² m⁻² | part from non-green material: snags, stems, dead crowns. Colors come from nadir images, so interior voxels inherit their column's green share |

### understory

Map layers (grid B, 0.5 m). "Low returns" = all returns below `h_cut`, ground returns counted at height 0.

| layer | unit / range | meaning |
|---|---|---|
| `n_low` | count | low returns per pixel. Data density: the metrics below are noisy where it is small |
| `us_height` | m, 0–`h_cut` | 95th percentile height of low vegetation returns (> 5 cm); 0 = no low vegetation detected |
| `frac_0_0.15`, `frac_0.15_0.5`, `frac_0.5_1` | 0–1 | share of low returns that are vegetation in the height band [m] (0.05–0.15, 0.15–0.5, 0.5–1). Vertical profile of the herb layer |
| `us_index_2cm`, `us_index_15cm` | 0–3.9 | **understory index above the sensor**: −ln(share of low returns below 2 / 15 cm). 0 = nothing above the sensor; larger = more vegetation between the sensor and `h_cut`. A contact-frequency (LAI-like) proxy; capped at 3.9 (2 % gap) |
| `us_green_frac` | 0–1 | share of low vegetation returns that are green (ExG > 0.05 from the LAS colors) |
| `penetration` | 0–1 | share of pulses (last returns in the pixel) that reached the ground. 1 = open, 0 = no pulse gets through; NaN = no pulse |
| `ground_lift` | m, ≥ 0 | DTM above the local lower envelope of ground returns (4 m window). Where dense grass merges with the ground echo, the "ground" sits on top of the vegetation; > ~0.1 m points to dense, impenetrable herbs |
| `ground_roughness` | m | robust vertical spread (IQR/1.349) of ground returns in the pixel; high in dense herbs and on rough micro-relief |
| `rgb_exg` | ~ −0.2–0.6 | excess green 2g − r − b of chromatic coordinates, mean over the ortho pixels. > ~0.1 green vegetation; ~0 soil, litter, dead wood |
| `rgb_gcc` | 0–1 | green chromatic coordinate g/(r+g+b); 0.33 = grey |
| `rgb_gli` | −1–1 | green leaf index (2G − R − B)/(2G + R + B) |
| `rgb_vari` | −1–1 | VARI (G − R)/(G + R − B); sensitive to illumination |
| `rgb_green_cover` | 0–1 | share of ortho pixels classified as green (ExG above the site's Otsu threshold). Default static covariate of the model |

The `rgb_*` layers are NaN outside the orthomosaic. The ortho shows the top-most surface: under tree
crowns it describes the crowns, not the herb layer.

### horizon (only with `horizon.dem`)

| file | content |
|---|---|
| `horizon.npz` | far-horizon elevation angle [deg] per azimuth (1° steps) on a 25 m grid, from the external DEM up to 20 km with earth curvature. Sun positions below it count as blocked; sky directions below it get zero weight |
| `horizon.json` | `horizon_max_deg`, `horizon_mean_deg` |

### forcing

| file | content |
|---|---|
| `forcing.parquet` | open-sky radiation at the site per interval (15 min or 1 h, index = interval start, UTC): `ghi` global horizontal, `dni` direct normal, `dhi` diffuse horizontal [W m⁻², interval mean] |
| `reference.parquet` | reference air temperature `t2m` [°C] per hour (ERA5 via the database: lapse-corrected from each ERA5 point's elevation to the site's mean elevation, then averaged) or the configured file/ERA5-Land variables |
| `forcing.json` | site `lat, lon, altitude`, radiation `source`, `step_s`, number of intervals; for the database GHI the detected time-stamp convention (`ghi_label`, `ghi_label_corr`) |

### trace

| file | content |
|---|---|
| `logger_trace.npz` | per logger disk point (25 points within 2·`xy_sd`): transmittance towards the exact sun position of every daytime interval (`T_sun`) and diffuse transmittances (`T_dif_h`, `T_dif_s`) |
| `map_trace.npz`, `map_Tbins.npy` | per map pixel: transmittance towards every visited sun-path bin (1° × 1°, uint8 = T·255, memory-mapped), surface normals, sky view; input for `irradiance` |
| `trace.json` | `map_pixels`, `sun_bins`, `sky_dirs`, `edge_fraction_mean` |

Map layers (grid B, 0.5 m), all weather-independent:

| layer | unit / range | meaning |
|---|---|---|
| `svf_h` | 0–1 | **sky view through canopy and terrain** at DTM + `h_cut`, horizontal plane: cosine-weighted mean transmittance over 400 sky directions, i.e. the share of isotropic diffuse light that reaches the top of the understory. 1 = open sky |
| `svf_s` | 0–(1 + cos slope)/2 | the same for the slope-parallel plane (an unobstructed slope sees only part of the sky) |
| `edge_fraction` | 0–1 | cosine-weighted share of sky directions whose ray left the scanned volume sideways below the canopy top. Beyond the scan everything counts as open, so svf and SW are overestimated where this is high; treat values > ~0.05 with caution |
| `slope_deg` | deg | slope of the 1 m-smoothed DTM |
| `aspect_deg` | deg, 0–360 | direction the slope faces, clockwise from north (180 = south-facing); arbitrary on flat ground. (Before 2026-11-09 this map held the upslope direction; `irradiance` rewrites it) |

### irradiance

`maps_daily.nc` (NetCDF, CF, grid B; dimensions time × y × x, one time step per UTC day):

| variable | unit | meaning |
|---|---|---|
| `sw_dir_h` | MJ m⁻² d⁻¹ | direct sunlight (beam + circumsolar) on the horizontal plane at the top of the understory, after shading by canopy and terrain |
| `sw_dir_s` | MJ m⁻² d⁻¹ | the same on the slope plane (more on south-facing slopes) |
| `sw_dif_h` | MJ m⁻² d⁻¹ | isotropic diffuse light on the horizontal plane = `svf_h` × the day's open-sky isotropic diffuse |
| `sw_dif_s` | MJ m⁻² d⁻¹ | the same with `svf_s` |
| `sw_ground_2cm_firstguess`, `sw_ground_15cm_firstguess` | MJ m⁻² d⁻¹ | **uncalibrated** SW at sensor height with a default understory k = 0.5 on the unscaled `us_index_*`. Only for a first look; the calibrated attenuation is applied in the R model |

SW_tou (total at the top of the understory) = `sw_dir_h + sw_dif_h`. Relative transmittance = SW_tou / open-sky
GHI of the same day (`site_daily.csv`).

| file | content |
|---|---|
| `sw_tou_h_mean_daily.tif` | mean of the daily SW_tou (horizontal) over the period [MJ m⁻² d⁻¹]: **the single overview map of insolation** |
| `site_daily.csv` | per day, open sky: `ghi` (global), `iso` (isotropic diffuse), `beam_h` (beam incl. circumsolar on the horizontal) [MJ m⁻²], `cosz_eff` (beam-energy-weighted cos of the sun zenith, used for the understory term in the maps) |
| `logger_irradiance.parquet` | per logger and interval (15 min), mean over its uncertainty disk [W m⁻²]: `ghi` open sky; `sw_dir_h, sw_dif_h, sw_tou_h` and `_s` (slope plane) at the top of the understory; `sw_tou_h_sd` spread across the disk (position uncertainty); `trans_h` = sw_tou_h / ghi (when ghi > 10); `zenith, cosz, beam_n, iso` |
| `logger_static.csv` | per logger: every `maps/*.tif` layer averaged over its uncertainty disk, plus `svf_h_point, svf_s_point, edge_fraction_point` traced at the disk points; the static predictors of the calibration |
| `irradiance.json` | `days`, `logger_rows`, `sw_tou_h_mean`, `ghi_mean` |

### validate (`voxsol validate`, needs `inputs.ortho_time`)

| output | meaning |
|---|---|
| `maps/shadow_model_ortho_time.tif` | modeled direct transmittance (0–1) towards the sun at the ortho acquisition time, 0.3 m above ground |
| printed JSON | agreement of modeled shadow (T < 0.5) with dark ortho pixels in open areas: `agreement`, `kappa` (well above 0 = geometry fits), `spearman_T_brightness` |

### calibration (`Rscript R/run_calibration.R`, folder `calibration/`)

Responses: daily **offset = logger − reference** (daily max or mean of T3, T2, T1; days with ≥ 90 % of the values).

| file | content |
|---|---|
| `chosen_parameters.csv` | per sensor/statistic: `us_index` (understory descriptor chosen by CV), `k` (its attenuation coefficient on the descriptor scaled to its 5–95 % map range), `tau_days` (T1: memory of past insolation), `rho` (day-to-day residual autocorrelation), `covariates` |
| `cv_summary.csv` | held-out-logger skill (leave-one-logger-out) of six models: `no_radiation` (season + logger only), `open_ghi` (+ open-sky GHI), `sky_view` (+ `svf_h`), `sw_tou` (canopy insolation, no understory), `full` (SW at sensor height + covariates: the model used for the maps), `full_vwc` (+ soil moisture, explanatory only). Columns: `rmse` [K], `r2` (daily values), `spatial_r2` (per-logger means: how well the model ranks microsites), `bias`, `n`. The gain of `full` over `sky_view`/`sw_tou` is what the 3D insolation and understory add |
| `k_profile.csv` | CV RMSE for every descriptor × k (× tau): how sharply k is determined |
| `understory_daily_approximation.csv` | error of the daily effective-cos z shortcut used in the maps vs. the exact 15-min integration at the loggers (median/p95 relative error, bias) |
| `model_<sensor>_<stat>.rds` | fitted `mgcv::bam` (with AR(1)) used for the maps: `delta ~ s(sw_ground) + covariates (linear) + s(doy) + s(logger_id, bs = "re")` |
| `model_<sensor>_<stat>_explanatory.rds`, `summary_*_explanatory.txt` | the same plus `s(vwc)` and `sw_ground:vwc` (evaporative cooling); not used for maps |
| `summary_<sensor>_<stat>.txt` | `summary()` of the model: effect sizes and significance |
| `model_data_<sensor>_<stat>.csv` | the calibration table: `logger_id, date, obs, ref, delta` [°C/K], `ghi, sw_tou, sw_dir, sw_dif, sw_ground` [MJ m⁻² d⁻¹], `vwc`, covariates, `doy`, `pred_cv` (held-out prediction) |
| `calibration_<sensor>_<stat>.png` | left: k profile per descriptor; right: held-out prediction vs. observed offset |
| `maps/dT_<sensor>_<stat>_<YYYY-MM>.tif` | **predicted temperature offset maps**, monthly mean of the daily prediction, population level (no logger effect). Band 1 `dT_mean` [K], band 2 `se_approx` [K]: root mean square of the daily standard errors of the fitted mean (parameter uncertainty of the population-level model; excludes the day-to-day residual scatter and the logger random effect) |

Reading a ΔT map: `dT_T3_max_2025-07.tif` = 3 K means that in July 2025, the daily maximum at 15 cm above
ground in that pixel is on average 3 K above the daily maximum of the ERA5 reference (t2m). The absolute
temperature is reference + ΔT. The maps carry the seasonal term `s(doy)`; compare pixels within one map,
or the same pixel across months.

## Checks before trusting the maps

- `prepare.json` → `dtm_check_vs_logger_z`: a DTM bias of several dm means the ground model sits on dense vegetation.
- `voxelize.json` → `fraction_filled`: a high share of occluded voxels means the λ values under dense crowns are interpolated.
- `edge_fraction.tif`: keep the analysis area where it is small.
- `voxsol validate`: kappa well above 0.
- `cv_summary.csv`: `full` should beat `sky_view` and `sw_tou`. If it does not, the understory/insolation adds nothing over simpler covariates.
- `understory_daily_approximation.csv`: median relative error of a few percent is fine.
