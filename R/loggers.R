# TMS-4 data in long format: x, y, timestamp, value, height [, logger id], as CSV or Parquet (the
# database snapshot written by `voxsol db`). Height labels such as 15, 2, -8, "-0-10cm" (files) or
# "air 15 cm", "soil 8 cm", "soil 0-15 cm" (database) are mapped to sensors by calibration.sensors.
# (For raw TOMST data_*.csv files, myClim::mc_read_files + mc_prep_clean would be the entry point;
# myClim is not installed by setup.sh.)

# Canonical height label: signed cm, above ground positive, soil negative ("air 15 cm" -> "15",
# "soil 8 cm" -> "-8", "-0-10cm" -> "-0-10"); unparseable labels are compared in lower case without
# spaces. Same rules as voxsol.io.loggers.height_key (Python).
height_key <- function(x) {
  base <- gsub("[[:space:]]+", "", tolower(trimws(as.character(x))))
  s <- sub("cm$", "", base)
  negative <- grepl("^(soil|boden)", s)
  s <- sub("^(air|luft|soil|boden)", "", s)
  m <- regmatches(s, regexec("^([+-]?)([0-9]+(\\.[0-9]+)?)(-([0-9]+(\\.[0-9]+)?))?$", s))
  vapply(seq_along(s), function(i) {
    g <- m[[i]]
    if (!length(g)) return(base[i])
    num <- as.character(as.numeric(g[3]))
    if (nzchar(g[6])) num <- paste0(num, "-", as.numeric(g[6]))
    paste0(if (negative[i] || g[2] == "-") "-" else "", num)
  }, character(1))
}

# calibration.sensors (sensor -> label or list of labels) as a named vector height key -> sensor
sensor_lookup <- function(sensors) {
  labels <- lapply(sensors, function(v) as.character(unlist(v)))
  tab <- unique(data.frame(key = height_key(unlist(labels, use.names = FALSE)),
                           sensor = rep(names(labels), lengths(labels)), stringsAsFactors = FALSE))
  dup <- tab$key[duplicated(tab$key)]
  if (length(dup)) stop("calibration.sensors: height ", dup[1], " is given for more than one sensor")
  stats::setNames(tab$sensor, tab$key)
}

format_sensors <- function(sensors) {
  paste(vapply(names(sensors), function(s) sprintf("%s = %s", s, paste(dQuote(unlist(sensors[[s]]), FALSE), collapse = " | ")),
               character(1)), collapse = ", ")
}

read_tms_long <- function(cfg) {
  cols <- cfg$inputs$tms_columns
  lookup <- sensor_lookup(cfg$calibration$sensors)
  keep <- unlist(cols[c("x", "y", "time", "value", "height", "id")], use.names = FALSE)
  std <- c("x", "y", "time", "value", "height", if (!is.null(cols$id)) "logger_id")
  if (grepl("\\.(parquet|pq)$", cfg$inputs$tms, ignore.case = TRUE)) {
    dt <- read_parquet_dt(cfg$inputs$tms)
    has_name <- "sensor_name" %in% names(dt) && !"sensor_name" %in% keep
    dt <- dt[, c(keep, if (has_name) "sensor_name"), with = FALSE]
  } else {
    types <- stats::setNames(c("numeric", "numeric", "character", "numeric", "character", "character")[seq_along(keep)], keep)
    dt <- data.table::fread(cfg$inputs$tms, select = types)
  }
  data.table::setnames(dt, keep, std)
  dt[, height := trimws(as.character(height))]
  dt[, value := as.numeric(value)]
  labels <- dt[, .(n = .N, median = stats::median(value, na.rm = TRUE)), by = c("height", intersect("sensor_name", names(dt)))]
  labels[, sensor := unname(lookup[height_key(height)])]
  dt <- unique(labels[, .(height, sensor)])[dt, on = "height"]
  unknown <- labels[is.na(sensor)]
  if (nrow(unknown)) {
    desc <- sprintf("\"%s\"%s (%s values, median %.4g)", unknown$height,
                    if ("sensor_name" %in% names(unknown)) sprintf(" [sensor_name %s]", unknown$sensor_name) else "",
                    format(unknown$n, big.mark = ","), unknown$median)
    note <- paste0("TMS heights that match no sensor in calibration.sensors (dropped): ", paste(desc, collapse = "; "),
                   ".\n  Current mapping: ", format_sensors(cfg$calibration$sensors),
                   ".\n  Labels are compared without spaces/'cm', 'air' = above ground, 'soil' = below ground, ",
                   "e.g. calibration.sensors: {T3: \"air 15 cm\", T2: \"air 2 cm\", T1: \"soil 8 cm\", VWC: \"soil 0-15 cm\"}")
    if (nrow(unknown) == nrow(labels)) stop("no TMS values left. ", note, call. = FALSE)
    warning(note, call. = FALSE)
  }
  dt <- dt[!is.na(sensor)]
  if ("sensor_name" %in% names(dt)) dt[, sensor_name := NULL]
  dt <- vwc_as_fraction(dt)
  if (!inherits(dt$time, "POSIXct")) {
    dt[, time := as.POSIXct(time, tz = cfg$inputs$tms_timezone, tryFormats = c("%Y-%m-%d %H:%M:%OS", "%Y-%m-%dT%H:%M:%OS",
                                                                              "%d.%m.%Y %H:%M:%OS", "%Y-%m-%d %H:%M"))]
  }
  data.table::setattr(dt$time, "tzone", "UTC")
  dt[]
}

# TMS-4 soil moisture can be stored as volumetric fraction, percent or raw TOMST counts. The
# calibration uses m3/m3; raw counts need a soil-specific conversion (e.g. myClim::mc_calc_vwc).
vwc_as_fraction <- function(dt) {
  if (!"VWC" %in% dt$sensor) return(dt)
  med <- dt[sensor == "VWC", stats::median(value, na.rm = TRUE)]
  if (is.finite(med) && med > 1 && med <= 100) {
    message("VWC values look like percent (median ", signif(med, 3), "): divided by 100")
    dt[sensor == "VWC", value := value / 100]
  } else if (is.finite(med) && med > 100) {
    warning("VWC values look like raw TMS moisture counts (median ", signif(med, 4), "), not m3/m3; ",
            "soil moisture is not used (convert the counts to volumetric water content first, e.g. myClim::mc_calc_vwc)",
            call. = FALSE)
    dt <- dt[sensor != "VWC"]
  }
  dt
}

attach_logger_ids <- function(tms, loggers, tol = 0.5) {
  if ("logger_id" %in% names(tms)) {
    tms[, logger_id := as.character(logger_id)]
    unknown <- setdiff(unique(tms$logger_id), loggers$id)
    if (length(unknown)) warning(length(unknown), " TMS logger ids not in loggers.csv (dropped): ",
                                 paste(utils::head(unknown, 10), collapse = ", "))
    return(tms[logger_id %in% loggers$id])
  }
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
  data.table::setattr(out, "qc_removed", n_bad)
  out[]
}
