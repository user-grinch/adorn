#!/usr/bin/env bash
set -euo pipefail

VERSION="1.0.0"
APP_NAME="adorn"
DEB_DIR="$(pwd)/dist"
TMP_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "==> Building Debian package for ${APP_NAME} v${VERSION}..."
mkdir -p "${DEB_DIR}"

PKG_ROOT="${TMP_DIR}/${APP_NAME}_${VERSION}-1_all"
mkdir -p "${PKG_ROOT}/DEBIAN"
mkdir -p "${PKG_ROOT}/usr/bin"
mkdir -p "${PKG_ROOT}/usr/lib/python3/dist-packages"
mkdir -p "${PKG_ROOT}/usr/share/applications"
mkdir -p "${PKG_ROOT}/usr/share/metainfo"
mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${PKG_ROOT}/usr/share/doc/${APP_NAME}"

# Control file
cat <<EOF > "${PKG_ROOT}/DEBIAN/control"
Package: ${APP_NAME}
Version: ${VERSION}-1
Section: utils
Priority: optional
Architecture: all
Maintainer: Grinch_ <user.grinch@gmail.com>
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-glib-2.0, libglib2.0-bin
Description: GNOME Customization Storefront & Desktop Tweaks
 A native GTK4 and Libadwaita customization storefront and desktop tweaks
 manager for modern GNOME desktops.
EOF

# Copy application files
cp -r adorn "${PKG_ROOT}/usr/lib/python3/dist-packages/"
cp run.py "${PKG_ROOT}/usr/bin/${APP_NAME}"
chmod +x "${PKG_ROOT}/usr/bin/${APP_NAME}"

cp data/com.grinch.gnome.adorn.desktop "${PKG_ROOT}/usr/share/applications/"
cp data/com.grinch.gnome.adorn.metainfo.xml "${PKG_ROOT}/usr/share/metainfo/"
cp data/icons/com.grinch.gnome.adorn.svg "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/"
cp LICENSE "${PKG_ROOT}/usr/share/doc/${APP_NAME}/copyright"
cp README.md "${PKG_ROOT}/usr/share/doc/${APP_NAME}/"

DEB_OUT="${DEB_DIR}/${APP_NAME}_${VERSION}-1_all.deb"

if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --build --root-owner-group "${PKG_ROOT}" "${DEB_OUT}"
else
    # Build using ar + tar if dpkg-deb is not present
    echo "  (dpkg-deb not found; using standard tar+ar packaging)"
    echo "2.0" > "${TMP_DIR}/debian-binary"
    tar --owner=0 --group=0 -czf "${TMP_DIR}/control.tar.gz" -C "${PKG_ROOT}/DEBIAN" .
    tar --owner=0 --group=0 --exclude="./DEBIAN" -czf "${TMP_DIR}/data.tar.gz" -C "${PKG_ROOT}" .
    ar rcs "${DEB_OUT}" "${TMP_DIR}/debian-binary" "${TMP_DIR}/control.tar.gz" "${TMP_DIR}/data.tar.gz"
fi

echo "✓ Successfully built: ${DEB_OUT}"
