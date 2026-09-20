"""
ComfyUI inference engine adapter (D1, D2).

Implements the `InferenceEngine` seam (models/contracts/engines/base.py) for
ComfyUI's HTTP API, and its `ImageGenerator` half in `ComfyUIGenerator`.
ComfyUI's graph vocabulary -- node class names, node ids, the `/prompt`
payload shape -- lives ONLY in this module and `comfyui_workflows/`; above
this line the platform speaks `Operation`, `GenerationRequest`,
`JobStatus`, `InstalledModel`, `Asset`.

Nothing here downloads anything. `list_installed` reports what the operator
already placed in `ComfyUI/models/checkpoints/`, and `install_cmd_template`
tells the console to say exactly that.

HTTP is plain `httpx` with per-call timeouts and NO retries: retry policy is
a caller concern (the service layer decides), the same way `ollama.py`
leaves its errors raw for `discover()` to isolate.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from models.contracts.catalog import norm_tag
from models.contracts.engines.base import Asset, GenerationRejected, InstalledModel, JobStatus, SetupGuide, SetupStep
from models.contracts.engines.comfyui_workflows import (
    families, get_template, ignored_params as workflow_ignores, template_keys,
    variant_defaults, variants,
)
from models.contracts.engines.engine_http import get_bounded, safe_engine_ref
from models.contracts.operations import GenerationRequest

logger = logging.getLogger(__name__)

# A generation can legitimately run for minutes; submission and output
# fetching ride the same generous ceiling as Ollama's generation timeout.
DEFAULT_REQUEST_TIMEOUT = 300.0

# Health/listing calls are polled by pages and must fail fast instead.
DISCOVERY_TIMEOUT = 5.0

# S11 (review round 1, finding 3): `fetch_outputs` downloads one bounded
# body per "output" image a history entry names -- a COUNT the engine
# controls, not this platform. No workflow template in this codebase
# produces more than a handful of saved images per job (a batch_size,
# times at most a couple of SaveImage nodes); 64 is generous headroom over
# that with no real graph anywhere near it. Without this cap, a malformed
# or hostile history entry naming an implausible number of "output"
# images turns one `fetch_outputs` call into an unbounded number of
# `/view` round trips -- each individually bounded by `get_bounded`, but
# with no limit on how many of them happen.
MAX_OUTPUTS_PER_JOB = 64

# Below this, a free-memory delta is another process, not a checkpoint --
# the smallest image checkpoint anyone runs is an order of magnitude
# larger than 256 MiB. Reporting a noise-sized number would be WORSE than
# reporting nothing: `models/queue/scheduler.py` treats an unknown
# footprint as "this job runs alone" (safe), but would treat 200 MB as
# "this model fits alongside anything" (an OOM).
_MIN_CREDIBLE_FOOTPRINT = 256 * 1024 * 1024

# `unload` is neither a health probe nor a generation. ComfyUI's `/free`
# handler only sets a flag and returns, but that POST can still queue
# behind an in-flight request on a busy server: `DISCOVERY_TIMEOUT` (5s,
# sized for a bare health probe) would false-negative under exactly that
# mild contention, while `DEFAULT_REQUEST_TIMEOUT` (300s) would leave the
# worker -- which charges this call against its heartbeat margin -- hanging
# on a wedged engine. 30s, the same number and the same reasoning as
# `ollama.UNLOAD_TIMEOUT`.
#
# ALSO the hard cap on `unload`'s own settle poll (see its docstring), and
# deliberately ONE shared budget for the whole call rather than this
# amount for the POST plus another this amount for polling: `models/queue/
# worker.py`'s `MAX_UNLOADS_PER_TICK` comment already prices a single
# `unload()` call at "up to UNLOAD_TIMEOUT (30s)" when it reasons about
# `STALE_AFTER_SECONDS` margin. Doubling that budget here, unannounced to
# that module, would quietly invalidate arithmetic this adapter has no
# business touching.
UNLOAD_TIMEOUT = 30.0

# How often `unload`'s settle poll re-checks `/system_stats` (and, once
# that looks sufficient, `/queue`) while waiting for ComfyUI's prompt-
# worker thread to actually consume the `/free` flag it just set. Fine
# enough to notice a multi-second free promptly without hammering the
# engine; coarse enough that the worst case is a two-digit number of polls
# within `UNLOAD_TIMEOUT`, never a busy-loop.
UNLOAD_POLL_INTERVAL = 0.5

# How long a run memo is still evidence of anything. Long enough that no
# single generation outlives its own baseline (minutes, not hours); short
# enough that a memo left from a ComfyUI restarted hours ago stops
# claiming residency -- a restart is otherwise undetectable over HTTP,
# since `/system_stats` reports no pid and no uptime.
_RUN_MEMO_TTL = 6 * 3600.0

# At most this many endpoints are remembered at once (realistically one).
_RUN_MEMO_MAX = 8

# Which ComfyUI loader node(s) report each platform asset kind, and which
# of each node's required inputs holds the file list. Engine-specific by
# nature, so the mapping lives here rather than in the platform layer.
#
# A TUPLE of nodes per kind, because one kind can have more than one
# reporter: a text encoder is listed by ComfyUI-GGUF's `CLIPLoaderGGUF`
# (which sees `.gguf` files as well as safetensors) and by the built-in
# `CLIPLoader` (which sees only what core ComfyUI can load). The union in
# first-seen order is the honest answer; a file both report is one asset.
#
# "embedding" is deliberately absent: ComfyUI has no embedding LOADER node
# (embeddings are referenced from prompt text), so that kind honestly
# reports nothing.
_ASSET_NODES: dict[str, tuple[tuple[str, str], ...]] = {
    "lora": (("LoraLoader", "lora_name"),),
    "vae": (("VAELoader", "vae_name"),),
    "controlnet": (("ControlNetLoader", "control_net_name"),),
    "upscale_model": (("UpscaleModelLoader", "model_name"),),
    "text_encoder": (("CLIPLoaderGGUF", "clip_name"), ("CLIPLoader", "clip_name")),
}

# The node input whose combo IS this engine's model-family vocabulary: the
# `type` a CLIP loader is given for a multi-file family. Read live, so this
# platform ships no list of families and no list of models -- and so a
# ComfyUI build that does not know a family string never offers it.
_FAMILY_NODE = ("CLIPLoader", "type")

# Which node input backs each platform `"choice"` param.
_CHOICE_INPUTS: dict[str, tuple[str, str]] = {
    "sampler": ("KSampler", "sampler_name"),
    "scheduler": ("KSampler", "scheduler"),
}

# Every node whose combo IS a list of models this engine can run, in the
# order a listing reports them, and the input that holds the list.
#
# A single-file checkpoint (`CheckpointLoaderSimple`) is self-contained. The
# other two report a DIFFUSION MODEL file, which is only half a pipeline:
# it needs a text encoder and a VAE named alongside it on the connection
# (`ModelConnection.config`, D8). Which of the two reported a model is
# recorded on `InstalledModel.loader` so the console can tell those two
# cases apart without guessing from a filename.
#
# `UnetLoaderGGUF` belongs to the ComfyUI-GGUF custom-node pack. An install
# without it answers `/object_info/UnetLoaderGGUF` with `200 {}` (verified
# against a live 0.33.0), which `_combo_values` already reads as `()` --
# an absent pack costs this listing nothing and raises nothing.
_MODEL_NODES: tuple[tuple[str, str], ...] = (
    ("CheckpointLoaderSimple", "ckpt_name"),
    ("UnetLoaderGGUF", "unet_name"),
    ("UNETLoader", "unet_name"),
)

# ComfyUI's own upload endpoint and the multipart field it reads. An
# uploaded file lands in ComfyUI's `input/` folder and is addressed
# afterwards by the name ComfyUI reports back -- the ONLY handle a
# LoadImage-style node accepts. This is exactly the engine vocabulary that
# must not leave this module (spec §3), which is why the upload happens
# inside `submit` rather than being orchestrated by the platform.
_UPLOAD_PATH = "/upload/image"
_UPLOAD_FIELD = "image"


# How long a `/object_info/<node>` body is reused. One page render asks for
# a sampler list, a scheduler list (the SAME node), and one list per asset
# param -- serially, each at DISCOVERY_TIMEOUT. A few seconds collapses that
# burst into one request per node while staying far shorter than the time
# it takes an operator to drop a new file in and reload: what the page
# shows is still what the engine has.
_OBJECT_INFO_TTL = 5.0

# (endpoint, node) -> (monotonic timestamp, parsed body). Module-level, and
# deliberately the ONLY state in this adapter: engines stay stateless about
# BINDINGS (endpoint and model are passed per call, never held), which this
# does not change. Tests clear it via `clear_object_info_cache`.
_OBJECT_INFO_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def clear_object_info_cache() -> None:
    """Forget every memoized `/object_info` body. For tests, and for any
    caller that must see the engine's current state immediately."""
    _OBJECT_INFO_CACHE.clear()


