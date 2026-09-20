"""Unit tests for tools/vision/services.py (spec §4.7, §6).

Drives a stub engine registered in the real `ENGINES` registry, so the whole
resolve -> get_engine -> build_image_generator path runs. No HTTP.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from models.registry.models import ModelConnection, RoleBinding
from models.contracts import operations
from models.contracts.bindings import ResolvedModel
from models.contracts.engines import ENGINES
from models.contracts.engines.base import GenerationRejected, JobStatus
from models.contracts.operations import (
    EDIT, TXT2IMG, Operation, Param, ParamError, describe, operations_for,
)
from models.contracts.roles import IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_PERSONAL
from identity.contracts.principals import OPEN_PRINCIPAL, Principal
from tools.vision import probe_cache, services, store
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.tests._helpers import (
    PNG,
    RAW,
    FakeComfyUI,
    StubEngine,
    StubGenerator,
    clear_bindings,
    make_user,
    posture,
    reset_engine_caches,
    stored_output,
    user_principal,
)


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """C-07 half B. `_health_check` caches reachability for 30 seconds,
    process-locally, keyed on (engine, endpoint). Every test in this
    module uses the SAME literal endpoint with a DIFFERENT healthy state,
    so without this the second test in a run reads the first one's answer.
    Mirrors `models/registry/tests/test_availability.py:57-65`'s own
    `invalidate()`-around-the-yield fixture."""
    probe_cache.invalidate()
    yield
    probe_cache.invalidate()


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()


def _bind(engine_name="stubengine"):
    connection = ModelConnection.objects.create(
        name="stub image model", engine=engine_name, endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    return connection


def _registered(engine):
    return patch.dict(ENGINES, {engine.name: engine})


def _resolved(config: dict | None = None) -> ResolvedModel:
    """A `flux2`-family ComfyUI connection, resolved -- reaching the REAL
    `comfyui` adapter (registered globally, no `_registered` patch needed)
    so `live_defaults` tests exercise `ComfyUIEngine.param_defaults` itself,
    not a stand-in for it. `param_defaults` is pure (no HTTP), unlike
    `is_healthy`/`list_choices`/`list_assets`, so no `httpx` mock is needed
    either -- exactly why this idiom is safe to reuse without a fake
    server."""
    return ResolvedModel(
        engine="comfyui", model_id="weights-Q8_0.gguf",
        endpoint="http://comfy.local:8188", config=config or {},
    )


@pytest.mark.django_db
class TestPreflight:
    def test_unbound_role(self):
        result = services.preflight()
        assert result.state == "unbound"
        assert result.resolved is None
        assert "/inference/" in result.message

    def test_unreachable_engine_names_the_endpoint(self):
        _bind()
        with _registered(StubEngine(healthy=False)):
            result = services.preflight()

        assert result.state == "unreachable"
        assert "http://stub:9999" in result.message

    def test_health_check_blowing_up_reads_as_unreachable(self):
        _bind()
        with _registered(StubEngine(healthy=RuntimeError("boom"))):
            assert services.preflight().state == "unreachable"

    def test_ready(self):
        _bind()
        with _registered(StubEngine()):
            result = services.preflight()

        assert result.state == "ready"
        assert result.resolved.model_id == "stub.safetensors"
        assert result.message == ""

    def test_patching_bindings_resolve_is_observed(self):
        """`preflight` must call `bindings.resolve` through the module
        object, not a name copied into `services` at import time -- a test
        patching `models.contracts.bindings.resolve` (the same idiom every
        caller of the role-resolution seam uses) has to reach this call no
        matter what order the test suite imported `services` in. No role
        binding is created here at all: if `preflight` were still calling a
        stale local `resolve`, this would fall through to the real lookup
        and come back `unbound` instead."""
        stub = ResolvedModel(
            engine="stubengine", model_id="stub.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with _registered(StubEngine()), patch(
            "models.contracts.bindings.resolve", return_value=stub
        ) as mock_resolve:
            result = services.preflight()

        mock_resolve.assert_called_once_with(VISION_GENERATE_ROLE)
        assert result.state == "ready"
        assert result.resolved == stub


@pytest.mark.django_db
class TestSubmitJob:
    def test_creates_a_queued_job_carrying_the_binding_snapshot(self):
        _bind()
        generator = StubGenerator(engine_ref="p-7")
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert job.status == GenerationJob.Status.QUEUED
        assert job.engine_ref == "p-7"
        assert job.engine_payload["client_id"] == str(job.id)
        assert job.engine == "stubengine"
        assert job.model_id == "stub.safetensors"
        assert job.model_fingerprint == "stubengine:stub.safetensors:None"
        assert job.seed == 42
        assert job.params["width"] == 1024

    def test_the_request_carries_the_job_uuid_as_client_ref(self):
        _bind()
        generator = StubGenerator()
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert generator.submitted[0].client_ref == str(job.id)
        assert generator.submitted[0].operation == "txt2img"

    def test_submit_snapshots_an_endpoint_as_long_as_a_model_connections_own_max(self):
        """Fix-wave item 4: `ModelConnection.endpoint` allows up to 512
        chars; `GenerationJob.endpoint` snapshots it at submit time and must
        accept the same width instead of a DataError on anything longer
        than the old 255-char column."""
        long_endpoint = "http://" + "x" * 490 + ".example:8188"  # > 255, <= 512
        connection = ModelConnection.objects.create(
            name="stub image model", engine="stubengine", endpoint=long_endpoint,
            model_id="stub.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
        with _registered(StubEngine(StubGenerator())):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert job.endpoint == long_endpoint

    def test_invalid_params_never_create_a_job(self):
        _bind()
        with _registered(StubEngine()):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", dict(RAW, steps="9000"), actor=OPEN_PRINCIPAL)

        assert GenerationJob.objects.count() == 0

    def test_unbound_role_raises_vision_unavailable(self):
        with pytest.raises(services.VisionUnavailable) as excinfo:
            services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert excinfo.value.state == "unbound"
        assert GenerationJob.objects.count() == 0

    def test_unreachable_engine_raises_vision_unavailable(self):
        _bind()
        with _registered(StubEngine(healthy=False)):
            with pytest.raises(services.VisionUnavailable) as excinfo:
                services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert excinfo.value.state == "unreachable"

    def test_engine_rejection_leaves_a_failed_job_with_the_engine_words(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("ckpt_name: 'x' not in [...]"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert job.status == GenerationJob.Status.FAILED
        assert "not in" in job.error
        assert job.finished_at is not None

    def test_unknown_operation_is_a_value_error(self):
        _bind()
        with _registered(StubEngine()):
            with pytest.raises(ValueError, match="Unknown operation"):
                services.submit_job("controlnet", dict(RAW), actor=OPEN_PRINCIPAL)

    def test_bad_params_report_the_param_error_even_when_nothing_is_bound(self):
        """B10: a malformed submission is malformed whether or not an
        engine is answering. Reporting 503 for it tells the operator to go
        fix their engine over a typo -- and makes every rejected
        submission pay a health round trip first."""
        with pytest.raises(ParamError):
            services.submit_job("txt2img", {**RAW, "steps": "not a number"}, actor=OPEN_PRINCIPAL)

    def test_a_valid_submission_still_reports_an_unbound_role(self):
        with pytest.raises(services.VisionUnavailable) as exc:
            services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert exc.value.state == "unbound"

    def test_bad_params_never_reach_the_engine(self):
        _bind()
        engine = StubEngine()
        with _registered(engine):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", {**RAW, "width": "wide"}, actor=OPEN_PRINCIPAL)
        assert engine.built == []
        assert GenerationJob.objects.count() == 0


def _patch_ready_engine(monkeypatch):
    """Bind the vision role to a stub connection and register a stub
    engine directly into `ENGINES`, so `submit_job` reaches a `QUEUED`
    row without any real engine -- reused by `TestOwnership`, which
    asserts only on the STAMPED owner columns, nothing engine-shaped."""
    _bind()
    engine = StubEngine(StubGenerator())
    monkeypatch.setitem(ENGINES, engine.name, engine)


@pytest.mark.django_db
class TestOwnership:
    def test_a_generation_is_stamped_with_the_actor(self, monkeypatch):
        """Ownership is STAMPED, never inferred. A row written with
        blank owner columns is a row no filter can reason about, and
        backfilling one is a migration nobody has the information to
        write."""
        _patch_ready_engine(monkeypatch)
        job = services.submit_job(
            "txt2img", dict(RAW),
            actor=Principal("user", "7"),
        )
        assert (job.owner_kind, job.owner_key) == ("user", "7")

    def test_the_actor_is_required_not_defaulted(self):
        """A default would be a fail-open default: the caller that
        forgot it would silently write a row attributed to nobody, and
        every visibility rule downstream would then be reasoning about
        a blank."""
        with pytest.raises(TypeError):
            services.submit_job("txt2img", {"prompt": "a test"})

    def test_an_open_box_stamps_the_open_principal_not_a_blank(self, monkeypatch):
        """`("open", "box")` is what `adopt_open_rows` claims. `("", "")`
        is only ever the shape of a row written BEFORE this migration."""
        _patch_ready_engine(monkeypatch)
        job = services.submit_job(
            "txt2img", dict(RAW),
            actor=OPEN_PRINCIPAL,
        )
        assert (job.owner_kind, job.owner_key) == ("open", "box")


@pytest.mark.django_db
class TestRefreshJob:
    def _submit(self, generator):
        _bind()
        with _registered(StubEngine(generator)):
            return services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

    def test_queued_stays_queued(self):
        generator = StubGenerator(states=[JobStatus(state="queued")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.started_at is None

    def test_running_records_started_at_once(self):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            first = services.refresh_job(job)
            started = first.started_at
            second = services.refresh_job(first)

        assert first.status == GenerationJob.Status.RUNNING
        assert started is not None
        assert second.started_at == started

    def test_done_stores_every_output_with_its_dimensions(self, tmp_path):
        generator = StubGenerator(
            states=[JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png"), ("b.png", PNG, "image/png")],
        )
        job = self._submit(generator)
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.DONE
        assert refreshed.finished_at is not None
        outputs = list(refreshed.outputs.all())
        assert [output.index for output in outputs] == [0, 1]
        assert outputs[0].width == 512 and outputs[0].height == 512
        assert outputs[0].media_type == "image/png"

    def test_refresh_is_idempotent_on_a_terminal_job(self, tmp_path):
        """Idempotent when called the way every real caller does --
        `job_status`, `queue_job_status`, and `wait_for`'s own loop all
        reassign the return value (`job = services.refresh_job(job)`)
        rather than reusing a stale reference. Item D's fix (below) makes
        `refresh_job` itself the one place that verifies "already
        finalized" against the ROW, not a caller's own copy -- a second
        call on a STALE, unreassigned copy is covered separately by
        `test_two_concurrent_refreshes_on_stale_copies_never_burn_a_
        committed_output_id`, which is precisely the shape of caller
        this idempotency claim never covered."""
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.refresh_job(job)
            calls = generator.status_calls
            services.refresh_job(job)

        assert generator.status_calls == calls
        assert GeneratedOutput.objects.count() == 1

    def test_two_concurrent_refreshes_on_stale_copies_never_burn_a_committed_output_id(self, tmp_path):
        """Item D (2026-09-03 fix batch) -- diagnosis: `job_status`/
        `queue_job_status` (the web poll) and `tools.vision.jobs.
        run_generate`'s own `wait_for` loop (the queued worker) each call
        `refresh_job` directly on a `GenerationJob` fetched fresh for
        THAT call -- two INDEPENDENT Python objects, each still showing
        the pre-finish status, can be in flight on the same row at once.
        `refresh_job`'s finalize step used to trust the IN-MEMORY `job`
        object's own `is_terminal` (set once, at the top of the
        function) rather than re-reading the row -- so a SECOND call
        holding a stale, still-non-terminal copy would re-enter the
        `job.outputs.all().delete()` + recreate block AFTER the first
        call had already committed a row, deleting that just-committed
        row and creating a new one under a NEW id. Anything that had
        already rendered the FIRST id (a card, a `?format=json` poll
        response) then points at a row that no longer exists -- exactly
        the owner's "wrong number initially, refresh fixes it" symptom,
        and exactly why the id is always one BELOW the surviving row's:
        the first row's auto-incrementing pk is burned, never reused.

        Reproduced here with two SEPARATE, independently-fetched
        `GenerationJob` copies of the same row, refreshed one after the
        other -- no real thread/process concurrency is needed to prove
        the defect: the bug is that `refresh_job` trusts a stale
        in-memory flag instead of the row's true, current, committed
        state, which staleness alone (this test's shape) already
        exposes deterministically.
        """
        generator = StubGenerator(
            states=[JobStatus(state="done"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        job = self._submit(generator)
        assert not job.is_terminal

        stale_a = GenerationJob.objects.get(pk=job.pk)
        stale_b = GenerationJob.objects.get(pk=job.pk)

        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            refreshed_a = services.refresh_job(stale_a)
            first_output = refreshed_a.outputs.get()

            # `stale_b` is a DIFFERENT Python object, fetched before
            # `stale_a`'s finalize committed -- its OWN in-memory
            # `.status` is still whatever it was at fetch time, not
            # DONE, exactly the staleness the race depends on.
            assert stale_b.status != GenerationJob.Status.DONE
            refreshed_b = services.refresh_job(stale_b)
            second_output = refreshed_b.outputs.get()

        assert second_output.id == first_output.id
        assert GeneratedOutput.objects.filter(pk=first_output.id).exists()
        assert GeneratedOutput.objects.count() == 1

    def test_a_stale_copys_lost_answer_never_overwrites_a_committed_done_row(self, tmp_path):
        """Review finding 1: the `done` path above is guarded, but a
        STALE copy that gets a `lost` (or `failed`) answer back from the
        engine used to call `_fail`, which saved `status=failed` onto
        the row UNCONDITIONALLY -- even when a DIFFERENT, concurrent
        call had already finalized that same row as `done`. That would
        silently corrupt a genuinely finished generation's own status,
        which is worse than the id-burning defect item D's own fix
        closes. Same staleness shape: two independently-fetched copies
        of the same row, refreshed one after the other -- `stale_b`'s
        own `lost` answer must be discarded once the row it names is
        already durably `done`.
        """
        generator = StubGenerator(
            states=[JobStatus(state="done"), JobStatus(state="lost")],
            outputs=[("a.png", PNG, "image/png")],
        )
        job = self._submit(generator)
        stale_a = GenerationJob.objects.get(pk=job.pk)
        stale_b = GenerationJob.objects.get(pk=job.pk)

        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            refreshed_a = services.refresh_job(stale_a)
            assert refreshed_a.status == GenerationJob.Status.DONE

            assert stale_b.status != GenerationJob.Status.DONE
            refreshed_b = services.refresh_job(stale_b)

        assert refreshed_b.status == GenerationJob.Status.DONE
        assert refreshed_b.error == ""
        job.refresh_from_db()
        assert job.status == GenerationJob.Status.DONE
        assert job.error == ""
        assert GeneratedOutput.objects.count() == 1

    def test_a_second_refresh_on_a_stale_unreassigned_copy_costs_one_poll_and_no_new_outputs(self, tmp_path):
        """Review finding 3, pinning the accepted cost of the correct
        fix: a caller that does NOT reassign `refresh_job`'s return
        value (every real caller does -- `job = services.refresh_job
        (job)`) still pays one more `generator.status()` poll on a
        second call, because its own in-memory copy's `is_terminal`
        early-return check never sees the finish. That extra poll is
        an accepted cost, not a defect; what must NOT happen is a
        second `GeneratedOutput` row or any change to the first one.
        """
        generator = StubGenerator(
            states=[JobStatus(state="done"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        job = self._submit(generator)
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            services.refresh_job(job)  # `job` itself is never reassigned
            polls_after_first = generator.status_calls
            first_output_id = GeneratedOutput.objects.get().id

            refreshed_again = services.refresh_job(job)

        assert generator.status_calls == polls_after_first + 1
        assert refreshed_again.status == GenerationJob.Status.DONE
        assert GeneratedOutput.objects.count() == 1
        assert GeneratedOutput.objects.get().id == first_output_id

    def test_failed_records_the_engine_error(self):
        generator = StubGenerator(states=[JobStatus(state="failed", error="Allocation on device")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.FAILED
        assert refreshed.error == "Allocation on device"

    def test_lost_explains_what_to_do(self):
        generator = StubGenerator(states=[JobStatus(state="lost")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.FAILED
        assert refreshed.error == services.LOST_MESSAGE
        assert "resubmit" in services.LOST_MESSAGE

    def test_unreachable_during_poll_leaves_the_job_untouched(self):
        generator = StubGenerator(states=[ConnectionError("down")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        refreshed.refresh_from_db()
        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.error == ""
        assert getattr(refreshed, "unreachable", False) is True

    def test_a_job_that_never_reached_the_engine_is_not_polled(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("nope"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            services.refresh_job(job)

        assert generator.status_calls == 0

    def test_refresh_stamps_the_start_exactly_once(self):
        """Two `running` polls must not move the start -- the second would
        erase however long the job had already been running. Through the
        REAL `comfyui` adapter (`FakeComfyUI`, HTTP-level), not the stub
        engine `test_running_records_started_at_once` above uses."""
        job = _submitted_job()
        fake = FakeComfyUI(queue_running=[[None, job.engine_ref]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            first = services.refresh_job(job).started_at
            second = services.refresh_job(GenerationJob.objects.get(pk=job.pk)).started_at
        assert first is not None and second == first

    def test_a_shaped_engine_ref_is_unreachable_not_rejected(self):
        """S25 (H11 review round 1, finding 7/minor 8): an `engine_ref`
        shaped like a path-traversal attempt -- echoed back by a hostile
        or misbehaving engine at submission time and stored verbatim on
        the job, exactly `models.contracts.engines.comfyui._history`'s own
        `safe_engine_ref` guard exists to catch -- makes that guard raise
        a `ValueError` on the very first poll. `refresh_job` (unlike
        `submit_job`) has no fine-grained exception classification around
        `generator.status(...)`: ONE broad `except Exception` catches
        every poll failure identically, so this must read exactly like
        any other unreachable engine (`test_unreachable_during_poll_
        leaves_the_job_untouched` above) -- QUEUED, `unreachable=True` --
        never a `FAILED`/`ENGINE_REJECTED` verdict. (That verdict only
        exists on `submit_job`'s OWN classification chain, which
        `_history`'s `ValueError` cannot reach: `submit_job` never calls
        `_history`.) No new fake needed -- `FakeComfyUI`'s existing
        `prompt_id` param is enough to make `job.engine_ref` malformed."""
        job = _submitted_job(prompt_id="../queue")
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            refreshed = services.refresh_job(job)

        refreshed.refresh_from_db()
        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.error == ""
        assert getattr(refreshed, "unreachable", False) is True


@pytest.mark.django_db
class TestRefreshJobCarriesTheEnginePosition:
    """A TRANSIENT attribute, like `unreachable` -- the card reads it, the
    database never stores it, and no migration is involved."""

    def _submit(self, generator):
        _bind()
        with _registered(StubEngine(generator)):
            return services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

    def test_a_queued_job_carries_the_position_the_engine_reported(self):
        generator = StubGenerator(states=[JobStatus(state="queued", queue_position=3)])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.engine_position == 3

    def test_an_engine_that_reports_no_position_leaves_it_none(self):
        generator = StubGenerator(states=[JobStatus(state="queued")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.engine_position is None

    def test_a_running_job_reports_no_position(self):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.RUNNING
        assert refreshed.engine_position is None


@pytest.mark.django_db
class TestWaitFor:
    def test_returns_as_soon_as_the_job_is_terminal(self, tmp_path):
        _bind()
        generator = StubGenerator(
            states=[JobStatus(state="running"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            finished = services.wait_for(job, timeout=5, interval=0)

        assert finished.status == GenerationJob.Status.DONE

    def test_gives_up_at_the_timeout_without_touching_the_job(self):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="queued")])
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            waited = services.wait_for(job, timeout=0, interval=0)

        assert waited.status == GenerationJob.Status.QUEUED


@pytest.mark.django_db
class TestWaitForReportsEachPoll:
    """The loop is the only place that knows a poll happened, so the
    reporting seam belongs to it. `TestWaitFor` above pins the unchanged
    no-callback behaviour; these pin the new argument."""

    def test_on_poll_runs_for_every_refresh_with_the_job_and_elapsed_seconds(self, tmp_path):
        _bind()
        generator = StubGenerator(
            states=[JobStatus(state="running"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        seen = []

        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            services.wait_for(
                job, timeout=5, interval=0,
                on_poll=lambda polled, elapsed: seen.append((polled.status, elapsed)),
            )

        assert [status for status, _elapsed in seen] == [
            GenerationJob.Status.RUNNING,
            GenerationJob.Status.DONE,
        ]
        elapsed_values = [elapsed for _status, elapsed in seen]
        assert all(b >= a for a, b in zip(elapsed_values, elapsed_values[1:]))

    def test_a_caller_that_passes_no_callback_is_unaffected(self):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="queued")])

        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            waited = services.wait_for(job, timeout=0, interval=0)

        assert waited.status == GenerationJob.Status.QUEUED


@pytest.mark.django_db
class TestDeleteJob:
    def test_removes_rows_and_files(self, tmp_path):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
            services.refresh_job(job)
            job_directory = tmp_path / str(job.id)
            assert job_directory.exists()

            services.delete_job(job)

        assert not job_directory.exists()
        assert GenerationJob.objects.count() == 0
        assert GeneratedOutput.objects.count() == 0


@pytest.mark.django_db
class TestDeleteJobSweepsEngineFiles:
    """Engine files feature (2026-09-02): `delete_job` best-effort-sweeps
    the ENGINE's own output/input folders for anything prefixed with the
    deleted job's uuid, in addition to its own managed directory."""

    def _job(self, **overrides):
        fields = dict(
            operation="txt2img", params={"prompt": "a lighthouse"},
            engine="stubengine", model_id="stub.safetensors",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
        )
        fields.update(overrides)
        return GenerationJob.objects.create(**fields)

    def _engine_dirs(self, tmp_path, monkeypatch):
        output = tmp_path / "engine-output"
        input_dir = tmp_path / "engine-input"
        output.mkdir()
        input_dir.mkdir()
        monkeypatch.setattr(store, "ENGINE_OUTPUT_DIR", output)
        monkeypatch.setattr(store, "ENGINE_INPUT_DIR", input_dir)
        return output, input_dir

    def test_removes_matching_files_in_both_engine_directories(self, tmp_path, monkeypatch):
        output_dir, input_dir = self._engine_dirs(tmp_path, monkeypatch)
        job = self._job()
        (output_dir / f"{job.id}_00001_.png").write_bytes(PNG)
        (input_dir / f"{job.id}_init.png").write_bytes(PNG)
        (output_dir / "unrelated.png").write_bytes(PNG)

        services.delete_job(job)

        assert not (output_dir / f"{job.id}_00001_.png").exists()
        assert not (input_dir / f"{job.id}_init.png").exists()
        assert (output_dir / "unrelated.png").exists()

    def test_a_missing_engine_mount_does_not_raise(self, monkeypatch):
        """The module defaults (`/engine/output`, `/engine/input`) name
        paths absent on a test box -- exactly a preview stack with no
        bind mount configured -- and the sweep must still return
        quietly."""
        job = self._job()
        services.delete_job(job)  # must not raise

    def test_an_unlink_failure_does_not_raise(self, tmp_path, monkeypatch):
        output_dir, _input_dir = self._engine_dirs(tmp_path, monkeypatch)
        job = self._job()
        target = output_dir / f"{job.id}_00001_.png"
        target.write_bytes(PNG)

        with patch("pathlib.Path.unlink", side_effect=OSError("boom")):
            services.delete_job(job)  # must not raise

        assert target.exists()  # the failed unlink really did nothing


@pytest.mark.django_db
class TestJobRecordedBinding:
    """A job records the binding it ran on and is polled through THAT --
    never through a second, independent resolution of the role."""

    def test_submit_records_the_endpoint_it_actually_used(self):
        _bind()
        with _registered(StubEngine()):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert job.endpoint == "http://stub:9999"
        assert job.engine == "stubengine"
        assert job.model_id == "stub.safetensors"

    def test_submit_never_re_resolves_the_role_through_the_gateway(self):
        _bind()
        with _registered(StubEngine()), patch("models.contracts.gateway.resolve") as gateway_resolve:
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert gateway_resolve.call_count == 0
        assert job.status == GenerationJob.Status.QUEUED

    def test_submit_builds_from_the_preflighted_binding(self):
        _bind()
        engine = StubEngine()
        with _registered(engine):
            services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        assert engine.built == [("stub.safetensors", "http://stub:9999", {})]

    def test_refresh_polls_the_binding_on_the_job_not_the_one_bound_now(self):
        _bind()
        submit_engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(submit_engine):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)

        # The operator rebinds the role to a different checkpoint at a
        # different address while the job is still in flight.
        RoleBinding.objects.all().delete()
        rebound = ModelConnection.objects.create(
            name="other image model", engine="stubengine", endpoint="http://other:9999",
            model_id="other.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=rebound)

        poll_engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(poll_engine):
            refreshed = services.refresh_job(job)

        assert poll_engine.built == [("stub.safetensors", "http://stub:9999", {})]
        assert refreshed.status == GenerationJob.Status.RUNNING
        assert refreshed.unreachable is False

    def test_a_row_without_a_recorded_endpoint_falls_back_to_the_role(self):
        """Rows written before the endpoint column exists carry a blank one;
        they must still poll, through the role binding, rather than firing
        requests at an empty address."""
        _bind()
        job = GenerationJob.objects.create(
            operation="txt2img", params=dict(RAW), seed=42, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="stubengine:stub.safetensors:None",
            endpoint="", engine_ref="ref-1", status=GenerationJob.Status.QUEUED,
        )
        engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(engine):
            refreshed = services.refresh_job(job)

        assert engine.built == [("stub.safetensors", "http://stub:9999", {})]
        assert refreshed.status == GenerationJob.Status.RUNNING


@pytest.mark.django_db
class TestFileInputs:
    """The write half of the file-param path: uploads land in the job's own
    directory, `params` keeps only their names, and the engine seam receives
    the paths."""

    OPERATION = Operation(
        key="img2img",
        label="Image to image",
        capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
            Param("seed", "seed", "Seed", default=None),
        ),
    )

    def _registered_operation(self):
        return patch.dict(operations._OPERATIONS, {"img2img": self.OPERATION})

    def test_the_upload_is_stored_and_the_params_keep_only_its_name(self, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload}, actor=OPEN_PRINCIPAL,
            )

        assert job.params["init_image"] == "beach.png"
        json.dumps(job.params)

        stored = JobInput.objects.get(job=job)
        assert stored.param_key == "init_image"
        assert stored.media_type == "image/png"
        assert Path(stored.path).read_bytes() == PNG
        assert Path(stored.path).parent == tmp_path / str(job.id) / "inputs"

    def test_the_engine_seam_receives_the_stored_path(self, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload}, actor=OPEN_PRINCIPAL,
            )

        request = generator.submitted[0]
        assert set(request.inputs) == {"init_image"}
        assert Path(request.inputs["init_image"]).read_bytes() == PNG

    def test_an_upload_for_a_param_the_operation_does_not_declare_never_lands(self, tmp_path):
        """`request.FILES` is whatever was posted. Only files answering a
        declared `"file"` param may touch the managed store."""
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        rogue = SimpleUploadedFile("payload.bin", b"\x00\x01", content_type="application/octet-stream")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload, "rogue": rogue}, actor=OPEN_PRINCIPAL,
            )

        assert [row.param_key for row in job.inputs.all()] == ["init_image"]
        assert sorted(p.name for p in (tmp_path / str(job.id) / "inputs").iterdir()) == [
            "init_image-beach.png"
        ]


