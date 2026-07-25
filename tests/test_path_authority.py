# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The config root moves once, and no failure along the way may cost a user their settings.

`trackball_daemon/paths.py` renames the per-user configuration directory. The rules it has to hold
are all consequences of one fact: the directory it is reading may be the only copy of a user's
bindings, profiles, and the Onshape certificate they have already trusted in their Windows store.

So: an existing new root always wins, a partial copy can never become the new root, the legacy
directory is only ever read, and any failure leaves both locations exactly as they were.
"""

import json
from pathlib import Path
import sys

import pytest

from trackball_daemon import paths, product


def _tree(directory: Path):
    """Every file under `directory`, as {relative path: bytes}."""
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def _seed_legacy(root: Path):
    """A legacy directory holding the awkward parts: nesting, binary, and a live log."""
    legacy = root / product.LEGACY_CONFIG_DIRECTORY
    (legacy / "acad_plugin").mkdir(parents=True)
    (legacy / "unity" / "com.astrolabe.trackball-nav").mkdir(parents=True)
    (legacy / "config.json").write_text('{"version": 9}', encoding="utf-8")
    (legacy / "onshape_cert.pem").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    (legacy / "acad_plugin" / "TrackballNavAcad.dll").write_bytes(bytes(range(256)))
    (legacy / "unity" / "com.astrolabe.trackball-nav" / "package.json").write_text(
        '{"version": "0.1.16"}', encoding="utf-8")
    (legacy / "daemon.log").write_text("09:00:00 INFO started\n", encoding="utf-8")
    (legacy / "daemon.log.1").write_text("08:00:00 INFO started\n", encoding="utf-8")
    (legacy / "blender_addin.log").write_text("09:00:01 boot\n", encoding="utf-8")
    return legacy


@pytest.fixture(autouse=True)
def _forget_failed_migrations():
    """The failure memo is process-global; a test must not inherit another test's verdict."""
    paths._failed_migrations.clear()
    yield
    paths._failed_migrations.clear()


def test_a_fresh_machine_gets_the_current_root_and_nothing_else(isolated_config):
    resolved = paths.user_config_dir()

    assert resolved == paths.canonical_config_dir()
    assert resolved.is_dir()
    assert not paths.legacy_config_dir().exists(), (
        "a machine that never ran an earlier build must not get a directory named after one")


def test_an_earlier_builds_directory_is_carried_across_intact(isolated_config):
    legacy = _seed_legacy(isolated_config)
    before = _tree(legacy)

    resolved = paths.user_config_dir()

    assert resolved == paths.canonical_config_dir()
    carried = _tree(resolved)
    for relative, content in before.items():
        if relative.endswith(".log") or ".log." in relative:
            continue
        assert carried[relative] == content, relative
    assert (resolved / "unity" / "com.astrolabe.trackball-nav" / "package.json").is_file()


def test_the_legacy_directory_is_read_and_never_written(isolated_config):
    """Rollback is deleting the new directory, which only works if the old one is untouched."""
    legacy = _seed_legacy(isolated_config)
    before = _tree(legacy)

    paths.user_config_dir()

    assert _tree(legacy) == before


def test_rotating_logs_stay_behind_and_stay_readable(isolated_config):
    """Logs are the only files something else may append to mid-copy, and the old ones stay put."""
    legacy = _seed_legacy(isolated_config)

    resolved = paths.user_config_dir()

    assert not (resolved / "daemon.log").exists()
    assert not (resolved / "daemon.log.1").exists()
    assert not (resolved / "blender_addin.log").exists()
    assert (legacy / "daemon.log").read_text(encoding="utf-8") == "09:00:00 INFO started\n"


def test_the_migration_records_where_the_state_came_from(isolated_config):
    _seed_legacy(isolated_config)

    record = json.loads(
        (paths.user_config_dir() / paths.MIGRATION_RECORD).read_text(encoding="utf-8"))

    assert record["migrated_from"] == str(paths.legacy_config_dir())
    assert "config.json" in record["verified_files"]
    assert not any(name.endswith(".log") for name in record["verified_files"])


