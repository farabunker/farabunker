"""The RAG tools (spec section 5). Each runner calls the SAME service
function the page calls -- these tests patch that function and assert the
call, which is the gate the spec names for P1.

PRE-IMPORTS `tools.vision.services` below, BEFORE any test in this module
runs -- kept as belt-and-braces against a cross-app leak that was real
until PR #64, not a style choice. The leak: `run_search` calls
`retrieval._search_result_for`, which calls `reverse("rag-document-file",
...)`; the FIRST `reverse()` call in the whole test process makes Django
resolve the URLconf, which imports every app's `views.py` -- including
`tools/vision/views.py`, which does `from tools.vision import services`
at module scope. Before PR #64, `tools/vision/services.py` in turn did
`from models.contracts.bindings import resolve` at module scope, a NAME
bound once, at that import, never re-looked-up -- so a `with patch(...)`
on `models.contracts.bindings.resolve` around a `run_search` call could
lose the race against that one-time, cascading import and permanently
bind `tools.vision.services.resolve` to a stale `MagicMock`, a reference
no later, correctly-scoped patch could ever undo. PR #64 fixed the
premise: `tools/vision/services.py` now imports the *module*
(`from models.contracts import bindings`, `services.py:26` -- `:27`'s
by-name import of `ResolvedModel`/`config_family` is unchanged and
irrelevant, since nothing patches either) and calls `bindings.resolve(...)`
per call (`services.py:171`), so `patch("models.contracts.bindings.
resolve")` now reaches vision's call site correctly regardless of import
order. The `import tools.vision.services` pin below is kept anyway, as
belt-and-braces: a future `from ... import resolve` anywhere in the
URLconf's import cascade would reintroduce the same class of leak, and
the pin costs one import. Do not remove it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import tools.vision.services  # noqa: F401 -- see module docstring above
from agents.contracts.tools import ToolRefused, ToolResult, all_tools, get_tool, grantable_tools
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.jobkinds import resolve_dotted_path
from models.contracts.operations import ParamError
from tools.rag.tests._helpers import (  # noqa: F401
    isolated_tool_registry, make_admin, make_document, make_tool_ctx, make_user, posture,
    user_principal,
)

pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


class TestRegistration:
    def test_all_three_are_registered_by_ready(self):
        keys = {spec.key for spec in all_tools()}
        assert {"rag.search", "rag.ask", "rag.ingest"} <= keys

    def test_every_runner_resolves(self):
        """The dotted-path strings fail LAZILY -- a wrong one boots
        cleanly and throws on a worker."""
        for key in ("rag.search", "rag.ask", "rag.ingest"):
            assert callable(resolve_dotted_path(get_tool(key).runner))

    def test_declared_roles_match_what_each_runner_consumes(self):
        assert get_tool("rag.search").roles == ("rag.embed",)
        assert get_tool("rag.ask").roles == ("rag.answer", "rag.embed")
        assert get_tool("rag.ingest").roles == ("rag.embed",)

    def test_only_ingest_mutates_and_is_therefore_not_grantable(self):
        """`rag.ingest` REPLACES existing chunks, which is a change to
        state that already exists (ADR 0010:266-276). Registered,
        documented, and tested -- but not grantable until Identity & Auth."""
        assert get_tool("rag.ingest").mutates is True
        assert get_tool("rag.search").mutates is False
        assert get_tool("rag.ask").mutates is False
        grantable = {spec.key for spec in grantable_tools()}
        assert "rag.ingest" not in grantable
        assert {"rag.search", "rag.ask"} <= grantable

    def test_no_spec_declares_a_describer(self):
        """Ruling R3 -- no describer is implemented in P1, by anyone."""
        for key in ("rag.search", "rag.ask", "rag.ingest"):
            assert get_tool(key).describer == ""

    def test_category_is_a_text_param_not_a_choice(self):
        """`validate_params` requires a non-blank value for ANY choice
        param (operations.py:317-319), and "search every category" is the
        normal case. The LLM-facing schema is identical either way."""
        for key in ("rag.search", "rag.ask"):
            param = next(p for p in get_tool(key).params if p.key == "category")
            assert param.kind == "text"
            assert not param.required


@pytest.mark.django_db
class TestRagSearchRunner:
    def test_it_calls_retrieve_nodes_then_apply_score_floor(self):
        from tools.rag.tools import run_search

        node = MagicMock()
        with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
             patch("tools.rag.retrieval.apply_score_floor") as floor, \
             patch("models.contracts.bindings.resolve") as resolve:
            retrieve.return_value = ([node], False, MagicMock())
            floor.return_value = []
            run_search({"query": "anything"}, make_tool_ctx())

        assert retrieve.call_count == 1
        assert floor.call_count == 1
        assert resolve.call_args[0][0] == "rag.embed"

    def test_the_score_floor_and_hybrid_flag_come_from_settings_never_from_args(self):
        """Operator policy (ADR 0014 §14). A tool param that let a model
        lower the floor would be a settings mutation wearing a search
        tool's clothes."""
        from tools.rag.models import RagSettings
        from tools.rag.tools import RAG_SEARCH, run_search

        assert {p.key for p in RAG_SEARCH.params} == {"query", "category", "top_k"}

        row = RagSettings.get_solo()
        row.retrieval_score_floor = 0.42
        row.save(update_fields=["retrieval_score_floor"])

        with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
             patch("tools.rag.retrieval.apply_score_floor") as floor, \
             patch("models.contracts.bindings.resolve"):
            retrieve.return_value = ([], True, MagicMock())
            floor.return_value = []
            run_search({"query": "q"}, make_tool_ctx())

        assert floor.call_args[0][1] == 0.42
        assert floor.call_args[1]["hybrid"] is True

    def test_top_k_defaults_to_the_operators_configured_depth(self):
        from tools.rag.models import RagSettings
        from tools.rag.tools import run_search

        row = RagSettings.get_solo()
        row.retrieval_top_k = 9
        row.save(update_fields=["retrieval_top_k"])

        with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
             patch("tools.rag.retrieval.apply_score_floor", return_value=[]), \
             patch("models.contracts.bindings.resolve"):
            retrieve.return_value = ([], False, MagicMock())
            run_search({"query": "q"}, make_tool_ctx())
            settings_row = retrieve.call_args[0][2]
            assert settings_row.retrieval_top_k == 9

            run_search({"query": "q", "top_k": 3}, make_tool_ctx())
            assert retrieve.call_args[0][2].retrieval_top_k == 3

    def test_results_are_the_search_pages_own_dict_shape(self):
        """Not a fourth copy of the page/timestamp connector rule -- the
        tool and the search page must never drift."""
        from tools.rag import retrieval
        from tools.rag.tools import run_search

        node = MagicMock()
        node.node.metadata = {"file_id": "7", "file_name": "a.pdf", "page": 3}
        node.node.node_id = "n1"
        node.node.get_content.return_value = "some text"
        node.score = 0.9

        with patch("tools.rag.retrieval.retrieve_nodes", return_value=([node], False, MagicMock())), \
             patch("tools.rag.retrieval.apply_score_floor", return_value=[node]), \
             patch("models.contracts.bindings.resolve"):
            result = run_search({"query": "q"}, make_tool_ctx())

        assert result.data["results"] == [retrieval._search_result_for(node, hybrid=False)]

    def test_artifacts_are_deduped_document_references(self):
        from tools.rag.tools import run_search

        def node_for(file_id):
            node = MagicMock()
            node.node.metadata = {"file_id": file_id, "file_name": "a.pdf"}
            node.node.node_id = f"n-{file_id}"
            node.node.get_content.return_value = "text"
            node.score = 0.5
            return node

        nodes = [node_for("7"), node_for("7"), node_for("9")]
        with patch("tools.rag.retrieval.retrieve_nodes", return_value=(nodes, False, MagicMock())), \
             patch("tools.rag.retrieval.apply_score_floor", return_value=nodes), \
             patch("models.contracts.bindings.resolve"):
            result = run_search({"query": "q"}, make_tool_ctx())

        # R5 (chat-polish P3.1): the title `_document_artifacts` embeds
        # is the node's own `file_name` ("a.pdf" here, via
        # `retrieval._search_result_for`), quoted -- unchanged
        # characters for a plain filename with no special characters.
        assert result.artifacts == ("document:7:a.pdf", "document:9:a.pdf")

    def test_a_non_numeric_file_id_mints_no_artifact(self):
        """Mirrors `retrieval._document_url_for`'s own guard
        (retrieval.py:362-379): the `"document:<id>"` vocabulary only ever
        resolves through `<int:doc_id>`, so a stray non-numeric `file_id`
        in a chunk's stored metadata must not mint a reference that can
        never resolve -- degrade the artifact list, never crash on it."""
        from tools.rag.tools import run_search

        def node_for(file_id):
            node = MagicMock()
            node.node.metadata = {"file_id": file_id, "file_name": "a.pdf"}
            node.node.node_id = f"n-{file_id}"
            node.node.get_content.return_value = "text"
            node.score = 0.5
            return node

        nodes = [node_for("not-a-number"), node_for("7")]
        with patch("tools.rag.retrieval.retrieve_nodes", return_value=(nodes, False, MagicMock())), \
             patch("tools.rag.retrieval.apply_score_floor", return_value=nodes), \
             patch("models.contracts.bindings.resolve"):
            result = run_search({"query": "q"}, make_tool_ctx())

        assert result.artifacts == ("document:7:a.pdf",)

    def test_a_blank_query_raises_paramerror_with_a_per_arg_reason(self):
        from tools.rag.tools import run_search

        with pytest.raises(ParamError) as excinfo:
            run_search({"query": ""}, make_tool_ctx())
        assert "query" in excinfo.value.errors

    def test_an_unknown_category_is_refused_naming_what_exists(self):
        # NOTE: "Medical" (the brief's own example name) collides with
        # migration 0004's seeded default categories -- every test-db
        # transaction already has it, so `Category.objects.create(name=
        # "Medical")` raises `IntegrityError` before the runner is ever
        # exercised. "Botany" is not one of `0004_seed_default_categories.
        # DEFAULT_CATEGORIES`, so it proves the same behaviour without
        # the collision.
        from tools.rag.models import Category
        from tools.rag.tools import run_search

        Category.objects.create(name="Botany")
        with pytest.raises(ParamError) as excinfo:
            run_search({"query": "q", "category": "nonesuch"}, make_tool_ctx())
        assert "category" in excinfo.value.errors
        assert "Botany" in excinfo.value.errors["category"]

    def test_a_blank_category_searches_every_category(self):
        from tools.rag.tools import run_search

        with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
             patch("tools.rag.retrieval.apply_score_floor", return_value=[]), \
             patch("models.contracts.bindings.resolve"):
            retrieve.return_value = ([], False, MagicMock())
            run_search({"query": "q"}, make_tool_ctx())
        assert retrieve.call_args[0][1] is None

    def test_an_unbound_embed_role_is_a_refusal_not_a_bare_valueerror(self):
        """`bindings.resolve()`'s bare `ValueError` on an unbound role must
        not propagate as-is -- nothing the model can say fixes it, so it
        is translated to `ToolRefused` (section 10.1's no-retry class),
        mirroring `tools/vision/tools.py::run_generate`'s own
        `VisionUnavailable` -> `ToolRefused` translation."""
        from tools.rag.tools import run_search

        with patch("models.contracts.bindings.resolve", side_effect=ValueError("boom")):
            with pytest.raises(ToolRefused) as excinfo:
                run_search({"query": "q"}, make_tool_ctx())
        assert "embedding" in str(excinfo.value).lower()
        assert "boom" not in str(excinfo.value)  # the platform's own copy, never the raw exception

    def test_the_embedder_is_built_with_the_turns_remaining_budget(self):
        """Final review I-3 (fix round 3): `run_search` is called
        SYNCHRONOUSLY inside an agent turn -- the embedder `retrieve_
        nodes` builds must be bounded by what's LEFT of the turn's own
        budget, the same way `tools.vision.tools`'s own generate runner
        already bounds its wait, so the ollama embedder's hidden 300s
        default cannot compete with a longer operator-set response
        timeout. A tight, explicit deadline (50s out) proves the value
        passed through is DERIVED from the budget, not the engine's own
        300s default and not `None`."""
        import time as time_module

        from agents.contracts.tools import StepBudget
        from tools.rag.tools import run_search

        ctx = make_tool_ctx(budget=StepBudget(
            steps=8, deadline_monotonic=time_module.monotonic() + 50.0,
        ))
        with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
             patch("tools.rag.retrieval.apply_score_floor", return_value=[]), \
             patch("models.contracts.bindings.resolve"):
            retrieve.return_value = ([], False, MagicMock())
            run_search({"query": "q"}, ctx)

        passed = retrieve.call_args.kwargs["request_timeout"]
        assert passed is not None
        assert 0 < passed <= 50.0
        assert passed != 300.0  # never the engine's own hidden default


