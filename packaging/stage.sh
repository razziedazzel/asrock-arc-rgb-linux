#!/bin/sh
# Lay out arc-rgb's files under DESTDIR. Shared by install.sh and the .deb/.rpm builds.
#
#   stage.sh DESTDIR MODE VERSION
#     MODE=package  -> /usr, units/rules/modules under /usr/lib (distro packages)
#     MODE=local    -> /usr/local, units/rules/modules under /etc (install.sh)
set -e

DESTDIR=$1
MODE=$2
VERSION=$3
[ -n "$DESTDIR" ] && [ -n "$VERSION" ] || { echo "usage: $0 DESTDIR package|local VERSION" >&2; exit 2; }
SRC=$(cd "$(dirname "$0")/.." && pwd)
APP_ID=io.github.razziedazzel.ArcRGB

case $MODE in
    package) PREFIX=/usr;       SYSDIR=/usr/lib ;;
    local)   PREFIX=/usr/local; SYSDIR=/etc ;;
    *) echo "MODE must be package or local" >&2; exit 2 ;;
esac
LIBDIR=$PREFIX/lib/arc-rgb
BINDIR=$PREFIX/bin
UNITDIR=$SYSDIR/systemd/system
[ "$MODE" = package ] && UDEVDIR=$SYSDIR/udev/rules.d || UDEVDIR=/etc/udev/rules.d

install -d "$DESTDIR$LIBDIR" "$DESTDIR$BINDIR" "$DESTDIR$UNITDIR" "$DESTDIR$UDEVDIR" \
    "$DESTDIR$SYSDIR/modules-load.d" "$DESTDIR/usr/share/polkit-1/actions" \
    "$DESTDIR$PREFIX/share/applications" "$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps"

for f in arc_rgb.py arc_rgb_gui.py; do
    # Pin the interpreter (-B: no root-owned __pycache__ when run via pkexec) and stamp the version
    sed -e '1s|^#!.*|#!/usr/bin/python3 -B|' \
        -e "s|^__version__ = .*|__version__ = \"$VERSION\"|" \
        "$SRC/$f" > "$DESTDIR$LIBDIR/$f"
    chmod 0755 "$DESTDIR$LIBDIR/$f"
done
ln -sf ../lib/arc-rgb/arc_rgb.py "$DESTDIR$BINDIR/arc-rgb"
ln -sf ../lib/arc-rgb/arc_rgb_gui.py "$DESTDIR$BINDIR/arc-rgb-gui"

sed "s|@BINDIR@|$BINDIR|g" "$SRC/packaging/arc-rgb@.service.in" > "$DESTDIR$UNITDIR/arc-rgb@.service"
sed "s|@LIBDIR@|$LIBDIR|g" "$SRC/packaging/io.github.razziedazzel.arcrgb.policy.in" \
    > "$DESTDIR/usr/share/polkit-1/actions/io.github.razziedazzel.arcrgb.policy"
install -m 0644 "$SRC/packaging/60-asrock-arc-rgb.rules" "$DESTDIR$UDEVDIR/60-asrock-arc-rgb.rules"
echo i2c-dev > "$DESTDIR$SYSDIR/modules-load.d/arc-rgb.conf"
install -m 0644 "$SRC/packaging/$APP_ID.desktop" "$DESTDIR$PREFIX/share/applications/$APP_ID.desktop"
install -m 0644 "$SRC/packaging/$APP_ID.svg" "$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
chmod 0644 "$DESTDIR$UNITDIR/arc-rgb@.service" "$DESTDIR/usr/share/polkit-1/actions/io.github.razziedazzel.arcrgb.policy" \
    "$DESTDIR$SYSDIR/modules-load.d/arc-rgb.conf"
