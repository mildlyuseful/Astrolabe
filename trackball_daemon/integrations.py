"""3D-app integration registry: detect installed apps, install/enable each integration,
and auto-update the bundled add-ons.

Detection is best-effort via common Windows install paths. Every registered app has a real
`setup`: the socket-add-on apps (Fusion, Blender, FreeCAD, SketchUp, Unreal, Unity, Godot,
Rhino) copy their bundled add-on into the host app's add-on directory, AutoCAD stages its
bundled NETLOAD plugin, and the daemon-side direct integrations (SolidWorks, Onshape) verify
prerequisites and enable the driver (no file copy). `auto_update` re-copies a bundled add-on only when its
bundled version is newer than the installed one (see `docs/architecture.md`), preserving the user's
enabled state.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Optional

from .app_registry import APP_SPECS_BY_ID, AppSpec


def _operational_state(cfg, app_id):
    """Detached setup-workflow state; publish it only after external setup succeeds."""
    return dict(cfg.snapshot().app_operational[app_id])


def _save_operational(cfg, app_id, values):
    cfg.set_app_operational(app_id, **values)


def _hidden_check_output(args, *, timeout=8) -> str:
    """subprocess.check_output that does not flash a console window on Windows."""
    kw = dict(stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) + hidden STARTUPINFO — needed for powershell/wmic.
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
    return subprocess.check_output(args, **kw)


@dataclass
class AppDef:
    spec: AppSpec
    needs_plugin: bool                 # True: needs an add-on; False: gesture/profile only
    detect: Callable[[], Optional[str]]
    setup: Optional[Callable] = None   # per-app installer; None => generic mark-installed
    install_model: str = ""
    setup_required: bool = True
    first_run_action: Optional[str] = "Set up"
    supported_versions: str = ""
    setup_instructions: str = ""
    manual_install: str = ""
    health_check: str = ""
    security_notes: str = ""
    security_confirmation: str = ""       # non-empty => UI confirms before setup mutates state

    @property
    def key(self):
        return self.spec.app_id

    @property
    def name(self):
        return self.spec.display_name


@dataclass(frozen=True)
class Compatibility:
    """Detected host-version result rendered by the 3D Apps panel."""
    version: Optional[str]
    status: str                        # supported | unverified | unsupported | unknown
    message: str


def _first_glob(*patterns):
    for p in patterns:
        matches = sorted(glob.glob(p), reverse=True)
        if matches:
            return matches[0]
    return None


def _pf():
    return os.environ.get("ProgramFiles", r"C:\Program Files")


def detect_blender():
    return _first_glob(os.path.join(_pf(), "Blender Foundation", "Blender*", "blender.exe"))


def detect_freecad():
    return _first_glob(os.path.join(_pf(), "FreeCAD*", "bin", "FreeCAD.exe"),
                       os.path.join(_pf(), "FreeCAD*", "FreeCAD.exe"))


def _sketchup_exe_glob():
    # Desktop SketchUp installs each annual release as
    #   C:\Program Files\SketchUp\SketchUp <year>\SketchUp\SketchUp.exe
    return os.path.join(_pf(), "SketchUp", "SketchUp *", "SketchUp", "SketchUp.exe")


def detect_sketchup():
    return _first_glob(_sketchup_exe_glob())


def _unreal_exe_glob():
    # Epic Games launcher installs each engine as
    #   C:\Program Files\Epic Games\UE_5.*\Engine\Binaries\Win64\UnrealEditor.exe
    return os.path.join(_pf(), "Epic Games", "UE_*", "Engine", "Binaries", "Win64", "UnrealEditor.exe")


def detect_unreal():
    return _first_glob(_unreal_exe_glob())


def detect_unity():
    # Unity Hub installs editors under Editor\\Unity.exe; standalone installs vary.
    return _first_glob(
        os.path.join(_pf(), "Unity", "Hub", "Editor", "*", "Editor", "Unity.exe"),
        os.path.join(_pf(), "Unity", "Editor", "Unity.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Unity", "Editor", "Unity.exe"),
    )


def detect_godot():
    # Steam / itch / manual zips: Godot_v4*.exe or Godot*.exe in Program Files or user Downloads.
    la = os.environ.get("LOCALAPPDATA", "")
    return _first_glob(
        os.path.join(_pf(), "Godot*", "Godot*.exe"),
        os.path.join(_pf(), "Godot", "Godot*.exe"),
        os.path.join(la, "Programs", "Godot*", "Godot*.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Downloads", "Godot*.exe"),
    )


def detect_rhino():
    # Rhino 8 default: C:\\Program Files\\Rhino 8\\System\\Rhino.exe
    found = _first_glob(
        os.path.join(_pf(), "Rhino 8", "System", "Rhino.exe"),
        os.path.join(_pf(), "Rhino *", "System", "Rhino.exe"),
    )
    if found:
        return found
    # Registry fallback (non-default install path).
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\McNeel\Rhinoceros\8.0\Install") as key:
            install, _ = winreg.QueryValueEx(key, "InstallPath")
            exe = Path(install) / "System" / "Rhino.exe"
            if exe.exists():
                return str(exe)
    except Exception:
        pass
    return None


def detect_fusion():
    la = os.environ.get("LOCALAPPDATA", "")
    return _first_glob(os.path.join(la, "Autodesk", "webdeploy", "production", "*", "FusionLauncher.exe"),
                       os.path.join(la, "Autodesk", "webdeploy", "production", "*", "Fusion360.exe"))


def detect_solidworks():
    return _first_glob(os.path.join(_pf(), "SOLIDWORKS Corp", "SOLIDWORKS", "SLDWORKS.exe"),
                       os.path.join(_pf(), "SOLIDWORKS*", "SOLIDWORKS", "SLDWORKS.exe"))


def detect_autocad():
    # Autodesk installs each release as C:\Program Files\Autodesk\AutoCAD <year>\acad.exe. AutoCAD
    # verticals (Civil 3D / Architecture / Mechanical) are also acad.exe and expose the same
    # AutoCAD.Application COM object, so the driver drives them too.
    return _first_glob(os.path.join(_pf(), "Autodesk", "AutoCAD*", "acad.exe"))


def detect_onshape():
    return "browser-based (no local install)"


# --- host-version compatibility ---------------------------------------------------
def _match_version(path, pattern):
    match = re.search(pattern, str(path or ""), re.IGNORECASE)
    return match.group(1) if match else None


@lru_cache(maxsize=16)
def _exe_product_version(path):
    """Best-effort Windows ProductVersion. Cached because UI refreshes must stay cheap."""
    if sys.platform != "win32" or not path or not Path(path).exists():
        return None
    try:
        literal = str(path).replace("'", "''")
        value = _hidden_check_output([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"(Get-Item -LiteralPath '{literal}').VersionInfo.ProductVersion",
        ]).strip()
        return value or None
    except Exception:
        return None


def detected_host_version(appdef: AppDef, detected=None):
    """Return a normalized host version from the detected executable/path.

    Path parsing covers the normal install layouts without launching a host. SolidWorks and the
    rolling Fusion installer need the executable ProductVersion instead. None means detection
    succeeded but the version could not be established, which is surfaced as unverified.
    """
    detected = appdef.detect() if detected is None else detected
    if not detected:
        return None
    key = appdef.key
    if key == "blender":
        return _match_version(detected, r"Blender\s+(\d+(?:\.\d+)?)")
    if key == "freecad":
        return _match_version(detected, r"FreeCAD\s+(\d+(?:\.\d+)?)")
    if key == "sketchup":
        return _match_version(detected, r"SketchUp\s+(\d{4})")
    if key == "unreal":
        return _match_version(detected, r"UE_(\d+(?:\.\d+)?)")
    if key == "unity":
        return _match_version(detected, r"Editor[\\/](\d+(?:\.\d+)+(?:[abfp]\d+)?)")
    if key == "godot":
        return _match_version(Path(str(detected)).name, r"Godot[_-]?v?(\d+(?:\.\d+)+)")
    if key == "rhino":
        return _match_version(detected, r"Rhino\s+(\d+)")
    if key == "autocad":
        return _match_version(detected, r"AutoCAD\s+(\d{4})")
    if key == "solidworks":
        raw = _exe_product_version(str(detected))
        try:
            # SOLIDWORKS major 33 == 2025, 32 == 2024, etc.
            return str(int(str(raw).split(".", 1)[0]) + 1992)
        except (TypeError, ValueError):
            return None
    if key == "fusion360":
        return _exe_product_version(str(detected))
    if key == "onshape":
        return "current web release"
    return None


def _version_numbers(version):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", str(version)))
    except (TypeError, ValueError):
        return ()


def compatibility(appdef: AppDef, detected=None) -> Compatibility:
    """Classify the detected host against the deliberately conservative supported list."""
    detected = appdef.detect() if detected is None else detected
    if not detected:
        return Compatibility(None, "unknown", "not detected")
    version = detected_host_version(appdef, detected)
    if appdef.key in ("onshape", "fusion360"):
        label = version or "rolling release"
        return Compatibility(label, "supported", f"{label} (rolling release)")
    if not version:
        return Compatibility(None, "unverified", "version could not be detected")

    nums = _version_numbers(version)
    major = nums[0] if nums else -1
    minor = nums[1] if len(nums) > 1 else 0
    key = appdef.key
    supported = False
    known_incompatible = False
    if key == "blender":
        supported = (major == 4 and minor >= 2) or (major == 5 and minor <= 1)
        known_incompatible = major < 4
    elif key == "freecad":
        supported = major == 1 and 0 <= minor <= 1
    elif key == "sketchup":
        supported = 2025 <= major <= 2026
    elif key == "unreal":
        supported = (major, minor) == (5, 8)
        known_incompatible = major != 5
    elif key == "unity":
        supported = major == 6000
    elif key == "godot":
        supported = major == 4 and 4 <= minor <= 7
        known_incompatible = major != 4
    elif key == "rhino":
        supported = major == 8
        known_incompatible = major < 8
    elif key == "solidworks":
        supported = major == 2025
    elif key == "autocad":
        supported = 2025 <= major <= 2027
        known_incompatible = major < 2025

    if supported:
        return Compatibility(version, "supported", f"version {version} is supported")
    if known_incompatible:
        return Compatibility(version, "unsupported", f"version {version} is unsupported")
    return Compatibility(version, "unverified", f"version {version} has not been verified")


def integration_instructions(appdef: AppDef) -> str:
    """Complete, copyable instructions used by every expandable app card."""
    return (
        f"Install model\n{appdef.install_model}\n\n"
        f"Automatic setup\n{appdef.setup_instructions}\n\n"
        f"Manual setup / restricted permissions\n{appdef.manual_install}\n\n"
        f"Security and permissions\n{appdef.security_notes}\n\n"
        f"Health check\n{appdef.health_check}"
    )


# --- Fusion 360 add-in install --------------------------------------------------------
def fusion_addins_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns"


def _bundled_addin(*parts) -> Path:
    return Path(__file__).resolve().parent.joinpath("plugins", *parts)


def install_fusion(appdef: AppDef, cfg) -> tuple[bool, str]:
    if not detect_fusion():
        return False, "Fusion 360 was not found on this machine — install it first."
    src = _bundled_addin("fusion360", "TrackballNav")
    if not src.exists():
        return False, "Bundled Fusion add-in is missing from this build."
    dest = fusion_addins_dir() / "TrackballNav"
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)   # overwrite == update
    except OSError as exc:
        return False, f"Could not copy the add-in: {exc}"
    a["installed"] = True
    if not was_installed:                                # don't re-enable on an update
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "copied"
    return True, (f"Add-in {verb} in Fusion's AddIns folder (v{a['addin_version']}).\n\n"
                  "In Fusion: Utilities → Add-Ins (Shift+S) → select \"TrackballNav\" → Run, and "
                  "tick \"Run on Startup\". Restart Fusion (or stop/run the add-in) to apply.")


# --- SolidWorks: no-file COM integration ----------------------------------------------
def setup_solidworks(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the SolidWorks integration. Unlike Fusion there is NOTHING to copy or register:
    SolidWorks is driven live over its COM automation API (pywin32) by the daemon-side driver,
    not by a registered add-in. So we only verify SolidWorks is installed and pywin32 is
    importable, then mark the app enabled/installed in config."""
    if not detect_solidworks():
        return False, "SolidWorks was not found on this machine — install it first."
    try:
        import win32com.client  # noqa: F401  (presence check only)
    except Exception:
        return False, ("pywin32 is required to drive SolidWorks over COM, but it isn't "
                       "installed.\nInstall it with:  pip install pywin32")
    a = _operational_state(cfg, appdef.key)
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = ""                       # no add-in for SolidWorks (driven via COM)
    _save_operational(cfg, appdef.key, a)
    return True, ("SolidWorks integration enabled — it's driven directly via COM, so there's "
                  "nothing to install.\n\nOpen SolidWorks with a part or assembly, switch the "
                  "daemon to 3D mode, and focus SolidWorks. The row flips to \"connected\" once "
                  "the daemon attaches to the running instance.")


