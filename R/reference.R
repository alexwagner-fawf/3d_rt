# Macroclimate reference per sensor: ERA5-Land / own file (from Python) or open-area reference loggers.

reference_long <- function(cfg, tms, loggers) {
  src <- cfg$forcing$reference
  vars <- unlist(cfg$calibration$reference_vars)
  if (src %in% c("era5land", "file")) {
    ref <- read_parquet_dt(file.path(cfg$workdir, "reference.parquet"))
    missing <- setdiff(vars, names(ref))
    if (length(missing)) stop("reference lacks columns: ", paste(missing, collapse = ", "))
    out <- data.table::rbindlist(lapply(names(vars), function(s)
      data.table::data.table(sensor = s, time = ref$time, ref = ref[[vars[[s]]]])))
  } else {
    ids <- loggers[reference == TRUE, id]
    if (!length(ids)) stop("reference = loggers, but no logger is flagged as reference")
    out <- tms[logger_id %in% ids & sensor %in% names(vars), .(ref = mean(value)), by = .(sensor, time)]
  }
  data.table::setattr(out, "source", src)
  out[]
}

add_delta <- function(tms, ref) {
  # linear interpolation of the reference to the logger time stamps (NA outside its range)
  tms[, ref := NA_real_]
  for (s in unique(ref$sensor)) {
    r <- ref[sensor == s][order(time)]
    idx <- tms$sensor == s
    tms[idx, ref := stats::approx(as.numeric(r$time), r$ref, xout = as.numeric(time), rule = 1)$y]
  }
  tms[, delta := value - ref]
  tms[]
}
