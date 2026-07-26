# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""First-run setup guide projected from the daemon's existing authorities."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from . import integrations
from .app_registry import APP_IDS_BY_TIER, APP_SPECS_BY_ID, SupportTier
from .tray import is_startup_enabled, set_startup_enabled


STEP_IDS = (
    "welcome",
    "device",
    "input",
    "applications",
    "setup",
    "navigation",
    "finish",
)


@dataclass(frozen=True)
class ApplicationSetupView:
    app_id: str
    display_name: str
    tier: str
    status: str
    enabled: bool
    installed: bool


class OnboardingModel:
    """Read-only setup projections plus the two explicit user actions the guide offers."""

    def __init__(self, app):
        self.app = app

    def device_identity(self):
        snapshot = self.app.config.snapshot()
        return {
            "name": snapshot.device_value("device.name"),
            "address": snapshot.device_value("device.address") or "automatic discovery",
            "status": self.app.status_text(),
            "connected": self.app.is_connected(),
        }

    def use_keyboard_only(self):
        return self.app.config.transaction().set_input_profile("keyboard_only").commit()

    def input_state(self):
        snapshot = self.app.input_aggregator.snapshot()
        health = []
        for source_id, item in sorted(snapshot.provider_health.items()):
            detail = f" — {item.detail}" if item.detail else ""
            health.append(f"{source_id}: {item.status.value}{detail}")
        return {
            "pressed": tuple(snapshot.pressed_tokens),
            "health": tuple(health),
            "profile": self.app.config.snapshot().input_profile,
        }

    def applications(self):
        operational = self.app.config.snapshot().app_operational
        rows = []
        for tier in (SupportTier.SUPPORTED, SupportTier.EXPERIMENTAL):
            for app_id in APP_IDS_BY_TIER[tier]:
                spec = APP_SPECS_BY_ID[app_id]
                appdef = integrations.APPS_BY_KEY[app_id]
                state = operational[app_id]
                try:
                    status = integrations.status_line(appdef)
                except Exception as exc:  # detection is informative; it must not break onboarding
                    status = f"detection failed: {exc}"
                rows.append(ApplicationSetupView(
                    app_id=app_id,
                    display_name=spec.display_name,
                    tier=tier.value,
                    status=status,
                    enabled=bool(state["enabled"]),
                    installed=bool(state["installed"]),
                ))
        return tuple(rows)

    @staticmethod
    def setup_instructions(app_id):
        return integrations.integration_instructions(integrations.APPS_BY_KEY[app_id])

    def navigation_state(self):
        runtime = self.app.runtime.snapshot()
        health = self.app.runtime_health_summary()
        context = runtime.focused_context
        return {
            "input_mode": runtime.effective_input_mode,
            "navigation_mode": runtime.effective_navigation_mode,
            "navigation_layer": runtime.effective_navigation_layer,
            "focused_app": context.app_id or "none",
            "health": health,
            "connection": self.app.status_text(),
        }


