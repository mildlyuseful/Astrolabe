@tool
extends EditorPlugin

const ADDIN_VERSION := "0.1.6"
const DEFAULT_PORT := 47900
const PIVOT_HOLD_IDLE := 0.35
const OBJ_CACHE_SEC := 0.5
const TRACE_BIG := 1.0e7

var _stop := false
var _thread: Thread
var _mutex := Mutex.new()
var _queue: Array = []
var _gesture_t := 0.0
var _gesture_pivot = null
var _gesture_invalid := true
var _zoom_gesture_pivot = null
var _focus_dist := 10.0
var _obj_cache_t := 0.0
var _obj_center = null
var _last_scheme := ""
var _host := "?"


func _enter_tree() -> void:
	_stop = false
	_gesture_t = 0.0
	_gesture_pivot = null
	_gesture_invalid = true
	_zoom_gesture_pivot = null
	_focus_dist = TrackballNavCamera.DIST_DEFAULT
	_host = str(Engine.get_version_info().get("string", "?"))
	_log("start: TrackballNav v%s (Godot %s)" % [ADDIN_VERSION, _host])
	_thread = Thread.new()
	_thread.start(_reader)
	set_process(true)


func _exit_tree() -> void:
	_stop = true
	set_process(false)
	if _thread != null:
		_thread.wait_to_finish()
		_thread = null
	_log("stop: TrackballNav v%s" % ADDIN_VERSION)


func _process(_delta: float) -> void:
	var frames: Array = []
	_mutex.lock()
	while not _queue.is_empty():
		frames.append(_queue.pop_front())
	_mutex.unlock()
	if frames.is_empty():
		return
	var now := Time.get_ticks_msec() / 1000.0
	var idle := now - _gesture_t
	_gesture_t = now
	for fr in frames:
		_apply(fr, idle)
		idle = 0.0


func _bridge_port() -> int:
	var appdata := OS.get_environment("APPDATA")
	if appdata.is_empty():
		return DEFAULT_PORT
	var path := appdata.path_join("TrackballDaemon").path_join("bridge.json")
	if not FileAccess.file_exists(path):
		return DEFAULT_PORT
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return DEFAULT_PORT
	var txt := f.get_as_text()
	var data = JSON.parse_string(txt)
	if typeof(data) == TYPE_DICTIONARY and data.has("port"):
		return int(data["port"])
	return DEFAULT_PORT


func _reader() -> void:
	var port := _bridge_port()
	var hello := JSON.stringify({
		"type": "hello",
		"app": "godot",
		"version": ADDIN_VERSION,
		"host": _host,
		"pid": OS.get_process_id(),
	}) + "\n"
	while not _stop:
		var peer := StreamPeerTCP.new()
		var err := peer.connect_to_host("127.0.0.1", port)
		if err != OK:
			OS.delay_msec(1500)
			continue
		# Wait until connected
		var waited := 0
		while peer.get_status() == StreamPeerTCP.STATUS_CONNECTING and waited < 2000 and not _stop:
			peer.poll()
			OS.delay_msec(50)
			waited += 50
		if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			peer.disconnect_from_host()
			OS.delay_msec(1500)
			continue
		peer.put_data(hello.to_utf8_buffer())
		var buf := ""
		while not _stop and peer.get_status() == StreamPeerTCP.STATUS_CONNECTED:
			peer.poll()
			var avail := peer.get_available_bytes()
			if avail > 0:
				var res := peer.get_partial_data(avail)
				if res[0] == OK:
					buf += res[1].get_string_from_utf8()
					while "\n" in buf:
						var parts := buf.split("\n", true, 1)
						var line := parts[0].strip_edges()
						buf = parts[1] if parts.size() > 1 else ""
						if not line.is_empty():
							var parsed = JSON.parse_string(line)
							if typeof(parsed) == TYPE_DICTIONARY:
								_mutex.lock()
								_queue.append(parsed)
								_mutex.unlock()
			else:
				OS.delay_msec(5)
		peer.disconnect_from_host()
		OS.delay_msec(1500)


func _editor_camera() -> Camera3D:
	var vp := EditorInterface.get_editor_viewport_3d(0)
	if vp == null:
		return null
	return vp.get_camera_3d()


func _read_cam(camera: Camera3D) -> TrackballNavCamera.Cam:
	return TrackballNavCamera.Cam.from_basis(camera.global_position, camera.global_transform.basis)


func _write_cam(camera: Camera3D, cam: TrackballNavCamera.Cam) -> void:
	var t := Transform3D(cam.to_basis(), cam.location)
	camera.global_transform = t


