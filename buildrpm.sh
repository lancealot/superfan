#!/bin/bash
# Build the superfan RPM from this checkout.
#
#   ./buildrpm.sh                 result under ~/rpmbuild/RPMS/noarch/
#   TOPDIR=/some/dir ./buildrpm.sh
#
# Needs: rpm-build python3-devel python3-pip python3-setuptools python3-wheel
#        python3-pyyaml pyproject-rpm-macros systemd-rpm-macros
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v rpmbuild >/dev/null 2>&1; then
    echo "rpmbuild not found. Install the build tools first:" >&2
    echo "  sudo dnf install rpm-build python3-devel python3-pip python3-setuptools python3-wheel \\" >&2
    echo "                   python3-pyyaml pyproject-rpm-macros systemd-rpm-macros" >&2
    exit 1
fi

VERSION=$(awk '/^Version:/ {print $2}' superfan.spec)
TOPDIR=${TOPDIR:-$HOME/rpmbuild}
TARBALL="$TOPDIR/SOURCES/superfan-$VERSION.tar.gz"
mkdir -p "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [ -n "$(git status --porcelain)" ]; then
        echo "note: uncommitted changes are not included; the RPM is built from the last commit" >&2
    fi
    git archive --format=tar.gz --prefix="superfan-$VERSION/" -o "$TARBALL" HEAD
else
    tar --exclude-vcs --exclude='*.egg-info' --exclude=__pycache__ --exclude=build --exclude=dist \
        --transform "s,^\.,superfan-$VERSION," -czf "$TARBALL" .
fi

cp superfan.spec "$TOPDIR/SPECS/"
rpmbuild --define "_topdir $TOPDIR" -ba "$TOPDIR/SPECS/superfan.spec"

echo
echo "Built:"
find "$TOPDIR/RPMS" "$TOPDIR/SRPMS" -name "superfan-$VERSION-*.rpm" | sed 's/^/  /'