# --- AutoCAD: bundled NETLOAD plugin (sole transport) + COM plugin loader --------------------
def _acad_runtime_plugin_dir():
    """Where the plugin runs from (%APPDATA%\\TrackballDaemon\\acad_plugin) -- single source of
    truth lives next to the NETLOAD logic in autocad_driver."""
    from . import autocad_driver
    return autocad_driver._runtime_plugin_dir()


def _is_locked_acad_plugin(exc: OSError) -> bool:
    """Return whether Windows reported an in-use file, rather than a generic write failure."""
    return getattr(exc, "winerror", None) in (32, 33)  # sharing / lock violation


def _copy_acad_plugin() -> tuple[str, str]:
    """Copy the AutoCAD runtime payload and distinguish copied, staged, and failed outcomes.

    ``staged`` requires an existing runtime DLL plus a Windows sharing/lock violation. The loader
    can retry from the bundled copy next session. Missing bundles and other write failures are
    honest errors, not staged updates.
    """
    src = _bundled_addin("autocad", "TrackballNavAcad.dll")
    if not src.exists():
        return "error", "Bundled AutoCAD plugin is missing from this build."
    dst_dir = _acad_runtime_plugin_dir()
    dst = dst_dir / src.name
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        if dst.exists() and _is_locked_acad_plugin(exc):
            return "staged", ("The existing runtime plugin could not be replaced while AutoCAD "
                              f"may be using it ({exc}).")
        return "error", f"Could not copy the AutoCAD plugin into {dst_dir}: {exc}"
    ver = _bundled_addin("autocad", "version.json")
    if ver.exists():
        try:
            shutil.copy2(ver, dst_dir / "version.json")
        except OSError as exc:
            return "copied", f"Plugin copied, but its version manifest could not be updated: {exc}"
    return "copied", ""


def install_autocad(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the AutoCAD integration and stage its bundled smooth-orbit plugin.

    AutoCAD's navigation comes from a compiled NETLOAD plugin (`TrackballNavAcad.dll`,
    plugin_src/autocad) -- the SOLE AutoCAD transport (the COM nav transport is archived at
    archive/autocad_com_transport/). The daemon's plugin loader auto-NETLOADs it into a running
    AutoCAD. Install/Update here copies the bundled DLL + version.json into the runtime dir.
    AutoCAD locks the loaded DLL for its whole session, so with AutoCAD running an update is only
    STAGED: the daemon retries the copy and NETLOADs it automatically the next time AutoCAD starts.
    COM is used only to attach to AutoCAD and issue that NETLOAD."""
    if not detect_autocad():
        return False, "AutoCAD was not found on this machine — install it first."
    try:
        import win32com.client  # noqa: F401  (presence check only)
    except Exception:
        return False, ("pywin32 is required to NETLOAD the AutoCAD plugin (COM delivery), but it "
                       "isn't installed.\nInstall it with:  pip install pywin32")
    ver = bundled_addin_version("autocad") or "?"
    copy_status, copy_detail = _copy_acad_plugin()
    if copy_status == "error":
        return False, copy_detail
    a = _operational_state(cfg, appdef.key)
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = (ver if copy_status == "copied"
                            else (installed_addin_version("autocad") or ""))
    _save_operational(cfg, appdef.key, a)
    if copy_status == "copied":
        warning = f"\n\nNote: {copy_detail}" if copy_detail else ""
        return True, (f"AutoCAD plugin v{ver} installed.{warning}\n\nNothing to do inside AutoCAD: the "
                      "daemon NETLOADs it automatically when it attaches to a running AutoCAD "
                      "(if AutoCAD is open right now with an older plugin loaded, restart it to "
                      "pick this version up). Type TBNAV in AutoCAD to check the plugin status.")
    return True, (f"AutoCAD plugin update to v{ver} is STAGED — an existing runtime copy could not "
                  f"be replaced. {copy_detail}\n\nClose AutoCAD; the daemon retries the bundled "
                  "copy and loads it automatically the next time AutoCAD starts.")


# --- Onshape: browser bridge (impersonate the 3Dconnexion local NL-Proxy) -------------------
def _onshape_cert_paths(cfg):
    """Cert/key paths for the Onshape bridge: the config override if set, else the driver default
    (onshape_cert.pem / onshape_key.pem in the per-user config dir)."""
    from . import onshape_bridge
    o = cfg.snapshot().onshape
    d_cert, d_key = onshape_bridge.default_cert_paths()
    return (o.get("cert_path") or d_cert, o.get("key_path") or d_key)


def setup_onshape(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the Onshape integration. Like SolidWorks there is no add-in to install: Onshape is
    driven over the browser's native 3Dconnexion support by a daemon-side bridge that impersonates
    the local NL-Proxy service. Setup (a) generates a self-signed TLS cert for 127.51.68.120 (safe
    — only writes files in our own config dir; it does NOT touch any trust store), then (b) returns
    the exact, user-run steps to trust the cert + enable Onshape's SpaceMouse option. We never
    modify the system trust store automatically."""
    from . import onshape_bridge
    cert_path, key_path = _onshape_cert_paths(cfg)
    if not onshape_bridge.ensure_cert(cert_path, key_path):
        return False, ("Could not generate the TLS certificate the Onshape bridge needs.\n"
                       "Install the Python 'cryptography' package (pip install cryptography) or "
                       "make sure 'openssl' is on PATH, then try again.")
    a = _operational_state(cfg, appdef.key)
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = ""                       # no add-in for Onshape (browser bridge)
    _save_operational(cfg, appdef.key, a)
    return True, (
        "Onshape integration enabled — it's driven through the browser's built-in 3Dconnexion "
        "support, so there's no add-in to install.\n\n"
        "One-time steps:\n"
        "1) Trust the local certificate so Chrome/Edge/Firefox will connect. Recommended (no admin) "
        "— run in a terminal:\n"
        "      certutil -user -addstore Root \"%s\"\n"
        "   Click 'Yes' on the Windows prompt. (To undo later: certutil -user -delstore Root "
        "127.51.68.120.) Alternatively, just browse to https://127.51.68.120:8181 once and accept "
        "the warning.\n"
        "2) In Onshape, enable the SpaceMouse / 3Dconnexion option (Account → Preferences, or the "
        "view settings).\n"
        "3) For under-cursor orbit (Orbit pivot = cursor), install the userscript with "
        "\"Copy userscript\" below (also available under Per-App Bindings → Onshape).\n\n"
        "Then open an Onshape document, switch the daemon to 3D mode, and focus the Onshape tab — "
        "the row flips to \"connected\" once Onshape's 3D mouse client connects."
        % cert_path)


