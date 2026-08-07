# Onshape 3D navigation bridge — maintainer's guide

This guide owns the Onshape bridge protocol, browser/camera API facts, setup, threading, and earned
warnings. Shared focus, state, mapping, routing, and lifecycle contracts are defined in
[`../architecture.md`](../architecture.md).

Primary code: [`trackball_daemon/onshape_bridge.py`](../../trackball_daemon/onshape_bridge.py). Wiring:
`app_registry.py`, `navigation_router.py`, `config_store.py`, `integrations.py`, and `app.py`.

---

## 1. What it is, in one paragraph

Onshape runs in a browser and has **native 3Dconnexion SpaceMouse support**: its page loads a
3Dconnexion JavaScript client that (on Windows/Mac) connects to a local "NL-Proxy" service at the
loopback endpoint `127.51.68.120:8181` over a **TLS WebSocket** speaking a **WAMP v1** protocol.
We don't own a SpaceMouse, so instead of faking mouse drags we **stand up our own server
impersonating that NL-Proxy**. Onshape connects to us, exposes accessors for its camera/scene, and
**we run the navigation model**: read the camera, apply the trackball's orbit/pan/zoom about a
pivot, write the new camera back. `OnshapeBridge` is a direct transport parallel to the socket broker;
`NavigationRouter` is the sole delivery boundary and sends it only active, revision-matched Onshape
envelopes.

