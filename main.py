
"""Playstation1ToolchainInstaller-Windows

Single-file Windows installer/updater/uninstaller for PSn00bSDK.

Features:
- Installs the latest PSn00bSDK Windows ZIP from GitHub Releases
- Can install a specific release tag
- Can show a dynamic CLI list of releases to pick from
- Extracts into C:\\Playstation1Toolchain\\<version-folder>
- Copies the project template from:
    <version-folder>\\share\\psn00bsdk\\template
  into:
    C:\\Playstation1Toolchain\\Projects\\template
- Creates C:\\Playstation1Toolchain\\Projects if it does not exist
- Sets user environment variable PSN00BSDK_LIBS to the current install
- Appends the PSn00bSDK bin directory to the user PATH
- Can install missing build dependencies:
    * MSYS2 + mingw-w64 GCC + Ninja  (via MSYS2/pacman)
    * CMake for Windows               (official MSI from cmake.org / GitHub)
      Downloads cmake-<ver>-windows-x86_64.msi and runs it silently.
      Automatically adds C:\\Program Files\\CMake\\bin to the user PATH
      regardless of the installer checkbox state.
      Defaults to the latest stable release; override with --cmake-version.
- Uninstall only removes the selected PSn00bSDK version folder, not Projects
- Opens File Explorer to C:\\Playstation1Toolchain after a successful install

Build example:
    pyinstaller --onefile --console main.py

Usage examples:
    main.py list
    main.py install
    main.py install --pick
    main.py install --version v0.24
    main.py install --deps
    main.py install --deps --cmake-version 4.2.5
    main.py update
    main.py update --version v0.24
    main.py update --pick
    main.py deps
    main.py deps --cmake-version 4.2.5
    main.py uninstall
    main.py uninstall --pick
    main.py uninstall --version v0.24
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ROOT            = Path(r"C:\Playstation1Toolchain")
PROJECTS_ROOT       = APP_ROOT / "Projects"
GITHUB_RELEASES_API = "https://api.github.com/repos/Lameguy64/PSn00bSDK/releases"
CMAKE_GITHUB_API    = "https://api.github.com/repos/Kitware/CMake/releases/latest"
CMAKE_MSI_TEMPLATE  = "https://github.com/Kitware/CMake/releases/download/v{ver}/cmake-{ver}-windows-x86_64.msi"
CMAKE_BIN_DIR       = Path(r"C:\Program Files\CMake\bin")
DOWNLOAD_TIMEOUT    = 90
USER_ENV_SUBKEY     = r"Environment"
MSYS2_ROOT          = Path(r"C:\msys64")
MSYS2_BASH          = MSYS2_ROOT / "usr" / "bin" / "bash.exe"
PSN00BSDK_VERSION_RE = re.compile(r"^PSn00bSDK-.*-win32$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ReleaseInfo:
    tag_name:       str
    name:           str
    published_at:   str
    zip_asset_name: str
    zip_asset_url:  str


@dataclass
class InstalledInfo:
    version_dir: Path
    libs_path:   Path
    bin_path:    Path


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"[+] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}")


def error(msg: str) -> None:
    print(f"[x] {msg}")


def die(msg: str, code: int = 1) -> None:
    error(msg)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# OS / command helpers
# ---------------------------------------------------------------------------

def ensure_windows() -> None:
    if os.name != "nt":
        die("This installer is intended for Windows only.")


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def broadcast_environment_change() -> None:
    HWND_BROADCAST  = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0,
            "Environment", SMTO_ABORTIFHUNG, 5000, None,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Registry / environment helpers
# ---------------------------------------------------------------------------

def read_user_env(name: str) -> Optional[str]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_ENV_SUBKEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def set_user_env(name: str, value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, USER_ENV_SUBKEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)


def delete_user_env(name: str) -> None:
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, USER_ENV_SUBKEY) as key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def append_user_path(path_to_add: str) -> None:
    current = read_user_env("PATH") or ""
    parts   = [p.strip() for p in current.split(";") if p.strip()]
    lowered = {p.lower() for p in parts}
    if path_to_add.lower() not in lowered:
        parts.append(path_to_add)
        set_user_env("PATH", ";".join(parts))


def remove_from_user_path(path_to_remove: str) -> None:
    current   = read_user_env("PATH") or ""
    parts     = [p.strip() for p in current.split(";") if p.strip()]
    new_parts = [p for p in parts if p.lower() != path_to_remove.lower()]
    if new_parts != parts:
        set_user_env("PATH", ";".join(new_parts))


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def safe_remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def on_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            raise

    shutil.rmtree(path, onerror=on_error)


def download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Playstation1ToolchainInstaller/1.3"})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response, dest.open("wb") as f:
            shutil.copyfileobj(response, f)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP error while downloading {url}: {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error while downloading {url}: {e}") from e


def extract_zip(zip_path: Path, target_dir: Path) -> Path:
    """Extract *zip_path* into *target_dir* and return the single top-level folder."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        top_levels: list[str] = []
        for name in zf.namelist():
            if not name or name.endswith("/"):
                continue
            top = Path(name).parts[0]
            if top not in top_levels:
                top_levels.append(top)
        if len(top_levels) != 1:
            raise ValueError(
                f"Expected a single top-level folder inside the ZIP, found: {top_levels}"
            )
        zf.extractall(target_dir)
    extracted = target_dir / top_levels[0]
    if not extracted.exists():
        raise FileNotFoundError(f"Extracted folder not found: {extracted}")
    return extracted


