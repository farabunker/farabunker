"""Unit tests for models/queue/views.py -- the Queue page (T7).

`@pytest.mark.django_db` at class level, pytest-django's own `client`
fixture -- no local override here, unlike `models/registry/tests/
_helpers.py`'s and `tools/rag/tests/_helpers.py`'s own trivial `client`
passthroughs, which their respective packages' test modules import by
name. Rows are created directly against `InferenceJob`
(the exact `models/queue/tests/test_backend.py::TestCancelJob` idiom --
`InferenceJob.objects.create(kind="k", priority=1, state=QUEUED)`), never
through `backend.enqueue`, since these tests are about rendering/cancelling
rows that already exist, not about the enqueue path itself.

A test job kind ("test.marker") is registered against the real core
registry (`models.contracts.jobkinds`) via the same snapshot/restore
autouse fixture `test_backend.py` uses -- its `summarizer` just echoes
`payload["marker"]`, so each row's rendered summary is a caller-chosen,
searchable marker string rather than a full sentence, which keeps the
ordering/position assertions unambiguous.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import payload_fields
from identity.models import AuditEvent
from models.queue.models import (
    CANCELLED,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    InferenceJob,
    JobSettings,
)
from models.contracts import jobkinds
from models.contracts.jobkinds import JobKind, register_job_kind
from models.contracts.queue import QueueUnavailable
from models.queue.tests._helpers import (
    make_admin, make_queue_job, make_user, posture, sign_in, user_principal,
)

MODULE = "models.queue.tests.test_views"


def plan_noop(payload):  # pragma: no cover - never called by the view
    return ([], False)


def handle_noop(payload, models, ctx):  # pragma: no cover - never called by the view
    return {}


def summarize_marker(payload):
    return payload.get("marker", "")


@pytest.fixture(autouse=True)
def registered_test_kind():
    """Registers `"test.marker"` for every test in this module (snapshot/
    restore around the real registry, `test_backend.py`'s idiom) so the
    view's `_summarize()` always has a kind to resolve -- individual tests
    that care about the *unregistered* fallback path register their own
    other key instead; nothing here prevents that."""
    original = dict(jobkinds._JOB_KINDS)
    jobkinds._JOB_KINDS.clear()
    register_job_kind(
        JobKind(
            key="test.marker",
            label="Test job",
            planner=f"{MODULE}.plan_noop",
            handler=f"{MODULE}.handle_noop",
            summarizer=f"{MODULE}.summarize_marker",
        )
    )
    yield
    jobkinds._JOB_KINDS.clear()
    jobkinds._JOB_KINDS.update(original)


def _ref(*, connection_name="conn", model_id="model", footprint_bytes=1024**3, engine="ollama", endpoint="http://ollama.local:11434"):
    return {
        "role": "test.role",
        "engine": engine,
        "endpoint": endpoint,
        "model_id": model_id,
        "connection_name": connection_name,
        "footprint_bytes": footprint_bytes,
    }


def _make_job(
    *,
    kind="test.marker",
    state=QUEUED,
    priority=100,
    marker="job",
    model_refs=None,
    exclusive=False,
    created_at=None,
    started_at=None,
    finished_at=None,
    error="",
    progress=None,
):
    job = InferenceJob.objects.create(
        kind=kind,
        state=state,
        priority=priority,
        payload={"marker": marker},
        model_refs=model_refs or [],
        exclusive=exclusive,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
        progress=progress,
    )
    if created_at is not None:
        InferenceJob.objects.filter(pk=job.pk).update(created_at=created_at)
        job.refresh_from_db()
    return job


# --- Waiting order: the owner requirement ----------------------------------


@pytest.mark.django_db
class TestWaitingOrder:
    def test_lower_priority_number_submitted_later_renders_above_earlier_higher_number(
        self, client
    ):
        """THE owner-requirement test: consumption order is `(priority,
        id)`, not submission order -- a job created SECOND with a LOWER
        priority number must render ABOVE a job created FIRST with a
        higher one."""
        _make_job(priority=10, marker="MARKER_EARLY_HIGH_NUM")
        _make_job(priority=5, marker="MARKER_LATE_LOW_NUM")

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert body.index("MARKER_LATE_LOW_NUM") < body.index("MARKER_EARLY_HIGH_NUM")

    def test_fifo_tie_break_with_1_based_contiguous_positions(self, client):
        _make_job(priority=50, marker="MARKER_ONE")
        _make_job(priority=50, marker="MARKER_TWO")
        _make_job(priority=50, marker="MARKER_THREE")

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()
        pos_1 = '<span class="position">#1</span>'
        pos_2 = '<span class="position">#2</span>'
        pos_3 = '<span class="position">#3</span>'

        assert (
            body.index(pos_1) < body.index("MARKER_ONE")
            < body.index(pos_2) < body.index("MARKER_TWO")
            < body.index(pos_3) < body.index("MARKER_THREE")
        )


# --- Section presence -------------------------------------------------------


@pytest.mark.django_db
class TestSectionPresence:
    def test_whole_page_empty_state_when_no_jobs_exist(self, client):
        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert "Nothing has been queued yet." in body
        assert "<h2>Running</h2>" not in body
        assert "<h2>Waiting</h2>" not in body
        assert "<h2>Finished</h2>" not in body
        # Settings sections still render even with no jobs at all.
        assert "Memory budget" in body
        assert "Retention" in body

    def test_only_waiting_section_renders(self, client):
        _make_job(state=QUEUED, marker="ONLY_WAITING")

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert "<h2>Waiting</h2>" in body
        assert "<h2>Running</h2>" not in body
        assert "<h2>Finished</h2>" not in body
        assert "Nothing has been queued yet." not in body

    def test_only_running_section_renders(self, client):
        _make_job(state=RUNNING, marker="ONLY_RUNNING", started_at=timezone.now())

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert "<h2>Running</h2>" in body
        assert "<h2>Waiting</h2>" not in body
        assert "<h2>Finished</h2>" not in body

    def test_only_finished_section_renders(self, client):
        _make_job(state=SUCCEEDED, marker="ONLY_FINISHED", finished_at=timezone.now())

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert "<h2>Finished</h2>" in body
        assert "<h2>Running</h2>" not in body
        assert "<h2>Waiting</h2>" not in body


# --- Cancel ------------------------------------------------------------------


@pytest.mark.django_db
class TestQueueJobCancel:
    def test_cancels_a_queued_job(self, client):
        job = _make_job(state=QUEUED)

        response = client.post(reverse("jobs-queue-cancel", args=[job.pk]), follow=True)

        assert response.status_code == 200
        assert "Cancelled." in response.content.decode()
        job.refresh_from_db()
        assert job.state == CANCELLED

    def test_already_running_outcome_message(self, client):
        job = _make_job(state=RUNNING, started_at=timezone.now())

        response = client.post(reverse("jobs-queue-cancel", args=[job.pk]), follow=True)

        assert "That job had already started — it will run to completion." in response.content.decode()
        job.refresh_from_db()
        assert job.state == RUNNING

    def test_already_finished_outcome_message(self, client):
        job = _make_job(state=SUCCEEDED, finished_at=timezone.now())

        response = client.post(reverse("jobs-queue-cancel", args=[job.pk]), follow=True)

        assert "That job had already finished." in response.content.decode()

    def test_unknown_job_outcome_message(self, client):
        response = client.post(reverse("jobs-queue-cancel", args=[999999]), follow=True)

        assert "That job is no longer in the queue." in response.content.decode()

    def test_redirects_to_the_queue_page(self, client):
        job = _make_job(state=QUEUED)

        response = client.post(reverse("jobs-queue-cancel", args=[job.pk]))

        assert response.status_code == 302
        assert response.url == reverse("jobs-queue")


# --- Settings: memory budget + max concurrent jobs --------------------------


@pytest.mark.django_db
class TestBudgetConcurrencySettings:
    def _post(self, client, **fields):
        data = {"form": "budget", "budget_gb": "", "max_concurrent_jobs": "4"}
        data.update(fields)
        return client.post(reverse("jobs-settings-update"), data, follow=True)

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Memory budget must be a number of GB."),
            ("0", "Memory budget must be greater than zero."),
            ("-5", "Memory budget must be greater than zero."),
        ],
    )
    def test_invalid_budget_is_a_clean_error_and_does_not_save(self, client, bad_value, expected):
        JobSettings.objects.create(pk=1, memory_budget_bytes=None, max_concurrent_jobs=4)

        response = self._post(client, budget_gb=bad_value)

        assert response.status_code == 200
        assert expected in response.content.decode()
        assert JobSettings.get_solo().max_concurrent_jobs == 4

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Max concurrent jobs must be a whole number."),
            ("", "Max concurrent jobs must be a whole number."),
            ("0", "Max concurrent jobs must be a positive number."),
            ("-1", "Max concurrent jobs must be a positive number."),
        ],
    )
    def test_invalid_max_concurrent_is_a_clean_error_and_does_not_save(
        self, client, bad_value, expected
    ):
        JobSettings.objects.create(pk=1, memory_budget_bytes=None, max_concurrent_jobs=7)

        response = self._post(client, max_concurrent_jobs=bad_value, budget_gb="")

        assert expected in response.content.decode()
        assert JobSettings.get_solo().max_concurrent_jobs == 7

    def test_astronomically_large_budget_is_rejected_cleanly_never_500s(self, client):
        """S1 (Coherence Wave B): `memory_budget_bytes` is a
        `BigIntegerField` (Postgres `bigint`, max 2**63-1) -- same failure
        class T10 review MINOR 5 fixed for `tools.rag.views.
        _upload_cap_update`'s own GB field (`1e308` is itself a
        finite float, but `gb_to_bytes(1e308)` overflows `round()` to an
        uncaught `OverflowError`), never previously guarded here."""
        JobSettings.objects.create(pk=1, memory_budget_bytes=None, max_concurrent_jobs=4)

        response = self._post(client, budget_gb="1e308")

        assert response.status_code == 200
        assert "too large" in response.content.decode()
        assert JobSettings.get_solo().memory_budget_bytes is None

    def test_a_budget_past_the_bigint_ceiling_is_rejected_cleanly(self, client):
        """M1 (Coherence Wave B review): the test above (`budget_gb=
        "1e308"`) is satisfied by the `OverflowError` catch alone --
        neutralising `exceeds_field_ceiling` leaves it green, because
        `1e308 * 1024**3` itself overflows `round()` before the ceiling
        check ever runs. `1e10` is the input ONLY the ceiling check
        catches: finite, `round()` succeeds (1.07e19 bytes), and that
        number exceeds `BIGINT_FIELD_MAX` (2**63-1 = 9.22e18)."""
        JobSettings.objects.create(pk=1, memory_budget_bytes=None, max_concurrent_jobs=4)

        response = self._post(client, budget_gb="1e10")

        assert response.status_code == 200
        assert "too large" in response.content.decode()
        assert JobSettings.get_solo().memory_budget_bytes is None

    def test_astronomically_large_max_concurrent_is_rejected_cleanly_never_500s(self, client):
        """S1 (Coherence Wave B): `max_concurrent_jobs` is a
        `PositiveIntegerField` (Postgres `integer`, max 2**31-1) with no
        prior ceiling check -- `int()` parses a 40-digit whole-number
        string without raising, so it used to reach `settings_row.save()`
        unchecked and 500 as an uncaught `django.db.utils.DataError`."""
        JobSettings.objects.create(pk=1, memory_budget_bytes=None, max_concurrent_jobs=7)
        astronomically_large = "9" + "0" * 39

        response = self._post(client, max_concurrent_jobs=astronomically_large, budget_gb="")

        assert "too large" in response.content.decode()
        assert JobSettings.get_solo().max_concurrent_jobs == 7

    def test_valid_budget_round_trips_gb_identically(self, client):
        """F1 (Coherence Wave C): the FORM reads back on the Job
        execution page, which now carries it; the queue page still
        RENDERS the same human size beside what is running against it,
        so the round trip is asserted on both surfaces rather than moved
        off one of them."""
        self._post(client, budget_gb="8.5", max_concurrent_jobs="3")

        settings_row = JobSettings.get_solo()
        assert settings_row.memory_budget_bytes == round(8.5 * 1024**3)
        assert settings_row.max_concurrent_jobs == 3

        body = client.get(reverse("jobs-settings")).content.decode()
        assert 'value="8.5"' in body
        assert "8.5 GB" in body
        assert "8.5 GB" in client.get(reverse("jobs-queue")).content.decode()

    def test_blank_budget_clears_to_none_and_shows_not_set_copy(self, client):
        JobSettings.objects.create(
            pk=1, memory_budget_bytes=round(8.5 * 1024**3), max_concurrent_jobs=3
        )

        self._post(client, budget_gb="", max_concurrent_jobs="3")

        assert JobSettings.get_solo().memory_budget_bytes is None
        response = client.get(reverse("jobs-queue"))
        assert "not set. Jobs run one at a time." in response.content.decode()

    def test_a_valid_write_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): `JobSettings` was one of the four
        unaudited settings surfaces the backend audit named with no
        recorded rationale."""
        self._post(client, budget_gb="8.5", max_concurrent_jobs="3")

        assert AuditEvent.objects.filter(action=actions.QUEUE_SETTINGS_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.QUEUE_SETTINGS_UPDATED)
        assert event.actor_kind == "open"
        assert event.detail == {
            "memory_budget_bytes": round(8.5 * 1024**3), "max_concurrent_jobs": 3,
        }

    def test_an_invalid_write_is_not_audited(self, client):
        self._post(client, budget_gb="not-a-number")

        assert AuditEvent.objects.count() == 0


