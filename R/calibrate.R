# Linking modeled insolation to TMS-4 temperature offsets.
#
# SW at sensor height = SW_tou_dir * exp(-k * US / cos z) + SW_tou_dif * exp(-k * US * 1.66), integrated
# exactly over the 15-min logger series. US is one of several understory descriptors (map layers),
# scaled to (x - q05) / (q95 - q05) over the map. Descriptor and k are chosen by grouped CV per
# sensor/statistic; T1 additionally uses an exponentially weighted SW history (time constant tau).

DIFFUSE_FACTOR <- 1.66
MJ <- 1e-6

n_cores <- function() max(1L, min(8L, parallel::detectCores() - 2L))

us_scaling <- function(cfg, layer) {
  r <- terra::rast(file.path(cfg$workdir, "maps", paste0(layer, ".tif")))
  q <- stats::quantile(terra::values(r), c(0.05, 0.95), na.rm = TRUE, names = FALSE)
  c(q05 = q[1], q95 = max(q[2], q[1] + 1e-9))
}

us_normalized <- function(x, scaling) pmax(x - scaling[["q05"]], 0) / (scaling[["q95"]] - scaling[["q05"]])

daily_base <- function(irr) {
  step <- attr(irr, "step_s")
  irr[, .(ghi = sum(ghi) * step * MJ, sw_tou = sum(sw_tou_h) * step * MJ,
          sw_dir = sum(sw_dir_h) * step * MJ, sw_dif = sum(sw_dif_h) * step * MJ),
      by = .(logger_id, date)]
}

daily_sw_ground <- function(irr, us, k) {
  step <- attr(irr, "step_s")
  u <- us[irr$logger_id]
  sw <- irr$sw_dir_h * exp(-k * u / pmax(irr$cosz, 0.05)) + irr$sw_dif_h * exp(-k * u * DIFFUSE_FACTOR)
  data.table::data.table(logger_id = irr$logger_id, date = irr$date, sw = sw)[
    , .(sw_ground = sum(sw) * step * MJ), by = .(logger_id, date)]
}

ewma_days <- function(x, tau) {
  if (tau <= 0 || length(x) < 2) return(x)
  a <- 1 - exp(-1 / tau)
  as.numeric(stats::filter(a * x, 1 - a, method = "recursive", init = x[1]))
}

daily_response <- function(tms, sensor_name, stat, min_cov) {
  f <- switch(stat, max = max, mean = mean, min = min)
  x <- tms[sensor == sensor_name & !is.na(ref)]
  step <- as.numeric(stats::median(diff(sort(unique(x$time)))), units = "secs")
  d <- x[, .(obs = f(value), ref = f(ref), n = .N),
         by = .(logger_id, date = data.table::as.IDate(time, tz = "UTC"))]
  d <- d[n >= min_cov * 86400 / step]
  d[, delta := obs - ref]
  d[, n := NULL]
  d[]
}

daily_vwc <- function(tms) {
  if (!"VWC" %in% tms$sensor) return(NULL)
  tms[sensor == "VWC", .(vwc = mean(value)), by = .(logger_id, date = data.table::as.IDate(time, tz = "UTC"))]
}

usable_covariates <- function(static, cov) {
  cov <- intersect(cov, names(static))
  cov[vapply(cov, function(v) all(is.finite(static[[v]])) && stats::var(static[[v]]) > 0, logical(1))]
}

assemble <- function(resp, base, sw, vwc, static, cov, tau = 0) {
  d <- merge(resp, base, by = c("logger_id", "date"))
  d <- merge(d, sw, by = c("logger_id", "date"))
  if (!is.null(vwc)) d <- merge(d, vwc, by = c("logger_id", "date"), all.x = TRUE)
  d <- merge(d, static[, c("logger_id", unique(c(cov, "svf_h"))), with = FALSE], by = "logger_id")
  data.table::setorder(d, logger_id, date)
  if (tau > 0) d[, c("sw_ground", "sw_tou", "ghi") := lapply(.SD, ewma_days, tau = tau),
                 by = logger_id, .SDcols = c("sw_ground", "sw_tou", "ghi")]
  d[, doy := data.table::yday(date)]
  d[, logger_id := factor(logger_id)]
  d[]
}

model_terms <- function(name, cov = character()) {
  base <- c("s(doy, k = 8)", "s(logger_id, bs = 're')")
  switch(name,
         no_radiation = base,
         open_ghi = c("s(ghi, k = 6)", base),
         sky_view = c("s(ghi, k = 6)", "s(svf_h, k = 4)", base),
         sw_tou = c("s(sw_tou, k = 6)", base),
         full = c("s(sw_ground, k = 6)", cov, base),
         # parametric interaction: stable for held-out loggers outside the training moisture range
         full_vwc = c("s(sw_ground, k = 6)", cov, "s(vwc, k = 4)", "sw_ground:vwc", base))
}

