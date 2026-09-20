"""Unit tests for the Ask/Search views in tools/rag/views.py (ADR 0008; T6: async Ask).

One of four modules `tools/rag/tests/test_views.py` split into by feature
area (C-56b): this one, `test_views_documents.py`,
`test_views_upload_and_settings.py`, and
`test_views_retrieval_settings_and_gating.py`. Unlike the registry
module's split (C-56a), this file's original had exactly one `# ---`
divider, so the cut follows class boundaries rather than dividers.

Uses Django's test client. AskView (T6) only enqueues -- it never calls
tools.rag.retrieval.answer_question itself anymore (that pipeline's own
correctness is test_retrieval.py's job; run_ask's re-check/record/answer
behavior is tools/rag/tests/test_jobs.py's job, including a real
enqueue -> worker-tick -> succeeded end-to-end test). This file's Ask
classes therefore mock `tools.rag.views.enqueue`/`get_job` (the queue
seam) rather than the retrieval pipeline, and never hit Ollama, the
pgvector index, or a real queue worker.

Also carries C-15's characterization tests for the shared endpoint-dedup
+ health-check prologue (`tools.rag.messages.unreachable_endpoints`),
pinned here because two of its three surfaces (`AskView`'s pre-check and
`run_ask`'s pre-run re-check) live in this file's Ask classes.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.utils import OperationalError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from identity.contracts.postures import POSTURE_ENTERPRISE
from models.registry.models import (
    ModelConnection, ModelSet, ModelSetEntitlement, ModelSetMember, RoleBinding,
)
from models.queue.models import CANCELLED, FAILED, QUEUED, RUNNING, SUCCEEDED, InferenceJob
from models.contracts.bindings import ResolvedModel, resolve as real_resolve
from models.contracts.queue import QueueUnavailable
from tools.rag import jobs
from tools.rag.models import Document, RagSettings
from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    bind_rag_roles, client, make_admin, make_entitlement, make_user, model_available, posture,
    post_ask, sign_in,
)


@pytest.mark.django_db
class TestAskPageView:
    def test_get_renders_question_form(self, client):
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert '<form id="ask-form">' in body
        assert 'name="question"' in body

    def test_get_renders_category_select_with_seeded_categories_and_uncategorized(self, client):
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert '<select id="category" name="category">' in body
        assert '<option value="">All categories</option>' in body
        for name in ("Medical", "Engineering", "Reference & Manuals", "Business"):
            escaped = escape(name)
            assert f'<option value="{escaped}">{escaped}</option>' in body
        assert '<option value="Uncategorized">Uncategorized</option>' in body

    def test_get_renders_a_link_into_the_settings_area(self, client):
        """UI-2: the bar names ONE door to the operator surfaces rather
        than naming the console itself. The route from a use-surface to
        "bind a model" is Settings -> Models, and `/settings/` forwards
        an administrator straight to Models -- so this is the same
        journey it always asserted, one hop longer and one link
        shorter."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f'href="{reverse("settings-index")}"' in body
        assert f'href="{reverse("inference-console")}"' not in body

    def test_get_renders_shared_nav_with_ask_marked_current(self, client):
        """The shared shell's app bar (foundation/templates/_shell.html)
        renders every destination on every page, with the current one
        marked via the `.current` class.

        BINDS THE TWO RAG ROLES FIRST (UI-1 decision 2): the Ask entry
        renders only when the answer AND embedding roles have a model
        bound, so a test about marking it current has to make it
        available. The always-shown entries -- Document library, Ask
        history, Queue, Settings -- need no binding, which is the point
        of asserting them here beside it."""
        bind_rag_roles()

        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f'href="{reverse("rag-ask-page")}"' in body
        assert f'href="{reverse("rag-documents")}"' in body
        assert f'href="{reverse("rag-history")}"' in body
        assert f'href="{reverse("jobs-queue")}"' in body
        assert f'href="{reverse("settings-index")}"' in body
        assert f'href="{reverse("rag-ask-page")}" class="current"' in body
        assert f'href="{reverse("rag-documents")}" class="current"' not in body
        assert f'href="{reverse("rag-history")}" class="current"' not in body
        assert f'href="{reverse("settings-index")}" class="current"' not in body

    def test_the_nav_offers_no_ask_or_search_entry_while_nothing_is_bound(self, client):
        """UI-1 decision 2, stated as its own claim: with no role bound,
        the two model-backed RAG entries are absent -- while Document
        library, Ask history, Queue and Settings stay, because those are
        storage, a record, what the box is doing, and the one door to
        the surfaces an operator fixes "nothing is bound" WITH."""
        body = client.get(reverse("rag-ask-page")).content.decode()

        assert ">Ask</a>" not in body
        assert ">Search</a>" not in body
        assert ">Document library</a>" in body
        assert ">Ask history</a>" in body
        assert ">Queue</a>" in body
        assert ">Settings</a>" in body

    def test_renders_no_citations_copy_in_the_results_js(self, client):
        """W4 (ADR 0014 §14): the score-floor all-below short-circuit
        returns zero citations for a successfully-answered question --
        `renderResults`'s existing `citations.length === 0` branch (never
        touched by W4) already renders "(no citations)" instead of an
        empty list, so a floor short-circuit's answer renders exactly like
        any other zero-citation answer, not as an error state."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        assert '"(no citations)"' in response.content.decode()

    def test_no_chat_connections_and_no_env_override_renders_no_picker(self, client):
        """Empty-state rule: with nothing chat-capable registered
        and no explicit environment override, the Model <select> is absent
        entirely -- not rendered with zero/filler options."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="connection"' not in body

    def test_picker_lists_chat_connections_in_picker_order_with_descriptor_labels(self, client):
        ModelConnection.objects.create(
            name="b conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"], rank=2,
        )
        ModelConnection.objects.create(
            name="a conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"], rank=1, descriptor="light + fast",
        )
        ModelConnection.objects.create(
            name="embed only", engine="ollama", endpoint="http://e:1", model_id="m2",
            capabilities=["embeddings"], embed_dim=768,
        )

        response = client.get(reverse("rag-ask-page"))

        body = response.content.decode()
        assert '<select id="connection" name="connection">' in body
        # rank 1 ("a conn", descriptored) before rank 2 ("b conn"); the
        # embeddings-only connection never appears at all.
        a_pos = body.index("a conn — light + fast")
        b_pos = body.index(">b conn<")
        assert a_pos < b_pos
        assert "embed only" not in body

    def test_picker_marks_bound_connection_primary_and_preselected(self, client):
        connection = ModelConnection.objects.create(
            name="workstation llama", engine="ollama", endpoint="http://e:1",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = client.get(reverse("rag-ask-page"))

        body = response.content.decode()
        assert f'<option value="{connection.pk}" selected>workstation llama (primary)</option>' in body

    @override_settings(LLM_MODEL="llama3.1:8b", OLLAMA_BASE_URL="http://localhost:11434")
    def test_picker_env_override_primary_is_a_blank_value_option(self, client):
        """No connection is bound to rag.answer, but an explicit env
        override resolves it -- the picker's first option stands in for
        "use the role path" (value="", selected)."""
        ModelConnection.objects.create(
            name="unbound chat conn", engine="ollama", endpoint="http://e:1",
            model_id="m", capabilities=["chat"],
        )

        response = client.get(reverse("rag-ask-page"))

        body = response.content.decode()
        assert '<option value="" selected>llama3.1:8b (environment override)</option>' in body
        assert "unbound chat conn" in body

    def test_single_chat_connection_still_renders_as_a_select(self, client):
        """Built-to-scale ruling: a lone option is never collapsed to plain
        text or a filler-free single-option select stays a real <select>."""
        connection = ModelConnection.objects.create(
            name="only chat conn", engine="ollama", endpoint="http://e:1",
            model_id="m", capabilities=["chat"],
        )

        response = client.get(reverse("rag-ask-page"))

        body = response.content.decode()
        assert '<select id="connection" name="connection">' in body
        assert f'<option value="{connection.pk}"' in body

    def test_renders_priority_field_with_no_placeholder_or_suggested_value(self, client):
        """T6: the optional priority field is a bare number input -- no
        `placeholder`/`value` implying a number the operator should type,
        since the whole point is that blank is a legitimate, first-class
        choice (the queue's own default), not an omission."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        # The exact tag, verbatim -- proves no placeholder/value attribute
        # snuck onto this specific input (other fields on the page, e.g.
        # the question textarea, have their own unrelated placeholder).
        assert '<input type="number" min="1" step="1" id="priority" name="priority">' in body

    def test_never_hardcodes_the_ask_job_status_path(self, client):
        """T6: the poller must read `status_url` off the enqueue response,
        never construct `/rag/ask/jobs/<id>/` itself -- so that literal path
        segment must never appear in the page's own markup/script."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert "/rag/ask/jobs/" not in body

    def test_no_tabular_documents_renders_no_note(self, client):
        """W2 (ADR 0014 §18): the honesty note is server-rendered ONLY
        when the library actually holds a tabular Document -- an install
        with none never sees a note about a shape of document it doesn't
        have."""
        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        assert "aren't searched" not in response.content.decode()

    def test_tabular_documents_present_renders_the_honesty_note(self, client):
        Document.objects.create(
            title="Sales.csv",
            source_path="/tmp/sales.csv",
            file_hash="a" * 64,
            doc_type=Document.DocType.TABULAR,
            media_type="text/csv",
        )
        Document.objects.create(
            title="Inventory.xlsx",
            source_path="/tmp/inventory.xlsx",
            file_hash="b" * 64,
            doc_type=Document.DocType.TABULAR,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        body = response.content.decode()
        assert (
            "2 table file(s) in the library aren't searched — tables are stored but not "
            "part of retrieval yet." in body
        )

    @pytest.mark.parametrize("status", [Document.Status.FAILED, Document.Status.PENDING])
    def test_failed_or_pending_tabular_documents_are_not_counted(self, client, status):
        """W2/W6 review MINOR 5: a FAILED or still-PENDING tabular row was
        never actually stored -- counting it toward `tabular_count` would
        tell an operator "N table file(s) ... are stored" when N includes
        rows that aren't stored at all (yet, or ever)."""
        Document.objects.create(
            title="Sales.csv",
            source_path="/tmp/sales.csv",
            file_hash="a" * 64,
            doc_type=Document.DocType.TABULAR,
            media_type="text/csv",
            status=status,
        )

        response = client.get(reverse("rag-ask-page"))

        assert response.status_code == 200
        assert "aren't searched" not in response.content.decode()


@pytest.mark.django_db
class TestAskView:
    """The pre-check matrix (T6: unchanged by the sync->async move -- a
    pre-check failure means AskView returns its 503 and enqueues
    NOTHING, exactly like it returned its 503 and answered nothing before).
    """

    @pytest.fixture(autouse=True)
    def _model_available(self):
        """AskView pre-checks resolve()/is_healthy() before enqueuing.
        Stub both, at the names bound in tools.rag.views, so every test in
        this class transits the pre-check without touching the network --
        matching this file's "never hit Ollama" convention (see module
        docstring). Tests below that exercise the pre-check itself layer
        their own @patch on top of this default, which takes precedence for
        the duration of that test.
        """
        yield from model_available()

    def _post(self, client, payload):
        return post_ask(client, payload)

    @patch("tools.rag.views.enqueue")
    def test_missing_question_returns_400(self, mock_enqueue, client):
        response = self._post(client, {})

        assert response.status_code == 400
        assert "error" in response.json()
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    def test_empty_question_returns_400(self, mock_enqueue, client):
        response = self._post(client, {"question": "   "})

        assert response.status_code == 400
        assert "error" in response.json()
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    def test_non_string_question_returns_400(self, mock_enqueue, client):
        response = self._post(client, {"question": 123})

        assert response.status_code == 400
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.views.resolve")
    def test_unresolvable_binding_returns_503_with_friendly_message(
        self, mock_resolve, mock_enqueue, client
    ):
        mock_resolve.side_effect = ValueError("No inference binding resolved for role 'rag.answer'")

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "No language model is connected yet — set one up"
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()

    @override_settings(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)
    @patch("tools.rag.views.resolve", new=real_resolve)
    @patch("tools.rag.views.enqueue")
    def test_a_box_with_nothing_assigned_degrades_through_the_real_resolver(
        self, mock_enqueue, client
    ):
        """No regression in the cause-accurate 503
        matrix now that there are no baked model defaults.

        The class's autouse `_model_available` stub is deliberately UNDONE
        here (`new=real_resolve`) so this walks the real resolver with the
        settings made explicit (no override) and an empty registry -- the
        exact state of a fresh box. Both roles come back unbound, so the
        matrix's generic branch fires and nothing is ever enqueued. The
        copy never mentions "defaults", because none exist."""
        RoleBinding.objects.all().delete()
        ModelConnection.objects.all().delete()

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "No language model is connected yet — set one up"
        assert "default" not in data["error"].lower()
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.views.resolve")
    def test_unhealthy_engine_returns_503_with_friendly_message(
        self, mock_resolve, mock_get_engine, mock_enqueue, client
    ):
        """Both roles resolve to the SAME endpoint (mock_resolve.return_value
        is a single constant, reused for every role) and that shared
        endpoint fails its health check. Message rule: name the endpoint,
        not an arbitrary role -- they're equally affected -- so this asserts
        the endpoint-level wording, not the old generic "no model connected"
        copy (that copy is reserved for "nothing is bound at all"; here a
        binding *is* bound, it's just unreachable)."""
        mock_resolve.return_value = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False
        mock_get_engine.return_value = mock_engine

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == (
            "The model endpoint is unreachable — check that your model server is running."
        )
        assert data["setup_url"] == reverse("inference-console")
        mock_get_engine.assert_called_once_with("ollama")
        mock_engine.is_healthy.assert_called_once_with("http://localhost:11434")
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.views.resolve")
    def test_unexpected_resolve_error_logs_and_returns_friendly_503(
        self, mock_resolve, mock_enqueue, client, caplog
    ):
        """An unexpected failure (broken INFERENCE_BINDING_PROVIDER path, DB
        error, etc.) must not look identical to "no model connected" on the
        server side -- it should still get the friendly response, but also
        leave a log record an operator can find."""
        mock_resolve.side_effect = RuntimeError("bad INFERENCE_BINDING_PROVIDER dotted path")

        with caplog.at_level(logging.ERROR, logger="tools.rag.views"):
            response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "No language model is connected yet — set one up"
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()

        assert any(
            "bad INFERENCE_BINDING_PROVIDER dotted path" in record.getMessage()
            or record.exc_info is not None
            for record in caplog.records
        )

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.views.resolve")
    def test_embed_unhealthy_answer_healthy_names_embedding_role(
        self, mock_resolve, mock_get_engine, mock_enqueue, client
    ):
        """rag.answer and rag.embed resolve to different endpoints; only the
        embedding one is unhealthy. The message must name the embedding
        model specifically rather than the old generic "no model connected"."""

        def resolve_side_effect(role):
            if role == "rag.answer":
                return ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
            return ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435")

        mock_resolve.side_effect = resolve_side_effect

        mock_engine = MagicMock()
        mock_engine.is_healthy.side_effect = lambda endpoint: endpoint == "http://localhost:11434"
        mock_get_engine.return_value = mock_engine

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "The embedding model is unreachable."
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.views.resolve")
    def test_embed_unbound_answer_healthy_names_setup_not_unreachable(
        self, mock_resolve, mock_get_engine, mock_enqueue, client
    ):
        """rag.answer resolves and is healthy; rag.embed has no binding at
        all (resolve() raises ValueError for it). The cause is "not set up",
        not "unreachable" -- these call for different operator action, so
        the message must say the former and must not say the latter, and
        must not fall back to the "No language model is connected" header
        (that would overstate the breakage: the chat model IS connected)."""

        def resolve_side_effect(role):
            if role == "rag.answer":
                return ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
            raise ValueError("No inference binding resolved for role 'rag.embed'")

        mock_resolve.side_effect = resolve_side_effect

        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "The embedding model isn't set up yet — assign one in the model console."
        assert "unreachable" not in data["error"].lower()
        assert "No language model is connected" not in data["error"]
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()


@pytest.mark.django_db
class TestAskEnqueue:
    """T6: AskView's own job -- validate, pre-check, then
    `models.contracts.queue.enqueue("rag.ask", payload, priority=...)` and
    return 202. `enqueue`/`get_job` are the names bound in
    `tools.rag.views` (the queue seam), mocked here the same way
    `resolve`/`get_engine` are mocked for the pre-check -- never a real
    queue row, never a real worker (that's `tools/rag/tests/test_jobs.py`
    and `models/queue/tests/test_worker.py`'s job).
    """

    @pytest.fixture(autouse=True)
    def _model_available(self):
        yield from model_available()

    def _post(self, client, payload):
        return post_ask(client, payload)

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_202_body_carries_all_five_keys_and_a_resolvable_status_url(
        self, mock_enqueue, mock_get_job, client
    ):
        mock_enqueue.return_value = 42
        mock_get_job.return_value = SimpleNamespace(position=3, priority=100)

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 202
        data = response.json()
        assert data.keys() == {"job_id", "state", "position", "priority", "status_url"}
        assert data["job_id"] == 42
        assert data["state"] == "queued"
        assert data["position"] == 3
        assert data["priority"] == 100
        assert data["status_url"] == reverse("rag-ask-status", args=[42])

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_an_open_box_records_the_open_principal_as_the_actor(
        self, mock_enqueue, mock_get_job, client
    ):
        """Task 10, the acting rule part 1. Not a blank: every job on an
        open box has an honest actor, which is what makes
        `models/queue/visibility.py` able to reason about it later."""
        mock_enqueue.return_value = 42
        mock_get_job.return_value = SimpleNamespace(position=3, priority=100)

        self._post(client, {"question": "what is the answer?"})

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("open", "box")

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_a_signed_in_user_records_their_own_principal_as_the_actor(
        self, mock_enqueue, mock_get_job, client
    ):
        from identity.contracts.postures import POSTURE_PERSONAL
        from tools.rag.tests._helpers import make_user, posture, sign_in

        mock_enqueue.return_value = 42
        mock_get_job.return_value = SimpleNamespace(position=3, priority=100)
        user = make_user()

        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            self._post(client, {"question": "what is the answer?"})

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("user", str(user.pk))

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_healthy_engine_proceeds_to_enqueue(self, mock_enqueue, mock_get_job, client):
        # The class's autouse `_model_available` already stubs
        # `resolve`/`get_engine` healthy, so this only needs to add
        # `enqueue`/`get_job`.
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 202
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args.args[0] == "rag.ask"

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.views.resolve")
    def test_both_roles_healthy_at_distinct_endpoints_enqueues_and_checks_each_once(
        self, mock_resolve, mock_get_engine, mock_enqueue, mock_get_job, client
    ):
        """Distinct endpoints, both healthy: the pre-check must health-check
        each one (no false dedup across genuinely different endpoints) and
        still let a normal request through to the queue."""

        def resolve_side_effect(role):
            if role == "rag.answer":
                return ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
            return ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435")

        mock_resolve.side_effect = resolve_side_effect

        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        response = self._post(client, {"question": "what is the answer?"})

        assert response.status_code == 202
        assert mock_engine.is_healthy.call_count == 2
        checked_endpoints = {call.args[0] for call in mock_engine.is_healthy.call_args_list}
        assert checked_endpoints == {"http://localhost:11434", "http://localhost:11435"}
        mock_enqueue.assert_called_once()

    @patch("tools.rag.views.enqueue")
    def test_precheck_failure_never_calls_enqueue(self, mock_enqueue, client):
        with patch("tools.rag.views.resolve", side_effect=ValueError("nope")):
            response = self._post(client, {"question": "q"})

        assert response.status_code == 503
        mock_enqueue.assert_not_called()

    @pytest.mark.parametrize(
        "raw_priority, expected_error",
        [
            ("abc", "Priority must be a whole number."),
            ("1.5", "Priority must be a whole number."),
            ("0", "Priority must be a positive number."),
            ("-1", "Priority must be a positive number."),
        ],
    )
    @patch("tools.rag.views.enqueue")
    def test_invalid_priority_returns_400_and_never_enqueues(
        self, mock_enqueue, client, raw_priority, expected_error
    ):
        response = self._post(client, {"question": "q", "priority": raw_priority})

        assert response.status_code == 400
        assert response.json()["error"] == expected_error
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_blank_or_absent_priority_passes_none_to_enqueue(self, mock_enqueue, mock_get_job, client):
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        self._post(client, {"question": "q"})

        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_valid_priority_string_still_parses_but_is_ignored_by_queue_policy(
        self, mock_enqueue, mock_get_job, client
    ):
        """RETARGETED, C-4 review round 1 (constraint-26 waiver): this
        test used to pin a well-formed `priority` being forwarded to
        `enqueue()` -- exactly the vulnerability C-4 closes. `priority`
        still parses (a malformed one is still `TestAskEnqueue`'s own
        400, above), but the value `models.contracts.queue.
        resolve_client_priority` resolves to is `None` for every caller,
        so `enqueue()` never sees the client's `3`."""
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=3)

        response = self._post(client, {"question": "q", "priority": "3"})

        assert response.status_code == 202
        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_valid_priority_as_a_raw_json_int_is_also_ignored_by_queue_policy(
        self, mock_enqueue, mock_get_job, client
    ):
        """RETARGETED, C-4 review round 1 (constraint-26 waiver, same
        reason as the string-priority test above). The Ask page's own JS
        always sends `priority` as a string (an `<input>`'s `.value`),
        but a non-browser caller hitting this JSON API directly may
        reasonably send a raw JSON number instead of a numeric string --
        `int(priority_param)` accepts both, so this path (never
        exercised by the string-priority test above) needs its own
        coverage that it, too, resolves to `None` rather than the
        client's `3`."""
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=3)

        response = self._post(client, {"question": "q", "priority": 3})

        assert response.status_code == 202
        assert mock_enqueue.call_args.kwargs["priority"] is None

    @patch("tools.rag.views.enqueue")
    def test_queue_unavailable_returns_503_with_migrations_copy(self, mock_enqueue, client):
        mock_enqueue.side_effect = QueueUnavailable("relation does not exist")

        response = self._post(client, {"question": "q"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == (
            "The queue isn't ready yet — run database migrations, then ask again."
        )
        assert data["setup_url"] == reverse("inference-console")

    @patch("tools.rag.views.enqueue")
    def test_generic_enqueue_failure_returns_503_nothing_queued_copy(
        self, mock_enqueue, client, caplog
    ):
        mock_enqueue.side_effect = RuntimeError("db exploded")

        with caplog.at_level(logging.ERROR, logger="tools.rag.views"):
            response = self._post(client, {"question": "q"})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "Couldn't add your question to the queue — nothing was queued."
        )
        assert any(record.exc_info is not None for record in caplog.records)

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_payload_passed_to_enqueue_has_stripped_question_category_and_connection(
        self, mock_enqueue, mock_get_job, client
    ):
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)
        connection = ModelConnection.objects.create(
            name="picked model", engine="ollama", endpoint="http://picked:11434",
            model_id="qwen3:30b", capabilities=["chat"],
        )

        self._post(
            client,
            {
                "question": "  what is the answer?  ",
                "category": "Medical",
                "connection": str(connection.pk),
            },
        )

        args, kwargs = mock_enqueue.call_args
        assert args[0] == "rag.ask"
        payload = args[1]
        assert payload["question"] == "what is the answer?"
        assert payload["category"] == "Medical"
        assert payload["connection"] == str(connection.pk)

    @patch("tools.rag.views.get_job")
    @patch("tools.rag.views.enqueue")
    def test_missing_category_and_connection_are_not_forced_into_the_payload(
        self, mock_enqueue, mock_get_job, client
    ):
        mock_enqueue.return_value = 1
        mock_get_job.return_value = SimpleNamespace(position=1, priority=100)

        self._post(client, {"question": "q"})

        payload = mock_enqueue.call_args.args[1]
        assert payload["category"] is None
        assert "connection" not in payload


