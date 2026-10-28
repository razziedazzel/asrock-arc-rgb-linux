#!/bin/sh
# Run after install/upgrade (deb postinst, rpm %post).
modprobe i2c-dev 2>/dev/null || true
if command -v udevadm >/dev/null; then
    udevadm control --reload 2>/dev/null || true
    udevadm trigger --subsystem-match=i2c-dev 2>/dev/null || true
fi
systemctl daemon-reload 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
