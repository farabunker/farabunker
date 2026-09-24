"""The settings assistant's two tools (spec §5.2, §5.3).

BOTH READ-ONLY, NEITHER MUTATING, and that is structural rather than
intended: neither spec declares `mutates`, so both default to False,
and `grantable_tools` would refuse a row naming a mutating key anyway
(`docs/EXTENDING.md`). Owner ruling 1 is guide-only; this module is
where it is true.

TWO TOOLS THAT LOOK ALIKE ARE GATED DIFFERENTLY, ON PURPOSE, and it is
said out loud here so a reviewer does not read the asymmetry as an
oversight:

  * `settings.overview` REFUSES A NON-ADMIN, in the runner, before it
    reads anything. `register_tool` puts a spec in the PLATFORM-WIDE
    registry and grants are per-AGENT rows plus the Tool access page, so
    nothing stops an operator adding this key to the general assistant's
    `tool_keys` -- at which point a MEMBER on an accounts-on box, chatting
    on `/chat/`, learns this box's posture, library posture, whether
    administrators read other people's content, and the session idle
    timeout. Read-only is not admin-only, and the owner's ruling is about
    the INFORMATION, not only about the panel. The route class is the
    SURFACE's gate; this is the TOOL's. "Every layer keeps its own
    defensive call" is already this repository's doctrine
    (`agents/chat/templates/chat/_composer.html:56-62`), and
    `ToolContext` carries the acting principal precisely so a runner can
    ask (`agents/runtime/loop.py:432-446`).

  * `settings.card` DOES NOT, and the difference is the point. Its
    content is platform-authored help text about pages the reader may or
    may not be able to open -- the same text this repository is about to
    publish in `docs/EXTENDING.md` -- and it reports NO VALUE OF ANY KIND
    about this box. Gating it would be security theatre that also broke
    the member-facing variant the spec defers.

MODULE-SCOPE IMPORTS STAY PURE, so `AgentsConfig.ready()` keeps its
no-DB-no-heavy-imports promise: `foundation.settings_help` is a rule-1
pure leaf and `agents.contracts.tools` is one too. `identity.access` and
`agents.models` are imported LAZILY, inside `run_overview`'s body, the
same shape `models/registry/tools.py::run_status` uses.

WHAT THE OVERVIEW DOES NOT REPORT, AND WHY (spec §5.4, owner flag 2;
extended to `JobSettings` at S4, Coherence Wave B; REASON CORRECTED at
I1, Coherence Wave B review -- read this whole section before trusting
the old one, if it turns up in history): the library's own live caps --
upload cap, page cap, media cap, retrieval top-k, score floor -- live on
`tools.rag.models.RagSettings`, and the queue's own -- memory budget, max
concurrent jobs, retention limit, default priority -- live on
`models.queue.models.JobSettings`. `agents/` MAY NOT IMPORT `tools/` AT
ALL (ADR 0015, swept by `foundation/ops/tests/test_import_law.py::
test_no_agents_module_imports_a_tools_package`, an unconditional sweep
with no named seam, unlike `identity`'s four), and `models.queue.models`
is a FORBIDDEN_MODULE to every column but one narrow `foundation.ops.
backup` carve-out that has nothing to do with this tool.

THAT CONSTRAINT IS NARROWER THAN "CANNOT BE REACHED", AND AN EARLIER
VERSION OF THIS COMMENT OVERSTATED IT (I1): "may not IMPORT" rules out a
direct `from tools.rag.models import RagSettings`, but it is not the same
claim as "unreachable by any lawful seam" -- and the house already has an
answer for exactly this shape, not built here. `agents/contracts/
workstreams.py`'s `_PANELS`/`register_workstream_panel`/
`all_workstream_panels` (`:120-129`) is a rule-1 pure leaf `agents/`
itself owns; `tools/rag/apps.py::AppConfig.ready()` (`:190-196`)
registers a dotted-path STRING into it, never a direct import; `agents/
workstreams.py` resolves that string with `import_string` at render time
and never raises on a broken one. `foundation/ops/tests/
test_import_law.py:918`'s own docstring states the doctrine this pattern
exists to satisfy: *"a workstream needs to know something about a
document, and the correct answer there is always a registry, never an
import."* A `SettingsOverviewSource` registry of the identical shape --
`tools/rag/apps.py`/`models/queue/apps.py` each registering a read-only
dotted-path provider at `ready()`, resolved by `import_string` in `run_
overview` below -- would be lawful under the exact same rule this module
already cites, if it existed.

IT DOES NOT EXIST, AND THAT IS A PARKED PRODUCT DECISION, NOT AN
ARCHITECTURAL WALL THIS TOOL IS BLOCKED BY. Spec §5.4 / owner flag 2
argues independently for staying guide-only here ("Guide-only is what
makes that refusal safe... Reciting the number is not the feature"), and
that argument does not depend on whether a seam is buildable -- so the
absence of the seam is a choice available to revisit, not a fact this
module discovered. So: these eleven fields are NOT COVERED TODAY, on a
route to coverage that import law does not close if a future owner
decision opens it. Building that registry is explicitly NOT part of this
change.

S4 (Coherence Wave B) made the gap NAMED rather than merely absent: `run_
overview` below now says outright, in its own output, that Library's
seven fields and Job execution's five are not covered here (compact, one line
each, grouped with the two pages it DOES cover) instead of silently
reporting 5 of 17 settings fields as though they were the whole
configuration -- the exact "reports a partial configuration as if it
were the configuration" defect the backend audit named (S4).
`REPORTED_SETTINGS_FIELDS`/`UNREPORTED_SETTINGS_FIELDS` below are the
closed accounting `agents/tests/test_settings_tools.py::
TestTheOverviewFieldCoverage` checks against the four singleton models'
OWN concrete fields (via introspection, in a test file -- exempt from
the import-law sweep, so IT may import `RagSettings`/`JobSettings` even
though this module never DIRECTLY IMPORTS them) -- so a field added to
either model in the future goes red here rather than silently joining
neither set.
"""
from __future__ import annotations

