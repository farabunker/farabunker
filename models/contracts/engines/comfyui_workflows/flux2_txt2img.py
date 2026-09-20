"""Text-to-image generation for the `flux2` model family -- the same two
graphs (ordinary / guidance-distilled) `flux2_edit` builds, minus the
reference-image plumbing and instruction encode an edit needs.

The ordinary graph is derived node-for-node from the "Text to Image (Flux.2
Dev)" workflow ComfyUI 0.33.0 ships in its own
`comfyui_workflow_templates_json` bundle, minus that workflow's
`ComfySwitchNode` / `PrimitiveInt` / `PrimitiveBoolean` TOGGLE cluster -- the
same UI convenience `flux2_edit` already strips, not a pipeline fact. Its
`LoraLoaderModelOnly` stays for the same reason `edit`'s does: a real
operator param, applied through `_fragments.model_lora_chain` straight after
the loader (ADR 0012 D-EDIT-9) -- this family's bundled txt2img workflow
puts the node at that same position.

The distilled graph is derived node-for-node from the "Text to Image (Flux.2
Klein 4B Distilled)" subgraph -- a SECOND subgraph inside
`image_flux2_klein_text_to_image.json`, sibling to that same file's
ordinary "Text to Image (Flux.2 Klein 4B)" one. That sibling is NOT what
the ordinary graph above is drawn from, and this module does not build its
shape at all (`CFGGuider` at `cfg=5` over an empty-text `CLIPTextEncode`
negative, 20 steps) -- the ordinary graph comes from the entirely
DIFFERENT bundle named above, `image_flux2_text_to_image.json`'s "Text to
Image (Flux.2 Dev)". The distilled graph differs from the ordinary one in
exactly the same three ways `flux2_edit`'s own distilled graph differs from
its ordinary one: no `FluxGuidance` at all (the weights are
guidance-distilled), a `CFGGuider` whose negative is a `ConditioningZeroOut`
of the same encoded prompt instead of a `BasicGuider`'s single conditioning,
and its own step/cfg widget values (`DISTILLED_DEFAULTS` below) -- which
this bundled subgraph gives as `Flux2Scheduler.steps = 4`,
`CFGGuider.cfg = 1`, the SAME numbers `flux2_edit.DISTILLED_DEFAULTS`
already reports, because both templates build one guidance-distilled BUILD
of this family. That identity is exactly why the guider/negative wiring is
SHARED code (`_fragments.cfg_guider`/`.zeroed_conditioning`,
`.basic_guider`/`.flux_guidance`) rather than written out a second time here
-- one guidance-distilled build has one guider shape, whether the operation
asking for it is an edit or a plain generation.

Three real differences from `edit`, all because txt2img starts from nothing
rather than an operator's own picture:
- `EmptyFlux2LatentImage`/`Flux2Scheduler` take `width`/`height` as this
  operation's OWN params, as plain ints, rather than links off a
  `GetImageSize` read of an input image -- there is no input image.
- `EmptyFlux2LatentImage.batch_size` comes from `TXT2IMG`'s own
  `batch_size` param rather than the `1` `edit` pins (R1: one output per
  edit, many per txt2img).
- `KSamplerSelect.sampler_name` comes from `TXT2IMG`'s own `sampler` param
  rather than `flux2_edit.SAMPLER`'s pinned `"euler"` -- `edit` pins it
  because `EDIT` declares no `sampler` param at all; `TXT2IMG` does, and the
  node honours whatever this operation is given.

`IGNORES` below documents the two `TXT2IMG` params this family's graphs
still cannot honour: no negative-prompt path exists on the ordinary graph
(the distilled graph's own "negative" is a fixed zeroed copy of the
positive, not an operator-writable prompt), and `Flux2Scheduler` has no
scheduler input at all -- both true of `edit` too (ADR 0012 D-EDIT-1).

`family`, `text_encoder`, and `vae` come from the connection's `config`
(D8, ADR 0012 D-EDIT-2/3); `variant` selects which of the two graphs below
this module builds (D8, ADR 0012 D-EDIT-6) -- same as `edit`.
"""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments, flux2_edit
from models.contracts.operations import GenerationRequest

