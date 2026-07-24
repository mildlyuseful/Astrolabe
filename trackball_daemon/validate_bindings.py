# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Offline contributor validator for packaged profiles and optional sparse overrides."""

import argparse
import json
from pathlib import Path

from .input.bindings import (
    SYSTEM_INPUT_PROFILE_IDS,
    compile_binding_profile,
    compose_binding_profile,
    load_system_binding_profiles,
)


def validate(*, profiles_path=None, config_path=None):
    catalog = load_system_binding_profiles(profiles_path)
    overrides = {profile_id: {} for profile_id in SYSTEM_INPUT_PROFILE_IDS}
    if config_path is not None:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        candidate = data.get("keybinding_overrides")
        if not isinstance(candidate, dict):
            raise ValueError("config has no keybinding_overrides object")
        overrides = candidate
    result = {}
    for profile_id in SYSTEM_INPUT_PROFILE_IDS:
        profile = compose_binding_profile(catalog, profile_id, overrides[profile_id])
        compiled = compile_binding_profile(profile, catalog)
        result[profile_id] = {
            "enabled_bindings": len(compiled.bindings),
            "diagnostics": [
                {
                    "binding_id": item.binding_id,
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                }
                for item in compiled.diagnostics
            ],
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate keybinding profiles without starting the daemon")
    parser.add_argument("--profiles", type=Path,
                        help="alternate developer-owned system profile JSON")
    parser.add_argument("--config", type=Path,
                        help="optional daemon config containing sparse overrides")
    args = parser.parse_args(argv)
    try:
        result = validate(profiles_path=args.profiles, config_path=args.config)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"binding validation failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 1 if any(row["diagnostics"] for row in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
