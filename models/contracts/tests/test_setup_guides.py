"""Unit tests for the engine-declared setup guides (owner requirement 2026-08-23).

Two layers: Ollama's own guide, and a UNIVERSAL shape check every registered
adapter that declares a guide must pass -- so a future adapter cannot ship a
half-filled guide the /setup/ page would render as blanks.

MOVED FROM `tools/vision/tests/` (C-58). This module tests `OllamaEngine`,
`ComfyUIEngine`, `ENGINES`, and `SetupGuide` -- all `models.contracts.*` --
and imports nothing from `tools.vision`; it was found alongside the three
modules Task 36's brief named, by the same repo-shape gate
(`foundation/ops/tests/test_column_boundaries.py::
test_a_contract_module_keeps_its_tests_beside_itself`) that pins the
original three, since it fits the finding identically.
"""
from __future__ import annotations

import pytest

from models.contracts.engines import ENGINES
from models.contracts.engines.base import SETUP_PLATFORMS, SetupGuide
from models.contracts.engines.comfyui import ComfyUIEngine
from models.contracts.engines.ollama import OllamaEngine


class TestOllamaSetupGuide:
    def test_covers_every_platform(self):
        guide = OllamaEngine.setup_guide
        assert set(guide.platforms) == set(SETUP_PLATFORMS)
        assert all(guide.platforms[key] for key in SETUP_PLATFORMS)

    def test_every_platform_says_how_to_bind_all_interfaces(self):
        guide = OllamaEngine.setup_guide
        for key in SETUP_PLATFORMS:
            text = " ".join(
                f"{step.title} {step.body} {step.command or ''}" for step in guide.platforms[key]
            )
            assert "OLLAMA_HOST=0.0.0.0" in text

    def test_install_commands_are_the_platform_native_ones(self):
        platforms = OllamaEngine.setup_guide.platforms
        assert any("brew install ollama" in (s.command or "") for s in platforms["macos"])
        assert any("winget install" in (s.command or "") for s in platforms["windows"])
        assert any("ollama.com/install.sh" in (s.command or "") for s in platforms["linux"])

    def test_network_note_explains_the_container_hop(self):
        assert "host.docker.internal" in OllamaEngine.setup_guide.network_note

    def test_models_note_carries_the_pull_shape_and_who_pulls(self):
        note = OllamaEngine.setup_guide.models_note
        assert "ollama pull <model-name>" in note
        assert "never" in note

    def test_verify_path_matches_the_health_check(self):
        assert OllamaEngine.setup_guide.verify_url_path == "/api/tags"

    def test_it_declares_the_capabilities_it_serves(self):
        assert OllamaEngine.serves_capabilities == ("chat", "embeddings", "vision")


class TestComfyUISetupGuide:
    def test_the_models_note_names_every_folder_an_operation_can_ask_for(self):
        note = ComfyUIEngine.setup_guide.models_note
        for folder in ("models/checkpoints/", "models/loras/", "models/upscale_models/"):
            assert folder in note


class TestEveryRegisteredGuideIsComplete:
    """Runs over whatever is registered, so a new adapter is covered the day
    it lands -- no per-engine test to remember to write."""

    @pytest.mark.parametrize("engine", list(ENGINES.values()), ids=lambda e: e.name)
    def test_a_declared_guide_is_fully_filled_in(self, engine):
        guide = getattr(engine, "setup_guide", None)
        if guide is None:
            pytest.skip(f"{engine.name} declares no setup guide")
        assert isinstance(guide, SetupGuide)
        assert guide.summary.strip()
        assert guide.network_note.strip()
        assert guide.models_note.strip()
        assert guide.verify_url_path.startswith("/")
        assert set(guide.platforms) <= set(SETUP_PLATFORMS)
        for steps in guide.platforms.values():
            assert steps
            for step in steps:
                assert step.title.strip()

    @pytest.mark.parametrize("engine", list(ENGINES.values()), ids=lambda e: e.name)
    def test_declared_capabilities_are_platform_vocabulary(self, engine):
        from models.contracts.roles import CAPABILITIES

        for capability in getattr(engine, "serves_capabilities", ()):
            assert capability in CAPABILITIES
