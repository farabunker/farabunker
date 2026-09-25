"""Unit tests for tools/vision/jobs.py -- the `vision.generate` job kind.

`plan_generate`/`summarize_generate` are tested against a real DB (a bound
role, or a `patch.dict`-registered operation) rather than mocked seams,
since both are thin wrappers with little to mock; `run_generate`'s happy
path is proven end-to-end with `FakeComfyUI` at the `httpx` layer (mirroring
`tools/vision/tests/test_comfyui_generator.py`'s convention), through the
REAL, globally-registered `vision.generate` job kind's handler -- the same
"prove the real registered kind" shape `tools/rag/tests/test_jobs.py`'s
`TestEndToEndViaWorker` uses, minus the worker/queue plumbing (a queue
end-to-end test belongs to `models/queue/tests`, not here).
"""
from __future__ import annotations

import importlib
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps as django_apps
from django.test import override_settings
from django.urls import clear_url_caches, reverse

from identity.contracts.principals import OPEN_PRINCIPAL
from models.registry.models import ModelConnection, RoleBinding
from models.contracts import jobkinds as jobkinds_module
from models.contracts import operations as operations_module
from models.contracts.engines import ENGINES
from models.contracts.engines.base import JobStatus
from models.contracts.operations import Operation, Param
from models.contracts.roles import RAG_EXTRACT_ROLE, VISION_GENERATE_ROLE
from tools.vision import jobs
from tools.vision.models import GenerationJob
from tools.vision.tests._helpers import (
    PNG,
    RAW,
    FakeComfyUI,
    StubEngine,
    StubGenerator,
    clear_bindings,
    history_error,
    history_success,
    make_job_ctx,
    reset_engine_caches,
    stored_output,
)


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()


ENDPOINT = "http://comfy.local:8188"


def _stub_done_job():
    """A `GenerationJob`-shaped stand-in for the tests that pin how the
    handler CALLS the service layer rather than what it produces."""
    stub = MagicMock()
    stub.id = "stub-id"
    # A real, valid (if unmatched) pk: `run_generate` now does a real
    # `.filter(pk=job.pk).update(...)` after `submit_job` returns, which
    # a real UUIDField validates even against a mocked stand-in -- an
    # unconfigured MagicMock's default `__iter__` (empty) would otherwise
    # be coerced into `[]` and fail that validation. Zero rows match, so
    # the update is a harmless no-op; these tests pin the CALL shape, not
    # persistence.
    stub.pk = uuid.uuid4()
    stub.status = GenerationJob.Status.DONE
    stub.is_terminal = True
    stub.outputs.values_list.return_value = []
    return stub


