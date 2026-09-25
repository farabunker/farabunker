"""
Role registry.

A "role" is a named, model-consuming purpose a feature app declares against
the Inference Gateway — e.g. "rag.answer" (chat) or "rag.embed" (embeddings).
Feature apps register their roles at import time; the console layer reads
`all_roles()` / `get_role()` to let an operator assign a model to each role
without either side importing the other.

Kept pure and in-memory on purpose: no Django, no DB, no UI/serialization
fields on `RoleSpec` beyond what the manifest vocabulary (§5) needs. That
keeps a `RoleSpec` trivially manifest-serializable and keeps
`models/contracts/` free of any dependency on `tools/` or `models/registry/`.
"""
from __future__ import annotations

from dataclasses import dataclass

CAPABILITIES = {"chat", "embeddings", "vision", "image-generation", "transcription"}

# The two role keys the core platform ships bindings for out of the box
# (`models.contracts.bindings.env_provider` knows only these two -- see that
# module). Shared here, next to `CAPABILITIES`, rather than hand-typing
# "rag.answer" / "rag.embed" as bare literals in `bindings.py`, `gateway.py`,
# and the RAG module's own registration (`tools/rag/apps.py`) and
# pre-check (`tools/rag/views.py`) -- `models/contracts/` is where both
# `models/` and `tools/` (which may import `models/contracts/`, never the
# reverse) can reach a single definition.
RAG_ANSWER_ROLE = "rag.answer"
RAG_EMBED_ROLE = "rag.embed"

# The first non-RAG role (ADR 0010's own validation case for the framework),
# registered by `tools.vision` when the "vision" feature is enabled. Lives
# here beside the RAG role keys for the same reason they do:
# `models/contracts/` is the one place both `models/` and `tools/` can
# reach a single definition.
VISION_GENERATE_ROLE = "vision.generate"

# The media-ingestion roles (media-into-RAG plan, T1; ADR 0014),
# registered by `tools.rag` when the "media" feature is enabled. Lives
# here beside the role keys above for the same reason they do:
# `models/contracts/` is the one place both `models/` and `tools/` can
# reach a single definition. Two roles, not one, because transcription
# and image-text-extraction bind to different capabilities (and very
# plausibly different models).
RAG_TRANSCRIBE_ROLE = "rag.transcribe"
RAG_EXTRACT_ROLE = "rag.extract"

# The agent-conversation role, registered by the `agents` app (P2). Lives
# here beside the role keys above for the same reason they do:
# `models/contracts/` is the one place both `models/` and every feature
# column can reach a single definition, and `agents/models.py` needs it
# as a field default while `agents/apps.py` needs it to register with.
# ROADMAP:237 named this key years before it existed; it is spelled the
# same here so the roadmap line and the code agree.
CHAT_CONVERSE_ROLE = "chat.converse"

# The capability `vision.generate` answers and every image `Operation`
# declares. Shared here for the same reason the role keys above are:
# `models/contracts/` is the one place both `models/` and `tools/` can
# reach a single definition, and hand-typing the string a seventh time is
# exactly what that comment exists to prevent. (The five existing literals
# in `operations.py`, `comfyui.py`, and `apps.py` are left alone --
# retiring them is not this plan's business.)
IMAGE_GENERATION_CAPABILITY = "image-generation"

# The capability a role must answer to back a CHAT agent, shared here for the
# same reason the role keys above are -- see `chat_capable_roles` below, whose
# two callers sit in two different columns that may not import each other.
CHAT_CAPABILITY = "chat"


@dataclass(frozen=True)
class RoleSpec:
    """A model-consuming role a feature app declares.

    `rematerialize` is a dotted-path string to a callable the console can
    resolve lazily (e.g. to rebuild an index after a model change) — it is
    never a live callable, so a `RoleSpec` stays plain data.
    """

    key: str
    label: str
    capability: str  # one of CAPABILITIES above (§5 manifest vocabulary)
    rematerialize: str | None = None

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITIES:
            raise ValueError(
                f"Unknown capability {self.capability!r}; must be one of {sorted(CAPABILITIES)}"
            )


_ROLES: dict[str, RoleSpec] = {}


def register_role(spec: RoleSpec) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.

    Idempotent: registering the same key again simply overwrites the prior
    entry, so re-importing a module that calls this at import time is safe.
    """
    _ROLES[spec.key] = spec


def all_roles() -> list[RoleSpec]:
    """Return all registered roles, in registration order."""
    return list(_ROLES.values())


def get_role(key: str) -> RoleSpec | None:
    """Return the registered role for `key`, or None if not registered."""
    return _ROLES.get(key)


def chat_capable_roles() -> tuple[tuple[str, str], ...]:
    """`((key, label), ...)` -- every registered role that can back a CHAT
    agent, in key order.

    ONE VOCABULARY, TWO COLUMNS, HERE BECAUSE NEITHER MAY IMPORT THE OTHER.
    An agent's `llm_role` is answered in two places: the FORM decides what to
    offer (`agents.chat.agentform.chat_role_options`, spec 4.4) and the WRITER
    decides what to accept (`agents.visibility._validated_agent_fields`).
    `agents/visibility.py` may not import `agents.chat` (import law), so a
    single filter reachable from both has to live below both -- and
    `models/contracts/` is that place, exactly as it is for the role KEYS
    above. Two spellings of "which roles are chat roles" would be a form that
    offers what the writer rejects, which is the render-vs-gate bug in its
    other direction.

    A TUPLE OF PAIRS, not `RoleSpec` objects: the form renders `(value, label)`
    straight into a `<select>`, and the writer only needs the keys. Returning
    plain data keeps both callers free of this module's dataclass.
    """
    return tuple(sorted((role.key, role.label) for role in _ROLES.values()
                        if role.capability == CHAT_CAPABILITY))