# --- Settings: retention limit + default priority ---------------------------


@pytest.mark.django_db
class TestRetentionPrioritySettings:
    def _post(self, client, **fields):
        data = {"form": "retention", "retention_limit": "50", "default_priority": "100"}
        data.update(fields)
        return client.post(reverse("jobs-settings-update"), data, follow=True)

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Retention limit must be a whole number."),
            ("", "Retention limit must be a whole number."),
            ("0", "Retention limit must be a positive number."),
            ("-1", "Retention limit must be a positive number."),
        ],
    )
    def test_invalid_retention_limit(self, client, bad_value, expected):
        JobSettings.objects.create(pk=1, retention_limit=20, default_priority=100)

        response = self._post(client, retention_limit=bad_value)

        assert expected in response.content.decode()
        assert JobSettings.get_solo().retention_limit == 20

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Default priority must be a whole number."),
            ("", "Default priority must be a whole number."),
            ("0", "Default priority must be a positive number."),
            ("-1", "Default priority must be a positive number."),
        ],
    )
    def test_invalid_default_priority(self, client, bad_value, expected):
        JobSettings.objects.create(pk=1, retention_limit=20, default_priority=77)

        response = self._post(client, default_priority=bad_value)

        assert expected in response.content.decode()
        assert JobSettings.get_solo().default_priority == 77

    def test_astronomically_large_retention_limit_is_rejected_cleanly_never_500s(self, client):
        """S1 (Coherence Wave B): `retention_limit` is a
        `PositiveIntegerField` (Postgres `integer`, max 2**31-1) with no
        prior ceiling check."""
        JobSettings.objects.create(pk=1, retention_limit=20, default_priority=100)
        astronomically_large = "9" + "0" * 39

        response = self._post(client, retention_limit=astronomically_large)

        assert "too large" in response.content.decode()
        assert JobSettings.get_solo().retention_limit == 20

    def test_astronomically_large_default_priority_is_rejected_cleanly_never_500s(self, client):
        """S1 (Coherence Wave B): `default_priority` is a
        `PositiveIntegerField` (Postgres `integer`, max 2**31-1) with no
        prior ceiling check."""
        JobSettings.objects.create(pk=1, retention_limit=20, default_priority=77)
        astronomically_large = "9" + "0" * 39

        response = self._post(client, default_priority=astronomically_large)

        assert "too large" in response.content.decode()
        assert JobSettings.get_solo().default_priority == 77

    def test_valid_values_save_together(self, client):
        self._post(client, retention_limit="30", default_priority="5")

        settings_row = JobSettings.get_solo()
        assert settings_row.retention_limit == 30
        assert settings_row.default_priority == 5

    def test_a_valid_write_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): the SAME `QUEUE_SETTINGS_UPDATED`
        action `TestBudgetConcurrencySettings`'s own pin uses -- this
        form is the other of the two `JobSettings` write paths.

        RE-PINNED ON THE MERGE of round-3 hardening into this branch:
        C-7 added `max_queued_per_principal` as a THIRD field on this
        same form, saved in the same `transaction.atomic()` block, so
        the one audit record this form writes carries all three. A
        record naming only two of the three fields the write changed
        would be the exact partial-truth this assertion exists to
        prevent; it is still ONE record, which is what "exactly once"
        pins."""
        self._post(client, retention_limit="30", default_priority="5")

        assert AuditEvent.objects.filter(action=actions.QUEUE_SETTINGS_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.QUEUE_SETTINGS_UPDATED)
        assert event.actor_kind == "open"
        assert event.detail == {
            "retention_limit": 30, "default_priority": 5,
            "max_queued_per_principal": None,
        }

    def test_count_of_limit_kept_arithmetic(self, client):
        JobSettings.objects.create(pk=1, retention_limit=10)
        for i in range(3):
            _make_job(state=SUCCEEDED, marker=f"finished-{i}", finished_at=timezone.now())

        response = client.get(reverse("jobs-queue"))

        assert "3 of 10 kept" in response.content.decode()


# --- Settings: response timeout (one-timeout task, 2026-09-17) --------------


@pytest.mark.django_db
class TestResponseTimeoutSettings:
    """`JobSettings.response_timeout_seconds` -- its own form, its own
    dispatched `form="timeout"` value (see `models/queue/views.py::
    _update_response_timeout`'s own docstring for why it isn't folded
    into either sibling group). Bounds 60..7200, default 1800."""

    def _post(self, client, **fields):
        data = {"form": "timeout", "response_timeout_seconds": "1800"}
        data.update(fields)
        return client.post(reverse("jobs-settings-update"), data, follow=True)

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Response timeout must be a whole number of seconds."),
            ("", "Response timeout must be a whole number of seconds."),
            ("59", "Response timeout must be at least 60 seconds."),
            ("-1", "Response timeout must be at least 60 seconds."),
            # Not the apostrophe-bearing "can't" substring -- Django
            # autoescapes the message into `can&#x27;t` in the rendered
            # HTML (same convention `tools/rag/tests/test_views_
            # retrieval_settings_and_gating.py::test_above_maximum_is_
            # rejected` already documents for the identical "can't be
            # more than" copy).
            ("7201", "more than 7200 seconds"),
        ],
    )
    def test_invalid_response_timeout_is_a_clean_error_and_does_not_save(
        self, client, bad_value, expected,
    ):
        JobSettings.objects.create(pk=1, response_timeout_seconds=1234)

        response = self._post(client, response_timeout_seconds=bad_value)

        assert expected in response.content.decode()
        assert JobSettings.get_solo().response_timeout_seconds == 1234

    @pytest.mark.parametrize("boundary_value", ["60", "7200"])
    def test_the_boundary_values_are_accepted(self, client, boundary_value):
        JobSettings.objects.create(pk=1, response_timeout_seconds=1234)

        response = self._post(client, response_timeout_seconds=boundary_value)

        assert "more than 7200 seconds" not in response.content.decode()
        assert "must be at least" not in response.content.decode()
        assert JobSettings.get_solo().response_timeout_seconds == int(boundary_value)

    def test_astronomically_large_response_timeout_is_rejected_cleanly_never_500s(self, client):
        """MINOR 1 (fix round 1): ONE ceiling check, using the FIELD'S
        OWN max (`RESPONSE_TIMEOUT_SECONDS_MAX`) rather than the bare
        column ceiling -- so this pins the EXACT message that fires,
        not merely "some rejection happened", which is what a bare
        `response.status_code == 200` + unchanged-value assertion would
        pass under EITHER guard, pinning nothing about which one ran."""
        JobSettings.objects.create(pk=1, response_timeout_seconds=1234)
        astronomically_large = "9" + "0" * 39

        response = self._post(client, response_timeout_seconds=astronomically_large)

        assert response.status_code == 200
        assert "Response timeout" in response.content.decode()
        assert "more than 7200 seconds" in response.content.decode()
        assert JobSettings.get_solo().response_timeout_seconds == 1234

    def test_a_valid_write_saves_and_redirects_to_this_page(self, client):
        response = self._post(client, response_timeout_seconds="600")

        assert JobSettings.get_solo().response_timeout_seconds == 600
        assert response.redirect_chain
        assert response.redirect_chain[-1][0] == reverse("jobs-settings")

    def test_a_valid_write_is_audited_exactly_once(self, client):
        self._post(client, response_timeout_seconds="600")

        assert AuditEvent.objects.filter(action=actions.QUEUE_SETTINGS_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.QUEUE_SETTINGS_UPDATED)
        assert event.detail == {"response_timeout_seconds": 600}

    def test_an_invalid_write_is_not_audited(self, client):
        self._post(client, response_timeout_seconds="59")

        assert AuditEvent.objects.filter(action=actions.QUEUE_SETTINGS_UPDATED).count() == 0


# --- F1 (Coherence Wave C): the "Job execution" settings page ----------------


@pytest.mark.django_db
class TestTheJobExecutionPage:
    """`jobs-settings` -- the registered settings page all six
    `JobSettings` controls now live on (originally four; `max_queued_
    per_principal` joined at C-7 round-3 hardening, `response_timeout_
    seconds` at the one-timeout task, fix round 1 MINOR 2 -- this class
    docstring drifted behind both additions until now). What the
    settings-area guards (`foundation/tests/test_settings_help.py`,
    `test_page_names.py`, `test_settings_area.py`) already pin is not
    repeated here; this is the page's own behaviour."""

    def test_it_renders_the_six_controls_each_under_its_own_anchor(self, client):
        """One id per CONTROL, not one per form. The help card cites six
        separate anchors and two fields sharing one is exactly the defect
        `TestTheModelFieldCoverage` exists to catch -- this is the same
        claim asserted against the rendered page from the page's own
        side."""
        JobSettings.objects.create(
            pk=1, memory_budget_bytes=round(8.5 * 1024**3), max_concurrent_jobs=3,
            retention_limit=20, default_priority=77, max_queued_per_principal=9,
            response_timeout_seconds=600,
        )

        body = client.get(reverse("jobs-settings")).content.decode()

        for anchor in ("memory-budget", "max-concurrent-jobs",
                       "retention-limit", "default-priority",
                       "max-queued-per-principal", "response-timeout-seconds"):
            assert f'id="{anchor}"' in body, anchor
        assert 'value="8.5"' in body
        assert 'value="3"' in body
        assert 'value="20"' in body
        assert 'value="77"' in body
        assert 'value="9"' in body
        assert 'value="600"' in body

    def test_both_forms_post_to_the_one_dispatched_endpoint(self, client):
        """S2's sanctioned topology, from the template's side: ONE POST
        URL for the page, with a hidden `form` field saying which of the
        three submitted -- never a URL per field. Three forms now
        (one-timeout task, 2026-09-17 added "timeout"), the test name
        kept as-is since it names the TOPOLOGY, not a count."""
        body = client.get(reverse("jobs-settings")).content.decode()

        assert body.count(f'action="{reverse("jobs-settings-update")}"') == 3
        assert 'name="form" value="budget"' in body
        assert 'name="form" value="retention"' in body
        assert 'name="form" value="timeout"' in body

    def test_it_marks_its_own_sidebar_entry_current(self, client):
        """The one step of the settings-page recipe with no guard of its
        own (`docs/EXTENDING.md` step 3): a leaf that overrides a
        `side_current_*` block no other template defines simply never
        marks itself, silently. Pinned here for this page rather than
        left to a reader to notice."""
        body = client.get(reverse("jobs-settings")).content.decode()

        assert f'<a href="{reverse("jobs-settings")}" class="current">Job execution</a>' in body

    def test_an_unset_budget_says_jobs_run_one_at_a_time(self, client):
        JobSettings.objects.create(pk=1, memory_budget_bytes=None)

        body = client.get(reverse("jobs-settings")).content.decode()

        assert "Jobs run one at a time" in body

    def test_a_settings_table_that_is_not_reachable_falls_back_to_defaults(
            self, client, monkeypatch):
        """Never-500, the same independent degradation `QueueView` has --
        and it matters MORE here: this is a page an operator opens
        precisely when the box is half-configured."""

        def _raise(cls):
            raise OperationalError("settings table unavailable")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_raise))

        response = client.get(reverse("jobs-settings"))
        body = response.content.decode()

        assert response.status_code == 200
        assert f'value="{JobSettings.MAX_CONCURRENT_JOBS_DEFAULT}"' in body
        assert f'value="{JobSettings.RETENTION_LIMIT_DEFAULT}"' in body
        assert f'value="{JobSettings.RESPONSE_TIMEOUT_SECONDS_DEFAULT}"' in body
        assert "Jobs run one at a time" in body

    def test_it_costs_no_queue_snapshot(self, client, monkeypatch):
        """This page is configuration only: the live "what is running
        against this budget" reading stays on the Queue page, which
        already pays for a snapshot. A settings page that quietly took
        one would make the whole settings area depend on the jobs table
        being reachable."""

        def _never(*args, **kwargs):  # pragma: no cover - the point is it is not called
            raise AssertionError("the settings page must not read the queue")

        monkeypatch.setattr("models.queue.views.queue_snapshot", _never)

        assert client.get(reverse("jobs-settings")).status_code == 200

    def test_a_valid_write_redirects_back_to_this_page(self, client):
        response = client.post(
            reverse("jobs-settings-update"),
            {"form": "retention", "retention_limit": "30", "default_priority": "5"},
        )

        assert response.status_code == 302
        assert response.url == reverse("jobs-settings")

    @pytest.mark.parametrize("bad_form", ["", "budgets", "explode"])
    def test_an_unrecognised_form_flashes_and_saves_nothing(self, client, bad_form):
        """F1 (Coherence Wave C): a dispatch value naming no form used to
        be a SILENT no-op redirect -- a page that looks exactly like
        success. It refuses out loud now, and still never 500s and still
        never raw-400s."""
        JobSettings.objects.create(pk=1, retention_limit=20, default_priority=77)

        response = client.post(
            reverse("jobs-settings-update"),
            {"form": bad_form, "retention_limit": "30", "default_priority": "5"},
            follow=True,
        )

        assert response.status_code == 200
        assert "is not a recognised settings form." in response.content.decode()
        settings_row = JobSettings.get_solo()
        assert settings_row.retention_limit == 20
        assert settings_row.default_priority == 77
        assert not AuditEvent.objects.filter(action=actions.QUEUE_SETTINGS_UPDATED).exists()


