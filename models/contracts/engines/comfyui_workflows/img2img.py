"""Image-to-image graph: checkpoint -> two CLIP encodes, load the init
image -> VAE encode -> repeat for the batch -> KSampler at the requested
denoise -> VAE decode -> SaveImage. Built-in nodes only (D1)."""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one img2img request.

    `inputs["init_image"]` is the reference ComfyUI gave the transferred
    file (`ImageGenerator.submit` owns that transfer) -- never a path on
    the platform's filesystem. It is indexed, not `.get()`, because
    `init_image` is a REQUIRED file param: an absent reference means the
    layer above skipped the transfer, and a `KeyError` naming the param is
    a far better report than a graph ComfyUI rejects for `image: None`.

    The output size is the init image's own; this operation declares no
    width/height, so nothing here rescales the operator's picture.

    `config` (D8) is ignored, as in txt2img: a single-file SD checkpoint
    needs none of it.
    """
    params = request.params
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id, params)
    positive, negative = _fragments.prompts(graph, ckpt, params)
    image = _fragments.load_image(graph, inputs["init_image"])
    latent = _fragments.encode_image(graph, ckpt, image)
    latent = _fragments.repeat_batch(graph, latent, params["batch_size"])
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
