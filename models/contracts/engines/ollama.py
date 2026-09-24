"""
Ollama inference engine adapter.

Implements the `InferenceEngine` seam (models/contracts/engines/base.py) for
Ollama's HTTP API. `Ollama`/`OllamaEmbedding` are only ever constructed here
— callers reach them through `build_llm`/`build_embedder`, never by
importing `llama_index.llms.ollama` / `llama_index.embeddings.ollama`
directly.
"""
from __future__ import annotations

import logging

import httpx
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama

from models.contracts.engines.base import InstalledModel, SetupGuide, SetupStep
from models.contracts.roles import CAPABILITIES

logger = logging.getLogger(__name__)

# Generous timeout: local CPU/GPU inference on modest hardware can be slow,
# especially for longer generations or a cold model load.
DEFAULT_REQUEST_TIMEOUT = 300.0

# Bounded operational default for the KV cache the engine is asked to
# allocate. LlamaIndex's `Ollama` LLM defaults `context_window=-1`, which
# means "ask the model server what its architecture's theoretical MAXIMUM
# context length is, and request a KV cache sized for that on every call"
# (`Ollama.get_context_window()` calls `client.show()` and adopts whatever
# `context_length` it reports). Ollama's request-level `num_ctx` overrides
# anything set in a Modelfile, so an operator who deliberately capped a
# model's context (to bound its memory footprint) gets silently overridden
# the moment this adapter builds an LLM without an explicit context window
# -- a live incident reproduced this exactly: an architecture max of 262144
# tokens sized a KV cache dozens of gigabytes larger than intended,
# on a machine meant to keep the model's footprint small, and RAG requests
# never need anywhere near that much context anyway. This is a resource
# bound like `DEFAULT_REQUEST_TIMEOUT` above, not a claim about any
# particular model -- `build_llm` never lets the client probe architecture
# max; a caller (or a registered connection's stored override, see
# `models/registry/models.py::ModelConnection.context_window`) may always
# set a larger or smaller explicit value via `cfg`.
DEFAULT_CONTEXT_WINDOW = 8192

# Health/discovery calls should fail fast rather than hang for as long as a
# generation is allowed to — they're polled by things like a status page.
DISCOVERY_TIMEOUT = 5.0

# `unload` (T2b) is neither a health probe nor a generation: Ollama
# processes the keep_alive=0 request synchronously and, on a busy engine,
# it may briefly queue behind whatever request is already running before it
# can act on it. `DISCOVERY_TIMEOUT` (5s, sized for a bare health probe)
# would false-negative under exactly that mild contention; the full
# `DEFAULT_REQUEST_TIMEOUT` (300s) is generous to the point of leaving a
# caller (the future worker, releasing memory to make room for the next
# queued job) hanging far too long on a wedged engine. 30s splits the
# difference: enough slack for a request already in flight to finish, still
# bounded so `unload` never hangs a caller indefinitely.
UNLOAD_TIMEOUT = 30.0

# Ollama's `/api/tags` `capabilities` strings, mapped to the platform's
# capability vocabulary (models.contracts.roles.CAPABILITIES). Engine-specific
# by nature, so the mapping lives here rather than in the platform layer.
# Unknown/unrecognized Ollama capability strings are dropped, not raised on.
_CAPABILITY_MAP: dict[str, str] = {
    "embedding": "embeddings",
    "vision": "vision",
    "completion": "chat",
}