fit_model <- function(d, terms, rho = 0) {
  f <- stats::as.formula(paste("delta ~", paste(terms, collapse = " + ")))
  if (rho > 0) {
    mgcv::bam(f, data = d, method = "fREML", rho = rho, AR.start = !duplicated(d$logger_id))
  } else {
    mgcv::bam(f, data = d, method = "fREML")
  }
}

make_folds <- function(ids, n_folds, seed = 1) {
  u <- levels(ids)
  fold <- if (n_folds <= 0 || n_folds >= length(u)) seq_along(u) else {
    set.seed(seed)
    sample(rep_len(seq_len(n_folds), length(u)))
  }
  stats::setNames(fold, u)
}

cv_predict <- function(d, terms, folds) {
  pred <- rep(NA_real_, nrow(d))
  for (f in unique(folds)) {
    test_ids <- names(folds)[folds == f]
    is_test <- d$logger_id %in% test_ids
    train <- d[!is_test]
    train[, logger_id := droplevels(logger_id)]
    m <- fit_model(train, terms)
    test <- d[is_test]
    # population-level prediction: the random effect of a held-out logger is unknown
    test[, logger_id := factor(levels(train$logger_id)[1], levels = levels(train$logger_id))]
    pred[is_test] <- stats::predict(m, newdata = test, exclude = "s(logger_id)")
  }
  pred
}

par_lapply <- function(x, fun) {
  # one fork per worker (prescheduled); fails loudly instead of returning try-error objects
  out <- parallel::mclapply(x, fun, mc.cores = n_cores(), mc.preschedule = TRUE)
  bad <- vapply(out, inherits, logical(1), what = "try-error")
  if (any(bad)) stop(out[[which(bad)[1]]])
  out
}

cv_metrics <- function(obs, pred, ids) {
  ok <- is.finite(obs) & is.finite(pred)
  o <- obs[ok]; p <- pred[ok]; g <- droplevels(ids[ok])
  mo <- tapply(o, g, mean); mp <- tapply(p, g, mean)
  data.table::data.table(rmse = sqrt(mean((o - p)^2)), r2 = 1 - sum((o - p)^2) / sum((o - mean(o))^2),
                         spatial_r2 = 1 - sum((mo - mp)^2) / sum((mo - mean(mo))^2),
                         bias = mean(p - o), n = length(o))
}

lag1_rho <- function(res, ids) {
  r0 <- res[-length(res)]; r1 <- res[-1]
  same <- ids[-length(ids)] == ids[-1]
  max(0, min(0.95, stats::cor(r0[same], r1[same])))
}

calibrate_one <- function(cfg, resp, irr, base, static, vwc, sensor_name, stat) {
  cal <- cfg$calibration
  candidates <- intersect(unlist(cal$us_candidates), names(static))
  scalings <- lapply(stats::setNames(candidates, candidates), function(v) us_scaling(cfg, v))
  us_of <- function(v) stats::setNames(us_normalized(static[[v]], scalings[[v]]), static$logger_id)
  cov <- usable_covariates(static, unlist(cal$static_covariates))
  taus <- if (sensor_name == "T1") unlist(cal$tau_days_grid) else 0
  ids0 <- factor(unique(resp$logger_id))
  prof_folds <- make_folds(ids0, min(6L, length(levels(ids0))))
  # k = 0 is identical for every descriptor: evaluate it once
  grid <- rbind(data.table::CJ(us_col = candidates[1], k = 0, tau = taus),
                data.table::CJ(us_col = candidates, k = setdiff(unlist(cal$k_grid), 0), tau = taus))
  sw_for <- function(v, k) daily_sw_ground(irr, us_of(v), k)
  grid[, cv_rmse := unlist(par_lapply(seq_len(nrow(grid)), function(i) {
    d <- assemble(resp, base, sw_for(grid$us_col[i], grid$k[i]), NULL, static, cov, grid$tau[i])
    p <- cv_predict(d, model_terms("full", cov), prof_folds[levels(d$logger_id)])
    sqrt(mean((d$delta - p)^2, na.rm = TRUE))
  }))]
  if (!any(is.finite(grid$cv_rmse)))
    stop(sensor_name, " ", stat, ": no model could be fitted in the k profiling (too few loggers or days?)", call. = FALSE)
  best <- grid[which.min(cv_rmse)]
  if (best$k == max(grid$k)) warning(sensor_name, " ", stat, ": best k is at the upper end of k_grid; extend it")
  us_col <- best$us_col
  d <- assemble(resp, base, sw_for(us_col, best$k), vwc, static, cov, best$tau)
  folds <- make_folds(d$logger_id, cal$cv_folds)
  models <- c("no_radiation", "open_ghi", "sky_view", "sw_tou", "full")
  if (!is.null(vwc) && any(is.finite(d$vwc))) models <- c(models, "full_vwc")
  preds <- par_lapply(models, function(m) {
    dd <- if (m == "full_vwc") d[is.finite(vwc)] else d
    cv_predict(dd, model_terms(m, cov), folds)
  })
  names(preds) <- models
  d[, pred_cv := preds[["full"]]]
  cv <- data.table::rbindlist(lapply(models, function(m) {
    dd <- if (m == "full_vwc") d[is.finite(vwc)] else d
    cbind(data.table::data.table(sensor = sensor_name, stat = stat, model = m),
          cv_metrics(dd$delta, preds[[m]], dd$logger_id))
  }))
  fit0 <- fit_model(d, model_terms("full", cov))
  rho <- lag1_rho(stats::residuals(fit0), d$logger_id)
  fit <- fit_model(d, model_terms("full", cov), rho = rho)
  attr(fit, "voxsol") <- list(sensor = sensor_name, stat = stat, k = best$k, tau = best$tau, rho = rho,
                              us_col = us_col, us_scaling = scalings[[us_col]], covariates = cov,
                              terms = model_terms("full", cov), logger_levels = levels(d$logger_id))
  explanatory <- if ("full_vwc" %in% models) fit_model(d[is.finite(vwc)], model_terms("full_vwc", cov)) else NULL
  list(fit = fit, explanatory = explanatory, profile = cbind(sensor = sensor_name, stat = stat, grid),
       cv = cv, data = d, best = best, rho = rho)
}

