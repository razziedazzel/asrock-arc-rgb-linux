#!/bin/sh
# Install arc-rgb system-wide. Run with: sudo ./install.sh
# Pass --no-service to skip the boot/resume service.
set -e

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX=/usr/local
LIB=$PREFIX/lib/arc-rgb
USER_NAME=${SUDO_USER:-}

install -d "$LIB" "$PREFIX/bin" "$PREFIX/share/applications"
install -m 0755 "$SRC/arc_rgb.py" "$SRC/arc_rgb_gui.py" "$LIB/"
ln -sf "$LIB/arc_rgb.py" "$PREFIX/bin/arc-rgb"
ln -sf "$LIB/arc_rgb_gui.py" "$PREFIX/bin/arc-rgb-gui"
install -m 0644 "$SRC/packaging/arc-rgb.desktop" "$PREFIX/share/applications/arc-rgb.desktop"

install -m 0644 "$SRC/packaging/60-asrock-arc-rgb.rules" /etc/udev/rules.d/60-asrock-arc-rgb.rules
echo i2c-dev > /etc/modules-load.d/arc-rgb.conf
modprobe i2c-dev
udevadm control --reload
udevadm trigger --subsystem-match=i2c-dev

install -m 0644 "$SRC/packaging/arc-rgb@.service" /etc/systemd/system/arc-rgb@.service
systemctl daemon-reload
if [ "$1" != "--no-service" ] && [ -n "$USER_NAME" ]; then
    systemctl enable "arc-rgb@$USER_NAME.service"
    echo "Boot/resume service enabled for $USER_NAME (applies ~$USER_NAME/.config/arc-rgb/config.json)."
fi
echo "Installed. Run 'arc-rgb status' or open 'Arc RGB' from your app menu."
