# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The V1 installer must preserve product identity and the daemon's consent boundaries."""

from pathlib import Path

from trackball_daemon import product


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "installer" / "Astrolabe.iss").read_text(encoding="utf-8")
BUILDER = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")
IDENTITY_TOOL = (ROOT / "tools" / "release_identity.py").read_text(encoding="utf-8")


def test_installer_is_per_user_non_elevating_and_x64():
    assert "PrivilegesRequired=lowest" in INSTALLER
    assert "DefaultDirName={localappdata}\\{#InstallDirectory}" in INSTALLER
    assert "ArchitecturesAllowed=x64compatible" in INSTALLER
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in INSTALLER
    assert "runascurrentuser" not in INSTALLER.lower()


def test_installer_owns_only_application_files_and_shortcuts():
    assert 'Source: "{#SourceDirectory}\\*"' in INSTALLER
    assert 'Filename: "{app}\\{#ExecutableName}"' in INSTALLER
    assert "[Registry]" not in INSTALLER
    assert "TRUSTEDPATHS" not in INSTALLER
    assert "certificate" not in INSTALLER.lower().split("[code]", 1)[0]
    assert "firewall" not in INSTALLER.lower()


def test_installer_preserves_user_data_unless_uninstall_explicitly_requests_removal():
    assert "[UninstallDelete]" not in INSTALLER
    assert "CurUninstallStepChanged" in INSTALLER
    assert "not UninstallSilent" in INSTALLER
    assert "Choose No to preserve them" in INSTALLER
    assert "DelTree(ConfigPath" in INSTALLER


def test_installer_identity_is_projected_from_product_py_by_the_builder():
    expected = {
        "PRODUCT_NAME": product.PRODUCT_NAME,
        "PUBLISHER": product.PUBLISHER,
        "INSTALLER_APP_ID": product.INSTALLER_APP_ID,
        "INSTALL_DIRECTORY": product.INSTALL_DIRECTORY,
        "START_MENU_FOLDER": product.START_MENU_FOLDER,
        "EXECUTABLE_NAME": product.EXECUTABLE_NAME,
        "CONFIG_DIRECTORY": product.CONFIG_DIRECTORY,
        "SINGLE_INSTANCE_MUTEX": product.SINGLE_INSTANCE_MUTEX,
        "LEGACY_SINGLE_INSTANCE_MUTEX": product.LEGACY_SINGLE_INSTANCE_MUTEX,
    }
    for constant, value in expected.items():
        assert f"product.{constant}" in IDENTITY_TOOL, (
            f"identity projection does not read {constant} ({value})")
    assert "tools/release_identity.py" in BUILDER


def test_installer_blocks_upgrade_and_uninstall_while_either_daemon_generation_runs():
    assert "AppMutex={#CurrentMutex},{#LegacyMutex}" in INSTALLER
    assert "CloseApplications=yes" in INSTALLER
    assert "RestartApplications=no" in INSTALLER
