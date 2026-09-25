"""The vision tools (spec section 5). VISION-OWNED.

Every runner calls the SAME service function the page calls -- these
tests patch that function and assert the call, which is the gate the spec
names for P1.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents.contracts.tools import all_tools, get_tool, grantable_tools, validate_tool_args
from agents.contracts.toolschema import openai_tool_dict
from models.contracts.jobkinds import resolve_dotted_path
from models.contracts.operations import ParamError
# `isolated_tool_registry` imported BY NAME (no conftest.py in this repo --
# pytest discovers a fixture present in a test module's namespace, imported
# or defined locally) and activated for every test below via `pytestmark`:
# `_TOOLS` is a module-global registry, and most tests here register,
# re-register, or read from it, directly or via `run_generate`/`VisionConfig
# .ready()`.
from tools.vision.tests._helpers import isolated_tool_registry, make_tool_ctx  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


def _ready_preflight():
    """A `PreflightResult` in the `ready` state -- for tests that need
    `run_generate`'s OWN preflight (Task E: it runs exactly once, before
    the engine-param fill and the submit) to succeed, without ALSO
    exercising its `ToolRefused` refusal, which
    `TestVisionGenerateRunner::test_an_unbound_preflight_refuses_before_submit_with_the_platforms_own_copy`
    and its unreachable-engine sibling cover on their own. Without this,
    the real `services.preflight` -- no role bound in the test DB --
    reports `unbound`, and `run_generate` refuses before `_fill_engine_
    params`/`submit_job` (mocked in these tests) is ever reached."""
    from models.contracts.bindings import ResolvedModel
    from tools.vision import services

    resolved = ResolvedModel(
        engine="stubengine", model_id="stub.safetensors", endpoint="http://stub:9999",
    )
    return services.PreflightResult("ready", resolved, "")


class TestRegistration:
    def test_both_are_registered_when_the_feature_is_on(self):
        keys = {spec.key for spec in all_tools()}
        assert {"vision.operations", "vision.generate"} <= keys

    def test_every_runner_resolves(self):
        for key in ("vision.operations", "vision.generate"):
            assert callable(resolve_dotted_path(get_tool(key).runner))

    def test_neither_mutates_and_both_are_grantable(self):
        """Creating NEW work product -- a generated image, a queued job --
        is not `mutates`. That word means changing state that already
        exists."""
        grantable = {spec.key for spec in grantable_tools()}
        for key in ("vision.operations", "vision.generate"):
            assert get_tool(key).mutates is False
            assert key in grantable

    def test_declared_roles(self):
        assert get_tool("vision.operations").roles == ()
        assert get_tool("vision.generate").roles == ("vision.generate",)

    def test_neither_declares_a_describer(self):
        """Ruling R3 -- the vision describer is struck from P1 entirely."""
        for key in ("vision.operations", "vision.generate"):
            assert get_tool(key).describer == ""

    def test_with_the_feature_off_no_vision_tool_is_registered(self):
        """One gate, one behaviour: with the flag off there is no vision
        role, no operations, no job kind, and now no tools.

        This test overrides FARABUNKER_FEATURES but makes NO HTTP request
        and performs NO URL reverse lookup, which is the documented escape
        from the vision-flag rule (tools/rag/tests/_helpers.py:9-38) -- it
        calls `ready()` directly. (Worded as "URL reverse lookup" rather
        than naming Django's own URL-resolution function directly:
        `tools/rag/tests/test_flag_hygiene.py`'s static sweep flags that
        literal call token ANYWHERE in a file that also overrides
        `FARABUNKER_FEATURES` without `"vision"` -- file-scoped, not
        per-test, by that sweep's own design.)
        """
        from django.test import override_settings

        from agents.contracts import tools as tools_module
        from tools.vision.apps import VisionConfig

        # `isolated_tool_registry` (module-level `pytestmark` above) has
        # already snapshotted `_TOOLS` for restoration at teardown; this
        # test only needs to clear it before calling `ready()` directly.
        tools_module._TOOLS.clear()
        with override_settings(FARABUNKER_FEATURES=frozenset()):
            VisionConfig.ready(MagicMock(spec=VisionConfig))
        assert tools_module._TOOLS == {}


class TestGenerateSpecShape:
    def test_operation_choices_are_exactly_the_registered_operations(self):
        """Derived at REGISTRATION time, after the five
        register_operation calls -- a module constant would be evaluated
        at import time and ship an empty choices tuple."""
        from models.contracts.operations import all_operations

        param = next(p for p in get_tool("vision.generate").params if p.key == "operation")
        assert set(param.choices) == {op.key for op in all_operations()}
        assert param.required is True

    def test_image_is_a_text_param_never_a_file_one(self):
        """A tool call is JSON (ADR 0012:140), so it cannot carry an
        upload object. An image input is an artifact REFERENCE."""
        param = next(p for p in get_tool("vision.generate").params if p.key == "image")
        assert param.kind == "text"

    def test_the_full_param_set(self):
        """The COMPUTED union (ruling, superseding the brief's original
        literal list -- that list did not match any real operation's own
        param keys, which meant `run_generate` could not submit a single
        registered operation). Registration order is txt2img, img2img,
        inpaint, upscale, edit; a key is placed at its FIRST operation's
        declaration, `image`/`mask_image`/`reference_image` (the file
        params, translated) trail at the end."""
        assert [p.key for p in get_tool("vision.generate").params] == [
            "operation", "prompt", "negative_prompt", "width", "height",
            "steps", "cfg_scale", "seed", "sampler", "scheduler", "batch_size",
            "loras", "lora_strength", "denoise", "mask_grow", "upscale_model",
            "instruction", "guidance", "image", "mask_image", "reference_image",
        ]

    def test_the_union_covers_every_non_file_param_key_of_every_operation(self):
        """The bug this ruling fixes: the original literal param list
        (`prompt`, `cfg`, ...) did not match what any real `Operation`
        declares (`cfg_scale`, not `cfg`), so `submit_job`'s own
        `validate_params` rejected every real submission. Pinning this
        against `all_operations()` directly is what would have caught
        it."""
        from models.contracts.operations import all_operations

        spec_keys = {p.key for p in get_tool("vision.generate").params}
        for operation in all_operations():
            for param in operation.params:
                if param.kind == "file":
                    continue
                assert param.key in spec_keys, (operation.key, param.key)

    def test_cfg_scale_and_guidance_are_both_present_today(self):
        spec_keys = {p.key for p in get_tool("vision.generate").params}
        assert {"cfg_scale", "guidance"} <= spec_keys
        assert "cfg" not in spec_keys

    def test_only_operation_is_required_at_the_union_level(self):
        """Every other param's `required` is real but LOCAL to one
        operation -- `prompt` is required for `txt2img`, meaningless for
        `upscale` -- and a union schema cannot honestly project one
        operation's requirement onto every call. The real enforcement is
        `submit_job`'s own `validate_params`, downstream of narrowing,
        where the picked operation is known."""
        for param in get_tool("vision.generate").params:
            assert param.required == (param.key == "operation")

    def test_a_second_file_param_becomes_a_text_reference_under_its_own_key(self):
        """`mask_image` (inpaint) and `reference_image` (edit) are NOT the
        operation's first file param, so they do not collapse onto the
        shared `image` key -- a caller supplies them IN ADDITION to
        `image`, not instead of it."""
        for key in ("mask_image", "reference_image"):
            param = next(p for p in get_tool("vision.generate").params if p.key == key)
            assert param.kind == "text"
            assert param.required is False

    def test_sampler_and_scheduler_are_text_not_choice(self):
        """`validate_params` requires a non-blank value for ANY `"choice"`
        param regardless of `required` (operations.py:317-319) -- the same
        reason `tools/rag/tools.py:35-41`'s own `_CATEGORY` is `"text"`."""
        for key in ("sampler", "scheduler"):
            param = next(p for p in get_tool("vision.generate").params if p.key == key)
            assert param.kind == "text"

    def test_the_asset_params_pass_through_as_declared(self):
        """`"asset"` is already in `TOOL_PARAM_KINDS` -- `loras` (multiple)
        and `upscale_model` (single) need no translation."""
        loras = next(p for p in get_tool("vision.generate").params if p.key == "loras")
        upscale_model = next(
            p for p in get_tool("vision.generate").params if p.key == "upscale_model"
        )
        assert loras.kind == "asset" and loras.multiple is True
        assert upscale_model.kind == "asset" and upscale_model.multiple is False

    def test_it_renders_to_a_valid_llm_schema_with_an_operation_enum(self):
        rendered = openai_tool_dict(get_tool("vision.generate"))
        assert json.loads(json.dumps(rendered)) == rendered
        props = rendered["function"]["parameters"]["properties"]
        assert set(props["operation"]["enum"])
        assert rendered["function"]["parameters"]["required"] == ["operation"]
        assert rendered["function"]["name"] == "vision__generate"

    def test_every_param_carries_a_description(self):
        """`description` is the entire prompt surface a model gets for an
        argument (ruling R2 half one)."""
        for param in get_tool("vision.generate").params:
            assert param.description


@pytest.mark.django_db
class TestVisionOperationsRunner:
    def test_it_calls_operation_catalog_exactly_once_with_no_picked_model(self):
        """`operation_catalog` runs ONE preflight for the whole catalog
        (services.py:319-324); calling it per-operation would pay a
        blocking health round trip each time. `resolved=None` means "the
        role binding answers", which is the only model a tool has."""
        from tools.vision.tools import run_operations

        with patch("tools.vision.services.operation_catalog", return_value=[]) as catalog:
            run_operations({}, make_tool_ctx())

        assert catalog.call_count == 1
        positional, keyword = catalog.call_args
        assert positional in ((), (None,))
        assert keyword.get("resolved") is None

    def test_the_catalog_lands_in_data_verbatim(self):
        from tools.vision.tools import run_operations

        catalog = [{"key": "txt2img", "label": "Text to image", "params": []}]
        with patch("tools.vision.services.operation_catalog", return_value=catalog):
            result = run_operations({}, make_tool_ctx())
        assert result.data["operations"] == catalog
        assert json.loads(json.dumps(result.data)) == result.data
        assert result.artifacts == ()

    def test_the_text_names_each_operation(self):
        from tools.vision.tools import run_operations

        catalog = [
            {"key": "txt2img", "label": "Text to image", "params": []},
            {"key": "upscale", "label": "Upscale", "params": []},
        ]
        with patch("tools.vision.services.operation_catalog", return_value=catalog):
            text = run_operations({}, make_tool_ctx()).text
        assert "txt2img" in text and "upscale" in text

    def test_an_unsupported_operation_carries_the_reason_on_its_own_line(self):
        """The catalog now lists every registered operation, marked
        `"supported"` or not (Task 2's reversal of the earlier "never
        tell a tool about a mode it cannot perform" rule). An LLM reads
        `.text`, not `.data` -- so the reason has to be IN the text, on
        the unsupported operation's own line only."""
        from tools.vision.tools import run_operations

        catalog = [
            {
                "key": "txt2img", "label": "Text to image", "params": [],
                "supported": True, "unsupported_reason": "",
            },
            {
                "key": "inpaint", "label": "Inpaint", "params": [],
                "supported": False, "unsupported_reason": "no mask input on this model",
            },
        ]
        with patch("tools.vision.services.operation_catalog", return_value=catalog):
            text = run_operations({}, make_tool_ctx()).text

        lines = text.splitlines()
        txt2img_line = next(line for line in lines if line.startswith("txt2img"))
        inpaint_line = next(line for line in lines if line.startswith("inpaint"))
        assert "not runnable here" not in txt2img_line
        assert "not runnable here: no mask input on this model" in inpaint_line

    def test_an_unexpected_argument_is_rejected(self):
        from tools.vision.tools import run_operations

        with pytest.raises(ParamError):
            run_operations({"operation": "txt2img"}, make_tool_ctx())


@pytest.mark.django_db
class TestVisionGenerateRunner:
    def test_it_calls_submit_job_then_wait_for(self):
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job) as wait, \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "a thing"}, make_tool_ctx())

        assert submit.call_args[0][0] == "txt2img"
        assert submit.call_args[0][1]["prompt"] == "a thing"
        assert wait.call_args[0][0] is job

    def test_preflight_runs_exactly_once(self):
        """Task E: `_fill_engine_params` used to preflight for itself
        (whenever an operation had a blank engine-owned param) and
        `submit_job` preflighted again internally -- two calls, one role
        resolved from the database twice, for the same generation.
        `run_generate` now preflights ONCE, itself, and threads the
        result to both. `submit_job` is mocked out here (its OWN internal
        preflight is `services`' contract, unchanged and out of this
        item's scope) so the count below is purely `tools.py`'s own
        call -- `txt2img` is picked because it declares blank
        `sampler`/`scheduler` (`SAMPLING_PARAMS`), the exact case that
        used to trigger the second call."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch(
            "tools.vision.services.preflight", return_value=_ready_preflight()
        ) as preflight, \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "a thing"}, make_tool_ctx())

        assert preflight.call_count == 1

    def test_it_drops_an_argument_the_picked_operation_does_not_declare_AND_SAYS_SO(self):
        """The tool's schema is the union across operations; `submit_job`
        validates against the PICKED one and rejects an undeclared key.
        So the runner narrows -- but never silently."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            result = run_generate(
                {"operation": "upscale", "prompt": "x", "negative_prompt": "y"},
                make_tool_ctx(),
            )

        submitted = set(submit.call_args[0][1])
        from models.contracts.operations import get_operation

        declared = {p.key for p in get_operation("upscale").params}
        assert submitted <= declared
        assert "negative_prompt" in result.text  # named, not dropped in silence

    def test_an_image_reference_resolves_to_the_operations_first_file_param(self):
        """`file_params()` returns them in DECLARATION order, and the
        "use this image here" link already pre-fills the first for
        exactly this reason (operations.py:103-111).

        `resolve_inputs` is asked for exactly the keys the picked
        operation names, so it always answers under those SAME keys --
        its return value is handed to `submit_job(files=...)` unchanged,
        no remapping.
        """
        from models.contracts.operations import get_operation
        from tools.vision.tools import run_generate

        job = MagicMock()
        first_file_param = get_operation("img2img").file_params()[0].key
        with patch(
            "tools.vision.services.resolve_inputs",
            return_value={first_file_param: MagicMock()},
        ) as resolve, \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate(
                {"operation": "img2img", "prompt": "x", "image": "output:12"},
                make_tool_ctx(),
            )

        assert resolve.call_args[0][1] == {first_file_param: "output:12"}
        assert first_file_param in submit.call_args[1]["files"]

    def test_a_second_file_key_resolves_alongside_image_for_the_same_operation(self):
        """`inpaint` declares TWO file params -- `init_image` (first,
        answered by `image`) and `mask_image` (second, its own key). Both
        must reach ONE `resolve_inputs` call and ONE `files=` dict, never
        two separate resolutions."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch(
            "tools.vision.services.resolve_inputs",
            return_value={"init_image": MagicMock(), "mask_image": MagicMock()},
        ) as resolve, \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate(
                {
                    "operation": "inpaint", "prompt": "x",
                    "image": "output:1", "mask_image": "input:9",
                },
                make_tool_ctx(),
            )

        assert resolve.call_count == 1
        assert resolve.call_args[0][1] == {"init_image": "output:1", "mask_image": "input:9"}
        assert {"init_image", "mask_image"} <= set(submit.call_args[1]["files"])

    def test_a_file_key_belonging_to_a_different_operation_is_dropped_and_announced(self):
        """`mask_image` is `inpaint`'s own key and `guidance` is `edit`'s
        own -- neither means anything to `txt2img`, so both are narrowed
        away exactly like any other undeclared argument, announced, not
        silently ignored. `image` itself is dropped too: txt2img declares
        no file param at all."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("tools.vision.services.resolve_inputs") as resolve, \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            result = run_generate(
                {
                    "operation": "txt2img", "prompt": "x",
                    "image": "output:1", "mask_image": "input:9", "guidance": "5",
                },
                make_tool_ctx(),
            )

        resolve.assert_not_called()
        assert "files" not in submit.call_args[1] or submit.call_args[1]["files"] is None
        for name in ("image", "mask_image", "guidance"):
            assert name in result.text

    def test_a_malformed_image_reference_is_refused_with_the_shape(self):
        from tools.vision.tools import run_generate

        with pytest.raises(ValueError) as excinfo:
            run_generate(
                {"operation": "img2img", "prompt": "x", "image": "/etc/passwd"},
                make_tool_ctx(),
            )
        assert "output:<id>" in str(excinfo.value)

    def test_a_document_reference_reaches_resolve_inputs_for_a_generation(self):
        """RE-PINNED (chat image artifacts, 2026-09-16). This test used
        to assert the opposite -- `document:` was refused outright by
        `parse_input_reference`. The whole point of this change is that
        an image attached to the conversation is a generation input like
        any other, so what is pinned now is that the reference reaches
        `resolve_inputs` UNDER THE OPERATION'S OWN FILE KEY, unchanged."""
        from tools.vision.tools import run_generate

        with patch("tools.vision.services.resolve_inputs",
                   return_value={"init_image": MagicMock()}) as resolve, \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.operation_states", return_value=()), \
             patch("tools.vision.services.submit_job", return_value=MagicMock()), \
             patch("tools.vision.services.wait_for", side_effect=lambda j, **k: j), \
             patch("tools.vision.services.job_json",
                   return_value={"id": "x", "status": "succeeded", "outputs": []}):
            run_generate(
                {"operation": "img2img", "prompt": "x", "image": "document:5"},
                make_tool_ctx(),
            )
        assert resolve.call_args[0][1] == {"init_image": "document:5"}

    def test_progress_is_reported_per_poll_with_no_fabricated_total(self):
        """ADR 0013:410-417 -- this platform never fabricates a
        denominator."""
        from tools.vision.tools import run_generate

        reported = []
        ctx = make_tool_ctx()
        object.__setattr__(ctx.job, "_report", reported.append)

        job = MagicMock()

        def fake_wait(j, timeout, interval=1.0, on_poll=None):
            on_poll(j, 3.0)
            return j

        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", side_effect=fake_wait), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "x"}, ctx)

        assert reported == [
            {"done": 3, "total": None, "unit": "seconds", "label": "generating"}
        ]

    def test_data_is_job_json_verbatim_and_exposes_urls_never_paths(self):
        from tools.vision.tools import run_generate

        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [{"id": 7, "url": "/vision/outputs/7/file/"}],
        }
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert result.data == payload
        assert json.loads(json.dumps(result.data)) == result.data
        assert result.artifacts == ("output:7",)
        serialised = json.dumps(result.data)
        assert "/data/" not in serialised and ".safetensors" not in serialised

    def test_the_text_names_the_single_output_ref_for_a_followup_edit(self):
        """Item C (2026-09-03 fix batch): the ref must be IN the text, not
        only in `.artifacts` -- a model replaying the turn never sees
        `.artifacts`, so a follow-up edit step could not name the image.
        Prose only, exact `output:<id>` spelling -- no bracketed
        `[artifacts: ...]` block; the chat runtime appends its own such
        line to a replayed tool turn, and a second one here would read as
        duplicate noise (peer binding, chat column)."""
        from tools.vision.tools import run_generate

        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [{"id": 36, "url": "/vision/outputs/36/file/"}],
        }
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert "output:36" in result.text
        assert "[artifacts:" not in result.text

    def test_the_text_names_every_output_ref_for_a_batch(self):
        from tools.vision.tools import run_generate

        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [
                {"id": 36, "url": "/vision/outputs/36/file/"},
                {"id": 37, "url": "/vision/outputs/37/file/"},
            ],
        }
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert "output:36" in result.text
        assert "output:37" in result.text
        assert "[artifacts:" not in result.text

    def test_no_output_refs_line_when_there_are_no_outputs_yet(self):
        from tools.vision.tools import run_generate

        payload = {"id": "abc", "status": "queued", "outputs": []}
        job = MagicMock()
        job.is_terminal = False
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert "output:" not in result.text

    def test_it_calls_describe_if_ready_after_waiting_before_reading_the_result(self):
        """WIRING (vision-describes-its-own-output task, ruling 3): this
        runner now calls the SAME `services.describe_if_ready` gate
        `tools.vision.jobs.run_generate` (the queued job kind) calls --
        one implementation, two callers -- with the job `wait_for` gave
        back, and STRICTLY AFTER `wait_for` returns, BEFORE `job_json` is
        read (so a description it just wrote is what `job_json` sees)."""
        from tools.vision.tools import run_generate

        order = []
        job = MagicMock()
        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [{"id": 36, "url": "/vision/outputs/36/file/"}],
        }
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch(
                 "tools.vision.services.wait_for",
                 side_effect=lambda *a, **k: order.append("wait_for") or job,
             ), \
             patch(
                 "tools.vision.services.describe_if_ready",
                 side_effect=lambda j: order.append("describe_if_ready"),
             ) as describe_mock, \
             patch(
                 "tools.vision.services.job_json",
                 side_effect=lambda j: order.append("job_json") or payload,
             ):
            run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        describe_mock.assert_called_once_with(job)
        assert order == ["wait_for", "describe_if_ready", "job_json"]

    def test_a_stored_description_is_appended_verbatim(self):
        """READS the field, never templates it: whatever `describe_if_
        ready` (patched away here, real behaviour covered in `test_
        services.py::TestDescribeIfReady`/`TestDescribeOutput`) leaves on
        the job, `job_json` hands back under `"description"`, and this
        runner appends verbatim, alongside the untouched output-id
        sentence."""
        from tools.vision.tools import run_generate

        description = (
            "The platform's own description of the generated image "
            "(not the request that produced it): a lighthouse at dusk."
        )
        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [{"id": 36, "url": "/vision/outputs/36/file/"}],
            "description": description,
        }
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.describe_if_ready"), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert description in result.text
        # The output-id sentence's own wording is untouched -- other
        # tests already pin it byte-for-byte; this one only proves the
        # NEW line does not disturb it.
        assert "output:36" in result.text
        assert "— reference this to edit." in result.text

    def test_no_description_key_appends_nothing(self):
        """A `describe_if_ready` call that left nothing (an unbound
        `rag.extract` role, or nothing yet to describe) means `payload`
        carries no `description` key -- `payload.get("description")`
        must degrade to nothing added, never a raise on a missing key."""
        from tools.vision.tools import run_generate

        payload = {
            "id": "abc", "status": "succeeded",
            "outputs": [{"id": 36, "url": "/vision/outputs/36/file/"}],
        }
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.describe_if_ready"), \
             patch("tools.vision.services.job_json", return_value=payload):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert result.text == "Generation abc finished as succeeded. Outputs: output:36 — reference this to edit."

    def test_an_unbound_role_surfaces_the_platforms_own_copy(self):
        """`VisionUnavailable` carries the message
        `services.role_unbound_message()` already writes for the page. A
        tool must not invent a second sentence for the same state."""
        from tools.vision import services
        from tools.vision.tools import run_generate

        from agents.contracts.tools import ToolRefused

        # VisionUnavailable is a RuntimeError (services.py:99), which is
        # neither of the two classes section 10.1 defines. The runner must
        # translate it to ToolRefused -- the no-retry class -- because
        # nothing the model can say will bind a role.
        assert issubclass(services.VisionUnavailable, RuntimeError)
        assert not issubclass(services.VisionUnavailable, ValueError)

        with patch("tools.vision.services.submit_job",
                   side_effect=services.VisionUnavailable("unbound", services.role_unbound_message())):
            with pytest.raises(ToolRefused) as excinfo:
                run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        # The platform's OWN copy, verbatim. A tool must not invent a
        # second sentence for a state the page already has words for.
        assert services.role_unbound_message() in str(excinfo.value)

    def test_an_unbound_preflight_refuses_before_submit_with_the_platforms_own_copy(self):
        """The defect this AMEND fixes: `_fill_engine_params` used to call
        `services.preflight` and ignore `check.ready` -- an unbound role
        left `live_defaults`/`live_options` answering `{}`, so a blank
        `sampler`/`scheduler` (txt2img declares both, via
        `SAMPLING_PARAMS`) stayed blank and `submit_job`'s own
        `validate_params` raised a bogus, RETRYABLE `ParamError` ("Sampler
        must be chosen") for a failure no retry could ever fix.

        `_fill_engine_params` must now raise `ToolRefused` itself, with
        the platform's own copy, before `submit_job` is ever reached."""
        from tools.vision import services
        from tools.vision.tools import run_generate

        from agents.contracts.tools import ToolRefused

        unbound = services.PreflightResult("unbound", None, services.role_unbound_message())
        with patch("tools.vision.services.preflight", return_value=unbound), \
             patch("tools.vision.services.submit_job") as submit:
            with pytest.raises(ToolRefused) as excinfo:
                run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert services.role_unbound_message() in str(excinfo.value)
        submit.assert_not_called()

    def test_an_unbound_role_wins_over_a_missing_required_param(self):
        """The accepted ordering trade (Task E review): `submit_job`'s own
        `validate_params` used to run BEFORE any preflight -- schema
        validation first, so an invalid submission got the 400-shaped,
        RETRYABLE `ParamError` regardless of engine state (submit_job's
        own docstring: "schema validation FIRST... then preflight").
        `run_generate` now preflights before `submit_job` is ever
        reached, so a call that is BOTH missing a required param (`txt2img`
        needs `prompt`) AND facing an unbound role gets `ToolRefused` (no
        retry) instead -- the reverse of `submit_job`'s own ordering.

        Accepted: one unconditional preflight is worth more than
        preserving that ordering for the narrow case where both failures
        coincide, and `ToolRefused` is still an honest answer here -- the
        role really is unbound, and no retry with a different or complete
        set of params would have helped either."""
        from tools.vision import services
        from tools.vision.tools import run_generate

        from agents.contracts.tools import ToolRefused

        unbound = services.PreflightResult("unbound", None, services.role_unbound_message())
        with patch("tools.vision.services.preflight", return_value=unbound), \
             patch("tools.vision.services.submit_job") as submit:
            with pytest.raises(ToolRefused) as excinfo:
                run_generate({"operation": "txt2img"}, make_tool_ctx())

        assert services.role_unbound_message() in str(excinfo.value)
        assert not isinstance(excinfo.value, ParamError)
        submit.assert_not_called()

    def test_an_unreachable_preflight_refuses_before_submit_with_the_platforms_own_copy(self):
        """The same defect, the OTHER non-`ready` state `_fill_engine_
        params` used to ignore: a role assigned but its engine not
        answering. Must refuse with the platform's own engine-unreachable
        sentence, never a bogus `ParamError`, and `submit_job` must never
        be reached."""
        from models.contracts.bindings import ResolvedModel
        from tools.vision import services
        from tools.vision.tools import run_generate

        from agents.contracts.tools import ToolRefused

        resolved = ResolvedModel(
            engine="stubengine", model_id="stub.safetensors", endpoint="http://stub:9999",
        )
        message = f"The image engine ({resolved.engine}) at {resolved.endpoint} is not reachable."
        unreachable = services.PreflightResult("unreachable", resolved, message)
        with patch("tools.vision.services.preflight", return_value=unreachable), \
             patch("tools.vision.services.submit_job") as submit:
            with pytest.raises(ToolRefused) as excinfo:
                run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert message in str(excinfo.value)
        submit.assert_not_called()

    def test_a_dead_image_reference_is_a_retryable_valueerror_not_a_refusal(self):
        """Repairable -- the model can name a different artifact -- so it
        gets section 10.2's one retry, which a ToolRefused would not."""
        from agents.contracts.tools import ToolRefused
        from tools.vision import services
        from tools.vision.tools import run_generate

        assert issubclass(services.InputReferenceError, ValueError)
        with patch("tools.vision.services.resolve_inputs",
                   side_effect=services.InputReferenceError("image", "output:99 is gone.")):
            with pytest.raises(ValueError) as excinfo:
                run_generate(
                    {"operation": "img2img", "prompt": "x", "image": "output:99"},
                    make_tool_ctx(),
                )
        assert not isinstance(excinfo.value, ToolRefused)
        assert "output:99 is gone." in str(excinfo.value)

    def test_a_timed_out_generation_reports_honestly_and_does_not_raise(self):
        """`wait_for` returns the job AS-IS at the deadline. A
        non-terminal job is a real, reportable state -- not a failure to
        crash on."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        job.is_terminal = False
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json",
                   return_value={"id": "abc", "status": "running", "outputs": []}):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert "still running" in result.text.lower()
        assert result.artifacts == ()

    def test_wait_for_polls_the_engine_and_the_queue_is_never_touched(self):
        """`services.wait_for` polls the IMAGE ENGINE about a generation it
        already submitted; it never calls `models.contracts.queue.get_job`.
        That distinction is the whole rule.

        The SOURCE-TEXT guard on that rule lives in ONE place --
        `foundation/ops/tests/test_column_boundaries.py::
        test_no_tool_runner_blocks_on_a_queue_job` (P1 Task 10), which owns
        the forbidden-name list for every tool module. A second copy here,
        with its own list, would drift from it. This test pins the
        BEHAVIOUR instead: the runner reaches the engine through
        `wait_for` and nothing else."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("models.contracts.queue.get_job",
                   side_effect=AssertionError("a tool runner must never poll the queue")), \
             patch("models.contracts.queue.enqueue",
                   side_effect=AssertionError("vision.generate enqueues nothing")), \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job) as wait, \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert wait.call_count == 1

    def test_the_wait_is_clamped_to_the_budgets_remaining_time(self):
        """`GENERATE_WAIT_TIMEOUT_SECONDS` alone could ask for hours
        longer than a turn has left -- the wait must never outlast
        `ctx.budget.deadline_monotonic`."""
        from agents.contracts.tools import StepBudget
        from tools.vision.tools import run_generate

        job = MagicMock()
        ctx = make_tool_ctx(budget=StepBudget(steps=8, deadline_monotonic=time.monotonic() + 5.0))
        captured = {}

        def fake_wait(j, timeout, interval=1.0, on_poll=None):
            captured["timeout"] = timeout
            return j

        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", side_effect=fake_wait), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "x"}, ctx)

        assert 0 < captured["timeout"] <= 5.0

    def test_the_wait_never_goes_below_one_second_even_past_deadline(self):
        """A turn that arrives here with its budget already expired still
        gets ONE poll, not a zero or negative timeout `wait_for` would
        treat as "never even try"."""
        from agents.contracts.tools import StepBudget
        from tools.vision.tools import run_generate

        job = MagicMock()
        ctx = make_tool_ctx(budget=StepBudget(steps=8, deadline_monotonic=time.monotonic() - 5.0))
        captured = {}

        def fake_wait(j, timeout, interval=1.0, on_poll=None):
            captured["timeout"] = timeout
            return j

        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", side_effect=fake_wait), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "x"}, ctx)

        assert captured["timeout"] == 1.0

    def test_a_failed_jobs_own_error_is_spoken_in_the_result_text(self):
        """Fix 2 (image-model-trace.md, Follow-up 3): `payload['error']`
        sat one dict key away from the finished-job sentence and was
        never read -- "finished as failed." told the model nothing it
        could act on."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        job.is_terminal = True
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={
                 "id": "abc", "status": "failed",
                 "error": "No image to image graph for the flux2 family.",
                 "outputs": [],
             }):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert (
            "Generation abc finished as failed: "
            "No image to image graph for the flux2 family." in result.text
        )

    def test_a_done_job_carries_no_error_suffix(self):
        """Truthful for `done` too -- unchanged, no dangling ": " when
        there is nothing to append."""
        from tools.vision.tools import run_generate

        job = MagicMock()
        job.is_terminal = True
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={
                 "id": "abc", "status": "done", "error": "", "outputs": [],
             }):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert result.text == "Generation abc finished as done."

    def test_a_failed_job_with_no_error_carries_no_dangling_colon(self):
        from tools.vision.tools import run_generate

        job = MagicMock()
        job.is_terminal = True
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job), \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={
                 "id": "abc", "status": "failed", "error": "", "outputs": [],
             }):
            result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert result.text == "Generation abc finished as failed."

    @pytest.mark.django_db
    def test_an_unsupported_operation_is_refused_before_submit_job_is_ever_called(self):
        """Fix 3 (image-model-trace.md, Follow-up 3): a model bound with
        no img2img graph must never reach `submit_job` -- without this,
        a real `GenerationJob` row is created and stamped with the
        model's own name, and only the engine's own template lookup
        refuses it, AFTER the row exists. A plain, RETRYABLE
        `ValueError` (never `ToolRefused`, which subclasses `ValueError`
        too -- review round 1, finding 5: a DIFFERENT operation
        genuinely can succeed, so this must be repairable, not refused),
        carrying `operation_states`'s own honest reason plus the
        supported list. `GenerationJob.objects.count()` (review round 1,
        finding 7), not only the mock, is the pin: it holds even if some
        future refactor lets a row get created through a path that never
        touches the mocked `submit_job` at all."""
        from agents.contracts.tools import ToolRefused
        from models.contracts.operations import get_operation
        from tools.vision import services
        from tools.vision.models import GenerationJob
        from tools.vision.tools import run_generate

        states = (
            services.OperationState(
                operation=get_operation("img2img"), supported=False,
                reason="No image to image graph for the flux2 family.",
            ),
            services.OperationState(
                operation=get_operation("txt2img"), supported=True, reason="",
            ),
            services.OperationState(
                operation=get_operation("edit"), supported=True, reason="",
            ),
        )
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.operation_states", return_value=states), \
             patch("tools.vision.services.submit_job") as submit:
            with pytest.raises(ValueError) as excinfo:
                run_generate({"operation": "img2img", "prompt": "x"}, make_tool_ctx())

        assert not isinstance(excinfo.value, ToolRefused)
        assert submit.call_count == 0
        assert GenerationJob.objects.count() == 0
        assert "No image to image graph for the flux2 family." in str(excinfo.value)
        assert "This model runs: txt2img, edit." in str(excinfo.value)

    def test_a_supported_operation_is_never_refused_by_the_same_check(self):
        from models.contracts.operations import get_operation
        from tools.vision import services
        from tools.vision.tools import run_generate

        states = (
            services.OperationState(
                operation=get_operation("txt2img"), supported=True, reason="",
            ),
        )
        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.operation_states", return_value=states), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

        assert submit.call_count == 1


class TestNarrowedGenerateSpec:
    """Fix 1 (image-model-trace.md, Follow-up 3): `narrowed_generate_spec`
    is the per-turn seam `agents.runtime.loop.available_tools` calls
    (Fix 1b) beside `agents.runtime.flowtool.narrowed_flow_spec`."""

    def _ready(self, resolved=None):
        from models.contracts.bindings import ResolvedModel
        from tools.vision import services

        resolved = resolved or ResolvedModel(
            engine="stubengine", model_id="stub.gguf", endpoint="http://stub:9999",
        )
        return services.PreflightResult("ready", resolved, "")

    def test_an_unbound_role_leaves_the_spec_unchanged(self):
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec
        from tools.vision import services

        spec = build_generate_spec()
        with patch("tools.vision.services.preflight",
                   return_value=services.PreflightResult("unbound", None, "no model")):
            narrowed = narrowed_generate_spec(spec)

        assert narrowed is spec

    def test_engine_no_opinion_leaves_the_spec_unchanged(self):
        """`services.operations_for_model` already treats "no opinion" as
        "offer everything" -- narrowing to the full registered set would
        be a no-op dressed up as one, so this returns the SAME spec."""
        from models.contracts.operations import all_operations
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        spec = build_generate_spec()
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model",
                   return_value=all_operations()):
            narrowed = narrowed_generate_spec(spec)

        assert narrowed is spec

    def test_a_model_that_supports_nothing_is_never_none_and_is_left_unchanged(self):
        """A real, narrower fact ("this engine runs nothing") is still
        not a reason to hand back an `operation` enum with no legal
        value -- that is worse than the union it would replace. Left to
        Fix 3's submit-time refusal to explain why."""
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        spec = build_generate_spec()
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model", return_value=[]):
            narrowed = narrowed_generate_spec(spec)

        assert narrowed is not None
        assert narrowed is spec

    def test_it_narrows_the_operation_enum_and_drops_orphaned_params(self):
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        edit = get_operation("edit")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model", return_value=[edit]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        operation_param = next(p for p in narrowed.params if p.key == "operation")
        assert operation_param.choices == ("edit",)
        assert {p.key for p in narrowed.params} == {
            "operation", "instruction", "guidance", "steps", "seed",
            "loras", "lora_strength", "image", "reference_image",
        }

    def test_it_rewrites_the_image_param_away_from_the_unsupported_family_steer(self):
        """The incident: "Required by the image-to-image family" told a
        model bound to a family with no img2img graph to pick it anyway
        (image-model-trace.md, Follow-up 3)."""
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        txt2img = get_operation("txt2img")
        edit = get_operation("edit")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model",
                   return_value=[txt2img, edit]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        image_param = next(p for p in narrowed.params if p.key == "image")
        assert "image-to-image family" not in image_param.description
        assert "Required by: edit." in image_param.description
        assert "Ignored by: txt2img." in image_param.description

    def test_it_overwrites_the_default_and_appends_the_models_own_value_to_the_prose(self):
        """Review round 1, finding 3: the model-specific number is
        APPENDED to the param's own description, never a replacement --
        `steps` keeps meaning "how many denoising steps to run" even
        though its generic "5-8 suits most checkpoints" RANGE ADVICE
        (image-model-trace.md's numeric-defaults finding, the thing
        that actually taught a past model to supply the wrong numbers)
        now sits beside a model-specific correction rather than
        replacing the param's real explanation."""
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        edit = get_operation("edit")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model", return_value=[edit]), \
             patch("tools.vision.services.live_defaults",
                   return_value={"steps": 4, "guidance": 1.0}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        steps = next(p for p in narrowed.params if p.key == "steps")
        guidance = next(p for p in narrowed.params if p.key == "guidance")
        assert steps.default == 4
        assert "How many denoising steps to run" in steps.description  # meaning kept
        assert "This model's own default is 4; omit this to use it." in steps.description
        assert guidance.default == 1.0

    def test_the_description_names_the_supported_operations(self):
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        edit = get_operation("edit")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model", return_value=[edit]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        assert "This model runs: edit." in narrowed.description

    def test_the_image_param_is_dropped_when_no_survivor_takes_a_file(self):
        """Review round 1, finding 4: every survivor's `file_params()`
        is empty (a txt2img-only bind) -- `image` is a slot nothing can
        use and is dropped outright, not kept with an all-"Ignored by"
        description offering nothing."""
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        txt2img = get_operation("txt2img")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model", return_value=[txt2img]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        assert "image" not in {p.key for p in narrowed.params}

    def test_orphaned_params_are_dropped_by_name_for_a_txt2img_and_edit_model(self):
        """Peer review Q2: pin the actual incident's own shape --
        flux2 klein binds exactly `txt2img` + `edit` -- so every param
        belonging ONLY to `img2img`/`inpaint`/`upscale` is gone by
        name, not merely "the set got smaller"."""
        from models.contracts.operations import get_operation
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        txt2img = get_operation("txt2img")
        edit = get_operation("edit")
        with patch("tools.vision.services.preflight", return_value=self._ready()), \
             patch("tools.vision.services.operations_for_model",
                   return_value=[txt2img, edit]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            narrowed = narrowed_generate_spec(build_generate_spec())

        keys = {p.key for p in narrowed.params}
        for orphaned in ("denoise", "mask_grow", "mask_image", "upscale_model"):
            assert orphaned not in keys, orphaned

    def test_an_unreachable_engine_leaves_the_spec_unchanged_and_never_raises(self):
        """Peer review Q1: `unreachable` (bound, but the engine is not
        answering) is treated exactly like `unbound` -- there is
        nothing LIVE to narrow from either way, so this costs nothing
        beyond the one preflight health round trip it already paid: no
        `operations_for_model` call, no exception, the spec unchanged."""
        from models.contracts.bindings import ResolvedModel
        from tools.vision import services
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        spec = build_generate_spec()
        resolved = ResolvedModel(engine="stubengine", model_id="m", endpoint="http://x")
        unreachable = services.PreflightResult(
            "unreachable", resolved, "The image engine is not reachable.",
        )
        with patch("tools.vision.services.preflight", return_value=unreachable), \
             patch("tools.vision.services.operations_for_model") as operations_for_model:
            narrowed = narrowed_generate_spec(spec)

        assert narrowed is spec
        operations_for_model.assert_not_called()

    def test_a_narrowing_failure_fails_open_to_the_union_spec(self):
        """Review round 1, finding 2 (internal half): a narrowing
        failure anywhere in this function -- including the lazy
        `services` import itself -- must never cost the turn its
        tools. Monkeypatches a `services` attribute to blow up; the
        union spec still comes back, identical, not a copy."""
        from tools.vision.tools import build_generate_spec, narrowed_generate_spec

        spec = build_generate_spec()
        with patch("tools.vision.services.preflight", side_effect=RuntimeError("boom")):
            narrowed = narrowed_generate_spec(spec)

        assert narrowed is spec


@pytest.mark.django_db
def test_two_narrowing_calls_in_one_window_cost_one_health_probe():
    """C-07 half B. `narrowed_generate_spec` runs on every chat turn that
    offers `vision.generate`, and its preflight paid one HTTP round trip
    to the generation server each time -- on the turn's critical path.
    Two turns inside the 30s window must cost one probe. Uses the REAL
    `services.preflight`/`_health_check` (only `get_engine` is mocked),
    so this exercises `probe_cache` itself, not a stand-in for it."""
    from models.contracts.roles import VISION_GENERATE_ROLE
    from models.registry.models import ModelConnection, RoleBinding
    from tools.vision import probe_cache, tools

    probe_cache.invalidate()
    connection = ModelConnection.objects.create(
        name="stub image model", engine="stubengine", endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)

    spec = tools.build_generate_spec()
    engine = MagicMock()
    engine.is_healthy.return_value = True
    with patch("tools.vision.services.get_engine", return_value=engine):
        tools.narrowed_generate_spec(spec)
        tools.narrowed_generate_spec(spec)
    assert engine.is_healthy.call_count == 1
    probe_cache.invalidate()


@pytest.mark.django_db
class TestNarrowingAndBlankFillProduceSubmittableParams:
    """The test that would have caught the original bugs: for EVERY
    registered operation, the runner's own narrowing (`_narrow_for_
    operation`) plus its own engine-param blank-fill (`_fill_engine_
    params`) must produce a dict `models.contracts.operations.
    validate_params` accepts for THAT operation -- with `submit_job`
    itself never touched, so this is a pure unit check of the two
    functions the AMEND introduced, not an end-to-end job submission.
    """

    def test_every_operation_validates_after_narrowing_and_filling(self):
        from models.contracts.operations import all_operations, validate_params

        from tools.vision.tools import (
            _fill_engine_params, _narrow_for_operation, build_generate_spec,
        )

        spec = build_generate_spec()
        stub_options = {"sampler": ("euler",), "scheduler": ("simple",)}

        for operation in all_operations():
            args = {"operation": operation.key}
            for param in spec.params:
                if param.key in ("operation", "image"):
                    continue
                if param.kind == "text":
                    args[param.key] = "x"
                elif param.kind == "asset":
                    args[param.key] = ["stub-asset"]
                # int/float/seed left blank on purpose -- the union's own
                # defaults (or, for seed, a random fill) apply, exactly as
                # they would for a real caller who only named what it
                # cared about.

            clean = validate_tool_args(spec, args)
            supplied = set(args) - {"operation"}
            raw_params, _references, _dropped = _narrow_for_operation(
                clean, operation, supplied
            )

            # `_fill_engine_params` no longer preflights for itself (Task
            # E) -- it trusts the `resolved` binding its caller,
            # `run_generate`, already confirmed `ready`. `_ready_preflight
            # ().resolved` stands in for that here.
            with patch("tools.vision.services.live_defaults", return_value={}), \
                 patch("tools.vision.services.live_options", return_value=stub_options):
                filled = _fill_engine_params(operation, raw_params, _ready_preflight().resolved)

            stub_files = {param.key: "stub.png" for param in operation.file_params()}

            # Must not raise ParamError -- this is the assertion. Every
            # required param `operation` declares (a file, `instruction`,
            # `upscale_model`, ...) is covered by `stub_files` or the loop
            # above; every engine-owned param `_fill_engine_params` leaves
            # is filled by the stubbed live options.
            validate_params(operation, {**filled, **stub_files})

    def test_a_union_default_never_overrides_the_picked_operations_own(self):
        """The bug CQ-1 fixes: `clean` (the union schema's own
        always-filled dict) carries a default for a key even when the
        caller never touched it -- the FIRST operation to declare that
        key wins the union's default (`denoise` from img2img: 0.6,
        `steps` from txt2img: 25). Forwarding an untouched key would
        forward THAT default into a DIFFERENT operation's submission --
        inpaint would run at img2img's denoise, never its own 1.0; edit
        would run at txt2img's steps, never its own 20. Only a key the
        caller actually SUPPLIED may be forwarded -- passed to
        `_narrow_for_operation` explicitly here, the caller's own raw
        `args` keys, not read off `clean` (which always carries every
        union key regardless of what the caller sent) -- and everything
        else is left for `validate_params` to fill from the picked
        operation's OWN default.

        Every OTHER required param the picked operation declares (its own
        `prompt`/`instruction`, its own `sampler`/`scheduler` where it has
        one, its own second file param) is supplied here too -- this test
        is about `denoise`/`steps` specifically, not a repeat of the
        roundtrip test above, so those must not be the thing that raises.
        """
        from models.contracts.operations import get_operation, validate_params

        from tools.vision.tools import _narrow_for_operation, build_generate_spec

        spec = build_generate_spec()
        per_operation_extra = {
            "inpaint": {
                "prompt": "x", "sampler": "euler", "scheduler": "normal",
                "mask_image": "input:9",
            },
            "edit": {"instruction": "x"},
        }
        for op_key, key, own in (("inpaint", "denoise", 1.0), ("edit", "steps", 20)):
            operation = get_operation(op_key)
            args = {"operation": op_key, "image": "output:1", **per_operation_extra[op_key]}
            clean = validate_tool_args(spec, dict(args))
            supplied = set(args) - {"operation"}
            raw_params, _references, _dropped = _narrow_for_operation(
                clean, operation, supplied
            )

            assert key not in raw_params

            stub_files = {p.key: "s.png" for p in operation.file_params()}
            validated = validate_params(operation, {**raw_params, **stub_files})
            assert validated.get(key) == own


@pytest.mark.django_db
class TestCQ1SuppliedIsRecoveredThroughInvokeTool:
    """CQ-1, fix round 1. `invoke_tool` is the one production caller of
    every tool runner (`agents/runtime/invoke.py:143-146`) and it passes the
    VALIDATED, always-filled union dict, never the caller's raw one.
    Every test above calls `run_generate` directly with a hand-written
    sparse dict -- a shape `invoke_tool` never produces, because by the
    time it reaches a runner every union key already carries a value.
    Driven through `invoke_tool` itself, a sparse call used to have BOTH
    bugs the review named: every untouched union key was announced as
    an "Ignored argument", and an untouched key's UNION default (the
    first operation to declare it) was forwarded into an operation that
    declares a DIFFERENT default of its own.

    Round 1 replaced the value-vs-default heuristic (which had its own
    two holes -- an `"asset"` param's blank-fill ignoring its declared
    default, and a caller's explicit value coincidentally matching some
    OTHER operation's default) with the caller's TRUE raw argument keys,
    carried on `ctx.supplied_keys` (`agents/contracts/tools.py::
    ToolContext.supplied_keys`, stamped by `invoke_tool`). These tests
    exercise that path directly, through `invoke_tool`, not
    `_narrow_for_operation` in isolation.

    `inpaint`, not `txt2img`, is the sparse call below: `txt2img` is
    FIRST in registration order, so it IS the union's default for every
    key it declares -- there is no divergence a `txt2img` call could
    expose. `denoise` (img2img's default 0.6, first to declare it;
    inpaint's own is 1.0) is the review's own example of the divergence
    bug.
    """

    def test_a_sparse_inpaint_call_reports_no_spurious_drops_and_keeps_the_operations_own_default(self):
        from agents.runtime.invoke import invoke_tool
        from models.contracts.operations import get_operation, validate_params

        spec = get_tool("vision.generate")
        job = MagicMock()
        with patch(
            "tools.vision.services.resolve_inputs",
            return_value={"init_image": MagicMock(), "mask_image": MagicMock()},
        ), \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            outcome = invoke_tool(
                spec,
                {
                    "operation": "inpaint",
                    "prompt": "a cat",
                    "image": "output:1",
                    "mask_image": "input:9",
                    "sampler": "euler",
                    "scheduler": "normal",
                    # `denoise`, `steps`, `cfg_scale`, `negative_prompt`,
                    # `seed`, `batch_size`, LoRA params, ... deliberately
                    # left unsent: `invoke_tool`'s own validation fills
                    # every one of them before `run_generate` ever sees
                    # this dict.
                },
                make_tool_ctx(),
            )

        assert not outcome.failed
        # (a) nothing the caller never sent is announced as ignored --
        # before the fix this listed every one of the union keys above.
        assert "Ignored" not in outcome.text
        # (b) the picked operation's OWN default is used, never the
        # union's: `denoise` is never forwarded at all (the caller never
        # set it), so `submit_job` receives no value for it...
        assert "denoise" not in submit.call_args[0][1]
        # ...and F8: prove it downstream too, not just its absence --
        # `validate_params` fills inpaint's own default (1.0), never
        # img2img's (0.6), for exactly the dict this call submitted.
        validated = validate_params(get_operation("inpaint"), {
            **submit.call_args[0][1],
            "init_image": "output:1", "mask_image": "input:9",
            "sampler": "euler", "scheduler": "normal",
        })
        assert validated["denoise"] == 1.0

    def test_an_explicit_denoise_equal_to_the_union_default_is_still_forwarded(self):
        """F2. `denoise=0.6` is `img2img`'s default and therefore the
        union's -- so a caller who explicitly asks for it must not be
        indistinguishable from one who asked for nothing. Before round
        1's fix, the value-vs-default heuristic silently dropped this
        and let `inpaint`'s own default (1.0) apply instead, overriding
        an explicit choice with no announcement at all."""
        from agents.runtime.invoke import invoke_tool

        spec = get_tool("vision.generate")
        job = MagicMock()
        with patch(
            "tools.vision.services.resolve_inputs",
            return_value={"init_image": MagicMock(), "mask_image": MagicMock()},
        ), \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            outcome = invoke_tool(
                spec,
                {
                    "operation": "inpaint", "prompt": "a cat", "image": "output:1",
                    "mask_image": "input:9", "sampler": "euler", "scheduler": "normal",
                    "denoise": 0.6,
                },
                make_tool_ctx(),
            )

        assert not outcome.failed
        assert submit.call_args[0][1]["denoise"] == 0.6

    def test_a_sparse_upscale_call_never_announces_an_ignored_loras(self):
        """F1. `loras` is an `"asset"` param (`multiple=True`), whose
        blank-fill is `[]` regardless of its declared `default` --
        `[] != None` marked it "supplied" on EVERY call under the old
        value-vs-default heuristic, so a plain `upscale` call (which
        declares no `loras` at all) falsely announced
        `Ignored argument not used by 'upscale': loras.` even though the
        caller never mentioned it."""
        from agents.runtime.invoke import invoke_tool

        spec = get_tool("vision.generate")
        job = MagicMock()
        with patch(
            "tools.vision.services.resolve_inputs",
            return_value={"init_image": MagicMock()},
        ), \
             patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            outcome = invoke_tool(
                spec,
                {"operation": "upscale", "image": "output:1", "upscale_model": "x.pth"},
                make_tool_ctx(),
            )

        assert not outcome.failed
        assert "Ignored" not in outcome.text
        assert "loras" not in submit.call_args[0][1]


class TestGenerateSpecFollowsTheServiceUnion:
    """`build_generate_spec` and `services.union_params` compute the SAME
    union (every operation's params, de-duplicated by key, first
    declaration wins). They are not one function on purpose:
    `VisionConfig.ready()` calls `build_generate_spec()`, and this module
    must not import the service layer at registration time (its own
    docstring; `foundation/ops/tests/test_column_boundaries.py`). This
    test is what keeps the two from drifting instead."""

    def test_the_tool_schema_is_the_service_union_minus_the_collapsed_file_key(self):
        from models.contracts.operations import all_operations
        from tools.vision import services
        from tools.vision.tools import build_generate_spec

        operations = all_operations()
        union = [param.key for param in services.union_params(operations)]
        # Every operation's FIRST file param collapses onto the shared
        # `image` param (this module's own docstring); every other param
        # keeps its own key.
        collapsed = {
            operation.file_params()[0].key
            for operation in operations
            if operation.file_params()
        }
        expected = {key for key in union if key not in collapsed}

        actual = {
            param.key for param in build_generate_spec().params
            if param.key not in ("operation", "image")
        }
        assert actual == expected

    def test_the_shared_image_param_stands_in_for_the_collapsed_one(self):
        from tools.vision.tools import build_generate_spec

        keys = [param.key for param in build_generate_spec().params]
        assert "image" in keys
        assert "init_image" not in keys


class TestFillEngineParamsDelegates:
    """`_fill_engine_params` fills every blank ENGINE-OWNED choice param,
    not only the ones the model ignores -- a tool caller may simply have
    omitted `sampler`. It does that through the same
    `services.fill_engine_blanks` the page uses, with its own key set."""

    def test_it_calls_the_shared_filler_with_its_own_wider_key_set(self):
        from models.contracts.operations import TXT2IMG
        from tools.vision import tools as vision_tools

        check = SimpleNamespace(ready=True, resolved=object(), message="")
        with patch(
            "tools.vision.services.fill_engine_blanks",
            return_value={"sampler": "euler", "scheduler": "normal"},
        ) as mock_fill, patch("tools.vision.services.live_defaults", return_value={}):
            filled = vision_tools._fill_engine_params(
                TXT2IMG, {"prompt": "x", "sampler": "", "scheduler": ""}, check.resolved
            )

        assert filled == {"sampler": "euler", "scheduler": "normal"}
        _operation, _params, resolved = mock_fill.call_args.args
        assert resolved is check.resolved
        assert mock_fill.call_args.kwargs["keys"] == {"sampler", "scheduler"}


class TestFillEngineParamsFillsOmittedNumericParams:
    """Item A (2026-09-03 fix batch, tool path 6x-slowdown fix). A
    numeric param the CALLER OMITTED entirely -- `steps`, `cfg_scale`,
    whatever a picked operation declares `"int"`/`"float"` -- must get
    the SAME family/live default the page's own GET spreads into its
    form's `initial` (`views.py` ~586, `services.live_defaults`), not
    `submit_job`'s own static SCHEMA default (steps=25, cfg_scale=7.0,
    `operations.py` `SAMPLING_PARAMS`). Before this, only ENGINE-owned
    `"choice"` params (`sampler`/`scheduler`) were filled from live
    values on the tool path -- see `TestFillEngineParamsDelegates`
    above -- so a distilled-family model, resolved through the tool
    path, still generated at the checkpoint-tuned schema numbers."""

    def test_an_omitted_numeric_param_gets_the_live_family_default(self):
        from models.contracts.operations import TXT2IMG
        from tools.vision import tools as vision_tools

        with patch(
            "tools.vision.services.live_defaults",
            return_value={"steps": 4, "cfg_scale": 1.0},
        ):
            filled = vision_tools._fill_engine_params(
                TXT2IMG,
                {"prompt": "x", "sampler": "euler", "scheduler": "normal"},
                object(),
            )

        assert filled["steps"] == 4
        assert filled["cfg_scale"] == 1.0

    def test_an_explicitly_supplied_numeric_param_is_never_overridden(self):
        """Even a value that happens to equal the SCHEMA's own default
        (25/7.0) was SUPPLIED, and is respected verbatim -- only a key
        the caller genuinely never sent is ever filled."""
        from models.contracts.operations import TXT2IMG
        from tools.vision import tools as vision_tools

        with patch(
            "tools.vision.services.live_defaults",
            return_value={"steps": 4, "cfg_scale": 1.0},
        ):
            filled = vision_tools._fill_engine_params(
                TXT2IMG,
                {
                    "prompt": "x", "sampler": "euler", "scheduler": "normal",
                    "steps": 25, "cfg_scale": 7.0,
                },
                object(),
            )

        assert filled["steps"] == 25
        assert filled["cfg_scale"] == 7.0

    def test_a_numeric_param_with_no_reported_live_default_is_left_omitted(self):
        """`live_defaults` reports `{}` (unbound role, dead engine, or an
        adapter with no opinion) -- the numeric key stays OMITTED here,
        exactly as it always did, and `submit_job`'s own `validate_params`
        supplies the operation's schema default downstream."""
        from models.contracts.operations import TXT2IMG
        from tools.vision import tools as vision_tools

        with patch("tools.vision.services.live_defaults", return_value={}):
            filled = vision_tools._fill_engine_params(
                TXT2IMG,
                {"prompt": "x", "sampler": "euler", "scheduler": "normal"},
                object(),
            )

        assert "steps" not in filled
        assert "cfg_scale" not in filled

    def test_choice_fill_behaviour_is_unchanged(self):
        """The engine-owned `"choice"` fill (`sampler`/`scheduler`) still
        runs through `services.fill_engine_blanks`'s own BLANK check --
        this item adds a numeric fill alongside it, it does not touch
        the existing choice path."""
        from models.contracts.operations import TXT2IMG
        from tools.vision import tools as vision_tools

        with patch(
            "tools.vision.services.fill_engine_blanks",
            return_value={"sampler": "euler", "scheduler": "normal", "prompt": "x"},
        ) as mock_fill, patch("tools.vision.services.live_defaults", return_value={}):
            filled = vision_tools._fill_engine_params(
                TXT2IMG, {"prompt": "x", "sampler": "", "scheduler": ""}, object()
            )

        assert mock_fill.called
        assert filled["sampler"] == "euler"
        assert filled["scheduler"] == "normal"


class TestFillEngineParamsFillsOmittedFixedChoiceParams:
    """Review finding 6: the brief's own item A wording said
    "numeric/choice", but every operation registered today only declares
    a `"choice"` param the ENGINE-owned way (sampler/scheduler, always an
    EMPTY `choices` tuple) -- so exercising a `"choice"` param with a
    FIXED, non-empty vocabulary needs a SYNTHETIC operation. An omitted
    one must be filled from `services.live_defaults`, the identical
    mechanism the numeric fill above uses -- never `services.
    fill_engine_blanks` (that filler is for the engine-owned, no-fixed-
    choices vocabulary only, and never runs for this kind of param at
    all, since `engine_keys` excludes any `"choice"` param that DOES
    carry a fixed `choices` tuple)."""

    @staticmethod
    def _synthetic_operation():
        from models.contracts.operations import Operation, Param
        from models.contracts.roles import IMAGE_GENERATION_CAPABILITY

        return Operation(
            key="synthetic",
            label="Synthetic",
            capability=IMAGE_GENERATION_CAPABILITY,
            params=(
                Param("prompt", "text", "Prompt", required=True),
                Param(
                    "style", "choice", "Style",
                    choices=("vivid", "natural"), default="vivid",
                ),
            ),
            output_media="image/png",
        )

    def test_an_omitted_fixed_choice_param_gets_the_family_default(self):
        from tools.vision import tools as vision_tools

        operation = self._synthetic_operation()
        with patch(
            "tools.vision.services.live_defaults",
            return_value={"style": "natural"},
        ):
            filled = vision_tools._fill_engine_params(operation, {"prompt": "x"}, object())

        assert filled["style"] == "natural"

    def test_an_explicitly_supplied_fixed_choice_param_is_never_overridden(self):
        """Supplied and equal to neither the schema default ("vivid") nor
        the reported family default ("natural") -- proves the caller's
        own choice wins outright, not just that it happens to match one
        of the two candidates."""
        from tools.vision import tools as vision_tools

        operation = self._synthetic_operation()
        with patch(
            "tools.vision.services.live_defaults",
            return_value={"style": "natural"},
        ):
            filled = vision_tools._fill_engine_params(
                operation, {"prompt": "x", "style": "vivid"}, object()
            )

        assert filled["style"] == "vivid"

    def test_a_fixed_choice_param_with_no_reported_live_default_is_left_omitted(self):
        from tools.vision import tools as vision_tools

        operation = self._synthetic_operation()
        with patch("tools.vision.services.live_defaults", return_value={}):
            filled = vision_tools._fill_engine_params(operation, {"prompt": "x"}, object())

        assert "style" not in filled

    def test_fill_engine_blanks_is_never_called_for_a_fixed_choice_param(self):
        from tools.vision import tools as vision_tools

        operation = self._synthetic_operation()
        with patch(
            "tools.vision.services.fill_engine_blanks",
        ) as mock_fill, patch(
            "tools.vision.services.live_defaults", return_value={"style": "natural"},
        ):
            filled = vision_tools._fill_engine_params(operation, {"prompt": "x"}, object())

        mock_fill.assert_not_called()
        assert filled["style"] == "natural"


class TestRunGenerateAppliesLiveDefaultsToOmittedNumericParams:
    """End-to-end through `run_generate`: the tool path now matches the
    page's own behaviour for a resolved model whose family tunes
    `steps`/`cfg_scale` away from the schema's checkpoint-oriented
    numbers."""

    def test_omitted_steps_and_cfg_take_the_resolved_models_family_defaults(self):
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch(
                 "tools.vision.services.live_defaults",
                 return_value={"steps": 4, "cfg_scale": 1.0},
             ), \
             patch(
                 "tools.vision.services.live_options",
                 return_value={"sampler": ("euler",), "scheduler": ("normal",)},
             ), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate({"operation": "txt2img", "prompt": "a thing"}, make_tool_ctx())

        submitted = submit.call_args[0][1]
        assert submitted["steps"] == 4
        assert submitted["cfg_scale"] == 1.0

    def test_explicitly_supplied_steps_and_cfg_are_kept(self):
        from tools.vision.tools import run_generate

        job = MagicMock()
        with patch("tools.vision.services.preflight", return_value=_ready_preflight()), \
             patch(
                 "tools.vision.services.live_defaults",
                 return_value={"steps": 4, "cfg_scale": 1.0},
             ), \
             patch(
                 "tools.vision.services.live_options",
                 return_value={"sampler": ("euler",), "scheduler": ("normal",)},
             ), \
             patch("tools.vision.services.submit_job", return_value=job) as submit, \
             patch("tools.vision.services.wait_for", return_value=job), \
             patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
            run_generate(
                {"operation": "txt2img", "prompt": "a thing", "steps": 25, "cfg_scale": 7.0},
                make_tool_ctx(),
            )

        submitted = submit.call_args[0][1]
        assert submitted["steps"] == 25
        assert submitted["cfg_scale"] == 7.0
