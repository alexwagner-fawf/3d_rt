#!/usr/bin/env bash
# Configure the corporate proxy for this repository.
#
#   scripts/proxy.sh set URL [--https URL] [--no-proxy LIST] [--ca FILE]   write secrets/proxy.env (+ git config)
#   scripts/proxy.sh show                                                  print the settings (credentials masked)
#   scripts/proxy.sh check                                                 test PyPI, CRAN, CAMS, CDS, PROJ via curl, Python, R
#   scripts/proxy.sh unset                                                 remove secrets/proxy.env and the git proxy config
#
# After `set`, run `source env.sh` (new shells) or restart R so the settings take effect.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
file="${VOXSOL_PROXY_FILE:-$root/secrets/proxy.env}"
urls=("https://pypi.org/simple/" "https://cloud.r-project.org/" "https://api.soda-solardata.com/"
      "https://cds.climate.copernicus.eu/api" "https://cdn.proj.org/")

mask() { sed -E 's#(://)[^/@]+@#\1***@#g'; }
in_git() { git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; }
usage() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

cmd_set() {
  [ $# -ge 1 ] || usage 1
  local http="$1" https="$1" no_proxy="localhost,127.0.0.1" ca=""
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --https) https="$2"; shift 2 ;;
      --no-proxy) no_proxy="$2"; shift 2 ;;
      --ca) ca="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
      *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
  done
  for u in "$http" "$https"; do
    case "$u" in http://*|https://*) ;; *) echo "proxy URL must start with http:// or https://: $u" >&2; exit 1 ;; esac
  done
  if [ -n "$ca" ] && [ ! -f "$ca" ]; then echo "CA file not found: $ca" >&2; exit 1; fi
  mkdir -p "$(dirname "$file")"
  umask 077
  {
    echo "# written by scripts/proxy.sh on $(date -I); see config/proxy.example.env"
    echo "HTTP_PROXY=$http"
    echo "HTTPS_PROXY=$https"
    echo "NO_PROXY=$no_proxy"
    [ -n "$ca" ] && echo "CA_BUNDLE=$ca"
  } > "$file"
  echo "wrote $file"
  if in_git; then
    git -C "$root" config --local http.proxy "$https"
    if [ -n "$ca" ]; then
      # shellcheck disable=SC1091
      ( source "$root/env.sh" >/dev/null )  # builds .cache/ca-bundle.pem
      git -C "$root" config --local http.sslCAInfo "$root/.cache/ca-bundle.pem"
    else
      git -C "$root" config --local --unset http.sslCAInfo 2>/dev/null || true
    fi
    echo "set git http.proxy for this repository (.git/config)"
  fi
  echo "next: source env.sh  (and restart R sessions)"
}

cmd_show() {
  if [ -f "$file" ]; then echo "# $file"; grep -v '^[[:space:]]*#' "$file" | grep . | mask; else echo "no proxy file ($file)"; fi
  if in_git; then
    echo "# git (.git/config)"
    git -C "$root" config --local --get http.proxy | mask || echo "http.proxy not set"
    git -C "$root" config --local --get http.sslCAInfo || true
  fi
}

cmd_unset() {
  rm -f "$file" "$root/.cache/ca-bundle.pem"
  if in_git; then
    git -C "$root" config --local --unset http.proxy 2>/dev/null || true
    git -C "$root" config --local --unset http.sslCAInfo 2>/dev/null || true
  fi
  echo "removed $file and the repository git proxy settings; open a new shell (exported variables stay in this one)"
}

cmd_check() {
  # shellcheck disable=SC1091
  source "$root/env.sh"
  echo "proxy: ${https_proxy:-none}" | mask
  echo "CA bundle: ${CURL_CA_BUNDLE:-system default}"
  echo "== curl (pip/git/PROJ path)"
  local err="$TMPDIR/proxy_check_curl.err"
  for u in "${urls[@]}"; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 ${CURL_CA_BUNDLE:+--cacert "$CURL_CA_BUNDLE"} -I "$u" 2>"$err" || true)"
    case "$code" in [1-5][0-9][0-9]) echo "  ok   ($code) $u" ;; *) echo "  FAIL $u: $(sed 's/^curl: //' "$err" | head -1)" ;; esac
  done
  rm -f "$err"
  if command -v voxsol >/dev/null 2>&1; then
    echo "== Python requests (CAMS/CDS downloads)"
    voxsol proxy --check | sed -n '/^$/,$p' | sed '/^$/d; s/^/  /'
  fi
  if command -v Rscript >/dev/null 2>&1; then
    echo "== R libcurl (install.packages)"
    (cd "$root" && Rscript -e 'for (u in c("https://cloud.r-project.org/")) { r <- tryCatch(attr(curlGetHeaders(u), "status"), error = function(e) gsub("\\s+", " ", conditionMessage(e))); cat("  ", if (is.numeric(r)) sprintf("ok   (%d)", r) else paste("FAIL", r), u, "\n") }')
  fi
}

case "${1:-}" in
  set) shift; cmd_set "$@" ;;
  show) cmd_show ;;
  check) cmd_check ;;
  unset) cmd_unset ;;
  -h|--help|"") usage 0 ;;
  *) echo "unknown command: $1" >&2; usage 1 ;;
esac
