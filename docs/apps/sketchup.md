# SketchUp navigation — maintainer's guide

The SketchUp side of Trackball Daemon is a **Ruby socket extension**. It runs inside SketchUp
Desktop (Pro/Studio), connects to the daemon's localhost nav broker, and drives the active view's
explicit eye/target/up camera. SketchUp for Web is not supported because it has no local Ruby hook.

Verified live on this machine against **SketchUp 2026.2.243**, bundled **Ruby 3.2.2**, add-on
`0.2.0`, daemon `0.1.38`. The API probe and production self-test were run from *Extensions →
Developer → Ruby Console*. The production add-on also completed its broker hello; `daemon.log`
reported the loaded SketchUp build through its versioned hello.

## 1. File map

| Path | Role |
|---|---|
| `trackball_daemon/plugins/sketchup/trackball_nav_loader.rb` | Top-level Plugins loader. Registers and auto-enables the extension in Extension Manager. |
| `…/trackball_nav/main.rb` | Single-threaded non-blocking socket client, hello handshake, reconnect, `UI.start_timer` pump, logging. |
| `…/trackball_nav/camera.rb` | Focused camera math: orbit/pan/zoom, pivots, raytest validation, gesture hold. |
| `…/trackball_nav/version.json` | Bundled/installed version read by daemon auto-update. Keep in sync with both Ruby `ADDIN_VERSION` constants. |
| `tools/sketchup_nav_selftest.rb` | Interactive Ruby Console test of the real production `TrackballNav.apply` path. |
| `tests/test_integrations_sketchup.py` | Headless daemon install/detect/version/update tests. |

## 2. Data flow and threading

```text
BLE → output.py (unchanged, already scaled/inverted) → App._nav_sink
    → focused app == sketchup → NavBroker.submit
    → newline JSON over 127.0.0.1 TCP
    → main.rb UI.start_timer(0.02, repeat=true)
    → non-blocking socket read + partial-line buffer
    → TrackballNav.apply(frame) → CameraDriver.apply → cam.set → view.invalidate
```

SketchUp's Ruby API must be used on the main thread. Unlike Fusion/Blender/FreeCAD, the extension
needs **no reader thread and no queue**: its repeating UI timer owns both non-blocking socket I/O and
camera application. The socket uses `connect_nonblock`, `write_nonblock`, and `read_nonblock`; a
string buffer keeps incomplete JSON lines between timer ticks. This matters because `IO.select`
saying “readable” does not guarantee that a blocking `gets("\n")` already has a complete line.

The hello is:

```json
{"type":"hello","app":"sketchup","version":"0.2.0","host":"26.2.243","pid":1234}
```

Port discovery reads `%APPDATA%\TrackballDaemon\bridge.json`, falling back to `47900`. Socket
errors close the client and retry after 1.5 seconds. Diagnostics go to
`%APPDATA%\TrackballDaemon\sketchup_addin.log`.

## 3. Verified camera model

The live probe confirmed the documented model:

```ruby
model = Sketchup.active_model
view = model.active_view
cam = view.camera
cam.eye        # Geom::Point3d
cam.target     # Geom::Point3d
cam.up         # Geom::Vector3d
cam.direction  # normalized eye -> target vector
cam.set(eye, target, up)
cam.perspective?
cam.fov        # degrees (perspective)
cam.height     # inches (parallel projection)
view.invalidate
```

`cam.set` changed the live viewport camera directly on 2026.2; assigning `view.camera = cam` was not
needed. `view.invalidate` is still called after each applied frame and is the API's preferred redraw
request. Internal model coordinates and camera height are **inches**. SketchUp is right-handed and
Z-up, so turntable yaw uses `(0,0,1)`.

Camera axes are recomputed defensively each frame:

```text
forward = normalize(target - eye)
right   = normalize(forward × up)
up      = normalize(right × forward)
```

All three values are set together after each edit. Degenerate eye/target or axis vectors are a
no-op. SketchUp's `Length` values should be converted with `.to_f` before epsilon comparisons; the
first live probe exposed that relying on implicit Float comparison is brittle even when displayed
coordinates match exactly.