# --- Worker-down hint --------------------------------------------------------


@pytest.mark.django_db
class TestWorkerDownHint:
    HINT = (
        "Nothing has started in the last 10 minutes. If this doesn't move, "
        "check that the worker container is running."
    )

    def test_shown_when_oldest_waiting_job_exceeds_threshold_and_nothing_running(self, client):
        _make_job(state=QUEUED, marker="OLD", created_at=timezone.now() - timedelta(minutes=11))

        response = client.get(reverse("jobs-queue"))

        assert self.HINT in response.content.decode()

    def test_suppressed_when_under_the_threshold(self, client):
        _make_job(state=QUEUED, marker="RECENT", created_at=timezone.now() - timedelta(minutes=5))

        response = client.get(reverse("jobs-queue"))

        assert self.HINT not in response.content.decode()

    def test_suppressed_when_something_is_running(self, client):
        _make_job(state=QUEUED, marker="OLD", created_at=timezone.now() - timedelta(minutes=20))
        _make_job(state=RUNNING, marker="RUNNING_ONE", started_at=timezone.now())

        response = client.get(reverse("jobs-queue"))

        assert self.HINT not in response.content.decode()

    def test_suppressed_when_no_waiting_jobs(self, client):
        response = client.get(reverse("jobs-queue"))

        assert self.HINT not in response.content.decode()


