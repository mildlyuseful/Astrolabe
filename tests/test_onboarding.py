# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

from trackball_daemon.app_registry import APP_IDS_BY_TIER, SupportTier
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import InputAggregator
from trackball_daemon.onboarding import OnboardingModel, STEP_IDS
from trackball_daemon.runtime_state import ConfigRuntimeBaseResolver, RuntimeStore


def _app(tmp_path):
    config = ConfigStore(tmp_path / "config.json").load()
    return SimpleNamespace(
        config=config,
        input_aggregator=InputAggregator(),
        runtime=RuntimeStore(ConfigRuntimeBaseResolver(config)),
        status_text=lambda: "disconnected",
        is_connected=lambda: False,
        runtime_health_summary=lambda: "waiting: navigation-broker",
    )


def test_onboarding_has_the_complete_first_run_sequence():
    assert STEP_IDS == (
        "welcome", "device", "input", "applications", "setup", "navigation", "finish")


def test_device_and_keyboard_skip_project_existing_config_authority(tmp_path):
    app = _app(tmp_path)
    model = OnboardingModel(app)

    assert model.device_identity()["name"]
    assert model.device_identity()["connected"] is False
    model.use_keyboard_only()
    assert app.config.snapshot().input_profile == "keyboard_only"


def test_applications_are_supported_first_and_keep_release_tier_separate(tmp_path, monkeypatch):
    app = _app(tmp_path)
    model = OnboardingModel(app)
    monkeypatch.setattr(
        "trackball_daemon.onboarding.integrations.status_line",
        lambda appdef: f"{appdef.key} detected")

    rows = model.applications()
    expected_ids = (
        APP_IDS_BY_TIER[SupportTier.SUPPORTED] +
        APP_IDS_BY_TIER[SupportTier.EXPERIMENTAL])

    assert tuple(row.app_id for row in rows) == expected_ids
    assert tuple(row.tier for row in rows[:len(APP_IDS_BY_TIER[SupportTier.SUPPORTED])]) == (
        SupportTier.SUPPORTED.value,) * len(APP_IDS_BY_TIER[SupportTier.SUPPORTED])
    assert all(row.status.endswith("detected") for row in rows)


def test_input_and_navigation_pages_are_passive_snapshot_consumers(tmp_path):
    model = OnboardingModel(_app(tmp_path))

    assert model.input_state()["pressed"] == ()
    navigation = model.navigation_state()
    assert navigation["input_mode"] == model.app.runtime.snapshot().effective_input_mode
    assert navigation["focused_app"] == "none"
