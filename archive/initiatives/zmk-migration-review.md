# ZMK migration review

Independent review of the `codex/zmk-migration` working tree, 2026-08-02. This is a review
record, not a design document — [`zmk_migration_plan.md`](../../docs/zmk_migration_plan.md) owns the
design and [`TODO.md`](../../TODO.md) owns the release gates. The plan follows this file into
`archive/initiatives/` once the migration's live gates close.

## Note for other reviewers adding to this file

This repository depends on outside contributions, so **a finding's cost to fix is part of its
severity**. Before adding an item here:

- Prescribe the smallest change that addresses the cause. Prefer fixes that *delete* code or
  make two existing paths consistent over fixes that add machinery.
- Do not propose retry loops, backoff state, error-classification layers, watchdog timers, or
  sanity thresholds to cover a rare failure that an existing mechanism already recovers from.
  Say what you observed, say what would prove it matters, and stop there.
- Estimate reachability before assigning severity. If a path requires a state the code cannot
  actually reach, it is a footnote, not a finding.
- A comment explaining a non-obvious coupling is often the correct and complete fix. That is a
  legitimate outcome, not a cop-out.
- Unproven hardware behavior belongs in `TODO.md` as a test to run, not in the firmware as a
  filter. This mirrors the existing project stance: no magnitude caps or unearned filters
  without physical evidence.

If a finding's only available fix is disproportionate to its impact, record it as a
watch-item with the symptom to look for during bring-up, and explicitly say no code change is
recommended.

## Implementation status

Findings 1, 3, 4, 5, 6, 8, 10, 11 and the smaller items are **applied**. Finding 2 needed no code.
Two were **not** implemented as written, for reasons recorded at the finding itself:

| Finding | Outcome |
|---|---|
| 7 (`clear_local_session` leaves `tx_busy` set) | **Rejected.** The proposed one-line fix introduces a worse race than the one it closes. Comment added instead. |
| 9 (compliance apparatus precedes distribution) | **Deferred to the maintainer** — removing license material is a distribution decision, not a review call. Its nrfx sub-point was **incorrect** and is answered in place. |

After the changes: `uv run pytest` -> **1101 passed, 1 skipped** (8 fewer tests, all deleted
change-detectors); firmware rebuild -> **succeeded, 0 warnings**, byte-identical size to before,
confirming the firmware edits were comments only.

## Verification performed

Both were run against the working tree when this review was written:

- `uv run pytest` -> **1109 passed, 1 skipped**
- Full firmware rebuild from current source -> **succeeded, 0 warnings**;
  197968 B flash (24.41%), 44828 B RAM (17.10%)

Also confirmed: the provider-lease API migration is complete (every `accept_snapshot`,
`begin_session`, `disconnect`, and `run_if_current` caller in `trackball_daemon/` and `tests/`
passes a lease), and git hygiene is correct (28 files would be staged; the 75,939-file west and
build tree is properly ignored).

---

## Blocker

### 1. The `zmk_firmware` CI job cannot start -- malformed container digest

`.github/workflows/ci.yml:322`, repeated into build provenance at `:410`:

```
sha256:ba4e851529ac9a5cf6b02e7dd3297b78bd1b0e75546b9f1bb22023437836373
```

That is **63 hex characters; sha256 requires exactly 64**. The container reference is invalid,
so the job fails before any step runs. Everything downstream — SPDX inventories, the license
bundle, the VID/PID assertion, `sha256sums.txt` — belongs to a job that has never executed.

The contract test does not catch this: `tests/test_ci_contract.py:41` asserts only
`"zmk_firmware" in jobs`. The suite is green while the gate is unrunnable, which is why the
plan document's claim that CI builds in a pinned immutable image is currently unbacked.

The digest is copy-pasted into **three** locations — `ci.yml:322`, `ci.yml:410`, and the `IMAGE`
constant at `tests/test_zmk_contract.py:20` — all carrying the same 63 characters. Two tests
assert that the workflow contains that constant, and both pass. The mirror test propagated the
defect into a third file and then certified it; see finding 8.

**Fix:** recover the real digest
(`docker buildx imagetools inspect zmkfirmware/zmk-build-arm:3.5`) and use it in all three
places. Add a length assertion to the contract test — one line, and it is the only thing that
would have caught this class of error.

### 2. The committed build evidence was stale

