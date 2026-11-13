"""Trim the Linux build: keep what the Qt WebEngine window needs, leave the rest to the host.

PyInstaller collects everything PyQt6 ships (QML, Quick 3D, 40 translations) and every
system library any collected file links to, copied from the build machine. Two kinds of
copied system library break the app on another distribution:

- graphics and C++ runtime (Mesa's libgbm/libdrm, libstdc++): the host's GPU driver needs
  the host's own copies, and an older bundled libstdc++ stops it loading;
- desktop plumbing (X11, fontconfig, glib): every desktop has them, and an old copy next to
  the host's newer ones causes symbol and config errors.

So those come from the host, Qt's GTK theme plugin (which drags in all of GTK) is dropped,
and then every bundled library nothing still links to is dropped too.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

# Qt pieces the window never loads.
QT_UNUSED = re.compile(
    r"^PyQt6/Qt6/("
    r"qml/"
    r"|translations/(?!qtwebengine_locales/en-US\.pak$)"  # the page is English
    r"|plugins/(platformthemes/libqgtk3|position/|generic/|egldeviceintegrations/"
    r"|sensors/|multimedia/|qmltooling/|sqldrivers/|designer/|renderers/|scenegraph/)"
    r"|resources/qtwebengine_devtools_resources\.pak$"
    r")"
)

# Libraries the host must provide (present on every Linux desktop, tied to its drivers).
HOST_ONLY = re.compile(
    r"^lib(stdc\+\+|gcc_s|gbm|drm|GL|GLX|GLdispatch|OpenGL|EGL|asound|X11|X11-xcb|xcb|Xext"
    r"|Xau|Xdmcp|fontconfig|freetype|harfbuzz|expat|z|glib-2\.0|gobject-2\.0|gio-2\.0"
    r"|gmodule-2\.0|gthread-2\.0)\.so(\.|$)"
)

# Loaded with dlopen, so nothing lists them as a dependency.
DLOPENED = re.compile(r"^lib(python3|softokn3|freebl3|freeblpriv3|nssckbi|nssdbm3)")


def _needed(path: str) -> list[str]:
    out = subprocess.run(["readelf", "-d", path], capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


def _prunable(entry) -> bool:
    """A plain shared library (not a Python extension, plugin or helper program)."""
    dest, _src, kind = entry
    name = Path(dest).name
    return (
        kind == "BINARY"
        and name.startswith("lib")
        and ".so" in name
        and "/plugins/" not in dest
        and not DLOPENED.match(name)
    )


def prune(binaries: list, datas: list) -> tuple[list, list]:
    def wanted(entry) -> bool:
        dest = entry[0]
        return not QT_UNUSED.match(dest) and not HOST_ONLY.match(Path(dest).name)

    binaries = [b for b in binaries if wanted(b)]
    datas = [d for d in datas if wanted(d)]
    if shutil.which("readelf") is None:
        print("prune_linux: readelf missing; unused libraries are kept")
        return binaries, datas

    files = [b for b in binaries if b[2] != "SYMLINK"]
    reached: set[str] = set()
    todo = [b for b in files if not _prunable(b)]  # roots: extensions, plugins, programs
    by_name: dict[str, list] = {}
    for b in files:
        by_name.setdefault(Path(b[0]).name, []).append(b)
    seen: set[str] = set()
    while todo:
        dest, src, _kind = todo.pop()
        if dest in seen:
            continue
        seen.add(dest)
        for lib in _needed(src):
            reached.add(lib)
            todo.extend(by_name.get(lib, []))

    def keep(entry) -> bool:
        if entry[2] == "SYMLINK":  # a link to a library in a sub-folder
            return Path(entry[0]).name in reached or Path(entry[1]).name in reached
        return not _prunable(entry) or Path(entry[0]).name in reached

    dropped = [b[0] for b in binaries if not keep(b)]
    print(f"prune_linux: dropped {len(dropped)} unused libraries")
    return [b for b in binaries if keep(b)], datas


def host_libs(folder: Path) -> list[Path]:
    """Host-only libraries in a finished build (none may ship: they break the host's GPU driver)."""
    return sorted(p for p in folder.rglob("lib*.so*") if HOST_ONLY.match(p.name))


def remove_host_libs(folder: Path) -> None:
    """PyInstaller can collect some after the Analysis lists are pruned; remove those too."""
    found = host_libs(folder)
    for p in found:
        p.unlink()
    print(f"prune_linux: removed {len(found)} host libraries from {folder.name}")


if __name__ == "__main__":  # prune_linux.py dist/YouVsYou: exit 1 if a host library ships
    left = host_libs(Path(sys.argv[1]))
    for p in left:
        print(f"host library in the build: {p}")
    sys.exit(1 if left else 0)
