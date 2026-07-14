"""Small Tk smoke tests; semantic behavior remains covered by UI-independent models."""

from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

import pytest

from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import InputAggregator, load_system_binding_profiles
from trackball_daemon.ui import SettingsWindow


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


def test_generated_tabs_and_linked_value_refresh_after_global_edit(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless non-Windows CI
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    store = ConfigStore(tmp_path / "config.json").load()
    app = SimpleNamespace(
        config=store,
        binding_catalog=load_system_binding_profiles(),
        input_aggregator=InputAggregator(),
        status_text=lambda: "stopped",
    )
    ui = SettingsWindow(root, app)
    try:
        ui._build()
        root.update()
        notebook = next(widget for widget in ui.win.winfo_children()
                        if isinstance(widget, ttk.Notebook))
        assert [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()] == [
            "3D Apps", "Global", "Per-App", "Keybindings"]
        per_app = _tab(notebook, "Per-App")
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
    finally:
        if ui.win is not None:
            ui.win.destroy()
        root.destroy()