@dataclass(frozen=True)
class _RunMemo:
    """What this adapter last did at ONE endpoint.

    ComfyUI has no per-model residency endpoint: `/system_stats` reports
    the machine's memory and nothing else, and on MPS every free number in
    it is the same `psutil.virtual_memory().available` (verified in
    ComfyUI 0.33.0's `comfy/model_management.get_free_memory`). So the
    only way to learn what a checkpoint costs is to measure the memory
    that disappeared across a run this adapter itself started -- which
    means remembering the "before".

    Fields:
        model_key: `norm_tag`-normalized model id, so the worker's
            `loaded_footprint(endpoint, norm_tag(model_id))` call matches
            (a checkpoint filename has no colon, so `norm_tag` always
            appends `:latest` to it).
        free_at_submit: bytes ComfyUI reported free immediately before the
            graph was queued.
        at: `time.monotonic()` at submit, for the TTL.
        footprint: the measured delta, once `loaded_footprint` has taken
            one -- so `list_installed` can report a size alongside its
            loaded flag without re-measuring.
        resident_at_submit: `True` when `model_key` was ALREADY the
            endpoint's believed-resident model at the moment this memo was
            stamped -- i.e. the memo `_remember_run` replaced named the
            same `norm_tag`-normalized model. `loaded_footprint` refuses to
            report a number when this is `True` (see its docstring): the
            baseline this memo's `free_at_submit` measures against is not
            "nothing loaded" but "this model already loaded", so any delta
            against it can only ever be the run's incremental growth, never
            the checkpoint's real footprint -- and that growth is not
            reliably small enough for the credible-floor guard alone to
            catch it (the distilled-checkpoint incident, 2026-08-25 job
            24: 2,537,799,680 bytes of growth measured, comfortably above
            `_MIN_CREDIBLE_FOOTPRINT`, against an actual ~19.5 GB resident
            checkpoint).
    """

    model_key: str
    free_at_submit: int
    at: float
    footprint: int | None = None
    resident_at_submit: bool = False


# endpoint -> the run this adapter last started there. The SECOND piece of
# module-level state in this adapter, of the same nature as
# `_OBJECT_INFO_CACHE` above: it holds no bindings (endpoint and model are
# still passed per call, never held), it is bounded, it expires, and tests
# clear it via `clear_run_memo`.
_RUN_MEMO: dict[str, _RunMemo] = {}


def clear_run_memo() -> None:
    """Forget every run memo. For tests, and for any caller that must see
    the engine's current state immediately."""
    _RUN_MEMO.clear()


def _memo_key(endpoint: str) -> str:
    """The run memo's key: the endpoint, minus a trailing slash.

    Deliberately NOT `models.registry.discovery.norm_endpoint` --
    `models/contracts/` imports nothing from `models/registry/`, and it
    does not need to here: every
    writer and reader gets this string from the same `ModelConnection
    .endpoint` (`tools/vision/jobs.py` hands `resolved.endpoint` to the
    generator; `models/queue/worker.py` hands `ref["endpoint"]` to the
    seams), so they already agree. A spelling that somehow disagrees yields
    `None` -- a missed measurement, never a wrong one.
    """
    return endpoint.rstrip("/")


def _run_memo(endpoint: str) -> _RunMemo | None:
    """The live run memo for `endpoint`, or `None` if there is none or it
    has aged out (an expired entry is dropped on the way past)."""
    key = _memo_key(endpoint)
    memo = _RUN_MEMO.get(key)
    if memo is None:
        return None
    if time.monotonic() - memo.at >= _RUN_MEMO_TTL:
        _RUN_MEMO.pop(key, None)
        return None
    return memo


def _remember_run(endpoint: str, model_id: str, free_at_submit: int) -> None:
    """Record the baseline `loaded_footprint` will measure against.

    One entry per endpoint -- a new run at an endpoint replaces whatever
    was there, because ComfyUI loads the new graph's checkpoint and the
    old reading no longer describes anything. Re-inserting (rather than
    mutating) also keeps insertion order meaningful for the bound below.

    Before replacing it, this reads whatever memo is still live for
    `endpoint` (`_run_memo`, which already applies the TTL) and compares
    its `model_key` to this run's own. A match means `model_id` was
    already the believed-resident model when THIS run started -- so the
    new memo is stamped `resident_at_submit=True`, telling
    `loaded_footprint` that the baseline it is about to measure against is
    not a clean "nothing loaded" reading (see `_RunMemo.resident_at_submit`
    and `loaded_footprint`'s docstring).
    """
    key = _memo_key(endpoint)
    new_model_key = norm_tag(model_id)
    previous = _run_memo(endpoint)
    resident_at_submit = previous is not None and previous.model_key == new_model_key
    _RUN_MEMO.pop(key, None)
    _RUN_MEMO[key] = _RunMemo(
        model_key=new_model_key,
        free_at_submit=free_at_submit,
        at=time.monotonic(),
        resident_at_submit=resident_at_submit,
    )
    while len(_RUN_MEMO) > _RUN_MEMO_MAX:
        _RUN_MEMO.pop(next(iter(_RUN_MEMO)))


