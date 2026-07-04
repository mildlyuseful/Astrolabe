"""3D-app integration registry: detect installed apps and run their setup.

Detection is best-effort via common Windows install paths. "Set up" records the
integration as installed/enabled in config; the actual add-on payload (e.g. a Blender
add-on or a SolidWorks macro) is intentionally left as a TODO -- this layer is the shell
the UI drives, not the plugin itself.
"""
import glob
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class AppDef:
    key: str
    name: str
    needs_plugin: bool                 # True: needs an add-on; False: gesture/profile only
    detect: Callable[[], Optional[str]]
    setup: Optional[Callable] = None   # per-app installer; None => generic mark-installed


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
    a = cfg.data["apps"][appdef.key]
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
    cfg.save()
    verb = "updated" if was_installed else "copied"
    return True, (f"Add-in {verb} in Fusion's AddIns folder (v{a['addin_version']}).\n\n"
                  "In Fusion: Utilities → Add-Ins (Shift+S) → select \"TrackballNav\" → Run, and "
                  "tick \"Run on Startup\". Restart Fusion (or stop/run the add-in) to apply.")


# --- SolidWorks: no-file COM integration ----------------------------------------------
def setup_solidworks(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the SolidWorks integration. Unlike Fusion there is NOTHING to copy or register:
    SolidWorks is driven live over its COM automation API (pywin32) by the in-process driver,
    not by a registered add-in. So we only verify SolidWorks is installed and pywin32 is
    importable, then mark the app enabled/installed in config."""
    if not detect_solidworks():
        return False, "SolidWorks was not found on this machine — install it first."
    try:
        import win32com.client  # noqa: F401  (presence check only)
    except Exception:
        return False, ("pywin32 is required to drive SolidWorks over COM, but it isn't "
                       "installed.\nInstall it with:  pip install pywin32")
    a = cfg.data["apps"][appdef.key]
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = ""                       # no add-in for SolidWorks (driven via COM)
    cfg.save()
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


def _copy_acad_plugin() -> bool:
    """Copy the bundled plugin DLL + version manifest into the runtime dir. Returns False when the
    DLL is locked (AutoCAD has this session's copy loaded) -- the loader retries the copy on its
    next session attach, i.e. the update lands on AutoCAD's next start."""
    src = _bundled_addin("autocad", "TrackballNavAcad.dll")
    if not src.exists():
        return False
    dst_dir = _acad_runtime_plugin_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst_dir / src.name)
    except OSError:
        return False
    ver = _bundled_addin("autocad", "version.json")
    if ver.exists():
        try:
            shutil.copy2(ver, dst_dir / "version.json")
        except OSError:
            pass
    return True


def install_autocad(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the AutoCAD integration and stage its bundled smooth-orbit plugin.

    AutoCAD's navigation comes from a compiled NETLOAD plugin (`TrackballNavAcad.dll`,
    plugin_src/autocad) -- the SOLE AutoCAD transport (the COM nav transport is archived at
    archive/autocad_com_transport/). The daemon's plugin loader auto-NETLOADs it into a running
    AutoCAD -- a PROVEN zero-user-friction pattern (docs 8.14). Install/Update here just copies
    the bundled DLL + version.json into the runtime dir. AutoCAD locks the loaded DLL for its
    whole session, so with AutoCAD running an update is only STAGED: it is copied + NETLOADed
    automatically the next time AutoCAD starts. COM's only remaining job is that NETLOAD."""
    if not detect_autocad():
        return False, "AutoCAD was not found on this machine — install it first."
    try:
        import win32com.client  # noqa: F401  (presence check only)
    except Exception:
        return False, ("pywin32 is required to NETLOAD the AutoCAD plugin (COM delivery), but it "
                       "isn't installed.\nInstall it with:  pip install pywin32")
    ver = bundled_addin_version("autocad") or "?"
    copied = _copy_acad_plugin()
    a = cfg.data["apps"][appdef.key]
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = ver if copied else (installed_addin_version("autocad") or "")
    cfg.save()
    if copied:
        return True, (f"AutoCAD plugin v{ver} installed.\n\nNothing to do inside AutoCAD: the "
                      "daemon NETLOADs it automatically when it attaches to a running AutoCAD "
                      "(if AutoCAD is open right now with an older plugin loaded, restart it to "
                      "pick this version up). Type TBNAV in AutoCAD to check the plugin status.")
    return True, (f"AutoCAD plugin update to v{ver} is STAGED — the currently loaded plugin file "
                  "is locked by a running AutoCAD.\n\nClose AutoCAD; the daemon installs and "
                  "loads the new version automatically the next time AutoCAD starts.")


# --- Onshape: browser bridge (impersonate the 3Dconnexion local NL-Proxy) -------------------
def _onshape_cert_paths(cfg):
    """Cert/key paths for the Onshape bridge: the config override if set, else the driver default
    (onshape_cert.pem / onshape_key.pem in the per-user config dir)."""
    from . import onshape_bridge
    o = cfg.data.get("onshape", {}) or {}
    d_cert, d_key = onshape_bridge.default_cert_paths()
    return (o.get("cert_path") or d_cert, o.get("key_path") or d_key)


def setup_onshape(appdef: "AppDef", cfg) -> tuple[bool, str]:
    """Enable the Onshape integration. Like SolidWorks there is no add-in to install: Onshape is
    driven over the browser's native 3Dconnexion support by an in-process bridge that impersonates
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
    a = cfg.data["apps"][appdef.key]
    a["installed"] = True
    a["enabled"] = True
    a["addin_version"] = ""                       # no add-in for Onshape (browser bridge)
    cfg.save()
    return True, (
        "Onshape integration enabled — it's driven through the browser's built-in 3Dconnexion "
        "support, so there's no add-in to install.\n\n"
        "Two one-time steps:\n"
        "1) Trust the local certificate so Chrome/Edge will connect. Recommended (no admin) — run "
        "in a terminal:\n"
        "      certutil -user -addstore Root \"%s\"\n"
        "   Click 'Yes' on the Windows prompt. (To undo later: certutil -user -delstore Root "
        "127.51.68.120.) Alternatively, just browse to https://127.51.68.120:8181 once and accept "
        "the warning.\n"
        "2) In Onshape, enable the SpaceMouse / 3Dconnexion option (Account → Preferences, or the "
        "view settings).\n\n"
        "Then open an Onshape document in Chrome, switch the daemon to 3D mode, and focus the "
        "Onshape tab — the row flips to \"connected\" once Onshape's 3D mouse client connects."
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
    a = cfg.data["apps"][appdef.key]
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
    cfg.save()
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
    a = cfg.data["apps"][appdef.key]
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
    cfg.save()
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
        return False, "SketchUp Desktop was not found on this machine â€” install Pro/Studio first."
    src_root = _bundled_addin("sketchup")
    src_loader = src_root / "trackball_nav_loader.rb"
    src_addon = src_root / "trackball_nav"
    if not src_loader.exists() or not src_addon.exists():
        return False, "Bundled SketchUp extension is missing from this build."
    plugin_dirs = _sketchup_plugins_dirs()
    if not plugin_dirs:
        return False, "Could not determine SketchUp's per-version Plugins folders."

    a = cfg.data["apps"][appdef.key]
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
    cfg.save()
    verb = "updated" if was_installed else "installed"
    years = ", ".join(installed_to) or "â€”"
    return True, (
        f"SketchUp extension {verb} (v{a['addin_version']}) for SketchUp {years}.\n\n"
        "It registers in Extension Manager and auto-loads on SketchUp's next launch. Restart "
        "SketchUp, open a model, switch the daemon to 3D mode, and focus SketchUp â€” the row "
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
    a = cfg.data["apps"][appdef.key]
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
            "        <YourProject>\\Plugins\\TrackballNav")
    a["installed"] = True
    if not was_installed:                                # don't re-enable on an update
        a["enabled"] = True
    a["addin_version"] = bundled_addin_version(appdef.key) or ""
    cfg.save()
    verb = "updated" if was_installed else "installed"
    tail = ("\n\nNote: some engines need admin to write and were skipped: "
            + ", ".join(lbl for lbl, _ in failed)) if failed else ""
    return True, (
        f"Trackball plugin {verb} (v{a['addin_version']}) for {', '.join(copied)}.\n\n"
        "Enable it ONCE per project: Edit -> Plugins -> search \"Trackball\" -> tick \"Trackball "
        "Nav\" -> restart the editor (this also enables the Python Editor Script Plugin it depends "
        "on).\n\n"
        "Then open a level, switch the daemon to 3D mode, and focus the Unreal Editor — the row "
        "flips to \"connected\" once the editor loads the plugin." + tail)


# --- add-in version tracking + one-click / auto update --------------------------------
# Apps that ship a bundled add-in: key -> (bundled subpath, dest folder, manifest filename).
_ADDINS = {
    "fusion360": ("fusion360/TrackballNav", lambda: fusion_addins_dir() / "TrackballNav",
                  "TrackballNav.manifest"),
    "blender": ("blender/trackball_nav", _blender_primary_addon_dir, "version.json"),
    "freecad": ("freecad/TrackballNav", freecad_user_mod_dir, "version.json"),
    "sketchup": ("sketchup/trackball_nav", _sketchup_primary_addon_dir, "version.json"),
    "unreal": ("unreal/TrackballNav", unreal_plugin_dir, "version.json"),
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


def update_available(key: str) -> bool:
    inst = installed_addin_version(key)
    if inst is None:
        return False
    return _ver_tuple(bundled_addin_version(key)) > _ver_tuple(inst)


def auto_update(cfg) -> list:
    """Re-copy any installed add-in whose bundled version is newer. Returns [(key, old, new)].
    Safe while the app is running -- new files take effect on the app's next launch."""
    updated = []
    for key in _ADDINS:
        old = installed_addin_version(key)
        if old is not None and update_available(key):
            appdef = APPS_BY_KEY[key]
            ok, _msg = appdef.setup(appdef, cfg)
            if ok:
                updated.append((key, old, bundled_addin_version(key)))
    return updated


APPS = [
    AppDef("blender",    "Blender",    True,  detect_blender, setup=install_blender),
    AppDef("freecad",    "FreeCAD",    True,  detect_freecad, setup=install_freecad),
    AppDef("sketchup",   "SketchUp",   True,  detect_sketchup, setup=install_sketchup),
    AppDef("unreal",     "Unreal Engine", True, detect_unreal, setup=install_unreal),
    AppDef("fusion360",  "Fusion 360", True,  detect_fusion, setup=install_fusion),
    AppDef("solidworks", "SolidWorks", True,  detect_solidworks, setup=setup_solidworks),
    AppDef("onshape",    "Onshape",    False, detect_onshape, setup=setup_onshape),
    AppDef("autocad",    "AutoCAD",    True,  detect_autocad, setup=install_autocad),
]
APPS_BY_KEY = {a.key: a for a in APPS}


def status_line(appdef: AppDef) -> str:
    if sys.platform != "win32" and appdef.key != "onshape":
        return "version detection is Windows-only"
    found = appdef.detect()
    if not found:
        return "not detected"
    return found if appdef.key == "onshape" else f"detected: {found}"


def install(appdef: AppDef, cfg) -> tuple[bool, str]:
    """Set up the integration. Returns (ok, message). Persists installed/enabled to config."""
    if appdef.setup is not None:                 # app with a real installer (e.g. Fusion)
        return appdef.setup(appdef, cfg)
    found = appdef.detect()
    if appdef.needs_plugin and not found:
        return False, f"{appdef.name} was not found on this machine — install it first."
    cfg.data["apps"][appdef.key]["installed"] = True
    cfg.data["apps"][appdef.key]["enabled"] = True
    cfg.save()
    if appdef.needs_plugin:
        return True, f"{appdef.name} integration enabled.\n(Add-on payload install is a TODO.)"
    return True, f"{appdef.name} gesture profile enabled."