@pytest.mark.django_db
class TestAskViewConnectionOverride:
    """AskView's `connection` POST field, on the validation/pre-check side
    only (T6: the resolved model no longer answers anything inline here --
    `tools/rag/tests/test_jobs.py` proves the picked connection is
    re-resolved and actually used once the job runs). Uses real
    `ModelConnection` rows (native, per this repo's HTTP-layer-mocks
    convention) resolved through the real `resolve_connection_named` --
    only the network boundary (`resolve` for the role path, `get_engine`/
    `is_healthy`, the queue seam) is mocked, matching `TestAskView` above.
    """

    @pytest.fixture(autouse=True)
    def _model_available(self):
        """Same default-healthy stub as `TestAskView`'s -- `rag.embed`
        (and `rag.answer` on the un-overridden path) always resolves to this
        one endpoint and passes its health check unless a test overrides it."""
        yield from model_available()

    def _post(self, client, payload):
        return post_ask(client, payload)

    @patch("tools.rag.views.enqueue")
    def test_unknown_connection_pk_returns_cause_accurate_503(self, mock_enqueue, client):
        response = self._post(client, {"question": "q", "connection": "999999"})

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == (
            "That model is no longer registered — pick another in the model console."
        )
        assert data["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    def test_a_forbidden_connection_pk_returns_403_not_the_503(self, mock_enqueue, client):
        """IA-2 T14 review finding 1: a registered connection this
        account may not USE is a DIFFERENT fact from one that is gone --
        "no longer registered" would be a false sentence, and the 503's
        `setup_url` would hand the member a link to `inference-console`,
        a page that 403s them too."""
        connection = ModelConnection.objects.create(
            name="restricted", engine="ollama", endpoint="http://e:1",
            model_id="m", capabilities=["chat"],
        )
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = self._post(client, {"question": "q", "connection": str(connection.pk)})

        assert response.status_code == 403
        data = response.json()
        assert data["error"] == (
            "That model needs an entitlement this account does not hold — pick another."
        )
        assert "setup_url" not in data
        assert reverse("inference-console") not in response.content.decode()
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    def test_non_chat_connection_pk_returns_cause_accurate_503(self, mock_enqueue, client):
        connection = ModelConnection.objects.create(
            name="embed only", engine="ollama", endpoint="http://e:1",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._post(client, {"question": "q", "connection": str(connection.pk)})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "That model is no longer registered — pick another in the model console."
        )
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    def test_non_integer_connection_value_returns_cause_accurate_503(self, mock_enqueue, client):
        response = self._post(client, {"question": "q", "connection": "not-a-pk"})

        assert response.status_code == 503
        assert response.json()["error"] == (
            "That model is no longer registered — pick another in the model console."
        )
        mock_enqueue.assert_not_called()

    @patch("tools.rag.views.enqueue")
    @patch("tools.rag.messages.get_engine")
    def test_unreachable_picked_endpoint_names_chat_not_the_role_binding(
        self, mock_get_engine, mock_enqueue, client
    ):
        """The picked connection's OWN endpoint fails health -- reusing the
        pre-check machinery names "chat" (never a model name/version), the
        same wording an unreachable role binding would get, not a stale
        message about whatever the role happens to be bound to."""
        connection = ModelConnection.objects.create(
            name="picked model", engine="ollama", endpoint="http://picked:11434",
            model_id="qwen3:30b", capabilities=["chat"],
        )
        mock_engine = MagicMock()
        mock_engine.is_healthy.side_effect = lambda endpoint: endpoint != "http://picked:11434"
        mock_get_engine.return_value = mock_engine

        response = self._post(client, {"question": "q", "connection": str(connection.pk)})

        assert response.status_code == 503
        assert response.json()["error"] == "The chat model is unreachable."
        assert response.json()["setup_url"] == reverse("inference-console")
        mock_enqueue.assert_not_called()


@pytest.mark.django_db
class TestAskJobStatus:
    """GET /rag/ask/jobs/<job_id>/ (T6) -- `InferenceJob` rows are created
    directly here (production `tools/rag` code may never import
    `models.queue.models`, but tests may -- see tools/rag/views.py's
    module-boundary docstring), so these tests never depend on a real
    queue worker (that's tools/rag/tests/test_jobs.py's
    `TestEndToEndViaWorker`)."""

    def _url(self, job_id):
        return reverse("rag-ask-status", args=[job_id])

    def test_unknown_job_id_returns_404_with_history_link(self, client):
        response = client.get(self._url(999999))

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == (
            "That question is no longer in the queue — check Ask history for the answer."
        )
        assert data["history_url"] == reverse("rag-history")

    def test_queued_state_reports_position_priority_and_submitted_at(self, client):
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=QUEUED, payload={"question": "q"}
        )

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "queued"
        assert data["position"] == 1
        assert data["priority"] == 100
        assert "submitted_at" in data

    def test_queued_position_counts_only_jobs_strictly_ahead(self, client):
        InferenceJob.objects.create(kind="rag.ask", priority=50, state=QUEUED, payload={})
        job = InferenceJob.objects.create(kind="rag.ask", priority=100, state=QUEUED, payload={})

        response = client.get(self._url(job.pk))

        assert response.json()["position"] == 2

    def test_running_state_reports_started_at_and_answered_by_from_model_id(self, client):
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=RUNNING, payload={"question": "q"},
            started_at=timezone.now(),
            model_refs=[
                {
                    "role": "rag.answer", "engine": "ollama", "endpoint": "http://e:1",
                    "model_id": "llama3.1:8b", "connection_name": "", "footprint_bytes": None,
                },
                {
                    "role": "rag.embed", "engine": "ollama", "endpoint": "http://e:1",
                    "model_id": "nomic-embed-text", "connection_name": "", "footprint_bytes": None,
                },
            ],
        )

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "running"
        assert "started_at" in data
        assert data["answered_by"] == "llama3.1:8b"

    def test_running_state_carries_progress_when_the_job_has_reported_one(self, client):
        """T3: `progress` is an ADDITIVE key on the running-state body --
        `InferenceJob.progress` verbatim, no reshaping."""
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=RUNNING, payload={"question": "q"},
            started_at=timezone.now(),
            progress={"done": 3, "total": 10, "unit": "items", "label": "chunks"},
        )

        response = client.get(self._url(job.pk))

        assert response.json()["progress"] == {
            "done": 3, "total": 10, "unit": "items", "label": "chunks",
        }

    def test_running_state_progress_is_none_when_never_reported(self, client):
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=RUNNING, payload={"question": "q"},
            started_at=timezone.now(),
        )

        response = client.get(self._url(job.pk))

        assert response.json()["progress"] is None

    def test_running_state_prefers_connection_name_when_overridden(self, client):
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=RUNNING, payload={"question": "q"},
            model_refs=[
                {
                    "role": "rag.answer", "engine": "ollama", "endpoint": "http://picked:1",
                    "model_id": "qwen3:30b", "connection_name": "picked model",
                    "footprint_bytes": None,
                },
            ],
        )

        response = client.get(self._url(job.pk))

        assert response.json()["answered_by"] == "picked model"

    def test_cancelled_state_reports_only_state(self, client):
        job = InferenceJob.objects.create(kind="rag.ask", priority=100, state=CANCELLED, payload={})

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        assert response.json() == {"state": "cancelled"}

    def test_failed_state_reports_error_and_setup_url(self, client):
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=FAILED, payload={},
            error="No language model is connected yet — set one up",
        )

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "failed"
        assert data["error"] == "No language model is connected yet — set one up"
        assert data["setup_url"] == reverse("inference-console")

    def test_failed_state_hides_the_error_when_content_is_hidden(self, client):
        """`error` is content -- it can quote payload back -- so an
        administrator with `admin_sees_content` off polling someone
        else's failed job sees no error text, the same rule the queue
        page's `_present_row` applies to `summary`."""
        admin = make_admin()
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=FAILED,
            payload={"question": "a private question", "actor_kind": "user", "actor_key": "99999999"},
            error="could not answer: a private question",
        )
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            data = client.get(self._url(job.pk)).json()

        assert data["state"] == "failed"
        assert data["content_hidden"] is True
        assert data["error"] == ""
        assert "a private question" not in str(data)
        assert data["setup_url"] == reverse("inference-console")

    def test_failed_state_still_shows_the_error_when_content_is_visible(self, client):
        admin = make_admin()
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=FAILED,
            payload={"question": "a private question", "actor_kind": "user", "actor_key": "99999999"},
            error="could not answer: a private question",
        )
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            data = client.get(self._url(job.pk)).json()

        assert data.get("content_hidden") is not True
        assert data["error"] == "could not answer: a private question"

    def test_succeeded_state_body_is_a_superset_of_the_old_synchronous_response(self, client):
        result = {
            "answer": "42",
            "citations": [{"title": "doc.md"}],
            "answered_by": "workstation llama",
            "summary": "q",
        }
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=SUCCEEDED, payload={}, result=result,
        )

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "succeeded"
        assert {"answer", "citations", "answered_by"} <= data.keys()
        assert data["answer"] == "42"
        assert data["citations"] == [{"title": "doc.md"}]
        assert data["answered_by"] == "workstation llama"
        assert data["summary"] == "q"

    def test_jobs_real_state_wins_even_if_result_ever_carried_its_own_state_key(self, client):
        """Regression: the succeeded body is `job.result` spread first with
        `"state"` set LAST, so the job's actual state can never be shadowed
        by a same-named key inside `result` -- `run_ask` never returns one
        today, but the merge order itself must not depend on that staying
        true forever."""
        job = InferenceJob.objects.create(
            kind="rag.ask", priority=100, state=SUCCEEDED, payload={},
            result={"answer": "42", "citations": [], "state": "corrupted"},
        )

        response = client.get(self._url(job.pk))

        assert response.status_code == 200
        assert response.json()["state"] == "succeeded"

    def test_queue_unavailable_returns_503_with_migrations_copy(self, client):
        with patch("tools.rag.views.get_job", side_effect=QueueUnavailable("boom")):
            response = client.get(self._url(1))

        assert response.status_code == 503
        data = response.json()
        assert data["error"] == (
            "The queue isn't ready yet — run database migrations, then ask again."
        )
        assert data["setup_url"] == reverse("inference-console")


