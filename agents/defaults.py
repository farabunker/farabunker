"""The shipped-defaults catalogue (ruling 2, 2026-08-28) -- what this
platform OFFERS, never what it has installed.

PURE DATA plus ONE row writer. No Django import of any kind at MODULE
SCOPE -- not `django.db`, not `agents.models` -- so `Flow.save()` and
anything `AppConfig`-adjacent can import the declarations without
paying for the app registry. `install_default`, the one function that
writes a row, imports Django INSIDE its own body, the same discipline
`agents/resident.py`'s retired `sync_resident_agents` used to take the
model CLASS as an argument for.

RULING 2, and it is the ruling that changes how this platform treats
the operator's database: **no code path writes a row the operator did
not ask for.** `DEFAULT_AGENTS` (moved here from `agents/resident.py`,
renamed from `RESIDENT_AGENTS`) and `DEFAULT_FLOWS` describe what a
person may adopt; `install_default(kind, slug, principal)` is the ONE
create-if-absent, idempotent way a row comes from one of them, and
`reset=True` is the ONE path that ever rewrites an existing row.
`manage.py sync_agents` -- which re-applied every declaration to its
row on every deploy -- is retired with the model it enforced.

THE CATALOGUE IS NOT A REGISTRY. A `FlowSpec` is used for exactly two
things: rendering the "Add the default X" offer, and validating row
JSON (`validate_flow_json`, shared with `Flow.save()`). Nothing resolves
a flow through it at run time -- `flow.run` loads the ROW.
Nothing here calls `register_tool`, and `agents/apps.py::ready()` never
imports this module for that purpose (it reaches `DEFAULT_AGENTS`
through `agents/resident.py::agent_tool_specs`, which stays the one
code-driven exception ruling 1's own docstring names).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agents.limits import MAX_STEPS_DEFAULT
from models.contracts.roles import CHAT_CONVERSE_ROLE

logger = logging.getLogger(__name__)


class FlowDeclarationError(ValueError):
    """A flow's declared `inputs`/`steps` do not have the shape
    `flow.run` can execute.

    Raised by `validate_flow_json`, the ONE validator `FlowSpec.
    __post_init__` and `Flow.save()` both call. A row and a shipped
    default checked by two different rules would be two different
    things wearing one name, and the row is the thing that actually
    runs.
    """


# --- The reference grammar a flow step's `args` may use -------------------
#
# `$input.<key>` names a declared `FlowInput`. `$steps.<N>.<field>` names
# a PRECEDING step's `ToolResult` field -- `text`, `data`, or
# `artifacts`, `agents.contracts.tools.ToolResult`'s own three fields,
# exactly the fields a runner can hand back. An optional dotted tail
# (`.citations`, `.0`) reaches inside `data`/`artifacts` at RUN time;
# this validator checks only that the reference resolves to a real,
# preceding step and a real field, never the shape of what that field
# will hold. Spelled `$steps.<N>` -- plural, dotted before the index --
# per the spec (`docs/superpowers/specs/2026-08-25-agents-and-tools-
# design.md:1490-1504,2144`), the binding authority for this grammar.
#
# A string is treated as an ATTEMPTED reference only when it starts with
# "$input" or "$steps" -- anything else starting with "$" ("$5.00") is a
# plain value nobody is asking this validator to parse. Refusing every
# "$"-prefixed string would make a whole class of legitimate argument
# (a price, a currency amount) undeclarable.
_INPUT_REF = re.compile(r"^\$input\.([A-Za-z_][A-Za-z0-9_]*)$")
_STEP_REF = re.compile(r"^\$steps\.(\d+)\.(text|data|artifacts)(?:\.[A-Za-z0-9_]+)*$")


@dataclass(frozen=True)
class AgentSpec:
    """One code-declared, shippable default agent.

    Moved here from `agents/resident.py` (ruling 2, 2026-08-28) and
    renamed from nothing -- the class name is unchanged, only its home
    and what surrounds it. `as_tool` exposes this agent to OTHER agents
    as an `agent.<slug>` tool (`agents/resident.py::agent_tool_specs`).
    Only entries in `DEFAULT_AGENTS` can be exposed that way: those
    specs are registered in `AppConfig.ready()`, which may not touch the
    database, so a row-declared (non-default) agent has no way in. A
    named deferral, not an oversight (see `agents/README.md`'s
    P3-G1 note).
    """

    slug: str
    name: str
    description: str
    system_prompt: str
    tool_keys: tuple[str, ...]
    llm_role: str = CHAT_CONVERSE_ROLE
    max_steps: int = MAX_STEPS_DEFAULT
    as_tool: bool = False


@dataclass(frozen=True)
class FlowInput:
    """One value a flow's caller must (or may) supply."""

    key: str
    label: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class FlowStep:
    """One step of a flow: a tool key and its arguments, some of which
    may be `$`-prefixed references (see the grammar above)."""

    tool: str
    args: dict = field(default_factory=dict)


