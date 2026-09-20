"""
The ONE ComfyUI graph vocabulary, shared by every template in this package.

A template describes a mode; the nodes it strings together are not the
mode. txt2img, img2img, and inpaint all load a checkpoint, encode two
prompts, sample, decode, and save -- written out four times that is four
places to get a link index wrong. Written here once, a template is the
five lines that are actually different.

Node ids are ALLOCATED, never hand-written: `Graph.add` returns the id it
assigned and a fragment hands back the LINK (`[node_id, output_index]`)
its caller needs, so inserting a node (a LoRA chain, a batch repeat) can
never silently renumber a link someone typed by hand.

This module is the deepest part of the engine-vocabulary boundary
(spec §3): node class names and link shapes live here and in `comfyui.py`
and nowhere above.
"""
from __future__ import annotations

from dataclasses import dataclass

from models.contracts.engines.base import GenerationRejected
from models.contracts.operations import GenerationRequest

# A link in ComfyUI's API format: `[<source node id>, <output index>]`.
Link = list


class Graph:
    """An API-format ComfyUI graph under construction.

    Ids are the string integers ComfyUI's own frontend emits ("1", "2",
    ...), allocated in call order. Nothing reads them back apart from the
    links this module builds, so their only real job is to be unique and
    stable within one submission.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}

    def add(self, class_type: str, **inputs) -> str:
        """Append a node and return the id it was given."""
        node_id = str(len(self._nodes) + 1)
        self._nodes[node_id] = {"class_type": class_type, "inputs": dict(inputs)}
        return node_id

    def as_dict(self) -> dict:
        """The graph as the `/prompt` body's `prompt` value.

        A shallow copy of the id map, and deliberately no deeper: every
        template's last statement is `return graph.as_dict()` and
        `ComfyUIGenerator.submit` posts that dict and stores it verbatim
        (D6) without touching it, so there is no caller to defend the node
        dicts from.
        """
        return dict(self._nodes)


@dataclass(frozen=True)
class Checkpoint:
    """The three links a checkpoint loader yields, named rather than
    indexed. `CheckpointLoaderSimple` outputs MODEL, CLIP, VAE in that
    order; every template downstream wants them by name."""

    model: Link
    clip: Link
    vae: Link


def _declared(config: dict, key: str, what: str) -> str:
    """One operator-declared companion from the connection's `config` (D8),
    or a refusal in the operator's own terms.

    A multi-file family is only runnable once all three of `family`,
    `text_encoder`, and `vae` have been declared at registration time (ADR
    0012 D-EDIT-2/3). A half-registered connection must fail the JOB with a
    sentence that says what to do -- `GenerationRejected` is exactly that
    signal, and `tools.vision.services.submit_job` already turns it into
    an immediately-failed job carrying this text.
    """
    value = str(config.get(key) or "").strip()
    if not value:
        raise GenerationRejected(
            f"This model's connection does not name {what}. Register it again with "
            "a model family, a text encoder, and a VAE."
        )
    return value


def _diffusion_model(graph: Graph, model_id: str) -> Link:
    """Load a diffusion-model file with the loader that can read it.

    ComfyUI-GGUF's `UnetLoaderGGUF` reads a `.gguf`; the built-in
    `UNETLoader` reads a safetensors (and takes a `weight_dtype` the GGUF
    loader has no input for). The EXTENSION of the file the operator named
    decides -- a fact of that file, never a pattern matched against a list
    of model names this platform ships.
    """
    if model_id.lower().endswith(".gguf"):
        return [graph.add("UnetLoaderGGUF", unet_name=model_id), 0]
    return [graph.add("UNETLoader", unet_name=model_id, weight_dtype="default"), 0]


def _text_encoder(graph: Graph, clip_name: str, family: str) -> Link:
    """Load the connection's declared text encoder, as the declared family's
    `type`. Same extension rule as `_diffusion_model`, for the same reason.

    `CLIPLoaderGGUF` takes NO `device` input (verified against a live
    ComfyUI 0.33.0), unlike the built-in `CLIPLoader` -- passing one would
    be a graph ComfyUI rejects.
    """
    if clip_name.lower().endswith(".gguf"):
        return [graph.add("CLIPLoaderGGUF", clip_name=clip_name, type=family), 0]
    return [
        graph.add("CLIPLoader", clip_name=clip_name, type=family, device="default"), 0
    ]


def components(graph: Graph, model_id: str, config: dict) -> Checkpoint:
    """A multi-file family's three loaders, handed back with the SAME names
    a checkpoint loader's three outputs have -- so every fragment downstream
    (`encode_text`, `encode_image`, `decode_and_save`) works
    unchanged for a family pipeline.

    `config` is the bound connection's own `ModelConnection.config` (D8):
    `family`, `text_encoder`, and `vae`, all three declared by the operator
    at registration time. `model_id` is ComfyUI's own opaque string and is
    passed through untouched.
    """
    family = _declared(config, "family", "a model family")
    return Checkpoint(
        model=_diffusion_model(graph, model_id),
        clip=_text_encoder(graph, _declared(config, "text_encoder", "a text encoder"), family),
        vae=[graph.add("VAELoader", vae_name=_declared(config, "vae", "a VAE")), 0],
    )


def encode_text(graph: Graph, ckpt: Checkpoint, text: str) -> Link:
    """Encode ONE piece of text against the loaded CLIP.

    A param the operation does not declare (or declares and left blank)
    encodes as the empty string: `None` is not text ComfyUI can encode.
    """
    return [graph.add("CLIPTextEncode", text=text or "", clip=ckpt.clip), 0]


def scale_to_megapixels(graph: Graph, image: Link, megapixels: float = 1.0) -> Link:
    """Resample an image to a total pixel budget, the way every edit graph
    this engine ships normalizes its input before encoding it.

    `upscale_method`, `megapixels`, and `resolution_steps` are fixed graph
    facts, not operator params: only one of the two edit families has a node
    with a megapixel input at all, so exposing one would be a control that
    silently does nothing for the other (ADR 0012 D-EDIT-1).
    """
    return [
        graph.add(
            "ImageScaleToTotalPixels",
            image=image,
            upscale_method="area",
            megapixels=megapixels,
            resolution_steps=1,
        ),
        0,
    ]


def image_size(graph: Graph, image: Link) -> tuple[Link, Link]:
    """`(width, height)` links read off an image already in the graph -- so
    an edit's output size is the operator's own picture's size and never a
    number this platform invented."""
    node = graph.add("GetImageSize", image=image)
    return [node, 0], [node, 1]


def _lora_chain(graph: Graph, ckpt: Checkpoint, params: dict) -> Checkpoint:
    """Chain one `LoraLoader` per selected LoRA, MODEL->MODEL and
    CLIP->CLIP, and hand back the adorned links.

    Both strengths take the operation's single `lora_strength`: the schema
    has one value per param, so a per-LoRA strength would need a param kind
    that carries options per item -- honestly out of scope, and recorded as
    such in `tools/vision/README.md` rather than faked here.

    The VAE is NOT rewired: `LoraLoader` outputs MODEL and CLIP only.
    """
    names = params.get("loras") or ()
    if isinstance(names, str):
        names = (names,)
    strength = float(params.get("lora_strength") or 1.0)
    adorned = ckpt
    for name in names:
        node = graph.add(
            "LoraLoader",
            lora_name=name,
            strength_model=strength,
            strength_clip=strength,
            model=adorned.model,
            clip=adorned.clip,
        )
        adorned = Checkpoint(model=[node, 0], clip=[node, 1], vae=ckpt.vae)
    return adorned


def model_lora_chain(graph: Graph, model: Link, params: dict) -> Link:
    """Chain one `LoraLoaderModelOnly` per selected adapter, MODEL->MODEL,
    and hand back the adorned link.

    The MODEL-ONLY sibling of `_lora_chain` above, and a separate function
    rather than a flag on it: that one rewires CLIP as well because a
    checkpoint's `LoraLoader` outputs both, while this node outputs MODEL
    alone (verified against a live ComfyUI 0.33.0 -- there is no
    `strength_clip` input to pass). Both edit families' bundled workflows
    use exactly this node, which is what makes a LoRA param honest for
    `edit` at all (ADR 0012 D-EDIT-9).

    Called by each edit template at the position ITS bundled workflow puts
    the node: last model step before the guider or sampler.

    One strength for the whole selection, like every other mode -- a `Param`
    carries one value.
    """
    names = params.get("loras") or ()
    if isinstance(names, str):
        names = (names,)
    strength = float(params.get("lora_strength") or 1.0)
    for name in names:
        model = [
            graph.add(
                "LoraLoaderModelOnly", model=model, lora_name=name, strength_model=strength
            ),
            0,
        ]
    return model


def checkpoint(graph: Graph, model_id: str, params: dict) -> Checkpoint:
    """Load the bound checkpoint and apply any adornments the operation's
    params carry (today: the LoRA chain).

    `model_id` is ComfyUI's own opaque string (a Windows host reports
    `subdir\\file.safetensors`) and is passed through untouched.

    Adornments live HERE rather than in each template so every
    checkpoint-based mode -- txt2img, img2img, inpaint, and whatever comes
    next -- gets them from the same three lines. A mode that declares no
    `loras` param simply gets no extra nodes.
    """
    node = graph.add("CheckpointLoaderSimple", ckpt_name=model_id)
    return _lora_chain(graph, Checkpoint(model=[node, 0], clip=[node, 1], vae=[node, 2]), params)


def prompts(graph: Graph, ckpt: Checkpoint, params: dict) -> tuple[Link, Link]:
    """Encode the positive and negative prompts, in that order."""
    return (
        encode_text(graph, ckpt, params.get("prompt")),
        encode_text(graph, ckpt, params.get("negative_prompt")),
    )


def empty_latent(graph: Graph, params: dict) -> Link:
    """A blank latent at the requested size and batch -- what txt2img
    samples from when there is no input image."""
    node = graph.add(
        "EmptyLatentImage",
        width=params["width"],
        height=params["height"],
        batch_size=params["batch_size"],
    )
    return [node, 0]


def load_image(graph: Graph, reference: str) -> Link:
    """Load a file the engine already holds. `reference` is whatever
    `ImageGenerator.submit` got back from the transfer -- never a path on
    the platform's filesystem."""
    node = graph.add("LoadImage", image=reference)
    return [node, 0]


