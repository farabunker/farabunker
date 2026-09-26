"""
Inference Gateway — the platform's stable, model-agnostic LLM + embeddings seam.

Per docs/ARCHITECTURE.md §3-4, modules never talk to an inference engine
directly. Everything goes through here, so the engine (Ollama today; vLLM /
llama.cpp / other later) and the models are swapped purely by config/binding,
without touching module code. This is the plug-and-play contract realized in
code.

The gateway itself constructs nothing: it resolves a role to a binding
(`models/contracts/bindings.py`) and asks that binding's engine
(`models/contracts/engines/`) to build the LLM/embedder. Callers should not
import `llama_index.llms.ollama` / `llama_index.embeddings.ollama` — or any
other engine's SDK — directly; go through `get_llm()` / `get_llm_for()` /
`get_embed_model()` / `get_embed_model_for()` instead.

Two entry points build an LLM, for two different questions: `get_llm(role)`
answers "what's durably bound to this role right now" (resolved via
`models.contracts.bindings.resolve`), while `get_llm_for(resolved)` takes an
already-resolved `ResolvedModel` a caller supplied directly — e.g. a
per-request override (the Ask-time model picker) that stands in for
the role binding just for that one call, without changing it.
`get_embed_model`/`get_embed_model_for` are the exact same pair, one rung
down, for embeddings.

Image generation mirrors that pair exactly: `get_image_generator(role)`
answers "what's durably bound right now", `get_image_generator_for(resolved)`
takes a binding the caller already holds — a job's own recorded engine,
model, endpoint, and config.

Transcription (media-into-RAG plan T6) mirrors it a third time:
`get_transcriber(role)` / `get_transcriber_for(resolved)`, identical shape,
one rung further down.

There is deliberately no `configure_settings()` here anymore (removed with
the execution queue's per-job model construction, T5): every caller now
builds its own LLM/embed model explicitly via the four LLM/embed functions
above and threads them through (LlamaIndex's
`VectorStoreIndex.from_vector_store(..., embed_model=...)` and
`index.as_query_engine(llm=...)` both bypass the
global `llama_index.core.Settings` entirely when given an explicit value —
verified against the installed `llama_index-core`, including at
retrieval/query-embedding time). Writing to the process-global `Settings`
was wrong for two reasons this module used to carry as one function's
docstring: (1) it has its own lazy hosted-API fallback, which is wrong for an
offline-first product -- an unconfigured `Settings.llm`/`embed_model`
silently tries to reach `api.openai.com` instead of failing loudly; (2) a
single mutable global is a race the moment more than one request/job can be
answering concurrently with different resolved models (the whole point of
the execution queue) -- one job's `Settings.llm = ...` could be overwritten
by another's mid-flight. Explicit construction per call sidesteps both.
"""
from __future__ import annotations

from pathlib import Path

from llama_index.core.llms import ChatMessage, ImageBlock, MessageRole, TextBlock

from models.contracts.bindings import ResolvedModel, resolve
from models.contracts.engines import get_engine
from models.contracts.roles import (
    RAG_ANSWER_ROLE,
    RAG_EMBED_ROLE,
    RAG_TRANSCRIBE_ROLE,
    VISION_GENERATE_ROLE,
)


def get_llm_for(resolved: ResolvedModel, *, request_timeout: float | None = None):
    """Return a configured LlamaIndex LLM for an explicit `resolved` binding.

    Builds via `resolved`'s engine adapter (same engine-lookup + `build_llm`
    call `get_llm()` uses) without going through `resolve()` at all — for a
    caller that already has its own `ResolvedModel` (e.g.
    `models.registry.bindings.resolve_connection`'s pk-addressed lookup),
    not the durable role binding.

    `request_timeout` (one-timeout task, 2026-09-17) is the ONE FIRING
    AUTHORITY seam: when given, it OVERWRITES `request_timeout` in the
    config handed to `build_llm` — landing BEFORE that adapter's own
    `cfg.setdefault("request_timeout", ...)` (`models.contracts.engines.
    ollama.DEFAULT_REQUEST_TIMEOUT`, 300s) ever runs, and winning over
    any `request_timeout` already present on `resolved.config` itself
    (an operator's own per-connection override). `agents.runtime.loop`
    is the caller that passes this — the SAME value it builds the turn's
    own step-budget deadline from — so the loop's own deadline check is
    the only timeout that ever fires; the engine's inner client is never
    the one that ends a turn early. `None` (the default) changes nothing:
    every other caller of `get_llm_for`/`get_llm` keeps the engine
    adapter's own default exactly as before.
    """
    engine = get_engine(resolved.engine)
    cfg = dict(resolved.config)
    if request_timeout is not None:
        cfg["request_timeout"] = request_timeout
    return engine.build_llm(resolved.model_id, resolved.endpoint, **cfg)