def _flow_input_dict(item) -> dict:
    """`item` as a plain dict, whether it arrived as a `FlowInput` (a
    `FlowSpec`'s own declaration) or as JSON off a `Flow` row."""
    if isinstance(item, FlowInput):
        return {
            "key": item.key, "label": item.label,
            "description": item.description, "required": item.required,
        }
    if isinstance(item, dict):
        return item
    raise FlowDeclarationError(
        f"a flow input must be a dict or FlowInput, got {item!r}"
    )


def _flow_step_dict(item) -> dict:
    """`item` as a plain dict, whether it arrived as a `FlowStep` (a
    `FlowSpec`'s own declaration) or as JSON off a `Flow` row."""
    if isinstance(item, FlowStep):
        return {"tool": item.tool, "args": item.args}
    if isinstance(item, dict):
        return item
    raise FlowDeclarationError(
        f"a flow step must be a dict or FlowStep, got {item!r}"
    )


def parse_flow_json(
    inputs: list, steps: list, *, label: str = "this flow"
) -> tuple[list[FlowInput], list[FlowStep]]:
    """Shape-check `inputs`/`steps` and parse them into `FlowInput`/
    `FlowStep` dataclasses. The ONE parser, shared by `validate_flow_json`
    (below -- a thin wrapper that discards the return value) and by
    `agents.runtime.flow._run_steps`, which parses a `Flow`
    ROW's JSON through this exact function before running it. A row and
    a shipped default are then read by IDENTICAL code, which is the only
    way "the same rules apply to both" stays a fact about the code
    rather than a claim about it.

    Checks SHAPE only:

    - at least one step;
    - no step names an `agent.*` or `flow.*` tool key -- the recursion
      guard, closed at DECLARATION time. A flow calling an agent that
      could call the flow back is a cycle the shared step budget would
      only stop after spending it; refusing the shape outright is
      cheaper and cannot be raced.
    - every `$`-prefixed reference that LOOKS like an attempted
      reference (starts with `$input` or `$steps`) is syntactically
      well-formed and resolves BACKWARDS -- `$input.<key>` names a
      declared input, `$steps.<N>.<field>` names a field of a step that
      PRECEDES the one referencing it (spec section 7.4: "resolvable
      against the steps that precede it");
    - a reference may live anywhere in a step's `args` -- directly, or
      nested inside a list or a dict;
    - a `$`-prefixed string that does not look like an attempted
      reference (`"$5.00"`) is a VALUE, not a reference, and is left
      alone -- a validator that refused it would make a whole class of
      legitimate argument undeclarable.

    Whether a step's tool is REGISTERED on this install is not checked
    here -- that is an install fact, tolerated at run time exactly as
    `Agent.tool_keys` tolerates an unregistered key (ruling R1): a
    vision step on a box with the vision flag off is a not-here, not a
    fault.
    """
    input_dicts = [_flow_input_dict(item) for item in inputs]
    step_dicts = [_flow_step_dict(item) for item in steps]

    if not step_dicts:
        raise FlowDeclarationError(f"{label}: a flow must declare at least one step")

    declared_keys: set[str] = set()
    parsed_inputs: list[FlowInput] = []
    for entry in input_dicts:
        key = entry.get("key")
        if not key or not isinstance(key, str):
            raise FlowDeclarationError(
                f"{label}: every input needs a non-blank string key, got {entry!r}"
            )
        declared_keys.add(key)
        parsed_inputs.append(FlowInput(
            key=key,
            label=entry.get("label", ""),
            description=entry.get("description", ""),
            required=bool(entry.get("required", False)),
        ))

    parsed_steps: list[FlowStep] = []
    for index, entry in enumerate(step_dicts):
        tool = entry.get("tool")
        if not tool or not isinstance(tool, str):
            raise FlowDeclarationError(f"{label}: step {index} names no tool")
        if tool.startswith("agent.") or tool.startswith("flow."):
            raise FlowDeclarationError(
                f"{label}: step {index} names {tool!r} -- a flow step may not call "
                f"an agent or another flow (the recursion guard, closed at "
                f"declaration time)"
            )
        args = entry.get("args", {})
        if not isinstance(args, dict):
            raise FlowDeclarationError(f"{label}: step {index}'s args must be a dict")
        _validate_refs(args, index=index, declared_keys=declared_keys, label=label)
        parsed_steps.append(FlowStep(tool=tool, args=args))

    return parsed_inputs, parsed_steps