# Params `TXT2IMG` declares that neither flux2 graph can honour, with the
# operator-facing reason each is skipped. Surfaced as hover text once the
# constant-form work picks this up (CLAUDE.local.md, "vision next priority");
# `build` never reads either key.
IGNORES: dict[str, str] = {
    "negative_prompt": (
        "This model has no negative-prompt input; describe what you want in "
        "the prompt instead."
    ),
    "scheduler": "This model's own noise schedule has no separate setting to choose.",
}

# The ordinary graph's `FluxGuidance.guidance` widget, transcribed from
# ComfyUI's own "Text to Image (Flux.2 Dev)" bundle -- the same value the
# undistilled graph's own bundled workflow gives `FluxGuidance`. `EDIT`'s
# own ordinary graph starts an operator at this same `4.0`, but from
# `EDIT.params`'s own schema default (`Param("guidance", ..., default=4.0)`,
# `models/contracts/operations.py`) rather than from `variant_defaults` at
# all -- `edit`'s schema was written flux2-first, so its default already
# matched the bundle. `TXT2IMG.params`'s `cfg_scale` default is a generic
# `7.0` (right for an arbitrary SD checkpoint, wrong for this family's own
# graph), which is exactly why `flux2`'s ordinary txt2img needs this
# FAMILY-LEVEL entry in `comfyui_workflows._VARIANTS` at all.
DEFAULTS = {"cfg_scale": 4.0}

# The distilled graph's widgets, transcribed from the "Text to Image
# (Flux.2 Klein 4B Distilled)" subgraph: `Flux2Scheduler.steps` = 4,
# `CFGGuider.cfg` = 1. Read off `flux2_edit.DISTILLED_DEFAULTS` rather than
# transcribed a second time -- not a coincidence that the two agree, but the
# same fact stated once: one guidance-distilled BUILD of this family, one
# set of honest numbers, aliased here rather than duplicated. The key is
# `cfg_scale`, not `guidance`: `TXT2IMG.params`
# (`models/contracts/operations.py`) names its CFG control `cfg_scale`,
# where `EDIT.params` names its `guidance`.
DISTILLED_DEFAULTS = {
    "steps": flux2_edit.DISTILLED_DEFAULTS["steps"],
    "cfg_scale": flux2_edit.DISTILLED_DEFAULTS["guidance"],
}


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one `flux2` txt2img request.

    `inputs` is unused -- `TXT2IMG` declares no file params -- and accepted
    only to keep this module's `build` the same shape every template in
    this package has.

    `config.get("variant") == flux2_edit.DISTILLED` branches to the
    distilled wiring; anything else (absent, blank, unrecognized) builds
    the ordinary graph -- the same degrade-to-ordinary rule `edit` applies
    (ADR 0012 D-EDIT-6).
    """
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)
    # This family's bundled txt2img workflow puts the adapter straight
    # after the loader too; nothing else wraps the model here before the
    # guider (ADR 0012 D-EDIT-9).
    model = _fragments.model_lora_chain(graph, parts.model, params)
    distilled = str(config.get("variant") or "") == flux2_edit.DISTILLED
    cfg_scale = float(params["cfg_scale"])

    text = _fragments.encode_text(graph, parts, params.get("prompt"))
    # Distilled: the prompt goes in unguided, and its ZEROED copy is the
    # negative half of a CFG pair. Undistilled: one guided conditioning and
    # no negative path at all -- `IGNORES["negative_prompt"]` is why.
    positive = text if distilled else _fragments.flux_guidance(graph, text, cfg_scale)
    negative = _fragments.zeroed_conditioning(graph, text) if distilled else None

    guider = (
        _fragments.cfg_guider(graph, model, positive, negative, cfg_scale)
        if distilled
        else _fragments.basic_guider(graph, model, positive)
    )
    latent = _fragments.flux2_empty_latent(
        graph, params["width"], params["height"], params["batch_size"]
    )
    sigmas = _fragments.flux2_sigmas(graph, params["steps"], params["width"], params["height"])
    samples = _fragments.flux2_sample(
        graph, guider, sigmas, latent, params["seed"], params["sampler"]
    )
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()
