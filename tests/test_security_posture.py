"""Regression checks for the integration security and permission contract."""
from pathlib import Path

from trackball_daemon import onshape_bridge


ROOT = Path(__file__).parents[1]


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_network_listeners_are_loopback_only():
    broker = _source("trackball_daemon/navbroker.py")
    onshape = _source("trackball_daemon/onshape_bridge.py")
    assert 'srv.bind(("127.0.0.1", self.port))' in broker
    assert onshape_bridge.BRIDGE_HOST.startswith("127.")
    assert "srv.bind((self._host, self._port))" in onshape


def test_onshape_trust_is_manual_and_origin_scoped():
    integrations = _source("trackball_daemon/integrations.py")
    bridge = _source("trackball_daemon/onshape_bridge.py")
    assert "certutil -user -addstore" in integrations       # instructions users can copy
    assert "subprocess.run" not in integrations             # no automatic trust command
    assert "_allowed_web_origin(origin)" in bridge
    assert '"Access-Control-Allow-Origin: *"' not in bridge
    assert "BasicConstraints(ca=False" in bridge


def test_autocad_trust_is_scoped_without_disabling_secureload():
    loader = _source("trackball_daemon/autocad_driver.py")
    executable = "\n".join(line for line in loader.splitlines() if not line.lstrip().startswith("#"))
    assert 'SetVariable("TRUSTEDPATHS"' in executable
    assert 'SetVariable("SECURELOAD"' not in executable
    assert "GetActiveObject" in loader
    assert "DispatchEx" not in loader                        # never launches a new AutoCAD


def test_login_startup_is_current_user_and_user_toggled():
    tray = _source("trackball_daemon/tray.py")
    assert "HKEY_CURRENT_USER" in tray
    assert "HKEY_LOCAL_MACHINE" not in tray
    assert 'MenuItem("Start at login"' in tray


def test_release_packages_exclude_interpreter_cache_files():
    pyproject = _source("pyproject.toml")
    manifest = _source("MANIFEST.in")
    assert "[tool.setuptools.exclude-package-data]" in pyproject
    assert '"**/*.pyc"' in pyproject
    assert '"**/*.pyc.*"' in pyproject
    assert "global-exclude __pycache__" in manifest
    assert "global-exclude *.py[cod]" in manifest
    assert "global-exclude *.py[cod].*" in manifest


def test_release_builder_is_onedir_repository_bounded_and_self_verifying():
    builder = _source("tools/build_release.ps1")
    assert "--mode=standalone" in builder
    assert "--mode=onefile" not in builder and "--onefile" not in builder
    assert "OutputDirectory must resolve inside the repository" in builder
    assert "$env:NUITKA_CACHE_DIR = $NuitkaCache" in builder
    assert "--release-smoke" in builder
    assert "Get-FileHash -Algorithm SHA256" in builder


def test_keyboard_observation_is_lazy_pass_through_and_has_no_raw_logging():
    provider = _source("trackball_daemon/input/windows_raw_input.py")
    app = _source("trackball_daemon/app.py")
    security = _source("docs/security.md")

    assert "RIDEV_INPUTSINK" in provider
    assert "RIDEV_NOLEGACY =" not in provider
    assert "def configure(self, required_control_ids)" in provider
    assert "if not expanded:" in provider
    assert "self.input_aggregator.shutdown" in app
    assert "does not log raw keyboard packets" in security
    assert "Keys remain pass-through" in security
    assert "GetAsyncKeyState" in security
    assert "logger." not in provider and "print(" not in provider
