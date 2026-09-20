"""Shared test helpers for tools/vision/tests.

A plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Each test module imports what it needs; autouse fixtures
stay defined per module and delegate their bodies here, per repo
convention.

`FakeComfyUI` is an HTTP-LAYER double: tests patch
`models.contracts.engines.comfyui.httpx.get` / `.post` with its methods, so
the adapter's real URL building, JSON parsing, and error handling run. The
engine's own methods are never mocked away.

H11 part 2 (S11): `fetch_outputs`'s `/view` read and `_history`'s
`/history/<id>` read now go through `engine_http.get_bounded`, not
`httpx.get` -- `FakeComfyUI.get_bounded` is the matching double, patched
ALONGSIDE `.get` (never instead of it: `/queue`/`/system_stats`/
`/object_info` still go through plain `httpx.get`) wherever a test reaches
`status()` or `fetch_outputs()`. It returns real bytes for `get_bounded`'s
own caller (`comfyui.py`) to parse -- a JSON-encoded history entry, or a raw
image body -- never a pre-parsed dict, so the adapter's own `json.loads`/
byte-handling still runs for real.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from models.registry.models import ModelConnection, RoleBinding
from models.contracts.jobkinds import JobContext
from tools.vision import probe_cache
from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    grant, make_admin, make_entitlement, make_user, posture, seed_sweep_posture, sign_in,
    user_principal,
)


@pytest.fixture
def isolated_tool_registry():
    """Snapshot `agents.contracts.tools._TOOLS` on entry, restore it on
    exit.

    A module-global registry surviving between tests is exactly the state
    that makes a suite pass in one collection order and fail in the
    other, which is why the repo runs both orders. Any test that
    registers, re-registers, or clears a tool -- directly, or by calling
    `VisionConfig.ready()`, which now also calls
    `tools.vision.tools.build_generate_spec()` and registers its result
    -- needs this.

    A real `@pytest.fixture`, defined here rather than in a `conftest.py`
    (the repo forbids those -- module docstring above): a test module
    that needs it imports it BY NAME, e.g. `from tools.vision.tests.
    _helpers import isolated_tool_registry  # noqa: F401` -- pytest
    discovers any fixture present in a test module's namespace, imported
    or defined locally, so the import alone makes it requestable there
    (directly, or via `pytest.mark.usefixtures("isolated_tool_registry")`
    / `pytestmark` for a whole module). Not autouse itself: most tests in
    this package never touch the registry, and a module that wants it on
    every test says so explicitly with its own `pytestmark`.
    """
    from agents.contracts import tools as tools_module

    saved = dict(tools_module._TOOLS)
    yield
    tools_module._TOOLS.clear()
    tools_module._TOOLS.update(saved)


def clear_bindings() -> None:
    """Body of the `_clear_bindings` autouse fixture used by every vision
    test that touches roles: migration 0002 may seed a connection/binding
    pair from an operator's environment, and these tests assume they own
    the registry.

    Also drops `probe_cache` (C-07 half B). Nearly every one of these
    test modules binds the SAME literal stub endpoint
    (`"http://stub:9999"`) with a DIFFERENT healthy state per test --
    without this, a 30-second-cached answer from one test's `_bind()`
    would leak into the next test's, in this file or another, that reuses
    the same (engine, endpoint) key. Mirrors production: a role/connection
    change is exactly the kind of event `probe_cache` invalidates on."""
    RoleBinding.objects.all().delete()
    ModelConnection.objects.all().delete()
    probe_cache.invalidate()


@dataclass
class _Response:
    """Just enough of `httpx.Response` for the adapter's call sites."""

    status_code: int = 200
    payload: Any = None
    content: bytes = b""
    text: str = ""

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://comfyui.test/")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


def make_job_ctx(**overrides) -> JobContext:
    """A `models.contracts.jobkinds.JobContext` for calling `run_generate`
    directly (T3) -- no real worker/DB behind it, just inert `_report`/
    `_checkpoint` writers. See `models/registry/tests/_helpers.py`'s
    identical function for why this is duplicated per app rather than
    imported cross-app."""
    fields = dict(
        job_id=1,
        attempt=0,
        checkpoint_state=None,
        _report=lambda progress: None,
        _checkpoint=lambda state: None,
    )
    fields.update(overrides)
    return JobContext(**fields)


