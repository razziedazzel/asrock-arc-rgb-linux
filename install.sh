#!/bin/sh
# Install arc-rgb from this source tree into /usr/local. Run with: sudo ./install.sh
# Prefer the .deb / .rpm from the Releases page if your distro can use one.
# Add --enable-service to also turn on the boot/resume service for you.
set -e

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/arc_rgb.py")

"$SRC/packaging/stage.sh" / local "$VERSION"
sh "$SRC/packaging/postinst.sh"

if [ "$1" = "--enable-service" ] && [ -n "$SUDO_USER" ]; then
    /usr/local/bin/arc-rgb service enable --user "$SUDO_USER"
fi
echo "Installed arc-rgb $VERSION. Open 'Arc RGB' from your app menu or run 'arc-rgb status'."
echo "If the app can't reach the card, log out and back in once so the new permission applies."
