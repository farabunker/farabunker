"""
Engine adapter contract.

Every inference engine (Ollama today; vLLM / llama.cpp / other later)
implements `InferenceEngine`, so the platform can discover, health-check, and
build LLMs/embedders against any of them through one seam. Engines are
stateless: `endpoint` is data passed in per call, never read from Django
settings — that keeps an engine instance reusable across connections and
trivial to unit test without a running server.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from models.contracts.operations import GenerationRequest


@dataclass(frozen=True)
class InstalledModel:
    """A model an engine reports as installed at a given endpoint."""

    model_id: str
    size: int | None = None
    loaded: bool = False  # from /api/ps
    # Total bytes the loaded instance occupies in memory right now (the ps
    # entry's `size`; RAM + VRAM combined), None while not loaded or when the
    # engine doesn't report one. The loaded-now chip reads this via
    # `getattr(..., "loaded_size", None)` so third-party/stub engines that
    # predate the field degrade to "no size shown", never AttributeError.
    loaded_size: int | None = None
    capabilities: tuple[str, ...] = ()  # platform vocabulary, mapped by the engine adapter
    embedding_length: int | None = None  # raw engine-reported hidden/embedding size
    # Which of the engine's OWN loader nodes reported this model -- an
    # opaque, engine-shaped string (ComfyUI: "CheckpointLoaderSimple" vs
    # "UnetLoaderGGUF" vs "UNETLoader"). The one observable fact that
    # separates a self-contained checkpoint from a diffusion-model file
    # that needs companion encoder/VAE files, which is what the console
    # needs in order to know whether registration must ask for them. It is
    # NOT a model family and must never be treated as one -- no HTTP
    # surface on ComfyUI reports a GGUF file's architecture (verified
    # 2026-08-25), so the family is an operator declaration (ADR 0012
    # D-EDIT-2). Read downstream with `getattr(..., "loader", "")` so an
    # adapter predating this field degrades to blank, exactly like
    # `loaded_size` above.
    loader: str = ""


# Every job state the platform understands, in lifecycle order. "lost" is
# not a job status a record can hold -- it means the ENGINE no longer knows
# this job (restarted, queue cleared), which the service layer turns into a
# `failed` record with an explaining message.
JOB_STATES = ("queued", "running", "done", "failed", "lost")


@dataclass(frozen=True)
class Asset:
    """A file an engine can adorn a generation with -- a LoRA, VAE,
    ControlNet, upscaler, or embedding (D7).

    A second axis from role-bound MODELS: assets don't answer a role, they
    decorate a job. `asset_id` is opaque and engine-shaped -- on Windows
    hosts it may contain a backslash-separated subfolder -- so it is never
    parsed, only passed back.
    """

    kind: str
    asset_id: str
    size: int | None = None


@dataclass(frozen=True)
class JobStatus:
    """What an engine says about one submitted job right now.

    `state` is one of `JOB_STATES`. `error` carries the engine's own words
    for a failure (never a rewritten guess); `progress` is 0..1 only when
    the engine actually reports it, `None` otherwise -- a fabricated
    progress bar is worse than none; `queue_position` is a 1-based position
    among the engine's own pending jobs, `None` when the engine or state
    doesn't genuinely know one.
    """

    state: str
    error: str | None = None
    progress: float | None = None
    # 1-based position among the jobs the ENGINE has waiting, or `None` for
    # any state or engine that does not genuinely know one. Distinct from
    # `progress`: a position is a fact about a queue, not a fraction of
    # work done, and reporting it as one would be the fabricated bar the
    # field above refuses. `None` is the honest default -- an adapter that
    # cannot see a position simply never sets it.
    queue_position: int | None = None


class GenerationRejected(RuntimeError):
    """The engine refused a submission (a generation, a transcription)
    outright (unknown checkpoint, invalid graph). Distinct from a transport
    failure: the request reached the engine and came back rejected, so the
    job is immediately `failed` with the engine's own message rather than
    retried."""


# The platforms the setup page renders a guide for, in display order, and
# their human labels. Owned here (not in the page's template) so an adapter
# and the page agree on the keys without either hardcoding prose.
SETUP_PLATFORMS = ("macos", "windows", "linux")
SETUP_PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


@dataclass(frozen=True)
class SetupStep:
    """One instruction on the setup page: a heading, a sentence of context,
    and at most ONE command to copy. A step with no command (download a zip,
    edit a .bat) carries `command=None` rather than a fake shell line."""

    title: str
    body: str
    command: str | None = None


@dataclass(frozen=True)
class SetupGuide:
    """Everything the `/setup/` page needs to explain ONE engine.

    Declared by the ADAPTER, because only the adapter knows how its server is
    installed, how it must listen to be reachable from a container, and where
    its model files go. The page renders whatever it is handed and names no
    engine of its own -- a newly registered adapter appears there with no
    change to the page.

    `platforms` is keyed by `SETUP_PLATFORMS` entries; a platform an engine
    does not support is simply absent. `verify_url_path` is the path the page
    appends to the engine's default endpoint for its "Verify" line (e.g.
    "/api/tags", "/system_stats") -- the same path the adapter's own
    `is_healthy` uses, so what the page tells an operator to open is exactly
    what the platform checks.
    """

    summary: str
    platforms: dict[str, tuple[SetupStep, ...]]
    network_note: str
    models_note: str
    verify_url_path: str


class ImageGenerator(Protocol):
    """The seam a generating engine hands back for one bound model.

    Stateless above the endpoint + model it was built with: the platform
    holds no engine connection between requests, so every method is safe to
    call from any request or a later worker.
    """

    def submit(self, request: GenerationRequest) -> tuple[str, dict]:
        """Submit `request` and return `(engine_ref, payload)`.

        `engine_ref` is the engine's own job handle; `payload` is the EXACT
        submission, stored verbatim on the job row (D6) so a result is
        reproducible and exportable. Raises `GenerationRejected` when the
        engine refuses the submission.

        **The implementation owns input TRANSFER.** `request.inputs` maps a
        `"file"` param's key to a path in the PLATFORM's managed store
        (`<GENERATED_DIR>/<job>/inputs/`) -- a path on the platform's
        filesystem, which the engine may not share at all: ADR 0012 D1 puts
        the engine on the host while the platform runs in the `web`
        container. Making those bytes reachable is engine knowledge (an
        upload endpoint, an inline encoding, a shared volume), so it happens
        HERE, inside `submit`, and never as a separate platform-orchestrated
        step -- the handle a transfer yields is engine vocabulary that must
        not cross this line (spec §3).

        A transfer the engine REFUSES (unsupported type, too large) raises
        `GenerationRejected` like any other refusal. A transfer that fails
        in TRANSPORT raises whatever the HTTP layer raised; since this
        happens during `submit`, the service layer's generic exception
        handler fails the job immediately (there is no partially-submitted
        job to leave alone and retry) rather than treating it as transient
        the way a transport failure during `refresh_job`'s polling is. The
        platform keeps the authoritative copy of every input, so an
        operator who resubmits re-transfers with nothing lost.
        """
        ...

    def status(self, engine_ref: str) -> JobStatus:
        """Current `JobStatus` for `engine_ref` (see `JOB_STATES`)."""
        ...

    def fetch_outputs(self, engine_ref: str) -> list[tuple[str, bytes, str]]:
        """Every finished output as `(filename, content, media_type)`."""
        ...


@dataclass(frozen=True)
class TranscriptSegment:
    """One utterance a `Transcriber` recovered from an audio slice.

    `start`/`end` are seconds measured from the start of THIS audio -- the
    file `transcribe` was actually handed, never an absolute wall-clock
    time and never relative to some longer source the caller sliced this
    audio from (that offset, if any, is the caller's to add back via
    `TranscriptResult.offset` below).
    """

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    """Everything a `Transcriber` hands back for one bounded call.

    `language` is what the engine REPORTED -- its own detection, or the
    caller's `language=` hint echoed back -- never a guess made at this
    layer. `duration` is the audio's length in seconds when the engine
    reports one, `None` otherwise: the same opportunistic-fact shape as
    `InstalledModel.loaded_size` above, a measurement rather than a claim.
    """

    segments: tuple[TranscriptSegment, ...] = ()
    language: str | None = None
    duration: float | None = None

    @property
    def text(self) -> str:
        """The whole transcript as one string: every non-empty segment's
        text, newline-joined, blank segments skipped."""
        return "\n".join(segment.text for segment in self.segments if segment.text)

    def offset(self, seconds: float) -> "TranscriptResult":
        """A copy with every segment's `start`/`end` shifted by `seconds`.

        The one arithmetic op a caller who sliced a longer source into
        bounded audio chunks needs to re-express a slice's segments in the
        source's own timeline -- owned HERE so no pipeline re-derives it.
        `language`/`duration` are carried over unchanged.
        """
        return TranscriptResult(
            segments=tuple(
                TranscriptSegment(start=s.start + seconds, end=s.end + seconds, text=s.text)
                for s in self.segments
            ),
            language=self.language,
            duration=self.duration,
        )


class Transcriber(Protocol):
    """The seam a transcribing engine hands back for one bound model.

    Stateless above the endpoint + model it was built with, same as
    `ImageGenerator` above -- the platform holds no engine connection
    between requests, so `transcribe` is safe to call from any request or a
    later worker.
    """

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> TranscriptResult:
        """Transcribe ONE bounded audio slice, synchronously.

        One bounded call per audio slice -- no streaming, no background job
        of its own (the platform's execution queue is what makes this async,
        not this method). `audio_path` is 16kHz mono 16-bit PCM WAV produced
        by the CALLER; this method reads exactly that format and never
        re-encodes or probes the input itself. `language=None` means say
        nothing: the server auto-detects, because the platform never
        guesses a language on an operator's behalf.
        """
        ...


class InferenceEngine(Protocol):
    """The seam an inference engine adapter implements."""

    name: str  # e.g. "ollama"

    # One-phrase, human description of the API this adapter speaks (e.g.
    # "the Ollama HTTP API"). The console's "Supported APIs" disclosure
    # derives its whole listing from just this, `.name`, `well_known_ports`,
    # and the adapter's own module file path -- nothing about a specific
    # engine is hardcoded in the console layer, so a second registered
    # adapter appears there automatically.
    api_description: str

    # Where an operator can browse this engine's FULL model library (e.g.
    # Ollama: "https://ollama.com/library") -- the console's starter section
    # prints this verbatim, as plain shown text, never fetched (this
    # platform never makes outbound calls on an operator's behalf). Read via
    # `getattr(engine, "library_url", "")` like `api_description`, so a
    # future adapter that hasn't set one yet degrades to an empty string
    # rather than an AttributeError. A second registered adapter's library
    # appears in the console automatically, with no template change.
    library_url: str = ""

    # The SHAPE of this engine's install command, with a `<model-name>`
    # placeholder (e.g. Ollama: "ollama pull <model-name>") -- the console's
    # "Getting models" facts line prints it verbatim. Never a
    # runnable, model-pinned command: the platform never names models for
    # the operator and never pulls one itself. Read via
    # `getattr(engine, "install_cmd_template", "")` like `api_description`/
    # `library_url`, so an adapter without one degrades to an empty string
    # (its facts line just omits the install shape).
    install_cmd_template: str = ""

    # Ports this engine is conventionally served on (e.g. Ollama: (11434,)).
    # Declarative, engine-owned detection surface for the user-
    # triggered server scan (models/registry/discovery.py) -- the scan
    # loop itself never hardcodes a port or engine name, it only reads this
    # per-engine tuple. A future adapter (vLLM/openai-compat) declares its
    # own without the scan loop changing at all.
    well_known_ports: tuple[int, ...]

    def is_healthy(self, endpoint: str, timeout: float | None = None) -> bool:
        """Return True if the engine at `endpoint` is reachable.

        `timeout` is optional -- omitting it keeps each adapter's own
        default (e.g. Ollama's discovery-oriented `DISCOVERY_TIMEOUT`);
        callers doing a rapid multi-candidate sweep (the server scan) pass a
        much shorter one explicitly.
        """
        ...

    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Return the models installed at `endpoint`, flagging which are loaded."""
        ...

    def build_llm(self, model_id: str, endpoint: str, **cfg):
        """Build a LlamaIndex LLM bound to `model_id` at `endpoint`.

        `cfg` MAY carry `request_timeout` (final review MINOR 6, fix
        round 3, one-timeout task): `models.contracts.gateway.
        get_llm_for` injects it, unconditionally, for EVERY engine when
        a caller passes one (the turn's own wall clock, so the engine's
        client can never time out before the turn's own deadline does --
        see that function's own docstring). An adapter that forwards
        `**cfg` straight into its underlying client's constructor without
        first popping or renaming this key will raise a `TypeError`
        inside a turn the moment a caller asks for it; `ollama`'s own
        adapter both documents and honours it as its own `request_
        timeout` parameter, which is the shape to copy.
        """
        ...

    def build_embedder(self, model_id: str, endpoint: str, **cfg):
        """Build a LlamaIndex BaseEmbedding bound to `model_id` at `endpoint`.

        `cfg` MAY carry `client_kwargs={"timeout": ...}` for the identical
        reason `build_llm`'s own `request_timeout` does (final review
        MINOR 6, fix round 3): `models.contracts.gateway.
        get_embed_model_for` injects it when a caller passes a
        `request_timeout`, since the embeddings client has no bare
        `request_timeout` field of its own to set directly.
        """
        ...

    # --- Optional members (spec §4.2) ------------------------------------
    # Read downstream via `getattr(engine, name, None)`, exactly like
    # `library_url` / `install_cmd_template`, so an adapter that implements
    # none of them (OllamaEngine) is untouched and never raises.

    def supported_operations(
        self, model_id: str, endpoint: str, family: str = ""
    ) -> tuple[str, ...]:
        """Operation keys this engine can run for `model_id`, optionally
        narrowed to a connection's declared model `family`. An engine with
        one pipeline shape ignores `family`; an engine whose graphs differ
        per family reports only that family's operations, and `()` for a
        family it has no graph for."""
        ...

    def list_assets(self, endpoint: str, kind: str) -> list[Asset]:
        """Assets of `kind` installed at `endpoint`; `[]` for a kind this
        engine doesn't have."""
        ...

    def list_choices(self, endpoint: str, param_key: str) -> tuple[str, ...]:
        """Live option list for a `"choice"` param (samplers, schedulers);
        `()` for a param key this engine doesn't report."""
        ...

    def build_image_generator(self, model_id: str, endpoint: str, **cfg) -> ImageGenerator:
        """Build an `ImageGenerator` bound to `model_id` at `endpoint`."""
        ...

    def build_transcriber(self, model_id: str, endpoint: str, **cfg) -> Transcriber:
        """Build a `Transcriber` bound to `model_id` at `endpoint`."""
        ...

    def list_families(self, endpoint: str) -> tuple[str, ...]:
        """Model families an operator may declare for a connection at
        `endpoint`; `()` for an engine whose pipelines don't vary by family.
        Read downstream via `getattr(engine, "list_families", None)`, so an
        adapter without one simply never asks for a family."""
        ...

    # --- Optional: a variant (a second graph for the same family), its own
    # param defaults, and the params its graph cannot honour (ADR 0012
    # D-EDIT-6/7/12/13) -----------------------------------------------
    #
    # All three OPTIONAL, the same way `list_families` above is: read
    # downstream via `getattr(engine, "list_variants"/"param_defaults"/
    # "ignored_params", None)`, so an adapter that predates them (or has no
    # such concept at all) simply never offers a variant, never overrides a
    # schema default, and never disables a param.

    def list_variants(self) -> tuple[str, ...]:
        """Variants an operator may declare for a connection; `()` for an
        engine whose graphs don't vary by variant. Unlike `list_families`,
        this is not an engine vocabulary intersected with anything -- it is
        whichever second graphs this adapter's own template package has
        code for, so it takes no `endpoint` argument at all."""
        ...

    def param_defaults(self, operation_key: str, config: dict | None = None) -> dict:
        """Where a form for `operation_key` should START for a connection
        registered with `config` -- `{}` when this adapter has no opinion.
        The twin of `list_choices`/`list_assets`: those report options the
        schema cannot know, this reports defaults the schema cannot know."""
        ...

    def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
        """Params `operation_key`'s schema declares that this engine's graph
        for a connection registered with `config` cannot honour, mapped to
        the operator-facing reason. `{}` when this adapter honours
        everything. The negative twin of `param_defaults`: that one reports
        where a param should START, this one reports that it will not be
        read at all -- so a console can disable the field and say why
        instead of accepting a value and silently dropping it (ADR 0012
        D-EDIT-12/13). It ANNOTATES a param the schema still declares;
        D-EDIT-7 still stands."""
        ...

    # --- Optional: the execution queue's engine seam (T2b) -----------------
    #
    # Both methods below are OPTIONAL, the same way `library_url`/
    # `install_cmd_template` above are: callers read them defensively via
    # `getattr(engine, "loaded_footprint", None)` / `getattr(engine,
    # "unload", None)`, never by calling them directly off an
    # `InferenceEngine`-typed value, so a third-party/stub adapter that
    # predates these methods degrades to "no measurement" / "can't unload"
    # rather than `AttributeError`. `OllamaEngine` implements both;
    # declaring them here (rather than leaving them undocumented) is what
    # lets a future adapter's author discover the seam at all.

    def loaded_footprint(self, endpoint: str, model_id: str) -> int | None:
        """Total bytes `model_id` occupies in memory at `endpoint` right
        now, or `None` if it isn't loaded or the engine doesn't report one
        -- the same opportunistic-fact shape as `InstalledModel.loaded_size`
        above: a measurement, never a claim, and never an operation that
        can fail its caller.
        """
        ...

    def unload(self, endpoint: str, model_id: str) -> bool:
        """Best-effort request that the engine release `model_id`'s memory
        at `endpoint` now, instead of waiting for its own idle timeout.
        Returns `True` only if the engine ACCEPTED the request -- not a
        guarantee the memory is already free by the time this returns.

        This is a FLOOR, not a ceiling: an adapter MAY strengthen it and
        verify the memory is actually free before returning `True` (an
        engine whose own "accepted" and "released" are not synchronous
        events has good reason to -- see `models/contracts/engines/comfyui.py
        ::ComfyUIEngine.unload`'s own docstring for a case that does).
        `False` from such an adapter can then mean "accepted but not yet
        visibly free" as well as "refused" -- still within this contract,
        since `True` was never promised for the merely-accepted case
        alone, only permitted for it.
        """
        ...

    def supports_tool_calling(self, model_id: str, endpoint: str) -> bool | None:
        """Whether `model_id` at `endpoint` can be driven with TOOL CALLS,
        or `None` if this engine does not report it -- the same
        opportunistic-fact shape as `loaded_footprint` above: a
        measurement, never a claim.

        OPTIONAL, like the two methods above. Called via
        `getattr(engine, "supports_tool_calling", None)`, so an adapter
        predating this method degrades to `None`, never
        `AttributeError`.

        Three-valued on purpose. `False` means the engine reported its
        capabilities and tool calling was not among them -- the chat
        preflight refuses BEFORE enqueue with an honest 503 naming the
        role. `None` means the engine does not report the fact at all --
        the turn runs, and if the model emits no tool call that is a
        final answer, honestly indistinguishable from a model that chose
        not to use one. Never parse a tool call out of prose, never
        inject a hand-rolled syntax into the system prompt, never retry
        with a "please respond in JSON" nudge.
        """
        ...

    # How to install and reach this engine, rendered verbatim by the
    # `/setup/` page. `None` (or absent) means the page falls back to the
    # facts every adapter has: `name`, `api_description`, `well_known_ports`,
    # `install_cmd_template`.
    setup_guide: SetupGuide | None = None

    # Which platform capabilities this engine can serve at all -- the
    # "Served by" column of the setup page's "What each feature needs"
    # table. Declarative and engine-owned, like `well_known_ports`; absent
    # means "unknown", rendered as no engine claiming that capability.
    serves_capabilities: tuple[str, ...] = ()
