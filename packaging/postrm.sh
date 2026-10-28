#!/bin/sh
# Run after removal (deb postrm, rpm %postun).
systemctl daemon-reload 2>/dev/null || true
command -v udevadm >/dev/null && udevadm control --reload 2>/dev/null || true
exit 0
