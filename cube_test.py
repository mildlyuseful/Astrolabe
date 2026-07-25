#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""
cube_test.py -- BLE trackball test/visualizer + software mouse injector (Windows).

A stand-in for the future PC daemon. It owns a single BLE connection to the trackball,
subscribes to the custom 3-axis rotation characteristic, and routes the ball motion to
ONE of two destinations, toggled live with SPACE:

  * CUBE mode   -- spin a 3D cube 1:1 with the ball (correctness test). Hold SHIFT to
                   pan (roll/pitch) and zoom (twist) instead of orbiting; orbit always
                   pivots on the object's center of mass.
  * CURSOR mode -- move the real Windows mouse pointer (and wheel) via SendInput,
                   so the trackball acts as a pointing device through this daemon.

Why the daemon moves the pointer itself: on Windows a BLE device that is paired as an
HID mouse is held by the OS and stops advertising, so a second app (this daemon) can't
attach to it. Rather than fight that, the daemon is the single BLE consumer and
synthesizes pointer input. DON'T pair the trackball in Windows while using this daemon.
(The firmware's native BLE HID mouse still works standalone -- just close this daemon and
pair it in Windows normally.)

While this daemon is subscribed the firmware is in "controller mode" and suppresses its
own HID output, so there's never double movement.

Each notification carries the integrated rotation delta since the previous packet as
three little-endian float32 (rx, ry, rz) in radians. In CUBE mode we treat that vector as
an axis-angle increment and compose it into a running quaternion (no gimbal lock / drift).