from agents.contracts.tools import (
    Param, ToolContext, ToolRefused, ToolResult, ToolSpec, validate_tool_args,
)
from foundation.settings_help import CARDS, CONTENT_HASH, card_for, page_choices

# ONE `<route> -- <title>` LINE PER CARD, BUILT AT IMPORT, so the index a
# model reads cannot go stale relative to the table it is derived from.
# It rides the TOOL SCHEMA rather than the prompt (spec §5.2, decision
# 6): a contributor appended in `build_messages` would need a condition
# on the agent's slug inside a function every agent on this platform goes
# through, to inject a paragraph a tool schema already carries; and the
# agent ROW's `system_prompt` is a COPY, which goes stale until somebody
# re-runs `install_defaults --reset`.
_PAGE_INDEX = "\n\nThe settings pages on this box:\n" + "\n".join(
    f"  {card.route_name} -- {card.title}" for card in CARDS
)


SETTINGS_CARD = ToolSpec(
    key="settings.card",
    label="Settings page help",
    description=(
        "Explain ONE settings page on this box: what it is for, every control on it, what "
        "each control means and what changes when it changes, and the anchor to link to. "
        "Call this before answering any question about a settings page rather than "
        "answering from memory."
        + _PAGE_INDEX
        + f"\n\nSettings context version: {CONTENT_HASH}."
    ),
    params=(
        Param("page", "choice", "Settings page", required=True,
              choices=page_choices(),
              description="Which page to explain."),
    ),
    roles=(),
    runner="agents.settings_tools.run_card",
)


SETTINGS_OVERVIEW = ToolSpec(
    key="settings.overview",
    label="This box's current settings",
    description=(
        "Report how THIS box is configured right now: its posture, whether administrators "
        "may read other people's content, the session idle timeout, the library posture, "
        "and whether conversations are told the current date and time. Also names, without "
        "reciting numbers, that the Library and Job execution pages carry their own "
        "settings this tool does not report. Read-only. Call it when the answer depends on how this box "
        "is set up, not on what a page could do."
    ),
    params=(),
    roles=(),
    runner="agents.settings_tools.run_overview",
)


