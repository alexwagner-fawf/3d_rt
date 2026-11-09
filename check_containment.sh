#!/usr/bin/env bash
# Lists files outside the repo that this project's tooling could have written since the stamp.
# Empty output = contained. Usage: ./check_containment.sh [stamp-file]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stamp="${1:-$root/.cache/containment_stamp}"
candidates=(
  "$HOME/.cache/pip" "$HOME/.cache/numba" "$HOME/.cache/R" "$HOME/.cache/proj" "$HOME/.cache/pytest"
  "$HOME/.local/share/proj" "$HOME/.local/share/R" "$HOME/.local/lib" "$HOME/.local/bin"
  "$HOME/R" "$HOME/.config/R" "$HOME/.local/share/r-miniconda"
  "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc" "$HOME/.cdsapirc" "$HOME/.Renviron" "$HOME/.Rprofile"
  "$HOME/.Rhistory" "$HOME/.python_history" "$HOME/.matplotlib" "$HOME/.config/matplotlib"
  "$HOME/.local/share/QGIS" "$HOME/.config/QGIS"
)
for c in "${candidates[@]}"; do
  [ -e "$c" ] && find "$c" -newer "$stamp" -not -type d 2>/dev/null
done
find "${TMPDIR_SYSTEM:-/tmp}" -maxdepth 1 -newer "$stamp" \( -name 'Rtmp*' -o -name 'pip-*' -o -name 'pytest-of-*' -o -name 'tmp*voxsol*' \) 2>/dev/null
exit 0