# --- Blender add-on install (legacy bl_info add-on -> scripts/addons, + auto-enable startup shim) --
def _blender_appdata_root() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"


def _all_blender_exes() -> list:
    return sorted(glob.glob(os.path.join(_pf(), "Blender Foundation", "Blender*", "blender.exe")))


def _blender_exe_version(exe_path: str):
    """The major.minor version Blender uses for its user-scripts folder, parsed from the install
    folder name ("...\\Blender Foundation\\Blender 5.1\\blender.exe" -> "5.1")."""
    m = re.search(r"(\d+\.\d+)", os.path.basename(os.path.dirname(exe_path)))
    return m.group(1) if m else None


def _blender_version_dirs() -> list:
    """Per-version user `scripts` dirs to install into: every existing %APPDATA%\\Blender Foundation\\
    Blender\\<ver>\\scripts, PLUS the <maj.min> parsed from each detected blender.exe (covers a
    freshly-installed, never-launched Blender). Blender scans scripts/addons regardless of who
    created the folder."""
    roots = set()
    base = _blender_appdata_root()
    if base.exists():
        for child in base.iterdir():
            if child.is_dir() and child.name[:1].isdigit():     # version folders: "5.1", "4.2", ...
                roots.add(child / "scripts")
    for exe in _all_blender_exes():
        ver = _blender_exe_version(exe)
        if ver:
            roots.add(base / ver / "scripts")
    return sorted(roots)


def blender_addon_dirs() -> list:
    return [scripts / "addons" / "trackball_nav" for scripts in _blender_version_dirs()]


def _blender_primary_addon_dir() -> Path:
    """The add-on dir used for version reading (newest detected version). A non-existent fallback
    when no Blender is detected => installed_addin_version() reports None (UI shows 'Set up')."""
    dirs = blender_addon_dirs()
    if dirs:
        return dirs[-1]
    return _blender_appdata_root() / "0.0" / "scripts" / "addons" / "trackball_nav"


def install_blender(appdef: "AppDef", cfg, install_startup=None) -> tuple[bool, str]:
    """Copy the Blender add-on into every detected Blender version's scripts/addons, and (with
    consent) drop a scripts/startup shim that auto-enables it on launch -- the analogue of Fusion's
    "Run on Startup". Overwriting an existing copy == update.

    install_startup: True  -> write the auto-enable startup shim (user consented)
                     False -> don't write it (user enables the add-on by hand in Preferences)
                     None  -> refresh the shim only where it already exists (used by auto_update,
                              so updates keep a previously-opted-in shim current without newly
                              opting the user in)
    """
    if not detect_blender():
        return False, "Blender was not found on this machine — install it first."
    src = _bundled_addin("blender", "trackball_nav")
    shim = _bundled_addin("blender", "startup", "trackball_nav_startup.py")
    if not src.exists():
        return False, "Bundled Blender add-on is missing from this build."
    scripts_dirs = _blender_version_dirs()
    if not scripts_dirs:
        return False, "Could not determine Blender's user scripts folder."
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    installed_to, started_to = [], []
    for scripts in scripts_dirs:
        ver = scripts.parent.name
        try:
            addon_dest = scripts / "addons" / "trackball_nav"
            addon_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, addon_dest, dirs_exist_ok=True)        # overwrite == update
            installed_to.append(ver)
            shim_dest = scripts / "startup" / "trackball_nav_startup.py"
            want_shim = install_startup is True or (install_startup is None and shim_dest.exists())
            if want_shim and shim.exists():
                shim_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shim, shim_dest)
                started_to.append(ver)
        except OSError as exc:
            return False, f"Could not copy the Blender add-on (version {ver}): {exc}"
    a["installed"] = True
    if not was_installed:                                # don't re-enable on an update
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    vers = ", ".join(installed_to) or "—"
    if started_to:
        tail = ("It auto-enables on Blender's next launch (a startup script was written to "
                "scripts/startup/trackball_nav_startup.py; delete it there to opt out).")
    else:
        tail = ("Enable it once in Blender: Preferences → Add-ons → search \"Trackball\" → tick it.\n"
                "(Re-run Set up and choose auto-start to enable it on every launch.)")
    return True, (f"Blender add-on {verb} (v{a['addin_version']}) for Blender {vers}.\n\n{tail}\n\n"
                  "Then open a 3D viewport, switch the daemon to 3D mode, and focus Blender — the "
                  "row flips to \"connected\" once the add-on attaches.")


# --- FreeCAD add-on install (copy into FreeCAD's user Mod dir; InitGui.py auto-runs it) ------
def _freecad_install_version(exe):
    """(major, minor) parsed from a detected FreeCAD exe path (e.g. "...\\FreeCAD 1.1\\bin\\
    FreeCAD.exe" -> (1, 1)), or None."""
    if not exe:
        return None
    m = re.search(r"FreeCAD[ _-]?(\d+)\.(\d+)", exe.replace("/", "\\"))
    return (int(m.group(1)), int(m.group(2))) if m else None


def freecad_user_mod_dir() -> Path:
    """The TrackballNav path inside FreeCAD's user Mod dir (where add-ons auto-load from).
    FreeCAD >= 1.0 uses a VERSIONED user dir (%APPDATA%\\FreeCAD\\vMAJ-MIN\\Mod -- verified live on
    1.1 via App.getUserAppDataDir()); <= 0.21 used the flat %APPDATA%\\FreeCAD\\Mod. Resolve the
    newest versioned dir (existing on disk, or derived from the detected install), else flat."""
    root = Path(os.environ.get("APPDATA", "")) / "FreeCAD"
    candidates = {}                                    # (maj, min) -> versioned base Path
    if root.exists():
        for child in root.iterdir():
            m = re.fullmatch(r"v(\d+)-(\d+)", child.name)
            if child.is_dir() and m:
                candidates[(int(m.group(1)), int(m.group(2)))] = child
    ver = _freecad_install_version(detect_freecad())
    if ver is not None and ver >= (1, 0):
        candidates[ver] = root / ("v%d-%d" % ver)      # versioned layout (FreeCAD >= 1.0)
    if candidates:
        return candidates[max(candidates)] / "Mod" / "TrackballNav"
    return root / "Mod" / "TrackballNav"               # legacy flat layout (<= 0.21) / best-effort