def _ps_sizes(endpoint: str) -> dict[str, int | None]:
    """The one place that knows `/api/ps`'s response shape: `{model name:
    total bytes the running instance occupies in memory}` for every model
    currently loaded at `endpoint`. An entry without a `size` key still
    contributes `None` (loaded, unknown size) rather than being dropped --
    matching `list_installed`'s existing `loaded`-without-`loaded_size`
    case.

    Shared by `list_installed` (marks every installed row `loaded`/
    `loaded_size`) and `loaded_footprint` (a single model's lookup) so
    `/api/ps`'s shape is parsed in exactly one place. Raises
    `httpx.HTTPError` on a request failure or non-2xx response -- callers
    decide for themselves whether that should propagate
    (`list_installed`, which already lets `/api/tags` failures propagate)
    or degrade to `None` (`loaded_footprint`, an opportunistic measurement).
    """
    ps_response = httpx.get(f"{endpoint}/api/ps", timeout=DISCOVERY_TIMEOUT)
    ps_response.raise_for_status()
    return {model["name"]: model.get("size") for model in ps_response.json().get("models", [])}


def _map_capabilities(raw: list[str]) -> tuple[str, ...]:
    """Map Ollama's reported capability strings to the platform vocabulary.

    Drops any engine capability that doesn't map to a known platform
    capability (`CAPABILITIES`); keeps all that do, in the order Ollama
    reported them.
    """
    mapped = []
    for capability in raw:
        target = _CAPABILITY_MAP.get(capability)
        if target is not None and target in CAPABILITIES:
            mapped.append(target)
    return tuple(mapped)


