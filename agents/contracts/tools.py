"""The tool contract: what a tool IS, what a runner is called with, and
what it hands back.

Deliberately NOT a class hierarchy. Every sibling registry in this
codebase -- `RoleSpec` (models/contracts/roles.py:69), `Operation`
(operations.py:106), `JobKind` (jobkinds.py:175) -- is a frozen dataclass
with dotted-path STRINGS for its callables, specifically so registration
never imports the implementing module. `tools/vision/apps.py:51-55` says
so in a code comment. A base class would force that import at `ready()`
time and break the exact property those docstrings exist to protect.

Pure: no Django, no database, no I/O. Rule 1 of the import law -- any
column may import this, in any direction.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from models.contracts.operations import Param, _describe_param, validate_params

logger = logging.getLogger(__name__)

# `_describe_param` is a deliberate reuse of a private name from
# `models.contracts.operations` -- `describe_tool` below reuses it for the
# `params` list rather than growing a third copy of the param-serialisation
# rule (`operations.describe` and `operations._describe_param` are the
# other two).

if TYPE_CHECKING:  # pragma: no cover -- annotation only, never imported at runtime
    # `models/contracts/jobkinds.py:34` imports `django.utils.module_loading`
    # at module scope. Importing `JobContext` for real would therefore drag
    # Django into this rule-1 pure leaf and break its universal
    # importability -- `agents/contracts/tests/test_purity.py` fails on
    # exactly that. `ToolContext.job` is an annotation-only field and
    # `from __future__ import annotations` is in force above, so the name
    # is never evaluated at runtime and this costs nothing.
    from models.contracts.jobkinds import JobContext
    # Same rule-1 purity rationale for `ToolContext.stream`:
    # `agents.contracts.workstreams` is itself a pure leaf (no Django), so
    # importing it for real would cost nothing -- but annotation-only
    # keeps this module symmetric with `job` above and needs no runtime
    # import either way.
    from agents.contracts.workstreams import WorkstreamScope

# Param kinds a tool may declare. "file" is excluded: a tool call is JSON
# (ADR 0012:140 -- "future chatbot tool call is JSON, so neither can carry
# an upload object"), so an image input is an artifact REFERENCE (see
# agents/contracts/artifacts.py), never an upload object.
TOOL_PARAM_KINDS = frozenset({"text", "int", "float", "choice", "seed", "asset"})

# The image tool's dotted key, spelled ONCE for the whole agents column
# (packet finding B, 2026-09-17). TWO call sites need it and neither may
# import the other: `agents.runtime.loop` narrows the tool's per-turn
# spec by this key, and `agents.runtime.prompt` decides whether to offer
# the attached-image steering clause by it -- and `loop` already imports
# `prompt`, so a constant living in either is a cycle from the other.
#
# A LITERAL HERE, NOT AN IMPORT FROM `tools.vision.tools`: `agents/` may
# not import `tools/` at all (import-law rule 3), which is the same
# reason `agents.contracts.artifacts._URL_NAMES` spells two vision URL
# names as literals one module over. The agreement with vision's own
# `VISION_GENERATE_TOOL_KEY` is enforced by a test that imports across
# columns -- a test may, production may not.
VISION_GENERATE_KEY = "vision.generate"


class ToolRefused(ValueError):
    """A tool call this platform will not run at all: the tool is not
    granted to this agent, the delegation depth cap is reached, or the
    tool mutates existing state and Identity & Auth has not landed
    (ADR 0010:266-276).

    Distinct from `ParamError` because the recovery policy differs:
    section 10.1 gives a `ParamError` exactly ONE retry (the model was
    told precisely what was wrong and can fix it) and a refusal NONE
    (nothing the model can say will change the answer).

    Nothing catches this until P2's `invoke_tool`. It is declared here,
    now, because a runner author needs to know which exception means
    which, and this module is the only place that can say so.
    """


# MOVED (Identity & Auth, IA-1). `Principal` is no longer an agents-
# column detail -- it is the base identity type of the whole platform --
# so it lives in `identity/contracts/principals.py`, a rule-1 pure leaf
# every column may import. Imported here, never re-exported: every call
# site names its real home, so there is one import path to grep for and
# the AST guard in `foundation/ops/tests/test_import_law.py` has one
# place to look.
from identity.contracts.principals import Principal  # noqa: F401 -- ToolContext's type


@dataclass(frozen=True)
class ToolSpec:
    """One tool an agent may call.

    `runner` is a dotted-path string to `callable(args: dict, ctx:
    ToolContext) -> ToolResult`, resolved lazily by
    `models.contracts.jobkinds.resolve_dotted_path` -- never a live
    callable, so a `ToolSpec` stays plain data and registration imports
    nothing, exactly like `JobKind.handler`.

    `roles` is the set of `RoleSpec.key`s this tool's runner may consume.
    The agent-turn planner unions these across an agent's granted tools
    and declares the result to the queue at enqueue time; a tool that
    consumes no model declares `()`.

    `mutates` is True when the tool changes state that ALREADY EXISTS --
    configuration, a role binding, a platform setting, or
    previously-ingested content. Creating new work product (a generated
    image, a queued job, a conversation turn) is not `mutates`. A
    `mutates=True` tool is registered but NOT grantable until Identity &
    Auth lands (ADR 0010:266-276).

    `describer` is an INERT RESERVED FIELD (ruling R3). It is a dotted
    path to `callable(spec, resolved) -> dict` returning
    `describe_tool(spec)` with per-param live facts merged in, for the
    one constant vision input screen designed in the spec's section 4.8.
    NOTHING in P1-P4 calls it: `openai_tool_dict` does not consult it and
    `describe_tool` emits no live-facts keys. Two reasons -- a per-prompt
    describer call means an engine round trip inside the turn loop, which
    is the unbounded-latency cost ruling R2 rejects; and a tool schema
    that silently changes shape between turns is a debugging nightmare
    for no v1 benefit. The field exists now only so that work is a
    fill-in rather than a contract change. A string, not a callable, for
    the same reason `runner` is one.
    """

    key: str                          # "rag.search" -- namespace is the owning column
    label: str                        # operator-facing
    description: str                  # what the LLM reads; the whole prompt surface of a tool
    params: tuple[Param, ...] = ()
    roles: tuple[str, ...] = ()
    runner: str = ""
    mutates: bool = False
    describer: str = ""

    def __post_init__(self) -> None:
        if not self.key or " " in self.key:
            raise ValueError(
                f"Tool key must be a non-blank, space-free string, got {self.key!r}"
            )
        if "__" in self.key:
            # `wire_name` maps "." -> "__" and `key_from_wire_name` maps
            # back (toolschema.py). A key that already contains "__"
            # makes that round trip ambiguous, so it is a definition-time
            # error, not a runtime surprise on the one tool call that
            # happens to hit it.
            raise ValueError(
                f"Tool key must not contain '__' (wire-name collision): {self.key!r}"
            )
        if not self.runner:
            raise ValueError(f"Tool {self.key!r} declares no runner")
        for param in self.params:
            if param.kind not in TOOL_PARAM_KINDS:
                raise ValueError(
                    f"Tool {self.key!r} param {param.key!r}: kind {param.kind!r} is not "
                    f"usable in a JSON tool call; must be one of {sorted(TOOL_PARAM_KINDS)}"
                )


@dataclass(frozen=True)
class ToolResult:
    """What a runner returns.

    `text` is what goes back to the LLM as the tool message -- the only
    part the model ever sees. `data` is JSON-safe structured payload for
    the UI (RAG citations land in `data["citations"]`, in the exact dict
    shape `tools/rag/retrieval.py:325-339` already produces).
    `artifacts` are JSON-safe reference strings (see
    `agents/contracts/artifacts.py`) -- never filesystem paths, never
    bytes.
    """

    text: str = ""
    data: dict = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()


class StepBudget:
    """The one mutable object in this module: a turn's remaining
    allowance, SHARED by every tool call and every delegated sub-agent in
    that turn.

    Mutable by design -- steps are spent. `ToolContext` stays frozen and
    holds a reference, the same shape `JobContext` uses for its injected
    closures (models/contracts/jobkinds.py:118-121).
    """

    def __init__(
        self, *, steps: int, deadline_monotonic: float, recoveries: int = 1
    ) -> None:
        self.steps_left = steps
        self.deadline_monotonic = deadline_monotonic
        self.recoveries_left = recoveries

    def spend(self, n: int = 1) -> None:
        """Spend `n` steps. Never clamps at zero: a caller that overspends
        should see how far past the line it went, not a tidied-up zero."""
        self.steps_left -= n

    @property
    def exhausted(self) -> bool:
        return self.steps_left <= 0

    @property
    def expired(self) -> bool:
        """Wall-clock, monotonic. Checked at the top of every loop
        iteration and before every tool call; expiry ends a turn the same
        way exhaustion does."""
        return time.monotonic() >= self.deadline_monotonic


@dataclass(frozen=True)
class ToolAccess:
    """What the ACTING principal may call, as plain data.

    `granted_tools` lives in this rule-1 pure leaf and therefore CANNOT
    query `agents.models.ToolEntitlement`. The facts arrive as data
    instead, built once per turn by `agents.entitlements.tool_access_for`
    -- an `agents` module, which may import `identity.access` through the
    named seam and its own models freely.

    `required` maps a tool key -> the entitlement ids that LABEL it. A
    key ABSENT from this mapping is unlabelled, and therefore callable.
    `held` is the entitlement ids the acting principal holds.
    `unrestricted` short-circuits the whole check: open posture, or a
    principal that sees everything.

    IT DEFAULTS TO UNRESTRICTED, deliberately, so that
    `UNRESTRICTED_TOOL_ACCESS` is the zero-argument answer and every
    existing call site and every test that does not care keeps working
    unchanged. A fail-OPEN default is safe here and only here, because
    the one function that consumes it is called with an explicit value
    on every production path (`agents/runtime/{jobs,loop,preflight}.py`),
    and those three call sites are pinned by tests.
    """

    required: Mapping[str, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, key: str) -> bool:
        if self.unrestricted:
            return True
        needed = self.required.get(key)
        return not needed or bool(needed & self.held)


UNRESTRICTED_TOOL_ACCESS = ToolAccess()


@dataclass(frozen=True)
class ToolContext:
    """The second argument every tool runner is called with.

    `principal` is WHO is calling (2026-08-27 addendum, consequence 1).
    It replaced P1's bare `agent_key` string because a grant attaches to
    a principal, not to an agent: an external MCP `tools/call` has no
    agent at all and must arrive through this same field.

    `tool_key` is WHICH registered spec is being run, set by
    `invoke_tool` immediately before it calls the runner and blank
    otherwise. Needed because several specs may share one runner --
    `agent.<slug>` registers one spec per code-declared resident and
    they all run `agents.runtime.delegate.run_agent_tool` -- and a
    runner's signature is `(args, ctx)`, so there is nowhere else for it
    to learn its own identity from.

    `job` is this turn's `models.contracts.jobkinds.JobContext` -- the
    runner's only progress-reporting path, and the same object the
    vision job handler already threads into `services.wait_for`'s
    `on_poll` (`tools/vision/jobs.py:308-336`). Annotated, never
    imported at runtime (see the `TYPE_CHECKING` block above):
    `jobkinds` imports Django, and this module must not.

    `supplied_keys` is the RAW argument keys `invoke_tool` received,
    BEFORE `validate_tool_args` filled in a single default (CQ-1 fix
    round 1). Set alongside `tool_key`, from the same `replace()` call,
    immediately before the runner is invoked. A runner whose schema is a
    UNION across several real targets (`tools.vision.tools.run_generate`
    is the one that exists today) cannot otherwise tell "the caller sent
    this value" from "validation filled this in", because the dict it
    receives -- `invoke_tool`'s own validated `clean` -- carries a value
    for every declared param either way. Empty for a runner called
    directly (every test that builds a bare `ToolContext` and skips
    `invoke_tool`), which such a runner reads as "fall back to my own
    `args`' keys" rather than "the caller supplied nothing" -- `args`
    always carries at least one key in production (`ToolSpec.params`
    requiring at least one field is the common case), so the empty set
    is otherwise never a real answer.

    `agent_slug` (the acting rule, part 1) is WHOSE TOOL
    DECLARATION IS IN FORCE -- a second, separate fact from `principal`
    (WHO THIS IS BEING DONE FOR). A turn runs as the USER and a delegate
    inherits the root user's `principal` while carrying its OWN
    `agent_slug` -- that pair is the whole of "an agent is never a way
    around labels" (the runtime reads it back elsewhere; this field
    only makes it exist and travel).
    """

    conversation_id: str          # UUID as a string; "" for a call with no conversation
    principal: Principal
    depth: int                    # 0 at the top level; +1 per agent-as-tool hop
    budget: StepBudget
    job: JobContext
    tool_key: str = ""
    supplied_keys: frozenset[str] = frozenset()
    # THE TURN'S STREAM SCOPE, or `None` for a loose turn and for every
    # caller with no conversation at all (an external MCP `tools/call`).
    # Defaulting to `None` is what keeps every existing runner and every
    # test that builds a bare context working -- and the behaviour they
    # get is the SAFE one, because `None` excludes contained documents
    # rather than including them.
    #
    # A DELEGATE INHERITS IT, the same way it inherits `principal` and
    # `tool_access` and for the same reason: a delegate is not a way
    # around a wall any more than it is a way around a label. The
    # instructions PROSE does not travel (author decision 21) and the
    # enforcement does, which spec §24 concern 2 records as a named
    # asymmetry.
    stream: "WorkstreamScope | None" = None
    # WHOSE TOOL DECLARATION IS IN FORCE, as distinct from `principal`,
    # which is WHO THIS IS BEING DONE FOR. Both facts used to be crammed
    # into `principal`, which is exactly why the acting rule -- a turn
    # runs as the USER, and a delegate inherits the root user -- could
    # not be expressed: there was one field and two questions.
    #
    # Blank for a runner called directly and for a caller with no agent
    # at all (the future MCP edge).
    #
    # `agents.models.ToolInvocation` gains a matching column IN IA-2,
    # with `invoke_tool` as its writer. It is NOT added here: IA-1 ships
    # exactly four migrations (spec section 17), and a column with no
    # writer would be a schema change nobody could point at a behaviour
    # for.
    agent_slug: str = ""
    # THE ROOT ACTOR'S TOOL ACCESS, carried so a delegate REUSES it
    # rather than rebuilding one for the sub-agent it is about to run
    # (`agents.runtime.delegate.run_agent_tool`). That is the whole of
    # "an agent is never a way around labels": there is no hop at which
    # the acting principal widens, and there is no hop at which its
    # access is recomputed from a different subject.
    #
    # Unrestricted by default, so a runner called directly in a test
    # behaves exactly as it did before this phase.
    tool_access: ToolAccess = UNRESTRICTED_TOOL_ACCESS


def validate_tool_args(spec: ToolSpec, raw: dict) -> dict:
    """Coerce and range-check `raw` against `spec.params`.

    Raises `models.contracts.operations.ParamError`, whose `.errors` maps
    a param key to a human-readable reason (operations.py:226-236) --
    which is what makes a bad tool call the ONE failure worth handing
    back to the model for a retry (section 10.2).

    One line, deliberately: `validate_params` is the schema FLOOR every
    caller shares (ADR 0012:676-679), and `ToolSpec` satisfies its
    `HasParams` protocol. A second validator here would be exactly the
    parallel seam that ADR rules out.
    """
    return validate_params(spec, raw)


_TOOLS: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.

    Idempotent, like `register_role` (roles.py:92) and
    `register_job_kind` (jobkinds.py:243): registering the same key again
    simply overwrites the prior entry, so re-importing a module that
    registers at import time is safe.
    """
    _TOOLS[spec.key] = spec


