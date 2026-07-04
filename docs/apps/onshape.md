# Onshape 3D-navigation bridge — implementation notes & handoff

This is the maintainer's guide to the **Onshape** integration in the Trackball Daemon. It documents
what the bridge does, the reverse-engineered protocol, and — most importantly — the **gotchas and
solved problems you would not discover by reading the code alone** (§8). If you only read one
section, read §8.

Primary code: [`trackball_daemon/onshape_bridge.py`](../../trackball_daemon/onshape_bridge.py). Wiring:
`app.py`, `config.py`, `integrations.py`, `ui.py`, `winfocus.py`.

---

## 1. What it is, in one paragraph

Onshape runs in a browser and has **native 3Dconnexion SpaceMouse support**: its page loads a
3Dconnexion JavaScript client that (on Windows/Mac) connects to a local "NL-Proxy" service at the
loopback endpoint `127.51.68.120:8181` over a **TLS WebSocket** speaking a **WAMP v1** protocol.
We don't own a SpaceMouse, so instead of faking mouse drags we **stand up our own server
impersonating that NL-Proxy**. Onshape connects to us, exposes accessors for its camera/scene, and
**we run the navigation model**: read the camera, apply the trackball's orbit/pan/zoom about a
pivot, write the new camera back. The in-process `OnshapeBridge` is the browser analogue of
`solidworks_driver.py` (an external in-process driver, parallel to the socket broker) and exposes
the same surface (`submit/set_rate/set_scheme/start/stop/is_connected/version` +
`on_connection_changed`).