Stack: bleak (BLE) + pygame + PyOpenGL + ctypes (SendInput). Install the ``debug`` package extra.
Keys:  SPACE = cube <-> cursor mode   SHIFT (in cube) = pan/zoom   R = recenter view   ESC = quit
"""

import asyncio
import ctypes
import math
import struct
import sys
import threading
from ctypes import wintypes

from bleak import BleakScanner, BleakClient

import pygame
from pygame.locals import (
    DOUBLEBUF, OPENGL, QUIT, KEYDOWN, K_ESCAPE, K_SPACE, K_r, KMOD_SHIFT,
)
from OpenGL.GL import (
    glClear, glClearColor, glEnable, glBegin, glEnd, glColor3f, glVertex3f,
    glLoadIdentity, glTranslatef, glMultMatrixf, glMatrixMode, glViewport,
    GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
    GL_QUADS, GL_PROJECTION, GL_MODELVIEW,
)
from OpenGL.GLU import gluPerspective

# ===========================================================================
# Config -- must match the firmware
# ===========================================================================
DEVICE_NAME    = "Trackball BLE"
ROT_CHAR_UUID  = "2cad0002-6e64-0146-b139-9cf2a4cd57fc"   # rotation notify characteristic
# Optional: connect by address to skip scanning, e.g. "AA:BB:CC:DD:EE:FF". Leave "" to scan
# by name. (Scanning always works here because we never pair the device in Windows.)
DEVICE_ADDRESS = ""

# ---------------------------------------------------------------------------
# CUBE-mode axis mapping / sign  (firmware already remaps for the cube; leave default)
# ---------------------------------------------------------------------------
# The firmware sends (rx, ry, rz).  AXIS_SOURCE[k] picks which feeds world axis k
# (0=X,1=Y,2=Z); AXIS_SIGN[k] flips it.
AXIS_SOURCE = (0, 1, 2)
AXIS_SIGN   = (1.0, 1.0, 1.0)
ANGLE_SCALE = 1.0                # global gain; keep 1.0 for true 1:1

# ---------------------------------------------------------------------------
# CURSOR-mode mapping  (ball -> OS pointer; all tunable, defaults approximate the
# firmware's locked mouse feel at its current CPI).
# Firmware stream is now (rx,ry,rz) = (-roll, -pitch, -yaw) -- gy/gz un-swapped and
# ROT_SIGN_Z=-1 for the look-down-on-the-ball scheme. So pitch drives pointer X (idx 1)
# and yaw drives the wheel (idx 2). Indices: 0=rx 1=ry 2=rz.
# ---------------------------------------------------------------------------
MOUSE_X_SRC      = 1       # pitch (ry) drives pointer X
MOUSE_X_SIGN     = 1.0
MOUSE_Y_SRC      = 0       # roll (rx) drives pointer Y
MOUSE_Y_SIGN     = 1.0
MOUSE_GAIN       = 216.0   # radians -> pixels (tune to taste)
SCROLL_SRC       = 2       # yaw (rz) drives the wheel
SCROLL_SIGN      = 1.0
SCROLL_GAIN      = 29.0    # radians -> wheel notches
SCROLL_DEADZONE  = 0.004   # rad/packet: below this the wheel stays put
SCROLL_DOMINANCE = 1.7     # wheel axis must exceed this * pointer-plane magnitude to scroll

# ---------------------------------------------------------------------------
# CUBE-mode PAN/ZOOM  (hold SHIFT). Pan reuses the pointer-move axes; zoom reuses the
# twist/scroll axis with the same dominance gating, so a pure twist zooms and otherwise
# the ball pans -- exactly like move-vs-scroll in cursor mode. Tune signs/gains to taste.
# ---------------------------------------------------------------------------
PAN_X_SRC,  PAN_X_SIGN = 1,  1.0   # mirrors MOUSE_X_* (pitch -> pan X)
PAN_Y_SRC,  PAN_Y_SIGN = 0, -1.0   # mirrors MOUSE_Y_* but world-Y is up -> sign vs cursor flipped
PAN_GAIN               = 2.0       # radians -> world-units of pan in the view plane
ZOOM_SRC,   ZOOM_SIGN  = 2,  1.0   # mirrors SCROLL_* (yaw twist); flip ZOOM_SIGN to invert zoom
ZOOM_GAIN              = 3.0       # radians -> camera-dolly distance change
ZOOM_DEADZONE          = SCROLL_DEADZONE
ZOOM_DOMINANCE         = SCROLL_DOMINANCE
DIST_MIN, DIST_MAX     = 2.5, 30.0 # camera distance clamp (near-plane safe .. far)
DIST_DEFAULT           = 6.0

WINDOW_SIZE = (900, 700)
FPS         = 60

MODE_CUBE, MODE_CURSOR = 0, 1

# ===========================================================================
# Windows SendInput (relative pointer move + wheel), pure ctypes -- no dependency
# ===========================================================================
_WIN = (sys.platform == "win32")
if _WIN:
    MOUSEEVENTF_MOVE  = 0x0001
    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA       = 120
    INPUT_MOUSE       = 0

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", _MOUSEINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _SendInput = ctypes.windll.user32.SendInput

    def send_mouse(dx=0, dy=0, wheel=0):
        flags = 0
        if dx or dy:
            flags |= MOUSEEVENTF_MOVE
        if wheel:
            flags |= MOUSEEVENTF_WHEEL
        if not flags:
            return
        mi = _MOUSEINPUT(int(dx), int(dy),
                         (int(wheel) * WHEEL_DELTA) & 0xFFFFFFFF,
                         flags, 0, None)
        inp = _INPUT(INPUT_MOUSE)
        inp.mi = mi
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
else:
    _warned = [False]

    def send_mouse(dx=0, dy=0, wheel=0):
        if not _warned[0]:
            print("[WARN] cursor mode needs Windows SendInput; pointer injection disabled")
            _warned[0] = True

# ===========================================================================
# Quaternion helpers  (q = (w, x, y, z), unit quaternions)
# ===========================================================================
def quat_mul(a, b):
    """Hamilton product a*b."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_normalize(q):
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / n, x / n, y / n, z / n)


def quat_from_axis_angle(vx, vy, vz):
    """Delta quaternion from an axis-angle vector: axis = direction, angle = magnitude."""
    angle = math.sqrt(vx * vx + vy * vy + vz * vz)
    if angle < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle * 0.5) / angle      # (sin(a/2)/angle) folds in the 1/|v| normalize
    return (math.cos(angle * 0.5), vx * s, vy * s, vz * s)


def quat_to_gl_matrix(q):
    """Unit quaternion -> 4x4 column-major matrix for glMultMatrixf."""
    w, x, y, z = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        1 - 2 * (yy + zz), 2 * (xy + wz),     2 * (xz - wy),     0.0,
        2 * (xy - wz),     1 - 2 * (xx + zz), 2 * (yz + wx),     0.0,
        2 * (xz + wy),     2 * (yz - wx),     1 - 2 * (xx + yy), 0.0,
        0.0,               0.0,               0.0,               1.0,
    ]

# ===========================================================================
# Shared state
# ===========================================================================
_orientation = (1.0, 0.0, 0.0, 0.0)      # running cube orientation
_pan_x = 0.0                             # view-plane pan (world units), under _orient_lock
_pan_y = 0.0
_distance = DIST_DEFAULT                 # camera dolly distance (zoom), under _orient_lock
_orient_lock = threading.Lock()
_status = "starting"
_status_lock = threading.Lock()
_stop = threading.Event()

