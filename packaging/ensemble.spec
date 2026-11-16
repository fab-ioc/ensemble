# PyInstaller build of the Ensemble app: a folder (Windows: dist/Ensemble with
# Ensemble.exe) or Ensemble.app (Mac, universal2). One folder, not one file:
# the agents' hooks start the app on every tool call, and a one-file build
# unpacks itself on each start.
#
#   pyinstaller --noconfirm packaging/ensemble.spec
#
# The Mac build needs a universal2 Python (the python.org installer).
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
import app_version  # noqa: E402

datas = [(str(ROOT / name), ".") for name in
         ("index.html", "session.html", "fileview.html", "pty-test.html",
          "restart-hub.ps1", "LICENSE")]
datas += [(str(ROOT / "static"), "static"), (str(ROOT / "skills"), "skills")]
# ptyhost.version() hashes these two sources (sys._MEIPASS/backends): without them the
# app cannot answer /api/version, so no update to it would ever install, and
# its PTY host never starts.
datas += [(str(ROOT / "backends" / name), "backends") for name in ("ptyhost.py", "ptyrun.py")]

MAC = sys.platform == "darwin"

# Notifications (webpush.py) need cryptography and its compiled parts: the
# Rust bindings and cffi's _cffi_backend, which those load from compiled code
# where the import analysis cannot see it. A build without them would ship a
# hub that cannot notify, so it stops here instead. On the Mac they must be
# universal2: packaging/mac-universal2-wheels.sh, after requirements.txt.
try:
    import cryptography.hazmat.bindings._rust  # noqa: E402,F401
    import _cffi_backend  # noqa: E402,F401
    import webpush  # noqa: E402,F401
except ImportError as e:
    raise SystemExit(f"ensemble.spec: notifications would not work ({e}); "
                     "pip install -r requirements.txt first") from e
hiddenimports = ["webpush", "_cffi_backend", "cryptography.hazmat.bindings._rust"]

binaries = []
if sys.platform == "win32":
    # pywinpty starts its terminals through OpenConsole.exe (and the older
    # winpty-agent.exe), which the import analysis does not see: without them
    # every agent's terminal stays blank and nothing starts.
    import winpty
    binaries += [(str(p), "winpty") for p in Path(winpty.__file__).parent.glob("*.exe")]

a = Analysis(
    [str(ROOT / "ensemble_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "test", "unittest.test"],
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ensemble",
    # No console window: the hub logs to a file, and a hook started with
    # pipes still reads and writes them.
    console=False,
    upx=False,
    target_arch="universal2" if MAC else None,
    icon=str(ROOT / "static" / "icons" / ("icon-512.png" if MAC else "favicon.ico")),
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Ensemble")

if MAC:
    app = BUNDLE(
        coll,
        name="Ensemble.app",
        icon=str(ROOT / "static" / "icons" / "icon-512.png"),
        bundle_identifier="com.ensemble.dashboard",
        version=app_version.VERSION,
        info_plist={
            "CFBundleShortVersionString": app_version.VERSION,
            "CFBundleVersion": app_version.VERSION,
            # The hub has no window of its own: no Dock icon that never answers.
            "LSUIElement": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "MIT licence",
        },
    )
