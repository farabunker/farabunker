"""The ONE filter point, and the single most dangerous line in the phase.

`MetadataFilters(filters=[])` means "no filter", which means EVERYTHING.
So a principal who may see nothing gets an EARLY RETURN, never an empty
filter list -- and that has its own test, first, because it is the line
whose failure mode is silent and total.
"""
from __future__ import annotations

import io
import inspect

import pytest
from django.urls import reverse
from llama_index.core.vector_stores.types import (
    FilterCondition, FilterOperator, MetadataFilter, MetadataFilters,
)

from agents.contracts.workstreams import WorkstreamScope
from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE
from identity.contracts.principals import Principal
from models.contracts.bindings import ResolvedModel
from tools.rag import index as rag_index
from tools.rag import retrieval
from tools.rag.access import DocumentVisibility
from tools.rag.models import RagSettings
from tools.rag.tests._helpers import (
    grant, make_admin, make_entitlement, make_job_ctx, make_tool_ctx, make_user,
    model_available, posture, sign_in,
)

pytestmark = pytest.mark.django_db

# GLOBAL CONSTRAINT 1: a `ResolvedModel`'s second argument is a MODEL id,
# so this module builds its own placeholders rather than copying
# `test_commands.py:24-25`'s literals into a new file. Only the ENGINE
# key is a real registered name, which the constraint's own carve-out
# allows. Nothing here reads `.model_id` -- the three runner tests assert
# on `spy["visibility"]`, and `run_ask`'s one use is inside a swallowing
# `try` (`tools/rag/jobs.py:259`).
ANSWER_RESOLVED = ResolvedModel("ollama", "the-assigned-chat-model",
                                "http://localhost:11434")
EMBED_RESOLVED = ResolvedModel("ollama", "the-embedding-role-model",
                               "http://localhost:11434", embed_dim=768)


def _resolve_side_effect(role):
    from models.contracts.roles import RAG_ANSWER_ROLE
    return ANSWER_RESOLVED if role == RAG_ANSWER_ROLE else EMBED_RESOLVED


def _settings_row():
    return RagSettings.get_solo()


def _filters(visibility, category=None):
    """The `MetadataFilters` `retrieve_nodes` would build, without
    building an index: the filter construction is factored into
    `retrieval._visibility_filters` precisely so it can be asserted
    directly rather than through a live vector store."""
    return retrieval._visibility_filters(category, visibility)


