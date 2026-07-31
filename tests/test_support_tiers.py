# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""What the product claims about an integration must be what the registry says.

A support tier is a promise: it decides whether a regression blocks a release and whether a user
should expect the integration to survive the next host update. That promise is made in several
places -- the settings panel, the copyable setup text, the README, the release smoke output -- and
the failure mode is not a crash. It is a claim quietly drifting away from the decision behind it, so
these tests exist to fail when any surface and `app_registry.py` disagree.

The other half is keeping the tier apart from host-version compatibility. They answer different
questions, and the plan this implements forbids using one as the other.
"""

from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pytest

from trackball_daemon import integrations
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import InputAggregator, load_system_binding_profiles
from trackball_daemon.ui import SettingsWindow
from trackball_daemon.app_registry import (
    APP_IDS,
    APP_IDS_BY_TIER,
    APP_SPECS,
    APP_SPECS_BY_ID,
    SUPPORT_TIER_LABELS,
    SUPPORT_TIER_SUMMARIES,
    SupportTier,
)
from trackball_daemon.release_smoke import run_release_smoke


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")

# The assignment from the P1 release foundation plan, restated independently of the registry. If a
# tier changes, this test should be the thing that forces the change to be deliberate.
EXPECTED_TIERS = {
    "blender": SupportTier.SUPPORTED,
    "freecad": SupportTier.SUPPORTED,
    "fusion360": SupportTier.SUPPORTED,
    "solidworks": SupportTier.SUPPORTED,
    "onshape": SupportTier.SUPPORTED,
    "sketchup": SupportTier.EXPERIMENTAL,
    "unreal": SupportTier.EXPERIMENTAL,
    "unity": SupportTier.EXPERIMENTAL,
    "godot": SupportTier.EXPERIMENTAL,
    "rhino": SupportTier.EXPERIMENTAL,
    "autocad": SupportTier.EXPERIMENTAL,
}


def _readme_section(heading):
    """The lines of one README section, up to the next heading of the same or higher level."""
    lines = README.splitlines()
    start = lines.index(heading)
    for offset, line in enumerate(lines[start + 1:], start + 1):
        if line.startswith("## ") or line.startswith("### "):
            return lines[start + 1:offset]
    return lines[start + 1:]


def _readme_app_ids(heading):
    """App ids listed in a README section, taken from each row's maintainer-guide link.

    The link target is the app id, so this does not depend on display names matching -- the README
    legitimately writes "Rhino 8" and "SketchUp Desktop" where the registry says "Rhino" and
    "SketchUp".
    """
    return tuple(match.group(1)
                 for line in _readme_section(heading)
                 for match in [re.search(r"docs/apps/([a-z0-9]+)\.md", line)]
                 if match)


def test_every_integration_declares_a_tier():
    assert set(EXPECTED_TIERS) == set(APP_IDS)
    for spec in APP_SPECS:
        assert isinstance(spec.support_tier, SupportTier), spec.app_id


@pytest.mark.parametrize("app_id, tier", sorted(EXPECTED_TIERS.items()))
def test_tier_assignment_is_the_one_that_was_decided(app_id, tier):
    assert APP_SPECS_BY_ID[app_id].support_tier is tier


def test_a_tier_cannot_be_defaulted_into():
    """A new integration's release commitment has to be chosen, not inherited by omission."""
    from dataclasses import fields

    support_tier = next(f for f in fields(APP_SPECS[0]) if f.name == "support_tier")
    import dataclasses

    assert support_tier.default is dataclasses.MISSING
    assert support_tier.default_factory is dataclasses.MISSING


def test_the_tier_partition_is_complete_and_disjoint():
    grouped = [app_id for tier in SupportTier for app_id in APP_IDS_BY_TIER[tier]]

    assert sorted(grouped) == sorted(APP_IDS)
    assert len(grouped) == len(set(grouped))


def test_registry_order_survives_grouping():
    """UI groups and README tables both order by tier; neither may resort the registry."""
    for tier in SupportTier:
        grouped = APP_IDS_BY_TIER[tier]
        assert grouped == tuple(app_id for app_id in APP_IDS
                                if APP_SPECS_BY_ID[app_id].support_tier is tier)


# --- the tier is not host-version compatibility ----------------------------------------------------

def test_an_experimental_integration_can_still_have_a_verified_host_version():
    """The plan's example case: "AutoCAD: Experimental integration; AutoCAD 2026: verified host"."""
    autocad = integrations.APPS_BY_KEY["autocad"]

    assert autocad.support_tier is SupportTier.EXPERIMENTAL
    assert integrations.compatibility(
        autocad, r"C:\Autodesk\AutoCAD 2026\acad.exe").status == "supported"


def test_a_supported_integration_can_still_meet_an_unsupported_host_version():
    blender = integrations.APPS_BY_KEY["blender"]

    assert blender.support_tier is SupportTier.SUPPORTED
    assert integrations.compatibility(
        blender, r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe"
    ).status == "unsupported"


def test_the_host_version_field_is_not_named_for_support():
    """One field named "supported" on each side is how the two claims get conflated."""
    from dataclasses import fields

    names = {field.name for field in fields(integrations.AppDef)}

    assert "verified_versions" in names
    assert "supported_versions" not in names


