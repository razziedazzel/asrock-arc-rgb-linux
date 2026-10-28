#!/bin/sh
# Build dist/asrock-arc-rgb-VERSION-1.noarch.rpm with rpmbuild.   usage: build-rpm.sh VERSION
set -e
VERSION=${1:?usage: $0 VERSION}
SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
install -d "$SRC/dist"

rpmbuild -bb --quiet \
    --define "_topdir $WORK" \
    --define "srcroot $SRC" \
    --define "pkgversion $VERSION" \
    "$SRC/packaging/asrock-arc-rgb.spec"
cp "$WORK"/RPMS/noarch/*.rpm "$SRC/dist/"