class TestTheDangerousLine:
    def test_sees_nothing_returns_no_nodes_and_never_calls_the_retriever(
            self, monkeypatch):
        """An EARLY RETURN, not an empty filter list. The index is still
        built and returned -- `answer_question` synthesizes from it even
        with zero nodes -- and the query EMBEDDING is skipped, because it
        happens inside `retriever.retrieve`, which is what we skip."""
        calls = []

        class _Index:
            def as_retriever(self, **kwargs):
                calls.append(kwargs)
                raise AssertionError("the retriever must not be built")

        monkeypatch.setattr(retrieval, "get_index", lambda *a, **k: _Index())
        # C-06: `retrieve_nodes` borrows its store from `tools.rag.index.
        # disposing_vector_store()`, which calls `get_vector_store()` in
        # `tools.rag.index`'s own module globals -- not `retrieval`'s copy
        # of the name (retrieval.py no longer even imports it).
        monkeypatch.setattr(rag_index, "get_vector_store",
                            lambda: type("S", (), {"hybrid_search": False})())
        monkeypatch.setattr(retrieval.gateway, "get_embed_model_for", lambda r, **k: object())
        nothing = DocumentVisibility(unrestricted=False, entitlement_ids=frozenset(),
                                     unlabelled_allowed=False)
        nodes, hybrid, index = retrieval.retrieve_nodes(
            "a question", None, _settings_row(), embed_resolved=object(),
            visibility=nothing)
        assert nodes == []
        assert index is not None
        assert calls == []

    def test_sees_nothing_still_wins_even_with_a_conversation_id(self, monkeypatch):
        """ROUND 12 REVIEW I-3 (RULING'S OWN REQUIRED TEST): the
        conversation leg's exemption from the ENTITLEMENT gate
        (`TestTheChunkFilterCorpus` in `test_chat_scoped_documents.py`
        pins that half) does not reach past THIS earlier, coarser gate
        -- a principal with ZERO entitlements on a locked library still
        gets nothing for their OWN chat-scoped attachment, because
        `retrieve_nodes` never even calls `_visibility_filters` for
        them. Byte-identical to the test above but for `conversation_id`
        being set, proving that field changes nothing about this
        early return."""
        calls = []

        class _Index:
            def as_retriever(self, **kwargs):
                calls.append(kwargs)
                raise AssertionError("the retriever must not be built")

        monkeypatch.setattr(retrieval, "get_index", lambda *a, **k: _Index())
        # C-06: `retrieve_nodes` borrows its store from `tools.rag.index.
        # disposing_vector_store()`, which calls `get_vector_store()` in
        # `tools.rag.index`'s own module globals -- not `retrieval`'s copy
        # of the name (retrieval.py no longer even imports it).
        monkeypatch.setattr(rag_index, "get_vector_store",
                            lambda: type("S", (), {"hybrid_search": False})())
        monkeypatch.setattr(retrieval.gateway, "get_embed_model_for", lambda r, **k: object())
        nothing = DocumentVisibility(unrestricted=False, entitlement_ids=frozenset(),
                                     unlabelled_allowed=False,
                                     conversation_id="the-principals-own-conversation")
        nodes, hybrid, index = retrieval.retrieve_nodes(
            "a question", None, _settings_row(), embed_resolved=object(),
            visibility=nothing)
        assert nodes == []
        assert index is not None
        assert calls == []


class TestTheFilterShape:
    def test_an_unrestricted_principal_with_no_category_still_carries_the_containment_clause(
            self):
        """WAS `assert filters is None`. §6.2's `workstream` clause is
        appended in every branch, so the fast path is retired: an
        unrestricted principal outside a stream still gets one filter, and
        that filter is what keeps contained documents out of Ask and
        Search. Rewritten deliberately, not repaired."""
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True)
        filters = _filters(v)
        assert filters is not None
        # ROUND 12: the containment clause is now WRAPPED one level
        # deeper -- `_visibility_filters` ANDs a `conversation IS_EMPTY`
        # leg onto it (the round 12 amendment's own guard against a
        # chat-scoped chunk leaking into the universal corpus).
        assert len(filters.filters) == 1
        corpus = filters.filters[0]
        assert [(f.key, f.operator) for f in corpus.filters] == [
            ("workstream", FilterOperator.IS_EMPTY),
            ("conversation", FilterOperator.IS_EMPTY),
        ]

    def test_a_holder_gets_an_ANY_clause_of_decimal_strings(self):
        """ROUND 12 REVIEW I-3: the entitlement gate is now composed
        WITH the corpus formula, one level deeper than before, so a
        NON-`conversation_id` visibility's `built.filters[0]` is that
        AND-composition (`gated`) rather than the entitlement group
        itself -- `.filters[0].filters[0]` reaches it, see
        `_visibility_filters`'s own "THE GATED FORMULA" comment."""
        v = DocumentVisibility(False, frozenset({3, 7}), False)
        built = _filters(v)
        group = built.filters[0].filters[0]
        assert group.condition == FilterCondition.OR
        clause = group.filters[0]
        assert clause.key == "entitlements"
        assert clause.operator == FilterOperator.ANY
        assert clause.value == ["3", "7"]

    def test_every_value_passed_to_ANY_is_a_decimal_string(self):
        """The `ANY` clause builder INTERPOLATES its values into SQL as
        quoted strings rather than binding them. Ours are integers
        rendered as strings, straight off a database column, so the
        interpolation is safe BY CONSTRUCTION -- and this test is what
        makes a future refactor that passed an entitlement NAME fail
        loudly instead of quietly."""
        v = DocumentVisibility(False, frozenset({3, 7}), False)
        clause = _filters(v).filters[0].filters[0].filters[0]
        assert all(isinstance(value, str) and value.isdigit() for value in clause.value)

    def test_unlabelled_allowed_adds_an_IS_EMPTY_clause_ORed_with_it(self):
        v = DocumentVisibility(False, frozenset({3}), True)
        group = _filters(v).filters[0].filters[0]
        assert group.condition == FilterCondition.OR
        assert {clause.operator for clause in group.filters} == {
            FilterOperator.ANY, FilterOperator.IS_EMPTY}

    def test_unlabelled_only_is_an_IS_EMPTY_clause_alone(self):
        v = DocumentVisibility(False, frozenset(), True)
        group = _filters(v).filters[0].filters[0]
        assert [clause.operator for clause in group.filters] == [FilterOperator.IS_EMPTY]

    def test_a_category_is_AND_combined_with_the_visibility_group(self):
        v = DocumentVisibility(False, frozenset({3}), False)
        built = _filters(v, category="Finance")
        assert built.condition == FilterCondition.AND
        assert isinstance(built.filters[0], MetadataFilter)      # the category
        assert isinstance(built.filters[1], MetadataFilters)     # the visibility group


