"""Unit tests for tools/vision/forms.py -- schema-driven form building."""
from __future__ import annotations

import pytest
from django import forms as django_forms
from django.urls import reverse

from models.contracts.operations import EDIT, IMG2IMG, INPAINT, TXT2IMG, UPSCALE, Operation, Param
from tools.vision.forms import build_constant_form, build_form
from tools.vision.services import union_params

CHOICES = {"sampler": ("euler", "dpmpp_2m"), "scheduler": ("normal", "karras")}


class TestBuildForm:
    def test_every_param_becomes_a_field_of_the_right_type(self):
        form = build_form(TXT2IMG, CHOICES)
        assert isinstance(form.fields["prompt"], django_forms.CharField)
        assert isinstance(form.fields["width"], django_forms.IntegerField)
        assert isinstance(form.fields["cfg_scale"], django_forms.FloatField)
        assert isinstance(form.fields["sampler"], django_forms.ChoiceField)
        assert isinstance(form.fields["seed"], django_forms.CharField)

    def test_ranges_and_widget_hints_come_from_the_schema(self):
        form = build_form(TXT2IMG, CHOICES)
        width = form.fields["width"]
        assert width.min_value == 64
        assert width.max_value == 4096
        assert width.widget.attrs["step"] == 8
        assert form.fields["prompt"].required is True
        assert form.fields["negative_prompt"].required is False

    def test_engine_choices_populate_choice_fields_with_the_first_as_default(self):
        form = build_form(TXT2IMG, CHOICES)
        assert form.fields["sampler"].choices == [("euler", "euler"), ("dpmpp_2m", "dpmpp_2m")]
        assert form.fields["sampler"].initial == "euler"

    def test_a_choice_param_with_no_live_options_renders_a_text_input(self):
        """The engine could not be asked (unreachable). The operator can still
        type a sampler name rather than facing an empty dropdown -- and the
        engine rejects an unknown one as a failed job, honestly."""
        form = build_form(TXT2IMG, {})
        assert isinstance(form.fields["sampler"], django_forms.CharField)

    def test_initial_values_prefill_the_form(self):
        form = build_form(TXT2IMG, CHOICES, initial={"prompt": "reuse me", "steps": 40})
        assert form.fields["prompt"].initial == "reuse me"
        assert form.fields["steps"].initial == 40

    def test_bound_form_validates_against_the_live_choices(self):
        form = build_form(TXT2IMG, CHOICES, data={"prompt": "x", "sampler": "nope", "scheduler": "normal"})
        assert form.is_valid() is False
        assert "sampler" in form.errors

    def test_a_valid_bound_form_cleans_to_schema_shaped_values(self):
        form = build_form(
            TXT2IMG, CHOICES,
            data={"prompt": "x", "sampler": "euler", "scheduler": "normal",
                  "width": "512", "height": "512", "steps": "20", "cfg_scale": "5",
                  "batch_size": "1", "seed": "", "negative_prompt": ""},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["width"] == 512

    def test_a_file_param_becomes_a_file_field(self):
        operation = Operation(
            "img2img", "Image to image", "image-generation",
            (Param("init_image", "file", "Init image", accept="image/*", required=True),),
            "image/png",
        )
        form = build_form(operation, {})
        assert isinstance(form.fields["init_image"], django_forms.FileField)
        assert form.fields["init_image"].widget.attrs["accept"] == "image/*"


class TestAssetFields:
    MULTI = Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=())
    SINGLE = Param("upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True)

    def _operation(self, *params):
        return Operation(
            key="assets", label="Assets", capability="image-generation",
            output_media="image/png", params=params,
        )

    def test_a_multiple_asset_param_renders_a_multi_select_of_the_engines_own_list(self):
        form = build_form(
            self._operation(self.MULTI),
            {"loras": ("a.safetensors", "b.safetensors")},
        )
        field = form.fields["loras"]
        assert isinstance(field, django_forms.MultipleChoiceField)
        assert field.choices == [("a.safetensors", "a.safetensors"), ("b.safetensors", "b.safetensors")]
        assert field.required is False

    def test_a_single_required_asset_param_renders_a_required_select(self):
        form = build_form(self._operation(self.SINGLE), {"upscale_model": ("4x.pth",)})
        field = form.fields["upscale_model"]
        assert isinstance(field, django_forms.ChoiceField)
        assert not isinstance(field, django_forms.MultipleChoiceField)
        assert field.required is True

    def test_with_nothing_reported_a_required_asset_refuses_at_the_field(self):
        """THE RULING (S4): the form keeps the field required and carries
        the explanation on the field itself, so the refusal lands where the
        operator is looking -- never a form that says "optional" and a 400
        that says "required". `validate_params` still refuses it too; that
        is the schema floor a non-form caller (the chatbot tool) lands on."""
        form = build_form(self._operation(self.SINGLE), {})
        field = form.fields["upscale_model"]
        assert field.choices == []
        assert field.required is True
        assert "no upscale model" in field.help_text.lower()
        assert "cannot run" in field.help_text.lower()

    def test_the_message_names_both_causes_and_never_asserts_an_engine_said_so(self):
        """R2-3: an empty option list means EITHER a reachable engine with
        nothing installed OR no model assigned at all -- `services.live_options`
        returns `{}` for both, and this layer cannot tell them apart. The
        copy must not claim the engine reported anything."""
        help_text = build_form(self._operation(self.SINGLE), {}).fields["upscale_model"].help_text
        assert "engine has none installed" in help_text
        assert "no image model is assigned yet" in help_text
        assert reverse("setup-index") in help_text
        assert reverse("inference-console") in help_text
        assert "reports no" not in help_text

    def test_that_refusal_is_the_message_a_submission_gets_back(self):
        form = build_form(self._operation(self.SINGLE), {}, data={})
        assert form.is_valid() is False
        assert "no upscale model" in " ".join(form.errors["upscale_model"]).lower()

    def test_an_optional_multi_asset_with_nothing_reported_is_simply_empty(self):
        """"No LoRAs" is a normal answer, so nothing is refused here."""
        form = build_form(self._operation(self.MULTI), {})
        assert form.fields["loras"].required is False
        help_text = form.fields["loras"].help_text
        assert "no loras is available" in help_text.lower()   # the cause still shown
        assert "cannot run" not in help_text.lower()          # LW-1: not a blocker for an optional asset

    def test_a_bound_form_refuses_an_asset_the_engine_never_reported(self):
        form = build_form(
            self._operation(self.SINGLE), {"upscale_model": ("4x.pth",)},
            data={"upscale_model": "made-up.pth"},
        )
        assert form.is_valid() is False
        assert "upscale_model" in form.errors

    def test_a_bound_multi_select_cleans_to_a_list(self):
        form = build_form(
            self._operation(self.MULTI), {"loras": ("a.safetensors", "b.safetensors")},
            data={"loras": ["a.safetensors", "b.safetensors"]},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["loras"] == ["a.safetensors", "b.safetensors"]

    def test_a_multiple_asset_param_gets_the_checkbox_widget_and_the_chip_group_flag(self):
        """T2: the owner's platform-wide ruling against raw `<select
        multiple>` -- a `multiple=True` asset param renders with
        `CheckboxSelectMultiple`, and `_field.html` picks the chip-picker
        markup off `field.chip_group` rather than introspecting the
        widget class."""
        form = build_form(
            self._operation(self.MULTI), {"loras": ("a.safetensors", "b.safetensors")},
        )
        field = form.fields["loras"]
        assert isinstance(field.widget, django_forms.CheckboxSelectMultiple)
        assert field.chip_group is True

    def test_a_single_asset_param_carries_no_chip_group_flag(self):
        """The single-choice case (`upscale_model`) keeps its plain
        `ChoiceField` widget -- `chip_group` is stamped ONLY for the
        multiple case."""
        form = build_form(self._operation(self.SINGLE), {"upscale_model": ("4x.pth",)})
        field = form.fields["upscale_model"]
        assert not isinstance(field.widget, django_forms.CheckboxSelectMultiple)
        assert getattr(field, "chip_group", False) is False

    def test_the_chip_group_flag_and_widget_still_apply_with_nothing_reported(self):
        """The "nothing installed" degradation (S4) is a fact about
        `choices`, not about the widget -- an empty chip group is still a
        chip group, not a select."""
        form = build_form(self._operation(self.MULTI), {})
        field = form.fields["loras"]
        assert isinstance(field.widget, django_forms.CheckboxSelectMultiple)
        assert field.chip_group is True


class TestFileFieldsAreNeverPrefilled:
    def test_reuse_initial_prefills_text_but_not_a_file_field(self):
        """`?reuse=<job>` hands back a previous job's params, in which a
        file param is a basename STRING. Django would render that on a
        FileField as "Currently: beach.png" with a keep-it checkbox -- a
        file the browser cannot re-send and the engine never receives. The
        operator must pick the file again, so the field starts empty."""
        operation = Operation(
            "img2img", "Image to image", "image-generation",
            (
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("init_image", "file", "Init image", accept="image/*", required=True),
            ),
            "image/png",
        )
        form = build_form(operation, {}, initial={"prompt": "a lighthouse", "init_image": "beach.png"})

        assert form.fields["prompt"].initial == "a lighthouse"
        assert form.fields["init_image"].initial is None


class TestSchemaDescriptionsBecomeTooltips:
    """The schema already carries a sentence per param, and it is still
    the field's `help_text` -- R2 only moves where that text is SHOWN
    (`vision/_field.html`'s ⓘ tooltip instead of an always-visible
    `.field-hint`). Situational copy still WINS: a seed field's "leave
    blank for a random seed" and an asset field's "nothing installed"
    explain the situation the operator is actually in, which a generic
    description cannot."""

    def test_a_param_description_becomes_the_fields_help_text(self):
        form = build_form(TXT2IMG, {})

        width = next(param for param in TXT2IMG.params if param.key == "width")
        assert form.fields["width"].help_text == width.description

    def test_situational_copy_is_not_overwritten(self):
        form = build_form(TXT2IMG, {})

        assert form.fields["seed"].help_text == "Leave blank for a random seed."

    def test_a_param_with_no_description_gets_no_hint(self):
        operation = Operation(
            "nodesc", "No description", "image-generation",
            (Param("steps", "int", "Steps", default=20),),
            "image/png",
        )

        form = build_form(operation, {})

        assert form.fields["steps"].help_text == ""


class TestStoredKeys:
    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
        ),
    )

    def test_a_file_field_a_stored_reference_answers_is_not_required(self):
        """The operator picked a gallery image; asking them to also attach
        a file would be asking twice for the same thing."""
        form = build_form(self.OPERATION, {}, stored_keys=frozenset({"init_image"}))
        assert form.fields["init_image"].required is False

    def test_without_a_stored_reference_the_file_stays_required(self):
        form = build_form(self.OPERATION, {})
        assert form.fields["init_image"].required is True

    def test_a_bound_form_validates_with_the_file_left_empty(self):
        form = build_form(
            self.OPERATION, {}, data={"prompt": "p"}, stored_keys=frozenset({"init_image"})
        )
        assert form.is_valid(), form.errors