@pytest.mark.django_db
class TestRagAskRunner:
    def test_it_calls_answer_question_with_both_roles_resolved_fresh(self):
        """`run_ask` re-resolves at call time and documents why -- a
        queued job can sit for a while (ADR 0013:205-213)."""
        from tools.rag.tools import run_ask

        with patch("tools.rag.retrieval.answer_question") as answer, \
             patch("models.contracts.bindings.resolve") as resolve:
            answer.return_value = {"answer": "42", "citations": []}
            run_ask({"question": "why"}, make_tool_ctx())

        assert [c[0][0] for c in resolve.call_args_list] == ["rag.answer", "rag.embed"]
        assert answer.call_args[0][0] == "why"
        assert answer.call_args[1]["category"] is None

    def test_the_answer_is_the_text_and_citations_land_in_data(self):
        from tools.rag.tools import run_ask

        citations = [
            {"document_id": "7", "title": "a.pdf", "locator_text": ", p. 3", "score": 0.9},
            {"document_id": "9", "title": "b.md", "locator_text": "", "score": 0.7},
        ]
        with patch("tools.rag.retrieval.answer_question") as answer, \
             patch("models.contracts.bindings.resolve"):
            answer.return_value = {"answer": "Because.", "citations": citations}
            result = run_ask({"question": "why"}, make_tool_ctx())

        assert isinstance(result, ToolResult)
        assert result.text == "Because."
        assert result.data["citations"] == citations
        # R5: each citation's own "title" is embedded in its artifact.
        assert result.artifacts == ("document:7:a.pdf", "document:9:b.md")

    def test_it_passes_no_session_id(self):
        """The `session_id` parameter is retired in P2 (spec section 2.3).
        P1 must not start depending on it."""
        from tools.rag.tools import run_ask

        with patch("tools.rag.retrieval.answer_question") as answer, \
             patch("models.contracts.bindings.resolve"):
            answer.return_value = {"answer": "x", "citations": []}
            run_ask({"question": "why"}, make_tool_ctx())

        assert len(answer.call_args[0]) == 1          # question only, positionally
        assert "session_id" not in answer.call_args[1]

    def test_both_roles_unbound_is_a_refusal_with_the_generic_no_model_copy(self):
        """`bindings.resolve()`'s bare `ValueError` on an unbound role must
        not propagate as-is -- nothing the model can say fixes it, so it
        is translated to `ToolRefused` (section 10.1's no-retry class),
        mirroring `tools/vision/tools.py::run_generate`'s own
        `VisionUnavailable` -> `ToolRefused` translation. Both
        `rag.answer`/`rag.embed` unbound is the exact two-role cause dict
        `tools.rag.messages.model_unavailable_message`'s generic "no
        language model is connected yet" branch fires for."""
        from tools.rag.tools import run_ask

        with patch("models.contracts.bindings.resolve", side_effect=ValueError("boom")):
            with pytest.raises(ToolRefused) as excinfo:
                run_ask({"question": "why"}, make_tool_ctx())
        assert "No language model is connected yet" in str(excinfo.value)
        assert "boom" not in str(excinfo.value)

    def test_one_role_unbound_is_a_refusal_naming_that_role(self):
        from tools.rag.tools import run_ask

        def resolve_side_effect(role):
            if role == "rag.answer":
                raise ValueError("boom")
            return MagicMock(endpoint="http://x")

        with patch("models.contracts.bindings.resolve", side_effect=resolve_side_effect):
            with pytest.raises(ToolRefused) as excinfo:
                run_ask({"question": "why"}, make_tool_ctx())
        assert "chat" in str(excinfo.value).lower()

    def test_answer_question_is_built_with_the_turns_remaining_budget(self):
        """Final review I-3 (fix round 3): `run_ask` is called
        SYNCHRONOUSLY inside an agent turn -- both clients `answer_
        question` builds (the retrieval embedder and the answer LLM)
        must be bounded by what's LEFT of the turn's own budget, the
        same "derive from what's left, never a fixed ceiling" pattern
        `tools.vision.tools`'s own generate runner already uses. A
        tight, explicit deadline (50s out) proves the value passed
        through is DERIVED from the budget, not the engine's own 300s
        default and not `None`."""
        import time as time_module

        from agents.contracts.tools import StepBudget
        from tools.rag.tools import run_ask

        ctx = make_tool_ctx(budget=StepBudget(
            steps=8, deadline_monotonic=time_module.monotonic() + 50.0,
        ))
        with patch("tools.rag.retrieval.answer_question") as answer, \
             patch("models.contracts.bindings.resolve"):
            answer.return_value = {"answer": "x", "citations": []}
            run_ask({"question": "why"}, ctx)

        passed = answer.call_args.kwargs["request_timeout"]
        assert passed is not None
        assert 0 < passed <= 50.0
        assert passed != 300.0  # never the engine's own hidden default