def install_freecad(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy the bundled add-on into FreeCAD's user Mod dir. FreeCAD runs every Mod/<name>/
    InitGui.py at GUI startup, so it auto-starts with NO user activation (unlike Blender, which
    needs an enable shim). Overwriting an existing copy == update. Modelled on install_blender."""
    if not detect_freecad():
        return False, "FreeCAD was not found on this machine — install it first."
    src = _bundled_addin("freecad", "TrackballNav")
    if not src.exists():
        return False, "Bundled FreeCAD add-on is missing from this build."
    dest = freecad_user_mod_dir()
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)   # overwrite == update
    except OSError as exc:
        return False, f"Could not copy the FreeCAD add-on: {exc}"
    a["installed"] = True
    if not was_installed:                                # don't re-enable on an update
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    return True, (f"FreeCAD add-on {verb} (v{a['addin_version']}) in FreeCAD's user Mod folder:\n"
                  f"{dest}\n\n"
                  "It auto-starts on FreeCAD's next launch — nothing to enable by hand. Restart "
                  "FreeCAD, open a document with a 3D view, switch the daemon to 3D mode, and focus "
                  "FreeCAD — the row flips to \"connected\" once the add-on attaches.")


# --- SketchUp Ruby extension install (top-level loader + folder in every annual Plugins dir) ---
def _sketchup_appdata_root() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "SketchUp"


def _all_sketchup_exes() -> list:
    return sorted(glob.glob(_sketchup_exe_glob()))


def _sketchup_exe_year(exe_path: str):
    """Annual release folder parsed from an exe path (SketchUp 2026 -> 2026), or None."""
    m = re.search(r"SketchUp[ _-](\d{4})", str(exe_path).replace("/", "\\"), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _sketchup_plugins_dirs() -> list:
    """Every per-year user Plugins dir known from installed exes or existing APPDATA folders."""
    years = set()
    root = _sketchup_appdata_root()
    if root.exists():
        for child in root.iterdir():
            m = re.fullmatch(r"SketchUp (\d{4})", child.name, re.IGNORECASE)
            if child.is_dir() and m:
                years.add(int(m.group(1)))
    for exe in _all_sketchup_exes():
        year = _sketchup_exe_year(exe)
        if year:
            years.add(year)
    return [root / f"SketchUp {year}" / "SketchUp" / "Plugins" for year in sorted(years)]


def sketchup_addon_dirs() -> list:
    return [plugins / "trackball_nav" for plugins in _sketchup_plugins_dirs()]


def _sketchup_primary_addon_dir() -> Path:
    dirs = sketchup_addon_dirs()
    if dirs:
        return dirs[-1]
    return _sketchup_appdata_root() / "SketchUp 0000" / "SketchUp" / "Plugins" / "trackball_nav"


def install_sketchup(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy the Ruby extension into every detected annual SketchUp Desktop Plugins folder.

    SketchUp auto-loads the top-level loader at launch; it registers Trackball Nav with the
    Extension Manager and enables it on startup. Overwriting an existing copy is an update.
    """
    if not detect_sketchup():
        return False, "SketchUp Desktop was not found on this machine -- install Pro/Studio first."
    src_root = _bundled_addin("sketchup")
    src_loader = src_root / "trackball_nav_loader.rb"
    src_addon = src_root / "trackball_nav"
    if not src_loader.exists() or not src_addon.exists():
        return False, "Bundled SketchUp extension is missing from this build."
    plugin_dirs = _sketchup_plugins_dirs()
    if not plugin_dirs:
        return False, "Could not determine SketchUp's per-version Plugins folders."

    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    installed_to = []
    for plugins in plugin_dirs:
        year = plugins.parents[1].name.replace("SketchUp ", "")
        try:
            plugins.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_loader, plugins / "trackball_nav_loader.rb")
            shutil.copytree(src_addon, plugins / "trackball_nav", dirs_exist_ok=True)
            installed_to.append(year)
        except OSError as exc:
            return False, f"Could not copy the SketchUp extension (version {year}): {exc}"

    a["installed"] = True
    if not was_installed:
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    years = ", ".join(installed_to) or "--"
    return True, (
        f"SketchUp extension {verb} (v{a['addin_version']}) for SketchUp {years}.\n\n"
        "It registers in Extension Manager and auto-loads on SketchUp's next launch. Restart "
        "SketchUp, open a model, switch the daemon to 3D mode, and focus SketchUp -- the row "
        "flips to \"connected\" once the Ruby extension attaches."
    )


# --- Unreal Engine: content-only Python PLUGIN (drop into an engine/project Plugins dir) ------
def _all_unreal_engines() -> list:
    """Every detected UnrealEditor.exe (Epic Games launcher installs), sorted so the newest is last."""
    return sorted(glob.glob(_unreal_exe_glob()))


def _unreal_plugin_dest(exe: str) -> Path:
    """The TrackballNav plugin folder inside an engine's Engine/Plugins, for a given UnrealEditor.exe
    (...\\UE_x.y\\Engine\\Binaries\\Win64\\UnrealEditor.exe -> ...\\UE_x.y\\Engine\\Plugins\\TrackballNav)."""
    return Path(exe).parents[2] / "Plugins" / "TrackballNav"


def _unreal_engine_label(exe: str) -> str:
    """The engine folder name (e.g. "UE_5.8") for a detected UnrealEditor.exe."""
    return Path(exe).parents[3].name


def unreal_plugin_dir() -> Path:
    """Primary install + version-read dir: the NEWEST detected engine's Engine/Plugins/TrackballNav,
    or a non-existent fallback when no engine is detected (=> installed_addin_version() is None =>
    the UI shows "Set up"). Mirrors _blender_primary_addon_dir / freecad_user_mod_dir."""
    exe = detect_unreal()
    if exe:
        return _unreal_plugin_dest(exe)
    return Path(_pf()) / "Epic Games" / "UE_0.0" / "Engine" / "Plugins" / "TrackballNav"


