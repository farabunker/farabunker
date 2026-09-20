"""Unit tests for tools/rag/messages.py -- the shared "no usable model"
message formatting (`model_unavailable_message`).

Most of this module's behavior is already exercised indirectly through
`test_views_ask.py` (AskView's 503) and `test_jobs.py` (`run_ask`'s
pre-run re-check) -- both walk the real, unpatched `PRECHECK_ROLES`
(length 2).
This file adds the one direct, module-level test the T9.5 audit's docstring
truth sweep calls for: the `set(causes) == set(PRECHECK_ROLES)` cardinality
guard (formerly `len(causes) == len(PRECHECK_ROLES)`) proven correct at a
cardinality other than 2, and specifically proven to no longer depend on
`PRECHECK_ROLES`'s own *tuple length* the way the `len()` comparison did.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from models.contracts.bindings import ResolvedModel
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_EXTRACT_ROLE
from tools.rag import messages
from tools.rag.messages import UNBOUND, UNREACHABLE, model_unavailable_message, unreachable_endpoints


class TestCardinalityGuardIsSetBasedNotLenBased:
    """Simulates a 3-role PRECHECK (T9.5 audit) to prove the guard no
    longer depends on `PRECHECK_ROLES`'s tuple *length* -- only on which
    roles it actually names. A `PRECHECK_ROLES` tuple carrying a duplicate
    entry (three elements, two DISTINCT roles) is the case that tells the
    old `len(causes) == len(PRECHECK_ROLES)` guard apart from the new
    `set(causes) == set(PRECHECK_ROLES)` one: a `causes` dict naming both
    distinct roles has `len(causes) == 2`, which never equals the tuple's
    own `len() == 3` -- the old guard would silently fall through to the
    generic "mixed causes" sentence instead of the intended "no model
    connected" one; the new guard compares the actual role SETS and gets
    it right regardless of how many times a role happens to repeat in the
    tuple.
    """

    def test_duplicate_role_in_precheck_roles_does_not_break_the_all_unbound_branch(self, monkeypatch):
        monkeypatch.setattr(
            messages, "PRECHECK_ROLES", (RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_ANSWER_ROLE)
        )
        causes = {RAG_ANSWER_ROLE: UNBOUND, RAG_EMBED_ROLE: UNBOUND}

        assert model_unavailable_message(causes, {}) == messages._NO_MODEL_MESSAGE

    def test_duplicate_role_in_precheck_roles_does_not_break_the_shared_endpoint_branch(self, monkeypatch):
        monkeypatch.setattr(
            messages, "PRECHECK_ROLES", (RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_ANSWER_ROLE)
        )
        shared = ResolvedModel("ollama", "llama3", "http://localhost:11434")
        causes = {RAG_ANSWER_ROLE: UNREACHABLE, RAG_EMBED_ROLE: UNREACHABLE}
        resolved = {RAG_ANSWER_ROLE: shared, RAG_EMBED_ROLE: shared}

        assert model_unavailable_message(causes, resolved) == messages._ENDPOINT_UNREACHABLE_MESSAGE

    def test_three_genuinely_distinct_roles_all_unbound_still_collapses(self, monkeypatch):
        """Baseline correctness at N=3 (no duplicate this time) -- the
        guard scales past the real, shipped `PRECHECK_ROLES` length of 2
        without being hardcoded to it."""
        monkeypatch.setattr(
            messages, "PRECHECK_ROLES", (RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_EXTRACT_ROLE)
        )
        causes = {RAG_ANSWER_ROLE: UNBOUND, RAG_EMBED_ROLE: UNBOUND, RAG_EXTRACT_ROLE: UNBOUND}

        assert model_unavailable_message(causes, {}) == messages._NO_MODEL_MESSAGE

    def test_partial_causes_never_collapses_even_at_matching_count(self, monkeypatch):
        """A `causes` dict naming roles OUTSIDE `PRECHECK_ROLES` (however
        contrived) must never collapse to the generic message just because
        it happens to have the same number of entries -- proof the guard
        is a genuine set-membership check, not a disguised count check."""
        monkeypatch.setattr(messages, "PRECHECK_ROLES", (RAG_ANSWER_ROLE, RAG_EMBED_ROLE))
        causes = {RAG_ANSWER_ROLE: UNBOUND, RAG_EXTRACT_ROLE: UNBOUND}  # same length as PRECHECK_ROLES

        result = model_unavailable_message(causes, {})

        assert result != messages._NO_MODEL_MESSAGE


class TestUnreachableEndpoints:
    """C-15: `unreachable_endpoints` is the extracted dedup-by-normalized-
    endpoint health-check probe every `tools/rag` precheck surface calls
    into now (`views._precheck_models`, `views._precheck_embed_
    role`, `jobs._precheck`) -- see `test_views_ask.py`'s own
    characterization tests for the observable behaviour pinned at each of
    those callers.
    This file only proves the function's own contract, isolated from any
    caller."""

    def test_two_roles_at_the_same_normalized_endpoint_are_probed_once(self):
        """The dedup is keyed on `endpoint.rstrip("/")`, matching
        `models/registry/views.py`'s own `norm_endpoint` -- a trailing
        slash on one of the two must not defeat the dedup."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False
        resolved = {
            RAG_ANSWER_ROLE: ResolvedModel("ollama", "llama3", "http://localhost:11434"),
            RAG_EMBED_ROLE: ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11434/"),
        }

        with patch("tools.rag.messages.get_engine", return_value=mock_engine):
            unreachable = unreachable_endpoints(resolved, log_prefix="test")

        assert unreachable == {RAG_ANSWER_ROLE, RAG_EMBED_ROLE}
        mock_engine.is_healthy.assert_called_once_with("http://localhost:11434")

    def test_distinct_endpoints_are_each_probed_and_only_the_bad_one_returns(self):
        answer_resolved = ResolvedModel("ollama", "llama3", "http://localhost:11434")
        embed_resolved = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435")
        mock_engine = MagicMock()
        mock_engine.is_healthy.side_effect = lambda endpoint: endpoint == answer_resolved.endpoint

        with patch("tools.rag.messages.get_engine", return_value=mock_engine):
            unreachable = unreachable_endpoints(
                {RAG_ANSWER_ROLE: answer_resolved, RAG_EMBED_ROLE: embed_resolved},
                log_prefix="test",
            )

        assert unreachable == {RAG_EMBED_ROLE}
        assert mock_engine.is_healthy.call_count == 2

    def test_an_engine_that_raises_is_unreachable_not_a_crash(self, caplog):
        """AN ENGINE THAT RAISES IS UNREACHABLE, not a 500 -- the
        function's own docstring. The exception is logged with the
        caller-supplied `log_prefix`, not swallowed silently."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.side_effect = RuntimeError("connection refused")
        resolved = {RAG_ANSWER_ROLE: ResolvedModel("ollama", "llama3", "http://localhost:11434")}

        with patch("tools.rag.messages.get_engine", return_value=mock_engine):
            with caplog.at_level("ERROR", logger="tools.rag.messages"):
                unreachable = unreachable_endpoints(resolved, log_prefix="rag.ask re-check")

        assert unreachable == {RAG_ANSWER_ROLE}
        assert any(
            "rag.ask re-check" in record.getMessage() and record.exc_info is not None
            for record in caplog.records
        )

    def test_healthy_endpoint_names_no_role(self):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        resolved = {RAG_EMBED_ROLE: ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435")}

        with patch("tools.rag.messages.get_engine", return_value=mock_engine):
            assert unreachable_endpoints(resolved, log_prefix="test") == set()

    def test_empty_resolved_never_calls_get_engine(self):
        with patch("tools.rag.messages.get_engine") as mock_get_engine:
            assert unreachable_endpoints({}, log_prefix="test") == set()

        mock_get_engine.assert_not_called()