approximation_check <- function(irr, static, site_daily, us_col, k, scaling) {
  # daily maps use one effective cos(z) per day for the understory beam term; compare at loggers
  us <- stats::setNames(us_normalized(static[[us_col]], scaling), static$logger_id)
  exact <- daily_sw_ground(irr, us, k)
  b <- daily_base(irr)
  b <- merge(b, site_daily[, .(date, cosz_eff)], by = "date")
  u <- us[b$logger_id]
  b[, approx := sw_dir * exp(-k * u / pmax(cosz_eff, 0.05)) + sw_dif * exp(-k * u * DIFFUSE_FACTOR)]
  m <- merge(exact, b[, .(logger_id, date, approx)], by = c("logger_id", "date"))
  m <- m[sw_ground > 0.5]
  data.table::data.table(us_col = us_col, k = k, median_rel_err = stats::median(abs(m$approx / m$sw_ground - 1)),
                         p95_rel_err = stats::quantile(abs(m$approx / m$sw_ground - 1), 0.95, names = FALSE),
                         bias_rel = sum(m$approx) / sum(m$sw_ground) - 1)
}

plot_calibration <- function(res, path) {
  i <- attr(res$fit, "voxsol")
  ok <- tryCatch({
    grDevices::png(path, width = 1400, height = 600, res = 120)
    on.exit(grDevices::dev.off())
    graphics::par(mfrow = c(1, 2))
    p <- res$profile[res$profile$tau == i$tau, ]
    k0 <- p$cv_rmse[p$k == 0][1]
    cands <- unique(p$us_col)
    cols <- grDevices::hcl.colors(max(length(cands), 2), "Dark 3")
    graphics::plot(range(p$k), range(p$cv_rmse), type = "n", xlab = "understory k (scaled descriptor)",
                   ylab = "grouped CV RMSE [K]",
                   main = sprintf("%s daily %s: %s, k = %.2f, tau = %g d", i$sensor, i$stat, i$us_col, i$k, i$tau))
    for (j in seq_along(cands)) {
      q <- p[p$us_col == cands[j] & p$k > 0, ]
      graphics::lines(c(0, q$k), c(k0, q$cv_rmse), type = "b", col = cols[j], pch = 16, cex = 0.6)
    }
    graphics::legend("topright", legend = cands, col = cols[seq_along(cands)], lwd = 1, cex = 0.7, bty = "n")
    d <- res$data
    graphics::plot(d$pred_cv, d$delta, pch = 16, cex = 0.4, col = grDevices::adjustcolor("grey20", 0.4),
                   xlab = "CV prediction (held-out logger) [K]", ylab = "observed offset [K]",
                   main = sprintf("leave-logger-out CV, full model (rho = %.2f)", res$rho))
    graphics::abline(0, 1, col = "firebrick")
    TRUE
  }, error = function(e) {
    message("plot failed: ", conditionMessage(e))
    FALSE
  })
  invisible(ok)
}
