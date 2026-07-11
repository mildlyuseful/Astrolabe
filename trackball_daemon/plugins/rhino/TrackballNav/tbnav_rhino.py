"""TrackballNav — Rhino 8 add-on (default suite: orbit / pan / zoom).

Background socket thread reads the Trackball Daemon nav broker; RhinoApp.Idle drains
frames on the UI thread and drives the active viewport camera (eye+target).
"""
from __future__ import print_function

import json
import os
import queue
import socket
import threading
import time
import traceback

import Rhino
import Rhino.Geometry as RG
import System
from System.Windows.Forms import Cursor

import tbnav_camera as cammath

ADDIN_VERSION = "0.1.11"          # 0.1.11: configurable orbit-pivot fallback chain.
_DEFAULT_PORT = 47900
PIVOT_HOLD_IDLE = 0.35
OBJ_CACHE_SEC = 0.5
BBOX_MARGIN = 0.10

_stop = threading.Event()
_q = queue.Queue()
_started = False
_reader_thread = None
_idle_hooked = False
_host = "?"
_gesture = {"t": 0.0, "pivot": None, "invalid": True}
_zoom_gesture = {"pivot": None}
_obj_cache = {"t": 0.0, "center": None, "bbox": None}
_last_scheme = {"v": None}
_last_err = {"t": 0.0, "s": ""}
_rl = {}


def _log(msg):
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "rhino_addin.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _log_rl(key, msg, period=2.0):
    now = time.time()
    if now - _rl.get(key, 0.0) > period:
        _rl[key] = now
        _log(msg)


def _log_apply_error(tb):
    now = time.time()
    if tb != _last_err["s"] or now - _last_err["t"] > 5.0:
        _last_err["s"], _last_err["t"] = tb, now
        _log("apply error: " + tb.strip().replace("\n", " | "))


def _bridge_port():
    try:
        appdata = os.environ.get("APPDATA", "")
        with open(os.path.join(appdata, "TrackballDaemon", "bridge.json"), "r") as f:
            return int(json.load(f).get("port", _DEFAULT_PORT))
    except Exception:
        return _DEFAULT_PORT


def _active_view():
    try:
        return Rhino.RhinoDoc.ActiveDoc.Views.ActiveView
    except Exception:
        return None


def _read_camera(view):
    vp = view.ActiveViewport
    eye = vp.CameraLocation
    target = vp.CameraTarget
    up = vp.CameraUp
    return cammath.Camera((eye.X, eye.Y, eye.Z),
                          (target.X, target.Y, target.Z),
                          (up.X, up.Y, up.Z))


def _write_camera(view, cam):
    vp = view.ActiveViewport
    eye = RG.Point3d(cam.eye[0], cam.eye[1], cam.eye[2])
    target = RG.Point3d(cam.target[0], cam.target[1], cam.target[2])
    up = RG.Vector3d(cam.up[0], cam.up[1], cam.up[2])
    vp.SetCameraLocations(target, eye)
    vp.CameraUp = up
    view.Redraw()


def _selection_center():
    now = time.time()
    if _obj_cache["center"] is not None and now - _obj_cache["t"] < OBJ_CACHE_SEC:
        return _obj_cache["center"], _obj_cache["bbox"]
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None:
        _obj_cache.update(t=now, center=None, bbox=None)
        return None, None
    objs = list(doc.Objects.GetSelectedObjects(False, False) or [])
    if not objs:
        _obj_cache.update(t=now, center=None, bbox=None)
        return None, None
    bbox = RG.BoundingBox.Empty
    centers = []
    for obj in objs:
        try:
            b = obj.Geometry.GetBoundingBox(True)
            if not b.IsValid:
                continue
            bbox = RG.BoundingBox.Union(bbox, b) if bbox.IsValid else b
            c = b.Center
            centers.append((c.X, c.Y, c.Z))
        except Exception:
            continue
    if not centers:
        _obj_cache.update(t=now, center=None, bbox=None)
        return None, None
    n = float(len(centers))
    center = (sum(c[0] for c in centers) / n,
              sum(c[1] for c in centers) / n,
              sum(c[2] for c in centers) / n)
    bb = None
    if bbox.IsValid:
        mn, mx = bbox.Min, bbox.Max
        bb = ((mn.X, mn.Y, mn.Z), (mx.X, mx.Y, mx.Z))
    _obj_cache.update(t=now, center=center, bbox=bb)
    return center, bb


