"""Unit tests for ComfyUIGenerator (spec §4.3, §6).

HTTP mocked at the `httpx` layer; the generator's real payload building,
state mapping, and `/view` fetching all run.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from models.contracts.engines import comfyui
from models.contracts.engines.base import GenerationRejected
from models.contracts.engines.comfyui import ComfyUIEngine
from models.contracts.operations import GenerationRequest
from tools.vision.tests._helpers import FakeComfyUI, history_error, history_success, reset_engine_caches

ENDPOINT = "http://comfy.local:8188"
REF = "1a2b3c"


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()

PARAMS = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": 1024, "height": 1024,
    "steps": 25, "cfg_scale": 7.0, "seed": 99, "sampler": "euler",
    "scheduler": "normal", "batch_size": 1,
}


def _request(client_ref: str = "job-uuid") -> GenerationRequest:
    return GenerationRequest(
        operation="txt2img", model_id="sdxl.safetensors", params=dict(PARAMS), client_ref=client_ref
    )


def _generator(config: dict | None = None):
    return ComfyUIEngine().build_image_generator("sdxl.safetensors", ENDPOINT, **(config or {}))


class TestSubmit:
    def test_posts_the_graph_and_returns_the_prompt_id_with_the_verbatim_payload(self):
        fake = FakeComfyUI(prompt_id="p-42")
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            engine_ref, payload = _generator().submit(_request())

        assert engine_ref == "p-42"
        assert payload["client_id"] == "job-uuid"
        assert payload["prompt"]["1"]["inputs"]["ckpt_name"] == "sdxl.safetensors"
        assert payload["prompt"]["5"]["inputs"]["seed"] == 99
        assert fake.submitted == [payload]

    def test_rejection_carries_comfyui_own_words(self):
        fake = FakeComfyUI(prompt_status=400)
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected) as excinfo:
                _generator().submit(_request())

        message = str(excinfo.value)
        assert "Prompt outputs failed validation" in message
        assert "not in ['sdxl.safetensors']" in message

    def test_missing_prompt_id_is_a_rejection_not_a_silent_success(self):
        fake = FakeComfyUI()
        fake.prompt_id = ""
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected, match="no prompt_id"):
                _generator().submit(_request())


class TestSubmitStampsTheMemoryBaseline:
    """`loaded_footprint` measures against the free memory ComfyUI reported
    just before the graph was queued, so `submit` is where that reading is
    taken -- there is nowhere later that still knows the "before"."""

    def test_the_baseline_is_read_before_the_prompt_is_posted(self):
        fake = FakeComfyUI(ram_free=40_000_000_000)
        order = []

        def ordered_get(url, params=None, timeout=None):
            order.append(("get", url))
            return fake.get(url, params=params, timeout=timeout)

        def ordered_post(url, json=None, files=None, data=None, timeout=None):
            order.append(("post", url))
            return fake.post(url, json=json, files=files, data=data, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", ordered_get), \
             patch("models.contracts.engines.comfyui.httpx.post", ordered_post):
            _generator().submit(_request())

        assert order[0] == ("get", f"{ENDPOINT}/system_stats")
        assert ("post", f"{ENDPOINT}/prompt") in order

    def test_a_refused_graph_stamps_no_baseline(self):
        """Nothing loaded, so there is nothing to measure against."""
        fake = FakeComfyUI(prompt_status=400)

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected):
                _generator().submit(_request())

        assert comfyui._RUN_MEMO == {}

    def test_an_unreadable_system_stats_never_breaks_a_submission(self):
        """The measurement is opportunistic; the generation is not."""
        fake = FakeComfyUI()

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("models.contracts.engines.comfyui.httpx.get", failing_get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            engine_ref, _payload = _generator().submit(_request())

        assert engine_ref == fake.prompt_id
        assert comfyui._RUN_MEMO == {}


class TestStatus:
    def test_running_when_the_ref_is_in_the_running_queue(self):
        fake = FakeComfyUI(queue_running=[[0, REF, {}, {}, []]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "running"

    def test_queued_when_the_ref_is_pending(self):
        fake = FakeComfyUI(queue_pending=[[1, REF, {}, {}, []]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "queued"

    def test_done_when_history_has_outputs(self):
        fake = FakeComfyUI(history=history_success(REF))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "done"

    def test_failed_carries_the_first_execution_error_message(self):
        fake = FakeComfyUI(history=history_error(REF, "Allocation on device"))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            state = _generator().status(REF)

        assert state.state == "failed"
        assert "Allocation on device" in state.error

    def test_lost_when_neither_history_nor_queue_knows_the_ref(self):
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "lost"

    def test_success_with_no_outputs_is_a_failure_not_a_silent_done(self):
        fake = FakeComfyUI(
            history={REF: {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": {}}}
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            state = _generator().status(REF)

        assert state.state == "failed"
        assert "no output images" in state.error

    def test_success_with_a_node_that_ran_but_saved_nothing_is_also_a_failure(self):
        fake = FakeComfyUI(
            history={
                REF: {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {"7": {"images": []}},
                }
            }
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            state = _generator().status(REF)

        assert state.state == "failed"
        assert "no output images" in state.error

    def test_history_entry_without_outputs_falls_back_to_the_queue(self):
        fake = FakeComfyUI(
            history={REF: {"status": {"status_str": "success", "completed": False, "messages": []}}},
            queue_running=[[0, REF, {}, {}, []]],
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "running"

    def test_a_malformed_queue_entry_never_raises(self):
        fake = FakeComfyUI(queue_pending=[None, [], [1]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).state == "lost"

    def test_a_pending_job_reports_its_position_in_pop_order(self):
        """ComfyUI's /queue hands back its own heap list, which is NOT in
        execution order -- but every entry's first element is the monotonic
        `number` the heap is keyed on, so sorting by it is the real order.
        REF is queued third by number while sitting first in the dump."""
        fake = FakeComfyUI(
            queue_pending=[
                [9, REF, {}, {}, []],
                [3, "other-a", {}, {}, []],
                [5, "other-b", {}, {}, []],
            ]
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            state = _generator().status(REF)

        assert state.state == "queued"
        assert state.queue_position == 3

    def test_a_running_job_has_no_position(self):
        fake = FakeComfyUI(queue_running=[[0, REF, {}, {}, []]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().status(REF).queue_position is None

    def test_a_malformed_pending_entry_never_breaks_the_poll(self):
        fake = FakeComfyUI(queue_pending=[None, [], [1], ["x", REF, {}, {}, []]])
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            state = _generator().status(REF)

        assert state.state == "queued"
        assert state.queue_position == 1


class TestFetchOutputs:
    def test_every_output_image_is_downloaded_with_its_media_type(self):
        fake = FakeComfyUI(
            history=history_success(REF, ("job_00001_.png", "job_00002_.png")),
            images={"job_00001_.png": b"PNG-ONE", "job_00002_.png": b"PNG-TWO"},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            outputs = _generator().fetch_outputs(REF)

        assert outputs == [
            ("job_00001_.png", b"PNG-ONE", "image/png"),
            ("job_00002_.png", b"PNG-TWO", "image/png"),
        ]
        assert fake.view_calls[0] == {"filename": "job_00001_.png", "subfolder": "", "type": "output"}

    def test_temp_previews_are_skipped(self):
        history = history_success(REF)
        history[REF]["outputs"]["7"]["images"].append(
            {"filename": "preview.png", "subfolder": "", "type": "temp"}
        )
        fake = FakeComfyUI(history=history, images={"job_00001_.png": b"PNG"})
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            outputs = _generator().fetch_outputs(REF)

        assert [name for name, _, _ in outputs] == ["job_00001_.png"]

    def test_no_history_yields_nothing(self):
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert _generator().fetch_outputs(REF) == []

    def test_outputs_are_ordered_by_node_id(self):
        fake = FakeComfyUI(
            history={
                REF: {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {
                        "9": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]},
                        "7": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
                    },
                }
            },
            images={"a.png": b"A", "b.png": b"B"},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            assert [name for name, _, _ in _generator().fetch_outputs(REF)] == ["a.png", "b.png"]

    def _history_with_n_outputs(self, count: int) -> dict:
        images = [
            {"filename": f"{i}.png", "subfolder": "", "type": "output"} for i in range(count)
        ]
        return {
            REF: {
                "status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {"7": {"images": images}},
            }
        }

    def test_exactly_the_output_cap_is_allowed(self):
        """S11 (review round 1, finding 3): the cap refuses BEYOND
        `MAX_OUTPUTS_PER_JOB`, not AT it."""
        count = comfyui.MAX_OUTPUTS_PER_JOB
        images = {f"{i}.png": f"content-{i}".encode() for i in range(count)}
        fake = FakeComfyUI(history=self._history_with_n_outputs(count), images=images)
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            outputs = _generator().fetch_outputs(REF)

        assert len(outputs) == count

    def test_more_than_the_output_cap_is_refused(self):
        """A malformed or hostile history entry naming more than
        `MAX_OUTPUTS_PER_JOB` output images is refused with `httpx.
        HTTPError` -- the same exception family a byte-budget overrun
        uses -- rather than fetching an unbounded number of `/view`
        bodies."""
        count = comfyui.MAX_OUTPUTS_PER_JOB + 1
        images = {f"{i}.png": f"content-{i}".encode() for i in range(count)}
        fake = FakeComfyUI(history=self._history_with_n_outputs(count), images=images)
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            with pytest.raises(httpx.HTTPError):
                _generator().fetch_outputs(REF)


class TestInputTransfer:
    """`submit` moves every file input to the engine before building the
    graph: a path inside the platform's container means nothing to a
    ComfyUI running natively on the host (ADR 0012 D1)."""

    @staticmethod
    def _template(request, model_id, config, inputs):
        """A stand-in img2img template: the only thing under test is that
        the engine-side reference reaches the graph."""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": inputs["init_image"]}},
            "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": request.client_ref, "images": ["1", 0]}},
        }

    def _img2img_request(self, tmp_path, client_ref="job-uuid"):
        source = tmp_path / "beach.png"
        source.write_bytes(b"PNGBYTES")
        return GenerationRequest(
            operation="img2img",
            model_id="sdxl.safetensors",
            params={"prompt": "a lighthouse", "denoise": 0.6},
            inputs={"init_image": source},
            client_ref=client_ref,
        )

    def _registered_template(self):
        from models.contracts.engines import comfyui_workflows

        return patch.dict(comfyui_workflows._TEMPLATES, {("", "img2img"): self._template})

    def test_the_file_is_uploaded_and_its_engine_reference_reaches_the_graph(self, tmp_path):
        fake = FakeComfyUI()
        request = self._img2img_request(tmp_path)
        with self._registered_template(), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _generator().submit(request)

        assert len(fake.uploads) == 1
        upload = fake.uploads[0]
        assert upload["field"] == "image"
        assert upload["filename"] == "beach.png"
        assert upload["media_type"] == "image/png"
        assert upload["content"] == b"PNGBYTES"
        assert upload["data"] == {"subfolder": "job-uuid", "type": "input", "overwrite": "true"}
        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "job-uuid/beach.png"

    def test_an_engine_reported_name_wins_over_the_local_one(self, tmp_path):
        """ComfyUI renames on collision. The reference must be ITS name."""
        fake = FakeComfyUI(upload_name="beach (1).png")
        with self._registered_template(), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "job-uuid/beach (1).png"

    def test_an_engine_that_reports_no_subfolder_yields_a_bare_name(self, tmp_path):
        fake = FakeComfyUI(upload_subfolder="")
        with self._registered_template(), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "beach.png"

    def test_a_refused_upload_is_a_rejection_carrying_comfyui_own_words(self, tmp_path):
        fake = FakeComfyUI(upload_status=400, upload_text="image file is required")
        with self._registered_template(), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected, match="image file is required"):
                _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted == []

    def test_a_transport_failure_during_upload_propagates_untouched(self, tmp_path):
        """Not a rejection: `ComfyUIGenerator.submit` itself must let the
        transport exception through unrecognized, rather than translating it
        into a `GenerationRejected`. Per ADR 0012's two-failure-mode ruling,
        the SERVICE layer treats this as final -- `submit_job`'s generic
        `except Exception` branch fails the job immediately with the
        exception text, because the job was never accepted anywhere; only
        `refresh_job`, polling an already-submitted job, forgives a
        transport failure as transient."""

        fake = FakeComfyUI()

        def refuse(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        with self._registered_template(), \
             patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", refuse):
            with pytest.raises(httpx.ConnectError):
                _generator().submit(self._img2img_request(tmp_path))

    def test_txt2img_uploads_nothing(self):
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _generator().submit(_request())

        assert fake.uploads == []
        assert len(fake.submitted) == 1


class TestSubmitPicksTheGraphForTheConnectionsDeclaredFamily:
    """`submit` must hand `get_template` the SAME family
    `supported_operations` used to offer this operation in the first place
    (`ModelConnection.config["family"]`, ADR 0012 D-EDIT-2): a connection
    the console let an operator pick `edit` for must actually get the
    family's edit graph, not the empty-family checkpoint template."""

    def _edit_request(self, tmp_path, client_ref="job-uuid"):
        source = tmp_path / "beach.png"
        source.write_bytes(b"PNGBYTES")
        return GenerationRequest(
            operation="edit",
            model_id="flux2-dev.safetensors",
            params={"instruction": "add a red hat", "guidance": 4.0, "steps": 20, "seed": 7},
            inputs={"init_image": source},
            client_ref=client_ref,
        )

    def test_a_flux2_connection_submits_the_flux2_edit_graph(self, tmp_path):
        fake = FakeComfyUI()
        config = {"family": "flux2", "text_encoder": "enc.gguf", "vae": "vae.safetensors"}
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _generator(config).submit(self._edit_request(tmp_path))

        class_types = {node["class_type"] for node in fake.submitted[0]["prompt"].values()}
        assert "EmptyFlux2LatentImage" in class_types
        assert "Flux2Scheduler" in class_types

    def test_a_json_null_family_behaves_as_no_family_declared(self):
        """Finding 9: `config={"family": None}` is a live possibility (a
        JSON `null` stored in the connection's config, not merely an
        absent key -- `dict(config or {})` in `ComfyUIGenerator.__init__`
        keeps the key when it's present-and-None) and must normalize to
        `""`, the single-file-checkpoint case, exactly like an absent key
        does -- not propagate a bare `None` into `get_template`, which
        raises `ValueError` on anything but a real string key."""
        fake = FakeComfyUI(prompt_id="p-1")
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _engine_ref, payload = _generator({"family": None}).submit(_request())

        assert payload["prompt"]["1"]["inputs"]["ckpt_name"] == "sdxl.safetensors"

    def test_a_family_less_connection_still_gets_the_checkpoint_txt2img_graph(self):
        """The existing, family-less path -- reused rather than re-asserted
        elsewhere, since `TestSubmit` above already pins the checkpoint
        graph's node shape for a connection with no declared family."""
        fake = FakeComfyUI(prompt_id="p-42")
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            _engine_ref, payload = _generator().submit(_request())

        assert payload["prompt"]["1"]["inputs"]["ckpt_name"] == "sdxl.safetensors"