def _bind_comfyui():
    connection = ModelConnection.objects.create(
        name="comfy sdxl", engine="comfyui", endpoint=ENDPOINT,
        model_id="sdxl.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    return connection


def _bind_extract():
    """Binds `rag.extract` to a SEPARATE stub connection/engine -- the
    vision-describes-its-own-output task's own multi-role precedent
    (`plan_ingest`'s image/pdf-scanned branch). A DIFFERENT engine name
    from `_bind_comfyui`'s ("stubengine", never "comfyui") on purpose:
    the two roles name two different runtimes in production (an image
    engine and a vision-capable chat engine), and a test that bound both
    to the SAME engine name could not catch a bug that mixed them up."""
    connection = ModelConnection.objects.create(
        name="vision-capable chat", engine="stubengine", endpoint="http://stub:9999",
        model_id="describer.gguf", capabilities=["vision"],
    )
    RoleBinding.objects.create(role_key=RAG_EXTRACT_ROLE, connection=connection)
    return connection


# --- plan_generate -----------------------------------------------------


@pytest.mark.django_db
class TestPlanGenerate:
    @pytest.fixture(autouse=True)
    def _clear_bindings(self, db):
        clear_bindings()

    def test_resolves_the_bound_model(self):
        _bind_comfyui()

        model_refs, exclusive = jobs.plan_generate({})

        assert exclusive is True
        assert len(model_refs) == 1
        ref = model_refs[0]
        assert ref.role == VISION_GENERATE_ROLE
        assert ref.engine == "comfyui"
        assert ref.endpoint == ENDPOINT
        assert ref.model_id == "sdxl.safetensors"
        assert ref.connection_name == ""
        # Provenance contract (models/queue/scheduler.py): the planner never
        # resolves footprints -- claim-time code fills that in fresh.
        assert ref.footprint_bytes is None

    def test_unbound_role_raises_honestly(self):
        with pytest.raises(ValueError, match="vision.generate"):
            jobs.plan_generate({})

    def test_a_payload_with_no_connection_plans_identically(self):
        """A payload that names no `connection` plans against the role
        binding, whatever else it carries -- `_resolve_model` falls back to
        it the same way regardless of what other keys are present. (A
        payload that DOES name a `connection`, per-job model choice the same
        way `rag.ask`'s override works, is covered elsewhere -- see
        `TestPickedConnectionTravelsInThePayload`.)"""
        _bind_comfyui()

        refs_a, _ = jobs.plan_generate({})
        refs_b, _ = jobs.plan_generate({"operation": "txt2img", "params": {"prompt": "x"}})

        assert refs_a == refs_b

    def test_rag_extract_unbound_declares_only_vision_generate(self):
        """The tolerant fallback (vision-describes-its-own-output task,
        mirroring `tools.rag.jobs.plan_ingest`'s own `rag.extract`
        branch): an unbound extraction role must not stop this job from
        being planned at all."""
        _bind_comfyui()

        model_refs, exclusive = jobs.plan_generate({})

        assert exclusive is True
        assert [ref.role for ref in model_refs] == [VISION_GENERATE_ROLE]

    def test_rag_extract_bound_declares_both_roles(self):
        _bind_comfyui()
        _bind_extract()

        model_refs, exclusive = jobs.plan_generate({})

        assert exclusive is True
        assert [ref.role for ref in model_refs] == [VISION_GENERATE_ROLE, RAG_EXTRACT_ROLE]
        extract_ref = model_refs[1]
        assert extract_ref.engine == "stubengine"
        assert extract_ref.model_id == "describer.gguf"
        # Same provenance contract as the image ref: never resolved here.
        assert extract_ref.footprint_bytes is None
        # Fix round item 4: declared `False` -- the barrier must not pay
        # an unconditional wait at this endpoint for a conditional call
        # that may never run (a failed or output-less generation never
        # reaches `describe_if_ready` at all). Protection, sweeping and
        # budget accounting are all unaffected by the flag -- see
        # `plan_generate`'s own docstring for the full decision.
        assert extract_ref.synchronous is False
        assert model_refs[0].synchronous is True  # vision.generate: unchanged


# --- run_generate: payload-referenced file inputs ------------------------


@pytest.mark.django_db
class TestPayloadInputs:
    """A queued job carries an image by REFERENCE. The payload stays JSON;
    the bytes stay in the managed store."""

    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
            Param("seed", "seed", "Seed"),
        ),
    )

    def test_the_handler_resolves_inputs_through_the_one_service_resolver(self, tmp_path):
        """`jobs.py` keeps no resolver of its own: the page and the queue
        must refuse and accept exactly the same references."""
        assert not hasattr(jobs, "_payload_files")
        _bind_comfyui()
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "tools.vision.jobs.services.resolve_inputs", return_value={}
        ) as mock_resolve, patch(
            "tools.vision.jobs.services.submit_job"
        ) as mock_submit, patch("tools.vision.jobs.services.wait_for") as mock_wait:
            mock_submit.return_value = mock_wait.return_value = _stub_done_job()
            jobs.run_generate(
                {"operation": "img2img", "params": {"prompt": "x"},
                 "inputs": {"init_image": f"output:{output.id}"}},
                [],
                make_job_ctx(),
            )
        operation, references, actor = mock_resolve.call_args.args
        assert operation.key == "img2img"
        assert references == {"init_image": f"output:{output.id}"}
        # IA-1: the payload named no actor, so `principal_from_payload`
        # answers the OPEN principal -- the least-privileged honest
        # reading (`identity.contracts.principals.principal_from_
        # payload`'s own docstring), and it must be the SAME actor
        # `submit_job` is given below, not a second, disagreeing one.
        assert actor == OPEN_PRINCIPAL

    def test_an_undeclared_file_param_still_refuses_the_whole_job(self, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            with pytest.raises(ValueError, match="declares no file parameter named mask_image"):
                jobs.run_generate(
                    {"operation": "img2img", "params": {"prompt": "x"},
                     "inputs": {"mask_image": f"output:{output.id}"}},
                    [],
                    make_job_ctx(),
                )

    def test_the_payload_itself_stays_json(self, tmp_path):
        """The whole reason references exist: nothing in a payload has to
        be anything but a string."""
        output = stored_output(tmp_path)
        payload = {"operation": "img2img", "params": {"prompt": "p"},
                   "inputs": {"init_image": f"output:{output.id}"}}
        assert json.loads(json.dumps(payload)) == payload

    # --- carried over from TestRunGenerateRejectsFileParams -------------

    def test_an_unregistered_operation_is_left_to_submit_jobs_own_error(self):
        """`run_generate` has no schema of its own to check for an unknown
        operation key -- an unresolvable `get_operation` short-circuits
        input resolution and lets the payload through to `submit_job`,
        which raises the one operator-facing "Unknown operation" message.
        A second copy of that check here would be a second place to keep
        in sync."""
        _bind_comfyui()
        with pytest.raises(ValueError, match="Unknown operation"):
            jobs.run_generate({"operation": "not-a-real-operation", "params": {}}, [], make_job_ctx())

    def test_run_generate_passes_the_resolved_files_through_to_submit_job(self):
        """The ONE test that pins how the handler calls the service layer.
        A payload with no `inputs` still passes `files=None`, so the page
        path and the queue path reach `submit_job` in exactly one shape --
        plus the freshly re-resolved binding (T9), which `run_generate` now
        looks up itself (`_resolve_model`) rather than leaving to
        `submit_job`'s own internal `preflight()`."""
        _bind_comfyui()
        stub_job = MagicMock()
        stub_job.id = "stub-id"
        stub_job.pk = uuid.uuid4()  # see `_stub_done_job`'s comment
        stub_job.status = GenerationJob.Status.DONE
        stub_job.outputs.values_list.return_value = []

        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "tools.vision.jobs.services.submit_job", return_value=stub_job
        ) as mock_submit, patch("tools.vision.jobs.services.wait_for", return_value=stub_job):
            jobs.run_generate({"operation": "img2img", "params": {"prompt": "x"}}, [], make_job_ctx())

        mock_submit.assert_called_once()
        assert mock_submit.call_args.args == ("img2img", {"prompt": "x"})
        assert mock_submit.call_args.kwargs["files"] is None
        assert mock_submit.call_args.kwargs["resolved"].model_id == "sdxl.safetensors"

    def test_a_referenced_input_reaches_submit_job_as_a_stored_file(self, tmp_path):
        _bind_comfyui()
        output = stored_output(tmp_path)
        stub_job = MagicMock()
        stub_job.id = "stub-id"
        stub_job.pk = uuid.uuid4()  # see `_stub_done_job`'s comment
        stub_job.status = GenerationJob.Status.DONE
        stub_job.outputs.values_list.return_value = []

        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "tools.vision.jobs.services.submit_job", return_value=stub_job
        ) as mock_submit, patch("tools.vision.jobs.services.wait_for", return_value=stub_job):
            jobs.run_generate(
                {"operation": "img2img", "params": {"prompt": "x"},
                 "inputs": {"init_image": f"output:{output.id}"}},
                [],
                make_job_ctx(),
            )

        _key, _params = mock_submit.call_args.args
        files = mock_submit.call_args.kwargs["files"]
        assert list(files) == ["init_image"]
        assert b"".join(files["init_image"].chunks()) == PNG