# --- Footprint display -------------------------------------------------------


@pytest.mark.django_db
class TestFootprintDisplay:
    def test_unknown_footprint_row_shows_size_not_measured(self, client):
        _make_job(state=QUEUED, marker="UNSIZED", model_refs=[_ref(footprint_bytes=None)])

        response = client.get(reverse("jobs-queue"))

        assert "size not measured" in response.content.decode()

    def test_running_now_line_shows_at_least_phrasing_when_a_running_model_is_unmeasured(
        self, client
    ):
        JobSettings.objects.create(pk=1, memory_budget_bytes=round(16 * 1024**3))
        _make_job(
            state=RUNNING,
            marker="RUNNING_UNSIZED",
            model_refs=[_ref(footprint_bytes=None)],
            started_at=timezone.now(),
        )

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert "at least" in body
        assert "one model's size not yet measured" in body


# --- "runs alone" chip -------------------------------------------------------


@pytest.mark.django_db
class TestRunsAloneChip:
    """`_present_row`'s `runs_alone` flag (models/queue/views.py) is
    `row.exclusive or row.footprint_bytes is None` -- deliberately
    narrower than `models.queue.scheduler.effectively_exclusive()` (see
    that flag's own docstring). Pinned here per row-shape, independent of
    `TestFootprintDisplay`'s "at least" budget-line coverage above."""

    def test_declared_exclusive_running_job_shows_the_chip(self, client):
        _make_job(
            state=RUNNING,
            marker="EXCLUSIVE_RUNNING",
            exclusive=True,
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
        )

        response = client.get(reverse("jobs-queue"))

        assert "runs alone" in response.content.decode()

    def test_unmeasured_footprint_running_job_shows_the_chip(self, client):
        _make_job(
            state=RUNNING,
            marker="UNMEASURED_RUNNING",
            exclusive=False,
            model_refs=[_ref(footprint_bytes=None)],
            started_at=timezone.now(),
        )

        response = client.get(reverse("jobs-queue"))

        assert "runs alone" in response.content.decode()

    def test_normal_measured_running_job_does_not_show_the_chip(self, client):
        _make_job(
            state=RUNNING,
            marker="NORMAL_RUNNING",
            exclusive=False,
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
        )

        response = client.get(reverse("jobs-queue"))

        assert "runs alone" not in response.content.decode()


