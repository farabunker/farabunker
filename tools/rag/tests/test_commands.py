"""Unit tests for the three RAG management commands
(tools/rag/management/commands/{ask,ingest,ingest_watch}.py) -- mirrors
models/registry/tests/test_reencode_command.py's grammar: patch the
callable at the name it's bound to INSIDE the command module (never the
module it's originally defined in), so each command's own invocation/error-
translation logic is tested in isolation from the thing it calls.

`answer_question`, `ingest_path`, and `watch_folder` are always mocked, so
neither the ingest nor the ingest_watch commands ever touch the database;
real filesystem paths (via `tmp_path`) exercise the commands' own
directory-walking / mkdir logic without ever reaching network or a real
ingest.

`ask` is the one exception (IA-2 T11): it builds its own `DocumentVisibility`
via `tools.rag.access.document_visibility(SERVICE_PRINCIPAL)` before calling
the mocked `answer_question`, and that read touches `IdentitySettings` --
so `TestAskCommand` carries its own `pytestmark = pytest.mark.django_db`
(everything else in this module still needs none).
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from identity.contracts.postures import POSTURE_OPEN
from models.contracts.bindings import ResolvedModel
from tools.rag.access import DocumentVisibility
from tools.rag.tests._helpers import make_document, posture

ANSWER_RESOLVED = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
EMBED_RESOLVED = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768)

# The open-box visibility `document_visibility(SERVICE_PRINCIPAL)` returns
# under `POSTURE_OPEN` -- every test below pins that posture explicitly
# (GLOBAL CONSTRAINT 7) rather than relying on it being the untouched
# default, since the sweep re-runs this module under enterprise/personal
# too, and `document_visibility` answers differently there.
# `owner_kind`/`owner_key` (C-03) are `SERVICE_PRINCIPAL`'s own
# ("service"/"local") -- `document_visibility` threads them from
# whichever principal it is handed, unrestricted branch included.
OPEN_VISIBILITY = DocumentVisibility(True, frozenset(), True,
                                     owner_kind="service", owner_key="local")


# --- ask ---------------------------------------------------------------


def _resolve_side_effect(role):
    from models.contracts.roles import RAG_ANSWER_ROLE

    return ANSWER_RESOLVED if role == RAG_ANSWER_ROLE else EMBED_RESOLVED


@pytest.mark.django_db
class TestAskCommand:
    """`ask.py`'s `handle()` now calls `tools.rag.access.document_visibility`
    (IA-2 T11) before it calls the (mocked) `answer_question`, and that read
    touches `IdentitySettings` -- hence the class-level `django_db` marker
    the rest of this module does not need."""

    def _patch_resolve(self):
        return patch("tools.rag.management.commands.ask.resolve", side_effect=_resolve_side_effect)

    def test_passes_question_through_to_answer_question(self):
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": []},
        ) as mock_answer:
            call_command("ask", "What is the answer?")

        mock_answer.assert_called_once_with(
            "What is the answer?",
            answer_resolved=ANSWER_RESOLVED,
            embed_resolved=EMBED_RESOLVED,
            visibility=OPEN_VISIBILITY,
        )

    def test_unbound_role_becomes_command_error(self):
        """An unresolvable role (`resolve()` raises `ValueError`) is a clear,
        operator-actionable setup problem -- translated to `CommandError`,
        never a raw traceback (matches
        models/registry/management/commands/reencode.py's identical
        pattern)."""
        with patch(
            "tools.rag.management.commands.ask.resolve",
            side_effect=ValueError("No inference binding resolved for role 'rag.answer'"),
        ), patch("tools.rag.management.commands.ask.answer_question") as mock_answer:
            with pytest.raises(CommandError, match="No inference binding resolved"):
                call_command("ask", "q")

        mock_answer.assert_not_called()

    def test_prints_the_answer(self, capsys):
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "The sky is blue.", "citations": []},
        ):
            call_command("ask", "Why is the sky blue?")

        captured = capsys.readouterr()
        assert "The sky is blue." in captured.out

    def test_no_citations_prints_none_placeholder(self, capsys):
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": []},
        ):
            call_command("ask", "q")

        captured = capsys.readouterr()
        assert "Citations: (none)" in captured.out

    def test_missing_citations_key_also_prints_none_placeholder(self, capsys):
        """`result.get("citations")` -- a result dict omitting the key
        entirely degrades the same as an explicit empty list."""
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42"},
        ):
            call_command("ask", "q")

        captured = capsys.readouterr()
        assert "Citations: (none)" in captured.out

    def test_prints_citation_fields(self, capsys):
        """H36: the path comes off the `Document` row `document_id`
        names, and from nowhere else -- the `source_path` key the
        pipeline stopped writing at C-5 is no longer read back."""
        doc = make_document(title="Handbook", source_path="/data/handbook.pdf")
        citations = [
            {
                "title": "Handbook",
                "source": "prose",
                "document_id": str(doc.id),
                "score": 0.8765,
                "sql_query": "SELECT 1",
                "snippet": "some short snippet",
            }
        ]
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": citations},
        ):
            call_command("ask", "q")

        out = capsys.readouterr().out
        assert "Citations (1):" in out
        assert f"[1] Handbook  (source=prose, document_id={doc.id})" in out
        assert "path: /data/handbook.pdf" in out
        assert "score: 0.8765" in out
        assert "sql: SELECT 1" in out
        assert "snippet: some short snippet" in out

    def test_a_citation_carrying_its_own_source_path_is_ignored(self, capsys):
        """The H36 branch itself: a hand-assembled dict can no longer
        talk this command into printing a path the row does not have."""
        doc = make_document(title="Handbook", source_path="/data/real.pdf")
        citations = [{
            "title": "Handbook",
            "source": "prose",
            "document_id": str(doc.id),
            "source_path": "/data/supplied-by-the-caller.pdf",
        }]
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": citations},
        ):
            call_command("ask", "q")

        out = capsys.readouterr().out
        assert "path: /data/real.pdf" in out
        assert "supplied-by-the-caller" not in out

    def test_untitled_citation_falls_back_and_long_snippet_is_truncated(self, capsys):
        long_snippet = ("x" * 200) + "\nwith a newline"
        citations = [{"source": "?", "document_id": None, "snippet": long_snippet}]
        with posture(POSTURE_OPEN), self._patch_resolve(), patch(
            "tools.rag.management.commands.ask.answer_question",
            return_value={"answer": "42", "citations": citations},
        ):
            call_command("ask", "q")

        out = capsys.readouterr().out
        assert "(untitled)" in out
        # newline collapsed to a space, truncated to 157 chars + "..."
        assert "\nwith a newline" not in out
        truncated_line = next(line for line in out.splitlines() if "snippet:" in line)
        assert truncated_line.strip().endswith("...")


# --- ingest --------------------------------------------------------------


class TestIngestCommand:
    def test_missing_path_raises_command_error(self, tmp_path):
        missing = tmp_path / "does-not-exist"

        with pytest.raises(CommandError, match="Path does not exist"):
            call_command("ingest", str(missing))

    def test_single_file_is_ingested_with_no_category(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        fake_doc = MagicMock(id="doc-1", doc_type="prose")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path", return_value=fake_doc
        ) as mock_ingest:
            call_command("ingest", str(f))

        mock_ingest.assert_called_once_with(str(f), category=None)

    def test_single_file_uses_explicit_category(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        fake_doc = MagicMock(id="doc-1", doc_type="prose")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path", return_value=fake_doc
        ) as mock_ingest:
            call_command("ingest", str(f), "--category", "Medical")

        mock_ingest.assert_called_once_with(str(f), category="Medical")

    def test_directory_walk_derives_category_from_subfolder_per_file(self, tmp_path):
        (tmp_path / "medical").mkdir()
        (tmp_path / "medical" / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")  # directly under root -> None
        fake_doc = MagicMock(id="doc-1", doc_type="prose")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path", return_value=fake_doc
        ) as mock_ingest:
            call_command("ingest", str(tmp_path))

        mock_ingest.assert_has_calls(
            [
                call(str(tmp_path / "medical" / "a.txt"), category="medical"),
                call(str(tmp_path / "b.txt"), category=None),
            ],
            any_order=True,
        )
        assert mock_ingest.call_count == 2

    def test_directory_walk_explicit_category_overrides_subfolder(self, tmp_path):
        (tmp_path / "medical").mkdir()
        (tmp_path / "medical" / "a.txt").write_text("a")
        fake_doc = MagicMock(id="doc-1", doc_type="prose")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path", return_value=fake_doc
        ) as mock_ingest:
            call_command("ingest", str(tmp_path), "--category", "Forced")

        mock_ingest.assert_called_once_with(str(tmp_path / "medical" / "a.txt"), category="Forced")

    def test_one_file_failure_does_not_stop_the_loop(self, tmp_path, capsys):
        """A per-file exception is caught, reported to stderr, and counted
        as a failure -- the loop continues on to remaining files rather
        than aborting."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        good_doc = MagicMock(id="doc-good", doc_type="prose")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path",
            side_effect=[RuntimeError("boom"), good_doc],
        ) as mock_ingest:
            call_command("ingest", str(tmp_path))

        assert mock_ingest.call_count == 2
        out = capsys.readouterr().out
        assert "Ingested 1 file(s), 1 failure(s)." in out

    def test_all_files_failing_raises_command_error(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(CommandError, match="All files failed to ingest"):
                call_command("ingest", str(tmp_path))

    def test_directory_with_no_supported_files_warns_and_returns(self, tmp_path, capsys):
        (tmp_path / "unsupported.exe").write_text("nope")

        with patch(
            "tools.rag.management.commands.ingest.ingest_path"
        ) as mock_ingest:
            call_command("ingest", str(tmp_path))

        mock_ingest.assert_not_called()
        out = capsys.readouterr().out
        assert "No supported files found under" in out


@pytest.mark.django_db
class TestIngestCommandRealFailureReporting:
    """The one deliberate exception to this file's "never touch the
    database, always mock ingest_path" rule (module docstring): a T2
    review MAJOR finding was that a `manage.py ingest` failure used to
    strand its Document row at PROCESSING forever, with no way to notice
    from the command's own output alone. This proves the REAL `ingest_path`
    (not mocked) both writes the row FAILED and still reports the failure
    to the command's stderr exactly as before -- `ingest_path`'s own
    dedicated FAILED-write test lives in test_ingest.py; this one instead
    proves the CLI's per-file reporting survives that change unchanged."""

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_real_ingest_failure_is_reported_and_the_row_is_failed(
        self, mock_gateway, mock_rag_index, tmp_path, capsys, settings
    ):
        from tools.rag.models import Document

        # Redirect the managed store so the real ingest_path's copy never
        # touches the repo-local data/ directory (see test_ingest.py's own
        # `_managed_store` autouse fixture -- this file has no such fixture
        # of its own, per its "never touch the database" module docstring).
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_rag_index.get_index.side_effect = RuntimeError("index is down")

        path = tmp_path / "notes.md"
        path.write_text("some content")

        # The only file given fails, so the command's own "every file
        # failed" check (tools/rag/management/commands/ingest.py) still
        # raises CommandError afterward -- unchanged by this fix; both the
        # per-file stderr line and the CommandError are the CLI's existing
        # failure-reporting shape, exercised here for real.
        with pytest.raises(CommandError, match="All files failed to ingest"):
            call_command("ingest", str(path))

        err = capsys.readouterr().err
        assert "FAIL" in err
        assert "index is down" in err

        doc = Document.objects.get(original_path=str(path.resolve()))
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "index is down"


# --- ingest_watch ----------------------------------------------------------


class TestIngestWatchCommand:
    def test_creates_directory_then_watches_it(self, tmp_path):
        target = tmp_path / "inbox" / "nested"

        with patch(
            "tools.rag.management.commands.ingest_watch.watch_folder"
        ) as mock_watch:
            call_command("ingest_watch", str(target))

        assert target.is_dir()
        mock_watch.assert_called_once_with(str(target))

    def test_not_a_directory_error_becomes_command_error(self, tmp_path):
        """A path whose PARENT already exists as a plain file can never be
        created as a directory -- `Path.mkdir(parents=True)` surfaces that
        as `NotADirectoryError`, which the command translates to
        `CommandError` rather than letting an OS error escape."""
        blocking_file = tmp_path / "blocking-file"
        blocking_file.write_text("not a directory")
        target = blocking_file / "subdir"

        with patch(
            "tools.rag.management.commands.ingest_watch.watch_folder"
        ) as mock_watch:
            with pytest.raises(CommandError):
                call_command("ingest_watch", str(target))

        mock_watch.assert_not_called()