def validate_flow_json(inputs: list, steps: list, *, label: str = "this flow") -> None:
    """Shape-check `inputs`/`steps`. The ONE validator, called by
    `FlowSpec.__post_init__` AND by `Flow.save()` -- a row and a shipped
    default are checked by the same rule, and the row is the thing that
    actually runs.

    A thin wrapper over `parse_flow_json`: this function's whole contract
    is "raises on a bad shape, returns nothing on a good one", so it
    parses and discards. `agents.runtime.flow._run_steps` is the caller
    that wants the parsed `FlowInput`/`FlowStep` dataclasses back, not
    this one.
    """
    parse_flow_json(inputs, steps, label=label)


def _validate_refs(value, *, index: int, declared_keys: set[str], label: str) -> None:
    """Walk `value` -- a step's `args`, or anything nested inside it --
    and validate every string leaf that looks like an attempted `$`
    reference."""
    if isinstance(value, dict):
        for nested in value.values():
            _validate_refs(nested, index=index, declared_keys=declared_keys, label=label)
    elif isinstance(value, list):
        for nested in value:
            _validate_refs(nested, index=index, declared_keys=declared_keys, label=label)
    elif isinstance(value, str):
        _validate_ref_string(value, index=index, declared_keys=declared_keys, label=label)


def _validate_ref_string(value: str, *, index: int, declared_keys: set[str], label: str) -> None:
    if not (value.startswith("$input") or value.startswith("$steps")):
        return  # not an attempted reference -- e.g. "$5.00" is a value

    input_match = _INPUT_REF.match(value)
    if input_match:
        key = input_match.group(1)
        if key not in declared_keys:
            raise FlowDeclarationError(
                f"{label}: step {index} references undeclared input {key!r}"
            )
        return

    step_match = _STEP_REF.match(value)
    if step_match:
        referenced = int(step_match.group(1))
        if referenced >= index:
            raise FlowDeclarationError(
                f"{label}: step {index} references step {referenced}, which does "
                f"not precede it -- a reference must resolve backwards"
            )
        return

    raise FlowDeclarationError(f"{label}: step {index} has a malformed reference {value!r}")


@dataclass(frozen=True)
class FlowSpec:
    """One code-declared, shippable default flow.

    Checked by the SAME validator a `Flow` row is (`__post_init__`
    calls `validate_flow_json`), so a catalogue entry that could not
    become a legal row is a declaration-time error, never a surprise at
    install time.
    """

    slug: str
    name: str
    description: str
    inputs: tuple[FlowInput, ...]
    steps: tuple[FlowStep, ...]

    def __post_init__(self) -> None:
        validate_flow_json(list(self.inputs), list(self.steps))