# --- Progress display (T3) -----------------------------------------------


@pytest.mark.django_db
class TestProgressDisplay:
    """`_present_row`'s `progress_text`/`progress_percent` (T3), rendered
    by the Running section only (`queue.html`'s `progress-line`/
    `progress-track` markup) -- waiting/finished rows never carry a live
    `progress` value worth showing (a queued job hasn't started; a
    finished job's is cleared on success and stale-but-uninteresting on
    failure), so these tests all use `state=RUNNING`."""

    def test_running_row_with_total_shows_text_and_bar(self, client):
        _make_job(
            state=RUNNING,
            marker="WITH_TOTAL",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 3, "total": 10, "unit": "items", "label": "chunks"},
        )

        response = client.get(reverse("jobs-queue"))
        content = response.content.decode()

        assert "3 of 10 chunks" in content
        assert 'class="progress-track"' in content
        assert "width: 30%" in content

    def test_running_row_without_total_shows_text_only_no_bar(self, client):
        _make_job(
            state=RUNNING,
            marker="NO_TOTAL",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 4, "total": None, "unit": "items", "label": "chunks"},
        )

        response = client.get(reverse("jobs-queue"))
        content = response.content.decode()

        assert "4 chunks" in content
        assert "4 of" not in content.split('class="job-row"')[1].split("</li>")[0]
        assert 'class="progress-track"' not in content

    def test_running_row_with_seconds_unit_renders_mm_ss(self, client):
        _make_job(
            state=RUNNING,
            marker="SECONDS",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 65, "total": 125, "unit": "seconds", "label": ""},
        )

        response = client.get(reverse("jobs-queue"))
        content = response.content.decode()

        assert "1:05 of 2:05" in content

    def test_running_row_with_seconds_past_an_hour_rolls_over_to_hmmss(self, client):
        """T10 review MINOR 1: the Queue's own progress timecode now
        renders via `foundation.format.format_timecode`, the SAME formatter
        `tools.rag.retrieval.locator_for`'s citations already use --
        `3722` seconds ("1:02:02") used to render as the old local
        `_format_seconds_mm_ss` helper's own no-hour-rollover `"62:02"`,
        a second, disagreeing timecode format for the same video an
        operator could see BOTH surfaces for. Pinning `"1:02:02"` here
        (not `"62:02"`) proves the two surfaces now agree."""
        _make_job(
            state=RUNNING,
            marker="OVER_AN_HOUR",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 3722, "total": None, "unit": "seconds", "label": ""},
        )

        response = client.get(reverse("jobs-queue"))
        content = response.content.decode()

        assert "1:02:02" in content
        assert "62:02" not in content

    def test_running_row_with_no_progress_shows_neither_text_nor_bar(self, client):
        _make_job(
            state=RUNNING,
            marker="NO_PROGRESS",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress=None,
        )

        response = client.get(reverse("jobs-queue"))
        content = response.content.decode()

        assert 'class="progress-line"' not in content
        assert 'class="progress-track"' not in content


