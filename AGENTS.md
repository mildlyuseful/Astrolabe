# Astrolabe agent guide

This file is a task router. Read [`docs/architecture.md`](docs/architecture.md) for the shared current
firmware, daemon, state, mapping, routing, and integration contracts. Do not duplicate those contracts
here.

## Before changing code

1. Inspect the working tree and preserve unrelated user changes.
2. Read the source of truth and the matching guide from the table below.
3. Check [`TODO.md`](TODO.md) for known defects, deferred work, and open live-verification gaps.
4. Update behavior, focused tests, and the owning current documentation together.

## Task router

| Task | Read and update |
|---|---|
| Cross-component architecture, ownership, data flow, or invariants | [`docs/architecture.md`](docs/architecture.md) |
| User capabilities, installation, operation, or troubleshooting | [`README.md`](README.md) |
| Open defects, deferred work, release blockers, or unverified behavior | [`TODO.md`](TODO.md) |
| Firmware, BLE transport, input snapshots, or device descriptors | The active firmware source and [`docs/ble_device_adapters.md`](docs/ble_device_adapters.md) |
| Settings, defaults, config resolution, or migration | `trackball_daemon/settings_schema.py`, `trackball_daemon/app_registry.py`, `trackball_daemon/config_store.py`, `trackball_daemon/config_resolver.py`, packaged default data, and [`docs/default_profiles.md`](docs/default_profiles.md) |
| Input providers, chords, actions, dependencies, or live control state | `trackball_daemon/input/`, `trackball_daemon/commands.py`, `trackball_daemon/runtime_state.py`, and [`docs/keybindings.md`](docs/keybindings.md) |
| App identity, capabilities, focus selection, or feature exposure | `trackball_daemon/app_registry.py`, `trackball_daemon/settings_schema.py`, and [`docs/feature_parity.md`](docs/feature_parity.md) |
| A specific host integration, installer, camera model, or host API | The matching guide under [`docs/apps/`](docs/apps/) plus its driver/add-on and `trackball_daemon/integrations.py` |
| Permissions, listeners, COM, Raw Input, certificates, trust, or reversal | [`docs/security.md`](docs/security.md) |
| Build artifacts, release gates, or manual/live verification claims | [`docs/release_verification.md`](docs/release_verification.md) |
| Investigation results, rejected approaches, or retired implementations | [`archive/`](archive/) |
| UI design prototypes | `ui_demo/`; prototypes are not daemon runtime sources |

## Working rules

- Code and packaged data are authoritative. Documentation explains current contracts and earned
  behavior that code alone cannot establish.
- `trackball_daemon/app_registry.py` is the only supported-app identity, order, transport, and
  capability registry. `trackball_daemon/settings_schema.py` is the stable setting and command
  registry. Do not create parallel tables.
- Put shared invariants in `docs/architecture.md`, host-specific facts in `docs/apps/`, user
  instructions in `README.md`, and unresolved work only in `TODO.md`.
- Keep evergreen docs free of copied release numbers, test counts, dated execution logs, completed
  phase history, and "next action" ledgers.
- Treat automated, mocked, headless-host, and live-viewport verification as distinct claims. Never
  describe one as another.
- Do not edit user configuration or installed host copies as the source of a fix. Change repository
  source or packaged data and exercise the supported setup/update path.
- Preserve compatibility and safe release behavior across config migration, provider loss, focus
  changes, profile reload, owner replacement, and shutdown.