# THREE agents, not the spec's four -- moved verbatim from
# `agents/resident.py`'s retired `RESIDENT_AGENTS` (ruling 2, 2026-08-28).
# The owner's P2 direction dropped `critic` (the self-delegation worked
# example) and named the RAG one `library` rather than `librarian`.
DEFAULT_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        slug="general",
        name="General assistant",
        description="Answers with the library, image generation and model status.",
        # ROUND 19 (owner, verbatim: "the system prompt seems to be overly
        # restrictive... if I ask it for some python code, it shouldn't
        # reject my request... if it can't be supported by the rag
        # framework[, it] shouldn't be held down so tight it doesn't give
        # any response to basic questions"). RAG-FIRST, NOT RAG-ONLY: this
        # SHIPPED text is what `manage.py install_defaults --reset general`
        # restores, and it SUPERSEDES both this prompt's own pre-round-19
        # wording (which never actually told the model to refuse anything --
        # it just never told it NOT to) and any ad-hoc tuning block an
        # operator may have appended to their own row by hand on a live box
        # (checked: the preview row carried no such block; this reset is the
        # sanctioned way to clear one if a live box does).
        system_prompt=(
            "You are the general assistant on a private, offline-first box. You have "
            "tools for searching the operator's own document library, for looking at "
            "and generating images, and for reporting which models this box currently "
            "has assigned to each role.\n\n"
            "RAG-FIRST: when a question plausibly concerns the document library, an "
            "attachment, or this workstream's own material, search first and ground "
            "your answer in what you find, with citations.\n\n"
            "NOT RAG-ONLY: finding nothing in the library is never a reason to refuse "
            "a benign request. Writing code, explaining a concept, drafting text, and "
            "everyday reasoning need no search at all -- answer them directly. When a "
            "search was warranted and came back empty, say so in one honest line, "
            "then answer from your own general knowledge, plainly not attributed to "
            "any document.\n\n"
            "Do not describe calling a tool instead of calling it, and do not invent "
            "a result or a citation. If a tool fails, say what failed and what you "
            "can still do.\n\n"
            "Two specialist agents are available to you as tools: one that answers "
            "only from the document library, and one that turns a description into an "
            "image. Delegate when the whole request belongs to one of them; do the "
            "work yourself when it does not.\n\n"
            "You can also run a saved flow: a fixed sequence of steps this box "
            "already has set up for a common request. Use one when it matches what "
            "was asked; do the steps yourself when it does not quite fit."
        ),
        # rag.ingest is deliberately absent: it is the one mutates=True
        # spec in the registry, and ADR 0010:266-276 makes a mutating
        # tool ungrantable until Identity & Auth lands. `Agent.save()`
        # would refuse this row outright if it were listed.
        tool_keys=(
            "rag.search", "rag.ask", "models.status",
            "vision.operations", "vision.generate",
            "agent.library", "agent.illustrator",
            # P2's deviation D8 withheld a flow key because P3 had not
            # registered one yet. It is registered now, and it is ONE
            # key for every flow the box has (ruling 1) rather than one
            # per flow -- so this grant does not change when a flow is
            # added. Ruling 2: the row this grant lives on appears when
            # somebody INSTALLS the `general` default, not when a
            # deploy runs.
            "flow.run",
        ),
    ),
    AgentSpec(
        slug="library",
        name="Library",
        description="Answers only from the operator's own documents, with citations.",
        system_prompt=(
            "You answer only from the operator's own ingested documents. Search the "
            "library before you answer, and base every claim on what you found.\n\n"
            "If the library has nothing relevant, say so plainly. Do not fill the gap "
            "with general knowledge -- an answer that is not in the documents is not "
            "an answer this agent gives."
        ),
        tool_keys=("rag.search", "rag.ask"),
        as_tool=True,
    ),
    AgentSpec(
        slug="illustrator",
        name="Illustrator",
        description="Turns a description into an image prompt and generates it.",
        system_prompt=(
            "You turn a description into a generated image.\n\n"
            "First ask what image operations this box actually offers and what each "
            "one accepts -- never assume. Then compose a clear, concrete prompt from "
            "what the person asked for, pick the operation that fits, and generate.\n\n"
            "You can also search the document library when a request refers to "
            "something in it. Report what you generated and what you passed; if an "
            "argument was dropped because the picked operation does not accept it, "
            "say which."
        ),
        tool_keys=("vision.operations", "vision.generate", "rag.search"),
        as_tool=True,
    ),
    AgentSpec(
        slug="settings-helper",
        name="Settings assistant",
        description="Explains this box's settings and points at the exact control.",
        # DOCTRINE ONLY, AND THAT IS THE DECISION (spec §5.1, §5.2).
        # The list of settings pages rides the `settings.card` tool's own
        # SCHEMA -- a `choice` param's `enum` plus a description built
        # from the card table at import -- so it cannot go stale. Putting
        # it here instead would put a COPY on a ROW, and a row goes stale
        # until somebody re-runs `install_defaults --reset`, which is
        # exactly the staleness this feature was asked to rule out. This
        # text IS the row's, so an operator may tune it, and
        # `install_defaults --reset settings-helper` restores it.
        # FOLD A DOCTRINE ADDITION IN; NEVER GIVE IT ITS OWN PARAGRAPH
        # (flagship-drill follow-up, 2026-09-11). A standalone doctrine
        # paragraph inserted here reproducibly made the installed model
        # return an empty answer with zero tool calls. The identical
        # sentence folded onto the end of an EXISTING paragraph instead did
        # not reproduce that failure. See ADR 0018 for the drilled evidence.
        #
        # PLACEMENT-ROUND RULING 2 (owner, 2026-09-11): the resilience
        # battery found the fold-in sentence above was worded around the
        # flagship drill's own shape only -- a CARD's text redirecting to a
        # second page. Q8 (a multi-part user question naming two pages
        # itself) and Q4 (a negative claim -- "no page has this control" --
        # about pages never queried) are the SAME discipline gap from two
        # directions that sentence does not name. The fix is one more
        # sentence, in the SAME `settings.card` paragraph, never a new one,
        # for the identical reason the first fold-in had to be one.
        #
        # PLACEMENT-ROUND RULING 3 (owner, 2026-09-11): concise answers.
        # One line, folded into the closing paragraph rather than given a
        # new one, same law.
        #
        # OWNER FEEDBACK, LIVE, SCREENSHOT-VERIFIED, 2026-09-13: the
        # finale answer "reads glitched" -- it spoke in internal route
        # names ("chat-tool-entitlements", "identity-settings
        # (identity-settings)") instead of the page's own human name.
        # This is the PROMPT half of a two-part fix; the platform half
        # (`agents/chat/context_processors.py::_linkify_named_pages`)
        # makes whatever the model writes clickable in place, matched
        # against the same whitelist `249523e` already built. One more
        # sentence, folded into the EXISTING page-naming paragraph
        # ("Quote the page's own words..."), never a new one, same law.
        #
        # FINAL-CODE BATTERY RULING, Q10 (owner, 2026-09-13): asked "Why
        # can't my teammate see the Settings area?", the model called
        # `settings.card(page='identity-entitlements')` only and answered
        # confidently that Settings-area visibility is an entitlements
        # matter -- a fabricated mechanism. `foundation/settings_area.py`
        # gates every settings-area entry through `identity.access.
        # is_admin`; entitlements gate documents/tools/agents and play no
        # role in whether the Settings sidebar itself renders. Same root
        # cause as the two rulings above -- naming a mechanism on a page
        # never queried this turn -- in a THIRD shape neither sentence
        # names: not a card's own redirect (ruling above this one), not a
        # second page the user's own question or the model's own answer
        # spans (ruling 2), but confusing WHICH mechanism gates something
        # because only one candidate page was ever read. One more
        # sentence, in the SAME `settings.card` paragraph as the two
        # before it, never a new one, same law.
        #
        # RE-FOLD, OWNER-SANCTIONED SINGLE RETRY (2026-09-13): the first
        # wording's own live re-drill closed the fabrication (the model
        # correctly named administrator status, never entitlements) but
        # then recommended "the Accounts page" as where to promote the
        # teammate without ever calling `settings.card(page=
        # 'identity-users')` this turn -- the identical discipline gap
        # one step later, at the ACTION rather than the DIAGNOSIS. Same
        # sentence, same paragraph, widened to also name "where someone
        # would go to act on it".
        system_prompt=(
            "You are the settings guide on a private, offline-first box. You explain what "
            "this box's settings are, what each control does, and where it lives. YOU NEVER "
            "CHANGE A SETTING, and you cannot: every tool you hold is read-only.\n\n"
            "Call `settings.card` for the page in question rather than answering from "
            "memory. It lists every control on that page, what each one means, what changes "
            "when it changes, and the anchor the platform turns into a link. The set of "
            "pages you may ask about is the set the tool itself offers -- never invent "
            "one. A card naming another page is a page you have not read yet -- get its "
            "card too before you describe what is on it. The same holds before you name any "
            "page in a multi-part answer, or say a page lacks a control -- get that page's "
            "card this turn too, whether or not another card sent you there. The same holds "
            "before you say who can see or access something, which mechanism controls it, or "
            "where someone would go to act on it -- fetch the card of that owning page first, "
            "rather than the page it merely sounds like.\n\n"
            "Call `settings.overview` whenever the answer depends on how THIS box is "
            "configured rather than on what a page could do -- its posture, its library "
            "posture, whether administrators may read other people's content, the session "
            "idle window, whether conversations are told the date and time. Answer from what "
            "it returns, never from a guess about how a box is usually set up.\n\n"
            "Quote the page's own words for a control's name, so the person can find it by "
            "reading. Name the page that OWNS a control, which is not always the page whose "
            "subject it sounds like. Refer to a page by its human name only -- \"Identity & "
            "security\", \"Tool access\" -- never its internal route name or a parenthesised "
            "slug.\n\n"
            "When you do not know, say so and name the page you would look at. Never invent "
            "a setting, a control or a page. Do not write out a link yourself -- the "
            "platform builds the links from what the tool returned, beneath your answer. "
            "Answer in short plain prose, leading with the direct answer -- no markdown "
            "headers, no emoji, and numbered steps only where there are real steps to number."
        ),
        tool_keys=("settings.card", "settings.overview", "models.status"),
        # `llm_role` defaults to CHAT_CONVERSE_ROLE (`:97`); `max_steps`
        # to MAX_STEPS_DEFAULT (`:98`); `as_tool` stays False (`:99`) --
        # an `agent.settings-helper` tool key would put this agent inside
        # OTHER agents' reach, which is the opposite of settings-only.
    ),
)

