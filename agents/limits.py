"""The five numbers that bound a turn.

Here, not at the top of `agents/runtime/loop.py` where spec section 6.2
declares them, for one mechanical reason: `agents/models.py`'s
`Agent.max_steps` field default and `agents/defaults.py`'s
`AgentSpec.max_steps` dataclass default both need `MAX_STEPS_DEFAULT`,
and `agents/models.py` importing `agents/runtime/loop.py` -- which
imports `agents/models.py` -- is a cycle. A module with four ints and
no imports at all cannot participate in one.

Pure: no Django, no imports. Read by `agents/models.py`,
`agents/defaults.py`, `agents/runtime/loop.py`, and
`agents/runtime/delegate.py`.
"""
from __future__ import annotations

# LLM calls per turn. A per-agent override lives on `Agent.max_steps`;
# this is the default that row starts at. Every LLM call spends one,
# including a call that fails, so a failing loop cannot outrun it.
MAX_STEPS_DEFAULT = 8

# THE FALLBACK, NOT THE OPERATIVE VALUE (one-timeout task, 2026-09-17).
# The operative wall clock for one whole turn is now the operator-
# editable `models.queue.models.JobSettings.response_timeout_seconds`
# (default 1800s), threaded in via `models.contracts.jobkinds.JobContext.
# response_timeout_seconds` -- `agents.runtime.loop._run_turn` reads
# THAT value first and only falls back to this constant when `ctx.
# response_timeout_seconds is None` (a job claimed with no stamped
# value, e.g. mid-deploy against an older worker). Kept here, not
# deleted, for exactly that fallback, and because `agents/` may not
# import `models.queue` to read the operative value directly (import
# law) -- this module stays the one pure, import-free constant every
# caller without a `JobContext` in hand can still fall back to.
# Final review I-1: THIS constant (and even `RESPONSE_TIMEOUT_SECONDS_
# MAX`, 7200s) is SMALLER than vision's own `GENERATE_WAIT_TIMEOUT_
# SECONDS` (4 * 3600.0 = 14400s, `tools/vision/jobs.py`) -- an EARLIER
# comment here claimed the opposite ("deliberately LARGER... so exactly
# one image generation fits inside a turn"), which was already false the
# moment that constant was raised from 600s to 4 hours and was copied
# forward unchecked by this task. What actually makes an in-turn
# generation safe is NOT a size relationship between these two numbers:
# it is that the vision tool itself clamps its own wait to whatever
# remains of the TURN's budget, never past it
# (`tools.vision.tools.run_generate`,
# `remaining = ctx.budget.deadline_monotonic - time.monotonic(); timeout
# = max(1.0, min(GENERATE_WAIT_TIMEOUT_SECONDS, remaining))`) -- the same
# endorsed "derive from the remaining budget" pattern this task's own
# fix rounds extended to other in-turn clients. `GENERATE_WAIT_TIMEOUT_
# SECONDS` is a DIFFERENT axis entirely (how long ONE detached,
# page-submitted generation job may run when nothing is waiting on it,
# `tools/vision/views.py`'s own create flow) -- not a sibling of this
# constant, and never compared against it for correctness; the vision
# tool's own clamp is what keeps its in-turn wait honest, not this
# module.
#
# Checked at the top of each loop iteration and before each tool call,
# never DURING one -- see `agents/runtime/loop.py`'s docstring on
# post-deadline latency.
TURN_DEADLINE_SECONDS = 900.0

# Final review IMPORTANT (fix round 3): the floor applied at the two
# `gateway.get_llm_for` call sites (`agents/runtime/loop.py:468`,
# `agents/runtime/delegate.py:183`) so a degenerate stamped value (a
# `0` or negative `JobContext.response_timeout_seconds` -- a fixture,
# a hand edit, a future migration default; never reachable through the
# settings UI, which refuses anything below `JobSettings.
# RESPONSE_TIMEOUT_SECONDS_MIN`, 60, at the write path) can never reach
# a real httpx client as `request_timeout=0`. The flooring is localized
# to those call sites only; `resolved_turn_timeout` itself returns the
# stamped value verbatim (see its own docstring), and the budget's own
# deadline is deliberately left unfloored so such a value ends the turn
# immediately through the existing `budget.expired` branch -- the honest,
# graceful ending. A SEPARATE constant from `JobSettings.
# RESPONSE_TIMEOUT_SECONDS_MIN`, deliberately: `agents/` may not import
# `models.queue` (import law), the identical reason `TURN_DEADLINE_
# SECONDS` above is a duplicated fallback rather than a live read of
# the operator's own setting. Small on purpose -- this is a foot-gun
# guard, not a second policy floor competing with the writer's own
# 60-second minimum.
RESOLVED_TIMEOUT_FLOOR_SECONDS = 1.0

