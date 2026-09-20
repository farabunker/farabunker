"""role_reencode's enqueue-only re-encode trigger, the shared
`_stamp_first_materialization` helper, `connection_add`'s create path, and
the console's three-section layout including the "Getting models"
checklist and its adapter-derived server facts.

One of six files `models/registry/tests/test_views.py` split into (C-56):
`test_views_console_and_roles.py`, `test_views_reencode_and_sections.py`,
`test_views_machine_add_and_dropdowns.py`, `test_views_connection_edit.py`,
`test_views_engine_and_remove.py`, `test_views_tables_and_picker.py`. The
original was 7,704 lines and 43 classes; `tools/vision/tests/` is the
in-repo precedent for splitting one `views.py` module's tests by feature
area. Cut at class boundaries, on the file's own `# ---` dividers, with
no assertion changed.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.test import override_settings
from django.urls import reverse

from identity.contracts import actions
from identity.models import AuditEvent
from models.registry import views
from models.registry import views as views_module
from models.registry.models import Materialization, ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    ENDPOINT,
    NO_ENV_OVERRIDE,
    _catalog_only_row,
    _extra_role_registry,  # noqa: F401 -- requested by name as a fixture
    _getting_models_html,
    _installed_row,
    _mock_engine,
    _only_rag_roles,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
    make_embed_connection,
)
from models.contracts import roles as roles_module
from models.contracts.roles import RAG_EXTRACT_ROLE, RoleSpec, register_role


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Clear the ModelConnection/RoleBinding rows migration 0002 seeds from
    env settings, so each test starts from a known-empty registry."""
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """`probe_cache` (C-07) carries NO `connection.in_atomic_block` guard
    -- deliberately, an HTTP probe has no transaction to roll back -- so
    unlike `models.registry.availability`'s cache it is NOT automatically
    inert inside this file's `django_db` tests. Every test in this module
    shares the same `ENDPOINT` constant and mocks `ENGINES`/`discover`
    with a different answer per test; without this, a cached `True` from
    one test's healthy mock would silently answer the next test's
    unhealthy one. Invalidated before AND after so it can never leak past
    this module either."""
    clean_probe_cache()
    yield
    clean_probe_cache()


# --- role_reencode ------------------------------------------------------------
#
# T8: this view no longer runs the re-encode synchronously -- it enqueues a
# `rag.reencode` job (`models.registry.jobs`) and reports whether the
# ENQUEUE itself succeeded. The run's own success/failure surface moved to
# the Queue page (the job row's stored `result`/`error`); nothing here
# calls `run_rematerialize` directly any more.


@pytest.mark.django_db
class TestRoleReencode:
    @patch("models.registry.views.enqueue")
    def test_enqueues_rag_reencode_with_the_role_key_payload_and_reports_success(
        self, mock_enqueue, client
    ):
        mock_enqueue.return_value = 42

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        mock_enqueue.assert_called_once_with(
            "rag.reencode", {"role_key": "rag.embed", "actor_kind": "open", "actor_key": "box"},
        )
        followed = client.get(response.url)
        assert "Re-encode queued — track it on the Queue page." in followed.content.decode()

    @patch("models.registry.views.enqueue")
    def test_the_open_box_stamps_the_open_principal_as_the_actor(self, mock_enqueue, client):
        """Task 10, the acting rule part 1: not a blank. Every job on an
        open box has an honest actor, which is what makes
        `models/queue/visibility.py` able to reason about it later."""
        mock_enqueue.return_value = 42

        client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("open", "box")

    @patch("models.registry.views.enqueue")
    def test_a_signed_in_superuser_stamps_their_own_principal_as_the_actor(
        self, mock_enqueue, client
    ):
        """This console is superuser-only, so its actor is always a
        signed-in superuser or the open principal -- there is no service
        half (nothing runs `role_reencode` from a shell)."""
        from models.registry.tests._helpers import make_admin, posture, sign_in
        from identity.contracts.postures import POSTURE_PERSONAL

        mock_enqueue.return_value = 42
        admin = make_admin()

        with posture(POSTURE_PERSONAL):
            sign_in(client, admin)
            client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("user", str(admin.pk))

    @override_settings(**NO_ENV_OVERRIDE)
    def test_unbound_role_degrades_honestly_instead_of_500ing(self, client):
        """Review MAJOR fix: `rag.embed` is registered (with its
        `rematerialize` callback) but has NO `RoleBinding` row AND no
        environment override (`NO_ENV_OVERRIDE` -- `_clear_seeded_rows`
        already emptied `RoleBinding`/`ModelConnection`) -- genuinely
        unbound. `enqueue` is deliberately NOT mocked here: this exercises
        the REAL `models.registry.jobs.plan_reencode` -> `models.contracts.
        bindings.resolve()` chain, which raises `ValueError` for an unbound
        role. Before the fix that `ValueError` propagated uncaught out of
        `enqueue()` (`models.queue.backend.enqueue` calls the planner
        directly, letting a raise through) all the way to a 500 -- this
        proves the view's `except ValueError` around the `enqueue()` call
        actually catches it instead. The mocked-`enqueue` tests around this
        one test a different layer (the view's own branching, with the
        planner assumed to succeed); this one proves the real resolve
        failure specifically."""
        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "has no model assigned" in body
        assert "assign one before re-encoding." in body
        assert "Re-encode queued" not in body

    def test_bound_unmaterialized_role_enqueues_cleanly_not_an_error(self, client):
        """T-reencode-gate review: the exact case the template gate fix
        makes reachable from the UI -- an embeddings role BOUND to a
        registered connection with no `Materialization` row at all yet
        (mirrors the live evidence: `rag.embed` bound via `EMBED_MODEL`,
        never materialized). `enqueue` is deliberately NOT mocked, same as
        `test_unbound_role_degrades_honestly_instead_of_500ing` above,
        so this exercises the REAL `plan_reencode` -> `resolve()` chain --
        `plan_reencode` only calls `resolve(role_key)` (see
        `models.registry.jobs.plan_reencode`'s docstring), which
        succeeds for a bound role regardless of whether it has ever been
        materialized; nothing in the enqueue path reads `Materialization`
        at plan time. Proves the view doesn't need a `Materialization` row
        to enqueue successfully -- only `role_reencode`'s own guards
        (rematerialize configured, no duplicate job, role resolves)."""
        embed = make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        assert Materialization.objects.filter(role_key="rag.embed").exists() is False

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "Re-encode queued — track it on the Queue page." in body
        assert "has no model assigned" not in body

    @patch("models.registry.views.enqueue")
    def test_queue_unavailable_becomes_the_migrations_copy_error(self, mock_enqueue, client):
        from models.contracts.queue import QueueUnavailable

        mock_enqueue.side_effect = QueueUnavailable("jobs tables unavailable")

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        followed = client.get(response.url)
        body = followed.content.decode()
        # `isn't` is HTML-escaped (`isn&#x27;t`) by autoescaping -- match
        # the unambiguous, apostrophe-free tail instead of the raw string.
        assert "run database migrations." in body
        assert "Re-encode queued" not in body

    @patch("models.registry.views.enqueue")
    def test_refuses_a_duplicate_when_one_is_already_queued(self, mock_enqueue, client):
        from models.queue.models import QUEUED, InferenceJob

        InferenceJob.objects.create(kind="rag.reencode", state=QUEUED, priority=200, payload={})

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        mock_enqueue.assert_not_called()
        followed = client.get(response.url)
        assert "A re-encode is already queued — check the Queue page." in followed.content.decode()

    @patch("models.registry.views.enqueue")
    def test_refuses_a_duplicate_when_one_is_already_running(self, mock_enqueue, client):
        from models.queue.models import RUNNING, InferenceJob

        InferenceJob.objects.create(kind="rag.reencode", state=RUNNING, priority=200, payload={})

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        mock_enqueue.assert_not_called()
        followed = client.get(response.url)
        assert "A re-encode is already queued — check the Queue page." in followed.content.decode()

    @patch("models.registry.views.enqueue")
    def test_a_finished_reencode_job_is_not_treated_as_a_duplicate(self, mock_enqueue, client):
        from models.queue.models import SUCCEEDED, InferenceJob

        InferenceJob.objects.create(kind="rag.reencode", state=SUCCEEDED, priority=200, payload={})
        mock_enqueue.return_value = 43

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        assert response.status_code == 302
        mock_enqueue.assert_called_once_with(
            "rag.reencode", {"role_key": "rag.embed", "actor_kind": "open", "actor_key": "box"},
        )

    @patch("models.registry.views.enqueue")
    def test_role_with_no_rematerialize_callback_errors_without_enqueuing(
        self, mock_enqueue, client
    ):
        """`rag.answer` (chat) has no `rematerialize` callback configured
        (`tools.rag.apps.RagConfig.ready()`) -- refused with the same
        copy `models.registry.drift.run_rematerialize` itself would
        raise, checked BEFORE ever enqueuing so a certain-to-fail job is
        never queued."""
        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.answer"})

        assert response.status_code == 302
        mock_enqueue.assert_not_called()
        followed = client.get(response.url)
        assert "no rematerialize callback configured" in followed.content.decode()

    def test_unknown_role_key_errors_without_calling_enqueue(self, client):
        with patch("models.registry.views.enqueue") as mock_enqueue:
            response = client.post(
                reverse("inference-role-reencode"), data={"role_key": "does.not.exist"}
            )
            mock_enqueue.assert_not_called()

        assert response.status_code == 302


