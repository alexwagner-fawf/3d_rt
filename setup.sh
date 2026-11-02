#!/usr/bin/env bash
# One-time setup on a new machine (proxy-aware, idempotent).
#
#   scripts/proxy.sh set http://proxy.example.com:8080 [--ca corp-root-ca.pem]   # behind a corporate proxy
#   ./setup.sh [PYTHON]               # PYTHON: base interpreter 3.11/3.12 (default: python3.12, python3.11, python3)
#   ./setup.sh --standalone-python    # no Python 3.11/3.12 on the system (Ubuntu 20.04 ships 3.8): download a
#                                     # pinned, checksum-verified Python 3.12 build into .python/ (no admin rights)
#   VOXSOL_CRAN=https://packagemanager.posit.co/cran/__linux__/focal/latest ./setup.sh   # precompiled R packages
#
# Writes only inside the repository: .Renviron (machine-specific R paths), .venv/, .python/, r-lib/, .cache/.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

# Python used by --standalone-python: python-build-standalone release, SHA-256 from its SHA256SUMS
PBS_RELEASE=20261003
PBS_FILE="cpython-3.12.15+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_SHA256=f937814031eab4698ca6d07ec606ede1825768f3f3e99af76d9db3900bee03c5

standalone=0
python_arg=""
for arg in "$@"; do
  case "$arg" in
    --standalone-python) standalone=1 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) python_arg="$arg" ;;
  esac
done
python_arg="${python_arg:-${VOXSOL_PYTHON:-}}"

standalone_python() {  # prints the interpreter path; messages go to stderr
  local py="$root/.python/bin/python3.12" tgz="$root/.cache/$PBS_FILE"
  if [ ! -x "$py" ]; then
    [ "$(uname -m)" = x86_64 ] || { echo "--standalone-python: only an x86_64 build is pinned" >&2; exit 1; }
    echo "downloading $PBS_FILE (~65 MB) from github.com/astral-sh/python-build-standalone" >&2
    curl -fL --retry 3 -o "$tgz" \
      "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/${PBS_FILE//+/%2B}" >&2
    echo "$PBS_SHA256  $tgz" | sha256sum -c --quiet - >&2 || { rm -f "$tgz"; echo "checksum mismatch: $tgz" >&2; exit 1; }
    rm -rf "${root:?}/.python" && mkdir -p "$root/.python"
    tar -xzf "$tgz" -C "$root/.python" --strip-components=1  # archive root is python/
    rm -f "$tgz"
  fi
  echo "$py"
}

echo "== R environment (.Renviron)"
cat > .Renviron <<EOF
# Machine-specific R environment, written by setup.sh (not versioned). Replaces ~/.Renviron while R
# runs from this folder; .Rprofile re-reads ~/.Renviron so personal settings are kept.
TMPDIR=$root/.cache/tmp
R_LIBS_USER=$root/r-lib:\${HOME}/R/%p-library/%v
XDG_CACHE_HOME=$root/.cache/xdg
R_USER_CACHE_DIR=$root/.cache/xdg/R
EOF
if [ -n "${VOXSOL_CRAN:-}" ]; then echo "VOXSOL_CRAN=$VOXSOL_CRAN" >> .Renviron; fi  # package repository (.Rprofile)
mkdir -p .cache/tmp r-lib work data/external secrets

echo "== proxy and caches (env.sh)"
# shellcheck disable=SC1091
source env.sh
echo "proxy: ${https_proxy:-none}" | sed -E 's#(://)[^/@]+@#\1***@#'

echo "== Python environment (.venv)"
if [ ! -x .venv/bin/python ]; then
  if [ "$standalone" = 1 ]; then
    python_bin="$(standalone_python)"
  elif [ -n "$python_arg" ]; then
    python_bin="$python_arg"
  else
    python_bin=""
    for cand in python3.12 python3.11 python3; do
      if command -v "$cand" >/dev/null 2>&1 \
         && "$cand" -c 'import sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 12))' 2>/dev/null; then
        python_bin="$cand"
        break
      fi
    done
    if [ -z "$python_bin" ]; then
      echo "no Python 3.11/3.12 found (python3: $(python3 --version 2>&1 || echo missing)). On Ubuntu 20.04 run:" \
           "./setup.sh --standalone-python" >&2
      exit 1
    fi
  fi
  "$python_bin" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
    || { echo "need Python >= 3.11 (got $("$python_bin" --version 2>&1)); try ./setup.sh --standalone-python" >&2; exit 1; }
  "$python_bin" -c 'import sys; sys.exit(sys.version_info >= (3, 13))' \
    || echo "warning: $("$python_bin" --version 2>&1): cloth-simulation-filter has no binary wheel for Python >= 3.13" \
            "and is compiled (needs a C++ compiler). Python 3.11/3.12 avoids this." >&2
  echo "base interpreter: $python_bin ($("$python_bin" --version 2>&1))"
  "$python_bin" -m venv .venv \
    || { echo "venv creation failed: on Ubuntu install python3.X-venv, or use ./setup.sh --standalone-python" >&2; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .

echo "== R packages (r-lib)"
r_ok=0
if ! command -v Rscript >/dev/null 2>&1; then
  echo "Rscript not found: install R >= 4.0 for the calibration step (https://cloud.r-project.org/bin/linux/ubuntu/)" >&2
elif ! Rscript -e 'quit(status = getRversion() < "4.0.0")' >/dev/null 2>&1; then
  echo "R $(Rscript -e 'cat(format(getRversion()))' 2>/dev/null) is too old (need >= 4.0; Ubuntu 20.04 ships 3.6):" \
       "install R from CRAN's Ubuntu repository (focal-cran40), see https://cloud.r-project.org/bin/linux/ubuntu/" >&2
else
  r_ok=1
  Rscript -e 'pkgs <- c("data.table", "mgcv", "terra", "yaml", "nanoparquet")
    missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
    cat("repository:", getOption("repos")[["CRAN"]], "\n")
    if (length(missing)) install.packages(missing, lib = .libPaths()[1], Ncpus = 4) else cat("all present\n")'
fi
if [ "$r_ok" = 1 ] && ! Rscript -e 'quit(status = !requireNamespace("terra", quietly = TRUE))' >/dev/null 2>&1; then
  echo "terra could not be installed. From source it needs the GDAL, GEOS and PROJ development packages" \
       "(sudo apt install libgdal-dev libgeos-dev libproj-dev); precompiled packages (VOXSOL_CRAN, see README)" \
       "need the runtime libraries (Ubuntu 20.04: libgdal26 libgeos-c1v5 libproj15)" >&2
fi

echo "== done. Next: source env.sh && pytest"
