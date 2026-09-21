"""Known-models catalog — enrichment data, never display copy.

Spec facts (capability, embedding dimension) for models the platform
recognizes, so an operator registering a well-known model can leave those
fields blank and have them filled in (`find()`, used by the console's
connection form and one-step role assignment). The console renders NOTHING
from this catalog: pinned model names age, so all
getting-models guidance is derived from the engine adapters instead
(`models.contracts.engines`), and the platform itself never downloads models
— operators pull them ahead of time, out of band.

The catalog is pure data; the one non-stdlib import is `CAPABILITIES`
(`models.contracts.roles`), also pure/in-memory, for `CatalogEntry`'s own
capability validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from models.contracts.roles import CAPABILITIES


@dataclass(frozen=True)
class CatalogEntry:
    """Spec facts for one known inference model.

    Fields:
        name: Human-readable model name (e.g. "Qwen 2.5 7B").
        engine: Inference engine hosting this model (e.g. "ollama").
        model_id: Full model identifier for engine (e.g. "qwen2.5:7b").
        capability: Model capability ("chat", "embeddings", or "vision").
        embed_dim: Embedding dimension for embeddings models (unused for others).
        config: Engine-specific configuration as a dict (unused in v1).
        digest: Future airlock field — content-addressed digest (unused in v1).

    Install guidance is not tracked here: it comes from each engine
    adapter's own `install_cmd_template`/`library_url`, with no pinned
    model names anywhere in the UI.
    """

    name: str
    engine: str
    model_id: str
    capability: str
    embed_dim: int | None = None
    config: dict = field(default_factory=dict)
    digest: str | None = None
    # Media types this model accepts NATIVELY in a chat message, e.g.
    # ("image",) -- the chat-attachments capability gate
    # (agents/runtime/prompt.py::native_media_types) reads it; an
    # empty tuple means text-only, which is every entry that predates
    # the field. ADDITIVE ONLY: nothing else in this column consumes it.
    accepts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITIES:
            raise ValueError(
                f"Unknown capability {self.capability!r}; must be one of {sorted(CAPABILITIES)}"
            )


# Chat models
_QWEN = CatalogEntry(
    name="Qwen 2.5 7B",
    engine="ollama",
    model_id="qwen2.5:7b",
    capability="chat",
)

_LLAMA31 = CatalogEntry(
    name="Llama 3.1 8B",
    engine="ollama",
    model_id="llama3.1:8b",
    capability="chat",
)

_PHI3 = CatalogEntry(
    name="Phi 3 Mini",
    engine="ollama",
    model_id="phi3:mini",
    capability="chat",
)

# Embedding models
_NOMIC = CatalogEntry(
    name="Nomic Embed Text",
    engine="ollama",
    model_id="nomic-embed-text",
    capability="embeddings",
    embed_dim=768,
)

_MXBAI = CatalogEntry(
    name="Mixedbread AI Embed Large",
    engine="ollama",
    model_id="mxbai-embed-large",
    capability="embeddings",
    embed_dim=1024,
)

# Vision models
_LLAVA = CatalogEntry(
    name="LLaVA 7B",
    engine="ollama",
    model_id="llava:7b",
    capability="vision",
    accepts=("image",),
)

_LLAMA32VISION = CatalogEntry(
    name="Llama 3.2 Vision 11B",
    engine="ollama",
    model_id="llama3.2-vision:11b",
    capability="vision",
    accepts=("image",),
)

_QWEN25VL = CatalogEntry(
    name="Qwen 2.5 VL 7B",
    engine="ollama",
    model_id="qwen2.5vl:7b",
    capability="vision",
    accepts=("image",),
)

CATALOG: list[CatalogEntry] = [
    _QWEN,
    _LLAMA31,
    _PHI3,
    _NOMIC,
    _MXBAI,
    _LLAVA,
    _LLAMA32VISION,
    _QWEN25VL,
]

# `find()` below is the catalog's one consumer surface (connection-form/
# role-assign enrichment).


def norm_tag(model_id: str) -> str:
    """Treat a bare model name as `name:latest` for comparison purposes.

    Engines (Ollama) always report installed models with an explicit tag,
    while catalog entries, the seed migration, and hand-typed connections
    sometimes carry a bare model_id -- normalizing is what lets
    `nomic-embed-text` and `nomic-embed-text:latest` be recognized as the
    same model. A model_id that already carries a tag is left untouched, so
    an explicit non-`latest` tag never falsely matches a different tag.

    This is the ONE canonical normalizer: `find()` below needs it,
    and `models/contracts/` must never import from `models/registry/` --
    so it lives here and discovery re-exports it for its own callers.
    """
    return model_id if ":" in model_id else f"{model_id}:latest"


def find(model_id: str) -> CatalogEntry | None:
    """Find a catalog entry by model_id, tag-normalized on both sides.

    Engines report tagged ids (`nomic-embed-text:latest`) while both
    embedding catalog entries are bare (`nomic-embed-text`), so an exact
    string compare would miss exactly the models the catalog fallback
    exists for. `norm_tag` treats a bare id as
    `:latest`; an explicitly tagged entry (e.g. `llama3.1:8b`) still only
    matches that exact tag and never a different one.

    Args:
        model_id: The model identifier to search for (bare or tagged).

    Returns:
        The matching CatalogEntry, or None if not found.
    """
    target = norm_tag(model_id)
    for entry in CATALOG:
        if norm_tag(entry.model_id) == target:
            return entry
    return None
