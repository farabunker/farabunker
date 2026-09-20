"""Unit tests for the ComfyUI graph templates (spec §4.4).

MOVED FROM `tools/vision/tests/` (C-58). This module tests
`models.contracts.engines.comfyui_workflows` exclusively and imports
nothing from `tools.vision`; it was found alongside the three modules
Task 36's brief named, by the same repo-shape gate
(`foundation/ops/tests/test_column_boundaries.py::
test_a_contract_module_keeps_its_tests_beside_itself`) that pins the
original three, since it fits the finding identically.
"""
from __future__ import annotations

import pytest

from models.contracts.engines.base import GenerationRejected
from models.contracts.engines.comfyui_workflows import (
    families, get_template, ignored_params, template_keys, variant_defaults, variants,
)
from models.contracts.operations import GenerationRequest

PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "width": 832,
    "height": 1216,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 2,
}


def _graph(config: dict | None = None) -> dict:
    request = GenerationRequest(
        operation="txt2img",
        model_id="sdxl\\turbo.safetensors",
        params=dict(PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("txt2img")(request, request.model_id, config or {}, {})


class TestGetTemplate:
    def test_unknown_operation_is_a_clear_error(self):
        with pytest.raises(ValueError, match="No ComfyUI template"):
            get_template("controlnet")

    def test_img2img_is_registered(self):
        assert get_template("img2img") is not None


class TestTxt2ImgGraph:
    def test_checkpoint_node_carries_the_opaque_model_id(self):
        node = _graph()["1"]
        assert node["class_type"] == "CheckpointLoaderSimple"
        assert node["inputs"]["ckpt_name"] == "sdxl\\turbo.safetensors"

    def test_positive_and_negative_prompts_land_on_their_own_encoders(self):
        graph = _graph()
        assert graph["2"]["class_type"] == "CLIPTextEncode"
        assert graph["2"]["inputs"]["text"] == "a lighthouse at dusk"
        assert graph["2"]["inputs"]["clip"] == ["1", 1]
        assert graph["3"]["inputs"]["text"] == "blurry"
        assert graph["3"]["inputs"]["clip"] == ["1", 1]

    def test_latent_carries_size_and_batch(self):
        inputs = _graph()["4"]["inputs"]
        assert inputs == {"width": 832, "height": 1216, "batch_size": 2}

    def test_sampler_node_carries_every_sampling_param_and_its_wiring(self):
        inputs = _graph()["5"]["inputs"]
        assert inputs["seed"] == 123456
        assert inputs["steps"] == 30
        assert inputs["cfg"] == 6.5
        assert inputs["sampler_name"] == "dpmpp_2m"
        assert inputs["scheduler"] == "karras"
        assert inputs["denoise"] == 1.0
        assert inputs["model"] == ["1", 0]
        assert inputs["positive"] == ["2", 0]
        assert inputs["negative"] == ["3", 0]
        assert inputs["latent_image"] == ["4", 0]

    def test_decode_and_save_wiring(self):
        graph = _graph()
        assert graph["6"]["class_type"] == "VAEDecode"
        assert graph["6"]["inputs"] == {"samples": ["5", 0], "vae": ["1", 2]}
        assert graph["7"]["class_type"] == "SaveImage"
        assert graph["7"]["inputs"]["images"] == ["6", 0]

    def test_filename_prefix_is_the_client_ref(self):
        """The job UUID prefixes every engine-side output file, so a result
        found on the engine can always be traced back to its job."""
        assert _graph()["7"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_connection_config_is_ignored_by_this_template(self):
        """`cfg` is reserved for multi-file families (a Flux template will
        read `cfg["unet"]`/`cfg["clip"]`/`cfg["vae"]`); a single-file SD
        checkpoint needs none of it, and an unexpected key must never leak
        into the graph."""
        assert _graph({"unet": "flux.safetensors"}) == _graph({})

    def test_missing_negative_prompt_becomes_an_empty_encoder(self):
        request = GenerationRequest(
            operation="txt2img",
            model_id="sd15.ckpt",
            params={k: v for k, v in PARAMS.items() if k != "negative_prompt"},
            client_ref="ref",
        )
        graph = get_template("txt2img")(request, request.model_id, {}, {})
        assert graph["3"]["inputs"]["text"] == ""

    def test_the_whole_graph_is_exactly_what_it_was_before_the_fragment_rewrite(self):
        """A golden copy of the shipped txt2img graph. The fragment
        extraction is a refactor: if this changes, it was not one."""
        assert _graph() == {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sdxl\\turbo.safetensors"}},
            "2": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a lighthouse at dusk", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 832, "height": 1216, "batch_size": 2}},
            "5": {"class_type": "KSampler",
                  "inputs": {"seed": 123456, "steps": 30, "cfg": 6.5,
                             "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                             "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                             "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
                             "images": ["6", 0]}},
        }


from models.contracts.engines.comfyui_workflows import _fragments


class TestGraphBuilder:
    """Node ids are allocated in call order, so a template reads as the
    graph it builds and no template hand-numbers a node."""

    def test_ids_are_allocated_in_call_order(self):
        graph = _fragments.Graph()
        assert graph.add("CheckpointLoaderSimple", ckpt_name="a.safetensors") == "1"
        assert graph.add("VAEDecode", samples=["1", 0]) == "2"

    def test_as_dict_is_the_api_format_shape(self):
        graph = _fragments.Graph()
        graph.add("SaveImage", filename_prefix="job", images=["9", 0])
        assert graph.as_dict() == {
            "1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "job", "images": ["9", 0]}}
        }


class TestFragments:
    def test_checkpoint_yields_the_three_links_its_loader_outputs(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl\\turbo.safetensors", {})
        assert graph.as_dict()["1"]["inputs"]["ckpt_name"] == "sdxl\\turbo.safetensors"
        assert (ckpt.model, ckpt.clip, ckpt.vae) == (["1", 0], ["1", 1], ["1", 2])

    def test_prompts_encode_positive_then_negative_against_the_checkpoint_clip(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {})
        positive, negative = _fragments.prompts(graph, ckpt, {"prompt": "a lighthouse", "negative_prompt": "blurry"})
        nodes = graph.as_dict()
        assert positive == ["2", 0] and negative == ["3", 0]
        assert nodes["2"]["inputs"] == {"text": "a lighthouse", "clip": ["1", 1]}
        assert nodes["3"]["inputs"] == {"text": "blurry", "clip": ["1", 1]}

    def test_a_missing_prompt_encodes_as_empty_text_never_none(self):
        """`None` is not a string ComfyUI can encode; an operation that
        declares no negative prompt must still produce a valid graph."""
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {})
        _fragments.prompts(graph, ckpt, {})
        nodes = graph.as_dict()
        assert nodes["2"]["inputs"]["text"] == ""
        assert nodes["3"]["inputs"]["text"] == ""

    def test_sample_defaults_denoise_to_one_when_the_operation_has_no_such_param(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {})
        params = {"seed": 7, "steps": 20, "cfg_scale": 6.0, "sampler": "euler", "scheduler": "normal"}
        _fragments.sample(graph, ckpt, ["2", 0], ["3", 0], ["4", 0], params)
        assert graph.as_dict()["2"]["inputs"]["denoise"] == 1.0

    def test_sample_reads_denoise_from_the_params_when_declared(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {})
        params = {"seed": 7, "steps": 20, "cfg_scale": 6.0, "sampler": "euler",
                  "scheduler": "normal", "denoise": 0.4}
        _fragments.sample(graph, ckpt, ["2", 0], ["3", 0], ["4", 0], params)
        assert graph.as_dict()["2"]["inputs"]["denoise"] == 0.4

    def test_repeat_batch_and_load_image_are_single_nodes(self):
        graph = _fragments.Graph()
        image = _fragments.load_image(graph, "job-uuid/beach.png")
        latent = _fragments.repeat_batch(graph, ["5", 0], 3)
        nodes = graph.as_dict()
        assert image == ["1", 0]
        assert nodes["1"] == {"class_type": "LoadImage", "inputs": {"image": "job-uuid/beach.png"}}
        assert latent == ["2", 0]
        assert nodes["2"] == {"class_type": "RepeatLatentBatch", "inputs": {"samples": ["5", 0], "amount": 3}}


IMG2IMG_PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "denoise": 0.35,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 2,
}


def _img2img_graph(inputs=None):
    request = GenerationRequest(
        operation="img2img",
        model_id="sdxl.safetensors",
        params=dict(IMG2IMG_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    references = {"init_image": "8f14e45f/init_image-beach.png"} if inputs is None else inputs
    return get_template("img2img")(request, request.model_id, {}, references)


class TestImg2ImgGraph:
    def test_the_init_image_is_loaded_by_the_engine_side_reference(self):
        """The template never sees a filesystem path: `submit` transferred
        the file first and handed back the engine's own name for it."""
        graph = _img2img_graph()
        assert graph["4"] == {
            "class_type": "LoadImage",
            "inputs": {"image": "8f14e45f/init_image-beach.png"},
        }

    def test_the_loaded_image_is_encoded_with_the_checkpoint_vae(self):
        assert _img2img_graph()["5"] == {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        }

    def test_batch_size_repeats_the_encoded_latent(self):
        assert _img2img_graph()["6"] == {
            "class_type": "RepeatLatentBatch",
            "inputs": {"samples": ["5", 0], "amount": 2},
        }

    def test_the_sampler_denoises_from_that_latent_at_the_requested_strength(self):
        inputs = _img2img_graph()["7"]["inputs"]
        assert inputs["denoise"] == 0.35
        assert inputs["latent_image"] == ["6", 0]
        assert inputs["positive"] == ["2", 0]
        assert inputs["negative"] == ["3", 0]

    def test_it_decodes_and_saves_under_the_job_prefix(self):
        graph = _img2img_graph()
        assert graph["8"] == {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}}
        assert graph["9"]["class_type"] == "SaveImage"
        assert graph["9"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_a_missing_input_reference_is_a_clear_error_not_a_broken_graph(self):
        """`submit` transfers every declared file input before a template
        runs, so an absent reference is a bug in the layer above -- and it
        must say so rather than posting a graph with `image: None`."""
        with pytest.raises(KeyError, match="init_image"):
            _img2img_graph(inputs={})


INPAINT_PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "mask_grow": 12,
    "denoise": 1.0,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 1,
}


def _inpaint_graph():
    request = GenerationRequest(
        operation="inpaint",
        model_id="sdxl.safetensors",
        params=dict(INPAINT_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("inpaint")(
        request,
        request.model_id,
        {},
        {"init_image": "8f14e45f/beach.png", "mask_image": "8f14e45f/mask.png"},
    )


class TestInpaintGraph:
    def test_the_image_and_the_mask_are_loaded_by_their_own_references(self):
        graph = _inpaint_graph()
        assert graph["4"] == {"class_type": "LoadImage", "inputs": {"image": "8f14e45f/beach.png"}}
        assert graph["5"] == {
            "class_type": "LoadImageMask",
            "inputs": {"image": "8f14e45f/mask.png", "channel": "red"},
        }

    def test_the_masked_latent_carries_the_grow_amount(self):
        assert _inpaint_graph()["6"] == {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2], "mask": ["5", 0], "grow_mask_by": 12},
        }

    def test_the_batch_repeat_and_sampler_follow_the_masked_latent(self):
        graph = _inpaint_graph()
        assert graph["7"]["class_type"] == "RepeatLatentBatch"
        assert graph["8"]["inputs"]["latent_image"] == ["7", 0]
        assert graph["8"]["inputs"]["denoise"] == 1.0

    def test_it_decodes_and_saves_under_the_job_prefix(self):
        graph = _inpaint_graph()
        assert graph["9"]["class_type"] == "VAEDecode"
        assert graph["10"]["class_type"] == "SaveImage"
        assert graph["10"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"


class TestLoraChain:
    """One LoRA loader per selected file, chained MODEL->MODEL and
    CLIP->CLIP, between the checkpoint and everything downstream -- so the
    prompt encoders see the adorned CLIP, which is the whole point."""

    def test_no_loras_adds_no_nodes(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": []})
        assert list(graph.as_dict()) == ["1"]
        assert (ckpt.model, ckpt.clip, ckpt.vae) == (["1", 0], ["1", 1], ["1", 2])

    def test_an_operation_with_no_lora_params_at_all_adds_no_nodes(self):
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {})
        assert list(graph.as_dict()) == ["1"]

    def test_one_lora_wires_model_and_clip_through_it(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(
            graph, "sdxl.safetensors", {"loras": ["style.safetensors"], "lora_strength": 0.8}
        )
        assert graph.as_dict()["2"] == {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style.safetensors", "strength_model": 0.8,
                       "strength_clip": 0.8, "model": ["1", 0], "clip": ["1", 1]},
        }
        assert (ckpt.model, ckpt.clip) == (["2", 0], ["2", 1])

    def test_the_vae_still_comes_from_the_checkpoint(self):
        """`LoraLoader` outputs MODEL and CLIP only -- a graph that took a
        VAE link from it would be wired to nothing."""
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": ["style.safetensors"]})
        assert ckpt.vae == ["1", 2]

    def test_several_loras_chain_in_order(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(
            graph, "sdxl.safetensors", {"loras": ["a.safetensors", "b.safetensors"]}
        )
        nodes = graph.as_dict()
        assert nodes["2"]["inputs"]["model"] == ["1", 0]
        assert nodes["3"]["inputs"]["model"] == ["2", 0]
        assert nodes["3"]["inputs"]["clip"] == ["2", 1]
        assert (ckpt.model, ckpt.clip) == (["3", 0], ["3", 1])

    def test_the_strength_defaults_to_one_when_the_operation_omits_it(self):
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": ["a.safetensors"]})
        assert graph.as_dict()["2"]["inputs"]["strength_model"] == 1.0

    def test_a_single_asset_value_is_accepted_as_well_as_a_list(self):
        """`validate_params` gives a non-`multiple` asset param one string;
        a template must not have to know which shape it got."""
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": "a.safetensors"})
        assert graph.as_dict()["2"]["inputs"]["lora_name"] == "a.safetensors"


class TestLorasReachEveryCheckpointMode:
    def test_txt2img_encodes_its_prompts_against_the_adorned_clip(self):
        request = GenerationRequest(
            operation="txt2img", model_id="sdxl.safetensors",
            params={**PARAMS, "loras": ["style.safetensors"], "lora_strength": 0.7},
            client_ref="job",
        )
        graph = get_template("txt2img")(request, request.model_id, {}, {})
        assert graph["2"]["class_type"] == "LoraLoader"
        assert graph["3"]["inputs"]["clip"] == ["2", 1]
        assert graph["6"]["inputs"]["model"] == ["2", 0]

    def test_img2img_does_too_with_no_edit_of_its_own(self):
        request = GenerationRequest(
            operation="img2img", model_id="sdxl.safetensors",
            params={**IMG2IMG_PARAMS, "loras": ["style.safetensors"]},
            client_ref="job",
        )
        graph = get_template("img2img")(request, request.model_id, {}, {"init_image": "job/beach.png"})
        assert graph["2"]["class_type"] == "LoraLoader"

    def test_inpaint_does_too(self):
        request = GenerationRequest(
            operation="inpaint", model_id="sdxl.safetensors",
            params={**INPAINT_PARAMS, "loras": ["style.safetensors"]},
            client_ref="job",
        )
        graph = get_template("inpaint")(
            request, request.model_id, {}, {"init_image": "job/b.png", "mask_image": "job/m.png"}
        )
        assert graph["2"]["class_type"] == "LoraLoader"


class TestUpscaleGraph:
    def _graph(self):
        request = GenerationRequest(
            operation="upscale",
            model_id="sdxl.safetensors",
            params={"init_image": "beach.png", "upscale_model": "4x-ultrasharp.pth"},
            client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
        )
        return get_template("upscale")(request, request.model_id, {}, {"init_image": "job/beach.png"})

    def test_it_loads_the_image_and_the_chosen_upscaler(self):
        graph = self._graph()
        assert graph["1"] == {"class_type": "LoadImage", "inputs": {"image": "job/beach.png"}}
        assert graph["2"] == {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": "4x-ultrasharp.pth"},
        }

    def test_it_upscales_and_saves_under_the_job_prefix(self):
        graph = self._graph()
        assert graph["3"] == {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
        }
        assert graph["4"]["class_type"] == "SaveImage"
        assert graph["4"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_it_never_loads_the_bound_checkpoint(self):
        """The role binding answers "which image engine and which
        checkpoint", and this mode needs only the first half. Loading a
        multi-gigabyte checkpoint it does not use would cost the operator
        VRAM for nothing."""
        classes = {node["class_type"] for node in self._graph().values()}
        assert "CheckpointLoaderSimple" not in classes
        assert "KSampler" not in classes


class TestFamilyKeyedRegistry:
    def test_the_checkpoint_operations_belong_to_the_no_family_key(self):
        """A single-file checkpoint connection declares no family, so the
        four checkpoint-based modes live under the empty family -- and are
        exactly what an undeclared connection is offered."""
        assert set(template_keys()) == {"txt2img", "img2img", "inpaint", "upscale"}
        assert set(template_keys("")) == set(template_keys())

    def test_an_unknown_family_has_no_templates_and_says_so(self):
        """A family the operator declared and this adapter has no graph for
        offers nothing -- an honest empty list, never a checkpoint graph
        run against weights that are not a checkpoint."""
        assert template_keys("not-a-family-we-have") == ()

    def test_get_template_names_the_family_it_could_not_find(self):
        with pytest.raises(ValueError) as excinfo:
            get_template("txt2img", "not-a-family-we-have")
        assert "not-a-family-we-have" in str(excinfo.value)
        assert "txt2img" in str(excinfo.value)

    def test_families_lists_only_non_empty_family_keys(self):
        """`families()` is what the adapter offers an operator to declare,
        so the empty 'no family' key must never appear in it."""
        assert "" not in families()


EDIT_PARAMS = {
    "instruction": "put a red hat on the woman",
    "init_image": "beach.png",
    "reference_image": None,
    "guidance": 4.0,
    "steps": 20,
    "seed": 987654,
}


def _by_class(graph: dict) -> dict[str, list[dict]]:
    """Every node in `graph`, grouped by class_type, in id order. Node ids
    are ALLOCATED (`_fragments.Graph.add`), so a test that indexed them by
    hand would break the moment a node is inserted -- which is exactly the
    renumbering `Graph` exists to make safe."""
    grouped: dict[str, list[dict]] = {}
    for _node_id, node in sorted(graph.items(), key=lambda item: int(item[0])):
        grouped.setdefault(node["class_type"], []).append(node)
    return grouped


def _reference_chain(graph: dict, start: list) -> list:
    """Walk a conditioning chain of `ReferenceLatent` nodes back from
    `start` (a `[node_id, output_index]` link, e.g. a guider's own
    `positive` or `negative` input) to the node the chain roots at, and
    return the `latent` link each hop wraps -- in the order the images were
    attached, the edited image first.

    A walk rather than a set/sorted comparison: node ids are ALLOCATED
    (`Graph.add`), so which id a given `ReferenceLatent` gets can differ
    between an interleaved and a branch-outer build without either being
    wrong. What must hold is what each branch actually SEES when read the
    way `ComfyUI` itself would -- backward from the guider."""
    latents = []
    node_id = start[0]
    while graph[node_id]["class_type"] == "ReferenceLatent":
        node = graph[node_id]
        latents.append(node["inputs"]["latent"])
        node_id = node["inputs"]["conditioning"][0]
    latents.reverse()
    return latents


def _edit_graph(family: str, config: dict, inputs: dict, params: dict | None = None) -> dict:
    request = GenerationRequest(
        operation="edit",
        model_id="weights-Q4_K_S.gguf",
        params=dict(params or EDIT_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("edit", family)(request, request.model_id, config, inputs)


FLUX2_CONFIG = {
    "family": "flux2",
    "text_encoder": "encoder-Q4_K_M.gguf",
    "vae": "family-vae.safetensors",
}


class TestFlux2EditGraph:
    def test_the_family_is_registered_and_offered(self):
        assert "flux2" in families()
        assert "edit" in template_keys("flux2")

    def test_each_declared_file_is_loaded_by_the_loader_that_can_read_it(self):
        """A `.gguf` goes to ComfyUI-GGUF's loader, a safetensors to the
        built-in one. The extension of the file the OPERATOR named decides
        -- never a pattern matched against a shipped list of models."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["UnetLoaderGGUF"][0]["inputs"]["unet_name"] == "weights-Q4_K_S.gguf"
        assert nodes["CLIPLoaderGGUF"][0]["inputs"] == {
            "clip_name": "encoder-Q4_K_M.gguf", "type": "flux2",
        }
        assert nodes["VAELoader"][0]["inputs"]["vae_name"] == "family-vae.safetensors"

    def test_a_safetensors_pair_uses_the_built_in_loaders(self):
        request = GenerationRequest(
            operation="edit", model_id="weights.safetensors",
            params=dict(EDIT_PARAMS), client_ref="ref",
        )
        graph = get_template("edit", "flux2")(
            request, request.model_id,
            {"family": "flux2", "text_encoder": "enc.safetensors", "vae": "v.safetensors"},
            {"init_image": "in/beach.png"},
        )
        nodes = _by_class(graph)
        assert nodes["UNETLoader"][0]["inputs"] == {
            "unet_name": "weights.safetensors", "weight_dtype": "default",
        }
        assert nodes["CLIPLoader"][0]["inputs"] == {
            "clip_name": "enc.safetensors", "type": "flux2", "device": "default",
        }

    def test_the_instruction_is_encoded_once_and_guided(self):
        """One conditioning path: this family's guider takes a single
        conditioning, which is why `edit` declares no negative prompt."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert len(nodes["CLIPTextEncode"]) == 1
        assert nodes["CLIPTextEncode"][0]["inputs"]["text"] == "put a red hat on the woman"
        assert nodes["FluxGuidance"][0]["inputs"]["guidance"] == 4.0

    def test_the_edited_image_is_scaled_encoded_and_referenced(self):
        graph = _edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        assert nodes["LoadImage"][0]["inputs"]["image"] == "in/beach.png"
        scale = nodes["ImageScaleToTotalPixels"][0]["inputs"]
        assert scale["upscale_method"] == "area"
        assert scale["megapixels"] == 1.0
        assert scale["resolution_steps"] == 1
        assert len(nodes["VAEEncode"]) == 1
        assert len(nodes["ReferenceLatent"]) == 1

    def test_the_output_size_comes_from_the_edited_image_never_a_number_we_invented(self):
        graph = _edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        size_id = next(nid for nid, n in graph.items() if n["class_type"] == "GetImageSize")
        latent = nodes["EmptyFlux2LatentImage"][0]["inputs"]
        sigmas = nodes["Flux2Scheduler"][0]["inputs"]
        assert latent["width"] == [size_id, 0]
        assert latent["height"] == [size_id, 1]
        # Pinned at 1 by the template, not read from a param -- `EDIT`
        # declares none (R1: one output per edit).
        assert latent["batch_size"] == 1
        assert sigmas["width"] == [size_id, 0]
        assert sigmas["height"] == [size_id, 1]
        assert sigmas["steps"] == 20

    def test_a_reference_image_adds_a_second_reference_latent_after_the_first(self):
        """Chain order IS the reference numbering an instruction can name:
        the image being edited is Reference Image 1, the optional second one
        is Reference Image 2."""
        graph = _edit_graph(
            "flux2", FLUX2_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        assert [n["inputs"]["image"] for n in nodes["LoadImage"]] == [
            "in/beach.png", "in/style.png",
        ]
        first, second = nodes["ReferenceLatent"]
        first_id = next(nid for nid, n in graph.items() if n is first)
        assert second["inputs"]["conditioning"] == [first_id, 0]
        assert len(nodes["ImageScaleToTotalPixels"]) == 2
        assert len(nodes["VAEEncode"]) == 2

    def test_the_sampler_is_wired_and_the_seed_is_the_jobs_own(self):
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["RandomNoise"][0]["inputs"]["noise_seed"] == 987654
        assert nodes["KSamplerSelect"][0]["inputs"]["sampler_name"] == "euler"
        sampler = nodes["SamplerCustomAdvanced"][0]["inputs"]
        assert set(sampler) == {"noise", "guider", "sampler", "sigmas", "latent_image"}
        assert nodes["SaveImage"][0]["inputs"]["filename_prefix"] == (
            "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"
        )

    def test_a_connection_missing_a_companion_is_refused_in_the_operators_words(self):
        """A half-registered connection must fail the JOB with a sentence
        that says what to do, not a KeyError in a traceback."""
        with pytest.raises(GenerationRejected) as excinfo:
            _edit_graph("flux2", {"family": "flux2", "vae": "v.safetensors"},
                        {"init_image": "in/beach.png"})
        assert "text encoder" in str(excinfo.value)


QWEN_CONFIG = {
    "family": "qwen_image",
    "text_encoder": "vl-encoder.safetensors",
    "vae": "family-vae.safetensors",
}


class TestQwenEditGraph:
    def test_the_family_is_registered_and_offered(self):
        assert "qwen_image" in families()
        assert template_keys("qwen_image") == ("edit",)

    def test_it_reuses_the_same_declared_files_and_loaders(self):
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        assert nodes["UnetLoaderGGUF"][0]["inputs"]["unet_name"] == "weights-Q4_K_S.gguf"
        assert nodes["CLIPLoader"][0]["inputs"] == {
            "clip_name": "vl-encoder.safetensors", "type": "qwen_image", "device": "default",
        }

    def test_the_model_passes_through_this_familys_own_two_stage_chain(self):
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        unet_id = next(nid for nid, n in graph.items() if n["class_type"] == "UnetLoaderGGUF")
        shift_id = next(
            nid for nid, n in graph.items() if n["class_type"] == "ModelSamplingAuraFlow"
        )
        assert nodes["ModelSamplingAuraFlow"][0]["inputs"] == {
            "model": [unet_id, 0], "shift": 3.1,
        }
        assert nodes["CFGNorm"][0]["inputs"] == {"model": [shift_id, 0], "strength": 1.0}
        assert nodes["KSampler"][0]["inputs"]["model"] == [
            next(nid for nid, n in graph.items() if n["class_type"] == "CFGNorm"), 0
        ]

    def test_the_instruction_and_an_empty_negative_are_encoded_against_the_same_images(self):
        """This family's sampler takes a positive and a negative, and its
        shipped workflow leaves the negative's prompt empty -- which is why
        `edit` declares no negative-prompt param."""
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        positive, negative = nodes["TextEncodeQwenImageEditPlus"]
        assert positive["inputs"]["prompt"] == "put a red hat on the woman"
        assert negative["inputs"]["prompt"] == ""
        assert positive["inputs"]["image1"] == negative["inputs"]["image1"]
        assert "image2" not in positive["inputs"]

    def test_both_conditionings_pass_through_the_reference_method_node(self):
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        assert len(nodes["FluxKontextMultiReferenceLatentMethod"]) == 2
        assert all(
            node["inputs"]["reference_latents_method"] == "index_timestep_zero"
            for node in nodes["FluxKontextMultiReferenceLatentMethod"]
        )
        method_ids = [
            nid for nid, n in sorted(graph.items(), key=lambda i: int(i[0]))
            if n["class_type"] == "FluxKontextMultiReferenceLatentMethod"
        ]
        sampler = nodes["KSampler"][0]["inputs"]
        assert sampler["positive"] == [method_ids[0], 0]
        assert sampler["negative"] == [method_ids[1], 0]

    def test_only_the_edited_image_is_scaled_and_it_is_what_gets_encoded(self):
        """The bundled workflow scales the image being edited and wires any
        second reference raw -- reproduced rather than improved upon."""
        graph = _edit_graph(
            "qwen_image", QWEN_CONFIG,
            {"init_image": "in/sofa.png", "reference_image": "in/fur.png"},
        )
        nodes = _by_class(graph)
        assert len(nodes["FluxKontextImageScale"]) == 1
        scale_id = next(
            nid for nid, n in graph.items() if n["class_type"] == "FluxKontextImageScale"
        )
        assert nodes["VAEEncode"][0]["inputs"]["pixels"] == [scale_id, 0]
        positive = nodes["TextEncodeQwenImageEditPlus"][0]["inputs"]
        assert positive["image1"] == [scale_id, 0]
        assert positive["image2"] == [
            next(
                nid for nid, n in sorted(graph.items(), key=lambda i: int(i[0]))
                if n["class_type"] == "LoadImage" and n["inputs"]["image"] == "in/fur.png"
            ),
            0,
        ]

    def test_the_encoded_input_is_the_latent_sampled_from_directly(self):
        """This family samples from the input image's OWN latent, with no
        batching node between them -- the bundled workflow has none, which
        is why `edit` declares no `batch_size` (R1: one output per edit)."""
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        assert "RepeatLatentBatch" not in nodes
        encode_id = next(nid for nid, n in graph.items() if n["class_type"] == "VAEEncode")
        assert nodes["KSampler"][0]["inputs"]["latent_image"] == [encode_id, 0]

    def test_the_sampler_carries_the_shared_params_and_this_familys_fixed_ones(self):
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        inputs = nodes["KSampler"][0]["inputs"]
        assert inputs["seed"] == 987654
        assert inputs["steps"] == 20
        assert inputs["cfg"] == 4.0
        assert inputs["sampler_name"] == "euler"
        assert inputs["scheduler"] == "simple"
        assert inputs["denoise"] == 1.0

    def test_a_connection_missing_a_companion_is_refused_the_same_way(self):
        with pytest.raises(GenerationRejected):
            _edit_graph("qwen_image", {"family": "qwen_image"}, {"init_image": "in/sofa.png"})


DISTILLED_CONFIG = {**FLUX2_CONFIG, "variant": "distilled"}


class TestFlux2DistilledEditGraph:
    def test_the_variant_vocabulary_is_what_the_templates_implement(self):
        """Unlike a family, a variant is not a word the engine knows: it
        names which of a family's graphs this package has code for, so the
        package IS the vocabulary -- ONE list, because the registration form
        that offers it has not chosen a family yet."""
        assert variants() == ("distilled",)

    def test_a_distilled_graph_has_no_guidance_node(self):
        """These weights are guidance-distilled: the bundled workflow
        carries no `FluxGuidance` at all."""
        nodes = _by_class(_edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"}))
        assert "FluxGuidance" not in nodes
        assert "BasicGuider" not in nodes

    def test_a_distilled_graph_guides_with_a_zeroed_negative(self):
        graph = _edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        text_id = next(i for i, n in graph.items() if n["class_type"] == "CLIPTextEncode")
        zero = nodes["ConditioningZeroOut"][0]
        assert zero["inputs"]["conditioning"] == [text_id, 0]
        guider = nodes["CFGGuider"][0]
        assert set(guider["inputs"]) == {"model", "positive", "negative", "cfg"}

    def test_the_guidance_param_is_this_variants_cfg(self):
        """One operator control, three honest wirings: this family's
        guidance node, the other family's `KSampler.cfg`, and here the
        `CFGGuider`'s own."""
        nodes = _by_class(_edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["CFGGuider"][0]["inputs"]["cfg"] == 4.0  # EDIT_PARAMS' value, unchanged

    def test_every_reference_is_chained_on_both_branches_from_the_same_latents(self):
        """Two images, four `ReferenceLatent`s: the positive chain and the
        zeroed negative chain each see BOTH images, in the same order, and
        they share the two `VAEEncode` latents rather than encoding anything
        twice.

        Walks each branch back from `CFGGuider.positive`/`.negative` through
        its own `ReferenceLatent` chain rather than pinning creation order
        (which a two-loop vs. one-loop build can legitimately differ on) --
        the thing that must be true is what each branch actually SEES."""
        graph = _edit_graph(
            "flux2", DISTILLED_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        assert len(nodes["ReferenceLatent"]) == 4
        assert len(nodes["VAEEncode"]) == 2
        encode_ids = [i for i, n in graph.items() if n["class_type"] == "VAEEncode"]
        edited_latent, reference_latent = ([encode_id, 0] for encode_id in encode_ids)

        guider_inputs = nodes["CFGGuider"][0]["inputs"]
        for branch in (guider_inputs["positive"], guider_inputs["negative"]):
            assert _reference_chain(graph, branch) == [edited_latent, reference_latent]

    def test_the_edited_image_is_chained_first_on_both_branches(self):
        """Chain order IS the reference numbering an instruction can refer
        to, and the bundled two-image distilled subgraph chains the edited
        image first on BOTH branches (link ids 201-216 of
        `image_flux2_klein_image_edit_9b_distilled.json`)."""
        graph = _edit_graph(
            "flux2", DISTILLED_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        first_encode = next(i for i, n in graph.items() if n["class_type"] == "VAEEncode")
        heads = [n for n in nodes["ReferenceLatent"] if n["inputs"]["latent"] == [first_encode, 0]]
        assert len(heads) == 2  # one per branch, and they come first

    def test_an_undeclared_variant_still_builds_the_undistilled_graph(self):
        """Every connection registered before variants existed declares
        none, and must keep the graph it has always had."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert "FluxGuidance" in nodes and "BasicGuider" in nodes
        assert "CFGGuider" not in nodes

    def test_an_unrecognized_variant_degrades_to_the_undistilled_graph(self):
        """A declaration this adapter has no graph for is not a crash: it is
        the family's ordinary graph, which is what the family word promised."""
        nodes = _by_class(
            _edit_graph("flux2", {**FLUX2_CONFIG, "variant": "no-such-variant"},
                        {"init_image": "in/beach.png"})
        )
        assert "BasicGuider" in nodes

    def test_the_variant_defaults_are_the_bundled_widgets(self):
        assert variant_defaults("edit", "flux2", "distilled") == {"steps": 4, "guidance": 1.0}
        assert variant_defaults("edit", "flux2", "") == {}
        # txt2img's own distilled defaults now exist (flux2_txt2img), keyed
        # `cfg_scale` rather than `guidance` -- TXT2IMG's own spelling of
        # its CFG control.
        assert variant_defaults("txt2img", "flux2", "distilled") == {
            "steps": 4, "cfg_scale": 1.0,
        }


LORA_EDIT_PARAMS = {**EDIT_PARAMS, "loras": ["speed-a.safetensors"], "lora_strength": 0.8}


class TestEditLoRAs:
    """`edit` splices in the same `loras`/`lora_strength` params every
    checkpoint-based mode already carries, applied through
    `_fragments.model_lora_chain` -- the MODEL-ONLY sibling of
    `_fragments._lora_chain` both bundled edit workflows actually contain
    (ADR 0012 D-EDIT-9)."""

    def test_no_selection_adds_no_node_to_either_family(self):
        """"No adapters" is a normal answer, and it must not change the
        graph's shape."""
        for family, config in (("flux2", FLUX2_CONFIG), ("qwen_image", QWEN_CONFIG)):
            nodes = _by_class(_edit_graph(family, config, {"init_image": "in/beach.png"},
                                          {**EDIT_PARAMS, "loras": [], "lora_strength": 1.0}))
            assert "LoraLoaderModelOnly" not in nodes

    def test_each_selected_adapter_is_chained_model_only(self):
        for family, config in (("flux2", FLUX2_CONFIG), ("qwen_image", QWEN_CONFIG)):
            graph = _edit_graph(family, config, {"init_image": "in/beach.png"},
                                {**LORA_EDIT_PARAMS,
                                 "loras": ["speed-a.safetensors", "style-b.safetensors"]})
            chain = _by_class(graph)["LoraLoaderModelOnly"]
            assert [n["inputs"]["lora_name"] for n in chain] == [
                "speed-a.safetensors", "style-b.safetensors"
            ]
            assert all(n["inputs"]["strength_model"] == 0.8 for n in chain)
            assert all("strength_clip" not in n["inputs"] for n in chain)
            assert chain[1]["inputs"]["model"] == [
                next(i for i, n in graph.items() if n is chain[0]), 0
            ]

    def test_the_chain_is_the_last_model_step_before_the_sampler(self):
        """Both bundled edit workflows put the adapter last: straight after
        the loader where nothing else wraps the model, and after this
        family's own two model nodes where they do."""
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/beach.png"},
                            LORA_EDIT_PARAMS)
        nodes = _by_class(graph)
        norm_id = next(i for i, n in graph.items() if n["class_type"] == "CFGNorm")
        assert nodes["LoraLoaderModelOnly"][0]["inputs"]["model"] == [norm_id, 0]
        lora_id = next(i for i, n in graph.items() if n["class_type"] == "LoraLoaderModelOnly")
        assert nodes["KSampler"][0]["inputs"]["model"] == [lora_id, 0]

    def test_a_distilled_flux_graph_takes_them_too(self):
        graph = _edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"},
                            LORA_EDIT_PARAMS)
        nodes = _by_class(graph)
        lora_id = next(i for i, n in graph.items() if n["class_type"] == "LoraLoaderModelOnly")
        assert nodes["CFGGuider"][0]["inputs"]["model"] == [lora_id, 0]


FLUX2_TXT2IMG_PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "width": 832,
    "height": 1216,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 987654,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 2,
}


def _txt2img_graph(family: str, config: dict, params: dict | None = None) -> dict:
    request = GenerationRequest(
        operation="txt2img",
        model_id="weights-Q4_K_S.gguf",
        params=dict(FLUX2_TXT2IMG_PARAMS if params is None else params),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("txt2img", family)(request, request.model_id, config, {})


class TestFlux2Txt2ImgGraph:
    def test_the_family_offers_both_operations_edit_first(self):
        assert template_keys("flux2") == ("edit", "txt2img")

    def test_it_reuses_the_same_declared_files_and_loaders_as_edit(self):
        nodes = _by_class(_txt2img_graph("flux2", FLUX2_CONFIG))
        assert nodes["UnetLoaderGGUF"][0]["inputs"]["unet_name"] == "weights-Q4_K_S.gguf"
        assert nodes["CLIPLoaderGGUF"][0]["inputs"] == {
            "clip_name": "encoder-Q4_K_M.gguf", "type": "flux2",
        }
        assert nodes["VAELoader"][0]["inputs"]["vae_name"] == "family-vae.safetensors"

    def test_the_ordinary_graph_has_one_prompt_and_no_negative_path(self):
        nodes = _by_class(_txt2img_graph("flux2", FLUX2_CONFIG))
        assert len(nodes["CLIPTextEncode"]) == 1
        assert nodes["CLIPTextEncode"][0]["inputs"]["text"] == "a lighthouse at dusk"
        assert "ConditioningZeroOut" not in nodes
        assert "CFGGuider" not in nodes
        assert "BasicGuider" in nodes

    def test_flux_guidance_wraps_the_encoded_prompt_at_cfg_scale(self):
        graph = _txt2img_graph("flux2", FLUX2_CONFIG)
        text_id = next(i for i, n in graph.items() if n["class_type"] == "CLIPTextEncode")
        nodes = _by_class(graph)
        assert nodes["FluxGuidance"][0]["inputs"] == {
            "conditioning": [text_id, 0], "guidance": 6.5,
        }
        assert nodes["BasicGuider"][0]["inputs"]["conditioning"] == [
            next(i for i, n in graph.items() if n["class_type"] == "FluxGuidance"), 0
        ]

    def test_negative_prompt_and_scheduler_are_never_read(self):
        """`IGNORES` names both -- `build` must not index either key, so a
        request missing them entirely still builds the identical graph."""
        graph = _txt2img_graph("flux2", FLUX2_CONFIG)
        serialized = str(graph)
        assert "blurry" not in serialized
        assert "karras" not in serialized
        trimmed = {k: v for k, v in FLUX2_TXT2IMG_PARAMS.items()
                   if k not in ("negative_prompt", "scheduler")}
        assert _txt2img_graph("flux2", FLUX2_CONFIG, trimmed) == graph

    def test_latent_scheduler_and_sampler_carry_their_own_params(self):
        """Unlike `edit`, which reads width/height off the picture being
        edited and pins batch_size/sampler, txt2img starts from nothing --
        every one of these is this operation's OWN param."""
        nodes = _by_class(_txt2img_graph("flux2", FLUX2_CONFIG))
        assert nodes["EmptyFlux2LatentImage"][0]["inputs"] == {
            "width": 832, "height": 1216, "batch_size": 2,
        }
        assert nodes["Flux2Scheduler"][0]["inputs"] == {
            "steps": 30, "width": 832, "height": 1216,
        }
        assert nodes["KSamplerSelect"][0]["inputs"]["sampler_name"] == "dpmpp_2m"
        assert nodes["RandomNoise"][0]["inputs"]["noise_seed"] == 987654

    def test_loras_apply_at_the_same_position_edit_uses(self):
        graph = _txt2img_graph(
            "flux2", FLUX2_CONFIG,
            {**FLUX2_TXT2IMG_PARAMS, "loras": ["speed-a.safetensors"], "lora_strength": 0.8},
        )
        nodes = _by_class(graph)
        chain = nodes["LoraLoaderModelOnly"]
        assert chain[0]["inputs"]["lora_name"] == "speed-a.safetensors"
        assert chain[0]["inputs"]["strength_model"] == 0.8
        lora_id = next(i for i, n in graph.items() if n is chain[0])
        assert nodes["BasicGuider"][0]["inputs"]["model"] == [lora_id, 0]

    def test_it_decodes_and_saves_under_the_job_prefix(self):
        graph = _txt2img_graph("flux2", FLUX2_CONFIG)
        nodes = _by_class(graph)
        vae_id = next(i for i, n in graph.items() if n["class_type"] == "VAELoader")
        assert nodes["VAEDecode"][0]["inputs"]["vae"] == [vae_id, 0]
        assert nodes["SaveImage"][0]["inputs"]["filename_prefix"] == (
            "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"
        )

    def test_family_level_defaults_are_the_ordinary_graphs_flux_guidance_widget(self):
        assert variant_defaults("txt2img", "flux2", "") == {"cfg_scale": 4.0}


FLUX2_TXT2IMG_DISTILLED_CONFIG = {**FLUX2_CONFIG, "variant": "distilled"}


class TestFlux2DistilledTxt2ImgGraph:
    def test_no_flux_guidance_or_basic_guider(self):
        nodes = _by_class(_txt2img_graph("flux2", FLUX2_TXT2IMG_DISTILLED_CONFIG))
        assert "FluxGuidance" not in nodes
        assert "BasicGuider" not in nodes

    def test_cfg_guider_guides_with_a_zeroed_negative_at_cfg_scale(self):
        graph = _txt2img_graph("flux2", FLUX2_TXT2IMG_DISTILLED_CONFIG)
        nodes = _by_class(graph)
        text_id = next(i for i, n in graph.items() if n["class_type"] == "CLIPTextEncode")
        zero_id = next(i for i, n in graph.items() if n["class_type"] == "ConditioningZeroOut")
        assert nodes["ConditioningZeroOut"][0]["inputs"]["conditioning"] == [text_id, 0]
        guider = nodes["CFGGuider"][0]
        assert set(guider["inputs"]) == {"model", "positive", "negative", "cfg"}
        assert guider["inputs"]["cfg"] == 6.5
        assert guider["inputs"]["positive"] == [text_id, 0]
        assert guider["inputs"]["negative"] == [zero_id, 0]

    def test_the_variant_defaults_are_the_bundled_distilled_widgets(self):
        assert variant_defaults("txt2img", "flux2", "distilled") == {
            "steps": 4, "cfg_scale": 1.0,
        }


class TestIgnoredParams:
    def test_flux2_txt2img_names_its_two_unusable_params(self):
        reasons = ignored_params("txt2img", "flux2")
        assert set(reasons) == {"negative_prompt", "scheduler"}
        assert all(reasons.values())

    def test_a_checkpoint_pairing_has_nothing_to_ignore(self):
        assert ignored_params("txt2img", "") == {}

    def test_an_unknown_pairing_is_empty_not_a_crash(self):
        assert ignored_params("not-a-mode", "not-a-family") == {}