Official API references: [Camera](https://ruby.sketchup.com/Sketchup/Camera.html),
[View](https://ruby.sketchup.com/Sketchup/View.html),
[Model#raytest](https://ruby.sketchup.com/Sketchup/Model.html#raytest-instance_method), and
[UI timers](https://ruby.sketchup.com/UI.html#start_timer-class_method).

## 4. Navigation model

The broker has already applied per-app sensitivity/gain and generic invert. `camera.rb` contains
only SketchUp's baseline orientation/feel:

```ruby
ORBIT_SCALE = [-1.0, -1.0, 1.0] # full-angle eye-camera orbit; signs need hardware feel check
PAN_SIGN = [-1.0, -1.0]
PAN_SCALE = 0.14                 # fraction of visible view span
ZOOM_SIGN = 1.0
ZOOM_SCALE = 0.25
FLY_MOVE = 0.5
WALK_MOVE = 0.5
WORLD_UP = Geom::Vector3d.new(0, 0, 1)
```

- **Free orbit:** compose pitch/yaw/twist about the frame-start camera right/up/forward axes with
  `Geom::Transformation.rotation(pivot, axis, radians)`. Transform eye, target, and up, then rebuild
  an orthonormal basis.
- **Turntable:** yaw around world Z, pitch around the post-yaw camera right, drop twist, and rebuild
  up from world Z to remove roll.
- **Pan:** translate eye and target by the same camera-right/up vector. Scale derives from the
  visible height at the target (parallel `cam.height`; perspective distance/FOV corrected for view
  aspect), so pan remains useful for both small and large inch-based models.
- **Perspective zoom:** scale eye and target about the requested zoom pivot. With `to_center`, target
  stays fixed and this is a dolly; with `to_object`, the model centre stays fixed on screen.
- **Parallel zoom:** scale `cam.height`; for `to_object`, translate eye/target while scaling so the
  object centre stays fixed. Factors and eye-target distance are clamped so the eye never crosses
  the target.

### Viewpoint, fly, and walk (`0.2.0`)

SketchUp now consumes the same additive broker `adv` shape used by Blender/Unreal:

```json
{
  "nav_mode": "orbit | fly | walk",
  "lock_horizon": false,
  "fly_speed": 1.0,
  "walk_speed": 1.0,
  "invert": {
    "orbit": {"pitch":false,"yaw":false,"twist":false,"pan_x":false,"pan_y":false,"zoom":false},
    "viewpoint": {"pitch":false,"yaw":false,"roll":true},
    "fly": {"pitch":false,"yaw":false,"bank":true,"forward":false,"strafe":false,"vertical":false},
    "walk": {"pitch":false,"yaw":false,"forward":false,"strafe":false,"vertical":false}
  }
}
```

- **Viewpoint** is an orbit pivot, not a separate mode: rotate target/up about `cam.eye`, leaving the
  eye and focal distance fixed. Its pitch/yaw/roll inversions are independent; pan/zoom share the
  Orbit group, matching Blender.
- **Fly look** also rotates about the eye but is unconstrained and uses twist as bank. Shift channels
  translate eye+target along camera right/forward/up. Translation scale follows visible view span,
  with a one-inch floor so it never vanishes near the target.
- **Walk look** yaws about world Z, pitches about camera right, drops twist, and rebuilds up from
  world Z. Shift movement projects right/forward onto world XY; twist moves vertically along Z.
- `lock_horizon` forces external-pivot free orbit through the same turntable/horizon rebuild.

The generic per-app invert remains default-off underneath; the SketchUp UI exposes the richer
per-mode invert groups instead. This is deliberate: the same broker pan axis means screen pan in
Orbit, thrust in Fly, and ground-forward in Walk, so direction choices belong in the Ruby extension
where the active mode is known.

## 5. Pivots and the live raytest

- `origin` → `ORIGIN`
- `viewpoint` → camera eye (turn in place)
- `object` → `model.bounds.center`
- `cursor` → object centre (the broker pipeline has no cursor pixel)
- `view` → surface under the viewport centre, else object centre

The live Ruby Console probe created a temporary box, aimed the camera at it, and verified:

```ruby
ray = view.pickray(view.vpwidth * 0.5, view.vpheight * 0.5)
hit = model.raytest(ray) # [Geom::Point3d, instance_path] or nil
```

SketchUp 2025+ returns logical-pixel `Float` viewport dimensions and accepts Float coordinates in
`pickray`; the 2026 probe observed `1176.8 × 767.2`. A hit is accepted only inside `model.bounds`
expanded by 10% of its diagonal. The chosen point is held through the orbit gesture and reacquired
after pan/zoom or 0.35 seconds idle, preventing the pivot from chasing the moving surface.

## 6. Install, loading, and versioning

Each annual desktop release has its own user Plugins directory:

```text
%APPDATA%\SketchUp\SketchUp <year>\SketchUp\Plugins\
  trackball_nav_loader.rb
  trackball_nav\
    main.rb
    camera.rb
    version.json
```

`install_sketchup` unions every detected `SketchUp.exe` year with existing annual APPDATA folders
and copies to all of them. The top-level loader creates `SketchupExtension.new("Trackball Nav",
"trackball_nav/main")` and calls `Sketchup.register_extension(extension, true)`, so it appears in
Extension Manager and loads on startup. A version bump must update three places:

1. loader `ADDIN_VERSION`
2. main `ADDIN_VERSION`
3. `version.json`

The daemon's version-gated `auto_update` recopies a newer bundle. Ruby changes require SketchUp to
restart (or a development `load '.../main.rb'`); daemon changes require the daemon to restart.

## 7. Testing and live status

Headless daemon coverage:

```powershell
python -m pytest tests/test_integrations_sketchup.py tests/test_app_routing.py -q
python -m pytest tests -q
```

Interactive camera coverage:

```ruby
load '<repo>/tools/sketchup_nav_selftest.rb'  # use your checkout's absolute path
```

The self-test creates a temporary box inside an abortable operation and currently performs 23 live
assertions: the original orbit/pan/zoom/raycast checks plus viewpoint eye/focal hold, independent
viewpoint pitch reversal, fly look/move/vector preservation, fly-forward inversion, walk horizon
lock, and ground-plane walk movement. It writes `%TEMP%\sketchup_nav_selftest.log`, restores the
original camera, and restarts the socket timer in `ensure`.

Verified on this machine:

- live camera/accessor/rotation/redraw/timer/socket probe: pass;
- production Ruby module load from the repo: pass;
- viewpoint/fly/walk production self-test (23 checks): pass;
- broker hello observed by the running daemon with the loaded add-on version: pass;
- production Ruby Console self-test: pass;
- automatic loader from the real Plugins directory: pass (fresh normal launch produced a new
  `start` log and daemon handshake after selecting the blank template);
- physical trackball sign/feel calibration: TODO. The magnitudes follow the established eye-camera
  baselines, but the baseline direction signs remain a live hardware pass.

## 8. Gotchas

1. **No headless SketchUp Ruby API.** Tests that touch the camera must run inside the GUI.
2. **`-RubyStartup` waits at the Welcome screen.** On the first probe the script did not execute
   until a modeling template was selected and a real model window gained focus. For reliable manual
   development, open a model and use the Ruby Console.
3. **API calls stay on the timer/main thread.** Do not add a reader thread unless timer-only polling
   is proven insufficient; if one is ever added, it may only enqueue bytes/frames.
4. **Partial TCP lines are normal.** Keep `read_nonblock` plus the persistent receive buffer; a
   readable socket is not necessarily a complete newline-delimited frame.
5. **Logical pixels changed in SketchUp 2025.** `vpwidth`/`vpheight` are Floats now; do not truncate
   before `pickray`.
6. **Units are inches.** Do not copy metre/cm constants blindly. Pan is based on visible span to
   avoid a fixed-inch speed that vanishes on architectural models.
7. **SketchUp for Web is out of scope.** It cannot load the local Ruby extension.
