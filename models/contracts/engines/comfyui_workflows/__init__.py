"""
ComfyUI graph templates -- one per platform operation (spec §4.4).

Each template turns a `GenerationRequest` into a ComfyUI API-format graph:
`{"<node id>": {"class_type": "<Node>", "inputs": {...}}}`, where a link is
`[<source node id>, <output index>]`. This package and `comfyui.py` are the
ONLY places that vocabulary exists.

Adding a mode is one module here plus one entry in `_TEMPLATES` plus one
`Operation` definition -- never a change to the page or the service layer,
and none to the engine adapter either: `ComfyUIEngine.supported_operations`
reads `template_keys(family)`. `_TEMPLATES` is keyed by
`(model family, operation key)`: the empty family is a single-file
checkpoint, and a multi-file family (whose graphs share almost no wiring
with a checkpoint's) gets its own entries under its own key.

A template that takes a file input reads its engine-side reference out of
the fourth argument (e.g. `inputs["init_image"]`) and never touches a
filesystem path -- the file has already been transferred by the time a
template runs.

A template module MAY also declare a module-level `IGNORES: dict[str, str]`
naming any `Operation.params` key its graph has no wiring for at all, and
list it in `_IGNORES` below -- `ignored_params` is its reader. This
annotates a param the schema still declares (ADR 0012 D-EDIT-7 still
stands: nothing here narrows, adds, or removes one), never a second way to
change what a template builds.
"""
from __future__ import annotations

from typing import Callable

from models.contracts.engines.comfyui_workflows import (
    flux2_edit, flux2_txt2img, img2img, inpaint, qwen_edit, txt2img, upscale,
)
from models.contracts.operations import GenerationRequest

# (model family, operation key) -> (request, model_id, connection config,
# engine-side input references) -> graph.
#
# The EMPTY family is "no family declared", which is what a single-file
# checkpoint connection has: a `CheckpointLoaderSimple` graph needs to know
# nothing else about the weights. A multi-file family is only runnable once
# an operator has declared which one it is (ADR 0012 D-EDIT-2 -- ComfyUI
# reports no architecture for a weights file over HTTP), and each family
# gets its OWN graphs, because a Flux-shaped pipeline and a Qwen-shaped one
# share almost no wiring.
#
# The family keys are ComfyUI's OWN `CLIPLoader.type` combo values -- the
# string a CLIP loader node is literally given as its `type` input. They
# are graph vocabulary, which is why they live here and nowhere above this
# package (spec §3, ADR 0012 D1); they are not, and must never become, a
# list of model names.
#
# The fourth template argument is what `ComfyUIGenerator._upload_inputs`
# got back for each of `request.inputs`: `{param key: the string a ComfyUI
# node's image input takes}`. It is empty for an operation with no file
# params.
Template = Callable[[GenerationRequest, str, dict, dict], dict]

_TEMPLATES: dict[tuple[str, str], Template] = {
    ("", "txt2img"): txt2img.build,
    ("", "img2img"): img2img.build,
    ("", "inpaint"): inpaint.build,
    ("", "upscale"): upscale.build,
    ("flux2", "edit"): flux2_edit.build,
    ("flux2", "txt2img"): flux2_txt2img.build,
    ("qwen_image", "edit"): qwen_edit.build,
}


def get_template(operation_key: str, family: str = "") -> Template:
    """The graph builder for `operation_key` under `family`.

    Raises `ValueError` naming BOTH when this engine has no such template --
    the console never offers such a pairing (role options are filtered by
    capability and `supported_operations`, which reads this same registry),
    so reaching this is a bug, and it says so plainly.
    """
    try:
        return _TEMPLATES[(family, operation_key)]
    except KeyError:
        raise ValueError(
            f"No ComfyUI template for operation {operation_key!r} in family "
            f"{family!r}; this engine implements "
            f"{sorted(f'{fam}:{op}' for fam, op in _TEMPLATES)}"
        ) from None


def template_keys(family: str = "") -> tuple[str, ...]:
    """Every operation key this engine has a template for in `family`, in
    registration order.

    `ComfyUIEngine.supported_operations` returns this rather than a
    hand-maintained tuple, so adding a template really is the only edit --
    which is what this package's docstring has always claimed. A family
    with no templates answers `()`: an operator who declared a family this
    adapter cannot run is told so honestly, never handed a checkpoint graph.
    """
    return tuple(op for fam, op in _TEMPLATES if fam == family)


def families() -> tuple[str, ...]:
    """Every non-empty family key with at least one template, in
    registration order -- what the adapter can honestly offer an operator to
    declare at registration time.

    The empty key is excluded on purpose: "no family" is not a family an
    operator picks, it is what a single-file checkpoint already is.
    """
    ordered: list[str] = []
    for family, _operation in _TEMPLATES:
        if family and family not in ordered:
            ordered.append(family)
    return tuple(ordered)


