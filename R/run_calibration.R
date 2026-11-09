#!/usr/bin/env Rscript
# Calibrate modeled insolation against TMS-4 temperature offsets and predict monthly maps.
# Usage (from the repo root, after `voxsol run config/<site>.yaml`):
#   Rscript R/run_calibration.R config/<site>.yaml [--no-maps]
# Outputs: work/<site>/calibration/ (cv_summary.csv, k_profile.csv, models, plots, maps/).

# OpenBLAS reads its thread count when the library loads (before .Renviron), and many small mgcv
# fits in forked CV workers then busy-wait in BLAS threads. Re-launch once with 1 BLAS thread.
if (Sys.getenv("VOXSOL_BLAS_SINGLE") != "1") {
  file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE))
  status <- system2(file.path(R.home("bin"), "Rscript"), c(shQuote(file_arg), shQuote(commandArgs(trailingOnly = TRUE))),
                    env = c("VOXSOL_BLAS_SINGLE=1", "OPENBLAS_NUM_THREADS=1", "OMP_NUM_THREADS=1"))
  quit(save = "no", status = status)
}

suppressPackageStartupMessages(library(data.table))

script_dir <- local({
  f <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
  if (length(f)) dirname(normalizePath(sub("^--file=", "", f))) else "R"
})
for (f in c("io_voxsol.R", "loggers.R", "reference.R", "calibrate.R", "predict_maps.R")) source(file.path(script_dir, f))

args <- commandArgs(trailingOnly = TRUE)
cfg <- read_site_config(if (length(args) && !startsWith(args[1], "--")) args[1] else "config/synthetic.yaml")
do_maps <- !("--no-maps" %in% args)
msg <- function(...) message(format(Sys.time(), "[%H:%M:%S] "), ...)

loggers <- read_loggers_table(cfg)
tms <- read_tms_long(cfg)
tms <- attach_logger_ids(tms, loggers)
tms <- qc_tms(tms)
msg("TMS: ", uniqueN(tms$logger_id), " loggers, ", nrow(tms), " values (QC removed ", attr(tms, "qc_removed"), ")")
if (!nrow(tms)) stop("no TMS values left after matching logger ids and QC", call. = FALSE)
print(tms[, .(loggers = uniqueN(logger_id), values = .N, first = min(time), last = max(time)), keyby = sensor])
ref <- reference_long(cfg, tms, loggers)
tms <- add_delta(tms, ref)
if (cfg$forcing$reference == "loggers") tms <- tms[!logger_id %in% loggers[reference == TRUE, id]]

irr <- read_logger_irradiance(cfg)
static <- read_logger_static(cfg)
static[, logger_id := as.character(logger_id)]
static <- static[reference == FALSE | cfg$forcing$reference != "loggers"]
base <- daily_base(irr)
vwc <- daily_vwc(tms)
site_daily <- read_site_daily(cfg)

# daily responses first, then release the large 15-min table (keeps forked CV workers small)
responses <- list()
for (s in cfg$calibration$responses) {
  if (!s %in% tms$sensor) {
    warning("no ", s, " data (check calibration.sensors and calibration.reference_vars): ", s, " skipped", call. = FALSE)
    next
  }
  for (st in cfg$calibration$stats)
    responses[[paste(s, st, sep = "_")]] <- daily_response(tms, s, st, cfg$calibration$min_day_coverage)
}
rm(tms); invisible(gc())

results <- list()
for (s in cfg$calibration$responses) {
  for (st in cfg$calibration$stats) {
    resp <- responses[[paste(s, st, sep = "_")]]
    if (is.null(resp)) next
    n_log <- uniqueN(resp$logger_id)
    if (n_log < 3) {
      warning(s, " daily ", st, ": ", n_log, " loggers with complete days and a reference value (need >= 3): skipped",
              call. = FALSE)
      next
    }
    msg("calibrating ", s, " daily ", st, " (", n_log, " loggers, ", nrow(resp), " logger-days)")
    res <- calibrate_one(cfg, resp, irr, base, static, vwc, s, st)
    res$best$sensor <- s
    tag <- paste(s, st, sep = "_")
    saveRDS(res$fit, file.path(cfg$outdir, sprintf("model_%s.rds", tag)))
    if (!is.null(res$explanatory)) {
      saveRDS(res$explanatory, file.path(cfg$outdir, sprintf("model_%s_explanatory.rds", tag)))
      capture.output(summary(res$explanatory), file = file.path(cfg$outdir, sprintf("summary_%s_explanatory.txt", tag)))
    }
    capture.output(summary(res$fit), file = file.path(cfg$outdir, sprintf("summary_%s.txt", tag)))
    fwrite(res$data, file.path(cfg$outdir, sprintf("model_data_%s.csv", tag)))
    plot_calibration(res, file.path(cfg$outdir, sprintf("calibration_%s.png", tag)))
    results[[tag]] <- res
  }
}

if (!length(results)) stop("nothing calibrated (see the warnings above)", call. = FALSE)
cv <- rbindlist(lapply(results, `[[`, "cv"))
prof <- rbindlist(lapply(results, `[[`, "profile"))
chosen <- rbindlist(lapply(results, function(r) {
  i <- attr(r$fit, "voxsol")
  data.table(sensor = i$sensor, stat = i$stat, k = i$k, tau_days = i$tau, rho = r$rho,
             us_index = i$us_col, covariates = paste(i$covariates, collapse = "+"))
}))
approx <- unique(rbindlist(lapply(results, function(r) {
  i <- attr(r$fit, "voxsol")
  cbind(sensor = i$sensor, stat = i$stat, approximation_check(irr, static, site_daily, i$us_col, i$k, i$us_scaling))
})))
fwrite(cv, file.path(cfg$outdir, "cv_summary.csv"))
fwrite(prof, file.path(cfg$outdir, "k_profile.csv"))
fwrite(chosen, file.path(cfg$outdir, "chosen_parameters.csv"))
fwrite(approx, file.path(cfg$outdir, "understory_daily_approximation.csv"))

cat("\nChosen parameters:\n"); print(chosen)
cat("\nCross-validated skill (held-out loggers, population-level predictions):\n")
print(cv[, .(sensor, stat, model, rmse = round(rmse, 3), r2 = round(r2, 3), spatial_r2 = round(spatial_r2, 3))])
cat("\nDaily effective cos(z) approximation used by the maps (vs exact 15-min integration):\n"); print(approx)

if (do_maps) {
  for (tag in names(results)) {
    msg("maps ", tag)
    files <- predict_maps(cfg, results[[tag]]$fit)
  }
  msg("maps written to ", file.path(cfg$outdir, "maps"))
}