def test_every_tier_has_wording_to_render():
    for tier in SupportTier:
        assert SUPPORT_TIER_LABELS[tier]
        assert SUPPORT_TIER_SUMMARIES[tier]


# --- what the settings panel actually shows --------------------------------------------------------
#
# These build the real tab and read rendered widget text. Scanning ui.py source instead would fail on
# a comment or docstring that merely mentions the old wording, which is the wrong thing to police.

def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _texts(widget):
    values = []
    for candidate in _walk(widget):
        try:
            values.append(str(candidate.cget("text")))
        except tk.TclError:
            pass
    return values


def _card(tab, display_name):
    return next(candidate for candidate in _walk(tab)
                if isinstance(candidate, ttk.LabelFrame)
                and str(candidate.cget("text")) == display_name)


@pytest.fixture(scope="module")
def settings_ui(tmp_path_factory):
    """One real settings window for the whole module.

    Module-scoped on purpose: creating and tearing down a Tk root per test is enough churn that the
    interpreter intermittently fails to locate tk.tcl, and a test that skips itself under load is a
    test that stops covering anything. Nothing here toggles panel state, and the widgets the install
    test creates hang off the window rather than the tab, so sharing is safe.
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:                    # pragma: no cover - headless non-Windows CI
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = SimpleNamespace(
        config=ConfigStore(tmp_path_factory.mktemp("tiers") / "config.json").load(),
        binding_catalog=load_system_binding_profiles(),
        input_aggregator=InputAggregator(),
        status_text=lambda: "stopped",
        battery_status_text=lambda: "Battery: unavailable",
        runtime_health_snapshot=lambda: {},
    )
    ui = SettingsWindow(root, app)
    try:
        ui._build()
        root.update()
        yield ui
    finally:
        root.destroy()


@pytest.fixture(scope="module")
def apps_tab(settings_ui):
    notebook = next(widget for widget in settings_ui.win.winfo_children()
                    if isinstance(widget, ttk.Notebook))
    return next(notebook.nametowidget(tab_id) for tab_id in notebook.tabs()
                if notebook.tab(tab_id, "text") == "3D Apps")


def test_the_panel_does_not_call_the_whole_list_supported(apps_tab):
    shown = "\n".join(_texts(apps_tab))

    assert "Supported 3D apps" not in shown
    assert "Supported versions:" not in shown
    assert "Verified host versions:" in shown


def test_the_panel_states_both_claims_on_a_card(apps_tab):
    """The plan's example: an experimental integration whose host version is verified."""
    shown = _texts(_card(apps_tab, "AutoCAD"))

    assert SUPPORT_TIER_LABELS[SupportTier.EXPERIMENTAL] in shown
    assert any(text.startswith("Verified host versions:") for text in shown)


def test_experimental_integrations_are_collapsed_by_default(apps_tab):
    """An eleven-card list giving every integration equal weight is itself a support claim."""
    texts = _texts(apps_tab)

    assert f"{SUPPORT_TIER_LABELS[SupportTier.SUPPORTED]}s" in texts
    assert any("experimental integrations" in text for text in texts)
    # The cards themselves are packed either way; it is their group container that is withheld.
    assert _card(apps_tab, "Blender").master.winfo_manager() == "pack"
    assert _card(apps_tab, "AutoCAD").master.winfo_manager() == ""


def test_every_integration_appears_under_exactly_one_tier(apps_tab):
    containers = {app_id: _card(apps_tab, APP_SPECS_BY_ID[app_id].display_name).master
                  for app_id in APP_IDS}

    assert len({id(container) for container in containers.values()}) == len(SupportTier)
    for tier in SupportTier:
        grouped = {id(containers[app_id]) for app_id in APP_IDS_BY_TIER[tier]}
        assert len(grouped) == 1, tier


# --- user-facing projections -----------------------------------------------------------------------

@pytest.mark.parametrize("heading, tier", [
    ("### Supported integrations", SupportTier.SUPPORTED),
    ("### Experimental integrations", SupportTier.EXPERIMENTAL),
])
def test_the_readme_tables_list_exactly_the_registry_tier(heading, tier):
    assert _readme_app_ids(heading) == APP_IDS_BY_TIER[tier]


def test_the_readme_states_the_release_gate_for_each_tier():
    supported = " ".join(_readme_section("### Supported integrations"))
    experimental = " ".join(_readme_section("### Experimental integrations"))

    assert "release-blocking" in supported
    assert "does not block a release" in experimental
    # Experimental is not a licence to ship a defect that harms every integration.
    for shared in ("security", "data-loss", "configuration-corruption", "lifecycle"):
        assert shared in experimental, shared


def test_the_copyable_instructions_state_both_claims_separately():
    for appdef in integrations.APPS:
        text = integrations.integration_instructions(appdef)

        assert "Release support\n" in text, appdef.key
        assert "Verified host versions\n" in text, appdef.key
        assert appdef.support_label in text, appdef.key
        assert appdef.verified_versions in text, appdef.key