def get_llm(role: str = RAG_ANSWER_ROLE):
    """Return a configured LlamaIndex LLM for `role`.

    The role is resolved to an engine + model (`models.contracts.bindings.resolve`)
    and built by that engine's adapter, via `get_llm_for` — never constructed
    here directly.
    """
    return get_llm_for(resolve(role))


def describe_image(
    role: str, image_path, prompt: str, *, request_timeout: float | None = None
) -> str:
    """Ask `role`'s bound vision-capable model about ONE local image, and
    return exactly what it said — stripped, never rewritten.

    THE SHARED "ask a vision-capable model about an image file" MECHANISM
    (vision-describes-its-own-output task, fix round item 5): `tools.rag.
    extract._ask_vision` and `tools.vision.services.describe_output` had
    each hand-built this same shape independently — one `ChatMessage`,
    instruction block before the image block, verbatim-stripped answer —
    because neither column may import the other and this seam did not
    exist yet. It does now, so a THIRD column never re-derives it either.
    `tools.vision.services.describe_output` calls this; `tools.rag.
    extract` does NOT yet (a named follow-up, not part of this task —
    converging it onto this seam is its own change, reviewed on its own).

    THE PROMPT IS DELIBERATELY A PARAMETER, NEVER OWNED HERE. A reviewer
    ruled explicitly that rag's retrieval-caption prompt
    (`tools.rag.extract.DESCRIPTION_PROMPT`) and vision's own judging
    prompt (`tools.vision.services.DESCRIBE_OUTPUT_PROMPT`) are different
    things that must not converge into one shared constant — retrieval
    wants a searchable caption, judging wants a verdict against an
    unseen request. This function is MECHANISM ONLY: it has no opinion on
    what to ask, only how to ask it and how to hand back the answer.

    `request_timeout` (one-timeout task pattern, extended here): passed
    straight through to `get_llm_for`, the SAME "one firing authority"
    seam `tools.rag.tools`'s own in-turn calls already use for their
    embedder/answer-role clients. `None` (the default) leaves the engine
    adapter's own default in place, exactly like every other caller of
    `get_llm_for` that passes nothing. EVERY caller of this function
    should pass one deliberately, not rely on the default -- see `tools.
    vision.services.describe_output`'s own two callers for the two
    honest shapes (a turn's own remaining budget; a bounded module
    constant where no turn budget exists).

    `image_path`: a LOCAL file (`str` or `Path`) -- this platform is
    offline-first, so the block reads the file itself rather than
    fetching a URL (`ImageBlock(path=...)`, never `image=...`/`url=...`).

    VERBATIM: `str(response.message.content or "").strip()` is the whole
    return -- no post-processing, no re-wording. `content or ""` guards
    only a `None` content (some engines return that for a truly blank
    answer); `.strip()` then collapses a whitespace-only answer to `""`,
    which every caller already treats as "no description" (verified
    against `tools.rag.extract._ask_vision`'s identical contract, which
    this function replaces the BODY of, never the meaning).

    Raises whatever `resolve()`/`get_llm_for()`/`llm.chat()` raise --
    unbound role, engine failure, timeout -- uncaught: this is MECHANISM,
    not policy, and every caller today (`describe_output`, `tools.rag.
    extract._ask_vision`'s own future migration) already owns its own
    catch-and-degrade contract; a second one here would just be a second
    place for that policy to drift from the caller's real one.
    """
    llm = get_llm_for(resolve(role), request_timeout=request_timeout)
    message = ChatMessage(
        role=MessageRole.USER,
        blocks=[TextBlock(text=prompt), ImageBlock(path=Path(image_path))],
    )
    response = llm.chat([message])
    return str(response.message.content or "").strip()


