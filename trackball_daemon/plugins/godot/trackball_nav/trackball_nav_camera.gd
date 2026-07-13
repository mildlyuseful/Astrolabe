# Pure camera math for Trackball Nav (no EditorInterface). Godot 4 is RIGHT-HANDED, Y-up.
# Free-fly eye + orthonormal basis; orbit/pan/dolly/fly/walk synthesised like Unreal.
#
# The Godot editor viewport cursor is yaw/pitch only (no roll). Callers must use turntable
# orbit and must not feed twist-as-roll — free/banked bases are not persistable there.
extends RefCounted
class_name TrackballNavCamera

const ORBIT_SCALE := Vector3.ONE # daemon supplies host baseline in frame.adv
const ORBIT_SIGN := Vector3(1.0, 1.0, 1.0)
const PAN_SIGN := Vector2.ONE
const PAN_SCALE := 1.0
const ZOOM_SIGN := 1.0
const ZOOM_SCALE := 1.0
const MOVE_SCALE := 1.0
const WORLD_UP := Vector3(0.0, 1.0, 0.0)
const DIST_DEFAULT := 10.0
const DIST_MIN := 0.01
const DIST_MAX := 1.0e7


class Cam:
	var location: Vector3
	var forward: Vector3
	var right: Vector3
	var up: Vector3

	func _init(loc: Vector3 = Vector3.ZERO, fwd: Vector3 = Vector3(0, 0, -1),
			rgt: Vector3 = Vector3(1, 0, 0), upp: Vector3 = Vector3(0, 1, 0)) -> void:
		location = loc
		forward = fwd
		right = rgt
		up = upp

	static func from_basis(loc: Vector3, basis: Basis) -> Cam:
		# Godot Camera3D looks down -Z in local space.
		var c := Cam.new()
		c.location = loc
		c.forward = -basis.z
		c.right = basis.x
		c.up = basis.y
		return c

	func to_basis() -> Basis:
		return Basis(right, up, -forward)


static func clamp_dist(dist: float) -> float:
	if is_nan(dist) or dist <= 0.0:
		return DIST_DEFAULT
	return clampf(dist, DIST_MIN, DIST_MAX)


static func _rotate_about_axis(v: Vector3, axis: Vector3, angle: float) -> Vector3:
	var n := axis.length()
	if n < 1e-12 or absf(angle) < 1e-15:
		return v
	return v.rotated(axis / n, angle)


static func _rotate_frame(cam: Cam, axis: Vector3, angle: float, pivot) -> void:
	cam.forward = _rotate_about_axis(cam.forward, axis, angle).normalized()
	cam.right = _rotate_about_axis(cam.right, axis, angle).normalized()
	cam.up = _rotate_about_axis(cam.up, axis, angle).normalized()
	if pivot != null:
		var rel: Vector3 = cam.location - pivot
		cam.location = pivot + _rotate_about_axis(rel, axis, angle)


static func orbit(cam: Cam, o: Vector3, turntable: bool, pivot) -> void:
	var pitch := o.x * ORBIT_SCALE.x * ORBIT_SIGN.x
	var yaw := o.y * ORBIT_SCALE.y * ORBIT_SIGN.y
	var twist := o.z * ORBIT_SCALE.z * ORBIT_SIGN.z
	if turntable:
		if absf(yaw) > 1e-15:
			_rotate_frame(cam, WORLD_UP, yaw, pivot)
		if absf(pitch) > 1e-15:
			_rotate_frame(cam, cam.right, pitch, pivot)
		return
	# Free path kept for math parity / tests; Godot plugin always passes turntable=true.
	var axis := cam.right * pitch + cam.up * yaw + cam.forward * twist
	var angle := axis.length()
	if angle > 1e-12:
		_rotate_frame(cam, axis, angle, pivot)


static func pan(cam: Cam, px: float, py: float, dist: float) -> void:
	var k := PAN_SCALE * clamp_dist(dist)
	var dx := PAN_SIGN.x * px * k
	var dy := PAN_SIGN.y * py * k
	cam.location += cam.right * dx + cam.up * dy


static func dolly(cam: Cam, z: float, dist: float, toward) -> void:
	var k := ZOOM_SIGN * z * ZOOM_SCALE * clamp_dist(dist)
	if toward != null:
		var d: Vector3 = toward - cam.location
		if d.length() > 1e-6:
			cam.location += d.normalized() * k
			return
	cam.location += cam.forward * k


static func look(cam: Cam, o: Vector3, horizon_lock: bool) -> void:
	orbit(cam, o, horizon_lock, null)


static func fly_move(cam: Cam, p: Vector2, z: float, dist: float, speed: float) -> void:
	var k := MOVE_SCALE * speed * clamp_dist(dist)
	cam.location += cam.right * (p.x * k) + cam.forward * (p.y * k) + cam.up * (z * k)


static func walk_move(cam: Cam, p: Vector2, z: float, dist: float, speed: float) -> void:
	var k := MOVE_SCALE * speed * clamp_dist(dist)
	var rh := _horizontal(cam.right)
	var fh := _horizontal(cam.forward)
	cam.location += rh * (p.x * k) + fh * (p.y * k) + WORLD_UP * (z * k)


static func _horizontal(v: Vector3) -> Vector3:
	var h := Vector3(v.x, 0.0, v.z)
	return h.normalized() if h.length_squared() > 1e-12 else Vector3.ZERO