class TestBothBuildersShareTheSameTail:
    """`build_form` and `build_constant_form` end with the same shared
    tail now (`_finish_form`, forms.py) instead of two copies of it. This
    locks in that the tail still behaves identically from EITHER caller:
    a `stored_keys` file field relaxes to optional, and `initial` never
    lands on a file field (`operations._file_reference` stores a basename
    string a browser cannot re-send as a file)."""

    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
        ),
    )

    def _via_build_form(self, **kwargs):
        return build_form(self.OPERATION, {}, **kwargs)

    def _via_build_constant_form(self, **kwargs):
        return build_constant_form(self.OPERATION.params, self.OPERATION, {}, {}, **kwargs)

    @pytest.mark.parametrize("builder_name", ["_via_build_form", "_via_build_constant_form"])
    def test_stored_keys_and_file_initial_exclusion_agree(self, builder_name):
        build = getattr(self, builder_name)

        stored = build(stored_keys=frozenset({"init_image"}))
        assert stored.fields["init_image"].required is False

        prefilled = build(initial={"prompt": "reuse me", "init_image": "beach.png"})
        assert prefilled.fields["prompt"].initial == "reuse me"
        assert prefilled.fields["init_image"].initial is None


class TestConstantFormFields:
    """ADR 0012 D-EDIT-13: one form, every registered operation's params,
    three states. A RENDERING object -- it never validates a submission."""

    OPERATIONS = (TXT2IMG, IMG2IMG, INPAINT, UPSCALE, EDIT)
    UNION = union_params(OPERATIONS)

    def _form(self, operation, ignored=None, **kwargs):
        return build_constant_form(
            self.UNION, operation, CHOICES, ignored or {}, **kwargs
        )

    def test_every_union_key_is_present_in_first_declaration_order(self):
        for operation in self.OPERATIONS:
            form = self._form(operation)
            assert list(form.fields) == [
                "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
                "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
                "init_image", "denoise", "mask_image", "mask_grow", "upscale_model",
                "instruction", "reference_image", "guidance",
            ], operation.key

    def test_the_picked_operations_own_param_renders_an_enabled_field(self):
        """`steps` is 25 under txt2img and 20 under edit; `denoise` is 0.6
        under img2img and 1.0 under inpaint. A field the picked operation
        declares is built from THAT `Param`."""
        assert self._form(TXT2IMG).fields["steps"].initial == 25
        assert self._form(EDIT).fields["steps"].initial == 20
        assert self._form(IMG2IMG).fields["denoise"].initial == 0.6
        assert self._form(INPAINT).fields["denoise"].initial == 1.0

    def test_a_key_the_picked_operation_does_not_declare_is_unused(self):
        field = self._form(EDIT).fields["width"]
        assert field.state == "unused"
        assert field.reason == "Not used by Edit an image."
        assert field.required is False
        assert field.widget.attrs["disabled"] is True

    def test_a_key_the_model_cannot_honour_is_ignored_with_the_engines_words(self):
        form = self._form(TXT2IMG, ignored={"scheduler": "No scheduler input."})
        field = form.fields["scheduler"]
        assert field.state == "ignored"
        assert field.reason == "No scheduler input."
        assert field.required is False
        assert field.widget.attrs["disabled"] is True

    def test_an_ignored_choice_field_still_renders_the_engines_real_options(self):
        """Disabled, but honest: the operator can see WHAT this model
        would have chosen from, which is the point of showing it at all."""
        form = self._form(TXT2IMG, ignored={"scheduler": "No scheduler input."})
        assert form.fields["scheduler"].choices == [
            ("normal", "normal"), ("karras", "karras")
        ]

    def test_everything_else_is_enabled_with_no_reason(self):
        field = self._form(TXT2IMG).fields["prompt"]
        assert field.state == "enabled"
        assert field.reason == ""
        assert field.required is True
        assert "disabled" not in field.widget.attrs

    def test_unused_wins_over_ignored_for_a_param_the_operation_never_declared(self):
        """An engine may only annotate a param the OPERATION declares
        (`services.live_ignored` drops the rest), but the rule is stated
        here too so the two layers cannot disagree."""
        form = self._form(EDIT, ignored={"width": "irrelevant"})
        assert form.fields["width"].state == "unused"

    def test_a_text_param_is_marked_wide_and_nothing_else_is(self):
        form = self._form(TXT2IMG)
        assert form.fields["prompt"].wide is True
        assert form.fields["negative_prompt"].wide is True
        assert form.fields["instruction"].wide is True
        assert form.fields["seed"].wide is False
        assert form.fields["width"].wide is False

    def test_every_field_carries_its_params_kind_and_multiplicity(self):
        """T1: `field.kind`/`field.multiple` are stamped in `_field_for`,
        the path both builders share -- `vision/_field.html` reads them
        as `field--kind-*`/`field--multi` on the wrapper."""
        form = self._form(TXT2IMG)
        assert form.fields["prompt"].kind == "text"
        assert form.fields["width"].kind == "int"
        assert form.fields["sampler"].kind == "choice"
        assert form.fields["loras"].kind == "asset"
        assert form.fields["loras"].multiple is True
        assert form.fields["lora_strength"].multiple is False
        assert form.fields["width"].multiple is False

    def test_a_file_params_kind_is_stamped_too(self):
        form = self._form(IMG2IMG)
        assert form.fields["init_image"].kind == "file"

    def test_build_form_and_build_constant_form_agree_on_kind_and_multiple(self):
        """The two builders must never disagree about a fact that does
        not depend on which operation is picked."""
        legacy = build_form(TXT2IMG, CHOICES)
        constant = self._form(TXT2IMG)
        for key in ("prompt", "width", "sampler", "loras"):
            assert legacy.fields[key].kind == constant.fields[key].kind, key
            assert legacy.fields[key].multiple == constant.fields[key].multiple, key

    def test_the_schema_sentence_is_still_the_help_text(self):
        width = next(param for param in TXT2IMG.params if param.key == "width")
        assert self._form(TXT2IMG).fields["width"].help_text == width.description

    def test_every_widget_is_wired_to_its_own_tooltip(self):
        """`aria-describedby` belongs on the CONTROL, not only on the ⓘ
        that opens the tooltip: a screen-reader user tabbing into the
        field must hear the description without ever landing on the ⓘ."""
        form = self._form(TXT2IMG)
        assert form.fields["width"].widget.attrs["aria-describedby"] == "id_width-info"
        assert form.fields["prompt"].widget.attrs["aria-describedby"] == "id_prompt-info"

    def test_initial_prefills_an_enabled_field_but_never_a_file_field(self):
        form = self._form(
            IMG2IMG, initial={"prompt": "reuse me", "init_image": "beach.png"}
        )
        assert form.fields["prompt"].initial == "reuse me"
        assert form.fields["init_image"].initial is None

    def test_a_stored_key_makes_its_file_field_optional(self):
        form = self._form(IMG2IMG, stored_keys=frozenset({"init_image"}))
        assert form.fields["init_image"].required is False

    def test_a_bound_constant_form_validates_two_selected_loras(self):
        """T2: the constant form's `loras` field is `CheckboxSelectMultiple`
        exactly like `build_form`'s -- POSTing two names cleans to a list
        with no error on that field, the same `getlist` shape either
        builder produces (the POST shape did not change)."""
        # `_form` binds `CHOICES` (no `loras` entry) by default; build
        # directly with the engine actually reporting two LoRAs, so the
        # submitted values are ones the field's `choices` recognise.
        options = dict(CHOICES, loras=("a.safetensors", "b.safetensors"))
        form = build_constant_form(
            self.UNION, TXT2IMG, options, {},
            data={"prompt": "a lighthouse", "loras": ["a.safetensors", "b.safetensors"]},
        )
        assert form.fields["loras"].chip_group is True
        form.is_valid()
        assert "loras" not in form.errors
        assert form.cleaned_data["loras"] == ["a.safetensors", "b.safetensors"]

    def test_a_bound_constant_form_re_populates_typed_values(self):
        """The non-XHR re-render binds this form to the POST so the
        operator's typing comes back (`views._create_page_response`)."""
        form = self._form(TXT2IMG, data={"prompt": "a lighthouse", "width": "512"})
        assert form["prompt"].value() == "a lighthouse"
        assert form["width"].value() == "512"

    def test_a_bound_form_never_blocks_on_a_field_the_page_disabled(self):
        form = self._form(
            EDIT, ignored={"scheduler": "No scheduler input."},
            data={"instruction": "make it blue"},
        )
        form.is_valid()
        assert "width" not in form.errors
        assert "scheduler" not in form.errors

    def test_a_description_less_enabled_field_gets_no_aria_describedby(self):
        """`_field.html` renders the `.tip` span only `{% if f.help_text
        or f.field.reason %}` -- stamping `aria-describedby` unconditionally
        would point a description-less, enabled field at an id the
        template never renders."""
        operation = Operation(
            "nodesc", "No description", "image-generation",
            (Param("steps", "int", "Steps", default=20),),
            "image/png",
        )
        form = build_constant_form(operation.params, operation, CHOICES, {})
        assert "aria-describedby" not in form.fields["steps"].widget.attrs

    def test_a_described_enabled_field_still_gets_aria_describedby(self):
        operation = Operation(
            "withdesc", "With description", "image-generation",
            (Param("steps", "int", "Steps", default=20, description="How many steps."),),
            "image/png",
        )
        form = build_constant_form(operation.params, operation, CHOICES, {})
        assert form.fields["steps"].widget.attrs["aria-describedby"] == "id_steps-info"
