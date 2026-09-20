"""Unit tests for models/contracts/operations.py (spec §4.1).

Pure data + validation: no DB, no HTTP, no Django settings.

MOVED FROM `tools/vision/tests/` (C-58). This module tests
`models.contracts.operations` and imports nothing from `tools.vision` at
all -- it lived in a consumer column only because that column was the
first consumer. A contract's own tests belong beside the contract, where
the next consumer finds them.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Protocol

import pytest

from models.contracts.operations import (
    EDIT,
    IMG2IMG,
    INPAINT,
    LORA_PARAMS,
    PROMPT_PARAMS,
    SAMPLING_PARAMS,
    TXT2IMG,
    UPSCALE,
    Operation,
    Param,
    ParamError,
    all_operations,
    describe,
    get_operation,
    operations_for,
    register_operation,
    validate_params,
)


def _clean(**overrides) -> dict:
    raw = {
        "prompt": "a lighthouse",
        "negative_prompt": "",
        "width": "1024",
        "height": "1024",
        "steps": "25",
        "cfg_scale": "7.0",
        "seed": "",
        "sampler": "euler",
        "scheduler": "normal",
        "batch_size": "1",
    }
    raw.update(overrides)
    return raw


class TestOperationShape:
    def test_txt2img_declares_the_image_generation_capability(self):
        assert TXT2IMG.key == "txt2img"
        assert TXT2IMG.label == "Text to image"
        assert TXT2IMG.capability == "image-generation"
        assert TXT2IMG.output_media == "image/png"

    def test_txt2img_carries_every_spec_param(self):
        assert [param.key for param in TXT2IMG.params] == [
            "prompt", "negative_prompt", "width", "height", "steps",
            "cfg_scale", "seed", "sampler", "scheduler", "batch_size",
            "loras", "lora_strength",
        ]

    def test_param_lookup(self):
        assert TXT2IMG.param("steps").default == 25
        assert TXT2IMG.param("nope") is None

    def test_unknown_capability_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown capability"):
            Operation("x", "X", "telepathy", (), "image/png")

    def test_unknown_param_kind_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown param kind"):
            Operation("x", "X", "image-generation", (Param("p", "runes", "P"),), "image/png")


class TestRegistry:
    def test_register_and_look_up(self):
        op = Operation("t-op", "Test op", "image-generation", (), "image/png")
        register_operation(op)
        try:
            assert get_operation("t-op") is op
            assert op in all_operations()
            assert op in operations_for("image-generation")
            assert op not in operations_for("chat")
        finally:
            from models.contracts import operations

            operations._OPERATIONS.pop("t-op", None)

    def test_unknown_key_is_none(self):
        assert get_operation("no-such-op") is None


class TestValidateParams:
    def test_coerces_numbers_and_keeps_text(self):
        clean = validate_params(TXT2IMG, _clean())
        assert clean["prompt"] == "a lighthouse"
        assert clean["width"] == 1024 and isinstance(clean["width"], int)
        assert clean["cfg_scale"] == 7.0 and isinstance(clean["cfg_scale"], float)
        assert clean["batch_size"] == 1

    def test_missing_optional_params_take_their_defaults(self):
        clean = validate_params(TXT2IMG, {"prompt": "x", "sampler": "euler", "scheduler": "normal"})
        assert clean["steps"] == 25
        assert clean["width"] == 1024
        assert clean["negative_prompt"] == ""

    def test_missing_required_param_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(prompt="   "))
        assert "prompt" in excinfo.value.errors

    def test_out_of_range_value_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(steps="500"))
        assert "steps" in excinfo.value.errors

    def test_non_numeric_value_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(width="wide"))
        assert "width" in excinfo.value.errors

    def test_unknown_keys_are_rejected(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(sneaky="1"))
        assert "sneaky" in excinfo.value.errors

    def test_blank_seed_is_resolved_to_a_random_integer(self):
        first = validate_params(TXT2IMG, _clean())["seed"]
        second = validate_params(TXT2IMG, _clean())["seed"]
        assert isinstance(first, int) and 0 <= first < 2 ** 32
        assert first != second  # 1-in-4-billion flake, acceptable

    def test_explicit_seed_is_kept(self):
        assert validate_params(TXT2IMG, _clean(seed="42"))["seed"] == 42

    def test_choice_is_free_when_the_schema_has_no_list(self):
        """Sampler/scheduler lists come from the ENGINE at render time
        (`list_choices`), so the schema cannot enumerate them; the form adds
        the live list and the engine rejects anything else as a failed job."""
        assert validate_params(TXT2IMG, _clean(sampler="dpmpp_3m_sde"))["sampler"] == "dpmpp_3m_sde"

    def test_required_choice_cannot_be_blank(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(sampler=""))
        assert "sampler" in excinfo.value.errors


class _Upload:
    """The shape a Django `UploadedFile` presents to `validate_params`: a
    non-string object carrying a `.name`. Deliberately NOT JSON
    serialisable -- that is the whole bug this fixes."""

    def __init__(self, name: str):
        self.name = name


# A stand-in operation with TWO file params, distinct from the real
# `IMG2IMG` (which has only `init_image`): this class exercises the
# GENERIC "file" param handling in `validate_params` -- basename
# extraction, an optional second file, JSON-safety -- independent of any
# one production operation's shape. Named apart from the real `IMG2IMG`
# import above so the two never shadow each other.
_TWO_FILE_OP = Operation(
    key="img2img",
    label="Image to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("init_image", "file", "Init image", accept="image/*", required=True),
        Param("mask", "file", "Mask", accept="image/*"),
        Param("denoise", "float", "Denoise", default=0.6, min=0, max=1, step=0.05),
        Param("seed", "seed", "Seed", default=None),
    ),
)


class TestFileParams:
    """`GenerationJob.params` is a JSONField; a `"file"` param must resolve
    to something that can live in one."""

    def test_a_file_param_records_a_basename_not_the_upload(self):
        clean = validate_params(_TWO_FILE_OP, {"prompt": "a lighthouse", "init_image": _Upload("beach.png")})

        assert clean["init_image"] == "beach.png"
        json.dumps(clean)  # would raise TypeError on an UploadedFile

    def test_a_windows_style_upload_name_keeps_only_its_basename(self):
        clean = validate_params(
            _TWO_FILE_OP, {"prompt": "p", "init_image": _Upload(r"C:\Users\op\my photos\beach.png")}
        )

        assert clean["init_image"] == "beach.png"

    def test_a_plain_string_file_reference_survives_a_round_trip(self):
        """A caller re-submitting a job's stored params (the chatbot tool,
        `?reuse=`) hands back the basename string, not an upload."""
        clean = validate_params(_TWO_FILE_OP, {"prompt": "p", "init_image": "beach.png"})

        assert clean["init_image"] == "beach.png"

    def test_file_param_keys_names_every_file_param_and_nothing_else(self):
        """One definition of "which params are files", shared by the form
        layer (never prefill one) and the service layer (only these reach
        the store) -- so the two can never drift."""
        assert _TWO_FILE_OP.file_param_keys() == frozenset({"init_image", "mask"})
        assert TXT2IMG.file_param_keys() == frozenset()

    def test_a_required_file_param_that_is_absent_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(_TWO_FILE_OP, {"prompt": "p"})

        assert excinfo.value.errors["init_image"] == "Init image is required."

    def test_an_optional_file_param_that_is_absent_takes_its_default(self):
        clean = validate_params(_TWO_FILE_OP, {"prompt": "p", "init_image": _Upload("beach.png")})

        assert clean["mask"] is None


class TestImg2ImgSchema:
    def test_it_declares_its_init_image_as_a_required_image_file(self):
        param = IMG2IMG.param("init_image")
        assert (param.kind, param.required, param.accept) == ("file", True, "image/*")
        assert IMG2IMG.file_param_keys() == frozenset({"init_image"})

    def test_denoise_is_the_parameter_that_makes_this_mode_itself(self):
        param = IMG2IMG.param("denoise")
        assert (param.kind, param.default, param.min, param.max) == ("float", 0.6, 0.0, 1.0)

    def test_it_declares_no_size_because_the_init_image_carries_it(self):
        assert IMG2IMG.param("width") is None
        assert IMG2IMG.param("height") is None

    def test_a_submission_without_an_init_image_is_a_param_error(self):
        with pytest.raises(ParamError) as exc:
            validate_params(IMG2IMG, {"prompt": "a lighthouse", "sampler": "euler", "scheduler": "normal"})
        assert "init_image" in exc.value.errors

    def test_txt2img_is_named_for_its_mode_now_that_it_has_a_sibling(self):
        """The create page renders `operation.label` as its heading and its
        chooser entry; "Generate an image" beside "Image to image" named a
        page, not a mode."""
        assert TXT2IMG.label == "Text to image"


class TestFileParamOrder:
    def test_file_params_keep_declaration_order(self):
        """A link that pre-fills "the image" must pick the FIRST file
        param, which a frozenset cannot tell it."""
        operation = Operation(
            key="two_files", label="Two files", capability="image-generation",
            output_media="image/png",
            params=(
                Param("init_image", "file", "Image", required=True),
                Param("steps", "int", "Steps", default=1),
                Param("mask_image", "file", "Mask", required=True),
            ),
        )
        assert [param.key for param in operation.file_params()] == ["init_image", "mask_image"]
        assert operation.file_param_keys() == frozenset({"init_image", "mask_image"})

    def test_an_operation_with_no_file_params_has_an_empty_tuple(self):
        assert TXT2IMG.file_params() == ()


class TestInpaintSchema:
    def test_it_takes_an_image_and_a_mask_in_that_order(self):
        assert [param.key for param in INPAINT.file_params()] == ["init_image", "mask_image"]
        assert all(param.required for param in INPAINT.file_params())
        assert all(param.accept == "image/*" for param in INPAINT.file_params())

    def test_the_mask_label_says_which_way_round_it_is(self):
        """A mask is useless if the operator has to guess whether white
        means keep or repaint. The label answers it where it is read."""
        assert "repaint" in INPAINT.param("mask_image").label.lower()

    def test_it_denoises_fully_by_default_because_the_masked_area_is_being_replaced(self):
        assert INPAINT.param("denoise").default == 1.0

    def test_growing_the_mask_is_a_bounded_int(self):
        param = INPAINT.param("mask_grow")
        assert (param.kind, param.default, param.min, param.max) == ("int", 6, 0, 64)

    def test_both_files_are_required_by_validation(self):
        with pytest.raises(ParamError) as exc:
            validate_params(INPAINT, {"prompt": "p", "sampler": "euler", "scheduler": "normal"})
        assert set(exc.value.errors) >= {"init_image", "mask_image"}


class TestAssetParams:
    OPERATION = Operation(
        key="assets", label="Assets", capability="image-generation", output_media="image/png",
        params=(
            Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=()),
            Param("upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True),
        ),
    )

    def test_a_multiple_asset_param_cleans_to_a_list_of_strings(self):
        clean = validate_params(
            self.OPERATION,
            {"loras": ["a.safetensors", "b.safetensors"], "upscale_model": "4x.pth"},
        )
        assert clean["loras"] == ["a.safetensors", "b.safetensors"]

    def test_a_single_asset_param_cleans_to_one_string(self):
        clean = validate_params(self.OPERATION, {"upscale_model": "4x.pth"})
        assert clean["upscale_model"] == "4x.pth"

    def test_asset_ids_stay_opaque(self):
        """An asset id is the engine's own string -- a Windows host reports
        a backslash subfolder, and it must go back exactly as it came."""
        clean = validate_params(
            self.OPERATION,
            {"loras": [r"style\\detail.safetensors"], "upscale_model": "4x.pth"},
        )
        assert clean["loras"] == [r"style\\detail.safetensors"]

    def test_a_blank_multiple_asset_param_cleans_to_an_empty_list(self):
        clean = validate_params(self.OPERATION, {"upscale_model": "4x.pth"})
        assert clean["loras"] == []

    def test_blank_entries_are_dropped_rather_than_submitted(self):
        clean = validate_params(
            self.OPERATION, {"loras": ["a.safetensors", "", "  "], "upscale_model": "4x.pth"}
        )
        assert clean["loras"] == ["a.safetensors"]

    def test_a_required_asset_param_is_required(self):
        with pytest.raises(ParamError) as exc:
            validate_params(self.OPERATION, {"loras": []})
        assert "upscale_model" in exc.value.errors

    def test_an_empty_list_for_a_required_asset_param_is_also_missing(self):
        with pytest.raises(ParamError) as exc:
            validate_params(self.OPERATION, {"upscale_model": []})
        assert "upscale_model" in exc.value.errors

    def test_the_cleaned_params_stay_json_safe(self):
        clean = validate_params(self.OPERATION, {"loras": ["a.safetensors"], "upscale_model": "4x.pth"})
        assert json.loads(json.dumps(clean)) == clean


class TestLoraParams:
    def test_every_checkpoint_mode_declares_the_same_two_params(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT):
            loras = operation.param("loras")
            strength = operation.param("lora_strength")
            assert (loras.kind, loras.asset_kind, loras.multiple) == ("asset", "lora", True)
            assert (strength.kind, strength.default, strength.min, strength.max) == (
                "float", 1.0, 0.0, 2.0,
            )

    def test_they_are_optional_because_no_lora_is_a_normal_answer(self):
        clean = validate_params(TXT2IMG, {"prompt": "p", "sampler": "euler", "scheduler": "normal"})
        assert clean["loras"] == []
        assert clean["lora_strength"] == 1.0

    def test_a_chosen_lora_survives_validation_as_a_json_safe_list(self):
        clean = validate_params(
            TXT2IMG,
            {"prompt": "p", "sampler": "euler", "scheduler": "normal",
             "loras": ["style.safetensors"], "lora_strength": "0.75"},
        )
        assert clean["loras"] == ["style.safetensors"]
        assert clean["lora_strength"] == 0.75


class TestUpscaleSchema:
    def test_it_asks_for_an_image_and_an_upscaler_and_nothing_else(self):
        assert [param.key for param in UPSCALE.params] == ["init_image", "upscale_model"]
        assert UPSCALE.param("init_image").required is True
        assert UPSCALE.param("upscale_model").asset_kind == "upscale_model"

    def test_it_has_no_seed_because_nothing_about_it_is_random(self):
        assert UPSCALE.param("seed") is None

    def test_validation_returns_no_seed_for_it(self):
        clean = validate_params(UPSCALE, {"init_image": "beach.png", "upscale_model": "4x.pth"})
        assert "seed" not in clean


class TestSharedParamTuples:
    """The `LORA_PARAMS` treatment, applied to the params that were three
    byte-identical copies: txt2img, img2img and inpaint cannot drift apart
    on what a prompt or a sampler control is."""

    def test_every_checkpoint_mode_splices_the_same_two_tuples(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT):
            assert operation.params[:2] == PROMPT_PARAMS
            assert operation.params[-len(LORA_PARAMS) - len(SAMPLING_PARAMS):
                                    -len(LORA_PARAMS)] == SAMPLING_PARAMS

    def test_the_tuples_carry_exactly_the_params_that_were_copied(self):
        assert [param.key for param in PROMPT_PARAMS] == ["prompt", "negative_prompt"]
        assert [param.key for param in SAMPLING_PARAMS] == [
            "steps", "cfg_scale", "seed", "sampler", "scheduler", "batch_size",
        ]

    def test_declaration_order_is_unchanged_by_the_extraction(self):
        """The characterization half: param order drives form field order
        (`create.html` renders prompt/negative_prompt first, then the rest
        in declaration order), so the refactor must move nothing."""
        assert [param.key for param in TXT2IMG.params] == [
            "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
            "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
        ]
        assert [param.key for param in IMG2IMG.params] == [
            "prompt", "negative_prompt", "init_image", "denoise", "steps", "cfg_scale",
            "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
        ]
        assert [param.key for param in INPAINT.params] == [
            "prompt", "negative_prompt", "init_image", "mask_image", "mask_grow",
            "denoise", "steps", "cfg_scale", "seed", "sampler", "scheduler",
            "batch_size", "loras", "lora_strength",
        ]

    def test_the_shared_params_are_the_same_objects_not_copies(self):
        """Frozen dataclasses compare by value, so identity is what proves
        one definition rather than three that happen to agree today."""
        assert TXT2IMG.param("steps") is IMG2IMG.param("steps")
        assert IMG2IMG.param("sampler") is INPAINT.param("sampler")


class TestParamDescriptions:
    """Semantic hints belong in the schema, not in Python comments: the
    same sentence has to reach a form's help text, a `?format=json`
    schema, and a tool's parameter documentation."""

    def test_a_param_defaults_to_no_description(self):
        assert Param("x", "text", "X").description == ""

    def test_an_operation_defaults_to_no_description(self):
        assert Operation(
            key="k", label="L", capability="image-generation",
            params=(), output_media="image/png",
        ).description == ""

    def test_every_shipped_operation_describes_itself(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            assert operation.description, f"{operation.key} has no description"

    def test_every_param_of_every_shipped_operation_describes_itself(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            for param in operation.params:
                assert param.description, f"{operation.key}.{param.key} has no description"

    def test_denoise_says_what_its_ends_mean(self):
        """The hint the audit named: the one parameter an operator cannot
        guess from its label."""
        assert "0 returns the input" in IMG2IMG.param("denoise").description

    def test_inpaints_denoise_describes_its_own_default_not_img2imgs(self):
        assert IMG2IMG.param("denoise").description != INPAINT.param("denoise").description


class TestDescribe:
    """The schema an agent reads to learn what to send. Pure data: no
    engine, no DB, no Django."""

    def test_it_names_the_operation_and_its_media(self):
        described = describe(UPSCALE)
        assert described["key"] == "upscale"
        assert described["label"] == "Upscale"
        assert described["capability"] == "image-generation"
        assert described["output_media"] == "image/png"
        assert described["description"] == UPSCALE.description

    def test_it_is_json_safe(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            assert json.loads(json.dumps(describe(operation))) == describe(operation)

    def test_every_param_field_is_serialized(self):
        """Drift-proof: a field added to `Param` and not to `describe` is a
        field no schema consumer can see, so the test reads the dataclass
        itself rather than a hand-written list."""
        described = describe(IMG2IMG)
        expected = {field.name for field in dataclasses.fields(Param)}
        for param in described["params"]:
            assert set(param) == expected

    def test_params_keep_declaration_order(self):
        assert [param["key"] for param in describe(UPSCALE)["params"]] == [
            "init_image", "upscale_model",
        ]

    def test_a_choice_param_with_engine_reported_options_reports_no_choices(self):
        """`sampler` has no fixed `choices` -- the engine owns them. The
        schema says so honestly (an empty list) rather than inventing one;
        `services.operation_catalog` is what fills them in live."""
        sampler = next(p for p in describe(TXT2IMG)["params"] if p["key"] == "sampler")
        assert sampler["choices"] == []
        assert sampler["kind"] == "choice"

    def test_an_asset_param_reports_its_asset_kind_and_multiplicity(self):
        loras = next(p for p in describe(TXT2IMG)["params"] if p["key"] == "loras")
        assert (loras["asset_kind"], loras["multiple"], loras["required"]) == ("lora", True, False)

    def test_a_file_param_reports_what_it_accepts(self):
        init = next(p for p in describe(IMG2IMG)["params"] if p["key"] == "init_image")
        assert (init["kind"], init["accept"], init["required"]) == ("file", "image/*", True)


class TestEditOperation:
    def test_it_declares_only_params_every_edit_family_really_wires(self):
        """The honesty rule for a family-dispatched operation: a param this
        schema declares must be wired by EVERY graph that can run it. A
        sampler, a scheduler, a negative prompt, a denoise, and a size are
        each honoured by at most one of the two edit graphs, so none of
        them is offered. `batch_size` is excluded for a second reason on
        top of that: one bundled edit workflow carries no batching node at
        all, and an edit produces ONE output. LoRAs ARE offered: both
        bundled edit workflows carry a model-only LoRA loader
        (`LoraLoaderModelOnly`), so `*LORA_PARAMS` is honest here too (ADR
        0012 D-EDIT-9)."""
        assert [param.key for param in EDIT.params] == [
            "instruction", "init_image", "reference_image",
            "guidance", "steps", "seed", "loras", "lora_strength",
        ]

    def test_edit_declares_the_same_lora_controls_as_every_other_mode(self):
        """Both edit families' bundled workflows carry a model-only LoRA
        loader, so the control is honest for `edit` under D-EDIT-1's
        intersection rule -- and it is the SAME two params, not a second
        vocabulary."""
        keys = [param.key for param in EDIT.params]
        assert keys[-2:] == ["loras", "lora_strength"]
        assert EDIT.param("loras").asset_kind == "lora"

    def test_the_image_being_edited_is_the_first_file_param(self):
        """`Operation.file_params()` order is what a gallery 'use this image
        here' link pre-fills, and the image being EDITED is the one it
        should land on -- not the optional reference."""
        assert [param.key for param in EDIT.file_params()] == [
            "init_image", "reference_image",
        ]
        assert EDIT.file_param_keys() == frozenset({"init_image", "reference_image"})

    def test_the_reference_image_is_optional_and_the_edited_one_is_not(self):
        assert EDIT.param("init_image").required is True
        assert EDIT.param("reference_image").required is False

    def test_a_submission_without_a_reference_image_validates(self):
        clean = validate_params(
            EDIT,
            {
                "instruction": "put a red hat on the woman",
                "init_image": "beach.png",
                "guidance": "4.0", "steps": "20", "seed": "42",
            },
        )
        assert clean["instruction"] == "put a red hat on the woman"
        assert clean["init_image"] == "beach.png"
        assert clean["reference_image"] is None
        assert clean["guidance"] == 4.0
        assert clean["seed"] == 42

    def test_a_blank_instruction_is_refused(self):
        """An edit with no instruction is not an edit."""
        with pytest.raises(ParamError) as excinfo:
            validate_params(EDIT, {"instruction": "", "init_image": "beach.png"})
        assert "instruction" in excinfo.value.errors

    def test_it_names_no_model_and_no_family_anywhere_in_its_copy(self):
        """`models/contracts/` ships no model names. The schema is
        family-agnostic; the graph template is where a family is named."""
        copy = " ".join(
            [EDIT.label, EDIT.description]
            + [param.label for param in EDIT.params]
            + [param.description for param in EDIT.params]
        ).lower()
        for forbidden in ("flux", "qwen", "gguf", "mistral", "sdxl"):
            assert forbidden not in copy


class TestValidateParamsAcceptsAnythingWithAParamSchema:
    """`validate_params` is the schema FLOOR every caller shares -- the
    page's form and a tool call both land here (ADR 0012:676-679). Its
    annotation says `Operation`, but its BODY only ever reads
    `.params` (operations.py:276,282), so a second validator in
    `agents/contracts` would be exactly the parallel seam that ADR rules
    out. This pins the structural contract instead."""

    def test_hasparams_is_a_protocol_with_exactly_one_member(self):
        from models.contracts.operations import HasParams

        assert issubclass(type(HasParams), type(Protocol))
        # `params` and nothing else. A second member would be a claim
        # `validate_params`'s body does not make.
        members = {
            name for name in HasParams.__annotations__
            if not name.startswith("_")
        }
        assert members == {"params"}

    def test_operation_satisfies_hasparams(self):
        from models.contracts.operations import HasParams, TXT2IMG

        def takes(schema: HasParams) -> tuple:
            return schema.params

        assert takes(TXT2IMG) == TXT2IMG.params

    def test_a_bare_object_with_params_validates(self):
        """The whole point: a non-`Operation` carrying a `params` tuple
        validates through the SAME floor, with the same coercions and
        the same `ParamError`."""
        from models.contracts.operations import Param, ParamError, validate_params

        @dataclass(frozen=True)
        class _Schema:
            params: tuple[Param, ...]

        schema = _Schema(params=(
            Param("query", "text", "Query", required=True),
            Param("top_k", "int", "Results", default=None, min=1, max=50),
        ))

        assert validate_params(schema, {"query": "x", "top_k": "7"}) == {
            "query": "x", "top_k": 7,
        }

        with pytest.raises(ParamError) as excinfo:
            validate_params(schema, {"query": "", "top_k": "999"})
        assert set(excinfo.value.errors) == {"query", "top_k"}

    def test_an_unknown_key_is_still_rejected_for_a_non_operation_schema(self):
        from models.contracts.operations import Param, ParamError, validate_params

        @dataclass(frozen=True)
        class _Schema:
            params: tuple[Param, ...]

        with pytest.raises(ParamError) as excinfo:
            validate_params(_Schema(params=(Param("a", "text", "A"),)), {"b": "x"})
        assert excinfo.value.errors == {"b": "Unknown parameter for this operation."}


class TestNoOperatorFacingStringNamesAModel:
    """Non-negotiable 3 names the surfaces a model name may NOT reach, and a
    form's help text is one of them: `Param.description` becomes the field's
    `help_text` (`tools/vision/forms.py`) and renders on `/vision/`. The
    repo-wide documentation gate
    (`foundation/ops/tests/test_docs_model_names.py`) walks MARKDOWN only, so
    it could never have seen this string -- `operations.py` shipped "512 for
    SD 1.5, 1024 for SDXL" straight to the operator's screen while every
    surrounding docstring was scrubbed (round-1 review finding I2).

    This pins the CLASS, not that one string: every operator-visible string
    on every registered operation, checked against the documentation gate's
    own pattern list so the two can never disagree about what a model name is.
    """

    def _operator_visible_strings(self):
        from models.contracts.operations import all_operations

        for operation in all_operations():
            yield f"{operation.key}.label", operation.label
            yield f"{operation.key}.description", operation.description or ""
            for param in operation.params:
                yield f"{operation.key}.{param.key}.label", param.label
                yield f"{operation.key}.{param.key}.description", param.description or ""

    def test_no_operation_label_or_description_names_a_model_family(self):
        import re

        from foundation.ops.tests.test_docs_model_names import _MODELNAME_PATTERNS

        offenders = [
            f"{where}: {text}"
            for where, text in self._operator_visible_strings()
            for pattern in _MODELNAME_PATTERNS
            if re.search(pattern, text, re.IGNORECASE)
        ]
        assert offenders == [], "\n".join(offenders)

    def test_the_operator_facing_pin_is_not_vacuous(self):
        """A pin that walked nothing, or compared against an empty pattern
        list, would pass forever. Both halves are real."""
        from foundation.ops.tests.test_docs_model_names import _MODELNAME_PATTERNS

        collected = dict(self._operator_visible_strings())
        assert len(collected) >= 40
        assert "txt2img.width.description" in collected
        assert "pixels" in collected["txt2img.width.description"]
        assert len(_MODELNAME_PATTERNS) >= 15
