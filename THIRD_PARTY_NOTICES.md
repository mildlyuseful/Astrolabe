# Third-party notices

Astrolabe's release artifacts embed third-party software. This file attributes every such
component and points to its license text. It covers what is *shipped* — the wheel, the source
distribution, the onedir tree, the release ZIP, the installer, and production-firmware UF2/ELF
artifacts. Tools used only to build a release, such as Nuitka's build driver, pytest, west, and the
CycloneDX generator, are not distributed and are not attributed here;
[`third_party.json`](third_party.json) is the machine-readable record and `tools/audit_notices.py`
fails a desktop build when a newly bundled Python component has no disposition. Firmware has a
separate link-map-derived component section in that record and a build-generated SPDX inventory.

This file is deliberately separate from [`NOTICE`](NOTICE). Apache-2.0 section 4(d) propagates
`NOTICE` into derivative works, so it stays a short attribution notice for Astrolabe itself, and
third-party texts live here.

License identifiers and copyright lines are taken from each component's own published metadata and
license files, not from a summary. Where a project's metadata and its shipped license text disagree,
the shipped text is what is recorded.

## Prominent notice: this product uses pystray, which is LGPL-3.0-or-later

Astrolabe uses **pystray** for its system-tray icon. pystray is free software licensed under the GNU
Lesser General Public License, version 3 or (at your option) any later version. Astrolabe as a whole
remains licensed under Apache-2.0; combining the two is what the LGPL is designed to permit.

Because a release artifact is a combined work that embeds pystray:

- pystray's copyright notice and license are reproduced below and in
  [`LICENSES/LGPL-3.0.txt`](LICENSES/LGPL-3.0.txt);
- a copy of the GNU General Public License, which the LGPL incorporates by reference, is included at
  [`LICENSES/GPL-3.0.txt`](LICENSES/GPL-3.0.txt);
- you have the right to modify pystray and relink it into the application. Astrolabe's complete
  source and its exact dependency lock are published at
  <https://github.com/mildlyuseful/Astrolabe>, so a modified pystray can be substituted and the
  application rebuilt from source.

pystray is the only copyleft component in the desktop application artifacts. The firmware's GCC
runtime attribution, including the GCC Runtime Library Exception, is recorded separately below.

## Components in release artifacts