# --- _stamp_first_materialization (the shared first-stamp helper) -----------


@pytest.mark.django_db
class TestStampFirstMaterialization:
    """Direct unit coverage for the helper extracted from three call sites
    (`connection_add`'s update path, `connection_remove`, `role_assign`'s
    first embeddings bind) -- each site's own end-to-end behavior is still
    covered by its existing tests (e.g.
    `TestConnectionEdit.test_confirmed_fingerprint_edit_with_no_prior_stamp_stamps_pre_edit_fingerprint`,
    `TestRoleAssignConnectionPick`'s first-rebind stamp tests), which stay
    green unchanged since this is a pure extraction, not a behavior change.
    """

    def test_stamps_every_role_missing_a_materialization(self):
        answer_role = roles_module.get_role("rag.answer")
        embed_role = roles_module.get_role("rag.embed")

        views_module._stamp_first_materialization(
            [answer_role, embed_role], "ollama:llama3.1:8b:None"
        )

        assert Materialization.objects.filter(role_key="rag.answer").exists()
        assert Materialization.objects.filter(role_key="rag.embed").exists()
        assert (
            Materialization.objects.get(role_key="rag.answer").fingerprint
            == "ollama:llama3.1:8b:None"
        )

    def test_never_overwrites_an_existing_stamp(self):
        role = roles_module.get_role("rag.embed")
        Materialization.objects.create(role_key="rag.embed", fingerprint="original:fingerprint:768")

        views_module._stamp_first_materialization([role], "new:fingerprint:1024")

        assert (
            Materialization.objects.get(role_key="rag.embed").fingerprint
            == "original:fingerprint:768"
        )

    def test_empty_role_list_is_a_no_op(self):
        views_module._stamp_first_materialization([], "ollama:llama3.1:8b:None")

        assert not Materialization.objects.exists()


# --- connection_add -----------------------------------------------------------