_mode = MODE_CUBE                        # written by render thread (SPACE), read by BLE thread
_shift = False                           # render thread mirrors SHIFT here for the BLE thread

# cursor-mode sub-pixel/notch accumulators -- touched only by the BLE thread
_mx_acc = 0.0
_my_acc = 0.0
_sc_acc = 0.0
_last_mode = MODE_CUBE


def set_status(text):
    global _status
    with _status_lock:
        changed = text != _status
        _status = text
    if changed:
        print(f"[BLE] {text}")


def get_status():
    with _status_lock:
        return _status


def reset_view():
    global _orientation, _pan_x, _pan_y, _distance
    with _orient_lock:
        _orientation = (1.0, 0.0, 0.0, 0.0)
        _pan_x = _pan_y = 0.0
        _distance = DIST_DEFAULT


def on_rotation(_sender, data: bytearray):
    """Notification handler: unpack 3 float32 and route them by the current mode."""
    global _orientation, _pan_x, _pan_y, _distance, _mx_acc, _my_acc, _sc_acc, _last_mode
    if len(data) < 12:
        return
    rx, ry, rz = struct.unpack_from("<fff", data, 0)
    recv = (rx, ry, rz)

    mode = _mode
    if mode != _last_mode:                # reset cursor accumulators on any switch
        _last_mode = mode
        _mx_acc = _my_acc = _sc_acc = 0.0

    if mode == MODE_CUBE:
        if not _shift:
            # Orbit: axis-angle increment -> delta quaternion, composed in the world frame.
            vx = AXIS_SIGN[0] * recv[AXIS_SOURCE[0]] * ANGLE_SCALE
            vy = AXIS_SIGN[1] * recv[AXIS_SOURCE[1]] * ANGLE_SCALE
            vz = AXIS_SIGN[2] * recv[AXIS_SOURCE[2]] * ANGLE_SCALE
            dq = quat_from_axis_angle(vx, vy, vz)
            with _orient_lock:
                _orientation = quat_normalize(quat_mul(dq, _orientation))
        else:
            # SHIFT held: pan (move part) + zoom (twist part), mutually exclusive via the
            # same dominance test cursor mode uses for move-vs-scroll. Orbit is paused.
            twist = ZOOM_SIGN * recv[ZOOM_SRC]
            plane = math.hypot(recv[PAN_X_SRC], recv[PAN_Y_SRC])
            with _orient_lock:
                if abs(twist) > ZOOM_DEADZONE and abs(twist) > ZOOM_DOMINANCE * plane:
                    _distance = min(DIST_MAX, max(DIST_MIN, _distance - twist * ZOOM_GAIN))
                else:
                    _pan_x += PAN_X_SIGN * recv[PAN_X_SRC] * PAN_GAIN
                    _pan_y += PAN_Y_SIGN * recv[PAN_Y_SRC] * PAN_GAIN
        return

    # ---- CURSOR mode: convert this packet's rotation into pointer/wheel input ----
    yaw   = SCROLL_SIGN * recv[SCROLL_SRC]
    plane = math.hypot(recv[MOUSE_X_SRC], recv[MOUSE_Y_SRC])
    if abs(yaw) > SCROLL_DEADZONE and abs(yaw) > SCROLL_DOMINANCE * plane:
        # yaw dominates -> scroll the wheel, carry the fractional remainder
        _sc_acc += yaw * SCROLL_GAIN
        notches = int(_sc_acc)
        if notches:
            send_mouse(wheel=notches)
            _sc_acc -= notches
    else:
        # otherwise -> move the pointer, carry sub-pixel remainder
        _mx_acc += MOUSE_X_SIGN * recv[MOUSE_X_SRC] * MOUSE_GAIN
        _my_acc += MOUSE_Y_SIGN * recv[MOUSE_Y_SRC] * MOUSE_GAIN
        ix, iy = int(_mx_acc), int(_my_acc)
        if ix or iy:
            send_mouse(dx=ix, dy=iy)
            _mx_acc -= ix
            _my_acc -= iy

