#!/usr/bin/env bash
# Install wireplay for the current user: an app-menu entry, an icon and a `wireplay` command.
#
#   ./install.sh              install (safe to run again, e.g. after moving the folder)
#   ./install.sh --uninstall  remove exactly the three files this script installs
#
# Nothing outside your home directory is touched and no root access is needed. wireplay itself keeps running from
# this folder (its library database lives here), so do not delete the folder after installing.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}
apps=$data/applications
icons=$data/icons/hicolor/scalable/apps
bindir=${XDG_BIN_HOME:-$HOME/.local/bin}
entry=$apps/wireplay.desktop
icon=$icons/wireplay.svg
cmd=$bindir/wireplay

if [[ ${1:-} == --uninstall ]]; then
    for f in "$entry" "$icon" "$cmd"; do
        if [[ -e $f ]]; then rm -- "$f" && echo "removed $f"; fi
    done
    command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true
    exit 0
fi
if [[ -n ${1:-} ]]; then
    echo "usage: $0 [--uninstall]" >&2
    exit 2
fi

# Which Python: a virtual environment next to gui.py if there is one, else the python3 on PATH (as an absolute path,
# because desktop sessions often have a shorter PATH than your terminal).
if [[ -x $here/.venv/bin/python ]]; then
    py=$here/.venv/bin/python
elif py=$(command -v python3); then
    :
else
    echo "python3 not found. Install Python 3.10 or newer first." >&2
    exit 1
fi
if ! "$py" -c 'import PySide6' 2>/dev/null; then
    echo "PySide6 is missing for $py." >&2
    echo "  pip:    $py -m pip install -r \"$here/requirements.txt\"   (inside a virtual environment)" >&2
    echo "  Arch:   sudo pacman -S pyside6" >&2
    exit 1
fi

# A path goes inside double quotes in an Exec= line; the desktop-entry spec wants \ " ` $ escaped there.
quote() {
    local s=$1
    s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//\`/\\\`}; s=${s//\$/\\\$}
    printf '"%s"' "$s"
}

mkdir -p "$apps" "$icons" "$bindir"

# 1. the command (also works from a terminal: `wireplay`, `wireplay --tab snes`)
printf '#!/usr/bin/env bash\nexec %s %s "$@"\n' "$(printf '%q' "$py")" "$(printf '%q' "$here/gui.py")" > "$cmd"
chmod 755 "$cmd"

# 2. the app-menu entry, made from the template with this folder's paths
exec_line="Exec=$(quote "$py") $(quote "$here/gui.py")"
awk -v exec_line="$exec_line" -v path_line="Path=$here" '
    /^Exec=/ { print exec_line; next }
    /^Path=/ { print path_line; next }
    /^# Replace/ { next }
    { print }' "$here/wireplay.desktop" > "$entry"
chmod 644 "$entry"

# 3. the icon
install -m 644 "$here/wireplay.svg" "$icon"

command -v desktop-file-validate >/dev/null && desktop-file-validate "$entry"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true

echo "installed:"
echo "  $entry"
echo "  $icon"
echo "  $cmd"
case ":$PATH:" in *":$bindir:"*) ;; *) echo "note: $bindir is not on your PATH, so the 'wireplay' command works only by full path." ;; esac
echo "Open your app launcher and type 'Wireplay'."
