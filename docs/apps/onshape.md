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
the same surface (`submit/set_rate/set_scheme/set_pivot_hold/set_zoom_hold/start/stop/is_connected/version` +
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
- Degrades gracefully: missing/invalid cert → server doesn't start (logged once), submit/flush
  become no-ops, rest of the daemon runs. Windows-only behaviour is guarded; the module imports on
  non-Windows.

---

## 3. Endpoint & discovery

- Host/port: **`127.51.68.120:8181`, HTTPS/WSS only** (Onshape is https, so mixed-content rules
  forbid `ws://`).
- HTTP discovery: Onshape first does `GET https://127.51.68.120:8181/3dconnexion/nlproxy`; we reply
  `{"port":8181,"version":"1.4.8.21486"}`. This is **cross-origin** (page is `https://cad.onshape.com`)
  so an allowed response carries CORS headers (`Access-Control-Allow-Origin`, echoing the request
  Origin only after `_allowed_web_origin` accepts HTTPS `onshape.com`/subdomains or the bridge's own
  local status origin). Other origins are rejected before HTTP handling or WebSocket upgrade; there
  is no wildcard reflected CORS.
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
- **Not exposed:** `pointer` — navlib gives us no cursor position. The daemon takes a **page-
  reported** `#canvas` NDC from a userscript (`/trackball/pointer`, §8.14) and aims the hit-test
  through that ray. The missing `pointer` accessor is *not* a blocker.
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

The local math constants are neutral. `OutputEngine` composes Onshape's immutable sign/scale
alignment from `trackball_daemon/host_profiles.json` with the user's Source/Invert/Gain settings
before calling the bridge. Keep camera-model facts such as `WORLD_UP` and the affine layout here;
keep suite calibration in the host profile so it is not applied twice. See
[`../default_profiles.md`](../default_profiles.md).

---

## 6. The orbit pivots: navlib hit-test raycast (`_hit_screen_center`, `_hit_cursor`, `_hit_ray`, `_pivot`)

`screen_center` orbits about **what's under the center of the screen** and `cursor` about **what's under the
mouse** — both like Onshape's own right-drag orbit. We use the navlib **hit-test**: write the ray
(`hit.lookfrom` origin, `hit.direction`, `hit.aperture` = ray **thickness**, `hit.selectionOnly=false`)
and read `hit.lookat` (the surface hit point). **Onshape does the actual raycast** (GPU/BVH — cheap on
their side). The ray-aim + aperture loop live in the shared `_hit_ray`; `_hit_screen_center` and `_hit_cursor`
just build the ray.

Algorithm:
1. **Build the ray from a canvas point** (`_pixel_ray`): given the point in NDC (x right, y up, both
   in [-1,1]), the ray runs along `direction = normalize(-back)` through the point offset
   `ndc_x·half_x` right + `ndc_y·half_y` up from the view center, with `lookfrom` pulled `view_half*8`
   back along forward (started outside the model). This is the **orthographic** ray (Onshape is ortho
   by default, §8.8); NDC = (0,0) reproduces the old screen-center ray exactly.
   - `screen_center` → NDC (0,0) (screen center).
   - `cursor` → page-reported `#canvas` NDC (§8.14); off-canvas / no userscript ⇒ the method is
     **unavailable** and the configured fallback chain continues.
2. Try apertures smallest-first (`HIT_APERTURES = (0.03, 0.1, 0.3)` × view half-extent), widening
   until something is hit ("expand the radius until it hits").
3. Validate the hit is a finite 3-point **essentially inside `model.extents`** (0.1% of the
   diagonal of slop, `_valid_hit`) — a real surface point always is. Onshape never signals a miss:
   it **fabricates `hit.lookat`** as a point on the pick ray at roughly the scene's camera distance
   (§8.6), and the old 10%-of-diagonal margin let those fabrications become "orbit about empty air"
   pivots near the model.
4. **Confirm the candidate is a real surface** (daemon 0.1.62): re-cast the SAME ray with
   `hit.lookfrom` slid `_CONFIRM_BACKOFF` (4 × view half-extent) further back and require the same
   world point within `_CONFIRM_TOL`. A real surface hit is invariant to the ray origin; a
   fabricated at-depth point tracks it. (A fabrication computed from Onshape's own camera would
   survive the re-cast — the strict bbox test in step 3 is the backstop for that case.)
5. No valid, confirmed hit at any aperture → the method is **unavailable**; resolution continues
   through the **configured fallback chain** (Global → Failure fallback order).
6. **Hold the pivot for the whole gesture** (`_held_pivot`): captured once on the first orbit frame,
   reused every frame, re-picked only after a pan/zoom or the configured Pivot hold expires. The hit-test therefore runs
   **once per gesture (a handful of round-trips), not per frame.**

Other pivots: `origin` = world origin; `object` = model centre; `selection` reads navlib's
`selection.extents`; when unavailable, resolution continues through the configured chain.
**`camera` and `cursor_3d` are unsupported and skipped**: Onshape has no 3D cursor, and its default
view is orthographic, where turn-in-place (rotating about the eye) degenerates to sliding the image
around — so a `camera` primary or chain entry simply falls through to the next candidate. A camera
primary still keeps its selection-override exemption (never hijacked by selection), the same
convention as Fusion/SolidWorks/FreeCAD, whose resolvers also skip camera. Daemon
0.1.58 wires `selection_overrides_pivot` into
the in-process bridge, so a non-empty selection replaces the designated orbit pivot. Onshape builds
that omit the optional selection properties safely continue through the designated-pivot path. The
scheme comes from `set_scheme` (Global → 3D control scheme, or per-app Onshape override).

Config v8 separates **Pivot hold** (`orbit_pivot_hold_sec`) from **Zoom hold**
(`zoom_cursor_hold_sec`). Pan and zoom invalidate the orbit pivot; pan preserves a held To Cursor
zoom target, while orbit invalidates it. Orbit ray misses remain strict and continue through the
explicit chain. To Cursor zoom has a different contract: if the cursor ray misses geometry, the
bridge intersects that ray with a view-facing plane at `view.target`, model-centre, or a safe view
depth. Empty space therefore keeps the cursor's screen position instead of silently becoming
To Center.

When the effective orbit style really transitions from `free` to `turntable` and
`level_horizon_on_entry` is enabled, `set_scheme` queues a single leveling pass for the next worker
step. It rebuilds camera right/up around the unchanged view direction and eye using world **+Z**
(Onshape's Top-plane normal). Ordinary turntable frames preserve the established horizon, startup
does not manufacture a transition, and the straight-up/down singularity is skipped.

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
  true on Windows, so **no userscript is needed for connection or screen-centre navigation**. The
  optional pointer userscript is required only for Under Cursor orbit/To Cursor zoom (§8.14).
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
  worker — not from the window title. The obsolete title-query helper was removed.

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
- `pointer` is **not exposed**, but the hit-test accepts an **arbitrary ray**, so a cursor-position
  pivot IS possible: the page reports exact `#canvas` NDC via userscript (`cursor` pivot, §8.14).
  Win32 window geometry alone cannot size the canvas on Firefox; screen capture is not used.
- A **no-hit** result is not signalled at all: Onshape **fabricates** `hit.lookat` as a point on
  the pick ray at roughly the scene's distance from the camera (observed live: "a point below the
  cursor the same distance from the camera as the objects"). Two guards keep fabrications from
  becoming pivots — `_valid_hit` requires the point essentially inside the model bbox (0.1% of the
  diagonal of slop; a real surface point always is), and `_hit_ray` **re-casts the same ray from
  further back** and requires the same world point (real surfaces are ray-origin-invariant;
  at-depth fabrications track the origin). Rejected ⇒ the method is unavailable and the configured
  fallback chain continues.
- **Cache-poisoning trap:** do **not** read `hit.lookat` through the caching `conn.read()` — a no-hit
  CALLERROR would get cached in `conn._unsupported` and permanently disable the read. Use
  `conn._rpc("self:read", ["hit.lookat"])` directly and catch `_PropUnsupported` per attempt. Only
  the hit.* **writes** failing means the build lacks hit-testing (sets `conn._hit_unsupported`, which
  stops further attempts).

### 8.7 Capture the pivot once per gesture and hold it
- Re-raycasting every frame both chases the moving view (the pivot would crawl) and adds round-trips.
  `_held_pivot` is captured on the first orbit frame and held; pan/zoom and the configured orbit
  hold invalidate it. `_held_zoom_pivot` has an independent Zoom hold used only by To Cursor;
  pan preserves it and orbit invalidates it. Mirrors the SolidWorks driver's split holds.

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

### 8.14 Under-cursor orbit: exact #canvas pointer from the page (✓ LIVE-VERIFIED)
The `cursor` pivot orbits about the surface **under the mouse**. Two halves:
- **Half B (the ray) — solid.** `_hit_cursor` → `_pixel_ray` → `_hit_ray` (same aperture/bbox/hold as `screen_center`).
- **Half A (mouse → canvas NDC) — page-reported, exact.** navlib exposes no pointer. Win32
  `GetCursorPos` + window geometry cannot recover the WebGL canvas on Firefox (client includes
  chrome; no content HWND). Screen-DC / BitBlt measurement was tried and rejected (inaccurate /
  user-forbidden). **`view.extents` aspect is not the canvas aspect** (live: `#canvas` ≈ 1.72 vs
  extents halves ≈ 0.98), so auto-left from extents was wrong and removed.

  **Why a userscript:** the only exact size is `document.getElementById("canvas").getBoundingClientRect()`
  inside the page. A Violentmonkey/Tampermonkey script (served at
  `https://127.51.68.120:8181/trackball/pointer.js`, also copyable from the daemon UI) posts canvas
  NDC to `/trackball/pointer`. Mousemove updates the coordinates, and a short interval republishes
  the last on-canvas sample so it remains fresh while the physical pointer is stationary after an
  orbit or pan. The bridge caches samples (~0.75 s TTL). Off-canvas / stale / missing makes the
  orbit method unavailable and the configured fallback chain continues; To Cursor zoom can
  synthesize a target only when a fresh on-canvas ray exists.

- **Install (daemon UI):** 3D Apps → Onshape → Set up shows cert + SpaceMouse steps plus
  **Copy userscript** and the install list. Per-App Bindings → Onshape has a dedicated
  **Copy userscript…** button (copies immediately, then shows “Copied!” + steps). Choosing Orbit
  pivot = **cursor (under mouse)** opens a one-time warning with the same copy/steps and an
  optional **Do not show again** checkbox (`onshape.cursor_userscript_warn_dismissed`).
- **Manual steps:** install Tampermonkey/Violentmonkey → new script → paste → save → reload Onshape.
  Optional check: `GET /trackball/pointer` should show updating `ndc_x`/`ndc_y`.
- **Verified live** (daemon 0.1.56+): under-cursor orbit works; residual error is small / mostly
  imperceptible. Offline tests cover ray/parse/TTL/`_hit_cursor`/fallbacks and the extents≠canvas
  aspect lock (`tests/test_onshape_cursor_pivot.py`).

#### Simpler install alternatives (not shipped yet)
Listed for future UX work — current path is copy-from-daemon + userscript manager:

| Approach | User effort | Notes |
|---|---|---|
| Fold deeper into Set up (already partially done) | One dialog | Copy button + expandable Instructions |
| Greasy Fork / GitHub raw + `@updateURL` | One “Install” click | Needs hosted script + version sync |
| Bookmarklet | Drag bookmark; click per tab | No extension; easy to forget |
| Tiny Firefox/Chrome extension | “Add to browser” once | Best long-term UX; review/signing cost |
| Warn when `cursor` selected but no samples | Zero install change | Makes failure obvious (complementary) |

### 8.15 "Connects in Firefox but not Chrome/Edge" — Private Network Access
- **Symptom:** the bridge links from Firefox but a Chromium browser (Chrome/Edge) never connects, even
  with the cert trusted in the Windows store.
- **Cause:** Chromium enforces **Private Network Access (PNA)** — a page on a *public* origin
  (`cad.onshape.com`) connecting to a *loopback* address (`127.51.68.120`) is blocked unless the local
  server opts in. Chromium sends an `OPTIONS` **preflight** carrying
  `Access-Control-Request-Private-Network: true` and requires `Access-Control-Allow-Private-Network:
  true` in the response. Firefox doesn't enforce PNA yet, so it "just works" there — the classic
  split-by-browser tell. (Cert trust is a *separate*, per-browser axis, §8.9 — Chrome reads the Windows
  store, Firefox its own; both must be satisfied.)
- **Fix:** the bridge now sends `Access-Control-Allow-Private-Network: true` on **every** CORS response
  (`_http`), harmless to Firefox. `TB_ONSHAPE_DEBUG=1` logs each `onshape HTTP< METHOD /path
  (origin=… pna_req=…)` so you can see the preflight arrive. If Chrome still won't connect, check its
  **DevTools → Console/Network** for a cert error (`NET::ERR_CERT_*` → re-trust the cert in the Windows
  store, §7) vs. a PNA error, and that the SpaceMouse/3Dconnexion option is enabled in Onshape.

---

## 9. Runtime constants and host alignment

The bridge's local motion multipliers are neutral because the daemon has already composed the
developer-owned Onshape profile with the user's bindings. Effective calibration lives in
`trackball_daemon/host_profiles.json`; see [`../default_profiles.md`](../default_profiles.md).

| Constant | Value | Meaning |
|---|---|---|
| `ORBIT_SIGN` | `(1,1,1)` | neutral local multiplier; effective factors come from `host_profiles.json` |
| `WORLD_UP` | `(0,0,1)` | Onshape Top-plane normal; shared by turntable yaw and one-shot horizon leveling |
| `PAN_SIGN` / `PAN_SCALE` | `(1,1)` / `1.0` | neutral local multiplier; effective pan alignment comes from the host profile |
| `ZOOM_SIGN` / `ZOOM_SCALE` | `1.0` / `1.0` | neutral local multiplier; effective zoom factor comes from the host profile |
| `AFFINE_TRANSLATION_IN_COLUMN` | `False` | §8.1 — row-vector for Onshape |
| `HIT_APERTURES` | `(0.03,0.1,0.3)` | hit-test ray thicknesses (× view half-extent), widened in order |
| `_CONFIRM_BACKOFF` / `_CONFIRM_TOL` | `4.0` / `1e-3` | §8.6 fabricated-no-hit guard: extra ray-origin slide for the confirmation re-cast / agreement tolerance (both × view half-extent) |
| `_POINTER_TTL` | `0.75` | §8.14 — max age of a page-reported `#canvas` NDC sample |
| `_MOTION_IDLE` | `0.5` | fallback used when either configured hold value is absent/invalid |
| `_RPC_TIMEOUT` | `2.0` | §8.13 |

Per-app sensitivity/invert/scheme, the two hold values, horizon-entry override, and viewport rate
come from config (Per-App Bindings), same as the other apps. `config.data["onshape"]` holds
`{address, port, cert_path, key_path}` (additive
block; blank cert paths → the generated defaults). Under-cursor orbit needs the userscript from
`/trackball/pointer.js` (no canvas-inset calibration).

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

- **Working:** TLS + handshake + connection status; orbit with the hit-test `screen_center` pivot; under-mouse
  **`cursor` pivot** (daemon 0.1.57 — page userscript posts exact `#canvas` NDC; **live-verified**,
  small residual inaccuracy; install via Copy userscript in Set up, the expanded Instructions panel,
  Per-App Bindings, or the cursor-pivot warning); ortho zoom (rubberband fixed); pan; control scheme
  (screen_center/object/origin/selection/cursor, free/turntable, zoom modes — no `camera`: Onshape
  is orthographic, so turn-in-place degenerates to an image slide and the method is skipped).
- **Needs a live interaction pass:** the §8.6 fabricated-no-hit guard (strict bbox + confirmation
  re-cast), stationary-pointer refresh, v8 independent holds/synthetic To Cursor target, and
  free→turntable +Z leveling were added after the original live cursor verification.
- **Limitations:** the fabricated-no-hit guard (daemon
  0.1.62) is offline-tested only — live-verify that real screen-centre hits still land and that
  empty-space misses now fall through the configured chain (a fabrication computed from Onshape's
  own camera rather than the ray origin would only be caught by the bbox test);
  Firefox needs its own cert trust; perspective path is lightly tested (Onshape defaults to ortho).
  Under-cursor orbit requires the `/trackball/pointer.js` userscript (daemon UI copies it). There is
  **no add-in** to install or update for Onshape — it's all the in-process bridge.

---

## 12. File / wiring map

- `onshape_bridge.py` — the driver: WS+WAMP server (`_OnshapeConn`), the worker/camera math
  (`OnshapeBridge`), cert generation, the standalone spike.
- `app.py` — constructs `self.onshape_bridge`, starts/stops it, routes `onshape` frames in
  `_nav_sink`, browser-based focus detection in `_foreground_app_key`, status merge in
  `_on_onshape_connection_changed` / `_refresh_connected_apps`, rate/scheme push in
  `_apply_rates`/`_apply_schemes`.
- `config.py` — top-level `"onshape"` endpoint/certificate block plus the shared
  `apps.onshape` profile. Config v8 owns the split hold migration; the per-app nullable horizon
  value inherits the Global value until explicitly changed.
- `integrations.py` — `setup_onshape` (generate cert + trust instructions; wired as the AppDef
  `setup`). Onshape is **not** in `_ADDINS` (no add-in to copy/auto-update).
- `ui.py` — the 3D-Apps row + Per-App Bindings render via the generic no-add-in path
  (`appdef.setup is not None`, not in `ADDIN_KEYS`).
- `winfocus.py` — `foreground_process_name`, used for browser-process routing (see §8.2).
- `requirements.txt` — `cryptography` (win32 marker; recommended-but-optional, openssl is the
  fallback). The WSS server itself is stdlib-only (`ssl` + hand-rolled WebSocket).