@pytest.mark.django_db
class TestConnectionAdd:
    def _post(self, client, **data):
        return client.post(reverse("inference-connection-add"), data=data)

    def test_creates_a_connection(self, client):
        response = self._post(
            client,
            name="my chat model",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="my chat model")
        assert connection.capabilities == ["chat"]
        assert connection.model_id == "llama3.1:8b"

    def test_a_valid_create_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): `ModelConnection` create/edit/delete was
        one of the four unaudited settings surfaces the backend audit
        named with no recorded rationale -- the recorded ruling is
        "audit all four", `footprint_override_bytes` included."""
        self._post(
            client,
            name="my chat model",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="2",
        )

        connection = ModelConnection.objects.get(name="my chat model")
        assert AuditEvent.objects.filter(action=actions.CONNECTION_CREATED).count() == 1
        event = AuditEvent.objects.get(action=actions.CONNECTION_CREATED)
        assert event.actor_kind == "open"
        assert event.target_key == str(connection.pk)
        assert event.target_label == "my chat model"
        assert event.detail == {"footprint_override_bytes": round(2 * 1024**3)}

    def test_an_invalid_create_is_not_audited(self, client):
        self._post(
            client, name="bad conn", endpoint=ENDPOINT, model_id="llama3.1:8b",
            capability="chat", context_window="not-a-number",
        )

        assert AuditEvent.objects.count() == 0

    # --- context_window (this task, the qwen3:30b-rag incident) ---------

    def test_blank_context_window_stores_none(self, client):
        response = self._post(
            client,
            name="chat conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            context_window="",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="chat conn")
        assert connection.context_window is None

    def test_positive_context_window_is_stored(self, client):
        response = self._post(
            client,
            name="capped conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            context_window="16384",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="capped conn")
        assert connection.context_window == 16384

    @pytest.mark.parametrize("bad_value", ["0", "-1"])
    def test_non_positive_context_window_is_rejected_cleanly(self, client, bad_value):
        response = self._post(
            client,
            name="bad conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            context_window=bad_value,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad conn").exists()
        followed = client.get(response.url)
        assert "Context window must be a positive number of tokens." in followed.content.decode()

    def test_non_numeric_context_window_is_rejected_cleanly(self, client):
        response = self._post(
            client,
            name="bad conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            context_window="not-a-number",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad conn").exists()
        followed = client.get(response.url)
        assert "Context window must be a whole number of tokens." in followed.content.decode()

    # --- descriptor / rank (operator-assigned qualifiers) -------------------

    def test_blank_descriptor_and_rank_store_unset(self, client):
        response = self._post(
            client,
            name="plain conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            descriptor="",
            rank="",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="plain conn")
        assert connection.descriptor == ""
        assert connection.rank is None

    def test_descriptor_and_rank_are_stored(self, client):
        response = self._post(
            client,
            name="light conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            descriptor="light + fast",
            rank="2",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="light conn")
        assert connection.descriptor == "light + fast"
        assert connection.rank == 2

    def test_descriptor_over_80_chars_is_rejected_cleanly(self, client):
        response = self._post(
            client,
            name="verbose conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            descriptor="x" * 81,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="verbose conn").exists()
        followed = client.get(response.url)
        assert "Descriptor must be 80 characters or fewer." in followed.content.decode()

    def test_descriptor_at_exactly_80_chars_is_accepted(self, client):
        response = self._post(
            client,
            name="max descriptor conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            descriptor="x" * 80,
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="max descriptor conn")
        assert connection.descriptor == "x" * 80

    @pytest.mark.parametrize("bad_value", ["0", "-1", "not-a-number"])
    def test_bad_rank_is_rejected_cleanly(self, client, bad_value):
        response = self._post(
            client,
            name="bad rank conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            rank=bad_value,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad rank conn").exists()
        followed = client.get(response.url)
        assert "Rank must be a positive number." in followed.content.decode()

    # --- footprint_gb / footprint_override_bytes (T2b) -------------------

    def test_blank_footprint_gb_stores_none(self, client):
        response = self._post(
            client,
            name="no override conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="no override conn")
        assert connection.footprint_override_bytes is None

    def test_valid_footprint_gb_is_stored_as_bytes(self, client):
        response = self._post(
            client,
            name="overridden conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="8.5",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="overridden conn")
        assert connection.footprint_override_bytes == round(8.5 * 1024**3)

    def test_footprint_gb_round_trips_through_the_stored_property(self, client):
        """8.5 in -> stored bytes -> 8.5 back out via
        `footprint_override_gb`, the exact value the edit form re-renders."""
        self._post(
            client,
            name="round trip conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="8.5",
        )

        connection = ModelConnection.objects.get(name="round trip conn")
        assert connection.footprint_override_gb == "8.5"

    def test_non_numeric_footprint_gb_is_rejected_cleanly(self, client):
        response = self._post(
            client,
            name="bad footprint conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="not-a-number",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad footprint conn").exists()
        followed = client.get(response.url)
        assert "Memory footprint override must be a number of GB." in followed.content.decode()

    @pytest.mark.parametrize("bad_value", ["0", "-1", "-0.5"])
    def test_non_positive_footprint_gb_is_rejected_cleanly(self, client, bad_value):
        response = self._post(
            client,
            name="bad footprint conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb=bad_value,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad footprint conn").exists()
        followed = client.get(response.url)
        assert "Memory footprint override must be greater than zero." in followed.content.decode()

    @pytest.mark.parametrize("bad_value", ["inf", "-inf", "nan", "infinity"])
    def test_non_finite_footprint_gb_is_rejected_cleanly_never_500s(self, client, bad_value):
        """T4 review MAJOR, reproduced live: `float("inf")` parses without
        raising, so `round(footprint_gb * 1024**3)` was an uncaught
        OverflowError (a 500), and `float("nan") <= 0` is False, so nan
        sailed past the positivity check too. Both must be rejected with
        the SAME "not a number" copy an unparseable string gets."""
        response = self._post(
            client,
            name="bad footprint conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb=bad_value,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad footprint conn").exists()
        followed = client.get(response.url)
        assert followed.status_code == 200
        assert "Memory footprint override must be a number of GB." in followed.content.decode()

    def test_astronomically_large_footprint_gb_is_rejected_cleanly_never_500s(self, client):
        """M2 (Coherence Wave B review): `1e308` is itself a FINITE float
        (passes `math.isfinite`), but `1e308 * 1024**3` overflows `round()`
        to an uncaught `OverflowError` -- the same failure class T10
        review MINOR 5 fixed for `tools.rag.views.
        _upload_cap_update`'s identically-shaped GB field, which
        this one had not caught up to."""
        response = self._post(
            client,
            name="huge footprint conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="1e308",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="huge footprint conn").exists()
        followed = client.get(response.url)
        assert "Memory footprint override is too large." in followed.content.decode()

    def test_a_footprint_gb_past_the_bigint_ceiling_is_rejected_cleanly(self, client):
        """M2 (Coherence Wave B review): the input ONLY the ceiling check
        catches -- finite, `round()` succeeds (1.07e19 bytes), and that
        number exceeds `BIGINT_FIELD_MAX` (2**63-1 = 9.22e18) -- so the
        `OverflowError` guard above never sees it."""
        response = self._post(
            client,
            name="ceiling footprint conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
            footprint_gb="1e10",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="ceiling footprint conn").exists()
        followed = client.get(response.url)
        assert "Memory footprint override is too large." in followed.content.decode()

    def test_embeddings_connection_stores_embed_dim(self, client):
        self._post(
            client,
            name="my embed model",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capability="embeddings",
            embed_dim="768",
        )

        connection = ModelConnection.objects.get(name="my embed model")
        assert connection.embed_dim == 768

    @pytest.mark.parametrize("bad_value", ["0", "-5"])
    def test_non_positive_embed_dim_is_rejected_cleanly_on_create(self, client, bad_value):
        """embed_dim must reject 0/negative values with the same never-500,
        clean-error shape as context_window and rank."""
        response = self._post(
            client,
            name="bad embed conn",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capability="embeddings",
            embed_dim=bad_value,
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad embed conn").exists()
        followed = client.get(response.url)
        assert "Embedding dimension must be a positive number." in followed.content.decode()

    def test_non_numeric_embed_dim_is_rejected_cleanly_on_create(self, client):
        response = self._post(
            client,
            name="bad embed conn",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capability="embeddings",
            embed_dim="not-a-number",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad embed conn").exists()
        followed = client.get(response.url)
        assert "Embedding dimension must be a whole number." in followed.content.decode()

    @pytest.mark.parametrize("missing_field", ["name", "model_id", "endpoint"])
    def test_blank_required_field_is_rejected(self, client, missing_field):
        fields = {
            "name": "conn", "endpoint": ENDPOINT, "model_id": "llama3.1:8b", "capability": "chat",
        }
        fields[missing_field] = ""

        response = self._post(client, **fields)

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="conn").exists()
        followed = client.get(response.url)
        assert "required" in followed.content.decode()

    def test_invalid_capability_is_rejected(self, client):
        response = self._post(
            client, name="conn", endpoint=ENDPOINT, model_id="llama3.1:8b", capability="not-a-capability",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="conn").exists()
        followed = client.get(response.url)
        assert "Capability must be one of" in followed.content.decode()

    # --- T9: multi-capability registration (manual checkbox group) --------

    def test_multi_select_checkbox_group_registers_every_checked_capability(self, client):
        """The manual form's Capability field is a checkbox group -- every
        checked value posts as its own `capability` field, read back whole
        via `getlist`. A llava/qwen-vl-class model registered chat+vision
        appears in both the chat picker and the vision dropdown."""
        response = self._post(
            client,
            name="my vlm",
            endpoint=ENDPOINT,
            model_id="llava:13b",
            capability=["chat", "vision"],
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="my vlm")
        assert connection.capabilities == ["chat", "vision"]

    def test_empty_capability_selection_is_refused(self, client):
        """Never-500 grammar matching the neighboring "must be one of"
        error: no box checked (the field posted entirely absent, as an
        unchecked checkbox group does) is refused, never registered with no
        capability at all. A non-catalog model_id -- catalog enrichment has
        nothing to fill a blank selection in with here (unlike
        `test_non_catalog_model_id_with_blank_capability_still_rejected`
        above, which proves the same thing for the single-value case)."""
        response = self._post(
            client,
            name="uncapable conn",
            endpoint=ENDPOINT,
            model_id="my-totally-custom-uncapable-model:latest",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="uncapable conn").exists()
        followed = client.get(response.url)
        assert "Capability must be one of" in followed.content.decode()

    def test_single_checked_box_still_registers_one_capability(self, client):
        """Back-compat: a single checked box (or a single posted value from
        an older caller) still round-trips to exactly one capability --
        including a transcription-only (whisper-class) connection, which
        must keep working unchanged."""
        response = self._post(
            client,
            name="whisper conn",
            endpoint=ENDPOINT,
            model_id="whisper:latest",
            capability="transcription",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="whisper conn")
        assert connection.capabilities == ["transcription"]

    def test_unknown_engine_is_rejected_cleanly_on_create(self, client):
        """The support boundary is the engine ADAPTER, enforced at exactly
        that level -- a garbage engine string must not be stored on
        the connection as-is, only to fail later, opaquely, the
        first time something tries to actually resolve it."""
        response = self._post(
            client, name="conn", engine="not-a-real-engine", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capability="chat",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="conn").exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "Unknown model server" in body
        assert "not-a-real-engine" in body

    def test_duplicate_name_case_insensitive_is_rejected(self, client):
        ModelConnection.objects.create(
            name="Chat Conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(
            client, name="chat conn", endpoint=ENDPOINT, model_id="llama3.1:8b", capability="chat",
        )

        assert response.status_code == 302
        assert ModelConnection.objects.filter(name__iexact="chat conn").count() == 1
        followed = client.get(response.url)
        assert "already exists" in followed.content.decode()

    def test_get_is_not_allowed(self, client):
        response = client.get(reverse("inference-connection-add"))
        assert response.status_code == 405

    # --- catalog enrichment (catalog.find() wired into the
    # manual "type a model id" path; enriches, never rejects) ---

    def test_catalog_match_fills_blank_capability_and_embed_dim(self, client):
        """model_id "nomic-embed-text" matches a catalog entry
        (capability="embeddings", embed_dim=768) -- leaving both blank on
        the form still registers a fully-specified connection."""
        response = self._post(
            client,
            name="my nomic conn",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capability="",
            embed_dim="",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="my nomic conn")
        assert connection.capabilities == ["embeddings"]
        assert connection.embed_dim == 768

    def test_catalog_match_fires_for_a_typed_tagged_model_id(self, client):
        """Final-review F1 regression: a hand-typed TAGGED id
        ("nomic-embed-text:latest") must still enrich from the bare
        catalog entry -- `find()` is tag-normalized on both sides."""
        response = self._post(
            client,
            name="tagged nomic conn",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text:latest",
            capability="",
            embed_dim="",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="tagged nomic conn")
        assert connection.capabilities == ["embeddings"]
        assert connection.embed_dim == 768

    def test_catalog_match_does_not_override_explicit_capability(self, client):
        """An operator-supplied capability wins over the catalog's own even
        when the model_id matches -- enrichment fills blanks, never
        overrides an explicit choice."""
        response = self._post(
            client,
            name="explicit chat conn",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",  # catalog capability is "chat"
            capability="chat",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="explicit chat conn")
        assert connection.capabilities == ["chat"]

    def test_non_catalog_model_id_with_blank_capability_still_rejected(self, client):
        """Enrichment never rejects a non-catalog id -- but it also can't
        rescue a blank capability it has nothing to fill it in with; the
        pre-existing validation error still applies."""
        response = self._post(
            client,
            name="custom conn",
            endpoint=ENDPOINT,
            model_id="my-totally-custom-model:latest",
            capability="",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="custom conn").exists()
        followed = client.get(response.url)
        assert "Capability must be one of" in followed.content.decode()

    def test_catalog_match_does_not_fill_blank_name(self, client):
        """`name` is deliberately not enriched -- it is the operator's own
        label, not spec data -- so a blank name still fails validation even
        when model_id matches a catalog entry."""
        response = self._post(
            client,
            name="",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(model_id="llama3.1:8b").exists()
        followed = client.get(response.url)
        assert "required" in followed.content.decode()

    # --- multi-file model family + companions (ADR 0012 D-EDIT-2/3) --------

    def test_manual_registration_stores_family_and_companions_in_config(self, client):
        """A multi-file family needs three facts nothing on the machine can
        supply: which family it is, which text encoder, which VAE. They land
        in `ModelConnection.config`, the JSONField D8 added for exactly
        this, and nowhere else."""
        response = self._post(
            client,
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capability="image-generation",
            family="flux2",
            text_encoder="enc-a.gguf",
            vae="vae-a.safetensors",
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="edit model")
        assert connection.config == {
            "family": "flux2",
            "text_encoder": "enc-a.gguf",
            "vae": "vae-a.safetensors",
        }

    def test_a_checkpoint_registration_stores_no_config_at_all(self, client):
        """A single-file checkpoint needs none of it, and an empty dict in
        the JSONField would be three keys of noise on every existing row."""
        self._post(
            client,
            name="plain checkpoint",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="sdxl.safetensors",
            capability="image-generation",
        )

        assert ModelConnection.objects.get(name="plain checkpoint").config is None

    def test_editing_a_connection_can_clear_a_companion_back_to_unset(self, client):
        """Blank means unset -- an operator who mis-picked must be able to
        undo it without deleting the connection."""
        connection = ModelConnection.objects.create(
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        self._post(
            client,
            connection_id=str(connection.pk),
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capability="image-generation",
            family="flux2",
            text_encoder="",
            vae="vae-a.safetensors",
        )

        connection.refresh_from_db()
        assert connection.config == {"family": "flux2", "vae": "vae-a.safetensors"}

    # --- final-review B1: the Edit form's family fields --------------------

    def test_editing_a_connection_with_no_family_fields_preserves_existing_config(
        self, client
    ):
        """`_connection_edit.html` did not used to post `family`/
        `text_encoder`/`vae` at all -- before this fix, the view read those
        three as missing-means-blank and overwrote `config` with `None` on
        every edit, silently nulling a flux2 connection's family the moment
        an operator changed its descriptor or rank. A caller (or an older
        template) that posts none of the three fields must leave `config`
        exactly as it was."""
        connection = ModelConnection.objects.create(
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
            rank=1,
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.post(
            reverse("inference-connection-add"),
            data={
                "connection_id": str(connection.pk),
                "name": "edit model",
                "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "weights-Q4_K_S.gguf",
                "capability": "image-generation",
                "rank": "2",
                # No family / text_encoder / vae fields at all.
            },
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.rank == 2
        assert connection.config == {
            "family": "flux2",
            "text_encoder": "enc-a.gguf",
            "vae": "vae-a.safetensors",
        }

    def test_editing_a_connection_with_blank_family_fields_clears_config(self, client):
        """The Edit form's own fields, all left on "— none —", must clear a
        previously-declared family -- the operator's explicit undo, distinct
        from a caller that never mentions the fields at all (preserved,
        above)."""
        connection = ModelConnection.objects.create(
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.post(
            reverse("inference-connection-add"),
            data={
                "connection_id": str(connection.pk),
                "name": "edit model",
                "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "weights-Q4_K_S.gguf",
                "capability": "image-generation",
                "family": "",
                "text_encoder": "",
                "vae": "",
            },
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.config is None

    def test_editing_a_connection_with_new_family_fields_replaces_config(self, client):
        """A genuine re-declaration -- the operator picking a different
        family/companions from the Edit form -- must replace the stored
        config, not merge with the old one."""
        connection = ModelConnection.objects.create(
            name="edit model",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.post(
            reverse("inference-connection-add"),
            data={
                "connection_id": str(connection.pk),
                "name": "edit model",
                "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "weights-Q4_K_S.gguf",
                "capability": "image-generation",
                "family": "qwen_image",
                "text_encoder": "enc-b.gguf",
                "vae": "vae-b.safetensors",
            },
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.config == {
            "family": "qwen_image",
            "text_encoder": "enc-b.gguf",
            "vae": "vae-b.safetensors",
        }

    # --- variant (ADR 0012 D-EDIT-6/8) --------------------------------------

    def test_manual_registration_stores_a_declared_variant(self, client):
        self._post(
            client, name="distilled edit model", engine="comfyui",
            endpoint="http://comfy.test:8188", model_id="weights-Q8_0.gguf",
            capability="image-generation", family="flux2", variant="distilled",
            text_encoder="enc-b.gguf", vae="vae-a.safetensors",
        )
        assert ModelConnection.objects.get(name="distilled edit model").config == {
            "family": "flux2", "variant": "distilled",
            "text_encoder": "enc-b.gguf", "vae": "vae-a.safetensors",
        }

    def test_an_edit_form_save_round_trips_the_variant(self, client):
        """The new word rides the rule the fix round already landed: posted
        blank clears, absent keeps. This pins that adding a fourth key did
        not give it a fourth behaviour."""
        connection = ModelConnection.objects.create(
            name="edit model", engine="comfyui", endpoint="http://comfy.test:8188",
            model_id="weights-Q8_0.gguf", capabilities=["image-generation"],
            config={"family": "flux2", "variant": "distilled",
                    "text_encoder": "enc-b.gguf", "vae": "vae-a.safetensors"},
        )
        self._post(
            client, connection_id=str(connection.pk), name="edit model",
            engine="comfyui", endpoint="http://comfy.test:8188",
            model_id="weights-Q8_0.gguf", capability="image-generation",
            family="flux2", variant="", text_encoder="enc-b.gguf",
            vae="vae-a.safetensors",
        )
        connection.refresh_from_db()
        assert "variant" not in connection.config   # posted blank -> cleared
        assert connection.config["family"] == "flux2"

    # --- C-48b: the whole refusal surface, in one place, before the split ---

    @pytest.mark.parametrize(("post", "fragment"), [
        ({"name": "", "endpoint": ENDPOINT, "model_id": "m"},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": "", "model_id": "m"},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": ""},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "engine": "nosuchengine"},
         'Unknown model server “nosuchengine” — the supported model-server APIs are listed on the console.'),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": []},
         "Capability must be one of:"),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "embed_dim": "wide"},
         "Embedding dimension must be a whole number."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "embed_dim": "0"},
         "Embedding dimension must be a positive number."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "context_window": "big"},
         "Context window must be a whole number of tokens."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "context_window": "-1"},
         "Context window must be a positive number of tokens."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "descriptor": "x" * 81},
         "Descriptor must be 80 characters or fewer."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "rank": "first"},
         "Rank must be a positive number."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "rank": "0"},
         "Rank must be a positive number."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "footprint_gb": "heavy"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "footprint_gb": "inf"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "footprint_gb": "nan"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": ENDPOINT, "model_id": "m", "capability": "chat",
          "footprint_gb": "0"},
         "Memory footprint override must be greater than zero."),
    ])
    def test_every_form_refusal_in_one_table(self, client, post, fragment):
        """C-48b, written BEFORE the split. Sixteen refusal cases across
        nine fields, each with the operator sentence it produces and each
        asserting that NOTHING was written -- the whole refusal surface in
        one place, so a validation/write split cannot silently drop one.

        `inf`/`nan` are here on purpose: `float()` parses both without
        raising, `round(inf * 1024**3)` is an uncaught OverflowError, and
        `nan <= 0` is False -- the T4 review MAJOR this field's own
        `math.isfinite` guard exists for."""
        before = ModelConnection.objects.count()
        response = self._post(client, **post)
        assert response.status_code == 302
        assert ModelConnection.objects.count() == before
        followed = client.get(response.url)
        assert fragment in followed.content.decode()

    def test_a_connection_id_that_names_no_row_is_refused_before_any_validation(self, client):
        """The FIRST refusal, and the only one in the parse phase: a
        tampered or stale `connection_id` answers "The connection being
        edited no longer exists." without validating a single field --
        so it must stay ahead of every other check after the split."""
        before = ModelConnection.objects.count()
        response = self._post(client, connection_id="999999", name="", endpoint="", model_id="")

        assert response.status_code == 302
        assert ModelConnection.objects.count() == before
        followed = client.get(response.url)
        assert "The connection being edited no longer exists." in followed.content.decode()

    def test_a_name_collision_is_case_insensitive_and_excludes_the_row_being_edited(self, client):
        """The LAST refusal in the validation phase, and the only one
        that reads the database. Two halves: a different row with the
        same name in any casing is refused with the typographic-quoted
        `_NAME_TAKEN_MESSAGE`; re-saving (or re-casing) a connection's
        OWN name is not a collision."""
        ModelConnection.objects.create(
            name="taken", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        collided = self._post(
            client, connection_id=str(connection.pk), name="Taken",
            engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b",
            capability="chat",
        )
        assert collided.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "chat conn"  # unchanged
        followed = client.get(collided.url)
        assert 'A connection named “Taken” already exists.' in followed.content.decode()

        recased = self._post(
            client, connection_id=str(connection.pk), name="Chat Conn",
            engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b",
            capability="chat",
        )
        assert recased.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "Chat Conn"  # own name, any casing: not a collision

    def test_the_operator_sentences_use_typographic_quotes(self, client):
        """Global Constraint 17, as an assertion. Registered/Updated/
        name-taken/confirm-gate copy all use U+201C/U+201D. The other
        tests assert on substrings that stop before the quote, so
        nothing else would catch a silent swap to ASCII during a
        refactor of this function."""
        # register one, assert '“' and '”' both appear in the flash
        registered = self._post(
            client, name="quoted conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capability="chat",
        )
        followed = client.get(registered.url)
        body = followed.content.decode()
        assert 'Registered connection “quoted conn”.' in body

        # update it, same
        connection = ModelConnection.objects.get(name="quoted conn")
        updated = self._post(
            client, connection_id=str(connection.pk), name="quoted conn ii",
            engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b",
            capability="chat",
        )
        followed = client.get(updated.url)
        body = followed.content.decode()
        assert 'Updated connection “quoted conn ii”.' in body

        # collide a name, same
        ModelConnection.objects.create(
            name="collider", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        collided = self._post(
            client, name="Collider", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capability="chat",
        )
        followed = client.get(collided.url)
        body = followed.content.decode()
        assert 'A connection named “Collider” already exists.' in body


# --- ConsoleView: three-section layout ---------------------------------------


@pytest.mark.django_db
class TestConsoleViewSections:
    """Three plain sections: In use, On this machine (installed=True rows
    only), and "Getting models" -- rows that are not installed here render
    NOWHERE (the catalog is enrichment data, never display)."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_only_installed_rows_render_catalog_only_rows_render_nowhere(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        installed = [_installed_row(f"installed-{i}:latest") for i in range(2)]
        catalog_only = [_catalog_only_row(f"catalog-{i}") for i in range(6)]
        mock_discover.return_value = installed + catalog_only

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert len(response.context["installed_rows"]) == 2
        assert {item["row"].model_id for item in response.context["installed_rows"]} == {
            "installed-0:latest",
            "installed-1:latest",
        }
        body = response.content.decode()
        for i in range(6):
            assert f"catalog-{i}" not in body
        assert "Starter models" not in body
        assert "Supported models you could add" not in body
        assert "Getting models" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_installed_row_shows_size_and_capability(self, mock_engines, mock_discover, client):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("llama3.1:8b", capability="chat", size=4_700_000_000)
        ]

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "GB" in body
        assert "chat" in body
        # Size must read as size-on-disk, never as a
        # requirement or hardware limit -- nothing here gates by size.
        # The ON DISK column header
        # already says so -- the cell itself is the bare humanized size,
        # not "<size> on disk" repeated per row.
        assert "4.4 GB" in body
        assert "GB on disk" not in body


