#!/bin/sh
# Remove a source install made by install.sh. Run with: sudo ./uninstall.sh
# (Saved settings in ~/.config/arc-rgb are kept. Package installs: use your package manager.)
set -e

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
sh "$SRC/packaging/prerm.sh"
rm -rf /usr/local/lib/arc-rgb
rm -f /usr/local/bin/arc-rgb /usr/local/bin/arc-rgb-gui \
      /etc/systemd/system/arc-rgb@.service \
      /etc/udev/rules.d/60-asrock-arc-rgb.rules \
      /etc/modules-load.d/arc-rgb.conf \
      /usr/share/polkit-1/actions/io.github.razziedazzel.arcrgb.policy \
      /usr/local/share/applications/io.github.razziedazzel.ArcRGB.desktop \
      /usr/local/share/icons/hicolor/scalable/apps/io.github.razziedazzel.ArcRGB.svg
sh "$SRC/packaging/postrm.sh"
echo "Uninstalled."