def test_a_legacy_file_named_like_the_record_is_not_displaced(isolated_config):
    legacy = _seed_legacy(isolated_config)
    (legacy / paths.MIGRATION_RECORD).write_text("mine, not yours", encoding="utf-8")

    resolved = paths.user_config_dir()

    assert (resolved / paths.MIGRATION_RECORD).read_text(encoding="utf-8") == "mine, not yours"


def test_an_existing_current_root_wins_and_the_legacy_one_is_not_consulted(isolated_config):
    _seed_legacy(isolated_config)
    canonical = paths.canonical_config_dir()
    canonical.mkdir(parents=True)
    (canonical / "config.json").write_text('{"version": 9, "mine": true}', encoding="utf-8")

    resolved = paths.user_config_dir()

    assert resolved == canonical
    assert json.loads((resolved / "config.json").read_text(encoding="utf-8"))["mine"] is True
    assert not (resolved / "onshape_cert.pem").exists(), "nothing may be re-copied over a live root"


def test_unreadable_state_in_the_current_root_does_not_send_the_daemon_back(isolated_config):
    """A failure to load must never be answered by reverting to pre-upgrade configuration."""
    _seed_legacy(isolated_config)
    canonical = paths.canonical_config_dir()
    canonical.mkdir(parents=True)
    (canonical / "config.json").write_text("{ this is not json", encoding="utf-8")

    assert paths.user_config_dir() == canonical


def test_an_empty_legacy_directory_is_not_treated_as_state(isolated_config):
    (isolated_config / product.LEGACY_CONFIG_DIRECTORY).mkdir()

    resolved = paths.user_config_dir()

    assert resolved == paths.canonical_config_dir()
    assert not (resolved / paths.MIGRATION_RECORD).exists()


def test_a_copy_that_does_not_verify_is_discarded_and_the_legacy_root_keeps_serving(
        isolated_config, monkeypatch):
    legacy = _seed_legacy(isolated_config)
    before = _tree(legacy)

    def truncating_copy(source, target):
        Path(target).write_bytes(b"")

    monkeypatch.setattr(paths.shutil, "copy2", truncating_copy)

    resolved = paths.user_config_dir()

    assert resolved == legacy
    assert not paths.canonical_config_dir().exists()
    assert _tree(legacy) == before
    assert not list(isolated_config.glob("**/*.migrating-*")), "the staging copy must be cleaned up"


def test_a_failed_migration_is_not_attempted_again_by_the_same_process(
        isolated_config, monkeypatch):
    _seed_legacy(isolated_config)
    attempts = []

    def counting_copy(source, target):
        attempts.append(source)
        Path(target).write_bytes(b"")

    monkeypatch.setattr(paths.shutil, "copy2", counting_copy)
    paths.user_config_dir()
    first_round = len(attempts)
    assert first_round > 0

    paths.user_config_dir()
    paths.user_config_dir()

    assert len(attempts) == first_round, (
        "re-copying a whole directory on every path lookup would be a per-call disk cost")


def test_a_blocked_destination_leaves_both_locations_alone(isolated_config):
    """The obstruction here is real, not injected: a file where the parent directory must go."""
    legacy = _seed_legacy(isolated_config)
    before = _tree(legacy)
    parent = paths.canonical_config_dir().parent
    parent.write_text("not a directory", encoding="utf-8")

    resolved = paths.user_config_dir()

    assert resolved == legacy
    assert _tree(legacy) == before
    assert parent.read_text(encoding="utf-8") == "not a directory"


