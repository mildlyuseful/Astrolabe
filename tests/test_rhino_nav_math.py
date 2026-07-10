"""Headless unit tests for Rhino add-on pure camera math (tbnav_camera).

Loaded via importlib from the Rhino plugin dir so it does not collide with FreeCAD's
identically named ``tbnav_camera`` module on sys.path.
"""
import importlib.util
import math
import os
import sys

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "..", "trackball_daemon", "plugins", "rhino",
                     "TrackballNav", "tbnav_camera.py")
_spec = importlib.util.spec_from_file_location("tbnav_rhino_camera", _PATH)
cam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cam)


def vclose(u, v, eps=1e-6):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


def _cam(eye=(0, 0, 10), target=(0, 0, 0), up=(0, 1, 0)):
    return cam.Camera(eye, target, up)


def test_orbit_scale_is_one():
    assert cam.ORBIT_SCALE == (1.0, 1.0, 1.0)


def test_orbit_about_target_keeps_distance():
    c = _cam()
    d0 = c.distance()
    cam.orbit(c, (0.0, 0.3, 0.0), False, (0.0, 0.0, 0.0))
    assert abs(c.distance() - d0) < 1e-6


def test_turntable_yaw_about_world_z():
    c = _cam(eye=(10, 0, 0), target=(0, 0, 0), up=(0, 0, 1))
    cam.orbit(c, (0.0, math.pi / 2, 0.0), True, (0.0, 0.0, 0.0))
    # +90 deg yaw about Z: eye (10,0,0) -> (0,10,0) approximately
    assert abs(c.eye[0]) < 1e-6 and abs(c.eye[1] - 10.0) < 1e-6


def test_pan_moves_eye_and_target():
    c = _cam()
    eye0, tgt0 = tuple(c.eye), tuple(c.target)
    cam.pan(c, 1.0, 0.0, 10.0)
    assert not vclose(tuple(c.eye), eye0)
    delta_eye = cam.v_sub(tuple(c.eye), eye0)
    delta_tgt = cam.v_sub(tuple(c.target), tgt0)
    assert vclose(delta_eye, delta_tgt)


def test_dolly_moves_along_forward():
    c = _cam(eye=(0, 0, 10), target=(0, 0, 0), up=(0, 1, 0))
    cam.dolly(c, 0.1, 10.0, None)
    # Positive z dollies in (toward target / along forward -Z here)
    assert c.eye[2] < 10.0