# --- The "Getting models" section --------------------------------------------
#
# A pinned model list ages, so the section is a needs checklist with
# LIVE status (derived from the role registry + the already-scanned
# installed rows) plus one adapter-derived facts line per model server,
# and NO model names render anywhere in it.


@pytest.mark.django_db
class TestGettingModelsChecklist:
    """"This system needs:" -- one row per distinct capability among
    registered roles, fully derived from `all_roles()`, each with live
    installed-on-this-machine status.

    Two of the tests below assert the checklist's EXACT contents, which
    means they need an exact set of registered roles -- pinned explicitly
    to the two RAG roles via `_only_rag_roles()` rather than assumed, since
    `tools.vision` also registers a role (`vision.generate`) whenever the
    "vision" feature is enabled, which is its default.
    """

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_nothing_installed_every_need_reads_none_installed_yet(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        with _only_rag_roles():
            response = client.get(reverse("inference-console"))

            assert response.context["needs_checklist"] == [
                {"requirement": "a chat model", "used_by": "RAG answer", "satisfied": False},
                {"requirement": "an embedding model", "used_by": "RAG embeddings", "satisfied": False},
            ]
            body = response.content.decode()
            assert "This system needs:" in body
            assert "a chat model" in body
            assert "an embedding model" in body
            assert "RAG answer" in body
            assert "RAG embeddings" in body
            assert body.count("missing — install one from the library below") == 2
            assert "✓ installed" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_satisfied_and_unsatisfied_rows_render_side_by_side(
        self, mock_engines, mock_discover, client
    ):
        """An installed chat model satisfies the chat need; the embeddings
        need still reads as missing -- and the satisfied row never names
        the model (live names live in the dropdowns and "On this machine",
        not here)."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        with _only_rag_roles():
            response = client.get(reverse("inference-console"))

            assert response.context["needs_checklist"] == [
                {"requirement": "a chat model", "used_by": "RAG answer", "satisfied": True},
                {"requirement": "an embedding model", "used_by": "RAG embeddings", "satisfied": False},
            ]
            body = response.content.decode()
            assert "✓ installed at your model server" in body
            assert "missing — install one from the library below" in body
            section = _getting_models_html(body)
            assert "llama3.1:8b" not in section  # no model names in the section

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_capability_unreported_installed_row_satisfies_nothing(
        self, mock_engines, mock_discover, client
    ):
        """Degraded metadata: an installed model whose capability the model
        server didn't report can't honestly satisfy any need -- assigning a
        role is what decides its capability."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("mystery:latest", capability=None, capability_source=None)
        ]

        response = client.get(reverse("inference-console"))

        assert all(not item["satisfied"] for item in response.context["needs_checklist"])

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_newly_registered_role_adds_its_own_checklist_row(
        self, mock_engines, mock_discover, client, _extra_role_registry
    ):
        """A future vision role adds its row automatically -- no code
        change here, just `all_roles()` growing by one entry.

        Pinned explicitly (do not trust the ambient environment): `modules.
        rag.apps.RagConfig.ready()` ALSO registers a real "vision"-capability
        role (`RAG_EXTRACT_ROLE`, media-into-RAG plan T8) whenever "media" is
        in `FARABUNKER_FEATURES` at Django STARTUP -- a fact `_needs_checklist`
        (below) dedupes on ("two roles sharing a capability still produce
        exactly one row", first-registered wins), which would make this
        test's own `used_by` assertion depend on the ambient env var a
        developer/CI happened to run under, not on the behavior this test
        actually means to prove. Explicitly popped here (reverted by
        `_extra_role_registry`'s own snapshot/restore) so `test.vision` is
        deterministically the sole "vision"-capability role regardless of
        that ambient state.
        """
        roles_module._ROLES.pop(RAG_EXTRACT_ROLE, None)
        register_role(RoleSpec(key="test.vision", label="Vision test role", capability="vision"))
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert {
            "requirement": "a vision model",
            "used_by": "Vision test role",
            "satisfied": False,
        } in response.context["needs_checklist"]
        body = response.content.decode()
        assert "a vision model" in body
        assert "Vision test role" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_media_flag_on_grows_a_real_vision_model_row_for_rag_extract(
        self, mock_engines, mock_discover, client, _extra_role_registry
    ):
        """Reviewer coverage note: the test above pops the REAL
        `RAG_EXTRACT_ROLE` out of the way (a synthetic role stands in for
        it, deliberately, to stay deterministic across ambient
        `FARABUNKER_FEATURES` states) -- which means nothing was left
        proving that the REAL role (`tools.rag.apps.RagConfig.ready()`'s
        own "media" registration, T8) actually grows its own checklist row
        in production. This test re-runs `RagConfig.ready()` under an
        explicit "media" override to register it for real, then checks the
        checklist for ITS OWN actual label -- no synthetic role involved.

        The `override_settings` block wraps ONLY the `ready()` call, not
        the HTTP request below: role registration is a one-time side
        effect on the mutable `models.contracts.roles._ROLES` dict (reverted
        afterward by `_extra_role_registry`'s own snapshot/restore), not
        something that needs to stay overridden for the page render itself
        -- keeping the actual `client.get()` outside it avoids the same
        ambient-settings-poisons-the-URL-resolver-cache risk documented in
        `tools.rag.tests.test_ingest.TestIngestEventHandlerPollOnce.
        test_corrupt_pdf_via_watcher_fails_honestly_not_stranded_at_
        pending`'s own docstring.

        `ready()` also re-registers `rag.search`/`rag.ask`/`rag.ingest` in
        `agents.contracts.tools._TOOLS` every time it runs, so this
        snapshots/restores that registry around the call too, inline
        rather than through the `isolated_tool_registry` fixture (a
        fixture cannot wrap the save/restore of BOTH registries around
        ONE `override_settings` block the way this test's own `try`/
        `finally` does) -- otherwise the re-run would reorder those three
        entries relative to every other app's tools for the rest of the
        process, the same registration-order drift `_extra_role_registry`
        exists to prevent for roles.
        """
        from agents.contracts import tools as tools_module

        saved_tools = dict(tools_module._TOOLS)
        try:
            with override_settings(FARABUNKER_FEATURES=frozenset({"vision", "media"})):
                django_apps.get_app_config("rag").ready()
        finally:
            tools_module._TOOLS.clear()
            tools_module._TOOLS.update(saved_tools)

        role = roles_module.get_role(RAG_EXTRACT_ROLE)
        assert role is not None  # the registration above really landed
        assert role.label == "RAG image text extraction"

        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert {
            "requirement": "a vision model",
            "used_by": role.label,
            "satisfied": False,
        } in response.context["needs_checklist"]
        body = response.content.decode()
        assert "a vision model" in body
        assert "RAG image text extraction" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_satisfied_and_missing_rows_render_distinct_status_classes(
        self, mock_engines, mock_discover, client, _extra_role_registry
    ):
        """The needs checklist renders as a table with the status
        column aligned -- a satisfied row and a missing row must carry
        visually distinct classes (the "ok"/green treatment vs the shared
        warning palette), not just different words. The missing row here
        comes from a freshly registered role (isolated-role-registry idiom)
        whose capability has no installed model."""
        register_role(RoleSpec(key="test.vision", label="Vision test role", capability="vision"))
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        response = client.get(reverse("inference-console"))

        checklist = {
            item["requirement"]: item["satisfied"] for item in response.context["needs_checklist"]
        }
        assert checklist["a chat model"] is True
        assert checklist["a vision model"] is False

        body = response.content.decode()
        assert 'class="needs-cell need-status ok"' in body
        assert 'class="needs-cell need-status missing"' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_warm_page_renders_the_same_checklist(self, mock_engines, mock_discover, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is False
        body = response.content.decode()
        assert "Getting models" in body
        assert "✓ installed at your model server" in body
        assert "missing — install one from the library below" in body

    # --- T9: the checklist unions each row's FULL capability list ---------

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_one_multi_capability_row_satisfies_both_needs(
        self, mock_engines, mock_discover, client, _extra_role_registry
    ):
        """A single installed llava/qwen-vl-class row reporting BOTH "chat"
        and "vision" satisfies BOTH needs -- not just whichever one the
        row's own `capability` tie-break happened to pick as primary."""
        register_role(RoleSpec(key="test.vision", label="Vision test role", capability="vision"))
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("llava:13b", capability="chat", capabilities=("chat", "vision"))
        ]

        response = client.get(reverse("inference-console"))

        checklist = {
            item["requirement"]: item["satisfied"] for item in response.context["needs_checklist"]
        }
        assert checklist["a chat model"] is True
        assert checklist["a vision model"] is True

    def test_needs_checklist_unit_union_of_capabilities_list(self):
        """Direct unit coverage of `_needs_checklist` itself: the union
        comes from `row.capabilities` (the full set), not only `row.
        capability` (the single tie-break value)."""
        chat_role = RoleSpec("rag.answer", "RAG answer", "chat")
        vision_role = RoleSpec("test.vision", "Vision test role", "vision")
        row = _installed_row("llava:13b", capability="chat", capabilities=("chat", "vision"))

        checklist = views._needs_checklist([chat_role, vision_role], [{"row": row}])

        assert all(item["satisfied"] for item in checklist)


