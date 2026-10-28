# TMS-4 data in long format: x, y, timestamp, value, height (15, 2, -8, "-0-10cm").
# (For raw TOMST data_*.csv files, myClim::mc_read_files + mc_prep_clean would be the entry point.)

read_tms_long <- function(cfg) {
  cols <- cfg$inputs$tms_columns
  sensors <- unlist(cfg$calibration$sensors)
  keep <- unlist(cols[c("x", "y", "time", "value", "height")], use.names = FALSE)
  types <- stats::setNames(c("numeric", "numeric", "character", "numeric", "character"), keep)
  dt <- data.table::fread(cfg$inputs$tms, select = types)
  data.table::setnames(dt, keep, c("x", "y", "time", "value", "height"))
  dt[, height := trimws(height)]
  lookup <- stats::setNames(names(sensors), sensors)
  dt[, sensor := unname(lookup[height])]
  unknown <- dt[is.na(sensor), unique(height)]
  if (length(unknown)) warning("TMS heights without sensor mapping (dropped): ", paste(unknown, collapse = ", "))
  dt <- dt[!is.na(sensor)]
  if (!inherits(dt$time, "POSIXct")) {
    dt[, time := as.POSIXct(time, tz = cfg$inputs$tms_timezone, tryFormats = c("%Y-%m-%d %H:%M:%OS", "%Y-%m-%dT%H:%M:%OS",
                                                                              "%d.%m.%Y %H:%M:%OS", "%Y-%m-%d %H:%M"))]
  }
  attr(dt$time, "tzone") <- "UTC"
  dt[, value := as.numeric(value)]
  dt[]
}

attach_logger_ids <- function(tms, loggers, tol = 0.5) {
  xy <- unique(tms[, .(x, y)])
  d2 <- outer(xy$x, loggers$x, "-")^2 + outer(xy$y, loggers$y, "-")^2
  j <- max.col(-d2, ties.method = "first")
  dist <- sqrt(d2[cbind(seq_len(nrow(xy)), j)])
  if (any(dist > tol)) warning(sum(dist > tol), " TMS positions have no logger within ", tol, " m (dropped)")
  xy[, logger_id := ifelse(dist <= tol, loggers$id[j], NA_character_)]
  tms <- xy[tms, on = .(x, y)]
  tms[!is.na(logger_id)]
}

qc_tms <- function(tms, max_jump = 8) {
  # plausible ranges and isolated spikes (jump > max_jump K away from both neighbours)
  data.table::setorder(tms, logger_id, sensor, time)
  tms[, bad := FALSE]
  tms[sensor != "VWC", bad := value < -40 | value > 70]
  tms[sensor == "VWC", bad := value < 0 | value > 1]
  tms[sensor != "VWC", `:=`(prev = data.table::shift(value), nxt = data.table::shift(value, -1)), by = .(logger_id, sensor)]
  tms[sensor != "VWC" & !is.na(prev) & !is.na(nxt),
      bad := bad | (abs(value - prev) > max_jump & abs(value - nxt) > max_jump)]
  n_bad <- tms[, sum(bad)]
  out <- tms[bad == FALSE][, c("bad", "prev", "nxt") := NULL]
  attr(out, "qc_removed") <- n_bad
  out[]
}
