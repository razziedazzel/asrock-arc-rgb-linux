# Built by packaging/build-rpm.sh, which passes the source tree as the srcroot macro.
Name:           asrock-arc-rgb
Version:        %{pkgversion}
Release:        1
Summary:        RGB lighting control for ASRock Intel Arc graphics cards
License:        MIT
URL:            https://github.com/razziedazzel/asrock-arc-rgb-linux
BuildArch:      noarch
Requires:       python3, python3-gobject, gtk4, libadwaita, polkit, kmod, systemd-udev

# Pure Python with a pinned shebang; no byte-compiling into the payload
%global __brp_python_bytecompile %{nil}
%global debug_package %{nil}
%global source_date_epoch_from_changelog 0

%description
Command-line tool and GTK 4 app for the LED controller on ASRock Arc cards
(B580 Steel Legend, A770), with an optional service that re-applies the
saved lighting at boot and after resume.

%install
%{srcroot}/packaging/stage.sh %{buildroot} package %{version}

%post
%{lua: print(io.open(rpm.expand("%{srcroot}") .. "/packaging/postinst.sh"):read("a"))}

%preun
if [ "$1" -eq 0 ]; then
%{lua: print(io.open(rpm.expand("%{srcroot}") .. "/packaging/prerm.sh"):read("a"))}
fi

%postun
%{lua: print(io.open(rpm.expand("%{srcroot}") .. "/packaging/postrm.sh"):read("a"))}

%files
/usr/lib/arc-rgb/
/usr/bin/arc-rgb
/usr/bin/arc-rgb-gui
/usr/lib/systemd/system/arc-rgb@.service
/usr/lib/udev/rules.d/60-asrock-arc-rgb.rules
/usr/lib/modules-load.d/arc-rgb.conf
/usr/share/polkit-1/actions/io.github.razziedazzel.arcrgb.policy
/usr/share/applications/io.github.razziedazzel.ArcRGB.desktop
/usr/share/icons/hicolor/scalable/apps/io.github.razziedazzel.ArcRGB.svg