def install_unreal(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy the bundled content-only plugin into every detected engine's Engine/Plugins dir. Unreal
    auto-runs an enabled plugin's Content/Python/init_unreal.py at editor startup (verified live), so
    once the user enables "Trackball Nav" in Edit -> Plugins it starts with the editor -- the
    analogue of Fusion's "Run on Startup". Overwriting an existing copy == update. Modelled on
    install_blender (multiple engine versions, like Blender's multiple version dirs).

    Writing under Program Files needs admin, so a failed copy is a COMMON case: we then return clear
    manual steps (run as admin, or drop the folder into the engine/project Plugins dir), mirroring
    install_fusion returning the copy error rather than silently 'succeeding'."""
    engines = _all_unreal_engines()
    if not engines:
        return False, "Unreal Engine was not found on this machine — install it first."
    src = _bundled_addin("unreal", "TrackballNav")
    if not src.exists():
        return False, "Bundled Unreal add-on is missing from this build."
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    copied, failed = [], []
    for exe in engines:
        dest = _unreal_plugin_dest(exe)
        label = _unreal_engine_label(exe)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)   # overwrite == update
            copied.append(label)
        except OSError:
            failed.append((label, dest))
    if not copied:                                       # almost always: Program Files needs admin
        steps = "\n".join("    %s  ->  %s" % (lbl, dst) for lbl, dst in failed)
        return False, (
            "Couldn't write the Trackball plugin into the engine's Plugins folder (writing under "
            "Program Files needs administrator rights):\n" + steps + "\n\n"
            "Do ONE of these, then enable it in Edit -> Plugins -> search \"Trackball\" -> tick -> "
            "restart the editor:\n"
            "  - Re-run the daemon as administrator and click Set up again, or\n"
            "  - Copy this bundled folder into the engine's Plugins dir (as admin):\n"
            "        " + str(src) + "\n"
            "  - Or copy it into YOUR PROJECT's Plugins folder (no admin needed):\n"
            "        <YourProject>\\Plugins\\TrackballNav"
        ), [("Copy bundled plugin folder", str(src))]
    a["installed"] = True
    if not was_installed:                                # don't re-enable on an update
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    tail = ("\n\nNote: some engines need admin to write and were skipped: "
            + ", ".join(lbl for lbl, _ in failed)) if failed else ""
    return True, (
        f"Trackball plugin {verb} (v{a['addin_version']}) for {', '.join(copied)}.\n\n"
        "Enable it ONCE per project: Edit -> Plugins -> search \"Trackball\" -> tick \"Trackball "
        "Nav\" -> restart the editor (this also enables the Python Editor Script Plugin and "
        "GeoReferencing it depends on — GeoReferencing supplies the under-cursor mouse pixel).\n\n"
        "Then open a level, switch the daemon to 3D mode, and focus the Unreal Editor — the row "
        "flips to \"connected\" once the editor loads the plugin. For Under Cursor orbit, click "
        "the level viewport so it has focus." + tail)


# --- Unity Editor: UPM package into the open project's Packages/ folder -----------------
_unity_running_cache = (0.0, [])  # (monotonic_ts, paths)


def _unity_running_project_paths() -> list:
    """Project paths from running Unity.exe command lines (-projectpath), best-effort.

    Prefers Win32_Process via PowerShell (WMIC is removed on many Win11 installs).
    Cached briefly — UI status / auto_update call this often and must not flash consoles.
    """
    global _unity_running_cache
    now = time.monotonic()
    if now - _unity_running_cache[0] < 2.5:
        return list(_unity_running_cache[1])

    paths = []
    lines = []
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" "
            "| Select-Object -ExpandProperty CommandLine"
        )
        out = _hidden_check_output(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            timeout=8)
        lines = out.splitlines()
    except Exception:
        try:
            out = _hidden_check_output(
                ["wmic", "process", "where", "name='Unity.exe'", "get", "CommandLine"],
                timeout=5)
            lines = out.splitlines()
        except Exception:
            lines = []
    for line in lines:
        low = line.lower()
        if "-projectpath" not in low:
            continue
        # -projectpath "C:\path with spaces"  OR  -projectpath C:\path
        idx = low.index("-projectpath") + len("-projectpath")
        rest = line[idx:].strip()
        if rest.startswith('"'):
            end = rest.find('"', 1)
            p = rest[1:end] if end > 0 else rest[1:]
        else:
            p = rest.split()[0] if rest.split() else ""
        if p and os.path.isdir(p):
            paths.append(p)
    _unity_running_cache = (now, list(paths))
    return paths


def _unity_hub_recent_projects() -> list:
    """Recent project paths from Unity Hub ``%APPDATA%\\UnityHub\\projects-v1.json``.

    Current Hub format::

        {"schema_version": "v1", "data": {"C:\\\\path": {"path": "C:\\\\path", ...}, ...}}

    Older builds used a flat dict/list or ``projectBasePaths.json``.
    """
    paths = []
    la = os.environ.get("APPDATA", "")
    candidates = [
        Path(la) / "UnityHub" / "projects-v1.json",
        Path(la) / "UnityHub" / "projectBasePaths.json",
    ]

    def _take(p) -> None:
        if p and os.path.isdir(p):
            paths.append(p)

    def _walk(obj) -> None:
        if isinstance(obj, dict):
            # Hub v1: unwrap {"schema_version", "data": {path: record, ...}}
            if "data" in obj and isinstance(obj["data"], dict):
                for key, rec in obj["data"].items():
                    if isinstance(rec, dict):
                        _take(rec.get("path") or rec.get("projectPath") or key)
                    elif isinstance(rec, str):
                        _take(rec)
                return
            for _k, v in obj.items():
                if isinstance(v, dict):
                    _take(v.get("path") or v.get("projectPath"))
                elif isinstance(v, str):
                    _take(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, str):
                    _take(item)
                elif isinstance(item, dict):
                    _take(item.get("path") or item.get("projectPath"))

    for cand in candidates:
        if not cand.exists():
            continue
        try:
            with open(cand, "r", encoding="utf-8") as f:
                _walk(json.load(f))
        except Exception:
            continue
    return paths


def _unity_project_candidates() -> list:
    """Deduped project roots: running editors first, then Hub recents."""
    seen, out = set(), []
    for p in _unity_running_project_paths() + _unity_hub_recent_projects():
        ap = os.path.abspath(p)
        if ap not in seen and os.path.isdir(ap):
            seen.add(ap)
            out.append(ap)
    return out


def unity_plugin_dir() -> Path:
    """Primary install dir for version reads: first project Packages/…, else APPDATA fallback."""
    projects = _unity_project_candidates()
    if projects:
        return Path(projects[0]) / "Packages" / "com.astrolabe.trackball-nav"
    return Path(os.environ.get("APPDATA", "")) / "TrackballDaemon" / "unity" / "com.astrolabe.trackball-nav"


def install_unity(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy the UPM package into each detected Unity project's Packages/ folder. UPM auto-loads
    packages under Packages/; InitializeOnLoad starts the broker client after domain reload."""
    if not detect_unity() and not _unity_project_candidates():
        # Still allow install into APPDATA fallback if a project path appears later — but warn.
        pass
    src = _bundled_addin("unity", "com.astrolabe.trackball-nav")
    if not src.exists():
        return False, "Bundled Unity package is missing from this build."
    projects = _unity_project_candidates()
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    if not projects:
        # Fallback: stage under APPDATA and ask the user to open a project + Set up again.
        dest = Path(os.environ.get("APPDATA", "")) / "TrackballDaemon" / "unity" / "com.astrolabe.trackball-nav"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
        except OSError as e:
            return False, f"Couldn't stage the Unity package: {e}"
        a["installed"] = False
        _save_operational(cfg, appdef.key, a)
        return False, (
            "Unity Editor was detected (or not), but no open/recent project path was found.\n\n"
            "Open a Unity project, then click Set up again — the package will be copied into\n"
            "  <YourProject>\\Packages\\com.astrolabe.trackball-nav\\\n"
            "A staged copy is at:\n  " + str(dest)
        ), [("Copy staged package folder", str(dest))]
    copied = []
    for proj in projects:
        dest = Path(proj) / "Packages" / "com.astrolabe.trackball-nav"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
            copied.append(proj)
        except OSError:
            continue
    if not copied:
        return False, "Couldn't write the Unity package into any project Packages/ folder."
    a["installed"] = True
    if not was_installed:
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    return True, (
        f"Trackball Nav package {verb} (v{a['addin_version']}) into {len(copied)} project(s).\n\n"
        "Unity will import the UPM package on the next domain reload (or restart the Editor).\n"
        "Switch the daemon to 3D mode and focus Unity — the row flips to \"connected\" once the "
        "Editor script handshakes. Scene view only (not Play mode)."
    )


# --- Godot 4: EditorPlugin into the open project's addons/ folder ----------------------
_godot_running_cache = (0.0, [])


def _godot_running_project_paths() -> list:
    global _godot_running_cache
    now = time.monotonic()
    if now - _godot_running_cache[0] < 2.5:
        return list(_godot_running_cache[1])

    paths = []
    try:
        out = _hidden_check_output(
            ["wmic", "process", "where", "name like 'Godot%'", "get", "CommandLine"],
            timeout=5)
        for line in out.splitlines():
            # Godot is often launched as: Godot_v4.x.x.exe --path "C:\project"  or with project.godot arg
            low = line.lower()
            if "--path" in low:
                idx = low.index("--path") + len("--path")
                rest = line[idx:].strip()
                if rest.startswith('"'):
                    end = rest.find('"', 1)
                    p = rest[1:end] if end > 0 else rest[1:]
                else:
                    p = rest.split()[0] if rest.split() else ""
                if p and os.path.isdir(p):
                    paths.append(p)
            # Bare project.godot on the command line
            for token in line.replace('"', " ").split():
                if token.lower().endswith("project.godot"):
                    p = os.path.dirname(token)
                    if p and os.path.isdir(p):
                        paths.append(p)
    except Exception:
        pass
    _godot_running_cache = (now, list(paths))
    return paths


def _godot_recent_projects() -> list:
    """Godot editor recent projects from %APPDATA%\\Godot\\.

    Godot 4 ``projects.cfg`` stores each project as an INI section whose *name* is the
    absolute path (forward slashes), e.g.::

        [C:/Users/me/Documents/MyGame]
        favorite=false

    Older / alternate files (``editor_settings-*.tres``) may embed paths as plain text.
    """
    paths = []
    appdata = os.environ.get("APPDATA", "")

    def _accept(p: str) -> None:
        if not p:
            return
        p = p.strip().strip('"').replace("/", os.sep)
        if p.lower().endswith("project.godot"):
            p = os.path.dirname(p)
        try:
            if os.path.isdir(p) and (Path(p) / "project.godot").exists():
                paths.append(p)
        except Exception:
            pass

    for name in ("projects.cfg", "editor_settings-4.tres", "editor_settings-3.tres"):
        cand = Path(appdata) / "Godot" / name
        if not cand.exists():
            continue
        try:
            text = cand.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        text = text.replace("\\\\", "\\")
        for raw in text.splitlines():
            line = raw.strip()
            # projects.cfg: section header is the project directory.
            if line.startswith("[") and line.endswith("]") and len(line) > 2:
                inner = line[1:-1].strip()
                # Skip non-path sections (e.g. [application] if ever present).
                if ":" in inner or inner.startswith("/") or (len(inner) >= 2 and inner[1] == ":"):
                    _accept(inner)
                continue
            for part in line.replace(",", " ").replace('"', " ").split():
                p = part.strip().strip("[]")
                if not p:
                    continue
                if p.lower().endswith("project.godot") or (":" in p) or p.startswith("/"):
                    _accept(p)
    return paths


def _godot_project_candidates() -> list:
    seen, out = set(), []
    for p in _godot_running_project_paths() + _godot_recent_projects():
        ap = os.path.abspath(p)
        if ap not in seen and (Path(ap) / "project.godot").exists():
            seen.add(ap)
            out.append(ap)
    return out


def _godot_enable_plugin(project_godot: Path) -> None:
    """Idempotently enable res://addons/trackball_nav/plugin.cfg under [editor_plugins]."""
    text = project_godot.read_text(encoding="utf-8") if project_godot.exists() else ""
    plugin = "res://addons/trackball_nav/plugin.cfg"
    if plugin in text:
        return
    marker = "[editor_plugins]"
    enabled_line_prefix = "enabled=PackedStringArray("
    if marker not in text:
        text = text.rstrip() + f"\n\n{marker}\n{enabled_line_prefix}\"{plugin}\")\n"
    else:
        lines = text.splitlines(True)
        out, done = [], False
        for line in lines:
            if (not done) and line.startswith("enabled=PackedStringArray("):
                if line.rstrip().endswith(")"):
                    inner = line[len(enabled_line_prefix):].rstrip()
                    if inner.endswith(")"):
                        inner = inner[:-1]
                    inner = inner.strip()
                    new_inner = (inner + f", \"{plugin}\"") if inner else f"\"{plugin}\""
                    out.append(f"{enabled_line_prefix}{new_inner})\n")
                else:
                    out.append(line)
                done = True
            else:
                out.append(line)
        if not done:
            out2 = []
            for line in out:
                out2.append(line)
                if line.strip() == marker:
                    out2.append(f"{enabled_line_prefix}\"{plugin}\")\n")
            out = out2
        text = "".join(out)
    project_godot.write_text(text, encoding="utf-8")


def godot_plugin_dir() -> Path:
    projects = _godot_project_candidates()
    if projects:
        return Path(projects[0]) / "addons" / "trackball_nav"
    return Path(os.environ.get("APPDATA", "")) / "TrackballDaemon" / "godot" / "trackball_nav"


def install_godot(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy the EditorPlugin into each detected Godot project's addons/ and enable it."""
    src = _bundled_addin("godot", "trackball_nav")
    if not src.exists():
        return False, "Bundled Godot add-on is missing from this build."
    projects = _godot_project_candidates()
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    if not projects:
        dest = Path(os.environ.get("APPDATA", "")) / "TrackballDaemon" / "godot" / "trackball_nav"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
        except OSError as e:
            return False, f"Couldn't stage the Godot add-on: {e}"
        a["installed"] = False
        _save_operational(cfg, appdef.key, a)
        return False, (
            "No Godot project was found automatically.\n\n"
            "Set up looks for projects in:\n"
            "  • Running Godot processes (--path / project.godot on the command line)\n"
            "  • Recent projects under %APPDATA%\\Godot\\ "
            "(projects.cfg / editor_settings-4.tres)\n"
            "It does not scan a fixed projects folder.\n\n"
            "Easiest fix: open your project in Godot, then click Set up again.\n\n"
            "Manual install:\n"
            "  1. Copy the staged trackball_nav folder into:\n"
            "       <YourProject>\\addons\\trackball_nav\\\n"
            "  2. In Godot: Project → Project Settings → Plugins → enable "
            "\"Trackball Nav\"\n"
            "     (or add under [editor_plugins] in project.godot:\n"
            "      enabled=PackedStringArray("
            "\"res://addons/trackball_nav/plugin.cfg\"))\n"
            "  3. Reload the project or restart Godot, switch the daemon to 3D mode, "
            "and focus the editor.\n\n"
            "Staged add-on folder:\n  " + str(dest)
        ), [
            ("Copy staged add-on folder", str(dest)),
            ("Copy plugin.cfg path", "res://addons/trackball_nav/plugin.cfg"),
        ]
    copied = []
    for proj in projects:
        dest = Path(proj) / "addons" / "trackball_nav"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
            _godot_enable_plugin(Path(proj) / "project.godot")
            copied.append(proj)
        except OSError:
            continue
    if not copied:
        return False, "Couldn't write the Godot add-on into any project addons/ folder."
    a["installed"] = True
    if not was_installed:
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    return True, (
        f"Trackball Nav {verb} (v{a['addin_version']}) into {len(copied)} Godot project(s).\n\n"
        "The plugin is enabled in project.godot. Reload the project or restart Godot, switch the "
        "daemon to 3D mode, and focus the editor — the row flips to \"connected\" on handshake."
    )


# --- Rhino 8: Python scripts + startup command ----------------------------------------
def rhino_scripts_dir() -> Path:
    return (Path(os.environ.get("APPDATA", "")) / "McNeel" / "Rhinoceros" / "8.0" /
            "scripts" / "TrackballNav")


def _rhino_startup_command(start_py: Path) -> str:
    # Rhino 8 Python 3: RunPythonScript works for both; -_ form is non-interactive.
    return f'_-RunPythonScript "{start_py}"'


def _rhino_append_startup_command(cmd: str) -> bool:
    """Best-effort: append cmd to Rhino 8 scheme settings StartupCommands if not present."""
    settings = (Path(os.environ.get("APPDATA", "")) / "McNeel" / "Rhinoceros" / "8.0" /
                "settings" / "settings-Scheme__Default.xml")
    if not settings.exists():
        return False
    try:
        text = settings.read_text(encoding="utf-8")
    except Exception:
        return False
    if "TrackballNav" in text and "RunPythonScript" in text:
        return True  # already registered
    # Rhino stores startup as a child value; try a few known shapes.
    needle = 'key="StartupCommands"'
    if needle not in text:
        # Insert a minimal entry before </settings> or at end of known block — if we can't find
        # a safe insertion point, skip (user can add manually).
        return False
    # Common shape: <value>existing</value> under StartupCommands — append with newline.
    import re as _re
    pattern = _re.compile(
        r'(key="StartupCommands"[^>]*>\s*<value>)(.*?)(</value>)',
        _re.DOTALL | _re.IGNORECASE)
    m = pattern.search(text)
    if not m:
        return False
    existing = m.group(2).strip()
    if "TrackballNav" in existing:
        return True
    new_val = (existing + "\n" + cmd) if existing else cmd
    text = text[:m.start(2)] + new_val + text[m.end(2):]
    try:
        settings.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


def install_rhino(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Copy Python TrackballNav into Rhino 8 user scripts and register a startup command."""
    if not detect_rhino():
        return False, "Rhino 8 was not found on this machine — install it first."
    src = _bundled_addin("rhino", "TrackballNav")
    if not src.exists():
        return False, "Bundled Rhino add-on is missing from this build."
    dest = rhino_scripts_dir()
    a = _operational_state(cfg, appdef.key)
    was_installed = a.get("installed", False)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except OSError as e:
        return False, f"Couldn't write the Rhino add-on: {e}"
    start_py = dest / "start.py"
    cmd = _rhino_startup_command(start_py)
    auto = _rhino_append_startup_command(cmd)
    a["installed"] = True
    if not was_installed:
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    _save_operational(cfg, appdef.key, a)
    verb = "updated" if was_installed else "installed"
    if auto:
        return True, (
            f"Trackball Nav {verb} (v{a['addin_version']}) to:\n  {dest}\n\n"
            "A Rhino startup command was registered so Trackball Nav loads on the next "
            "Rhino launch.\n\n"
            "Restart Rhino, switch the daemon to 3D mode, and focus Rhino — the row flips to "
            "\"connected\" once the script handshakes."
        )
    return True, (
        f"Trackball Nav {verb} (v{a['addin_version']}) to:\n  {dest}\n\n"
        "Could not auto-register the startup command. In Rhino, type Options in the "
        "command line, open General → \"Run these commands every time Rhino starts\", "
        "and add the command below (or run it once after opening Rhino).\n\n"
        "Restart Rhino, switch the daemon to 3D mode, and focus Rhino — the row flips to "
        "\"connected\" once the script handshakes."
    ), [("Copy startup command", cmd)]


# --- add-in version tracking + one-click / auto update --------------------------------
# Apps that ship a bundled add-in: key -> (bundled subpath, dest folder, manifest filename).
_ADDINS = {
    "fusion360": ("fusion360/TrackballNav", lambda: fusion_addins_dir() / "TrackballNav",
                  "TrackballNav.manifest"),
    "blender": ("blender/trackball_nav", _blender_primary_addon_dir, "version.json"),
    "freecad": ("freecad/TrackballNav", freecad_user_mod_dir, "version.json"),
    "sketchup": ("sketchup/trackball_nav", _sketchup_primary_addon_dir, "version.json"),
    "unreal": ("unreal/TrackballNav", unreal_plugin_dir, "version.json"),
    "unity": ("unity/com.astrolabe.trackball-nav", unity_plugin_dir, "version.json"),
    "godot": ("godot/trackball_nav", godot_plugin_dir, "version.json"),
    "rhino": ("rhino/TrackballNav", rhino_scripts_dir, "version.json"),
    "autocad": ("autocad", _acad_runtime_plugin_dir, "version.json"),
}
ADDIN_KEYS = set(_ADDINS)


def _read_manifest_version(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return str(json.load(f).get("version", "0"))
    except Exception:
        return None


def bundled_addin_version(key: str):
    if key not in _ADDINS:
        return None
    sub, _dest, mani = _ADDINS[key]
    return _read_manifest_version(_bundled_addin(*sub.split("/"), mani))


def installed_addin_version(key: str):
    if key not in _ADDINS:
        return None
    _sub, dest_fn, mani = _ADDINS[key]
    p = dest_fn() / mani
    return _read_manifest_version(p) if p.exists() else None


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, TypeError):
        return (0,)


_VERSION_UNSET = object()


def update_available(key: str, installed_version=_VERSION_UNSET) -> bool:
    """Compare the bundle with a specific loaded copy, or the fallback install path when omitted."""
    inst = (installed_addin_version(key)
            if installed_version is _VERSION_UNSET else installed_version)
    if inst is None:
        return False
    return _ver_tuple(bundled_addin_version(key)) > _ver_tuple(inst)


def auto_update(cfg) -> list:
    """Re-copy installed add-ins and report only versions confirmed at their runtime location."""
    updated = []
    for key in _ADDINS:
        old = installed_addin_version(key)
        if old is not None and update_available(key):
            appdef = APPS_BY_KEY[key]
            ok, _msg, _copies = normalize_install_result(appdef.setup(appdef, cfg))
            installed = installed_addin_version(key)
            if ok and installed is not None and not update_available(key):
                updated.append((key, old, installed))
    return updated


_APP_UX = {
    "blender": dict(
        install_model="User add-on copied into each detected Blender version; optional startup shim.",
        supported_versions="Blender 4.2 through 5.1 (tested on 5.1.1)",
        setup_instructions=("Set up copies Trackball Nav to every detected user scripts/addons "
                            "folder and asks whether to add the auto-enable startup shim."),
        manual_install=("Copy trackball_daemon\\plugins\\blender\\trackball_nav to "
                        "%APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons\\"
                        "trackball_nav, then enable Trackball Nav in Preferences > Add-ons. "
                        "No administrator access is required."),
        health_check=("Restart Blender. The row should show connected while Blender is focused; "
                      "details are in %APPDATA%\\TrackballDaemon\\blender_addin.log."),
        security_notes=("Copies unsigned Python source only into Blender's current-user folders. "
                        "The optional startup shim runs that source at Blender launch; the UI asks "
                        "separately before installing it. No elevation or external listener."),
    ),
    "freecad": dict(
        install_model="User Mod add-on; files are copied only under the current Windows profile.",
        supported_versions="FreeCAD 1.0 through 1.1 (tested on 1.1.1)",
        setup_instructions="Set up copies TrackballNav into FreeCAD's version-aware user Mod folder.",
        manual_install=("Copy trackball_daemon\\plugins\\freecad\\TrackballNav to "
                        "%APPDATA%\\FreeCAD\\v<major>-<minor>\\Mod\\TrackballNav (FreeCAD 1.x), "
                        "then restart FreeCAD. Use %APPDATA%\\FreeCAD\\Mod for older layouts."),
        health_check=("Open a 3D view and focus FreeCAD; the row should show connected. Check "
                      "%APPDATA%\\TrackballDaemon\\freecad_addin.log if it does not."),
        security_notes=("Copies unsigned Python into FreeCAD's current-user Mod folder, where "
                        "FreeCAD loads it at startup. No elevation, registry write, or external port."),
    ),
    "sketchup": dict(
        install_model="Per-version Ruby extension in SketchUp's user Plugins folder.",
        supported_versions="SketchUp Desktop 2025 through 2026 (tested on 2026.2.243)",
        setup_instructions="Set up copies the loader and extension into every detected annual release.",
        manual_install=("Copy trackball_daemon\\plugins\\sketchup\\trackball_nav_loader.rb and the "
                        "trackball_nav folder to %APPDATA%\\SketchUp\\SketchUp <year>\\SketchUp\\"
                        "Plugins, then restart SketchUp. SketchUp for Web is not supported."),
        health_check=("Extension Manager should list Trackball Nav; focus a model and look for "
                      "connected in this row or inspect %APPDATA%\\TrackballDaemon\\sketchup_addin.log."),
        security_notes=("Copies an unsigned Ruby extension into SketchUp's current-user Plugins "
                        "folder. SketchUp executes it at startup. No elevation or external listener."),
    ),
    "unreal": dict(
        install_model="Unreal Editor plugin, installed per engine or per project.",
        supported_versions="Unreal Engine 5.8 (tested on 5.8.0)",
        setup_instructions=("Set up copies TrackballNav to each detected Engine/Plugins folder. "
                            "Engine-level writes may require administrator permission."),
        manual_install=("Without administrator access, copy trackball_daemon\\plugins\\unreal\\"
                        "TrackballNav to <YourProject>\\Plugins\\TrackballNav. Enable Trackball Nav "
                        "and Python Editor Script Plugin in Edit > Plugins, then restart the editor."),
        health_check=("Focus a perspective level viewport; the row should show connected. Check "
                      "%APPDATA%\\TrackballDaemon\\unreal_addin.log and the Output Log on failure."),
        security_notes=("Engine-wide setup writes an unsigned Python editor plugin under Program "
                        "Files and may require UAC/elevation. Per-project installation avoids "
                        "elevation. The plugin connects only to the loopback nav broker."),
        security_confirmation=("Unreal engine-wide setup will attempt to copy an unsigned Python "
                               "editor plugin into each detected Engine/Plugins folder. Windows may "
                               "request administrator approval; use the documented per-project "
                               "Plugins folder if you do not want an elevated install."),
    ),
    "unity": dict(
        install_model="UPM Editor package copied into each detected Unity project.",
        supported_versions="Unity 6 / 6000.x (implemented against 6000.5.3f1)",
        setup_instructions=("Set up finds running/recent projects and copies the package into each "
                            "project's Packages folder; Unity recompiles it automatically."),
        manual_install=("Copy trackball_daemon\\plugins\\unity\\com.astrolabe.trackball-nav to "
                        "<YourProject>\\Packages\\com.astrolabe.trackball-nav. If Set up found no "
                        "project, the same package is staged under %APPDATA%\\TrackballDaemon\\unity."),
        health_check=("Open and focus a Scene view; the row should show connected. Check the Unity "
                      "Console and %APPDATA%\\TrackballDaemon\\unity_addin.log."),
        security_notes=("Copies unsigned C# editor source into each detected project's Packages "
                        "folder; Unity compiles and executes it in the Editor. No elevation or "
                        "machine-wide setting change."),
    ),
    "godot": dict(
        install_model="Godot EditorPlugin copied and enabled per project.",
        supported_versions="Godot 4.4 through 4.7",
        setup_instructions=("Set up finds running/recent projects, copies addons/trackball_nav, and "
                            "enables res://addons/trackball_nav/plugin.cfg."),
        manual_install=("Copy trackball_daemon\\plugins\\godot\\trackball_nav to "
                        "<YourProject>\\addons\\trackball_nav, then enable Trackball Nav under "
                        "Project > Project Settings > Plugins. A staged copy is also placed under "
                        "%APPDATA%\\TrackballDaemon\\godot when no project is found."),
        health_check=("Reload the project, focus a 3D editor viewport, and look for connected. "
                      "Check %APPDATA%\\TrackballDaemon\\godot_addin.log on failure."),
        security_notes=("Copies unsigned GDScript into each detected project and edits that "
                        "project's project.godot to enable the plugin. No elevation or machine-wide "
                        "setting change."),
        security_confirmation=("Godot setup copies editor scripts into every detected project and "
                               "edits project.godot to enable Trackball Nav. Review or use the "
                               "manual project-local steps if automatic project edits are unwanted."),
    ),
    "rhino": dict(
        install_model="Rhino 8 user Python scripts plus a per-user startup command.",
        supported_versions="Rhino 8",
        setup_instructions=("Set up copies TrackballNav into Rhino's user scripts folder and "
                            "best-effort registers its startup command."),
        manual_install=("Copy trackball_daemon\\plugins\\rhino\\TrackballNav to "
                        "%APPDATA%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav. In Rhino "
                        "Options > General, add _-RunPythonScript \"<path>\\start.py\" to startup "
                        "commands, then restart Rhino."),
        health_check=("Focus a Rhino viewport and look for connected. Check "
                      "%APPDATA%\\TrackballDaemon\\rhino_addin.log if startup failed."),
        security_notes=("Copies unsigned Python into Rhino's current-user scripts folder and may "
                        "edit the current-user Rhino startup-command XML so it runs at launch. No "
                        "elevation or machine-wide registry write."),
        security_confirmation=("Rhino setup copies Python scripts and will try to append a command "
                               "to your per-user Rhino startup configuration. If you decline, use "
                               "the documented manual command instead."),
    ),
    "fusion360": dict(
        install_model="Fusion user add-in copied to Autodesk's per-user AddIns folder.",
        supported_versions="Current Fusion production release (rolling Autodesk release)",
        setup_instructions=("Set up copies TrackballNav. In Fusion, open Utilities > Add-Ins, run "
                            "TrackballNav once, and enable Run on Startup."),
        manual_install=("Copy trackball_daemon\\plugins\\fusion360\\TrackballNav to "
                        "%APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav, "
                        "then run it from Utilities > Add-Ins. No administrator access is required."),
        health_check=("Focus an open design and look for connected. Check "
                      "%APPDATA%\\TrackballDaemon\\fusion_addin.log if the add-in does not handshake."),
        security_notes=("Copies unsigned Python into Fusion's current-user AddIns folder. Fusion "
                        "does not execute it until you explicitly Run it and select Run on Startup. "
                        "No elevation or machine-wide setting change."),
    ),
    "solidworks": dict(
        install_model="Direct COM automation; no SolidWorks add-in or host files are installed.",
        setup_required=False,
        first_run_action="Enable",
        supported_versions="SOLIDWORKS 2025 (tested on 2025)",
        setup_instructions=("Enable performs a one-time prerequisite check for SOLIDWORKS and "
                            "pywin32. After that, the Enabled checkbox is the only control needed."),
        manual_install=("There is nothing to copy. If the prerequisite check fails, install "
                        "pywin32 into the daemon's Python environment with: pip install pywin32."),
        health_check=("Open a part or assembly and focus SOLIDWORKS; the row should show connected. "
                      "Driver messages are recorded in %APPDATA%\\TrackballDaemon\\daemon.log."),
        security_notes=("Uses per-user COM automation against an already-running SOLIDWORKS "
                        "instance. It does not register a COM server, install a DLL, launch "
                        "SOLIDWORKS, request elevation, or listen on an external interface."),
    ),
    "onshape": dict(
        install_model="Browser bridge; no Onshape add-in is installed.",
        supported_versions="Current Onshape web release (rolling release)",
        setup_instructions=("Set up creates the bridge's per-user local TLS certificate. Trust it "
                            "once and enable SpaceMouse/3Dconnexion in Onshape preferences."),
        manual_install=("No application files need copying. Generate/trust the certificate using "
                        "the Set up dialog or certutil -user, then install the supplied userscript "
                        "only if Under Cursor orbit is wanted. Administrator access is not required."),
        health_check=("Open and focus an Onshape document; the row should show connected after the "
                      "browser handshake. Check %APPDATA%\\TrackballDaemon\\daemon.log."),
        security_notes=("Creates a per-user self-signed leaf certificate and binds TLS only to "
                        "127.51.68.120. Trust-store installation is never automatic. The optional "
                        "Onshape-only userscript reports canvas-relative pointer coordinates; it "
                        "does not capture the screen or send model data."),
        security_confirmation=("Onshape setup creates a self-signed certificate in your APPDATA "
                               "folder. Trusting it is a separate manual action that produces a "
                               "normal Windows/browser security warning and can be undone. The "
                               "local service accepts browser requests only from onshape.com."),
    ),
    "autocad": dict(
        install_model="Per-user .NET plugin staged by the daemon and NETLOADed automatically.",
        supported_versions="AutoCAD 2025 through 2027 (.NET 8 family; tested on 2026)",
        setup_instructions=("Set up stages TrackballNavAcad.dll under the daemon's APPDATA folder. "
                            "The daemon adds that folder to TRUSTEDPATHS and NETLOADs it on attach."),
        manual_install=("Copy trackball_daemon\\plugins\\autocad\\TrackballNavAcad.dll and "
                        "version.json to %APPDATA%\\TrackballDaemon\\acad_plugin. Add that folder "
                        "to TRUSTEDPATHS and run NETLOAD on the DLL. No Program Files write is needed."),
        health_check=("Type TBNAV in AutoCAD or look for connected in this row. Plugin details are "
                      "in %APPDATA%\\TrackballDaemon\\acad_plugin.log."),
        security_notes=("Stages an unsigned .NET DLL under the current user's APPDATA, adds only "
                        "that exact folder to AutoCAD TRUSTEDPATHS, and NETLOADs it through COM. "
                        "It never launches AutoCAD, writes Program Files, or requires elevation."),
        security_confirmation=("AutoCAD setup enables later automatic loading of an unsigned .NET "
                               "plugin. When the daemon attaches to a running AutoCAD it adds the "
                               "single APPDATA plugin folder to TRUSTEDPATHS and issues NETLOAD. "
                               "Cancel if you prefer the documented manual trust/load steps."),
    ),
}


APPS = (
    AppDef(APP_SPECS_BY_ID["blender"], True, detect_blender, setup=install_blender,
           **_APP_UX["blender"]),
    AppDef(APP_SPECS_BY_ID["freecad"], True, detect_freecad, setup=install_freecad,
           **_APP_UX["freecad"]),
    AppDef(APP_SPECS_BY_ID["sketchup"], True, detect_sketchup, setup=install_sketchup,
           **_APP_UX["sketchup"]),
    AppDef(APP_SPECS_BY_ID["unreal"], True, detect_unreal, setup=install_unreal,
           **_APP_UX["unreal"]),
    AppDef(APP_SPECS_BY_ID["unity"], True, detect_unity, setup=install_unity,
           **_APP_UX["unity"]),
    AppDef(APP_SPECS_BY_ID["godot"], True, detect_godot, setup=install_godot,
           **_APP_UX["godot"]),
    AppDef(APP_SPECS_BY_ID["rhino"], True, detect_rhino, setup=install_rhino,
           **_APP_UX["rhino"]),
    AppDef(APP_SPECS_BY_ID["fusion360"], True, detect_fusion, setup=install_fusion,
           **_APP_UX["fusion360"]),
    AppDef(APP_SPECS_BY_ID["solidworks"], False, detect_solidworks, setup=setup_solidworks,
           **_APP_UX["solidworks"]),
    AppDef(APP_SPECS_BY_ID["onshape"], False, detect_onshape, setup=setup_onshape,
           **_APP_UX["onshape"]),
    AppDef(APP_SPECS_BY_ID["autocad"], True, detect_autocad, setup=install_autocad,
           **_APP_UX["autocad"]),
)
APPS_BY_KEY = MappingProxyType({app_id: next(app for app in APPS if app.key == app_id)
                                for app_id in APP_SPECS_BY_ID})


def setup_action_label(appdef: AppDef, app_cfg, installed_version=_VERSION_UNSET) -> Optional[str]:
    """The meaningful setup action for the app's current state, or None for no button.

    Bundled add-ins can always be reinstalled/updated. No-file integrations expose their one-time
    prerequisite/setup action only until it succeeds; there is deliberately no placebo Re-check.
    """
    if appdef.key in ADDIN_KEYS:
        version = (installed_addin_version(appdef.key)
                   if installed_version is _VERSION_UNSET else installed_version)
        if version:
            if update_available(appdef.key, installed_version=version):
                return f"Update → v{bundled_addin_version(appdef.key)}"
            return "Reinstall"
        return appdef.first_run_action or "Set up"
    if not bool((app_cfg or {}).get("installed")):
        return appdef.first_run_action
    return None


def status_line(appdef: AppDef) -> str:
    if sys.platform != "win32" and appdef.key != "onshape":
        return "version detection is Windows-only"
    found = appdef.detect()
    if not found:
        return "not detected"
    result = compatibility(appdef, found)
    if appdef.key == "onshape":
        return found
    if result.status == "unsupported":
        return f"WARNING — detected {result.message}  •  {found}"
    if result.status == "unverified":
        return f"CAUTION — detected {result.message}  •  {found}"
    version = f" v{result.version}" if result.version else ""
    return f"detected{version}: {found}"


def install(appdef: AppDef, cfg):
    """Set up the integration. Returns ``(ok, message)`` or ``(ok, message, copyables)``
    where ``copyables`` is a list of ``(button_label, text_to_copy)`` for the UI dialog."""
    if appdef.setup is not None:                 # app with a real installer (e.g. Fusion)
        return appdef.setup(appdef, cfg)
    found = appdef.detect()
    if appdef.needs_plugin and not found:
        return False, f"{appdef.name} was not found on this machine — install it first."
    cfg.set_app_operational(appdef.key, installed=True, enabled=True)
    if appdef.needs_plugin:
        # Generic path for a future AppDef registered without its own setup; every
        # current app has one, so this is unreachable today.
        return True, f"{appdef.name} integration enabled.\n(No bundled add-on -- nothing was copied.)"
    return True, f"{appdef.name} gesture profile enabled."


def normalize_install_result(result):
    """Normalize installer returns to ``(ok, message, copyables)``."""
    if isinstance(result, tuple) and len(result) == 3:
        ok, msg, copies = result
        return bool(ok), str(msg), list(copies or [])
    ok, msg = result
    return bool(ok), str(msg), []