def test_the_release_smoke_reports_the_registry_tiers():
    """The release artifact's own claim about what it supports, derived rather than written down."""
    reported = run_release_smoke()["support_tiers"]

    assert reported == {tier.value: list(APP_IDS_BY_TIER[tier]) for tier in SupportTier}


def test_integrations_expose_the_tier_without_restating_it():
    """AppDef proxies the spec, so there is no second tier table to fall out of step."""
    integrations_source = (ROOT / "trackball_daemon" / "integrations.py").read_text(encoding="utf-8")

    for appdef in integrations.APPS:
        assert appdef.support_tier is appdef.spec.support_tier
    assert "SupportTier.SUPPORTED" not in integrations_source
    assert "SupportTier.EXPERIMENTAL" not in integrations_source


# --- gating ----------------------------------------------------------------------------------------

def test_no_integration_is_enabled_before_a_user_enables_it(isolated_config):
    from trackball_daemon.config import Config

    operational = Config().load().snapshot().app_operational

    for app_id in APP_IDS:
        assert operational[app_id]["enabled"] is False, app_id
        assert operational[app_id]["installed"] is False, app_id


@pytest.fixture
def blender_host(monkeypatch):
    """Pin what Blender detection reports.

    `AppDef.detect` holds a direct reference to its detector, so patching the module-level
    `detect_blender` would not change what this integration sees -- and the test would silently
    classify whatever Blender is really installed on the machine running it.
    """
    appdef = integrations.APPS_BY_KEY["blender"]

    def pin(version):
        monkeypatch.setattr(
            appdef, "detect",
            lambda: rf"C:\Program Files\Blender Foundation\Blender {version}\blender.exe")
        return appdef

    return pin


def test_setup_refuses_a_known_unsupported_host_and_names_it(isolated_config, blender_host):
    from trackball_daemon.config import Config

    cfg = Config().load()
    blender = blender_host("3.6")

    ok, message = integrations.install(blender, cfg)

    assert ok is False
    assert "3.6" in message                                  # the exact detected version
    assert blender.verified_versions in message              # and what was actually verified
    assert "Nothing has been changed" in message
    assert cfg.snapshot().app_operational["blender"]["installed"] is False


def test_an_explicit_override_is_what_lets_setup_proceed(isolated_config, blender_host, monkeypatch):
    from trackball_daemon.config import Config

    cfg = Config().load()
    blender = blender_host("3.6")
    reached = []
    monkeypatch.setattr(blender, "setup",
                        lambda appdef, config, **kwargs: reached.append(kwargs) or (True, "ok"))

    assert integrations.install(blender, cfg)[0] is False     # unchanged without the override

    ok, _message = integrations.install(blender, cfg, allow_unsupported_host=True)

    assert ok is True
    assert reached == [{}]


def test_setup_keyword_arguments_reach_the_installer_through_the_gate(isolated_config, blender_host,
                                                                     monkeypatch):
    """Blender's startup-shim choice goes through `install`, so it cannot bypass the host gate."""
    from trackball_daemon.config import Config

    cfg = Config().load()
    blender = blender_host("5.1")
    reached = []
    monkeypatch.setattr(blender, "setup",
                        lambda appdef, config, **kwargs: reached.append(kwargs) or (True, "ok"))

    integrations.install(blender, cfg, install_startup=True)

    assert reached == [{"install_startup": True}]


def test_an_unverified_host_version_is_not_blocked(isolated_config, blender_host):
    """Untested is not broken; refusing it would make every new host release un-setuppable."""
    blender = blender_host("5.2")

    assert integrations.compatibility(blender).status == "unverified"
    assert integrations.unsupported_host_warning(blender) is None


@pytest.mark.parametrize("confirmed, expected_override", [(False, None), (True, True)])
def test_the_settings_panel_asks_before_overriding_the_host_gate(
        settings_ui, blender_host, monkeypatch, confirmed, expected_override):
    """The override belongs to a user who read the warning; declining must set up nothing.

    Driven through the real handler rather than asserted against ui.py source, so a dialog whose
    answer was collected and then ignored would fail here.
    """
    from trackball_daemon import ui as ui_module

    blender = blender_host("3.6")
    asked = []
    monkeypatch.setattr(ui_module.messagebox, "askokcancel",
                        lambda title, message, **kwargs: asked.append(message) or confirmed)
    monkeypatch.setattr(ui_module.messagebox, "askyesnocancel",
                        lambda *args, **kwargs: True)
    calls = []
    monkeypatch.setattr(ui_module.integrations, "install",
                        lambda appdef, cfg, **kwargs: calls.append(kwargs) or (True, "ok"))
    monkeypatch.setattr(settings_ui, "_show_integration_dialog",
                        lambda *args, **kwargs: None)

    settings_ui._do_install(blender, tk.BooleanVar(value=False),
                            ttk.Label(settings_ui.win), {"btn": ttk.Button(settings_ui.win)})

    assert asked and "3.6" in asked[0], "the warning must name the detected version"
    if expected_override is None:
        assert calls == [], "declining the warning must not run setup"
    else:
        assert [call.get("allow_unsupported_host") for call in calls] == [expected_override]
