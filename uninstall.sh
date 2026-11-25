#!/bin/bash
# Remove what install.sh installed. Your configuration and calibration in
# /etc/superfan are kept unless you pass --purge.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo ./uninstall.sh" >&2
    exit 1
fi

if [ -d /run/systemd/system ] && [ -f /etc/systemd/system/superfan.service ]; then
    # Stopping the service hands the fans back to the BMC.
    systemctl disable --now superfan.service 2>/dev/null || true
elif [ -x /usr/local/bin/superfan ]; then
    /usr/local/bin/superfan release 2>/dev/null || true
fi

rm -f /etc/systemd/system/superfan.service /usr/local/bin/superfan /etc/modules-load.d/superfan.conf
rm -rf /opt/superfan /run/superfan
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi

if [ "${1:-}" = "--purge" ]; then
    rm -rf /etc/superfan
    echo "superfan removed, including /etc/superfan."
else
    echo "superfan removed. /etc/superfan was kept; pass --purge to remove it too."
fi