# ---------------------------------------------------------------------------
# Release discovery / selection
# ---------------------------------------------------------------------------

def fetch_releases() -> list[ReleaseInfo]:
    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={"User-Agent": "Playstation1ToolchainInstaller/1.2"},
    )
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Unable to query GitHub releases: {e}") from e

    releases: list[ReleaseInfo] = []
    for item in payload:
        tag_name     = str(item.get("tag_name", "")).strip()
        name         = str(item.get("name", tag_name)).strip() or tag_name
        published_at = str(item.get("published_at", "")).strip()
        assets       = item.get("assets", []) or []

        chosen:   Optional[tuple[str, str]] = None
        fallback: Optional[tuple[str, str]] = None
        for asset in assets:
            asset_name = str(asset.get("name", "")).strip()
            asset_url  = str(asset.get("browser_download_url", "")).strip()
            if not asset_name or not asset_url:
                continue
            if asset_name.lower().endswith(".zip"):
                if "psn00bsdk" in asset_name.lower() and "win32" in asset_name.lower():
                    chosen = (asset_name, asset_url)
                    break
                if fallback is None:
                    fallback = (asset_name, asset_url)

        pick = chosen or fallback
        if not pick:
            continue
        releases.append(
            ReleaseInfo(
                tag_name=tag_name,
                name=name,
                published_at=published_at,
                zip_asset_name=pick[0],
                zip_asset_url=pick[1],
            )
        )
    return releases


def find_release(releases: list[ReleaseInfo], query: str) -> Optional[ReleaseInfo]:
    q = query.strip().lower()
    for rel in releases:
        if rel.tag_name.lower() == q or rel.name.lower() == q:
            return rel
    for rel in releases:
        if (
            q in rel.tag_name.lower()
            or q in rel.name.lower()
            or q in rel.zip_asset_name.lower()
        ):
            return rel
    return None


def select_release_cli(
    releases: list[ReleaseInfo],
    title: str = "Select a PSn00bSDK release",
) -> ReleaseInfo:
    if not releases:
        die("No releases were found on GitHub.")

    print(f"\n{title}")
    print("-" * len(title))
    for idx, rel in enumerate(releases, start=1):
        date_part = rel.published_at[:10] if rel.published_at else "unknown-date"
        print(f"  {idx:>2}. {rel.tag_name:<16} {date_part}  {rel.zip_asset_name}")
    print()

    while True:
        raw = input(f"Choose 1-{len(releases)} (Enter to cancel): ").strip()
        if not raw:
            raise SystemExit(0)
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(releases):
                return releases[n - 1]
        print("  Invalid selection, please try again.")


