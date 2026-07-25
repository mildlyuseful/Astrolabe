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

*Legacy* values are the identity this project wrote on disk under its original name. They are no
longer where anything is written, but a machine that ran an earlier build still holds them, and so
does every host add-on copy an earlier build installed. They stay here because the frozen identity
is only reachable by carrying that state across, and because a build that stopped recognizing them
would strand it.
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


def archive_name(version):
    return ARCHIVE_BASENAME.format(version=version)


def installer_name(version):
    return INSTALLER_BASENAME.format(version=version)