class OllamaEngine:
    """`InferenceEngine` adapter for Ollama."""

    name = "ollama"

    # The console's "Supported APIs" disclosure prints this verbatim --
    # the one place this adapter's own API is described in prose.
    api_description = "the Ollama HTTP API"

    # The console's "Getting models" facts line prints this verbatim, as a
    # plain link -- the durable "go browse the real, current library
    # yourself" pointer that replaces trying to keep a pinned model list
    # fresh.
    library_url = "https://ollama.com/library"

    # The install shape the console's "Getting models" facts line prints
    # verbatim -- a placeholder template, never a model-pinned
    # command (pinned model names age and were rejected from the UI).
    install_cmd_template = "ollama pull <model-name>"

    # Ollama's default listen port -- the only address the generic
    # scan loop knows about this engine; declared here, read nowhere in the
    # scan's own code by name.
    well_known_ports = (11434,)

    # WHAT ONE `unload()` CALL FREES HERE. "model": this adapter's unload
    # is `POST /api/generate {"model": <name>, "keep_alive": 0}` (see
    # `unload`'s own docstring), which names ONE model and releases that
    # model alone -- every other model resident at the endpoint stays
    # loaded. The execution queue reads this to decide whether it may
    # skip a protected key one by one or must leave the whole endpoint
    # alone; without the declaration it assumes "endpoint", the safe
    # guess, and would then believe a single call had freed everything
    # here when it had freed one model.
    unload_scope = "model"

    # HOW MUCH THIS ENGINE'S RESIDENCY REPORT IS WORTH. "endpoint": the
    # `loaded` flags `list_installed` carries come from a LIVE `GET
    # /api/ps` against the engine itself (`_ps_sizes`), not from any
    # process-local memory this adapter keeps -- so "nothing resident"
    # here is a FACT about the engine, and a restart of THIS process
    # cannot produce it. The queue reads this to decide whether an empty
    # residency answer is worth trusting before it launches an exclusive
    # job; the declaration is what spares an idle engine a precautionary
    # unload on every such tick.
    residency_authority = "endpoint"

    # Which platform capabilities this engine can answer at all -- the setup
    # page's "Served by" column reads this; nothing else branches on it.
    serves_capabilities = ("chat", "embeddings", "vision")

    # How an operator installs and reaches Ollama. Declared HERE because only
    # this adapter knows it; the /setup/ page renders it verbatim.
    setup_guide = SetupGuide(
        summary=(
            "Ollama runs natively on the host and serves chat, embedding, and vision "
            "models over HTTP. farabunker never pulls a model for you — you pull the "
            "ones you want, then assign them to roles."
        ),
        platforms={
            "macos": (
                SetupStep(
                    "Install Ollama",
                    "Homebrew, or the .dmg from ollama.com.",
                    "brew install ollama",
                ),
                SetupStep(
                    "Start it so containers can reach it",
                    "OLLAMA_HOST=0.0.0.0 binds all interfaces; without it Ollama listens "
                    "on localhost only and the farabunker container cannot see it.",
                    "OLLAMA_HOST=0.0.0.0 ollama serve",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
            "windows": (
                SetupStep(
                    "Install Ollama",
                    "winget, or the installer from ollama.com.",
                    "winget install Ollama.Ollama",
                ),
                SetupStep(
                    "Let containers reach it",
                    "Set OLLAMA_HOST=0.0.0.0 in the system environment variables, then "
                    "restart Ollama from the tray.",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
            "linux": (
                SetupStep(
                    "Install Ollama",
                    "The official installer script sets up a systemd service.",
                    "curl -fsSL https://ollama.com/install.sh | sh",
                ),
                SetupStep(
                    "Let containers reach it",
                    'Bind all interfaces — for the systemd unit, add '
                    'Environment="OLLAMA_HOST=0.0.0.0" and reload; when running it by '
                    "hand, use the command shown.",
                    "OLLAMA_HOST=0.0.0.0 ollama serve",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
        },
        network_note=(
            "The farabunker web container reaches your machine as host.docker.internal, so "
            "Ollama must listen on all interfaces (OLLAMA_HOST=0.0.0.0). On the Linux "
            "appliance Ollama is its own container instead — same OLLAMA_BASE_URL contract, "
            "different value."
        ),
        models_note=(
            "Pull models yourself: ollama pull <model-name>. farabunker never pulls one for "
            "you and presumes no model — a role stays honestly unassigned until you assign "
            "it. The full library is at https://ollama.com/library."
        ),
        verify_url_path="/api/tags",
    )

    def is_healthy(self, endpoint: str, timeout: float | None = None) -> bool:
        """Return True if `endpoint` responds successfully to `/api/tags`.

        `timeout` defaults to `DISCOVERY_TIMEOUT` (existing callers,
        unchanged); the server scan passes its own much shorter
        `PROBE_TIMEOUT` explicitly.
        """
        try:
            response = httpx.get(
                f"{endpoint}/api/tags", timeout=DISCOVERY_TIMEOUT if timeout is None else timeout
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Return the models installed at `endpoint`, flagging which are loaded.

        Installed models come from `/api/tags`; currently loaded models come
        from `/api/ps` — a model is marked `loaded` if its name appears in
        the latter, and its `loaded_size` is that ps entry's `size` (the
        TOTAL bytes the running instance occupies in memory — Ollama also
        reports `size_vram`, the VRAM-resident portion of that total, which
        is deliberately not surfaced: the console's "in memory" chip answers
        "how much of this machine is this model holding right now", not how
        the engine split it across RAM and VRAM). A ps entry without a
        `size` (or a model not loaded at all) yields `loaded_size=None`.
        """
        tags_response = httpx.get(f"{endpoint}/api/tags", timeout=DISCOVERY_TIMEOUT)
        tags_response.raise_for_status()
        installed = tags_response.json().get("models", [])

        loaded_sizes = _ps_sizes(endpoint)

        return [
            InstalledModel(
                model_id=model["name"],
                size=model.get("size"),
                loaded=model["name"] in loaded_sizes,
                loaded_size=loaded_sizes.get(model["name"]),
                capabilities=_map_capabilities(model.get("capabilities", [])),
                embedding_length=model.get("details", {}).get("embedding_length"),
            )
            for model in installed
        ]

    def build_llm(self, model_id: str, endpoint: str, **cfg) -> Ollama:
        """Build a LlamaIndex `Ollama` LLM bound to `model_id` at `endpoint`.

        `request_timeout` defaults to `DEFAULT_REQUEST_TIMEOUT` but may be
        overridden via `cfg`; same for `context_window` and
        `DEFAULT_CONTEXT_WINDOW` (see that constant's comment) -- this
        adapter never leaves `context_window` at the LlamaIndex default
        (`-1`), which triggers the architecture-max probe against the model
        server. A registered connection's stored `context_window` (threaded
        through `models.registry.bindings.db_provider` ->
        `models.contracts.bindings.ResolvedModel.config`) lands in `cfg` and
        wins over this default; an unset connection or an env-provider
        resolution (which has no per-connection override to offer) falls
        through to it.
        """
        cfg.setdefault("request_timeout", DEFAULT_REQUEST_TIMEOUT)
        cfg.setdefault("context_window", DEFAULT_CONTEXT_WINDOW)
        return Ollama(model=model_id, base_url=endpoint, **cfg)

    def build_embedder(self, model_id: str, endpoint: str, **cfg) -> OllamaEmbedding:
        """Build a LlamaIndex `OllamaEmbedding` bound to `model_id` at `endpoint`.

        No `context_window` guard here: unlike `Ollama`, LlamaIndex's
        `OllamaEmbedding` has no `context_window` field and no
        architecture-max probing at all -- its `/api/embed` calls only ever
        send `options=self.ollama_additional_kwargs` (empty by default, and
        never touched here), so there is no analogous incident to guard
        against and nothing to configure. Verified by reading the installed
        `llama_index.embeddings.ollama.base.OllamaEmbedding` source.

        W6 review MAJOR 2: `build_llm` above bounds every request via
        `request_timeout`, but this constructor used to leave the
        underlying `ollama.Client`'s own httpx timeout at its library
        default of `None` (unbounded) -- a client built that way blocks its
        caller's request thread FOREVER against a wedged/hung embed
        server. That mattered only cosmetically while every embed call ran
        behind the execution queue (a stuck worker, not a stuck request);
        it became load-bearing the moment `tools.rag.views.SearchView`
        (W6) started calling `retrieve_nodes` -> an embed call
        SYNCHRONOUSLY inside an HTTP request, with no queue between a
        wedged engine and the thread serving that request.
        `OllamaEmbedding.__init__` (installed
        `llama_index.embeddings.ollama.base`) threads its `client_kwargs`
        straight into `ollama.Client(host=..., **client_kwargs)`
        (`ollama._client.BaseClient.__init__`), which passes them straight
        to `httpx.Client(timeout=..., ...)` -- so setting
        `client_kwargs={"timeout": DEFAULT_REQUEST_TIMEOUT}` bounds the
        embedder's requests to the SAME 300s ceiling `build_llm` already
        gives chat/generate calls: a stalled embed server can cost a
        request thread at most that long, never indefinitely.
        `client_kwargs.setdefault`, not an outright overwrite, so a caller
        who already passed their own `client_kwargs` (a custom timeout, TLS
        config, ...) via `cfg` keeps it -- this only fills in `timeout` when
        the caller didn't already set one.
        """
        client_kwargs = dict(cfg.get("client_kwargs") or {})
        client_kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT)
        cfg["client_kwargs"] = client_kwargs
        return OllamaEmbedding(model_name=model_id, base_url=endpoint, **cfg)

    def loaded_footprint(self, endpoint: str, model_id: str) -> int | None:
        """Total bytes `model_id` occupies in memory at `endpoint` right
        now, via the same `/api/ps` read `list_installed` uses (`_ps_sizes`)
        -- `None` if `model_id` isn't currently loaded, ps reports no size
        for it, or the request itself fails.

        Unlike `list_installed` (which lets a `/api/tags`/`/api/ps` failure
        propagate -- a caller there asked to see the whole install list and
        deserves to know it failed), any `httpx.HTTPError` here degrades to
        `None`: a measurement is an opportunistic fact, never an operation
        that can fail its caller -- the scheduler that calls this already
        treats `None` as "unknown footprint, job runs alone" regardless of
        why.
        """
        try:
            sizes = _ps_sizes(endpoint)
        except httpx.HTTPError:
            return None
        return sizes.get(model_id)

    def unload(self, endpoint: str, model_id: str) -> bool:
        """Request that Ollama release `model_id`'s memory at `endpoint`
        right now, via Ollama's documented immediate-unload mechanism: POST
        `/api/generate` with `{"model": model_id, "keep_alive": 0}` and no
        `prompt` -- Ollama's own FAQ ("How can I unload a model / keep it
        loaded in memory?") documents exactly this call ("To unload the
        model and free up memory use: `curl .../api/generate -d
        '{"model": "<name>", "keep_alive": 0}'`"); the same `keep_alive: 0`
        works against `/api/chat` with an empty `messages` list, but
        `/api/generate` is used here since it needs no messages array at
        all. Ollama accepts `keep_alive` values ("5m", `-1`, `0`, ...)
        uniformly on every generate/chat/embed endpoint; `0` is the one
        that means "unload immediately" rather than "keep loaded".

        Uses `UNLOAD_TIMEOUT` (see that constant's comment), not
        `DISCOVERY_TIMEOUT` -- this is a state-changing request that may
        briefly queue behind in-flight work, not a health probe. Returns
        `True` only if Ollama accepted the request (2xx); any
        `httpx.HTTPError` (refused connection, non-2xx response, or a
        response slower than `UNLOAD_TIMEOUT`) -> `False`, never an
        exception -- the caller degrades to "didn't unload".
        """
        try:
            response = httpx.post(
                f"{endpoint}/api/generate",
                json={"model": model_id, "keep_alive": 0},
                timeout=UNLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    def supports_tool_calling(self, model_id: str, endpoint: str) -> bool | None:
        """Ask Ollama whether `model_id` reports the `"tools"` capability.

        `POST /api/show` with `{"model": model_id}` returns a
        `capabilities` list. Verified live on 2026-08-25: a chat model
        reports `["completion", "tools"]`, an embedding model
        `["embedding"]`, and a vision-capable chat model
        `["completion", "vision"]`.

        Reads the RAW `"tools"` string. It deliberately does NOT route
        through `_map_capabilities`/`_CAPABILITY_MAP` above, which DROP
        `"tools"` because it has no platform-capability equivalent --
        routing through them would make this method always `False`. That
        drop is correct and is pinned by
        `test_unknown_engine_capability_is_dropped`; `"tools"` is a model
        trait, not a role purpose, so it stays out of `CAPABILITIES`.

        Uses `DISCOVERY_TIMEOUT`: this is metadata, not a generation. It
        loads nothing -- `/api/show` reads the manifest, it does not
        bring the model into memory.

        Returns `None` rather than raising on any HTTP failure, and
        `None` rather than `False` when the response carries no
        `capabilities` key at all -- "the engine does not report it" is a
        different fact from "the engine reports it cannot", and the
        caller acts differently on each. Also degrades to `None` (logged
        at debug, never raised) if `capabilities` is present but not a
        list, OR if the decoded body itself isn't a dict (e.g. valid JSON
        that decodes to a bare list) -- either shape is "not reported",
        not a crash. The body's own shape is checked BEFORE `.get` is
        called on it: a non-dict body has no `.get` at all, so a
        `.get("capabilities")` on the raw decode would raise
        `AttributeError` -- not one of the two exceptions this method
        already catches -- and escape the never-raise contract.
        """
        try:
            response = httpx.post(
                f"{endpoint}/api/show",
                json={"model": model_id},
                timeout=DISCOVERY_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(body, dict):
            logger.debug(
                "ollama: /api/show returned a non-dict body (%r); treating as unreported",
                body,
            )
            return None
        reported = body.get("capabilities")
        if reported is None:
            return None
        if not isinstance(reported, list):
            logger.debug(
                "ollama: /api/show returned a non-list capabilities value (%r); "
                "treating as unreported",
                reported,
            )
            return None
        return "tools" in reported
