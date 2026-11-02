#!/usr/bin/env bash
# PostgreSQL connection for `voxsol db` (TMS-4 data, plot locations, ERA5 reference).
#
#   scripts/db.sh set HOST DBNAME USER [--port 5432] [--sslmode prefer] [--service voxsol]
#                        (re)write secrets/pg_service.conf and ask for the password (secrets/pgpass, mode 600)
#   scripts/db.sh show   print the connection settings (no password)
#   scripts/db.sh check  connection test and database overview (= voxsol db --check)
#
# env.sh and .Rprofile export PGSERVICEFILE/PGPASSFILE, so voxsol, psql and R (RPostgres) all connect
# with service=voxsol. PostgreSQL connections do not go through the HTTP proxy.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_file="$root/secrets/pg_service.conf"
pass_file="$root/secrets/pgpass"
usage() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

cmd_set() {
  [ $# -ge 3 ] || usage 1
  local host="$1" dbname="$2" user="$3" port=5432 sslmode=prefer service=voxsol pw=""
  shift 3
  while [ $# -gt 0 ]; do
    case "$1" in
      --port) port="$2"; shift 2 ;;
      --sslmode) sslmode="$2"; shift 2 ;;
      --service) service="$2"; shift 2 ;;
      *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
  done
  mkdir -p "$root/secrets"
  umask 077
  printf '# written by scripts/db.sh on %s; see config/pg_service.example.conf\n[%s]\nhost=%s\nport=%s\ndbname=%s\nuser=%s\nsslmode=%s\n' \
    "$(date -I)" "$service" "$host" "$port" "$dbname" "$user" "$sslmode" > "$service_file"
  echo "wrote $service_file"
  if [ -t 0 ]; then read -rsp "password for $user@$host/$dbname (empty: none stored): " pw; echo; else IFS= read -r pw || true; fi
  if [ -n "$pw" ]; then
    pw="${pw//\\/\\\\}"; pw="${pw//:/\\:}"
    { grep -v -F "$host:$port:$dbname:$user:" "$pass_file" 2>/dev/null || true
      printf '%s:%s:%s:%s:%s\n' "$host" "$port" "$dbname" "$user" "$pw"; } > "$pass_file.tmp"
    mv "$pass_file.tmp" "$pass_file"
    chmod 600 "$pass_file"
    echo "wrote $pass_file (mode 600)"
  fi
  echo "next: source env.sh && scripts/db.sh check"
}

cmd_show() {
  if [ -f "$service_file" ]; then cat "$service_file"; else echo "no service file ($service_file)"; fi
  if [ -f "$pass_file" ]; then echo "# password file: $pass_file ($(stat -c %a "$pass_file"))"; else echo "# no password file"; fi
}

cmd_check() {
  # shellcheck disable=SC1091
  source "$root/env.sh"
  voxsol db --check --service "${1:-voxsol}"
}

case "${1:-}" in
  set) shift; cmd_set "$@" ;;
  show) cmd_show ;;
  check) shift; cmd_check "$@" ;;
  -h|--help|"") usage 0 ;;
  *) echo "unknown command: $1" >&2; usage 1 ;;
esac