# ---------------------------------------------------------------------------
# Installed-version discovery
# ---------------------------------------------------------------------------

def list_installed_versions() -> list[Path]:
    if not APP_ROOT.exists():
        return []
    versions = [
        p for p in APP_ROOT.iterdir()
        if p.is_dir() and PSN00BSDK_VERSION_RE.match(p.name)
    ]
    versions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return versions


def current_installed_info() -> Optional[InstalledInfo]:
    versions = list_installed_versions()
    if not versions:
        return None
    version_dir = versions[0]
    return InstalledInfo(
        version_dir=version_dir,
        libs_path=version_dir / "lib" / "libpsn00b",
        bin_path=version_dir / "bin",
    )


def refresh_environment_from_install() -> None:
    state = current_installed_info()
    if not state:
        delete_user_env("PSN00BSDK_LIBS")
        broadcast_environment_change()
        return
    set_user_env("PSN00BSDK_LIBS", str(state.libs_path))
    append_user_path(str(state.bin_path))
    broadcast_environment_change()


# ---------------------------------------------------------------------------
# Dependency installation
# ---------------------------------------------------------------------------

def has_gcc()    -> bool: return which("gcc")    is not None
def has_cmake()  -> bool: return which("cmake")  is not None or CMAKE_BIN_DIR.exists()
def has_ninja()  -> bool: return which("ninja")  is not None
def has_winget() -> bool: return which("winget") is not None


def _deps_satisfied() -> bool:
    return has_gcc() and has_cmake() and has_ninja()


# -- CMake MSI installer ----------------------------------------------------

def fetch_cmake_latest_version() -> str:
    """Query the GitHub API and return the latest stable CMake version string (e.g. '4.3.2')."""
    req = urllib.request.Request(
        CMAKE_GITHUB_API,
        headers={"User-Agent": "Playstation1ToolchainInstaller/1.3"},
    )
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Unable to query CMake GitHub releases: {e}") from e

    tag = str(payload.get("tag_name", "")).strip().lstrip("v")
    if not tag:
        raise RuntimeError("Could not determine the latest CMake version from GitHub.")
    return tag


def _is_elevated() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> None:
    """
    Re-launch the current script with administrator privileges via UAC and
    exit the current (unelevated) process.  The elevated child carries on
    from the beginning with the same argv, so this should only be called when
    the full operation genuinely requires elevation (i.e. the CMake MSI path).
    """
    script = sys.argv[0]
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,           # hwnd
        "runas",        # verb — triggers UAC prompt
        sys.executable,
        f'"{script}" {params}',
        None,           # working directory (inherit)
        1,              # SW_NORMAL
    )
    # ShellExecuteW returns a value > 32 on success.
    if ret <= 32:
        raise RuntimeError(
            f"ShellExecuteW (runas) failed with code {ret}.  "
            "Try running this script from an Administrator command prompt."
        )
    raise SystemExit(0)


