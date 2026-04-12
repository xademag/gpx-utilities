#!/usr/bin/env python3
"""
build_portable.py
=================
Creates a self-contained portable distribution of GPX Utility.

Output layout
-------------
dist/
  GPX Utility/
    python/          Python 3.11 embeddable runtime + pip + site-packages
    app/             Application source files + assets
    GPX Utility.bat  Double-click launcher (no console window)
    README.txt

  gpx-utility-portable.zip   Ready-to-distribute archive of the above

Requirements for building
-------------------------
  Python 3.x (any version)  — just to run this script
  Internet access            — one-time download of ~30 MB (cached in %TEMP%)

Usage
-----
  python build_portable.py              # full build + zip
  python build_portable.py --no-zip    # build folder only, skip zip step
"""

import os
import sys
import shutil
import zipfile
import urllib.request
import subprocess
import tempfile
import argparse

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT     = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist")
APP_DIST = os.path.join(DIST_DIR, "GPX Utility")
PY_DIR   = os.path.join(APP_DIST, "python")
APP_DIR  = os.path.join(APP_DIST, "app")
ZIP_OUT  = os.path.join(DIST_DIR, "gpx-utility-portable.zip")

# ── Embedded Python config ─────────────────────────────────────────────────────
# Python 3.11 is used because it has the broadest pythonnet 3.x compatibility.
PY_VERSION    = "3.11.9"
PY_ZIP_NAME   = f"python-{PY_VERSION}-embed-amd64.zip"
PY_ZIP_URL    = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP_NAME}"
GETPIP_URL    = "https://bootstrap.pypa.io/get-pip.py"
GETPIP_NAME   = "get-pip.py"

# Packages to install into the embedded runtime
PACKAGES = ["pythonnet", "fitparse", "cffi"]

