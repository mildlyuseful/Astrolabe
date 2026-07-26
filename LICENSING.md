# License scope

Astrolabe is a mixed-license repository. One root license would either overclaim the terms that
apply to future hardware design source or understate the terms that apply to the software that
exists today, so each artifact type is mapped to exactly one license here.

| License | SPDX identifier | Text |
|---|---|---|
| Apache License 2.0 | `Apache-2.0` | [`LICENSE`](LICENSE) |
| CERN Open Hardware Licence Version 2 - Weakly Reciprocal | `CERN-OHL-W-2.0` | [`LICENSES/CERN-OHL-W-v2.txt`](LICENSES/CERN-OHL-W-v2.txt) |

Both texts are stored unmodified. The Apache copyright notice for this project lives in
[`NOTICE`](NOTICE), not in the license text.

`CERN-OHL-W-2.0` is used as the exact version 2.0 grant. It is not an "or later" grant.

## Apache-2.0 — every tracked path today

Every path currently tracked in this repository is licensed under Apache-2.0.

| Path | Contents |
|---|---|
| `trackball_daemon/` | The daemon, its Python package, packaged default data, schemas, examples, and the host add-on payloads copied into 3D applications |
| `plugin_src/` | AutoCAD .NET plugin source and its navigation-math test project |
| `firmware/` | Device firmware, including future production firmware |
| `installer/` | Per-user Windows installer definition |
| `tools/` | Build, release, calibration, probe, and acceptance tooling |
| `tests/`, `conftest.py`, `cube_test.py` | Automated suite and the standalone cube verification demo |
| `docs/`, `README.md`, `AGENTS.md`, `TODO.md`, `archive/` | Documentation, contributor routing, and retained investigation records |
| `NOTICE`, `LICENSING.md`, `CONTRIBUTING.md`, `SECURITY.md`, `TRADEMARKS.md`, `THIRD_PARTY_NOTICES.md` | This project's own policy documents |
| `licensing.json`, `third_party.json` | Machine-readable license and attribution records |
| `ui_demo/` | Non-shipping UI design prototypes and their first-party assets |
| `.github/`, `.claude/` | CI workflows and repository tooling configuration |
| `pyproject.toml`, `uv.lock`, `MANIFEST.in`, `.gitattributes`, `.gitignore` | Packaging, dependency-lock, and repository configuration |

Firmware is software and stays Apache-2.0 even when it is written for the product controller.

Third-party dependencies are not covered by this mapping. They keep their own upstream licenses and
are attributed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), with their verbatim license
texts under `LICENSES/third-party/` and the machine-readable record in
[`third_party.json`](third_party.json). One of them, pystray, is LGPL-3.0-or-later, which places
obligations on every release artifact; the notices file states them.

### How each file says so

Source files carry the identifier inline, as the first thing in the file after any line the language
requires to come first — a shebang, a Python encoding declaration, a Ruby magic comment, or Godot's
`@tool` annotation:

```python
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0
```

This matters most for the add-on payloads under `trackball_daemon/plugins/`, because setup copies
them into Blender, FreeCAD, Fusion, Rhino, SketchUp, Unity, Godot, and Unreal, where they live
detached from this repository and its `LICENSE` file.

Files that cannot carry a comment, and the two directories that are deliberately exempt, take their
disposition from [`licensing.json`](licensing.json) instead. That file is the machine-readable form
of this document: ordered rules, first match wins, so no path is ambiguous and none is unclaimed.
Two exemptions exist, and each records its reason there:

- `plugin_src/autocad/TrackballNavAcad/**` — the bundled AutoCAD DLL's provenance manifest pins this
  directory's Git tree hash, so editing these files marks the shipped DLL stale until it is rebuilt
  against AutoCAD reference assemblies. Inline headers are added the next time that DLL is rebuilt
  for a real change.
- `ui_demo/**` — non-shipping design prototypes and their assets, which are not daemon runtime
  sources and are not distributed.

`python tools/apply_license_headers.py` adds any missing header; `--check` reports without writing.
The automated suite verifies the result independently rather than trusting that tool.

### Notices that leave with the files

Each add-on payload directory under `trackball_daemon/plugins/` also holds a verbatim copy of
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Setup copies those directories into Blender, FreeCAD,
Fusion, Godot, Rhino, SketchUp, Unity, and Unreal, where they live detached from this repository,
and the AutoCAD runtime folder receives the same two files alongside its compiled DLL — which
cannot carry a header at all. The copies are byte-identical to the originals and the automated
suite fails if one drifts. The single-file Blender startup shim is the one exception: it lands
directly in Blender's own startup folder, where an adjacent licence file would be litter, so its
SPDX header carries the disposition.

Distributed forms carry the full set. The wheel and source distribution get `LICENSE`, `NOTICE`,
`THIRD_PARTY_NOTICES.md`, this file, and everything under `LICENSES/`; the onedir tree, and the ZIP
made from it, get the same files beside the executable.

## CERN-OHL-W-2.0 — hardware design source

No hardware design source is present in this repository yet. The `CERN-OHL-W-2.0` text is added
ahead of that work so the terms are fixed before any design file is published. When hardware design
source is added, these artifact types are licensed under `CERN-OHL-W-2.0` rather than Apache-2.0:

- schematics and PCB layouts;
- editable enclosure and mechanical CAD;
- manufacturing and assembly drawings;
- bill of materials and configuration information;
- fixtures and any other source needed to make or test the product.

The distinction is the preferred form for making modifications to the *hardware*. A file that is
compiled, flashed, or executed remains software under Apache-2.0.

## Documents copied verbatim from elsewhere

These are unmodified copies of documents published by other organizations. They carry their own
terms, and none of them is licensed under this project's terms:

| Path | Document | Published by |
|---|---|---|
| `LICENSE` | Apache License 2.0 | Apache Software Foundation |
| `LICENSES/CERN-OHL-W-v2.txt` | CERN Open Hardware Licence Version 2 - Weakly Reciprocal | CERN |
| `LICENSES/LGPL-3.0.txt`, `LICENSES/GPL-3.0.txt` | GNU Lesser General Public License v3 and GNU General Public License v3 | Free Software Foundation |
| `LICENSES/third-party/*` | The license text each bundled dependency publishes | Their respective authors |
| `DCO` | Developer Certificate of Origin 1.1 | The Linux Foundation |

Each may be copied and distributed verbatim; none may be changed. Automated checks pin their
contents so an accidental edit fails rather than silently altering the terms.

## Related policy

- [`NOTICE`](NOTICE) — the Apache attribution notice carried with every distributed form.
- [`TRADEMARKS.md`](TRADEMARKS.md) — the Astrolabe and Mildly Useful names and logos are not
  granted by either license above.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — inbound contribution licensing and sign-off.
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting.
