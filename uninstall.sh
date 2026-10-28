#!/bin/sh
# Remove arc-rgb. Run with: sudo ./uninstall.sh  (saved configs in ~/.config/arc-rgb are kept)
set -e

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }
for unit in $(systemctl list-unit-files 'arc-rgb@*' --no-legend | awk '{print $1}'); do
    systemctl disable "$unit" || true
done
rm -f /etc/systemd/system/arc-rgb@.service /etc/udev/rules.d/60-asrock-arc-rgb.rules \
      /etc/modules-load.d/arc-rgb.conf /usr/local/bin/arc-rgb /usr/local/bin/arc-rgb-gui \
      /usr/local/share/applications/arc-rgb.desktop
rm -rf /usr/local/lib/arc-rgb
systemctl daemon-reload
udevadm control --reload
echo "Uninstalled."
