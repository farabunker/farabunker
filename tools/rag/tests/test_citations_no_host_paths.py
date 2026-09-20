"""C-5 (round-3 hardening), regression tests: the host filesystem path
stops riding a citation out of `tools.rag.retrieval._vector_citations`,
which is the ONE seam every citation passes through on its way to an
`rag.ask` tool result, a stored `Turn.data`, and the Ask page.

A NEW module (constraint 26): `tools/rag/tests/` is a whole directory
another session is editing this round, so a task needing rag tests adds
a module rather than appending to `test_retrieval.py`/`test_commands.py`
-- both of which carry their own pre-existing citation-shape assertions
that this fix does not touch or need to touch (`test_commands.py::
TestAskCommand::test_prints_citation_fields` hand-builds a citation dict
that already carries `source_path`, and stays green: the command still
prints it, unchanged, when a caller hands it one -- see
`Command._citation_path`'s own docstring for why that fallback exists).

The chat-render half of this finding ("a stored row from before this fix
still carries the key -- render must tolerate it") is ALREADY pinned,
with no change needed here: `agents/chat/tests/test_rendering.py::
TestCitations::test_source_path_never_reaches_the_card` and
`agents/chat/tests/test_thread.py::
test_a_source_path_in_the_citation_data_never_reaches_the_page` both
build a stored-shape citation dict that still carries `source_path` and
assert the renderer strips it down to a fixed four-key dict regardless.
Neither file is touched by this module.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command

from identity.contracts.postures import POSTURE_OPEN
from tools.rag import retrieval
from tools.rag.tests._helpers import make_document, make_tool_ctx, posture


def _resolve_side_effect(role):
    from models.contracts.bindings import ResolvedModel
    from models.contracts.roles import RAG_ANSWER_ROLE

    answer = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
    embed = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768)
    return answer if role == RAG_ANSWER_ROLE else embed


def _fake_source_node(*, file_id, file_name, source_path, score=0.91, node_id="n-1",
                       text="chunk text"):
    """A minimal double for a LlamaIndex `NodeWithScore` over a vector
    source node -- just enough for `_vector_citations` to read. Node
    metadata legitimately still carries `source_path` (ingest writes it
    there, `tools/rag/retrieval.py::_vector_citations`'s own docstring
    says why) -- it is the OUTPUT citation dict this module is proving
    no longer does.

    A local copy, not an import from `test_retrieval.py`'s identical
    fixture: that module is one of the ones constraint 26 names as held
    by another session this round, and this module's own point is to
    need nothing from it."""
    node = MagicMock()
    node.metadata = {"file_id": file_id, "file_name": file_name, "source_path": source_path}
    node.node_id = node_id
    node.get_content.return_value = text

    node_with_score = MagicMock()
    node_with_score.node = node
    node_with_score.score = score
    return node_with_score


def _fake_response(*, text="the answer", source_nodes=None):
    response = MagicMock()
    response.__str__.return_value = text
    response.source_nodes = source_nodes or []
    return response


class TestVectorCitationsDropTheHostPath:
    """`tools.rag.retrieval._vector_citations` -- the seam."""

    def test_a_vector_citation_carries_no_source_path(self):
        node = _fake_source_node(file_id="42", file_name="notes.md",
                                  source_path="/data/notes.md")
        response = _fake_response(source_nodes=[node])

        citations = retrieval._vector_citations(response)

        assert citations
        assert all("source_path" not in citation for citation in citations)

    def test_the_narrowed_shape_keeps_every_other_key(self):
        """Pinned alongside the deletion so the narrowing stays a
        SINGLE-KEY change, not a silent drop of anything else --
        `test_retrieval.py::TestVectorCitations::
        test_builds_citation_dicts_from_source_nodes` pinned the pre-fix
        shape (source_path included); this is its post-fix twin, kept
        here rather than edited there per constraint 26."""
        node = _fake_source_node(file_id="42", file_name="notes.md",
                                  source_path="/data/notes.md", score=0.91, node_id="n-1")
        response = _fake_response(source_nodes=[node])

        citation = retrieval._vector_citations(response)[0]

        assert citation == {
            "source": "vector",
            "document_id": "42",
            "title": "notes.md",
            "chunk_id": "n-1",
            "row_index": None,
            "score": 0.91,
            "snippet": "chunk text",
            "page": None,
            "start_seconds": None,
            "end_seconds": None,
            "locator": "",
            "locator_text": "",
        }


@pytest.mark.django_db
class TestAStoredAskToolTurnCarriesNoSourcePath:
    """`agents.runtime.loop.py:590` writes a TOOL turn's `Turn.data` as
    `outcome.result.data` VERBATIM -- so `ToolResult.data`, which is what
    `tools.rag.tools.run_ask` returns, IS what a stored ask tool turn
    carries, with no further step between them. Exercising `run_ask`
    (with only the LLM synthesis mocked, via `retrieval.answer_question`,
    the same seam `test_tools.py::TestRagAskRunner` mocks) with a REAL
    `_vector_citations` output proves the stored shape without also
    standing up a real conversation/agent/queue -- scaffolding this
    finding has no other need for."""

    def test_run_ask_result_data_carries_no_source_path(self):
        from tools.rag.tools import run_ask

        node = _fake_source_node(file_id="7", file_name="a.pdf",
                                  source_path="/srv/farabunker/media/a.pdf")
        response = _fake_response(source_nodes=[node])
        citations = retrieval._vector_citations(response)
        assert citations and "source_path" not in citations[0]  # the seam, again

        with patch("tools.rag.retrieval.answer_question") as answer, \
                patch("models.contracts.bindings.resolve"):
            answer.return_value = {"answer": "Because.", "citations": citations}
            result = run_ask({"question": "why"}, make_tool_ctx())

        assert "source_path" not in json.dumps(result.data)


@pytest.mark.django_db
class TestTheShellCommandStillPrintsAPath:
    """`manage.py ask` is the one caller this finding says the path is
    FOR (an operator already at the machine's own filesystem) --
    `Command._citation_path` reads it off `Document.source_path` by
    `document_id` now that the citation dict itself never carries it."""

    def _patch_resolve(self):
        return patch("tools.rag.management.commands.ask.resolve", side_effect=_resolve_side_effect)

    def test_it_reads_the_path_off_the_document_row(self, capsys):
        doc = make_document(source_path="/data/documents/9/handbook.pdf")
        citations = [{"title": doc.title, "source": "vector",
                      "document_id": str(doc.id), "score": 0.9}]
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": citations},
        ):
            call_command("ask", "anything")

        out = capsys.readouterr().out
        assert f"path: {doc.source_path}" in out

    def test_a_document_id_that_no_longer_resolves_prints_no_path_line(self, capsys):
        """The row can be gone (deleted since the chunk was indexed) or
        the id malformed -- either way this is a CLI convenience, never
        worth a traceback over."""
        citations = [{"title": "ghost.pdf", "source": "vector",
                      "document_id": "999999", "score": 0.9}]
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": citations},
        ):
            call_command("ask", "anything")

        out = capsys.readouterr().out
        assert "path:" not in out


class TestTheAskPageTemplateHasNoPathLine:
    def test_source_path_is_not_in_the_template_source(self):
        template = (
            settings.BASE_DIR / "tools" / "rag" / "templates" / "rag" / "ask.html"
        ).read_text()
        assert "source_path" not in template