def encode_image(graph: Graph, ckpt: Checkpoint, image: Link) -> Link:
    """Pixels -> latent, for a mode that starts from an image."""
    node = graph.add("VAEEncode", pixels=image, vae=ckpt.vae)
    return [node, 0]


def repeat_batch(graph: Graph, latent: Link, amount: int) -> Link:
    """Repeat a latent `amount` times so an image-started mode can produce
    a batch the way `EmptyLatentImage`'s `batch_size` does for txt2img.
    `amount=1` is a no-op node, kept unconditionally so the graph shape
    does not depend on a parameter value."""
    node = graph.add("RepeatLatentBatch", samples=latent, amount=amount)
    return [node, 0]


def sample(
    graph: Graph,
    ckpt: Checkpoint,
    positive: Link,
    negative: Link,
    latent: Link,
    params: dict,
) -> Link:
    """The sampler every generating mode shares.

    `denoise` comes from the operation's schema when it declares one
    (img2img's whole point) and is 1.0 otherwise -- sampling a blank
    latent from anything less would be a lie about what was asked.
    """
    denoise = params.get("denoise")
    node = graph.add(
        "KSampler",
        seed=params["seed"],
        steps=params["steps"],
        cfg=params["cfg_scale"],
        sampler_name=params["sampler"],
        scheduler=params["scheduler"],
        denoise=1.0 if denoise is None else float(denoise),
        model=ckpt.model,
        positive=positive,
        negative=negative,
        latent_image=latent,
    )
    return [node, 0]