@pytest.mark.django_db
class TestSearchView:
    """W6 (ADR 0014 §18): the retrieval-only search page (`GET /rag/
    search/`) -- no LLM, no queue, no `AskRecord`. `tools.rag.views.
    retrieval.retrieve_nodes` (the shared retrieve step `answer_question`
    also calls, see `test_retrieval.py`'s own `TestAnswerQuestionUsesSharedRetrieveNodes`)
    is mocked in every test below except the embed-precheck-failure ones,
    matching this file's "never hit Ollama/pgvector" convention."""

    @pytest.fixture(autouse=True)
    def _model_available(self):
        """`SearchView`'s embed-role precheck resolves()/is_healthy()
        exactly like `AskView`'s own pre-check, at the same module-level
        names -- stub both by default; the precheck-failure tests below
        layer their own @patch on top."""
        yield from model_available()

    @pytest.fixture(autouse=True)
    def _live_store_exists(self):
        """W6 review NIT: `SearchView` short-circuits to the honest empty
        state, WITHOUT calling `retrieve_nodes` at all, when `rag_index.
        live_store_shape() is None` (no live chunk table -- nothing
        prose has ever been ingested on this install, see
        `test_short_circuits_to_empty_state_when_the_chunk_table_does_not_
        exist` below). None of the tests in this class ingest anything for
        real, so the actual `data_rag_chunks` table never exists in the
        test database either -- every test below mocks `retrieve_nodes`
        directly and needs the short-circuit to NOT fire so that mock is
        actually reached. Only the one test below that targets the
        short-circuit itself overrides this back to `None`."""
        with patch("tools.rag.views.rag_index.live_store_shape", return_value={"hybrid": False, "jsonb": False}):
            yield

    def _fake_node(self, *, file_id="1", file_name="notes.md", score=0.87, text="chunk text", extra_metadata=None):
        node = MagicMock()
        node.metadata = {"file_id": file_id, "file_name": file_name, "source_path": f"/data/{file_name}"}
        if extra_metadata:
            node.metadata.update(extra_metadata)
        node.get_content.return_value = text
        node_with_score = MagicMock()
        node_with_score.node = node
        node_with_score.score = score
        return node_with_score

    def test_empty_query_renders_only_the_form(self, client):
        """No `q` at all -- the bare form, no precheck, no retrieval."""
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            response = client.get(reverse("rag-search"))
            mock_retrieve_nodes.assert_not_called()

        assert response.status_code == 200
        body = response.content.decode()
        assert '<form class="search-form"' in body
        assert 'name="q"' in body
        assert response.context["results"] is None

    def test_results_render_with_title_locator_score_and_snippet(self, client):
        node = self._fake_node(
            file_id="9", file_name="report.pdf", score=0.734, text="the relevant chunk",
            extra_metadata={"page": 3},
        )
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "uranium"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "report.pdf" in body
        assert ", p. 3" in body
        assert "0.734" in body
        assert "cosine similarity" in body
        assert "the relevant chunk" in body

    def test_category_and_query_are_passed_through_to_retrieve_nodes(self, client):
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([], False, MagicMock())
            client.get(reverse("rag-search"), {"q": "uranium", "category": "Medical"})

        args, _ = mock_retrieve_nodes.call_args
        assert args[0] == "uranium"
        assert args[1] == "Medical"

    def test_blank_category_is_passed_as_none(self, client):
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([], False, MagicMock())
            client.get(reverse("rag-search"), {"q": "uranium"})

        args, _ = mock_retrieve_nodes.call_args
        assert args[1] is None

    def test_hybrid_store_gets_a_neutral_score_label(self, client):
        """W5: with no per-result arm tag available under hybrid, every
        result gets the neutral "relevance score" label, never a
        confidently-wrong "cosine similarity"."""
        node = self._fake_node(score=0.03)
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], True, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "term"})

        body = response.content.decode()
        assert "relevance score" in body
        assert "cosine similarity" not in body

    def test_no_results_and_no_floor_shows_the_generic_empty_state(self, client):
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "nonsense"})

        assert "Nothing in the library matched this search." in response.content.decode()

    def test_floor_filtered_empty_state_names_the_exact_floor(self, client):
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.6)
        node = self._fake_node(score=0.2)
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert "Nothing scored above the retrieval floor (0.60)." in response.content.decode()

    def test_floor_is_suspended_under_hybrid_even_when_nothing_survives(self, client):
        """W5: the floor never fires while the live store is hybrid -- a
        node scoring below it still renders as a result, never the floor's
        empty-state copy."""
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.6)
        node = self._fake_node(score=0.03)
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], True, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "term"})

        body = response.content.decode()
        assert "Nothing scored above the retrieval floor" not in body
        assert response.context["results"]

    def test_floor_set_but_nothing_retrieved_shows_the_generic_empty_state(self, client):
        """W6 review MAJOR 1: `retrieve_nodes` came back with ZERO nodes at
        all (an empty/no-match library or category) -- a non-zero floor
        being SET must not, by itself, make the empty state claim the floor
        was the reason nothing came back. Mirrors `answer_question`'s own
        `nodes and score_floor > 0.0 and not surviving_nodes` guard
        (`tools.rag.retrieval`) -- without the `nodes` term this used to
        print "Nothing scored above the retrieval floor (0.60)." even
        though nothing was ever retrieved for the floor to reject."""
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.6)
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "nonsense"})

        body = response.content.decode()
        assert "Nothing in the library matched this search." in body
        assert "Nothing scored above the retrieval floor" not in body

    def test_short_circuits_to_empty_state_when_the_chunk_table_does_not_exist(self, client):
        """W6 review NIT: on a fresh install (no live `rag_chunks` table --
        `live_store_shape() is None`), the view must render the honest
        empty state WITHOUT ever calling `retrieve_nodes` -- building a
        `PGVectorStore` and retrieving against a table that doesn't exist
        yet would otherwise create it as a side effect (idempotent DDL a
        read-only GET has no business issuing)."""
        with patch("tools.rag.views.rag_index.live_store_shape", return_value=None):
            with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
                response = client.get(reverse("rag-search"), {"q": "term"})
                mock_retrieve_nodes.assert_not_called()

        assert response.status_code == 200
        assert response.context["results"] == []
        assert "Nothing in the library matched this search." in response.content.decode()

    def test_live_store_shape_error_renders_the_failed_message_never_a_500(self, client, caplog):
        """The `live_store_shape()` probe above sits outside
        `retrieve_nodes`'s own try/except -- a `ProgrammingError`/
        `OperationalError` out of it (e.g. the table not migrated yet in
        some deployment order, or any other DB hiccup on this single cheap
        lookup) must degrade exactly like any other retrieval failure:
        logged at `warning` with `exc_info=True`, rendered as the generic
        `SEARCH_FAILED_MESSAGE` inline, 200, never a 500."""
        with patch(
            "tools.rag.views.rag_index.live_store_shape",
            side_effect=OperationalError("could not connect to server"),
        ):
            with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
                with caplog.at_level(logging.WARNING, logger="tools.rag.views"):
                    response = client.get(reverse("rag-search"), {"q": "term"})
                mock_retrieve_nodes.assert_not_called()

        assert response.status_code == 200
        assert "Something went wrong running this search" in response.content.decode()
        assert any(
            record.levelno == logging.WARNING and record.exc_info is not None
            for record in caplog.records
        )

    def test_non_integer_file_id_renders_unlinked_never_a_500(self, client):
        """W6 review MINOR 3: `rag-document-file` reverses `<int:doc_id>`
        (`tools/rag/urls.py`) -- a chunk whose stored `file_id` metadata
        isn't a plain integer string used to 500 with `NoReverseMatch` from
        the template's own `{% url %}` tag. `_document_url_for` guards the
        `reverse()` call and the template only links when it succeeds."""
        node = self._fake_node(file_id="not-an-int", file_name="mystery.md")
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "mystery.md" in body
        assert response.context["results"][0]["document_url"] is None
        # Rendered as plain text, not a broken/absent link around the title.
        title_line = next(line for line in body.splitlines() if "mystery.md" in line)
        assert "<a " not in title_line

    def test_value_error_from_retrieve_nodes_renders_its_own_message(self, client):
        """W6 review MINOR 4: `tools.rag.index.get_vector_store` raises
        `ValueError` with an actionable, operator-facing message (e.g. "no
        recorded embedding dimension ... set one in the model console")
        when the resolved `rag.embed` binding has no `embed_dim`. That
        specific message must reach the page verbatim, not get flattened
        into the generic `SEARCH_FAILED_MESSAGE` a bare `except Exception`
        would produce."""
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.side_effect = ValueError(
                "Embedding connection 'foo' (ollama) has no recorded embedding dimension"
            )
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "has no recorded embedding dimension" in body
        assert "Something went wrong running this search" not in body

    def test_unresolvable_embed_role_renders_inline_copy_never_a_503(self, client):
        with patch("tools.rag.views.resolve") as mock_resolve:
            mock_resolve.side_effect = ValueError("no binding configured")
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert response.status_code == 200
        # Django autoescapes the apostrophe as `&#x27;` -- check the
        # unescaped tail of the sentence instead of hand-escaping it here.
        assert "set up yet" in response.content.decode()

    def test_unreachable_embed_endpoint_renders_inline_copy_never_a_503(self, client):
        with patch("tools.rag.messages.get_engine") as mock_get_engine:
            mock_engine = MagicMock()
            mock_engine.is_healthy.return_value = False
            mock_get_engine.return_value = mock_engine
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert response.status_code == 200
        assert "unreachable" in response.content.decode()

    def test_embedding_call_exception_renders_inline_error_never_a_500(self, client):
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.side_effect = RuntimeError("boom")
            response = client.get(reverse("rag-search"), {"q": "term"})

        assert response.status_code == 200
        assert "Something went wrong running this search" in response.content.decode()

    def test_chunk_text_is_html_escaped(self, client):
        node = self._fake_node(text="<script>alert(1)</script>")
        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            mock_retrieve_nodes.return_value = ([node], False, MagicMock())
            response = client.get(reverse("rag-search"), {"q": "term"})

        body = response.content.decode()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body

    def test_query_over_length_cap_is_rejected_without_calling_retrieve_nodes(self, client):
        from tools.rag.views import SEARCH_QUERY_MAX_LENGTH

        with patch("tools.rag.views.retrieval.retrieve_nodes") as mock_retrieve_nodes:
            response = client.get(reverse("rag-search"), {"q": "x" * (SEARCH_QUERY_MAX_LENGTH + 1)})
            mock_retrieve_nodes.assert_not_called()

        assert response.status_code == 200
        assert "too long" in response.content.decode()

    def test_nav_marks_search_current_and_not_ask_or_documents(self, client):
        # Bound first: the Search entry renders only when `rag.embed`
        # has a model (UI-1 decision 2), so marking it current needs it
        # to exist.
        bind_rag_roles()

        response = client.get(reverse("rag-search"))

        body = response.content.decode()
        assert f'href="{reverse("rag-search")}" class="current"' in body
        assert f'href="{reverse("rag-ask-page")}" class="current"' not in body
        assert f'href="{reverse("rag-documents")}" class="current"' not in body


