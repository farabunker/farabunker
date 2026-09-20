"""Unit tests for the per-principal queued-job cap (C-7, round-3 hardening,
H39) -- `models.queue.backend._enforce_principal_quota`, called from
`enqueue()` before a job row is ever created -- and (fix round 1) the
"Job execution" settings page's own form field for `JobSettings.
max_queued_per_principal`, `models.queue.views._update_retention_and_
priority`.

A NEW MODULE, not appended to `test_backend.py`: round-3's own constraint
26 (which restates round-2 constraint 18) holds `tools/rag/tests/` for
another session; this module carries no such hold, but the plan's own
H39 addendum names this exact file (`models/queue/tests/test_quota.py`)
as the queue-side test placement, so it stays a module of its own rather
than growing `test_backend.py` in a fix round that would conflict with a
concurrent edit there. The fix-round-1 brief names this same file for
the settings-page view test too, and explicitly forbids opening
`models/queue/tests/test_views.py` (which already carries its own
`TestRetentionPrioritySettings` for the other two fields on this same
form) -- so the view test below is self-contained rather than reusing
that class's private `_post` helper.

Same `_register` job-kind idiom as `test_backend.py` (a fresh copy, not
an import of that module's private helper -- two test files each
registering their own throwaway kinds is the existing pattern, not a
DRY violation worth a shared import for).
"""
from __future__ import annotations

import logging

import pytest
from django.db import OperationalError
from django.urls import reverse

from identity.contracts.principals import SERVICE_PRINCIPAL, payload_fields
from identity.testing import make_user, user_principal
from models.contracts import jobkinds
from models.contracts.jobkinds import JobKind, register_job_kind
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable
from models.queue import backend
from models.queue.models import QUEUED, RUNNING, SUCCEEDED, InferenceJob, JobSettings
from models.queue.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported

MODULE = "models.queue.tests.test_quota"

reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


def plan_no_models(payload):
    return ([], False)


def handle_noop(payload, models, ctx):  # pragma: no cover - not exercised this task
    return {}


def summarize_noop(payload):  # pragma: no cover - not exercised this task
    return "test job"


def _register(key: str) -> None:
    register_job_kind(
        JobKind(
            key=key,
            label=key,
            planner=f"{MODULE}.plan_no_models",
            handler=f"{MODULE}.handle_noop",
            summarizer=f"{MODULE}.summarize_noop",
        )
    )


def set_queue_settings(**overrides) -> JobSettings:
    """`JobSettings.get_solo()`, with `overrides` applied and saved --
    every test in this module reaches for `max_queued_per_principal`
    alone, but this takes `**overrides` so a future test can set another
    field alongside it without a second helper."""
    settings_row = JobSettings.get_solo()
    for field, value in overrides.items():
        setattr(settings_row, field, value)
    settings_row.save()
    return settings_row


def payload_for(*, actor) -> dict:
    """A minimal `rag.ask`-shaped payload stamped for `actor`, exactly
    the way every real enqueuer stamps one (`identity.contracts.
    principals.payload_fields`) -- the cap reads the actor back off this,
    never off a parameter `enqueue()` doesn't take."""
    return {"question": "test", **payload_fields(actor)}


