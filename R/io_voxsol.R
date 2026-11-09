# Reading the site config and the Python (voxsol) outputs from work/<site>/.

`%||%` <- function(a, b) if (is.null(a)) b else a

repo_root <- function() Sys.getenv("VOXSOL_ROOT", unset = getwd())

calibration_defaults <- list(
  sensors = list(T3 = "15", T2 = "2", T1 = "-8", VWC = c("-0-10cm", "-0-15cm")),  # see height_key()
  reference_vars = list(T3 = "t2m", T2 = "t2m", T1 = "stl1"),
  us_candidates = c("us_index_15cm", "us_index_2cm", "ground_lift", "ground_roughness", "rgb_exg"),
  responses = c("T3", "T2", "T1"),
  stats = c("max", "mean"),
  k_grid = seq(0, 8, by = 0.5),
  tau_days_grid = c(0, 1, 2, 4),
  static_covariates = c("rgb_green_cover"),
  cv_folds = 0,
  min_day_coverage = 0.9
)

read_site_config <- function(path) {
  cfg <- yaml::read_yaml(path)
  cfg$calibration <- utils::modifyList(calibration_defaults, cfg$calibration %||% list())
  cfg$forcing <- utils::modifyList(list(reference = "era5land", reference_loggers = list()),
                                   cfg$forcing %||% list())
  cfg$inputs$tms_columns <- utils::modifyList(
    list(x = "x", y = "y", time = "timestamp", value = "value", height = "height"),
    cfg$inputs$tms_columns %||% list())
  cfg$inputs$tms_timezone <- cfg$inputs$tms_timezone %||% "UTC"
  cfg$workdir <- file.path(repo_root(), "work", cfg$site)
  if (!is.null(cfg$database) && is.null(cfg$inputs$tms)) {
    # snapshot written by `voxsol db` (same default as the Python config)
    cfg$inputs$tms <- file.path(cfg$workdir, "db", "tms_long.parquet")
    cfg$inputs$tms_columns$id <- cfg$inputs$tms_columns$id %||% "logger_id"
  }
  cfg$outdir <- file.path(cfg$workdir, "calibration")
  dir.create(file.path(cfg$outdir, "maps"), recursive = TRUE, showWarnings = FALSE)
  cfg
}

read_parquet_dt <- function(path) {
  dt <- data.table::as.data.table(nanoparquet::read_parquet(path))
  for (col in names(dt)) {
    if (inherits(dt[[col]], "POSIXct")) data.table::set(dt, j = col, value = as.POSIXct(dt[[col]], tz = "UTC"))
  }
  dt
}

read_logger_irradiance <- function(cfg) {
  irr <- read_parquet_dt(file.path(cfg$workdir, "logger_irradiance.parquet"))
  irr[, date := data.table::as.IDate(time, tz = "UTC")]
  step <- as.numeric(stats::median(diff(sort(unique(irr$time)))), units = "secs")
  data.table::setattr(irr, "step_s", step)
  irr
}

read_logger_static <- function(cfg) {
  data.table::fread(file.path(cfg$workdir, "logger_static.csv"))
}

read_loggers_table <- function(cfg) {
  data.table::fread(file.path(cfg$workdir, "loggers.csv"), colClasses = list(character = "id"))
}

read_site_daily <- function(cfg) {
  sd <- data.table::fread(file.path(cfg$workdir, "site_daily.csv"))
  sd[, date := data.table::as.IDate(substr(as.character(date), 1, 10))]
  sd
}
