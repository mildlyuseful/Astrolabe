# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Canonical app identity, capability, transport, and focus registry contracts."""
from dataclasses import FrozenInstanceError

import pytest

from trackball_daemon import integrations
from trackball_daemon.app import App
from trackball_daemon.app_registry import (
    APP_BINDING_PROFILES,
    APP_IDS,
    APP_SPECS,
    APP_SPECS_BY_ID,
    FocusKind,
    OnshapeFocusState,
    TransportKind,
    resolve_foreground_context,
)
from trackball_daemon.config import HOST_PROFILE_APP_KEYS


def test_one_immutable_registry_owns_the_supported_app_suite():
    assert APP_IDS == HOST_PROFILE_APP_KEYS
    assert tuple(APP_SPECS_BY_ID) == APP_IDS
    assert tuple(integrations.APPS_BY_KEY) == APP_IDS
    assert tuple(APP_BINDING_PROFILES) == APP_IDS
    assert len(APP_IDS) == len(set(APP_IDS))

    for spec in APP_SPECS:
        assert integrations.APPS_BY_KEY[spec.app_id].spec is spec
        assert APP_BINDING_PROFILES[spec.app_id] is spec.binding_profile
        assert integrations.APPS_BY_KEY[spec.app_id].name == spec.display_name

    with pytest.raises(TypeError):
        APP_SPECS_BY_ID["new_app"] = APP_SPECS[0]
    with pytest.raises(FrozenInstanceError):
        APP_SPECS[0].display_name = "changed"


def test_process_selectors_and_transports_match_existing_routing_contract():
    expected = {
        "fusion360": ("fusion360.exe",), "blender": ("blender.exe",),
        "freecad": ("freecad.exe",), "sketchup": ("sketchup.exe",),
        "unreal": ("unrealeditor.exe", "ue4editor.exe"), "unity": ("unity.exe",),
        "rhino": ("rhino.exe",),
        "solidworks": ("sldworks.exe",), "autocad": ("acad.exe",),
        "onshape": ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
                    "vivaldi.exe"),
    }
    assert {spec.app_id: tuple(selector.executable for selector in spec.process_selectors)
            for spec in APP_SPECS} == expected
    assert APP_SPECS_BY_ID["solidworks"].transport is TransportKind.SOLIDWORKS_COM
    assert APP_SPECS_BY_ID["onshape"].transport is TransportKind.ONSHAPE_BRIDGE
    assert all(APP_SPECS_BY_ID[key].transport is TransportKind.BROKER
               for key in APP_IDS if key not in {"solidworks", "onshape"})
    assert not hasattr(App, "_APP_PROC_HINTS")


def test_desktop_process_resolution_is_case_and_path_insensitive():
    context = resolve_foreground_context(r"C:\Program Files\Blender\BLENDER.EXE")
    assert context.app_id == "blender"
    assert context.process_name == "blender.exe"
    assert context.focus_kind is FocusKind.DESKTOP_PROCESS
    assert context.onshape_state is OnshapeFocusState.DISCONNECTED


@pytest.mark.parametrize("process_name", [
    "Unity Hub.exe",
    "UnityCrashHandler64.exe",
    "FusionLauncher.exe",
    "Fusion360Launcher.exe",
    "acadlt.exe",
    "my-acad.exe",
    "UnrealEditor-Cmd.exe",
    "freecadcmd.exe",
    "blender-launcher.exe",
    "not-really-sldworks.exe",
])
def test_similarly_named_desktop_processes_do_not_route(process_name):
    assert resolve_foreground_context(process_name).app_id is None


def test_onshape_connection_and_foreground_are_explicit_independent_states():
    disconnected = resolve_foreground_context("chrome.exe", onshape_connected=False)
    assert disconnected.app_id is None
    assert disconnected.onshape_state is OnshapeFocusState.DISCONNECTED

    background = resolve_foreground_context("notepad.exe", onshape_connected=True)
    assert background.app_id is None
    assert background.onshape_state is OnshapeFocusState.CONNECTED_BACKGROUND

    unfocused_browser = resolve_foreground_context(
        "msedge.exe", onshape_connected=True, onshape_viewport_focused=False)
    assert unfocused_browser.app_id is None
    assert unfocused_browser.onshape_state is OnshapeFocusState.CONNECTED_BACKGROUND

    foreground = resolve_foreground_context(
        "msedge.exe", onshape_connected=True, onshape_viewport_focused=True)
    assert foreground.app_id == "onshape"
    assert foreground.focus_kind is FocusKind.ONSHAPE_BROWSER
    assert foreground.onshape_state is OnshapeFocusState.CONNECTED_FOREGROUND

    other_host = resolve_foreground_context(
        "sldworks.exe", onshape_connected=True, onshape_viewport_focused=True)
    assert other_host.app_id == "solidworks"
    assert other_host.onshape_state is OnshapeFocusState.CONNECTED_BACKGROUND

    similarly_named_browser_helper = resolve_foreground_context(
        "chrome_proxy.exe", onshape_connected=True, onshape_viewport_focused=True)
    assert similarly_named_browser_helper.app_id is None
    assert similarly_named_browser_helper.onshape_state is OnshapeFocusState.CONNECTED_BACKGROUND


def test_supported_navigation_modes_are_capability_owned():
    rich = {"blender", "sketchup", "unreal", "unity"}
    object_hosts = {"blender", "unreal", "unity"}
    for spec in APP_SPECS:
        expected = (("orbit", "fly", "walk", "object")
                    if spec.app_id in object_hosts else
                    ("orbit", "fly", "walk") if spec.app_id in rich else ("orbit",))
        assert spec.supported_modes == expected
        assert ("object_manipulation" in spec.capabilities) == (spec.app_id in object_hosts)
        assert spec.binding_profile.rich_actions is (spec.app_id in rich)