def save_image(graph: Graph, images: Link, request: GenerationRequest) -> str:
    """Save the produced images. The job UUID prefixes every engine-side
    file, so an output found on the engine can be traced back to its
    job."""
    return graph.add("SaveImage", filename_prefix=request.client_ref, images=images)


def decode_and_save(graph: Graph, ckpt: Checkpoint, samples: Link, request: GenerationRequest) -> None:
    """Latent -> pixels -> saved file: the tail every sampling mode
    shares."""
    decoded = graph.add("VAEDecode", samples=samples, vae=ckpt.vae)
    save_image(graph, [decoded, 0], request)


def load_mask(graph: Graph, reference: str, channel: str = "red") -> Link:
    """Load a transferred image AS A MASK.

    ComfyUI's `LoadImageMask` reads one channel of an image file into a
    MASK, which is what an inpainting encoder wants; `"red"` is the channel
    a plain black-and-white mask carries its shape in, and white reads as
    the area to repaint.
    """
    node = graph.add("LoadImageMask", image=reference, channel=channel)
    return [node, 0]


def encode_for_inpaint(graph: Graph, ckpt: Checkpoint, image: Link, mask: Link, grow_by: int) -> Link:
    """Pixels + mask -> a latent whose masked area is erased, ready to be
    resampled. `grow_by` feathers the mask outward to hide the seam."""
    node = graph.add(
        "VAEEncodeForInpaint",
        pixels=image,
        vae=ckpt.vae,
        mask=mask,
        grow_mask_by=grow_by,
    )
    return [node, 0]


def upscale_with_model(graph: Graph, model_asset_id: str, image: Link) -> Link:
    """Run an image through a loaded upscale model. Two nodes, no
    checkpoint and no sampler: an upscaler is a small standalone network,
    which is exactly why this mode needs neither."""
    loader = graph.add("UpscaleModelLoader", model_name=model_asset_id)
    node = graph.add("ImageUpscaleWithModel", upscale_model=[loader, 0], image=image)
    return [node, 0]