class TestTheConversationLeg:
    """Round 12 amendment (owner ruling, verbatim: "if I submit a
    document but have scope for chat, then it should only be used in
    that chat"). `_visibility_filters` layers a `conversation` leg ON
    TOP of the corpus formula `TestTheFilterShape` already pins,
    rather than weaving it into each of that formula's own legs -- see
    that function's own "ROUND 12 AMENDMENT" comment for why."""

    def test_no_conversation_id_means_no_extra_or_leg_at_all(self):
        """The common case (Search, Ask, `manage.py ask`, and every
        caller before this round): `conversation_id=None` -- the corpus
        wrap is AND-only (workstream/wall formula, AND conversation
        IS_EMPTY), never OR-wrapped with an EQ leg at all."""
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True)
        corpus = _filters(v).filters[0]
        assert corpus.condition == FilterCondition.AND
        keys = [f.key for f in corpus.filters]
        assert keys == ["workstream", "conversation"]
        conversation_clause = corpus.filters[1]
        assert conversation_clause.operator == FilterOperator.IS_EMPTY

    def test_a_conversation_id_ors_in_an_exact_match_leg(self):
        """A chat turn's own retrieval call (`tools.rag.tools.run_search`/
        `run_ask`, threading `ctx.conversation_id`): the corpus wrap
        gains an OUTER OR with an EQ leg naming this exact conversation
        -- guaranteed availability, no wall arithmetic, matching the
        owner's own ruling."""
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, conversation_id="abc-123")
        corpus = _filters(v).filters[0]
        assert corpus.condition == FilterCondition.OR
        assert len(corpus.filters) == 2
        and_wrap, eq_leg = corpus.filters
        assert and_wrap.condition == FilterCondition.AND
        assert eq_leg.key == "conversation"
        assert eq_leg.value == "abc-123"
        assert eq_leg.operator == FilterOperator.EQ

    def test_the_conversation_leg_survives_alongside_a_wall(self):
        """A WALLED stream turn's own retrieval call still gets the
        guaranteed EQ leg -- the wall narrows the UNIVERSAL leg inside
        the AND-wrap; it does not touch the OR-wrapped conversation leg
        beside it at all."""
        stream = WorkstreamScope(workstream_id=7, wall=frozenset({3}),
                                 default_upload_placement="", may_upload=False)
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, stream=stream,
                               conversation_id="turn-conv")
        corpus = _filters(v).filters[0]
        assert corpus.condition == FilterCondition.OR
        eq_leg = corpus.filters[1]
        assert (eq_leg.key, eq_leg.value, eq_leg.operator) == (
            "conversation", "turn-conv", FilterOperator.EQ)


