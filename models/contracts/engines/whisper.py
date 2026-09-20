"""
whisper.cpp server inference engine adapter (media-into-RAG plan T6;
ADR 0014).

Implements the `InferenceEngine` seam (models/contracts/engines/base.py) for
whisper.cpp's HTTP server (`whisper-server`), and its `Transcriber` half in
`WhisperTranscriber`. This adapter speaks ONLY the `transcription`
capability -- `build_llm`/`build_embedder` raise, the same "raise, don't
fake" posture `comfyui.py` takes for the roles IT cannot serve.

Nothing here downloads anything. `list_installed` reports the honest truth
that whisper.cpp gives no enumeration surface at all (see its own
docstring), and `install_cmd_template` -- unlike every other adapter's --
IS the model-choice mechanism: `whisper-server` loads exactly one model
file, named on its own command line via `-m`, for the life of the process.

HTTP is plain `httpx` with per-call timeouts and NO retries: retry policy is
a caller concern (the service layer decides), the same stance `ollama.py`
and `comfyui.py` both take -- errors are left raw for a caller (`discover()`,
the execution queue) to isolate.

VERIFICATION STATUS: every point tagged "V1"-"V4" in a comment below was
originally a design decision made against whisper.cpp's PUBLISHED HTTP API
docs alone, without a live `whisper-server` to confirm it against (T6). A
follow-up pass (T6 review) then verified all four live against a real
brew-installed `whisper-server` (large-v3-turbo) at http://localhost:8080 --
each "V<n>" comment below now says what was actually observed, and still
documents which fallback branches remain speculative for OTHER whisper.cpp
builds this adapter has not seen.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from models.contracts.engines.base import (
    GenerationRejected,
    InstalledModel,
    SetupGuide,
    SetupStep,
    TranscriptResult,
    TranscriptSegment,
)
from models.contracts.engines.engine_http import HEALTH_MARKER_SEARCH_BYTES, get_bounded

logger = logging.getLogger(__name__)

# A transcription call has to cover the WORST-CASE slice, not the typical
# one: `tools.rag.transcode.DEFAULT_SLICE_SECONDS` (300s / 5 minutes) is
# how much audio a caller hands `transcribe()` in one call, and this timeout
# is 3x that -- generous headroom for a slow/CPU-only whisper-server on a
# long slice, without being an unbounded wait. (Not imported:
# `models/contracts/` may not depend on `tools/` -- see
# `models.contracts.roles`'s own layering comment -- so the 300s figure is
# cited here in prose, not code, and the
# two constants must be kept in sync by hand if either changes.)
DEFAULT_TRANSCRIBE_TIMEOUT = 900.0

# Health/discovery calls should fail fast rather than hang -- the same
# discovery-oriented ceiling every other adapter uses (see
# `ollama.DISCOVERY_TIMEOUT` / `comfyui.DISCOVERY_TIMEOUT`).
DISCOVERY_TIMEOUT = 5.0

# whisper.cpp's server answers a bare GET on "/" with a small HTML status
# page. This path is ALSO what `setup_guide.verify_url_path` points an
# operator at (see the comment on that field below) -- the two must stay
# equal, since what the page tells an operator to open is exactly what this
# adapter checks.
_HEALTH_PATH = "/"

# V1 -- VERIFIED LIVE (brew-installed whisper-server, large-v3-turbo,
# http://localhost:8080): `GET /` answers 200 with an HTML page titled
# "Whisper.cpp Server", containing "whisper" case-insensitively well within
# the first 4KB -- the default port (8080) and this marker are both correct
# as shipped. The marker still matters: port 8080 is commonly squatted by
# other local dev servers (other HTTP APIs, static file servers, reverse
# proxies), so a bare "did something answer on 8080" would not be enough to
# call it whisper.cpp -- this case-insensitive substring sniff of the
# response body (first 4KB) is the guard against a false-positive
# server-scan hit. Setting it to "" disables the sniff entirely (falls back
# to a bare 200 check, like every other adapter's `is_healthy`).
_HEALTH_MARKER = "whisper"

# How much of the health response body the marker sniff searches -- a
# resource bound (never read an unbounded body just to health-check),
# comfortably larger than the small status page whisper.cpp actually
# returns. Lives in `engine_http.py` as `HEALTH_MARKER_SEARCH_BYTES` (S11):
# one constant, imported here, rather than a second `4096` under a local
# name that could drift from it.

# V2 -- VERIFIED LIVE: `POST /inference` with multipart field "file"
# (audio/wav) plus `response_format=verbose_json` answered 200 against the
# same real whisper-server -- this path and the request shape
# `WhisperTranscriber.transcribe` builds are both correct as shipped. The
# verified build exposed no `GET /` or `/models` style route naming the
# loaded model file (see `list_installed`'s own note) -- `_INFERENCE_PATH`
# stays the one endpoint this adapter treats as load-bearing.
_INFERENCE_PATH = "/inference"


def _looks_like_whisper(text: str) -> bool:
    """True if `text` (a health response body) contains `_HEALTH_MARKER`,
    case-insensitively, within the first `HEALTH_MARKER_SEARCH_BYTES`
    characters -- or unconditionally True when the marker is disabled
    (`_HEALTH_MARKER == ""`, see that constant's comment)."""
    if not _HEALTH_MARKER:
        return True
    return _HEALTH_MARKER.lower() in text[:HEALTH_MARKER_SEARCH_BYTES].lower()


class WhisperEngine:
    """`InferenceEngine` adapter for whisper.cpp's `whisper-server`."""

    name = "whisper"

    # The console's "Supported APIs" disclosure prints this verbatim.
    api_description = "the whisper.cpp server HTTP API"

    # Where an operator browses ready-made GGUF/ggml model files. whisper.cpp
    # has no single canonical model registry the way Ollama does, but this
    # is the durable, current pointer -- the project that publishes
    # whisper.cpp's own converted models.
    library_url = "https://huggingface.co/ggerganov/whisper.cpp"

    # The install SHAPE the console's "Getting models" facts line prints
    # verbatim. Unlike every other adapter's template, this one carries NO
    # `<model-name>` placeholder for a second argument to fill in later --
    # the START COMMAND *is* the model choice: `-m <model-file>` names the
    # exact file `whisper-server` loads for the life of the process, so
    # there is no separate "pull a model" step to template. No model names
    # appear anywhere in this string, matching every other adapter's
    # placeholder-only convention.
    install_cmd_template = "whisper-server -m <model-file> --host 0.0.0.0 --port 8080"

    # whisper.cpp's server default listen port.
    well_known_ports = (8080,)

    # Which platform capability this engine can answer at all -- the setup
    # page's "Served by" column reads this; nothing else branches on it.
    serves_capabilities = ("transcription",)

    # How an operator installs and reaches whisper-server. Declared HERE
    # because only this adapter knows it; the /setup/ page renders it
    # verbatim and hardcodes nothing about whisper.cpp.
    setup_guide = SetupGuide(
        summary=(
            "whisper.cpp's server runs natively on the host so it can use the GPU (Metal on "
            "macOS, CUDA on Linux/Windows) or a fast CPU build. farabunker talks to it over "
            "HTTP and never downloads a model for you."
        ),
        platforms={
            "macos": (
                SetupStep(
                    "Install whisper.cpp",
                    # V4 -- VERIFIED LIVE: the whisper-cpp Homebrew formula
                    # does ship the whisper-server binary (confirmed at
                    # /opt/homebrew/bin/whisper-server on the host that ran
                    # this verification, a large-v3-turbo model).
                    "Homebrew ships the server binary as part of the whisper-cpp formula.",
                    "brew install whisper-cpp",
                ),
                SetupStep(
                    "Or build it from source instead",
                    # V4: source-build is the fallback path this guide
                    # offers when Homebrew's build lacks a wanted backend
                    # (e.g. a specific Metal/CoreML tuning) -- the live
                    # verification above used the Homebrew path, so this
                    # source-build alternative itself remains unexercised.
                    "Clone the project and build with cmake; the server target is "
                    "whisper-server.",
                    "git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp && "
                    "cmake -B build -DGGML_METAL=1 && cmake --build build --config Release "
                    "--target whisper-server",
                ),
                SetupStep(
                    "Get a model file",
                    "Download a ggml/GGUF model file from the whisper.cpp model library and "
                    "place it anywhere on disk -- this is an operator action, not something "
                    "farabunker does for you. Browse models at "
                    "https://huggingface.co/ggerganov/whisper.cpp.",
                ),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces (--host 0.0.0.0), or the farabunker container "
                    "cannot reach it. -m names the exact model file this server instance "
                    "serves -- that choice IS how you pick a model here, not a separate pull "
                    "step.",
                    "whisper-server -m <model-file> --host 0.0.0.0 --port 8080",
                ),
            ),
            "linux": (
                SetupStep(
                    "Clone and build whisper.cpp",
                    "cmake build; add -DGGML_CUDA=1 to build against an NVIDIA GPU (requires "
                    "the CUDA toolkit already installed) -- omit it for a CPU-only build.",
                    "git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp && "
                    "cmake -B build -DGGML_CUDA=1 && cmake --build build --config Release "
                    "--target whisper-server",
                ),
                SetupStep(
                    "Get a model file",
                    "Download a ggml/GGUF model file and place it anywhere on disk -- your "
                    "choice, not farabunker's. Browse models at "
                    "https://huggingface.co/ggerganov/whisper.cpp.",
                ),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces, or the farabunker container cannot reach it.",
                    "./build/bin/whisper-server -m <model-file> --host 0.0.0.0 --port 8080",
                ),
            ),
            "windows": (
                SetupStep(
                    "Download a release build",
                    "Grab a prebuilt whisper-server.exe from the project's GitHub releases "
                    "page and unzip it anywhere.",
                ),
                SetupStep(
                    "Get a model file",
                    "Download a ggml/GGUF model file and place it anywhere on disk. Browse "
                    "models at https://huggingface.co/ggerganov/whisper.cpp.",
                ),
                SetupStep(
                    "Start it",
                    "Run from PowerShell, listening on all interfaces so the farabunker "
                    "container can reach it.",
                    ".\\whisper-server.exe -m <model-file> --host 0.0.0.0 --port 8080",
                ),
            ),
        },
        network_note=(
            "whisper-server binds 127.0.0.1 by default -- start it with --host 0.0.0.0 or it "
            "listens on localhost only. The farabunker web container reaches your machine as "
            "host.docker.internal, and a localhost-only server is invisible to it (the same "
            "OLLAMA_HOST=0.0.0.0 contract Ollama's own setup section above uses)."
        ),
        models_note=(
            "whisper-server loads exactly ONE model file for its whole process lifetime, named "
            "on its own command line via -m -- there is no in-process model switch. Register "
            "it manually at /inference/, using your model file's own name as the Model ID, "
            "with the transcription capability. Wanting a second model available at the same "
            "time means a second whisper-server process on a second port, registered as its "
            "own connection -- not a second model on this one. farabunker never downloads a "
            "model file for you."
        ),
        # MUST equal `_HEALTH_PATH` above -- what this guide tells an
        # operator to open is exactly what `is_healthy` checks.
        verify_url_path=_HEALTH_PATH,
    )

    def is_healthy(self, endpoint: str, timeout: float | None = None) -> bool:
        """True if `endpoint` answers `_HEALTH_PATH` with 200 AND a body
        that sniffs as whisper.cpp (`_looks_like_whisper`) -- see
        `_HEALTH_MARKER`'s comment for why the marker check exists at all
        (this port is commonly squatted by something else). Any `httpx`
        transport error -> False, matching every other adapter's
        `is_healthy`.

        S11: the body is read through `get_bounded`, capped at
        `HEALTH_MARKER_SEARCH_BYTES` -- a health check only ever needs the
        first few KB, so a hostile or misbehaving process on this port
        cannot turn a health probe into an unbounded read. `truncate=True`
        (review round 1): a status page LONGER than the cap is still
        sniffed on its first `HEALTH_MARKER_SEARCH_BYTES` bytes, not
        refused outright -- the marker sniff only ever wanted a prefix of
        the body anyway, so a long page is not the same failure a
        truncated/over-budget DATA read would be.
        """
        try:
            body = get_bounded(
                f"{endpoint}{_HEALTH_PATH}",
                timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
                max_bytes=HEALTH_MARKER_SEARCH_BYTES,
                truncate=True,
            )
        except httpx.HTTPError:
            return False
        return _looks_like_whisper(body.decode("utf-8", errors="replace"))

    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Always `[]`.

        whisper.cpp's server loads exactly one model file, chosen on its own
        command line via `-m` when the process starts -- there is no HTTP
        route that reports back WHICH file that was (confirmed live against
        a real brew-installed whisper-server, V2 above: its `GET /` and
        `POST /inference` are the only routes found, neither names the
        loaded model file). A future whisper.cpp build may still add a
        `/models`-style introspection route, which would make this honestly
        answerable -- until then this platform never guesses a model id on
        the operator's behalf, the
        same "never fabricate" stance `comfyui.list_installed`'s docstring
        states for its own opaque ids. `install_cmd_template` on this
        adapter documents the real mechanism: the start command IS the
        model choice. This method never calls anything resembling a
        `/load` endpoint -- there is nothing to load, the model is already
        fixed for the life of the process.
        """
        return []

    def build_llm(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("whisper serves transcription only")

    def build_embedder(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("whisper serves transcription only")

    def build_transcriber(self, model_id: str, endpoint: str, **cfg) -> "WhisperTranscriber":
        """A `Transcriber` bound to `model_id` at `endpoint`.

        `model_id` is carried on the returned object for the platform's own
        record-keeping (D6-style: a job documents which connection ran it)
        but is NEVER sent in a request -- see `WhisperTranscriber.transcribe`'s
        docstring for why.
        """
        return WhisperTranscriber(endpoint, model_id, config=cfg)


def _rejection_message(response: httpx.Response) -> str:
    """whisper-server's own words for a refused transcription request --
    its `.text` body, falling back to the bare status code when the body is
    empty. Never a rewritten guess, matching `comfyui._upload_rejection`'s
    identical stance."""
    text = (response.text or "").strip()
    return text or f"HTTP {response.status_code}"


class WhisperTranscriber:
    """`Transcriber` for one model file at one whisper-server endpoint.

    Stateless between calls, like `ComfyUIGenerator`: holds only the
    endpoint, model id, and the connection config it was built with. No
    retries -- the service layer decides what a transient failure means,
    the same stance every other engine adapter takes.
    """

    def __init__(self, endpoint: str, model_id: str, config: dict | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_id = model_id
        self.config = dict(config or {})

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> TranscriptResult:
        """Transcribe one bounded audio slice via `POST _INFERENCE_PATH`.

        Sends exactly three things, on purpose, and NOTHING else:

        - `files={"file": (<name>, <handle>, "audio/wav")}` -- V2 (verified
          live): the multipart field name a real whisper-server accepts.
        - `data={"response_format": "verbose_json"}` -- the shape
          `_parse_transcript` is written against (segment-level timestamps;
          see V3 below), always requested, never left to the server's own
          default.
        - `language` -- included ONLY when the CALLER passed one explicitly
          (a per-call hint, e.g. a user-selected language for one document);
          otherwise falls back to `config.get("language")` when the bound
          CONNECTION carries an operator-set default (a per-connection
          "this server only ever transcribes French" override); omitted
          from the request entirely when neither is set, which is
          whisper.cpp's own auto-detect.

        `model_id` is NEVER sent. whisper-server has exactly one model
        loaded (named on ITS OWN command line via `-m`) for the life of the
        process -- that IS the truth; a second model is a second server
        process on a second port, reached through its own connection/
        endpoint, never a parameter on this one (see `WhisperEngine.
        build_transcriber`'s docstring and this adapter's `models_note`).

        NO other request parameter is ever sent -- no `temperature`, no
        `beam_size`, nothing tunable. This mirrors `ollama.build_llm`'s
        `DEFAULT_CONTEXT_WINDOW` guard's own lesson (a live incident where
        an unbounded, silently-probed parameter blew a resource budget):
        the smallest, most literal request this adapter can make is the
        safest one, and any future tuning knob is an explicit, reviewed
        addition to this exact list, never an implicit passthrough of
        arbitrary `config`.

        Timeout is `config.get("request_timeout", DEFAULT_TRANSCRIBE_TIMEOUT)`
        -- an operator override on the bound connection wins, same shape as
        every other adapter's timeout override.

        A non-2xx response raises `GenerationRejected` carrying the server's
        own words (`_rejection_message`). A transport failure (connection
        refused, timeout) propagates raw -- the caller's concern, same
        stance as `comfyui.ComfyUIGenerator.submit`.
        """
        data = {"response_format": "verbose_json"}
        if language is not None:
            data["language"] = language
        elif self.config.get("language"):
            data["language"] = self.config["language"]

        timeout = self.config.get("request_timeout", DEFAULT_TRANSCRIBE_TIMEOUT)

        with open(audio_path, "rb") as handle:
            response = httpx.post(
                f"{self.endpoint}{_INFERENCE_PATH}",
                files={"file": (audio_path.name, handle, "audio/wav")},
                data=data,
                timeout=timeout,
            )

        if response.status_code >= 400:
            raise GenerationRejected(_rejection_message(response))

        return _parse_transcript(response.json() or {})


def _segments_from_seconds(raw_segments: list) -> tuple[TranscriptSegment, ...] | None:
    """Branch 1: `segments[]` entries carrying numeric `start`/`end` in
    seconds directly -- the shape whisper.cpp's `verbose_json`
    `response_format` actually returns, CONFIRMED live against a real
    brew-installed whisper-server (V3 in `_parse_transcript`'s docstring
    below).

    A malformed INDIVIDUAL entry (not a dict, or missing/non-numeric
    `start`/`end`) is skipped and logged -- the `readers._read_pdf`
    skip-the-bad-page precedent applied to segments: one bad entry costs its
    own segment, never every good one around it (T6 review). Returns `None`
    (not `()`) only when NOT ONE entry in the list matched this shape, so
    `_parse_transcript` can fall through to the next branch rather than
    mistaking "wrong shape entirely" for "genuinely empty"."""
    segments = []
    for entry in raw_segments:
        if not isinstance(entry, dict):
            logger.warning("whisper: skipping non-dict segment entry (expected an object): %r", entry)
            continue
        start, end = entry.get("start"), entry.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            logger.warning(
                "whisper: skipping segment entry with missing/non-numeric start or end: %r", entry
            )
            continue
        segments.append(
            TranscriptSegment(start=float(start), end=float(end), text=str(entry.get("text", "")))
        )
    return tuple(segments) if segments else None


def _segments_from_offsets(raw_segments: list) -> tuple[TranscriptSegment, ...] | None:
    """Branch 2: `segments[]` entries carrying an `offsets: {"from": ms,
    "to": ms}` sub-object instead -- whisper.cpp's OWN native CLI/server
    JSON output shape (distinct from the OpenAI-compatible `verbose_json`
    shape branch 1 targets and confirms), in milliseconds, converted to
    seconds here. Not observed on the verified build (branch 1 matched it
    directly), kept as a fallback for other whisper.cpp builds/versions.

    Same per-entry skip-and-log, all-or-none-`None` contract as
    `_segments_from_seconds` above (see that docstring)."""
    segments = []
    for entry in raw_segments:
        if not isinstance(entry, dict):
            logger.warning("whisper: skipping non-dict segment entry (expected an object): %r", entry)
            continue
        offsets = entry.get("offsets")
        if not isinstance(offsets, dict):
            logger.warning("whisper: skipping segment entry with no offsets object: %r", entry)
            continue
        start_ms, end_ms = offsets.get("from"), offsets.get("to")
        if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
            logger.warning(
                "whisper: skipping segment entry with missing/non-numeric offsets.from/to: %r", entry
            )
            continue
        text = entry.get("text", "")
        if isinstance(text, dict):  # some builds nest text under {"text": {"content": ...}}
            text = text.get("content", "")
        segments.append(
            TranscriptSegment(start=start_ms / 1000.0, end=end_ms / 1000.0, text=str(text))
        )
    return tuple(segments) if segments else None


def _parse_transcript(payload: dict) -> TranscriptResult:
    """Parse whisper-server's `/inference` response body into a
    `TranscriptResult`, defensively, trying shapes in order:

    1. `segments[]` with numeric `start`/`end` seconds (`_segments_from_seconds`)
       -- the OpenAI-Whisper-API-compatible `verbose_json` shape this
       adapter's request asks for by name. V3 -- VERIFIED LIVE (brew-installed
       whisper-server, large-v3-turbo): the real response's top-level keys
       are `text`, `language`, `duration`, `segments` (plus `detected_language`,
       `language_probabilities`, and `task`, all ignored here), and each
       `segments[]` entry carries numeric SECONDS `start`/`end` floats plus
       `text` (plus `id`/`tokens`/`words`/`avg_logprob`/etc, all ignored) --
       this branch matches reality exactly, confirming the offset math and
       `TranscriptResult.text` both operate on real seconds, not some other
       unit. NOTE: `language` arrives as a full word (e.g. `"english"`), not
       an ISO code (`"en"`) -- read verbatim below, never normalized or
       guessed at, per `TranscriptResult.language`'s own "the engine's own
       report" contract.
    2. `segments[]` with `offsets: {from, to}` in milliseconds
       (`_segments_from_offsets`) -- whisper.cpp's own native JSON shape;
       NOT observed on the verified build (branch 1 already matched), kept
       as a fallback in case some other build answers with this shape
       instead of (or in addition to) the OpenAI-compatible one.
    3. A bare top-level `text` string with no parseable `segments` at all --
       one single segment spanning `0.0` to `payload["duration"]` (or `0.0`
       if no duration is reported), rather than discarding the transcript
       text just because segment-level timing wasn't recoverable.
    4. Neither -- an empty `TranscriptResult()`. An engine that answered
       with something this adapter cannot parse is not this adapter's job to
       guess at; the caller sees "no segments, no text" and can decide what
       that means, exactly the same "never fabricate" posture
       `InstalledModel.loaded_size`/`TranscriptResult.duration` document.

    `language`/`duration` are read straight off the payload when present
    (the server's own report, or its echo of a caller's `language=` hint)
    and left `None` otherwise -- never guessed at this layer, per
    `TranscriptResult`'s own docstring.
    """
    language = payload.get("language") if isinstance(payload.get("language"), str) else None
    duration = payload.get("duration") if isinstance(payload.get("duration"), (int, float)) else None
    if duration is not None:
        duration = float(duration)

    raw_segments = payload.get("segments")
    if isinstance(raw_segments, list) and raw_segments:
        segments = _segments_from_seconds(raw_segments)
        if segments is None:
            segments = _segments_from_offsets(raw_segments)
        if segments is not None:
            return TranscriptResult(segments=segments, language=language, duration=duration)

    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        segment = TranscriptSegment(start=0.0, end=duration or 0.0, text=text)
        return TranscriptResult(segments=(segment,), language=language, duration=duration)

    return TranscriptResult(language=language, duration=duration)
