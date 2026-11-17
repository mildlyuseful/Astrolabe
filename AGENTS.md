# Working on Astrolabe

Read [`docs/architecture.md`](docs/architecture.md) first: it says what each part owns. Check
[`TODO.md`](TODO.md) for known gaps before assuming something is a bug. Change behaviour, its tests,
and the doc that owns it together.

## Where things live

| Task | Start here |
|---|---|
| Firmware, motion sources, route, transports | [`firmware/README.md`](firmware/README.md), `firmware/module/` |
| The wire protocol | [`docs/protocol.md`](docs/protocol.md) and `firmware/module/include/astrolabe/protocol.h` |
| Pins, geometry, gestures, LED | [`docs/hardware.md`](docs/hardware.md); the shield overlay holds the values |
| A setting, its default, or where it shows | `settings_schema.py`, `system_defaults.json`, `settings_ui_model.py` |
| The settings UI | `trackball_daemon/webui/` (API in `api.py`, pages in `static/`) |
| Bindings, input, live state | `input/`, `commands.py`, `runtime_state.py`, [`docs/keybindings.md`](docs/keybindings.md) |
| An app integration | [`docs/integrations.md`](docs/integrations.md), its guide in [`docs/apps/`](docs/apps/), `integrations.py`, `app_registry.py` |
| Listeners, permissions, reversal | [`docs/security.md`](docs/security.md) |
| Builds and releases | [`docs/releasing.md`](docs/releasing.md) |
| Licensing and notices | [`LICENSING.md`](LICENSING.md), [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## Rules

- **Read the API before theorising about it.** When behaviour does not match what a call obviously
  does, open its source — ZMK, Zephyr, bleak, the host SDK — before proposing a cause. ZMK's mouse
  buttons are reference counted, not idempotent; bleak resolves a bare address by scanning; west
  resolves paths through junctions. Each looked like a design question and was a contract question.
- **Evidence from your own build is not evidence** until you have confirmed the thing you measured
  is the thing you meant to build.
- **One device, no installed base.** Nothing has shipped. Don't write compatibility shims, version
  negotiation, or migrations for states that cannot exist: change the format and every reader in
  the same commit, and reflash. This stops applying at public V1.
- **Fix the layer that owns the broken invariant** and remove the workaround it needed. Prefer
  short, plain code that lets the next contributor add an app, a setting, or a device without
  refactoring. Comments explain why, briefly.
- **Don't regress press jitter.** Control presses and releases must keep reopening the sensor's
  active window (the motion source's wake hook), the MOTION interrupt must only restart a stopped
  poll chain, standalone output classifies whole delivery windows in radians, and buttons are
  latched until sent, driven as edges, and released before a daemon claim. See
  [`docs/hardware.md`](docs/hardware.md).
- **One authority per fact.** `app_registry.py` for apps, `settings_schema.py` for settings,
  `protocol.h` for the wire format, `LICENSING.md` for licenses. Don't build parallel tables.
- **Automated, mocked, and live are different claims.** Never describe one as another.
- **Fix the source, not the install.** Don't edit a user's config or an installed add-on to fix a
  bug; change the repository and go through setup.
- **Firmware builds with `python firmware/build.py`**, which handles the Zephyr workspace. Zephyr
  cannot build from a path with spaces or through a junction; the script works around both.