class TestTheSignaturesAreRequired:
    def test_visibility_is_required_and_keyword_only_on_both(self):
        """REQUIRED, NOT DEFAULTED. A default of "see everything" is a
        fail-open default, and a caller that forgets it should fail at
        signature-checking time rather than at a security review two
        years later."""
        for function in (retrieval.retrieve_nodes, retrieval.answer_question):
            parameter = inspect.signature(function).parameters["visibility"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is inspect.Parameter.empty


class TestEveryCallerBuildsItsOwnVisibility:
    """FIVE CALLERS, ONE FILTER POINT. What each test asserts is that the
    caller PASSES a visibility built from the right subject -- not that
    retrieval works, which `TestTheFilterShape` above already covers
    without a live store.
    """

    @pytest.fixture
    def spy(self, monkeypatch):
        """Captures the `visibility` `retrieve_nodes` was handed.

        IT HANDS BACK A USABLE INDEX, not `None`. `answer_question`
        (`tools/rag/retrieval.py:623-650`) takes the `else` branch
        whenever the score floor is its default `0.0` -- which is every
        call here, since `nodes` is empty -- and that branch does
        `gateway.get_llm_for(answer_resolved)` and then
        `index.as_query_engine(llm=...)`. A `None` index raises
        `AttributeError`, and an unpatched gateway reaches a real engine.
        Both are stubbed here so the three tests below fail only for a
        reason about visibility.
        """
        seen = {}

        class _QueryEngine:
            def synthesize(self, *_args, **_kwargs):
                return type("R", (), {"response": "an answer", "source_nodes": []})()

        class _Index:
            def as_query_engine(self, **_kwargs):
                return _QueryEngine()

        def _fake(question, category, settings_row, *, embed_resolved, visibility,
                  request_timeout=None):
            seen["visibility"] = visibility
            return [], False, _Index()

        monkeypatch.setattr(retrieval, "retrieve_nodes", _fake)
        monkeypatch.setattr(
            retrieval.gateway, "get_llm_for", lambda _resolved, **_kwargs: object()
        )
        return seen

    @pytest.fixture
    def resolved(self, monkeypatch):
        """EVERY IN-PROCESS CALLER RESOLVES ITS ROLES BEFORE IT RETRIEVES,
        so the spy above is unreachable without this.

        - `tools/rag/jobs.py:239` -- `run_ask` opens with
          `resolved, causes, answer_name = _precheck(payload)` and raises
          `RuntimeError(model_unavailable_message(...))` when a role will
          not resolve or an endpoint fails health.
        - `tools/rag/tools.py:202` and `:273` -- both runners do a
          FUNCTION-LOCAL `resolve(...)` at call time and translate a
          `ValueError` into `ToolRefused` before `retrieve_nodes`/
          `answer_question` is ever called.

        Patched at each caller's own seam, exactly as
        `tools/rag/tests/test_tools.py:92` already patches
        `models.contracts.bindings.resolve` -- which works precisely
        because the runners' import is function-local. `SearchView` has
        its own stub already: `tools/rag/tests/_helpers.py::model_available`.
        """
        from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
        from tools.rag import jobs
        monkeypatch.setattr("models.contracts.bindings.resolve",
                            lambda role: ANSWER_RESOLVED if role == RAG_ANSWER_ROLE
                            else EMBED_RESOLVED)
        monkeypatch.setattr(
            jobs, "_precheck",
            lambda payload: ({RAG_ANSWER_ROLE: ANSWER_RESOLVED,
                              RAG_EMBED_ROLE: EMBED_RESOLVED}, {}, "the assigned model"))

    def test_the_ask_job_builds_it_from_the_payload_actor(self, spy, resolved):
        """`run_ask(payload, models, ctx)` -- THREE required positional
        parameters (`tools/rag/jobs.py:212`), none defaulted."""
        from identity.contracts.principals import payload_fields
        from tools.rag import jobs
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            jobs.run_ask(
                {"question": "q",
                 **payload_fields(Principal("user", str(member.pk)))},
                [], make_job_ctx())
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    def test_the_search_runner_builds_it_from_ctx_principal(self, spy, resolved):
        """`RAG_SEARCH`'s required param is `query`, not `question`
        (`tools/rag/tests/test_tools.py:98`); `validate_tool_args` refuses
        anything else."""
        from tools.rag import tools as rag_tools
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_search(
                {"query": "q"},
                make_tool_ctx(principal=Principal("user", str(member.pk))))
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    def test_the_search_runner_threads_ctx_conversation_id(self, spy, resolved):
        """Round 12 amendment: `run_search` threads `ctx.conversation_id`
        into `document_visibility`, the ONE thing that lets a
        conversation-scoped attachment reach THIS turn's own corpus."""
        from tools.rag import tools as rag_tools
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_search(
                {"query": "q"}, make_tool_ctx(conversation_id="turn-conv-id"))
        assert spy["visibility"].conversation_id == "turn-conv-id"

    def test_the_ask_runner_threads_ctx_conversation_id(self, spy, resolved):
        """The sibling of the search-runner pin above, for `run_ask`."""
        from tools.rag import tools as rag_tools
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_ask(
                {"question": "q"}, make_tool_ctx(conversation_id="turn-conv-id"))
        assert spy["visibility"].conversation_id == "turn-conv-id"

    def test_the_ask_runner_builds_it_from_ctx_principal(self, spy, resolved):
        """`RAG_ASK`'s param IS `question` (`tools/rag/tools.py:283`).

        T11 review: asserting only `unrestricted is False` is true for
        ANY non-open subject, including `ANONYMOUS` -- it would pass even
        if `ctx.principal` were silently dropped in favor of some other
        non-open stand-in. Granting a real entitlement and checking
        `entitlement_ids` proves the SUBJECT itself reached
        `document_visibility`, mirroring the two strong tests above."""
        from tools.rag import tools as rag_tools
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_ask(
                {"question": "q"},
                make_tool_ctx(principal=Principal("user", str(member.pk))))
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    @pytest.fixture
    def search_models(self):
        """`SearchView` resolves the embed role before it retrieves, and
        it binds `resolve` in `tools.rag.views` and `get_engine` in
        `tools.rag.messages` -- a different seam from the runners'
        function-local import, which is why this one test does not use
        the `resolved` fixture above.

        `tools/rag/tests/_helpers.py:85::model_available` is the stub this
        package already uses for exactly that. It is a GENERATOR FUNCTION
        -- the BODY of a fixture, not a fixture itself (its own docstring
        says so) -- so it is wrapped here, which is this repository's
        no-`conftest.py` convention: fixtures are defined per module and
        delegate their bodies to `_helpers`.
        """
        yield from model_available()

    def test_the_search_page_builds_it_from_the_request_principal(
            self, spy, search_models, client, monkeypatch):
        """`SearchView` short-circuits to the honest empty state WITHOUT
        calling `retrieve_nodes` at all when the live chunk table doesn't
        exist yet (`tools.rag.index.live_store_shape() is None`) -- true
        of every fresh test database, since nothing here ever ingests for
        real. `tools/rag/tests/test_views_ask.py`'s own `_live_store_exists`
        fixture patches around exactly this; this test does the same at
        its own seam so the spy above is actually reached.

        T11 review: asserting only `unrestricted is False` would pass for
        ANY non-open subject (`ANONYMOUS` included) -- granting a real
        entitlement to the signed-in member and checking `entitlement_ids`
        proves `principal_for_request(request)` is what actually reached
        `document_visibility`."""
        monkeypatch.setattr(
            "tools.rag.views.rag_index.live_store_shape",
            lambda: {"hybrid": False, "jsonb": False})
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            client.get(reverse("rag-search"), {"q": "a question"})
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    def test_the_ask_command_builds_it_from_the_service_principal_and_says_so(self):
        """It calls `answer_question` DIRECTLY, not through the queue, so
        without its own visibility the required argument would break the
        command outright.

        PATCHED AT THE COMMAND'S OWN SEAM, exactly as
        `tools/rag/tests/test_commands.py:38-52` already does: the module
        binds `resolve` and `answer_question` by name, so patching
        `tools.rag.management.commands.ask.resolve` and
        `...ask.answer_question` needs no bound role and no engine, and
        the `visibility` keyword is read straight off the recorded call.
        """
        from unittest.mock import patch
        from django.core.management import call_command
        make_admin()
        with posture(POSTURE_ENTERPRISE), \
                patch("tools.rag.management.commands.ask.resolve",
                      side_effect=_resolve_side_effect), \
                patch("tools.rag.management.commands.ask.answer_question",
                      return_value={"answer": "an answer", "citations": []}) as mock_answer:
            out = io.StringIO()
            call_command("ask", "a question", stdout=out)
        assert "unlabelled documents only" in out.getvalue()
        assert mock_answer.call_args.kwargs["visibility"].unrestricted is False

    def test_the_ask_command_says_so_differently_when_the_library_is_locked(self):
        """T11 review: `visibility.sees_nothing` (a LOCKED library, where
        even the unlabelled part is off limits to a service principal) is
        a DISTINCT, stronger case than merely "restricted" -- the
        unlabelled-only line would be dishonest here, since
        `answer_question` retrieves nothing at all. Same seam as the test
        above, `library_posture=LIBRARY_LOCKED` added."""
        from unittest.mock import patch
        from django.core.management import call_command
        make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED), \
                patch("tools.rag.management.commands.ask.resolve",
                      side_effect=_resolve_side_effect), \
                patch("tools.rag.management.commands.ask.answer_question",
                      return_value={"answer": "an answer", "citations": []}) as mock_answer:
            out = io.StringIO()
            call_command("ask", "a question", stdout=out)
        assert "no documents at all" in out.getvalue()
        assert "unlabelled documents only" not in out.getvalue()
        assert mock_answer.call_args.kwargs["visibility"].sees_nothing is True