func _apply(frame: Dictionary, idle: float) -> void:
	var camera := _editor_camera()
	if camera == null:
		return
	var o := _vec3(frame.get("o", [0, 0, 0]))
	var p := _vec2(frame.get("p", [0, 0]))
	var z := float(frame.get("z", 0.0))
	var op := str(frame.get("op", "screen_center"))
	# Godot editor: turntable only (no free trackball / roll).
	var style := "turntable"
	var zm := str(frame.get("zm", "to_center"))
	var adv: Dictionary = frame.get("adv", {})
	if typeof(adv) != TYPE_DICTIONARY:
		adv = {}
	var nav_mode := str(adv.get("nav_mode", "orbit"))
	var lock_h := true
	var twist_action := str(adv.get("twist_action", "zoom"))
	if twist_action == "roll":
		twist_action = "none"
	var pan_scales := bool(adv.get("pan_scales_with_distance", true))
	var sel_override := bool(adv.get("selection_overrides_pivot", true))
	var pivot_candidates = adv.get("orbit_pivot_candidates", [op])
	if typeof(pivot_candidates) != TYPE_ARRAY:
		pivot_candidates = [op]
	var fly_speed := float(adv.get("fly_speed", 1.0))
	var walk_speed := float(adv.get("walk_speed", 1.0))
	var invert: Dictionary = adv.get("invert", {})
	if typeof(invert) != TYPE_DICTIONARY:
		invert = {}

	var sig := "%s|%s|%s|%s|%s|%s|%s" % [nav_mode, op, style, zm, twist_action, lock_h, sel_override]
	if sig != _last_scheme:
		_last_scheme = sig
		_log("scheme: nav=%s pivot=%s style=%s zoom=%s twist=%s horizon=%s sel_override=%s" % [
			nav_mode, op, style, zm, twist_action, lock_h, sel_override])

	var inv_res := _apply_inverts(nav_mode, op, o, p, z, invert)
	o = inv_res[0]
	p = inv_res[1]
	z = inv_res[2]

	var cam := _read_cam(camera)
	_focus_dist = TrackballNavCamera.clamp_dist(_focus_dist)
	var changed := false
	if nav_mode == "fly":
		changed = _apply_fly(cam, o, p, z, fly_speed)
	elif nav_mode == "walk":
		changed = _apply_walk(cam, o, p, z, walk_speed)
	else:
		changed = _apply_orbit(cam, o, p, z, op, style, zm, twist_action, lock_h, pan_scales,
			idle, sel_override, pivot_candidates)
	if changed:
		_write_cam(camera, cam)


func _apply_orbit(cam: TrackballNavCamera.Cam, o: Vector3, p: Vector2, z: float,
		op: String, _style: String, zm: String, twist_action: String, _lock_h: bool,
		pan_scales: bool, idle: float, sel_override: bool, pivot_candidates: Array) -> bool:
	if absf(o.x) > 1e-12 or absf(o.y) > 1e-12 or absf(o.z) > 1e-12:
		var twist := o.z
		var orbit_o := Vector3(o.x, o.y, 0.0)  # never roll
		var did := false
		if absf(twist) > 1e-12 and twist_action in ["zoom", "dolly"]:
			TrackballNavCamera.dolly(cam, twist, _focus_dist, null)
			_gesture_invalid = true
			did = true
		if absf(orbit_o.x) > 1e-12 or absf(orbit_o.y) > 1e-12:
			var pivot = _orbit_pivot(op, cam, idle, sel_override, pivot_candidates)
			if pivot == null:
				return did
			_focus_dist = TrackballNavCamera.clamp_dist((cam.location - pivot).length())
			TrackballNavCamera.orbit(cam, orbit_o, true, pivot)  # turntable only
			_zoom_gesture_pivot = null
			return true
		return did
	if absf(p.x) > 1e-12 or absf(p.y) > 1e-12:
		_gesture_invalid = true
		_zoom_gesture_pivot = null
		TrackballNavCamera.pan(cam, p.x, p.y,
			_focus_dist if pan_scales else TrackballNavCamera.DIST_DEFAULT)
		return true
	if absf(z) > 1e-12:
		_gesture_invalid = true
		TrackballNavCamera.dolly(cam, z, _focus_dist, _zoom_toward(zm, idle, sel_override))
		return true
	return false