def _in_bbox(p, bbox):
    if bbox is None:
        return True
    mn, mx = bbox
    diag = ((mx[0] - mn[0]) ** 2 + (mx[1] - mn[1]) ** 2 + (mx[2] - mn[2]) ** 2) ** 0.5
    m = BBOX_MARGIN * diag
    return all(mn[i] - m <= p[i] <= mx[i] + m for i in range(3))


def _raycast_client(view, client_x, client_y, bbox):
    """Raycast from viewport pixel to the front surface under that pixel.

    The path that reliably hits geometry is the raw frustum ``RayShoot``
    (``Ray3d(line.From, line.Direction)``) — same as 0.1.7. That ray often runs
    far→near so ``hits[0]`` is the *back* face.

    Front face: meshed brep + ``MeshRay`` from the camera through the far frustum
    point; keep the hit with smallest camera distance (front of a solid beats the
    RayShoot back-face fallback).

    Do **not** use bare ``vector.IsTiny`` — in Rhino Python that name is a method
    object (always truthy), which silently aborted 0.1.8/0.1.9 before any shoot.
    """
    try:
        vp = view.ActiveViewport
        ok, line = vp.GetFrustumLine(client_x, client_y)
        if not ok:
            _log_rl("raycast", "raycast: GetFrustumLine failed at (%.0f,%.0f)" % (client_x, client_y))
            return None
        doc = Rhino.RhinoDoc.ActiveDoc
        if doc is None:
            return None
        cam = vp.CameraLocation
        # Exact construction from the working 0.1.7 logs.
        shoot_ray = RG.Ray3d(line.From, line.Direction)
        # Eye → farther frustum end (into the scene) for MeshRay front-face picks.
        if cam.DistanceTo(line.From) >= cam.DistanceTo(line.To):
            far_pt = line.From
        else:
            far_pt = line.To
        eye_dir = far_pt - cam
        eye_ray = None
        if eye_dir.Length > 1e-12:
            eye_ray = RG.Ray3d(cam, eye_dir)

        best_pt = None
        best_d = None
        all_logged = []

        def _consider(pt, src):
            nonlocal best_pt, best_d
            if pt is None:
                return
            p = (float(pt.X), float(pt.Y), float(pt.Z))
            if not _in_bbox(p, bbox):
                all_logged.append("%s skipped-bbox d=%.3f p=(%.3f,%.3f,%.3f)" % (
                    src, cam.DistanceTo(pt), p[0], p[1], p[2]))
                return
            d = cam.DistanceTo(pt)
            all_logged.append("%s keep-candidate d=%.3f p=(%.3f,%.3f,%.3f)" % (
                src, d, p[0], p[1], p[2]))
            if d < 1e-9:
                return
            if best_d is None or d < best_d:
                best_d, best_pt = d, p

        def _mesh_front(mesh, label):
            if eye_ray is None or mesh is None:
                return
            t = RG.Intersect.Intersection.MeshRay(mesh, eye_ray)
            if t is None or t < 0.0:
                all_logged.append("%s miss" % label)
                return
            pt = eye_ray.PointAt(t)
            all_logged.append("%s t=%.4f d=%.3f p=(%.3f,%.3f,%.3f)" % (
                label, t, cam.DistanceTo(pt), pt.X, pt.Y, pt.Z))
            _consider(pt, label)

        for obj in doc.Objects:
            try:
                geom = obj.Geometry
                if geom is None:
                    continue
                name = "?"
                try:
                    name = obj.Name or obj.Id.ToString()[:8]
                except Exception:
                    pass

                if isinstance(geom, RG.Mesh):
                    _mesh_front(geom, "Mesh[%s]" % name)
                    # Fallback: same MeshRay along the working frustum shoot ray.
                    t = RG.Intersect.Intersection.MeshRay(geom, shoot_ray)
                    if t is not None and t >= 0.0:
                        pt = shoot_ray.PointAt(t)
                        all_logged.append("MeshShoot[%s] t=%.4f d=%.3f" % (
                            name, t, cam.DistanceTo(pt)))
                        _consider(pt, "MeshShoot[%s]" % name)
                    continue

                breps = []
                if isinstance(geom, RG.Brep):
                    breps = [geom]
                elif isinstance(geom, RG.Extrusion):
                    b = geom.ToBrep()
                    if b:
                        breps = [b]
                elif isinstance(geom, RG.Surface):
                    b = geom.ToBrep()
                    if b:
                        breps = [b]

                for bi, brep in enumerate(breps):
                    label = "%s/%d" % (name, bi)
                    # Front face via display / quick render mesh + eye MeshRay.
                    meshed = False
                    try:
                        meshes = obj.GetMeshes(RG.MeshType.Default)
                        if meshes:
                            for mi, mesh in enumerate(meshes):
                                _mesh_front(mesh, "GetMeshes[%s][%d]" % (label, mi))
                                meshed = True
                    except Exception as ex:
                        all_logged.append("GetMeshes[%s] error %s" % (label, ex))
                    if not meshed and eye_ray is not None:
                        try:
                            mp = RG.MeshingParameters.FastRenderMesh
                            created = RG.Mesh.CreateFromBrep(brep, mp)
                            if created:
                                for mi, mesh in enumerate(created):
                                    _mesh_front(mesh, "CreateFromBrep[%s][%d]" % (label, mi))
                        except Exception as ex:
                            all_logged.append("CreateFromBrep[%s] error %s" % (label, ex))

                    # Working RayShoot (often back face) — still registers a pivot.
                    hits = RG.Intersect.Intersection.RayShoot(shoot_ray, [brep], 8)
                    if not hits:
                        all_logged.append("RayShoot[%s] hits=[]" % label)
                        continue
                    parts = []
                    for i, pt in enumerate(hits):
                        parts.append("[%d] d=%.3f p=(%.3f,%.3f,%.3f)" % (
                            i, cam.DistanceTo(pt), pt.X, pt.Y, pt.Z))
                    all_logged.append("RayShoot[%s] n=%d %s" % (
                        label, len(hits), " | ".join(parts)))
                    # Min camera-distance among RayShoot points (back face if n==1).
                    # Eye MeshRay candidates above win when present (closer = front).
                    pick = min(hits, key=lambda pt: cam.DistanceTo(pt))
                    _consider(pick, "RayShoot[%s]-min" % label)
            except Exception as ex:
                all_logged.append("obj-error %s" % ex)
                continue

        if all_logged:
            _log_rl("raycast_hits",
                    "raycast (%.0f,%.0f) cam=(%.2f,%.2f,%.2f) lineFrom=(%.2f,%.2f,%.2f) "
                    "lineTo=(%.2f,%.2f,%.2f) :: %s" % (
                        client_x, client_y,
                        cam.X, cam.Y, cam.Z,
                        line.From.X, line.From.Y, line.From.Z,
                        line.To.X, line.To.Y, line.To.Z,
                        " || ".join(all_logged)),
                    period=0.5)
        elif best_pt is None:
            _log_rl("raycast", "raycast (%.0f,%.0f): no mesh/brep hits logged" % (
                client_x, client_y))
        return best_pt
    except Exception:
        _log_rl("raycast", "raycast exception: " + traceback.format_exc().strip().replace("\n", " | "))
        return None


