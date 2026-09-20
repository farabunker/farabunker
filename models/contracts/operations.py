"""
Operation registry -- the platform's generation vocabulary (D5).

An "operation" is a named generation mode with a parameter SCHEMA: txt2img
today; img2img, inpaint, controlnet, upscale later (spec §9). The schema is
what the page renders its form from and what an engine adapter maps onto its
own template, so adding a mode is one `Operation` definition plus one engine
template -- never a reshape of the page or the service layer.

Definitions live here in `models/contracts/` (pure data, no Django); REGISTRATION is done
by the feature app that actually serves them (`tools/vision/apps.py`), so
`all_operations()` only ever lists what an enabled feature can really run.

Kept dependency-free apart from `CAPABILITIES` (also pure), matching
`models.contracts.roles` / `models.contracts.catalog`.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from models.contracts.roles import CAPABILITIES

# Every parameter kind the form layer knows how to render and this module
# knows how to validate. A kind outside this set is a programming error in an
# Operation definition, caught at definition time rather than at render time.
PARAM_KINDS = {"text", "int", "float", "choice", "seed", "file", "asset"}

# Asset kinds an engine may be asked to list (`InferenceEngine.list_assets`).
ASSET_KINDS = {"lora", "vae", "controlnet", "upscale_model", "embedding"}

# Upper bound for a randomly resolved seed. Deliberately modest (32-bit): it
# is small enough to type, paste, and compare by eye when reproducing a
# generation, and every engine we target accepts it.
SEED_MAX = 2 ** 32


@dataclass(frozen=True)
class Param:
    """One parameter of an operation, as both a validation rule and a
    rendering instruction.

    `choices` is for a `"choice"` param whose options are FIXED. Options an
    engine reports live (samplers, schedulers) are left empty here and
    supplied by the form layer from `InferenceEngine.list_choices` --
    `validate_params` therefore accepts any non-blank string for such a
    param, and an option the engine does not know surfaces as a failed job
    rather than a lie about what this platform supports.
    """

    key: str
    kind: str
    label: str
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    asset_kind: str | None = None
    accept: str | None = None
    required: bool = False
    multiple: bool = False
    # Last in the field order deliberately: every existing construction
    # passes `key`, `kind`, `label` positionally and the rest by keyword,
    # so a field added anywhere earlier would silently change what a
    # positional fourth argument means.
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in PARAM_KINDS:
            raise ValueError(f"Unknown param kind {self.kind!r}; must be one of {sorted(PARAM_KINDS)}")
        if self.asset_kind is not None and self.asset_kind not in ASSET_KINDS:
            raise ValueError(f"Unknown asset kind {self.asset_kind!r}; must be one of {sorted(ASSET_KINDS)}")


class HasParams(Protocol):
    """Anything carrying a parameter schema `validate_params` can validate
    against.

    ONE member, deliberately. `validate_params` touches exactly one thing
    on its first argument -- `operation.params`, read twice (below at the
    `known = {...}` set and the coercion loop). It never calls
    `file_params()`; the `"file"` kind is handled inline in that loop, BY
    KIND, not by consulting a key set. A second member here would be a
    claim the body does not make.

    `Operation` satisfies this structurally, and so does
    `agents.contracts.tools.ToolSpec` -- which is the point: ADR
    0012:676-679 makes this function the schema FLOOR every caller
    shares, "including a future chatbot tool that builds no Django form
    at all". A second validator in `agents/contracts` would be exactly
    the parallel seam that rules out.

    Deliberately NOT `@runtime_checkable`: a runtime-checkable Protocol
    with a non-method member raises `TypeError` on `isinstance`, and
    nothing in this codebase needs an isinstance check against it. It is
    a type annotation, and only that.
    """

    params: tuple[Param, ...]


@dataclass(frozen=True)
class Operation:
    """A generation mode plus its parameter schema."""

    key: str
    label: str
    capability: str
    params: tuple[Param, ...]
    output_media: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITIES:
            raise ValueError(
                f"Unknown capability {self.capability!r}; must be one of {sorted(CAPABILITIES)}"
            )

    def param(self, key: str) -> Param | None:
        """The parameter named `key`, or None."""
        for param in self.params:
            if param.key == key:
                return param
        return None

    def file_params(self) -> tuple[Param, ...]:
        """Every `"file"` param, in DECLARATION order.

        Order matters to one caller and only one: a "use this image here"
        link pre-fills the first file param an operation declares (an
        inpaint job's image, leaving its mask to be chosen), and a
        frozenset cannot say which that is.
        """
        return tuple(param for param in self.params if param.kind == "file")

    def file_param_keys(self) -> frozenset[str]:
        """Every `"file"` param's key.

        One definition of "which of my params are files", because two layers
        need exactly that set for opposite reasons: the form layer must
        never prefill one (a browser cannot re-send a file from a name) and
        the service layer must let only these reach the managed store (an
        undeclared upload has no meaning). Written twice, they drift; written
        here beside `param()`, they cannot.
        """
        return frozenset(param.key for param in self.file_params())


_OPERATIONS: dict[str, Operation] = {}


def register_operation(operation: Operation) -> None:
    """Register `operation` under its `.key`, replacing any existing entry.
    Idempotent, like `register_role` -- re-importing a registering module is
    safe."""
    _OPERATIONS[operation.key] = operation


def all_operations() -> list[Operation]:
    """Every registered operation, in registration order."""
    return list(_OPERATIONS.values())


def get_operation(key: str) -> Operation | None:
    """The registered operation for `key`, or None."""
    return _OPERATIONS.get(key)


def operations_for(capability: str) -> list[Operation]:
    """Every registered operation answering `capability`."""
    return [op for op in _OPERATIONS.values() if op.capability == capability]


def describe(operation: Operation) -> dict:
    """`operation` as plain, JSON-safe data -- the machine-readable answer
    to "what do I send?".

    The schema dataclasses are the platform's generation vocabulary; this
    is the one place that turns them into data a caller who is not Python
    can read: a `?format=json` schema endpoint, a chatbot tool's parameter
    list, an MCP tool definition. Pure, like everything else in this
    module -- it takes no engine, so it reports what the SCHEMA knows and
    nothing about a particular install. `tools.vision.services.
    operation_catalog` is what layers a bound engine's live option lists
    on top.

    A `"choice"` param whose options the engine owns reports `choices: []`
    -- an honest "the schema does not know", never a guessed list.
    """
    return {
        "key": operation.key,
        "label": operation.label,
        "description": operation.description,
        "capability": operation.capability,
        "output_media": operation.output_media,
        "params": [_describe_param(param) for param in operation.params],
    }


def _describe_param(param: Param) -> dict:
    """One `Param` as JSON-safe data, one key per dataclass field.

    Written out explicitly rather than via `dataclasses.asdict` so the
    tuple-valued `choices` becomes a list (JSON has no tuple) and so a
    field added to `Param` without a decision about how it serializes
    fails a test instead of silently appearing in a public contract.
    """
    return {
        "key": param.key,
        "kind": param.kind,
        "label": param.label,
        "description": param.description,
        "default": param.default,
        "min": param.min,
        "max": param.max,
        "step": param.step,
        "choices": list(param.choices),
        "asset_kind": param.asset_kind,
        "accept": param.accept,
        "required": param.required,
        "multiple": param.multiple,
    }


class ParamError(ValueError):
    """Raised by `validate_params` when the submitted values don't fit the
    schema. `.errors` maps a param key (or an unknown key) to a
    human-readable reason, so a view can re-render a form with per-field
    messages instead of one opaque string."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = errors


def _coerce_number(param: Param, raw: Any, errors: dict[str, str]) -> Any:
    caster = int if param.kind == "int" else float
    try:
        value = caster(str(raw).strip())
    except (TypeError, ValueError):
        errors[param.key] = f"{param.label} must be a number."
        return None
    if param.min is not None and value < param.min:
        errors[param.key] = f"{param.label} must be at least {caster(param.min)}."
        return None
    if param.max is not None and value > param.max:
        errors[param.key] = f"{param.label} must be at most {caster(param.max)}."
        return None
    return value


def _file_reference(supplied: Any) -> str:
    """The JSON-safe value a `"file"` param records in a job's `params`: the
    uploaded file's BASENAME, never the upload object.

    `GenerationJob.params` is a JSONField, so a Django `UploadedFile` in
    there is a `TypeError` at save time -- and it would be the first line of
    code img2img executes. The bytes are not lost: `submit_job` writes them
    into the job's own `inputs/` directory, records a `JobInput` row that
    owns the path, and puts that path in `GenerationRequest.inputs`. What
    stays in `params` is a LABEL, so an operator reading a job card can see
    which file was run.

    Only the basename survives, and a backslash counts as a separator: a
    browser on Windows may report a full local path, and none of it is ours
    to keep.
    """
    name = getattr(supplied, "name", None)
    if name is None:
        name = supplied
    return str(name).replace("\\", "/").rsplit("/", 1)[-1]


def validate_params(operation: HasParams, raw: dict) -> dict:
    """Coerce and range-check `raw` against `operation`'s schema. `operation`
    is anything with a `params` tuple (`HasParams`): an `Operation`, or a
    `ToolSpec`.

    This is the ONLY validation the service layer does, and it is the floor
    every caller shares -- the page's form and (later) the chatbot tool both
    land here. It:

    - rejects keys the operation does not declare (a typo must not be
      silently swallowed into a graph);
    - fills absent optional params from their defaults;
    - coerces `int`/`float` and enforces `min`/`max`;
    - resolves a blank `seed` to a random integer and returns it, so the
      job row can record exactly what was run (D6 reproducibility);
    - reduces a `file` param to the upload's basename (`_file_reference`),
      so what lands in the JSONField is a label and never an upload object;
    - requires a non-blank value for any `required` param and for any
      `choice` param.

    `step` is a UI hint only, not a rule: ComfyUI rejects a size that isn't
    divisible by 8 with a clear error, and surfacing THAT is more honest
    than this module inventing an engine's constraint.

    Raises `ParamError` with every problem found (not just the first), so a
    form can show all of them at once.
    """
    errors: dict[str, str] = {}
    known = {param.key for param in operation.params}
    for key in raw:
        if key not in known:
            errors[key] = "Unknown parameter for this operation."

    clean: dict[str, Any] = {}
    for param in operation.params:
        supplied = raw.get(param.key)
        blank = supplied is None or (isinstance(supplied, str) and not supplied.strip())

        if param.kind == "seed":
            if blank:
                clean[param.key] = secrets.randbelow(SEED_MAX)
                continue
            value = _coerce_number(Param(param.key, "int", param.label, min=0), supplied, errors)
            if value is not None:
                clean[param.key] = value
            continue

        if blank:
            if param.required:
                errors[param.key] = f"{param.label} is required."
                continue
            if param.kind == "choice":
                errors[param.key] = f"{param.label} must be chosen."
                continue
            if param.kind == "asset":
                # An unanswered asset param is "none chosen", which for a
                # multi-select is an empty LIST -- not the `None` a
                # scalar default would put in a JSONField for a value the
                # graph template iterates.
                clean[param.key] = [] if param.multiple else None
                continue
            clean[param.key] = param.default
            continue

        if param.kind in ("int", "float"):
            value = _coerce_number(param, supplied, errors)
            if value is not None:
                clean[param.key] = value
            continue

        if param.kind == "choice":
            value = str(supplied).strip()
            if param.choices and value not in param.choices:
                errors[param.key] = f"{param.label} must be one of {', '.join(param.choices)}."
                continue
            clean[param.key] = value
            continue

        if param.kind == "file":
            clean[param.key] = _file_reference(supplied)
            continue

        if param.kind == "asset":
            # An asset id is the ENGINE's own opaque string (a Windows host
            # reports `subdir\file.safetensors`): stripped of surrounding
            # whitespace and otherwise passed through untouched. Never
            # basenamed the way a `"file"` param's upload name is -- that
            # separator is part of the id, not a path we own.
            supplied_values = supplied if isinstance(supplied, (list, tuple)) else [supplied]
            chosen = [str(value).strip() for value in supplied_values if str(value).strip()]
            if not chosen and param.required:
                errors[param.key] = f"{param.label} is required."
                continue
            clean[param.key] = chosen if param.multiple else (chosen[0] if chosen else None)
            continue

        clean[param.key] = str(supplied).strip() if isinstance(supplied, str) else supplied

    if errors:
        raise ParamError(errors)
    return clean


@dataclass(frozen=True)
class GenerationRequest:
    """One generation, as the engine seam receives it.

    `params` is always `validate_params` output (never raw form data);
    `inputs` maps a file param's key to the path it was stored at inside the
    job directory; `client_ref` is the job's UUID, used as the engine-side
    output filename prefix so a result can always be traced back to its job.
    """

    operation: str
    model_id: str
    params: dict
    inputs: dict[str, Path] = field(default_factory=dict)
    client_ref: str = ""


# --- The operations this platform ships ----------------------------------
# Defined here; REGISTERED by the feature app that serves them
# (tools/vision/apps.py), gated on its feature flag.

# The two prompt fields every checkpoint-based mode opens with. Declared
# once and spliced in, for exactly the reason `LORA_PARAMS` below is:
# txt2img, img2img, and inpaint must not drift apart on what a prompt
# control is.
PROMPT_PARAMS = (
    Param(
        "prompt", "text", "Prompt", default="", required=True,
        description=(
            "What to generate. Describe the subject, the setting, and the style; most "
            "checkpoints respond well to comma-separated phrases."
        ),
    ),
    Param(
        "negative_prompt", "text", "Negative prompt", default="",
        description="What to keep out of the image — subjects, styles, or artifacts you don't want.",
    ),
)

# The sampling controls every checkpoint-based mode shares, in the order
# they were declared in all three. Split from `PROMPT_PARAMS` rather than
# written as one 8-tuple because the modes differ in the MIDDLE (txt2img's
# width/height, img2img's init_image/denoise, inpaint's mask): two tuples
# spliced around the mode-specific params preserve every operation's
# declaration order, and declaration order is form field order.
SAMPLING_PARAMS = (
    Param(
        "steps", "int", "Steps", default=25, min=1, max=150,
        description=(
            "How many denoising steps to run. More steps cost more time; most checkpoints "
            "stop improving somewhere past 30-40."
        ),
    ),
    Param(
        "cfg_scale", "float", "CFG scale", default=7.0, min=0, max=30, step=0.5,
        description=(
            "How strictly to follow the prompt. Low values wander off it, high values "
            "over-bake the image; 5-8 suits most checkpoints."
        ),
    ),
    Param(
        "seed", "seed", "Seed", default=None,
        description=(
            "The random seed. Leave it blank for a new one every run; reuse a finished "
            "job's seed to reproduce that job exactly."
        ),
    ),
    # Choices are engine-reported (`list_choices`) -- see `Param.choices`.
    Param(
        "sampler", "choice", "Sampler",
        description=(
            "The sampling algorithm, named as the bound engine names it. The options are "
            "read from that engine, never from a list this platform ships."
        ),
    ),
    Param(
        "scheduler", "choice", "Scheduler",
        description=(
            "The noise schedule the sampler follows, named as the bound engine names it. "
            "Read from that engine, like the sampler."
        ),
    ),
    Param(
        "batch_size", "int", "Batch size", default=1, min=1, max=8,
        description=(
            "How many images this one submission produces. They share the prompt and every "
            "other setting, including the seed the job records."
        ),
    ),
)

# The adornments every checkpoint-based operation shares (D7: assets are a
# second axis -- they decorate a job, they don't answer a role). Declared
# once and spliced into each operation's params, so txt2img, img2img, and
# inpaint cannot drift apart on what a LoRA control is.
#
# ONE strength for the whole selection, deliberately: a `Param` carries one
# value, so per-LoRA strengths would need a param kind that holds options
# per item. Two chained LoRAs at one strength is the common case; the
# limitation is written down in `tools/vision/README.md` rather than
# faked with a parallel list.
LORA_PARAMS = (
    # No `default`: the blank branch of `validate_params` answers an
    # unfilled asset param with `[]` (or `None`) and never reads one, so a
    # default here would be a value nothing consults.
    Param(
        "loras", "asset", "LoRAs", asset_kind="lora", multiple=True,
        description=(
            "Style or subject adapters to apply on top of the model, chosen from what "
            "the engine reports having installed. Selecting none is a normal answer. "
            "Some adapters are distillation adapters: they replace the guidance "
            "setting — set Steps to the count the adapter names and Guidance to 1."
        ),
    ),
    Param(
        "lora_strength", "float", "LoRA strength", default=1.0, min=0.0, max=2.0, step=0.05,
        description=(
            "How strongly to apply every selected LoRA — one strength covers the whole "
            "selection: 0 disables them, 1 applies them fully."
        ),
    ),
)

TXT2IMG = Operation(
    key="txt2img",
    label="Text to image",
    capability="image-generation",
    output_media="image/png",
    description="Generate an image from a text prompt alone.",
    params=(
        *PROMPT_PARAMS,
        Param(
            "width", "int", "Width", default=1024, min=64, max=4096, step=8,
            description=(
                "Output width in pixels. Stay near the checkpoint's native resolution "
                "(512 for older checkpoint families, 1024 for XL-class); most "
                "engines need a multiple of 8."
            ),
        ),
        Param(
            "height", "int", "Height", default=1024, min=64, max=4096, step=8,
            description="Output height in pixels. Same rule as width — native resolution, multiple of 8.",
        ),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)

IMG2IMG = Operation(
    key="img2img",
    label="Image to image",
    capability="image-generation",
    output_media="image/png",
    description=(
        "Generate a new image guided by an existing one, keeping as much of the original "
        "as the denoise setting allows."
    ),
    params=(
        *PROMPT_PARAMS,
        # The image the generation starts from. Its own dimensions decide
        # the output size, which is why this operation declares no
        # width/height: inventing a size here would silently rescale the
        # operator's picture.
        Param(
            "init_image", "file", "Init image", accept="image/*", required=True,
            description=(
                "The image this generation starts from. Its own dimensions decide the "
                "output size, which is why this mode has no width or height."
            ),
        ),
        # How much of the init image to throw away. The one parameter that
        # makes this mode itself: 0 returns the input, 1 ignores it.
        Param(
            "denoise", "float", "Denoise", default=0.6, min=0.0, max=1.0, step=0.05,
            description=(
                "How much of the init image to throw away: 0 returns the input untouched, "
                "1 ignores it entirely. 0.4-0.7 keeps the composition while changing the content."
            ),
        ),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)

INPAINT = Operation(
    key="inpaint",
    label="Inpaint",
    capability="image-generation",
    output_media="image/png",
    description=(
        "Repaint the masked area of an existing image, leaving everything outside the mask "
        "untouched."
    ),
    params=(
        *PROMPT_PARAMS,
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description="The image to repaint part of. Everything outside the mask is preserved.",
        ),
        # Which way round a mask reads is the one thing an operator cannot
        # guess, so the label answers it where it is read.
        Param(
            "mask_image", "file", "Mask (white = repaint)", accept="image/*", required=True,
            description=(
                "A black-and-white image the same size as the picture: white marks what to "
                "repaint, black is left alone."
            ),
        ),
        # Feathering the mask outward hides the seam. ComfyUI's own
        # `VAEEncodeForInpaint` input, exposed rather than hardcoded.
        Param(
            "mask_grow", "int", "Grow mask by", default=6, min=0, max=64,
            description=(
                "Pixels to expand the mask outward before repainting. A few pixels of growth "
                "hides the seam at the edge of the masked area."
            ),
        ),
        # Unlike img2img, the masked area is being REPLACED, so the honest
        # default is full denoise; lowering it keeps some of what was there.
        Param(
            "denoise", "float", "Denoise", default=1.0, min=0.0, max=1.0, step=0.05,
            description=(
                "How much of the masked area to throw away: 1 replaces it entirely, lower "
                "values keep some of what was there. The default is full denoise here, "
                "unlike image-to-image, because the masked area is being replaced."
            ),
        ),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)

UPSCALE = Operation(
    key="upscale",
    label="Upscale",
    capability="image-generation",
    output_media="image/png",
    description=(
        "Enlarge an existing image with an upscaling model. No prompt and no seed — "
        "nothing about it is random."
    ),
    params=(
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description=(
                "The image to enlarge. Nothing is regenerated — the upscale model resamples "
                "what is already there."
            ),
        ),
        # The upscaler is an ASSET, not a model that answers a role (D7):
        # it adorns this job, and the operator picks it from what the
        # engine reports having.
        Param(
            "upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True,
            description=(
                "The upscaling model to run, chosen from what the engine reports having "
                "installed (ESRGAN-family weights and the like)."
            ),
        ),
    ),
)

# The one instruction-driven mode, and the first operation whose GRAPH
# differs per model family (ADR 0012 D-EDIT-1). It is deliberately ONE
# operation, not one per family: the operator's question is "change this
# image like so", and which model answers it is the picker's job -- exactly
# the split D4/D5 already draw between a binding and an operation.
#
# Its params are the honest INTERSECTION of what every edit graph really
# wires. A param only one family honours is not declared here, because a
# form that offers it would be lying about the other: no sampler or
# scheduler (one family's schedule has no scheduler input and both fix the
# sampler), no negative prompt (one family has no negative path at all), no
# width/height (both derive the size from the image being edited), and no
# denoise (one family's sampler has no such input). LoRAs ARE declared
# (`*LORA_PARAMS`, spliced after `seed`): both edit families' bundled
# workflows carry a `LoraLoaderModelOnly`, applied through
# `_fragments.model_lora_chain` -- the checkpoint-shaped `_lora_chain`
# genuinely could not serve `edit`, but that was never a reason to exclude
# the whole control (ADR 0012 D-EDIT-9).
EDIT = Operation(
    key="edit",
    label="Edit an image",
    capability="image-generation",
    output_media="image/png",
    description=(
        "Change an existing image by describing the change in words. Everything the "
        "instruction does not ask for is left as it was."
    ),
    params=(
        Param(
            "instruction", "text", "Instruction", default="", required=True,
            description=(
                "What to change, phrased as an instruction rather than a description — "
                "\"put a red hat on the woman\", \"replace the leather with fur\". The "
                "image you attach is the thing being changed."
            ),
        ),
        # First file param on purpose: a gallery "use this image here" link
        # pre-fills an operation's FIRST file param, and the image being
        # edited is the one it should land on.
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description=(
                "The image to edit. Its own dimensions decide the output size, which is "
                "why this mode has no width or height."
            ),
        ),
        Param(
            "reference_image", "file", "Reference image", accept="image/*",
            description=(
                "An optional second image the instruction can refer to — a style to "
                "apply, a texture to borrow, a face to carry over. Leaving it empty is "
                "the normal case."
            ),
        ),
        Param(
            "guidance", "float", "Guidance", default=4.0, min=0, max=30, step=0.5,
            description=(
                "How strictly to follow the instruction. Low values drift off it, high "
                "values over-bake the image; 3-5 suits most edits."
            ),
        ),
        Param(
            "steps", "int", "Steps", default=20, min=1, max=150,
            description=(
                "How many denoising steps to run. More steps cost more time; an edit "
                "usually needs fewer than a generation from nothing."
            ),
        ),
        Param(
            "seed", "seed", "Seed", default=None,
            description=(
                "The random seed. Leave it blank for a new one every run; reuse a "
                "finished job's seed to reproduce that job exactly."
            ),
        ),
        # No `batch_size`, unlike every checkpoint-based mode: one of the
        # two bundled edit workflows carries no batching node whatsoever, so
        # a batch could only ever be honoured by half the models this
        # operation runs on. An edit produces ONE output, and each edit
        # graph pins its own latent's batch at 1.
        *LORA_PARAMS,
    ),
)
