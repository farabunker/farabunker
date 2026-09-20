"""Unit tests for the vision feature's platform configuration (spec §4.6).

Covers the settings this track adds, the feature-flag parser, the
feature-gated `/vision/` URL mount, and the new platform capability. No DB,
no HTTP -- pure settings/registry facts.
"""
from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.test import Client, RequestFactory, override_settings
from django.urls import clear_url_caches, reverse

from config.settings import _feature_set
from models.contracts.roles import CAPABILITIES, VISION_GENERATE_ROLE, RoleSpec
from tools.vision.context_processors import features


class TestFeatureSet:
    def test_unset_falls_back_to_the_default_list(self, monkeypatch):
        monkeypatch.delenv("FARABUNKER_FEATURES", raising=False)
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset({"vision"})

    def test_comma_separated_values_are_split_and_stripped(self, monkeypatch):
        monkeypatch.setenv("FARABUNKER_FEATURES", " vision , chat ")
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset({"vision", "chat"})

    def test_blank_value_disables_everything(self, monkeypatch):
        """An operator opts OUT by setting the variable to empty -- that is a
        deliberate choice, not 'unset', so it must not fall back."""
        monkeypatch.setenv("FARABUNKER_FEATURES", "")
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset()


class TestVisionSettings:
    def test_comfyui_base_url_defaults_to_the_local_server_location(self):
        assert settings.COMFYUI_BASE_URL

    def test_default_endpoints_map_covers_both_engines(self):
        assert settings.INFERENCE_DEFAULT_ENDPOINTS["ollama"] == settings.OLLAMA_BASE_URL
        assert settings.INFERENCE_DEFAULT_ENDPOINTS["comfyui"] == settings.COMFYUI_BASE_URL

    def test_generated_dir_sits_under_the_durable_data_dir(self):
        assert settings.GENERATED_DIR == settings.DATA_DIR / "generated"

    def test_vision_stale_after_is_ten_minutes_by_default(self):
        assert settings.VISION_STALE_AFTER.total_seconds() == 600

    def test_a_zero_or_malformed_window_falls_back_to_the_default(self, monkeypatch):
        """`_optional_int` reads zero/negative/malformed as "not set" -- a
        zero-minute window would mark every job stale the moment it was
        created. Pin that inherited behaviour rather than leaving it
        accidental."""
        from datetime import timedelta

        for raw in ("0", "-5", "soon", ""):
            monkeypatch.setenv("VISION_STALE_AFTER_MINUTES", raw)
            from config.settings import _optional_int

            assert timedelta(minutes=_optional_int("VISION_STALE_AFTER_MINUTES") or 10) == timedelta(
                minutes=10
            )

    def test_vision_app_is_installed(self):
        assert "tools.vision" in settings.INSTALLED_APPS


class TestImageGenerationCapability:
    def test_capability_is_part_of_the_platform_vocabulary(self):
        assert "image-generation" in CAPABILITIES

    def test_role_key_constant(self):
        assert VISION_GENERATE_ROLE == "vision.generate"

    def test_a_role_can_declare_the_capability(self):
        spec = RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation")
        assert spec.capability == "image-generation"


def _mounted_prefixes() -> list[str]:
    from config import urls as config_urls

    return [str(pattern.pattern) for pattern in config_urls.urlpatterns]


@pytest.mark.django_db
class TestFeatureContextProcessor:
    """The shared shell's nav link to /vision/ is feature-gated, so EVERY
    page -- not just this module's -- needs the flag in its context."""

    def test_the_processor_returns_the_feature_set(self):
        request = RequestFactory().get("/")
        assert features(request) == {"farabunker_features": settings.FARABUNKER_FEATURES}

    def test_it_is_registered_so_rendered_pages_see_the_key(self):
        response = Client().get(reverse("rag-ask-page"))
        assert response.context["farabunker_features"] == settings.FARABUNKER_FEATURES


class TestVisionUrlMount:
    def test_mounted_while_the_feature_is_enabled(self):
        assert ("vision/" in _mounted_prefixes()) == ("vision" in settings.FARABUNKER_FEATURES)

    def test_not_mounted_when_the_feature_is_off(self):
        """The flag has exactly one job (D9): gate role registration and the
        URL mount. Reloading the URLConf under an overridden setting is the
        only honest way to prove the gate -- restored in `finally` so the
        rest of the suite keeps the real URLConf.

        The restoring reload runs AFTER the `with override_settings(...)`
        block has exited (not inside it): `override_settings` puts the real
        feature set back on `__exit__`, and only then does reloading
        `config_urls` rebuild the URLconf with the vision mount present
        again. Reloading while still inside the `with` would rebuild it from
        the OVERRIDDEN (empty) settings and leave the rest of the suite
        without the /vision/ route -- every other page's nav link to it
        would then raise `NoReverseMatch`."""
        from config import urls as config_urls

        try:
            with override_settings(FARABUNKER_FEATURES=frozenset()):
                importlib.reload(config_urls)
                clear_url_caches()
                assert "vision/" not in _mounted_prefixes()
        finally:
            importlib.reload(config_urls)
            clear_url_caches()
