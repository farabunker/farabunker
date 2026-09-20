"""The distillation constant and its one call.

No `pytestmark`: nothing here touches the database. Every test imports
locally, so the module stays importable with no Django settings — the
same shape `tools/rag/tests/test_extract.py` has for `EXTRACTION_PROMPT`,
which is the precedent this constant follows.
"""
from __future__ import annotations


def test_the_distillation_prompt_is_the_documented_constant():
    """Asserted the way `tools/rag/tests/test_extract.py:45` asserts
    `EXTRACTION_PROMPT` — the one precedent in the tree, and this is the
    second instance of it."""
    from tools.rag.distil import DISTILLATION_PROMPT

    assert DISTILLATION_PROMPT.startswith("Distil the conversation below")
    assert "self-contained note" in DISTILLATION_PROMPT
    assert "\n" not in DISTILLATION_PROMPT     # implicit concatenation, no indentation


def test_the_prompt_describes_the_OUTPUT_and_addresses_no_particular_engine():
    """PLATFORM BEHAVIOR, not a model default: the wording is what makes
    "a retrievable note, not a chat recap" true of every engine the chat
    role could ever be bound to, regardless of that model's own
    summarising manners.

    Asserted STRUCTURALLY -- every sentence is an instruction about the
    OUTPUT, and the prompt contains no capitalised token that is not
    sentence-initial -- rather than against a list of names to avoid.
    Enumerating vendors in a test would put exactly the strings Global
    Constraint 1 forbids into a committed file, which is the rule this
    test exists to keep.
    """
    from tools.rag.distil import DISTILLATION_PROMPT

    lowered = DISTILLATION_PROMPT.lower()
    # It never addresses an engine, names a role, or mentions the thing
    # doing the work -- it describes what must come out.
    for forbidden in ("you are", "as an", "assistant", "model", "engine"):
        assert forbidden not in lowered, forbidden
    # And no proper noun: every capital is sentence-initial.
    sentences = [t.strip() for t in DISTILLATION_PROMPT.split(". ") if t.strip()]
    for sentence in sentences:
        assert sentence[1:] == sentence[1:].lower() or "-" in sentence, sentence


def test_it_sends_the_constant_and_the_transcript_in_one_message():
    """The body is EXERCISED, not only its constant asserted. Every
    consolidation test patches this function out, so without this case an
    empty body would not surface until production (M20).

    RETARGETED to the bracketed, fenced shape (H32/C-3, constraint 26
    waived by the orchestrator for this one assertion).
    """
    from unittest.mock import MagicMock

    from tools.rag.distil import DISTILLATION_PROMPT, distil_conversation

    llm = MagicMock()
    llm.chat.return_value.message.content = "  The note.  "
    out = distil_conversation(
        ({"role": "user", "text": "What did we decide?"},
         {"role": "assistant", "text": "To ship in Q3."}), llm=llm)

    assert out == "The note."
    (messages,), _kwargs = llm.chat.call_args
    assert len(messages) == 1
    content = messages[0].content
    assert content.startswith(DISTILLATION_PROMPT)
    assert "[user]\nWhat did we decide?" in content
    assert "[assistant]\nTo ship in Q3." in content


def test_it_refuses_to_resolve_its_own_model():
    """A function that could quietly resolve one would be a second
    binding path beside the planner's."""
    import pytest

    from tools.rag.distil import distil_conversation

    with pytest.raises(ValueError):
        distil_conversation(({"role": "user", "text": "hi"},))


def test_the_cap_is_named_and_is_not_the_prompt_budget():
    """NOT `HISTORY_TURNS = 20` — that is a live turn's prompt budget,
    and distilling only the last twenty turns would silently make "the
    conversation" mean something else."""
    from agents.limits import HISTORY_TURNS
    from tools.rag.distil import CONSOLIDATION_MAX_TURNS

    assert CONSOLIDATION_MAX_TURNS == 400
    assert CONSOLIDATION_MAX_TURNS != HISTORY_TURNS
