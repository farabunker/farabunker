"""Unit tests for models/contracts/engines/comfyui.py (spec §4.3).

HTTP is mocked at the `httpx` layer (never the engine's own methods), so
the adapter's real `/object_info` parsing runs. No DB.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from models.contracts.catalog import norm_tag
from models.contracts.engines import ENGINES, get_engine
from models.contracts.engines import comfyui
from models.contracts.engines.base import SETUP_PLATFORMS
from models.contracts.engines.comfyui import ComfyUIEngine
from models.contracts.engines.comfyui_workflows import families, template_keys
from models.contracts.operations import GenerationRequest
from tools.vision.tests._helpers import FakeComfyUI, free_effect, reset_engine_caches

ENDPOINT = "http://comfy.local:8188"

# The seam tests submit for real rather than poking the run memo: the
# memory baseline is stamped by `ComfyUIGenerator.submit`, and a test that
# writes the memo itself would be testing the test.
_SUBMIT_PARAMS = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": 1024, "height": 1024,
    "steps": 25, "cfg_scale": 7.0, "seed": 99, "sampler": "euler",
    "scheduler": "normal", "batch_size": 1,
}


def _submit_once(fake, endpoint=ENDPOINT, model_id="sdxl.safetensors"):
    request = GenerationRequest(
        operation="txt2img", model_id=model_id, params=dict(_SUBMIT_PARAMS), client_ref="test"
    )
    with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
         patch("models.contracts.engines.comfyui.httpx.post", fake.post):
        ComfyUIEngine().build_image_generator(model_id, endpoint).submit(request)


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()


class TestRegistrationAndMetadata:
    def test_registered_under_its_own_name(self):
        assert isinstance(get_engine("comfyui"), ComfyUIEngine)
        assert "comfyui" in ENGINES

    def test_console_facing_metadata(self):
        engine = ComfyUIEngine()
        assert engine.name == "comfyui"
        assert engine.api_description == "the ComfyUI HTTP API"
        assert engine.library_url == "https://github.com/comfyanonymous/ComfyUI"
        assert "ComfyUI/models/checkpoints/" in engine.install_cmd_template
        assert "ComfyUI/models/unet/" in engine.install_cmd_template
        assert "ComfyUI/models/text_encoders/" in engine.install_cmd_template
        assert "ComfyUI/models/vae/" in engine.install_cmd_template
        assert engine.well_known_ports == (8188,)


class TestIsHealthy:
    def test_true_when_system_stats_answers(self):
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().is_healthy(ENDPOINT) is True

    def test_false_when_unreachable(self):
        fake = FakeComfyUI(healthy=False)
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().is_healthy(ENDPOINT) is False

    def test_caller_supplied_timeout_is_used(self):
        seen = {}

        def _get(url, params=None, timeout=None):
            seen["timeout"] = timeout
            return FakeComfyUI().get(url, params, timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", _get):
            ComfyUIEngine().is_healthy(ENDPOINT, timeout=0.5)
        assert seen["timeout"] == 0.5


class TestListInstalled:
    def test_every_checkpoint_becomes_an_image_generation_model(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors", "sd15.ckpt"))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [model.model_id for model in installed] == ["sdxl.safetensors", "sd15.ckpt"]
        assert all(model.capabilities == ("image-generation",) for model in installed)
        assert all(model.size is None and model.loaded is False for model in installed)

    def test_windows_subfolder_ids_are_passed_through_verbatim(self):
        """A Windows host reports `subdir\\file.safetensors`; the id is
        opaque and must never be split, normalized, or re-cased -- it goes
        straight back to ComfyUI as `ckpt_name`."""
        fake = FakeComfyUI(checkpoints=("sdxl\\turbo.safetensors",))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert installed[0].model_id == "sdxl\\turbo.safetensors"

    def test_no_checkpoints_is_an_empty_list(self):
        fake = FakeComfyUI(checkpoints=())
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_installed(ENDPOINT) == []

    def test_unreachable_endpoint_raises(self):
        """Adapters stay raw: retry/catch policy is the caller's
        (`discover()` isolates per engine+endpoint)."""
        import httpx

        with patch("models.contracts.engines.comfyui.httpx.get", side_effect=httpx.ConnectError("x")):
            with pytest.raises(httpx.HTTPError):
                ComfyUIEngine().list_installed(ENDPOINT)

    def test_gguf_and_native_unets_are_listed_beside_checkpoints(self):
        """A multi-file family's diffusion-model file is a model this engine
        can run, so it belongs in the listing next to a checkpoint -- tagged
        with the loader node that reported it, which is the ONE observable
        engine fact about it."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",),
            unets_gguf=("family-a-Q4_K_S.gguf",),
            unets=("family-b.safetensors",),
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [(m.model_id, m.loader) for m in installed] == [
            ("sdxl.safetensors", "CheckpointLoaderSimple"),
            ("family-a-Q4_K_S.gguf", "UnetLoaderGGUF"),
            ("family-b.safetensors", "UNETLoader"),
        ]
        assert all(m.capabilities == ("image-generation",) for m in installed)
        # Residency is the run memo's to report (memory-seams Task 2), and
        # nothing has run here -- so every row is honestly not resident.
        # Asserted so widening the listing cannot silently drop the
        # residency reporting that task added.
        assert all(m.loaded is False and m.loaded_size is None for m in installed)

    def test_an_absent_loader_node_reports_nothing_and_never_raises(self):
        """An install without the ComfyUI-GGUF pack has no `UnetLoaderGGUF`;
        the live server answers 200 with an empty body for it. The listing
        must lose that one node's models and keep every other node's."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",),
            unets_gguf=("never-seen.gguf",),
            missing_nodes=("UnetLoaderGGUF",),
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [m.model_id for m in installed] == ["sdxl.safetensors"]
        assert installed[0].loaded is False

    def test_the_same_file_reported_by_two_loaders_is_one_model(self):
        """Two loader nodes can see the same file. It is one model, and the
        FIRST loader to report it owns the row -- never a duplicate the
        console would render twice."""
        fake = FakeComfyUI(unets_gguf=("shared.gguf",), unets=("shared.gguf",))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [(m.model_id, m.loader) for m in installed] == [
            ("shared.gguf", "UnetLoaderGGUF")
        ]
        # The dedupe keeps ONE row; it must not let the second loader's
        # sighting overwrite what the run memo believes about residency for
        # that model id (memory-seams Task 2's single-resident belief).
        assert installed[0].loaded is False


class TestListAssets:
    def test_each_kind_reads_its_own_loader_node(self):
        fake = FakeComfyUI(
            loras=("detail.safetensors",), vaes=("sdxl_vae.safetensors",),
            controlnets=("canny.pth",), upscalers=("4x_ultrasharp.pth",),
        )
        engine = ComfyUIEngine()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "lora")] == ["detail.safetensors"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "vae")] == ["sdxl_vae.safetensors"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "controlnet")] == ["canny.pth"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "upscale_model")] == ["4x_ultrasharp.pth"]
            assert all(a.kind == "lora" for a in engine.list_assets(ENDPOINT, "lora"))

    def test_unknown_kind_is_empty_without_any_http_call(self):
        called = []

        def _get(url, params=None, timeout=None):
            called.append(url)
            raise AssertionError("should not be called")

        with patch("models.contracts.engines.comfyui.httpx.get", _get):
            assert ComfyUIEngine().list_assets(ENDPOINT, "embedding") == []
        assert called == []


class TestListChoices:
    def test_samplers_and_schedulers_come_from_ksampler(self):
        fake = FakeComfyUI(samplers=("euler", "heun"), schedulers=("karras", "sgm_uniform"))
        engine = ComfyUIEngine()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert engine.list_choices(ENDPOINT, "sampler") == ("euler", "heun")
            assert engine.list_choices(ENDPOINT, "scheduler") == ("karras", "sgm_uniform")

    def test_unknown_param_key_is_empty(self):
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_choices(ENDPOINT, "guidance") == ()


class TestSupportedOperationsDerivation:
    def test_supported_operations_lists_every_shipped_template(self):
        assert ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT) == (
            "txt2img",
            "img2img",
            "inpaint",
            "upscale",
        )

    def test_supported_operations_follows_the_template_registry(self):
        """A registered template is reported with no edit to the adapter."""
        from models.contracts.engines import comfyui_workflows

        def _stub(request, model_id, config, inputs):
            return {}

        with patch.dict(comfyui_workflows._TEMPLATES, {("", "img2img"): _stub}):
            assert "img2img" in ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)

    def test_supported_operations_gained_img2img_with_no_adapter_edit(self):
        """`supported_operations` reads the template registry, so shipping
        a template is the whole of teaching the adapter a mode."""
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) >= {
            "txt2img",
            "img2img",
        }


class TestSetupGuide:
    """The adapter owns its own install guide; the /setup/ page just renders
    it (spec §8 / owner requirement 2026-08-23)."""

    def test_covers_every_platform_the_page_renders(self):
        guide = ComfyUIEngine.setup_guide
        assert set(guide.platforms) == set(SETUP_PLATFORMS)
        assert all(guide.platforms[key] for key in SETUP_PLATFORMS)

    def test_every_platform_says_how_to_start_it_listening(self):
        guide = ComfyUIEngine.setup_guide
        for key in ("macos", "linux"):
            commands = " ".join(step.command or "" for step in guide.platforms[key])
            assert "--listen 0.0.0.0" in commands
        windows = " ".join(step.body for step in guide.platforms["windows"])
        assert "--listen 0.0.0.0" in windows

    def test_network_note_explains_the_container_hop(self):
        assert "host.docker.internal" in ComfyUIEngine.setup_guide.network_note

    def test_models_note_names_the_checkpoint_folder_and_who_downloads(self):
        note = ComfyUIEngine.setup_guide.models_note
        assert "models/checkpoints" in note
        assert "models/loras" in note
        assert "models/vae" in note
        assert "never downloads" in note

    def test_models_note_names_the_multi_file_family_folders(self):
        """The GGUF/UNet folders a multi-file model family (flux2/qwen_image
        edit) needs, added once Task 4's registration form existed to
        collect which installed file plays which role."""
        note = ComfyUIEngine.setup_guide.models_note
        assert "models/unet" in note
        assert "models/text_encoders" in note

    def test_verify_path_matches_the_health_check(self):
        assert ComfyUIEngine.setup_guide.verify_url_path == "/system_stats"

    def test_it_declares_the_capability_it_serves(self):
        assert ComfyUIEngine.serves_capabilities == ("image-generation",)


class TestUnsupportedRoles:
    def test_build_llm_refuses(self):
        with pytest.raises(NotImplementedError, match="image generation only"):
            ComfyUIEngine().build_llm("sdxl.safetensors", ENDPOINT)

    def test_build_embedder_refuses(self):
        with pytest.raises(NotImplementedError, match="image generation only"):
            ComfyUIEngine().build_embedder("sdxl.safetensors", ENDPOINT)


class TestComboShapes:
    """ComfyUI 0.33.0 serves two combo shapes at once: the legacy
    `[[...values...], {...}]` (CheckpointLoaderSimple, LoraLoader) and the
    newer `["COMBO", {"multiselect": false, "options": [...]}]`
    (UpscaleModelLoader). The adapter reads both, and the double defaults
    to the newer one so a green suite cannot hide an unfillable form."""

    def test_a_combo_spec_is_read_from_its_options_list(self):
        fake = FakeComfyUI(upscalers=("4x-ultrasharp.pth",))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "upscale_model")
        assert [asset.asset_id for asset in assets] == ["4x-ultrasharp.pth"]

    def test_the_legacy_shape_is_still_read(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), combo_shape="legacy")
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)
        assert [model.model_id for model in installed] == ["sdxl.safetensors"]

    def test_an_empty_combo_reports_nothing_rather_than_raising(self):
        fake = FakeComfyUI(upscalers=())
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_assets(ENDPOINT, "upscale_model") == []

    def test_a_typed_input_is_still_not_a_combo(self):
        """`KSampler.seed` is `["INT", {...}]` -- a two-element list whose
        first entry is a string, exactly like a COMBO spec. It must not be
        mistaken for one.

        Calls the PARSER directly: `list_choices` short-circuits on
        `_CHOICE_INPUTS` (which holds only "sampler" and "scheduler") and
        returns `()` without issuing a request at all, so going through it
        would pin the lookup table rather than the guard under test."""
        fake = FakeComfyUI()
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert comfyui._combo_values(ENDPOINT, "KSampler", "seed") == ()


class TestObjectInfoMemo:
    """B6: one page render asks for a sampler list, a scheduler list, and
    every asset param's list -- serially, each at DISCOVERY_TIMEOUT. The
    first two are the SAME node. A short-lived memo collapses the burst
    without pretending the engine's model folders never change."""

    def test_two_lookups_of_the_same_node_cost_one_request(self):
        fake = FakeComfyUI()
        calls = []

        def counting_get(url, params=None, timeout=None):
            calls.append(url)
            return fake.get(url, params=params, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", counting_get):
            engine = ComfyUIEngine()
            assert engine.list_choices(ENDPOINT, "sampler") == ("euler", "dpmpp_2m")
            assert engine.list_choices(ENDPOINT, "scheduler") == ("normal", "karras")

        assert len(calls) == 1

    def test_different_nodes_are_still_fetched_separately(self):
        fake = FakeComfyUI(loras=("style.safetensors",))
        calls = []

        def counting_get(url, params=None, timeout=None):
            calls.append(url)
            return fake.get(url, params=params, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", counting_get):
            engine = ComfyUIEngine()
            engine.list_choices(ENDPOINT, "sampler")
            engine.list_assets(ENDPOINT, "lora")

        assert len(calls) == 2

    def test_the_memo_expires_so_a_newly_placed_file_appears(self):
        fake = FakeComfyUI(loras=())
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            assert engine.list_assets(ENDPOINT, "lora") == []
            fake.loras = ("style.safetensors",)
            with patch.object(comfyui, "_OBJECT_INFO_TTL", -1):
                assert [asset.asset_id for asset in engine.list_assets(ENDPOINT, "lora")] == [
                    "style.safetensors"
                ]

    def test_a_failed_lookup_is_not_remembered(self):
        """Caching an outage would make a recovered engine look down for
        as long as the TTL."""
        fake = FakeComfyUI(healthy=False)

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("models.contracts.engines.comfyui.httpx.get", failing_get):
            with pytest.raises(httpx.HTTPError):
                ComfyUIEngine().list_choices(ENDPOINT, "sampler")

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_choices(ENDPOINT, "sampler") == ("euler", "dpmpp_2m")


class TestLoadedFootprint:
    """The queue's footprint-learning seam. ComfyUI has NO per-model
    residency endpoint (verified: `/system_stats` reports machine memory
    only, and on MPS every free number in it is the same
    `psutil.virtual_memory().available`), so the adapter measures the
    memory that disappeared across its own run rather than asking."""

    def test_none_before_anything_has_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_the_memory_that_disappeared_across_the_run_is_the_footprint(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000  # 8 GB went somewhere during the run

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            measured = ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")

        assert measured == 8_000_000_000

    def test_the_worker_asks_with_a_tag_normalized_id(self):
        """`models/queue/worker.py` calls `loaded_footprint(endpoint,
        norm_tag(model_id))`, and `norm_tag` appends `:latest` to any id
        without a colon -- which every ComfyUI checkpoint filename is. An
        adapter keyed on the raw name would silently never match."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, norm_tag("sdxl.safetensors")) == 8_000_000_000

    def test_none_for_a_model_this_endpoint_did_not_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "other.safetensors:latest") is None

    def test_none_for_an_endpoint_this_adapter_never_submitted_to(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert (
                ComfyUIEngine().loaded_footprint("http://elsewhere:8188", "sdxl.safetensors:latest")
                is None
            )

    def test_a_noise_sized_delta_is_not_a_measurement(self):
        """Under-reporting is the dangerous direction: the scheduler treats
        `None` as "unknown, runs alone" but would treat 10 MB as "fits
        alongside anything". No image checkpoint is 10 MB, so this delta is
        another process, not a model."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 39_990_000_000  # 10 MB

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_memory_freed_during_the_run_is_not_a_negative_footprint(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=32_000_000_000)
        _submit_once(fake)
        fake.ram_free = 40_000_000_000  # something else quit

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_an_unreachable_engine_yields_none_and_never_raises(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("models.contracts.engines.comfyui.httpx.get", failing_get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_malformed_system_stats_body_yields_none_and_never_raises(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        def junk_get(url, params=None, timeout=None):
            from tools.vision.tests._helpers import _Response

            return _Response(payload={"devices": "not a list", "system": None})

        with patch("models.contracts.engines.comfyui.httpx.get", junk_get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_stale_memo_stops_measuring(self):
        """A memo older than its TTL is no longer evidence that anything is
        resident -- ComfyUI may have been restarted, and `/system_stats`
        exposes no pid or uptime to detect that with."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch.object(comfyui, "_RUN_MEMO_TTL", -1):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_the_memo_is_bounded(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",))
        for port in range(8200, 8212):
            _submit_once(fake, endpoint=f"http://comfy.local:{port}")

        assert len(comfyui._RUN_MEMO) <= comfyui._RUN_MEMO_MAX

    def test_a_second_run_of_a_warm_model_reports_none(self):
        """The footprint is learned on the COLD load. A re-run of a model
        that is already resident re-stamps the baseline `resident_at_submit
        =True` (`_remember_run` saw the memo it replaced already naming
        this same model), so `loaded_footprint` refuses to report a number
        for it at all -- `None`, which the worker skips (`if not size:
        continue`), leaving the cold-load value standing on the
        connection. Reporting the warm delta instead would overwrite a
        correct 8 GB with a wrong small number, which is the under-count
        direction that ends in an OOM."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)                       # cold load
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 8_000_000_000

        _submit_once(fake)                       # warm re-run, baseline now 32 GB
        fake.ram_free = 31_960_000_000           # 40 MB of activations

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_resident_at_submit_reports_none_even_for_a_credible_sized_delta(self):
        """The Klein incident (2026-08-25, job 24): a model already resident
        at submit only shows the run's incremental growth, not its real
        footprint -- and that growth can itself clear
        `_MIN_CREDIBLE_FOOTPRINT` (Klein measured 2,537,799,680 bytes / 2.4
        GiB of growth, comfortably above the 256 MiB floor, against an
        actual ~19.5 GB resident checkpoint). The credible-floor guard
        alone cannot catch this case, so `resident_at_submit` must be
        checked first and unconditionally win."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)                        # cold load
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 8_000_000_000

        _submit_once(fake)                        # warm re-run, already resident at submit
        fake.ram_free = 29_000_000_000            # 3 GB of further growth -- above the floor

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_cold_load_of_a_different_model_still_measures_normally(self):
        """`resident_at_submit` must be genuine self-residency, not "any
        run happened before" -- a previous run of a DIFFERENT model does
        not suppress measurement of a freshly-loaded one."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors", "other.safetensors"), ram_free=40_000_000_000
        )
        _submit_once(fake, model_id="other.safetensors")  # cold load of a different model
        fake.ram_free = 32_000_000_000

        _submit_once(fake, model_id="sdxl.safetensors")   # a new model swapped in
        fake.ram_free = 20_000_000_000                     # 12 GB for the new checkpoint

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 12_000_000_000

    def test_the_device_number_wins_over_the_host_number(self):
        """On MPS the two are the same value by construction, but on a
        discrete-GPU host they are separate pools and the device's is the
        one a checkpoint lives in. Pinned so a refactor cannot quietly
        start measuring host RAM on a machine where that is the wrong
        pool."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000, vram_free=8_000_000_000
        )
        _submit_once(fake)
        fake.vram_free = 2_000_000_000           # 6 GB of VRAM went to the model

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 6_000_000_000

    def test_a_concurrent_submit_during_measurement_is_not_clobbered(self):
        """`loaded_footprint` captures `memo` (the baseline it measures
        against) BEFORE its own `_free_bytes` network round trip. If a
        NEWER `submit()` at this same endpoint re-stamps `_RUN_MEMO` while
        that round trip is still in flight, the delta this call computes
        was measured against a baseline that is no longer the endpoint's
        current run. Writing it back would clobber the newer memo --
        attaching this stale delta to whatever the newer run's own
        `loaded_footprint` call has not measured yet. The write must be
        skipped; the newer memo must survive untouched, even though this
        call still returns the (still-honest, for THIS run) delta."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors", "other.safetensors"), ram_free=40_000_000_000
        )
        _submit_once(fake)  # cold load of sdxl.safetensors, baseline 40 GB free
        fake.ram_free = 32_000_000_000  # 8 GB gone -- what this call is about to measure

        def racing_get(url, params=None, timeout=None):
            # A second submit() lands and re-stamps the memo -- for a
            # DIFFERENT model -- while this call's own /system_stats round
            # trip is still in flight.
            comfyui._remember_run(ENDPOINT, "other.safetensors", 32_000_000_000)
            return fake.get(url, params=params, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", racing_get):
            measured = ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")

        assert measured == 8_000_000_000  # still an honest answer for THIS call

        current = comfyui._run_memo(ENDPOINT)
        assert current.model_key == norm_tag("other.safetensors")
        assert current.footprint is None  # NOT overwritten with sdxl's stale delta


class TestUnload:
    """The queue's eviction seam. ComfyUI frees ALL models at an endpoint
    or none -- there is no per-model free -- so this is deliberately a
    blunt instrument, and its docstring says so.

    `unload` is now a BARRIER (2026-08-25, the job-28 incident: a 2xx from
    `/free` is not itself confirmation, because ComfyUI's prompt-worker
    thread consumes that flag on ITS next loop tick, not synchronously
    with the POST). So every test here that expects `True` must make the
    fake ComfyUI actually show the free-memory rise on `/system_stats`
    (via `free_effect`) -- a fake that only accepts the POST is, correctly,
    the "no effect observed" case now.
    """

    def test_it_posts_free_with_both_flags_and_waits_for_the_effect(self):
        fake = FakeComfyUI(ram_free=20_000_000_000)
        get, post = free_effect(fake)

        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

        assert fake.free_calls == [{"unload_models": True, "free_memory": True}]

    def test_it_keeps_polling_until_comfyui_actually_frees_memory(self):
        """The first `/system_stats` read right after `/free` returns can
        still show the PRE-free number -- ComfyUI's prompt-worker thread
        has not necessarily consumed the flag yet. A single-check
        implementation would report `False` (or, worse, `True` off a stale
        reading); this pins that the adapter genuinely keeps polling."""
        fake = FakeComfyUI(ram_free=20_000_000_000)
        get, post = free_effect(fake, settle_after_polls=3)

        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

    def test_a_refusing_engine_is_false_not_an_exception(self):
        fake = FakeComfyUI(free_status=500)
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_an_unreachable_engine_is_false_not_an_exception(self):
        fake = FakeComfyUI()

        def failing_post(url, json=None, files=None, data=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", failing_post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_a_refused_post_never_enters_the_settle_poll(self):
        """A refused/unreachable POST returns immediately -- it must not
        pay the settle poll's own timeout on top of the POST's, and must
        not issue a single further GET once the POST itself has been
        attempted (not pinned to an exact pre-POST count -- the baseline
        `free_before` read is this call's own implementation detail --
        just that nothing was read AFTER the POST)."""
        fake = FakeComfyUI(free_status=500)
        calls = {"get": 0}
        snapshot = {}

        def counting_get(url, params=None, timeout=None):
            calls["get"] += 1
            return fake.get(url, params=params, timeout=timeout)

        def snapshotting_post(url, json=None, files=None, data=None, timeout=None):
            snapshot["get_count_at_post"] = calls["get"]
            return fake.post(url, json=json, files=files, data=data, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", counting_get), \
             patch("models.contracts.engines.comfyui.httpx.post", snapshotting_post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

        assert calls["get"] == snapshot["get_count_at_post"]

    def test_a_transient_baseline_read_failure_falls_back_without_polling(self):
        """A single `/system_stats` hiccup right at the top of `unload()`
        (the `free_before` read) leaves nothing for a delta-based poll to
        ever measure against, at any point before the deadline -- looping
        anyway would only burn the full settle budget and report `False`
        for a free that may well have genuinely happened. This must
        degrade to the pre-barrier contract instead (POST accepted, one
        immediate `/queue` check, no poll) rather than consume budget."""
        fake = FakeComfyUI(ram_free=20_000_000_000)
        calls = {"system_stats": 0}

        def flaky_get(url, params=None, timeout=None):
            if url.endswith("/system_stats"):
                calls["system_stats"] += 1
                if calls["system_stats"] == 1:
                    raise httpx.ConnectError("transient")
            return fake.get(url, params=params, timeout=timeout)

        started = time.monotonic()
        with patch("models.contracts.engines.comfyui.httpx.get", flaky_get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            result = ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors")
        elapsed = time.monotonic() - started

        assert result is True  # POST accepted, `/queue` empty -- the fallback contract
        assert elapsed < 1.0  # never looped, never waited anywhere near UNLOAD_TIMEOUT

    def test_no_effect_by_the_settle_timeout_is_false(self):
        """ComfyUI accepted the POST but never actually freed anything
        visible on `/system_stats` (a wedged prompt-worker thread, or a
        server that silently ignored the flag) -- the settle poll must
        give up and report `False`, not the old "2xx is enough" `True`.

        A small POSITIVE budget (not 0.0) so the settle loop genuinely
        enters and reads at least once, rather than the deadline having
        already elapsed before the loop is ever reached."""
        fake = FakeComfyUI(ram_free=20_000_000_000)  # never rises

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post), \
             patch.object(comfyui, "UNLOAD_TIMEOUT", 0.01):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_a_still_running_prompt_withholds_success_until_it_clears(self):
        """Free memory already rose, but `/queue` still shows something
        executing -- the settle poll must not treat that as settled yet."""
        fake = FakeComfyUI(ram_free=20_000_000_000)
        get, post = free_effect(fake)
        fake.queue_running = [[0, "still-going", {}, {}, []]]

        clear_after = {"n": 0}

        def clearing_get(url, params=None, timeout=None):
            if url.endswith("/queue"):
                clear_after["n"] += 1
                if clear_after["n"] >= 2:
                    fake.queue_running = []
            return get(url, params=params, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", clearing_get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

        assert clear_after["n"] >= 2

    def test_a_running_prompt_still_never_waits_past_the_deadline(self):
        """A prompt that never clears must not buy extra time beyond
        `UNLOAD_TIMEOUT` -- the hard cap always wins. A small POSITIVE
        budget so the loop genuinely enters at least once rather than the
        deadline having already elapsed before it is ever reached."""
        fake = FakeComfyUI(ram_free=20_000_000_000)
        get, post = free_effect(fake)
        fake.queue_running = [[0, "forever", {}, {}, []]]  # never clears

        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch.object(comfyui, "UNLOAD_TIMEOUT", 0.01):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_the_settle_threshold_uses_the_endpoints_known_footprint(self):
        """When this endpoint's run memo already carries a measured
        footprint larger than the bare credible floor, the settle poll
        must wait for a MAJORITY of that footprint -- half, not all of it
        (see `unload`'s own docstring: `/free` has been observed to
        release only `mps`-device models on this host, so a checkpoint
        whose text encoder sits on `cpu` never gives back its full
        recorded footprint even on a genuinely successful free; demanding
        the whole amount would make every correct eviction stall the full
        settle timeout and falsely report `False`)."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000  # 8 GB resident, matches the memo

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert (
                ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")
                == 8_000_000_000
            )

        # A rise that clears the bare 256 MiB floor but falls well short of
        # HALF the memo's known 8 GB footprint (4 GB) must NOT be accepted.
        get, post = free_effect(fake, amount=300_000_000)  # +300 MB, below 4 GB
        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch.object(comfyui, "UNLOAD_TIMEOUT", 0.01):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

        # HALF the footprint (4 GB) IS accepted -- a majority, not the whole.
        get, post = free_effect(fake, amount=4_000_000_000)
        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

    def test_it_uses_the_bounded_unload_timeout_for_the_post_itself(self):
        """The worker charges this call against its heartbeat margin, so
        the POST may never inherit the 300s generation ceiling. A
        non-degenerate budget (not 0.0), so the assertion checks the POST
        actually received the literal constant rather than two zeroes
        trivially matching each other. The fake actually shows the free
        effect (rather than leaving the settle poll to spin/sleep out to
        the real 7s deadline, which would make this one test slow for no
        reason -- the POST's own timeout is what this test is about)."""
        seen = {}
        fake = FakeComfyUI(ram_free=20_000_000_000)
        get, post = free_effect(fake)

        def recording_post(url, json=None, files=None, data=None, timeout=None):
            seen["timeout"] = timeout
            return post(url, json=json, files=files, data=data, timeout=timeout)

        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", recording_post), \
             patch("models.contracts.engines.comfyui.time.sleep"), \
             patch.object(comfyui, "UNLOAD_TIMEOUT", 7.0):
            ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors")
            assert seen["timeout"] == 7.0


class TestResidency:
    """`models/queue/worker.py` only ever calls `unload()` for a model
    `list_installed` reported as loaded (three call sites, each guarded by
    `if not model.loaded: continue`). ComfyUI has no residency endpoint, so
    what this adapter reports is its own belief -- the checkpoint it last
    submitted and has not since freed -- and it is labelled as one."""

    def test_nothing_is_resident_before_anything_has_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors", "other.safetensors"))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [model.loaded for model in installed] == [False, False]

    def test_the_checkpoint_just_run_is_reported_resident(self):
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors", "other.safetensors"), ram_free=40_000_000_000
        )
        _submit_once(fake)

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert {model.model_id: model.loaded for model in installed} == {
            "sdxl.safetensors": True,
            "other.safetensors": False,
        }

    def test_a_measured_footprint_becomes_the_resident_size(self):
        """`worker._evict_to_match_plan` sums `loaded_size` over resident
        models for its budget arithmetic; without one, a resident model
        contributes zero."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            engine.loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")
            installed = engine.list_installed(ENDPOINT)

        assert installed[0].loaded_size == 8_000_000_000

    def test_a_successful_unload_ends_the_residency_claim(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        get, post = free_effect(fake)
        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            assert engine.list_installed(ENDPOINT)[0].loaded is False
            assert engine.loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_failed_unload_leaves_the_claim_standing(self):
        """Believing a model went away when it did not is the one error
        that could let two large checkpoints sit resident at once."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000, free_status=500
        )
        _submit_once(fake)

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_installed(ENDPOINT)[0].loaded is True

    def test_a_settle_timeout_also_leaves_the_claim_standing(self):
        """A `/free` ComfyUI accepted but never visibly acted on (the
        settle poll's own timeout) is exactly as untrustworthy as a
        refused POST -- the model must still read as resident. A small
        POSITIVE budget so the loop genuinely enters at least once."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)  # never actually frees anything on this fake

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch("models.contracts.engines.comfyui.httpx.post", fake.post), \
             patch.object(comfyui, "UNLOAD_TIMEOUT", 0.01):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_installed(ENDPOINT)[0].loaded is True

    def test_a_stale_memo_stops_claiming_residency(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        with patch("models.contracts.engines.comfyui.httpx.get", fake.get), \
             patch.object(comfyui, "_RUN_MEMO_TTL", -1):
            assert ComfyUIEngine().list_installed(ENDPOINT)[0].loaded is False


class TestQueueSeamDiscovery:
    """`models/queue/worker.py` finds both seams by `getattr` on the engine
    object it got from `get_engine(...)`, and calls `loaded_footprint` with
    a `norm_tag`-normalized id and `unload` with the raw one. This test
    walks that exact path, so a signature or naming drift fails here rather
    than silently disabling memory governance for every vision job."""

    def test_the_worker_s_own_call_shape_works_end_to_end(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        request = GenerationRequest(
            operation="txt2img",
            model_id="sdxl.safetensors",
            params=dict(_SUBMIT_PARAMS),
            client_ref="test",
        )
        engine = get_engine("comfyui")

        # `get`/`post` behave exactly like `fake.get`/`fake.post` until the
        # real `POST /free` inside `unload()` below -- only from that point
        # does `free_effect` make the fake's reported free memory actually
        # rise, the way a real ComfyUI's prompt-worker thread eventually
        # does (see `TestUnload`'s own docstring). `amount` is the full 8 GB
        # `measure(...)` is about to record as this endpoint's known
        # footprint -- comfortably clearing `unload`'s own settle threshold,
        # which is only HALF of that (a majority, not the whole footprint;
        # see `unload`'s docstring for the MPS reason).
        get, post = free_effect(fake, amount=8_000_000_000)
        with patch("models.contracts.engines.comfyui.httpx.get", get), \
             patch("models.contracts.engines.comfyui.httpx.post", post), \
             patch("models.contracts.engines.comfyui.time.sleep"):
            engine.build_image_generator("sdxl.safetensors", ENDPOINT).submit(request)
            fake.ram_free = 32_000_000_000

            measure = getattr(engine, "loaded_footprint", None)
            unload = getattr(engine, "unload", None)
            assert measure is not None and unload is not None

            resident = [m for m in engine.list_installed(ENDPOINT) if m.loaded]
            assert [m.model_id for m in resident] == ["sdxl.safetensors"]
            assert measure(ENDPOINT, norm_tag("sdxl.safetensors")) == 8_000_000_000
            assert unload(ENDPOINT, resident[0].model_id) is True


class TestSupportedOperations:
    def test_a_connection_with_no_family_gets_the_checkpoint_modes(self):
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) == {
            "txt2img", "img2img", "inpaint", "upscale",
        }

    def test_a_family_with_no_template_offers_nothing(self):
        """Honest silence, not a checkpoint graph: the page shows 'this
        model offers no modes here' rather than a form that cannot run."""
        assert ComfyUIEngine().supported_operations(
            "weights.gguf", ENDPOINT, family="not-a-family-we-have"
        ) == ()

    def test_it_still_reads_the_registry_and_never_a_hand_written_tuple(self):
        """ADR 0012's derivation honesty: adding a template is still the
        only edit that changes what this reports."""
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) == set(
            template_keys()
        )

    def test_flux2_offers_edit_and_txt2img(self):
        """R1: `flux2` gained a second template, and this adapter reports
        it with no change beyond the registry itself."""
        assert ComfyUIEngine().supported_operations(
            "weights.gguf", ENDPOINT, family="flux2"
        ) == ("edit", "txt2img")


class TestTextEncodersAndFamilies:
    def test_text_encoders_union_both_clip_loaders_first_seen_order(self):
        """A text encoder can be a `.gguf` (only the GGUF loader sees it) or
        a safetensors (both loaders do). The honest answer is the union, and
        a file both report is listed once."""
        fake = FakeComfyUI(
            text_encoders_gguf=("enc-a.gguf", "enc-b.safetensors"),
            text_encoders=("enc-b.safetensors", "enc-c.safetensors"),
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "text_encoder")

        assert [a.asset_id for a in assets] == [
            "enc-a.gguf", "enc-b.safetensors", "enc-c.safetensors",
        ]
        assert all(a.kind == "text_encoder" for a in assets)

    def test_text_encoders_survive_an_absent_gguf_pack(self):
        fake = FakeComfyUI(
            text_encoders_gguf=("never-seen.gguf",),
            text_encoders=("enc-c.safetensors",),
            missing_nodes=("CLIPLoaderGGUF",),
        )
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "text_encoder")

        assert [a.asset_id for a in assets] == ["enc-c.safetensors"]

    def test_families_are_the_engines_own_vocabulary_narrowed_to_what_we_can_run(self):
        """The strings come from ComfyUI's `CLIPLoader.type` combo -- this
        platform ships no list of model families -- and only those this
        adapter has a graph template for are offered, so an operator can
        never declare a family that would then run nothing."""
        fake = FakeComfyUI(families=("stable_diffusion", "flux2", "qwen_image"))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            offered = ComfyUIEngine().list_families(ENDPOINT)

        assert set(offered) <= set(fake.families)
        assert set(offered) == set(families())

    def test_a_family_the_engine_does_not_report_is_never_offered(self):
        """Even a family we have a template for is not offered by an engine
        build that does not know the string -- the engine's vocabulary is
        the authority on what its own loader will accept."""
        fake = FakeComfyUI(families=("stable_diffusion",))
        with patch("models.contracts.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_families(ENDPOINT) == ()


class TestVariantsAndParamDefaults:
    """Unlike `list_families`, `list_variants` is not intersected with an
    engine vocabulary -- ComfyUI has no word for this at all -- so these
    never touch `httpx` (ADR 0012 D-EDIT-6/7)."""

    def test_the_engine_republishes_the_variants_its_templates_implement(self):
        engine = ComfyUIEngine()
        assert engine.list_variants() == ("distilled",)

    def test_param_defaults_come_from_the_connections_own_declaration(self):
        engine = ComfyUIEngine()
        assert engine.param_defaults(
            "edit", {"family": "flux2", "variant": "distilled"}
        ) == {"steps": 4, "guidance": 1.0}
        assert engine.param_defaults("edit", {"family": "flux2"}) == {}
        assert engine.param_defaults("edit", None) == {}

    def test_txt2img_param_defaults_cover_both_the_family_and_its_variant(self):
        """R1: `flux2` txt2img reports family-level defaults for a
        connection that declared no variant, and its own distilled
        defaults (keyed `cfg_scale`, TXT2IMG's own spelling) for one that
        did."""
        engine = ComfyUIEngine()
        assert engine.param_defaults("txt2img", {"family": "flux2"}) == {"cfg_scale": 4.0}
        assert engine.param_defaults(
            "txt2img", {"family": "flux2", "variant": "distilled"}
        ) == {"steps": 4, "cfg_scale": 1.0}
        assert engine.param_defaults("txt2img", None) == {}


class TestIgnoredParams:
    """`ComfyUIEngine.ignored_params` republishes what the template
    package declares (`flux2_txt2img.IGNORES`, ADR 0012 D-EDIT-12) --
    pure, no HTTP, exactly like `param_defaults` beside it."""

    def test_flux2_txt2img_names_both_params_neither_graph_can_honour(self):
        ignored = ComfyUIEngine().ignored_params("txt2img", {"family": "flux2"})

        assert set(ignored) == {"negative_prompt", "scheduler"}
        assert "no negative-prompt input" in ignored["negative_prompt"]
        assert ignored["scheduler"] == (
            "This model's own noise schedule has no separate setting to choose."
        )

    def test_a_checkpoint_connection_ignores_nothing(self):
        engine = ComfyUIEngine()
        assert engine.ignored_params("txt2img", None) == {}
        assert engine.ignored_params("txt2img", {}) == {}

    def test_a_pairing_with_nothing_to_ignore_is_empty_not_an_error(self):
        assert ComfyUIEngine().ignored_params("edit", {"family": "flux2"}) == {}

    def test_a_json_null_family_reads_as_a_checkpoint(self):
        """`config["family"]` may be a JSON `null` on a stored connection;
        it must read as the empty family, never as the string 'None'."""
        assert ComfyUIEngine().ignored_params("txt2img", {"family": None}) == {}