def _free_bytes(endpoint: str, timeout: float | None = None) -> int | None:
    """Bytes of memory ComfyUI reports free at `endpoint` right now, or
    `None` if it cannot be read or the body does not carry one.

    The device's own `vram_free` is preferred when there is one, falling
    back to the host's `ram_free`: on a discrete-GPU host those are
    genuinely different pools and the device's is the one a model lives
    in. On MPS they are the same number by construction -- ComfyUI's
    `get_free_memory` returns `psutil.virtual_memory().available` for both
    the `mps` and `cpu` devices -- so unified memory needs no special case
    here.

    Every failure mode degrades to `None`: an unreachable engine, a
    non-2xx, a 200 whose body is not JSON, and a body whose shape is not
    the one documented. This function feeds a measurement, and a
    measurement never fails its caller.

    `timeout` defaults to `DISCOVERY_TIMEOUT`, same as always -- but
    `unload`'s settle poll passes a SHRINKING one (`min(DISCOVERY_TIMEOUT,
    whatever of its own deadline remains)`) so a single slow read near the
    end of that budget cannot itself carry the call past `UNLOAD_TIMEOUT`.
    """
    try:
        response = httpx.get(
            f"{endpoint}/system_stats",
            timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
        )
        response.raise_for_status()
        body = response.json() or {}
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(body, dict):
        return None

    devices = body.get("devices")
    if isinstance(devices, list) and devices and isinstance(devices[0], dict):
        free = devices[0].get("vram_free")
        if isinstance(free, int) and free > 0:
            return free

    system = body.get("system")
    free = system.get("ram_free") if isinstance(system, dict) else None
    return free if isinstance(free, int) and free > 0 else None


def _queue_running(endpoint: str, timeout: float | None = None) -> bool:
    """`True` when ComfyUI's own `/queue` reports something currently
    executing at `endpoint` right now -- `unload`'s settle poll uses this
    as a confirmation signal, so a `/free` that raced a still-running
    generation is never mistaken for a settled eviction.

    An unreachable or malformed `/queue` degrades to `False` -- "cannot
    confirm anything is running" -- the same permissive-on-failure shape
    `_free_bytes` uses for its own reads. This is deliberately a secondary
    signal: a `/queue` hiccup must never by itself block `unload` from
    reporting the free-memory rise `/system_stats` already confirmed.

    `timeout` defaults to `DISCOVERY_TIMEOUT`; see `_free_bytes`'s own
    docstring for why `unload` passes a remaining-budget-shrinking one.
    """
    try:
        response = httpx.get(
            f"{endpoint}/queue",
            timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
        )
        response.raise_for_status()
        body = response.json() or {}
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(body, dict):
        return False
    return bool(body.get("queue_running"))


def _object_info(endpoint: str, node: str, timeout: float | None = None) -> dict:
    """`/object_info/<node>`, memoized for `_OBJECT_INFO_TTL` seconds.

    A FAILURE is never cached: caching an outage would keep a recovered
    engine looking down for the rest of the TTL, and the honest report is
    the one the next call makes.
    """
    key = (endpoint, node)
    now = time.monotonic()
    cached = _OBJECT_INFO_CACHE.get(key)
    if cached is not None and now - cached[0] < _OBJECT_INFO_TTL:
        return cached[1]
    response = httpx.get(
        f"{endpoint}/object_info/{node}",
        timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
    )
    response.raise_for_status()
    body = response.json() or {}
    _OBJECT_INFO_CACHE[key] = (now, body)
    return body


def _combo_values(endpoint: str, node: str, input_key: str, timeout: float | None = None) -> tuple[str, ...]:
    """The allowed values of one node input, from `/object_info/<node>`.

    ComfyUI serves two combo shapes at once, and a live 0.33.0 server
    answers with each of them depending on the node:

    - legacy -- `[[...values...], {...options...}]`, still what
      `CheckpointLoaderSimple.ckpt_name` and `LoraLoader.lora_name` return;
    - newer  -- `["COMBO", {"multiselect": false, "options": [...]}]`, what
      `UpscaleModelLoader.model_name` returns.

    Reading only the first would report NOTHING for an upscaler the
    operator really installed -- a form that can never be filled, with no
    error to explain it. Anything that is neither (a typed input like
    `["INT", {...}]`, a node this build doesn't have) yields `()` rather
    than raising: an absent optional node is not an error, an unreachable
    server is (and propagates).
    """
    spec = (
        _object_info(endpoint, node, timeout)
        .get(node, {})
        .get("input", {})
        .get("required", {})
        .get(input_key)
    )
    if not spec or not isinstance(spec, list):
        return ()
    values = spec[0]
    if values == "COMBO":
        options = spec[1] if len(spec) > 1 else None
        values = options.get("options") if isinstance(options, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values)