# --- run_generate: happy path, end-to-end through the real handler ------


@pytest.mark.django_db
class TestRunGenerateEndToEnd:
    @pytest.fixture(autouse=True)
    def _clear_bindings(self, db):
        clear_bindings()

    def test_happy_path_reaches_done_with_output_rows(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        payload = {"operation": "txt2img", "params": dict(RAW)}

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(payload, [], make_job_ctx())

        assert result["status"] == GenerationJob.Status.DONE
        assert len(result["output_ids"]) == 1

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.status == GenerationJob.Status.DONE
        assert job.engine == "comfyui"
        assert job.model_id == "sdxl.safetensors"
        assert list(job.outputs.values_list("id", flat=True)) == result["output_ids"]

    def test_unbound_role_raises_vision_unavailable(self):
        """T9 fix-round Q1: `run_generate` now resolves the model itself
        (`_resolve_model`), fresh, before ever calling `submit_job` -- but
        an unbound role at claim time must still surface as the SAME
        `VisionUnavailable` the page's own preflight raises, not a bare
        `resolve()` `ValueError` with an implementation-shaped message."""
        with pytest.raises(jobs.services.VisionUnavailable):
            jobs.run_generate({"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx())

    def test_unbound_role_carries_the_role_unbound_failure_kind(self):
        """The queue worker stores `str(exc)` on the job row, but a caller
        reading the exception itself (as `models.queue.worker.Worker.
        _execute` does today for the synchronous-preflight path) needs the
        MACHINE-readable kind too -- the same vocabulary a FAILED row
        carries (`GenerationJob.FailureKind`)."""
        with pytest.raises(jobs.services.VisionUnavailable) as exc_info:
            jobs.run_generate({"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx())

        assert exc_info.value.state == "unbound"
        assert exc_info.value.failure_kind == GenerationJob.FailureKind.ROLE_UNBOUND
        assert "No model assigned for Image generation" in exc_info.value.message

    def test_timeout_returns_the_jobs_state_as_is_without_raising(self, tmp_path):
        """`wait_for`'s own semantics (services.py): a job still non-terminal
        at the timeout is returned exactly as-is -- proven here by giving
        `run_generate` no time to wait at all."""
        _bind_comfyui()
        # Still pending in ComfyUI's own queue -> `status()` reports "queued",
        # matching `test_comfyui_generator.py::TestStatus.
        # test_queued_when_the_ref_is_pending`.
        fake = FakeComfyUI(prompt_id="p-1", queue_pending=[[1, "p-1", {}, {}, []]])
        payload = {"operation": "txt2img", "params": dict(RAW)}

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch("tools.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0):
            result = jobs.run_generate(payload, [], make_job_ctx())

        assert result["status"] == GenerationJob.Status.QUEUED
        assert result["output_ids"] == []

    def test_the_generation_records_the_queue_job_that_asked_for_it(self, tmp_path):
        """Correlation while it runs: `/vision/queue/<id>/` finds the
        generation by this column, so the page's queued card can become
        the real card mid-generation instead of at the very end."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx(job_id=41)
            )

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.queue_job_id == 41

    def test_a_failed_generation_is_still_correlated(self, tmp_path):
        """The stamp happens on whatever `submit_job` returned, including
        a job it already failed -- an engine rejection is a normal
        outcome, and a rejected job the queue cannot be traced back to is
        exactly the debugging hole this column closes."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-1", prompt_status=400,
            prompt_body={"error": {"message": "unknown checkpoint"}},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx(job_id=42)
            )

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.status == GenerationJob.Status.FAILED
        assert job.queue_job_id == 42

    def test_a_finished_generation_reports_it_did_not_time_out(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["timed_out"] is False
        assert result["output_urls"] == [
            reverse("vision-output-file", args=[output_id]) for output_id in result["output_ids"]
        ]

    def test_the_queue_result_carries_the_durations(self, tmp_path):
        """Beside `output_urls` and `timed_out`, so an agent driving the
        queue sees the cost of what it just ran."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert set(result["durations"]) == {"submitted", "queued", "processing", "total"}

    def test_a_generation_still_running_at_the_timeout_says_so(self, tmp_path):
        """The unreadable shape this replaces: a SUCCEEDED queue job whose
        status is "queued". The flag says what happened; the status still
        says what the generation was doing when we stopped watching."""
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-1", queue_pending=[[1, "p-1", {}, {}, []]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch("tools.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["timed_out"] is True
        assert result["status"] == GenerationJob.Status.QUEUED
        assert result["output_ids"] == []
        assert result["output_urls"] == []

    def test_a_failed_generation_is_not_a_timeout(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-2", history=history_error("p-2", "CUDA out of memory"))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["status"] == GenerationJob.Status.FAILED
        assert result["timed_out"] is False

    def test_the_handler_reports_progress_from_the_wait_loop(self, tmp_path):
        """T3's seam, wired: the queue page shows a live "0:07 generating"
        line for a running generation instead of nothing at all. Seconds
        with NO total, because ComfyUI reports no fraction over HTTP -- a
        percentage here would be invented.

        The engine is scripted to be RUNNING on the first poll and finished
        by the second, which is the only shape that produces a reportable
        tick: the terminal tick reports nothing (see `run_generate`'s own
        `report`)."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            queue_running=[[0, "p-99", {}, {}, []]],
            images={"out.png": PNG},
        )
        real_get = fake.get
        reported = []

        def scripted_get(url, params=None, timeout=None):
            """Script the SERVER, not the adapter: the job is running when
            the first poll asks, and its history has landed before the
            second. The adapter's real URL building and parsing still run."""
            response = real_get(url, params=params, timeout=timeout)
            if "/queue" in url:
                fake.history = history_success("p-99", filenames=("out.png",))
                fake.queue_running = []
            return response

        with patch("models.contracts.engines.comfyui.httpx.get", scripted_get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch("tools.vision.services.time.sleep"), override_settings(
            GENERATED_DIR=tmp_path
        ):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported, "run_generate must report at least one progress tick"
        assert reported[-1]["unit"] == "seconds"
        assert reported[-1]["total"] is None
        assert reported[-1]["label"] == "generating"
        assert "done" in reported[-1]
        assert isinstance(reported[-1]["done"], int)

    def test_a_finished_job_gets_no_final_progress_tick(self, tmp_path):
        """A generation already done on its first poll reports nothing:
        there is no honest label for a terminal tick."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        reported = []

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), override_settings(GENERATED_DIR=tmp_path):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported == []

    def test_a_still_queued_generation_reports_the_queued_label(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-1", queue_pending=[[1, "p-1", {}, {}, []]])
        reported = []

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch("tools.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported[-1]["label"] == "waiting on the image engine"


@pytest.mark.django_db
class TestRunGenerateDescribesItsOutput:
    """The sequential describe step (vision-describes-its-own-output
    task; ruling 1 removed an earlier release-the-image-model step --
    see `run_generate`'s own comment for why: ComfyUI's `/free` frees
    EVERY model at an endpoint, never one, so releasing would have cost
    the NEXT generation a cold load of up to ~25 minutes on this
    hardware). `tools.vision.services.describe_output` is patched
    directly rather than exercised for real -- the CALL SHAPE (whether
    it runs at all, and with which job) is what these tests pin, exactly
    as `_stub_done_job`'s sibling tests elsewhere in this file pin
    `submit_job`'s call shape rather than exercising a real engine for
    every case; `TestDescribeOutput` in `test_services.py` covers what
    the function itself does."""

    @pytest.fixture(autouse=True)
    def _clear_bindings(self, db):
        clear_bindings()

    def test_extract_unbound_skips_describing_entirely(self, tmp_path):
        """The gate that keeps every existing, role-unbound box (and
        every OTHER test in this file) exactly as fast as it is today:
        no `rag.extract` binding means this whole step never runs."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-10", history=history_success("p-10", filenames=("out.png",)),
            images={"out.png": PNG},
        )

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch(
            "tools.vision.services.describe_output"
        ) as describe_mock, override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["status"] == GenerationJob.Status.DONE
        describe_mock.assert_not_called()

    def test_extract_bound_describes_the_finished_job(self, tmp_path):
        _bind_comfyui()
        _bind_extract()
        fake = FakeComfyUI(
            prompt_id="p-11", history=history_success("p-11", filenames=("out.png",)),
            images={"out.png": PNG},
        )

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch(
            "tools.vision.services.describe_output"
        ) as describe_mock, override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        job = GenerationJob.objects.get(pk=result["job_id"])
        # `request_timeout` (fix round items 3 and its follow-up): the
        # shared ceiling BOTH callers use, never a bare number threaded
        # blind, and never written twice -- lives in `services.py`
        # because the CHAT caller needs the same number too.
        describe_mock.assert_called_once_with(
            job, request_timeout=jobs.services.DESCRIBE_REQUEST_TIMEOUT_SECONDS
        )

    def test_a_failed_job_never_describes(self, tmp_path):
        """Requirement 4: never for a failed, refused, or cancelled job."""
        _bind_comfyui()
        _bind_extract()
        fake = FakeComfyUI(
            prompt_id="p-14", prompt_status=400,
            prompt_body={"error": {"message": "unknown checkpoint"}},
        )

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch(
            "tools.vision.services.describe_output"
        ) as describe_mock, override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["status"] == GenerationJob.Status.FAILED
        describe_mock.assert_not_called()

    def test_a_still_running_job_never_describes(self, tmp_path):
        """Requirement 4 again, the still-running/timed-out edge: a job
        `wait_for` gave back non-terminal must not be treated as done."""
        _bind_comfyui()
        _bind_extract()
        fake = FakeComfyUI(prompt_id="p-15", queue_pending=[[1, "p-15", {}, {}, []]])

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), patch(
            "models.contracts.engines.comfyui.httpx.post", fake.post
        ), patch(
            "models.contracts.engines.comfyui.get_bounded", fake.get_bounded
        ), patch(
            "tools.vision.services.describe_output"
        ) as describe_mock, patch(
            "tools.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0
        ):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["timed_out"] is True
        describe_mock.assert_not_called()


@pytest.mark.django_db
class TestPickedConnectionTravelsInThePayload:
    def test_the_planner_plans_against_the_picked_connection(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://picked:9999",
            model_id="picked.safetensors", capabilities=["image-generation"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            models, exclusive = jobs.plan_generate(
                {"operation": "txt2img", "params": {}, "connection": str(connection.pk)}
            )

        assert exclusive is True
        assert [(m.engine, m.model_id, m.endpoint) for m in models] == [
            ("stubengine", "picked.safetensors", "http://picked:9999")
        ]
        assert models[0].connection_name == "picked"

    def test_a_payload_with_no_connection_still_plans_against_the_role(self):
        """A blank or absent field is exactly today's behaviour, which is
        what every job enqueued before the picker existed carries."""
        connection = ModelConnection.objects.create(
            name="bound", engine="stubengine", endpoint="http://bound:9999",
            model_id="bound.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            models, _exclusive = jobs.plan_generate({"operation": "txt2img", "params": {}})
            blank, _ = jobs.plan_generate(
                {"operation": "txt2img", "params": {}, "connection": ""}
            )

        assert models[0].model_id == "bound.safetensors"
        assert blank[0].model_id == "bound.safetensors"

    def test_a_pk_that_no_longer_resolves_fails_the_job_loudly(self):
        """A deleted connection is a bad payload, not something to quietly
        substitute the role binding for -- the operator asked for a model
        that is gone and must be told."""
        with pytest.raises(ValueError):
            jobs.plan_generate({"operation": "txt2img", "params": {}, "connection": "999999"})

    def test_a_pk_lacking_the_capability_also_fails_planning_loudly(self):
        """A real, registered connection that just isn't image-generation
        capable (a chat model, say) is exactly as bad a pick as a deleted
        pk -- `resolve_connection_named` refuses it the same way, and
        `plan_generate` must not quietly fall back to the role binding for
        either kind of bad pick."""
        chat_only = ModelConnection.objects.create(
            name="chat model", engine="stubengine", endpoint="http://chat:9999",
            model_id="chat.gguf", capabilities=["chat"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True), pytest.raises(ValueError):
            jobs.plan_generate(
                {"operation": "txt2img", "params": {}, "connection": str(chat_only.pk)}
            )

    def test_the_handler_turns_a_rejected_connection_into_an_honest_unavailable(self):
        """Final-review finding 4 (previously T9 fix-round Q3, which folded
        a bad PICK into the SAME `unbound`/`ROLE_UNBOUND` shape a genuinely
        unassigned role gets -- honestly wrong: `_resolve_model` never
        touches the role at all when the payload names a connection, so
        the failure is never "no model assigned", it is "the model you
        picked is gone"). Same capability rejection as before, reached
        through `run_generate` (claim time) rather than `plan_generate`
        (enqueue time); now asserts the DISTINCT `connection_unavailable`
        state/kind and the shared `UNREGISTERED_CONNECTION_MESSAGE`
        sentence."""
        chat_only = ModelConnection.objects.create(
            name="chat model", engine="stubengine", endpoint="http://chat:9999",
            model_id="chat.gguf", capabilities=["chat"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            with pytest.raises(jobs.services.VisionUnavailable) as exc_info:
                jobs.run_generate(
                    {
                        "operation": "txt2img",
                        "params": dict(RAW),
                        "connection": str(chat_only.pk),
                    },
                    [],
                    make_job_ctx(),
                )

        assert exc_info.value.state == "connection_unavailable"
        assert exc_info.value.failure_kind == GenerationJob.FailureKind.CONNECTION_UNAVAILABLE
        assert exc_info.value.message == jobs.services.UNREGISTERED_CONNECTION_MESSAGE

    def test_a_bad_pick_is_honest_even_when_the_role_is_bound(self):
        """The distinguishing fact is "did the payload name a connection",
        never "is the role also bound" -- a bound role must not make a bad
        PICK read as `role_unbound` (there IS a model assigned; it just
        isn't the one that was picked)."""
        _bind_comfyui()
        chat_only = ModelConnection.objects.create(
            name="chat model", engine="stubengine", endpoint="http://chat:9999",
            model_id="chat.gguf", capabilities=["chat"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            with pytest.raises(jobs.services.VisionUnavailable) as exc_info:
                jobs.run_generate(
                    {
                        "operation": "txt2img",
                        "params": dict(RAW),
                        "connection": str(chat_only.pk),
                    },
                    [],
                    make_job_ctx(),
                )

        assert exc_info.value.state == "connection_unavailable"
        assert exc_info.value.failure_kind == GenerationJob.FailureKind.CONNECTION_UNAVAILABLE

    def test_the_handler_runs_the_generation_on_the_picked_connection(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://picked:9999",
            model_id="picked.safetensors", capabilities=["image-generation"],
        )
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[])
        engine = StubEngine(generator=generator)
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            jobs.run_generate(
                {
                    "operation": "txt2img",
                    "params": dict(RAW),
                    "connection": str(connection.pk),
                },
                [],
                make_job_ctx(),
            )

        assert engine.built[0][0] == "picked.safetensors"


# --- summarize_generate ---------------------------------------------------


class TestSummarizeGenerate:
    def test_prompt_is_returned_unchanged_when_short(self):
        payload = {"operation": "txt2img", "params": {"prompt": "a lighthouse"}}
        assert jobs.summarize_generate(payload) == "a lighthouse"

    def test_long_prompt_is_truncated_to_120_chars(self):
        payload = {"operation": "txt2img", "params": {"prompt": "x" * 200}}

        summary = jobs.summarize_generate(payload)

        assert len(summary) == 120
        assert summary == ("x" * 119) + "…"

    def test_missing_prompt_falls_back_to_the_operations_label(self):
        payload = {"operation": "txt2img", "params": {}}
        assert jobs.summarize_generate(payload) == "Text to image"

    def test_unregistered_operation_falls_back_to_its_raw_key(self):
        payload = {"operation": "not-a-real-operation", "params": {}}
        assert jobs.summarize_generate(payload) == "not-a-real-operation"

    def test_an_edit_payload_summarizes_by_its_instruction(self):
        """Finding 6: `edit`'s schema carries `instruction`, never
        `prompt` -- a queued (not-yet-run) `edit` job used to fall all the
        way through to `summarize_generate`'s own bare
        `params.get("prompt")` and land on the operation's schema label
        ("Edit an image") instead of naming what the job actually asked
        for, the exact gap `GenerationJob.headline` was already fixed for
        (owner ruling 2026-08-24(b)). This must read the SAME logic, not a
        second copy of it."""
        payload = {"operation": "edit", "params": {"instruction": "put a red hat on the woman"}}
        assert jobs.summarize_generate(payload) == "put a red hat on the woman"

    def test_summarize_generate_matches_headline_for_every_field_combination(self):
        """Behavioural guard for "one place, no drift": whatever
        `GenerationJob.headline` would show a FINISHED job with these exact
        operation/params, `summarize_generate` must show the QUEUED
        placeholder for the same payload -- proven by calling both, not by
        asserting on a shared private helper's name."""
        from tools.vision.models import GenerationJob

        cases = [
            {"operation": "txt2img", "params": {"prompt": "a lighthouse"}},
            {"operation": "edit", "params": {"instruction": "make it blue"}},
            {"operation": "txt2img", "params": {}},
        ]
        for payload in cases:
            job = GenerationJob(operation=payload["operation"], params=payload["params"])
            assert jobs.summarize_generate(payload) == job.headline


# --- registration (tools/vision/apps.py::ready()) -----------------------


@pytest.mark.django_db
class TestJobKindRegistration:
    def test_kind_registered_with_priority_200_when_the_feature_is_on(self):
        with patch.dict(jobkinds_module._JOB_KINDS, {}, clear=False):
            jobkinds_module._JOB_KINDS.pop("vision.generate", None)
            with override_settings(FARABUNKER_FEATURES=frozenset({"vision"})):
                django_apps.get_app_config("vision").ready()
            kind = jobkinds_module.get_job_kind("vision.generate")

        assert kind.planner == "tools.vision.jobs.plan_generate"
        assert kind.handler == "tools.vision.jobs.run_generate"
        assert kind.summarizer == "tools.vision.jobs.summarize_generate"
        assert kind.default_priority == 200

    def test_kind_absent_when_the_feature_is_off(self):
        """Overrides FARABUNKER_FEATURES to an empty set to prove `ready()`
        skips registration -- this test itself never resolves a URL, but
        this FILE does (`reverse("vision-output-file", ...)` elsewhere), so
        the repo-wide hygiene sweep (`tools.rag.tests.test_flag_hygiene`)
        treats it as at-risk file-wide. Earn the narrower carve-out the
        documented way (`tools.vision.tests.test_views_create`'s own
        pattern): reload (and always restore) `config.urls` under the
        override, rather than leave the empty-set literal for the sweep to
        flag.
        """
        from config import urls as config_urls

        try:
            with patch.dict(jobkinds_module._JOB_KINDS, {}, clear=False):
                jobkinds_module._JOB_KINDS.pop("vision.generate", None)
                with override_settings(FARABUNKER_FEATURES=frozenset()):
                    importlib.reload(config_urls)
                    clear_url_caches()
                    django_apps.get_app_config("vision").ready()
                with pytest.raises(ValueError, match="vision.generate"):
                    jobkinds_module.get_job_kind("vision.generate")
        finally:
            importlib.reload(config_urls)
            clear_url_caches()

    def test_the_real_startup_registered_it(self):
        """The running app registers at import time -- proof the wiring is
        live, not just callable."""
        kind = jobkinds_module.get_job_kind("vision.generate")
        assert kind.default_priority == 200
