"""Unit tests for tools/rag/extract.py -- the one-image-one-call vision
extraction seam (media-into-RAG plan T8).

`llm` is always a `MagicMock` here -- no real network call, no real model.
`extract_image_text` builds a real `ChatMessage`/`TextBlock`/`ImageBlock`
(`llama_index.core.llms`) and calls `llm.chat([...])`; these tests assert on
the CONSTRUCTED message's own shape (role, block order, block contents),
not merely that some call happened.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole

from models.contracts.engines.base import GenerationRejected
from models.contracts.roles import RAG_EXTRACT_ROLE
from tools.rag import extract


def _chat_response(content: str | None) -> ChatResponse:
    return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content=content))


class TestExtractImageText:
    def test_builds_one_user_message_with_prompt_then_image_blocks(self, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"fake png bytes")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("hello world")

        result = extract.extract_image_text(image_path, llm=mock_llm)

        assert result == "hello world"
        mock_llm.chat.assert_called_once()
        (messages,), _ = mock_llm.chat.call_args
        assert len(messages) == 1
        message = messages[0]
        assert message.role == MessageRole.USER
        assert len(message.blocks) == 2
        text_block, image_block = message.blocks
        # Prompt block first, image block second -- the instruction reads
        # before the pixels it applies to.
        assert text_block.text == extract.EXTRACTION_PROMPT
        assert image_block.path == image_path

    def test_verbatim_storage_only_outer_whitespace_stripped(self, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("  Line one\nLine two  ")

        result = extract.extract_image_text(image_path, llm=mock_llm)

        # Verbatim storage: no rewriting of the model's own words -- only
        # leading/trailing whitespace is stripped, the interior newline
        # survives untouched.
        assert result == "Line one\nLine two"

    def test_whitespace_only_response_becomes_empty_string(self, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("   \n  \t ")

        assert extract.extract_image_text(image_path, llm=mock_llm) == ""

    def test_none_content_becomes_empty_string(self, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response(None)

        assert extract.extract_image_text(image_path, llm=mock_llm) == ""

    @patch("tools.rag.extract.gateway")
    def test_llm_none_resolves_role_once_via_gateway(self, mock_gateway, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("resolved text")
        mock_gateway.get_llm.return_value = mock_llm

        result = extract.extract_image_text(image_path)

        assert result == "resolved text"
        mock_gateway.get_llm.assert_called_once_with(RAG_EXTRACT_ROLE)
        mock_llm.chat.assert_called_once()

    @patch("tools.rag.extract.gateway")
    def test_llm_given_never_touches_gateway(self, mock_gateway, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("text")

        extract.extract_image_text(image_path, llm=mock_llm)

        mock_gateway.get_llm.assert_not_called()

    def test_generation_rejected_propagates_unchanged(self, tmp_path):
        image_path = tmp_path / "page.png"
        image_path.write_bytes(b"x")
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = GenerationRejected("unknown model file")

        with pytest.raises(GenerationRejected, match="unknown model file"):
            extract.extract_image_text(image_path, llm=mock_llm)


class TestDescribeImage:
    """The UAT gap (2026-09-17): `EXTRACTION_PROMPT` is OCR-only, so a
    photo, an icon or a drawing produced an EMPTY sidecar and the chat
    model was left telling the owner to keep waiting for an extraction
    that had already finished.

    A SECOND, SEPARATE CALL, not a combined prompt: `extract_image_text`
    keeps its own prompt and its own verbatim-storage contract
    byte-for-byte, which is what makes "scanned-PDF OCR is unchanged"
    provable rather than asserted."""

    def test_it_sends_the_description_prompt_then_the_image(self, tmp_path):
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("A red bicycle against a brick wall.")

        result = extract.describe_image(image_path, llm=mock_llm)

        assert result == "A red bicycle against a brick wall."
        mock_llm.chat.assert_called_once()
        (messages,), _ = mock_llm.chat.call_args
        assert len(messages) == 1
        message = messages[0]
        assert message.role == MessageRole.USER
        assert len(message.blocks) == 2
        text_block, image_block = message.blocks
        # Prompt block first, image block second -- the instruction
        # reads before the pixels it applies to.
        assert text_block.text == extract.DESCRIPTION_PROMPT
        assert image_block.path == image_path

    def test_it_stores_what_the_model_said_verbatim_only_stripped(self, tmp_path):
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        mock_llm = MagicMock()
        mock_llm.chat.return_value = _chat_response("  A red bicycle.  \n")
        assert extract.describe_image(image_path, llm=mock_llm) == "A red bicycle."

    def test_a_blank_or_none_answer_is_the_empty_string(self, tmp_path):
        image_path = tmp_path / "photo.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        for content in ("", "   \n ", None):
            mock_llm = MagicMock()
            mock_llm.chat.return_value = _chat_response(content)
            assert extract.describe_image(image_path, llm=mock_llm) == ""

    def test_the_two_prompts_are_different_and_the_ocr_one_is_untouched(self):
        """THE REGRESSION PIN FOR EVERY SCANNED PDF ON THIS BOX: this
        task adds a prompt, it does not edit the existing one."""
        assert extract.DESCRIPTION_PROMPT != extract.EXTRACTION_PROMPT
        assert extract.EXTRACTION_PROMPT == (
            "Transcribe all text visible in this image exactly as it appears, preserving "
            "reading order and line breaks. Output only the transcribed text, with no "
            "commentary. If the image contains no text, output nothing."
        )

    def test_the_description_prompt_is_instruction_shaped_and_vendor_neutral(self):
        """House rule (AGENTS.md non-negotiable 3): no model or vendor
        name in committed prose, and this string IS platform prose — it
        must read as an instruction to any bound model, not as a request
        tuned to one."""
        lowered = extract.DESCRIPTION_PROMPT.lower()
        assert lowered.startswith("describe")
        assert "words" in lowered          # the length bound is stated TO the model
        for forbidden in ("you are", "as an ai", "assistant", "gpt", "llava", "claude"):
            assert forbidden not in lowered