`build/spdx-check2/zephyr/zmk.uf2` (19:06:08) predates the four most concurrency-critical
files:

| File | mtime |
|---|---|
| `firmware/zmk/module/include/astrolabe/route.h` | 19:07:10 |
| `firmware/zmk/module/src/route.c` | 19:07:12 |
| `firmware/zmk/module/src/gatt.c` | 19:07:13 |
| `firmware/zmk/module/src/usb.c` | 19:17:38 |

A rebuild from current source succeeds cleanly, so this is not a code defect. It is recorded
because combined with finding 1 it means no automated build had ever covered this code at
handoff. No action beyond re-running the build once CI works.

---

## Worth fixing

### 3. `_on_rotation` holds two locks across the whole motion pipeline

`trackball_daemon/devices/astrolabe_legacy.py:60-68` and
`trackball_daemon/devices/snapshot_provider.py:176-184`

`_on_rotation` takes `adapter._lock`, then calls `provider.run_if_current(...)`, which takes
`provider._publish_lock` and invokes the callback inside both. Previously `_on_rotation` took no
lock at all. At `send-interval-ms = 7` that is roughly 140 Hz holding both locks through the
full nav pipeline.

Three consequences: hot-path serialization against `subscriptions` / `connected` /
`disconnected`; a latent lock-order inversion — the provider calls `_publish_events` and
`_publish_health_callback` while holding `_publish_lock`, so any consumer that re-enters the
adapter would invert the hot path's order; and USB handover stall — `UsbTransport` needs that
same `_publish_lock` for `begin_session` / `disconnect`, so a slow BLE motion callback can push
attach cleanup past the firmware keepalive window and leave a false USB session that dies on
the next keepalive.

**Fix is a deletion of lock scope, not new machinery.** `_on_input` in
`astrolabe_fiveway.py:38-42` already checks the lease under the adapter lock, releases, then
calls the provider. Making `_on_rotation` match removes the adapter-lock hold. Completing the
same idea in `run_if_current` — check the lease under `_publish_lock`, drop the lock, then
invoke the callback — removes the handoff stall without adding queues or timeouts. Stale
callbacks remain harmless: the lease object is still the gate.

### 4. Handover order contradicts the documented contract

The plan document specifies "release the exact USB lease, mark external power absent, then
re-enable BLE." `trackball_daemon/devices/usb_transport.py:338-344` sets `enabled_event` before
`_publish_external_power(False)`, so BLE can reconnect and publish a battery percentage that is
then reclassified.

**Fix:** swap the two statements, or amend the sentence in the plan. Either is correct; pick
one so the code and the document agree. Do this together with finding 6 so teardown has one
coherent order.

### 5. Undocumented couplings that a future ZMK bump would break silently

All three are comment-only fixes. That is the appropriate and complete resolution.

- `firmware/zmk/module/src/usb.c:416,431` — binding `CONFIG_USB_HID_DEVICE_NAME "_1"` at
  `SYS_INIT(APPLICATION, 89)` works only because ZMK initializes at 94
  (`CONFIG_ZMK_USB_INIT_PRIORITY`) and 95 (`CONFIG_ZMK_USB_HID_INIT_PRIORITY`). Both the `_1`
  instance assumption and the priority headroom deserve a comment.
- `firmware/zmk/module/boards/shields/astrolabe/astrolabe.overlay:35` —
  `force-standalone-gpios` shares `gpio1 15` with kscan position 4. This is safe only because
  the route probe runs at `POST_KERNEL/40` and kscan at `POST_KERNEL/90`, so kscan reconfigures
  the pin last. Worth one line in the overlay.
- `firmware/zmk/config/astrolabe.conf:19-20` — `CONFIG_ENABLE_HID_INT_OUT_EP` and
  `CONFIG_HID_INTERRUPT_EP_MPS` are global Zephyr symbols. They also apply to ZMK's keyboard
  HID interface, giving it an unused OUT endpoint. Endpoint budget is fine on nRF52840; note
  that these are not scoped to the vendor interface.

### 6. DETACH is fire-and-forget, then BLE is re-enabled immediately

`trackball_daemon/devices/usb_transport.py:330-348`

On session end the daemon writes `COMMAND_DETACH` with `_write_command`, then under
`handover_lock` disconnects the provider, sets `enabled_event`, and closes the device — with no
ACK wait. Attach already uses `_request` and refuses to proceed without a matching ACK; detach
is the only ownership command that does not.

