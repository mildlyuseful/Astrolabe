# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The one place that decides where per-user state lives, and how it got there.

Everything the daemon keeps for a user -- configuration, the broker discovery file, the Onshape
certificate the user has already trusted, the staged AutoCAD plugin, staged host add-on copies,
diagnostics -- lives in a single directory under the platform's per-user roaming location. Its name
comes from `product.CONFIG_DIRECTORY`; nothing else may spell a config path out.

Earlier builds wrote that state under the project's original name. This module carries it across the
rename, once, and the shape of that migration is the whole reason this file is more than four lines:

1. An existing new root wins outright. It is never re-derived from the legacy one, and a failure to
   read something inside it never sends the daemon back to the old location -- that is how a
   transient error would silently revert a user's settings to whatever they were before the upgrade.
2. When only the legacy root exists, its contents are copied into a staging directory beside the new
   root, verified byte for byte, and only then moved into place under the real name. The move is a
   single rename, so the new root never exists in a partial state and step 1 does not need a
   separate validity check to lean on.
3. The migration only ever *reads* the legacy directory. It is left exactly as it was, which makes
   rollback "delete the new directory" and makes the old diagnostics still readable afterwards.
4. Any failure leaves both locations untouched and keeps using the legacy one.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from .product import CONFIG_DIRECTORY, LEGACY_CONFIG_DIRECTORY

#: Discovery file. Host add-ons read the broker port from it on every reconnect attempt.
BRIDGE_FILENAME = "bridge.json"

#: Where the AutoCAD plugin is staged for NETLOAD, relative to the config root. The name lives here
#: because the bundled DLL's own path expectations are a fact about this directory layout;
#: `autocad_driver` owns everything else about that plugin.
ACAD_PLUGIN_DIRECTORY = "acad_plugin"

#: Provenance breadcrumb a migration leaves in the new root. Written into the staging copy, so its
#: presence means the same thing the root's own existence does: the copy was complete and verified.
MIGRATION_RECORD = "migration.json"

_STAGING_SUFFIX = ".migrating-"

# Rotating diagnostics: daemon.log, daemon.log.1, blender_addin.log.
_LOG_NAME = re.compile(r"\.log(\.\d+)?$", re.IGNORECASE)

# Legacy roots whose migration already failed in this process. Without this, a root that cannot be
# copied would be copied again on every path lookup for the life of the daemon.
_failed_migrations = set()


def _roaming_base() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base)


def _relative_parts(spec):
    """Split an identity path into components, so a Windows-shaped name nests on any platform."""
    return tuple(part for part in spec.replace("\\", "/").split("/") if part)


def canonical_config_dir() -> Path:
    """Where per-user state belongs. Pure: resolving it creates nothing."""
    return _roaming_base().joinpath(*_relative_parts(CONFIG_DIRECTORY))


def legacy_config_dir() -> Path:
    """Where earlier builds wrote per-user state. Pure: resolving it creates nothing."""
    return _roaming_base().joinpath(*_relative_parts(LEGACY_CONFIG_DIRECTORY))


def user_config_dir() -> Path:
    """The config root to use now, migrating an earlier build's directory on first call."""
    canonical = canonical_config_dir()
    if canonical.is_dir():
        return canonical
    legacy = legacy_config_dir()
    if _holds_state(legacy):
        migrated = migrate_config_root(legacy, canonical)
        if migrated is not None:
            return migrated
        return legacy
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def config_path() -> Path:
    return user_config_dir() / "config.json"


def bridge_publication_paths() -> tuple:
    """Every path the broker port must be published to for add-ons to find it.

    The canonical root is where add-ons look first. The legacy root is added only when an add-on
    that can look nowhere else may exist on this machine, and there are exactly two ways to know
    that: an earlier build's directory is still here, or an AutoCAD plugin has been staged. The
    bundled AutoCAD DLL compiles the legacy path in and cannot be taught to probe twice without
    rebuilding it, which would invalidate its provenance manifest (see `TODO.md`). A machine that
    has neither never gets a directory named after the old product.
    """
    canonical = user_config_dir()
    targets = [canonical / BRIDGE_FILENAME]
    legacy = legacy_config_dir()
    if legacy.is_dir() or (canonical / ACAD_PLUGIN_DIRECTORY).is_dir():
        legacy.mkdir(parents=True, exist_ok=True)
        targets.append(legacy / BRIDGE_FILENAME)
    return tuple(targets)


