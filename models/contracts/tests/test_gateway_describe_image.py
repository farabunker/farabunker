"""Unit tests for `models/contracts/gateway.py::describe_image` -- the
shared "ask a vision-capable model about an image file" mechanism
(vision-describes-its-own-output task, fix round item 5).

`models/contracts/gateway.py`'s OTHER functions (`get_llm`, `get_llm_for`,
...) have no dedicated test file in this package -- they are exercised
through their consuming columns' own suites (`tools/rag`, `tools/vision`),
the established convention here. `describe_image` gets its own file
anyway: it is a NEW shared mechanism two columns call (today one,
`tools.vision.services.describe_output` -- `tools/rag` converges onto it
in a later, separate change), and a shared mechanism's own contract
belongs with the mechanism, not re-proven once per caller's mock.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from models.contracts import gateway
from models.contracts.bindings import ResolvedModel


def _resolved() -> ResolvedModel:
    return ResolvedModel(
        engine="stubengine", model_id="describer.gguf", endpoint="http://stub:9999",
    )


class TestDescribeImage:
    def test_the_prompt_precedes_the_image_and_the_answer_is_stripped(self, tmp_path):
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        fake_llm = MagicMock()
        fake_llm.chat.return_value = SimpleNamespace(
            message=SimpleNamespace(content="  a lighthouse.  ")
        )

        with patch("models.contracts.gateway.resolve", return_value=_resolved()), \
             patch(
                 "models.contracts.gateway.get_llm_for", return_value=fake_llm
             ) as get_llm_for_mock:
            text = gateway.describe_image(
                "rag.extract", image_path, "describe it", request_timeout=5.0,
            )

        assert text == "a lighthouse."
        get_llm_for_mock.assert_called_once()
        assert get_llm_for_mock.call_args.kwargs["request_timeout"] == 5.0
        # Prompt block BEFORE the image block -- a model reading its
        # input in order sees the instruction before the pixels.
        message = fake_llm.chat.call_args[0][0][0]
        assert message.blocks[0].text == "describe it"
        assert str(message.blocks[1].path) == str(image_path)

    def test_a_blank_answer_is_the_empty_string(self, tmp_path):
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"x")
        fake_llm = MagicMock()
        fake_llm.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="   "))

        with patch("models.contracts.gateway.resolve", return_value=_resolved()), \
             patch("models.contracts.gateway.get_llm_for", return_value=fake_llm):
            text = gateway.describe_image("rag.extract", image_path, "describe it")

        assert text == ""

    def test_a_none_content_is_the_empty_string(self, tmp_path):
        """`content or ""` guards only a `None` content -- some engines
        return that for a truly blank answer, rather than `""`."""
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"x")
        fake_llm = MagicMock()
        fake_llm.chat.return_value = SimpleNamespace(message=SimpleNamespace(content=None))

        with patch("models.contracts.gateway.resolve", return_value=_resolved()), \
             patch("models.contracts.gateway.get_llm_for", return_value=fake_llm):
            text = gateway.describe_image("rag.extract", image_path, "describe it")

        assert text == ""

    def test_request_timeout_defaults_to_none_and_is_never_invented(self, tmp_path):
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"x")
        fake_llm = MagicMock()
        fake_llm.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="x"))

        with patch("models.contracts.gateway.resolve", return_value=_resolved()), \
             patch(
                 "models.contracts.gateway.get_llm_for", return_value=fake_llm
             ) as get_llm_for_mock:
            gateway.describe_image("rag.extract", image_path, "describe it")

        assert get_llm_for_mock.call_args.kwargs["request_timeout"] is None

    def test_it_resolves_the_role_it_was_given(self, tmp_path):
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"x")
        fake_llm = MagicMock()
        fake_llm.chat.return_value = SimpleNamespace(message=SimpleNamespace(content="x"))

        with patch(
            "models.contracts.gateway.resolve", return_value=_resolved()
        ) as resolve_mock, patch("models.contracts.gateway.get_llm_for", return_value=fake_llm):
            gateway.describe_image("rag.extract", image_path, "describe it")

        resolve_mock.assert_called_once_with("rag.extract")

    def test_a_raising_resolve_propagates_uncaught(self, tmp_path):
        """MECHANISM, not policy -- `describe_image` has no catch-and-
        degrade contract of its own; that is every caller's own job
        (`tools.vision.services.describe_output`'s non-fatal wrapper)."""
        image_path = tmp_path / "out.png"
        image_path.write_bytes(b"x")

        with patch("models.contracts.gateway.resolve", side_effect=ValueError("unbound")):
            with pytest.raises(ValueError):
                gateway.describe_image("rag.extract", image_path, "describe it")
