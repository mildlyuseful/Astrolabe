# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import sys

from trackball_daemon import __version__, product

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from release_identity import release_identity  # noqa: E402


def test_release_identity_projects_every_installer_and_artifact_authority():
    identity = release_identity()

    assert identity == {
        "version": __version__,
        "channel": product.release_channel(__version__),
        "product_name": product.PRODUCT_NAME,
        "publisher": product.PUBLISHER,
        "distribution_name": product.DISTRIBUTION_NAME,
        "executable_name": product.EXECUTABLE_NAME,
        "archive_name": product.archive_name(__version__),
        "installer_name": product.installer_name(__version__),
        "windows_file_version": product.windows_file_version(__version__),
        "installer_app_id": product.INSTALLER_APP_ID,
        "inno_installer_app_id": product.INSTALLER_APP_ID.replace("{", "{{", 1),
        "install_directory": product.INSTALL_DIRECTORY,
        "start_menu_folder": product.START_MENU_FOLDER,
        "config_directory": product.CONFIG_DIRECTORY,
        "single_instance_mutex": product.SINGLE_INSTANCE_MUTEX,
        "legacy_single_instance_mutex": product.LEGACY_SINGLE_INSTANCE_MUTEX,
    }
