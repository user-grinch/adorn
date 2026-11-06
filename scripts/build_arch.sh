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

echo "==> Building Arch Linux package for ${APP_NAME} v${VERSION}..."
mkdir -p "${DIST_DIR}"

# Build tarball
./scripts/build_release_tarball.sh > /dev/null

cp "${DIST_DIR}/${APP_NAME}-${VERSION}.tar.gz" "${TMP_DIR}/"
cp packaging/arch/PKGBUILD "${TMP_DIR}/"

if command -v makepkg >/dev/null 2>&1; then
    cd "${TMP_DIR}"
    # Generate checksum
    updpkgsums 2>/dev/null || sed -i "s|sha256sums=.*|sha256sums=('SKIP')|" PKGBUILD
    makepkg -f --nodeps
    find . -type f -name "*.pkg.tar.*" -exec cp {} "${DIST_DIR}/" \;
    echo "✓ Arch Linux package built in ${DIST_DIR}"
else
    echo "  (makepkg not found; see packaging/arch/PKGBUILD)"
fi