@pytest.mark.django_db
class TestRagIngestRunner:
    def test_it_takes_a_document_id_and_nothing_else(self):
        """Store-path only, in the strongest available sense: it accepts
        NO path at all, so the model has zero filesystem reach."""
        from tools.rag.tools import RAG_INGEST

        assert [p.key for p in RAG_INGEST.params] == ["document_id"]
        assert RAG_INGEST.params[0].kind == "int"
        assert RAG_INGEST.params[0].required is True

    def test_it_calls_enqueue_reingest_and_returns_the_queue_job_id(self):
        from tools.rag.models import Document
        from tools.rag.tools import run_ingest

        doc = Document.objects.create(title="a.pdf", source_path="/x/a.pdf")
        with patch("tools.rag.ingest.enqueue_reingest", return_value=99) as enqueue:
            result = run_ingest({"document_id": doc.pk}, make_tool_ctx())

        assert enqueue.call_args[0][0].pk == doc.pk
        assert result.data["queue_job_id"] == 99
        assert result.data["document_id"] == doc.pk

    def test_it_stamps_the_turns_own_principal_as_the_actor(self):
        """Task 10, the acting rule part 1: this tool runs as the turn's
        `ctx.principal` -- WHO THIS IS BEING DONE FOR -- and that is
        exactly the principal the queued `rag.ingest` job should be
        attributed to."""
        from identity.contracts.principals import Principal
        from tools.rag.models import Document
        from tools.rag.tools import run_ingest

        doc = Document.objects.create(title="a.pdf", source_path="/x/a.pdf")
        ctx = make_tool_ctx(principal=Principal("user", "5"))
        with patch("tools.rag.ingest.enqueue_reingest", return_value=99) as enqueue:
            run_ingest({"document_id": doc.pk}, ctx)

        assert enqueue.call_args.kwargs["actor"] == Principal("user", "5")

    def test_it_enqueues_and_never_waits(self):
        """A tool runner must never block on a queue job. In sequential
        mode the child sits queued until the turn finishes and then runs;
        no deadlock, because nothing blocks."""
        import inspect

        from tools.rag import tools as tools_module

        source = inspect.getsource(tools_module.run_ingest)
        assert "get_job" not in source
        assert "wait_for" not in source

    def test_a_missing_document_is_an_honest_tool_error(self):
        from tools.rag.tools import run_ingest

        with pytest.raises(ValueError) as excinfo:
            run_ingest({"document_id": 999999}, make_tool_ctx())
        assert "999999" in str(excinfo.value)

    def test_a_missing_store_copy_surfaces_the_real_cause(self):
        """`enqueue_reingest` raises FileNotFoundError for a data-integrity
        problem distinct from "the queue is down". Surfaced, not
        swallowed."""
        from tools.rag.models import Document
        from tools.rag.tools import run_ingest

        doc = Document.objects.create(title="a.pdf", source_path="/nope/a.pdf")
        with pytest.raises(ValueError) as excinfo:
            run_ingest({"document_id": doc.pk}, make_tool_ctx())
        assert "store" in str(excinfo.value).lower()

    def test_a_queue_that_is_down_returns_a_honest_result_not_a_crash(self):
        """`enqueue_reingest` returns None when the queue refuses. The
        tool says so; it does not pretend work was queued."""
        from tools.rag.models import Document
        from tools.rag.tools import run_ingest

        doc = Document.objects.create(title="a.pdf", source_path="/x/a.pdf")
        with patch("tools.rag.ingest.enqueue_reingest", return_value=None):
            result = run_ingest({"document_id": doc.pk}, make_tool_ctx())
        assert result.data["queue_job_id"] is None
        assert "not queued" in result.text.lower()


