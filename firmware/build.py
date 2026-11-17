#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Build the Astrolabe firmware, and optionally flash an attached board.

    python firmware/build.py                 # build (layout from the attached bootloader, else v7)
    python firmware/build.py --flash         # build and flash, no buttons required
    python firmware/build.py --logging       # add the USB log console and calibration output

Needs Python and the Zephyr SDK (ZEPHYR_SDK_INSTALL_DIR, or ~/zephyr-sdk-*). Everything else --
west, the pinned ZMK/Zephyr checkouts, CMake, Ninja, the flashing tools -- is set up on first run in
a workspace under ~/.cache/astrolabe. Zephyr cannot build from a path containing spaces or through a
junction, so the workspace lives there and this directory's config/ and module/ are mirrored into it.

Flash layout matters: the application must be linked after the SoftDevice the board's bootloader
shipped with (S140 6.x: 0x26000, 7.x: 0x27000), and SuperMini clones differ. An image linked for the
wrong one sits inside the SoftDevice and the board never enumerates. So the layout is read from the
bootloader (UF2 INFO_UF2.TXT), and serial-DFU packages declare the SoftDevice they were linked for,
which the bootloader checks before it writes anything.
"""

import argparse
import os
import re
import shutil
import string
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = (Path.home() / ".cache" / "astrolabe" / "zmk-workspace").resolve()
VENV = WORKSPACE / ".venv"
BIN = VENV / ("Scripts" if os.name == "nt" else "bin")
OUT = HERE / "build"
TOOLS = ("west", "cmake<4", "ninja", "adafruit-nrfutil", "hidapi", "pyserial")

LAYOUTS = {
    # name: (application start, SoftDevice firmware IDs the image may be installed over)
    "s140-v7": (0x27000, (0xCA, 0x100, 0x123)),  # 7.0.1, 7.2.0, 7.3.0
    "s140-v6": (0x26000, (0xA9, 0xAE, 0xB6)),  # 6.0.0, 6.1.0, 6.1.1
}

ZMK_USB = (0x1D50, 0x615E, 0xFF00)  # VID, PID, vendor usage page of the Astrolabe interface
BOOTLOADER_COMMAND = bytes((3, 1, 4, 1, 0))  # report 3: version 1, opcode 4, request 1
SERIAL_DFU = {(0x1209, 0x5284), (0x239A, 0x0029), (0x239A, 0x002A)}  # bootloader CDC ports


def run(*args, **kwargs):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def sdk_dir():
    found = os.environ.get("ZEPHYR_SDK_INSTALL_DIR")
    candidates = [Path(found)] if found else sorted(Path.home().glob("zephyr-sdk-0.16.*"))
    for path in candidates:
        if (path / "cmake" / "Zephyr-sdkConfig.cmake").exists():
            return path.resolve()
    sys.exit("Zephyr SDK 0.16.x not found. Install it from "
             "https://github.com/zephyrproject-rtos/sdk-ng/releases/tag/v0.16.8 (the minimal "
             "bundle plus the arm-zephyr-eabi toolchain) into ~/zephyr-sdk-0.16.8.")


def ensure_tools():
    """Create the workspace venv on first run, then re-run this script inside it."""
    python = BIN / ("python.exe" if os.name == "nt" else "python")
    marker = VENV / "astrolabe-tools.txt"
    if not marker.exists() or marker.read_text() != " ".join(TOOLS):
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        if not python.exists():
            run(sys.executable, "-m", "venv", VENV)
        run(python, "-m", "pip", "install", "-q", *TOOLS)
        marker.write_text(" ".join(TOOLS))
    if Path(sys.prefix).resolve() != VENV:
        sys.exit(subprocess.call([str(python), __file__, *sys.argv[1:]]))


def ensure_workspace():
    manifest = WORKSPACE / "config"
    previous = (manifest / "west.yml").read_bytes() if (manifest / "west.yml").exists() else None
    shutil.copytree(HERE / "config", manifest, dirs_exist_ok=True)
    env = tool_env()
    if not (WORKSPACE / ".west").exists():
        run(BIN / "west", "init", "-l", "config", cwd=WORKSPACE, env=env)
    if previous != (manifest / "west.yml").read_bytes() or not (WORKSPACE / "zephyr").exists():
        run(BIN / "west", "update", "--fetch-opt=--filter=tree:0", cwd=WORKSPACE, env=env)
        run(BIN / "python", "-m", "pip", "install", "-q", "-r",
            WORKSPACE / "zephyr" / "scripts" / "requirements-base.txt")
    module = WORKSPACE / "astrolabe-module"
    shutil.rmtree(module, ignore_errors=True)
    shutil.copytree(HERE / "module", module)
    return module


def tool_env():
    env = dict(os.environ)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    env["ZEPHYR_SDK_INSTALL_DIR"] = str(sdk_dir())
    return env


def build(layout, logging):
    module = ensure_workspace()
    build_dir = WORKSPACE / "build" / (layout + ("-logging" if logging else ""))
    args = [BIN / "west", "build", "-p", "auto", "-s", "zmk/app", "-d", build_dir,
            "-b", "nice_nano_v2"]
    if logging:
        args += ["-S", "zmk-usb-logging"]
    config = WORKSPACE / "config"
    # CMake reads backslashes as escapes, so every path it sees is written with forward slashes.
    args += ["--", "-DSHIELD=astrolabe", f"-DZMK_CONFIG={config.as_posix()}",
             f"-DZMK_EXTRA_MODULES={module.as_posix()}"]
    if layout == "s140-v6":
        overlay = module / "boards" / "shields" / "astrolabe" / "s140-v6.overlay"
        args.append(f"-DEXTRA_DTC_OVERLAY_FILE={overlay.as_posix()}")
    if logging:
        args.append(f"-DEXTRA_CONF_FILE={(config / 'logging.conf').as_posix()}")
    run(*args, cwd=WORKSPACE, env=tool_env())

    OUT.mkdir(exist_ok=True)
    uf2 = OUT / f"astrolabe-{layout}.uf2"
    shutil.copyfile(build_dir / "zephyr" / "zmk.uf2", uf2)
    shutil.copyfile(build_dir / "zephyr" / "zmk.hex", uf2.with_suffix(".hex"))
    start = uf2_start(uf2)
    if start != LAYOUTS[layout][0]:
        sys.exit(f"{uf2.name} starts at {start:#x}, expected {LAYOUTS[layout][0]:#x}")
    print(f"built {uf2} (application at {start:#x})")
    return uf2


def uf2_start(path):
    with open(path, "rb") as handle:
        return struct.unpack_from("<I", handle.read(32), 12)[0]


# --- Finding the board ------------------------------------------------------------------------

def uf2_drives():
    if os.name == "nt":
        roots = [Path(f"{letter}:\\") for letter in string.ascii_uppercase]
    else:
        roots = [*Path("/Volumes").glob("*"), *Path("/media").glob("*/*"),
                 *Path("/run/media").glob("*/*")]
    found = []
    for root in roots:
        try:
            if (root / "INFO_UF2.TXT").is_file():
                found.append(root)
        except OSError:
            pass
    return found


def layout_of(drive):
    """Clones word this differently ("S140 version 6.1.1", "S140 7.3.0"), so match loosely."""
    info = (drive / "INFO_UF2.TXT").read_text(errors="replace")
    found = re.search(r"^SoftDevice:\s*S140\s+(?:version\s+)?(\d+)\.(\d+\.\d+)", info, re.M | re.I)
    if found and found.group(1) in ("6", "7"):
        return f"s140-v{found.group(1)}", f"{found.group(1)}.{found.group(2)}"
    sys.exit(f"{drive}INFO_UF2.TXT names no S140 6.x/7.x SoftDevice:\n{info}")


def wait_for(probe, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(0.25)
    sys.exit(f"timed out waiting for {what}")


def reboot_app_to_uf2():
    """Ask running Astrolabe firmware to reboot into its UF2 bootloader. True if one answered."""
    try:
        import hid
    except ImportError:
        return False
    vid, pid, page = ZMK_USB
    for info in hid.enumerate(vid, pid):
        if info.get("usage_page") != page:
            continue
        device = hid.Device(path=info["path"]) if hasattr(hid, "Device") else hid.device()
        if not hasattr(hid, "Device"):
            device.open_path(info["path"])
        try:
            device.write(BOOTLOADER_COMMAND)
        finally:
            device.close()
        print("asked the running firmware to reboot into its bootloader")
        return True
    return False


def serial_ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def serial_dfu_port():
    return next((p.device for p in serial_ports() if (p.vid, p.pid) in SERIAL_DFU), None)


def touch_1200(port):
    """The Arduino convention: opening at 1200 baud asks TinyUSB firmware to reboot to DFU."""
    import serial
    try:
        serial.Serial(port, 1200).close()
    except serial.SerialException:
        pass  # the port often vanishes mid-open as the board resets


# --- Flashing ---------------------------------------------------------------------------------

def flash_uf2(drive, layout, logging):
    uf2 = build(layout, logging)
    target = drive / "CURRENT.UF2"
    print(f"copying {uf2.name} to {drive}")
    try:
        shutil.copyfile(uf2, target)
    except OSError:
        pass  # the bootloader resets as soon as the last block lands
    print("flashed; the board restarts into the new firmware")


def flash_serial(port, logging, layouts):
    """Serial DFU cannot report its SoftDevice, so the package names the ones it was linked for and
    the bootloader rejects a mismatch before writing. Try each layout until one is accepted."""
    for layout in layouts:
        uf2 = build(layout, logging)
        package = uf2.with_suffix(".zip")
        sd_req = ",".join(f"{value:#06x}" for value in LAYOUTS[layout][1])
        run(BIN / "adafruit-nrfutil", "dfu", "genpkg", "--dev-type", "0x0052",
            "--sd-req", sd_req, "--application", uf2.with_suffix(".hex"), package)
        result = subprocess.run([str(BIN / "adafruit-nrfutil"), "dfu", "serial", "--package",
                                 str(package), "-p", port, "-b", "115200", "--singlebank"])
        if result.returncode == 0:
            print(f"flashed {layout} over serial DFU")
            return
        print(f"bootloader refused the {layout} image; trying the next layout")
        port = wait_for(serial_dfu_port, 15, "the serial DFU bootloader to return")
    sys.exit("no layout was accepted; double-tap reset and run again to flash over UF2")


def flash(logging, layout_override):
    drives = uf2_drives()
    if not drives and reboot_app_to_uf2():
        drives = [wait_for(lambda: next(iter(uf2_drives()), None), 20, "the UF2 drive")]
    if drives:
        layout, version = layout_of(drives[0])
        print(f"UF2 bootloader at {drives[0]} with S140 {version}: {layout}")
        return flash_uf2(drives[0], layout_override or layout, logging)

    port = serial_dfu_port()
    if port is None:
        for candidate in serial_ports():
            if candidate.vid in (0x1209, 0x239A):  # TinyUSB/Arduino firmware on an nRF52840
                print(f"rebooting {candidate.device} ({candidate.description}) into DFU")
                touch_1200(candidate.device)
                port = wait_for(serial_dfu_port, 15, "the serial DFU bootloader")
                break
    if port is None:
        sys.exit("no board found: plug it in, or double-tap reset to enter the bootloader")
    print(f"serial DFU bootloader on {port}")
    order = [layout_override] if layout_override else ["s140-v7", "s140-v6"]
    flash_serial(port, logging, order)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--flash", action="store_true", help="flash the attached board")
    parser.add_argument("--logging", action="store_true",
                        help="add the USB log console and calibration output (not for daily use)")
    parser.add_argument("--layout", choices=sorted(LAYOUTS),
                        help="force a flash layout instead of reading it from the bootloader")
    args = parser.parse_args()
    ensure_tools()
    if args.flash:
        flash(args.logging, args.layout)
    else:
        drives = uf2_drives()
        build(args.layout or (layout_of(drives[0])[0] if drives else "s140-v7"), args.logging)


if __name__ == "__main__":
    main()