def _screen_center_pivot(view, bbox):
    try:
        x, y = _frustum_xy_center(view)
        return _raycast_client(view, x, y, bbox)
    except Exception:
        return None


def _frustum_xy_center(view):
    """Centre pixel for GetFrustumLine — same coordinate space as ScreenToClient."""
    vp = view.ActiveViewport
    size = vp.Size
    return float(size.Width) * 0.5, float(size.Height) * 0.5


def _cursor_frustum_xy(view):
    """Mouse position in the coordinate space ``GetFrustumLine`` expects.

    McNeel samples pass ``view.ScreenToClient(mouse)`` straight into ``GetFrustumLine``.
    That API maps through the viewport's screenport internally, so the values are
    **view-client** coordinates (not viewport-local 0..Size after subtracting the port).

    An earlier GetScreenPort-based offset produced garbage bounds like ``1x311`` and
    negative Y, so every cursor sample was rejected as "outside viewport".
    """
    try:
        try:
            screen = Rhino.UI.MouseCursor.Location
        except Exception:
            screen = Cursor.Position
        client = view.ScreenToClient(screen)
        x, y = float(client.X), float(client.Y)
        # Bounds: prefer the view control size (same space as ScreenToClient). Fall back
        # to the viewport Size. Allow a small margin so chrome/DPI rounding doesn't reject.
        try:
            vw = float(view.Size.Width)
            vh = float(view.Size.Height)
        except Exception:
            vw = vh = 0.0
        if vw < 2.0 or vh < 2.0:
            sz = view.ActiveViewport.Size
            vw, vh = float(sz.Width), float(sz.Height)
        margin = 4.0
        if x < -margin or y < -margin or x > vw + margin or y > vh + margin:
            return None, x, y, vw, vh
        return (x, y), x, y, vw, vh
    except Exception:
        return None, 0.0, 0.0, 0.0, 0.0


