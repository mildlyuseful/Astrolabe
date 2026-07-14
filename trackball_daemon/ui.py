"""Tkinter settings window.

Lives as a Toplevel under a hidden Tk root. Closing the window HIDES it (withdraw) so the
app keeps running in the tray; the app exits only via tray -> Quit. Every edit writes to
the config store immediately (typed transaction -> listeners -> OutputEngine.apply_config), so
changes persist and apply live.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from . import integrations
from .app_registry import binding_profile
from .settings_schema import BINDING_SECTIONS
from .config import (ORBIT_PIVOT_METHODS,
                     normalize_axis_permutation, normalize_orbit_pivot_fallbacks,
                     swap_axis_source)

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
    "orbit": "Orbit", "fly": "Fly", "walk": "Walk",
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
        self._app_status_labels = {}      # key -> ttk.Label (3D Apps tab)
        self._app_action_buttons = {}     # key -> setup/update button
        self._refresh_twist_warning = None

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

    def update_app_connections(self, infos):
        """Reflect live add-on handshakes in the 3D-Apps rows (called from the Tk thread)."""
        if not self._app_status_labels:
            return
        connected = {a: v for a, v, _ in infos}
        for key, lbl in self._app_status_labels.items():
            try:
                if not lbl.winfo_exists():
                    continue
                if key in connected:
                    version = connected[key]
                    stale = (key in integrations.ADDIN_KEYS and
                             integrations.update_available(key, installed_version=version))
                    suffix = "  •  update available" if stale else ""
                    lbl.config(text=f"active • connected (v{version}){suffix}",
                               foreground="#1a7f37")
                else:
                    appdef = integrations.APPS_BY_KEY.get(key)
                    if appdef is not None:
                        lbl.config(text=self._app_status_text(appdef), foreground="#666")
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

    # --- build --------------------------------------------------------------------
    def _build(self):
        self.win = tk.Toplevel(self.root)
        self.win.title("Trackball Daemon — Settings")
        self.win.geometry("660x600")
        self.win.minsize(560, 480)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        nb.add(self._build_apps_tab(nb), text="3D Apps")
        nb.add(self._build_bindings_tab(nb), text="Per-App Bindings")
        nb.add(self._build_general_tab(nb), text="General")

        self.status_var = tk.StringVar(value=f"Connection: {self.app.status_text()}")
        ttk.Separator(self.win).pack(fill="x")
        ttk.Label(self.win, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=10, pady=4)

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
    # "Default" stores 0 (use the global bridge rate); any number is clamped to 1..240 Hz.
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
        ttk.Label(outer, text="Supported 3D apps — enable integrations, check host compatibility, "
                              "and expand honest setup/manual-install instructions.",
                  wraplength=600, foreground="#555").pack(anchor="w", padx=10, pady=(10, 6))
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        body = self._scrollable(holder)
        for appdef in integrations.APPS:
            self._app_row(body, appdef)
        return outer

    def _app_status_text(self, appdef):
        base = integrations.status_line(appdef)
        observed = getattr(self.app, "observed_addin_versions", {}).get(appdef.key)
        stale = (integrations.update_available(appdef.key, installed_version=observed)
                 if observed is not None else integrations.update_available(appdef.key))
        if appdef.key in integrations.ADDIN_KEYS and stale:
            return base + "  •  update available"
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
        result = integrations.compatibility(appdef)
        text = f"Supported versions: {appdef.supported_versions}"
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
        if appdef.security_confirmation:
            proceed = messagebox.askokcancel(
                f"{appdef.name} — before setup",
                appdef.security_confirmation + "\n\nContinue?",
                parent=self.win if self.win is not None else self.root)
            if not proceed:
                return
        if appdef.key == "blender":
            result = self._install_blender_interactive()
        else:
            result = integrations.install(appdef, self.cfg)
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

    def _install_blender_interactive(self):
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
        return integrations.install_blender(appdef, self.cfg, install_startup=bool(want))

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
            self._combo_row(parent, "Mode", adv + ("nav_mode",), values=["orbit", "fly", "walk"],
                            hint="Orbit rotates around a pivot; Fly is free 6DOF; Walk keeps a fixed horizon.")
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
        mode = self.cfg.snapshot().global_value("input.mode.default")
        self.app.engine.set_mode(self.app.engine.MODE_CUBE if mode == "3d"
                                 else self.app.engine.MODE_CURSOR)
