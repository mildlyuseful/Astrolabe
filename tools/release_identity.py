# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Print the release/build projection of the canonical product identity as JSON."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trackball_daemon import __version__
from trackball_daemon import product


def release_identity(version=__version__):
    return {
        "version": version,
        "channel": product.release_channel(version),
        "product_name": product.PRODUCT_NAME,
        "publisher": product.PUBLISHER,
        "distribution_name": product.DISTRIBUTION_NAME,
        "executable_name": product.EXECUTABLE_NAME,
        "archive_name": product.archive_name(version),
        "installer_name": product.installer_name(version),
        "windows_file_version": product.windows_file_version(version),
        "installer_app_id": product.INSTALLER_APP_ID,
        "inno_installer_app_id": product.INSTALLER_APP_ID.replace("{", "{{", 1),
        "install_directory": product.INSTALL_DIRECTORY,
        "start_menu_folder": product.START_MENU_FOLDER,
        "config_directory": product.CONFIG_DIRECTORY,
        "single_instance_mutex": product.SINGLE_INSTANCE_MUTEX,
        "legacy_single_instance_mutex": product.LEGACY_SINGLE_INSTANCE_MUTEX,
    }


def main():
    print(json.dumps(release_identity(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
