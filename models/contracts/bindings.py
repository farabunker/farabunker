"""
Binding resolver.

A "binding" answers the question "which engine + model backs this role right
now?" Resolution goes through an optional, dotted-path-configured
`BindingProvider` first (e.g. the DB-backed console provider), falling back
to `env_provider` — the settings-derived binding for an operator who
deliberately pinned a role from the environment (`LLM_MODEL` /
`EMBED_MODEL` / `OLLAMA_BASE_URL`), no DB required.

Nothing here invents a model. With no provider opinion and no explicit
environment override, `resolve()` raises: the role is unassigned, and every
surface says so honestly (ADR 0010 third amendment).

Kept pure on purpose: `models/contracts/` never imports `tools/` or
`models/registry/` directly. The only way `resolve()` reaches a richer provider is via the
dotted-path string in `settings.INFERENCE_BINDING_PROVIDER`, imported lazily
per call (not cached at module level) so settings changes — including in
tests — take effect immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.conf import settings
from django.utils.module_loading import import_string

from models.contracts.engines.ollama import DEFAULT_CONTEXT_WINDOW, OllamaEngine
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE


@dataclass(frozen=True)
class ResolvedModel:
    """A concrete engine + model bound to a role, ready to hand to `get_engine()`."""

    engine: str
    model_id: str
    endpoint: str
    embed_dim: int | None = None
    config: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """A short identity string for logging/caching — not a hash, just a label."""
        return f"{self.engine}:{self.model_id}:{self.embed_dim}"


def config_family(config: dict | None) -> str:
    """The declared model family (ADR 0012 D-EDIT-2) from a
    `ModelConnection.config`-shaped dict, normalized to `""` -- the
    single-file-checkpoint case -- whether the dict itself is `None`, the
    `"family"` key is simply absent, or it is PRESENT with an explicit
    JSON `null` (`{"family": None}` is a live possibility once a value has
    been stored and then cleared; `dict.get("family", "")`'s default only
    covers the absent-key case).

    ONE normalization (final-review finding 9), used by both
    `models.contracts.engines.comfyui.ComfyUIGenerator.submit` (deciding
    which graph template to build) and `tools.vision.services.
    operations_for_model` (deciding which operations an engine reports for
    the selected model) -- two call sites that used to normalize this
    fact two slightly different ways.
    """
    return (config or {}).get("family") or ""


def embed_dim_from_fingerprint(fingerprint: str) -> str:
    """The `embed_dim` segment of a `ResolvedModel.fingerprint`-shaped string
    (`f"{engine}:{model_id}:{embed_dim}"`), recovered with `rsplit(":", 1)`
    rather than a plain `split(":")` since `model_id` may itself contain
    colons (e.g. "a-chat-model:7b"). Lives right next to the format it
    parses (`ResolvedModel.fingerprint`, above) so the two can never drift
    apart --
    the one caller today is `models.registry.views._drift_severity`,
    comparing a live `ResolvedModel.embed_dim` against a stored
    `Materialization.fingerprint`'s.
    """
    return fingerprint.rsplit(":", 1)[-1]


# THE ENGINES THAT DECLARE A BOUNDED DEFAULT WINDOW OF THEIR OWN. A
# future adapter adds itself here; the function grows a lookup, never a
# special case. An engine absent from this map answers "unknown" rather
# than borrowing somebody else's number.
_ENGINE_DEFAULT_WINDOWS: dict[str, int] = {OllamaEngine.name: DEFAULT_CONTEXT_WINDOW}


def effective_context_window(resolved: ResolvedModel) -> tuple[int, str]:
    """The window this binding will ask its engine to allocate, and where
    it came from.

    `(n, "connection")`      -- an operator set `ModelConnection.context_window`.
    `(n, "engine-default")`  -- the engine adapter's own bounded default applies.
    `(0, "unknown")`         -- this engine declares no default here; say so.

    A PURE READ OF A VALUE THE PLATFORM ALREADY RESOLVED. It never probes
    an engine for an architecture maximum -- ADR 0010's own fix sentence
    forbids exactly that, and a display probe is still a probe, one per
    page render, against a machine that may be asleep. It never shows a
    model's theoretical maximum either: if the engine is asked for 8,192
    then 8,192 is the number that truncates the conversation, and showing
    a larger one would tell the reader they have room they do not have.
    It writes nothing and sends nothing.

    THE `int()` CAST AND ITS DEGRADATION are the same defence
    `tools.rag.views._resolved_answer_context_window` already records for
    its own cast: nothing enforces that a stored `context_window` is an
    integer, and an uncastable one reaching an arithmetic comparison is a
    500 on a never-500 surface. A non-positive value is treated the same
    way -- it is not a ceiling anybody could act on.

    IT ANSWERS DIFFERENTLY FROM `tools.rag.views.
    _resolved_answer_context_window`, ON PURPOSE -- a fit check may skip
    a guessed number, a display may not. `models/registry/README.md`
    records the reconciliation.
    """
    raw = (resolved.config or {}).get("context_window")
    if raw is not None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0, "unknown"
        return (value, "connection") if value > 0 else (0, "unknown")
    default = _ENGINE_DEFAULT_WINDOWS.get(resolved.engine)
    if default is None:
        return 0, "unknown"
    return default, "engine-default"


# role_key -> resolved binding, or None if this provider has no opinion on that role.
BindingProvider = Callable[[str], "ResolvedModel | None"]


def env_provider(role_key: str) -> ResolvedModel | None:
    """Resolve `role_key` from an operator's explicit environment override.

    Only knows about the two roles the core platform ships with today
    (`RAG_ANSWER_ROLE`, `RAG_EMBED_ROLE`); any other role is not this
    provider's to answer, so it returns None and lets `resolve()` raise a
    clear error.

    There are NO baked model defaults (owner ruling, ADR 0010 third
    amendment): `settings.LLM_MODEL` / `settings.EMBED_MODEL` are `None`
    unless an operator deliberately set them, and when a role's model
    setting is `None` this provider has NOTHING to say about that role --
    it returns None rather than substituting a model nobody chose. The role
    is then genuinely unassigned and `resolve()` raises, which is what the
    console's "No model assigned" state and the Ask page's cause-accurate
    503 both report.

    `settings.EMBED_DIM` is passed through as-is, including `None`: an
    override that names an embedding model but no dimension is incomplete,
    and the point of use (`tools.rag.index`) fails loudly with a clear
    error rather than guessing a width.

    No per-connection `context_window` override exists here, by
    construction: an env override has no `ModelConnection` DB record to
    hold one (there is nothing to edit in the console for it), so `config`
    is left empty and the role simply gets the engine adapter's own
    bounded default (`models.contracts.engines.ollama.DEFAULT_CONTEXT_WINDOW`)
    -- same as any other setting this provider doesn't have an opinion on.
    """
    if role_key == RAG_ANSWER_ROLE:
        if not settings.LLM_MODEL:
            return None
        return ResolvedModel(OllamaEngine.name, settings.LLM_MODEL, settings.OLLAMA_BASE_URL)
    if role_key == RAG_EMBED_ROLE:
        if not settings.EMBED_MODEL:
            return None
        return ResolvedModel(
            OllamaEngine.name,
            settings.EMBED_MODEL,
            settings.OLLAMA_BASE_URL,
            embed_dim=settings.EMBED_DIM,
        )
    return None


def resolve(role_key: str) -> ResolvedModel:
    """Resolve `role_key` to a `ResolvedModel`.

    If `settings.INFERENCE_BINDING_PROVIDER` names a callable (dotted path),
    it is imported fresh on every call and tried first; if it has no opinion
    (returns None) — or no provider is configured at all — falls back to
    `env_provider`. Raises `ValueError` if nothing resolves the role.
    """
    resolved: ResolvedModel | None = None

    if settings.INFERENCE_BINDING_PROVIDER:
        provider: BindingProvider = import_string(settings.INFERENCE_BINDING_PROVIDER)
        resolved = provider(role_key)

    if resolved is None:
        resolved = env_provider(role_key)

    if resolved is None:
        raise ValueError(f"No inference binding resolved for role {role_key!r}")

    return resolved
