#!/usr/bin/env bash
set -euo pipefail

VERSION="1.0.0"
APP_NAME="adorn"
APP_ID="com.grinch.gnome.adorn"
DIST_DIR="$(pwd)/dist"
TMP_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "==> Building Flatpak package for ${APP_NAME} v${VERSION}..."
mkdir -p "${DIST_DIR}"

if command -v flatpak-builder >/dev/null 2>&1; then
    BUILD_DIR="${TMP_DIR}/build"
    REPO_DIR="${TMP_DIR}/repo"
    flatpak-builder --force-clean --repo="${REPO_DIR}" "${BUILD_DIR}" "packaging/flatpak/${APP_ID}.yml"
    flatpak build-bundle "${REPO_DIR}" "${DIST_DIR}/${APP_NAME}-${VERSION}.flatpak" "${APP_ID}"
    echo "✓ Flatpak bundle built in ${DIST_DIR}/${APP_NAME}-${VERSION}.flatpak"
else
    echo "  (flatpak-builder command not found; see packaging/flatpak/${APP_ID}.yml or run in CI / container)"
fi
