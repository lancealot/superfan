#!/bin/bash
# Install superfan from this checkout, for systems where the RPM is not an option.
#
# Puts the program in its own virtual environment under /opt/superfan, links
# /usr/local/bin/superfan to it, and installs the config and the systemd unit.
# An existing /etc/superfan/config.yaml is never overwritten.
# The service is NOT enabled or started: calibrate first (see the README).
set -euo pipefail
cd "$(dirname "$0")"

PREFIX=/opt/superfan
BIN=/usr/local/bin/superfan
ETC=/etc/superfan
UNIT=/etc/systemd/system/superfan.service

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh" >&2
    exit 1
fi

echo "Installing system packages..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq ipmitool python3 python3-venv
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q ipmitool python3
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q ipmitool python3
else
    echo "No supported package manager found; install ipmitool and python3 (3.9 or newer) yourself." >&2
fi

echo "Installing superfan into $PREFIX..."
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade .
ln -sf "$PREFIX/venv/bin/superfan" "$BIN"

echo "Setting up the IPMI kernel modules..."
printf 'ipmi_devintf\nipmi_si\n' > /etc/modules-load.d/superfan.conf
modprobe -a ipmi_devintf ipmi_si 2>/dev/null \
    || echo "  could not load ipmi_devintf/ipmi_si now (no BMC here?); they will be tried again at boot"

install -d -m 0755 "$ETC"
if [ ! -f "$ETC/config.yaml" ]; then
    install -m 0644 config/default.yaml "$ETC/config.yaml"
    echo "Installed $ETC/config.yaml"
elif ! cmp -s config/default.yaml "$ETC/config.yaml"; then
    install -m 0644 config/default.yaml "$ETC/config.yaml.new"
    echo "Kept your $ETC/config.yaml; the current default is in $ETC/config.yaml.new"
fi

sed "s#/usr/bin/superfan#$BIN#g" superfan.service > "$UNIT"
chmod 0644 "$UNIT"
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    if systemctl is-active --quiet superfan.service; then
        echo "Restarting the running service..."
        systemctl restart superfan.service
    fi
fi

echo
echo "superfan $("$BIN" --version | awk '{print $2}') installed. The service is not enabled yet. Next:"
echo "  superfan sensors                    # what this machine can measure"
echo "  \$EDITOR $ETC/config.yaml   # make every pattern match"
echo "  superfan check"
echo "  superfan calibrate"
echo "  systemctl enable --now superfan"
