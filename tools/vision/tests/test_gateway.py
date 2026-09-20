"""Unit tests for gateway.get_image_generator (spec §4.5).

Uses real DB rows (a registered connection + role binding) so the whole
resolve -> get_engine -> build path runs; no HTTP happens until the
generator is actually used.
"""
from __future__ import annotations

import pytest

from models.registry.models import ModelConnection, RoleBinding
from models.contracts.engines.comfyui import ComfyUIGenerator
from models.contracts.gateway import get_image_generator
from models.contracts.roles import VISION_GENERATE_ROLE
from tools.vision.tests._helpers import clear_bindings


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind(engine: str, model_id: str, endpoint: str, config=None) -> ModelConnection:
    connection = ModelConnection.objects.create(
        name=f"{engine} {model_id}", engine=engine, endpoint=endpoint,
        model_id=model_id, capabilities=["image-generation"], config=config,
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    return connection


@pytest.mark.django_db
class TestGetImageGenerator:
    def test_builds_the_bound_engine_generator(self):
        _bind("comfyui", "sdxl.safetensors", "http://comfy.local:8188")

        generator = get_image_generator()

        assert isinstance(generator, ComfyUIGenerator)
        assert generator.model_id == "sdxl.safetensors"
        assert generator.endpoint == "http://comfy.local:8188"

    def test_connection_config_reaches_the_generator(self):
        _bind("comfyui", "flux.safetensors", "http://comfy.local:8188", config={"vae": "ae.safetensors"})

        assert get_image_generator().config == {"vae": "ae.safetensors"}

    def test_unbound_role_raises_the_resolver_error(self):
        with pytest.raises(ValueError, match="No inference binding resolved"):
            get_image_generator()

    def test_engine_that_cannot_generate_images_says_so(self):
        _bind("ollama", "llama3.1:8b", "http://localhost:11434")

        with pytest.raises(ValueError, match="cannot generate images"):
            get_image_generator()


@pytest.mark.django_db
class TestGetImageGeneratorFor:
    """`get_image_generator_for` is the explicit-binding counterpart to
    `get_llm_for`: it builds from a `ResolvedModel` a caller already holds,
    never from the role registry."""

    def test_builds_from_an_explicit_binding_without_resolving_a_role(self):
        from unittest.mock import patch

        from models.contracts.bindings import ResolvedModel
        from models.contracts.engines import ENGINES
        from models.contracts.gateway import get_image_generator_for
        from tools.vision.tests._helpers import StubEngine

        engine = StubEngine()
        resolved = ResolvedModel(
            engine="stubengine",
            model_id="recorded.safetensors",
            endpoint="http://recorded:9999",
            config={"unet": "flux.safetensors"},
        )
        with patch.dict(ENGINES, {"stubengine": engine}), patch(
            "models.contracts.gateway.resolve"
        ) as never_resolved:
            generator = get_image_generator_for(resolved)

        assert never_resolved.call_count == 0
        assert generator is engine.generator
        assert engine.built == [("recorded.safetensors", "http://recorded:9999", {"unet": "flux.safetensors"})]

    def test_an_engine_that_cannot_generate_images_says_so(self):
        """`test_engine_that_cannot_generate_images_says_so` (already in this
        module) reaches this `ValueError` through the ROLE path. This reaches
        it through the explicit-binding path, which is where the check now
        lives and whose message this task reworded -- two entry points, one
        guarantee, and the wording is pinned at the entry point that owns it."""
        from unittest.mock import patch

        from models.contracts.bindings import ResolvedModel
        from models.contracts.engines import ENGINES
        from models.contracts.gateway import get_image_generator_for

        class TextOnly:
            name = "textonly"

        resolved = ResolvedModel(engine="textonly", model_id="m", endpoint="http://x:1")
        with patch.dict(ENGINES, {"textonly": TextOnly()}):
            with pytest.raises(ValueError, match="cannot generate images"):
                get_image_generator_for(resolved)