Prior art that made this possible: **`RmStorm/spacenav-ws`** (Python; same endpoint, reverse-engineered
the same traffic for Linux) — we reused its **protocol** findings and its **HAR captures of a real
NL-Proxy session**, but not its camera math (we reuse the Fusion add-in's vector math instead).

---

## 2. Architecture & threading

- `submit(ox,oy,oz,px,py,zoom)` is called by `NavigationRouter` on the navigation-delivery path; it
  only accumulates a six-float delta under a lock. It never blocks and never touches the socket.
- A **server thread** runs the TLS accept loop on `127.51.68.120:8181`. Each accepted connection
  gets a **reader thread** (`_OnshapeConn.serve`) that does the HTTP/WS handshake, then parses WAMP
  frames: it answers the client's create/subscribe/focus calls and resolves our read/write replies
  by `call_id` (each pending RPC has a `threading.Event`).
- A **worker thread** (`_run_worker`) waits until a client is subscribed+focused, then at `rate_hz`
  coalesces the accumulated delta and runs **one navigation step** (`_navigate`): read `view.affine`,
  apply the camera change, write it back — wrapped in the `motion`/`transaction` framing. **All
  network round-trips happen on this worker, never on the producer/delivery path.**
- `on_connection_changed(connected, version)` fires on handshake-complete / disconnect so the
  tray/UI status updates (exactly like the SolidWorks COM driver).
- Degrades gracefully: missing/invalid cert → server doesn't start (logged once), submit/flush
  become no-ops, rest of the daemon runs. Windows-only behaviour is guarded; the module imports on
  non-Windows.

---

## 3. Endpoint & discovery

- Host/port: **`127.51.68.120:8181`, HTTPS/WSS only**. The complete endpoint is a fixed loopback
  security boundary: config validation and `OnshapeBridge` reject any other bind address or port.
  Onshape is HTTPS, so mixed-content rules forbid `ws://`.
- HTTP discovery: Onshape first does `GET https://127.51.68.120:8181/3dconnexion/nlproxy`; we reply
  `{"port":8181,"version":"1.4.8.21486"}`. This is **cross-origin** (page is `https://cad.onshape.com`)
  so an allowed response carries CORS headers (`Access-Control-Allow-Origin`, echoing the request
  Origin only after `_allowed_web_origin` accepts HTTPS `onshape.com`/subdomains or the bridge's own
  local status origin). Other origins are rejected before HTTP handling or WebSocket upgrade; there
  is no wildcard reflected CORS.
- WebSocket: path `/`, **subprotocol `wamp`** (echoed in our 101 response). The hand-written reader
  requires masked client frames, validates RSV/opcodes/fragmentation/control-frame rules and UTF-8,
  and bounds frame, aggregate-message, and fragment counts before buffering payloads.
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
- Preserve a focus update whether it arrives before or after SUBSCRIBE; subscription and viewport
  focus are separate pieces of state.
- Bound each incoming text message, including the total of all fragments, to 1 MiB. An individual
  data frame uses that same ceiling: fragmentation is a wire-format choice and must not make an
  otherwise valid message acceptable or unacceptable.

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

### Accessors supported by the current Onshape client
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
add-in**
([`trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py`](../../trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py)):
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
4. **Confirm the candidate is a real surface:** re-cast the SAME ray with
   `hit.lookfrom` slid `_CONFIRM_BACKOFF` (4 × view half-extent) further back and require the same
   world point within `_CONFIRM_TOL`. A real surface hit is invariant to the ray origin; a
   fabricated at-depth point tracks it. (A fabrication computed from Onshape's own camera would
   survive the re-cast — the strict bbox test in step 3 is the backstop for that case.)
5. No valid, confirmed hit at any aperture → the method is **unavailable**; resolution continues
   through the **configured fallback chain** (**Global → Orbit → Orbit pivot fallback order**).
6. **Hold the pivot for the whole gesture** (`_held_pivot`): captured once on the first orbit frame,
   reused every frame, re-picked only after a pan/zoom or the configured Pivot hold expires. The
   hit-test therefore runs **once per gesture (a handful of round-trips), not per frame.**

Other pivots: `origin` = world origin; `object` = model centre; `selection` reads navlib's
`selection.extents`; when unavailable, resolution continues through the configured chain.
**`camera` and `cursor_3d` are unsupported and skipped**: Onshape has no 3D cursor, and its default
view is orthographic, where turn-in-place (rotating about the eye) degenerates to sliding the image
around — so a `camera` primary or chain entry simply falls through to the next candidate. A camera
primary still keeps its selection-override exemption (never hijacked by selection), the same
convention as Fusion/SolidWorks/FreeCAD, whose resolvers also skip camera. The registered Selection
override setting is consumed by the daemon-side direct bridge, so a non-empty selection replaces the
designated orbit pivot. Onshape builds
that omit the optional selection properties safely continue through the designated-pivot path. The
scheme comes from the resolved Global/app settings published through `set_scheme`.

**Pivot hold** (`orbit_pivot_hold_sec`) and **Zoom hold**
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
  stored at `%APPDATA%\Mildly Useful\Astrolabe\onshape_cert.pem` / `onshape_key.pem`. The WSS server loads
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
- Browsers may open and abandon auxiliary TLS sockets while one controller remains subscribed.
  Only an explicit TLS certificate alert can report rejected trust, and it cannot demote an already
  healthy controller. EOFs, resets, and wrong-protocol probes are ignored; a normal WebSocket close
  returns the bridge to waiting for a reconnect.

---

## 8. Load-bearing warnings

These are current protocol and camera invariants. Completed probe narratives and rejected approaches
belong under `archive/`; keep this section concise.

### 8.1 Decode `view.affine` as a row-vector transform

Real Onshape places translation in indices 12–14 and stores the camera basis as rows.
`AFFINE_TRANSLATION_IN_COLUMN` must remain `False` for this bridge. The 3Dconnexion sample uses a
different layout and is not authority for Onshape.

### 8.2 Browser process and bridge focus form one context

`app_registry.resolve_foreground_context` selects Onshape only when a supported browser is foreground
and its connected controller explicitly reports viewport focus. Browser titles are not reliable
Onshape identifiers. A WAMP focus transition forces the foreground monitor to re-resolve context
even while the browser process and ball are stationary, keeping Onshape-scoped bindings, HUD state,
mapping, delivery, and the 3D Apps active row aligned. A background tab may keep its transport
subscribed and healthy, but connection or subscription alone never grants Onshape context or an
active status.

### 8.3 Write `view.affine` on every motion frame

Orthographic magnification lives in `view.extents`, but the affine write commits the frame. Every
orthographic zoom frame must read extents fresh, write the new extents, and re-write the unchanged
affine. New motion types must preserve the same commit rule.

### 8.4 Use explicit WAMP `focus` as the viewport gate

Onshape sends `3dx_rpc:update {"focus":true/false}` as the 3D view gains or loses focus. Preserve and
honor the update regardless of whether it precedes SUBSCRIBE. Apply camera motion only when
`conn.focus` is true (or `_force_focus` in the standalone diagnostic), and publish focus transitions
to the daemon context gate. Do not infer focus from subscription, window titles, or browser process
identity alone.

### 8.5 Do not use `view.target` as the orbit pivot

`view.target` is on the optical axis at an arbitrary depth, so it cannot anchor a surface-preserving
orbit. Orbit pivots come from the configured hit-test chain. `_cursor_depth_point` may still read
`view.target` as a depth fallback for synthesized To Cursor zoom; that does not make it an orbit pivot.

### 8.6 Treat hit testing as an adversarial protocol boundary

- `hit.lookfrom`, `hit.direction`, and `hit.aperture` are write-only; failed reads are expected.
- Navlib exposes no pointer, but it accepts an arbitrary ray. Under Cursor therefore uses page-reported
  `#canvas` NDC rather than browser-window geometry.
- Onshape can fabricate `hit.lookat` for empty space. Accept a point only when it is inside the model
  bounds and a second cast from farther back resolves to the same world point; otherwise continue the
  fallback chain.
- Read `hit.lookat` with direct `_rpc("self:read", ...)`, not caching `conn.read()`, so an expected
  no-hit error cannot permanently poison capability detection.

### 8.7 Capture pivots once per gesture

Capture `_held_pivot` on the first orbit frame and invalidate it on pan, zoom, hold expiry, or scheme
change. `_held_zoom_pivot` is independent: pan preserves it and orbit invalidates it. Recasting every
frame chases a moving view and adds round-trips.

### 8.8 Qualify orthographic behavior explicitly

Onshape normally reports `view.perspective == false`. The orthographic extents path is therefore the
primary zoom path; do not treat perspective-only coverage as host qualification.

### 8.9 Certificate trust is browser-specific

Chrome and Edge use the Windows certificate store; Firefox uses its own NSS store unless enterprise
roots are enabled. The certificate must contain the loopback IP as a SAN. Trust in one browser does not
establish trust in another.

### 8.10 Keep the fixed Windows loopback endpoint

Windows treats all of `127.0.0.0/8` as loopback, so `127.51.68.120` needs no alias. Onshape already sees
a supported Windows platform, so the pointer userscript is required only for Under Cursor behavior,
not for the base bridge connection.

### 8.11 Keep negotiation capability-based

Treat client-library version strings as diagnostic metadata. Do not gate the handshake on a copied
version number.

### 8.12 Publish the subscribed short EVENT topic

Publish to `3dconnexion:3dcontroller/<instance>`, not the resolved long URI.

### 8.13 An RPC timeout ends the connection

If a read or write exceeds `_RPC_TIMEOUT`, drop the connection so a wedged or backgrounded tab cannot
block the worker.

### 8.14 Under Cursor requires exact page-reported canvas coordinates

Navlib does not expose the pointer, and browser-window geometry does not identify the WebGL canvas.
The served Tampermonkey/Violentmonkey script reads `#canvas.getBoundingClientRect()` in the page and
posts NDC to `/trackball/pointer`. The bridge accepts only fresh on-canvas samples; stale or missing
samples make the pivot unavailable so the fallback chain continues.

`mousemove` only updates the script's local state — a `SEND_MS` (100 ms) timer owns the transport,
and resends an unchanged sample every `REFRESH_MS` (300 ms) to stay inside `_POINTER_TTL`. The rate
is deliberately decoupled from the event: the bridge reads this once per gesture start, so posting
per pointer event bought nothing and cost 120+ cross-origin requests a second, each its own TLS
handshake and, pre-grant, its own Local Network Access prompt (§8.15).

Install through **3D Apps → Onshape → Set up** or the Per-App **Copy userscript…** action, then reload
Onshape. `GET /trackball/pointer` is a diagnostic view of the cached sample. Offline tests cover
parsing, TTL, ray construction, targeting, and fallback behavior; current live qualification belongs
in [`TODO.md`](../../TODO.md), with completed evidence under `archive/release-evidence/`.

### 8.15 Chromium gates the loopback bridge, first by preflight and now by permission

Two mechanisms, and a Chrome version decides which one is in force:

- **Private Network Access (older Chromium).** An `OPTIONS` preflight when `cad.onshape.com`
  connects to the loopback bridge; every CORS response must carry
  `Access-Control-Allow-Private-Network: true`. `_http` still sends it, because the browsers that
  want it are still in use.
- **Local Network Access (Chrome 142+, flag-gated from 138).** The preflight opt-in is replaced by a
  user permission: "cad.onshape.com wants to access other apps and services on this device". The
  server cannot grant it and no response header suppresses it — the user must Allow. Until the grant
  is remembered, *every* request from the page re-asks, so a page that talks to the bridge steadily
  makes the prompt appear to flicker. Tell users to tick **"Remember my choice for this site"**;
  one grant then covers the session and the prompt stops.

This is why the userscript's send rate is decoupled from `mousemove` (§8.14): at one request per
pointer event the pre-grant prompt is re-triggered ~120 times a second.

Certificate trust remains a separate requirement from both. Use browser DevTools and
`TB_ONSHAPE_DEBUG=1` to distinguish certificate, permission, and missing Onshape-option failures.

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

Per-app sensitivity, axis routing, scheme, independent holds, horizon entry, and viewport rate
resolve from sparse v9 settings. The immutable `ConfigSnapshot.onshape` block exposes validated
operational/certificate state; the protocol host remains fixed at `127.51.68.120`, and blank
certificate paths use generated defaults. Under-cursor orbit needs the userscript from
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
- **Restarting with environment variables:** use tray **Quit** first so providers, sockets, and held
  controls shut down cleanly, then relaunch from the repository with the variable set. Forced
  termination is recovery-only: identify the exact PID and verify its executable and complete command
  line before stopping it. Logs go to `%APPDATA%\Mildly Useful\Astrolabe\daemon.log`.
- **What a healthy session looks like in the log:** `onshape: created 3dcontroller for client
  'Onshape' <client-version>` → `onshape: client subscribed` → `onshape: focus -> True` (with
  `TB_ONSHAPE_DEBUG=1`) → on first successful motion, `onshape: orbit reached the view.affine camera
  write`. If pivot resolution prevents orbit from reaching that write, a one-shot message says so.
- **Verifying accessors before relying on them** (the lesson from §8.1): a throwaway probe that writes
  candidate accessors and logs whether Onshape ACKs or returns "unknown property" is the fast way to
  confirm the protocol against a new Onshape version. (One was used to confirm the hit-test exists.)
- **The `127.51.68.120:8181` port:** if it won't bind, the real 3DxWare driver is probably installed
  and owns it (the user has no SpaceMouse, so normally it's free).

---

## 11. Current capabilities and limitations

The direct bridge supports TLS/WAMP connection status, screen-center and under-mouse surface pivots,
orthographic zoom, pan, free/turntable orbit, origin/object/selection/cursor targets, and independent
Orbit/To-Cursor gesture holds. Camera pivot is not exposed because turn-in-place under Onshape's
normal orthographic projection degenerates into an image slide.

Firefox uses its own certificate store, while Chromium browsers also require the Private Network
Access response or the Local Network Access permission described in §8.15. Under-cursor behavior
requires the supplied
`/trackball/pointer.js` userscript. Onshape has no host add-in to install or update; the transport is
the daemon-side bridge. Current live qualification is tracked only in [`TODO.md`](../../TODO.md).

---

## 12. File / wiring map

- `onshape_bridge.py` — the driver: WS+WAMP server (`_OnshapeConn`), the worker/camera math
  (`OnshapeBridge`), cert generation, the standalone spike.
- `app.py` — bridge lifecycle, runtime profile publication, connection-status merge, and routing
  through `NavigationRouter`.
- `app_registry.py` — exact browser identities, bridge-focus-aware foreground context, modes, and
  capabilities.
- `config_store.py` / `settings_schema.py` — validated operational Onshape state and sparse typed
  settings. Legacy v8 reconstruction remains isolated in `config.py`.
- `integrations.py` — `setup_onshape` generates the certificate and returns explicit trust steps.
  Onshape is not in `_ADDINS`; there is no host add-in to copy or auto-update.
- `ui.py` — generated 3D Apps and Per-App surfaces through the generic no-add-in setup path.
- `winfocus.py` — read-only foreground process query used by `app_registry` context resolution.
- `pyproject.toml` — `cryptography` is a base Windows dependency, not an extra: setup has to be
  able to mint the certificate on any install, including a bare `uv run astrolabe` from a clone.
  `openssl` remains the fallback but cannot be relied on (Windows puts none on PATH). The WSS
  server itself is stdlib-only (`ssl` + hand-rolled WebSocket).