# S4 (Coherence Wave B): the closed accounting for every concrete field on
# the four settings singletons the backend audit's Dimension 1 table
# names -- `IdentitySettings`, `ChatSettings`, `RagSettings`,
# `JobSettings`. FIELD NAMES ONLY, no model import: this module cannot
# import `RagSettings`/`JobSettings` (see the module docstring above), and
# never needs to -- `agents/tests/test_settings_tools.py::
# TestTheOverviewFieldCoverage` is the one place that imports all four
# models (a test file, exempt from the import-law sweep) to check every
# concrete field lands in exactly one of the two structures below.
REPORTED_SETTINGS_FIELDS: dict[str, frozenset[str]] = {
    "IdentitySettings": frozenset(
        {"posture", "library_posture", "admin_sees_content", "session_idle_minutes"}
    ),
    "ChatSettings": frozenset({"time_aware"}),
}

# The SAME reason for every `RagSettings`/`JobSettings` entry below.
# Named once and reused rather than restated eleven times over, since
# restating it eleven times would be eleven chances for one copy to
# drift from the other ten.
#
# CORRECTED AT I1 (Coherence Wave B review): the prior text here claimed
# these tables "cannot be read... by any lawful import, seam or
# otherwise", which is false -- see the module docstring above for the
# full correction. What is actually true, and all this constant claims
# now: `agents/` may not IMPORT either table directly (import-law rule
# 3: `agents/` may not import `tools/` at all; `models.queue.models` is
# a FORBIDDEN_MODULE to every column but one narrow backup carve-out).
# NOT covered today FOR THAT REASON, not because coverage is impossible
# -- a registered, read-only dotted-path provider (the `agents/
# contracts/workstreams.py` pattern the module docstring names) would be
# lawful if built, and building one is a parked owner decision, not part
# of this change.
_IMPORT_LAW_REASON = (
    "agents/ may not import this table directly (import-law rule 3: agents/ may "
    "not import tools/ at all; models.queue.models is a FORBIDDEN_MODULE to every "
    "column but one narrow backup carve-out) -- named on its own page in the "
    "overview's text instead of reported here. NOT the same as unreachable: a "
    "registered read-only provider (the agents.contracts.workstreams pattern) "
    "would be lawful if built; that is a parked owner decision, not done here."
)

# `updated_at` is a plain `auto_now=True` bookkeeping timestamp on
# `IdentitySettings`/`ChatSettings` (the two singletons that carry one at
# all -- `RagSettings`/`JobSettings` have neither) -- never operator-set,
# never something an administrator asks "what is this configured to",
# and therefore not one of the audit's sixteen OPERATOR-EDITABLE fields
# in the first place. Named here anyway, with its own reason, rather than
# filtered out of the introspection sweep silently -- the whole point of
# a named-exclusion list is that nothing is invisible to it.
_BOOKKEEPING_REASON = (
    "auto_now bookkeeping timestamp, not an operator-editable setting -- nothing "
    "for 'how is this box configured' to say about it."
)

# `JobSettings.detected_memory_bytes`/`detected_memory_at` (queue
# memory-governance track, `models/queue/migrations/0005`) are the SAME
# SHAPE as `updated_at` above, not a second `_IMPORT_LAW_REASON` field:
# the worker process writes them ONCE, at boot, as a measurement, never as
# something an operator sets (`models.queue.models.JobSettings`:282-291).
# They render on the Job execution page as a labelled prefill note --
# "detected by the worker process on <date>" -- and are NEVER applied to
# the memory budget on the operator's behalf. So, like `updated_at`, they
# are not one of the audit's operator-editable fields in the first place;
# named here with their own reason rather than folded into
# `_IMPORT_LAW_REASON`, whose siblings are all fields an operator DOES
# set and this tool merely cannot read.
_DETECTED_MEMORY_REASON = (
    "written once by the worker process at boot as a measurement, not an "
    "operator-editable setting -- rendered on the Job execution page as a "
    "labelled prefill note ('detected by the worker process on <date>') and "
    "never applied to the memory budget on the operator's behalf."
)

