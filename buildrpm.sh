#!/bin/bash

# Exit on error
set -e

# Function to check if command succeeded
check_status() {
    if [ $? -eq 0 ]; then
        echo "✓ $1"
    else
        echo "✗ $1 failed"
        exit 1
    fi
}

# Check for required tools
check_required_tools() {
    local missing_tools=()
    
    # Check for rpm-build
    if ! command -v rpmbuild >/dev/null 2>&1; then
        missing_tools+=("rpm-build")
    fi
    
    # Check for rpmdevtools
    if ! command -v rpmdev-setuptree >/dev/null 2>&1; then
        missing_tools+=("rpmdevtools")
    fi
    
    # If any tools are missing, print message and exit
    if [ ${#missing_tools[@]} -ne 0 ]; then
        echo "Error: Required tools are missing. Please install:"
        printf '  - %s\n' "${missing_tools[@]}"
        echo
        echo "On Red Hat-based systems (RHEL/CentOS/Fedora), install with:"
        echo "  sudo dnf install ${missing_tools[*]}"
        echo
        echo "On Debian-based systems (Ubuntu/Debian), install with:"
        echo "  sudo apt-get install ${missing_tools[*]}"
        exit 1
    fi
}

echo "Starting RPM build process for Superfan..."

# Check for required tools before proceeding
echo "Checking for required tools..."
check_required_tools
check_status "Required tools check passed"

# Create necessary directories
echo "Creating RPM build directories..."
mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
check_status "RPM directories created"

# Create source tarball
echo "Creating source tarball..."
VERSION=$(grep "Version:" superfan.spec | awk '{print $2}')
cd ..
mkdir -p superfan-${VERSION}
cp -r superfan/* superfan-${VERSION}/
tar czf ~/rpmbuild/SOURCES/superfan-${VERSION}.tar.gz superfan-${VERSION}/
rm -rf superfan-${VERSION}
cd superfan/
check_status "Source tarball created"

# Copy spec file
echo "Copying spec file..."
cp superfan.spec ~/rpmbuild/SPECS/
check_status "Spec file copied"

# Build RPM
echo "Building RPM..."
rpmbuild -ba ~/rpmbuild/SPECS/superfan.spec
check_status "RPM built successfully"

echo "✓ RPM build process completed"
echo
echo "RPM files can be found in:"
echo "  - Binary RPM: ~/rpmbuild/RPMS/$(uname -m)/"
echo "  - Source RPM: ~/rpmbuild/SRPMS/"