def _cursor_pivot(view, bbox):
    """Under-mouse surface via screen cursor → view client → frustum ray."""
    try:
        xy, x, y, w, h = _cursor_frustum_xy(view)
        if xy is None:
            _log_rl("cpivot", "cursor-pivot: mouse outside view (%.0f,%.0f in %.0fx%.0f) → "
                    "view/forward fallback" % (x, y, w, h))
            return None
        hit = _raycast_client(view, xy[0], xy[1], bbox)
        if hit is None:
            _log_rl("cpivot", "cursor-pivot: nothing under cursor (%.0f,%.0f) → "
                    "view/forward fallback" % (xy[0], xy[1]))
        return hit
    except Exception:
        _log_rl("cpivot", "cursor-pivot: exception → view/forward fallback")
        return None


def _forward_point(cam):
    d = cam.distance()
    f = cam.forward()
    return (cam.eye[0] + f[0] * d, cam.eye[1] + f[1] * d, cam.eye[2] + f[2] * d)


def _orbit_pivot(op, cam, view, idle, sel_override=True, candidates=None):
    center, bbox = _selection_center()
    if sel_override and op != "viewpoint" and center is not None:
        return center
    ray_bbox = bbox if sel_override else None
    if _gesture["pivot"] is not None and not _gesture["invalid"] and idle <= PIVOT_HOLD_IDLE:
        return _gesture["pivot"]
    for method in (candidates or [op]):
        if method == "viewpoint":
            point = tuple(cam.eye)
        elif method == "origin":
            point = (0.0, 0.0, 0.0)
        elif method == "view":
            point = _screen_center_pivot(view, ray_bbox)
        elif method == "cursor":
            point = _cursor_pivot(view, ray_bbox)
        elif method in ("object", "selection"):
            point = center
        else:
            continue
        if point is not None:
            _gesture["pivot"] = point
            _gesture["invalid"] = False
            return point
    return None


def _zoom_toward(zm, view, idle=0.0, sel_override=True):
    center, bbox = _selection_center()
    if zm == "to_object":
        return center
    if zm == "to_cursor":
        if sel_override and center is not None:
            return center
        if _zoom_gesture["pivot"] is None or idle > PIVOT_HOLD_IDLE:
            ray_bbox = bbox if sel_override else None
            _zoom_gesture["pivot"] = _cursor_pivot(view, ray_bbox)
        return _zoom_gesture["pivot"]
    return None