If that OUT is dropped, firmware keeps USB ownership until the 1500 ms keepalive. Meanwhile the
daemon believes fallback is enabled and BLE tries to claim, which firmware rejects with
`-EBUSY`. Result: a post-USB blackout for up to the keepalive window despite a clean-looking
teardown on the host.

**Fix:** use `_request(COMMAND_DETACH, ...)` with the existing ACK timeout (same path as attach
and keepalive). On timeout still continue teardown — firmware keepalive remains the fail-safe —
but do not set `enabled_event` until the ACK attempt has finished. No retry loop; one wait,
then proceed. Pair with finding 4's external-power ordering so the finally-block has one
agreed sequence: detach attempt → provider disconnect → external power false → enable BLE →
close.

### 7. `clear_local_session` leaves `tx_busy` set

`firmware/zmk/module/src/usb.c:131-143`

`clear_local_session` drops `usb_lease`, clears the keepalive deadline, and purges both TX
queues, but never `atomic_clear(&tx_busy)`. USB disconnect/suspend clear the flag
(`usb.c:407`); keepalive timeout, DETACH, and USB-over-BLE revoke go through
`clear_local_session` / `astrolabe_usb_route_revoked` and do not.

Reachable path: an IN report is accepted (`tx_busy = 1`) and the session is then revoked before
`int_in_ready` runs — for example the host closed the collection after a lost DETACH, or
keepalive fired while a transfer was outstanding. Later `send_ack` queues an attach ACK and
submits `tx_work`, but `tx_handler` fails `atomic_cas` and returns. Attach ACKs sit in a purged
world that can no longer drain until a bus-level disconnect clears the flag.

Watch-item A is adjacent (fatal `-EAGAIN` on write) but different: that path *does* clear
`tx_busy` before releasing ownership. This path releases ownership and leaves the flag stuck.

**Rejected — the fix is worse than the defect.** `tx_busy` means "an IN transfer is outstanding",
which is a property of the endpoint, not of the session. Clearing it while a transfer is still in
flight lets the next queued report be written concurrently, and `usb_write`'s `-EAGAIN` is exactly
what `tx_handler` treats as fatal — so the one-line fix can convert a transient wait into a
dropped session, which is watch-item A.

The premise is also weaker than it looks. `int_in_ready` clears the flag on transfer completion
and resubmits `tx_work`, and the host polls this endpoint every 1 ms
(`CONFIG_USB_HID_POLL_INTERVAL_MS=1`), so a queued ACK drains about a millisecond later rather
than waiting for a bus-level disconnect. The only state in which completions genuinely stop
arriving is suspend or disconnect, and `usb_event_listener` already clears the flag there
(`usb.c:407`) — which is the correct and only safe place for it.

**Applied instead:** a comment above `clear_local_session` recording why the flag is deliberately
left alone, so the next reviewer does not re-raise it.

---

## Contribution cost -- bloat and change-detector tests

Applying this file's own severity rule to the branch's non-firmware surface. These do not break
anything today; they charge a toll on every unrelated change a contributor makes. Every fix
below is a deletion.

### 8. `tests/test_zmk_contract.py` -- roughly 150 of 282 lines cannot fail on a real defect

Two anti-patterns in one file.

**Substring assertions against C source** (`tests/test_zmk_contract.py:237-282`):

```python
assert "return -EBUSY" in route
assert "k_msgq_purge(&data_queue)" in usb
assert 'device_get_binding(CONFIG_USB_HID_DEVICE_NAME "_1")' in usb
```

Nothing here compiles the firmware, so a `route.c` that does not build passes all of them. They
cannot fail when the firmware is wrong, and they will fail when a contributor renames a queue or
extracts a helper. `route.index(a) < route.index(b)` is worse: it asserts the *textual* order of
two strings as a proxy for execution order, which is a different property — both could sit in
dead code or in unrelated functions.

**Mirror assertions against `ci.yml`** (`:100-234`). `assert initialize["run"] == "west init -l
config"` asserts that a config file equals a copy of itself. Finding 1 is the proof: the copy at
`:20` carries the same 63-character digest, so the test compares wrong against wrong and passes.
A test that duplicates a value can only ever confirm the duplication.

