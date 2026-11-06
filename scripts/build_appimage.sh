#!/usr/bin/env bash
set -euo pipefail

VERSION="1.0.0"
APP_NAME="adorn"
DIST_DIR="$(pwd)/dist"
TMP_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "==> Building AppImage for ${APP_NAME} v${VERSION}..."
mkdir -p "${DIST_DIR}"

APPDIR="${TMP_DIR}/Adorn.AppDir"
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib/python3/site-packages"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/metainfo"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

# Copy application files
cp -r adorn "${APPDIR}/usr/lib/python3/site-packages/"
mkdir -p "${APPDIR}/usr/share"
cp -r adorn "${APPDIR}/usr/share/adorn"
cp run.py "${APPDIR}/usr/bin/${APP_NAME}"
chmod +x "${APPDIR}/usr/bin/${APP_NAME}"

# Copy metadata & desktop files
cp data/com.grinch.gnome.adorn.desktop "${APPDIR}/usr/share/applications/"
cp data/com.grinch.gnome.adorn.desktop "${APPDIR}/"
cp data/com.grinch.gnome.adorn.metainfo.xml "${APPDIR}/usr/share/metainfo/"
cp data/icons/com.grinch.gnome.adorn.svg "${APPDIR}/usr/share/icons/hicolor/scalable/apps/"
cp data/icons/com.grinch.gnome.adorn.svg "${APPDIR}/com.grinch.gnome.adorn.svg"
cp data/icons/com.grinch.gnome.adorn.svg "${APPDIR}/.DirIcon"

# Copy AppRun
cp packaging/appimage/AppRun "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"

OUT_APPIMAGE="${DIST_DIR}/Adorn-${VERSION}-x86_64.AppImage"

if command -v appimagetool >/dev/null 2>&1; then
    export APPIMAGE_EXTRACT_AND_RUN=1
    ARCH=x86_64 appimagetool "${APPDIR}" "${OUT_APPIMAGE}"
    echo "✓ AppImage successfully created: ${OUT_APPIMAGE}"
else
    # Pack AppDir as portable directory archive
    echo "  (appimagetool not found on host; packing AppDir archive)"
    tar -czf "${DIST_DIR}/Adorn-${VERSION}-x86_64.AppDir.tar.gz" -C "${TMP_DIR}" "Adorn.AppDir"
    echo "✓ Portable AppDir bundle created: ${DIST_DIR}/Adorn-${VERSION}-x86_64.AppDir.tar.gz"
    echo "  (Run with appimagetool in CI / container to generate binary .AppImage)"
fi