def test_symlinked_entries_are_not_followed_or_duplicated(isolated_config):
    """A redirection is the user's arrangement; copying what it points at is not ours to do."""
    legacy = _seed_legacy(isolated_config)
    outside = isolated_config / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("elsewhere", encoding="utf-8")
    try:
        (legacy / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host does not allow creating symlinks without elevation")

    resolved = paths.user_config_dir()

    assert not (resolved / "linked").exists()
    assert (resolved / "config.json").is_file()


def test_config_path_sits_in_whichever_root_was_selected(isolated_config):
    _seed_legacy(isolated_config)

    assert paths.config_path() == paths.canonical_config_dir() / "config.json"


# --- broker discovery -----------------------------------------------------------------------------

def test_a_fresh_machine_publishes_the_broker_port_only_to_the_current_root(isolated_config):
    written = paths.publish_bridge_port(47901)

    assert written == (paths.canonical_config_dir() / paths.BRIDGE_FILENAME,)
    assert json.loads(written[0].read_text(encoding="utf-8")) == {"port": 47901}
    assert not paths.legacy_config_dir().exists()


def test_a_migrated_machine_also_publishes_where_older_add_ons_look(isolated_config):
    """An add-on installed by an earlier build reads the old root and knows nothing about the new."""
    _seed_legacy(isolated_config)

    written = paths.publish_bridge_port(47902)

    assert written == (paths.canonical_config_dir() / paths.BRIDGE_FILENAME,
                       paths.legacy_config_dir() / paths.BRIDGE_FILENAME)
    for path in written:
        assert json.loads(path.read_text(encoding="utf-8")) == {"port": 47902}


def test_a_staged_autocad_plugin_alone_is_enough_to_publish_to_the_legacy_root(isolated_config):
    """The bundled AutoCAD DLL compiles the legacy path in and cannot be taught to probe twice."""
    (paths.canonical_config_dir() / paths.ACAD_PLUGIN_DIRECTORY).mkdir(parents=True)

    written = paths.publish_bridge_port(47903)

    assert paths.legacy_config_dir() / paths.BRIDGE_FILENAME in written


def test_the_bundled_autocad_plugin_reads_the_path_the_daemon_publishes_to():
    resolver = (Path(__file__).resolve().parents[1] / "plugin_src" / "autocad" /
                "TrackballNavAcad" / "BrokerConfig.cs").read_text(encoding="utf-8")

    assert f'"{product.LEGACY_CONFIG_DIRECTORY}", "{paths.BRIDGE_FILENAME}"' in resolver


# --- host add-on payloads -------------------------------------------------------------------------

PAYLOADS = Path(__file__).resolve().parents[1] / "trackball_daemon" / "plugins"
PAYLOAD_SUFFIXES = (".py", ".rb", ".cs", ".gd")

#: Hosts whose add-on is source this project ships and can therefore re-point. AutoCAD is absent on
#: purpose: its add-on is a compiled DLL, and rebuilding it would invalidate the provenance manifest
#: that pins the shipped binary (`TODO.md` carries that work).
PROBING_HOSTS = ("blender", "freecad", "fusion360", "godot", "rhino", "sketchup", "unity", "unreal")


def _payload_sources():
    for path in sorted(PAYLOADS.rglob("*")):
        if path.suffix in PAYLOAD_SUFFIXES and "__pycache__" not in path.parts:
            yield path, path.read_text(encoding="utf-8")


@pytest.mark.parametrize("host", PROBING_HOSTS)
def test_every_shippable_host_add_on_probes_both_configuration_roots(host):
    """Whichever of the add-on and the daemon is older, the two still meet at one discovery file."""
    probing = [path for path, text in _payload_sources()
               if path.relative_to(PAYLOADS).parts[0] == host
               and product.LEGACY_CONFIG_DIRECTORY in text]

    assert probing, f"no {host} payload source resolves a configuration root"


def test_no_payload_names_the_old_root_without_naming_the_current_one_first():
    current, legacy = "Astrolabe", product.LEGACY_CONFIG_DIRECTORY
    for path, text in _payload_sources():
        if legacy not in text:
            continue
        assert current in text, f"{path.name} probes only the previous root"
        assert text.index(current) < text.index(legacy), (
            f"{path.name} must prefer the current root over the previous one")


def test_the_autocad_payload_ships_no_source_that_could_have_been_repointed():
    autocad = [path.name for path, _text in _payload_sources()
               if path.relative_to(PAYLOADS).parts[0] == "autocad"]

    assert autocad == [], f"these are editable and should probe both roots: {autocad}"


# --- Windows registration -------------------------------------------------------------------------

TEST_RUN_KEY = r"Software\Mildly Useful\Astrolabe\test-startup-migration"


@pytest.fixture
def scratch_run_key():
    """A throwaway HKCU subkey, so these tests never touch the real login registration."""
    if sys.platform != "win32":
        pytest.skip("the Run key exists only on Windows")
    import winreg

    winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY).Close()
    yield TEST_RUN_KEY
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_RUN_KEY)
    except OSError:
        pass


