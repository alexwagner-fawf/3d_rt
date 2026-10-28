# Monthly maps of predicted temperature offsets from the daily SW maps and static layers.
# Uses the population-level model (logger random effect excluded). The understory beam term uses
# the day's energy-weighted cos(z) (see approximation_check() for its error at the loggers).

align_to <- function(r, template) {
  if (terra::compareGeom(r, template, stopOnError = FALSE)) return(r)
  terra::resample(r, template, method = "average")
}

predict_maps <- function(cfg, fit) {
  info <- attr(fit, "voxsol")
  nc <- file.path(cfg$workdir, "maps_daily.nc")
  dir_h <- terra::rast(nc, subds = "sw_dir_h")
  dif_h <- terra::rast(nc, subds = "sw_dif_h")
  dates <- data.table::as.IDate(terra::time(dir_h))
  site_daily <- read_site_daily(cfg)
  cosz <- site_daily$cosz_eff[match(dates, site_daily$date)]
  cosz[!is.finite(cosz)] <- 0.5
  maps <- file.path(cfg$workdir, "maps")
  template <- dir_h[[1]]
  U <- us_normalized(terra::values(align_to(terra::rast(file.path(maps, paste0(info$us_col, ".tif"))), template))[, 1],
                     info$us_scaling)
  covs <- lapply(info$covariates, function(v) terra::values(align_to(terra::rast(file.path(maps, paste0(v, ".tif"))), template))[, 1])
  names(covs) <- info$covariates
  lev <- info$logger_levels
  a <- if (info$tau > 0) 1 - exp(-1 / info$tau) else 1
  state <- NULL
  month <- format(dates, "%Y-%m")
  acc <- list()
  for (i in seq_along(dates)) {
    sw <- terra::values(dir_h[[i]])[, 1] * exp(-info$k * U / max(cosz[i], 0.05)) +
      terra::values(dif_h[[i]])[, 1] * exp(-info$k * U * DIFFUSE_FACTOR)
    state <- if (is.null(state)) sw else state + a * (sw - state)
    nd <- data.frame(sw_ground = state, doy = data.table::yday(dates[i]), covs,
                     logger_id = factor(lev[1], levels = lev))
    ok <- stats::complete.cases(nd)
    p <- stats::predict(fit, newdata = nd[ok, , drop = FALSE], exclude = "s(logger_id)", se.fit = TRUE)
    m <- month[i]
    if (is.null(acc[[m]])) acc[[m]] <- list(sum = numeric(length(sw)), var = numeric(length(sw)), n = 0L)
    fill <- rep(NA_real_, length(sw)); fill[ok] <- p$fit
    vfill <- rep(NA_real_, length(sw)); vfill[ok] <- p$se.fit^2
    acc[[m]]$sum <- acc[[m]]$sum + fill
    acc[[m]]$var <- acc[[m]]$var + vfill
    acc[[m]]$n <- acc[[m]]$n + 1L
  }
  files <- character()
  for (m in names(acc)) {
    r <- terra::rast(template, nlyrs = 2)  # geometry only (no units/time of the SW template)
    terra::values(r) <- cbind(acc[[m]]$sum / acc[[m]]$n, sqrt(acc[[m]]$var / acc[[m]]$n))
    names(r) <- c("dT_mean", "se_approx")
    terra::units(r) <- c("K", "K")
    f <- file.path(cfg$outdir, "maps", sprintf("dT_%s_%s_%s.tif", info$sensor, info$stat, m))
    terra::writeRaster(r, f, overwrite = TRUE)
    files <- c(files, f)
  }
  files
}
