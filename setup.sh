#!/usr/bin/env bash
# One-time setup on a new machine (proxy-aware, idempotent).
#
#   scripts/proxy.sh set http://proxy.example.com:8080 [--ca corp-root-ca.pem]   # behind a corporate proxy
#   ./setup.sh [PYTHON]        # PYTHON: base interpreter 3.11 or 3.12 for .venv (default: python3)
#
# Writes only inside the repository: .Renviron (machine-specific R paths), .venv/, r-lib/, .cache/.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
python_bin="${1:-${VOXSOL_PYTHON:-python3}}"

echo "== R environment (.Renviron)"
cat > .Renviron <<EOF
# Machine-specific R environment, written by setup.sh (not versioned). Replaces ~/.Renviron while R
# runs from this folder; .Rprofile re-reads ~/.Renviron so personal settings are kept.
TMPDIR=$root/.cache/tmp
R_LIBS_USER=$root/r-lib:\${HOME}/R/%p-library/%v
XDG_CACHE_HOME=$root/.cache/xdg
R_USER_CACHE_DIR=$root/.cache/xdg/R
EOF
mkdir -p .cache/tmp r-lib work data/external secrets

echo "== proxy and caches (env.sh)"
# shellcheck disable=SC1091
source env.sh
echo "proxy: ${https_proxy:-none}" | sed -E 's#(://)[^/@]+@#\1***@#'

echo "== Python environment (.venv)"
if [ ! -x .venv/bin/python ]; then
  "$python_bin" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
    || { echo "need Python >= 3.11 (got $("$python_bin" --version 2>&1))" >&2; exit 1; }
  "$python_bin" -c 'import sys; sys.exit(sys.version_info >= (3, 13))' \
    || echo "warning: $("$python_bin" --version 2>&1): cloth-simulation-filter has no binary wheel for Python >= 3.13" \
            "and is compiled (needs a C++ compiler). Python 3.11/3.12 avoids this." >&2
  "$python_bin" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .

echo "== R packages (r-lib)"
if command -v Rscript >/dev/null 2>&1; then
  Rscript -e 'pkgs <- c("data.table", "mgcv", "terra", "yaml", "nanoparquet")
    missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
    if (length(missing)) install.packages(missing, lib = .libPaths()[1], Ncpus = 4) else cat("all present\n")'
else
  echo "Rscript not found: install R >= 4.3 for the calibration step" >&2
fi
if command -v Rscript >/dev/null 2>&1 && ! Rscript -e 'quit(status = !requireNamespace("terra", quietly = TRUE))' >/dev/null 2>&1; then
  echo "terra could not be installed: on Linux it is compiled and needs the GDAL, GEOS and PROJ development" \
       "packages (Debian/Ubuntu: sudo apt install libgdal-dev libgeos-dev libproj-dev)" >&2
fi

echo "== done. Next: source env.sh && pytest"
