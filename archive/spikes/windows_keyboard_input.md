# Windows keyboard input backend decision

> **Archived backend-decision evidence.** Raw Input is now implemented. Current lifecycle and
> security behavior is documented in [`../../docs/security.md`](../../docs/security.md) and
> [`../../docs/architecture.md`](../../docs/architecture.md).

Status: accepted Phase 0 architecture decision; implementation completed in the rich-keybindings work

Decision date: 2026-07-13

## Decision

Use Windows Raw Input as the primary event backend for rich keyboard bindings:

- Register keyboard usage page `0x01`, usage `0x06`, with `RIDEV_INPUTSINK` on one daemon-owned,
  dedicated message-only window.
- Do not use `RIDEV_NOLEGACY`; configured keys remain pass-through to the foreground application.
- Register lazily only while the compiled profile contains an enabled keyboard binding. Because
  Windows permits one registered window per raw-input device class per process, this registration
  belongs to the daemon provider manager rather than a reusable library helper.
- The window procedure decodes and enqueues compact events only. Normalization, pressed-set updates,
  binding matching, and commands run outside the callback.
- Reconcile configured keys with `GetAsyncKeyState` only after lifecycle discontinuities and never
  from inside a low-level hook callback. A zero result can also mean inactive desktop or UIPI/access
  failure, so ambiguous access releases all keyboard-owned controls rather than asserting “up” as a
  trustworthy observation.
- Unregister/restart, session lock, shutdown, profile reload, and receiver failure synthesize
  releases before state is discarded.

`WH_KEYBOARD_LL` is a fallback only if Phase 5 physical acceptance demonstrates a Raw Input failure
that prevents a product requirement. A fallback requires a dedicated message-loop thread, an
enqueue-only callback, heartbeat/watchdog, reinstall plus reconciliation, and fail-safe release.

## Why

Microsoft describes Raw Input as a stable way to receive keyboard HID data, explicitly supports
background delivery with `RIDEV_INPUTSINK`, and recommends Raw Input over low-level hooks for most
asynchronous monitoring cases. Conversely, a low-level hook that exceeds `LowLevelHooksTimeout` can
be silently removed with no direct removal notification. That failure mode is a poor foundation for
hold states, where one lost release could force a mode indefinitely.

`GetKeyState` was rejected as a reconciliation source because it reports state associated with the
calling thread's message queue. `GetAsyncKeyState` reports the key's current state, distinguishes
left/right modifiers, and documents when inactive desktops or UIPI can make the call fail. Its most
significant bit is the only state bit used; the unreliable “pressed since last call” bit is ignored.

Primary references:

- [Raw Input overview](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input)
- [`RegisterRawInputDevices`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerrawinputdevices)
- [`LowLevelKeyboardProc`](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)
- [`GetAsyncKeyState`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate)
- [`GetKeyState`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeystate)

## Repository diagnostic

`tools/windows_input_spike.py` is disposable diagnostic code, not a production provider. It runs a
Raw Input message-only receiver and a `WH_KEYBOARD_LL` receiver side by side. Automated mode emits
F24 down, a repeat-like second down, and up with `SendInput`, then verifies lifecycle and
reconciliation without using a printable or common shortcut key.

Automated result on Windows `10.0.26200`, Python `3.11.9`:

- Raw Input registered and unregistered successfully.
- The message-only Raw Input receiver observed F24 pressed, repeated pressed, and released, each as
  background `input_sink` delivery.
- The low-level hook observed the same three events and marked all as injected.
- `GetAsyncKeyState(VK_F24)` reported down between injection and release, then released afterward.
- The foreground window handle was unchanged, so receiver startup and event handling did not steal
  focus.
- Native structure sizes were `RAWINPUTHEADER=24`, `RAWKEYBOARD=16`, `RAWINPUTDEVICE=16`,
  `KBDLLHOOKSTRUCT=24`, and `INPUT=40` in this 64-bit process.
- Both callbacks enqueue records and return; no matching or command work occurs in the diagnostic
  callback.

Run command:

```powershell
python tools/windows_input_spike.py --automated
```

## Acceptance matrix and remaining manual evidence

Synthetic F24 proves plumbing, not physical keyboard behavior. Phase 5 must run the interactive
diagnostic and retain exact results before production integration is accepted.

| Acceptance case | Phase 0 evidence | Required Phase 5 evidence |
|---|---|---|
| Background press/release | Automated F24 arrived as `input_sink` | Physical key while an ordinary host owns focus |
| Repeat delivery | Automated down/down/up reached both backends | Physical auto-repeat; binding activates on only the first edge |
| Left/right modifiers | API/normalization design only | Left/right Ctrl, Shift, and Alt scan/extended flags |
| AltGr and layouts | Not represented by synthetic F24 | At least one AltGr layout plus the supported default layout |
| Pass-through/no focus steal | No `RIDEV_NOLEGACY`; foreground handle unchanged | Bound key still reaches a test host and HUD/provider never activates itself |
| Lazy register/unregister | Receiver lifecycle passed | Empty/device-only profile, keyboard profile, reload, and shutdown |
| Session lock/sleep/resume | Policy specified only | Release-all on lock and correct state after resume |
| Elevated or unreadable foreground | Microsoft documents possible UIPI/access failure; fail-safe policy specified | Non-elevated daemon with an elevated test host; ambiguous state releases all controls |
| Remote Desktop | Not covered | RDP connect/disconnect and key-up recovery |

Interactive command:

```powershell
python tools/windows_input_spike.py --interactive --seconds 15
```

If a physical acceptance case fails, first determine whether it is a normalization or lifecycle
bug around Raw Input. Selecting the hook fallback requires a recorded Raw Input limitation, not
merely familiarity with hook APIs.