# --- C-15: the endpoint-dedup + health-check prologue --------------------
#
# `tools.rag.views._precheck_models`, `SearchView`'s own `_precheck_embed_role`,
# and `tools.rag.jobs._precheck` each used to carry their own copy of the
# dedup-by-normalized-endpoint health-check probe. `tools.rag.messages.
# unreachable_endpoints` now does that work once for all three callers.
# These are characterization tests: they pin the OBSERVABLE behaviour
# (health-check call count, rendered message text) at each surface, so the
# extraction is provably a no-op from every caller's point of view. (A
# later task moves these into test_views_ask.py.)


@pytest.mark.django_db
def test_one_unreachable_endpoint_shared_by_two_roles_is_probed_once(client):
    """C-15. The dedup is the point of the block being extracted: two
    roles pointing at one model server cost ONE health check. Pinned at
    the two surfaces that actually resolve two roles together (`AskView`'s
    pre-check and the job's pre-run re-check, via `run_ask`) -- the search
    page only ever checks one role (`rag.embed` alone), so it has no
    second role to dedup against; its own copy of the shared helper is
    covered separately below."""
    shared = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
    mock_engine = MagicMock()
    mock_engine.is_healthy.return_value = False

    # tools.rag.views._precheck_models: rag.answer and rag.embed resolve to the
    # SAME endpoint.
    with patch("tools.rag.views.resolve", return_value=shared), patch(
        "tools.rag.messages.get_engine", return_value=mock_engine
    ), patch("tools.rag.views.enqueue") as mock_enqueue:
        response = post_ask(client, {"question": "what is the answer?"})

    assert response.status_code == 503
    mock_engine.is_healthy.assert_called_once_with("http://localhost:11434")
    mock_enqueue.assert_not_called()

    # jobs._precheck (via run_ask): the same shape, going through the same
    # shared helper.
    mock_engine.reset_mock()
    from tools.rag.tests._helpers import make_job_ctx

    with patch("tools.rag.jobs.resolve", return_value=shared), patch(
        "tools.rag.messages.get_engine", return_value=mock_engine
    ):
        with pytest.raises(RuntimeError):
            jobs.run_ask({"question": "q"}, [], make_job_ctx())

    mock_engine.is_healthy.assert_called_once_with("http://localhost:11434")


