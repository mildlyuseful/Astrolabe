# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Tkinter settings window.

Lives as a Toplevel under a hidden Tk root. Closing the window HIDES it (withdraw) so the
app keeps running in the tray; the app exits only via tray -> Quit. Every edit writes to
the config store immediately (typed transaction -> listeners -> OutputEngine.apply_config), so
changes persist and apply live.
"""
import json
import tkinter as tk
from tkinter import ttk, messagebox

from . import integrations
from .app_registry import (APP_IDS_BY_TIER, APP_SPECS, APP_SPECS_BY_ID,
                           SUPPORT_TIER_LABELS, SUPPORT_TIER_SUMMARIES, SupportTier,
                           binding_profile)
from .binding_ui_model import BindingUIModel
from .settings_schema import BINDING_SECTIONS
from .settings_schema import SettingScope, ValueKind
from .settings_ui_model import CATEGORY_TITLES, SettingsUIModel
from .config import (ORBIT_PIVOT_METHODS,
                     normalize_axis_permutation, normalize_orbit_pivot_fallbacks,
                     swap_axis_source)
from .service_health import ServiceHealthState
from .product import PRODUCT_NAME

_PAD = {"padx": 8, "pady": 4}
_PIVOT_LABELS = {
    "camera": "Camera",
    "screen_center": "Screen Center",
    "cursor": "Under Cursor (mouse)",
    "selection": "Selection",
    "cursor_3d": "3D Cursor",
    "object": "Model Center",
    "origin": "World Origin",
}
_OPTION_LABELS = {
    "default": "Default", "free": "Free", "turntable": "Turntable",
    "orbit": "Orbit", "fly": "Fly", "walk": "Walk", "object": "Object",
    "roll": "Roll", "zoom": "Zoom", "dolly": "Dolly", "none": "None",
    "shift": "Shift", "3d": "3D", "pointer": "Pointer",
    "left": "Left", "right": "Right", "middle": "Middle",
    "to_center": "To Center", "to_object": "To Object", "to_cursor": "To Cursor",
}


def _option_label(value):
    value = str(value)
    return _OPTION_LABELS.get(value, value[:1].upper() + value[1:])


def _free_orbit_needs_roll_warning(app_style, general_style, twist_action, supports_roll=True):
    """Whether the effective orbit is Free but twist is not providing the third rotation axis."""
    effective_style = general_style if app_style == "default" else app_style
    return bool(supports_roll and effective_style == "free" and twist_action != "roll")


class _ToolTip:
    """Small dependency-free Tk tooltip used instead of permanent explanatory copy."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._after = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(450, self._show)

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        self._after = None
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 18
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            ttk.Label(win, text=self.text, padding=(7, 4), relief="solid", wraplength=420,
                      justify="left").pack()
            self._window = win
        except tk.TclError:
            self._window = None

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class SettingsWindow:
    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.cfg = app.config
        self.win = None
        self.status_var = None
        self.battery_var = None
        self._broker_health_label = None
        self._app_status_labels = {}      # key -> ttk.Label (3D Apps tab)
        self._app_action_buttons = {}     # key -> setup/update button
        self._refresh_twist_warning = None
        self.settings_model = SettingsUIModel(self.cfg)
        self.binding_model = BindingUIModel(
            self.cfg, app.binding_catalog, getattr(app, "input_aggregator", None))
        self._global_host = None
        self._app_settings_host = None
        self._keybinding_host = None
        self._settings_rebuilding = False
        self._refresh_pending = False
        self._global_category = None
        self._app_category = None
        self._selected_binding_id = None
        self._pending_setting_commit = None
        self._suppress_generated_refresh = False
        self._onboarding = None
        self.cfg.add_listener(self._on_config_event)
        self.root.bind_all("<Button-1>", self._focus_blank_space, add="+")

    # --- show / hide --------------------------------------------------------------
    def show(self):
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.win.deiconify()
                    self.win.lift()
                    self.win.focus_force()
                    return
            except tk.TclError:
                pass
        self._build()

    def show_onboarding(self):
        from .onboarding import FirstRunWizard

        if self._onboarding is None:
            self._onboarding = FirstRunWizard(
                self.root, self.app, open_settings=self.show)
        self._onboarding.show()

    def _on_close(self):
        # Hide to tray; do NOT destroy/exit. App exits only via tray -> Quit.
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def update_status(self, text):
        if self.status_var is not None and self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.status_var.set(f"Connection: {text}")
            except tk.TclError:
                pass

    def update_battery_status(self, text):
        if self.battery_var is not None and self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.battery_var.set(text)
            except tk.TclError:
                pass

    def update_app_connections(self, infos):
        """Reflect live add-on handshakes in the 3D-Apps rows (called from the Tk thread)."""
        if not self._app_status_labels:
            return
        connected = {a: v for a, v, _ in infos}
        health = self.app.runtime_health_snapshot()
        for key, lbl in self._app_status_labels.items():
            try:
                if not lbl.winfo_exists():
                    continue
                if key in connected:
                    version = connected[key]
                    stale = (key in integrations.ADDIN_KEYS and
                             integrations.update_available(key, installed_version=version))
                    suffix = "  •  update available" if stale else ""
                    item = health.get(key)
                    if item is not None and item.state in (
                            ServiceHealthState.DEGRADED, ServiceHealthState.FAILED):
                        lbl.config(
                            text=(
                                f"active • connected (v{version}){suffix}\n"
                                f"runtime {item.state.value}: {item.detail}"
                            ),
                            foreground=self._health_color(item.state),
                        )
                    else:
                        lbl.config(
                            text=f"active • connected (v{version}) • runtime healthy{suffix}",
                            foreground="#1a7f37",
                        )
                else:
                    appdef = integrations.APPS_BY_KEY.get(key)
                    if appdef is not None:
                        item = health.get(key)
                        lbl.config(
                            text=self._app_status_text(appdef),
                            foreground=self._health_color(item.state) if item else "#666",
                        )
            except tk.TclError:
                pass
        for key, button in self._app_action_buttons.items():
            try:
                action = self._app_button_text(integrations.APPS_BY_KEY[key])
                if action:
                    button.config(text=action)
                    if not button.winfo_manager():
                        button.pack(side="right")
                else:
                    button.pack_forget()
            except tk.TclError:
                pass

    @staticmethod
    def _health_color(state):
        return {
            ServiceHealthState.HEALTHY: "#1a7f37",
            ServiceHealthState.DEGRADED: "#b45309",
            ServiceHealthState.FAILED: "#b42318",
        }.get(state, "#666")

    def update_service_health(self, health):
        """Render transport health without conflating it with host connection presence."""
        broker = health.get("navigation-broker")
        if self._broker_health_label is not None:
            try:
                if self._broker_health_label.winfo_exists():
                    if broker is None:
                        text, color = "Navigation broker: status unavailable", "#666"
                    else:
                        text = f"Navigation broker: {broker.state.value} — {broker.detail}"
                        color = self._health_color(broker.state)
                    self._broker_health_label.config(text=text, foreground=color)
            except tk.TclError:
                pass
        self.update_app_connections(getattr(self.app, "connected_apps", ()))

    # --- build --------------------------------------------------------------------
    def _build(self):
        self.win = tk.Toplevel(self.root)
        self.win.title(f"{PRODUCT_NAME} — Settings")
        self.win.geometry("900x680")
        self.win.minsize(720, 520)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        nb.add(self._build_apps_tab(nb), text="3D Apps")
        nb.add(self._build_global_tab(nb), text="Global")
        nb.add(self._build_per_app_tab(nb), text="Per-App")
        nb.add(self._build_keybindings_tab(nb), text="Keybindings")

        self.status_var = tk.StringVar(value=f"Connection: {self.app.status_text()}")
        self.battery_var = tk.StringVar(value=self.app.battery_status_text())
        ttk.Separator(self.win).pack(fill="x")
        footer = ttk.Frame(self.win)
        footer.pack(fill="x", padx=10, pady=4)
        ttk.Label(footer, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.battery_var, anchor="e").pack(side="right")

    def _on_config_event(self, _event):
        if self.win is None or self._refresh_pending or self._suppress_generated_refresh:
            return
        self._refresh_pending = True
        try:
            self.root.after(0, self._refresh_generated_tabs)
        except tk.TclError:
            self._refresh_pending = False

    def _refresh_generated_tabs(self):
        self._refresh_pending = False
        if self.win is None:
            return
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        if self._global_host is not None:
            self._render_global(self._global_host)
        if self._app_settings_host is not None:
            self._render_per_app(self._app_settings_host)
        if self._keybinding_host is not None:
            self._render_keybindings(self._keybinding_host)

    def _focus_blank_space(self, event):
        """Commit the current generated setting when otherwise inert space is clicked."""
        if self.win is None:
            return
        try:
            if event.widget.winfo_toplevel() != self.win:
                return
        except tk.TclError:
            return
        interactive = (ttk.Entry, ttk.Combobox, tk.Entry, tk.Text, ttk.Button,
                       ttk.Checkbutton, tk.Button, tk.Listbox, ttk.Notebook, ttk.Scrollbar)
        if not isinstance(event.widget, interactive) and self._pending_setting_commit is not None:
            commit = self._pending_setting_commit
            self._pending_setting_commit = None
            commit()

    @staticmethod
    def _remember_category(notebook, attribute, owner):
        try:
            selected = notebook.select()
            if selected:
                setattr(owner, attribute, notebook.tab(selected, "text"))
        except tk.TclError:
            pass

    @staticmethod
    def _restore_category(notebook, title):
        if not title:
            return
        for tab_id in notebook.tabs():
            if notebook.tab(tab_id, "text") == title:
                notebook.select(tab_id)
                return

    @staticmethod
    def _choice_text(spec, value):
        if spec.id.startswith("input.axis_orientation.") and spec.id.endswith(".source"):
            return {0: "Axis 0 (X)", 1: "Axis 1 (Y)", 2: "Axis 2 (Z)"}[value]
        return SettingsWindow._setting_value_text(value)

    @staticmethod
    def _category_page(notebook, title):
        page = ttk.Frame(notebook)
        canvas = tk.Canvas(page, highlightthickness=0)
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        notebook.add(page, text=title)
        return inner

    @staticmethod
    def _setting_value_text(value):
        if value is None:
            return "None"
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _parse_setting_value(spec, raw):
        if spec.nullable and raw == "None":
            return None
        if spec.value_kind is ValueKind.BOOLEAN:
            if isinstance(raw, bool):
                return raw
            if raw not in {"True", "False"}:
                raise ValueError("expected True or False")
            return raw == "True"
        if spec.value_kind is ValueKind.INTEGER:
            return int(raw)
        if spec.value_kind is ValueKind.NUMBER:
            return float(raw)
        if spec.value_kind is ValueKind.STRING_LIST:
            return [item.strip() for item in str(raw).split(",") if item.strip()]
        return str(raw)

    def _run_setting_action(self, action):
        if self._settings_rebuilding:
            return
        try:
            action()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self.win)

    def _generated_setting_row(self, parent, view, *, app_id=None):
        spec = view.spec
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=4)
        linked = bool(app_id and view.linked)
        label = ttk.Label(row, text=spec.ui.label, width=31, anchor="w",
                          foreground="#777" if linked else "")
        label.pack(side="left")
        self._tooltip(label, spec.ui.help)

        if app_id:
            link_text = "🔗" if linked else "🔓"
            link = ttk.Button(row, text=link_text, width=3, command=lambda: self._run_setting_action(
                lambda: self.settings_model.toggle_app_link(app_id, spec.id)))
            link.pack(side="left", padx=(0, 5))
            self._tooltip(link, "Linked to Global" if linked else "App-specific override")

        choices = (tuple(view.choices) if app_id else
                   tuple(value for value in spec.choices
                         if value not in {"default", "cube", "cursor"}))
        if spec.value_kind is ValueKind.BOOLEAN and not spec.nullable:
            var = tk.BooleanVar(value=bool(view.value))
            control = ttk.Checkbutton(row, variable=var, command=lambda: self._run_setting_action(
                lambda: (self.settings_model.set_app(app_id, spec.id, var.get()) if app_id else
                         self.settings_model.set_global(spec.id, var.get()))))
            control.pack(side="left")
        elif spec.ui.control == "ordered_list":
            control = self._ordered_list_setting_control(row, view, app_id)
        elif choices or spec.value_kind is ValueKind.ENUM or spec.nullable:
            values = list(choices)
            if spec.nullable and None not in values:
                values.append(None)
            display = [self._choice_text(spec, value) for value in values]
            value_by_text = dict(zip(display, values))
            var = tk.StringVar(value=self._setting_value_text(view.value))
            control = ttk.Combobox(row, textvariable=var, values=display,
                                   state="readonly", width=23)
            control.pack(side="left")

            def choose(_event=None):
                value = value_by_text[var.get()]
                self._run_setting_action(lambda: (
                    self.settings_model.set_app(app_id, spec.id, value) if app_id else
                    self.settings_model.set_global(spec.id, value)))
            control.bind("<<ComboboxSelected>>", choose)
        else:
            var = tk.StringVar(value=self._setting_value_text(view.value))
            control = ttk.Entry(row, textvariable=var, width=26)
            control.pack(side="left")
            last_value = [view.value]
            live_apply_after = [None]

            def commit(_event=None, *, live=False):
                if live_apply_after[0] is not None:
                    try:
                        control.after_cancel(live_apply_after[0])
                    except tk.TclError:
                        pass
                    live_apply_after[0] = None
                try:
                    value = self._parse_setting_value(spec, var.get().strip())
                except (TypeError, ValueError):
                    if not live:
                        var.set(self._setting_value_text(last_value[0]))
                    return
                if not spec.validates(value):
                    if not live:
                        var.set(self._setting_value_text(last_value[0]))
                    return
                if self._pending_setting_commit is commit:
                    self._pending_setting_commit = None
                if value != last_value[0]:
                    self._suppress_generated_refresh = True
                    try:
                        if app_id:
                            self.settings_model.set_app(app_id, spec.id, value)
                        else:
                            self.settings_model.set_global(spec.id, value)
                    except (TypeError, ValueError) as exc:
                        if live:
                            return
                        messagebox.showerror("Invalid setting", str(exc), parent=self.win)
                        var.set(self._setting_value_text(last_value[0]))
                        return
                    finally:
                        self._suppress_generated_refresh = False
                    last_value[0] = value
                if not live:
                    self.root.after(0, self._refresh_generated_tabs)

            def entry_pending(_event=None):
                self._pending_setting_commit = commit
                if spec.value_kind not in (ValueKind.INTEGER, ValueKind.NUMBER):
                    return
                if live_apply_after[0] is not None:
                    control.after_cancel(live_apply_after[0])
                live_apply_after[0] = control.after(
                    250, lambda: commit(live=True))

            control.bind("<FocusIn>", entry_pending)
            control.bind("<KeyRelease>", entry_pending)
            control.bind("<Return>", commit)
            control.bind("<FocusOut>", commit)
        self._tooltip(control, spec.ui.help)

        if app_id:
            status = "Linked to Global" if linked else "App override"
            if linked and not view.global_compatible:
                status += " (Global value unsupported; using app System default)"
            ttk.Label(row, text=status, foreground="#777" if linked else "#333").pack(
                side="left", padx=8)
            if view.show_reset:
                reset = ttk.Button(row, text="↻", width=3, command=lambda: self._run_setting_action(
                    lambda: self.settings_model.reset_app_setting(app_id, spec.id)))
                reset.pack(side="right")
                self._tooltip(reset, "Reset app setting to System default")
        else:
            source = view.source_text
            if spec.scope is SettingScope.GLOBAL_AND_APP:
                source += " • per-app capable"
            elif spec.scope is SettingScope.DEVICE:
                source += " • device"
            ttk.Label(row, text=source,
                      foreground="#777" if not view.overridden else "#333").pack(
                side="left", padx=8)
            if view.overridden:
                reset = ttk.Button(row, text="↻", width=3, command=lambda: self._run_setting_action(
                    lambda: self.settings_model.reset_global(spec.id)))
                reset.pack(side="right")
                self._tooltip(reset, "Reset global to System default")

    def _ordered_list_setting_control(self, parent, view, app_id):
        """Render one ordered setting list for either Global or an app override."""
        spec = view.spec
        chain = list(view.value)
        body = ttk.Frame(parent)
        body.pack(side="left", fill="x", expand=True)
        listbox = tk.Listbox(body, height=4, width=28, exportselection=False)
        listbox.grid(row=0, column=0, rowspan=4, sticky="nsew")
        body.columnconfigure(0, weight=1)

        def label(value):
            return _PIVOT_LABELS.get(value, _option_label(value))

        def render(select=None):
            listbox.delete(0, "end")
            for index, value in enumerate(chain, 1):
                listbox.insert("end", f"{index}. {label(value)}")
            if chain and select is not None:
                select = max(0, min(select, len(chain) - 1))
                listbox.selection_set(select)
                listbox.activate(select)

        def selected():
            selection = listbox.curselection()
            return int(selection[0]) if selection else None

        def save(select=None):
            self._run_setting_action(lambda: (
                self.settings_model.set_app(app_id, spec.id, list(chain)) if app_id else
                self.settings_model.set_global(spec.id, list(chain))))
            render(select)

        def move(delta):
            index = selected()
            if index is None or not 0 <= index + delta < len(chain):
                return
            chain[index], chain[index + delta] = chain[index + delta], chain[index]
            save(index + delta)

        def remove():
            index = selected()
            if index is not None:
                chain.pop(index)
                save(min(index, len(chain) - 1))

        ttk.Button(body, text="Up", width=8, command=lambda: move(-1)).grid(
            row=0, column=1, padx=(6, 0), sticky="ew")
        ttk.Button(body, text="Down", width=8, command=lambda: move(1)).grid(
            row=1, column=1, padx=(6, 0), sticky="ew")
        ttk.Button(body, text="Remove", width=8, command=remove).grid(
            row=2, column=1, padx=(6, 0), sticky="ew")

        add_var = tk.StringVar(value=label(spec.choices[0]))
        add_combo = ttk.Combobox(
            body, textvariable=add_var, values=[label(value) for value in spec.choices],
            state="readonly", width=18)
        add_combo.grid(row=4, column=0, sticky="w", pady=(4, 0))

        def add():
            value = next(
                (value for value in spec.choices if label(value) == add_var.get()), None)
            if value is not None and value not in chain:
                chain.append(value)
                save(len(chain) - 1)

        ttk.Button(body, text="Add", width=8, command=add).grid(
            row=4, column=1, padx=(6, 0), pady=(4, 0), sticky="ew")
        render()
        return listbox

    def _build_global_tab(self, notebook):
        host = ttk.Frame(notebook)
        self._global_host = host
        self._render_global(host)
        return host

    def _render_global(self, host):
        self._settings_rebuilding = True
        try:
            for child in host.winfo_children():
                child.destroy()
            ttk.Label(host, text=(
                "Global values apply everywhere unless an app has an override. "
                "Device identity is shown here but has its own reset layer."),
                wraplength=780, justify="left").pack(fill="x", padx=10, pady=(10, 4))
            categories = ttk.Notebook(host)
            categories.pack(fill="both", expand=True, padx=6, pady=6)
            views = self.settings_model.global_views()
            present = {view.spec.category for view in views}
            for category in (key for key in CATEGORY_TITLES if key in present):
                inner = self._category_page(categories, CATEGORY_TITLES.get(
                    category, category.replace("_", " ").title()))
                if category == "transform":
                    ttk.Label(inner, text=(
                        "Device-wide base orientation. Map the ball's physical sensor axes to "
                        "logical X/Y/Z here; rotating the base should require only an axis swap "
                        "and, when needed, one inversion."), wraplength=760,
                        justify="left", foreground="#555").pack(fill="x", padx=10, pady=8)
                for view in (item for item in views if item.spec.category == category):
                    self._generated_setting_row(inner, view)
            self._restore_category(categories, self._global_category)
            categories.bind("<<NotebookTabChanged>>", lambda _e: self._remember_category(
                categories, "_global_category", self))
            actions = ttk.Frame(host)
            actions.pack(fill="x", padx=10, pady=(0, 10))
            button = ttk.Button(
                actions, text="Reset all Global settings to System defaults",
                command=lambda: self._run_setting_action(self.settings_model.reset_all_globals))
            button.pack(side="right")
            self._tooltip(button, "Clears all Global overrides; device identity is unchanged.")
        finally:
            self._settings_rebuilding = False

    def _build_per_app_tab(self, notebook):
        host = ttk.Frame(notebook)
        self._app_settings_host = host
        self._render_per_app(host)
        return host

    def _render_per_app(self, host):
        self._settings_rebuilding = True
        try:
            for child in host.winfo_children():
                child.destroy()
            snapshot = self.cfg.snapshot()
            app_id = snapshot.selected_app
            header = ttk.Frame(host)
            header.pack(fill="x", padx=10, pady=10)
            ttk.Label(header, text="Application:").pack(side="left")
            label_to_id = {app.display_name: app.app_id for app in APP_SPECS}
            selected = tk.StringVar(value=next(
                app.display_name for app in APP_SPECS if app.app_id == app_id))
            chooser = ttk.Combobox(header, textvariable=selected,
                                   values=tuple(label_to_id), state="readonly", width=22)
            chooser.pack(side="left", padx=6)
            chooser.bind("<<ComboboxSelected>>", lambda _e: self._run_setting_action(
                lambda: self.cfg.transaction().set_selected_app(
                    label_to_id[selected.get()]).commit()))
            all_linked = self.settings_model.all_app_settings_linked(app_id)
            link_text = "🔓 Break all links" if all_linked else "🔗 Link all to Global"
            ttk.Button(header, text=link_text, command=lambda: self._run_setting_action(
                lambda: self.settings_model.toggle_all_app_links(app_id))).pack(
                    side="right", padx=4)
            tk.Button(header, text="↻ Reset app", fg="#b42318", command=lambda: self._run_setting_action(
                lambda: self.settings_model.reset_app(app_id))).pack(side="right", padx=4)
            categories = ttk.Notebook(host)
            categories.pack(fill="both", expand=True, padx=6, pady=(0, 8))
            views = self.settings_model.app_views(app_id)
            present = {view.spec.category for view in views}
            for category in (key for key in CATEGORY_TITLES if key in present):
                inner = self._category_page(categories, CATEGORY_TITLES.get(
                    category, category.replace("_", " ").title()))
                for view in (item for item in views if item.spec.category == category):
                    self._generated_setting_row(inner, view, app_id=app_id)
            self._restore_category(categories, self._app_category)
            categories.bind("<<NotebookTabChanged>>", lambda _e: self._remember_category(
                categories, "_app_category", self))
        finally:
            self._settings_rebuilding = False

    def _build_keybindings_tab(self, notebook):
        host = ttk.Frame(notebook)
        self._keybinding_host = host
        self._binding_widgets = {}
        self._render_keybindings(host)
        return host

    def _render_keybindings(self, host):
        for child in host.winfo_children():
            child.destroy()
        profile_bar = ttk.Frame(host)
        profile_bar.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(profile_bar, text="Input profile:").pack(side="left")
        profiles = dict(self.binding_model.profiles())
        label_to_id = {label: profile_id for profile_id, label in profiles.items()}
        profile_var = tk.StringVar(value=profiles[self.binding_model.profile_id])
        profile = ttk.Combobox(profile_bar, textvariable=profile_var,
                               values=tuple(label_to_id), state="readonly", width=24)
        profile.pack(side="left", padx=6)
        profile.bind("<<ComboboxSelected>>", lambda _e: self._run_setting_action(
            lambda: self.binding_model.set_profile(label_to_id[profile_var.get()])))

        capabilities = self.binding_model.capability_views()
        capability_text = "  |  ".join(
            f"{item.source_id}: {item.status}"
            + (f" ({item.detail})" if item.detail else "") for item in capabilities)
        ttk.Label(host, text=capability_text or "No input controls required.",
                  wraplength=840, foreground="#666", justify="left").pack(
                      fill="x", padx=10, pady=(0, 6))

        body = ttk.Panedwindow(host, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=4)
        listing = ttk.Frame(body, width=280)
        editor = ttk.Frame(body)
        body.add(listing, weight=1)
        body.add(editor, weight=3)
        listbox = tk.Listbox(listing, exportselection=False)
        listbox.pack(fill="both", expand=True)
        bindings = self.binding_model.bindings()
        for binding in bindings:
            prefix = "" if binding.enabled else "[disabled] "
            listbox.insert("end", prefix + binding.label)
        ids = [binding.id for binding in bindings]
        ttk.Button(listing, text="New binding", command=lambda: (
            setattr(self, "_selected_binding_id", None),
            self._load_binding_editor(editor, None))).pack(fill="x", pady=(5, 0))

        def selected(_event=None):
            selection = listbox.curselection()
            if selection:
                self._selected_binding_id = ids[selection[0]]
                self._load_binding_editor(editor, self._selected_binding_id)
        listbox.bind("<<ListboxSelect>>", selected)
        if ids:
            selected_index = (ids.index(self._selected_binding_id)
                              if self._selected_binding_id in ids else 0)
            self._selected_binding_id = ids[selected_index]
            listbox.selection_set(selected_index)
            self._load_binding_editor(editor, self._selected_binding_id)

    @staticmethod
    def _csv_values(text):
        return [item.strip() for item in text.split(",") if item.strip()]

    def _load_binding_editor(self, parent, binding_id):
        for child in parent.winfo_children():
            child.destroy()
        if binding_id is None:
            row = {
                "id": self.binding_model.next_custom_id(),
                "label": "New binding",
                "enabled": True,
                "chord": [],
                "match": "exact",
                "activation": "hold",
                "priority": 0,
                "press": [{"command": "input.mode.toggle"}],
                "release": [],
            }
        else:
            row = self.binding_model.row(binding_id)
        def entry(label, value, *, width=52):
            line = ttk.Frame(parent)
            line.pack(fill="x", padx=8, pady=3)
            widget_label = ttk.Label(line, text=label, width=18, anchor="w")
            widget_label.pack(side="left")
            var = tk.StringVar(value=value)
            ttk.Entry(line, textvariable=var, width=width).pack(side="left", fill="x", expand=True)
            return line, var

        _line, label_var = entry("Label", row["label"])
        enabled_var = tk.BooleanVar(value=row["enabled"])
        enabled_line = ttk.Frame(parent)
        enabled_line.pack(fill="x", padx=8, pady=3)
        ttk.Label(enabled_line, text="Enabled", width=18, anchor="w").pack(side="left")
        ttk.Checkbutton(enabled_line, variable=enabled_var).pack(side="left")

        chord_line, chord_var = entry("Chord", ", ".join(row["chord"]))
        ttk.Button(chord_line, text="Record…", command=lambda: self._capture_chord(
            chord_var)).pack(side="right", padx=(5, 0))
        warning_var = tk.StringVar(value=self.binding_model.pass_through_warning(row["chord"]))
        ttk.Label(parent, textvariable=warning_var, foreground="#9a6700",
                  wraplength=520, justify="left").pack(fill="x", padx=26, pady=(0, 3))
        chord_var.trace_add("write", lambda *_: warning_var.set(
            self.binding_model.pass_through_warning(self._csv_values(chord_var.get()))))

        match_line = ttk.Frame(parent)
        match_line.pack(fill="x", padx=8, pady=3)
        ttk.Label(match_line, text="Extra modifier keys", width=18, anchor="w").pack(side="left")
        match_labels = {
            "Must match exactly": "exact",
            "May include extra modifiers": "allow_extra_modifiers",
        }
        match_var = tk.StringVar(value=next(
            label for label, value in match_labels.items() if value == row["match"]))
        ttk.Combobox(match_line, textvariable=match_var, values=tuple(match_labels),
                     state="readonly", width=28).pack(side="left")

        when = row.get("when", {})
        selected_apps = when.get("apps", [])
        app_options = tuple(
            (app_spec.app_id, app_spec.display_name) for app_spec in APP_SPECS
        ) + ((None, "Other applications (not integrated)"),)
        app_line = ttk.Frame(parent)
        app_line.pack(fill="x", padx=8, pady=3)
        app_label = ttk.Label(app_line, text="Applications", width=18, anchor="nw")
        app_label.pack(side="left")
        app_selector = ttk.Frame(app_line)
        app_selector.pack(side="left", fill="x", expand=True)
        app_list = tk.Listbox(
            app_selector, selectmode="multiple", exportselection=False, height=5)
        app_scroll = ttk.Scrollbar(app_selector, orient="vertical", command=app_list.yview)
        app_list.configure(yscrollcommand=app_scroll.set)
        app_list.pack(side="left", fill="x", expand=True)
        app_scroll.pack(side="right", fill="y")
        for index, (app_id, app_label_text) in enumerate(app_options):
            app_list.insert("end", app_label_text)
            if app_id in selected_apps or (app_id is None and when.get("other_apps")):
                app_list.selection_set(index)
        all_apps = ttk.Button(app_line, text="All", width=6, command=lambda: app_list.selection_clear(
            0, "end"))
        all_apps.pack(side="left", padx=(6, 0))
        app_help = (
            "Select any combination of integrated apps and Other applications. "
            "No selection means all applications.")
        self._tooltip(app_label, app_help)
        self._tooltip(app_list, app_help)
        self._tooltip(all_apps, app_help)

        simple = self.binding_model.simple_action(row)
        editor_advanced_only = bool(
            simple.advanced_only or when.get("executables") or when.get("input_profiles"))
        target_options = dict(self.binding_model.simple_target_options())
        target_by_label = {label: target_id for target_id, label in target_options.items()}
        simple_frame = ttk.LabelFrame(parent, text="Action")
        simple_frame.pack(fill="x", padx=8, pady=6)
        advanced_notice = tk.StringVar(value=(
            "This binding uses advanced DSL features. Use Advanced DSL to edit its action."
            if editor_advanced_only else ""))
        ttk.Label(simple_frame, textvariable=advanced_notice, foreground="#9a6700",
                  wraplength=520).pack(fill="x", padx=8, pady=(5, 0))
        target_line = ttk.Frame(simple_frame)
        target_line.pack(fill="x", padx=8, pady=4)
        ttk.Label(target_line, text="What it controls", width=18, anchor="w").pack(side="left")
        target_var = tk.StringVar(value=target_options.get(simple.target_id, ""))
        target_combo = ttk.Combobox(target_line, textvariable=target_var,
                                    values=tuple(target_by_label), state="readonly", width=38)
        target_combo.pack(side="left", fill="x", expand=True)

        operation_line = ttk.Frame(simple_frame)
        operation_line.pack(fill="x", padx=8, pady=4)
        ttk.Label(operation_line, text="Behavior", width=18, anchor="w").pack(side="left")
        operation_var = tk.StringVar()
        operation_combo = ttk.Combobox(operation_line, textvariable=operation_var,
                                       state="readonly", width=38)
        operation_combo.pack(side="left", fill="x", expand=True)
        value_line = ttk.Frame(simple_frame)
        value_line.pack(fill="x", padx=8, pady=4)
        value_label = ttk.Label(value_line, text="Value", width=18, anchor="w")
        value_label.pack(side="left")
        value_var = tk.StringVar(value=simple.value_text)
        value_entry = ttk.Combobox(value_line, textvariable=value_var)
        value_entry.pack(side="left", fill="x", expand=True)

        operation_ids = {}

        def refresh_value_field(*_):
            target_id = target_by_label.get(target_var.get(), "")
            if not target_id.startswith("setting:"):
                return
            setting_id = target_id.removeprefix("setting:")
            operation_id = operation_ids.get(operation_var.get(), "")
            choices = self.binding_model.setting_value_options(setting_id)
            if operation_id == "cycle":
                value_label.configure(text="Values (A, B, …)")
                value_entry.configure(values=(), state="normal")
                if value_var.get() in {"", "Automatic"} and choices:
                    value_var.set(", ".join(choices))
            elif operation_id == "toggle" and choices:
                value_label.configure(text="Values")
                value_entry.configure(values=(), state="disabled")
                value_var.set("Automatic")
            elif operation_id == "hold" and choices:
                value_label.configure(text="Value while held")
                value_entry.configure(values=choices, state="readonly")
                if value_var.get() not in choices:
                    value_var.set(choices[0])
            else:
                if operation_id == "toggle":
                    label = "Two values (A, B)"
                elif operation_id in {"add", "multiply"}:
                    label = "Amount / factor"
                else:
                    label = "Value while held"
                value_label.configure(text=label)
                value_entry.configure(values=(), state="normal")
                if value_var.get() == "Automatic":
                    value_var.set("")

        def refresh_action_fields(*_):
            target_id = target_by_label.get(target_var.get(), "")
            operation_ids.clear()
            if target_id.startswith("setting:"):
                setting_id = target_id.removeprefix("setting:")
                options = self.binding_model.setting_operation_options(setting_id)
                operation_ids.update({label: operation_id for operation_id, label in options})
                operation_combo.configure(values=tuple(operation_ids), state="readonly")
                selected = next((label for label, operation_id in operation_ids.items()
                                 if operation_id == simple.operation_id), None)
                operation_var.set(selected or (next(iter(operation_ids)) if operation_ids else ""))
                refresh_value_field()
            else:
                operation_combo.configure(values=(), state="disabled")
                operation_var.set("Built in")
                value_var.set("")
                value_entry.configure(state="disabled")
                value_label.configure(text="Value")
        def select_action_fields(*_):
            target_id = target_by_label.get(target_var.get(), "")
            current_policy = match_labels[match_var.get()]
            recommended = self.binding_model.recommended_match_policy(
                target_id, current_policy)
            match_var.set(next(label for label, value in match_labels.items()
                               if value == recommended))
            refresh_action_fields()

        target_combo.bind("<<ComboboxSelected>>", select_action_fields)
        operation_combo.bind("<<ComboboxSelected>>", refresh_value_field)
        refresh_action_fields()

        validation_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=validation_var, foreground="#b42318",
                  wraplength=540, justify="left").pack(fill="x", padx=26, pady=4)

        def current_row():
            when_value = {}
            selected_options = [app_options[index] for index in app_list.curselection()]
            selected_app_ids = [app_id for app_id, _label in selected_options
                                if app_id is not None]
            if selected_app_ids:
                when_value["apps"] = selected_app_ids
            if any(app_id is None for app_id, _label in selected_options):
                when_value["other_apps"] = True
            target_id = target_by_label.get(target_var.get(), "")
            operation_id = operation_ids.get(operation_var.get(), "")
            activation, press, release = self.binding_model.build_simple_action(
                target_id, operation_id, value_var.get())
            result = {
                "id": row["id"],
                "label": label_var.get().strip(),
                "enabled": enabled_var.get(),
                "chord": self._csv_values(chord_var.get()),
                "match": match_labels[match_var.get()],
                "activation": activation,
                "priority": row.get("priority", 0),
                "press": press,
                "release": release,
            }
            if when_value:
                result["when"] = when_value
            return result

        def save():
            try:
                saved = current_row()
                self._selected_binding_id = saved["id"]
                self.binding_model.save(saved)
                validation_var.set("Saved")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                validation_var.set(str(exc))

        actions = ttk.Frame(parent)
        actions.pack(fill="x", padx=8, pady=8)
        save_button = ttk.Button(actions, text="Save", command=save)
        save_button.pack(side="left")
        if editor_advanced_only:
            save_button.configure(state="disabled")
        ttk.Button(actions, text="Advanced DSL…", command=lambda: self._advanced_binding_editor(
            (lambda: row) if editor_advanced_only else current_row,
            validation_var)).pack(side="left", padx=5)
        if binding_id is not None:
            ttk.Button(actions, text="Delete", command=lambda: self._delete_binding(
                binding_id)).pack(side="right")
            if self.binding_model.is_system_binding(binding_id):
                ttk.Button(actions, text="Restore System binding", command=lambda: (
                    self.binding_model.restore_system(binding_id),
                    self._render_keybindings(self._keybinding_host))).pack(side="right", padx=5)

    def _delete_binding(self, binding_id):
        if messagebox.askyesno("Delete binding", f"Delete {binding_id}?", parent=self.win):
            self.binding_model.delete(binding_id)

    def _advanced_binding_editor(self, row_factory, validation_var):
        try:
            row = row_factory()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            validation_var.set(str(exc))
            return
        dialog = tk.Toplevel(self.win)
        dialog.title("Advanced binding DSL")
        dialog.geometry("700x500")
        ttk.Label(dialog, text=(
            "Edit the complete declarative binding. Priority is available here; executable "
            "code, shell commands, and unknown targets are rejected."),
            wraplength=650, justify="left").pack(fill="x", padx=10, pady=8)
        text = tk.Text(dialog, wrap="none")
        text.pack(fill="both", expand=True, padx=10, pady=4)
        text.insert("1.0", json.dumps(row, indent=2))
        error = tk.StringVar()
        ttk.Label(dialog, textvariable=error, foreground="#b42318").pack(
            fill="x", padx=10)

        def save():
            try:
                parsed = json.loads(text.get("1.0", "end"))
                if not isinstance(parsed, dict):
                    raise ValueError("binding DSL must be one JSON object")
                self._selected_binding_id = parsed.get("id")
                self.binding_model.save(parsed)
                dialog.destroy()
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                error.set(str(exc))
        ttk.Button(dialog, text="Validate and save", command=save).pack(
            side="right", padx=10, pady=8)

    def _capture_chord(self, destination):
        dialog = tk.Toplevel(self.win)
        dialog.title("Record chord")
        dialog.transient(self.win)
        dialog.grab_set()
        captured = set()
        text = tk.StringVar(value="Press the keyboard or device controls together.")
        ttk.Label(dialog, textvariable=text, width=58, wraplength=430,
                  justify="left").pack(padx=14, pady=14)
        selector_names = set(self.binding_model.catalog.control_selectors)
        available = ttk.Frame(dialog)
        available.pack(fill="x", padx=14, pady=(0, 10))
        available_var = tk.StringVar(value=next(iter(sorted(selector_names))))
        ttk.Combobox(available, textvariable=available_var,
                     values=tuple(sorted(selector_names)), state="readonly", width=42).pack(
                         side="left", fill="x", expand=True)
        ttk.Button(available, text="Add control", command=lambda: (
            captured.add(available_var.get()), update())).pack(side="left", padx=(6, 0))
        modifier_map = {
            "Control_L": "keyboard:ctrl", "Control_R": "keyboard:ctrl",
            "Shift_L": "keyboard:shift", "Shift_R": "keyboard:shift",
            "Alt_L": "keyboard:alt", "Alt_R": "keyboard:alt",
            "Meta_L": "keyboard:meta", "Meta_R": "keyboard:meta",
            "Super_L": "keyboard:meta", "Super_R": "keyboard:meta",
        }

        def update():
            text.set(" + ".join(sorted(captured)) or
                     "Press the keyboard or device controls together.")

        def key(event):
            token = modifier_map.get(event.keysym)
            if token is None:
                token = f"keyboard:{event.keysym.lower()}"
            if token in selector_names:
                captured.add(token)
                update()
            return "break"

        def poll():
            if not dialog.winfo_exists():
                return
            aggregator = self.binding_model.aggregator
            if aggregator is not None:
                for token in aggregator.snapshot().pressed_tokens:
                    if not token.startswith("keyboard:") and token in selector_names:
                        captured.add(token)
                update()
            dialog.after(60, poll)

        def done():
            if captured:
                destination.set(", ".join(sorted(captured)))
                dialog.destroy()
        dialog.bind("<KeyPress>", key)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(buttons, text="Clear", command=lambda: (captured.clear(), update())).pack(
            side="left")
        ttk.Button(buttons, text="Use chord", command=done).pack(side="right")
        dialog.focus_force()
        poll()

    # --- config helpers -----------------------------------------------------------
    def _get(self, keys):
        return self.cfg.ui_value(keys)

    def _set_and_save(self, keys, value):
        self.cfg.set_ui_value(keys, value)
        if self._refresh_twist_warning is not None:
            self._refresh_twist_warning()

    @staticmethod
    def _tooltip(widget, text):
        if text:
            _ToolTip(widget, text)
        return widget

    def _entry_row(self, parent, label, keys, cast=float, width=12, hint=""):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        label_widget = ttk.Label(frame, text=label, width=24, anchor="w")
        label_widget.pack(side="left")
        var = tk.StringVar(value=self._fmt(self._get(keys)))
        entry = ttk.Entry(frame, textvariable=var, width=width)
        entry.pack(side="left")
        self._tooltip(label_widget, hint)
        self._tooltip(entry, hint)

        def commit(*_):
            raw = var.get().strip()
            try:
                value = cast(raw)
            except (ValueError, TypeError):
                var.set(self._fmt(self._get(keys)))    # revert bad input
                return
            self._set_and_save(keys, value)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        return var

    def _combo_row(self, parent, label, keys, values, cast=str, on_change=None, hint=""):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        label_widget = ttk.Label(frame, text=label, width=24, anchor="w")
        label_widget.pack(side="left")
        display_to_value = {_option_label(v): v for v in values}
        value_to_display = {str(v): d for d, v in display_to_value.items()}
        var = tk.StringVar(value=value_to_display.get(str(self._get(keys)), _option_label(values[0])))
        combo = ttk.Combobox(frame, textvariable=var, values=list(display_to_value),
                             state="readonly", width=18)
        combo.pack(side="left")
        self._tooltip(label_widget, hint)
        self._tooltip(combo, hint)

        def on_sel(_):
            self._set_and_save(keys, cast(display_to_value[var.get()]))  # save first...
            if on_change is not None:
                on_change()                                # ...then apply side effects

        combo.bind("<<ComboboxSelected>>", on_sel)
        return var

    # Per-app viewport refresh rate: a dropdown of common rates that's also free-typeable.
    # "Default" is a UI-only compatibility value; ConfigStore converts it to a missing override.
    _RATE_PRESETS = ("Default", "15", "30", "45", "60", "90", "120")

    def _rate_combo(self, parent, label, keys, hint=""):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        label_widget = ttk.Label(frame, text=label, width=24, anchor="w")
        label_widget.pack(side="left")
        var = tk.StringVar(value=self._rate_text(self._get(keys)))
        combo = ttk.Combobox(frame, textvariable=var, values=self._RATE_PRESETS, width=10)
        combo.pack(side="left")
        self._tooltip(label_widget, hint)
        self._tooltip(combo, hint)

        def commit(*_):
            s = var.get().strip().lower()
            if s in ("default", "", "0"):
                value = 0
            else:
                try:
                    value = max(1, min(240, int(float(s))))
                except (ValueError, TypeError):
                    var.set(self._rate_text(self._get(keys)))      # revert bad input
                    return
            var.set(self._rate_text(value))
            self._set_and_save(keys, value)

        combo.bind("<<ComboboxSelected>>", commit)
        combo.bind("<Return>", commit)
        combo.bind("<FocusOut>", commit)
        return var

    @staticmethod
    def _rate_text(v):
        try:
            return "Default" if not int(v) else str(int(v))
        except (ValueError, TypeError):
            return "Default"

    def _invert_check(self, parent, label, keys):
        var = tk.BooleanVar(value=bool(self._get(keys)))
        ttk.Checkbutton(parent, text=label, variable=var,
                        command=lambda: self._set_and_save(keys, bool(var.get()))).pack(side="left", padx=4)
        return var

    def _axis_combo(self, parent, keys, width=4):
        """Compact X/Y/Z source selector for an action. Duplicate sources are allowed."""
        labels = ("X", "Y", "Z")
        try:
            current = int(self._get(keys))
        except (TypeError, ValueError):
            current = 0
        var = tk.StringVar(value=labels[current] if current in (0, 1, 2) else "X")
        combo = ttk.Combobox(parent, textvariable=var, values=labels,
                             state="readonly", width=width)
        combo.pack(side="left", padx=(2, 5))
        combo.bind("<<ComboboxSelected>>",
                   lambda _e: self._set_and_save(keys, labels.index(var.get())))
        return var

    def _global_axis_orientation_editor(self, parent):
        """Permutation-safe physical orientation editor.

        Selecting an already-used source swaps it with the affected logical axis, so every edit
        remains a complete permutation and can apply live without a transient duplicated axis.
        """
        source_keys = ("general", "axis_orientation", "source")
        invert_keys = ("general", "axis_orientation", "invert")
        labels = ("X", "Y", "Z")
        sources = normalize_axis_permutation(self._get(source_keys))
        vars_ = []
        combos = []
        invert_vars = []

        for target in range(3):
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=12, pady=2)
            ttk.Label(row, text=f"Logical {labels[target]}", width=12, anchor="w").pack(side="left")
            ttk.Label(row, text="uses physical", foreground="#555").pack(side="left")
            var = tk.StringVar(value=labels[sources[target]])
            combo = ttk.Combobox(row, textvariable=var, values=labels,
                                 state="readonly", width=4)
            combo.pack(side="left", padx=(5, 12))
            invert_vars.append(self._invert_check(row, "Invert", invert_keys + (target,)))
            vars_.append(var)
            combos.append(combo)

        def select(target):
            current = normalize_axis_permutation(self._get(source_keys))
            wanted = labels.index(vars_[target].get())
            current = swap_axis_source(current, target, wanted)
            for i, var in enumerate(vars_):
                var.set(labels[current[i]])
            self._set_and_save(source_keys, current)

        for i, combo in enumerate(combos):
            combo.bind("<<ComboboxSelected>>", lambda _e, target=i: select(target))

        def reset():
            for i, var in enumerate(vars_):
                var.set(labels[i])
            for var in invert_vars:
                var.set(False)
            self._set_and_save(source_keys, [0, 1, 2])
            self._set_and_save(invert_keys, [False, False, False])

        reset_button = ttk.Button(parent, text="Reset orientation", command=reset)
        reset_button.pack(anchor="w", padx=12, pady=(4, 5))
        self._tooltip(reset_button, "Changing a source swaps axes instead of duplicating one. This "
                                    "mapping applies to pointer and 3D modes before app settings.")

    def _bool_row(self, parent, label, keys, hint=""):
        """Full-width checkbox bound to a boolean config key (saves + applies live on toggle)."""
        var = tk.BooleanVar(value=bool(self._get(keys)))
        check = ttk.Checkbutton(parent, text=label, variable=var,
                                command=lambda: self._set_and_save(keys, bool(var.get())))
        check.pack(anchor="w", padx=12, pady=2)
        self._tooltip(check, hint)
        return var

    def _level_horizon_row(self, parent, app_key):
        """Per-app 'level horizon on fixed-horizon mode entry' checkbox. Shows the EFFECTIVE value
        (per-app override if the user ever touched it, else the General default); the first toggle
        writes an explicit per-app override. 'Reset user overrides' removes the override so the app
        follows the General checkbox again."""
        var = tk.BooleanVar(value=self.cfg.snapshot().app_value(
            app_key, "navigation.level_horizon_on_entry"))
        check = ttk.Checkbutton(parent, text="Level horizon when entering Turntable/Walk", variable=var,
                                command=lambda: self._set_and_save(
                                    ("apps", app_key, "level_horizon_on_entry"), bool(var.get())))
        check.pack(anchor="w", padx=12, pady=2)
        self._tooltip(check, "On removes existing roll once when entering Turntable, Lock Horizon, "
                             "or Walk. Off preserves the current tilt. Reset makes this app follow "
                             "the General default again.")
        return var

    def _mapped_combo_row(self, parent, label, keys, options, hint="", on_change=None,
                          trailing_factory=None):
        """Readonly combo whose display labels differ from the stored values. `options` is a list of
        (display, value) pairs -- used for the Blender Advanced combos that relabel the generic
        scheme with Blender terms. Optional `on_change(new_value)` runs after save."""
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        label_widget = ttk.Label(frame, text=label, width=24, anchor="w")
        label_widget.pack(side="left")
        disp_by_val = {v: d for d, v in options}
        val_by_disp = {d: v for d, v in options}
        cur = self._get(keys)
        var = tk.StringVar(value=disp_by_val.get(cur, options[0][0]))
        combo = ttk.Combobox(frame, textvariable=var, values=[d for d, _ in options],
                             state="readonly", width=22)
        combo.pack(side="left")
        self._tooltip(label_widget, hint)
        self._tooltip(combo, hint)
        if trailing_factory is not None:
            trailing_factory(frame, var)

        def _on_select(_e=None):
            val = val_by_disp[var.get()]
            self._set_and_save(keys, val)
            if on_change is not None:
                on_change(val)

        combo.bind("<<ComboboxSelected>>", _on_select)
        return var

    def _orbit_fallback_editor(self, parent):
        """Ordered global pivot-fallback editor; edits save/apply immediately."""
        keys = ("general", "orbit_pivot_fallbacks")
        labels = _PIVOT_LABELS
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(3, 5))
        fallback_label = ttk.Label(row, text="Failure fallback order", width=24, anchor="nw")
        fallback_label.pack(side="left")
        body = ttk.Frame(row)
        body.pack(side="left", fill="x", expand=True)
        listbox = tk.Listbox(body, height=4, width=27, exportselection=False)
        listbox.grid(row=0, column=0, rowspan=4, sticky="nsew")
        fallback_hint = ("When the selected orbit pivot fails, resolution restarts at the top of "
                         "this list. Unsupported methods are skipped; an empty list means no fallback.")
        self._tooltip(fallback_label, fallback_hint)
        self._tooltip(listbox, fallback_hint)
        body.columnconfigure(0, weight=1)

        chain = normalize_orbit_pivot_fallbacks(self._get(keys))

        def render(select=None):
            listbox.delete(0, "end")
            for i, method in enumerate(chain, 1):
                listbox.insert("end", "%d. %s" % (i, labels[method]))
            if chain and select is not None:
                select = max(0, min(select, len(chain) - 1))
                listbox.selection_set(select)
                listbox.activate(select)

        def selected():
            sel = listbox.curselection()
            return int(sel[0]) if sel else None

        def save(select=None):
            self._set_and_save(keys, list(chain))
            render(select)

        def move(delta):
            i = selected()
            if i is None or not 0 <= i + delta < len(chain):
                return
            chain[i], chain[i + delta] = chain[i + delta], chain[i]
            save(i + delta)

        ttk.Button(body, text="Up", width=8, command=lambda: move(-1)).grid(
            row=0, column=1, padx=(6, 0), sticky="ew")
        ttk.Button(body, text="Down", width=8, command=lambda: move(1)).grid(
            row=1, column=1, padx=(6, 0), sticky="ew")

        add_var = tk.StringVar(value=labels[ORBIT_PIVOT_METHODS[0]])
        add_combo = ttk.Combobox(body, textvariable=add_var,
                                 values=[labels[m] for m in ORBIT_PIVOT_METHODS],
                                 state="readonly", width=16)
        add_combo.grid(row=4, column=0, sticky="w", pady=(4, 0))

        def add():
            method = next((m for m in ORBIT_PIVOT_METHODS if labels[m] == add_var.get()), None)
            if method is not None and method not in chain:
                chain.append(method)
                save(len(chain) - 1)

        def remove():
            i = selected()
            if i is not None:
                chain.pop(i)
                save(min(i, len(chain) - 1))

        ttk.Button(body, text="Add", width=8, command=add).grid(
            row=4, column=1, padx=(6, 0), pady=(4, 0), sticky="ew")
        ttk.Button(body, text="Remove", width=8, command=remove).grid(
            row=2, column=1, padx=(6, 0), sticky="ew")
        render()

    def _copy_onshape_userscript(self):
        """Copy the Violentmonkey/Tampermonkey userscript to the clipboard. Returns True on success."""
        from .onshape_bridge import pointer_userscript_source
        return self._clipboard_set(pointer_userscript_source())

    def _show_onshape_userscript_dialog(self, *, title, lead="", show_dont_show_again=False,
                                        copy_on_open=False):
        """Modal with Copy userscript + install steps. Optional 'do not show again' for the cursor warn.
        When `copy_on_open` is True, the script is copied immediately and the lead becomes 'Copied!'."""
        from .onshape_bridge import pointer_install_instructions, POINTER_SCRIPT_URL

        parent = self.win if self.win is not None else self.root
        dlg = tk.Toplevel(parent)
        dlg.title(title)
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.grab_set()

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        status = tk.StringVar(value="")
        if copy_on_open:
            if self._copy_onshape_userscript():
                status.set("Copied!")
                if not lead:
                    lead = "The userscript is on your clipboard. Install steps:"
            else:
                status.set("Copy failed — open %s and paste manually." % POINTER_SCRIPT_URL)

        if lead:
            ttk.Label(body, text=lead, wraplength=520, justify="left").pack(anchor="w", pady=(0, 8))

        ttk.Label(body, textvariable=status, foreground="#1a7f37").pack(anchor="w")

        def _copy():
            if self._copy_onshape_userscript():
                status.set("Copied!")
            else:
                status.set("Copy failed — open %s and paste manually." % POINTER_SCRIPT_URL)

        ttk.Button(body, text="Copy userscript", command=_copy).pack(anchor="w", pady=(4, 10))

        ttk.Label(body, text=pointer_install_instructions(), wraplength=520,
                  justify="left").pack(anchor="w")

        dont = tk.BooleanVar(value=False)
        if show_dont_show_again:
            ttk.Checkbutton(body, text="Do not show again", variable=dont).pack(
                anchor="w", pady=(12, 0))

        def _close():
            if show_dont_show_again and dont.get():
                self._set_and_save(("onshape", "cursor_userscript_warn_dismissed"), True)
            dlg.destroy()

        ttk.Button(body, text="OK", command=_close).pack(anchor="e", pady=(14, 0))
        dlg.protocol("WM_DELETE_WINDOW", _close)
        dlg.wait_window()

    def _warn_onshape_cursor_userscript_if_needed(self, new_value):
        if new_value != "cursor":
            return
        if self.cfg.snapshot().onshape.get("cursor_userscript_warn_dismissed"):
            return
        self._show_onshape_userscript_dialog(
            title="Onshape — under-cursor orbit",
            lead="Orbit pivot \"Under Cursor (mouse)\" needs the Astrolabe userscript in your "
                 "Onshape browser. Without a hit, the configured fallback chain is used.",
            show_dont_show_again=True,
        )
    @staticmethod
    def _fmt(v):
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    # --- tab a: 3D app integrations -----------------------------------------------
    def _build_apps_tab(self, nb):
        outer = ttk.Frame(nb)
        # Not "Supported 3D apps": the list contains both release tiers, and calling the whole of it
        # supported is the claim this panel exists to stop making.
        ttk.Label(outer, text="3D app integrations — every one starts disabled. Enable the ones you "
                              "use, check their verified host versions, and expand for honest "
                              "setup/manual-install instructions.",
                  wraplength=600, foreground="#555").pack(anchor="w", padx=10, pady=(10, 6))
        self._broker_health_label = ttk.Label(
            outer,
            text="Navigation broker: status unavailable",
            wraplength=600,
            justify="left",
            foreground="#666",
        )
        self._broker_health_label.pack(anchor="w", padx=10, pady=(0, 6))
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        body = self._scrollable(holder)
        for tier in SupportTier:
            self._app_tier_section(body, tier)
        self.update_service_health(self.app.runtime_health_snapshot())
        return outer

    def _app_tier_section(self, parent, tier):
        """One release tier's integrations, grouped and ordered by the registry.

        Supported integrations stay open because they are what the release stands behind.
        Experimental ones start collapsed: they are opt-in, and an eleven-card list that gives every
        integration equal weight is itself a support claim.
        """
        appdefs = [integrations.APPS_BY_KEY[app_id] for app_id in APP_IDS_BY_TIER[tier]]
        if not appdefs:
            return
        label = SUPPORT_TIER_LABELS[tier]
        rows = ttk.Frame(parent)

        if tier is SupportTier.EXPERIMENTAL:
            shown = tk.BooleanVar(value=False)
            button = ttk.Button(parent)

            def button_text():
                return (("Hide " if shown.get() else "Show ")
                        + f"{len(appdefs)} experimental integrations")

            def toggle():
                shown.set(not shown.get())
                if shown.get():
                    rows.pack(fill="x")
                else:
                    rows.pack_forget()
                button.config(text=button_text())

            button.config(text=button_text(), command=toggle)
            button.pack(anchor="w", padx=10, pady=(12, 2))
        else:
            ttk.Label(parent, text=f"{label}s", font=("", 9, "bold")).pack(
                anchor="w", padx=10, pady=(8, 0))
            rows.pack(fill="x")

        ttk.Label(rows, text=SUPPORT_TIER_SUMMARIES[tier], foreground="#555",
                  wraplength=590, justify="left").pack(anchor="w", padx=10, pady=(0, 2))
        for appdef in appdefs:
            self._app_row(rows, appdef)

    def _app_status_text(self, appdef):
        base = integrations.status_line(appdef)
        observed = getattr(self.app, "observed_addin_versions", {}).get(appdef.key)
        stale = (integrations.update_available(appdef.key, installed_version=observed)
                 if observed is not None else integrations.update_available(appdef.key))
        if appdef.key in integrations.ADDIN_KEYS and stale:
            base += "  •  update available"
        health = self.app.runtime_health_snapshot().get(appdef.key)
        if health is not None:
            base += f"\nruntime {health.state.value}: {health.detail}"
        return base

    def _app_button_text(self, appdef):
        observed = getattr(self.app, "observed_addin_versions", {}).get(appdef.key)
        if observed is not None:
            return integrations.setup_action_label(
                appdef, self.cfg.snapshot().app_operational[appdef.key], installed_version=observed)
        return integrations.setup_action_label(
            appdef, self.cfg.snapshot().app_operational[appdef.key])

    @staticmethod
    def _compatibility_text(appdef):
        """The host-version claim only. The release tier is a separate line, deliberately.

        Colour here signals a problem with the detected host version, so folding the tier in would
        paint "Experimental integration" red and say something this project does not mean.
        """
        result = integrations.compatibility(appdef)
        text = f"Verified host versions: {appdef.verified_versions}"
        if result.status == "unsupported":
            return text + f"\nWARNING: detected {result.message}.", "#b42318"
        if result.status == "unverified":
            return text + f"\nCAUTION: detected host {result.message}.", "#b45309"
        return text, "#555"

    def _app_row(self, parent, appdef):
        a = self.cfg.snapshot().app_operational[appdef.key]
        card = ttk.LabelFrame(parent, text=appdef.name)
        card.pack(fill="x", padx=10, pady=5)

        status = ttk.Label(card, text=self._app_status_text(appdef), foreground="#666")
        status.pack(anchor="w", padx=8, pady=(4, 0))
        self._app_status_labels[appdef.key] = status

        # Stated on the card as well as in the group heading, so a card read on its own -- in a
        # screenshot, or in a support conversation -- still carries its release commitment.
        ttk.Label(card, text=appdef.support_label,
                  foreground=("#1a7f37" if appdef.support_tier is SupportTier.SUPPORTED
                              else "#7a4e00")).pack(anchor="w", padx=8, pady=(3, 0))

        compat_text, compat_color = self._compatibility_text(appdef)
        ttk.Label(card, text=compat_text, foreground=compat_color, wraplength=590,
                  justify="left").pack(anchor="w", padx=8, pady=(3, 0))
        ttk.Label(card, text=f"Install model: {appdef.install_model}", foreground="#555",
                  wraplength=590, justify="left").pack(anchor="w", padx=8, pady=(2, 0))
        ttk.Label(card, text=f"Security: {appdef.security_notes}", foreground="#7a4e00",
                  wraplength=590, justify="left").pack(anchor="w", padx=8, pady=(2, 0))
        setup_text = "one-time setup required" if appdef.setup_required else "no host setup required"
        ttk.Label(card, text=f"Setup requirement: {setup_text}", foreground="#555").pack(
            anchor="w", padx=8, pady=(2, 0))

        controls = ttk.Frame(card)
        controls.pack(fill="x", padx=8, pady=6)

        enabled = tk.BooleanVar(value=a["enabled"])
        ttk.Checkbutton(controls, text="Enabled", variable=enabled,
                        command=lambda: self._set_and_save(("apps", appdef.key, "enabled"),
                                                           bool(enabled.get()))).pack(side="left")

        holder = {}
        action = self._app_button_text(appdef)
        btn = ttk.Button(controls, text=action or "",
                         command=lambda: self._do_install(appdef, enabled, status, holder))
        holder["btn"] = btn
        self._app_action_buttons[appdef.key] = btn
        if action:
            btn.pack(side="right")

        details = ttk.Frame(card)
        shown = tk.BooleanVar(value=False)

        def toggle_details():
            if shown.get():
                details.pack_forget()
                shown.set(False)
                info_btn.config(text="Instructions ▾")
            else:
                details.pack(fill="x", padx=8, pady=(0, 8))
                shown.set(True)
                info_btn.config(text="Instructions ▴")

        info_btn = ttk.Button(controls, text="Instructions ▾", command=toggle_details)
        info_btn.pack(side="right", padx=(0, 8))

        instruction_text = integrations.integration_instructions(appdef)
        ttk.Separator(details).pack(fill="x", pady=(0, 6))
        ttk.Label(details, text=instruction_text, wraplength=570, justify="left").pack(anchor="w")

        copy_status = tk.StringVar(value="")
        copy_row = ttk.Frame(details)
        copy_row.pack(fill="x", pady=(7, 0))

        def copy_instructions():
            if self._clipboard_set(instruction_text):
                copy_status.set("Copied")
            else:
                copy_status.set("Copy failed")

        ttk.Button(copy_row, text="Copy instructions", command=copy_instructions).pack(side="left")
        if appdef.key == "onshape":
            ttk.Button(
                copy_row,
                text="Copy userscript…",
                command=lambda: self._show_onshape_userscript_dialog(
                    title="Onshape — copy userscript", copy_on_open=True),
            ).pack(side="left", padx=(8, 0))
        ttk.Label(copy_row, textvariable=copy_status, foreground="#1a7f37").pack(
            side="left", padx=8)

    def _clipboard_set(self, text):
        """Copy ``text`` to the clipboard. Returns True on success."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            return True
        except tk.TclError:
            return False

    def _show_integration_dialog(self, title, msg, copyables=None, *, warning=False):
        """Modal result dialog for Set up / Update. ``copyables`` is ``[(button_label, text), ...]``."""
        parent = self.win if self.win is not None else self.root
        dlg = tk.Toplevel(parent)
        dlg.title(title)
        dlg.transient(parent)
        dlg.resizable(True, True)
        dlg.grab_set()

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        # Scrollable message for long install notes (Unreal admin steps, etc.).
        text_frame = ttk.Frame(body)
        text_frame.pack(fill="both", expand=True)
        vsb = ttk.Scrollbar(text_frame, orient="vertical")
        text = tk.Text(text_frame, wrap="word", width=72, height=14,
                       yscrollcommand=vsb.set, relief="flat", padx=2, pady=2)
        vsb.config(command=text.yview)
        vsb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", msg)
        text.configure(state="disabled")

        status = tk.StringVar(value="")
        ttk.Label(body, textvariable=status, foreground="#1a7f37").pack(anchor="w", pady=(8, 0))

        if copyables:
            btn_row = ttk.Frame(body)
            btn_row.pack(fill="x", pady=(8, 0))

            def _make_copy(label, payload):
                def _copy():
                    if self._clipboard_set(payload):
                        status.set("Copied: " + label)
                    else:
                        status.set("Copy failed — select and copy from the message above.")
                return _copy

            for label, payload in copyables:
                ttk.Button(btn_row, text=label,
                           command=_make_copy(label, payload)).pack(side="left", padx=(0, 8))

        foot = ttk.Frame(body)
        foot.pack(fill="x", pady=(14, 0))
        ttk.Button(foot, text="OK", command=dlg.destroy).pack(side="right")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.wait_window()

    def _do_install(self, appdef, enabled_var, status_label, holder):
        parent = self.win if self.win is not None else self.root
        # A known-incompatible host version needs an explicit override, and the user has to be shown
        # the exact reason first. `integrations.install` refuses without the override, so declining
        # here and passing nothing lead to the same place.
        blocked = integrations.unsupported_host_warning(appdef)
        override = False
        if blocked is not None:
            override = bool(messagebox.askokcancel(
                f"{appdef.name} — unsupported host version",
                blocked + "\n\nSet up against it anyway?",
                icon=messagebox.WARNING, default=messagebox.CANCEL, parent=parent))
            if not override:
                return
        if appdef.security_confirmation:
            proceed = messagebox.askokcancel(
                f"{appdef.name} — before setup",
                appdef.security_confirmation + "\n\nContinue?",
                parent=parent)
            if not proceed:
                return
        if appdef.key == "blender":
            result = self._install_blender_interactive(allow_unsupported_host=override)
        else:
            result = integrations.install(appdef, self.cfg, allow_unsupported_host=override)
        ok, msg, copyables = integrations.normalize_install_result(result)
        if ok:
            enabled_var.set(bool(self.cfg.snapshot().app_operational[appdef.key]["enabled"]))
            try:
                status_label.config(text=self._app_status_text(appdef))
                action = self._app_button_text(appdef)
                if action:
                    holder["btn"].config(text=action)
                    if not holder["btn"].winfo_manager():
                        holder["btn"].pack(side="right")
                else:
                    holder["btn"].pack_forget()
            except tk.TclError:
                pass
            if appdef.key == "onshape":
                self._show_onshape_userscript_dialog(
                    title="Onshape — Set up",
                    lead=msg,
                )
            else:
                self._show_integration_dialog("Integration", msg, copyables, warning=False)
        else:
            self._show_integration_dialog("Integration", msg, copyables, warning=True)

    def _install_blender_interactive(self, *, allow_unsupported_host=False):
        """Blender setup, asking before writing the auto-start shim (it makes our code run on every
        Blender launch). Yes = install + auto-enable; No = install only; Cancel = abort."""
        appdef = integrations.APPS_BY_KEY["blender"]
        if not appdef.detect():
            return False, "Blender was not found on this machine — install it first."
        want = messagebox.askyesnocancel(
            "Blender — auto-start?",
            "Install the Trackball add-on into Blender's add-ons folder (every detected version).\n\n"
            "Auto-enable it on every Blender launch? This also writes a small startup script\n"
            "(scripts/startup/trackball_nav_startup.py) — the analogue of Fusion's \"Run on Startup\".\n\n"
            "• Yes — install + auto-enable on launch (recommended)\n"
            "• No — install only; you enable it once in Preferences → Add-ons\n"
            "• Cancel — don't install",
            parent=self.win)
        if want is None:
            return False, "Blender setup cancelled."
        # Through `install` rather than `install_blender`, so this path passes the same host gate as
        # every other integration instead of being the one that skips it.
        return integrations.install(appdef, self.cfg, allow_unsupported_host=allow_unsupported_host,
                                    install_startup=bool(want))

    # --- tab b: per-app bindings --------------------------------------------------
    def _build_bindings_tab(self, nb):
        outer = ttk.Frame(nb)
        top = ttk.Frame(outer)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="Editing app:", width=24, anchor="w").pack(side="left")

        app_keys = [a.key for a in integrations.APPS]
        self._edit_app = tk.StringVar(value=self.cfg.snapshot().selected_app)
        combo = ttk.Combobox(top, textvariable=self._edit_app, values=app_keys,
                             state="readonly", width=18)
        combo.pack(side="left")

        holder = ttk.Frame(outer)            # the scrollable body is rebuilt inside here on app change

        def rebuild(*_):
            for w in holder.winfo_children():
                w.destroy()
            self._set_and_save(("active_app",), self._edit_app.get())   # selection = active
            self._bindings_fields(self._scrollable(holder), self._edit_app.get())

        profile_actions = ttk.Frame(outer)
        profile_actions.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Label(profile_actions, text="Profile settings:", width=24, anchor="w").pack(side="left")

        def reset_current_profile():
            key = self._edit_app.get()
            name = integrations.APPS_BY_KEY[key].name
            if not messagebox.askyesno(
                    "Reset profile?",
                    f"Reset every {name} user-facing navigation setting to its clean default?\n\n"
                    "Enable/install state and installed add-in version will be preserved.",
                    parent=self.win):
                return
            self.cfg.reset_app_profile(key)
            for w in holder.winfo_children():
                w.destroy()
            self._bindings_fields(self._scrollable(holder), key)

        ttk.Button(profile_actions, text="Reset to defaults",
                   command=reset_current_profile).pack(side="left", padx=(6, 0))

        holder.pack(fill="both", expand=True)

        combo.bind("<<ComboboxSelected>>", rebuild)
        self._bindings_fields(self._scrollable(holder), self._edit_app.get())
        return outer

    def _scrollable(self, parent):
        """A vertically scrollable inner frame inside `parent` (the Blender section is tall)."""
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def _wheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except tk.TclError:
                pass
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    # --- declarative binding renderer --------------------------------------------
    # All apps pull presentation order from settings_schema and capabilities from app_registry.
    def _bindings_fields(self, parent, app_key):
        profile = binding_profile(app_key)
        controls = {}
        self._refresh_twist_warning = None
        ttk.Label(parent, text=profile.title, foreground="#555").pack(
            anchor="w", padx=10, pady=(8, 2))
        for section in BINDING_SECTIONS:
            fields = [field for field in section.fields if profile.supports(field)]
            if not fields:
                continue
            box = ttk.LabelFrame(parent, text=section.title)
            box.pack(fill="x", padx=10, pady=6)
            for field in fields:
                self._render_binding_field(box, app_key, profile, field, controls)

    def _render_binding_field(self, parent, app_key, profile, field, controls=None):
        controls = controls if controls is not None else {}
        base = ("apps", app_key, "bindings")
        adv = ("apps", app_key, "advanced")
        if field == "rate":
            self._rate_combo(parent, "Viewport refresh rate (Hz)", ("apps", app_key, "rate_hz"),
                             hint="Default follows the General update rate. Higher values are smoother; "
                                  "lower values reduce load in heavy scenes.")
        elif field == "orbit_sensitivity":
            self._entry_row(parent, "Orbit sensitivity", base + ("orbit", "sensitivity"),
                            hint="Multiplier for orbit rotation. 1.0 uses the app's aligned baseline.")
        elif field == "pan_gain":
            self._entry_row(parent, "Pan gain", base + ("pan", "gain"),
                            hint="Multiplier for shifted horizontal/vertical movement.")
        elif field == "zoom_gain":
            self._entry_row(parent, "Zoom gain", base + ("zoom", "gain"),
                            hint="Multiplier for shifted twist zoom or dolly.")
        elif field == "zoom_dominance":
            self._entry_row(parent, "Zoom dominance", base + ("zoom", "dominance"),
                            hint="How strongly twist must dominate planar movement before it becomes zoom.")
        elif field == "toggle":
            self._combo_row(parent, "Orbit / pan-zoom toggle", base + ("toggle",),
                            values=["shift", "none"],
                            hint="Shift uses normal motion for orbit and shifted motion for pan/zoom. "
                                 "None keeps the profile in orbit routing.")
        elif field == "nav_mode":
            self._combo_row(
                parent, "Mode", adv + ("nav_mode",),
                values=list(APP_SPECS_BY_ID[app_key].supported_modes),
                hint=("Orbit rotates around a pivot; Fly is free 6DOF; Walk keeps a fixed "
                      "horizon; Object transforms the current selection."))
        elif field == "fly_speed":
            self._entry_row(parent, "Fly speed", adv + ("fly_speed",),
                            hint="Movement multiplier while Mode is Fly.")
        elif field == "walk_speed":
            self._entry_row(parent, "Walk speed", adv + ("walk_speed",),
                            hint="Movement multiplier while Mode is Walk.")
        elif field == "orbit_style":
            controls["orbit_style"] = self._mapped_combo_row(
                parent, "Orbit style", base + ("scheme", "orbit_style"),
                [(_option_label(v), v) for v in profile.orbit_styles],
                hint="Default follows General. Free permits roll; Turntable keeps a fixed horizon.")
        elif field == "orbit_pivot":
            options = [("Default", "default")]
            options.extend([(_PIVOT_LABELS[value], value) for value in profile.pivots])
            self._mapped_combo_row(
                parent, "Orbit pivot", base + ("scheme", "orbit_pivot"), options,
                hint="Point the camera rotates around. Unsupported pivots are absent for this app.",
                on_change=(self._warn_onshape_cursor_userscript_if_needed
                           if app_key == "onshape" else None))
        elif field == "twist_action":
            def add_roll_warning(frame, _var):
                warning = tk.Canvas(frame, width=18, height=18, highlightthickness=0, bd=0,
                                    cursor="question_arrow")
                warning.create_oval(2, 2, 16, 16, fill="#c62828", outline="#c62828")
                warning.create_text(9, 9, text="!", fill="white", font=("TkDefaultFont", 9, "bold"))
                self._tooltip(warning, 'Switch to "roll" for 3-axis orbit')
                controls["twist_warning"] = warning

                def refresh_warning():
                    try:
                        app_style = self._get(base + ("scheme", "orbit_style"))
                        general_style = self._get(("general", "scheme", "orbit_style"))
                        twist_action = self._get(adv + ("twist_action",))
                        show = _free_orbit_needs_roll_warning(
                            app_style, general_style, twist_action,
                            supports_roll="roll" in profile.twist_actions)
                        if show and not warning.winfo_manager():
                            warning.pack(side="left", padx=(6, 0))
                        elif not show and warning.winfo_manager():
                            warning.pack_forget()
                    except tk.TclError:
                        pass

                self._refresh_twist_warning = refresh_warning
                refresh_warning()

            controls["twist_action"] = self._mapped_combo_row(
                parent, "Twist action", adv + ("twist_action",),
                [(_option_label(v), v) for v in profile.twist_actions],
                hint="Action driven by unshifted twist in Orbit mode, including Turntable.",
                trailing_factory=add_roll_warning)
        elif field == "lock_horizon":
            self._bool_row(parent, "Lock horizon", adv + ("lock_horizon",),
                           hint="Keep the horizon fixed even when Orbit style is Free.")
        elif field == "level_horizon":
            self._level_horizon_row(parent, app_key)
        elif field == "selection_override":
            self._bool_row(parent, "Selection overrides orbit center",
                           ("apps", app_key, "selection_overrides_pivot"),
                           hint="When a selection exists, use its center before the configured pivot. "
                                "Camera remains turn-in-place.")
        elif field == "zoom_target":
            self._mapped_combo_row(parent, "Zoom mode", base + ("scheme", "zoom_mode"),
                                   [(_option_label(v), v) for v in profile.zoom_targets],
                                   hint="Default follows General. To Cursor uses the surface under the mouse.")
        elif field == "zoom_behavior":
            self._mapped_combo_row(parent, "Pan-mode zoom", adv + ("zoom_style",),
                                   [(_option_label(v), v) for v in profile.zoom_behaviors],
                                   hint="Zoom changes view scale or lens. Dolly moves the camera. "
                                        "This applies to twist while the pan/zoom route is active.")
        elif field == "pan_scales":
            self._bool_row(parent, "Pan scales with view distance",
                           adv + ("pan_scales_with_distance",),
                           hint="Scale movement with camera distance for consistent on-screen travel.")
        elif field == "orbit_hold":
            self._entry_row(parent, "Pivot hold (s)",
                            ("apps", app_key, "orbit_pivot_hold_sec"),
                            hint="Idle gap that ends an orbit gesture. Pan immediately clears the "
                                 "orbit pivot so the next orbit resolves a new target.")
        elif field == "zoom_hold":
            self._entry_row(parent, "Zoom hold (s)",
                            ("apps", app_key, "zoom_cursor_hold_sec"),
                            hint="Idle gap before To Cursor zoom resolves a new target. Pan retains "
                                 "this target. To Center and To Object do not use this setting.")
        elif field == "dynamic_clip":
            self._bool_row(parent, "Override Unity Dynamic Clipping",
                           adv + ("override_dynamic_clip",),
                           hint="Disable Unity's automatic near/far-plane fitting while navigating.")
        elif field == "pivot_extent":
            self._entry_row(parent, "Pivot extent limit ×", adv + ("pivot_extent_mult",),
                            hint="Maximum raycast-pivot distance as a multiple of scene bounds.")
        elif field == "camera_lock":
            self._bool_row(parent, "Lock camera to view", adv + ("lock_camera_to_view",),
                           hint="When viewing through the Blender scene camera, drive that camera directly.")
        elif field == "action_routing":
            if profile.rich_actions:
                self._rich_action_routing(parent, adv, profile.no_roll)
            else:
                self._binding_axis_rows(parent, base)
        elif field == "onshape_userscript":
            button = ttk.Button(parent, text="Copy Under Cursor userscript…",
                                command=lambda: self._show_onshape_userscript_dialog(
                                    title="Onshape — copy userscript", copy_on_open=True))
            button.pack(anchor="w", padx=12, pady=6)
            self._tooltip(button, "Required only for the Under Cursor orbit pivot in Onshape.")

    def _rich_action_routing(self, parent, adv, no_roll):
        self._action_routing_group(parent, "Orbit", adv,
                                   [("Pitch", "pitch"), ("Yaw", "yaw"), ("Twist", "twist"),
                                    ("Pan X", "pan_x"), ("Pan Y", "pan_y"), ("Zoom", "zoom")])
        camera = [("Pitch", "pitch"), ("Yaw", "yaw")]
        fly = [("Pitch", "pitch"), ("Yaw", "yaw")]
        if not no_roll:
            camera.append(("Roll", "roll"))
            fly.append(("Bank", "bank"))
        fly.extend([("Forward", "forward"), ("Strafe", "strafe"), ("Up/Down", "vertical")])
        self._action_routing_group(parent, "Camera", adv, camera)
        self._action_routing_group(parent, "Fly", adv, fly)
        self._action_routing_group(parent, "Walk", adv,
                                   [("Pitch", "pitch"), ("Yaw", "yaw"),
                                    ("Forward", "forward"), ("Strafe", "strafe"),
                                    ("Up/Down", "vertical")])

    def _binding_axis_rows(self, parent, base):
        rows = [
            ("Orbit X", base + ("orbit", "axis_source", 0), base + ("invert", "orbit", 0)),
            ("Orbit Y", base + ("orbit", "axis_source", 1), base + ("invert", "orbit", 1)),
            ("Orbit Z", base + ("orbit", "axis_source", 2), base + ("invert", "orbit", 2)),
            ("Pan X", base + ("pan", "x_src"), base + ("invert", "pan", 0)),
            ("Pan Y", base + ("pan", "y_src"), base + ("invert", "pan", 1)),
            ("Zoom", base + ("zoom", "src"), base + ("invert", "zoom")),
        ]
        for label, source, invert in rows:
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=18, pady=1)
            ttk.Label(row, text=label, width=12, anchor="w").pack(side="left")
            ttk.Label(row, text="Source").pack(side="left")
            self._axis_combo(row, source)
            self._invert_check(row, "Invert", invert)

    def _action_routing_group(self, parent, label, advanced_keys, items):
        axis = advanced_keys + ("axis_source", label.lower())
        invert = advanced_keys + ("invert", label.lower())
        for index, (text, key) in enumerate(items):
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=12, pady=1)
            ttk.Label(row, text=label if index == 0 else "", width=10, anchor="w").pack(side="left")
            ttk.Label(row, text=text, width=10, anchor="w").pack(side="left")
            ttk.Label(row, text="Source").pack(side="left")
            self._axis_combo(row, axis + (key,))
            self._invert_check(row, "Invert", invert + (key,))

    # --- tab c: general bindings --------------------------------------------------
    def _build_general_tab(self, nb):
        outer = ttk.Frame(nb)
        body = self._scrollable(outer)

        sec1 = ttk.LabelFrame(body, text="Device")
        sec1.pack(fill="x", padx=10, pady=(10, 6))
        self._entry_row(sec1, "Name", ("device", "name"), cast=str, width=22,
                        hint="applies on reconnect")
        self._entry_row(sec1, "Address (optional)", ("device", "address"), cast=str, width=22,
                        hint="AA:BB:..  applies on reconnect")

        secA = ttk.LabelFrame(body, text="Physical trackball orientation")
        secA.pack(fill="x", padx=10, pady=6)
        self._global_axis_orientation_editor(secA)

        secB = ttk.LabelFrame(body, text="3D navigation bridge")
        secB.pack(fill="x", padx=10, pady=6)
        self._entry_row(secB, "Default update rate (Hz)", ("bridge", "rate_hz"), cast=int,
                        hint="global default; override per app in Per-App Bindings")

        secS = ttk.LabelFrame(body, text="3D control scheme (defaults)")
        secS.pack(fill="x", padx=10, pady=6)
        self._mapped_combo_row(secS, "Orbit pivot", ("general", "scheme", "orbit_pivot"),
                               [("Camera", "camera"), ("Screen Center", "screen_center"),
                                ("Under Cursor (mouse)", "cursor"), ("Selection", "selection"),
                                ("3D Cursor", "cursor_3d"), ("Model Center", "object"),
                                ("World Origin", "origin")])
        self._mapped_combo_row(secS, "Orbit style", ("general", "scheme", "orbit_style"),
                               [("Free", "free"), ("Turntable", "turntable")])
        self._bool_row(secS, "Level horizon when entering Turntable/Walk",
                       ("general", "level_horizon_on_entry"),
                       hint="Remove existing roll once when entering Turntable, Lock Horizon, or "
                            "Walk. Per-app checkboxes can override this default.")
        self._mapped_combo_row(secS, "Zoom mode", ("general", "scheme", "zoom_mode"),
                               [("To Center", "to_center"), ("To Object", "to_object"),
                                ("To Cursor", "to_cursor")],
                               hint="Default target used by apps whose Zoom mode follows General.")
        self._orbit_fallback_editor(secS)

        sec2 = ttk.LabelFrame(body, text="Pointer / scroll (cursor mode)")
        sec2.pack(fill="x", padx=10, pady=6)
        self._entry_row(sec2, "Cursor sensitivity", ("general", "cursor", "gain"),
                        hint="radians → pixels")
        self._entry_row(sec2, "Scroll gain", ("general", "scroll", "gain"))
        self._entry_row(sec2, "Scroll deadzone", ("general", "scroll", "deadzone"))
        self._entry_row(sec2, "Scroll dominance", ("general", "scroll", "dominance"))

        sec3 = ttk.LabelFrame(body, text="Mode")
        sec3.pack(fill="x", padx=10, pady=6)
        self._combo_row(sec3, "Default mode", ("general", "default_mode"),
                        values=["3d", "pointer"], on_change=self._apply_default_mode)
        return outer

    def _apply_default_mode(self):
        # ConfigStore's listener refreshes the context-aware runtime base through the command
        # queue. A Global default edit must not create a latched runtime override.
        return None