class TestOneFilterPoint:
    def test_no_module_outside_retrieval_builds_a_metadata_filter(self):
        """A visibility rule that lived in a runner would be a SECOND
        copy, and two copies of a visibility rule is how two surfaces come
        to disagree about what a person may see. `tools/rag/index.py`'s
        `delete_chunks_for_document` is the one sanctioned exception: it
        filters by `file_id` to DELETE, never to read.

        T11 review: plain `git grep -l` only searches TRACKED files --
        this branch's own history already hit that exact blind spot (a
        new test file invisible to a tracked-only scan until `git add`),
        so `--untracked` is not optional here. The pathspec also widens
        from four trees to seven: `identity` (in case a future cascade or
        access helper grew its own filter), `posture` (a real top-level
        package in this repo), and `config` (URLconf/settings), none of
        which the original list named."""
        import subprocess
        from pathlib import Path
        repo = Path(__file__).resolve().parents[3]
        out = subprocess.run(
            ["git", "grep", "-l", "--untracked", "MetadataFilter", "--",
             "tools", "agents", "models", "foundation", "identity", "posture", "config"],
            cwd=repo, capture_output=True, text=True)
        offenders = {
            line for line in out.stdout.splitlines()
            if line and not line.startswith("tools/rag/tests/")
            and line not in {"tools/rag/retrieval.py", "tools/rag/index.py"}
        }
        assert offenders == set(), offenders
