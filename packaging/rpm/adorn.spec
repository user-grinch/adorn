Name:           adorn
Version:        1.0.0
Release:        1%{?dist}
Summary:        GNOME Customization Storefront & Desktop Tweaks

License:        GPL-3.0-or-later
URL:            https://github.com/user-grinch/adorn
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita

%description
Adorn is a native GTK4 and Libadwaita customization storefront and desktop tweaks
manager for modern GNOME desktops. Allows browsing, installing, and managing
GNOME Shell extensions, themes, icon packs, cursors, and wallpapers.

%prep
%autosetup -n %{name}-%{version}

%build
%py3_build

%install
%py3_install

%files
%license LICENSE
%doc README.md
%{_bindir}/adorn
%{python3_sitelib}/adorn/
%{python3_sitelib}/adorn-*.egg-info/
%{_datadir}/applications/com.grinch.gnome.adorn.desktop
%{_datadir}/metainfo/com.grinch.gnome.adorn.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/com.grinch.gnome.adorn.svg

%changelog
* Fri Nov 06 2026 Grinch_ <user.grinch@gmail.com> - 1.0.0-1
- Initial release under GPL-3.0-or-later