@pytest.mark.django_db
class TestImg2ImgEndToEnd:
    """The real ComfyUI adapter, mocked only at `httpx`: an upload, a
    graph carrying the engine's own reference for it, and a stored
    output.

    Note the engine-side name: `store.store_input` writes
    `<param_key>-<basename>`, so the file uploaded (and therefore the
    reference the graph carries) is `init_image-beach.png`. That prefix is
    what keeps an inpaint job's image and mask from colliding when an
    operator picks two files with the same name."""

    @pytest.fixture(autouse=True)
    def _bound(self, db):
        connection = ModelConnection.objects.create(
            name="comfy sdxl", engine="comfyui", endpoint="http://comfy.local:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)

    def test_the_uploaded_file_reaches_the_engine_and_the_graph(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        fake = FakeComfyUI(prompt_id="p-9")
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"img2img": operations.IMG2IMG}), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "negative_prompt": "", "denoise": "0.4",
                 "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
                 "scheduler": "normal", "batch_size": "1", "init_image": upload},
                files={"init_image": upload}, actor=OPEN_PRINCIPAL,
            )

        assert job.status == GenerationJob.Status.QUEUED
        assert job.params["init_image"] == "beach.png"
        assert JobInput.objects.get(job=job).param_key == "init_image"
        assert fake.uploads[0]["data"]["subfolder"] == str(job.id)
        graph = job.engine_payload["prompt"]
        load_image = next(node for node in graph.values() if node["class_type"] == "LoadImage")
        assert load_image["inputs"]["image"] == f"{job.id}/init_image-beach.png"


