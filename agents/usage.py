"""What the next turn's prompt will carry, as an estimate, and how the
page says it.

IT LIVES AT THE COLUMN ROOT, not inside `agents/chat`, for the same
reason `agents/visibility.py` and `agents/labels.py` do: a future
management command and a future MCP edge need the same answer without
either of them being a view. `Turn` is deliberately NOT one of the
models `foundation/ops/tests/test_column_boundaries.py` fences off from
`agents/chat` (`_VISIBILITY_MODELS` is Conversation/Agent/Flow/Share/
Workstream) -- `agents/chat/service.py` already reads and writes `Turn`
rows -- so this module's placement is about reuse, not about a boundary.

ONE COUNTER, NOT TWO. `agents/limits.py::HISTORY_TURNS`' own docstring
warns that a token counter beside the replay cap would be "a second,
drifting one". `estimate_tokens` below is therefore the ONE PUBLIC
ENTRY to the ONLY arithmetic in this feature, and chat compaction --
out of scope -- will call this same function rather than growing its
own.

THE ESTIMATE IS AN UNDER-COUNT, AND THAT IS STATED RATHER THAN HIDDEN.
Six things the replayed prompt carries are not counted here:

  1. The ASSISTANT `ToolCallBlock` a TOOL turn replays. `agents.runtime.
     prompt.history_messages` does not emit `Turn.text` for a
     `role == "tool"` row -- it calls `tool_turn_messages`, which emits
     TWO messages, the first carrying the tool's wire name and the full
     `tool_kwargs` JSON. None of that is in `Turn.text`.
  2. The fence a replayed ASSISTANT turn carries: `_replay_assistant_
     text` re-derives a fenced version from `turn.data`.
  3. `_FOREIGN_TURN_HEADER`, wrapped around another person's USER turn.
  4. Attached-file text -- inlined extract and attachments block. It
     varies with the acting principal, costs extra queries, and belongs
     to the turn being sent rather than to the conversation's history.
  5. Tool results the turn has not produced yet.
  6. The stream instructions' own labelled header (`prompt.py::
     _instructions_block`). Counting it would mean importing
     `agents.runtime.prompt` -- and with it the chat-message type and
     the engine surface -- into a module every thread render calls,
     which is the exact weight `agents/limits.py`'s own docstring
     exists to keep out of the rendering path.

The clock line is not counted either, and that is a BUDGET decision:
`ChatSettings` is read nowhere on a thread render (`time_aware_now_
line()` reads the singleton inside `build_messages`, at turn time), so
counting it would cost a second new query on the page and another on
every poll tick, for the smallest term in the estimate.

Under-counting is the right direction to be wrong in for a "when should
I compact" signal ONLY IF THE READER IS TOLD. `DISCLOSURE_BODY` below
tells them, and names the TWO exclusions large enough to change a
decision -- attached files and tool traffic -- in so many words ("It
does not count files attached to a message, or what a tool was asked
and answered"). This paragraph said "the one" until wave item r8; ADR
0019, `agents/README.md` and `agents/chat/README.md` all correctly said
two, and this docstring was the outlier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from agents.limits import HISTORY_TURNS
from agents.models import Turn

# Disclosed in the UI, never presented as exact. There is no tokenizer on
# this box: `requirements.txt` carries none, adding one for a status line
# would be a new dependency for a cosmetic number, and asking the engine
# to tokenize would be a network call per page render on a surface whose
# whole design avoids per-render engine traffic.
CHARS_PER_TOKEN = 4

# Where the ceiling came from. The first three are
# `models.contracts.bindings.effective_context_window`'s own vocabulary,
# read here rather than retyped as literals at the call sites. The
# fourth is the VIEW's: `check.resolved is None` -- an unbound role, an
# unregistered pick, a label refusal -- never reaches that function at
# all, and is a different thing to say.
WINDOW_SOURCE_CONNECTION = "connection"
WINDOW_SOURCE_ENGINE_DEFAULT = "engine-default"
WINDOW_SOURCE_UNKNOWN = "unknown"
WINDOW_SOURCE_UNBOUND = "unbound"

BAND_OK = "ok"
BAND_HIGH = "high"
BAND_FULL = "full"
BAND_UNKNOWN = "unknown"

HIGH_PERCENT = 70
FULL_PERCENT = 90


@dataclass(frozen=True)
class ContextUsage:
    """What the next turn will cost, and how much room there is.

    `window` is 0 and `percent` is None whenever there is no ceiling to
    divide by -- an engine that declares no default, or no bound model
    at all. `truncated` is `total_turns > replayed_turns`: the first
    place this platform tells a reader that their long conversation is
    already being shortened before it is sent.
    """

    estimated_tokens: int
    window: int
    window_source: str
    percent: int | None
    band: str
    replayed_turns: int
    total_turns: int
    truncated: bool


@dataclass(frozen=True)
class MeterSegments:
    """The meter's sentence, split around the two numbers the page
    renders as spans.

    DECLARED IN PYTHON, IN ONE PLACE, which is the house rule -- and
    split rather than formatted whole because the two numbers must be
    addressable elements for the poller to rewrite by `textContent`.
    The template concatenates `lead` + the token span + `middle` + the
    percent span + `tail`; nothing composes prose in JavaScript.
    """

    lead: str
    middle: str
    tail: str
    has_percent: bool


FULL_CLAUSE = "Start a new conversation to keep the model's full attention."
ENGINE_DEFAULT_SENTENCE = (
    "No limit is set for this connection; the engine's own default applies."
)
DISCLOSURE_SUMMARY = "How this is worked out."
DISCLOSURE_BODY = (
    f"Only the last {HISTORY_TURNS} messages of a conversation are sent to the model; "
    "a longer conversation is already shortened before it is sent. The number is an "
    f"estimate, about {CHARS_PER_TOKEN} characters to a token, of the words in those "
    "messages. It does not count files attached to a message, or what a tool was asked "
    "and answered. The limit is the one this connection is set to use."
)


def _tokens_from_chars(chars: int) -> int:
    """Characters over `CHARS_PER_TOKEN`, rounded up. The one arithmetic
    in this feature -- see the module docstring on why there is only
    one; `estimate_tokens` below is its one public entry."""
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_tokens(text: str | None) -> int:
    """One text's token estimate -- see `_tokens_from_chars`."""
    return _tokens_from_chars(len(text or ""))