The branch also deleted the comment that recorded this lesson. `tests/test_ci_contract.py`
previously read: "pinning them here failed on hardware iteration rather than on a CI regression.
`ci.yml` remains the authority for what gets compiled."

**Fix:** delete both groups. Keep the west-manifest check (`:53-65`) — asserting every revision
is a 40-character sha is a real invariant — and add the digest length assertion from finding 1.

### 9. The firmware compliance apparatus precedes the distribution that would require it

Roughly 8,600 lines of vendored license text under `LICENSES/third-party/`, a
`firmware_components` block in `third_party.json`, a firmware section in
`THIRD_PARTY_NOTICES.md`, eight `cmp` steps, SPDX init/generate, `sha256sums.txt` over 20 paths,
and three tests.

Redistribution obligations attach at distribution, and this branch says in three places that
nothing is distributed: `TODO.md:189` keeps the Arduino gate, `README.md:266` says not to present
the development VID/PID as release identity, and `zmk_migration_plan.md:239` blocks the
release-gate swap until the live matrix passes.

The cost is recurring and lands on the most likely outside contribution to this tree. Bumping the
ZMK pin breaks four independent checks for one change: the `cmp` steps, the sha256 test,
`third_party.json`, and the notices table. `LICENSES/third-party/picolibc.txt` alone is 6,097
lines, and `THIRD_PARTY_NOTICES.md:118` already concedes that most of it covers build scripts and
helpers which are not linked runtime.

**The nrfx sub-point is incorrect.** nrfx ships no top-level `LICENSE` file — verified against the
checked-out workspace, where `modules/hal/nordic/nrfx/` contains no license file at all and its
BSD-3-Clause notice lives in source headers. The same is true of micro-ecc, whose notice is
carried inside TinyCrypt. The `cmp` list is therefore not an incomplete gate: it covers exactly
the components that have a canonical upstream file to compare against, and the two that do not are
pinned by content hash in `test_zmk_contract.py`. A comment in `ci.yml` now records this so the
asymmetry does not read as an oversight.

**The main point stands and is left to the maintainer.** Deleting license and attribution material
is a distribution decision with legal consequences, not something a code review should action
unilaterally, so nothing here was removed. If deferral is chosen, the move is: keep building the
firmware and retaining artifacts, and relocate the notice bundle, `cmp` steps, and hash pinning
into the change that first makes the UF2 a release asset. Nothing is lost; it moves to where the
obligation begins.

### 10. `device-descriptor-v2.schema.json` is a forked file for a 12-line delta

It is byte-identical to v1 apart from `$id`, `title`, the `const`, a `uint16` definition, and the
`usb_hid` block. But v2 makes `usb_hid` **required**, and `devices/model.py:128-130` enforces both
directions — schema 1 must not carry it, schema 2 must.

That makes the version number a device-capability flag rather than a schema version. A future
BLE-only device is pinned to v1 permanently, and an existing v1 device cannot gain USB without a
bump that says nothing about format. The tell is that v1 was edited in place on this same branch
anyway (`device-descriptor-v1.schema.json:38`, `bit` maximum 2039 -> 255). If v1 is mutable,
forking v2 bought nothing and costs a duplicate edit on every future schema change.

**Fix:** make `usb_hid` optional in v1 and delete v2. Keeping v2 is defensible only if it drops
the requirement and becomes a genuine superset.

The `bit` bound change itself is correct and should stay: `model.py` allowed 2039 while
`devices/protocol.py:13` caps payloads at 32 bytes, so the old bound was unreachable by decode.

### 11. `firmware/zmk/build.yaml` is read by nothing

CI builds with an explicit `west build -b nice_nano_v2 -DSHIELD=astrolabe` (`ci.yml:344`). The
file's only consumer in this repository is `test_zmk_contract.py:92`, which asserts its contents.
It is the `zmk-config` convention for forked user-config repositories, which this nested
workspace is not.

**Fix:** delete the file and its test, or add the ZMK user-config workflow that would consume it.

### Smaller

- `trackball_daemon/app.py:218,230,240` use `getattr(self, "_external_power", False)` for an
  attribute assigned unconditionally in `__init__` (`:116`). The defensive default exists only to
  support tests that construct via `App.__new__(App)`. A class-level default removes the guard
  from production code without touching the tests.
