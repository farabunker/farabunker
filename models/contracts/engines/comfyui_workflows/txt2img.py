"""Text-to-image graph: checkpoint -> two CLIP encodes -> KSampler -> VAE
decode -> SaveImage. Built from ComfyUI's BUILT-IN nodes only, so vanilla
ComfyUI with no custom nodes and no Manager can run it (D1), and composed
from `_fragments` so the nodes it shares with img2img and inpaint are
written once."""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one txt2img request.

    `model_id` is ComfyUI's own opaque checkpoint string (possibly with a
    Windows subfolder separator) and is passed through untouched.

    `config` is the bound connection's `ModelConnection.config` (D8) --
    populated when the connection declares a multi-file family
    (`family`/`text_encoder`/`vae`, ADR 0012 D-EDIT-2/3; see
    `models.contracts.engines.comfyui_workflows.flux2_edit`/`qwen_edit` for
    the templates that actually read those three keys). A single-file
    checkpoint has no config at all, and this template ignores it
    regardless -- a checkpoint graph has no multi-file wiring to build in
    the first place, family declared or not.

    `inputs` maps a file param's key to the reference ComfyUI gave the
    uploaded file. txt2img has no file params, so this template ignores it
    too -- img2img reads `inputs["init_image"]`.
    """
    params = request.params
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id, params)
    positive, negative = _fragments.prompts(graph, ckpt, params)
    latent = _fragments.empty_latent(graph, params)
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