# Application files/folders to bundle (relative to ROOT)
APP_ITEMS = ["app.py", "wpf.py", "core", "ui", "assets"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hr(char="─", width=56):
    print(char * width)

def _step(msg):
    print(f"\n  {msg}")

def _download(url, dest_path):
    """Download url to dest_path, showing a simple progress indicator."""
    name = os.path.basename(dest_path)
    print(f"    Downloading {name} ...", end="", flush=True)

    def _progress(count, block, total):
        if total > 0:
            pct = min(100, count * block * 100 // total)
            print(f"\r    Downloading {name} ... {pct}%", end="", flush=True)

    urllib.request.urlretrieve(url, dest_path, reporthook=_progress)
    size_mb = os.path.getsize(dest_path) / 1_048_576
    print(f"\r    Downloaded  {name} ({size_mb:.1f} MB)        ")


def _cached(filename):
    """Return path in %TEMP%/gpx-utility-build/<filename>, downloading if absent."""
    cache_dir = os.path.join(tempfile.gettempdir(), "gpx-utility-build")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, filename)
    return path


# ── Build steps ────────────────────────────────────────────────────────────────

def clean():
    _step("Cleaning output directory ...")
    if os.path.exists(APP_DIST):
        shutil.rmtree(APP_DIST)
    os.makedirs(APP_DIST, exist_ok=True)
    os.makedirs(DIST_DIR, exist_ok=True)
    print(f"    {APP_DIST}")


def setup_python():
    _step(f"Setting up Python {PY_VERSION} embeddable ...")
    os.makedirs(PY_DIR, exist_ok=True)

    # 1. Download (or use cached) embeddable zip
    zip_path = _cached(PY_ZIP_NAME)
    if not os.path.exists(zip_path):
        _download(PY_ZIP_URL, zip_path)
    else:
        print(f"    Using cached {PY_ZIP_NAME}")

    # 2. Extract
    print(f"    Extracting to {PY_DIR} ...", end="", flush=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(PY_DIR)
    print(" done")

    # 3. Patch the ._pth file: uncomment "import site" so pip-installed
    #    packages in Lib/site-packages are importable.
    pth_files = [f for f in os.listdir(PY_DIR) if f.endswith("._pth")]
    if not pth_files:
        raise RuntimeError("Could not find ._pth file in embeddable Python.")
    for pth in pth_files:
        pth_path = os.path.join(PY_DIR, pth)
        text = open(pth_path, encoding="utf-8").read()
        patched = text.replace("#import site", "import site")
        open(pth_path, "w", encoding="utf-8").write(patched)
        print(f"    Patched {pth} (enabled site-packages)")

    # 4. Bootstrap pip
    pip_script = _cached(GETPIP_NAME)
    if not os.path.exists(pip_script):
        _download(GETPIP_URL, pip_script)
    python_exe = os.path.join(PY_DIR, "python.exe")
    print("    Installing pip ...", end="", flush=True)
    subprocess.check_call(
        [python_exe, pip_script, "--no-warn-script-location", "--quiet"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(" done")

    # 5. Install required packages
    for pkg in PACKAGES:
        print(f"    Installing {pkg} ...", end="", flush=True)
        subprocess.check_call(
            [python_exe, "-m", "pip", "install", pkg,
             "--no-warn-script-location", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(" done")


def copy_app():
    _step("Copying application files ...")
    os.makedirs(APP_DIR, exist_ok=True)

    for item in APP_ITEMS:
        src = os.path.join(ROOT, item)
        dst = os.path.join(APP_DIR, item)
        if os.path.isdir(src):
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
            )
            print(f"    {item}/")
        elif os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"    {item}")
        else:
            print(f"    WARNING: {item} not found, skipping")

    # Copy default settings (reset personal map location to Paris/zoom 5)
    default_settings = '{"font_size": "medium", "map_lat": 48.8566, "map_lon": 2.3522, "map_zoom": 5}'
    settings_dst = os.path.join(APP_DIR, "settings.json")
    open(settings_dst, "w").write(default_settings)
    print("    settings.json (defaults)")


def create_launcher():
    _step("Creating launcher ...")

    # GPX Utility.bat — uses pythonw.exe (no console window).
    # "start /b" launches detached so the batch window exits immediately.
    bat_path = os.path.join(APP_DIST, "GPX Utility.bat")
    bat_content = (
        "@echo off\r\n"
        "cd /d \"%~dp0app\"\r\n"
        "start \"GPX Utility\" /b \"%~dp0python\\pythonw.exe\" app.py\r\n"
        "exit\r\n"
    )
    with open(bat_path, "w") as f:
        f.write(bat_content)
    print(f"    GPX Utility.bat")

    # run.vbs — alternative launcher via WScript (truly silent, no flash)
    vbs_path = os.path.join(APP_DIST, "run.vbs")
    vbs_content = (
        'Dim base\r\n'
        'base = Left(WScript.ScriptFullName, Len(WScript.ScriptFullName) - Len(WScript.ScriptName))\r\n'
        'Dim sh : Set sh = CreateObject("WScript.Shell")\r\n'
        'sh.CurrentDirectory = base & "app"\r\n'
        'sh.Run Chr(34) & base & "python\\pythonw.exe" & Chr(34) & " app.py", 0, False\r\n'
    )
    with open(vbs_path, "w") as f:
        f.write(vbs_content)
    print(f"    run.vbs  (silent launcher, recommended for desktop shortcuts)")


def create_readme():
    _step("Writing README.txt ...")
    txt_path = os.path.join(APP_DIST, "README.txt")
    content = (
        "GPX Utility — Portable Edition\r\n"
        "================================\r\n\r\n"
        "SYSTEM REQUIREMENTS\r\n"
        "  - Windows 10 or 11 (64-bit)\r\n"
        "  - .NET Framework 4.8\r\n"
        "    (included with Windows 10 v1903+ and all versions of Windows 11)\r\n"
        "  - Internet access for map tiles (OpenStreetMap)\r\n\r\n"
        "RUNNING THE APPLICATION\r\n"
        "  Option 1 (recommended): double-click  run.vbs\r\n"
        "  Option 2:               double-click  GPX Utility.bat\r\n\r\n"
        "DESKTOP SHORTCUT\r\n"
        "  Right-click run.vbs -> Send to -> Desktop (create shortcut)\r\n\r\n"
        "SETTINGS\r\n"
        "  Saved in app\\settings.json (font size, default map view).\r\n"
        "  This file is created / updated automatically while the app runs.\r\n\r\n"
        "PORTABLE\r\n"
        "  No installation required. Move or copy the entire folder anywhere;\r\n"
        "  settings travel with it.\r\n"
    )
    with open(txt_path, "w") as f:
        f.write(content)
    print(f"    README.txt")


def create_zip():
    _step(f"Creating archive {os.path.basename(ZIP_OUT)} ...")
    if os.path.exists(ZIP_OUT):
        os.remove(ZIP_OUT)

    file_count = 0
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for dirpath, _dirs, files in os.walk(APP_DIST):
            for fname in files:
                fpath   = os.path.join(dirpath, fname)
                arcname = os.path.join("GPX Utility", os.path.relpath(fpath, APP_DIST))
                zf.write(fpath, arcname)
                file_count += 1

    size_mb = os.path.getsize(ZIP_OUT) / 1_048_576
    print(f"    {ZIP_OUT}")
    print(f"    {file_count} files  |  {size_mb:.1f} MB")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build GPX Utility portable package")
    parser.add_argument("--no-zip", action="store_true", help="Skip the final zip step")
    args = parser.parse_args()

    _hr("=")
    print("  GPX Utility — Portable Build")
    print(f"  Python {PY_VERSION} embeddable  |  packages: {', '.join(PACKAGES)}")
    _hr("=")

    clean()
    setup_python()
    copy_app()
    create_launcher()
    create_readme()

    if not args.no_zip:
        create_zip()

    _hr()
    print("\n  Build complete.\n")
    print(f"  Folder : {APP_DIST}")
    if not args.no_zip:
        print(f"  Archive: {ZIP_OUT}")
    _hr()


if __name__ == "__main__":
    main()