@pytest.mark.django_db
class TestMalformedProgressDegradesGracefully:
    """Adversarial-review MAJOR fix: `row.progress` is kind-owned, opaque
    JSON a FAILED job's handler last wrote (preserved verbatim -- see
    `Worker._execute`'s docstring on the success/failure writeback
    asymmetry), so it can carry a shape a CURRENT reader doesn't expect.
    `_present_row` must degrade THIS ROW's progress display to nothing,
    never take the whole page down for every row on it -- the same
    guarantee `_summarize`'s own docstring already makes for a broken
    summarizer."""

    def test_non_numeric_total_does_not_500_the_page_and_hides_only_that_rows_progress(self, client):
        """T4 strengthening: a second, healthy RUNNING row with valid
        progress sits alongside the malformed one -- proving the "only" in
        this test's own name is actually true (the malformed row's own
        degradation must not blank out every OTHER row's progress too, a
        failure mode a guard hoisted up to loop scope by some future
        refactor could introduce without this test noticing)."""
        _make_job(
            state=FAILED,
            marker="MALFORMED_PROGRESS",
            model_refs=[_ref(footprint_bytes=1024**3)],
            finished_at=timezone.now(),
            error="boom",
            progress={"done": 3, "total": "10", "unit": "items", "label": "x"},
        )
        _make_job(
            state=RUNNING,
            marker="HEALTHY_RUNNING",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 3, "total": 10, "unit": "items", "label": "chunks"},
        )

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        content = response.content.decode()
        # Row-scoped (not whole-page) assertions -- Running renders before
        # Finished (queue.html's section order), so with exactly these two
        # rows, rows[1] is HEALTHY_RUNNING and rows[2] is MALFORMED_PROGRESS.
        rows = content.split('class="job-row"')
        assert "HEALTHY_RUNNING" in rows[1]
        assert "3 of 10 chunks" in rows[1]  # the healthy row's progress still renders
        assert 'class="progress-line"' in rows[1]
        assert 'class="progress-track"' in rows[1]

        assert "MALFORMED_PROGRESS" in rows[2]  # the row itself still renders
        assert 'class="progress-line"' not in rows[2]
        assert 'class="progress-track"' not in rows[2]

    def test_huge_done_against_normal_total_raises_past_the_inner_guard_and_degrades_the_whole_row(self, client):
        """T10 re-review MINOR 1 (renamed): this covers the OUTER guard in
        `_present_row`, not the inner blank-formatted-total handling in
        `_progress_text_and_percent` (see the sibling tests below for
        that). `done`/`total` off `InferenceJob.progress` are opaque JSON
        numbers -- an adversarial/corrupt handler could write one with far
        more digits than a `float` can represent (`10**400`). `total=1`
        here is a NORMAL, formattable total, so `_progress_text_and_
        percent`'s own blank-formatted-total check never fires; the raw
        `done / total * 100` percent calculation is a separate arithmetic
        site that still raises directly: `10**400 / 1` raises
        `OverflowError: integer division result too large for a float`
        (not through `format_timecode` at all), which `_present_row`'s
        outer guard catches. Unlike the blank-total fix (which degrades
        only the "of ..." half while keeping an honest `done` text), this
        raises before any text is returned, so the ENTIRE row's progress
        line -- text and bar -- disappears; the page itself still renders
        200."""
        _make_job(
            state=RUNNING,
            marker="HUGE_DONE",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 10**400, "total": 1, "unit": "seconds", "label": ""},
        )

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        content = response.content.decode()
        rows = content.split('class="job-row"')
        assert "HUGE_DONE" in rows[1]
        assert 'class="progress-line"' not in rows[1]
        assert 'class="progress-track"' not in rows[1]

    def test_huge_total_with_normal_done_hides_of_total_but_keeps_honest_done_text(self, client):
        """The actual bug this commit fixes: before it, a `total` too
        large for `format_timecode` to represent (`10**400`) rendered as
        a BLANK formatted total rather than raising -- `format_timecode`
        itself is OverflowError-safe (foundation/format.py) and returns `""`,
        so no exception ever reached `_present_row`'s guard. That left
        `_progress_text_and_percent` constructing the dishonest text
        `"0:01 of "` (a fabricated dangling "of" with nothing after it)
        with a 0%-wide, but still rendered, progress bar. The fix treats
        a blank-formatted total exactly like a falsy one: this row must
        show only the honest `done` text, no "of ...", and no bar at
        all."""
        _make_job(
            state=RUNNING,
            marker="HUGE_TOTAL",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 1, "total": 10**400, "unit": "seconds", "label": ""},
        )

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        content = response.content.decode()
        rows = content.split('class="job-row"')
        row = rows[1].split("</li>")[0]  # scope to this row only -- the page's
        # unrelated "{{ finished_count }} of {{ retention_limit }} kept" footer
        # also contains " of ", outside any job-row
        assert "HUGE_TOTAL" in row
        assert 'class="progress-line">0:01<' in row  # honest done text, nothing appended
        assert " of " not in row
        assert 'class="progress-track"' not in row

    def test_both_huge_and_equal_hides_of_total_and_percent_bar(self, client):
        """Sibling bug to the huge-total case above, at the OTHER
        arithmetic site: with `done == total == 10**400`, `format_timecode`
        blanks BOTH formatted halves (`" of "`, all fabrication, no honest
        text left at all), but the raw `done / total * 100` percent
        calculation does NOT raise here -- `10**400 / 10**400` divides
        cleanly to `1.0` with no `OverflowError`, since both operands are
        equal in magnitude, so it used to produce a fully-fabricated 100%-
        wide bar under that blank text. The fix's `has_total` check keys
        off the formatted total being blank (not off whether the percent
        calculation would raise), so it suppresses the bar here too, even
        though nothing here raises at all."""
        _make_job(
            state=RUNNING,
            marker="BOTH_HUGE",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress={"done": 10**400, "total": 10**400, "unit": "seconds", "label": ""},
        )

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        content = response.content.decode()
        rows = content.split('class="job-row"')
        row = rows[1].split("</li>")[0]  # scope to this row only -- the page's
        # unrelated "{{ finished_count }} of {{ retention_limit }} kept" footer
        # also contains " of ", outside any job-row
        assert "BOTH_HUGE" in row
        assert " of " not in row
        # both halves blank -> progress_text itself is "" (falsy), so the
        # template's own `{% if row.progress_text %}` guard skips the
        # progress-line entirely, not just the bar.
        assert 'class="progress-line"' not in row
        assert 'class="progress-track"' not in row

    @pytest.mark.parametrize("bad_progress", ["a bare string", [1, 2, 3]])
    def test_non_dict_progress_does_not_500(self, client, bad_progress):
        """T10 re-review MINOR 1: `_progress_text_and_percent`'s own `if
        not progress:` guard only screens out FALSY values (`None`/`{}`)
        -- a non-empty string or a non-empty list is truthy, so it sailed
        past that guard into `progress.get(...)`, an uncaught
        `AttributeError` before `_present_row`'s guard caught it too."""
        _make_job(
            state=RUNNING,
            marker="NON_DICT_PROGRESS",
            model_refs=[_ref(footprint_bytes=1024**3)],
            started_at=timezone.now(),
            progress=bad_progress,
        )

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        content = response.content.decode()
        rows = content.split('class="job-row"')
        assert "NON_DICT_PROGRESS" in rows[1]
        assert 'class="progress-line"' not in rows[1]
        assert 'class="progress-track"' not in rows[1]