@pytest.mark.django_db
class TestStoredInputReferences:
    """A file the platform already holds, named by a JSON-safe string so a
    queue payload -- and a link on the gallery -- can carry an image
    without carrying bytes."""

    def test_it_parses_both_kinds(self):
        assert services.parse_input_reference("output:12") == ("output", 12)
        assert services.parse_input_reference("input:3") == ("input", 3)

    def test_a_malformed_reference_says_what_a_reference_looks_like(self):
        for bad in ("", "12", "output:", "output:abc", "gallery:12", "output:1:2"):
            with pytest.raises(ValueError, match="output:<id>"):
                services.parse_input_reference(bad)

    def test_it_resolves_an_output_to_its_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        stored = services.stored_input(f"output:{output.id}", OPEN_PRINCIPAL)
        assert b"".join(stored.chunks()) == PNG
        assert stored.content_type == "image/png"
        assert stored.name == "0-job_00001_.png"

    def test_it_resolves_an_input_to_its_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "init_image-beach.png"
        source.write_bytes(PNG)
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png"
        )
        assert services.stored_input(
            f"input:{job_input.id}", OPEN_PRINCIPAL
        ).name == "init_image-beach.png"

    def test_an_unknown_id_is_a_value_error_naming_the_reference(self):
        with pytest.raises(ValueError, match="output:9999"):
            services.stored_input("output:9999", OPEN_PRINCIPAL)

    def test_a_row_whose_file_is_gone_says_so_rather_than_failing_later(self, tmp_path):
        output = stored_output(tmp_path)
        Path(output.path).unlink()
        with pytest.raises(ValueError, match="no longer on disk"):
            services.stored_input(f"output:{output.id}", OPEN_PRINCIPAL)

    def test_exists_answers_the_display_only_question_without_raising(self, tmp_path):
        """A page rendering a carried reference wants "is it still there",
        not a file object it would throw away -- and a stale link must
        leave a normal empty form behind, never an error page."""
        output = stored_output(tmp_path)
        assert services.stored_input_exists(f"output:{output.id}", OPEN_PRINCIPAL) is True
        assert services.stored_input_exists("output:9999", OPEN_PRINCIPAL) is False
        assert services.stored_input_exists("nonsense", OPEN_PRINCIPAL) is False

    def test_exists_is_false_once_the_file_is_gone(self, tmp_path):
        output = stored_output(tmp_path)
        Path(output.path).unlink()
        assert services.stored_input_exists(f"output:{output.id}", OPEN_PRINCIPAL) is False


