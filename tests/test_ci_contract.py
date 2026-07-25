# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest


WORKFLOW = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
RELEASE_WORKFLOW = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
RELEASE_BUILD = Path("tools/build_release.ps1").read_text(encoding="utf-8")


def test_ci_builds_and_smokes_wheel_outside_checkout():
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in WORKFLOW
    assert 'version: "0.11.28"' in WORKFLOW
    assert "uv sync --locked --all-extras --python 3.13" in WORKFLOW
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
    assert "--name \"astrolabe-daemon\" --version $InstalledVersion" in WORKFLOW


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


def test_installed_wheel_notices_are_verified_outside_the_checkout():
    """Declaring license files is not the same as shipping them."""
    assert "tools/verify_installed_metadata.py" in WORKFLOW
    assert "$VerifyInstalledMetadata" in WORKFLOW
    assert "Installed distribution metadata verification failed." in WORKFLOW


def test_bundled_component_notices_are_audited_against_a_real_release_runtime():
    """The gate must run against a synced runtime environment, not the all-extras build env."""
    assert "uv sync --locked --no-editable --extra onshape" in WORKFLOW
    assert "tools/audit_notices.py --environment" in WORKFLOW
    assert "Bundled-component notice audit failed." in WORKFLOW
    assert "tools/audit_notices.py --environment $RuntimePython" in RELEASE_BUILD


def test_ordinary_ci_compiles_the_tools_it_also_runs():
    """A syntax error in tools/ would otherwise only surface when someone ran a release build."""
    assert "compileall -q trackball_daemon tests tools" in WORKFLOW


def test_ordinary_ci_builds_and_verifies_the_unsigned_onedir():
    """The packaged artifact is what users run, and its build path runs nowhere else."""
    assert ".\\tools\\build_release.ps1" in WORKFLOW
    assert "tools/verify_release_manifest.py" in WORKFLOW
    assert "actions/upload-artifact@v4" in WORKFLOW
    assert "name: unsigned-onedir" in WORKFLOW
    assert "claiming to be signed" in WORKFLOW


def test_the_onedir_build_is_kept_off_pull_requests_but_stays_reachable():
    """A ten-minute Windows job per proposal costs more than it finds; it gates entry to main instead."""
    assert "if: github.event_name != 'pull_request'" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW, "it must still be runnable against a branch on demand"


def test_superseded_runs_are_cancelled():
    """Otherwise a rapid series of pushes keeps every intermediate run alive to completion."""
    assert "cancel-in-progress: true" in WORKFLOW
    assert "group: ci-${{ github.workflow }}-${{ github.ref }}" in WORKFLOW


@pytest.mark.parametrize("workflow, name", [(WORKFLOW, "ci.yml")])
def test_ordinary_ci_never_receives_signing_credentials(workflow, name):
    """The cheapest way to leak a certificate is to add one secret to the workflow everyone edits."""
    assert "secrets." not in workflow, name
    assert "environment:" not in workflow, name


# --- the protected release workflow --------------------------------------------------------------

def test_the_release_workflow_starts_from_an_exact_revision():
    assert "workflow_dispatch:" in RELEASE_WORKFLOW
    assert 'tags: ["v*"]' in RELEASE_WORKFLOW
    assert "ref: ${{ inputs.revision || github.ref }}" in RELEASE_WORKFLOW


def test_the_release_workflow_refuses_a_tag_that_disagrees_with_the_package():
    assert "does not match package version" in RELEASE_WORKFLOW


def test_every_gate_runs_before_any_artifact_is_produced():
    """A suite failure discovered after signing has already spent the certificate on bad bytes."""
    assert RELEASE_WORKFLOW.index("python -m pytest -q") < RELEASE_WORKFLOW.index(
        "build_release.ps1")
    assert "needs: gates" in RELEASE_WORKFLOW


def test_the_release_build_requires_a_clean_revision():
    assert "build_release.ps1 -RequireCleanRevision" in RELEASE_WORKFLOW


def test_signing_credentials_are_scoped_to_one_environment():
    assert "environment: release" in RELEASE_WORKFLOW


def test_a_public_release_cannot_be_produced_without_signing():
    """Failing closed is the difference between "not signed yet" and "shipped unsigned"."""
    assert "Authenticode signing is not implemented" in RELEASE_WORKFLOW
    assert 'if ($env:CHANNEL -ne "public")' in RELEASE_WORKFLOW
    assert "A public release manifest must record signatures." in RELEASE_WORKFLOW


def test_the_channel_is_resolved_from_the_version_not_from_an_input():
    assert "release_channel(__version__)" in RELEASE_WORKFLOW
    assert "Manifest channel" in RELEASE_WORKFLOW      # and cross-checked against the manifest


def test_the_manifest_is_verified_again_after_every_build_step():
    assert "tools/verify_release_manifest.py" in RELEASE_WORKFLOW
    assert "revision_describes_artifact" in RELEASE_WORKFLOW


def test_the_workflow_only_ever_creates_a_draft():
    """An environment with no required reviewers grants no approval, so publishing stays manual."""
    assert "--draft" in RELEASE_WORKFLOW
    assert "gh release edit" not in RELEASE_WORKFLOW
    assert "--draft=false" not in RELEASE_WORKFLOW
    assert "publish" in RELEASE_WORKFLOW.lower()


def test_only_the_drafting_job_can_write_to_the_repository():
    assert RELEASE_WORKFLOW.count("contents: write") == 1
    assert "permissions:\n  contents: read" in RELEASE_WORKFLOW


# --- workflow validity ----------------------------------------------------------------------------

# A workflow whose expressions reference a context that is not available at that level is rejected by
# GitHub before any job starts, so there is no job to carry the failure and nothing in the run to read.
# That is how this repository's CI stopped running for two days without anyone noticing. `runner` is the
# easy one to get wrong: it exists for a step's `env` but not for a job's.
JOB_LEVEL_FORBIDDEN_CONTEXTS = ("runner", "steps", "job", "env", "hashFiles")


def _job_level_env_blocks(workflow):
    """Yield each job-level ``env:`` block's body, which is indented four spaces under the job."""
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line != "    env:":
            continue
        body = []
        for candidate in lines[index + 1:]:
            if candidate.strip() and not candidate.startswith("      "):
                break
            body.append(candidate)
        yield "\n".join(body)


@pytest.mark.parametrize("workflow, name", [(WORKFLOW, "ci.yml"), (RELEASE_WORKFLOW, "release.yml")])
def test_job_level_env_uses_no_unavailable_context(workflow, name):
    for block in _job_level_env_blocks(workflow):
        for context in JOB_LEVEL_FORBIDDEN_CONTEXTS:
            assert f"{context}." not in block, (
                f"{name}: a job-level env cannot reference {context!r}; GitHub rejects the whole "
                f"workflow file and no job runs at all:\n{block}")


@pytest.mark.parametrize("workflow, name", [(WORKFLOW, "ci.yml"), (RELEASE_WORKFLOW, "release.yml")])
def test_workflows_parse_as_yaml_with_the_expected_jobs(workflow, name):
    """Unparseable YAML fails the same silent way an unavailable context does: no job, no message."""
    import yaml

    parsed = yaml.safe_load(workflow)

    assert parsed["jobs"], name
    for job_name, job in parsed["jobs"].items():
        assert job.get("runs-on"), f"{name}: {job_name} has no runner"
        assert job.get("steps"), f"{name}: {job_name} has no steps"
