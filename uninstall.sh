#!/bin/bash

# Uninstall the package
sudo pip uninstall -y superfan

# Remove configuration file if it exists
if [ -f "/etc/superfan/config.yaml" ]; then
    sudo rm -f /etc/superfan/config.yaml
fi

# Remove config directory if empty
if [ -d "/etc/superfan" ]; then
    sudo rmdir --ignore-fail-on-non-empty /etc/superfan
fi

# Remove any .pyc files
find . -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -r {} +

echo "Superfan has been uninstalled"