def all_tools() -> list[ToolSpec]:
    """Every registered tool, in registration order."""
    return list(_TOOLS.values())


def get_tool(key: str) -> ToolSpec:
    """The tool registered under `key`.

    RAISES `ValueError` naming it if absent -- matching `get_job_kind`
    (jobkinds.py:258), not `get_role` (roles.py:106). A role may
    legitimately be absent; a tool call for an unregistered tool is a
    caller bug, not a state to degrade gracefully from.

    A caller for whom an absent key is NORMAL -- P2's prompt builder,
    which drops an unregistered granted key rather than crashing -- uses
    `_TOOLS.get(key)` instead, deliberately.
    """
    try:
        return _TOOLS[key]
    except KeyError:
        raise ValueError(f"Unknown tool: {key!r}") from None


def grantable_tools() -> list[ToolSpec]:
    """Every registered tool with `mutates=False` -- the set an agent row
    may name in its `tool_keys`.

    ADR 0010:266-276: a settings-mutating tool is registered (so it is
    visible, documented, and testable) but must not be grantable before
    Identity & Auth.
    """
    return [spec for spec in _TOOLS.values() if not spec.mutates]


def granted_tools(principal: Principal, tool_keys: Sequence[str],
                  access: ToolAccess = UNRESTRICTED_TOOL_ACCESS) -> list[str]:
    """Which of `tool_keys` this `principal` may actually call, in the
    caller's own order, de-duplicated.

    The ONE function that decides availability. THREE DROPS:

    - a key absent from the registry is DROPPED and logged. Normal, not
      exceptional: a feature-gated tool with its flag off, or a tool that
      ships in a later phase.
    - a key whose registered spec is `mutates=True` is DROPPED, silently.
      ADR 0010's rule that a settings-mutating tool is registered but not
      grantable stays in force through IA-2 and is lifted in a later
      phase, not here.
    - a key `access` does not permit is DROPPED and logged AT DEBUG,
      because on a labelled install it is the NORMAL case and an info
      line per turn per tool would drown the log.

    `tool_keys` IS A DECLARATION, NOT A GRANT (IA-2). It is what this
    agent WANTS to be able to do; the grant is `agents.models.
    ToolEntitlement` plus the acting user's entitlements, arriving here
    as `access`. ADR 0015 section 9 said this argument would DISAPPEAR
    when grants moved to their own table; owner decision 13 keeps the
    declaration, so the argument survives and its MEANING changed. The
    ADR carries the amendment.
    """
    out: list[str] = []
    seen: set[str] = set()
    for key in tool_keys:
        if key in seen:
            continue
        seen.add(key)
        spec = _TOOLS.get(key)
        if spec is None:
            logger.info(
                "agents: %s %r was granted %r, which is not registered on this "
                "install; dropped from its tool list.",
                principal.kind, principal.key, key,
            )
            continue
        if spec.mutates:
            continue
        if not access.allows(key):
            logger.debug(
                "agents: %s %r does not hold an entitlement for %r; dropped from "
                "its tool list.", principal.kind, principal.key, key,
            )
            continue
        out.append(key)
    return out