# THE SETTINGS-ONLY SURFACE, EXPRESSED AS A SLUG SET (owner ruling 2,
# spec §7). ONE LINE BESIDE THE SPEC IT NAMES, and `agents/visibility.py`
# wraps the platform's one gate with it -- rather than a `surface` column
# on `Conversation` (a migration, a write path and back-fill reasoning,
# to express a fact the `agent` FK already carries), a `surface` member on
# `AgentSpec` (one more concept, with `"chat"` as a silent default), or a
# narrowing of `visible_conversations`/`visible_agents` themselves (which
# must keep answering YES -- the panel reads the same rows).
#
# `agents/tests/test_settings_tools.py::TestTheSurfaceSlugSet` pins that
# every slug here names a real catalogue entry, so this set cannot
# outlive the agent it was written for.
SETTINGS_SURFACE_SLUGS: frozenset[str] = frozenset({"settings-helper"})

# `library-brief` is the flow that proves `flow.run` end to end (Task
# 13): a fixed two-step search-then-answer, always in the same order,
# with no decisions in between.
#
# NOTE: this flow uses `$input.*` only. Neither `rag.search` nor
# `rag.ask` takes an argument a prior step's output can honestly fill
# -- `rag.ask` takes a question, and feeding it a retrieved title would
# ask a question nobody asked. `$steps.N.*` resolution is real, is the
# whole point of `resolve_ref`, and is proven end to end in
# `agents/runtime/tests/test_flow.py` over registered stub tools. A
# shipped default contorted into using one would be a worked example
# pretending to be a product.
DEFAULT_FLOWS: tuple[FlowSpec, ...] = (
    FlowSpec(
        slug="library-brief",
        name="Library brief",
        description=(
            "A fixed two-step brief on one topic: search the operator's own "
            "documents for it, then answer it from them with citations. Runs the "
            "same two steps every time, in the same order, with no decisions in "
            "between."
        ),
        inputs=(
            FlowInput("topic", "Topic", required=True,
                      description="What the brief should be about."),
            FlowInput("category", "Category",
                      description=(
                          "Restrict to one library category by name. Leave it out "
                          "to use every category."
                      )),
        ),
        steps=(
            FlowStep("rag.search", {"query": "$input.topic",
                                    "category": "$input.category"}),
            FlowStep("rag.ask", {"question": "$input.topic",
                                 "category": "$input.category"}),
        ),
    ),
)


