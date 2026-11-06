#!/usr/bin/env bash
set -euo pipefail

VERSION="1.0.0"
APP_NAME="adorn"
TARBALL_NAME="${APP_NAME}-${VERSION}.tar.gz"
DIST_DIR="$(pwd)/dist"
TMP_DIR="$(mktemp -d)"

echo "==> Building release tarball for ${APP_NAME} v${VERSION}..."

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

# Create distribution folder
mkdir -p "${DIST_DIR}"

# Prepare staging tree with standard root folder
STAGE_DIR="${TMP_DIR}/${APP_NAME}-${VERSION}"
mkdir -p "${STAGE_DIR}"

cp -r adorn "${STAGE_DIR}/"
cp run.py "${STAGE_DIR}/"
cp setup.py "${STAGE_DIR}/"
cp pyproject.toml "${STAGE_DIR}/"
cp LICENSE "${STAGE_DIR}/"
cp README.md "${STAGE_DIR}/"
mkdir -p "${STAGE_DIR}/data"
cp -r data/* "${STAGE_DIR}/data/"

# Clean any pycache
find "${STAGE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Archive files
tar --exclude="*__pycache__*" -czf "${DIST_DIR}/${TARBALL_NAME}" -C "${TMP_DIR}" "${APP_NAME}-${VERSION}"

# Compute size and checksum
FILE_SIZE=$(stat -c%s "${DIST_DIR}/${TARBALL_NAME}")
SHA256_HASH=$(sha256sum "${DIST_DIR}/${TARBALL_NAME}" | awk '{print $1}')

echo ""
echo "=========================================================="
echo "Release archive created successfully:"
echo "  File:    ${DIST_DIR}/${TARBALL_NAME}"
echo "  Size:    ${FILE_SIZE} bytes"
echo "  SHA256:  ${SHA256_HASH}"
echo "=========================================================="
