"""Unit tests for models/registry/drift.py.

`active_fingerprint`/`is_drifted` depend on `models.contracts.bindings.resolve`
-- patched at its bound name (`models.registry.drift.resolve`) so these
tests exercise drift's own comparison/upsert logic in isolation from the
full db/env binding-resolution chain (that chain is covered by
test_bindings.py / test_db_bindings.py).

`run_rematerialize` resolves `RoleSpec.rematerialize` via `import_string`;
most tests patch `import_string` at its bound name in
`models.registry.drift` to isolate drift's own orchestration
(capture-fingerprint / call / stamp-only-on-success). One integration test
(`TestRunRematerializeIntegration`) deliberately does NOT patch
`import_string`: it lets the real dotted path resolve to
`tools.rag.services.reencode_all` and mocks only that function's own
collaborators (`_ingest_prose` / `rag_index` / `resolve`), pinning the
console->modules seam that a typo'd dotted path or renamed callback would
break.

Roles are registered directly with `models.contracts.roles.register_role`
rather than relying on `tools.rag.apps.py`'s real registrations, so these
tests don't depend on app-ready() timing or leak roles across test files;
an inline autouse fixture snapshots/restores the registry (matching
test_roles.py's own convention).

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models.registry.drift import active_fingerprint, format_tally, is_drifted, run_rematerialize
from models.registry.models import Materialization
from models.contracts import roles
from models.contracts.bindings import ResolvedModel
from models.contracts.roles import RoleSpec, register_role


@pytest.fixture(autouse=True)
def _isolated_role_registry():
    """Snapshot/restore models.contracts.roles' module-level registry so
    these tests' role registrations don't leak into (or get clobbered by)
    other test files sharing the same process-wide dict."""
    original = dict(roles._ROLES)
    roles._ROLES.clear()
    yield
    roles._ROLES.clear()
    roles._ROLES.update(original)


RESOLVED = ResolvedModel("ollama", "nomic-embed-text", "http://ollama.local:11434", embed_dim=768)


# --- active_fingerprint ----------------------------------------------------


class TestActiveFingerprint:
    def test_returns_resolved_models_fingerprint(self):
        with patch("models.registry.drift.resolve", return_value=RESOLVED) as mock_resolve:
            result = active_fingerprint("rag.embed")

        mock_resolve.assert_called_once_with("rag.embed")
        assert result == RESOLVED.fingerprint == "ollama:nomic-embed-text:768"


# --- is_drifted -------------------------------------------------------


@pytest.mark.django_db
class TestIsDrifted:
    def test_no_materialization_row_is_not_drifted(self):
        with patch("models.registry.drift.resolve", return_value=RESOLVED):
            assert is_drifted("rag.embed") is False

    def test_matching_fingerprint_is_not_drifted(self):
        Materialization.objects.create(role_key="rag.embed", fingerprint=RESOLVED.fingerprint)

        with patch("models.registry.drift.resolve", return_value=RESOLVED):
            assert is_drifted("rag.embed") is False

    def test_mismatched_fingerprint_is_drifted(self):
        Materialization.objects.create(
            role_key="rag.embed", fingerprint="ollama:old-embed-model:512"
        )

        with patch("models.registry.drift.resolve", return_value=RESOLVED):
            assert is_drifted("rag.embed") is True

    def test_only_compares_the_named_roles_materialization(self):
        Materialization.objects.create(
            role_key="rag.answer", fingerprint="ollama:old-embed-model:512"
        )

        with patch("models.registry.drift.resolve", return_value=RESOLVED):
            assert is_drifted("rag.embed") is False


# --- run_rematerialize --------------------------------------------------


@pytest.mark.django_db
class TestRunRematerialize:
    def test_calls_dotted_path_and_returns_its_result(self):
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        callback = MagicMock(return_value=42)

        with (
            patch("models.registry.drift.import_string", return_value=callback) as mock_import,
            patch("models.registry.drift.resolve", return_value=RESOLVED),
        ):
            result = run_rematerialize("rag.embed")

        mock_import.assert_called_once_with("tools.rag.services.reencode_all")
        callback.assert_called_once_with()
        assert result == 42

    def test_stamps_materialization_with_current_active_fingerprint(self):
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        callback = MagicMock(return_value=7)

        with (
            patch("models.registry.drift.import_string", return_value=callback),
            patch("models.registry.drift.resolve", return_value=RESOLVED),
        ):
            run_rematerialize("rag.embed")

        materialization = Materialization.objects.get(role_key="rag.embed")
        assert materialization.fingerprint == RESOLVED.fingerprint

    def test_upserts_existing_materialization_row_rather_than_duplicating(self):
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:old-model:512")
        callback = MagicMock(return_value=1)

        with (
            patch("models.registry.drift.import_string", return_value=callback),
            patch("models.registry.drift.resolve", return_value=RESOLVED),
        ):
            run_rematerialize("rag.embed")

        assert Materialization.objects.filter(role_key="rag.embed").count() == 1
        assert Materialization.objects.get(role_key="rag.embed").fingerprint == RESOLVED.fingerprint

    def test_role_with_no_rematerialize_raises_clear_error(self):
        register_role(RoleSpec(key="rag.answer", label="RAG answer", capability="chat"))

        with pytest.raises(ValueError, match="rag.answer"):
            run_rematerialize("rag.answer")

    def test_unregistered_role_raises_clear_error(self):
        with pytest.raises(ValueError, match="does.not.exist"):
            run_rematerialize("does.not.exist")

    def test_no_rematerialize_does_not_touch_materialization_row(self):
        register_role(RoleSpec(key="rag.answer", label="RAG answer", capability="chat"))

        with pytest.raises(ValueError):
            run_rematerialize("rag.answer")

        assert Materialization.objects.filter(role_key="rag.answer").count() == 0

    def test_failing_callback_propagates_and_does_not_stamp(self):
        """A rematerialize that raises (e.g. reencode_all's
        RuntimeError-on-partial) must NOT be stamped as a completed
        materialization -- otherwise a totally failed run would clear the
        drift banner behind a green message."""
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        callback = MagicMock(side_effect=RuntimeError("only 0 of 3 re-encoded"))

        with (
            patch("models.registry.drift.import_string", return_value=callback),
            patch("models.registry.drift.resolve", return_value=RESOLVED),
        ):
            with pytest.raises(RuntimeError, match="0 of 3"):
                run_rematerialize("rag.embed")

        assert Materialization.objects.filter(role_key="rag.embed").count() == 0

    def test_failing_callback_leaves_an_existing_stamp_untouched(self):
        """With a prior (drifted) stamp in place, a failed run keeps it --
        so `is_drifted()` keeps reporting drift."""
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:old-model:512")
        callback = MagicMock(side_effect=RuntimeError("boom"))

        with (
            patch("models.registry.drift.import_string", return_value=callback),
            patch("models.registry.drift.resolve", return_value=RESOLVED),
        ):
            with pytest.raises(RuntimeError):
                run_rematerialize("rag.embed")

        assert (
            Materialization.objects.get(role_key="rag.embed").fingerprint
            == "ollama:old-model:512"
        )

    def test_stamps_the_fingerprint_active_when_the_run_started(self):
        """A rebind racing the rematerialize: the data was encoded under the
        binding active when the run STARTED, so that pre-captured
        fingerprint is what gets stamped -- not whatever the role resolves
        to after the callback returns."""
        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        before = RESOLVED
        after = ResolvedModel(
            "ollama", "mxbai-embed-large", "http://ollama.local:11434", embed_dim=1024
        )
        state = {"rebound": False}

        def fake_resolve(role_key):
            return after if state["rebound"] else before

        callback = MagicMock(side_effect=lambda: state.update(rebound=True))

        with (
            patch("models.registry.drift.import_string", return_value=callback),
            patch("models.registry.drift.resolve", side_effect=fake_resolve),
        ):
            run_rematerialize("rag.embed")

        assert Materialization.objects.get(role_key="rag.embed").fingerprint == before.fingerprint


# --- run_rematerialize -> reencode_all integration seam ---------------------


@pytest.mark.django_db
class TestRunRematerializeIntegration:
    """Resolve the REAL dotted path (`tools.rag.services.reencode_all`)
    through `import_string` -- no `import_string` patch -- and mock only
    reencode_all's own collaborators. Pins the seam a renamed/moved callback
    or a typo'd `RoleSpec.rematerialize` string would break, which the
    mocked-`import_string` tests above cannot catch."""

    def test_real_dotted_path_runs_reencode_all_and_stamps(self):
        from tools.rag.models import Document

        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        Document.objects.create(
            title="doc.txt",
            source_path="/data/documents/1/doc.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
        )

        with (
            patch("models.registry.drift.resolve", return_value=RESOLVED),
            patch("tools.rag.services.resolve", return_value=RESOLVED),
            patch("tools.rag.services.rag_index") as mock_rag_index,
            patch("tools.rag.ingest._ingest_prose") as mock_ingest_prose,
        ):
            mock_rag_index.live_embed_dim.return_value = RESOLVED.embed_dim

            result = run_rematerialize("rag.embed")

        assert result == {"documents": 1, "reencoded": 1}
        mock_ingest_prose.assert_called_once()
        assert Materialization.objects.get(role_key="rag.embed").fingerprint == RESOLVED.fingerprint

    def test_real_dotted_path_partial_failure_propagates_and_does_not_stamp(self):
        from tools.rag.models import Document

        register_role(
            RoleSpec(
                key="rag.embed",
                label="RAG embeddings",
                capability="embeddings",
                rematerialize="tools.rag.services.reencode_all",
            )
        )
        Document.objects.create(
            title="doc.txt",
            source_path="/data/documents/1/doc.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
        )

        with (
            patch("models.registry.drift.resolve", return_value=RESOLVED),
            patch("tools.rag.services.resolve", return_value=RESOLVED),
            patch("tools.rag.services.rag_index") as mock_rag_index,
            patch("tools.rag.ingest._ingest_prose", side_effect=Exception("store copy missing")),
        ):
            mock_rag_index.live_embed_dim.return_value = RESOLVED.embed_dim

            with pytest.raises(RuntimeError, match="0 of 1"):
                run_rematerialize("rag.embed")

        assert Materialization.objects.filter(role_key="rag.embed").count() == 0


# --- format_tally (shared by role_reencode + the reencode command) --------


class TestFormatTally:
    def test_dict_result_renders_key_equals_value_comma_joined(self):
        assert format_tally({"added": 3, "skipped": 1}) == "added=3, skipped=1"

    def test_dict_preserves_insertion_order(self):
        assert format_tally({"z": 1, "a": 2}) == "z=1, a=2"

    def test_single_key_dict(self):
        assert format_tally({"reencoded": 42}) == "reencoded=42"

    def test_empty_dict_renders_empty_string(self):
        assert format_tally({}) == ""

    def test_non_dict_result_renders_via_str(self):
        assert format_tally(42) == "42"
        assert format_tally("done") == "done"
        assert format_tally(None) == "None"