- `zmk_migration_plan.md:224-237` restates `TODO.md`'s P4 gate list nearly item for item, four
  lines after declaring that `TODO.md` owns every unclosed gate. Two copies will drift; keep the
  one in `TODO.md`. The stage A-G matrix below is operational detail, not a third copy.
- `docs/security.md` inventories BLE input and states that BLE snapshots are untrusted, but never
  mentions the vendor-HID attach path. One short inventory row plus a sentence that USB attach is
  identity-matched only (shared development VID/PID today; see watch-item D) keeps the trust
  document aligned with the new transport. Doc-only.

---

## Watch-items -- observed, no code change recommended

These are real observations whose only available fixes are disproportionate to their impact.
Record the symptom, watch for it during hardware bring-up, and only act with physical evidence.

### A. A single failed HID write releases USB ownership

`firmware/zmk/module/src/usb.c:184-191`. `hid_int_ep_write` returns `-EAGAIN` when the endpoint
is busy; the handler treats every non-zero return as fatal, releases route ownership, and
purges `ack_queue` — which can discard the ACK the daemon is blocking on.

Why no fix is proposed: `tx_busy` already serializes writes, so normal-path `-EAGAIN` is
unlikely, and when it does happen the firmware keepalive (1500 ms) plus the daemon's 500 ms
rescan already recover the session. Adding transient-versus-fatal classification with retry and
backoff would be substantially more code than the roughly two-second hiccup it saves.

**Symptom to watch for:** repeated `USB disconnected; BLE fallback enabled` status flaps during
the suspend/resume and cable-pull matrices, with no physical cable event. If that appears, the
error path is being hit for real and is worth revisiting with evidence.

### B. Retained rotation is not flushed once polling stops

`firmware/zmk/module/drivers/pmw3610/pmw3610.c:279-282` and
`firmware/zmk/module/src/route.c:462`. On transport failure `astrolabe_route_flush` correctly
retains the accumulator, but once `active-window-ms` (2000) elapses `poll_work_handler` returns
without rescheduling, so retained rotation waits for the next MOTION interrupt or button press.

This is a **design question, not a defect**: delivering up to two seconds of stale rotation late
is arguably worse than dropping it, and the Arduino prototype explicitly dropped it when no
daemon was subscribed (`else if(!g_controller) gx = gy = gz = 0`). Decide deliberately during
the latency matrix; do not add a flush-retry timer to paper over it.

### C. Error handling differs between the two USB report paths

`trackball_daemon/devices/usb_transport.py:266-271`. The rotation path wraps its callback in
`try/except`; `accept_snapshot` is unguarded, so an exception there tears down the session.
Both behaviors are defensible — one is quiet, one is loud. Pick one on purpose. Not worth
changing on its own.

### D. USB match relies on the product string as its only real discriminator

`usage_page 0xFF00` / `usage 1` is generic and the VID/PID is ZMK's shared development pair, so
`"Astrolabe"` carries the whole match. Already tracked as a release gate in the plan document
(production VID/PID allocation); no separate action.

### E. Footnote: the opening snapshot's discarded return is unreachable

`firmware/zmk/module/src/usb.c:251` discards the result of `astrolabe_route_publish_snapshot`.
This initially looked like a lost-sync risk, but `data_queue` is only written under a live lease
and is purged on session clear, so it is always empty at attach and `queue_report` cannot fail
there. Recorded so the next reviewer does not re-raise it.

### F. Windows-only reproduction caveat

The CI license `cmp` steps compare LF files against west checkouts. On a Windows workspace with
`autocrlf`, four of eight comparisons fail on line endings alone; on Linux CI they pass. Not a
CI defect — a note for anyone reproducing the compliance step locally.

---

## Verified correct

Checked in detail and found faithful:

- **Sign conventions match the prototype exactly.** `ROT_SIGN_{X,Y,Z} = -1` corresponds to
  `astrolabe_encode_rotation(-rotation[i])`; `CURSOR_SWAP_XY` with both inversions corresponds
  to `cursor_x -= wy`, `cursor_y -= wx`; `SCROLL_INVERT` corresponds to `scroll -= wz`.
- **All sensor and gain constants match** `firmware/PMW3610/PMW3610.ino`: phi 145/215, theta
  120/120, mount 270/270, flip 1/1, frame tilt -20, CPI 1600, cursor gain 0.125, scroll gain
  0.25, scroll divisor 60, yaw deadzone 1.2, yaw dominance 1.7, scroll hold 80 ms, poll
  1000 us, active window 2000 ms, send interval 7 ms.
