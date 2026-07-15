import json

from trackball_daemon import __main__ as entrypoint
from trackball_daemon.release_smoke import run_release_smoke


def test_release_smoke_loads_profiles_devices_schemas_and_examples():
    result = run_release_smoke()
    assert result["status"] == "ok"
    assert result["profiles"] == {"astrolabe_5way": 11, "keyboard_only": 4}
    assert result["devices"] == ["astrolabe_5way", "xiao3389_3button"]
    assert len(result["schemas"]) == len(result["examples"]) == 3


def test_release_smoke_cli_does_not_acquire_controller_or_start_app(capsys):
    class UnexpectedGuard:
        def acquire(self):
            raise AssertionError("release smoke must not acquire the controller mutex")

    assert entrypoint.main(["--release-smoke"], instance_guard=UnexpectedGuard()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"