class ComfyUIEngine:
    """`InferenceEngine` adapter for ComfyUI."""

    name = "comfyui"

    # The console's "Supported APIs" disclosure prints this verbatim.
    api_description = "the ComfyUI HTTP API"

    # Where an operator browses the engine itself. Printed as plain text,
    # never fetched -- this platform makes no outbound calls on an
    # operator's behalf. ComfyUI has no model library of its own (checkpoints
    # come from wherever the operator chooses), so this points at the engine.
    library_url = "https://github.com/comfyanonymous/ComfyUI"

    # The SHAPE of getting a model here: a file copy, not a download command.
    # The platform never fetches a checkpoint (ADR 0010 §5). A self-contained
    # checkpoint is one file; a multi-file model family (a `.gguf`/`.safetensors`
    # diffusion-model file the UNet/GGUF loaders report, per
    # `InstalledModel.loader`) is a copy into THREE directories -- verified on
    # disk against the reference machine's live ComfyUI 0.33.0 install (the
    # plan's own "Installed files" listing): `models/unet/` for the diffusion
    # model, `models/text_encoders/` for its text encoder(s), `models/vae/`
    # for its VAE. Registration then asks which of those installed files play
    # which role (models/registry/README.md's "Registering a multi-file
    # model family") -- this template names where to put them, not which ones
    # belong together.
    install_cmd_template = (
        "copy <model>.safetensors into ComfyUI/models/checkpoints/ for a "
        "self-contained checkpoint; a multi-file model (GGUF/UNet) instead "
        "goes into ComfyUI/models/unet/, its text encoder(s) into "
        "ComfyUI/models/text_encoders/, and its VAE into ComfyUI/models/vae/"
    )

    # ComfyUI's default listen port -- the only address the console's server
    # scan knows about this engine, declared here, never in the scan loop.
    well_known_ports = (8188,)

    # Which platform capability this engine can answer at all -- the setup
    # page's "Served by" column reads this; nothing else branches on it.
    serves_capabilities = ("image-generation",)

    # How an operator installs and reaches this engine. Declared HERE
    # because only this adapter knows it; the /setup/ page renders it
    # verbatim and hardcodes nothing about ComfyUI.
    setup_guide = SetupGuide(
        summary=(
            "ComfyUI runs natively on the host so it can use the GPU (Metal on macOS, "
            "CUDA or ROCm on Linux, CUDA on Windows). farabunker talks to it over HTTP "
            "and never downloads a model for you."
        ),
        platforms={
            "macos": (
                SetupStep(
                    "Clone ComfyUI",
                    "Anywhere outside the farabunker repo.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
                SetupStep(
                    "Create a virtual environment",
                    "Keeps ComfyUI's dependencies off your system Python.",
                    "python3 -m venv venv && source venv/bin/activate",
                ),
                SetupStep(
                    "Install PyTorch",
                    "The default wheels carry Apple Silicon (Metal) support.",
                    "pip install torch torchvision torchaudio",
                ),
                SetupStep(
                    "Install ComfyUI's requirements",
                    "Built-in nodes only — farabunker needs no custom nodes and no Manager.",
                    "pip install -r requirements.txt",
                ),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces, or the farabunker container cannot reach it.",
                    "python main.py --listen 0.0.0.0 --port 8188",
                ),
            ),
            "windows": (
                SetupStep(
                    "Download the portable build",
                    "Unzip the release archive anywhere; it carries its own Python and PyTorch.",
                ),
                SetupStep(
                    "Add the listen flag",
                    "Edit run_nvidia_gpu.bat (or run_cpu.bat) and append --listen 0.0.0.0 "
                    "to the python line it runs.",
                ),
                SetupStep("Start it", "Double-click the .bat file you just edited."),
                SetupStep(
                    "Or install from source instead",
                    "Same route as macOS/Linux if you prefer a checkout you control.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
            ),
            "linux": (
                SetupStep(
                    "Clone ComfyUI",
                    "Anywhere outside the farabunker repo.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
                SetupStep(
                    "Create a virtual environment",
                    "",
                    "python3 -m venv venv && source venv/bin/activate",
                ),
                SetupStep(
                    "Install PyTorch for your accelerator",
                    "Pick the CUDA or ROCm wheel index matching your GPU driver.",
                    "pip install torch torchvision torchaudio "
                    "--index-url https://download.pytorch.org/whl/cu124",
                ),
                SetupStep("Install ComfyUI's requirements", "", "pip install -r requirements.txt"),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces, or the farabunker container cannot reach it.",
                    "python main.py --listen 0.0.0.0 --port 8188",
                ),
            ),
        },
        network_note=(
            "Start ComfyUI with --listen 0.0.0.0. It binds to localhost by default, and the "
            "farabunker web container reaches your machine as host.docker.internal — a "
            "localhost-only server is invisible to it."
        ),
        models_note=(
            "Place model files yourself: checkpoints (.safetensors) in "
            "ComfyUI/models/checkpoints/, LoRAs in models/loras/, VAEs in models/vae/, "
            "upscale models in models/upscale_models/. A multi-file model family "
            "(GGUF or a bare UNet, e.g. for instruction-based editing) instead goes in "
            "models/unet/, with its text encoder(s) in models/text_encoders/ and its "
            "VAE alongside the others in models/vae/ — register it from the manual "
            "form and declare which files play which role. "
            "farabunker never downloads a model — community checkpoints come from Civitai "
            "or Hugging Face, and which one to run is your decision, not the platform's."
        ),
        verify_url_path="/system_stats",
    )

    def is_healthy(self, endpoint: str, timeout: float | None = None) -> bool:
        """True if `endpoint` answers `/system_stats` with 200."""
        try:
            response = httpx.get(
                f"{endpoint}/system_stats",
                timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Every model ComfyUI can load at `endpoint`: checkpoints AND the
        diffusion-model files a multi-file family is built from.

        `model_id` is ComfyUI's exact string and is OPAQUE -- a Windows host
        reports `subdir\\file.safetensors`, which must go back unchanged as
        the loader's own name input. ComfyUI reports no size for a model on
        disk, so `size` stays `None` rather than being guessed.

        `loaded`/`loaded_size` are the RUN MEMO's belief, unchanged from
        what this module's own memory-seams established here: the memo is read once for
        the whole listing, and a row is resident exactly when its
        `norm_tag`-normalized id is the model the memo remembers this
        endpoint last running. Widening the listing must not weaken that --
        a warm model this function stopped reporting as loaded is a warm
        model the scheduler can no longer evict.

        A file two loaders both report is ONE model: the first loader in
        `_MODEL_NODES` to report it owns the row, so the console never
        renders the same file twice. That surviving row carries the memo's
        residency for its own id, so a second loader's sighting can never
        overwrite a `True` with a default `False`.
        """
        memo = _run_memo(endpoint)
        installed: list[InstalledModel] = []
        seen: set[str] = set()
        for node, input_key in _MODEL_NODES:
            for name in _combo_values(endpoint, node, input_key):
                if name in seen:
                    continue
                seen.add(name)
                resident = memo is not None and memo.model_key == norm_tag(name)
                installed.append(
                    InstalledModel(
                        model_id=name,
                        capabilities=("image-generation",),
                        loaded=resident,
                        loaded_size=memo.footprint if resident else None,
                        loader=node,
                    )
                )
        return installed

    def list_assets(self, endpoint: str, kind: str) -> list[Asset]:
        """Assets of `kind` installed at `endpoint`; `[]` for a kind ComfyUI
        has no loader node for (e.g. "embedding").

        A kind with several reporting nodes is the union of what they
        report, in first-seen order, de-duplicated -- see `_ASSET_NODES`.
        """
        nodes = _ASSET_NODES.get(kind)
        if not nodes:
            return []
        assets: list[Asset] = []
        seen: set[str] = set()
        for node, input_key in nodes:
            for name in _combo_values(endpoint, node, input_key):
                if name in seen:
                    continue
                seen.add(name)
                assets.append(Asset(kind=kind, asset_id=name))
        return assets

    def list_families(self, endpoint: str) -> tuple[str, ...]:
        """Model families an operator may declare for a connection at
        `endpoint` (ADR 0012 D-EDIT-2).

        Two authorities, intersected, and no third:

        - the ENGINE's own vocabulary (`CLIPLoader.type`), because that
          string is what a CLIP loader is literally given, and a build that
          does not know it would reject the graph;
        - the families this adapter actually has a graph template for
          (`comfyui_workflows.families()`), because offering one we cannot
          run would let an operator declare a model into silence.

        This platform ships neither list. Order follows the engine's.
        """
        node, input_key = _FAMILY_NODE
        runnable = set(families())
        return tuple(
            value for value in _combo_values(endpoint, node, input_key) if value in runnable
        )

    def list_variants(self) -> tuple[str, ...]:
        """Variants an operator may declare for a connection (ADR 0012
        D-EDIT-6): a second graph for the SAME family, for weights this
        adapter cannot tell apart over HTTP.

        Unlike `list_families`, nothing is intersected with an engine
        vocabulary -- ComfyUI has no word for this. The template package's
        own list IS the honest answer, and it is ONE list rather than one
        per family: the form that renders it has no family chosen yet.
        """
        return variants()

    def param_defaults(self, operation_key: str, config: dict | None = None) -> dict:
        """Where a form for `operation_key` should START for a connection
        registered with `config` -- `{}` when this adapter has no opinion.

        The twin of `list_choices`/`list_assets`: those report options the
        SCHEMA cannot know, this reports defaults the schema cannot know,
        because a step count is a fact of a graph. It never narrows, adds,
        or removes a param -- the schema alone declares those (ADR 0012
        D-EDIT-7).
        """
        cfg = dict(config or {})
        return variant_defaults(
            operation_key, str(cfg.get("family") or ""), str(cfg.get("variant") or "")
        )

    def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
        """Params `operation_key`'s schema declares that this connection's
        graph cannot honour, with the operator-facing reason each is
        skipped -- read straight from the template that declares them
        (`comfyui_workflows.ignored_params`, ADR 0012 D-EDIT-12), so
        naming one is a template edit and nothing else.

        Keyed on the FAMILY alone, not the variant: an `IGNORES` entry is
        a fact about the wiring both of a family's graphs share (neither
        `flux2` graph has a negative-prompt input), never about which build
        of it an operator declared.
        """
        cfg = dict(config or {})
        return workflow_ignores(operation_key, str(cfg.get("family") or ""))

    def list_choices(self, endpoint: str, param_key: str) -> tuple[str, ...]:
        """Live options for a `"choice"` param (sampler, scheduler); `()`
        for a key this engine doesn't report."""
        mapping = _CHOICE_INPUTS.get(param_key)
        if mapping is None:
            return ()
        node, input_key = mapping
        return _combo_values(endpoint, node, input_key)

    def supported_operations(
        self, model_id: str, endpoint: str, family: str = ""
    ) -> tuple[str, ...]:
        """Operations this adapter has a graph template for, for a
        connection in `family` -- read straight from the template registry,
        so adding a mode is one template and nothing else (spec §9).

        `family` is the connection's declared model family
        (`ModelConnection.config["family"]`, ADR 0012 D-EDIT-2); the empty
        default is "no family declared", i.e. a single-file checkpoint,
        which is every connection that existed before families did.
        """
        return template_keys(family)

    def build_image_generator(self, model_id: str, endpoint: str, **cfg) -> "ComfyUIGenerator":
        """An `ImageGenerator` bound to `model_id` at `endpoint`.

        `cfg` is the bound connection's `config` (D8) and is handed to the
        graph template unchanged -- this adapter never interprets it.
        """
        return ComfyUIGenerator(endpoint=endpoint, model_id=model_id, config=cfg)

    def loaded_footprint(self, endpoint: str, model_id: str) -> int | None:
        """Bytes `model_id` occupies at `endpoint` right now -- measured as
        the memory that DISAPPEARED across this adapter's own run of it,
        `None` when there is nothing honest to report.

        ComfyUI has no per-model residency endpoint: `/system_stats`
        reports the machine's memory and nothing else, and on MPS every
        free number in it is the same `psutil.virtual_memory().available`
        (ComfyUI 0.33.0, `comfy/model_management.get_free_memory`). So a
        "total resident now" reading would be a reading of the whole
        machine -- browser, containers, everything -- not of a checkpoint.
        `submit` therefore records what was free just before it queued the
        graph, and this subtracts what is free now.

        What that number honestly is: an OVER-count of the checkpoint,
        deliberately. It covers everything the run allocated and has not
        released -- weights, VAE, text encoder, whatever caches held on to
        -- which is the right answer for admission, whose question is
        "what does running this model cost me", not "how large is this one
        tensor collection". It is also MPS-honest by construction: it
        measures bytes that actually went missing, so an fp8 tensor
        upcast to bf16 counts at its upcast size, never at its file size.

        A model already resident at submit is a fourth failure mode, and
        one that can slip past both guards below: the baseline `submit`
        recorded that time was not "nothing loaded" but "this model
        already loaded", so any delta measured against it can only ever be
        the run's incremental growth -- weights already paid for, more
        activations, whatever caches grew further -- never the checkpoint's
        real footprint. That growth is not reliably small: the
        distilled-checkpoint incident (2026-08-25, job 24) measured
        2,537,799,680 bytes of growth alone, comfortably above
        `_MIN_CREDIBLE_FOOTPRINT`, against an actual ~19.5 GB resident
        checkpoint, and the worker recorded it as that checkpoint's own
        footprint -- which then let the memory plan admit a
        second large model alongside it. So this checks
        `memo.resident_at_submit` (stamped by `_remember_run` when the memo
        it replaced already named the same model) BEFORE computing any
        delta at all, and returns `None` unconditionally when it is `True`
        -- an honest "unknown", never a growth-only number.

        A file-size floor -- refusing a delta smaller than the checkpoint's
        size on disk -- is NOT implemented here: `list_installed` above
        deliberately reports `size=None` for every ComfyUI model, because
        ComfyUI's `/object_info` never reports a file's size on disk and
        this adapter does not guess one. There is nothing cheap to compare
        the delta against. An engine adapter whose `list_installed` does
        carry a real size should treat a delta below it as not credible the
        same way this one treats a delta below `_MIN_CREDIBLE_FOOTPRINT`.

        What it is not: isolated from the rest of the machine. Another
        process growing during the run inflates it (harmless -- the job
        stays exclusive longer than it needed to); another process
        shrinking deflates it, which is the direction that could
        under-admit into an OOM. Guarded three ways -- already resident at
        submit (above), a non-positive delta, and anything below
        `_MIN_CREDIBLE_FOOTPRINT` -- all reported as `None` rather than as
        a small or growth-only number -- and, beyond that, documented
        rather than hidden: `ModelConnection.footprint_override_bytes`
        (the operator's "Memory footprint override" field on
        `/inference/`) outranks whatever this measures.

        It is a post-run snapshot, not a peak -- which is exactly what
        this seam is for; peak monitoring belongs to the queue.

        Never raises: an unreachable engine, a malformed body, a model
        this endpoint did not run, an endpoint this adapter never
        submitted to -- all `None`, the same opportunistic-fact shape
        `InstalledModel.loaded_size` and `ollama.loaded_footprint` have.
        The scheduler already treats `None` as "unknown footprint, job
        runs alone" regardless of why.
        """
        memo = _run_memo(endpoint)
        if memo is None or memo.model_key != norm_tag(model_id):
            return None
        if memo.resident_at_submit:
            return None
        free_now = _free_bytes(endpoint)
        if free_now is None:
            return None
        delta = memo.free_at_submit - free_now
        if delta < _MIN_CREDIBLE_FOOTPRINT:
            return None
        # `_free_bytes` above is a network round trip; a concurrent
        # `submit()` at this same endpoint can have already re-stamped
        # `_RUN_MEMO` (a newer run's `_remember_run`) while it was in
        # flight. `memo` is still the OLD baseline in that case, so `delta`
        # was measured against a run this endpoint has already moved past.
        # Writing it back would clobber the newer memo -- attaching a stale
        # delta to whatever the newer run's own measurement has not filled
        # in yet. Skip the shared-cache write when that has happened;
        # `delta` is still an honest answer for the run THIS call was asked
        # about, so it is still returned either way.
        if _run_memo(endpoint) is memo:
            _RUN_MEMO[_memo_key(endpoint)] = replace(memo, footprint=delta)
        return delta

    def unload(self, endpoint: str, model_id: str) -> bool:  # noqa: ARG002 - see docstring
        """Ask ComfyUI to release its loaded models at `endpoint`, and do
        not report success until that release has actually happened.

        `model_id` is UNUSED, and that is the honest shape of ComfyUI's
        API rather than an oversight: `POST /free` with `unload_models`
        set calls `comfy.model_management.unload_all_models()`, which frees
        EVERY model on EVERY device at that endpoint. There is no
        per-model free to call. A caller assuming this evicts one
        checkpoint and leaves another warm would be wrong -- so the
        parameter stays (the seam's signature is fixed) and this docstring
        says what actually happens. In practice this is what the caller
        wants anyway: `models/queue/worker.py` only calls this for models
        the admission plan does NOT need, and a needed checkpoint that
        gets caught in the sweep is reloaded on its next run -- a cost,
        never a corruption.

        `free_memory` is sent alongside `unload_models` because they free
        different things: `unload_models` drops model weights,
        `free_memory` also resets the execution cache holding intermediate
        tensors from the last run (ComfyUI 0.33.0, `main.py`'s
        `prompt_worker` flag handling). Eviction wants both.

        THIS IS A BARRIER, not a fire-and-forget POST -- a 2xx is no
        longer treated as the confirmation. `/free` only sets a flag on
        ComfyUI's prompt queue and returns 200 immediately (`server.py`);
        the actual `unload_all_models()` runs afterwards on the prompt-
        worker thread, woken by that flag, with no ordering guarantee
        between "this POST returned" and "the worker thread got around to
        consuming the flag". A caller that treated the 2xx itself as
        confirmation could launch its next job's `/prompt` before that
        happened, loading the new graph's checkpoint ON TOP OF the one
        this call was meant to evict rather than after it -- exactly what
        happened live (2026-08-25, job 28: the second image family's
        ordinary build loaded on top of an already-resident distilled
        checkpoint, free memory falling from 23.8 to 5.9 GiB instead of
        rising, because the worker's next `/prompt`
        landed before ComfyUI's prompt-worker had consumed the flag).

        So after a 2xx, this polls `/system_stats` (and, once that already
        looks sufficient, `/queue` -- to confirm nothing is still running
        that could mean the free has not truly settled) every
        `UNLOAD_POLL_INTERVAL` seconds until the free-memory reading taken
        right before the POST has risen by at least the settle THRESHOLD
        (below), or the deadline started at the very top of this call
        elapses -- see `UNLOAD_TIMEOUT`'s own comment for why that one
        deadline covers the baseline read, the POST, AND the poll
        TOGETHER (one shared 30s budget, not 30s stacked on 30s): each
        read inside the loop is itself given only
        `min(DISCOVERY_TIMEOUT, whatever of the deadline remains)`, and the
        loop checks that deadline BEFORE issuing a read on every pass, so
        neither a slow read nor a long sleep can carry the whole call past
        `UNLOAD_TIMEOUT`. A prompt still `queue_running` never earns extra
        time beyond that deadline; it only withholds `True` until the
        queue looks idle too.

        THE SETTLE THRESHOLD IS A MAJORITY, NOT THE WHOLE FOOTPRINT --
        `max(_MIN_CREDIBLE_FOOTPRINT, <this endpoint's known footprint
        from its run memo, if any> // 2)`. Demanding the full footprint
        back would make every correct eviction stall for the full
        `UNLOAD_TIMEOUT` and then falsely report `False`: `/free` has been
        observed on this host to release only the models ComfyUI tracks as
        resident on the `mps` device, and a component it chose to keep on
        `cpu` instead -- a text encoder, in practice -- is not necessarily
        among them (`comfy/model_management.py`'s `unload_all_models` and
        its `mps`-scoped bookkeeping). A checkpoint whose weights sit on
        `mps` but whose text encoder sits on `cpu` therefore never gives
        back its FULL recorded footprint even on a perfectly successful
        free. Half is still far above the noise `_MIN_CREDIBLE_FOOTPRINT`
        guards against, and still a real, checkpoint-sized rise -- not a
        number chosen to make this pass, but the best available split
        between "trust nothing rose" and "demand a total this host has
        never actually returned."

        THE SETTLE POLL IS SKIPPED, NOT RUN TO TIMEOUT, WHEN THERE IS NO
        BASELINE TO MEASURE AGAINST -- i.e. `free_before` itself came back
        `None` (`_free_bytes` could not read `/system_stats` at the moment
        this call started). A delta against no baseline can never be
        computed, at any point before the deadline, so polling for one
        would only burn the full budget and return `False` for a free that
        may well have genuinely happened. This degrades to the PRE-BARRIER
        contract instead: the POST's own 2xx is trusted, gated on ONE
        immediate `GET /queue` check (not a poll) so a `/free` that raced
        an actively-running prompt still is not mistaken for a settled
        eviction.

        Returns `True` only once the effect was actually observed (or, on
        the no-baseline path above, once the POST was accepted and nothing
        looked still running). `False` covers every other outcome: the
        POST itself refused or unreachable (returned immediately, without
        ever entering the settle poll), and the settle poll timing out
        without ever seeing the rise -- an unreadable `/system_stats`, a
        server that never actually freed anything, or one still busy when
        the clock ran out. The worker logs every `False` as "eviction
        unload refused"; read that log line as "memory might still be
        held", not as a guarantee -- see the false-POSITIVE note below for
        the direction `True` can be wrong in.

        WHAT CAN STILL MAKE THIS RETURN A WRONG `True`: the bare
        `_MIN_CREDIBLE_FOOTPRINT` floor (used whenever this endpoint has no
        recorded footprint yet) is memory-machine-wide, not
        ComfyUI-specific -- `psutil.virtual_memory().available` drifts by
        hundreds of MB on its own from an unrelated process (a browser tab,
        a container) finishing work at the same moment `/free` was called.
        A drift that happens to clear 256 MiB during exactly this call's
        poll window reads as a settled eviction and drops the run memo,
        even though ComfyUI itself may not have freed anything. This is
        the same class of imprecision `loaded_footprint` already lives
        with and documents (this adapter is not isolated from the rest of
        the machine); it is named here rather than hidden because `unload`
        acting on it has a real consequence -- the residency belief -- that
        `loaded_footprint`'s own read does not.

        Uses `UNLOAD_TIMEOUT` for the baseline read's ceiling and the
        POST's own per-request timeout, and the SAME constant as the
        settle poll's hard cap, all counted from this call's own start:
        the worker charges this whole call against its heartbeat margin.
        Any `httpx.HTTPError` from the POST -- refused connection, non-2xx,
        or a response slower than the timeout -- returns `False`
        immediately, never an exception.
        """
        deadline = time.monotonic() + UNLOAD_TIMEOUT

        def _remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        free_before = _free_bytes(endpoint, timeout=min(DISCOVERY_TIMEOUT, _remaining()))
        memo = _run_memo(endpoint)
        threshold = _MIN_CREDIBLE_FOOTPRINT
        if memo is not None and memo.footprint:
            threshold = max(_MIN_CREDIBLE_FOOTPRINT, memo.footprint // 2)

        try:
            response = httpx.post(
                f"{endpoint}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=UNLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False

        if free_before is None:
            # No baseline -- see the docstring's "SETTLE POLL IS SKIPPED"
            # paragraph. A single confirmation read, not a loop: there is
            # nothing further a poll could learn without something to
            # measure a rise against.
            settled = not _queue_running(endpoint, timeout=min(DISCOVERY_TIMEOUT, _remaining()))
        else:
            settled = False
            while _remaining() > 0:
                free_now = _free_bytes(endpoint, timeout=min(DISCOVERY_TIMEOUT, _remaining()))
                if free_now is not None and free_now - free_before >= threshold:
                    if _remaining() <= 0:
                        break
                    if not _queue_running(endpoint, timeout=min(DISCOVERY_TIMEOUT, _remaining())):
                        settled = True
                        break
                sleep_for = min(UNLOAD_POLL_INTERVAL, _remaining())
                if sleep_for <= 0:
                    break
                time.sleep(sleep_for)

        if not settled:
            return False
        # Everything at this endpoint is gone, so the run memo -- this
        # adapter's only residency evidence -- is gone with it. Dropped
        # ONLY once the free was actually observed: believing a model went
        # away when it did not is the one error here that could leave two
        # large checkpoints resident at once.
        _RUN_MEMO.pop(_memo_key(endpoint), None)
        return True

    def build_llm(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("comfyui serves image generation only")

    def build_embedder(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("comfyui serves image generation only")


def _media_type(filename: str) -> str:
    """Media type from the filename extension; a generic binary type when
    unrecognised. ComfyUI names its outputs, so the extension is the only
    honest signal available without sniffing bytes."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _rejection_message(payload: object) -> str:
    """Flatten ComfyUI's `/prompt` rejection body into one operator-readable
    line: its own `error.message` plus every `node_errors` detail. ComfyUI's
    words, never a rewritten guess."""
    if not isinstance(payload, dict):
        return "ComfyUI rejected the request."
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict):
        parts.append(str(error.get("message") or error.get("type") or "").strip())
    for node_id, node_error in (payload.get("node_errors") or {}).items():
        for entry in (node_error or {}).get("errors", []):
            detail = str(entry.get("details") or entry.get("message") or "").strip()
            if detail:
                parts.append(f"node {node_id}: {detail}")
    message = " — ".join(part for part in parts if part)
    return message or "ComfyUI rejected the request."


def _upload_rejection(response) -> str:
    """ComfyUI's own words for a refused upload.

    `/upload/image` answers a rejection with a plain-text body far more
    often than the JSON `/prompt` uses, so this reads `.text` and falls back
    to the bare status code -- never a rewritten guess."""
    text = (getattr(response, "text", "") or "").strip()
    return text or f"HTTP {response.status_code}"


def _has_output_images(outputs: object) -> bool:
    """True only when at least one node in a history entry's `outputs`
    actually produced an image -- a node key present with an empty
    `images` list (a node ran but saved nothing) is not enough, the same
    way an entirely empty/absent `outputs` dict is not enough."""
    if not isinstance(outputs, dict):
        return False
    return any((node_output or {}).get("images") for node_output in outputs.values())


def _execution_error(status_block: dict) -> str:
    """The first `execution_error` message ComfyUI recorded for a job.

    History `status.messages` is a list of `[event_name, payload]` pairs;
    the failure detail lives in the `execution_error` payload's
    `exception_message`.
    """
    for message in status_block.get("messages") or []:
        if isinstance(message, (list, tuple)) and len(message) == 2 and message[0] == "execution_error":
            payload = message[1] or {}
            detail = payload.get("exception_message") or payload.get("exception_type")
            if detail:
                return str(detail)
    return ""


def _queue_ref(entry: object) -> str | None:
    """The prompt id inside one `/queue` entry.

    ComfyUI's queue entries are positional lists,
    `[number, prompt_id, prompt, extra_data, outputs_to_execute]`; anything
    that doesn't fit that shape is skipped rather than crashing a poll."""
    if isinstance(entry, (list, tuple)) and len(entry) > 1:
        return str(entry[1])
    return None


def _pending_position(pending: object, engine_ref: str) -> int | None:
    """1-based position of `engine_ref` among ComfyUI's PENDING entries, in
    the order ComfyUI will actually pop them.

    `/queue` hands back a dump of ComfyUI's own `heapq` list, which is heap
    order, not execution order -- index 0 is next, the rest are not sorted.
    Every entry's FIRST element is the monotonic `number` the heap is keyed
    on (`[number, prompt_id, prompt, extra_data, outputs_to_execute]`), so
    sorting by it reproduces pop order exactly. An entry that does not fit
    that shape is skipped, and one whose `number` is not a number sorts
    last, rather than crashing a poll -- the same tolerance `_queue_ref`
    applies, for the same reason: a position is a display nicety and must
    never fail a live job.
    """
    ordered: list[tuple[float, str]] = []
    for entry in pending or []:
        ref = _queue_ref(entry)
        if ref is None:
            continue
        number = entry[0]
        ordered.append((float(number) if isinstance(number, (int, float)) else float("inf"), ref))
    ordered.sort(key=lambda item: item[0])
    for index, (_number, ref) in enumerate(ordered, start=1):
        if ref == engine_ref:
            return index
    return None


def _output_order(item) -> tuple:
    """Sort key for one `outputs` entry, ordering node ids NUMERICALLY.

    ComfyUI's node ids are strings, so a plain sort puts `"10"` before
    `"2"` -- invisible while a graph has one `SaveImage` node, wrong the
    moment one has two (a hi-res pass, an upscale-then-save). A node id
    that is not a number at all (a third-party node pack) sorts after the
    numeric ones by its own text rather than raising: output ORDER is a
    display nicety and must never fail a finished job.
    """
    node_id = item[0]
    text = str(node_id)
    return (0, int(text), "") if text.isdecimal() else (1, 0, text)


class ComfyUIGenerator:
    """`ImageGenerator` for one checkpoint at one ComfyUI endpoint.

    Stateless between calls: it holds only the endpoint, model id, and the
    connection config it was built with. No retries -- the service layer
    decides what a transient failure means (spec §6: an unreachable engine
    during a poll leaves the job untouched).
    """

    def __init__(
        self,
        endpoint: str,
        model_id: str,
        config: dict | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_id = model_id
        self.config = dict(config or {})
        self.timeout = timeout

    def _upload_inputs(self, request: GenerationRequest) -> dict[str, str]:
        """Transfer every file input to ComfyUI; return `{param key: engine
        reference}`.

        ComfyUI answers `POST /upload/image` with
        `{"name": ..., "subfolder": ..., "type": "input"}`, and a node's
        image input takes `"<subfolder>/<name>"` (or just `"<name>"` when
        there is no subfolder), so that string IS the reference.

        Each job uploads into a subfolder named after its own `client_ref`,
        with `overwrite=true`: two jobs sending files of the same name can
        never collide, while resubmitting the SAME job is idempotent.

        A refusal is a `GenerationRejected` carrying ComfyUI's own words; a
        transport failure propagates too, but since this runs inside
        `submit`, the service layer's generic exception handler fails the
        job immediately rather than treating it as transient -- that
        forgiveness is reserved for `refresh_job`'s poll of an
        already-submitted job (see `ComfyUIGenerator`'s docstring above).
        """
        references: dict[str, str] = {}
        for param_key, path in (request.inputs or {}).items():
            source = Path(path)
            with open(source, "rb") as handle:
                response = httpx.post(
                    f"{self.endpoint}{_UPLOAD_PATH}",
                    files={_UPLOAD_FIELD: (source.name, handle, _media_type(source.name))},
                    data={"subfolder": request.client_ref, "type": "input", "overwrite": "true"},
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                raise GenerationRejected(
                    f"ComfyUI refused the upload for {param_key!r}: {_upload_rejection(response)}"
                )
            body = response.json() or {}
            name = str(body.get("name") or source.name)
            subfolder = str(body.get("subfolder") or "")
            references[param_key] = f"{subfolder}/{name}" if subfolder else name
        return references

    def submit(self, request: GenerationRequest) -> tuple[str, dict]:
        """Build the graph for `request.operation` and queue it.

        Returns `(prompt_id, payload)` where `payload` is the EXACT body
        posted, stored verbatim on the job row (D6). Raises
        `GenerationRejected` when ComfyUI refuses the graph (unknown
        checkpoint, invalid wiring) -- the caller turns that into an
        immediately-failed job carrying ComfyUI's own words.

        Every file input is transferred FIRST (`_upload_inputs`) so the graph
        can reference ComfyUI's own name for it. The stored `payload` stays
        the EXACT `/prompt` body and gains no upload bookkeeping: the
        references are already inside the graph, which is what makes the
        submission reproducible. Immediately before the graph is posted,
        this also reads `/system_stats` and -- once ComfyUI has accepted
        the graph -- stamps the free-memory baseline `loaded_footprint`
        later measures against (T-memory-seams).
        """
        # Imported here, not at module level: `models.contracts.bindings`
        # imports `models.contracts.engines.ollama`, which this package's own
        # `__init__.py` imports ALONGSIDE this module -- a module-level
        # import here risks a circular import depending on which side
        # loads first (`tools.vision.services`, a non-`core` caller,
        # already imports `models.contracts.bindings` at module level with
        # no such risk). `config_family` (final-review finding 9) is the
        # ONE normalization of `config["family"]` -- a JSON `null` reads
        # as `""`, exactly like an absent key -- shared with `modules.
        # vision.services.operations_for_model`.
        from models.contracts.bindings import config_family

        inputs = self._upload_inputs(request)
        graph = get_template(request.operation, config_family(self.config))(
            request, self.model_id, self.config, inputs
        )
        payload = {"prompt": graph, "client_id": request.client_ref}
        # The "before" half of the footprint measurement (T-memory-seams).
        # Taken HERE because this is the last moment that still knows what
        # the machine looked like before this checkpoint was loaded --
        # `loaded_footprint`, called by the worker after the run, has only
        # the "after". One extra `/system_stats` round trip against an
        # engine we are about to hand a minutes-long job, and an
        # unreadable one costs the measurement, never the generation.
        free_before = _free_bytes(self.endpoint)
        response = httpx.post(f"{self.endpoint}/prompt", json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            raise GenerationRejected(_rejection_message(response.json()))
        prompt_id = (response.json() or {}).get("prompt_id")
        if not prompt_id:
            raise GenerationRejected("ComfyUI accepted the request but returned no prompt_id.")
        if free_before is not None:
            _remember_run(self.endpoint, self.model_id, free_before)
        return str(prompt_id), payload

    def status(self, engine_ref: str) -> JobStatus:
        """Map ComfyUI's history + queue onto a platform `JobStatus`.

        History first (it is authoritative for a finished job), then the
        queue (running vs pending). Known to neither means ComfyUI no
        longer has this job at all -- `lost`, which the service layer
        explains to the operator. A history entry reporting success with no
        actual output images -- whether `outputs` is empty or every node's
        `images` list is empty -- is reported as a failure rather than a
        silent `done`: there is nothing to show, and saying "done" would be
        a lie.
        """
        history = self._history(engine_ref)
        if history is None:
            return self._queue_state(engine_ref)

        status_block = history.get("status") or {}
        if status_block.get("status_str") == "error":
            return JobStatus(
                state="failed",
                error=_execution_error(status_block) or "ComfyUI reported an execution error.",
            )
        if _has_output_images(history.get("outputs")):
            return JobStatus(state="done")
        if status_block.get("completed"):
            return JobStatus(
                state="failed", error="ComfyUI finished the job but produced no output images."
            )
        return self._queue_state(engine_ref)

    def fetch_outputs(self, engine_ref: str) -> list[tuple[str, bytes, str]]:
        """Download every saved output image for `engine_ref`.

        Only `type == "output"` images are taken -- `temp` entries are
        ComfyUI's live previews, not results. Node ids are visited in sorted
        order so a multi-output graph always yields a stable sequence.

        S11 (review round 1, finding 3): refuses, via `httpx.HTTPError` --
        the same exception family a byte-budget overrun uses, so a caller
        already catching that needs no changes -- once more than
        `MAX_OUTPUTS_PER_JOB` output images have been named, BEFORE
        downloading the one that would cross it. A malformed or hostile
        history entry naming an implausible number of outputs is refused
        outright rather than turning one call into an unbounded number of
        `/view` round trips.
        """
        history = self._history(engine_ref) or {}
        outputs: list[tuple[str, bytes, str]] = []
        for _node_id, node_output in sorted((history.get("outputs") or {}).items(), key=_output_order):
            for image in (node_output or {}).get("images") or []:
                if image.get("type") != "output":
                    continue
                if len(outputs) >= MAX_OUTPUTS_PER_JOB:
                    raise httpx.HTTPError(
                        f"{self.endpoint}: history entry for {engine_ref!r} names more than "
                        f"{MAX_OUTPUTS_PER_JOB} output images; refusing to fetch the rest"
                    )
                params = {
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
                # S11: bounded read -- a compromised or hostile engine
                # streaming an oversize body for the full `self.timeout`
                # window is refused as unreachable rather than held whole
                # in the worker's memory.
                content = get_bounded(f"{self.endpoint}/view", params=params, timeout=self.timeout)
                filename = params["filename"]
                outputs.append((filename, content, _media_type(filename)))
        return outputs

    def _history(self, engine_ref: str) -> dict | None:
        """This job's `/history/<id>` entry, or None while ComfyUI has none."""
        # S25: `engine_ref` came back from ComfyUI's own `/prompt` response
        # and is stored on `GenerationJob.engine_ref` with no shape check --
        # refuse anything that isn't a bare token before it reaches this
        # path, so a malicious engine cannot retarget the poll (e.g. via
        # `../`, `?`, `#`) at a different path on the same engine. Logged
        # once at warning level (review round 1, finding 7/minor 8): this
        # should never happen against a well-behaved engine, so an operator
        # seeing it in the log is the first sign something upstream is
        # misbehaving, not routine noise worth silencing.
        try:
            safe_ref = safe_engine_ref(engine_ref)
        except ValueError:
            logger.warning(
                "ComfyUI at %s returned a job id that fails the shape check: %r",
                self.endpoint, engine_ref,
            )
            raise
        # S11: bounded read, same as `fetch_outputs` -- a history entry is
        # small JSON, but this path is still an engine response. `entry`'s
        # lookup key below stays the ORIGINAL (unvalidated) `engine_ref`:
        # only the URL path needs the shape-checked value, and ComfyUI's
        # own history dict is always keyed by its own `prompt_id` string.
        body = get_bounded(f"{self.endpoint}/history/{safe_ref}", timeout=DISCOVERY_TIMEOUT)
        entry = (json.loads(body or b"{}") or {}).get(engine_ref)
        return entry or None

    def _queue_state(self, engine_ref: str) -> JobStatus:
        """`running` / `queued` / `lost`, from `/queue`.

        A `queued` answer carries the job's real position among the pending
        entries (`_pending_position`); `running` carries none, because a job
        that is running has a state that says more than any number could.
        """
        response = httpx.get(f"{self.endpoint}/queue", timeout=DISCOVERY_TIMEOUT)
        response.raise_for_status()
        queue = response.json() or {}
        for entry in queue.get("queue_running") or []:
            if _queue_ref(entry) == engine_ref:
                return JobStatus(state="running")
        position = _pending_position(queue.get("queue_pending"), engine_ref)
        if position is not None:
            return JobStatus(state="queued", queue_position=position)
        return JobStatus(state="lost")