UNREPORTED_SETTINGS_FIELDS: dict[str, dict[str, str]] = {
    "IdentitySettings": {
        "updated_at": _BOOKKEEPING_REASON,
    },
    "ChatSettings": {
        "updated_at": _BOOKKEEPING_REASON,
    },
    "RagSettings": {
        "history_limit": _IMPORT_LAW_REASON,
        "max_upload_bytes": _IMPORT_LAW_REASON,
        "max_media_seconds": _IMPORT_LAW_REASON,
        "max_document_pages": _IMPORT_LAW_REASON,
        "retrieval_top_k": _IMPORT_LAW_REASON,
        "retrieval_score_floor": _IMPORT_LAW_REASON,
        "hybrid_search": _IMPORT_LAW_REASON,
    },
    "JobSettings": {
        "memory_budget_bytes": _IMPORT_LAW_REASON,
        "max_concurrent_jobs": _IMPORT_LAW_REASON,
        "default_priority": _IMPORT_LAW_REASON,
        "retention_limit": _IMPORT_LAW_REASON,
        "max_queued_per_principal": _IMPORT_LAW_REASON,
        "response_timeout_seconds": _IMPORT_LAW_REASON,
        # Queue memory-governance track (2026-09-21): the operator's
        # per-kind wait-ceiling map, edited on the Job execution page's
        # fourth form (`id="kind-waits"`) -- operator-editable, same
        # reason as its five siblings above.
        "kind_wait_seconds": _IMPORT_LAW_REASON,
        # Worker-detected, not operator-editable -- see
        # `_DETECTED_MEMORY_REASON` above.
        "detected_memory_bytes": _DETECTED_MEMORY_REASON,
        "detected_memory_at": _DETECTED_MEMORY_REASON,
    },
}


def run_card(args: dict, ctx: ToolContext) -> ToolResult:
    """One page's help card, as text a model reads well and as data the
    panel builds links from.

    NO DATABASE, NO REQUEST, NO LIVE VALUE. It reads
    `foundation.settings_help` only, so its cost is constant in the
    number of settings pages -- which is the point of an
    index-plus-on-demand shape.

    `links` are `route`/`anchor`/`label` TRIPLES, never URLs. The panel
    re-validates every one of them against `CARDS` before it reverses
    anything (spec §4.3), so nothing that is not already in the code-side
    table can become a link on a settings page. That is a whitelist by
    construction, and it is the property that matters most here, because
    a link is the one thing on that surface an operator will click.
    """
    clean = validate_tool_args(SETTINGS_CARD, args)
    card = card_for(clean["page"])
    # `validate_tool_args` has already refused anything outside
    # `page_choices()`, which is derived from `CARDS` at import -- so this
    # cannot be None. Asserted rather than branched: a None here would
    # mean the enum and the table had drifted inside one process, which is
    # not a runtime condition to handle, it is a bug to surface.
    assert card is not None, clean["page"]

    lines = [f"{card.title} ({card.route_name})", "", card.purpose, ""]
    for field in card.fields:
        lines += [
            f"- {field.name}",
            f"    What it is: {field.meaning}",
            f"    What it changes: {field.effects}",
            f"    Link anchor: {field.anchor}",
        ]

    return ToolResult(
        text="\n".join(lines),
        data={
            "page": card.route_name,
            "content_hash": CONTENT_HASH,
            "links": [
                {"route": card.route_name, "anchor": field.anchor, "label": field.name}
                for field in card.fields
            ],
        },
    )