# -- `flux2`-family fragments -------------------------------------------
#
# Shared by `flux2_edit.py` and `flux2_txt2img.py`: both operations build
# the SAME guidance-distilled wiring for the SAME family (a declared
# `variant` names one guidance-distilled BUILD of `flux2`, and that build's
# guider shape is a fact of the weights, not of which operation is asking),
# so it lives here once rather than in whichever template happened to write
# it first. Node names that are this family's OWN ComfyUI vocabulary
# (`Flux2Scheduler`, `EmptyFlux2LatentImage`) are prefixed `flux2_`, the
# same way `scale_to_megapixels`/`encode_for_inpaint`/`upscale_with_model`
# above are named for the mode they serve; `FluxGuidance`, `BasicGuider`,
# `CFGGuider`, and `ConditioningZeroOut` are ComfyUI's own generic node
# names (not `flux2`-specific class names) and keep plain ones.


def flux_guidance(graph: Graph, conditioning: Link, guidance: float) -> Link:
    """This family's instruction-adherence control, applied to the encoded
    text. The UNDISTILLED graph only -- the distilled weights carry no such
    node at all."""
    return [graph.add("FluxGuidance", conditioning=conditioning, guidance=guidance), 0]


def zeroed_conditioning(graph: Graph, conditioning: Link) -> Link:
    """The encoded text with its embedding zeroed -- the distilled variant's
    negative. Zeroed BEFORE any reference chaining an edit graph adds
    afterwards, so both branches still see every reference image."""
    return [graph.add("ConditioningZeroOut", conditioning=conditioning), 0]


def basic_guider(graph: Graph, model: Link, conditioning: Link) -> Link:
    """The undistilled guider: one conditioning, no negative -- this
    family's guider takes a single one, which is why neither `edit` nor
    `txt2img` declares a negative prompt for the ordinary graph."""
    return [graph.add("BasicGuider", model=model, conditioning=conditioning), 0]


def cfg_guider(graph: Graph, model: Link, positive: Link, negative: Link, cfg: float) -> Link:
    """The distilled guider. The operator's guidance/CFG control lands on
    `cfg` here, on `FluxGuidance.guidance` in the undistilled graph, and on
    `KSampler.cfg` in the checkpoint and `qwen_image` families -- one
    control, several honest wirings depending on the graph it reaches."""
    return [
        graph.add("CFGGuider", model=model, positive=positive, negative=negative, cfg=cfg),
        0,
    ]


def flux2_empty_latent(graph: Graph, width, height, batch_size: int) -> Link:
    """This family's latent, `EmptyFlux2LatentImage`. `width`/`height`
    accept either a plain value or a `[node_id, index]` link -- ComfyUI's
    own graph format takes either directly as a node input: `edit` passes a
    `GetImageSize` link read off the picture being edited and pins
    `batch_size=1` (it declares no such param); `txt2img` passes its own
    `width`/`height`/`batch_size` params as plain ints, since it starts
    from nothing."""
    return [
        graph.add("EmptyFlux2LatentImage", width=width, height=height, batch_size=batch_size),
        0,
    ]


def flux2_sigmas(graph: Graph, steps: int, width, height) -> Link:
    """This family's own noise schedule, `Flux2Scheduler`, which is
    resolution-aware -- which is why it takes a size rather than a fixed
    pair. See `flux2_empty_latent` for why `width`/`height` accept either a
    link or a plain value."""
    return [graph.add("Flux2Scheduler", steps=steps, width=width, height=height), 0]


def flux2_sample(
    graph: Graph, guider: Link, sigmas: Link, latent: Link, seed: int, sampler: str
) -> Link:
    """Guider + sampler + noise into `SamplerCustomAdvanced` -- this
    family's own sampling wiring, shared by `edit` and `txt2img`. Which
    guider is the caller's business, not this function's. `sampler` is the
    sampler NAME (`KSamplerSelect.sampler_name`): `edit` passes its own
    pinned `"euler"` (`flux2_edit.SAMPLER` -- `EDIT` declares no `sampler`
    param), `txt2img` passes `TXT2IMG`'s own `sampler` param, since
    `KSamplerSelect` honours whatever it is given."""
    sampler_node = graph.add("KSamplerSelect", sampler_name=sampler)
    noise = graph.add("RandomNoise", noise_seed=seed)
    node = graph.add(
        "SamplerCustomAdvanced",
        noise=[noise, 0], guider=guider, sampler=[sampler_node, 0],
        sigmas=sigmas, latent_image=latent,
    )
    return [node, 0]
