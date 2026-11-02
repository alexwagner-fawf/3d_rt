# Source before every run:  source env.sh
# Keeps all caches, temp files and installs inside this repository (see CLAUDE.md) and applies the
# corporate proxy settings from secrets/proxy.env (see config/proxy.example.env).
VOXSOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VOXSOL_ROOT
export TMPDIR="$VOXSOL_ROOT/.cache/tmp"
export PIP_CACHE_DIR="$VOXSOL_ROOT/.cache/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export NUMBA_CACHE_DIR="$VOXSOL_ROOT/.cache/numba"
export XDG_CACHE_HOME="$VOXSOL_ROOT/.cache/xdg"
export MPLCONFIGDIR="$VOXSOL_ROOT/.cache/xdg/matplotlib"
export PROJ_USER_WRITABLE_DIRECTORY="$VOXSOL_ROOT/.cache/proj"
export PROJ_NETWORK=OFF
export PYTHONNOUSERSITE=1
export R_LIBS_USER="$VOXSOL_ROOT/r-lib:$HOME/R/%p-library/%v"
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR" "$NUMBA_CACHE_DIR" "$XDG_CACHE_HOME" "$PROJ_USER_WRITABLE_DIRECTORY"

# --- corporate proxy -------------------------------------------------------------------------
# The file is parsed (not executed); only the keys below are taken. Lower-case variants are needed
# by curl/libcurl (R, PROJ, git), upper-case by Python requests and pip.
VOXSOL_PROXY_FILE="${VOXSOL_PROXY_FILE:-$VOXSOL_ROOT/secrets/proxy.env}"
if [ -f "$VOXSOL_PROXY_FILE" ]; then
  while IFS= read -r _voxsol_line || [ -n "$_voxsol_line" ]; do
    _voxsol_line="${_voxsol_line#"${_voxsol_line%%[![:space:]]*}"}"
    case "$_voxsol_line" in ''|'#'*) continue ;; esac
    _voxsol_key="$(printf '%s' "${_voxsol_line%%=*}" | sed 's/^export[[:space:]]*//; s/[[:space:]]*$//' | tr '[:lower:]' '[:upper:]')"
    _voxsol_val="${_voxsol_line#*=}"
    _voxsol_val="$(printf '%s' "$_voxsol_val" | sed "s/^[[:space:]]*//; s/[[:space:]]*\$//; s/^[\"']//; s/[\"']\$//")"
    [ -z "$_voxsol_val" ] && continue
    case "$_voxsol_key" in
      HTTP_PROXY)  export HTTP_PROXY="$_voxsol_val" http_proxy="$_voxsol_val" ;;
      HTTPS_PROXY) export HTTPS_PROXY="$_voxsol_val" https_proxy="$_voxsol_val" ;;
      NO_PROXY)    export NO_PROXY="$_voxsol_val" no_proxy="$_voxsol_val" ;;
      CA_BUNDLE)   _voxsol_ca="$_voxsol_val" ;;
    esac
  done < "$VOXSOL_PROXY_FILE"
  if [ -n "${_voxsol_ca:-}" ]; then
    if [ -f "$_voxsol_ca" ]; then
      _voxsol_out="$VOXSOL_ROOT/.cache/ca-bundle.pem"
      _voxsol_base=""
      for _voxsol_f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
        if [ -f "$_voxsol_f" ]; then _voxsol_base="$_voxsol_f"; break; fi
      done
      if [ ! -f "$_voxsol_out" ] || [ "$_voxsol_ca" -nt "$_voxsol_out" ] || { [ -n "$_voxsol_base" ] && [ "$_voxsol_base" -nt "$_voxsol_out" ]; }; then
        { [ -n "$_voxsol_base" ] && cat "$_voxsol_base"; echo; cat "$_voxsol_ca"; } > "$_voxsol_out"
      fi
      export REQUESTS_CA_BUNDLE="$_voxsol_out" SSL_CERT_FILE="$_voxsol_out" CURL_CA_BUNDLE="$_voxsol_out" \
             PIP_CERT="$_voxsol_out" GIT_SSL_CAINFO="$_voxsol_out"
    else
      echo "env.sh: CA_BUNDLE not found: $_voxsol_ca" >&2
    fi
  fi
  unset _voxsol_line _voxsol_key _voxsol_val _voxsol_ca _voxsol_out _voxsol_base _voxsol_f
fi

# --- PostgreSQL (voxsol db, psql, RPostgres): connection service and password file from secrets/ ---
# See config/pg_service.example.conf and scripts/db.sh. PostgreSQL does not use the HTTP proxy.
[ -f "$VOXSOL_ROOT/secrets/pg_service.conf" ] && export PGSERVICEFILE="$VOXSOL_ROOT/secrets/pg_service.conf"
[ -f "$VOXSOL_ROOT/secrets/pgpass" ] && export PGPASSFILE="$VOXSOL_ROOT/secrets/pgpass"

if [ -f "$VOXSOL_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VOXSOL_ROOT/.venv/bin/activate"
fi
