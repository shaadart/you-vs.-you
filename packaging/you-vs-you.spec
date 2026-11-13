# PyInstaller spec: one folder app per OS.   uv run --group build pyinstaller packaging/you-vs-you.spec
# onedir, not onefile: a onefile app unpacks hundreds of MB to a temp folder on every launch.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
src = root / "src"
icons = root / "packaging" / "icons"

# The model and engine travel inside the app (packaging/fetch_assets.py puts them here), so a
# first launch needs no network.
bundled = root / "packaging" / "_bundled" / "assets"
bundled_datas = [(str(bundled), "bundled")] if bundled.is_dir() else []

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(src)],
    datas=[
        (str(src / "pco" / "play" / "static"), "pco/play/static"),
        (str(icons / "icon.png"), "pco"),  # the window icon
        *bundled_datas,
    ],
    hiddenimports=[],
    excludes=[
        "pytest", "_pytest", "IPython", "matplotlib", "scipy", "pandas", "PIL", "gi",
        # Qt modules the window does not use (pywebview needs Widgets + WebEngine only)
        "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.QtQuickWidgets", "PyQt6.QtPositioning",
        "PyQt6.QtOpenGLWidgets", "PyQt6.QtMultimedia", "PyQt6.QtWebEngineQuick",
    ],
)
if sys.platform.startswith("linux"):  # the window's Qt, without the build host's libraries
    sys.path.insert(0, str(root / "packaging"))
    from prune_linux import prune, remove_host_libs

    a.binaries, a.datas = prune(a.binaries, a.datas)
pyz = PYZ(a.pure)
icon = {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YouVsYou",
    console=False,  # no terminal window
    icon=str(icons / icon) if icon else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="YouVsYou")
if sys.platform.startswith("linux"):
    remove_host_libs(Path(DISTPATH) / "YouVsYou")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="YouVsYou.app",
        icon=str(icons / "icon.icns"),
        bundle_identifier="com.madebyshaad.youvsyou",
        info_plist={
            "CFBundleDisplayName": "You vs. You",
            "CFBundleShortVersionString": "0.2.2",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