def _apply(view, frame, idle):
    o = list(frame.get("o", [0.0, 0.0, 0.0]))
    p = list(frame.get("p", [0.0, 0.0]))
    z = float(frame.get("z", 0.0))
    op = frame.get("op", "view")
    style = frame.get("os", "free")
    zm = frame.get("zm", "to_center")
    adv = frame.get("adv") or {}
    sel_override = bool(adv.get("selection_overrides_pivot", True))
    pivot_candidates = adv.get("orbit_pivot_candidates") or [op]

    sig = (op, style, zm, sel_override)
    if sig != _last_scheme["v"]:
        _last_scheme["v"] = sig
        _log("scheme: pivot=%s style=%s zoom=%s sel_override=%s" % sig)

    # Generic daemon invert already applied; no per-mode advanced invert for Rhino default suite.
    cam = _read_camera(view)
    dist = cam.distance()
    changed = False
    turntable = style == "turntable"

    if o[0] or o[1] or o[2]:
        pivot = _orbit_pivot(op, cam, view, idle, sel_override=sel_override,
                             candidates=pivot_candidates)
        if pivot is None:
            return
        if pivot is not None:
            dist = max(cammath.DIST_MIN, cammath.v_len(cammath.v_sub(tuple(cam.eye), pivot)))
        cammath.orbit(cam, o, turntable, pivot)
        _zoom_gesture["pivot"] = None
        changed = True
    elif p[0] or p[1]:
        _gesture["invalid"] = True
        _zoom_gesture["pivot"] = None
        cammath.pan(cam, p[0], p[1], dist)
        changed = True
    elif z:
        _gesture["invalid"] = True
        toward = _zoom_toward(zm, view, idle, sel_override=sel_override)
        cammath.dolly(cam, z, dist, toward)
        changed = True

    if changed:
        _write_camera(view, cam)


def _on_idle(sender, e):
    try:
        frames = []
        while True:
            try:
                frames.append(_q.get_nowait())
            except queue.Empty:
                break
        if not frames:
            return
        view = _active_view()
        if view is None:
            return
        now = time.time()
        idle = now - _gesture["t"]
        _gesture["t"] = now
        for fr in frames:
            _apply(view, fr, idle)
            idle = 0.0
    except Exception:
        _log_apply_error(traceback.format_exc())


def _reader():
    port = _bridge_port()
    hello = json.dumps({"type": "hello", "app": "rhino", "version": ADDIN_VERSION,
                        "host": _host, "pid": os.getpid()}) + "\n"
    while not _stop.is_set():
        sock = None
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            sock.sendall(hello.encode("utf-8"))
            sock.settimeout(0.5)
            buf = b""
            while not _stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            _q.put_nowait(json.loads(line.decode("utf-8")))
                        except Exception:
                            pass
        except Exception:
            time.sleep(1.5)
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass


def start():
    global _started, _reader_thread, _idle_hooked, _host
    if _started:
        return
    _started = True
    try:
        _host = str(Rhino.RhinoApp.Version)
    except Exception:
        _host = "?"
    _log("start: TrackballNav v%s (Rhino %s)" % (ADDIN_VERSION, _host))
    _stop.clear()
    _gesture.update({"t": 0.0, "pivot": None, "invalid": True})
    _zoom_gesture["pivot"] = None
    _reader_thread = threading.Thread(target=_reader, name="trackball-nav-reader", daemon=True)
    _reader_thread.start()
    if not _idle_hooked:
        Rhino.RhinoApp.Idle += _on_idle
        _idle_hooked = True
        _log("start: reader thread + Idle pump registered")


def stop():
    global _idle_hooked
    _stop.set()
    if _idle_hooked:
        try:
            Rhino.RhinoApp.Idle -= _on_idle
        except Exception:
            pass
        _idle_hooked = False
    _log("stop: TrackballNav v%s" % ADDIN_VERSION)
