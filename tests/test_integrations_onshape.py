# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Onshape setup: what the one-time trust steps have to hand the user, and in what form."""

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from trackball_daemon import integrations, onshape_bridge
from trackball_daemon.config import Config


def test_userscript_browser_lifecycle():
    node = shutil.which("node")
    assert node, "Install Node.js to run the Onshape userscript lifecycle tests."
    result = subprocess.run(
        [node, str(Path(__file__).with_name("onshape_userscript.cjs")),
         str(onshape_bridge._POINTER_TTL * 1000)],
        input=onshape_bridge.pointer_userscript_source(), text=True,
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_setup_hands_back_the_trust_steps_as_copyable_payloads(isolated_config):
    """Trusting the cert is the user's job, so setup has to make its two inputs reachable.

    Both are commands the user must run or visit exactly; retyping a certutil line with a full
    certificate path, or an IP and port, off a dialog is where this step gets abandoned."""
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["onshape"]

    ok, msg, copyables = integrations.install(appdef, cfg)

    assert ok is True
    payloads = dict((label, text) for label, text in copyables)
    assert payloads["Copy bridge URL"] == onshape_bridge.BRIDGE_URL
    assert payloads["Copy trust command"].startswith("certutil -user -addstore Root ")
    assert payloads["Copy trust command"].endswith('.pem"')
    # Every payload has to appear in the message too -- the dialog's text is what the user reads
    # when the clipboard is unavailable, and a button that copies something not shown is a guess.
    for text in payloads.values():
        assert text in msg


def test_setup_names_the_port_the_bridge_actually_binds(isolated_config):
    """The address and port are configurable, so the steps are formatted from the constants."""
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["onshape"]

    _ok, msg, _copyables = integrations.install(appdef, cfg)

    assert onshape_bridge.BRIDGE_URL in msg
    assert onshape_bridge.BRIDGE_URL == "https://%s:%d" % (
        onshape_bridge.BRIDGE_HOST, onshape_bridge.BRIDGE_PORT)


def test_setup_explains_the_local_network_prompt(isolated_config):
    """Site permission is separate from TLS trust and precedes cursor reporting."""
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["onshape"]

    _ok, msg, _copyables = integrations.install(appdef, cfg)

    assert "Remember my choice for this site" in msg
    assert "if offered" in msg
    assert "separate from certificate trust" in msg
    assert "waits for Onshape's grant" in msg


def test_installed_userscript_can_be_identified_and_updated():
    """The script runs from the browser extension, so pulling the repo cannot replace it.

    An installed copy is invisible and immortal without these two things: a version the daemon can
    compare against, and an update URL the extension can poll. Their absence is why a fixed script
    kept behaving like the old one after an update."""
    script = onshape_bridge.pointer_userscript_source()

    assert "// @version      %s" % onshape_bridge.USERSCRIPT_VERSION in script
    assert onshape_bridge.USERSCRIPT_VERSION != "0.1"        # was a literal that never moved
    assert "// @updateURL    %s" % onshape_bridge.POINTER_SCRIPT_URL in script
    assert "// @downloadURL  %s" % onshape_bridge.POINTER_SCRIPT_URL in script
    # It also has to say who it is on every sample, or the daemon can only guess.
    assert 'var VERSION = "%s";' % onshape_bridge.USERSCRIPT_VERSION in script
    assert "v: VERSION" in script
    assert "__ASTROLABE_" not in script                      # every placeholder substituted


def test_a_stale_userscript_is_reported_rather_than_silently_accepted(monkeypatch):
    """The daemon's logger does not propagate to root, so this records at the call site."""
    lines = []
    monkeypatch.setattr(onshape_bridge, "get_logger",
                        lambda: SimpleNamespace(info=lambda msg, *a: lines.append(msg % a)))
    onshape_bridge._USERSCRIPT_VERSION_SEEN.clear()
    try:
        onshape_bridge._set_page_pointer(0.0, 0.0, True, "0.1")
        onshape_bridge._set_page_pointer(0.1, 0.1, True, "0.1")   # same version, one message
        stale = [line for line in lines if "userscript reports version" in line]
        assert len(stale) == 1
        assert "'0.1'" in stale[0] and onshape_bridge.USERSCRIPT_VERSION in stale[0]

        lines.clear()
        onshape_bridge._set_page_pointer(0.0, 0.0, True, onshape_bridge.USERSCRIPT_VERSION)
        assert not [line for line in lines if "userscript reports version" in line]
    finally:
        onshape_bridge._USERSCRIPT_VERSION_SEEN.clear()


def test_pointer_status_reports_both_versions():
    """`GET /trackball/pointer` is the check the install steps point at, so it has to show the
    comparison rather than leave the user to infer it from behavior."""
    onshape_bridge._set_page_pointer(0.0, 0.0, True, "0.1")
    with onshape_bridge._PAGE_POINTER_LOCK:
        assert onshape_bridge._PAGE_POINTER["version"] == "0.1"
    assert onshape_bridge.USERSCRIPT_VERSION != "0.1"


def test_userscript_is_published_under_the_suffix_managers_recognise():
    """A userscript manager decides whether a URL is installable by its `.user.js` suffix.

    Served from a plain `.js` URL, Firefox renders the source and Violentmonkey's Install from URL
    has nothing to act on, so the install path the steps recommend cannot work."""
    assert onshape_bridge.POINTER_SCRIPT_URL.endswith("/trackball/pointer.user.js")
    assert onshape_bridge.POINTER_SCRIPT_LEGACY_URL.endswith("/trackball/pointer.js")
    # The metadata block must advertise the recognised URL, or the update check never fires.
    script = onshape_bridge.pointer_userscript_source()
    assert "// @updateURL    %s" % onshape_bridge.POINTER_SCRIPT_URL in script
    assert "pointer.user.js" in script