- **USB identity agrees end to end.** The built `.config` gives VID `0x1D50`, PID `0x615E`, and
  product `"Astrolabe"` (via `ZMK_KEYBOARD_NAME` defaulting `USB_DEVICE_PRODUCT`), matching the
  descriptor JSON's `7504 / 24926 / "Astrolabe"`.
- **Firmware lock ordering is consistent** throughout: `output_lock -> session_lock ->
  state.lock`, and transports are never invoked while holding `state.lock`.

### On the absent wake-settle discard

The plan document states the port applies no wake-settle discard and no SQUAL validity filter.
This is **not a regression**. Commit `eb9da3c` ("Rework PMW3610 wake handling", 2026-07-30)
removed `sensorsForceAwake`, `setForceAwake`, and `WAKE_SETTLE_MS` from the prototype
altogether. The current design eliminates the rest-to-run transient at its source — sensors stay
in automatic Run/Rest (`FMODE=0`) and the firmware never forces the transition — rather than
filtering the symptom. The ZMK port matches that design. Do not reintroduce a settle-discard
window or an SQUAL threshold without new physical evidence.

---

# Flashing and testing

## Build

```bash
cd firmware/zmk && west init -l config && west update --fetch-opt=--filter=tree:0 && west zephyr-export
```

Then, adjusting `ZEPHYR_SDK_INSTALL_DIR` to the local SDK:

```bash
cd firmware/zmk && ZEPHYR_TOOLCHAIN_VARIANT=zephyr ZEPHYR_SDK_INSTALL_DIR="$PWD/../../.tmp/zephyr-sdk-extracted/zephyr-sdk-0.16.3" west build -s zmk/app -d build/astrolabe -b nice_nano_v2 -p=auto -- -DSHIELD=astrolabe -DZMK_CONFIG="$PWD/config" -DZMK_EXTRA_MODULES="$PWD/module"
```

Output: `firmware/zmk/build/astrolabe/zephyr/zmk.uf2`

## Flash

Double-tap RESET on the SuperMini. A UF2 mass-storage volume appears (`NICENANO`, or an
Adafruit bootloader name on some SuperMini clones). Copy `zmk.uf2` onto it; the board reboots
itself. UF2 preserves the bootloader, so double-tap RESET is always the way back.

Before plugging it in: this firmware enumerates as a real HID mouse and will move the cursor and
click. Have a second pointing device attached. The escape hatch is **hold Center through boot**,
which forces standalone and rejects all daemon claims until reboot.

## Test matrix

Run in order; each stage gates the next.

**A — Enumeration.** Device Manager should show **two** HID interfaces under product
`Astrolabe`, VID `1D50`, PID `615E`. If only one appears, the `_1` instance failed to register
(see finding 5).

**B — Standalone, no daemon.** Cursor tracks; yaw twist scrolls; Down is left click, Right is
right click, Center is middle click; Up and Left are reserved and do nothing. Combos, 120 ms
window: `0+2+4` bootloader, `1+3+4` reset, `0+3+4` next BLE profile, `0+1+4` output toggle.

**C — USB daemon route.** Run `uv run astrolabe`. Expect status
`subscribed -- Astrolabe five-way switch over USB is live`. Buttons must stop producing OS
clicks, since the daemon now owns the route. The battery line should read `Power: USB (...)`.

**D — Cable pull.** Disconnect USB mid-session; expect
`USB disconnected; BLE fallback enabled`. Test this specifically **with a button held** — on
return to standalone that control must stay suppressed until physically released
(`route.c:315`).

**E — Keepalive fail-safe.** Kill the daemon from Task Manager with no clean detach. The
firmware must return to standalone within about 1500 ms and buttons must click again. This is
also the direct test for watch-item A.

**F — BLE route.** Unplug USB, pair, and confirm rotation and input snapshots. Then plug USB in
while BLE is live: USB must win and BLE must tear down without reconnect churn.

**G — Forced standalone.** Hold Center and plug in. Daemon attach must be rejected with
`result=1`, and BLE rotation-CCC writes must fail until reboot.

Stages **D with a held button** and **E** exercise the untested ownership-transition paths and
are the highest-value runs.