def describe_tool(spec: ToolSpec) -> dict:
    """`spec` as plain, JSON-safe data.

    Written out explicitly -- one key per field -- rather than via
    `dataclasses.asdict`, so a field added without a serialization
    decision fails a test instead of silently appearing in a public
    contract. That is the exact rationale
    `models/contracts/operations.py:203-211` gives for `_describe_param`,
    which this reuses for the `params` list rather than growing a second
    copy of the param-serialisation rule.

    `runner` and `describer` are INTERNAL and deliberately unserialized:
    one is a dotted path into this codebase, the other is reserved
    (section 4.8) -- both are implementation, and neither belongs in a
    contract handed to an LLM, an HTTP client, or a future MCP surface.
    The one-key-per-serialized-field test therefore asserts their
    ABSENCE, not their presence.

    In P1-P4 this output carries NO live-facts keys. Section 4.8's
    `supported` / `unsupported_reason` / `options` are reserved for
    R2/Phase 1.55 and are unreachable now: nothing calls a `describer`,
    so nothing can produce them (ruling R3).
    """
    return {
        "key": spec.key,
        "label": spec.label,
        "description": spec.description,
        "roles": list(spec.roles),
        "mutates": spec.mutates,
        "params": [_describe_param(p) for p in spec.params],
    }