def make_tool_ctx(**overrides):
    """An `agents.contracts.tools.ToolContext` for calling a tool runner
    directly -- no agent, no turn, no queue behind it.

    Duplicated per app, exactly as `make_job_ctx` above is (see
    `models/registry/tests/_helpers.py`'s identical note). Reuses this
    module's own `make_job_ctx` for the `job` field.
    """
    import time

    from agents.contracts.tools import Principal, StepBudget, ToolContext

    fields = dict(
        conversation_id="00000000-0000-0000-0000-000000000000",
        principal=Principal(kind="resident_agent", key="test-agent"),
        depth=0,
        budget=StepBudget(steps=8, deadline_monotonic=time.monotonic() + 900.0),
        job=make_job_ctx(),
    )
    fields.update(overrides)
    return ToolContext(**fields)


def object_info(node: str, inputs: dict[str, Any], combo_shape: str = "v3") -> dict:
    """A `/object_info/<node>` body in ComfyUI's real shape.

    Every required input maps to `[<spec>, <options dict>]`. A COMBO
    input's spec has TWO live forms in ComfyUI 0.33.0 and this double can
    emit either:

    - `"v3"` (default) -- `["COMBO", {"multiselect": False, "options":
      [...]}]`, what `UpscaleModelLoader.model_name` really returns today;
    - `"legacy"` -- `[[...values...], {...}]`, what
      `CheckpointLoaderSimple.ckpt_name` and `LoraLoader.lora_name` still
      return.

    The default is the NEWER shape on purpose: a double that only speaks
    the legacy form lets the suite stay green while the live server hands
    the adapter something it cannot parse.
    """
    rendered = {}
    for input_key, spec in inputs.items():
        if isinstance(spec, list) and isinstance(spec[0], list) and combo_shape == "v3":
            rendered[input_key] = ["COMBO", {"multiselect": False, "options": list(spec[0])}]
        else:
            rendered[input_key] = spec
    return {node: {"input": {"required": rendered}}}


def reset_engine_caches() -> None:
    """Body of the `_reset_engine_caches` autouse fixture every test module
    that patches `comfyui.httpx` uses.

    The adapter memoizes `/object_info` for a few seconds (B6) AND keeps a
    per-endpoint run memo (the free-memory baseline `loaded_footprint`
    measures against, and the residency `list_installed` reports). Either
    one surviving between tests is exactly the kind of state that makes a
    suite pass in one order and fail in the other. Both are cleared before
    AND after each test, so a module that forgets the fixture is the only
    thing that can leak.
    """
    from models.contracts.engines.comfyui import clear_object_info_cache, clear_run_memo

    clear_object_info_cache()
    clear_run_memo()


def free_effect(
    fake: "FakeComfyUI", *, settle_after_polls: int = 2, amount: int | None = None
):
    """A `(get, post)` pair that makes `fake` behave like a REAL ComfyUI's
    `/free`: the POST itself returns immediately (unaffected, still
    recorded in `fake.free_calls`), but the free-memory rise it causes
    only becomes visible on `/system_stats` after that endpoint has been
    polled `settle_after_polls` times following the POST -- reproducing
    the prompt-worker thread's own flag-consumption delay (the job-28
    incident, 2026-08-25: the first read right after `/free` returns can
    still show the pre-free number) rather than an instant, same-tick free
    no live ComfyUI ever actually exhibits.

    The default is 2, not 1: a default of 1 would apply the bump on the
    VERY FIRST post-POST read -- exactly the same-tick instant free this
    helper exists to avoid reproducing. A caller using the default should
    also patch `models.contracts.engines.comfyui.time.sleep` (a real settle
    poll sleeps between reads), unless it separately shrinks
    `UNLOAD_TIMEOUT` to something the deadline-bounded sleep keeps
    negligible on its own.

    `amount` defaults to comfortably clearing
    `models.contracts.engines.comfyui._MIN_CREDIBLE_FOOTPRINT` so a caller
    needs no engine-internal knowledge to use this.
    """
    from models.contracts.engines.comfyui import _MIN_CREDIBLE_FOOTPRINT

    bump = _MIN_CREDIBLE_FOOTPRINT + 1 if amount is None else amount
    state = {"freed": False, "polls_since_free": 0, "bumped": False}

    def post(url, json=None, files=None, data=None, timeout=None):
        response = fake.post(url, json=json, files=files, data=data, timeout=timeout)
        if url.endswith("/free"):
            state["freed"] = True
        return response

    def get(url, params=None, timeout=None):
        if state["freed"] and not state["bumped"] and url.endswith("/system_stats"):
            state["polls_since_free"] += 1
            if state["polls_since_free"] >= settle_after_polls:
                fake.ram_free += bump
                state["bumped"] = True
        return fake.get(url, params=params, timeout=timeout)

    return get, post


