"""Inpainting graph: checkpoint -> two CLIP encodes, load the image and its
mask -> VAEEncodeForInpaint -> repeat for the batch -> KSampler -> VAE
decode -> SaveImage. Built-in nodes only (D1)."""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one inpaint request.

    Two file params, both required, both already transferred by
    `ImageGenerator.submit`: `inputs["init_image"]` is the picture and
    `inputs["mask_image"]` is the black-and-white mask whose WHITE area is
    repainted. Both are indexed rather than `.get()` for the reason
    img2img's is -- an absent reference is a bug above this line and should
    name itself.

    `config` (D8) is ignored, as in every single-file-checkpoint template.
    """
    params = request.params
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id, params)
    positive, negative = _fragments.prompts(graph, ckpt, params)
    image = _fragments.load_image(graph, inputs["init_image"])
    mask = _fragments.load_mask(graph, inputs["mask_image"])
    latent = _fragments.encode_for_inpaint(graph, ckpt, image, mask, params["mask_grow"])
    latent = _fragments.repeat_batch(graph, latent, params["batch_size"])
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
