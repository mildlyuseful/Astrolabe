# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Small Tk smoke tests; semantic behavior remains covered by UI-independent models."""

from types import SimpleNamespace
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

import pytest

from trackball_daemon import onshape_bridge

from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import InputAggregator, load_system_binding_profiles
from trackball_daemon.ui import SettingsWindow


@pytest.fixture(scope="module")
def tk_root():
    """One Tk root for the module. Creating a second one after the first is destroyed fails on
    Windows with `invalid command name "tcl_findLibrary"`, so the root outlives each test and the
    tests clean up their own toplevels."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless non-Windows CI
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _tab(notebook, title):
    for tab_id in notebook.tabs():
        if notebook.tab(tab_id, "text") == title:
            return notebook.nametowidget(tab_id)
    raise AssertionError(f"missing tab {title}")


def _row_with_label(parent, text):
    for widget in _walk(parent):
        if isinstance(widget, ttk.Label) and widget.cget("text") == text:
            return widget.master, widget
    raise AssertionError(f"missing setting row {text}")


def test_generated_tabs_and_linked_value_refresh_after_global_edit(tmp_path, tk_root):
    root = tk_root
    store = ConfigStore(tmp_path / "config.json").load()
    app = SimpleNamespace(
        config=store,
        binding_catalog=load_system_binding_profiles(),
        input_aggregator=InputAggregator(),
        status_text=lambda: "stopped",
        battery_status_text=lambda: "Battery: 64% (last known)",
        runtime_health_snapshot=lambda: {},
    )
    ui = SettingsWindow(root, app)
    try:
        ui._build()
        root.update()
        assert ui.status_var.get() == "Connection: stopped"
        assert ui.battery_var.get() == "Battery: 64% (last known)"
        ui.update_battery_status("Battery: 63%")
        assert ui.battery_var.get() == "Battery: 63%"
        notebook = next(widget for widget in ui.win.winfo_children()
                        if isinstance(widget, ttk.Notebook))
        assert [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()] == [
            "3D Apps", "Global", "Per-App", "Keybindings"]
        global_tab = _tab(notebook, "Global")
        global_categories = next(widget for widget in _walk(global_tab)
                                 if isinstance(widget, ttk.Notebook))
        category_titles = [global_categories.tab(tab_id, "text")
                           for tab_id in global_categories.tabs()]
        assert "Physical transform" in category_titles
        orbit_tab = next(tab_id for tab_id in global_categories.tabs()
                         if global_categories.tab(tab_id, "text") == "Orbit")
        global_categories.select(orbit_tab)
        root.update()

        fallback_row, _label = _row_with_label(global_tab, "Orbit pivot fallback order")
        fallback_list = next(widget for widget in _walk(fallback_row)
                             if isinstance(widget, tk.Listbox))
        assert fallback_list.get(0, "end") == (
            "1. 3D Cursor", "2. Camera", "3. Model Center", "4. World Origin")
        fallback_list.selection_set(0)
        down = next(widget for widget in _walk(fallback_row)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == "Down")
        down.invoke()
        root.update()
        assert store.snapshot().global_value("navigation.orbit.pivot_fallbacks") == (
            "camera", "cursor_3d", "object", "origin")

        orbit_row, _label = _row_with_label(global_tab, "Orbit pivot hold")
        orbit_entry = next(widget for widget in orbit_row.winfo_children()
                           if isinstance(widget, ttk.Entry))
        orbit_entry.event_generate("<FocusIn>")
        orbit_entry.delete(0, "end")
        orbit_entry.insert(0, "0.75")
        blank_label = next(widget for widget in global_tab.winfo_children()
                           if isinstance(widget, ttk.Label))
        ui._focus_blank_space(SimpleNamespace(widget=blank_label))
        root.update()
        root.update_idletasks()
        assert store.snapshot().global_value(
            "navigation.orbit.pivot_hold_seconds") == 0.75
        global_categories = next(widget for widget in _walk(global_tab)
                                 if isinstance(widget, ttk.Notebook))
        assert global_categories.tab(global_categories.select(), "text") == "Orbit"

        pointer_tab = next(tab_id for tab_id in global_categories.tabs()
                           if global_categories.tab(tab_id, "text") == "Pointer")
        global_categories.select(pointer_tab)
        root.update()
        sensitivity_row, _label = _row_with_label(global_tab, "Pointer sensitivity")
        sensitivity_entry = next(widget for widget in sensitivity_row.winfo_children()
                                 if isinstance(widget, ttk.Entry))
        sensitivity_entry.event_generate("<FocusIn>")
        sensitivity_entry.delete(0, "end")
        sensitivity_entry.insert(0, "64")
        sensitivity_entry.event_generate("<KeyRelease>")
        deadline = time.monotonic() + 0.5
        while (store.snapshot().global_value("pointer.cursor.gain") != 64.0 and
               time.monotonic() < deadline):
            root.update()
            time.sleep(0.01)
        assert store.snapshot().global_value("pointer.cursor.gain") == 64.0

        per_app = _tab(notebook, "Per-App")
        fallback_row, fallback_label = _row_with_label(
            per_app, "Orbit pivot fallback order")
        assert any(isinstance(widget, tk.Listbox) for widget in _walk(fallback_row))
        assert str(fallback_label.cget("foreground")) == "#777"
        row, label = _row_with_label(per_app, "Orbit sensitivity")
        entry = next(widget for widget in row.winfo_children()
                     if isinstance(widget, ttk.Entry))
        assert entry.get() == "1.0"
        assert str(label.cget("foreground")) == "#777"

        store.set_global("navigation.orbit.sensitivity", 4.25)
        root.update()
        root.update_idletasks()
        row, label = _row_with_label(per_app, "Orbit sensitivity")
        entry = next(widget for widget in row.winfo_children()
                     if isinstance(widget, ttk.Entry))
        assert entry.get() == "4.25"
        assert str(label.cget("foreground")) == "#777"

        keybindings = _tab(notebook, "Keybindings")
        visible_labels = {str(widget.cget("text")) for widget in _walk(keybindings)
                          if isinstance(widget, ttk.Label)}
        assert "What it controls" in visible_labels
        assert "Behavior" in visible_labels
        assert "Applications" in visible_labels
        assert "Active application" not in visible_labels
        app_row, _label = _row_with_label(keybindings, "Applications")
        app_selector = next(widget for widget in _walk(app_row)
                            if isinstance(widget, tk.Listbox))
        assert str(app_selector.cget("selectmode")) == "multiple"
        assert {"Blender", "Unity"} <= set(app_selector.get(0, "end"))
        assert "Other applications (not integrated)" in app_selector.get(0, "end")
        selected_binding_id = ui._selected_binding_id
        app_selector.selection_clear(0, "end")
        app_selector.selection_set(app_selector.get(0, "end").index("Onshape"))
        app_selector.selection_set(
            app_selector.get(0, "end").index("Other applications (not integrated)"))
        save = next(widget for widget in _walk(keybindings)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == "Save")
        save.invoke()
        root.update()
        assert ui.binding_model.row(selected_binding_id)["when"] == {
            "apps": ["onshape"], "other_apps": True}
        assert "Executables" not in visible_labels
        assert "Input profiles" not in visible_labels
        assert "On press (JSON)" not in visible_labels
        assert "On release (JSON)" not in visible_labels
    finally:
        if ui.win is not None:
            ui.win.destroy()


def test_onshape_setup_dialog_is_copyable_and_can_open_the_trust_page(tmp_path, monkeypatch,
                                                                     tk_root):
    """The trust steps are only useful if the user can get them out of the dialog.

    They were rendered in a ttk.Label, which cannot be selected at all, so the certutil command
    and the bridge URL had to be retyped from the screen. The lead is a read-only Text now, and
    the page you have to visit to accept the certificate is one button away."""
    root = tk_root
    store = ConfigStore(tmp_path / "config.json").load()
    app = SimpleNamespace(
        config=store,
        binding_catalog=load_system_binding_profiles(),
        input_aggregator=InputAggregator(),
        status_text=lambda: "stopped",
        battery_status_text=lambda: "Battery: 64% (last known)",
        runtime_health_snapshot=lambda: {},
    )
    ui = SettingsWindow(root, app)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: (opened.append(url), True)[1])
    lead = 'Run certutil -user -addstore Root "C:\\cert.pem" or visit the bridge.'
    seen = {}

    def _inspect():
        # No assertions in here: an exception would skip the destroy and hang wait_window().
        try:
            dialog = next(w for w in _walk(root)
                          if isinstance(w, tk.Toplevel) and w.title() == "Onshape — Set up")
            texts = [w for w in _walk(dialog) if isinstance(w, tk.Text)]
            seen["contents"] = [w.get("1.0", "end-1c") for w in texts]
            seen["states"] = [str(w.cget("state")) for w in texts]
            buttons = {str(w.cget("text")): w for w in _walk(dialog)
                       if isinstance(w, ttk.Button)}
            seen["buttons"] = set(buttons)
            if "Open bridge page" in buttons:
                buttons["Open bridge page"].invoke()
            if "Copy trust command" in buttons:
                buttons["Copy trust command"].invoke()
                seen["clipboard"] = root.clipboard_get()
        except Exception as exc:                      # pragma: no cover - surfaced by the asserts
            seen["error"] = repr(exc)
        finally:
            for widget in _walk(root):
                if isinstance(widget, tk.Toplevel):
                    widget.destroy()

    try:
        root.after(0, _inspect)
        ui._show_onshape_userscript_dialog(
            title="Onshape — Set up", lead=lead,
            copyables=[("Copy trust command", 'certutil -user -addstore Root "C:\\cert.pem"')])

        assert "error" not in seen, seen.get("error")
        assert lead in seen["contents"]
        # "disabled" is what made the text unselectable; read-only is enforced by key bindings.
        assert "disabled" not in seen["states"]
        assert {"Open bridge page", "Copy userscript", "Copy trust command"} <= seen["buttons"]
        assert opened == [onshape_bridge.BRIDGE_URL]
        assert seen["clipboard"] == 'certutil -user -addstore Root "C:\\cert.pem"'
    finally:
        for widget in _walk(root):
            if isinstance(widget, tk.Toplevel):
                widget.destroy()