@dataclass
class FakeComfyUI:
    """A ComfyUI server, as seen through `httpx`.

    Every field is the fact the corresponding endpoint should report:

    - `healthy` -> `GET /system_stats` (200 vs a raised `ConnectError`)
    - `checkpoints`/`loras`/`vaes`/`controlnets`/`upscalers` ->
      `GET /object_info/<loader node>`
    - `samplers`/`schedulers` -> `GET /object_info/KSampler`
    - `prompt_id`, `prompt_status`, `prompt_body` -> `POST /prompt`
    - `history` -> `GET /history/<id>` (keyed by prompt id)
    - `queue_running`/`queue_pending` -> `GET /queue`
    - `images` -> `GET /view` (keyed by filename)
    - `upload_status`/`upload_text`/`upload_name`/`upload_subfolder` ->
      `POST /upload/image` (recorded in `uploads`)
    - `ram_total`/`ram_free`/`vram_free` -> `GET /system_stats` memory fields
    - `free_status`/`free_calls` -> `POST /free`
    """

    healthy: bool = True
    checkpoints: tuple[str, ...] = ()
    # Diffusion-model files, listed by their own loader nodes: ComfyUI-GGUF's
    # `UnetLoaderGGUF` and the built-in `UNETLoader`. Separate fields because
    # they are separate nodes on the real server -- a build without the
    # custom-node pack reports the second and not the first.
    unets_gguf: tuple[str, ...] = ()
    unets: tuple[str, ...] = ()
    # Nodes this fake server does NOT have: it answers `200 {}` for each,
    # exactly as a live ComfyUI does for a node no installed pack provides.
    missing_nodes: tuple[str, ...] = ()
    loras: tuple[str, ...] = ()
    vaes: tuple[str, ...] = ()
    controlnets: tuple[str, ...] = ()
    upscalers: tuple[str, ...] = ()
    samplers: tuple[str, ...] = ("euler", "dpmpp_2m")
    schedulers: tuple[str, ...] = ("normal", "karras")
    prompt_id: str = "1a2b3c"
    prompt_status: int = 200
    prompt_body: dict | None = None
    history: dict = field(default_factory=dict)
    queue_running: list = field(default_factory=list)
    queue_pending: list = field(default_factory=list)
    images: dict = field(default_factory=dict)
    submitted: list = field(default_factory=list)
    view_calls: list = field(default_factory=list)
    upload_status: int = 200
    upload_text: str = ""
    upload_name: str | None = None
    upload_subfolder: str | None = None
    uploads: list = field(default_factory=list)

    # `/system_stats` memory reporting. The defaults are the live host's
    # real numbers (48 GiB unified, ~21 GiB free) so a test that does not
    # care reads like the real machine. On MPS every free number ComfyUI
    # reports is the same `psutil.virtual_memory().available` value
    # (verified in ComfyUI 0.33.0's `model_management.get_free_memory`),
    # which is why `vram_free` defaults to `ram_free` rather than to a
    # second, independent number that could not occur on this hardware.
    ram_total: int = 51539607552
    ram_free: int = 22714040320
    vram_free: int | None = None

    # `POST /free` -- `free_calls` records each body posted.
    free_status: int = 200
    free_calls: list = field(default_factory=list)

    # Which combo shape this server speaks -- "v3" (default, what live
    # ComfyUI 0.33.0 returns for UpscaleModelLoader) or "legacy".
    combo_shape: str = "v3"

    # Text encoders as each CLIP loader reports them. ComfyUI-GGUF's loader
    # sees `.gguf` files AND the safetensors beside them; the built-in one
    # sees only what core ComfyUI can load. Separate fields because they are
    # separate nodes on the real server.
    text_encoders_gguf: tuple[str, ...] = ()
    text_encoders: tuple[str, ...] = ()
    # `CLIPLoader.type` -- the engine's OWN model-family vocabulary.
    families: tuple[str, ...] = ("stable_diffusion", "flux2", "qwen_image")

    _NODE_INPUTS = {
        "CheckpointLoaderSimple": ("checkpoints", "ckpt_name"),
        "UnetLoaderGGUF": ("unets_gguf", "unet_name"),
        "UNETLoader": ("unets", "unet_name"),
        "LoraLoader": ("loras", "lora_name"),
        "VAELoader": ("vaes", "vae_name"),
        "ControlNetLoader": ("controlnets", "control_net_name"),
        "UpscaleModelLoader": ("upscalers", "model_name"),
        "CLIPLoaderGGUF": ("text_encoders_gguf", "clip_name"),
    }

    @staticmethod
    def _path(url: str) -> str:
        """The URL path ComfyUI would see, stripped of scheme+host --
        shared by `.get` and `.get_bounded` (H11 review round 1, minor 7)
        so the two doubles cannot disagree about which endpoint a URL
        names."""
        return url.split("://", 1)[-1].split("/", 1)[-1]

    def _dispatch_history(self, ref: str) -> dict | None:
        """The `/history/<ref>` lookup shared by `.get` and `.get_bounded`
        (minor 7): one place decides what a fake history entry looks
        like, so the two doubles cannot drift apart."""
        return self.history.get(ref)

    def _dispatch_view(self, params: dict | None) -> tuple[str, bytes | None]:
        """The `/view` lookup shared by `.get` and `.get_bounded` (minor
        7): records the call identically for both (`self.view_calls`),
        then returns `(filename, content-or-None)` -- `None` content
        means "not found", left to each caller to render in ITS OWN
        not-found shape (`.get`'s 404 `_Response`, `.get_bounded`'s
        `httpx.HTTPStatusError`)."""
        self.view_calls.append(params or {})
        filename = (params or {}).get("filename", "")
        return filename, self.images.get(filename)

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> _Response:
        path = self._path(url)

        if path == "system_stats":
            if not self.healthy:
                raise httpx.ConnectError("connection refused")
            device_free = self.ram_free if self.vram_free is None else self.vram_free
            return _Response(
                payload={
                    "system": {
                        "os": "darwin",
                        "ram_total": self.ram_total,
                        "ram_free": self.ram_free,
                        "comfyui_version": "0.33.0",
                    },
                    "devices": [
                        {
                            "name": "mps",
                            "type": "mps",
                            "index": None,
                            "vram_total": self.ram_total,
                            "vram_free": device_free,
                            "torch_vram_total": self.ram_total,
                            "torch_vram_free": device_free,
                        }
                    ],
                }
            )

        if path.startswith("object_info/"):
            node = path.split("/", 1)[1]
            if node == "KSampler":
                return _Response(
                    payload=object_info(
                        "KSampler",
                        {
                            "seed": ["INT", {"default": 0}],
                            "sampler_name": [list(self.samplers), {"default": self.samplers[0]}],
                            "scheduler": [list(self.schedulers), {"default": self.schedulers[0]}],
                        },
                        self.combo_shape,
                    )
                )
            if node == "CLIPLoader" and node not in self.missing_nodes:
                # The one node in this double with two required combos: the
                # encoder file list and the family vocabulary.
                return _Response(
                    payload=object_info(
                        "CLIPLoader",
                        {
                            "clip_name": [list(self.text_encoders), {"tooltip": "x"}],
                            "type": [list(self.families), {"tooltip": "x"}],
                        },
                        self.combo_shape,
                    )
                )
            if node in self._NODE_INPUTS and node not in self.missing_nodes:
                attribute, input_key = self._NODE_INPUTS[node]
                values = list(getattr(self, attribute))
                return _Response(
                    payload=object_info(node, {input_key: [values, {"tooltip": "x"}]}, self.combo_shape)
                )
            # A live ComfyUI answers 200 with an EMPTY body for a node this
            # build does not have (verified against 0.33.0), not a 404 --
            # which is exactly how an install without the ComfyUI-GGUF
            # custom-node pack reports its absent loaders. `_combo_values`
            # reads that as `()`; a double that 404s here would make the
            # adapter raise where the real server does not.
            return _Response(payload={})

        if path.startswith("history/"):
            ref = path.split("/", 1)[1]
            entry = self._dispatch_history(ref)
            return _Response(payload={ref: entry} if entry is not None else {})

        if path == "queue":
            return _Response(
                payload={"queue_running": self.queue_running, "queue_pending": self.queue_pending}
            )

        if path.startswith("view"):
            _filename, content = self._dispatch_view(params)
            if content is None:
                return _Response(status_code=404)
            return _Response(content=content)

        raise AssertionError(f"unexpected GET url: {url}")

    def get_bounded(
        self,
        url: str,
        *,
        timeout: float | None = None,
        params: dict | None = None,
        max_bytes: int | None = None,
        client=None,
        truncate: bool = False,
    ) -> bytes:
        """`engine_http.get_bounded`-compatible double for the reads that
        go through it: `_history`'s `/history/<id>` and `fetch_outputs`'s
        `/view` (H11 part 2, S11). Shares `.get`'s own URL-path dispatch
        (`._path`, `._dispatch_history`, `._dispatch_view`, minor 7) and
        state (`self.history`, `self.images`, `self.view_calls`) so both
        doubles answer the SAME fake server -- only the return shape
        differs: real bytes here (a JSON-encoded history entry, or a raw
        image body), never a pre-parsed dict, so `comfyui.py`'s own
        `json.loads` call runs for real, matching what a real
        `get_bounded` hands back.

        `client is None` is asserted, not just accepted: `comfyui.py`
        never builds its own `httpx.Client` and passes it in -- that
        parameter exists solely as the PRODUCTION `get_bounded`'s test
        seam (see its own docstring) -- so a call reaching this double
        WITH a client would mean `comfyui.py` grew a code path this fake
        was never told about.
        """
        assert client is None, "comfyui.py never passes its own httpx.Client to get_bounded"
        path = self._path(url)

        if path.startswith("history/"):
            ref = path.split("/", 1)[1]
            entry = self._dispatch_history(ref)
            payload = {ref: entry} if entry is not None else {}
            return json.dumps(payload).encode()

        if path.startswith("view"):
            _filename, content = self._dispatch_view(params)
            if content is None:
                request = httpx.Request("GET", url)
                raise httpx.HTTPStatusError(
                    "HTTP 404", request=request, response=httpx.Response(404, request=request)
                )
            return content

        raise AssertionError(f"unexpected GET (bounded) url: {url}")

    def post(
        self,
        url: str,
        json: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        timeout: float | None = None,
    ) -> _Response:
        if url.endswith("/upload/image"):
            field_name, (filename, handle, media_type) = next(iter((files or {}).items()))
            self.uploads.append(
                {
                    "field": field_name,
                    "filename": filename,
                    "media_type": media_type,
                    "content": handle.read(),
                    "data": dict(data or {}),
                }
            )
            if self.upload_status >= 400:
                return _Response(status_code=self.upload_status, text=self.upload_text)
            subfolder = (
                (data or {}).get("subfolder", "")
                if self.upload_subfolder is None
                else self.upload_subfolder
            )
            return _Response(
                payload={
                    "name": self.upload_name or filename,
                    "subfolder": subfolder,
                    "type": "input",
                }
            )

        if url.endswith("/free"):
            self.free_calls.append(json)
            return _Response(status_code=self.free_status)

        if not url.endswith("/prompt"):
            raise AssertionError(f"unexpected POST url: {url}")
        self.submitted.append(json)
        if self.prompt_status >= 400:
            return _Response(
                status_code=self.prompt_status,
                payload=self.prompt_body
                or {
                    "error": {
                        "type": "prompt_outputs_failed_validation",
                        "message": "Prompt outputs failed validation",
                    },
                    "node_errors": {
                        "1": {
                            "errors": [
                                {
                                    "message": "Value not in list",
                                    "details": "ckpt_name: 'nope.safetensors' not in ['sdxl.safetensors']",
                                }
                            ]
                        }
                    },
                },
            )
        return _Response(payload={"prompt_id": self.prompt_id, "number": 0, "node_errors": {}})