# --- Degradation --------------------------------------------------------------


@pytest.mark.django_db
class TestDegradation:
    def test_queue_unavailable_renders_200_with_migrations_copy(self, client, monkeypatch):
        def _raise(*args, **kwargs):
            raise QueueUnavailable("jobs tables unavailable")

        monkeypatch.setattr("models.queue.views.queue_snapshot", _raise)

        response = client.get(reverse("jobs-queue"))

        assert response.status_code == 200
        assert "The queue isn't ready yet — run database migrations." in response.content.decode()

    def test_job_settings_unavailable_falls_back_to_defaults_independently_of_the_snapshot(
        self, client, monkeypatch
    ):
        """Pins the divergence `QueueView`'s own docstring documents: the
        settings-row fetch and `queue_snapshot()` are two INDEPENDENT
        try/excepts, not one shared one. Here only `JobSettings.get_solo()`
        fails (`queue_snapshot()` runs for real, untouched) -- the page
        must still be a 200 with the rows area rendering normally (no
        "run database migrations" copy, since the JOBS table itself is
        fine) while the settings sections fall back to `JobSettings`'s own
        documented defaults."""

        def _raise(cls):
            raise OperationalError("settings table unavailable")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_raise))

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "run database migrations" not in body
        assert "Nothing has been queued yet." in body
        assert "not set. Jobs run one at a time." in body


# --- Escaping ------------------------------------------------------------------


@pytest.mark.django_db
class TestEscaping:
    def test_job_summary_containing_script_tag_is_escaped(self, client):
        """XSS regression, mirroring `tools.rag.tests.
        test_views_upload_and_settings::test_question_and_answer_are_
        escaped`: a job summary containing a raw `<script>` tag must
        never render unescaped."""
        payload = "<script>alert(1)</script>"
        _make_job(state=QUEUED, marker=payload)

        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert payload not in body
        assert escape(payload) in body


# --- Nav -----------------------------------------------------------------------


@pytest.mark.django_db
class TestNav:
    def test_queue_link_marked_current_on_the_queue_page(self, client):
        response = client.get(reverse("jobs-queue"))
        body = response.content.decode()

        assert f'href="{reverse("jobs-queue")}" class="current"' in body

    def test_queue_link_renders_on_another_page_without_current(self, client):
        response = client.get(reverse("rag-history"))
        body = response.content.decode()

        assert f'href="{reverse("jobs-queue")}"' in body
        assert f'href="{reverse("jobs-queue")}" class="current"' not in body


# --- Chip style is shared, not page-local ---------------------------------------


@pytest.mark.django_db
class TestChipStyleIsSharedNotPageLocal:
    """The Queue page and the /vision/ cards render the same pill. Its rules
    live in foundation/templates/_shell.html so the two cannot drift; the Queue page
    keeps rendering it unchanged."""

    def test_the_queue_page_declares_the_chip_rules(self, client):
        body = client.get(reverse("jobs-queue")).content.decode()

        assert ".chip {" in body
        assert "--chip-bg:" in body

    def test_the_queue_page_declares_the_rules_exactly_once(self, client):
        r"""THE BASE RULE, counted at its own selector.

        `".chip {"` as a bare substring is also inside
        `".chip-check .chip {"` -- the shell's chip-shaped checkbox,
        which is a DIFFERENT rule about a different control, not a second
        declaration of this one.

        A REGEX, NOT A FIXED INDENT. Counting `"\n  .chip {"` would
        excuse a re-declaration at any other indentation -- and the most
        plausible way to reintroduce a page-local chip is "just the dark
        colours", inside a `@media` block at four spaces, which is
        precisely what this guard exists to catch. `^\s*` keeps the
        compound selector out at any depth all the same: after `.chip`
        comes `-check`, not a brace.
        """
        body = client.get(reverse("jobs-queue")).content.decode()

        assert len(re.findall(r"(?m)^\s*\.chip\s*\{", body)) == 1


# --- IA-2 T17: render-vs-gate -- settings forms are admin-only on the page ---