def _values(run_key):
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
        count = winreg.QueryInfoKey(key)[1]
        return {winreg.EnumValue(key, index)[0]: winreg.EnumValue(key, index)[1]
                for index in range(count)}


def _set(run_key, name, value):
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def test_an_earlier_registration_is_carried_over_verbatim_and_not_left_duplicated(scratch_run_key):
    from trackball_daemon import tray

    installed = r'"C:\Users\someone\AppData\Local\Programs\Mildly Useful\Astrolabe\Astrolabe.exe"'
    _set(scratch_run_key, product.LEGACY_STARTUP_VALUE_NAME, installed)

    assert tray.migrate_startup_entry(scratch_run_key) == installed
    # Verbatim: regenerating the command from this process would repoint an installed build's login
    # entry at whatever copy happened to run the migration.
    assert _values(scratch_run_key) == {product.STARTUP_VALUE_NAME: installed}
    assert tray.is_startup_enabled(scratch_run_key) is True


def test_migrating_twice_changes_nothing(scratch_run_key):
    from trackball_daemon import tray

    _set(scratch_run_key, product.LEGACY_STARTUP_VALUE_NAME, "old command")
    tray.migrate_startup_entry(scratch_run_key)

    assert tray.migrate_startup_entry(scratch_run_key) is None
    assert _values(scratch_run_key) == {product.STARTUP_VALUE_NAME: "old command"}


def test_a_current_registration_is_not_overwritten_by_a_stale_one(scratch_run_key):
    from trackball_daemon import tray

    _set(scratch_run_key, product.STARTUP_VALUE_NAME, "current command")
    _set(scratch_run_key, product.LEGACY_STARTUP_VALUE_NAME, "stale command")

    tray.migrate_startup_entry(scratch_run_key)

    assert _values(scratch_run_key) == {product.STARTUP_VALUE_NAME: "current command"}


def test_nothing_to_migrate_registers_nothing(scratch_run_key):
    from trackball_daemon import tray

    assert tray.migrate_startup_entry(scratch_run_key) is None
    assert _values(scratch_run_key) == {}
    assert tray.is_startup_enabled(scratch_run_key) is False


def test_turning_start_at_login_off_clears_both_generations_of_the_value(scratch_run_key):
    from trackball_daemon import tray

    _set(scratch_run_key, product.LEGACY_STARTUP_VALUE_NAME, "old command")
    tray.set_startup_enabled(True, scratch_run_key)
    assert set(_values(scratch_run_key)) == {product.STARTUP_VALUE_NAME}

    tray.set_startup_enabled(False, scratch_run_key)

    assert _values(scratch_run_key) == {}
    assert tray.is_startup_enabled(scratch_run_key) is False


def test_an_earlier_registration_alone_still_reads_as_enabled(scratch_run_key):
    """Otherwise the tray checkbox would appear off while Windows still launched the daemon."""
    from trackball_daemon import tray

    _set(scratch_run_key, product.LEGACY_STARTUP_VALUE_NAME, "old command")

    assert tray.is_startup_enabled(scratch_run_key) is True


def test_the_daemon_carries_the_registration_over_at_startup():
    """The migration must not depend on the user finding and toggling the checkbox."""
    source = (Path(__file__).resolve().parents[1] / "trackball_daemon" / "app.py").read_text(
        encoding="utf-8")

    assert "migrate_startup_entry()" in source