def history_success(prompt_id: str, filenames: tuple[str, ...] = ("job_00001_.png",)) -> dict:
    """A finished `/history/<id>` entry, in ComfyUI's real shape."""
    return {
        prompt_id: {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {
                "7": {
                    "images": [
                        {"filename": name, "subfolder": "", "type": "output"} for name in filenames
                    ]
                }
            },
        }
    }


def history_error(prompt_id: str, message: str = "Allocation on device") -> dict:
    """A failed `/history/<id>` entry: ComfyUI records the failure in
    `status.messages` as an `execution_error` tuple."""
    return {
        prompt_id: {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_start", {"prompt_id": prompt_id}],
                    ["execution_error", {"prompt_id": prompt_id, "exception_message": message}],
                ],
            },
            "outputs": {},
        }
    }


class StubGenerator:
    """A scripted `ImageGenerator` for service-layer tests.

    The service layer's contract is with the SEAM, not with ComfyUI, so its
    tests drive a stub generator rather than an HTTP double -- the ComfyUI
    adapter has its own HTTP-layer tests (test_comfyui_*.py).
    """

    def __init__(self, *, engine_ref="ref-1", states=None, outputs=(), submit_error=None):
        self.engine_ref = engine_ref
        self.states = list(states or [])
        self.outputs = list(outputs)
        self.submit_error = submit_error
        self.submitted = []
        self.status_calls = 0
        self.fetch_calls = 0

    def submit(self, request):
        self.submitted.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        return self.engine_ref, {"prompt": {"stub": True}, "client_id": request.client_ref}

    def status(self, engine_ref):
        self.status_calls += 1
        if not self.states:
            raise AssertionError("StubGenerator ran out of scripted states")
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    def fetch_outputs(self, engine_ref):
        self.fetch_calls += 1
        return list(self.outputs)