def publish_bridge_port(port) -> tuple:
    """Write the broker discovery file everywhere an add-on may read it. Returns those paths."""
    payload = json.dumps({"port": port})
    written = []
    for path in bridge_publication_paths():
        with path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
        written.append(path)
    return tuple(written)


# --- migration ------------------------------------------------------------------------------------

def migrate_config_root(legacy: Path, canonical: Path):
    """Carry `legacy` to `canonical` through a staged, verified, single-rename move.

    Returns the directory now holding the state, or None when nothing moved and the caller should
    keep using the legacy one. Never writes to `legacy`, and never leaves a partial `canonical`.
    """
    if legacy in _failed_migrations:
        return None
    staging = canonical.parent / (canonical.name + _STAGING_SUFFIX + str(os.getpid()))
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        # Clear any staging directory an interrupted attempt left behind. Once this one succeeds the
        # code never looks here again, so litter left now would sit in the user's profile forever.
        for orphan in canonical.parent.glob(canonical.name + _STAGING_SUFFIX + "*"):
            shutil.rmtree(orphan, ignore_errors=True)
        copied = _copy_legacy_state(legacy, staging)
        _verify_staged_copy(legacy, staging)
        _write_migration_record(staging, legacy, copied)
        os.rename(staging, canonical)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if canonical.is_dir():
            # Another process completed an equivalent migration; its copy passed the same checks.
            return canonical
        _failed_migrations.add(legacy)
        return None
    return canonical


def _holds_state(directory: Path) -> bool:
    try:
        return directory.is_dir() and any(directory.iterdir())
    except OSError:
        return False


def _legacy_entries(source: Path):
    """Yield ``(relative path, is_directory)`` for everything worth carrying across, parents first.

    Rotating logs are excluded. They are the only files something else may be appending to while
    this runs, so they are the one way a byte-for-byte check could fail over state nobody needs --
    and because the legacy directory is preserved, excluding them loses no history.

    Symlinks and junctions are excluded and not followed. A redirection is the user's arrangement;
    duplicating what it points at is not this migration's business.
    """
    for directory, subdirectories, filenames in os.walk(source):
        here = Path(directory)
        subdirectories[:] = sorted(
            name for name in subdirectories if not (here / name).is_symlink())
        for name in subdirectories:
            yield (here / name).relative_to(source), True
        for name in sorted(filenames):
            entry = here / name
            if entry.is_symlink() or _LOG_NAME.search(name):
                continue
            yield entry.relative_to(source), False


def _copy_legacy_state(source: Path, staging: Path) -> tuple:
    staging.mkdir(parents=True)
    copied = []
    for relative, is_directory in _legacy_entries(source):
        target = staging / relative
        if is_directory:
            target.mkdir(exist_ok=True)
            continue
        shutil.copy2(source / relative, target)
        copied.append(relative)
    return tuple(copied)


def _verify_staged_copy(source: Path, staging: Path) -> None:
    """Fail unless every carried entry is present in the staging copy with identical bytes.

    This runs before the rename, so the new root cannot come into existence holding a partial copy.
    That is what lets `user_config_dir` treat "the new root exists" as "the new root is complete".
    """
    for relative, is_directory in _legacy_entries(source):
        target = staging / relative
        if is_directory:
            if not target.is_dir():
                raise OSError(f"migration did not create directory {relative}")
            continue
        original = source / relative
        if not target.is_file():
            raise OSError(f"migration did not copy {relative}")
        if target.stat().st_size != original.stat().st_size:
            raise OSError(f"migration copied {relative} at the wrong length")
        if _digest(target) != _digest(original):
            raise OSError(f"migration copied {relative} with different contents")


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _write_migration_record(staging: Path, legacy: Path, copied) -> None:
    if (staging / MIGRATION_RECORD).exists():
        return                     # the legacy root already had this name; carried state wins
    record = {
        "migrated_from": str(legacy),
        "migrated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verified_files": sorted(str(relative) for relative in copied),
        "left_behind": "rotating *.log diagnostics, which stay readable in migrated_from",
    }
    (staging / MIGRATION_RECORD).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