func _apply_fly(cam: TrackballNavCamera.Cam, o: Vector3, p: Vector2, z: float, speed: float) -> bool:
	if absf(o.x) > 1e-12 or absf(o.y) > 1e-12 or absf(o.z) > 1e-12:
		# Horizon-locked look only — no bank/roll (editor cannot store it).
		TrackballNavCamera.look(cam, Vector3(o.x, o.y, 0.0), true)
		return true
	if absf(p.x) > 1e-12 or absf(p.y) > 1e-12 or absf(z) > 1e-12:
		TrackballNavCamera.fly_move(cam, p, z, _focus_dist, speed)
		_gesture_invalid = true
		_zoom_gesture_pivot = null
		return true
	return false


func _apply_walk(cam: TrackballNavCamera.Cam, o: Vector3, p: Vector2, z: float, speed: float) -> bool:
	if absf(o.x) > 1e-12 or absf(o.y) > 1e-12 or absf(o.z) > 1e-12:
		TrackballNavCamera.look(cam, o, true)
		return true
	if absf(p.x) > 1e-12 or absf(p.y) > 1e-12 or absf(z) > 1e-12:
		TrackballNavCamera.walk_move(cam, p, z, _focus_dist, speed)
		_gesture_invalid = true
		_zoom_gesture_pivot = null
		return true
	return false


func _orbit_pivot(op: String, cam: TrackballNavCamera.Cam, idle: float, sel_override: bool,
		candidates: Array):
	var center = _selection_center()
	if sel_override and op != "camera" and center != null:
		return center
	if _gesture_pivot != null and not _gesture_invalid and idle <= PIVOT_HOLD_IDLE:
		return _gesture_pivot
	for method in candidates:
		var point = null
		if method == "camera":
			point = cam.location
		elif method == "origin":
			point = Vector3.ZERO
		elif method == "screen_center":
			point = _screen_center_pivot(cam)
		elif method == "cursor":
			point = _cursor_pivot()
		elif method in ["object", "selection"]:
			point = center
		if point != null:
			_gesture_pivot = point
			_gesture_invalid = false
			return point
	return null


func _zoom_toward(zm: String, idle: float, sel_override: bool):
	var center = _selection_center()
	if zm == "to_object":
		return center
	if zm == "to_cursor":
		if sel_override and center != null:
			return center
		if _zoom_gesture_pivot == null or idle > PIVOT_HOLD_IDLE:
			_zoom_gesture_pivot = _cursor_pivot()
		return _zoom_gesture_pivot
	return null


func _forward_point(cam: TrackballNavCamera.Cam) -> Vector3:
	var d := TrackballNavCamera.clamp_dist(_focus_dist)
	return cam.location + cam.forward * d


func _screen_center_pivot(cam: TrackballNavCamera.Cam):
	return _trace_ray(cam.location, cam.forward)


func _cursor_pivot():
	var vp := EditorInterface.get_editor_viewport_3d(0)
	if vp == null:
		return null
	var camera := vp.get_camera_3d()
	if camera == null:
		return null
	var mouse := vp.get_mouse_position()
	var origin := camera.project_ray_origin(mouse)
	var direction := camera.project_ray_normal(mouse)
	return _trace_ray(origin, direction)


func _trace_ray(origin: Vector3, direction: Vector3):
	var space: Node = EditorInterface.get_edited_scene_root()
	if space == null:
		return null
	var world: World3D = space.get_world_3d()
	if world == null:
		return null
	var n := direction.length()
	if n < 1e-12:
		return null
	var d := direction / n
	var query := PhysicsRayQueryParameters3D.create(origin, origin + d * TRACE_BIG)
	query.collide_with_areas = false
	query.collide_with_bodies = true
	var hit: Dictionary = world.direct_space_state.intersect_ray(query)
	if hit.is_empty():
		# Fallback: editor raycast against MeshInstance3D AABBs in the edited scene.
		return _mesh_aabb_ray(origin, d, space)
	return hit["position"]


func _mesh_aabb_ray(origin: Vector3, direction: Vector3, root: Node):
	var best_t := TRACE_BIG
	var best = null
	var stack: Array = [root]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		for c in n.get_children():
			stack.append(c)
		if n is MeshInstance3D:
			var mi := n as MeshInstance3D
			var aabb: AABB = mi.global_transform * mi.get_aabb()
			var t: float = _aabb_ray(origin, direction, aabb)
			if t >= 0.0 and t < best_t:
				best_t = t
				best = origin + direction * t
	return best