def catalogue(kind: str) -> tuple:
    """Every shipped default of `kind` -- `"agent"` or `"flow"`."""
    if kind == "agent":
        return DEFAULT_AGENTS
    if kind == "flow":
        return DEFAULT_FLOWS
    raise ValueError(f"Unknown catalogue kind {kind!r}; must be 'agent' or 'flow'")


def default_for(kind: str, slug: str):
    """The `AgentSpec`/`FlowSpec` named `slug` in `kind`'s catalogue.

    Raises `ValueError` naming what the catalogue actually holds when
    `slug` is not one of its entries -- an unknown slug is a caller
    bug, not a state to degrade from.
    """
    for spec in catalogue(kind):
        if spec.slug == slug:
            return spec
    known = sorted(spec.slug for spec in catalogue(kind))
    raise ValueError(f"No shipped default {kind} {slug!r}. Known {kind}s: {known}")


def missing_defaults(kind: str, installed_slugs) -> tuple:
    """The entries of `kind`'s catalogue not yet present in
    `installed_slugs` -- what an "Add the default X" offers list shows.
    Case-insensitive, matching the `Lower("slug")` uniqueness both
    `Agent` and `Flow` enforce."""
    installed = {slug.lower() for slug in installed_slugs}
    return tuple(spec for spec in catalogue(kind) if spec.slug.lower() not in installed)


