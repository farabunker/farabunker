"""Instruction-based image editing for the `qwen_image` model family.

Derived node-for-node from the "Image Edit (Qwen-Image 2511)" workflow
ComfyUI 0.33.0 ships in its own `comfyui_workflow_templates_json` bundle,
minus that workflow's `ComfySwitchNode` / `PrimitiveInt` / `PrimitiveFloat`
TOGGLE cluster -- a UI convenience for a graph builder, not a pipeline
fact. Its `LoraLoaderModelOnly` stays, as a real operator param: `edit`
splices in `models.contracts.operations.LORA_PARAMS` and applies the
selection through `_fragments.model_lora_chain` (the model-only sibling of
`_lora_chain`) at THIS family's own position -- after
`ModelSamplingAuraFlow` -> `CFGNorm`, straight before the sampler (ADR
0012 D-EDIT-9).

Same operation, an entirely different pipeline from the other edit family:
the model passes through a two-stage chain before sampling, the instruction
and an empty negative are encoded by one node that takes the IMAGES as well
as the text, both conditionings pass through a reference-method node, and
sampling is a plain `KSampler` from the input image's own latent, with no
batching node between the two (the bundled workflow has none, which is one
of the reasons `edit` declares no `batch_size`). This is exactly why the
template registry is keyed by `(family, operation)`.

Reference numbering: the bundled workflow's subgraph input labeled
"image1" is the picture being EDITED (it alone is fed through
`FluxKontextImageScale` before it reaches `TextEncodeQwenImageEditPlus`'s
own `image1`); its "image2" input is the second, unscaled reference,
wired straight to that same node's `image2`. Qwen's numbering is in
attachment order: the edited image is `image1`, the optional second
reference is `image2`. Confirmed against the bundled
`image_qwen_image_edit_2511.json` template's own link graph, not assumed.
The other edit family agrees: the `flux2` family's own bundled distilled
two-image subgraph also chains the edited image first, on BOTH its
conditioning branches (link ids 201-216 of
`image_flux2_klein_image_edit_9b_distilled.json`, decoded in
`docs/superpowers/plans/2026-08-25-vision-distilled-fewstep.md`'s "Verified
facts" -- `flux2_edit.py`'s own `build` reproduces it). Both families number
references the same way.

`family`, `text_encoder`, and `vae` come from the connection's `config`
(D8, ADR 0012 D-EDIT-2/3) -- declared by the operator at registration time,
never inferred from a filename.
"""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest

# Fixed graph facts from this family's shipped edit workflow. None of them is
# an operator param: each is honoured by this family alone, and `edit`
# declares only what EVERY edit family wires (ADR 0012 D-EDIT-1).
SHIFT = 3.1
CFG_NORM_STRENGTH = 1.0
REFERENCE_METHOD = "index_timestep_zero"
SAMPLER = "euler"
SCHEDULER = "simple"


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one `qwen_image` edit request.

    `inputs["init_image"]` is INDEXED (a required file param — an absent
    reference is a bug above this line and should name itself);
    `reference_image` is optional and read with `.get`.
    """
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)

    # Only the image being EDITED is resized, exactly as the shipped
    # workflow wires it: a second reference goes in at its own size.
    edited = _kontext_scale(graph, _fragments.load_image(graph, inputs["init_image"]))
    images = [edited]
    reference = inputs.get("reference_image")
    if reference:
        images.append(_fragments.load_image(graph, reference))

    positive = _conditioning(graph, parts, params.get("instruction"), images)
    negative = _conditioning(graph, parts, "", images)

    latent = _fragments.encode_image(graph, parts, edited)
    # This family's bundled workflow puts the adapter after its own
    # `ModelSamplingAuraFlow` -> `CFGNorm` pair, straight before the sampler
    # (ADR 0012 D-EDIT-9).
    model = _fragments.model_lora_chain(graph, _model_chain(graph, parts.model), params)
    samples = _sample(graph, model, positive, negative, latent, params)
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()


def _kontext_scale(graph: _fragments.Graph, image: _fragments.Link) -> _fragments.Link:
    """This family's own input normalizer. It takes no megapixel budget --
    which is precisely why `edit` declares no megapixels param."""
    return [graph.add("FluxKontextImageScale", image=image), 0]


def _model_chain(graph: _fragments.Graph, model: _fragments.Link) -> _fragments.Link:
    """The two model-wrapping nodes this family's shipped workflow puts
    between its loader and its sampler."""
    shifted = graph.add("ModelSamplingAuraFlow", model=model, shift=SHIFT)
    normed = graph.add("CFGNorm", model=[shifted, 0], strength=CFG_NORM_STRENGTH)
    return [normed, 0]


def _conditioning(
    graph: _fragments.Graph,
    parts: _fragments.Checkpoint,
    prompt: str | None,
    images: list,
) -> _fragments.Link:
    """One conditioning: this family's text encoder takes the IMAGES as well
    as the text, and the result passes through a reference-method node
    before it reaches the sampler.

    Called twice -- once with the instruction, once with the empty string --
    because this family's sampler takes a positive AND a negative, and the
    shipped workflow leaves the negative's prompt empty. That is why `edit`
    declares no negative-prompt param: there is nothing honest for an
    operator to put there on the other family.
    """
    node_inputs = {"clip": parts.clip, "vae": parts.vae, "prompt": prompt or ""}
    for index, image in enumerate(images, start=1):
        node_inputs[f"image{index}"] = image
    encoded = graph.add("TextEncodeQwenImageEditPlus", **node_inputs)
    method = graph.add(
        "FluxKontextMultiReferenceLatentMethod",
        conditioning=[encoded, 0],
        reference_latents_method=REFERENCE_METHOD,
    )
    return [method, 0]


def _sample(
    graph: _fragments.Graph,
    model: _fragments.Link,
    positive: _fragments.Link,
    negative: _fragments.Link,
    latent: _fragments.Link,
    params: dict,
) -> _fragments.Link:
    """A plain `KSampler` at full denoise. `guidance` lands on `cfg` here and
    on the other family's own guidance node -- one operator control, two
    honest wirings."""
    node = graph.add(
        "KSampler",
        model=model,
        seed=params["seed"],
        steps=params["steps"],
        cfg=float(params["guidance"]),
        sampler_name=SAMPLER,
        scheduler=SCHEDULER,
        positive=positive,
        negative=negative,
        latent_image=latent,
        denoise=1.0,
    )
    return [node, 0]