| Component | License | Copyright | Text | Source |
|---|---|---|---|---|
| bleak | MIT | Copyright (c) 2020, Henrik Blidh | [text](LICENSES/third-party/bleak.txt) | [hbldh/bleak](https://github.com/hbldh/bleak) |
| cffi | MIT | Copyright (c) 2012-2018, Armin Rigo, Maciej Fijalkowski | [text](LICENSES/third-party/cffi.txt) | [python-cffi/cffi](https://github.com/python-cffi/cffi) |
| cryptography | Apache-2.0 | Copyright (c) Individual contributors to the cryptography project | [text](LICENSES/third-party/cryptography.txt), [Apache-2.0](LICENSE) | [pyca/cryptography](https://github.com/pyca/cryptography) |
| hidapi | BSD-3-Clause | Copyright (c) 2011, Gary Bishop | [text](LICENSES/third-party/hidapi.txt) | [trezor/cython-hidapi](https://github.com/trezor/cython-hidapi) |
| Pillow | MIT-CMU | Copyright (c) 1997-2011 Secret Labs AB, 1995-2011 Fredrik Lundh; Pillow copyright (c) 2010-2025 Jeffrey A. Clark and contributors | [text](LICENSES/third-party/pillow.txt) | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) |
| pycparser | BSD-3-Clause | Copyright (c) 2008-2022, Eli Bendersky | [text](LICENSES/third-party/pycparser.txt) | [eliben/pycparser](https://github.com/eliben/pycparser) |
| pystray | LGPL-3.0-or-later | Copyright (C) 2016-2022 Moses Palmer | [LGPL-3.0](LICENSES/LGPL-3.0.txt), [GPL-3.0](LICENSES/GPL-3.0.txt) | [moses-palmer/pystray](https://github.com/moses-palmer/pystray) |
| pywin32 | BSD-3-Clause | Copyright (c) 1994-2008, Mark Hammond | [text](LICENSES/third-party/pywin32.txt) | [mhammond/pywin32](https://github.com/mhammond/pywin32) |
| six | MIT | Copyright (c) 2010-2024 Benjamin Peterson | [text](LICENSES/third-party/six.txt) | [benjaminp/six](https://github.com/benjaminp/six) |
| typing_extensions | PSF-2.0 | Copyright (c) 2001-2025 Python Software Foundation | [text](LICENSES/third-party/typing-extensions.txt) | [python/typing_extensions](https://github.com/python/typing_extensions) |
| winrt-runtime and the `winrt-Windows.*` projection packages | MIT | Copyright (c) Microsoft Corporation; Copyright (c) 2021-2025 David Lechner | [text](LICENSES/third-party/pywinrt.txt) | [pywinrt/pywinrt](https://github.com/pywinrt/pywinrt) |

Eight `winrt-Windows.*` packages ship — Devices.Bluetooth, Devices.Bluetooth.Advertisement,
Devices.Bluetooth.GenericAttributeProfile, Devices.Enumeration, Foundation,
Foundation.Collections, and Storage.Streams, plus `winrt-runtime`. They are one upstream project
under one license and are listed together.

### Components embedded inside those components

- **OpenSSL**, Apache-2.0. The `cryptography` Windows wheel statically links OpenSSL 3.x.
- **Pillow's bundled codecs.** Pillow's license text above includes the licenses of the image
  libraries its wheel bundles; they are reproduced in full in that file rather than summarized.

## Runtime embedded by the packaged build

The Nuitka onedir build embeds components that are not Python distributions and cannot be
enumerated from the dependency lock:

| Component | License | Copyright | Source |
|---|---|---|---|
| CPython | PSF-2.0 | Copyright (c) 2001-2025 Python Software Foundation. All rights reserved. | <https://github.com/python/cpython> |
| Tcl/Tk | TCL | Copyright (c) 1991-1994 The Regents of the University of California; 1994-1998 Sun Microsystems, Inc.; 1998-2000 Scriptics Corporation; and other parties | <https://www.tcl-lang.org/> |
| Nuitka runtime support | Apache-2.0 | Copyright Kay Hayen | <https://github.com/Nuitka/Nuitka> |
| OpenSSL 3 | Apache-2.0 | Copyright 1998-2025 The OpenSSL Project Authors. All Rights Reserved. | <https://openssl-library.org/source/license/> |
| libffi | MIT | Copyright (c) 1996-2008 Red Hat, Inc and others. | <https://docs.python.org/3.13/license.html#libffi> |
| Microsoft Visual C++ Runtime | Microsoft Software License Terms | Copyright Microsoft Corporation. | <https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files> |
| zlib | Zlib | Copyright (c) 1995-2011 Jean-loup Gailly and Mark Adler. | <https://docs.python.org/3.13/license.html#zlib> |

Tcl/Tk is embedded because the settings window and control panel are Tkinter. Nuitka is a build
tool, but its runtime support code is compiled into the produced executable. OpenSSL and libffi are
the dynamic libraries carried by the pinned CPython distribution for its standard-library TLS,
hashing, and `ctypes` extensions. The application-local Visual C++ runtime files remain subject to
Microsoft's redistribution terms. Official CPython also carries a dynamic zlib library; supported
standalone interpreter layouts may link that component without a separate DLL.

`tools/audit_native_binaries.py` walks the completed onedir tree and requires every DLL, PYD, and EXE
to match exactly one component rule in `third_party.json`. The release build runs that artifact-level
audit after Nuitka and explicit interpreter-runtime staging.

## Production firmware

The ZMK UF2 and ELF contain the following third-party code. The linked set comes from the firmware
link map and compile database, not from everything fetched by the pinned west manifest. Each
firmware artifact also carries Zephyr-generated SPDX tag/value documents for the application,
Zephyr, build products, and SDK alongside this manual notice bundle.

| Component | License | Copyright | Text | Source |
|---|---|---|---|---|
| ZMK | MIT | Copyright (c) 2020 The ZMK Contributors | [text](LICENSES/third-party/zmk.txt) | [zmkfirmware/zmk](https://github.com/zmkfirmware/zmk) |
| Zephyr | Apache-2.0 | The Zephyr Project contributors and other file-level holders named in linked source | [text](LICENSES/third-party/zephyr.txt) | [zephyrproject-rtos/zephyr](https://github.com/zephyrproject-rtos/zephyr) |
| nrfx | BSD-3-Clause | Copyright (c) 2017-2024, Nordic Semiconductor ASA | [text](LICENSES/third-party/nrfx.txt) | [NordicSemiconductor/nrfx](https://github.com/NordicSemiconductor/nrfx/tree/v3.6.0) |
| TinyCrypt | BSD-3-Clause | Copyright (c) 2017, Intel Corporation. All rights reserved. | [TinyCrypt and micro-ecc texts](LICENSES/third-party/tinycrypt.txt) | [intel/tinycrypt](https://github.com/intel/tinycrypt) |
| micro-ecc | BSD-2-Clause | Copyright (c) 2013, Kenneth MacKay. All rights reserved. | [TinyCrypt and micro-ecc texts](LICENSES/third-party/tinycrypt.txt) | [kmackay/micro-ecc](https://github.com/kmackay/micro-ecc) |
| CMSIS | Apache-2.0 | Copyright (c) 2009-2021 Arm Limited. All rights reserved. | [text](LICENSES/third-party/cmsis.txt) | [ARM-software/CMSIS_5](https://github.com/ARM-software/CMSIS_5) |
| Picolibc/Newlib runtime | Per-file permissive Picolibc/Newlib license set | Keith Packard, The Newlib Project, and the additional holders reproduced in the texts | [Picolibc inventory](LICENSES/third-party/picolibc.txt), [Newlib notices](LICENSES/third-party/newlib.txt) | [picolibc/picolibc](https://github.com/picolibc/picolibc) |
| GCC libgcc runtime | GPL-3.0-only WITH GCC-exception-3.1 | Free Software Foundation, Inc. and GCC contributors | [GPL-3.0 source text](LICENSES/third-party/gcc-gpl-3.0.txt), [Runtime Library Exception 3.1](LICENSES/third-party/gcc-runtime-library-exception-3.1.txt) | [GCC](https://gcc.gnu.org/git/gcc.git) |

The linked Picolibc/Newlib archive members keep their per-file permissive terms. The complete
Picolibc inventory is reproduced because the prebuilt SDK archive does not expose a smaller source
bundle; entries in that inventory for GPL or AGPL build scripts, tests, and helpers are not linked
firmware runtime and are not described as such. `COPYING.GPL2` is therefore not part of the binary
notice bundle. The libgcc objects are under GPL-3.0-only with the GCC Runtime Library Exception 3.1,
whose additional permission applies to eligible target code linked with the runtime.

CMSIS contributes compiled-through headers rather than a separate prebuilt library. TinyCrypt's
bundled `README.zephyr` contains both Intel's three-clause notice and Kenneth MacKay's distinct
two-clause micro-ecc notice; both are retained verbatim. Manifest-only projects such as mbedTLS,
nanopb, and ZMK Studio are not listed as linked binary components. The frozen west manifest retained
with the artifact still records that those sources were fetched.

## Keeping this current

`tools/audit_notices.py` compares this file and `third_party.json` against the distributions
actually installed into a release runtime environment. A dependency that starts shipping, or one
that stops, fails the audit until both files are updated. `tools/audit_native_binaries.py`
independently does the same for compiled files in the completed onedir artifact.
Firmware changes additionally regenerate and retain Zephyr's SPDX inventory; the manual firmware
table remains required for linked third-party notices that build-graph scanning alone cannot
resolve reliably.
