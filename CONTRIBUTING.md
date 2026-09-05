# Contributing to Astrolabe

Astrolabe is pre-alpha. There is no packaged public release yet, and interfaces, defaults, and
packaged data can still change. Contributions are welcome anyway, on the terms below.

## Licensing of contributions

Inbound licensing matches outbound licensing. By contributing you agree that your contribution is
licensed under the license that [`LICENSING.md`](LICENSING.md) maps to the path you are changing:

- Apache License 2.0 for software, firmware, tooling, tests, and documentation — that is,
  everything in this repository today;
- CERN Open Hardware Licence Version 2 - Weakly Reciprocal for hardware design source, once such
  files exist.

There is no Contributor License Agreement and none is planned. The project intends to keep the two
licenses above fixed, so a CLA would ask contributors to grant rights the project does not intend to
use. If you are contributing on behalf of an employer, make sure they permit the contribution under
these terms before you send it.

## Sign off every commit

This project uses the [Developer Certificate of Origin](DCO) 1.1. Certify each commit with a
`Signed-off-by` trailer containing a real name and a reachable address:

```bash
git commit -s
```

That produces `Signed-off-by: Your Name <you@example.com>`. Use the same identity consistently.
Sign-off is a statement that you have the right to submit the work under the project's license; it
is not a copyright assignment, and you keep the copyright in your contribution.

Sign-off is required for new commits. Enforcement is not automated yet, so a missing trailer is
caught in review rather than by CI.

## Before you change code

Read [`AGENTS.md`](AGENTS.md) first. It routes each kind of task to its source of truth and its
owning document, and it records the rules that keep this repository maintainable:

- `trackball_daemon/app_registry.py` is the only app identity, order, transport, and capability
  registry, and `trackball_daemon/settings_schema.py` is the only setting and command registry. Do
  not add a parallel table.
- Fix a defect at the layer that owns the broken invariant rather than compensating downstream.
- Shared invariants belong in `docs/architecture.md`, host-specific facts in `docs/apps/`, user
  instructions in `README.md`, and unresolved work only in `TODO.md`.
- Update behavior, focused tests, and the owning document in the same change.

## Two Python floors

The daemon, its tooling, and the tests run on the interpreter `pyproject.toml` declares in
`requires-python`. Use whatever that version offers.

The add-on payloads under `trackball_daemon/plugins/` are different. They never run in Astrolabe's
interpreter — Blender, FreeCAD, Fusion, Rhino, and Unreal execute them inside their own embedded
Python. Their floor is **3.9**, set by Rhino 8, and raising the project's floor does not raise
theirs. The automated suite enforces this separately, so a `match` statement or a PEP 604
annotation in a payload fails the suite even though it is fine everywhere else.

## New files need a license header

Source files carry an SPDX header, and the suite fails without it. After adding files, run:

```powershell
python tools/apply_license_headers.py
```

It inserts the two-line header where [`licensing.json`](licensing.json) requires one, leaves files
that already have it byte-for-byte alone, and keeps a shebang or magic comment in first position.
Use `--check` to see what is missing without writing. If you add a file type that cannot carry a
comment, give it a disposition in `licensing.json` and explain the exemption there —
[`LICENSING.md`](LICENSING.md) has the details.

## Adding a dependency

A dependency that ends up in a release artifact needs a license disposition in
[`third_party.json`](third_party.json), an attribution row in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), and its license text under
`LICENSES/third-party/`. `tools/audit_notices.py` fails the build until all three exist, so check
before you add one — particularly its license. A copyleft dependency changes what every release
artifact must carry.

## Be exact about verification

Automated, mocked, headless-host, and live-viewport results are distinct claims, and this project
treats describing one as another as a defect. If you could not exercise a path — no host installed,
no hardware, no clean account — say so and record the release impact instead of implying a pass.
[`docs/release_verification.md`](docs/release_verification.md) is the durable gate and lists the
exact commands.

## Checks to run

Install Node.js (22 or newer) on PATH for the Onshape userscript lifecycle tests invoked by pytest.
They execute the served JavaScript with mocked browser permissions, requests, and timers; no npm
packages or running Onshape session are needed.

```powershell
python -m pytest -q
python -m compileall -q trackball_daemon tests tools
python -m trackball_daemon.validate_bindings
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj
```

CI runs the same suite against the locked environment, builds the wheel and source distribution, and
smoke-tests the installed wheel outside the checkout. Changes to `plugin_src/autocad/` additionally
invalidate the bundled AutoCAD DLL's provenance manifest; see [`docs/apps/autocad.md`](docs/apps/autocad.md)
before touching that project.

## Security issues

Do not open a public issue for a vulnerability. Follow [`SECURITY.md`](SECURITY.md).

## Names and logos

The Apache and CERN licenses cover code and design source. They do not grant use of the Astrolabe or
Mildly Useful names or logos — see [`TRADEMARKS.md`](TRADEMARKS.md).
