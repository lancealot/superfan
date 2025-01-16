#!/bin/bash

# Exit on error
set -e

echo "Starting Superfan installation..."

# Function to check if command succeeded
check_status() {
    if [ $? -eq 0 ]; then
        echo "✓ $1"
    else
        echo "✗ $1 failed"
        exit 1
    fi
}

# Check if running as root/sudo
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Detect package manager
if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt-get"
    echo "Detected Debian-based system (using apt-get)"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
    echo "Detected Red Hat-based system (using dnf)"
elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
    echo "Detected Red Hat-based system (using yum)"
else
    echo "Unsupported package manager. Please install dependencies manually."
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
if [ "$PKG_MANAGER" = "apt-get" ]; then
    apt-get update
    apt-get install -y ipmitool nvme-cli python3-pip
elif [ "$PKG_MANAGER" = "dnf" ] || [ "$PKG_MANAGER" = "yum" ]; then
    $PKG_MANAGER install -y ipmitool nvme-cli python3-pip
fi
check_status "System dependencies installed"

# Load IPMI kernel modules
echo "Loading IPMI kernel modules..."
modprobe ipmi_devintf
modprobe ipmi_si
check_status "Kernel modules loaded"

# Add modules to load at boot
echo "Configuring kernel modules to load at boot..."
echo "ipmi_devintf" > /etc/modules-load.d/superfan.conf
echo "ipmi_si" >> /etc/modules-load.d/superfan.conf
check_status "Boot configuration updated"

# Create config directory
echo "Creating configuration directory..."
mkdir -p /etc/superfan
check_status "Config directory created"

# Copy default config
echo "Installing default configuration..."
cp config/default.yaml /etc/superfan/config.yaml
check_status "Default config installed"

# Install Python package
echo "Installing Python package..."
pip install .
check_status "Python package installed"

# Install systemd service
echo "Installing systemd service..."
cp superfan.service /etc/systemd/system/
systemctl daemon-reload
check_status "Systemd service installed"

# Enable and start service
echo "Enabling and starting superfan service..."
systemctl enable superfan
systemctl start superfan
check_status "Service enabled and started"

# Verify installation
echo "Verifying installation..."
if systemctl is-active --quiet superfan; then
    echo "✓ Superfan service is running"
else
    echo "✗ Service failed to start"
    exit 1
fi

if [ -f "/etc/superfan/config.yaml" ]; then
    echo "✓ Configuration file is present"
else
    echo "✗ Configuration file is missing"
    exit 1
fi

echo "✓ Superfan has been successfully installed"
echo
echo "Usage:"
echo "  - View service status: systemctl status superfan"
echo "  - View logs: journalctl -u superfan"
echo "  - Edit config: nano /etc/superfan/config.yaml"
echo "  - Restart service: systemctl restart superfan"