@pytest.mark.django_db
class TestRunIngestAppliesTheDocumentPredicate:
    """S16. Every HTTP path to re-ingest applies `may_administer_document`
    (`tools.rag.views._administered_document`); this runner applied
    nothing and loaded by bare pk. It is unreachable today only because
    two fences in OTHER columns (`Agent._validate_tool_keys`,
    `granted_tools`) drop every `mutates=True` tool -- and both describe
    themselves as lasting only "until Identity & Auth lands" (ADR 0010),
    which it now has. `POSTURE_ENTERPRISE` is used throughout: under the
    default open posture `is_admin` returns True for every principal
    (nobody is hidden from anybody on a box with no accounts), which
    would make every one of these tests vacuous."""

    def test_a_principal_who_may_not_administer_gets_the_missing_row_answer(self):
        """The refusal and the not-found message must be
        INDISTINGUISHABLE, or the tool is an existence-and-title oracle
        over the whole library -- the more valuable half of the finding,
        because a poisoned document could walk the id space and report
        every title back into the conversation."""
        from tools.rag.tools import run_ingest

        with posture(POSTURE_ENTERPRISE):
            member = make_user()
            doc = make_document(title="secret.pdf")
            ctx = make_tool_ctx(principal=user_principal(member))

            with pytest.raises(ValueError) as refused:
                run_ingest({"document_id": doc.pk}, ctx)
            with pytest.raises(ValueError) as absent:
                run_ingest({"document_id": doc.pk + 10_000}, ctx)

        assert (str(refused.value).replace(str(doc.pk), "<id>")
                == str(absent.value).replace(str(doc.pk + 10_000), "<id>"))
        assert doc.title not in str(refused.value)

    def test_an_administrator_still_queues_the_reingest(self):
        from tools.rag.tools import run_ingest

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            doc = make_document()
            ctx = make_tool_ctx(principal=user_principal(admin))
            with patch("tools.rag.ingest.enqueue_reingest", return_value=7) as enqueue:
                result = run_ingest({"document_id": doc.pk}, ctx)

        assert enqueue.call_args[0][0].pk == doc.pk
        assert result.data["queue_job_id"] == 7

    def test_the_uploader_of_their_own_chat_scoped_document_still_queues_it(self):
        """`may_administer_document` is deliberately WIDER than
        `may_label_document` by exactly this clause -- the uploader of
        their own chat-scoped document, who owns no entitlement at all.
        Using the narrower predicate here would silently take the
        re-ingest button's equivalent away from the tool path."""
        from tools.rag.models import Document
        from tools.rag.tools import run_ingest

        with posture(POSTURE_ENTERPRISE):
            uploader = make_user()
            principal = user_principal(uploader)
            doc = make_document(scope=Document.Scope.CONVERSATION, **owner_fields(principal))
            ctx = make_tool_ctx(principal=principal)
            with patch("tools.rag.ingest.enqueue_reingest", return_value=9) as enqueue:
                result = run_ingest({"document_id": doc.pk}, ctx)

        assert enqueue.call_args[0][0].pk == doc.pk
        assert result.data["queue_job_id"] == 9

    def test_the_two_mutating_fences_are_untouched(self):
        """Anti-vacuous pin, and a statement of scope: this task adds the
        predicate; it does NOT lift either fence, and a future change
        that lifts them must not silently take this test with it."""
        from tools.rag.tools import RAG_INGEST

        assert RAG_INGEST.mutates is True
