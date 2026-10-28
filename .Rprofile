# Project-local R setup (active when R starts in the repository root):
# - new packages go to r-lib/ (first library path), caches stay in .cache/ (see CLAUDE.md)
# - corporate proxy / CA from secrets/proxy.env (see config/proxy.example.env) for install.packages etc.
# Machine-specific paths (TMPDIR, R_LIBS_USER) are in .Renviron, written by setup.sh.
local({
  root <- normalizePath(".")
  user_lib <- file.path(root, "r-lib")
  if (file.exists("~/.Renviron")) readRenviron("~/.Renviron")
  Sys.setenv(XDG_CACHE_HOME = file.path(root, ".cache", "xdg"),
             R_USER_CACHE_DIR = file.path(root, ".cache", "xdg", "R"))
  dir.create(user_lib, showWarnings = FALSE)
  .libPaths(unique(c(user_lib, .libPaths())))
  options(repos = c(CRAN = "https://cloud.r-project.org"))

  proxy_file <- Sys.getenv("VOXSOL_PROXY_FILE", file.path(root, "secrets", "proxy.env"))
  if (file.exists(proxy_file)) {
    lines <- trimws(readLines(proxy_file, warn = FALSE))
    lines <- lines[nzchar(lines) & !startsWith(lines, "#") & grepl("=", lines, fixed = TRUE)]
    keys <- toupper(trimws(sub("^export\\s+", "", sub("=.*$", "", lines))))
    vals <- gsub("^[\"']|[\"']$", "", trimws(sub("^[^=]*=", "", lines)))
    cfg <- stats::setNames(vals, keys)
    for (k in intersect(c("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"), names(cfg))) {
      do.call(Sys.setenv, stats::setNames(list(cfg[[k]], cfg[[k]]), c(k, tolower(k))))
    }
    if (!is.na(cfg["CA_BUNDLE"])) {
      ca <- cfg[["CA_BUNDLE"]]
      out <- file.path(root, ".cache", "ca-bundle.pem")
      base <- Filter(file.exists, c("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                                    "/etc/ssl/cert.pem"))
      if (!file.exists(ca)) {
        warning("CA_BUNDLE not found: ", ca)
      } else {
        if (!file.exists(out) || file.mtime(ca) > file.mtime(out)) {
          dir.create(dirname(out), showWarnings = FALSE, recursive = TRUE)
          writeLines(c(if (length(base)) readLines(base[1], warn = FALSE), "", readLines(ca, warn = FALSE)), out)
        }
        Sys.setenv(CURL_CA_BUNDLE = out, SSL_CERT_FILE = out, REQUESTS_CA_BUNDLE = out)
      }
    }
  }
})
