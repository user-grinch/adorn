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

echo "==> Building RPM package for ${APP_NAME} v${VERSION}..."
mkdir -p "${DIST_DIR}"

# First ensure source tarball is built
./scripts/build_release_tarball.sh > /dev/null

if command -v rpmbuild >/dev/null 2>&1; then
    RPM_TOPDIR="${TMP_DIR}/rpmbuild"
    mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

    cp "${DIST_DIR}/${APP_NAME}-${VERSION}.tar.gz" "${RPM_TOPDIR}/SOURCES/"
    cp packaging/rpm/adorn.spec "${RPM_TOPDIR}/SPECS/"

    rpmbuild --define "_topdir ${RPM_TOPDIR}" -ba "${RPM_TOPDIR}/SPECS/adorn.spec"

    find "${RPM_TOPDIR}/RPMS" -type f -name "*.rpm" -exec cp {} "${DIST_DIR}/" \;
    find "${RPM_TOPDIR}/SRPMS" -type f -name "*.src.rpm" -exec cp {} "${DIST_DIR}/" \;
    echo "✓ RPM package(s) built in ${DIST_DIR}"
else
    echo "  (rpmbuild command not found; see packaging/rpm/adorn.spec or run in Fedora container / GitHub Actions)"
fi
