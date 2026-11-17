# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""What the settings UI can see and do.

Two projections: `state()` is everything the user configures (settings, apps, bindings) and
changes only when they act; `live()` is what the daemon is doing right now. Every mutation is a
named action in ACTIONS, so adding a feature to the UI means one function here and one view there.
"""

from collections.abc import Mapping
import sys
import threading

from .. import __version__, integrations
from ..app_registry import APP_SPECS, APP_SPECS_BY_ID
from ..binding_ui_model import BindingUIModel
from ..commands import SetInputMode, SetNavigationMode, ToggleInputMode
from ..control_hud import HudTextProjector
from ..devices import builtin_device_descriptors
from ..product import DISPLAY_NAME
from ..settings_schema import SETTING_SPECS
from ..settings_ui_model import (
    CATEGORIES,
    CHOICE_LABELS,
    PIVOT_LABELS,
    SettingsUIModel,
    app_hints,
)

ACTIONS = {}


def action(name):
    def register(function):
        ACTIONS[name] = function
        return function
    return register


def plain(value):
    """Deep-convert immutable config values (MappingProxy, tuples) to JSON types."""
    if isinstance(value, Mapping):
        return {str(key): plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(child) for child in value]
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value  # enums
    return value


def _startup():
    if sys.platform != "win32":
        return None
    from ..tray import is_startup_enabled
    return is_startup_enabled()


class UiApi:
    def __init__(self, app, on_change=lambda topic: None):
        self.app = app
        self.settings = SettingsUIModel(app.config)
        self.bindings = BindingUIModel(app.config, app.binding_catalog, app.input_aggregator)
        self.on_change = on_change
        self._detection = {}
        self._detecting = False
        self._detection_lock = threading.Lock()
        self._hud_text = HudTextProjector()
        self._schema = self._build_schema()

    # --- projections ---------------------------------------------------------------------

    def _build_schema(self):
        settings = []
        for spec in SETTING_SPECS:
            settings.append({
                "id": spec.id,
                "kind": spec.value_kind.value,
                "label": spec.ui.label,
                "help": spec.ui.help,
                "control": spec.ui.control,
                "category": spec.category,
                "scope": spec.scope.value,
                "choices": list(spec.choices),
                "min": spec.minimum,
                "max": spec.maximum,
                "nullable": spec.nullable,
                "apps": [app.app_id for app in APP_SPECS if spec.applies_to(app)],
            })
        return {
            "settings": settings,
            "categories": [{"id": item.category_id, "title": item.title, "page": item.page,
                            "summary": item.summary} for item in CATEGORIES],
            "choiceLabels": CHOICE_LABELS,
            "pivotLabels": PIVOT_LABELS,
        }

    def state(self):
        snapshot = self.app.config.snapshot()
        return {
            "product": {"name": DISPLAY_NAME, "version": __version__},
            "schema": self._schema,
            "global": {
                view.spec.id: {"value": plain(view.value), "system": plain(view.system_value),
                               "overridden": view.overridden}
                for view in self.settings.global_views()
            },
            "apps": [self._app(spec, snapshot) for spec in APP_SPECS],
            "bindings": self._bindings(),
            "startup": _startup(),
            "firstRun": bool(getattr(self.app, "first_run", False)),
        }

    def _app(self, spec, snapshot):
        appdef = integrations.APPS_BY_KEY[spec.app_id]
        operational = snapshot.app_operational[spec.app_id]
        return {
            "id": spec.app_id,
            "name": spec.display_name,
            "tier": spec.support_tier.value,
            "tierLabel": spec.support_label,
            "tierSummary": spec.support_summary,
            "modes": list(spec.supported_modes),
            "enabled": bool(operational.get("enabled")),
            "installed": bool(operational.get("installed")),
            "addin": appdef.key in integrations.ADDIN_KEYS,
            "verified": appdef.verified_versions,
            "installModel": appdef.install_model,
            "security": appdef.security_notes,
            "securityConfirmation": appdef.security_confirmation,
            "instructions": integrations.integration_instructions(appdef),
            "detection": self._detection.get(spec.app_id),
            "allLinked": self.settings.all_app_settings_linked(spec.app_id),
            "settings": {
                view.spec.id: {"value": plain(view.value), "system": plain(view.system_value),
                               "linked": view.linked, "globalCompatible": view.global_compatible,
                               "choices": plain(view.choices)}
                for view in self.settings.app_views(spec.app_id)
            },
            "hints": app_hints(snapshot, spec.app_id),
        }

    def _bindings(self):
        model = self.bindings
        profile = model.profile_id
        overrides = self.app.config.snapshot().keybinding_overrides[profile]
        rows = []
        for binding in model.bindings():
            row = model.row(binding.id)
            simple = model.simple_action(row)
            rows.append({
                "row": plain(row),
                "system": model.is_system_binding(binding.id),
                "modified": binding.id in overrides,
                "simple": {"target": simple.target_id, "operation": simple.operation_id,
                           "value": simple.value_text, "advanced": simple.advanced_only},
                "warning": model.pass_through_warning(row["chord"]),
            })
        setting_ops = {}
        for target_id, _label in model.simple_target_options():
            if target_id.startswith("setting:"):
                setting_id = target_id.removeprefix("setting:")
                setting_ops[setting_id] = {
                    "operations": [list(item) for item in model.setting_operation_options(
                        setting_id)],
                    "values": list(model.setting_value_options(setting_id)),
                }
        controls = []
        for descriptor in builtin_device_descriptors():
            for control in descriptor.controls:
                controls.append({"token": f"{descriptor.source_id}:{control.control_id}",
                                 "label": control.label, "device": descriptor.label})
        return {
            "profile": profile,
            "profiles": [{"id": pid, "label": label} for pid, label in model.profiles()],
            "rows": rows,
            "targets": [{"id": tid, "label": label} for tid, label in
                        model.simple_target_options()],
            "settingOps": setting_ops,
            "capabilities": [
                {"source": item.source_id, "controls": list(item.required_controls),
                 "status": item.status, "detail": item.detail}
                for item in model.capability_views()
            ],
            "selectors": sorted(model.catalog.control_selectors),
            "controls": controls,
            "nextId": model.next_custom_id(),
        }

    def live(self):
        app = self.app
        runtime = app.runtime.snapshot()
        hud = self._hud_text.project(runtime)
        connected = {key: version for key, version, _pid in list(app.connected_apps)}
        return {
            "connection": {
                "status": app.status_text(),
                "connected": app.is_connected(),
                "battery": app.battery_level(),
                "power": app.battery_status_text(),
            },
            "runtime": {
                "inputMode": runtime.effective_input_mode,
                "navigationMode": runtime.effective_navigation_mode,
                "layer": runtime.effective_navigation_layer,
                "focusedApp": runtime.focused_context.app_id,
                "executable": runtime.focused_context.executable,
                "context": hud.context,
                "help": hud.help,
                "binding": hud.binding,
            },
            "connectedApps": connected,
            "health": {
                service_id: {"state": item.state.value, "detail": item.detail}
                for service_id, item in app.runtime_health_snapshot().items()
            },
            "pressed": list(app.input_aggregator.snapshot().pressed_tokens),
        }

    # --- integration detection (slow: file globs and executable version probes) -----------

    def detect(self, force=False):
        with self._detection_lock:
            if self._detecting or (self._detection and not force):
                return
            self._detecting = True
        threading.Thread(target=self._run_detection, name="ui-detect", daemon=True).start()

    def _run_detection(self):
        try:
            observed = dict(getattr(self.app, "observed_addin_versions", {}))
            for appdef in integrations.APPS:
                try:
                    compat = integrations.compatibility(appdef)
                    version = observed.get(appdef.key)
                    label = (integrations.setup_action_label(appdef, installed_version=version)
                             if version else integrations.setup_action_label(appdef))
                    stale = (appdef.key in integrations.ADDIN_KEYS and (
                        integrations.update_available(appdef.key, installed_version=version)
                        if version else integrations.update_available(appdef.key)))
                    result = {
                        "status": integrations.status_line(appdef),
                        "compatibility": compat.status,
                        "compatibilityMessage": compat.message,
                        "hostVersion": compat.version,
                        "setupLabel": label,
                        "updateAvailable": bool(stale),
                        "installedVersion": (
                            (version or integrations.installed_addin_version(appdef.key))
                            if appdef.key in integrations.ADDIN_KEYS else None),
                    }
                except Exception as exc:  # detection informs; it must never break the UI
                    result = {"status": f"detection failed: {exc}", "compatibility": "unknown",
                              "compatibilityMessage": "", "hostVersion": None,
                              "setupLabel": appdef.setup_action, "updateAvailable": False,
                              "installedVersion": None}
                self._detection[appdef.key] = result
        finally:
            with self._detection_lock:
                self._detecting = False
            self.on_change("state")

    # --- actions -------------------------------------------------------------------------

    def run(self, name, params):
        handler = ACTIONS.get(name)
        if handler is None:
            raise ValueError(f"unknown action: {name}")
        if not isinstance(params, dict):
            raise ValueError("action parameters must be an object")
        return handler(self, **params)


def _app_id(app):
    if app not in APP_SPECS_BY_ID:
        raise ValueError(f"unknown app: {app}")
    return app


@action("settings.set")
def _settings_set(api, id, value, app=None):
    if app is None:
        return api.settings.set_global(id, value)
    return api.settings.set_app(_app_id(app), id, value)


@action("settings.reset")
def _settings_reset(api, id, app=None):
    if app is None:
        return api.settings.reset_global(id)
    return api.settings.reset_app_setting(_app_id(app), id)


@action("settings.link")
def _settings_link(api, app, id):
    return api.settings.toggle_app_link(_app_id(app), id)


@action("settings.linkAll")
def _settings_link_all(api, app):
    return api.settings.toggle_all_app_links(_app_id(app))


@action("settings.resetApp")
def _settings_reset_app(api, app):
    return api.settings.reset_app(_app_id(app))


@action("settings.resetGlobals")
def _settings_reset_globals(api):
    return api.settings.reset_all_globals()


@action("apps.enable")
def _apps_enable(api, app, enabled):
    api.app.config.set_app_operational(_app_id(app), enabled=bool(enabled))


@action("apps.detect")
def _apps_detect(api):
    api.detect(force=True)


@action("apps.setup")
def _apps_setup(api, app, allowUnsupported=False, installStartup=None):
    appdef = integrations.APPS_BY_KEY[_app_id(app)]
    extra = {} if installStartup is None else {"install_startup": bool(installStartup)}
    ok, message, copyables = integrations.normalize_install_result(integrations.install(
        appdef, api.app.config, allow_unsupported_host=bool(allowUnsupported), **extra))
    api.detect(force=True)
    return {"ok": ok, "message": message,
            "copyables": [{"label": label, "text": text} for label, text in copyables]}


@action("apps.preflight")
def _apps_preflight(api, app):
    """What the user must confirm before setup: an unsupported host, and any security note."""
    appdef = integrations.APPS_BY_KEY[_app_id(app)]
    return {"unsupported": integrations.unsupported_host_warning(appdef),
            "confirmation": appdef.security_confirmation or None,
            "asksStartup": appdef.key == "blender",
            "detected": bool(appdef.detect())}


@action("onshape.userscript")
def _onshape_userscript(api):
    from ..onshape_bridge import (BRIDGE_URL, pointer_install_instructions,
                                  pointer_userscript_source)
    return {"script": pointer_userscript_source(), "instructions": pointer_install_instructions(),
            "bridgeUrl": BRIDGE_URL}


@action("bindings.profile")
def _bindings_profile(api, profile):
    return api.bindings.set_profile(profile)


@action("bindings.save")
def _bindings_save(api, row):
    return api.bindings.save(row)


@action("bindings.saveSimple")
def _bindings_save_simple(api, id, label, enabled, chord, match, target, operation="",
                          value="", when=None, priority=0):
    activation, press, release = api.bindings.build_simple_action(target, operation, value)
    row = {"id": id, "label": str(label).strip() or id, "enabled": bool(enabled),
           "chord": list(chord), "match": match, "activation": activation,
           "priority": priority, "press": press, "release": release}
    if when:
        row["when"] = when
    return api.bindings.save(row)


@action("bindings.delete")
def _bindings_delete(api, id):
    return api.bindings.delete(id)


@action("bindings.restore")
def _bindings_restore(api, id):
    return api.bindings.restore_system(id)


@action("runtime.inputMode")
def _runtime_input_mode(api, mode):
    command = ToggleInputMode(origin="ui") if mode == "toggle" else SetInputMode(
        origin="ui", mode=mode)
    api.app.commands.dispatch(command)


@action("runtime.navigationMode")
def _runtime_navigation_mode(api, mode):
    api.app.commands.dispatch(SetNavigationMode(origin="ui", mode=mode))


@action("device.recenter")
def _device_recenter(api):
    api.app.engine.reset_view()


@action("startup.set")
def _startup_set(api, enabled):
    from ..tray import set_startup_enabled
    set_startup_enabled(bool(enabled))


@action("welcome.done")
def _welcome_done(api):
    api.app.first_run = False

