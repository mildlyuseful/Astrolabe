from pathlib import Path


WORKFLOW = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
RELEASE_BUILD = Path("tools/build_release.ps1").read_text(encoding="utf-8")


def test_ci_builds_and_smokes_wheel_outside_checkout():
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in WORKFLOW
    assert 'version: "0.11.28"' in WORKFLOW
    assert "uv sync --locked --all-extras --python 3.9" in WORKFLOW
    assert "uv build --out-dir build/ci-python" in WORKFLOW
    assert "uv export --locked --no-dev --extra onshape --no-emit-project" in WORKFLOW
    assert 'Join-Path $env:RUNNER_TEMP "astrolabe-wheel-smoke"' in WORKFLOW
    assert "pip install --require-hashes -r $Requirements" in WORKFLOW
    assert "pip install --no-deps $Wheel" in WORKFLOW
    assert '"$Venv\\Scripts\\python.exe" -m trackball_daemon --release-smoke' in WORKFLOW
    assert '"$Venv\\Scripts\\python.exe" -m trackball_daemon.validate_bindings' in WORKFLOW
    assert '$SbomTool environment "$Venv\\Scripts\\python.exe"' in WORKFLOW
    assert "--output-reproducible --output-file $Sbom" in WORKFLOW
    assert "tools/finalize_sbom.py" in WORKFLOW
    assert "--name \"trackball-daemon\" --version $InstalledVersion" in WORKFLOW


def test_ci_pins_and_compiles_both_validation_firmware_targets():
    assert 'version: "1.5.0"' in WORKFLOW
    assert "pdcook/nRFMicro-Arduino-Core/3dab6477754d9b28053fe36b06c718cde6e93d3f" in WORKFLOW
    assert '"nRFMicro-like-Boards:nrf52@1.0.0"' in WORKFLOW
    assert '"Seeeduino:nrf52@1.1.12"' in WORKFLOW
    assert '"nRFMicro-like-Boards:nrf52:supermini:softdevice=s140v6,debug=l0"' in WORKFLOW
    assert '"Seeeduino:nrf52:xiaonRF52840:softdevice=s140v6,debug=l0"' in WORKFLOW
    assert "Report firmware artifact sizes" in WORKFLOW
    assert "actions/upload-artifact@v4" in WORKFLOW


def test_windows_release_includes_dynamic_winrt_projection_package():
    assert "--include-package=winrt" in RELEASE_BUILD