class StubEngine:
    """A registered engine that hands back a `StubGenerator` (and can be
    unhealthy on demand), so `resolve -> get_engine -> build_image_generator`
    runs for real."""

    name = "stubengine"
    api_description = "a stub API"
    well_known_ports = (9999,)

    def __init__(self, generator=None, healthy=True):
        self.generator = generator or StubGenerator()
        self.healthy = healthy
        # Every (model_id, endpoint, config) this engine was asked to build a
        # generator for. Tests assert on it to prove WHICH binding a call
        # used -- the point of the job-recorded-binding fix.
        self.built: list[tuple[str, str, dict]] = []

    def is_healthy(self, endpoint, timeout=None):
        if isinstance(self.healthy, Exception):
            raise self.healthy
        return self.healthy

    def list_installed(self, endpoint):
        return []

    def build_image_generator(self, model_id, endpoint, **cfg):
        self.built.append((model_id, endpoint, dict(cfg)))
        return self.generator


def png_bytes(width: int, height: int) -> bytes:
    """The smallest byte string `store.png_dimensions` can measure: the PNG
    signature plus an IHDR chunk whose width/height sit at bytes 16-24
    (`tools/vision/store.py`).

    ONE piece of PNG-header knowledge in this suite. `test_store.py` needs
    varying dimensions (it is testing the measurement); every other module
    needs one image and uses `PNG` below.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )


# The one image the rest of the suite uses, 512x512 -- what the view tests
# already assumed. `test_jobs.py`, `test_services.py` and
# `test_views_generate.py` each carried their own before this existed.
PNG = png_bytes(512, 512)


# The golden raw (unvalidated, string-typed -- as a POST body or a job
# kind's `payload["params"]` would carry them) txt2img param dict.
# `test_jobs.py` and `test_services.py` each carried their own identical
# copy before this existed.
RAW = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": "1024", "height": "1024",
    "steps": "25", "cfg_scale": "7.0", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}


def stored_output(tmp_path, job=None):
    """A finished `GenerationJob` plus one `GeneratedOutput` whose `path`
    is a real file on disk.

    The shared fixture for every test that needs an image the platform
    ALREADY HOLDS: the input-serving tests (Task 3), the stored-reference
    resolver (Task 4), and the gallery's "use in ..." links (Task 5). The
    existing `_job_with_output` (`test_views_gallery.py`) writes no file,
    so it cannot serve any of them.
    """
    from tools.vision.models import GeneratedOutput, GenerationJob

    if job is None:
        job = GenerationJob.objects.create(
            operation="txt2img",
            params={
                "prompt": "a lighthouse", "negative_prompt": "", "width": 512, "height": 512,
                "steps": 20, "cfg_scale": 7.0, "seed": 42, "sampler": "euler",
                "scheduler": "normal", "batch_size": 1,
            },
            seed=42, engine="stubengine", model_id="stub.safetensors",
            endpoint="http://stub:9999",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
        )
    source = tmp_path / "0-job_00001_.png"
    source.write_bytes(PNG)
    return GeneratedOutput.objects.create(
        job=job, index=0, path=str(source), media_type="image/png", width=512, height=512
    )


# THE FAKE `document` RESOLVER these tests register, and the box it
# answers out of. A MODULE-LEVEL function so the dotted path a test
# registers really resolves -- and, deliberately, NOT `tools.rag.access.
# artifact_file_for`: `tools/vision` may not import `tools/rag` in
# PRODUCTION (`foundation/ops/tests/test_import_law.py`'s peer gate), and
# a test that reached for the real one would make this column's own test
# suite depend on the peer it is forbidden to depend on. The contract --
# `(pk, principal) -> ArtifactFile`, `LookupError` for missing/file-less/
# invisible -- is what these tests exercise, and it is the same contract
# `tools/rag/tests/test_access_documents.py::TestArtifactFileFor` proves
# the real one keeps.
FAKE_DOCUMENTS: dict[int, object] = {}


def fake_document_resolver(pk: int, principal):
    """Registered by `document_resolver` (below) under the `document`
    kind. Raises `LookupError` for any pk `FAKE_DOCUMENTS` has no entry
    for -- the one exception the contract allows."""
    try:
        return FAKE_DOCUMENTS[pk]
    except KeyError:
        raise LookupError(f"document:{pk} does not name a readable document.") from None


@pytest.fixture
def no_document_resolver():
    """THE EMPTY STATE: no file resolver registered for ANY kind (a box
    with the document column uninstalled), with the real registry saved
    and restored around the test.

    Save/clear/restore, the same shape `agents/contracts/tests/
    _helpers.py::isolated_file_resolver_registry` has and for the same
    reason (a module-global registry surviving between tests is exactly
    what makes a suite pass in one collection order and fail in the
    other, which is why this repo runs both). `FAKE_DOCUMENTS` is
    cleared on both edges here too, so the ONE isolation body in this
    column lives in one fixture and `document_resolver` below only ever
    ADDS a registration to it -- rather than a second, near-identical
    copy of the same three-phase dance, which is exactly the shape that
    produced the I-4 leak `agents/contracts/tests/_helpers.py::
    isolated_attachment_registry`'s own docstring records.
    """
    from agents.contracts import artifacts

    saved = dict(artifacts._ARTIFACT_FILE_RESOLVERS)
    artifacts._ARTIFACT_FILE_RESOLVERS.clear()
    FAKE_DOCUMENTS.clear()
    try:
        yield
    finally:
        artifacts._ARTIFACT_FILE_RESOLVERS.clear()
        artifacts._ARTIFACT_FILE_RESOLVERS.update(saved)
        FAKE_DOCUMENTS.clear()


@pytest.fixture
def document_resolver(no_document_resolver):
    """`no_document_resolver`'s isolation PLUS one registration: the
    `document` kind resolved by this column's own fake. Yields
    `FAKE_DOCUMENTS` so a test writes the rows it wants to exist by
    `doc[pk] = ArtifactFile(...)`.

    Requests `no_document_resolver` rather than repeating its body --
    the save/clear/restore is written ONCE in this column.
    """
    from agents.contracts import artifacts

    artifacts.register_artifact_file_resolver(
        "document", "tools.vision.tests._helpers.fake_document_resolver")
    yield FAKE_DOCUMENTS


@pytest.fixture
def stub_generate_binding():
    """A `vision.generate` role bound to a registered STUB engine, for
    the one test that runs a real `services.submit_job`.

    A FIXTURE, NOT A PLAIN FUNCTION, and that is the whole point:
    engine registration in this repo is `patch.dict(ENGINES, {...})`
    (`tools/vision/tests/test_services.py:83`'s own `_registered`), a
    CONTEXT MANAGER -- a helper that merely called `ENGINES[name] = ...`
    would leak a stub engine into the global registry for the rest of the
    process. Entering the patch and yielding inside it is what makes the
    unregistration happen.

    Composed from pieces this module already owns (`StubEngine`,
    `StubGenerator`, `ModelConnection`, `RoleBinding`,
    `VISION_GENERATE_ROLE`), so nothing new is invented here -- this is
    `test_services.py:75-83`'s own `_bind()`/`_registered()` pair, made
    requestable by a second module.
    """
    from unittest.mock import patch

    from models.contracts.engines import ENGINES
    from models.contracts.roles import VISION_GENERATE_ROLE
    from models.registry.models import ModelConnection, RoleBinding

    engine = StubEngine()
    with patch.dict(ENGINES, {engine.name: engine}):
        connection = ModelConnection.objects.create(
            name="stub image model", engine=engine.name,
            endpoint="http://stub:9999", model_id="stub.safetensors",
            capabilities=["image-generation"],
        )
        RoleBinding.objects.create(
            role_key=VISION_GENERATE_ROLE, connection=connection)
        yield connection