Prior art that made this possible: **`RmStorm/spacenav-ws`** (Python; same endpoint, reverse-engineered
the same traffic for Linux) — we reused its **protocol** findings and its **HAR captures of a real
NL-Proxy session**, but not its camera math (we reuse the Fusion add-in's vector math instead).

---

## 2. Architecture & threading

- `submit(ox,oy,oz,px,py,zoom)` is called on the **BLE thread**; it only accumulates a 6-float delta
  under a lock. Never blocks, never touches the socket.
- A **server thread** runs the TLS accept loop on `127.51.68.120:8181`. Each accepted connection
  gets a **reader thread** (`_OnshapeConn.serve`) that does the HTTP/WS handshake, then parses WAMP
  frames: it answers the client's create/subscribe/focus calls and resolves our read/write replies
  by `call_id` (each pending RPC has a `threading.Event`).
- A **worker thread** (`_run_worker`) waits until a client is subscribed+focused, then at `rate_hz`
  coalesces the accumulated delta and runs **one navigation step** (`_navigate`): read `view.affine`,
  apply the camera change, write it back — wrapped in the `motion`/`transaction` framing. **All
  network round-trips happen on this worker, never on the BLE thread.**
- `on_connection_changed(connected, version)` fires on handshake-complete / disconnect so the
  tray/UI status updates (exactly like the SolidWorks COM driver).
- Degrades gracefully: missing/instalid cert → server doesn't start (logged once), submit/flush
  become no-ops, rest of the daemon runs. Windows-only behaviour is guarded; the module imports on
  non-Windows.

---

## 3. Endpoint & discovery

- Host/port: **`127.51.68.120:8181`, HTTPS/WSS only** (Onshape is https, so mixed-content rules
  forbid `ws://`).
- HTTP discovery: Onshape first does `GET https://127.51.68.120:8181/3dconnexion/nlproxy`; we reply
  `{"port":8181,"version":"1.4.8.21486"}`. This is **cross-origin** (page is `https://cad.onshape.com`)
  so the response carries CORS headers (`Access-Control-Allow-Origin`, echoing the request Origin).
- WebSocket: path `/`, **subprotocol `wamp`** (echoed in our 101 response).
- `GET /` (no upgrade) returns a tiny status page — handy for the one-time cert-trust visit (§6).

---

## 4. The WAMP wire protocol (verified against real captures + live Onshape)

Messages are JSON arrays `[TYPE, ...]`. Types: `WELCOME=0, PREFIX=1, CALL=2, CALLRESULT=3,
CALLERROR=4, SUBSCRIBE=5, UNSUBSCRIBE=6, PUBLISH=7, EVENT=8`. The Onshape client is **not**
spec-compliant (no HELLO); the server speaks first with WELCOME.

### Handshake
```
S->C  [0,"<session>",1,"<server ident>"]                                       # WELCOME
C->S  [1,"3dx_rpc","wss://127.51.68.120/3dconnexion#"]                         # PREFIX
C->S  [1,"3dconnexion","wss://127.51.68.120/3dconnexion"]                      # PREFIX
C->S  [1,"self","https://cad.onshape.com/documents/..."]                       # PREFIX (page URL)
C->S  [2,"<id>","3dx_rpc:create","3dconnexion:3dmouse","<libver>"]             # create mouse
S->C  [3,"<id>",{"connexion":"<mouse_id>"}]
C->S  [2,"<id>","3dx_rpc:create","3dconnexion:3dcontroller","<mouse_id>",{"version":..,"name":"Onshape"}]
S->C  [3,"<id>",{"instance":<instance_id>}]
C->S  [5,"3dconnexion:3dcontroller/<instance_id>"]                             # SUBSCRIBE (short form)
C->S  [2,"<id>","3dx_rpc:update","3dconnexion:3dcontroller/<instance_id>",{"focus":true}]
S->C  [3,"<id>",{}]
```
- Prefix resolution splits on the first `:` and prepends the registered prefix, so
  `3dconnexion:3dcontroller/<id>` resolves to `wss://127.51.68.120/3dconnexion3dcontroller/<id>`
  (no slash — the prefix has no trailing one). **We publish EVENTs using the SHORT prefixed form**
  `3dconnexion:3dcontroller/<id>` (what the real proxy does and what the client subscribes to).
- The `<instance_id>` we return in the create result is echoed by the client's SUBSCRIBE; use the
  same value as the topic suffix.

### Driving navigation (server reads/writes the app's scene)
To read a property we send an EVENT to the controller topic carrying a nested CALL; the client
answers with a top-level CALLRESULT (or CALLERROR for unsupported):
```
S->C  [8,"<topic>",[2,"<cid>","self:read","","view.affine"]]
C->S  [3,"<cid>",[ ...16 floats... ]]            # or [4,"<cid>","self:read#generic","... unknown property"]
```
A write is the same with `self:update` and the value appended. The empty `""` second arg of the
nested CALL is part of the protocol — always present.

### Per-frame motion framing
```
self:update pivot.position [x,y,z]   # best-effort (the on-screen pivot marker)
self:update pivot.visible  true      # best-effort
self:update motion         true
  # each animation frame, inside a transaction:
  self:update transaction <n>
  self:update view.extents [6 floats] # ORTHO only (see §8.3)
  self:update view.affine  [16 floats]
  self:update transaction 0
self:update pivot.visible  false
self:update motion         false
```

### Accessors Onshape actually supports (verified live with the probe, client lib v0.6.0)
- **Read:** `view.affine` (16 floats), `view.extents` (6: minx,miny,minz,maxx,maxy,maxz),
  `view.perspective` (bool — **false by default**, Onshape is orthographic), `view.target` (3),
  `model.extents` (6), `hit.lookat` (3, the hit-test result).
- **Write:** `view.affine`, `view.extents`, `hit.lookfrom` / `hit.direction` / `hit.aperture`
  (write-only inputs — reading them returns "unknown property", which is **expected**),
  `hit.selectionOnly`, `motion`, `transaction`, `pivot.position`, `pivot.visible`.
- **Not exposed:** `pointer` (so true cursor-position pivot is impossible — see §5/§8.6).
- Best-effort props (`motion`/`transaction`/`pivot.*`) are sent via `write_best_effort`: a
  CALLERROR is swallowed (logged once) and never disconnects. **Only `view.affine` must land.**

---

## 5. Camera math (decode → reuse Fusion math → encode)

`view.affine` is a **4×4 camera-to-world matrix, row-major flat, in ROW-VECTOR layout**: the camera
basis vectors are the upper-left **rows** and the eye/translation is the **last row** (indices
12,13,14). See §8.1 — this is the single most important and most surprising fact, and the opposite
of what a 3Dconnexion sample capture suggests. Decode (`_decode_affine`, with
`AFFINE_TRANSLATION_IN_COLUMN = False`):
```
right = (m[0],  m[1],  m[2])     # camera +X in world
up    = (m[4],  m[5],  m[6])     # camera +Y in world
back  = (m[8],  m[9],  m[10])    # camera +Z in world (OUT of the screen); forward = -back
eye   = (m[12], m[13], m[14])    # camera position in world
```
With eye + a right/up/forward basis we apply the **same orbit/pan/zoom-about-a-pivot as the Fusion
add-in** (`plugins/fusion360/TrackballNav/TrackballNav.py`):
- **orbit** — rotate `eye` about the pivot and rotate the basis (free = about the composed camera
  axis; turntable = yaw about `WORLD_UP` + pitch about camera-right, roll dropped). Re-orthonormalize
  the basis each frame (`_orthonormalize`) to fight drift.
- **pan** — shift `eye` by `right*gx + up*gy`, scaled by the view half-extent so it feels constant at
  any zoom.
- **zoom** — *perspective* (rare): dolly `eye` along forward. *orthographic* (default): scale
  `view.extents` (moving the eye does nothing under an orthographic projection). **See §8.3.**

Then re-encode (`_encode_affine`, same layout) and write `view.affine`.

---

## 6. The "view" orbit pivot: navlib hit-test raycast (`_hit_center`, `_pivot`, `_gesture_pivot`)

`view` (and `cursor`) orbit pivots about **what's under the center of the screen**, like Onshape's
own right-drag orbit. We use the navlib **hit-test**: write the ray (`hit.lookfrom` origin,
`hit.direction`, `hit.aperture` = ray **thickness**, `hit.selectionOnly=false`) and read `hit.lookat`
(the surface hit point). **Onshape does the actual raycast** (GPU/BVH — cheap on their side).

Algorithm:
1. Ray = screen-center optical axis: `direction = normalize(-back)`, `lookfrom = eye - dir*(view_half*8)`
   (started on the viewer side, outside the model).
2. Try apertures smallest-first (`HIT_APERTURES = (0.03, 0.1, 0.3)` × view half-extent), widening
   until something is hit ("expand the radius until it hits").
3. Validate the hit is a finite 3-point **inside `model.extents` expanded ~10% of its diagonal**
   (`_valid_hit`) — guards against a no-hit sentinel/stale value masquerading as a hit.
4. No valid hit at any aperture → **fall back to the model-center pivot** (= "object" orbit).
5. **Hold the pivot for the whole gesture** (`_held_pivot`): captured once on the first orbit frame,
   reused every frame, re-picked only after a pan/zoom or idle > 0.35s. The hit-test therefore runs
   **once per gesture (~5 round-trips), not per frame.**

Other pivots: `origin` = world origin; `object`/`cursor` fall back to model center when no hit. The
scheme comes from `set_scheme` (General → 3D control scheme, or per-app Onshape override).

---

## 7. TLS / cert trust & getting Onshape to connect

- We mint a **unique self-signed cert** at setup (`ensure_cert`, via `cryptography` else the
  `openssl` CLI) with `CN=127.51.68.120` and a mandatory **`subjectAltName = IP:127.51.68.120`**,
  stored at `%APPDATA%\TrackballDaemon\onshape_cert.pem` / `onshape_key.pem`. The WSS server loads
  them with stdlib `ssl`. **Generating the cert touches no trust store** (just our config dir).
- **Trusting it is per-browser (see §8.9):**
  - Chrome/Edge use the **Windows cert store** → `certutil -user -addstore Root "<cert>"` (per-user,
    **no admin**, user-confirmed by Windows' own dialog; undo: `certutil -user -delstore Root 127.51.68.120`).
  - Firefox uses its **own NSS store** → the in-browser exception (visit `https://127.51.68.120:8181`
    once) or `security.enterprise_roots.enabled`.
- **No loopback alias / hosts edit needed on Windows** — all of `127.0.0.0/8` is loopback, so we bind
  `127.51.68.120` directly (verified). (Linux needs an alias; the spacenav-ws launchers add one.)
- **Onshape side:** the SpaceMouse / 3Dconnexion option must be enabled in Onshape's preferences, and
  Onshape only probes the local service when `navigator.platform` is Windows/Mac — which is already
  true on Windows, so **no browser userscript is needed** (unlike the Linux projects).
- `integrations.setup_onshape` generates the cert and returns the exact trust steps; it never
  installs into a trust store on its own.

---

## 8. GOTCHAS & SOLVED PROBLEMS (read this)

These are the non-obvious things, each as *symptom → cause → fix*. Most cost real debugging time.

### 8.1 The affine convention is ROW-vector, not column — and it differs from the 3Dconnexion sample
- **Symptom:** orbit produced garbage / the model jumped to nowhere; debug showed the decoded eye as
  `(0,0,0)`.
- **Cause:** the only HAR capture available was the 3Dconnexion **three.js sample**, whose
  `view.affine` puts the **translation in the last column** (indices 3,7,11) — so the code originally
  defaulted to that. **Real Onshape uses the opposite (row-vector) layout**: translation in the last
  **row** (indices 12,13,14), basis as rows. Decoding with the wrong layout reads eye = the last
  column = `(0,0,0)` and a transposed basis.
- **How it was found:** with debug logging, the live affine's last column was `[0,0,0]` → that must be
  the homogeneous `[0,0,0,1]` column → translation is in the last row. Confirmed by checking
  `right × up == back` for the row interpretation.
- **Fix:** `AFFINE_TRANSLATION_IN_COLUMN = False`. The constant is kept as a one-line escape hatch:
  if a *different* navlib app ever drives this bridge and orbit/pan come out transposed, flip it.

### 8.2 Routing by window title silently drops every frame (the "connected but nothing moves" bug)
- **Symptom:** the 3D-Apps row shows **connected**, but moving the ball does nothing.
- **Cause:** `app.py::_foreground_app_key` originally required the foreground window **title to contain
  "Onshape"**. But the browser titles the tab with the **document** name —
  e.g. `monstera leaf | Part Studio 1 — Mozilla Firefox` — which contains no "Onshape". So
  `_active_app_key` returned `None` and `_nav_sink` dropped every frame before it reached the bridge.
- **Fix:** route to `onshape` when the **foreground process is a browser** (`_BROWSER_PROCS`:
  chrome/msedge/firefox/brave/opera/vivaldi) **AND the bridge is connected**. The precision ("is the
  user actually in the Onshape tab") comes from Onshape's own **focus** signal (§8.4), gated in the
  worker — not from the title. `winfocus.foreground_window_title()` still exists but is no longer used
  for routing.

### 8.3 Orthographic zoom "rubber-bands" (applies for one frame then snaps back)
- **Symptom:** zoom in/out is visible for a frame, then the view springs back; it "fights itself".
- **Cause:** in orthographic mode the magnification lives in `view.extents`, so the code wrote **only**
  `view.extents`. But Onshape treats the **`view.affine` write as the frame's commit** — a frame
  without an affine write is reverted on the next frame. Secondarily, `view.extents` was read from a
  0.2s cache, so successive frames scaled from a stale value (steppy).
- **Fix:** on every ortho-zoom frame, **also re-write the (unchanged) `view.affine`** alongside the
  new extents (this matches the real NL-Proxy, which writes `view.affine` on *every* motion frame),
  and **read `view.extents` fresh** so zoom compounds. See `_navigate` (ortho branch sets
  `changed_affine = True`) and `_zoom_ortho` (no TTL on the extents read).
- **General rule:** write `view.affine` on every motion frame. If you add a new motion type, don't
  skip it.

### 8.4 Onshape's `focus` flag is reliable — use it, not heuristics
- Onshape sends `3dx_rpc:update {"focus":true/false}` as its 3D view gains/loses focus (verified
  `focus -> True` live). The worker only applies motion when `conn.focus` (or `_force_focus` for
  tests). This is the authoritative "the user is in the Onshape viewport" signal and is what makes
  §8.2's looser routing safe — a backgrounded Onshape tab reports `focus:false` and won't be moved.

### 8.5 `view.target` is a poor orbit pivot (why §6 exists)
- **Symptom:** "view" orbit swung about an arbitrary point in space.
- **Cause:** `view.target` sits on the optical axis but at an **arbitrary depth** — measured ~1.5
  units off the actual surface in one test. Orbiting about it swings the model.
- **Fix:** the hit-test pivot (§6). `view.target` is no longer used.

### 8.6 navlib hit-test specifics that aren't obvious
- `hit.lookfrom`/`hit.direction`/`hit.aperture` are **write-only**; reading them returns "unknown
  property" — that is expected, **not** a failure.
- `pointer` is **not exposed**, so a true cursor-position pivot is impossible; `cursor` uses screen
  center (same as `view`).
- A **no-hit** result is not clearly signalled, so always validate `hit.lookat` against the model
  bbox (`_valid_hit`).
- **Cache-poisoning trap:** do **not** read `hit.lookat` through the caching `conn.read()` — a no-hit
  CALLERROR would get cached in `conn._unsupported` and permanently disable the read. Use
  `conn._rpc("self:read", ["hit.lookat"])` directly and catch `_PropUnsupported` per attempt. Only
  the hit.* **writes** failing means the build lacks hit-testing (sets `conn._hit_unsupported`, which
  stops further attempts).

### 8.7 Capture the pivot once per gesture and hold it
- Re-raycasting every frame both chases the moving view (the pivot would crawl) and adds round-trips.
  `_held_pivot` is captured on the first orbit frame and held; pan/zoom and idle>0.35s invalidate it.
  Mirrors the SolidWorks driver's pivot-hold.

### 8.8 Onshape is orthographic by default
- `view.perspective` is `false`. The orthographic zoom path (scale extents, §8.3) is the one that
  matters; the perspective dolly path exists but is rarely exercised. Test zoom in **ortho**.

### 8.9 Cert trust is per-browser; Chrome ≠ Firefox
- Chrome/Edge read the Windows store (so `certutil -user -addstore Root` works); **Firefox does not**
  (own NSS store). A user can have the cert trusted in one browser and not the other. The IP **SAN is
  mandatory** — browsers ignore CN.

### 8.10 Windows loopback & `navigator.platform`
- Binding `127.51.68.120` needs no configuration on Windows (whole 127/8 is loopback). And no
  userscript is needed (platform is already Win32). Both are Linux-only headaches.

### 8.11 Client-lib version varies / be version-tolerant
- Normal is client lib **v0.6.0**. A stale/odd reconnect once reported **v0.3.11 / "v0"**. The
  handshake does not gate on version — keep it that way.

### 8.12 EVENT topic must be the SHORT prefixed form
- Publish EVENTs to `3dconnexion:3dcontroller/<instance>` (the short form the client subscribed to),
  not the resolved long URI. The real proxy uses the short form.

### 8.13 RPC timeout = disconnect
- If the client doesn't answer a read/write within `_RPC_TIMEOUT` (2s), we treat the connection as
  dead and drop it. A wedged/backgrounded tab will disconnect rather than hang the worker.

---

## 9. Tuning knobs (top of `onshape_bridge.py`)

Current values (expect a sign/feel pass on real hardware — flip signs if a channel goes the wrong way):

| Constant | Value | Meaning |
|---|---|---|
| `ORBIT_SIGN` | `(-1,-1,1)` | per-channel orbit direction (pitch/yaw/roll) |
| `WORLD_UP` | `(0,1,0)` | turntable azimuth axis — **verify**: Onshape scene up may be Y or Z; if turntable tilts verticals, switch to `(0,0,1)` |
| `PAN_SIGN` / `PAN_SCALE` | `(1,-1)` / `0.14` | pan direction / magnitude (fraction of view half-extent) |
| `ZOOM_SIGN` / `ZOOM_SCALE` | `1.0` / `0.25` | zoom direction / magnitude |
| `AFFINE_TRANSLATION_IN_COLUMN` | `False` | §8.1 — row-vector for Onshape |
| `HIT_APERTURES` | `(0.03,0.1,0.3)` | hit-test ray thicknesses (× view half-extent), widened in order |
| `_MOTION_IDLE` | `0.35` | seconds idle before the gesture ends / pivot re-picks |
| `_RPC_TIMEOUT` | `2.0` | §8.13 |

Per-app sensitivity/invert/scheme and the viewport rate come from config (Per-App Bindings), same as
the other apps. `config.data["onshape"]` holds `{address, port, cert_path, key_path}` (additive
block; blank cert paths → the generated defaults).

---

## 10. Diagnostics & how to debug

- **`TB_ONSHAPE_DEBUG=1`** (env): logs focus changes, one camera read/write per gesture
  (`onshape nav: delta=… persp=… eye_in=… held_pivot=…`), and every CALLERROR. A good "is it
  navigating, and where's the pivot?" view. Off by default (no spam).
- **`TB_ONSHAPE_SPIN=<radians>`** (env): injects a continuous synthetic orbit whenever a client is
  connected (and **forces focus**), so you can validate the **bridge→Onshape camera path end-to-end
  without the BLE device or the routing/focus gates**. The model should orbit on its own.
- **Standalone:** `python -m trackball_daemon.onshape_bridge [--spin] [--force]` runs just the bridge
  (separate from the daemon). `--force` drives even if Onshape reports unfocused.
- **Restarting the running daemon with env vars** (it's launched as `pythonw -m trackball_daemon`):
  stop the `pythonw` process whose command line contains `trackball_daemon`, then relaunch from the
  repo dir with the env var set. Logs go to `%APPDATA%\TrackballDaemon\daemon.log`.
- **What a healthy session looks like in the log:** `onshape: created 3dcontroller for client
  'Onshape' v0.6` → `onshape: client subscribed` → `onshape: focus -> True` → (on motion)
  `onshape nav: … held_pivot=(a point ON the model)`.
- **Verifying accessors before relying on them** (the lesson from §8.1): a throwaway probe that writes
  candidate accessors and logs whether Onshape ACKs or returns "unknown property" is the fast way to
  confirm the protocol against a new Onshape version. (One was used to confirm the hit-test exists.)
- **The `127.51.68.120:8181` port:** if it won't bind, the real 3DxWare driver is probably installed
  and owns it (the user has no SpaceMouse, so normally it's free).

---

## 11. Status & known limitations (at handoff)

- **Working:** TLS + handshake + connection status; orbit with the hit-test "view" pivot; ortho zoom
  (rubberband fixed); pan; control scheme (view/object/origin/cursor, free/turntable, zoom modes).
- **Needs a feel/sign pass on hardware:** orbit/pan/zoom directions and magnitudes (`*_SIGN`,
  `*_SCALE`), and the turntable `WORLD_UP` axis (Y vs Z) — verify and flip as needed.
- **Limitations:** no cursor-position pivot (`pointer` not exposed → `cursor` == `view`); zoom-to-
  object/cursor for ortho is approximate (falls back toward center); Firefox needs its own cert trust;
  perspective path is lightly tested (Onshape defaults to ortho). There is **no add-in** to install or
  update for Onshape — it's all the in-process bridge.

---

## 12. File / wiring map

- `onshape_bridge.py` — the driver: WS+WAMP server (`_OnshapeConn`), the worker/camera math
  (`OnshapeBridge`), cert generation, the standalone spike.
- `app.py` — constructs `self.onshape_bridge`, starts/stops it, routes `onshape` frames in
  `_nav_sink`, browser-based focus detection in `_foreground_app_key`, status merge in
  `_on_onshape_connection_changed` / `_refresh_connected_apps`, rate/scheme push in
  `_apply_rates`/`_apply_schemes`.
- `config.py` — additive top-level `"onshape"` block (no migration / version bump).
- `integrations.py` — `setup_onshape` (generate cert + trust instructions; wired as the AppDef
  `setup`). Onshape is **not** in `_ADDINS` (no add-in to copy/auto-update).
- `ui.py` — the 3D-Apps row + Per-App Bindings render via the generic no-add-in path
  (`appdef.setup is not None`, not in `ADDIN_KEYS`).
- `winfocus.py` — `foreground_process_name` (used for routing); `foreground_window_title` (added for
  the original title heuristic, now unused — see §8.2).
- `requirements.txt` — `cryptography` (win32 marker; recommended-but-optional, openssl is the
  fallback). The WSS server itself is stdlib-only (`ssl` + hand-rolled WebSocket).