@pytest.mark.django_db
class TestGettingModelsServerFacts:
    """One facts line per registered model-server adapter -- usual address,
    library link (plain, never fetched), install shape -- every value read
    off the adapter itself, plus the one-sentence freshness pointer."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_facts_line_derives_from_the_adapter(self, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        # Sliced to the "Getting models" server-facts section itself
        # (`inference/_getting_models.html`'s own lead paragraph through
        # its `<ul class="server-facts">` close), not the whole page: the
        # settings-assistant panel (`chat/_assistant_panel.html`) now
        # legitimately renders on this page too in open posture, and its
        # own poll script calls `fetch(...)` -- a DIFFERENT feature's
        # DIFFERENT claim. This section's own claim is narrower: ITS facts
        # are read off the adapter and never fetched by the page itself.
        start = body.index("Get models from your model server's own library:")
        end = body.index("</ul>", start) + len("</ul>")
        facts_section = body[start:end]
        assert "model server's own library" in facts_section
        assert "usually at <code>http://localhost:11434</code>" in facts_section
        assert '<a href="https://ollama.com/library"' in facts_section
        # The install SHAPE (a placeholder template), never a runnable
        # model-pinned command. `<model-name>` renders HTML-escaped.
        assert "install with <code>ollama pull &lt;model-name&gt;</code>" in facts_section
        assert "ollama pull qwen2.5:7b" not in facts_section
        # Offline-by-design: a plain navigational link the operator may
        # click, never something the page fetches on its own. `fetch(`
        # is scoped to THIS section (the panel's own poll script
        # legitimately puts `fetch(` elsewhere on this page); the
        # remote-src assertion is NOT scoped -- it needs no slicing, and
        # slicing it would have made it trivially true instead of a real
        # invariant. Kept at PAGE level (final review, I3): "the Models
        # page never loads a remote asset" is a claim about the whole
        # page, the box's most network-adjacent one, and the section
        # never had a `src="` attribute to narrow away from.
        assert "fetch(" not in facts_section
        assert 'src="https://ollama.com' not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_freshness_pointer_replaces_the_curated_date(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "sorted by popularity" in body
        assert "Examples curated" not in body
        assert "catalog_curated" not in response.context


@pytest.mark.django_db
class TestConsoleViewNoPerRowRegisterForm:
    """There is no per-row "Register" mini-form on a discovered row --
    one-step assign (`role_assign`) is the only action surface. The
    advanced form's own submit button is labelled "Register connection",
    deliberately distinct text, so this checks for the old form's exact
    "Register" button rather than a substring that would also match the
    advanced form."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_per_row_register_form_is_gone(self, mock_engines, mock_discover, client):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_catalog_only_row("nomic-embed-text", capability="embeddings")]

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "<button type=\"submit\">Register</button>" not in body
        assert "Discovered models" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_use_for_control_is_gone(self, mock_engines, mock_discover, client):
        """There is no model-side one-step "use for..." control --
        an installed row is pure inventory; the role rows' "change"
        dropdown is the console's only action surface."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("mistral:7b", capability="chat")]

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "use for…" not in body
        assert "Use for…" not in body
        assert 'aria-label="Use ' not in body
        assert 'class="installed-row"' in body  # the inventory row itself remains