@pytest.mark.django_db
def _own(job: GenerationJob, user) -> GenerationJob:
    """Stamp `job` as owned by `user` and save it -- `stored_output`
    writes an unowned job by default, and these tests need an owner to
    have anyone to be visible-or-not TO."""
    for field, value in owner_fields(user_principal(user)).items():
        setattr(job, field, value)
    job.save(update_fields=["owner_kind", "owner_key"])
    return job


@pytest.mark.django_db
class TestReferencedRowsAreGatedByVisibility:
    """Fix round 1, CRITICAL 1: `stored_input`/`stored_input_exists`
    used to resolve ANY live reference with no principal at all -- a
    member could derive from another member's generated image or job
    input by naming its id in `input_<param>=output:<id>`, and the
    create page's own preview (`stored_input_exists`, via
    `_stored_input_context`) would confirm it existed (an existence
    oracle). Closed by `services._visible_referenced_row`: an invisible
    reference now answers EXACTLY like a nonexistent one."""

    def test_a_members_reference_to_anothers_output_is_refused_like_a_bogus_one(self, tmp_path):
        ann, bob = make_user(), make_user()
        theirs = _own(stored_output(tmp_path).job, bob)
        output = theirs.outputs.get()
        with posture(POSTURE_PERSONAL):
            with pytest.raises(ValueError, match=r"does not name a stored image") as foreign:
                services.stored_input(f"output:{output.id}", user_principal(ann))
            with pytest.raises(ValueError, match=r"does not name a stored image") as bogus:
                services.stored_input("output:999999", user_principal(ann))
            # NOT "the file for it is no longer on disk" -- the file IS
            # there, and revealing that would still be a leak. An
            # invisible reference must read exactly like a nonexistent
            # one, so both refusals share the SAME sentence shape (each
            # naming only the reference string it was given back).
            assert str(foreign.value) == f"output:{output.id} does not name a stored image."
            assert str(bogus.value) == "output:999999 does not name a stored image."
            assert services.stored_input_exists(f"output:{output.id}", user_principal(ann)) \
                is False
            assert services.stored_input_exists("output:999999", user_principal(ann)) is False

    def test_the_owner_may_still_resolve_their_own_output(self, tmp_path):
        bob = make_user()
        mine = _own(stored_output(tmp_path).job, bob)
        output = mine.outputs.get()
        with posture(POSTURE_PERSONAL):
            resolved = services.stored_input(f"output:{output.id}", user_principal(bob))
            assert services.stored_input_exists(f"output:{output.id}", user_principal(bob)) \
                is True
        assert b"".join(resolved.chunks()) == PNG

    def test_an_open_box_is_unchanged(self, tmp_path):
        bob = make_user()
        theirs = _own(stored_output(tmp_path).job, bob)
        output = theirs.outputs.get()
        resolved = services.stored_input(f"output:{output.id}", OPEN_PRINCIPAL)
        assert b"".join(resolved.chunks()) == PNG
        assert services.stored_input_exists(f"output:{output.id}", OPEN_PRINCIPAL) is True

    def test_a_members_reference_to_anothers_job_input_is_refused(self, tmp_path):
        ann, bob = make_user(), make_user()
        theirs = _own(stored_output(tmp_path).job, bob)
        source = tmp_path / "init.png"
        source.write_bytes(PNG)
        job_input = JobInput.objects.create(
            job=theirs, param_key="init_image", path=str(source), media_type="image/png",
        )
        with posture(POSTURE_PERSONAL):
            assert services.stored_input_exists(f"input:{job_input.id}", user_principal(ann)) \
                is False
            with pytest.raises(ValueError, match="does not name a stored image"):
                services.stored_input(f"input:{job_input.id}", user_principal(ann))


