#!/bin/sh
# Build dist/asrock-arc-rgb_VERSION_all.deb with dpkg-deb.   usage: build-deb.sh VERSION
set -e
VERSION=${1:?usage: $0 VERSION}
SRC=$(cd "$(dirname "$0")/.." && pwd)
OUT=$SRC/dist
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
ROOT=$WORK/root

"$SRC/packaging/stage.sh" "$ROOT" package "$VERSION"
install -d "$ROOT/DEBIAN" "$OUT"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: asrock-arc-rgb
Version: $VERSION
Architecture: all
Maintainer: razziedazzel <68195773+razziedazzel@users.noreply.github.com>
Homepage: https://github.com/razziedazzel/asrock-arc-rgb-linux
Section: utils
Priority: optional
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, kmod, udev, systemd, pkexec | policykit-1
Description: RGB lighting control for ASRock Intel Arc graphics cards
 Command-line tool and GTK 4 app for the LED controller on ASRock Arc
 cards (B580 Steel Legend, A770), with an optional service that re-applies
 the saved lighting at boot and after resume.
EOF

cp "$SRC/packaging/postinst.sh" "$ROOT/DEBIAN/postinst"
{ echo '#!/bin/sh'; echo '[ "$1" = remove ] || exit 0'; tail -n +2 "$SRC/packaging/prerm.sh"; } > "$ROOT/DEBIAN/prerm"
cp "$SRC/packaging/postrm.sh" "$ROOT/DEBIAN/postrm"
chmod 0755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/prerm" "$ROOT/DEBIAN/postrm"

dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$OUT/asrock-arc-rgb_${VERSION}_all.deb"
