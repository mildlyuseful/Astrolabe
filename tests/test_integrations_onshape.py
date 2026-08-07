# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Onshape setup: what the one-time trust steps have to hand the user, and in what form."""

from trackball_daemon import integrations, onshape_bridge
from trackball_daemon.config import Config


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
    """Chromium's Local Network Access permission re-asks on every request until it is remembered,
    which reads as the prompt flickering. The steps say to remember it rather than just allow."""
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["onshape"]

    _ok, msg, _copyables = integrations.install(appdef, cfg)

    assert "Remember my choice for this site" in msg


def test_pointer_userscript_rate_is_decoupled_from_pointer_events():
    """The userscript posted once per mousemove -- 120+ cross-origin requests a second, each its
    own TLS handshake, and before the site holds the Local Network Access permission, each one a
    fresh prompt. A timer owns the transport now; movement only updates local state."""
    script = onshape_bridge.pointer_userscript_source()

    assert script.count("fetch(ENDPOINT") == 1
    assert "setInterval(send, SEND_MS)" in script
    assert 'window.addEventListener("mousemove", observe' in script
    # The resend floor has to stay inside the bridge's staleness window or a still cursor expires.
    assert "var REFRESH_MS = 300;" in script
    assert onshape_bridge._POINTER_TTL * 1000 > 300
