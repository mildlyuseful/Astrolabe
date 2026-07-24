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

*Legacy* values are the identity currently written on disk under the project's original name. They
are still authoritative until a migration moves them, and that migration must carry existing users
across rather than stranding their configuration. `TODO.md` tracks it.
"""

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

#: The configuration root this product is moving to, relative to %APPDATA%.
CONFIG_DIRECTORY = r"Mildly Useful\Astrolabe"


# --- Legacy identity, still authoritative on disk ------------------------------------------------

#: Current %APPDATA% configuration directory. Real user configuration lives here today, so it stays
#: authoritative until a staged migration validates a copy at CONFIG_DIRECTORY.
LEGACY_CONFIG_DIRECTORY = "TrackballDaemon"

#: Current HKCU Run value name written by the tray's Start at login toggle.
LEGACY_STARTUP_VALUE_NAME = "TrackballDaemon"

#: Current single-instance mutex. Renaming it without also holding the old name would let an old
#: and a new build own the controller at the same time.
LEGACY_SINGLE_INSTANCE_MUTEX = r"Local\TrackballDaemon.Controller.v1"

STARTUP_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def archive_name(version):
    return ARCHIVE_BASENAME.format(version=version)


def installer_name(version):
    return INSTALLER_BASENAME.format(version=version)