@pytest.mark.django_db
class TestC7APrincipalCannotFloodTheQueue:
    def test_a_principal_at_the_cap_is_refused(self):
        _register("test.quota")
        member = user_principal(make_user())
        set_queue_settings(max_queued_per_principal=3)
        for _ in range(3):
            backend.enqueue("test.quota", payload_for(actor=member))

        with pytest.raises(QueueQuotaExceeded) as excinfo:
            backend.enqueue("test.quota", payload_for(actor=member))
        assert "3 jobs are already queued or running" in str(excinfo.value)

    def test_the_quota_refusal_is_not_a_queue_unavailable(self):
        """They mean different things and four call sites in the vision
        column already branch on `QueueUnavailable`."""
        assert not issubclass(QueueQuotaExceeded, QueueUnavailable)

    def test_a_different_principal_is_unaffected(self):
        _register("test.quota")
        mine = user_principal(make_user())
        theirs = user_principal(make_user())
        set_queue_settings(max_queued_per_principal=1)

        backend.enqueue("test.quota", payload_for(actor=mine))

        # `mine` is now at the cap; `theirs` has never enqueued anything
        # and is unaffected -- the cap is keyed on the payload's own
        # actor, never queue-wide.
        job_id = backend.enqueue("test.quota", payload_for(actor=theirs))
        assert InferenceJob.objects.filter(pk=job_id).exists()

        with pytest.raises(QueueQuotaExceeded):
            backend.enqueue("test.quota", payload_for(actor=mine))

    def test_null_means_no_cap(self):
        """The shipped default. A box that never sets one behaves exactly
        as it does today."""
        _register("test.quota")
        member = user_principal(make_user())
        set_queue_settings(max_queued_per_principal=None)

        for _ in range(50):
            backend.enqueue("test.quota", payload_for(actor=member))

        assert InferenceJob.objects.filter(state=QUEUED).count() == 50

    def test_a_finished_job_frees_a_slot(self):
        """The cap counts QUEUED and RUNNING, not history."""
        _register("test.quota")
        member = user_principal(make_user())
        set_queue_settings(max_queued_per_principal=1)

        job_id = backend.enqueue("test.quota", payload_for(actor=member))
        with pytest.raises(QueueQuotaExceeded):
            backend.enqueue("test.quota", payload_for(actor=member))

        # RUNNING still counts -- the slot is not freed by a claim.
        InferenceJob.objects.filter(pk=job_id).update(state=RUNNING)
        with pytest.raises(QueueQuotaExceeded):
            backend.enqueue("test.quota", payload_for(actor=member))

        # A terminal state (any of the three) frees it.
        InferenceJob.objects.filter(pk=job_id).update(state=SUCCEEDED)
        second_id = backend.enqueue("test.quota", payload_for(actor=member))
        assert InferenceJob.objects.filter(pk=second_id, state=QUEUED).exists()

    def test_a_service_job_is_never_capped(self):
        """The watcher, the rematerialize callback and the shell commands
        are the operator's own work, not a principal's."""
        _register("test.quota")
        set_queue_settings(max_queued_per_principal=1)

        for _ in range(10):
            backend.enqueue("test.quota", payload_for(actor=SERVICE_PRINCIPAL))

        assert InferenceJob.objects.filter(state=QUEUED).count() == 10

    def test_a_payload_with_no_actor_keys_resolves_to_the_open_principal_and_counts(self):
        """UNLIKE `SERVICE_PRINCIPAL` above, a payload with no actor keys
        at all -- every job enqueued before this phase existed, or a
        hand-built payload -- is not exempt: `identity.contracts.
        principals.principal_from_payload`'s own tolerant fallback
        resolves it to `OPEN_PRINCIPAL`, one shared identity every such
        caller's jobs already collapse onto for visibility purposes, and
        it IS counted against the cap (fix round 1, C-7 review)."""
        _register("test.quota")
        set_queue_settings(max_queued_per_principal=2)

        backend.enqueue("test.quota", {"question": "no actor keys here"})
        backend.enqueue("test.quota", {"question": "still no actor keys"})

        with pytest.raises(QueueQuotaExceeded):
            backend.enqueue("test.quota", {"question": "third one, over the cap"})


@pytest.mark.django_db
class TestC7TheJobExecutionPageSetsTheCap:
    """`models.queue.views._update_retention_and_priority`'s fourth
    hand-parsed field (fix round 1, C-7 review): an operator sets/clears
    `JobSettings.max_queued_per_principal` from the "Retention, default
    priority & per-account cap" form, the same POST-redirect-GET, never-500 shape
    `retention_limit`/`default_priority` already use on that same form --
    see `models/queue/tests/test_views.py::TestRetentionPrioritySettings`
    for those two fields' own coverage (not opened by this fix round, per
    its own brief).

    THE FORM MOVED, THE ENDPOINT KEPT ITS JOB (F1/I1, Coherence Wave C,
    merged in from `hardening`): the three fields are edited on the "Job
    execution" settings page now rather than on the Queue page, and the
    one dispatched write endpoint they post to was renamed with its path
    (`jobs-queue-settings` -> `jobs-settings-update`). This class pins
    the same behaviour through the endpoint's current name."""

    def _post(self, client, **fields):
        data = {"form": "retention", "retention_limit": "50", "default_priority": "100"}
        data.update(fields)
        return client.post(reverse("jobs-settings-update"), data, follow=True)

    def test_a_non_blank_value_sets_the_cap(self, client):
        self._post(client, max_queued_per_principal="5")

        assert JobSettings.get_solo().max_queued_per_principal == 5

    def test_a_blank_value_clears_the_cap_to_null(self, client):
        JobSettings.objects.create(pk=1, max_queued_per_principal=5)

        response = self._post(client, max_queued_per_principal="")

        assert JobSettings.get_solo().max_queued_per_principal is None
        assert "per-principal queue cap set to no cap" in response.content.decode()

    @pytest.mark.parametrize(
        "bad_value,expected",
        [
            ("abc", "Per-principal queue cap must be a whole number."),
            ("0", "Per-principal queue cap must be a positive number."),
            ("-1", "Per-principal queue cap must be a positive number."),
            # THE CEILING (fix round 2, C-7 re-review): the column is a
            # `PositiveIntegerField`, so a value past its stored ceiling
            # used to reach `save()` and 500 -- the guard its two
            # siblings already carry now refuses it in the form's own
            # voice instead.
            ("1000000000000", "Per-principal queue cap is too large."),
        ],
    )
    def test_a_bad_value_refuses_the_whole_form_and_saves_nothing(
        self, client, bad_value, expected
    ):
        JobSettings.objects.create(
            pk=1, retention_limit=20, default_priority=77, max_queued_per_principal=3
        )

        response = self._post(
            client, retention_limit="99", default_priority="88",
            max_queued_per_principal=bad_value,
        )

        assert expected in response.content.decode()
        settings_row = JobSettings.get_solo()
        # NEVER-500 GRAMMAR (this view's own docstring): a bad value in
        # ONE field on this form saves NONE of the three, not just the
        # bad one -- `retention_limit`/`default_priority` above are left
        # exactly as they were too, matching `test_views.py`'s own
        # `test_invalid_retention_limit`/`test_invalid_default_priority`.
        assert settings_row.retention_limit == 20
        assert settings_row.default_priority == 77
        assert settings_row.max_queued_per_principal == 3