def run_overview(args: dict, ctx: ToolContext) -> ToolResult:
    """How THIS box is configured right now -- administrator only.

    THE REFUSAL COMES BEFORE ANY VALUE IS READ OFF THE ROW, and its
    message names none of them: a refusal that leaked the posture in its
    own text would be the same defect wearing an exception.

    ONE ROW READ, not one per value: `identity.access.settings_row()` is
    fetched once and every answer read off that instance -- the per-call
    reuse norm `agents/entitlements.py:30-36` states -- and threaded into
    `is_admin` so the gate does not fetch it a second time.

    LAZY IMPORTS, inside the body, so this module stays importable from
    `AppConfig.ready()` with no database and no heavy import, exactly as
    `models/registry/tools.py::run_status` does.
    """
    from agents.models import ChatSettings
    from identity.access import is_admin, settings_row

    validate_tool_args(SETTINGS_OVERVIEW, args)   # declares no params: any arg is a bug

    row = settings_row()
    if not is_admin(ctx.principal, settings_row=row):
        # THE WORDING IS CONSTRAINED BY THE TEST BESIDE IT, deliberately.
        # `test_a_member_is_refused_and_the_refusal_names_no_value` bans
        # every posture token from this string, and "open" IS a posture
        # value -- the one drills 1b and 2 turn on, and the one a refusal
        # must never leak. The spec's own §5.3 draft used "open Settings"
        # as an ordinary verb; that reads fine and fails the assertion,
        # so the sentence -- not the assertion -- is what moved.
        raise ToolRefused(
            "This box's configuration is administrator-only. Ask an administrator, or sign "
            "in as one and read it on the settings pages."
        )

    chat = ChatSettings.get_solo()
    # S4 (Coherence Wave B): NAMED rather than silently absent -- one
    # compact line per page this tool cannot read, grouped with the two
    # it can (below) rather than left for the reader to notice are
    # missing. `REPORTED_SETTINGS_FIELDS`/`UNREPORTED_SETTINGS_FIELDS`
    # above are the closed accounting a guard test checks this against.
    # N2 (Coherence Wave B review): the COUNT below is derived from
    # `UNREPORTED_SETTINGS_FIELDS` (so it cannot drift), but the
    # parenthetical field-name prose beside each count is hand-
    # maintained and NOT guarded -- a new `RagSettings`/`JobSettings`
    # field correctly bumps the count while this prose still lists the
    # old set. This is the one hand-maintained line left in this
    # structure; `TestTheOverviewFieldCoverage` (`agents/tests/
    # test_settings_tools.py`) guards the FIELD ACCOUNTING, not this
    # sentence's wording.
    unreported = {
        "Library": (
            f"{len(UNREPORTED_SETTINGS_FIELDS['RagSettings'])} fields (history retention, "
            "upload/media/page caps, retrieval) -- call settings.card('rag-settings'), or "
            "check that page."
        ),
        # F1 (Coherence Wave C): keyed by the PAGE these five fields are
        # edited on, which is now "Job execution" -- a registered
        # settings page with a card of its own, so this line can send a
        # reader to `settings.card` exactly as the Library line does
        # rather than to an activity page that no longer carries the
        # forms. The import-law reason this tool cannot READ the values
        # is unchanged by the move.
        "Job execution": (
            f"{len(UNREPORTED_SETTINGS_FIELDS['JobSettings'])} fields (memory budget, max "
            "concurrent jobs, retention limit, default priority, per-principal queue cap, "
            "response timeout, per-kind wait ceilings, worker-detected memory and its "
            "detection date) -- call settings.card('jobs-settings'), or check that page."
        ),
    }
    data = {
        "content_hash": CONTENT_HASH,
        "posture": row.posture,
        "library_posture": row.library_posture,
        "admin_sees_content": bool(row.admin_sees_content),
        "session_idle_minutes": int(row.session_idle_minutes),
        "time_aware": bool(chat.time_aware),
        "unreported_settings": unreported,
    }
    # EVERY VALUE HERE IS AN ENUMERATED OR NUMERIC COLUMN (spec §9). No
    # free-text, user-supplied string is interpolated into this text in
    # v1 -- no account name, group name, entitlement name, workstream name
    # or conversation title -- which is also why nothing here is a count
    # or a name list. A later version that wants to report such a value
    # goes through the platform's existing doctrine
    # (`agents/runtime/prompt.py::_sanitize_attachment_title`, the
    # DATA-not-instructions header, and the per-call random-delimited
    # block for anything longer than a name), never a new one.
    #
    # GROUPED BY PAGE, compactly (S4): "Identity & security" and "Chat"
    # carry the values this tool CAN read; "Not reported here" carries
    # the two it cannot, so the reader sees both halves of the picture in
    # one place rather than a flat list that quietly stops at five values.
    text = "\n".join([
        "Identity & security:",
        f"  Posture: {data['posture']}",
        f"  Library posture: {data['library_posture']}",
        f"  Administrators may read other people's content: {data['admin_sees_content']}",
        f"  Session idle window (minutes): {data['session_idle_minutes']}",
        "Chat:",
        f"  Conversations are told the current date and time: {data['time_aware']}",
        "Not reported here (this tool cannot read those tables):",
        f"  Library -- {unreported['Library']}",
        f"  Job execution -- {unreported['Job execution']}",
        f"Settings context version: {data['content_hash']}",
    ])
    return ToolResult(text=text, data=data)
