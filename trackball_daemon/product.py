# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The single owner of product identity: names, paths, and Windows registration identifiers.

Nothing else may spell these out. An installer, an uninstaller, a startup entry, a single-instance
guard, and a configuration directory that disagree about who the product is are how an upgrade
orphans a user's settings or leaves a second copy running.

Two groups live here, and the difference matters:

*Frozen* values are permanent. Once a signed installer has written an upgrade identifier or a
Start Menu entry to a real machine, changing it makes the next release install alongside the old
one instead of upgrading it. They are chosen now, before any installer ships, so they never need to
change.

A third group at the bottom derives release identity from the version, so a build cannot describe
itself as something its version number contradicts.

*Legacy* values are the identity this project wrote on disk under its original name. They are no
longer where anything is written, but a machine that ran an earlier build still holds them, and so
does every host add-on copy an earlier build installed. They stay here because the frozen identity
is only reachable by carrying that state across, and because a build that stopped recognizing them
would strand it.
"""

import re

# --- Frozen identity ---------------------------------------------------------------------------

PRODUCT_NAME = "Astrolabe"
PUBLISHER = "Mildly Useful"

#: Displayed by Windows for the packaged build.
DISPLAY_NAME = PRODUCT_NAME
EXECUTABLE_NAME = "Astrolabe.exe"

#: Taskbar/notification identity. CompanyName.ProductName, no spaces, under 128 characters.
APP_USER_MODEL_ID = "MildlyUseful.Astrolabe"

#: Per-user installation, relative to %LOCALAPPDATA%. Deliberately not Program Files: installation
#: must never require administrator rights.
INSTALL_DIRECTORY = r"Programs\Mildly Useful\Astrolabe"

#: Start Menu folder, relative to the user's Programs folder.
START_MENU_FOLDER = PUBLISHER

#: Inno Setup AppId. This is the upgrade identity: a release that changes it installs a second copy
#: beside the first rather than upgrading it, and the old copy's uninstaller stays behind. It is
#: frozen before the first installer exists precisely so that can never happen.
INSTALLER_APP_ID = "{33239B81-1568-4836-A080-047000253B0D}"

#: Where Inno Setup registers the per-user uninstall entry, derived from the AppId above.
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    "\\" + INSTALLER_APP_ID + "_is1")

ARCHIVE_BASENAME = "Astrolabe-{version}-windows-x64.zip"
INSTALLER_BASENAME = "AstrolabeSetup-{version}-windows-x64.exe"

#: Python distribution name. The import package stays `trackball_daemon`: renaming an import
#: namespace for branding breaks every existing import for no user-visible benefit.
DISTRIBUTION_NAME = "astrolabe-daemon"
IMPORT_PACKAGE = "trackball_daemon"

#: The configuration root, relative to %APPDATA%. `trackball_daemon.paths` owns resolving it and
#: carrying an earlier build's directory across to it.
CONFIG_DIRECTORY = r"Mildly Useful\Astrolabe"

#: The same root as a user would see it written. Help text and documentation name a real path, and
#: they may not disagree with the one the daemon uses.
CONFIG_DIRECTORY_DISPLAY = "%APPDATA%\\" + CONFIG_DIRECTORY

#: HKCU Run value name written by the tray's Start at login toggle.
STARTUP_VALUE_NAME = PRODUCT_NAME

STARTUP_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

#: Names the one process allowed to own the controller for a desktop session.
SINGLE_INSTANCE_MUTEX = r"Local\Astrolabe.Controller.v1"


# --- Legacy identity, still recognized ------------------------------------------------------------

#: The %APPDATA% configuration directory earlier builds wrote. `paths` migrates it to
#: CONFIG_DIRECTORY and then keeps it in place, unmodified, as the rollback copy.
LEGACY_CONFIG_DIRECTORY = "TrackballDaemon"

#: Where earlier builds wrote the Start at login registration. Recognized so an existing
#: registration survives the rename instead of silently switching itself off.
LEGACY_STARTUP_VALUE_NAME = "TrackballDaemon"

#: The single-instance mutex earlier builds took. A new build holds this name as well as
#: SINGLE_INSTANCE_MUTEX; holding only the new name would let an old and a new build own the
#: controller at the same time.
LEGACY_SINGLE_INSTANCE_MUTEX = r"Local\TrackballDaemon.Controller.v1"

#: The same legacy root as a user would see it written, for prose that has to name it.
LEGACY_CONFIG_DIRECTORY_DISPLAY = "%APPDATA%\\" + LEGACY_CONFIG_DIRECTORY


# --- release channel ------------------------------------------------------------------------------

#: The channel a pre-release build belongs to: a private artifact for daily driving, not a product.
INTERNAL_ALPHA_CHANNEL = "internal-alpha"

#: The channel a final release belongs to.
PUBLIC_CHANNEL = "public"

#: The first version allowed to claim the public channel. Reserving it means no 0.x build can ever
#: be mistaken for V1, however it was labelled downstream.
FIRST_PUBLIC_VERSION = (1, 0, 0)

#: PEP 440, narrowed to the shapes this project actually publishes: `0.2.0a1`, `1.0.0rc2`, `1.0.0`.
_VERSION = re.compile(r"(?P<release>\d+\.\d+\.\d+)(?:(?P<phase>a|b|rc)(?P<serial>\d+))?$")

#: Windows build target recorded in the release manifest. One value because there is one target.
BUILD_TARGET = "windows-x64"


def parse_version(version):
    """Split a version into ``((major, minor, patch), phase, serial)``; phase is None when final."""
    match = _VERSION.fullmatch(str(version).strip())
    if match is None:
        raise ValueError(f"not a version this project publishes: {version!r}")
    release = tuple(int(part) for part in match.group("release").split("."))
    phase = match.group("phase")
    return release, phase, (int(match.group("serial")) if phase else None)


def release_channel(version):
    """Which channel `version` belongs to, derived rather than declared.

    The pre-release segment already states whether a build is private, so reading the channel from it
    removes the possibility of a manifest that says "public" over a version that says otherwise.

    Two version shapes raise rather than resolve, because both mean a policy question is unanswered
    and a provenance record must not guess at it:

    * a final release below `FIRST_PUBLIC_VERSION` means the reserved-for-V1 rule was broken;
    * a beta or release candidate belongs to a channel this project has not defined. Labelling one
      "internal-alpha" would understate it and "public" would overstate it, so the channel has to be
      decided before such a version can be built.
    """
    release, phase, _serial = parse_version(version)
    if phase == "a":
        return INTERNAL_ALPHA_CHANNEL
    if phase is not None:
        raise ValueError(
            f"{version} is a {phase!r} pre-release, and no channel is defined for one; define it "
            "here before building, rather than letting a manifest record a channel by accident")
    if release < FIRST_PUBLIC_VERSION:
        raise ValueError(
            f"{version} is a final release below {'.'.join(map(str, FIRST_PUBLIC_VERSION))}, which "
            "is reserved for public V1; use a pre-release segment such as 0.2.0a1")
    return PUBLIC_CHANNEL


def archive_name(version):
    return ARCHIVE_BASENAME.format(version=version)


def installer_name(version):
    return INSTALLER_BASENAME.format(version=version)


def windows_file_version(version):
    """Return the numeric four-part version required by Windows executable metadata."""
    release, phase, serial = parse_version(version)
    build = serial if phase is not None else 0
    return ".".join(str(part) for part in (*release, build))