def install_default(kind: str, slug: str, principal, *, reset: bool = False):
    """Create the shipped default `slug` if it is absent. Returns
    `(row, created)`.

    CREATE-IF-ABSENT, AND THAT IS THE WHOLE CONTRACT (ruling 2). An
    existing row is returned UNTOUCHED -- not refreshed, not merged,
    not "updated where it differs". Safe to call twice, safe behind a
    button somebody double-clicks, and incapable of reverting an edit
    the operator made. P2's `sync_resident_agents` did the opposite (it
    re-applied the code declaration on every deploy) and that is
    precisely what ruling 2 retires: nothing writes the operator's
    database because a deploy happened.

    `reset=True` is the ONE path that rewrites an existing row. It
    names a single slug, it is reached only from an explicit
    `install_defaults --reset <slug>` (or the equivalent page action),
    and it LOGS what it replaced -- because it is the only operation
    here that can destroy work. It also RE-ENABLES a disabled row and
    RE-STAMPS `owner_kind`/`owner_key` to the resetting principal --
    a reset is a fresh adoption of the shipped text, not a partial
    patch, so every field a fresh install would set is set again.

    THE INSTALLING PRINCIPAL OWNS THE ROW (ruling 4b). In open mode
    that is `Principal("open", "box")`, which is the true statement
    about a box with no accounts -- not a placeholder to be filled in
    later, but the correct answer for this posture.

    Takes no model class arguments and imports Django INSIDE its body,
    so the module's declarations stay importable by anything.
    """
    from identity.access import owner_fields

    spec = default_for(kind, slug)

    if kind == "agent":
        from agents.models import Agent as Model

        def _fields() -> dict:
            return dict(
                name=spec.name, description=spec.description,
                system_prompt=spec.system_prompt, llm_role=spec.llm_role,
                tool_keys=list(spec.tool_keys), max_steps=spec.max_steps,
                # THE SHIPPED CATALOGUE IS THE PLATFORM'S OFFER TO
                # EVERYBODY, so an installed default is box-wide --
                # UNCONDITIONALLY, never `is_admin(principal)` (spec
                # decision 23). `manage.py install_defaults` runs as
                # `OPEN_PRINCIPAL`, which it imports and never
                # constructs, AST-guarded; `identity.access.is_admin`
                # answers True for it on an OPEN box and False on an
                # accounts-on one, because `_user_row` returns None for
                # any `principal.kind != "user"`. Stamping by the
                # installer's authority would therefore leave the
                # canonical shell install working on an open box and
                # SILENTLY BREAK IT on an accounts-on one -- the posture
                # where a shipped default most needs to reach everybody.
                #
                # IN `_fields()` RATHER THAN BESIDE `resident=True`
                # BELOW, so the `--reset` path re-stamps it with every
                # other field (that function's own "a reset is a fresh
                # adoption" rule), and so the FLOW branch never learns
                # about a column `Flow` does not have.
                box_wide=True,
            )
    else:  # "flow" -- `catalogue()` (via `default_for`) already refused any other kind
        from agents.models import Flow as Model

        def _fields() -> dict:
            return dict(
                name=spec.name, description=spec.description,
                inputs=[_flow_input_dict(i) for i in spec.inputs],
                steps=[_flow_step_dict(s) for s in spec.steps],
            )

    existing = Model.objects.filter(slug__iexact=slug).first()
    if existing is not None and not reset:
        return existing, False

    fields = _fields()
    if existing is not None:
        logger.info(
            "agents.defaults: install_defaults --reset %s %r replacing "
            "name=%r description=%r with the shipped default",
            kind, slug, existing.name, existing.description,
        )
        for field_name, value in fields.items():
            setattr(existing, field_name, value)
        existing.resident = True
        existing.enabled = True
        for field_name, value in owner_fields(principal).items():
            setattr(existing, field_name, value)
        existing.save()
        return existing, False

    row = Model(
        slug=slug, resident=True, enabled=True,
        **owner_fields(principal),
        **fields,
    )
    row.save()
    return row, True