def get_embed_model_for(resolved: ResolvedModel, *, request_timeout: float | None = None):
    """Return a configured LlamaIndex embedding model for an explicit
    `resolved` binding.

    The embeddings sibling of `get_llm_for`: builds via `resolved`'s engine
    adapter (`build_embedder`, with `resolved.config` passed straight
    through -- the same as-is construction `get_llm_for` uses, and consistent
    with `models.registry.bindings.resolved_from_connection`'s own
    construction of that `config` dict) without going through `resolve()` at
    all -- for a caller that already has its own `ResolvedModel` (e.g. a job
    handler re-resolving fresh at run time rather than trusting a queued
    snapshot).

    `request_timeout` (final review I-3, fix round 3): the embeddings
    sibling of `get_llm_for`'s own kwarg. `OllamaEmbedding` has no bare
    `request_timeout` field the way `Ollama` does -- its adapter
    (`models.contracts.engines.ollama.build_embedder`) reads a nested
    `client_kwargs={"timeout": ...}` instead -- so, when given, this
    lands there: `cfg["client_kwargs"]["timeout"] = request_timeout`
    (a COPY of `resolved.config`, and of any `client_kwargs` already in
    it, never mutating the caller's own dict), landing BEFORE that
    adapter's own `client_kwargs.setdefault("timeout", DEFAULT_REQUEST_
    TIMEOUT)` -- the identical one-firing-authority shape `get_llm_for`
    already gives the chat LLM. `None` (the default) changes nothing for
    every existing caller.
    """
    engine = get_engine(resolved.engine)
    cfg = dict(resolved.config)
    if request_timeout is not None:
        client_kwargs = dict(cfg.get("client_kwargs") or {})
        client_kwargs["timeout"] = request_timeout
        cfg["client_kwargs"] = client_kwargs
    return engine.build_embedder(resolved.model_id, resolved.endpoint, **cfg)


def get_embed_model(role: str = RAG_EMBED_ROLE):
    """Return a configured LlamaIndex embedding model for `role`.

    The role is resolved to an engine + model (`models.contracts.bindings.resolve`)
    and built by that engine's adapter, via `get_embed_model_for` — never
    constructed here directly.
    """
    return get_embed_model_for(resolve(role))


def get_image_generator_for(resolved: ResolvedModel):
    """Return an `ImageGenerator` for an explicit `resolved` binding.

    The counterpart to `get_llm_for`: builds via `resolved`'s engine adapter
    without going through `resolve()` at all, for a caller that already
    holds its own `ResolvedModel`. `tools.vision.services` uses it twice
    -- once with the binding preflight just resolved (so the row and the
    submission can never document different models) and once with the
    binding RECORDED ON A JOB (so polling a job after the role was rebound
    still asks the engine that actually has it). That is D6's promise: a
    job documents the model that ran it, not the one bound now.

    Raises `ValueError` if the engine has no `build_image_generator` --
    reachable only if an operator bound an image-generation role to a
    non-generating engine, since role options are filtered by capability.
    """
    engine = get_engine(resolved.engine)
    builder = getattr(engine, "build_image_generator", None)
    if builder is None:
        raise ValueError(
            f"Engine {resolved.engine!r} cannot generate images "
            f"(no build_image_generator); bind image generation to an engine that can."
        )
    return builder(resolved.model_id, resolved.endpoint, **resolved.config)


def get_image_generator(role: str = VISION_GENERATE_ROLE):
    """Return an `ImageGenerator` for `role` (spec §4.5).

    Same resolve-then-build path as `get_llm()`: the role resolves to an
    engine + model, and THAT engine builds the generator, via
    `get_image_generator_for` -- the gateway constructs nothing itself, so a
    second image engine is a new adapter and nothing else.
    """
    return get_image_generator_for(resolve(role))


def get_transcriber_for(resolved: ResolvedModel):
    """Return a `Transcriber` for an explicit `resolved` binding.

    The transcription sibling of `get_image_generator_for` -- byte-parallel
    on purpose (media-into-RAG plan T6): builds via `resolved`'s engine
    adapter without going through `resolve()` at all, for a caller that
    already holds its own `ResolvedModel` (e.g. a job re-resolving fresh at
    run time rather than trusting a queued snapshot -- the same reason
    `get_embed_model_for` exists).

    Raises `ValueError` if the engine has no `build_transcriber` --
    reachable only if an operator bound the transcription role to a
    non-transcribing engine, since role options are filtered by capability.
    """
    engine = get_engine(resolved.engine)
    builder = getattr(engine, "build_transcriber", None)
    if builder is None:
        raise ValueError(
            f"Engine {resolved.engine!r} cannot transcribe audio "
            f"(no build_transcriber); bind transcription to an engine that can."
        )
    return builder(resolved.model_id, resolved.endpoint, **resolved.config)


def get_transcriber(role: str = RAG_TRANSCRIBE_ROLE):
    """Return a `Transcriber` for `role` (media-into-RAG plan T6).

    Same resolve-then-build path as `get_image_generator()`: the role
    resolves to an engine + model, and THAT engine builds the transcriber,
    via `get_transcriber_for` -- the gateway constructs nothing itself, so a
    second transcribing engine is a new adapter and nothing else.
    """
    return get_transcriber_for(resolve(role))