@pytest.mark.django_db
@pytest.mark.parametrize("surface", ["ask_view", "search_view", "ask_job"])
def test_each_surface_keeps_its_own_unreachable_copy(client, surface):
    """The half that must NOT change. The web path and the job path say
    different things about a deleted connection pk on purpose (`jobs.py`'s
    own docstring records the divergence), and the single-role search
    page has its own message shape. Extracting the probe must leave every
    rendered sentence exactly as it is."""
    if surface == "ask_view":
        # AskView short-circuits an unknown `connection` pk to
        # `_UNREGISTERED_CONNECTION_MESSAGE` BEFORE `_precheck_models`
        # (hence the shared helper) ever runs.
        with patch("tools.rag.views.enqueue") as mock_enqueue:
            response = post_ask(client, {"question": "q", "connection": "999999"})
        assert response.status_code == 503
        assert response.json()["error"] == (
            "That model is no longer registered — pick another in the model console."
        )
        mock_enqueue.assert_not_called()
    elif surface == "search_view":
        embed_resolved = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435")
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False
        with patch("tools.rag.views.resolve", return_value=embed_resolved), patch(
            "tools.rag.messages.get_engine", return_value=mock_engine
        ):
            response = client.get(reverse("rag-search"), {"q": "term"})
        assert response.status_code == 200
        assert "The embedding model is unreachable." in response.content.decode()
    else:
        # jobs._precheck folds the SAME unknown-pk `ValueError` into the
        # generic `UNBOUND` cause instead -- `jobs.py`'s own `_precheck`
        # docstring records this as the one place the two paths'
        # messages may legitimately differ.
        from tools.rag.tests._helpers import make_job_ctx

        embed_resolved = ResolvedModel(
            "ollama", "nomic-embed-text", "http://localhost:11435", embed_dim=768
        )
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        with patch("tools.rag.jobs.resolve", return_value=embed_resolved), patch(
            "tools.rag.messages.get_engine", return_value=mock_engine
        ):
            with pytest.raises(RuntimeError) as exc_info:
                jobs.run_ask({"question": "q", "connection": "999999"}, [], make_job_ctx())
        assert str(exc_info.value) == (
            "The chat model isn't set up yet — assign one in the model console."
        )
