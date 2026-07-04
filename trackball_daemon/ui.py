"""Tkinter settings window.

Lives as a Toplevel under a hidden Tk root. Closing the window HIDES it (withdraw) so the
app keeps running in the tray; the app exits only via tray -> Quit. Every edit writes to
the config store immediately (Config.save -> listeners -> OutputEngine.apply_config), so
changes persist and apply live.
"""
import tkinter as tk
from tkinter import ttk, messagebox

from . import integrations

_PAD = {"padx": 8, "pady": 4}


class SettingsWindow:
    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.cfg = app.config
        self.win = None
        self.status_var = None
        self._app_status_labels = {}      # key -> ttk.Label (3D Apps tab)

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
                    lbl.config(text=f"active • connected (v{connected[key]})", foreground="#1a7f37")
                else:
                    appdef = integrations.APPS_BY_KEY.get(key)
                    if appdef is not None:
                        lbl.config(text=integrations.status_line(appdef), foreground="#666")
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
        node = self.cfg.data
        for k in keys:
            node = node[k]
        return node

    def _set_and_save(self, keys, value):
        node = self.cfg.data
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
        self.cfg.save()                  # -> notifies listeners -> engine.apply_config()

    def _entry_row(self, parent, label, keys, cast=float, width=12, hint=""):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side="left")
        var = tk.StringVar(value=self._fmt(self._get(keys)))
        entry = ttk.Entry(frame, textvariable=var, width=width)
        entry.pack(side="left")
        if hint:
            ttk.Label(frame, text=hint, foreground="#777").pack(side="left", padx=6)

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

    def _combo_row(self, parent, label, keys, values, cast=str, on_change=None):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self._get(keys)))
        combo = ttk.Combobox(frame, textvariable=var, values=values,
                             state="readonly", width=18)
        combo.pack(side="left")

        def on_sel(_):
            self._set_and_save(keys, cast(var.get()))     # save first...
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
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side="left")
        var = tk.StringVar(value=self._rate_text(self._get(keys)))
        combo = ttk.Combobox(frame, textvariable=var, values=self._RATE_PRESETS, width=10)
        combo.pack(side="left")
        if hint:
            ttk.Label(frame, text=hint, foreground="#777").pack(side="left", padx=6)

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

    def _bool_row(self, parent, label, keys):
        """Full-width checkbox bound to a boolean config key (saves + applies live on toggle)."""
        var = tk.BooleanVar(value=bool(self._get(keys)))
        ttk.Checkbutton(parent, text=label, variable=var,
                        command=lambda: self._set_and_save(keys, bool(var.get()))).pack(
            anchor="w", padx=12, pady=2)
        return var

    def _mapped_combo_row(self, parent, label, keys, options, hint=""):
        """Readonly combo whose display labels differ from the stored values. `options` is a list of
        (display, value) pairs -- used for the Blender Advanced combos that relabel the generic
        scheme with Blender terms."""
        frame = ttk.Frame(parent)
        frame.pack(fill="x", **_PAD)
        ttk.Label(frame, text=label, width=24, anchor="w").pack(side="left")
        disp_by_val = {v: d for d, v in options}
        val_by_disp = {d: v for d, v in options}
        cur = self._get(keys)
        var = tk.StringVar(value=disp_by_val.get(cur, options[0][0]))
        combo = ttk.Combobox(frame, textvariable=var, values=[d for d, _ in options],
                             state="readonly", width=22)
        combo.pack(side="left")
        if hint:
            ttk.Label(frame, text=hint, foreground="#777").pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._set_and_save(keys, val_by_disp[var.get()]))
        return var

    @staticmethod
    def _fmt(v):
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    # --- tab a: 3D app integrations -----------------------------------------------
    def _build_apps_tab(self, nb):
        outer = ttk.Frame(nb)
        ttk.Label(outer, text="Supported 3D apps — enable, set up the integration, and "
                              "choose what starts automatically.",
                  wraplength=600, foreground="#555").pack(anchor="w", padx=10, pady=(10, 6))
        for appdef in integrations.APPS:
            self._app_row(outer, appdef)
        return outer

    def _app_status_text(self, appdef):
        base = integrations.status_line(appdef)
        if appdef.key in integrations.ADDIN_KEYS and integrations.update_available(appdef.key):
            return base + "  •  update available"
        return base

    def _app_button_text(self, appdef):
        key = appdef.key
        if key in integrations.ADDIN_KEYS:                 # socket add-in apps (e.g. Fusion)
            if integrations.installed_addin_version(key):
                if integrations.update_available(key):
                    return f"Update → v{integrations.bundled_addin_version(key)}"
                return "Reinstall"
            return "Set up"
        if appdef.setup is not None:                       # no-add-in setup (SolidWorks COM, Onshape bridge)
            return "Re-check" if self.cfg.data["apps"][key].get("installed") else "Enable"
        return "Set up" if appdef.needs_plugin else "Enable profile"

    def _app_row(self, parent, appdef):
        a = self.cfg.data["apps"][appdef.key]
        card = ttk.LabelFrame(parent, text=appdef.name)
        card.pack(fill="x", padx=10, pady=5)

        status = ttk.Label(card, text=self._app_status_text(appdef), foreground="#666")
        status.pack(anchor="w", padx=8, pady=(4, 0))
        self._app_status_labels[appdef.key] = status

        controls = ttk.Frame(card)
        controls.pack(fill="x", padx=8, pady=6)

        enabled = tk.BooleanVar(value=a["enabled"])
        ttk.Checkbutton(controls, text="Enabled", variable=enabled,
                        command=lambda: self._set_and_save(("apps", appdef.key, "enabled"),
                                                           bool(enabled.get()))).pack(side="left")

        auto = tk.BooleanVar(value=a["start_automatically"])
        ttk.Checkbutton(controls, text="Start automatically", variable=auto,
                        command=lambda: self._set_and_save(
                            ("apps", appdef.key, "start_automatically"),
                            bool(auto.get()))).pack(side="left", padx=12)

        holder = {}
        btn = ttk.Button(controls, text=self._app_button_text(appdef),
                         command=lambda: self._do_install(appdef, enabled, status, holder))
        holder["btn"] = btn
        btn.pack(side="right")

    def _do_install(self, appdef, enabled_var, status_label, holder):
        if appdef.key == "blender":
            ok, msg = self._install_blender_interactive()
        else:
            ok, msg = integrations.install(appdef, self.cfg)
        if ok:
            enabled_var.set(bool(self.cfg.data["apps"][appdef.key]["enabled"]))
            try:
                status_label.config(text=self._app_status_text(appdef))
                holder["btn"].config(text=self._app_button_text(appdef))
            except tk.TclError:
                pass
            messagebox.showinfo("Integration", msg, parent=self.win)
        else:
            messagebox.showwarning("Integration", msg, parent=self.win)

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
        self._edit_app = tk.StringVar(value=self.cfg.data["active_app"])
        combo = ttk.Combobox(top, textvariable=self._edit_app, values=app_keys,
                             state="readonly", width=18)
        combo.pack(side="left")
        ttk.Label(top, text="(also the active profile the daemon drives)",
                  foreground="#777").pack(side="left", padx=6)

        holder = ttk.Frame(outer)            # the scrollable body is rebuilt inside here on app change
        holder.pack(fill="both", expand=True)

        def rebuild(*_):
            for w in holder.winfo_children():
                w.destroy()
            self._set_and_save(("active_app",), self._edit_app.get())   # selection = active
            self._bindings_fields(self._scrollable(holder), self._edit_app.get())

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

    def _bindings_fields(self, parent, app_key):
        if app_key == "blender":             # Blender has its own richer, merged layout (below)
            self._blender_bindings_fields(parent)
            return
        if app_key == "sketchup":            # SketchUp has viewpoint/fly/walk + per-mode inverts
            self._sketchup_bindings_fields(parent)
            return
        if app_key == "unreal":              # Unreal has a Blender-style richer layout too
            self._unreal_bindings_fields(parent)
            return
        base = ("apps", app_key, "bindings")
        ttk.Label(parent, text="Navigation mapping — how trackball motion drives this app.",
                  foreground="#555", wraplength=600).pack(anchor="w", padx=10, pady=(8, 2))

        self._rate_combo(parent, "Viewport refresh rate (Hz)", ("apps", app_key, "rate_hz"),
                         hint="per-app; Default = global. Higher = smoother (try 60)")
        self._entry_row(parent, "Orbit sensitivity", base + ("orbit", "sensitivity"),
                        hint="1.0 = true 1:1")
        self._entry_row(parent, "Pan gain", base + ("pan", "gain"),
                        hint="radians → world units")
        self._entry_row(parent, "Zoom gain", base + ("zoom", "gain"),
                        hint="radians → dolly")
        self._entry_row(parent, "Zoom dominance", base + ("zoom", "dominance"),
                        hint="twist vs pan-plane")
        self._combo_row(parent, "Orbit ↔ pan/zoom toggle", base + ("toggle",),
                        values=["shift", "none"])

        ttk.Label(parent, text="Invert axes (per this app):", foreground="#555").pack(
            anchor="w", padx=10, pady=(10, 0))
        r1 = ttk.Frame(parent); r1.pack(fill="x", padx=18, pady=2)
        ttk.Label(r1, text="Orbit", width=7, anchor="w").pack(side="left")
        self._invert_check(r1, "X", base + ("invert", "orbit", 0))
        self._invert_check(r1, "Y", base + ("invert", "orbit", 1))
        self._invert_check(r1, "Z", base + ("invert", "orbit", 2))
        r2 = ttk.Frame(parent); r2.pack(fill="x", padx=18, pady=2)
        ttk.Label(r2, text="Pan", width=7, anchor="w").pack(side="left")
        self._invert_check(r2, "X", base + ("invert", "pan", 0))
        self._invert_check(r2, "Y", base + ("invert", "pan", 1))
        ttk.Label(r2, text="     ").pack(side="left")
        self._invert_check(r2, "Zoom", base + ("invert", "zoom"))

        ttk.Label(parent, text="Control scheme (Default = use the General default):",
                  foreground="#555").pack(anchor="w", padx=10, pady=(10, 0))
        # DISPLAY labels vs STORED values (user feedback: call the under-mouse pivot "cursor", not
        # "pointer" -- and never show both words at once). The under-mouse pivot is SHOWN as
        # "cursor" but STORED as "pointer"/"to_pointer" (the internal value predates the rename;
        # every plugin + broker frame speaks it -- do NOT rename the stored value). The LEGACY
        # stored value "cursor" (selection / 3D-cursor fallback) is shown as "selection"; legacy
        # "to_cursor" (a to_center alias in every app) is no longer offered -- a config that still
        # stores one keeps working (the combo just shows the first entry until changed).
        self._mapped_combo_row(parent, "Orbit pivot", base + ("scheme", "orbit_pivot"),
                               [("default", "default"), ("view", "view"),
                                ("cursor (under mouse)", "pointer"), ("object", "object"),
                                ("origin", "origin"), ("selection", "cursor")])
        self._combo_row(parent, "Orbit style", base + ("scheme", "orbit_style"),
                        values=["default", "free", "turntable"])
        self._mapped_combo_row(parent, "Zoom mode", base + ("scheme", "zoom_mode"),
                               [("default", "default"), ("to_center", "to_center"),
                                ("to_object", "to_object"), ("to_cursor (under mouse)", "to_pointer")])
        self._entry_row(parent, "View-pivot hold (s)", ("apps", app_key, "view_pivot_hold_sec"),
                        hint="'view' pivot only (SolidWorks): seconds still before it re-raycasts the "
                             "surface under the centre")

        ttk.Label(parent,
                  text="Which received axis feeds orbit/pan/zoom lives in the config file; "
                       "edits here apply live.",
                  foreground="#888", wraplength=600).pack(anchor="w", padx=10, pady=(10, 2))

    def _invert_row(self, parent, label, base_keys, items):
        """A labelled row of inline invert checkboxes. `items` = [(text, key), ...] under base_keys."""
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=12, pady=2)
        ttk.Label(row, text=label, width=10, anchor="w").pack(side="left")
        for text, key in items:
            self._invert_check(row, text, base_keys + (key,))

    def _blender_bindings_fields(self, parent):
        """The full Blender control set in one place (merged from the former 'Blender Advanced' tab).
        Sensitivities/toggle/rate write apps.blender.bindings; mode/orbit/pan-zoom/camera write
        apps.blender.advanced; per-mode direction flips write advanced.invert. All apply live."""
        base = ("apps", "blender", "bindings")
        adv = ("apps", "blender", "advanced")
        inv = adv + ("invert",)
        ttk.Label(parent, text="Blender navigation — every option in one place. Applies live to a "
                              "focused Blender viewport.", foreground="#555", wraplength=600).pack(
            anchor="w", padx=10, pady=(8, 2))

        s0 = ttk.LabelFrame(parent, text="Sensitivity & rate")
        s0.pack(fill="x", padx=10, pady=6)
        self._rate_combo(s0, "Viewport refresh rate (Hz)", ("apps", "blender", "rate_hz"),
                         hint="Default = global; try 60")
        self._entry_row(s0, "Orbit sensitivity", base + ("orbit", "sensitivity"), hint="1.0 = true 1:1")
        self._entry_row(s0, "Pan gain", base + ("pan", "gain"))
        self._entry_row(s0, "Zoom gain", base + ("zoom", "gain"))
        self._entry_row(s0, "Zoom dominance", base + ("zoom", "dominance"), hint="twist vs pan-plane")
        self._combo_row(s0, "Orbit ↔ pan/zoom toggle", base + ("toggle",), values=["shift", "none"])

        s1 = ttk.LabelFrame(parent, text="Navigation mode")
        s1.pack(fill="x", padx=10, pady=6)
        self._combo_row(s1, "Mode", adv + ("nav_mode",), values=["orbit", "fly", "walk"])
        self._entry_row(s1, "Fly speed", adv + ("fly_speed",))
        self._entry_row(s1, "Walk speed", adv + ("walk_speed",))
        ttk.Label(s1, text="In Blender, Alt+` (or View ▸ Trackball: Cycle Nav Mode) toggles "
                           "orbit/fly/walk too.", foreground="#888", wraplength=560).pack(
            anchor="w", padx=10, pady=(0, 4))

        s2 = ttk.LabelFrame(parent, text="Orbit")
        s2.pack(fill="x", padx=10, pady=6)
        self._mapped_combo_row(s2, "Orbit method", base + ("scheme", "orbit_style"),
                               [("Default (General)", "default"), ("Trackball (free)", "free"),
                                ("Turntable", "turntable")])
        self._mapped_combo_row(s2, "Orbit around", base + ("scheme", "orbit_pivot"),
                               [("Default (General)", "default"), ("Viewpoint", "viewpoint"),
                                ("Auto Depth (surface)", "view"), ("Under Cursor (mouse)", "pointer"),
                                ("Selection", "object"),
                                ("3D Cursor", "cursor"), ("World Origin", "origin")])
        self._combo_row(s2, "Twist action", adv + ("twist_action",),
                        values=["roll", "zoom", "dolly", "none"])
        self._bool_row(s2, "Lock horizon (keep level even in trackball)", adv + ("lock_horizon",))

        s3 = ttk.LabelFrame(parent, text="Pan / Zoom")
        s3.pack(fill="x", padx=10, pady=6)
        self._combo_row(s3, "Zoom style", adv + ("zoom_style",), values=["zoom", "dolly"])
        self._bool_row(s3, "Zoom to mouse (screen-centre surface)", adv + ("zoom_to_mouse",))
        self._bool_row(s3, "Pan scales with view distance", adv + ("pan_scales_with_distance",))
        self._entry_row(s3, "Auto-depth hold (s)", ("apps", "blender", "view_pivot_hold_sec"),
                        hint="'Auto Depth' pivot: seconds still before it re-raycasts")

        s4 = ttk.LabelFrame(parent, text="Camera view")
        s4.pack(fill="x", padx=10, pady=6)
        self._bool_row(s4, "Lock camera to view (drive the scene camera)", adv + ("lock_camera_to_view",))

        s5 = ttk.LabelFrame(parent, text="Invert directions — independent per mode")
        s5.pack(fill="x", padx=10, pady=6)
        self._invert_row(s5, "Orbit", inv + ("orbit",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Twist", "twist"),
                          ("Pan X", "pan_x"), ("Pan Y", "pan_y"), ("Zoom", "zoom")])
        self._invert_row(s5, "Viewpoint", inv + ("viewpoint",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Roll", "roll")])
        self._invert_row(s5, "Fly", inv + ("fly",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Bank", "bank"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        self._invert_row(s5, "Walk", inv + ("walk",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        ttk.Label(s5, text="Each mode's directions are independent — e.g. flip Walk ▸ Fwd without "
                           "touching Orbit. \"Viewpoint\" = orbit-around Viewpoint (turn in place); it "
                           "shares Orbit's pan/zoom inverts.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(2, 4))

    def _sketchup_bindings_fields(self, parent):
        """SketchUp's Blender-parity camera controls, minus Blender's 3D-cursor/camera-view options."""
        base = ("apps", "sketchup", "bindings")
        adv = ("apps", "sketchup", "advanced")
        inv = adv + ("invert",)
        ttk.Label(parent, text="SketchUp navigation — orbit, viewpoint, fly, and architectural "
                              "walk controls in one place. Applies live to the focused model.",
                  foreground="#555", wraplength=600).pack(anchor="w", padx=10, pady=(8, 2))

        s0 = ttk.LabelFrame(parent, text="Sensitivity & rate")
        s0.pack(fill="x", padx=10, pady=6)
        self._rate_combo(s0, "Viewport refresh rate (Hz)", ("apps", "sketchup", "rate_hz"),
                         hint="Default = global; try 50 or 60")
        self._entry_row(s0, "Orbit sensitivity", base + ("orbit", "sensitivity"),
                        hint="1.0 = full broker angle")
        self._entry_row(s0, "Pan / move gain", base + ("pan", "gain"))
        self._entry_row(s0, "Zoom / vertical gain", base + ("zoom", "gain"))
        self._entry_row(s0, "Zoom dominance", base + ("zoom", "dominance"),
                        hint="twist vs movement plane")
        self._combo_row(s0, "Orbit ↔ pan/move toggle", base + ("toggle",),
                        values=["shift", "none"])

        s1 = ttk.LabelFrame(parent, text="Navigation mode")
        s1.pack(fill="x", padx=10, pady=6)
        self._combo_row(s1, "Mode", adv + ("nav_mode",), values=["orbit", "fly", "walk"])
        self._entry_row(s1, "Fly speed", adv + ("fly_speed",))
        self._entry_row(s1, "Walk speed", adv + ("walk_speed",))
        ttk.Label(s1, text="Fly = unconstrained 6DOF look/move. Walk keeps the horizon level and "
                           "moves forward/sideways on the ground plane; Shift enables movement.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(0, 4))

        s2 = ttk.LabelFrame(parent, text="Orbit")
        s2.pack(fill="x", padx=10, pady=6)
        self._mapped_combo_row(s2, "Orbit method", base + ("scheme", "orbit_style"),
                               [("Default (General)", "default"), ("Trackball (free)", "free"),
                                ("Turntable", "turntable")])
        self._mapped_combo_row(s2, "Orbit around", base + ("scheme", "orbit_pivot"),
                               [("Default (General)", "default"),
                                ("Viewpoint (turn in place)", "viewpoint"),
                                ("Auto Depth (surface)", "view"), ("Under Cursor (mouse)", "pointer"),
                                ("Model Centre", "object"),
                                ("World Origin", "origin")])
        self._bool_row(s2, "Lock horizon (keep level in free orbit)", adv + ("lock_horizon",))

        s3 = ttk.LabelFrame(parent, text="Pan / Zoom")
        s3.pack(fill="x", padx=10, pady=6)
        self._entry_row(s3, "Auto-depth hold (s)", ("apps", "sketchup", "view_pivot_hold_sec"),
                        hint="'Auto Depth' pivot: seconds still before it re-raycasts")
        ttk.Label(s3, text="SketchUp has no 3D cursor target here. Auto Depth uses the surface "
                           "under the viewport centre; Model Centre uses model.bounds.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(0, 4))

        s5 = ttk.LabelFrame(parent, text="Invert directions — independent per mode")
        s5.pack(fill="x", padx=10, pady=6)
        self._invert_row(s5, "Orbit", inv + ("orbit",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Twist", "twist"),
                          ("Pan X", "pan_x"), ("Pan Y", "pan_y"), ("Zoom", "zoom")])
        self._invert_row(s5, "Viewpoint", inv + ("viewpoint",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Roll", "roll")])
        self._invert_row(s5, "Fly", inv + ("fly",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Bank", "bank"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        self._invert_row(s5, "Walk", inv + ("walk",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        ttk.Label(s5, text="Viewpoint turns the camera in place and shares Orbit's pan/zoom "
                           "inverts. Fly and Walk movement directions are independent.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(2, 4))

    def _unreal_bindings_fields(self, parent):
        """The full Unreal control set (Blender-style), in one place. Mirrors _blender_bindings_fields,
        adapted to the editor's free-fly eye+rotator camera: orbit/fly/walk modes, viewpoint pivot,
        twist action, lock-horizon, per-mode inverts. (No zoom-style / zoom-to-mouse / camera-lock —
        not applicable in Unreal.) Sensitivities/toggle/rate write apps.unreal.bindings; mode/orbit/
        pan options write apps.unreal.advanced; per-mode flips write advanced.invert. All apply live."""
        base = ("apps", "unreal", "bindings")
        adv = ("apps", "unreal", "advanced")
        inv = adv + ("invert",)
        ttk.Label(parent, text="Unreal navigation — Blender-style options in one place. Applies live "
                              "to a focused Unreal Editor perspective viewport.", foreground="#555",
                  wraplength=600).pack(anchor="w", padx=10, pady=(8, 2))

        s0 = ttk.LabelFrame(parent, text="Sensitivity & rate")
        s0.pack(fill="x", padx=10, pady=6)
        self._rate_combo(s0, "Viewport refresh rate (Hz)", ("apps", "unreal", "rate_hz"),
                         hint="Default = global; try 60")
        self._entry_row(s0, "Orbit sensitivity", base + ("orbit", "sensitivity"),
                        hint="1.0 = baseline (set 0.5 for the cube's 1:1)")
        self._entry_row(s0, "Pan gain", base + ("pan", "gain"))
        self._entry_row(s0, "Zoom gain", base + ("zoom", "gain"))
        self._entry_row(s0, "Zoom dominance", base + ("zoom", "dominance"), hint="twist vs pan-plane")
        self._combo_row(s0, "Orbit ↔ pan/zoom toggle", base + ("toggle",), values=["shift", "none"])

        s1 = ttk.LabelFrame(parent, text="Navigation mode")
        s1.pack(fill="x", padx=10, pady=6)
        self._combo_row(s1, "Mode", adv + ("nav_mode",), values=["orbit", "fly", "walk"])
        self._entry_row(s1, "Fly speed", adv + ("fly_speed",))
        self._entry_row(s1, "Walk speed", adv + ("walk_speed",))
        ttk.Label(s1, text="Fly = free 6DOF (banks on twist; forward follows pitch). Walk = horizon-"
                           "locked look, movement stays on the ground plane.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(0, 4))

        s2 = ttk.LabelFrame(parent, text="Orbit")
        s2.pack(fill="x", padx=10, pady=6)
        self._mapped_combo_row(s2, "Orbit method", base + ("scheme", "orbit_style"),
                               [("Default (General)", "default"), ("Trackball (free)", "free"),
                                ("Turntable", "turntable")])
        self._mapped_combo_row(s2, "Orbit around", base + ("scheme", "orbit_pivot"),
                               [("Default (General)", "default"), ("Viewpoint (turn in place)", "viewpoint"),
                                ("Auto Depth (surface)", "view"), ("Under Cursor (mouse)", "pointer"),
                                ("Selection", "object"),
                                ("3D Cursor (→ Selection)", "cursor"), ("World Origin", "origin")])
        self._combo_row(s2, "Twist action", adv + ("twist_action",),
                        values=["roll", "zoom", "dolly", "none"])
        self._bool_row(s2, "Lock horizon (keep level even in free orbit)", adv + ("lock_horizon",))
        ttk.Label(s2, text="Unreal has no 3D cursor, so \"3D Cursor\" orbits the selection (same as "
                           "Selection). \"Auto Depth\" raycasts the surface under screen-centre.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(0, 4))

        s3 = ttk.LabelFrame(parent, text="Pan / Zoom")
        s3.pack(fill="x", padx=10, pady=6)
        self._bool_row(s3, "Pan scales with focus distance", adv + ("pan_scales_with_distance",))
        self._entry_row(s3, "Auto-depth hold (s)", ("apps", "unreal", "view_pivot_hold_sec"),
                        hint="'Auto Depth' pivot: seconds still before it re-raycasts")

        s5 = ttk.LabelFrame(parent, text="Invert directions — independent per mode")
        s5.pack(fill="x", padx=10, pady=6)
        self._invert_row(s5, "Orbit", inv + ("orbit",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Twist", "twist"),
                          ("Pan X", "pan_x"), ("Pan Y", "pan_y"), ("Zoom", "zoom")])
        self._invert_row(s5, "Viewpoint", inv + ("viewpoint",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Roll", "roll")])
        self._invert_row(s5, "Fly", inv + ("fly",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"), ("Bank", "bank"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        self._invert_row(s5, "Walk", inv + ("walk",),
                         [("Pitch", "pitch"), ("Yaw", "yaw"),
                          ("Fwd", "forward"), ("Strafe", "strafe"), ("Up/Dn", "vertical")])
        ttk.Label(s5, text="Each mode's directions are independent. \"Viewpoint\" = orbit-around "
                           "Viewpoint (turn in place); it shares Orbit's pan/zoom inverts.",
                  foreground="#888", wraplength=560).pack(anchor="w", padx=10, pady=(2, 4))

    # --- tab c: general bindings --------------------------------------------------
    def _build_general_tab(self, nb):
        outer = ttk.Frame(nb)

        sec1 = ttk.LabelFrame(outer, text="Device")
        sec1.pack(fill="x", padx=10, pady=(10, 6))
        self._entry_row(sec1, "Name", ("device", "name"), cast=str, width=22,
                        hint="applies on reconnect")
        self._entry_row(sec1, "Address (optional)", ("device", "address"), cast=str, width=22,
                        hint="AA:BB:..  applies on reconnect")

        secB = ttk.LabelFrame(outer, text="3D navigation bridge")
        secB.pack(fill="x", padx=10, pady=6)
        self._entry_row(secB, "Default update rate (Hz)", ("bridge", "rate_hz"), cast=int,
                        hint="global default; override per app in Per-App Bindings")

        secS = ttk.LabelFrame(outer, text="3D control scheme (defaults)")
        secS.pack(fill="x", padx=10, pady=6)
        self._mapped_combo_row(secS, "Orbit pivot", ("general", "scheme", "orbit_pivot"),
                               [("view", "view"), ("cursor (under mouse)", "pointer"),
                                ("object", "object"), ("origin", "origin"),
                                ("selection", "cursor")])
        self._combo_row(secS, "Orbit style", ("general", "scheme", "orbit_style"),
                        values=["free", "turntable"])
        self._mapped_combo_row(secS, "Zoom mode", ("general", "scheme", "zoom_mode"),
                               [("to_center", "to_center"), ("to_object", "to_object"),
                                ("to_cursor (under mouse)", "to_pointer")])
        ttk.Label(secS, text="Per-app overrides in Per-App Bindings. origin = rotate about the world "
                             "origin (no translation); object = about the model centre; view = about "
                             "the surface under the screen centre (like native middle-drag orbit); "
                             "cursor / to_cursor = about the surface under the MOUSE CURSOR (FreeCAD, "
                             "AutoCAD + Fusion today; other apps fall back to their view/object "
                             "pivot); selection = the selection / 3D-cursor pivot (falls back to the "
                             "model centre where the app has neither).",
                  foreground="#888", wraplength=600).pack(anchor="w", padx=10, pady=(2, 6))

        sec2 = ttk.LabelFrame(outer, text="Pointer / scroll (cursor mode)")
        sec2.pack(fill="x", padx=10, pady=6)
        self._entry_row(sec2, "Cursor sensitivity", ("general", "cursor", "gain"),
                        hint="radians → pixels")
        self._entry_row(sec2, "Scroll gain", ("general", "scroll", "gain"))
        self._entry_row(sec2, "Scroll deadzone", ("general", "scroll", "deadzone"))
        self._entry_row(sec2, "Scroll dominance", ("general", "scroll", "dominance"))

        sec3 = ttk.LabelFrame(outer, text="Mode & buttons")
        sec3.pack(fill="x", padx=10, pady=6)
        self._combo_row(sec3, "Default mode", ("general", "default_mode"),
                        values=["cube", "cursor"], on_change=self._apply_default_mode)
        for b in ("left", "right", "middle"):
            self._combo_row(sec3, f"{b.capitalize()} button", ("general", "buttons", b),
                            values=["left", "right", "middle", "none"])
        ttk.Label(sec3, text="Button mappings are reserved (the device handles buttons in HID mode).",
                  foreground="#888", wraplength=600).pack(anchor="w", padx=10, pady=(2, 6))
        return outer

    def _apply_default_mode(self):
        mode = self.cfg.data["general"]["default_mode"]
        self.app.engine.set_mode(self.app.engine.MODE_CUBE if mode == "cube"
                                 else self.app.engine.MODE_CURSOR)
