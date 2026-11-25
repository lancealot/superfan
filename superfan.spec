Name:           superfan
Version:        0.2.0
Release:        1%{?dist}
Summary:        Temperature-driven fan control for Supermicro servers
License:        GPL-3.0-only
URL:            https://github.com/lancealot/superfan
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  python3-pyyaml
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros
Requires:       ipmitool
Requires:       python3-pyyaml
%{?systemd_requires}

%description
Superfan sets the fan duty cycle of each BMC fan zone on a Supermicro board
from the temperatures of whatever those fans cool: BMC sensors, any device the
kernel exposes through hwmon (CPUs, drives, GPUs, add-in cards) and any command
that prints a temperature. It hands fan control back to the BMC whenever it
stops, and fails towards full speed when a sensor or fan goes missing.

The service is installed disabled. Run "superfan sensors", edit
/etc/superfan/config.yaml, run "superfan calibrate", then enable it.

%prep
%autosetup

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files superfan
install -D -p -m 0644 config/default.yaml %{buildroot}%{_sysconfdir}/superfan/config.yaml
install -D -p -m 0644 superfan.service %{buildroot}%{_unitdir}/superfan.service
install -d %{buildroot}%{_modulesloaddir}
printf 'ipmi_devintf\nipmi_si\n' > %{buildroot}%{_modulesloaddir}/superfan.conf

%check
%pyproject_check_import

%post
%systemd_post superfan.service
modprobe -a ipmi_devintf ipmi_si >/dev/null 2>&1 || :

%preun
%systemd_preun superfan.service

%postun
%systemd_postun_with_restart superfan.service

%files -f %{pyproject_files}
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/superfan
%{_unitdir}/superfan.service
%{_modulesloaddir}/superfan.conf
%dir %{_sysconfdir}/superfan
%config(noreplace) %{_sysconfdir}/superfan/config.yaml

%changelog
* Wed Nov 25 2026 Superfan maintainers <noreply@github.com> - 0.2.0-1
- Rewrite: sensors from IPMI, hwmon and commands; per-zone curves; calibration
- Install under the system Python path instead of /usr/local
- Do not enable or start the service on install
- Require python3-pyyaml; drop the python3-pip requirement