def context_usage(conversation, agent, *, window: int, window_source: str) -> ContextUsage:
    """What the next turn's prompt will carry, for `conversation`.

    TWO QUERIES, BOTH CONSTANT IN THE CONVERSATION'S LENGTH, over one
    filtered queryset: the newest `HISTORY_TURNS` replayable rows as a
    flat `values_list`, and a `.count()` of the same set. The count is
    what `truncated` and the truncation clause are made of, and a
    `LIMIT` slice cannot produce it -- the spec's "exactly one query" was
    written before that clause became binding. What the equality-under-
    scale pins in `agents/chat/tests/test_thread_meter.py` actually assert is
    that a one-turn and a many-turn conversation cost the SAME, which
    both of these do.

    NEITHER IS AN INDEX HIT ON THE WHOLE FILTER, and that is said here
    rather than glossed: `Turn.Meta.indexes` is exactly
    `agents_turn_thread` on `(conversation, index)`, so the `state`/
    `depth` half is a scan WITHIN one conversation's rows. That bound is
    the thread's own length -- the same bound the page's per-turn render
    already pays -- and it is the honest statement. A plan that claimed a
    composite index here would have been copied forward by whoever read
    it next.

    THE FILTER MATCHES `agents.runtime.prompt.history_messages` EXACTLY
    -- `state="done"`, `depth=0` -- because a meter that measured a
    different corpus from the one that is sent would be false in the
    direction that matters.
    """
    rows = conversation.turns.filter(state=Turn.State.DONE, depth=0)
    recent = list(rows.order_by("-index").values_list("text", flat=True)[:HISTORY_TURNS])
    total_turns = rows.count()

    chars = len(agent.system_prompt or "")
    stream = conversation.workstream
    if stream is not None and (stream.instructions or "").strip():
        chars += len(stream.instructions.strip())
    chars += sum(len(text or "") for text in recent)

    estimated = _tokens_from_chars(chars)
    percent = round(100 * estimated / window) if window > 0 else None
    if percent is None:
        band = BAND_UNKNOWN
    elif percent >= FULL_PERCENT:
        band = BAND_FULL
    elif percent >= HIGH_PERCENT:
        band = BAND_HIGH
    else:
        band = BAND_OK
    replayed = len(recent)
    return ContextUsage(
        estimated_tokens=estimated,
        window=window,
        window_source=window_source,
        percent=percent,
        band=band,
        replayed_turns=replayed,
        total_turns=total_turns,
        truncated=total_turns > replayed,
    )


def meter_segments(usage: ContextUsage) -> MeterSegments:
    """The meter's sentence for this state -- see `MeterSegments`."""
    if usage.window > 0:
        return MeterSegments(
            lead="Context ~",
            middle=f" of {usage.window:,} tokens (",
            tail="%) · estimate",
            has_percent=True,
        )
    if usage.window_source == WINDOW_SOURCE_UNBOUND:
        # The page's existing `unavailable` banner already says why there
        # is no ceiling. The meter does not repeat it.
        return MeterSegments(lead="Context ~", middle="",
                             tail=" tokens · estimate", has_percent=False)
    return MeterSegments(
        lead="Context ~", middle="",
        tail=" tokens · estimate · no limit set for this connection",
        has_percent=False,
    )


def truncation_clause(usage: ContextUsage) -> str:
    """ONE OF THE TWO clauses that can newly become true mid-session,
    which is why the page renders it once and hides it rather than
    composing it on a poll tick.

    `FULL_CLAUSE` IS THE OTHER, and this docstring used to say "the one",
    which is how it went a whole branch without the same treatment
    (whole-branch review I-3). The two are handled differently on
    purpose: THIS one is pre-rendered and merely unhidden, because its
    text is per-conversation (it names two counts) and a page under the
    threshold gives nothing away by carrying it; `FULL_CLAUSE` is a
    constant with no counts in it, and `test_below_ninety_percent_it_
    does_not` says a page below the band must not carry it at all, so it
    travels in the poll body instead and the script writes it with
    `textContent`. Neither is composed in JavaScript.
    """
    dropped = max(0, usage.total_turns - usage.replayed_turns)
    return f"the oldest {dropped} of {usage.total_turns} messages are no longer sent"
