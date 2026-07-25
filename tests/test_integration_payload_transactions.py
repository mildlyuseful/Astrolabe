# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from trackball_daemon import integrations


def _write_tree(root: Path, files):
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def test_transactional_payload_swap_validates_and_removes_obsolete_files(tmp_path):
    source = tmp_path / "bundled"
    destination = tmp_path / "installed"
    _write_tree(source, {"main.py": "new", "nested/data.json": "{}"})
    _write_tree(destination, {"main.py": "old", "obsolete.py": "remove me"})

    report, = integrations._install_payloads_transactionally(((source, destination),))

    assert (destination / "main.py").read_text(encoding="utf-8") == "new"
    assert (destination / "nested" / "data.json").exists()
    assert not (destination / "obsolete.py").exists()
    assert report.replaced_existing is True
    assert report.obsolete_files_removed == ("obsolete.py",)
    assert report.retained_backup is None
    assert not destination.with_name(".installed.astrolabe-stage").exists()
    assert not destination.with_name(".installed.astrolabe-backup").exists()


def test_staging_validation_failure_does_not_touch_installed_payload(
        tmp_path, monkeypatch):
    source = tmp_path / "bundled"
    destination = tmp_path / "installed"
    _write_tree(source, {"main.py": "new"})
    _write_tree(destination, {"main.py": "old"})
    real_copytree = integrations.shutil.copytree

    def corrupt_copy(source_path, stage_path):
        result = real_copytree(source_path, stage_path)
        (Path(stage_path) / "main.py").write_text("corrupt", encoding="utf-8")
        return result

    monkeypatch.setattr(integrations.shutil, "copytree", corrupt_copy)

    with pytest.raises(OSError, match="staged payload validation failed"):
        integrations._install_payloads_transactionally(((source, destination),))

    assert (destination / "main.py").read_text(encoding="utf-8") == "old"
    assert not destination.with_name(".installed.astrolabe-stage").exists()
    assert not destination.with_name(".installed.astrolabe-backup").exists()


def test_group_staging_failure_removes_every_completed_stage(tmp_path, monkeypatch):
    first_source = tmp_path / "first.py"
    first_source.write_text("first", encoding="utf-8")
    second_source = tmp_path / "second.py"
    second_source.write_text("second", encoding="utf-8")
    first_destination = tmp_path / "installed" / "first.py"
    second_destination = tmp_path / "installed" / "second.py"
    real_copy2 = integrations.shutil.copy2

    def fail_second_copy(source_path, stage_path):
        if Path(source_path) == second_source:
            raise OSError("second stage failed")
        return real_copy2(source_path, stage_path)

    monkeypatch.setattr(integrations.shutil, "copy2", fail_second_copy)

    with pytest.raises(OSError, match="second stage failed"):
        integrations._install_payloads_transactionally((
            (first_source, first_destination),
            (second_source, second_destination),
        ))

    assert not first_destination.exists()
    assert not second_destination.exists()
    assert not list((tmp_path / "installed").glob(".*.astrolabe-stage"))


def test_swap_failure_rolls_back_every_destination(tmp_path, monkeypatch):
    source_file = tmp_path / "bundled_loader.py"
    source_file.write_text("new loader", encoding="utf-8")
    source_dir = tmp_path / "bundled_addon"
    _write_tree(source_dir, {"main.py": "new addon"})
    destination_file = tmp_path / "Plugins" / "loader.py"
    destination_file.parent.mkdir()
    destination_file.write_text("old loader", encoding="utf-8")
    destination_dir = tmp_path / "Plugins" / "addon"
    _write_tree(destination_dir, {"main.py": "old addon"})
    real_replace = integrations.os.replace
    failed = False

    def fail_second_install(source_path, destination_path):
        nonlocal failed
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if (not failed and source_path.name == ".addon.astrolabe-stage"
                and destination_path == destination_dir):
            failed = True
            raise OSError("simulated interrupted swap")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(integrations.os, "replace", fail_second_install)

    with pytest.raises(OSError, match="simulated interrupted swap"):
        integrations._install_payloads_transactionally((
            (source_file, destination_file),
            (source_dir, destination_dir),
        ))

    assert destination_file.read_text(encoding="utf-8") == "old loader"
    assert (destination_dir / "main.py").read_text(encoding="utf-8") == "old addon"
    assert not list((tmp_path / "Plugins").glob(".*.astrolabe-*"))


def test_next_run_recovers_interrupted_backup_before_staging(tmp_path, monkeypatch):
    source = tmp_path / "bundled"
    _write_tree(source, {"main.py": "new"})
    destination = tmp_path / "installed"
    backup = destination.with_name(".installed.astrolabe-backup")
    stage = destination.with_name(".installed.astrolabe-stage")
    _write_tree(backup, {"main.py": "old"})
    _write_tree(stage, {"partial.py": "partial"})

    def fail_staging(*_args, **_kwargs):
        raise OSError("stop after recovery")

    monkeypatch.setattr(integrations.shutil, "copytree", fail_staging)

    with pytest.raises(OSError, match="stop after recovery"):
        integrations._install_payloads_transactionally(((source, destination),))

    assert (destination / "main.py").read_text(encoding="utf-8") == "old"
    assert not backup.exists()
    assert not stage.exists()