def install_cmake_msi(cmake_version: Optional[str] = None) -> None:
    """
    Download the official Windows x64 CMake MSI from cmake.org (via GitHub)
    and install it silently with administrator privileges.

    * If the current process is already elevated the MSI is run directly.
    * If not, the entire script is re-launched via UAC (ShellExecuteW/runas)
      so that msiexec can write to C:\\Program Files without getting error 1603.

    Unconditionally adds C:\\Program Files\\CMake\\bin to the user PATH
    afterwards, regardless of what the installer checkbox did.

    Parameters
    ----------
    cmake_version:
        Dotted version string such as ``'4.3.2'`` or ``'4.2.5'``.
        Accepts an optional leading ``'v'`` (stripped automatically).
        When *None* the latest stable release is resolved from GitHub.
    """
    if cmake_version is None:
        info("Resolving latest CMake version ...")
        cmake_version = fetch_cmake_latest_version()

    # Strip a leading 'v' so callers can pass either '4.3.2' or 'v4.3.2'.
    cmake_version = cmake_version.lstrip("v")

    msi_url  = CMAKE_MSI_TEMPLATE.format(ver=cmake_version)
    msi_name = f"cmake-{cmake_version}-windows-x86_64.msi"

    info(f"Downloading CMake {cmake_version} installer ...")
    info(f"  Source: {msi_url}")

    # Download to a stable path outside of a TemporaryDirectory so the file
    # still exists if we need to hand off to an elevated re-launch.
    msi_dir  = Path(tempfile.gettempdir()) / "psn00b_cmake_install"
    msi_dir.mkdir(parents=True, exist_ok=True)
    msi_path = msi_dir / msi_name
    download_file(msi_url, msi_path)

    # msiexec writing to C:\Program Files requires elevation.
    # If we are not already running as admin, re-launch the whole script via
    # UAC so the user sees one prompt rather than a confusing msiexec failure.
    if not _is_elevated():
        info("Administrator privileges are required to install CMake.")
        info("A UAC prompt will appear — please click Yes to continue.")
        _relaunch_as_admin()
        # _relaunch_as_admin() raises SystemExit(0); execution never reaches here.

    info("Running CMake MSI installer (silent, elevated) ...")
    # /qn        = no UI (quiet)
    # /norestart = suppress reboot prompts
    proc = subprocess.run(
        ["msiexec", "/i", str(msi_path), "/qn", "/norestart"],
        text=True,
    )

    # Clean up the downloaded MSI regardless of outcome.
    try:
        msi_path.unlink(missing_ok=True)
        msi_dir.rmdir()
    except Exception:
        pass

    if proc.returncode not in (0, 3010):
        # 3010 = ERROR_SUCCESS_REBOOT_REQUIRED — safe to continue.
        raise RuntimeError(
            f"CMake MSI installer exited with code {proc.returncode}.  "
            "Try running this script from an Administrator command prompt."
        )

    # Unconditionally add CMake's bin dir to the user PATH.
    # The installer's "Add to PATH" checkbox is unreliable; we handle it ourselves.
    info(f"Adding CMake to user PATH: {CMAKE_BIN_DIR}")
    append_user_path(str(CMAKE_BIN_DIR))
    broadcast_environment_change()
    info(f"CMake {cmake_version} installed successfully.")


# -- MSYS2 / GCC / Ninja installer ------------------------------------------