@pytest.mark.django_db
class TestSubmitJobMergesFiles:
    def test_a_file_passed_only_as_a_file_still_validates_and_is_recorded(self):
        """A caller with a file has to name it once, not twice: `files` IS
        the value of that param as far as validation is concerned."""
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"img2img": operations.IMG2IMG}), \
             _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "img2img",
                {"prompt": "p", "negative_prompt": "", "denoise": "0.5", "steps": "20",
                 "cfg_scale": "7", "seed": "1", "sampler": "euler", "scheduler": "normal",
                 "batch_size": "1"},
                files={"init_image": upload}, actor=OPEN_PRINCIPAL,
            )
        assert job.params["init_image"] == "beach.png"
        assert JobInput.objects.get(job=job).param_key == "init_image"

    def test_an_undeclared_upload_is_ignored_not_stored(self):
        _bind()
        with _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "txt2img", dict(RAW),
                files={"init_image": SimpleUploadedFile("beach.png", PNG)}, actor=OPEN_PRINCIPAL,
            )
        assert job.inputs.count() == 0
        assert "init_image" not in job.params


@pytest.mark.django_db
class TestAnOperationWithNoSeed:
    def test_the_job_records_no_seed_rather_than_a_made_up_one(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"upscale": operations.UPSCALE}), \
             _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "upscale", {"upscale_model": "4x.pth"}, files={"init_image": upload}, actor=OPEN_PRINCIPAL
            )

        assert job.seed is None
        assert job.status == GenerationJob.Status.QUEUED


