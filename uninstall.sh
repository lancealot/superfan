#!/bin/bash

# Exit on error
set -e

echo "Starting Superfan uninstallation..."

# Function to check if command succeeded
check_status() {
    if [ $? -eq 0 ]; then
        echo "✓ $1"
    else
        echo "✗ $1 failed"
        exit 1
    fi
}

# Stop and disable systemd service if it exists
if systemctl is-active --quiet superfan; then
    echo "Stopping superfan service..."
    sudo systemctl stop superfan
    check_status "Service stopped"
fi

if systemctl is-enabled --quiet superfan 2>/dev/null; then
    echo "Disabling superfan service..."
    sudo systemctl disable superfan
    check_status "Service disabled"
fi

# Remove systemd service file
if [ -f "/etc/systemd/system/superfan.service" ]; then
    echo "Removing systemd service file..."
    sudo rm -f /etc/systemd/system/superfan.service
    sudo systemctl daemon-reload
    check_status "Service file removed"
fi

# Uninstall the package
echo "Uninstalling Python package..."
sudo pip uninstall -y superfan
check_status "Package uninstalled"

# Remove configuration files
if [ -f "/etc/superfan/config.yaml" ]; then
    echo "Removing configuration files..."
    sudo rm -f /etc/superfan/config.yaml
    check_status "Configuration file removed"
fi

# Remove config directory if empty
if [ -d "/etc/superfan" ]; then
    echo "Removing config directory if empty..."
    sudo rmdir --ignore-fail-on-non-empty /etc/superfan
    check_status "Config directory handled"
fi

# Clean up Python cache files
echo "Cleaning up Python cache files..."
find . -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
check_status "Cache files cleaned"

# Remove kernel module configuration
if [ -f "/etc/modules-load.d/superfan.conf" ]; then
    echo "Removing kernel module configuration..."
    sudo rm -f /etc/modules-load.d/superfan.conf
    check_status "Kernel module configuration removed"
fi

echo "✓ Superfan has been successfully uninstalled"
