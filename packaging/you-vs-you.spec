# PyInstaller spec: one folder app per OS.   uv run --group build pyinstaller packaging/you-vs-you.spec
# onedir, not onefile: a onefile app unpacks hundreds of MB to a temp folder on every launch.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
src = root / "src"
icons = root / "packaging" / "icons"

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(src)],
    datas=[
        (str(src / "pco" / "play" / "static"), "pco/play/static"),
        (str(icons / "icon.png"), "pco"),  # the control window's icon
    ],
    hiddenimports=[],
    excludes=["pytest", "_pytest", "IPython", "matplotlib", "scipy", "pandas", "PIL"],
)
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
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="YouVsYou.app",
        icon=str(icons / "icon.icns"),
        bundle_identifier="com.madebyshaad.youvsyou",
        info_plist={
            "CFBundleDisplayName": "You vs. You",
            "CFBundleShortVersionString": "0.1.2",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