@pytest.mark.django_db
class TestLiveOptions:
    """Moved out of `views.py` unchanged: an engine-reported option list is
    generation behaviour, and the page is supposed to hold none."""

    def test_no_binding_reports_nothing_without_touching_an_engine(self):
        assert services.live_options(TXT2IMG, None) == {}

    def test_a_choice_param_gets_the_engines_list(self):
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ("euler", "dpmpp_2m") if key == "sampler" else ()
        resolved = ResolvedModel(
            engine="stubengine", model_id="m.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with _registered(engine):
            options = services.live_options(TXT2IMG, resolved)
        assert options["sampler"] == ("euler", "dpmpp_2m")

    def test_one_failing_param_degrades_alone_and_never_raises(self):
        def boom(endpoint, key):
            if key == "sampler":
                raise RuntimeError("engine said no")
            return ("normal",)

        engine = StubEngine(StubGenerator())
        engine.list_choices = boom
        resolved = ResolvedModel(
            engine="stubengine", model_id="m.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with _registered(engine):
            options = services.live_options(TXT2IMG, resolved)
        assert options["sampler"] == ()
        assert options["scheduler"] == ("normal",)


class TestLiveDefaults:
    """The exact twin of `TestLiveOptions` above, for the seam that reports
    where a form should START rather than what it may choose from (ADR 0012
    D-EDIT-7)."""

    def test_the_selected_models_own_defaults_are_reported(self):
        """Switching to a distilled model changes what `edit`'s own
        `param_defaults` reports -- the real adapter, not a stub, because
        this is a fact of the graph, not a behaviour a test should have to
        script."""
        resolved = _resolved(config={"family": "flux2", "variant": "distilled"})
        assert services.live_defaults(EDIT, resolved) == {"steps": 4, "guidance": 1.0}

    def test_nothing_selected_reports_nothing(self):
        assert services.live_defaults(EDIT, None) == {}

    def test_a_key_the_operation_does_not_declare_is_dropped(self):
        """An engine reports what its graph wants; only the SCHEMA says what
        a param is. A reported key with no param is not a param."""
        engine = StubEngine()
        engine.param_defaults = lambda op, config: {"steps": 4, "not_a_param": 1}
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {"steps": 4}

    def test_an_adapter_with_no_such_member_reports_nothing(self):
        with patch.dict(ENGINES, {"comfyui": StubEngine()}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {}

    def test_a_raising_adapter_costs_the_defaults_and_never_a_500(self):
        def _raise(op, config):
            raise RuntimeError("boom")

        engine = StubEngine()
        engine.param_defaults = _raise
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {}

    def test_the_catalog_reports_the_selected_models_defaults(self):
        """`operation_catalog` is what a tool reads before it submits: the
        default it sees must be the one the page starts at."""
        fake = FakeComfyUI()
        resolved = _resolved(config={"family": "flux2", "variant": "distilled"})
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            entry = next(e for e in services.operation_catalog(resolved) if e["key"] == "edit")
        assert {p["key"]: p["default"] for p in entry["params"]}["steps"] == 4


class TestLiveIgnored:
    """The exact twin of `TestLiveDefaults` above, for the seam that
    reports which params this model's graph cannot honour AT ALL (ADR
    0012 D-EDIT-13). Same `_resolved()` idiom, same never-500 tolerances."""

    def test_the_selected_models_own_ignored_params_are_reported(self):
        resolved = _resolved(config={"family": "flux2"})
        ignored = services.live_ignored(TXT2IMG, resolved)

        assert set(ignored) == {"negative_prompt", "scheduler"}
        assert ignored["scheduler"].endswith("no separate setting to choose.")

    def test_nothing_selected_reports_nothing(self):
        assert services.live_ignored(TXT2IMG, None) == {}

    def test_a_key_the_operation_does_not_declare_is_dropped(self):
        """An engine names a param the SCHEMA declares, or it names
        nothing -- the same rule `live_defaults` follows."""
        engine = StubEngine()
        engine.ignored_params = lambda op, config: {
            "scheduler": "no scheduler input", "not_a_param": "nope",
        }
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_ignored(TXT2IMG, _resolved()) == {
                "scheduler": "no scheduler input"
            }

    def test_an_unregistered_engine_name_reports_nothing(self):
        with patch.dict(ENGINES, {}, clear=True):
            assert services.live_ignored(TXT2IMG, _resolved()) == {}

    def test_an_adapter_with_no_such_member_reports_nothing(self):
        with patch.dict(ENGINES, {"comfyui": StubEngine()}, clear=True):
            assert services.live_ignored(TXT2IMG, _resolved()) == {}

    def test_a_raising_adapter_costs_the_reasons_and_never_a_500(self):
        def _raise(op, config):
            raise RuntimeError("boom")

        engine = StubEngine()
        engine.ignored_params = _raise
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_ignored(TXT2IMG, _resolved()) == {}


class TestFillEngineBlanks:
    """A field the page DISABLED submitted nothing, and
    `validate_params` refuses a blank `"choice"` regardless of `required`
    (operations.py). This is what stands between those two facts."""

    def _engine(self, ignored, choices):
        engine = StubEngine()
        engine.ignored_params = lambda op, config: dict(ignored)
        engine.list_choices = lambda endpoint, key: choices.get(key, ())
        return patch.dict(ENGINES, {"comfyui": engine}, clear=True)

    def test_an_ignored_choice_param_is_filled_from_the_engines_first_option(self):
        with self._engine(
            {"scheduler": "no scheduler input"}, {"scheduler": ("normal", "karras")}
        ):
            filled = services.fill_engine_blanks(
                TXT2IMG, {"prompt": "x", "scheduler": ""}, _resolved()
            )

        assert filled["scheduler"] == "normal"
        assert filled["prompt"] == "x"

    def test_a_reported_default_wins_over_the_first_option(self):
        engine = StubEngine()
        engine.ignored_params = lambda op, config: {"scheduler": "no scheduler input"}
        engine.list_choices = lambda endpoint, key: ("normal", "karras")
        engine.param_defaults = lambda op, config: {"scheduler": "karras"}
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            filled = services.fill_engine_blanks(TXT2IMG, {"scheduler": ""}, _resolved())

        assert filled["scheduler"] == "karras"

    def test_a_value_the_operator_supplied_is_never_overwritten(self):
        with self._engine(
            {"scheduler": "no scheduler input"}, {"scheduler": ("normal",)}
        ):
            filled = services.fill_engine_blanks(
                TXT2IMG, {"scheduler": "karras"}, _resolved()
            )

        assert filled["scheduler"] == "karras"

    def test_an_ignored_text_param_is_never_filled(self):
        """`negative_prompt` is ignored by the flux2 graphs and is a
        `"text"` param. Writing a value into the job record for text the
        graph never read would be a lie -- and `validate_params` does not
        require it, so nothing is blocked."""
        with self._engine(
            {"negative_prompt": "no negative-prompt input"}, {}
        ):
            filled = services.fill_engine_blanks(
                TXT2IMG, {"prompt": "x", "negative_prompt": ""}, _resolved()
            )

        assert filled["negative_prompt"] == ""

    def test_a_blank_choice_the_model_does_honour_is_left_alone(self):
        """Only an IGNORED key is filled by default: an ENABLED choice
        field the operator left blank is a real validation failure the
        form already reports, not something to paper over."""
        with self._engine({}, {"scheduler": ("normal",)}):
            filled = services.fill_engine_blanks(TXT2IMG, {"scheduler": ""}, _resolved())

        assert filled["scheduler"] == ""

    def test_an_explicit_key_set_overrides_the_ignored_default(self):
        """The tool path fills every blank engine-owned choice param,
        not just the ignored ones (`tools.py::_fill_engine_params`)."""
        with self._engine({}, {"scheduler": ("normal",)}):
            filled = services.fill_engine_blanks(
                TXT2IMG, {"scheduler": ""}, _resolved(), keys={"scheduler"}
            )

        assert filled["scheduler"] == "normal"

    def test_nothing_to_fill_never_asks_the_engine_anything(self):
        engine = StubEngine()
        engine.ignored_params = lambda op, config: {}

        def _boom(*args, **kwargs):
            raise AssertionError("live_options must not be consulted")

        engine.list_choices = _boom
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.fill_engine_blanks(
                TXT2IMG, {"prompt": "x"}, _resolved()
            ) == {"prompt": "x"}

    def test_nothing_resolved_returns_the_params_untouched(self):
        params = {"prompt": "x", "scheduler": ""}
        assert services.fill_engine_blanks(TXT2IMG, params, None) == params

    def test_the_input_dict_is_never_mutated(self):
        with self._engine(
            {"scheduler": "no scheduler input"}, {"scheduler": ("normal",)}
        ):
            params = {"scheduler": ""}
            services.fill_engine_blanks(TXT2IMG, params, _resolved())

        assert params == {"scheduler": ""}


@pytest.mark.django_db
class TestOperationCatalog:
    """The schema a tool caller reads, with a live engine's option lists
    filled in -- `describe()` plus the one thing `describe()` cannot know."""

    def test_it_lists_every_registered_image_generation_operation(self):
        keys = [entry["key"] for entry in services.operation_catalog()]
        assert keys == [operation.key for operation in operations_for(IMAGE_GENERATION_CAPABILITY)]

    def test_each_entry_is_describe_output(self):
        entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        described = describe(TXT2IMG)
        assert entry["label"] == described["label"]
        assert entry["description"] == described["description"]
        assert [p["key"] for p in entry["params"]] == [p["key"] for p in described["params"]]

    def test_engine_reported_options_are_filled_in_live(self):
        _bind()
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ("euler",) if key == "sampler" else ()
        with _registered(engine):
            entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        sampler = next(p for p in entry["params"] if p["key"] == "sampler")
        assert sampler["options"] == ["euler"]

    def test_a_param_the_engine_does_not_own_carries_no_options_key(self):
        _bind()
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ()
        with _registered(engine):
            entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        steps = next(p for p in entry["params"] if p["key"] == "steps")
        assert "options" not in steps

    def test_it_is_json_safe(self):
        assert json.loads(json.dumps(services.operation_catalog())) == services.operation_catalog()

    def test_nothing_bound_still_returns_the_schema_with_no_options(self):
        """An unbound role is not an error here: the shapes are still true,
        only the engine's lists are missing. Readiness is `preflight`'s
        question, and this function does not pretend to answer it."""
        catalog = services.operation_catalog()
        assert catalog
        for entry in catalog:
            for param in entry["params"]:
                assert param.get("options", []) == []


def _queued_job():
    """A submitted, not-yet-finished job -- the row `refresh_job` polls."""
    return GenerationJob.objects.create(
        operation="txt2img", params=dict(RAW), seed=42, engine="stubengine",
        model_id="stub.safetensors", endpoint="http://stub:9999",
        model_fingerprint="f", engine_ref="p-1",
        status=GenerationJob.Status.QUEUED,
    )


def _submitted_job(prompt_id="p-1") -> GenerationJob:
    """A real, comfyui-submitted job -- `engine_ref` is the engine's own
    prompt id, the fact a later `/queue` poll (`FakeComfyUI(queue_running=
    ...)`) is scripted to name."""
    connection = ModelConnection.objects.create(
        name="comfy sdxl", engine="comfyui", endpoint="http://comfy.local:8188",
        model_id="sdxl.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    fake = FakeComfyUI(prompt_id=prompt_id)
    with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
        "models.contracts.engines.comfyui.httpx.post", fake.post
    ):
        return services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)


def _finished_job(*, queued: float, processing: float) -> GenerationJob:
    """A DONE job whose three stamps are exactly `queued` seconds of wait
    plus `processing` seconds of run, ending now -- written directly with
    `.update()` (bypassing `auto_now_add`, exactly like `test_models.py`'s
    `_timed_job`) because `job_json`'s durations contract is what is under
    test here, not `refresh_job`'s own stamping."""
    finished = timezone.now()
    started = finished - timedelta(seconds=processing)
    created = started - timedelta(seconds=queued)
    job = GenerationJob.objects.create(
        operation="txt2img", params=dict(RAW), seed=42, engine="comfyui",
        model_id="sdxl.safetensors", model_fingerprint="comfyui:sdxl.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    GenerationJob.objects.filter(pk=job.pk).update(
        created_at=created, started_at=started, finished_at=finished
    )
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestJobJson:
    """One job contract for HTML, for `?format=json`, and for the queue
    result -- which means it cannot live in a view."""

    def test_it_reports_the_jobs_own_facts(self, tmp_path):
        output = stored_output(tmp_path)
        data = services.job_json(output.job)
        assert data["id"] == str(output.job.id)
        assert data["operation"] == "txt2img"
        assert data["status"] == GenerationJob.Status.DONE
        assert data["seed"] == 42
        assert data["engine"] == "stubengine"
        assert data["params"]["prompt"] == "a lighthouse"

    def test_outputs_are_named_by_url_never_by_path(self, tmp_path):
        output = stored_output(tmp_path)
        data = services.job_json(output.job)
        assert data["outputs"] == [
            {
                "id": output.id,
                "url": reverse("vision-output-file", args=[output.id]),
                "media_type": "image/png",
                "width": 512,
                "height": 512,
            }
        ]
        assert "path" not in json.dumps(data)

    def test_it_is_json_safe(self, tmp_path):
        data = services.job_json(stored_output(tmp_path).job)
        assert json.loads(json.dumps(data)) == data

    def test_a_row_that_was_never_refreshed_reports_reachable(self, tmp_path):
        """The transient attribute `refresh_job` sets is read with a
        default, so a caller that never polled gets `False` -- honest:
        nothing has tried to reach the engine for this row yet."""
        job = stored_output(tmp_path).job
        assert not hasattr(job, "unreachable")
        assert services.job_json(job)["unreachable"] is False

    def test_a_refreshed_unreachable_job_says_so(self):
        job = _queued_job()
        # `StubGenerator` raises a scripted state that IS an exception
        # (`_helpers.py:338`) -- the file's own way of saying "the engine
        # could not be reached this poll".
        generator = StubGenerator(states=[RuntimeError("engine down")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert services.job_json(job)["unreachable"] is True

    def test_inputs_are_listed_with_their_param_and_url(self, tmp_path):
        job = stored_output(tmp_path).job
        job_input = JobInput.objects.create(
            job=job, param_key="init_image",
            path=str(tmp_path / "in.png"), media_type="image/png",
        )
        data = services.job_json(job)
        assert data["inputs"] == [
            {
                "id": job_input.id,
                "param_key": "init_image",
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": "image/png",
            }
        ]

    def test_job_json_carries_the_start_and_the_durations(self):
        """The tool API's own contract: an agent must be able to read how
        long a generation took without parsing a card."""
        job = _finished_job(queued=20, processing=270)
        payload = services.job_json(job)
        assert payload["started_at"] == job.started_at.isoformat()
        assert payload["durations"]["processing"] == pytest.approx(270.0, abs=1)
        assert set(payload["durations"]) == {"submitted", "queued", "processing", "total"}

    def test_job_json_reports_submitted_as_none_with_no_queue_link(self):
        job = _finished_job(queued=20, processing=270)
        assert services.job_json(job)["durations"]["submitted"] is None

    def test_job_json_reports_submitted_when_the_queue_link_resolves(self):
        """Fix round 2026-08-25: the platform-side wait (enqueue to claim)
        is knowable through `job.queue_job_id`, and the tool API surfaces
        it under the same `durations` key everything else reads."""
        from models.queue.models import InferenceJob

        job = _finished_job(queued=20, processing=270)
        queue_job = InferenceJob.objects.create(
            kind="vision.generate", state="succeeded", priority=200, payload={},
        )
        InferenceJob.objects.filter(pk=queue_job.pk).update(
            created_at=job.created_at - timedelta(seconds=10)
        )
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=queue_job.pk)
        job.refresh_from_db()

        payload = services.job_json(job)
        assert payload["durations"]["submitted"] == pytest.approx(10.0, abs=1)


@pytest.mark.django_db
class TestResolveInputs:
    """One resolver, one semantics: the page, the queued job and a tool
    all get the same answer for the same reference."""

    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
        ),
    )

    def test_no_references_is_no_files_and_no_queries(self):
        assert services.resolve_inputs(self.OPERATION, {}, OPEN_PRINCIPAL) == {}

    def test_a_reference_resolves_to_the_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        files = services.resolve_inputs(
            self.OPERATION, {"init_image": f"output:{output.id}"}, OPEN_PRINCIPAL
        )
        assert list(files) == ["init_image"]
        assert b"".join(files["init_image"].chunks()) == PNG

    def test_an_undeclared_param_is_refused_not_dropped(self, tmp_path):
        """Silently ignoring it would run a generation that is not the one
        that was asked for."""
        output = stored_output(tmp_path)
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(
                self.OPERATION, {"mask_image": f"output:{output.id}"}, OPEN_PRINCIPAL
            )
        assert "declares no file parameter named mask_image" in str(caught.value)
        assert caught.value.param_key == "mask_image"

    def test_a_malformed_reference_names_the_param_it_came_from(self):
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(self.OPERATION, {"init_image": "beach.png"}, OPEN_PRINCIPAL)
        assert caught.value.param_key == "init_image"
        # RE-PINNED (chat image artifacts, 2026-09-16): `_REFERENCE_SHAPE`
        # names a third kind now. The PARAM-NAMING claim this test is
        # actually about is unchanged.
        assert "output:<id>" in str(caught.value)
        assert "document:<id>" in str(caught.value)

    def test_a_reference_whose_file_is_gone_is_refused(self, tmp_path):
        output = stored_output(tmp_path)
        os.remove(output.path)
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(
                self.OPERATION, {"init_image": f"output:{output.id}"}, OPEN_PRINCIPAL
            )
        assert caught.value.param_key == "init_image"
        assert "no longer on disk" in str(caught.value)

    def test_it_is_still_a_value_error_for_a_caller_with_no_form(self):
        with pytest.raises(ValueError):
            services.resolve_inputs(self.OPERATION, {"init_image": "nonsense"}, OPEN_PRINCIPAL)


@pytest.mark.django_db
class TestFailureKinds:
    """Every failure a caller of `submit_job`/`refresh_job` can observe
    names itself with a value, not only with a sentence."""

    def test_an_engine_rejection_is_engine_rejected(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("unknown checkpoint"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert job.status == GenerationJob.Status.FAILED
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_REJECTED

    def test_any_other_submission_failure_is_engine_failed(self):
        _bind()
        generator = StubGenerator(submit_error=RuntimeError("connection reset"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED

    def test_a_template_miss_is_engine_rejected_not_engine_failed(self):
        """Fix 5 (image-model-trace.md, Follow-up 3):
        `models.contracts.engines.comfyui_workflows.get_template`'s own
        `ValueError` -- a template miss for this model's family -- is a
        routine, ex-ante-knowable condition, not an engine FAULT. It
        gets the SAME classification `GenerationRejected` gets, never
        the generic `engine_failed` a real submission fault (a dropped
        connection, an OOM) gets."""
        _bind()
        generator = StubGenerator(submit_error=ValueError(
            "No ComfyUI template for operation 'img2img' in family 'flux2'; "
            "this engine implements [':txt2img']"
        ))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert job.status == GenerationJob.Status.FAILED
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_REJECTED
        assert "template" in job.error.lower()

    def test_a_role_bound_to_a_non_generating_engine_is_engine_failed(self):
        """Review round 1, finding 1a: `models.contracts.gateway.
        get_image_generator_for`'s own `ValueError` ("no
        `build_image_generator`" -- reachable only if an operator bound
        the image-generation role to a non-generating engine) is a real
        misconfiguration FAULT, not a template miss. It must not be
        swallowed by the narrower `ValueError -> engine_rejected` clause
        `submit_job` carries for `get_template`'s miss alone -- that
        would misclassify a genuine fault as a routine, expected
        refusal AND suppress its traceback."""
        _bind()

        class _NoBuilderEngine:
            name = "stubengine"

            def is_healthy(self, endpoint, timeout=None):
                return True

        with _registered(_NoBuilderEngine()):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert job.status == GenerationJob.Status.FAILED
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED
        assert "build_image_generator" in job.error

    def test_a_non_json_error_response_is_engine_failed(self):
        """Review round 1, finding 1b: `ComfyUIGenerator.submit`'s own
        `response.json()` on a >=400 response raises `json.
        JSONDecodeError` for a non-JSON body (a 502 gateway's HTML
        page, say) -- a `ValueError` SUBCLASS. Caught by the bare
        `ValueError` clause (meant for `get_template`'s miss alone) it
        would misclassify a real transport fault as a routine refusal
        and suppress its traceback."""
        _bind()
        generator = StubGenerator(submit_error=json.JSONDecodeError(
            "Expecting value", "<html>502 Bad Gateway</html>", 0,
        ))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert job.status == GenerationJob.Status.FAILED
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED

    def test_a_poll_reporting_failure_is_engine_failed(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("failed", error="CUDA out of memory")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED
        assert job.error == "CUDA out of memory"

    def test_a_forgotten_job_is_lost(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("lost")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert job.failure_kind == GenerationJob.FailureKind.LOST
        assert job.error == services.LOST_MESSAGE

    def test_an_unbound_role_reports_its_kind_on_the_exception(self):
        """No row exists to carry it -- `submit_job` refuses to write an
        orphan job for an unbound role -- so the exception carries it."""
        with pytest.raises(services.VisionUnavailable) as caught:
            services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert caught.value.failure_kind == GenerationJob.FailureKind.ROLE_UNBOUND

    def test_an_unreachable_engine_reports_its_kind_on_the_exception(self):
        _bind()
        with _registered(StubEngine(StubGenerator(), healthy=False)):
            with pytest.raises(services.VisionUnavailable) as caught:
                services.submit_job("txt2img", dict(RAW), actor=OPEN_PRINCIPAL)
        assert caught.value.failure_kind == GenerationJob.FailureKind.ENGINE_UNREACHABLE

    def test_bad_params_still_raise_before_any_row_exists(self):
        """The vocabulary's `params_invalid` names THIS failure -- said in
        `FailureKind`'s docstring and in ADR 0012, not aliased in code
        nothing reads yet. What matters behaviourally is what this test
        pins: an invalid submission raises and writes no orphan job."""
        _bind()
        with _registered(StubEngine(StubGenerator())):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", {"prompt": ""}, actor=OPEN_PRINCIPAL)
        assert GenerationJob.objects.count() == 0

    def test_a_failed_job_reports_its_kind_in_job_json(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("lost")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        data = services.job_json(job)
        assert data["failure_kind"] == GenerationJob.FailureKind.LOST
        assert data["queue_job_id"] is None

    def test_a_job_that_has_not_failed_reports_no_kind(self, tmp_path):
        assert services.job_json(stored_output(tmp_path).job)["failure_kind"] == ""


@pytest.mark.django_db
class TestStagedUploads:
    """The page's half of the queue seam: a browser file becomes an
    ordinary `input:<id>` reference a JSON payload can carry."""

    def test_staging_returns_an_ordinary_input_reference(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", upload)
        assert services.parse_input_reference(reference)[0] == "input"
        assert b"".join(services.stored_input(reference, OPEN_PRINCIPAL).chunks()) == PNG

    def test_the_staged_row_belongs_to_no_job_and_remembers_its_param(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", upload)
        row = JobInput.objects.get(pk=int(reference.split(":")[1]))
        assert row.job_id is None
        assert row.param_key == "init_image"
        assert row.media_type == "image/png"

    def test_a_staged_upload_is_swept_once_it_is_old_enough(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            row = JobInput.objects.get(pk=int(reference.split(":")[1]))
            JobInput.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(hours=48)
            )
            assert services.prune_staged_inputs() == 1
        assert not JobInput.objects.filter(pk=row.pk).exists()
        assert not Path(row.path).exists()

    def test_a_fresh_staged_upload_survives_the_sweep(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            assert services.prune_staged_inputs() == 0
        assert services.stored_input_exists(reference, OPEN_PRINCIPAL)

    def test_a_jobs_own_input_is_never_swept(self, tmp_path):
        """Only rows with no job are staged uploads. A job's input is the
        job's, however old it is."""
        job = stored_output(tmp_path).job
        source = tmp_path / "in.png"
        source.write_bytes(PNG)
        row = JobInput.objects.create(job=job, param_key="init_image", path=str(source))
        JobInput.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=30))
        with override_settings(GENERATED_DIR=tmp_path):
            assert services.prune_staged_inputs() == 0
        assert JobInput.objects.filter(pk=row.pk).exists()
        assert source.exists()

    def test_discarding_removes_the_rows_and_the_files_at_once(self, tmp_path):
        """For the one case where the answer is certain: the enqueue
        failed, so nothing will ever consume them."""
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            row = JobInput.objects.get(pk=int(reference.split(":")[1]))
            services.discard_staged_inputs([reference])
        assert not JobInput.objects.filter(pk=row.pk).exists()
        assert not Path(row.path).exists()

    def test_discarding_never_touches_a_job_owned_input(self, tmp_path):
        job = stored_output(tmp_path).job
        source = tmp_path / "in.png"
        source.write_bytes(PNG)
        row = JobInput.objects.create(job=job, param_key="init_image", path=str(source))
        with override_settings(GENERATED_DIR=tmp_path):
            services.discard_staged_inputs([f"input:{row.id}"])
        assert JobInput.objects.filter(pk=row.pk).exists()

    def test_discarding_an_unresolvable_reference_is_not_an_error(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            services.discard_staged_inputs(["output:999", "nonsense", "input:999"])


@pytest.mark.django_db
class TestPreflightOnAPickedConnection:
    def test_a_supplied_binding_is_health_checked_instead_of_the_role(self):
        """The picked model stands in for the role binding for this one
        call -- so an unbound role is NOT an error when the caller brought
        its own model."""
        engine = StubEngine(healthy=True)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            check = services.preflight(picked)

        assert check.state == "ready"
        assert check.resolved is picked

    def test_a_supplied_binding_whose_engine_is_down_is_unreachable_not_unbound(self):
        engine = StubEngine(healthy=False)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            check = services.preflight(picked)

        assert check.state == "unreachable"
        assert "http://stub:9999" in check.message

    def test_no_argument_still_resolves_the_role(self):
        """Every existing caller passes nothing and must behave exactly as
        it did."""
        assert services.preflight().state == "unbound"


@pytest.mark.django_db
class TestSubmitJobOnAPickedConnection:
    def test_the_job_records_the_binding_that_actually_ran_it(self):
        """D6: a job documents the model that ran it, not the one bound now
        -- which is why no new column is needed to record the pick."""
        generator = StubGenerator()
        engine = StubEngine(generator=generator)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://picked:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            job = services.submit_job("txt2img", dict(RAW), resolved=picked, actor=OPEN_PRINCIPAL)

        assert job.engine == "stubengine"
        assert job.model_id == "picked.safetensors"
        assert job.endpoint == "http://picked:9999"
        assert job.model_config == {"family": "flux2"}
        assert engine.built == [("picked.safetensors", "http://picked:9999", {"family": "flux2"})]


class TestOperationsForModel:
    def test_nothing_selected_lists_every_registered_operation(self):
        assert {op.key for op in services.operations_for_model(None)} == {
            "txt2img", "img2img", "inpaint", "upscale", "edit",
        }

    def test_a_model_only_offers_what_its_engine_has_a_graph_for(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",) if family else ("txt2img",)

        picked = ResolvedModel(
            engine="stubengine", model_id="w.gguf",
            endpoint="http://stub:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            assert [op.key for op in services.operations_for_model(picked)] == ["edit"]

    def test_a_connection_with_no_family_gets_the_checkpoint_modes(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",) if family else ("txt2img",)

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            assert [op.key for op in services.operations_for_model(picked)] == ["txt2img"]

    def test_a_json_null_family_gets_the_checkpoint_modes_too(self):
        """Finding 9: `config={"family": None}` (a JSON `null`, not merely
        an absent key) must normalize the same way an absent key does --
        `config_family` (`models.contracts.bindings`) is the ONE shared
        normalization, also used by `ComfyUIGenerator.submit`."""
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",) if family else ("txt2img",)

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={"family": None},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            assert [op.key for op in services.operations_for_model(picked)] == ["txt2img"]

    def test_an_engine_that_cannot_say_never_narrows_anything(self):
        """`supported_operations` is an OPTIONAL protocol member. An adapter
        without one has no opinion, and no opinion must never be read as
        'nothing supported'."""
        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            assert len(services.operations_for_model(picked)) == 5

    def test_an_engine_that_raises_never_narrows_anything_either(self):
        class _Broken(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                raise RuntimeError("boom")

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Broken()}, clear=True):
            assert len(services.operations_for_model(picked)) == 5

    def test_a_model_that_supports_nothing_narrows_to_nothing(self):
        """The opposite of 'no opinion': the engine DID answer, and the
        answer is an empty set. That is a real, narrower fact and must
        empty the list, not fall back to the full registry."""
        class _Empty(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ()

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Empty()}, clear=True):
            assert services.operations_for_model(picked) == []


class TestUnionParams:
    """The SET and the ORDER the constant form renders: every registered
    operation's params, de-duplicated by key, first declaration wins, in
    registration order (ADR 0012 D-EDIT-13)."""

    def test_it_is_every_key_in_first_declaration_order(self):
        union = services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))

        assert [param.key for param in union] == [
            "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
            "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
            "init_image", "denoise", "mask_image", "mask_grow", "upscale_model",
            "instruction", "reference_image", "guidance",
        ]

    def test_the_first_declaration_of_a_shared_key_wins(self):
        """`steps` is 25 in `SAMPLING_PARAMS` and 20 in `EDIT`; `denoise`
        is 0.6 in img2img and 1.0 in inpaint. The union carries the
        FIRST, and the picked operation's own `Param` is what the form
        layer swaps back in for a field that operation declares."""
        union = {param.key: param for param in
                 services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))}

        assert union["steps"].default == 25
        assert union["denoise"].default == 0.6

    def test_an_empty_sequence_is_an_empty_union(self):
        assert services.union_params([]) == ()

    def test_it_returns_a_tuple_of_the_real_param_objects(self):
        union = services.union_params([TXT2IMG])
        assert isinstance(union, tuple)
        assert union[0] is TXT2IMG.params[0]


class TestOperationStates:
    """Every registered operation, with the reason the selected model
    cannot run the ones it cannot -- ONE `operations_for_model` call."""

    def test_nothing_selected_supports_everything_with_no_reasons(self):
        states = services.operation_states(None)

        assert [state.operation.key for state in states] == [
            op.key for op in operations_for(IMAGE_GENERATION_CAPABILITY)
        ]
        assert all(state.supported for state in states)
        assert all(state.reason == "" for state in states)

    def test_a_declared_family_names_itself_in_the_reason(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit", "txt2img")

        resolved = ResolvedModel(
            engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999",
            config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            states = {s.operation.key: s for s in services.operation_states(resolved)}

        assert states["txt2img"].supported is True
        assert states["txt2img"].reason == ""
        assert states["inpaint"].supported is False
        assert states["inpaint"].reason == "No inpaint graph for the flux2 family."

    def test_a_connection_with_no_family_gets_the_engine_wording(self):
        class _Checkpoint(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("txt2img", "img2img", "inpaint", "upscale")

        resolved = ResolvedModel(
            engine="stubengine", model_id="sdxl.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Checkpoint()}, clear=True):
            states = {s.operation.key: s for s in services.operation_states(resolved)}

        assert states["edit"].supported is False
        assert states["edit"].reason == "This model's engine has no edit an image graph."

    def test_a_model_that_supports_nothing_reports_a_reason_for_every_mode(self):
        class _Nothing(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ()

        resolved = ResolvedModel(
            engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999",
            config={"family": "unknown_family"},
        )
        with patch.dict(ENGINES, {"stubengine": _Nothing()}, clear=True):
            states = services.operation_states(resolved)

        assert states
        assert not any(state.supported for state in states)
        assert all("unknown_family" in state.reason for state in states)

    def test_it_asks_the_engine_exactly_once(self):
        """One `supported_operations` round trip for the whole select --
        the same dedup reasoning `operation_catalog`'s single preflight
        already follows."""
        calls = []

        class _Counting(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                calls.append(family)
                return ("txt2img",)

        resolved = ResolvedModel(
            engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Counting()}, clear=True):
            services.operation_states(resolved)

        assert len(calls) == 1


class TestOperationCatalogFollowsTheSelectedModel:
    def test_the_catalog_lists_every_mode_and_marks_which_one_is_runnable(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        picked = ResolvedModel(
            engine="stubengine", model_id="w.gguf",
            endpoint="http://stub:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            catalog = services.operation_catalog(picked)

        # The array is every REGISTERED mode now, in registration order
        # (ADR 0012 D-EDIT-13) -- "which can I run" is the `supported`
        # flag, not the presence of an entry.
        assert [entry["key"] for entry in catalog] == [
            operation.key for operation in operations_for(IMAGE_GENERATION_CAPABILITY)
        ]
        assert [entry["key"] for entry in catalog if entry["supported"]] == ["edit"]
        assert all(
            entry["unsupported_reason"] for entry in catalog if not entry["supported"]
        )