func _aabb_ray(origin: Vector3, direction: Vector3, aabb: AABB) -> float:
	# Slab method; returns t or -1.
	var tmin := 0.0
	var tmax := TRACE_BIG
	for i in 3:
		var o := origin[i]
		var d := direction[i]
		var mn := aabb.position[i]
		var mx := aabb.position[i] + aabb.size[i]
		if absf(d) < 1e-12:
			if o < mn or o > mx:
				return -1.0
			continue
		var inv := 1.0 / d
		var t1 := (mn - o) * inv
		var t2 := (mx - o) * inv
		if t1 > t2:
			var tmp := t1
			t1 = t2
			t2 = tmp
		tmin = maxf(tmin, t1)
		tmax = minf(tmax, t2)
		if tmin > tmax:
			return -1.0
	return tmin if tmin >= 0.0 else tmax


func _selection_center():
	var now := Time.get_ticks_msec() / 1000.0
	if _obj_center != null and now - _obj_cache_t < OBJ_CACHE_SEC:
		return _obj_center
	var nodes := EditorInterface.get_selection().get_selected_nodes()
	if nodes.is_empty():
		_obj_center = null
		_obj_cache_t = now
		return null
	var sum := Vector3.ZERO
	var n := 0
	for node in nodes:
		if node is Node3D:
			var p: Vector3 = (node as Node3D).global_position
			if node is MeshInstance3D:
				var mi := node as MeshInstance3D
				p = (mi.global_transform * mi.get_aabb()).get_center()
			sum += p
			n += 1
	if n == 0:
		_obj_center = null
		_obj_cache_t = now
		return null
	_obj_center = sum / float(n)
	_obj_cache_t = now
	return _obj_center


func _sgn(flag: bool) -> float:
	return -1.0 if flag else 1.0


func _apply_inverts(nav_mode: String, op: String, o: Vector3, p: Vector2, z: float, inv: Dictionary) -> Array:
	if nav_mode == "fly":
		var f: Dictionary = inv.get("fly", {})
		o = Vector3(o.x * _sgn(bool(f.get("pitch", false))), o.y * _sgn(bool(f.get("yaw", false))),
			o.z * _sgn(bool(f.get("bank", false))))
		p = Vector2(p.x * _sgn(bool(f.get("strafe", false))), p.y * _sgn(bool(f.get("forward", false))))
		z *= _sgn(bool(f.get("vertical", false)))
	elif nav_mode == "walk":
		var w: Dictionary = inv.get("walk", {})
		o = Vector3(o.x * _sgn(bool(w.get("pitch", false))), o.y * _sgn(bool(w.get("yaw", false))), o.z)
		p = Vector2(p.x * _sgn(bool(w.get("strafe", false))), p.y * _sgn(bool(w.get("forward", false))))
		z *= _sgn(bool(w.get("vertical", false)))
	else:
		var ob: Dictionary = inv.get("orbit", {})
		if op == "camera":
			var vp: Dictionary = inv.get("camera", {})
			o = Vector3(o.x * _sgn(bool(vp.get("pitch", false))), o.y * _sgn(bool(vp.get("yaw", false))),
				o.z * _sgn(bool(vp.get("roll", false))))
		else:
			o = Vector3(o.x * _sgn(bool(ob.get("pitch", false))), o.y * _sgn(bool(ob.get("yaw", false))),
				o.z * _sgn(bool(ob.get("twist", false))))
		p = Vector2(p.x * _sgn(bool(ob.get("pan_x", false))), p.y * _sgn(bool(ob.get("pan_y", false))))
		z *= _sgn(bool(ob.get("zoom", false)))
	return [o, p, z]


func _vec3(v) -> Vector3:
	if typeof(v) == TYPE_ARRAY and v.size() >= 3:
		return Vector3(float(v[0]), float(v[1]), float(v[2]))
	return Vector3.ZERO


func _vec2(v) -> Vector2:
	if typeof(v) == TYPE_ARRAY and v.size() >= 2:
		return Vector2(float(v[0]), float(v[1]))
	return Vector2.ZERO


func _log(msg: String) -> void:
	var appdata := OS.get_environment("APPDATA")
	if appdata.is_empty():
		return
	var dir := appdata.path_join("TrackballDaemon")
	DirAccess.make_dir_recursive_absolute(dir)
	var path := dir.path_join("godot_addin.log")
	var f := FileAccess.open(path, FileAccess.READ_WRITE)
	if f == null:
		f = FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		return
	f.seek_end()
	f.store_string("%s %s\n" % [Time.get_time_string_from_system(), msg])
	f.close()
