# Disable debug package
%global debug_package %{nil}

Name:           superfan
Version:        0.1.0
Release:        1%{?dist}
Summary:        Intelligent fan control for Supermicro servers
License:        GPL-3.0
URL:            https://github.com/yourusername/superfan
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
Requires:       python3
Requires:       python3-pip
Requires:       ipmitool
Requires:       nvme-cli
Requires:       systemd

%description
A Python utility for intelligent control of Supermicro server fan speeds based on 
component temperatures and user preferences. Supports multiple Supermicro generations
(X9/X10/X11/X13) with features like automated fan speed control, NVMe drive temperature
monitoring, zone-based fan control, and custom fan curves.

%prep
%autosetup

%build
%py3_build

%install
# Override default installation path to use /usr/local/bin
PYTHONPATH=%{buildroot}%{python3_sitelib} \
    %{__python3} setup.py install --skip-build --root %{buildroot} --prefix=/usr/local

# Create config directory
mkdir -p %{buildroot}%{_sysconfdir}/superfan

# Install config file
install -p -m 644 config/default.yaml %{buildroot}%{_sysconfdir}/superfan/config.yaml

# Install systemd service
mkdir -p %{buildroot}%{_unitdir}
install -p -m 644 superfan.service %{buildroot}%{_unitdir}/superfan.service

# Create modules-load.d config
mkdir -p %{buildroot}%{_sysconfdir}/modules-load.d
echo "ipmi_devintf" > %{buildroot}%{_sysconfdir}/modules-load.d/superfan.conf
echo "ipmi_si" >> %{buildroot}%{_sysconfdir}/modules-load.d/superfan.conf

%post
%systemd_post superfan.service
# Load IPMI modules
modprobe ipmi_devintf >/dev/null 2>&1 || :
modprobe ipmi_si >/dev/null 2>&1 || :

%preun
%systemd_preun superfan.service

%postun
%systemd_postun_with_restart superfan.service

%files
%license LICENSE
%doc README.md USAGE.md
/usr/local/lib/python3.12/site-packages/superfan
/usr/local/lib/python3.12/site-packages/superfan-%{version}*
/usr/local/bin/superfan
%dir %{_sysconfdir}/superfan
%config(noreplace) %{_sysconfdir}/superfan/config.yaml
%{_unitdir}/superfan.service
%config(noreplace) %{_sysconfdir}/modules-load.d/superfan.conf

%changelog
* Wed Jan 24 2024 System Administrator <root@localhost> - 0.1.0-1
- Initial RPM release