@pytest.mark.django_db
class TestTheQueueSettingsFormsAreAdminOnlyOnThePage:
    """F1 (Coherence Wave C) moved the two forms to `jobs-settings`, so
    the render-vs-gate question moved with them. It did not disappear:
    that page is class S, so a member is refused at the ROUTE before the
    template's own `{% if identity_is_admin %}` ever runs -- and the
    Queue page, which a member MAY open, must no longer carry the write
    endpoint for anybody at all."""

    def test_a_member_is_refused_the_settings_page_and_an_admin_gets_the_forms(self, client):
        member, admin = make_user(), make_admin()
        settings_url = reverse("jobs-settings-update").encode()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            assert client.get(reverse("jobs-settings")).status_code == 403
            other = client.__class__()
            sign_in(other, admin)
            assert settings_url in other.get(reverse("jobs-settings")).content

    def test_an_admin_gets_the_link_and_never_the_write_endpoint(self, client):
        """The forms left; a way to reach them did not. An operator
        reading the budget beside what is running must still be one
        click from changing it -- and that click must be a GET to the
        page, never this activity page carrying a write endpoint."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("jobs-queue")).content
        assert reverse("jobs-settings-update").encode() not in body
        assert body.count(reverse("jobs-settings").encode()) == 2

    def test_a_member_is_offered_neither(self, client):
        """I4 (Wave C review): the earlier version of this test signed in
        a MEMBER and asserted the link was present -- pinning a link to a
        class-S page that would answer that member 403. "The entry never
        leads anywhere that would refuse them" is the rule the settings
        area is built on, so the link is admin-only and this asserts the
        member half of it. The READING stays: the budget line is a fact
        about today's queue, not a control."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("jobs-queue"))
            body = response.content
        assert response.status_code == 200
        assert reverse("jobs-settings-update").encode() not in body
        assert reverse("jobs-settings").encode() not in body
        assert b"Memory budget" in body

    def test_an_open_box_gets_the_link_with_nobody_signed_in(self, client):
        """`identity_is_admin` is True for the open principal, so gating
        the link on it leaves a household box exactly as it was."""
        assert reverse("jobs-settings").encode() in \
            client.get(reverse("jobs-queue")).content

    def test_a_member_still_sees_the_cancel_control_on_their_own_row(self, client):
        """`jobs-queue-cancel` is class R and a member MAY cancel a job
        `visible_jobs` returned to them, so that control is honest for
        exactly the rows it appears on and is deliberately not gated."""
        member = make_user()
        job = make_queue_job(payload=payload_fields(user_principal(member)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("jobs-queue")).content
        assert reverse("jobs-queue-cancel", args=[job.id]).encode() in body

    def test_an_open_box_renders_the_settings_forms(self, client):
        """`identity_is_admin` is True for the open principal, so a
        household box gets the forms with nobody signed in at all --
        `jobs-settings` is class S, and class S resolves through
        `is_admin`, which an open box answers True for everybody."""
        assert reverse("jobs-settings-update").encode() in \
            client.get(reverse("jobs-settings")).content


# --- T13: a held-off row says why it is waiting -----------------------------


@pytest.mark.django_db
class TestHoldOffReading:
    """A job the queue is deliberately declining to consider for the next
    forty-five seconds is a stronger case of an unexplained delay than a
    reordering, and it would otherwise surface only as a log line -- which
    fails this track's own observability thesis."""

    def test_a_future_hold_off_renders_on_the_waiting_row(self, client):
        make_queue_job(kind="test.k", not_before=timezone.now() + timedelta(minutes=2))

        body = client.get(reverse("jobs-queue")).content.decode()

        assert "waiting for engine memory" in body
        assert "retries at" in body

    def test_a_past_hold_off_renders_nothing(self, client):
        make_queue_job(kind="test.k", not_before=timezone.now() - timedelta(minutes=2))

        body = client.get(reverse("jobs-queue")).content.decode()

        assert "waiting for engine memory" not in body

    def test_an_unheld_job_renders_nothing(self, client):
        make_queue_job(kind="test.k")

        assert "waiting for engine memory" not in client.get(
            reverse("jobs-queue")).content.decode()

    def test_the_reading_costs_no_extra_query(self, client, django_assert_num_queries):
        """It comes off the row `queue_snapshot` already loaded.

        A LITERAL, never a computed baseline: an earlier draft called a
        `_queue_page_query_count(client)` helper (which does not exist)
        and asserted against its own answer -- a test that passes whatever
        the page does, which the plan's own Global Constraints forbid.
        Fill the number from the first red run and write it in."""
        for index in range(3):
            make_queue_job(kind="test.k%d" % index,
                           not_before=timezone.now() + timedelta(minutes=2))

        with django_assert_num_queries(10):
            client.get(reverse("jobs-queue"))

    def test_the_reading_does_not_grow_with_the_number_of_held_off_rows(
            self, client, django_assert_num_queries):
        """The non-vacuous half: three held-off rows cost the same as
        one. Without this, the literal above would pass a reading that
        secretly queried per row."""
        make_queue_job(kind="test.only", not_before=timezone.now() + timedelta(minutes=2))

        with django_assert_num_queries(10):
            client.get(reverse("jobs-queue"))

    def test_the_page_never_500s_on_a_row_with_a_hold_off(self, client):
        make_queue_job(kind="test.unregistered", not_before=timezone.now() + timedelta(minutes=2))

        assert client.get(reverse("jobs-queue")).status_code == 200


# --- T14: a passed-over row says so -----------------------------------------


@pytest.mark.django_db
class TestPassedOverReading:
    """The other half of "why is this job still waiting": affinity
    batching can put a later peer ahead of an older job, and a count
    nobody can see is exactly the unexplained delay this track exists to
    remove. Read off the row `queue_snapshot` already loaded -- no query,
    no script."""

    def test_a_passed_over_job_says_so_on_the_waiting_row(self, client):
        make_queue_job(kind="test.k", passed_over=2)

        assert "passed over twice" in client.get(reverse("jobs-queue")).content.decode()

    def test_one_pass_over_reads_once(self, client):
        make_queue_job(kind="test.k", passed_over=1)

        assert "passed over once" in client.get(reverse("jobs-queue")).content.decode()

    def test_above_two_it_is_a_number(self, client):
        make_queue_job(kind="test.k", passed_over=5)

        assert "passed over 5 times" in client.get(reverse("jobs-queue")).content.decode()

    def test_a_job_never_passed_over_renders_nothing(self, client):
        make_queue_job(kind="test.k")

        assert "passed over" not in client.get(reverse("jobs-queue")).content.decode()
