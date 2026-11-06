#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"

echo "==> Installing Adorn icon and desktop entry for user..."

mkdir -p "${XDG_DATA_HOME}/icons/hicolor/scalable/apps"
mkdir -p "${XDG_DATA_HOME}/applications"

cp "${REPO_DIR}/data/icons/com.grinch.gnome.adorn.svg" "${XDG_DATA_HOME}/icons/hicolor/scalable/apps/"

if command -v adorn >/dev/null 2>&1; then
    cp "${REPO_DIR}/data/com.grinch.gnome.adorn.desktop" "${XDG_DATA_HOME}/applications/"
else
    sed "s|^Exec=adorn|Exec=${REPO_DIR}/run.py|" "${REPO_DIR}/data/com.grinch.gnome.adorn.desktop" > "${XDG_DATA_HOME}/applications/com.grinch.gnome.adorn.desktop"
fi

update-desktop-database "${XDG_DATA_HOME}/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "${XDG_DATA_HOME}/icons/hicolor" 2>/dev/null || true

echo "✓ Adorn desktop entry and icon installed to ${XDG_DATA_HOME}."