def run_winget_install(package_id: str) -> None:
    info(f"Installing {package_id} via winget ...")
    proc = subprocess.run(
        [
            "winget", "install",
            "--id", package_id,
            "-e", "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"winget failed for {package_id} (exit code {proc.returncode})"
        )


def ensure_msys2_toolchain(force: bool = False, cmake_version: Optional[str] = None) -> None:
    """
    Install the full build-dependency stack:

    1. MSYS2  (via winget if not already present)
    2. mingw-w64-x86_64-toolchain + ninja  (via pacman inside MSYS2)
    3. CMake for Windows  (official MSI from cmake.org)

    Parameters
    ----------
    force:
        When *True*, re-run pacman and re-install CMake even if everything
        looks satisfied already.
    cmake_version:
        Specific CMake version to install.  Defaults to the latest stable.
    """
    ensure_windows()

    # ------------------------------------------------------------------ CMake
    if force or not has_cmake():
        install_cmake_msi(cmake_version)
    else:
        info("CMake is already available — skipping CMake install.")

    # ---------------------------------------------------------------- GCC/Ninja
    if force or not (has_gcc() and has_ninja()):
        if not has_winget():
            raise RuntimeError(
                "winget was not found.  Install MSYS2 manually, or install "
                "App Installer so that winget is available."
            )

        if not MSYS2_BASH.exists():
            run_winget_install("MSYS2.MSYS2")

        if not MSYS2_BASH.exists():
            raise RuntimeError(
                f"MSYS2 bash was not found at {MSYS2_BASH} after installation."
            )

        info("Installing mingw-w64-x86_64 gcc + ninja via MSYS2 pacman ...")
        pacman_cmd = (
            "pacman -Sy --noconfirm --needed "
            "mingw-w64-x86_64-toolchain "
            "mingw-w64-x86_64-ninja"
        )
        proc = subprocess.run([str(MSYS2_BASH), "-lc", pacman_cmd], text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"MSYS2 pacman failed with exit code {proc.returncode}"
            )

        for extra in (
            MSYS2_ROOT / "mingw64" / "bin",
            MSYS2_ROOT / "usr" / "bin",
        ):
            append_user_path(str(extra))

        broadcast_environment_change()
    else:
        info("GCC and Ninja are already available — skipping MSYS2 install.")

    if not _deps_satisfied():
        warn(
            "Dependency installation finished, but one or more of "
            "gcc / cmake / ninja still could not be found.  "
            "You may need to open a new terminal for PATH changes to take effect."
        )
    else:
        info("All build dependencies (GCC, CMake, Ninja) are ready.")


# ---------------------------------------------------------------------------
# Install / update logic
# ---------------------------------------------------------------------------

def locate_template_source(version_dir: Path) -> Path:
    """
    Return the *template* directory inside the SDK share folder.
    Preferred: <version>/share/psn00bsdk/template
    Fallback:  <version>/share/psn00bsdk          (older layouts)
    """
    preferred = version_dir / "share" / "psn00bsdk" / "template"
    fallback  = version_dir / "share" / "psn00bsdk"
    if preferred.is_dir():
        return preferred
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(
        f"Template directory not found under {version_dir / 'share' / 'psn00bsdk'}"
    )


def install_release(release: ReleaseInfo, replace_existing: bool = False) -> None:
    """
    Download and install *release*.

    When *replace_existing* is True (update mode) every older version folder
    is removed after the new one is in place.
    """
    APP_ROOT.mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="psn00b_install_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        zip_path    = tmpdir_path / "psn00bsdk.zip"
        extract_dir = tmpdir_path / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # 1. Download
        # ------------------------------------------------------------------
        info(f"Downloading {release.tag_name} ({release.zip_asset_name}) ...")
        download_file(release.zip_asset_url, zip_path)

        # ------------------------------------------------------------------
        # 2. Extract
        # ------------------------------------------------------------------
        info("Extracting archive ...")
        extracted_root    = extract_zip(zip_path, extract_dir)
        target_version_dir = APP_ROOT / extracted_root.name

        if target_version_dir.exists():
            info(f"Replacing existing folder: {target_version_dir}")
            safe_remove_tree(target_version_dir)

        shutil.copytree(extracted_root, target_version_dir)
        info(f"SDK installed to: {target_version_dir}")

        # ------------------------------------------------------------------
        # 3. Copy template  (template dir itself → Projects/template)
        # ------------------------------------------------------------------
        template_source = locate_template_source(target_version_dir)
        template_dest   = PROJECTS_ROOT / "template"
        info(f"Copying project template to: {template_dest}")
        if template_dest.exists():
            safe_remove_tree(template_dest)
        shutil.copytree(template_source, template_dest)

        # ------------------------------------------------------------------
        # 4. Environment variables
        # ------------------------------------------------------------------
        libs_path = target_version_dir / "lib" / "libpsn00b"
        bin_path  = target_version_dir / "bin"

        info("Updating user environment variables ...")
        set_user_env("PSN00BSDK_LIBS", str(libs_path))
        append_user_path(str(bin_path))

        # ------------------------------------------------------------------
        # 5. Remove old version folders when updating
        # ------------------------------------------------------------------
        if replace_existing:
            for old in list_installed_versions():
                if old != target_version_dir:
                    info(f"Removing old version folder: {old}")
                    safe_remove_tree(old)

        broadcast_environment_change()

    info("Install complete.")
    print(f"\n  SDK  : {target_version_dir}")
    print(f"  Libs : {libs_path}")
    print(f"  Bin  : {bin_path}")
    print(f"  Tmpl : {template_dest}\n")

    # Open File Explorer to the toolchain root
    try:
        subprocess.Popen(["explorer", str(APP_ROOT)])
    except Exception:
        warn("Could not open File Explorer automatically.")


def resolve_release_for_install(args: argparse.Namespace) -> ReleaseInfo:
    releases = fetch_releases()
    if not releases:
        die("No GitHub releases could be found.")

    if args.pick:
        return select_release_cli(releases)

    if args.version:
        chosen = find_release(releases, args.version)
        if not chosen:
            available = ", ".join(r.tag_name for r in releases[:10])
            die(
                f"Could not find release '{args.version}'.  "
                f"Available (first 10): {available}"
            )
        return chosen

    # Default: latest release
    return releases[0]


# ---------------------------------------------------------------------------
# Uninstall logic
# ---------------------------------------------------------------------------

def prompt_yes_no_timeout(
    prompt: str,
    timeout_seconds: int = 5,
    default: bool = False,
) -> bool:
    ensure_windows()
    sys.stdout.write(f"{prompt} [y/N] ")
    sys.stdout.flush()

    try:
        import msvcrt
    except ImportError:
        reply = input().strip().lower()
        return reply in {"y", "yes"}

    start  = time.time()
    buffer = ""
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                break
            if ch == "\x08":          # backspace
                buffer = buffer[:-1]
                continue
            buffer += ch
            if len(buffer) >= 3:
                break
        if time.time() - start >= timeout_seconds:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return default
        time.sleep(0.05)

    sys.stdout.write("\n")
    sys.stdout.flush()
    reply = buffer.strip().lower()
    if not reply:
        return default
    return reply in {"y", "yes"}


def uninstall_version(version_dir: Path) -> None:
    if not version_dir.exists():
        warn(f"Folder not found: {version_dir}")
        return

    print(f"\n  Will delete : {version_dir}")
    print("  Projects folder will be kept.\n")
    ok = prompt_yes_no_timeout(
        "Are you sure you want to uninstall this PSn00bSDK version?",
        timeout_seconds=10,
        default=False,
    )
    if not ok:
        info("Uninstall cancelled.")
        return

    info(f"Removing {version_dir} ...")
    safe_remove_tree(version_dir)
    refresh_environment_from_install()
    info("Uninstall complete.")


def choose_installed_version_cli() -> Path:
    installed = list_installed_versions()
    if not installed:
        die("No installed PSn00bSDK folders were found.")

    title = "Select an installed version to uninstall"
    print(f"\n{title}")
    print("-" * len(title))
    for idx, version_dir in enumerate(installed, start=1):
        print(f"  {idx:>2}. {version_dir.name}")
    print()

    while True:
        raw = input(f"Choose 1-{len(installed)} (Enter to cancel): ").strip()
        if not raw:
            raise SystemExit(0)
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(installed):
                return installed[n - 1]
        print("  Invalid selection, please try again.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Playstation1ToolchainInstaller-Windows",
        description="Install, update, list, or uninstall PSn00bSDK on Windows.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- list ---------------------------------------------------------------
    sub.add_parser("list", help="List PSn00bSDK releases available from GitHub")

    # -- install ------------------------------------------------------------
    p_install = sub.add_parser("install", help="Install a PSn00bSDK release")
    p_install.add_argument(
        "--version", metavar="TAG",
        help="Release tag to install (e.g. v0.24); defaults to latest",
    )
    p_install.add_argument(
        "--pick", action="store_true",
        help="Choose from an interactive CLI list of GitHub releases",
    )
    p_install.add_argument(
        "--deps", action="store_true",
        help="Install GCC / CMake / Ninja before installing the SDK",
    )
    p_install.add_argument(
        "--cmake-version", metavar="VER", dest="cmake_version",
        help="CMake version to install with --deps (e.g. 4.2.5); defaults to latest",
    )

    # -- update -------------------------------------------------------------
    p_update = sub.add_parser(
        "update",
        help="Install a release and remove all older installed version folders",
    )
    p_update.add_argument(
        "--version", metavar="TAG",
        help="Release tag to update to (e.g. v0.24); defaults to latest",
    )
    p_update.add_argument(
        "--pick", action="store_true",
        help="Choose from an interactive CLI list of GitHub releases",
    )
    p_update.add_argument(
        "--deps", action="store_true",
        help="Install GCC / CMake / Ninja before updating",
    )
    p_update.add_argument(
        "--cmake-version", metavar="VER", dest="cmake_version",
        help="CMake version to install with --deps (e.g. 4.2.5); defaults to latest",
    )

    # -- deps ---------------------------------------------------------------
    p_deps = sub.add_parser(
        "deps",
        help="Install the full build-dependency stack (GCC + Ninja via MSYS2, CMake via MSI)",
    )
    p_deps.add_argument(
        "--force", action="store_true",
        help="Re-install even if gcc / cmake / ninja are already present",
    )
    p_deps.add_argument(
        "--cmake-version", metavar="VER", dest="cmake_version",
        help="CMake version to install (e.g. 4.2.5); defaults to latest stable",
    )

    # -- uninstall ----------------------------------------------------------
    p_uninstall = sub.add_parser(
        "uninstall",
        help="Remove a local PSn00bSDK version folder (Projects are kept)",
    )
    p_uninstall.add_argument(
        "--pick", action="store_true",
        help="Choose from the list of installed versions",
    )
    p_uninstall.add_argument(
        "--version", metavar="FOLDER",
        help="Exact folder name (or partial match) to uninstall",
    )

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def command_list() -> int:
    info("Fetching release list from GitHub ...")
    releases = fetch_releases()
    if not releases:
        warn("No releases found.")
        return 1
    title = "Available PSn00bSDK releases"
    print(f"\n{title}")
    print("-" * len(title))
    for idx, rel in enumerate(releases, start=1):
        date_part = rel.published_at[:10] if rel.published_at else "unknown-date"
        print(f"  {idx:>2}. {rel.tag_name:<16} {date_part}  {rel.zip_asset_name}")
    print()
    return 0


def command_install(args: argparse.Namespace, replace_existing: bool) -> int:
    if getattr(args, "deps", False):
        ensure_msys2_toolchain(cmake_version=getattr(args, "cmake_version", None))
    release = resolve_release_for_install(args)
    install_release(release, replace_existing=replace_existing)
    return 0


def command_deps(args: argparse.Namespace) -> int:
    ensure_msys2_toolchain(
        force=getattr(args, "force", False),
        cmake_version=getattr(args, "cmake_version", None),
    )
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    if args.pick:
        version_dir = choose_installed_version_cli()

    elif args.version:
        candidate = APP_ROOT / args.version
        if candidate.exists():
            version_dir = candidate
        else:
            matches = [
                p for p in list_installed_versions()
                if args.version.lower() in p.name.lower()
            ]
            if len(matches) == 1:
                version_dir = matches[0]
            elif len(matches) > 1:
                print("Multiple matching installed versions found:")
                for p in matches:
                    print(f"  - {p.name}")
                print("Use --pick or supply a more specific name.")
                return 1
            else:
                die(f"No installed version matching '{args.version}' was found.")

    else:
        installed = list_installed_versions()
        if not installed:
            die("No installed PSn00bSDK folders found.")
        if len(installed) == 1:
            version_dir = installed[0]
        else:
            version_dir = choose_installed_version_cli()

    uninstall_version(version_dir)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ensure_windows()
    parser = build_parser()
    args   = parser.parse_args(argv)

    try:
        if args.command == "list":
            return command_list()
        if args.command == "install":
            return command_install(args, replace_existing=False)
        if args.command == "update":
            return command_install(args, replace_existing=True)
        if args.command == "deps":
            return command_deps(args)
        if args.command == "uninstall":
            return command_uninstall(args)

        parser.print_help()
        return 1

    except KeyboardInterrupt:
        warn("Interrupted by user.")
        return 130
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    input("""Incorrect usage.\n
Usage examples:
    main.py list
    main.py install
    main.py install --pick
    main.py install --version v0.24
    main.py install --deps
    main.py install --deps --cmake-version 4.2.5
    main.py update
    main.py update --version v0.24
    main.py update --pick
    main.py deps
    main.py deps --cmake-version 4.2.5
    main.py uninstall
    main.py uninstall --pick
    main.py uninstall --version v0.24
Press Enter to continue...""")
raise SystemExit(main(sys.argv[1:]))