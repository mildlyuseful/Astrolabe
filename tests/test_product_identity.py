# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Product identity has exactly one owner, and its frozen values are frozen for good reasons.

An installer, an uninstaller, a startup entry, a single-instance guard, and a configuration
directory that disagree about who the product is are how an upgrade orphans a user's settings or
leaves two copies running. `trackball_daemon/product.py` is the only place any of it is spelled out.
"""

from pathlib import Path
import re

import pytest

from trackball_daemon import paths, product
from trackball_daemon.instance_lock import MUTEX_NAMES
from trackball_daemon.tray import _LEGACY_RUN_NAME, _RUN_NAME


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "trackball_daemon"
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

# Identity strings that must never be typed anywhere but product.py. Prose that merely mentions the
# product is fine; these are the exact values Windows and the filesystem key off.
OWNED_LITERALS = (
    product.APP_USER_MODEL_ID,
    product.INSTALLER_APP_ID,
    product.INSTALL_DIRECTORY,
    product.CONFIG_DIRECTORY,
    product.LEGACY_CONFIG_DIRECTORY,
    product.SINGLE_INSTANCE_MUTEX,
    product.LEGACY_SINGLE_INSTANCE_MUTEX,
    product.STARTUP_REGISTRY_KEY,
)


def _package_sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "product.py" or "plugins" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


@pytest.mark.parametrize("literal", OWNED_LITERALS)
def test_identity_literals_appear_only_in_the_authority(literal):
    offenders = [str(path.relative_to(ROOT)) for path, text in _package_sources()
                 if literal in text]

    assert offenders == [], f"{literal!r} must come from trackball_daemon.product"


def test_consumers_read_their_identity_from_the_authority():
    assert MUTEX_NAMES == (product.SINGLE_INSTANCE_MUTEX, product.LEGACY_SINGLE_INSTANCE_MUTEX)
    assert (_RUN_NAME, _LEGACY_RUN_NAME) == (
        product.STARTUP_VALUE_NAME, product.LEGACY_STARTUP_VALUE_NAME)


@pytest.mark.parametrize("directory, resolve", [
    (product.CONFIG_DIRECTORY, paths.canonical_config_dir),
    (product.LEGACY_CONFIG_DIRECTORY, paths.legacy_config_dir),
])
def test_displayed_config_paths_are_the_ones_actually_resolved(directory, resolve):
    """Help text and documentation name a real path; a drifted one sends users to an empty folder."""
    components = tuple(directory.split("\\"))

    assert resolve().parts[-len(components):] == components
    assert f"%APPDATA%\\{directory}" in (
        product.CONFIG_DIRECTORY_DISPLAY, product.LEGACY_CONFIG_DIRECTORY_DISPLAY)


def test_installer_upgrade_identity_is_a_well_formed_frozen_guid():
    """Inno Setup treats AppId as the upgrade identity; changing it installs a second copy."""
    assert re.fullmatch(
        r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}",
        product.INSTALLER_APP_ID), product.INSTALLER_APP_ID
    assert product.UNINSTALL_KEY.endswith(f"{product.INSTALLER_APP_ID}_is1")


def test_app_user_model_id_is_valid_for_windows():
    assert " " not in product.APP_USER_MODEL_ID
    assert len(product.APP_USER_MODEL_ID) <= 128
    assert product.APP_USER_MODEL_ID.count(".") >= 1


def test_installation_is_per_user_and_needs_no_elevation():
    """A per-user install is what keeps setup out of UAC; Program Files would not be."""
    assert product.INSTALL_DIRECTORY.startswith("Programs\\")
    assert "Program Files" not in product.INSTALL_DIRECTORY
    assert product.PUBLISHER in product.INSTALL_DIRECTORY


def test_distribution_and_import_names_stay_intentionally_distinct():
    assert product.DISTRIBUTION_NAME == "astrolabe-daemon"
    assert product.IMPORT_PACKAGE == "trackball_daemon"
    assert product.DISTRIBUTION_NAME != product.IMPORT_PACKAGE
    assert f'name = "{product.DISTRIBUTION_NAME}"' in PYPROJECT
    assert f'include = ["{product.IMPORT_PACKAGE}*"]' in PYPROJECT


def test_artifact_names_follow_the_documented_pattern():
    assert product.archive_name("0.2.0a1") == "Astrolabe-0.2.0a1-windows-x64.zip"
    assert product.installer_name("1.0.0") == "AstrolabeSetup-1.0.0-windows-x64.exe"


def test_release_build_takes_artifact_naming_from_the_authority():
    build = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")

    assert "from trackball_daemon.product import archive_name" in build
    assert "Astrolabe-$Version-windows-x64.zip" not in build


def test_package_metadata_declares_the_complete_project_surface():
    for field in ("readme =", "authors =", "keywords =", "classifiers =", "requires-python ="):
        assert field in PYPROJECT, field
    for label in ("Homepage", "Source", "Issues", "Documentation", "Security"):
        assert f"{label} = " in PYPROJECT, label
    # The urls sub-table must stay below every plain key of [project]; a table header ends the
    # parent table, so a key after it is silently parsed into the wrong place.
    assert PYPROJECT.index("[project.urls]") > PYPROJECT.index("dependencies = [")
