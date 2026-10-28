#!/bin/sh
# Run before final removal (deb prerm "remove", rpm %preun on erase): turn off every user's service.
for unit in $(systemctl list-unit-files 'arc-rgb@*' --state=enabled --no-legend 2>/dev/null | awk '{print $1}'); do
    systemctl disable "$unit" 2>/dev/null || true
done
for dir in /etc/systemd/system/*.wants; do
    rm -f "$dir"/arc-rgb@*.service
done
exit 0