# How many prior turns of a conversation are replayed into the prompt.
# A fixed cap, NOT a token budget: the bound model's `context_window` is
# an operational bound the platform already owns
# (`ModelConnection.context_window`, ADR 0010:412-435), and a token
# counter here would be a second, drifting one.
HISTORY_TURNS = 20

# The root turn is depth 0; an agent-as-tool call is depth 1; its own
# delegate is depth 2; no deeper. The depth cap is the belt; the SHARED
# StepBudget is the braces and is the real guard -- total LLM calls per
# turn are bounded by `Agent.max_steps` regardless of how the
# delegation tree is shaped.
MAX_AGENT_DEPTH = 2

# The fifth number (C-7, round-3 hardening). A turn's own text was
# bounded by nothing before this -- the only check `agents.chat.service.
# start_turn` ran was non-empty -- so its length was bounded solely by
# the framework's in-memory upload size. 20,000 characters is generous
# for a real message (many paragraphs, a long pasted log) while still
# refusing an obviously-abusive single request outright.
#
# A REQUEST-SIZE CEILING ON ONE TURN'S OWN TEXT, NOT A BOUND ON THE
# REPLAY WINDOW (fix round 1, C-7 review: the comment here previously
# read as if it were one -- corrected). `HISTORY_TURNS` above already
# caps how many PRIOR turns replay into the prompt -- a fixed COUNT, not
# a size -- and the actual size bound on what the bound model can accept
# for that whole replayed conversation is `models.registry.models.
# ModelConnection.context_window` (an operator's own per-connection
# setting, ADR 0010:412-435), not this constant. A conversation of many
# turns each sized right up to `MAX_TURN_CHARS` can still overflow
# `context_window` -- this number only ever bounds the ONE turn a caller
# just submitted, checked before it is ever queued.
MAX_TURN_CHARS = 20_000

# THE TWO ON-TIMEOUT SENTENCES (one-timeout task, MINOR 6, fix round 1).
# Not a number, but they live here for the SAME mechanical reason the
# numbers above do: `agents.chat.rendering` and `agents.chat.views.turns`
# both need to compare a `Turn.error` against `TURN_TIMEOUT_ERROR` (the
# content-withheld pattern's own trigger), and neither may import
# `agents.runtime.loop` for that ALONE without also pulling in
# `models.contracts.gateway`, the engine registry, and `llama_index` --
# real weight, for one string compare, in a module that renders every
# chat card and every poll response. This module has none of that: pure,
# no imports, already read by both sides of the column. `agents.runtime.
# loop` imports these two names FROM here (never re-declares them) so
# there is exactly one definition of each, and every existing `from
# agents.runtime.loop import TURN_TIMEOUT_ERROR` (loop.py re-exports it
# by importing it into its own namespace) keeps working unchanged.
TURN_TIMEOUT_ERROR = "The response ran out of time before it finished."
# Plain text, no markup, so `agents.chat.views.turns._failed_body` can
# append it directly onto `data.error` for the live poll path, which
# renders that string via `textContent` (`conversation.html`'s
# `showCardError`, held by PR #84, never reads a card's HTML for a
# FAILED turn). `_turn_card.html`'s full-page rendering carries the
# identical sentence plus a real link; this is the one bare-prose
# source of truth for it, so the two copies cannot drift apart in
# meaning even though only one of them can carry a link.
TURN_TIMEOUT_ADMIN_HINT = (
    "An administrator can raise this time limit on the Job execution settings page."
)