@pytest.mark.django_db
class TestH39TheSettingsRowIsReadOncePerEnqueue:
    """H39 follow-up: `enqueue` asked `JobSettings.get_solo()` up to three
    separate times -- once for the quota, once (on the last rung of the
    priority chain) for `default_priority`, once for the prune's
    `retention_limit` -- and `get_solo` is a `get_or_create`, so each one
    was a real query. One read now, threaded down.

    The forgiveness each step already had is unchanged, and the two
    behaviour tests below are what say so rather than a comment."""

    def _counting_get_solo(self, monkeypatch):
        calls = []
        original = JobSettings.get_solo.__func__

        def counted(cls):
            calls.append(cls)
            return original(cls)

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(counted))
        return calls

    def test_one_read_for_an_ordinary_enqueue(self, monkeypatch):
        _register("test.oneread")
        member = user_principal(make_user())
        set_queue_settings(max_queued_per_principal=5)

        calls = self._counting_get_solo(monkeypatch)
        backend.enqueue("test.oneread", payload_for(actor=member))
        assert len(calls) == 1

    def test_one_read_even_when_the_quota_step_exits_early(self, monkeypatch):
        """`SERVICE_PRINCIPAL` returns from the quota check before it
        looks at the row at all -- the priority and prune steps still
        need it, and still must not each go and read it again."""
        _register("test.oneread.service")
        set_queue_settings(max_queued_per_principal=5)

        calls = self._counting_get_solo(monkeypatch)
        backend.enqueue("test.oneread.service", payload_for(actor=SERVICE_PRINCIPAL))
        assert len(calls) == 1

    def test_an_unreadable_settings_row_still_queues_the_job(self, monkeypatch, caplog):
        """The quota's and the prune's forgiveness, together: neither may
        turn a job the `InferenceJob` table can perfectly well accept
        into a `QueueUnavailable`. The job kind carries its own
        `default_priority`, so the priority chain never reaches the rung
        that genuinely needs the row."""
        register_job_kind(
            JobKind(
                key="test.noSettings",
                label="test.noSettings",
                planner=f"{MODULE}.plan_no_models",
                handler=f"{MODULE}.handle_noop",
                summarizer=f"{MODULE}.summarize_noop",
                default_priority=7,
            )
        )
        member = user_principal(make_user())

        def _unreadable(cls):
            raise OperationalError("jobs_jobsettings is unreachable")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_unreadable))
        with caplog.at_level(logging.WARNING):
            job_id = backend.enqueue("test.noSettings", payload_for(actor=member))

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == QUEUED
        assert job.priority == 7
        assert any("skipped the per-principal queue quota check" in r.message
                   for r in caplog.records)
        assert any("skipped pruning finished jobs" in r.message for r in caplog.records)

    def test_an_unreadable_settings_row_still_fails_loudly_for_the_priority_rung(
        self, monkeypatch
    ):
        """The one step that does NOT forgive: a job kind with no default
        of its own needs the queue-wide one, and a priority is a column
        on the row about to be written, not housekeeping around it."""
        _register("test.needsSettings")
        member = user_principal(make_user())

        def _unreadable(cls):
            raise OperationalError("jobs_jobsettings is unreachable")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_unreadable))
        with pytest.raises(QueueUnavailable):
            backend.enqueue("test.needsSettings", payload_for(actor=member))

    def test_the_hoist_failure_itself_is_logged_with_a_traceback(self, monkeypatch, caplog):
        """round-3 final wave: the hoisted `JobSettings.get_solo()`
        read used to fail silently -- neither downstream warning (the
        quota check's, the prune's) still runs inside the `except` block
        that caught the real exception, so `exc_info=True` on either of
        them would carry nothing. The hoist's own `except` is the one
        place left that still has the traceback -- logged there now."""
        register_job_kind(
            JobKind(
                key="test.hoistLogged",
                label="test.hoistLogged",
                planner=f"{MODULE}.plan_no_models",
                handler=f"{MODULE}.handle_noop",
                summarizer=f"{MODULE}.summarize_noop",
                default_priority=7,
            )
        )
        member = user_principal(make_user())

        def _unreadable(cls):
            raise OperationalError("jobs_jobsettings is unreachable")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_unreadable))
        with caplog.at_level(logging.WARNING):
            backend.enqueue("test.hoistLogged", payload_for(actor=member))

        hoist_records = [r for r in caplog.records if "JobSettings.get_solo() failed" in r.message]
        assert len(hoist_records) == 1
        assert hoist_records[0].exc_info is not None
