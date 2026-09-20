"""Upscaling graph: load the image, load the chosen upscale model, run it,
save. Built-in nodes only (D1) -- and notably no checkpoint and no
sampler."""
from __future__ import annotations

from models.contracts.engines.comfyui_workflows import _fragments
from models.contracts.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one upscale request.

    `model_id` -- the checkpoint `vision.generate` is bound to -- is
    deliberately UNUSED: an upscale model is a small standalone network,
    and loading a multi-gigabyte checkpoint this graph never samples from
    would cost the operator VRAM for nothing. The binding still decides
    which ENGINE runs the job, which is the half that matters here.

    `config` (D8) is ignored for the same reason every current template
    ignores it.
    """
    graph = _fragments.Graph()
    image = _fragments.load_image(graph, inputs["init_image"])
    upscaled = _fragments.upscale_with_model(graph, request.params["upscale_model"], image)
    _fragments.save_image(graph, upscaled, request)
    return graph.as_dict()