# ===========================================================================
# BLE: scan/connect -> subscribe -> hold -> reconnect, forever
# ===========================================================================
async def ble_loop():
    while not _stop.is_set():
        if DEVICE_ADDRESS:
            target = DEVICE_ADDRESS
            set_status(f"connecting to {DEVICE_ADDRESS}...")
        else:
            set_status(f'scanning for "{DEVICE_NAME}"...')
            try:
                device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            except Exception as exc:                   # adapter hiccup, etc.
                set_status(f"scan error: {exc}")
                await asyncio.sleep(2.0)
                continue
            if device is None:
                set_status("device not found, retrying...")
                await asyncio.sleep(1.0)
                continue
            target = device

        try:
            async with BleakClient(target) as client:
                set_status(f"connected to {client.address}")
                await client.start_notify(ROT_CHAR_UUID, on_rotation)
                set_status("subscribed -- ball is live (SPACE: cube <-> cursor)")
                while client.is_connected and not _stop.is_set():
                    await asyncio.sleep(0.3)
                try:
                    await client.stop_notify(ROT_CHAR_UUID)
                except Exception:
                    pass
            set_status("disconnected, reconnecting...")
        except Exception as exc:
            set_status(f"connection error: {exc}, retrying...")
            await asyncio.sleep(2.0)


def start_ble_thread():
    def runner():
        try:
            asyncio.run(ble_loop())
        except Exception as exc:
            set_status(f"BLE thread stopped: {exc}")
    t = threading.Thread(target=runner, name="ble", daemon=True)
    t.start()
    return t

# ===========================================================================
# Rendering
# ===========================================================================
_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1),  (1, -1, 1),  (1, 1, 1),  (-1, 1, 1),
]
_FACES = [
    ((0, 1, 2, 3), (0.90, 0.20, 0.20)),   # -Z  red
    ((4, 5, 6, 7), (0.20, 0.80, 0.30)),   # +Z  green
    ((0, 4, 7, 3), (0.20, 0.40, 0.95)),   # -X  blue
    ((1, 5, 6, 2), (0.95, 0.80, 0.20)),   # +X  yellow
    ((3, 2, 6, 7), (0.80, 0.30, 0.85)),   # +Y  magenta
    ((0, 1, 5, 4), (0.20, 0.85, 0.90)),   # -Y  cyan
]


def draw_cube():
    glBegin(GL_QUADS)
    for face, color in _FACES:
        glColor3f(*color)
        for idx in face:
            glVertex3f(*_VERTS[idx])
    glEnd()


def init_gl(width, height):
    glClearColor(0.08, 0.08, 0.10, 1.0)
    glEnable(GL_DEPTH_TEST)
    glViewport(0, 0, width, height)
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(45.0, width / float(height), 0.1, 50.0)
    glMatrixMode(GL_MODELVIEW)


def main():
    global _mode, _shift
    print("=" * 72)
    print("Trackball daemon  --  do NOT pair the trackball in Windows while this runs")
    print("  SPACE = toggle CUBE (spin the cube) <-> CURSOR (move the OS mouse pointer)")
    print("  In CUBE mode: hold SHIFT to pan (move) + zoom (twist) instead of orbiting")
    print("  R = recenter view    ESC = quit")
    print("  For a plain Bluetooth mouse instead: close this, then pair in Windows.")
    print("=" * 72)

    start_ble_thread()

    pygame.init()
    pygame.display.set_mode(WINDOW_SIZE, DOUBLEBUF | OPENGL)
    init_gl(*WINDOW_SIZE)

    clock = pygame.time.Clock()
    last_caption = None
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
            elif event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    running = False
                elif event.key == K_SPACE:
                    _mode = MODE_CURSOR if _mode == MODE_CUBE else MODE_CUBE
                    print(f"[MODE] {'CURSOR (pointer)' if _mode == MODE_CURSOR else 'CUBE'}")
                elif event.key == K_r:
                    reset_view()

        # Mirror the SHIFT state for the BLE thread (pan/zoom vs orbit gate).
        _shift = bool(pygame.key.get_mods() & KMOD_SHIFT)

        with _orient_lock:
            q = _orientation
            pan_x, pan_y, dist = _pan_x, _pan_y, _distance

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -dist)          # zoom: camera dolly
        glTranslatef(pan_x, pan_y, 0.0)        # pan: view-plane translation
        glMultMatrixf(quat_to_gl_matrix(q))    # orbit about the object's center of mass
        draw_cube()
        pygame.display.flip()

        mode_txt = "CURSOR" if _mode == MODE_CURSOR else "CUBE"
        caption = f"Trackball [{mode_txt}] -- {get_status()}"
        if caption != last_caption:
            pygame.display.set_caption(caption)
            last_caption = caption

        clock.tick(FPS)

    _stop.set()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