class TestOutputOrdering:
    """B4: node ids are strings, so `"10" < "2"` -- inert while a graph has
    one output node, wrong the moment a graph has ten nodes and two."""

    def _history_with_two_output_nodes(self, ref="p-1"):
        return {
            ref: {
                "status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {
                    "10": {"images": [{"filename": "second.png", "subfolder": "", "type": "output"}]},
                    "2": {"images": [{"filename": "first.png", "subfolder": "", "type": "output"}]},
                },
            }
        }

    def test_outputs_come_back_in_numeric_node_order(self):
        fake = FakeComfyUI(
            history=self._history_with_two_output_nodes(),
            images={"first.png": b"one", "second.png": b"two"},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            outputs = _generator().fetch_outputs("p-1")

        assert [name for name, _content, _media in outputs] == ["first.png", "second.png"]

    def test_a_non_numeric_node_id_sorts_last_instead_of_raising(self):
        """A third-party node pack may use a non-numeric id. Ordering is a
        display nicety; crashing a finished job over it is not."""
        history = self._history_with_two_output_nodes()
        history["p-1"]["outputs"]["save_final"] = {
            "images": [{"filename": "third.png", "subfolder": "", "type": "output"}]
        }
        fake = FakeComfyUI(
            history=history,
            images={"first.png": b"one", "second.png": b"two", "third.png": b"three"},
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.get_bounded", fake.get_bounded):
            outputs = _generator().fetch_outputs("p-1")

        assert [name for name, _content, _media in outputs] == ["first.png", "second.png", "third.png"]
