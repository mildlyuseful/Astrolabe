from types import SimpleNamespace
import threading
import tkinter as tk

import pytest

from trackball_daemon.commands import (
    ReportBindingActivity,
    RequestState,
    SerializedCommandQueue,
    SetFocusedContext,
)
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.control_hud import (
    ControlHUD,
    HudTextProjector,
    LatestSnapshotMailbox,
    WorkArea,
    bottom_right_origin,
    project_hud_text,
)
from trackball_daemon.runtime_state import FocusedContext, RuntimeBaseState, RuntimeStore


def _runtime(mode="pointer", navigation="orbit", settings=None):
    return RuntimeStore(lambda _context: RuntimeBaseState(
        mode, navigation_mode=navigation, settings=settings or {}))


def test_snapshot_semantic_help_tracks_mode_layer_and_twist_action():
    runtime = _runtime("3d", settings={"navigation.orbit.twist_action": "dolly"})
    commands = SerializedCommandQueue(runtime)
    snapshot = runtime.snapshot()
    assert snapshot.control_help.primary_help == "Ball: planar = orbit · twist = dolly"
    assert snapshot.control_help.secondary_help == "Ball: planar = pan · twist = zoom"
    assert snapshot.control_help.current_help == snapshot.control_help.primary_help

    commands.dispatch(RequestState(
        binding_id="pan", activation_id="one", target="pan", source="test",
        label="Shift: Pan / Zoom"))
    snapshot = runtime.snapshot()
    assert snapshot.effective_input_mode == "3d"
    assert snapshot.effective_navigation_layer == "secondary"
    assert snapshot.control_help.state_label == "Pan / Zoom"
    assert snapshot.control_help.current_help == snapshot.control_help.secondary_help


def test_pure_projection_uses_registered_and_plain_executable_contexts():
    runtime = _runtime("3d")
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(SetFocusedContext(
        origin="test", context=FocusedContext("blender", "blender.exe")))
    text = project_hud_text(runtime.snapshot())
    assert text.lines == (
        "Astrolabe · 3D",
        "Blender · Orbit · Primary",
        "Ball: planar = orbit · twist = roll",
        "Binding: —",
    )

    commands.dispatch(SetFocusedContext(
        origin="test", context=FocusedContext(None, r"C:\\Windows\\notepad.exe")))
    assert project_hud_text(runtime.snapshot()).context == \
        "notepad.exe · Orbit · Primary"


def test_held_binding_precedes_last_used_then_times_out():
    runtime = _runtime()
    commands = SerializedCommandQueue(runtime)
    projector = HudTextProjector()
    commands.dispatch(ReportBindingActivity(
        origin="test", binding_id="ctrl-3d", activation_id="physical:ctrl-3d",
        source="test", active=True, label="Ctrl: 3D"))
    assert projector.project(runtime.snapshot(), now=10.0).binding == "Held: Ctrl: 3D"

    commands.dispatch(ReportBindingActivity(
        origin="test", binding_id="ctrl-3d", activation_id="physical:ctrl-3d",
        source="test", active=False, label="Ctrl: 3D"))
    assert projector.project(runtime.snapshot(), now=11.0, timeout=3.0).binding == \
        "Last: Ctrl: 3D"
    assert projector.project(runtime.snapshot(), now=14.01, timeout=3.0).binding == \
        "Binding: —"


def test_latest_snapshot_mailbox_is_bounded_and_monotonic():
    mailbox = LatestSnapshotMailbox()
    for revision in range(10_000):
        mailbox.publish(SimpleNamespace(revision=revision))
        assert mailbox.pending_count == 1
    mailbox.publish(SimpleNamespace(revision=10))
    assert mailbox.take().revision == 9_999
    assert mailbox.pending_count == 0


def test_latest_snapshot_mailbox_coalesces_concurrent_publishers():
    mailbox = LatestSnapshotMailbox()
    barrier = threading.Barrier(5)

    def publish(start):
        barrier.wait()
        for revision in range(start, 20_000, 4):
            mailbox.publish(SimpleNamespace(revision=revision))

    threads = [threading.Thread(target=publish, args=(offset,)) for offset in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert mailbox.pending_count == 1
    assert mailbox.take().revision == 19_999


def test_bottom_right_origin_respects_work_area_and_clamps_large_panel():
    work = WorkArea(-1920, 40, 0, 1040)
    assert bottom_right_origin(work, 320, 120, 16) == (-336, 904)
    assert bottom_right_origin(work, 4000, 3000, 16) == (-1920, 40)


def test_tk_hud_renders_snapshot_and_persistent_visibility(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless non-Windows CI
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    config = ConfigStore(tmp_path / "config.json").load()
    runtime = _runtime()
    hud = ControlHUD(root, runtime, config)
    try:
        root.update()
        labels = [child.cget("text") for child in hud.window.winfo_children()]
        assert labels[0] == "Astrolabe · Pointer"
        assert labels[2] == "Ball: planar = pointer · twist = scroll"

        config.set_global("hud.visible", False)
        root.after(70, root.quit)
        root.mainloop()
        assert hud._options.visible is False
        config.set_global("hud.visible", True)
        root.after(70, root.quit)
        root.mainloop()
        assert hud._options.visible is True
    finally:
        hud.stop()
        root.destroy()
