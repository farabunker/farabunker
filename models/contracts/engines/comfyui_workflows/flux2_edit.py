"""Instruction-based image editing for the `flux2` model family -- TWO
graphs, one ordinary and one for a guidance-distilled build.

The ordinary graph is derived node-for-node from the "Image Edit (Flux.2
Dev)" workflow ComfyUI 0.33.0 ships in its own
`comfyui_workflow_templates_json` bundle, minus that workflow's
`ComfySwitchNode` / `PrimitiveInt` / `PrimitiveBoolean` TOGGLE cluster -- a
UI convenience for a graph builder, not a pipeline fact. Its
`LoraLoaderModelOnly` stays, as a real operator param: `edit` splices in
`models.contracts.operations.LORA_PARAMS` and applies the selection through
`_fragments.model_lora_chain` (the model-only sibling of `_lora_chain`) at
THIS family's own position in the chain -- straight after the loader,
since nothing else wraps the model here (ADR 0012 D-EDIT-9). The
guidance-distilled Klein bundle below ships no `LoraLoaderModelOnly` of its
own; the same position is used for it too, mirroring the ordinary graph's
`image_flux2.json` placement since both graphs share every model-wrapping
step up to that point.

The pipeline: load the three declared files, encode the instruction once and
guide it, then chain one `ReferenceLatent` per input image so the model can
see what it is editing. Chain order IS the reference numbering an
instruction can refer to -- the image being edited is Reference Image 1.
Sampling is `SamplerCustomAdvanced` over this family's own scheduler, whose
sigmas are computed from the edited image's real size, so nothing here
rescales the operator's picture.

The distilled graph (`DISTILLED`, ADR 0012 D-EDIT-6) is derived node-for-node
from the "Image Edit (Flux.2 Klein 9B Distilled)" subgraphs in the same
ComfyUI bundle. It differs from the ordinary graph in exactly three ways: no
`FluxGuidance` at all (the weights are guidance-distilled), a `CFGGuider`
whose negative is a `ConditioningZeroOut` of the same encoded instruction
instead of a `BasicGuider`'s single conditioning, and its own step/cfg widget
values (`DISTILLED_DEFAULTS`). Everything else -- the loaders, the reference
chaining, the scheduler, the sampler -- is identical.

`family`, `text_encoder`, and `vae` come from the connection's `config`
(D8, ADR 0012 D-EDIT-2/3) -- declared by the operator at registration time,
never inferred from a filename. `variant` comes from the same `config` (D8,
ADR 0012 D-EDIT-6) and selects which of the two graphs above this module
builds.
"""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest

# The sampler this family's shipped edit workflow selects. Fixed here rather
# than exposed as a param: the schedule comes from `Flux2Scheduler`, which
# has no scheduler input at all, so a sampler/scheduler pair on the form
# would be half a control (ADR 0012 D-EDIT-1).
SAMPLER = "euler"

# The one variant of this family this module has a graph for (ADR 0012
# D-EDIT-6). Derived node-for-node from the "Image Edit (Flux.2 Klein 9B
# Distilled)" subgraphs in ComfyUI 0.33.0's own template bundle, whose two
# copies differ from the undistilled workflow in exactly three ways: no
# `FluxGuidance` (the weights are guidance-distilled), a `CFGGuider` whose
# negative is the zeroed-out instruction instead of a `BasicGuider`, and
# their own step/cfg widget values.
DISTILLED = "distilled"

# Those widget values: `Flux2Scheduler.steps` = 4, `CFGGuider.cfg` = 1.
# Reported to the platform through `variant_defaults` so the form STARTS
# where this graph expects, while `guidance` and `steps` stay ordinary
# operator params (ADR 0012 D-EDIT-7).
DISTILLED_DEFAULTS = {"steps": 4, "guidance": 1.0}


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one `flux2` edit request.

    `inputs["init_image"]` is the reference ComfyUI gave the transferred
    file and is INDEXED, not `.get()`: it is a required file param, so an
    absent reference means the layer above skipped the transfer, and a
    `KeyError` naming the param is a far better report than a graph ComfyUI
    rejects for `image: None`. `reference_image` is optional and is read
    with `.get`.

    `config.get("variant") == DISTILLED` branches to the distilled wiring;
    anything else (absent, blank, unrecognized) builds the ordinary graph --
    the behaviour every connection registered before variants existed
    already has (ADR 0012 D-EDIT-6).
    """
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)
    # This family's bundled workflow puts the adapter straight after the
    # loader; nothing else wraps the model here (ADR 0012 D-EDIT-9).
    model = _fragments.model_lora_chain(graph, parts.model, params)
    distilled = str(config.get("variant") or "") == DISTILLED
    guidance = float(params["guidance"])

    text = _fragments.encode_text(graph, parts, params.get("instruction"))
    # Distilled: the instruction goes in unguided, and its ZEROED copy is
    # the negative half of a CFG pair. Undistilled: one guided conditioning
    # and no negative path at all, which is why `edit` declares no negative
    # prompt for either.
    positive = text if distilled else _fragments.flux_guidance(graph, text, guidance)
    negative = _fragments.zeroed_conditioning(graph, text) if distilled else None

    # Reference Image 1: the picture being edited. Its size is the output's.
    edited = _fragments.scale_to_megapixels(
        graph, _fragments.load_image(graph, inputs["init_image"])
    )
    width, height = _fragments.image_size(graph, edited)
    latents = [_fragments.encode_image(graph, parts, edited)]

    # Reference Image 2, when the operator attached one.
    reference = inputs.get("reference_image")
    if reference:
        extra = _fragments.scale_to_megapixels(graph, _fragments.load_image(graph, reference))
        latents.append(_fragments.encode_image(graph, parts, extra))

    # Chain order IS the reference numbering, and every branch sees the same
    # latents in the same order -- the bundled distilled two-image subgraph
    # chains the edited image first on BOTH branches. Each branch's chain is
    # built to completion before the other starts (positive over every
    # latent, then negative over every latent) because that is the bundled
    # subgraph's own shape: two independent chains, not an interleaving of
    # the two (link ids 201-216 of
    # `image_flux2_klein_image_edit_9b_distilled.json` chain the positive
    # branch's own two `ReferenceLatent`s together, then the negative
    # branch's own two, never alternating between them).
    for latent in latents:
        positive = _reference_latent(graph, positive, latent)
    if negative is not None:
        for latent in latents:
            negative = _reference_latent(graph, negative, latent)

    guider = (
        _fragments.cfg_guider(graph, model, positive, negative, guidance)
        if distilled
        else _fragments.basic_guider(graph, model, positive)
    )
    # `batch_size` is pinned at 1 rather than taken from a param: `edit`
    # declares none, because the other edit family's bundled workflow has no
    # batching node at all and a control only half the models honour is a
    # lie (ADR 0012 D-EDIT-1).
    latent = _fragments.flux2_empty_latent(graph, width, height, 1)
    sigmas = _fragments.flux2_sigmas(graph, params["steps"], width, height)
    samples = _fragments.flux2_sample(graph, guider, sigmas, latent, params["seed"], SAMPLER)
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()


def _reference_latent(graph: _fragments.Graph, conditioning: list, latent: list) -> list:
    """Attach one encoded image to the conditioning as a reference. Chained
    once per input image, in the order the operation declares them, on
    EVERY conditioning branch the variant builds."""
    return [graph.add("ReferenceLatent", conditioning=conditioning, latent=latent), 0]
