"""Unit tests for models/queue/models.py (InferenceJob, JobSettings).

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
"""
from __future__ import annotations

import pytest

from models.queue.models import (
    CANCELLED,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TERMINAL_STATES,
    InferenceJob,
    JobSettings,
)
from models.queue.tests._helpers import make_queue_job


@pytest.mark.django_db
class TestInferenceJobDefaults:
    def test_create_with_minimal_fields_gets_expected_defaults(self):
        job = InferenceJob.objects.create(kind="test.job", priority=100)

        assert job.state == QUEUED
        assert job.payload == {}
        assert job.claimed_by == ""
        assert job.claim_token is None
        assert job.started_at is None
        assert job.heartbeat_at is None
        assert job.finished_at is None
        assert job.attempts == 0
        assert job.model_refs == []
        assert job.exclusive is False
        assert job.result is None
        assert job.error == ""

    def test_terminal_states_constant(self):
        assert TERMINAL_STATES == (SUCCEEDED, FAILED, CANCELLED)
        assert QUEUED not in TERMINAL_STATES
        assert RUNNING not in TERMINAL_STATES


@pytest.mark.django_db
class TestFootprintBytes:
    def test_sums_entries(self):
        job = InferenceJob.objects.create(
            kind="test.job",
            priority=100,
            model_refs=[
                {"role": "a", "footprint_bytes": 100},
                {"role": "b", "footprint_bytes": 250},
            ],
        )

        assert job.footprint_bytes == 350

    def test_none_when_any_entry_unsized(self):
        job = InferenceJob.objects.create(
            kind="test.job",
            priority=100,
            model_refs=[
                {"role": "a", "footprint_bytes": 100},
                {"role": "b", "footprint_bytes": None},
            ],
        )

        assert job.footprint_bytes is None

    def test_none_when_empty(self):
        job = InferenceJob.objects.create(kind="test.job", priority=100, model_refs=[])

        assert job.footprint_bytes is None


@pytest.mark.django_db
class TestJobSettingsSingleton:
    def test_get_solo_creates_row_with_defaults_on_first_call(self):
        settings = JobSettings.get_solo()

        assert settings.pk == 1
        assert settings.memory_budget_bytes is None
        assert settings.max_concurrent_jobs == JobSettings.MAX_CONCURRENT_JOBS_DEFAULT
        assert settings.default_priority == JobSettings.DEFAULT_PRIORITY_DEFAULT
        assert settings.retention_limit == JobSettings.RETENTION_LIMIT_DEFAULT
        # One-timeout task (2026-09-17): the sixth default, raised from
        # the ollama engine adapter's own previously-invisible 300s.
        assert settings.response_timeout_seconds == JobSettings.RESPONSE_TIMEOUT_SECONDS_DEFAULT
        assert JobSettings.RESPONSE_TIMEOUT_SECONDS_DEFAULT == 1800
        assert JobSettings.RESPONSE_TIMEOUT_SECONDS_MIN == 60
        assert JobSettings.RESPONSE_TIMEOUT_SECONDS_MAX == 7200

    def test_second_call_returns_the_same_row(self):
        first = JobSettings.get_solo()
        first.max_concurrent_jobs = 9
        first.save()

        second = JobSettings.get_solo()

        assert second.pk == first.pk
        assert second.max_concurrent_jobs == 9
        assert JobSettings.objects.count() == 1


@pytest.mark.django_db
class TestTheGovernanceColumns:
    def test_a_fresh_job_is_immediately_claimable_and_never_passed_over(self):
        job = make_queue_job(kind="test.k")

        assert job.not_before is None
        assert job.passed_over == 0

    def test_the_settings_row_ships_an_empty_wait_map_and_no_detected_memory(self):
        row = JobSettings.get_solo()

        assert row.kind_wait_seconds == {}
        assert row.detected_memory_bytes is None
        assert row.detected_memory_at is None

    def test_the_wait_map_round_trips(self):
        row = JobSettings.get_solo()
        row.kind_wait_seconds = {"agent.turn": 1800}
        row.save(update_fields=["kind_wait_seconds"])
        row.refresh_from_db()

        assert row.kind_wait_seconds == {"agent.turn": 1800}

    def test_the_queue_row_carries_both_job_columns(self):
        from models.queue.backend import queue_snapshot

        make_queue_job(kind="test.k")
        row = queue_snapshot().waiting[0]

        assert row.not_before is None
        assert row.passed_over == 0