class FirstRunWizard:
    """A reopenable, keyboard-navigable guide that performs no implicit system changes."""

    def __init__(self, root, app, *, open_settings):
        self.root = root
        self.app = app
        self.model = OnboardingModel(app)
        self.open_settings = open_settings
        self.win = None
        self.body = None
        self.step_var = None
        self.back_button = None
        self.next_button = None
        self._step_index = 0
        self._refresh_after = None
        self._setup_app_id = APP_IDS_BY_TIER[SupportTier.SUPPORTED][0]
        self._start_at_login = None

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

    def _build(self):
        self.win = tk.Toplevel(self.root)
        self.win.title("Astrolabe — Setup guide")
        self.win.geometry("760x560")
        self.win.minsize(680, 500)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        header = ttk.Frame(self.win, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Astrolabe setup", font=("", 17, "bold")).pack(anchor="w")
        self.step_var = tk.StringVar()
        ttk.Label(header, textvariable=self.step_var, foreground="#555").pack(anchor="w", pady=(3, 0))
        ttk.Separator(self.win).pack(fill="x")

        self.body = ttk.Frame(self.win, padding=18)
        self.body.pack(fill="both", expand=True)

        footer = ttk.Frame(self.win, padding=(18, 8, 18, 14))
        footer.pack(fill="x")
        ttk.Button(footer, text="Close", command=self.close).pack(side="left")
        self.next_button = ttk.Button(footer, command=self._next)
        self.next_button.pack(side="right")
        self.back_button = ttk.Button(footer, text="Back", command=self._back)
        self.back_button.pack(side="right", padx=(0, 8))
        self._render()

    def close(self):
        self._cancel_refresh()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    def _cancel_refresh(self):
        if self._refresh_after is not None and self.win is not None:
            try:
                self.win.after_cancel(self._refresh_after)
            except tk.TclError:
                pass
        self._refresh_after = None

    def _schedule_refresh(self, callback):
        self._cancel_refresh()
        if self.win is not None:
            self._refresh_after = self.win.after(350, callback)

    def _clear(self):
        self._cancel_refresh()
        for child in self.body.winfo_children():
            child.destroy()

    def _title(self, title, copy):
        ttk.Label(self.body, text=title, font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            self.body, text=copy, wraplength=700, justify="left",
        ).pack(fill="x", anchor="w", pady=(6, 14))

    def _render(self):
        self._clear()
        step_id = STEP_IDS[self._step_index]
        self.step_var.set(f"Step {self._step_index + 1} of {len(STEP_IDS)}")
        self.back_button.configure(state=("disabled" if self._step_index == 0 else "normal"))
        self.next_button.configure(text=("Finish" if step_id == "finish" else "Next"))
        getattr(self, f"_render_{step_id}")()

    def _back(self):
        if self._step_index:
            self._step_index -= 1
            self._render()

    def _next(self):
        if self._step_index == len(STEP_IDS) - 1:
            set_startup_enabled(bool(self._start_at_login.get()))
            self.open_settings()
            self.close()
            return
        self._step_index += 1
        self._render()

    def _render_welcome(self):
        self._title(
            "Welcome",
            "Astrolabe turns a compatible BLE trackball into a normal pointer and a focus-routed "
            "three-axis controller for supported CAD and 3D applications.",
        )
        points = (
            "No telemetry is collected or sent.",
            "BLE, optional pass-through Raw Input, and loopback-only host transports stay local.",
            "Host add-ons, certificate trust, startup registration, and project changes require "
            "an explicit action.",
            "Closing this guide changes nothing. Reopen it from the tray at any time.",
        )
        for point in points:
            ttk.Label(self.body, text=f"• {point}", wraplength=680, justify="left").pack(
                fill="x", anchor="w", pady=3)

    def _render_device(self):
        self._title(
            "Device",
            "Connect the trackball or continue with keyboard-only controls. BLE identity changes "
            "apply on reconnect.",
        )
        device = self.model.device_identity()
        for label, value in (
                ("Configured name", device["name"]),
                ("Address", device["address"]),
                ("Connection", device["status"])):
            row = ttk.Frame(self.body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=20, anchor="w").pack(side="left")
            ttk.Label(row, text=str(value), anchor="w").pack(side="left")
        ttk.Button(
            self.body, text="Use keyboard-only profile for now",
            command=lambda: (self.model.use_keyboard_only(), self._render()),
        ).pack(anchor="w", pady=(16, 4))
        ttk.Label(
            self.body,
            text=f"Current input profile: {self.app.config.snapshot().input_profile}",
            foreground="#555",
        ).pack(anchor="w")

    def _render_input(self):
        self._title(
            "Input test",
            "Press the five-way controls and verify that each token appears and releases. "
            "A control that remains listed after release is not safe to continue with.",
        )
        state_var = tk.StringVar()
        ttk.Label(
            self.body, textvariable=state_var, justify="left", wraplength=690,
        ).pack(fill="x", anchor="w")

        def refresh():
            state = self.model.input_state()
            pressed = ", ".join(state["pressed"]) or "none"
            providers = "\n".join(state["health"]) or "No provider health has been published yet."
            state_var.set(
                f"Profile: {state['profile']}\nPressed controls: {pressed}\n\nProviders\n{providers}")
            self._schedule_refresh(refresh)

        refresh()

    def _render_applications(self):
        self._title(
            "Applications",
            "Supported integrations gate V1. Experimental integrations are available without the "
            "same host-version maintenance promise.",
        )
        tree = ttk.Treeview(
            self.body, columns=("tier", "state", "status"), show="headings", height=13)
        for key, title, width in (
                ("tier", "Release tier", 100),
                ("state", "Configured", 100),
                ("status", "Detection / compatibility", 410)):
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=(key == "status"))
        for row in self.model.applications():
            configured = "enabled" if row.enabled else ("set up" if row.installed else "off")
            tree.insert(
                "", "end", iid=row.app_id,
                values=(row.tier.title(), configured, f"{row.display_name}: {row.status}"))
        tree.pack(fill="both", expand=True)

    def _render_setup(self):
        self._title(
            "Setup and consent",
            "Choose an application to review its exact setup, permission, update, and reversal "
            "instructions. Actual changes remain in the 3D Apps panel behind their existing "
            "confirmation boundary.",
        )
        labels = {
            APP_SPECS_BY_ID[app_id].display_name: app_id
            for tier in (SupportTier.SUPPORTED, SupportTier.EXPERIMENTAL)
            for app_id in APP_IDS_BY_TIER[tier]
        }
        selected = tk.StringVar(value=APP_SPECS_BY_ID[self._setup_app_id].display_name)
        chooser = ttk.Combobox(
            self.body, textvariable=selected, values=tuple(labels), state="readonly", width=28)
        chooser.pack(anchor="w")
        instructions = tk.Text(self.body, height=14, wrap="word", takefocus=True)
        instructions.pack(fill="both", expand=True, pady=10)

        def refresh(_event=None):
            self._setup_app_id = labels[selected.get()]
            instructions.configure(state="normal")
            instructions.delete("1.0", "end")
            instructions.insert("1.0", self.model.setup_instructions(self._setup_app_id))
            instructions.configure(state="disabled")

        chooser.bind("<<ComboboxSelected>>", refresh)
        refresh()
        ttk.Button(
            self.body, text="Open 3D Apps to set up or reverse integrations",
            command=self.open_settings,
        ).pack(anchor="e")

    def _render_navigation(self):
        self._title(
            "Navigation test",
            "Switch to 3D mode, focus a supported host viewport, then verify orbit, pan, zoom, and "
            "the health state below. This page observes state; it does not synthesize input.",
        )
        state_var = tk.StringVar()
        ttk.Label(
            self.body, textvariable=state_var, justify="left", wraplength=690,
        ).pack(fill="x", anchor="w")

        def refresh():
            state = self.model.navigation_state()
            state_var.set(
                f"BLE: {state['connection']}\n"
                f"Focused integration: {state['focused_app']}\n"
                f"Input mode: {state['input_mode']}\n"
                f"Navigation mode: {state['navigation_mode']}\n"
                f"Layer: {state['navigation_layer']}\n"
                f"Runtime health: {state['health']}")
            self._schedule_refresh(refresh)

        refresh()

    def _render_finish(self):
        self._title(
            "Finish",
            "The tray keeps Astrolabe running after its windows close. Settings and the setup guide "
            "remain available there. Start at login is the only system change offered on this page.",
        )
        self._start_at_login = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(
            self.body, text="Start Astrolabe when I sign in",
            variable=self._start_at_login,
        ).pack(anchor="w", pady=8)
        ttk.Label(
            self.body,
            text="Finish opens Settings. Host integrations remain disabled until you explicitly "
                 "enable and set them up.",
            wraplength=680, justify="left",
        ).pack(fill="x", anchor="w", pady=(8, 0))