# (family, variant) -> {operation key: {param key: the default that variant
# wants}}.
#
# A VARIANT is a second graph for the SAME family: same loaders, same
# `CLIPLoader.type`, same latent format -- different wiring, because the
# weights were distilled differently. It is an operator DECLARATION on the
# connection (`config["variant"]`, ADR 0012 D-EDIT-6), because nothing
# ComfyUI serves over HTTP distinguishes the two files, and this platform
# never infers one from a filename.
#
# Unlike a family, a variant is NOT a word the engine knows: it names which
# of a family's graphs THIS package has code for, so this mapping is the
# entire vocabulary. The defaults are widget values transcribed from the
# bundled workflow the variant's graph was derived from -- facts about a
# graph, which is why they live here and not in `models.contracts.operations`
# (ADR 0012 D-EDIT-7).
_VARIANTS: dict[tuple[str, str], dict[str, dict]] = {
    ("flux2", flux2_edit.DISTILLED): {
        "edit": flux2_edit.DISTILLED_DEFAULTS,
        "txt2img": flux2_txt2img.DISTILLED_DEFAULTS,
    },
    # FAMILY-LEVEL defaults -- the empty "variant" key, meaning "whatever
    # ordinary (non-distilled) graph this family builds", not "no variant
    # declared" (that reading stays `variant_defaults`'s own `{}` answer for
    # any pairing this dict has no entry for at all -- an unregistered
    # connection asks for defaults just like a registered one and must get
    # the same honest `{}`, never a `KeyError`). `flux2`'s ordinary graph
    # has widget facts of its own (`FluxGuidance.guidance`, transcribed in
    # `flux2_txt2img.DEFAULTS`) that are true regardless of which variant a
    # connection later declares, so they belong here rather than under a
    # variant key nothing names.
    ("flux2", ""): {"txt2img": flux2_txt2img.DEFAULTS},
}


def variants() -> tuple[str, ...]:
    """Every non-empty variant this package implements, in registration
    order, deduped.

    Deliberately NOT per-family. The one caller is a registration form that
    has not chosen a family yet -- one plain HTML form, no JavaScript, so it
    cannot re-fetch a list when the family select changes. It offers the
    union, and a pairing that does not exist simply builds the family's
    ordinary graph (`variant_defaults` is keyed by `(family, variant)` and
    answers `{}`). A per-family argument with no caller would be a parameter
    the tests exercise and nothing else does.

    The empty key is excluded on purpose, same reasoning as `families()`'s
    own exclusion: it is not a variant an operator picks, it is `_VARIANTS`'
    place to hold a family's OWN (non-distilled) widget facts, which
    `variant_defaults` already reaches without an operator ever declaring
    a variant at all.
    """
    ordered: list[str] = []
    for _family, variant in _VARIANTS:
        if variant and variant not in ordered:
            ordered.append(variant)
    return tuple(ordered)


def variant_defaults(operation_key: str, family: str = "", variant: str = "") -> dict:
    """The param defaults `(family, variant)` wants for `operation_key`, or
    `{}` -- for an unknown pairing, or an operation that variant changes
    nothing about. Never raises: a stored declaration is operator text and
    this is a lookup, not a validation.

    `variant=""` is a real, deliberate lookup here, not a special case:
    `_VARIANTS` can hold a `(family, "")` entry for a family's OWN widget
    facts (see its comment), so a connection that never declared a variant
    still gets the family's honest defaults rather than an unconditional
    `{}`. A family with no such entry answers `{}` all the same, from the
    plain dict lookup below.
    """
    return dict(_VARIANTS.get((family, variant), {}).get(operation_key, {}))


# (family, operation key) -> {param key: why this family's template for
# that operation cannot honour it}. A template module declares its own
# `IGNORES` (see `flux2_txt2img.IGNORES`) when `Operation.params` names a
# control the underlying graph has no wiring for at all -- distinct from
# `variant_defaults` above, which never narrows or removes a param (ADR
# 0012 D-EDIT-7): this DOES say a param is unusable, in the operator's own
# words, for the console to show as a reason rather than a silently ignored
# field.
_IGNORES: dict[tuple[str, str], dict[str, str]] = {
    ("flux2", "txt2img"): flux2_txt2img.IGNORES,
}


def ignored_params(operation_key: str, family: str = "") -> dict[str, str]:
    """The operator-facing reason each param `(family, operation_key)`'s
    template cannot honour, or `{}` -- for a pairing with nothing to
    ignore, an unregistered pairing, or a family that builds no such graph
    at all. Never raises, same as `variant_defaults`: a stored declaration
    is operator text and this is a lookup, not a validation."""
    return dict(_IGNORES.get((family, operation_key), {}))
