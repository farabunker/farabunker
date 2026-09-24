# agents/runtime/ — the turn, running

This is where somebody debugging a stuck, wrong, or crashed turn should
start. It is a **private package** inside the `agents` app, not its own
Django app (deviation D1) — `agents/apps.py::AgentsConfig.ready()` is the
only thing that registers anything out of it, and it imports none of this
package's implementation modules to do so (every `planner`/`handler`/
`summarizer`/`on_terminal`/`runner` is a dotted-path STRING).

## The modules

| Module | What it owns |
|---|---|
| `prompt.py` | Conversation rows → the message list an LLM is called with. `build_messages`, `history_messages` (only `depth=0` turns replay), `tool_turn_messages` (a past TOOL turn as exactly two messages — the SAME function the live loop calls when a tool runs, so replay and live append can never drift apart). The TOOL message carries the turn's result text AND, when it recorded any, its `artifacts` appended as one `[artifacts: output:36]` line — a reference is the exact string a later tool call takes back (an image tool's `image` param wants `output:36`), so a reference that reaches only the database and the page leaves the model unable to name what it just made. When `ChatSettings.time_aware` is on (the default, round 21) the system message also ends with one server-generated line naming the current day, date, time and UTC offset, and every replayed USER/ASSISTANT/SYSTEM turn carries a `[YYYY-MM-DD HH:MM] ` prefix from its own `created_at` — TOOL turns never do, because the live loop appends that pair unprefixed. `time_aware_now_line()` is the shared entry point for that line — `delegate.py` calls it too, so a delegated agent is told the date on the same toggle and in the same words. A tool result is **fenced** (S2) by PROVENANCE, not by its outermost key alone (H5 review round 1, CRITICAL): the two retrieval runners themselves (`rag.search`, `rag.ask` — the only tools whose result text is somebody else's bytes, chunk content out of a stored document), a `flow.run` whose LAST step (`data["steps"][-1]["tool"]`) was one of those two (`flow.run`'s own result text IS that step's text, character for character — `agents/runtime/flow.py::_finished`/`_degraded`), and any `agent.<slug>` delegate (a model's own synthesised answer, the same class of content as `rag.ask`'s, fenced by PREFIX rather than a maintained list of every agent slug). `_is_third_party_tool_result` makes this call from a turn's OWN recorded `tool`/`data`, never re-derived; `_tool_content` wraps the result of a `yes` in the SAME shape an attached file's text is wrapped in — an explicit header stating the content is DATA and never instructions, a per-call random BEGIN/END marker the content cannot pre-guess, and every fence-like dash run inside the body neutralised (`_fence_tool_result_text`, always wrapped, never content-sniffed — H5 review round 3, finding 1 reverses round 2's own finding 2 here: the round-2 idempotence guard, which skipped the wrap when the text already started with the header sentence, was itself a forgeable bypass, since that sentence is fixed and documented; wrapping again is safe because the outer marker is fresh on every call, so a doubled fence nests the earlier one as inert data rather than ever being sniffed and skipped). Every non-third-party tool's result is text this platform wrote itself and passes through untouched, and the `[artifacts: …]` line always stays outside the fence — it is our own reference, not the document's content. An ASSISTANT turn's own honest ending (`_honest_ending`/`_two_failures_text` in `loop.py`, when a turn runs out of time or steps or fails twice) can bake one of these same raw tool results directly into `Turn.text` — and `Turn.text` there is always the UNFENCED text a human actually reads in the chat bubble (H5 review round 2, finding 1: round 1 fenced it AT THAT WRITE, which put the DATA header and marker lines in front of the reader too). The fence for THAT text is applied only at REPLAY time instead: `loop.py` records the raw text's provenance onto `Turn.data["appended_tool_result"]` the moment it composes the honest ending, and `history_messages`' own `_replay_assistant_text` reconstructs the fenced version from that record — on a copy, for a LATER turn's own prompt — every time this turn is replayed, never touching the stored row. Each recorded entry carries `"offset"`/`"length"` too (H5 review round 3, finding 2): `loop.py`'s `_honest_ending`/`_two_failures_text` compute a raw substring's own exact position within the composed text directly from the fixed template pieces AS they build it, so `_replay_assistant_text` trusts that recorded position — after checking `turn.text[offset:offset + length]` still equals the entry's own recorded raw text — rather than re-finding it with a string search on every replay; an entry whose offset no longer matches is skipped on its own, leaving that one portion of the text untouched, and (finding 3) a `turn.data` that is not even a dict degrades the same way, the identical `isinstance(turn.data, dict)` guard `agents.chat.rendering.citations_of` uses. The fence is not a proof, and the module's comment says so: it makes the model's read order unambiguous about whose words these are. What actually bounds a poisoned document is elsewhere and holds regardless — retrieval visibility is derived server-side from the acting principal, and every mutating tool is dropped from the offered set, so no document can steer the model into another entitlement's material. C-1 (security round 3, H25) applies the SAME fence one hop earlier, to a REPLAYED USER turn's own text: `agents.models.Turn.author` records who wrote a USER or TOOL turn (`start_turn`'s and `run_loop`'s own writes, `None` for "not attributable"), and `history_messages` wraps a USER turn's replayed content in `_fence_foreign_turn_text` (`_FOREIGN_TURN_HEADER`, `_carrying_delimiter`, `_neutralize_fence_lines` — the identical three primitives, never a second fence) whenever its recorded `author_id` names somebody other than `build_messages`' own `principal` argument. An author-less row (every turn written before H25) is fenced this way only when its conversation actually names a share recipient at all (`_conversation_has_a_share_recipient`) — a loose, never-shared conversation's author-less rows replay unchanged, and an open-posture box, which mints no user and so has no `Share` rows at all, is never affected. `may_post_to` is untouched: this closes the REPLAY half of the finding, not who may post. The attachments block (`_attachments_block`) names every attached file with its status AND its artifact reference (`reference document:<id>`, the bare form, because a model retypes it into a tool call), and an image row also carries a one-line caption read model-free out of the extraction the ingest job already stored — or, when there is none, the honest line for WHY: `description not ready yet` (extraction still running), `no text or description could be extracted from this image` (it finished and found nothing), or `no description recorded; re-ingest to describe it` (it was ingested before this box described images at all) — read off the provider's own `caption_state`, and derived the pre-2026-09-17 way (a caption present means ready) for a row that carries no such key. One more steering clause is offered, and only when this turn actually holds the image tool and something attached is actually an image: "To edit an attached image, or use it as a reference while editing another, pass the reference exactly as written to the image tool." — the same `available`-dict gate the retrieval sentence already uses, so the model is never steered toward a tool it does not hold. The clause is gated on the tool being granted, not on whether the acting principal could resolve the reference — for a non-uploader in a shared conversation a chat-scoped image's reference refuses with the standard dead-reference sentence, which is accepted and documented rather than pre-empted (closing it would mean teaching the prompt a second visibility predicate about a column it may not import). The caption is bounded and single-lined HERE as well as in the providing column (`_MAX_ATTACHMENT_CAPTION_LEN`, a literal — the two columns may not import one another): it is text a model extracted from a file somebody uploaded, landing in the most-trusted channel this platform has. TASK 3 (native image input, gated) adds a SECOND way a carried image reaches the carrying message: `native_media_types(resolved)` reads `models.contracts.catalog.CatalogEntry.accepts` for THIS turn's own bound model (`agents.runtime.bindings.resolve_chat`'s return, threaded through `build_messages`' new `resolved=` keyword), and when it reports `"image"` AND a principal is present, each carried image row is resolved through `agents.attachments.attachment_image_path` (the artifact-file registry, gated on `readable_document` exactly like `tools.rag.access.artifact_file_for`'s own file route) and, if the path loads under `_NATIVE_IMAGE_COUNT_CAP`/`_NATIVE_IMAGE_BYTE_CAP` (`ImageBlock`/`resolve_image()` forced at build time, never left for later), rides the carrying USER message as a real `ImageBlock` beside its `TextBlock` — that file's line becomes one marker sentence ("attached image provided to you directly") instead of `_INLINE_STILL_PROCESSING_LINE`. `_carrying_attachments_block` therefore returns `list[ContentBlock]`, never a bare `str`; any failure (no principal, no `resolved`, an unrecognised model_id, a missing/oversized/unloadable file, or past the count cap) falls back to the EXACT pre-Task-3 rendering, byte-identical down to the `_INLINE_STILL_PROCESSING_LINE` sentence a gate-closed call always produced. KNOWN LIMIT, recorded again in ADR 0010: the gate reads the catalog's fixed model-id list, not a live per-connection capability, so a multimodal connection outside it still gets the still-processing fallback. |
| `invoke.py` | One tool call, start to finish: `invoke_tool` validates against the spec, resolves the runner by dotted path, runs it, classifies the result into one of five outcomes, and writes the `ToolInvocation` audit row. `invoke_unknown_tool` handles a wire name that maps to no registered tool. `invoke_tool` also asks `tool_ctx.tool_access.allows(spec.key)` itself, immediately after the audit row is created and before validation runs (C-2, security round 3, H31) — the SAME predicate `agents.contracts.tools.granted_tools` applies when it filters an agent's own key list, asked again HERE so that a caller reaching a tool through some other door (a flow's step, in this instance) cannot skip the question. A refusal here is RETURNED as a `ToolOutcome` in `Outcome.REFUSED`, never raised — see "The tool-entitlement question is asked once, at the seam" below. |
| `audit.py` | How an audit row reads (`finished_at IS NULL` is *running*, not *error*) and the one conditional UPDATE that closes a turn's open rows. |
| `loop.py` | `run_turn` (the `agent.turn` handler) and `run_loop` (the bounded ReAct loop itself, extracted so a delegate can call it too). One tool call per iteration; `calls[0]` taken, the rest discarded and reported. |
| `jobs.py` | The `agent.turn` job kind's other three dotted paths: `plan_turn` (the admission snapshot), `summarize_turn` (a one-line `/queue/` row, touches no database), and `on_turn_terminal` (fixes up a stranded placeholder turn). **`on_turn_terminal` is driven by the JOB ROW's own terminal write, so it cannot help a turn whose job row is GONE** — that case is `agents/reconcile.py`, deliberately outside this package because its condition is a `models.contracts.queue.get_job` call and `test_no_runtime_module_blocks_on_a_queue_job` forbids one here. See [`agents/README.md`](../README.md)'s module table. |
| `delegate.py` | Agent-as-tool (spec section 6.4, deviation D4): `run_agent_tool`, the one runner every `agent.<slug>` `ToolSpec` shares. Runs a nested loop inline, on the SAME shared `StepBudget`, in the SAME job. Builds its own two-message list rather than calling `build_messages` — a delegate inherits neither the parent's history, nor the stream's instructions prose, nor the attachments block — but it DOES get the clock line, through `prompt.time_aware_now_line()` and the same operator toggle (round 21). |
| `bindings.py` | `resolve_chat(agent, connection, *, access)` — the ONE function `plan_turn` and `run_turn` (and `agent_turn`'s own preflight) all call to answer "which model answers this turn", so the three can never disagree. `access` (IA-2 T14, a `models.registry.access.ModelAccess`) is consulted only when `connection` is not `None` — the role fallback is the exempt path (spec section 9.5/22.32), so a set holding, say, the embedding connection never breaks ingestion for the whole box. `principal_for(agent)` used to live here too, answering "who does a turn by this agent run as" with a principal MINTED FROM THE AGENT — deleted outright in Task 11 (the acting rule, spec section 5.3): a turn runs as the USER named in its payload (`identity.contracts.principals.principal_from_payload`), never as the agent, so the question itself was wrong, not merely misplaced. |
| `preflight.py` | "Can this agent take a turn at all," asked once and answered the same way for the CLI (`manage.py agent_turn`) and `/chat/` (P3 Task 9). `preflight_turn(agent, connection, *, actor) -> Preflight` raises spec section 10.1's first three refusals (unbound chat role, a picked connection that no longer resolves, a bound model that cannot call tools an agent's granted tools need) BEFORE anything is written, so a turn that cannot possibly run never becomes a queued job somebody has to go cancel. It does **not** replace `loop._run_turn`'s own tool-calling check inside the job — that one is the last-resort honest ending for a turn that got queued anyway (a binding can change between enqueue and run, ADR 0013:205-213), and a queued job never trusts an enqueue-time decision as its run-time truth. Two checks, two different jobs: this module keeps the queue clean of doomed turns; `loop.py`'s own check keeps a turn that got queued honest about what actually ran. |
| `flow.py` | A flow's execution half (P3 Task 12; deviation P3-D5, P3-D10): `run_flow`, the registered runner behind the one `flow.run` tool, loads the `Flow` ROW named by `args["flow"]` through `agents.visibility.visible_flows` and runs its steps via `_run_steps`. Every step goes through the SAME `invoke_tool` this loop uses — same validation floor, same audit row, same shared `StepBudget`, same five outcome classes, and (C-2, security round 3, H31) the SAME tool-entitlement question, asked inside `invoke_tool` itself rather than re-implemented here. `_run_steps`'s own step loop still refuses a `mutates=True` step directly (ADR 0010:266-276's "registered but not grantable" — a check `invoke_tool` cannot make, since whether a key is mutating is a property of the registered spec, not of who is asking), but it asks `ctx.tool_access` nothing at all; a step naming a tool the acting principal is not entitled to reaches `invoke_tool`, is refused there, and `_run_steps`'s existing `outcome.bars_retry` handling re-raises `ToolRefused` exactly as it does for any other refused step. `resolve_ref`/`resolve_args` resolve a step's `$input.<key>`/`$steps.<N>.<field>` references (the exact grammar `agents/defaults.py`'s validator already checks at declaration time), raising `FlowReferenceError` rather than ever substituting `None`. |
| `flowtool.py` | The flow's registration half (P3 Task 13; deviation P3-D11): the ONE `flow.run` `ToolSpec` (`FLOW_RUN`), registered at `ready()` time with `choices=()` because a `Flow` row cannot be reached from there (rule 3 below). `narrowed_flow_spec` fills the real, enabled slugs in per turn (`agents.visibility.visible_flows`, never the catalogue); `flow_row_roles` supplies `flow.run`'s roles the same way at enqueue time, since a flow's roles live in rows, not on the spec. |

`delegate.py` and `bindings.py` are the two modules that exist *because*
agent-as-tool and the picker override do; `prompt.py`, `invoke.py`,
`loop.py`, and `jobs.py` are the four that would exist even for an agent
with no tools and no delegates at all — the turn's own lifecycle.

## The loop's five endings

`run_loop` (`loop.py`) ends exactly one of five ways, and the assistant
turn always says honestly which:

1. **An answer** — the model returned a response with no tool call, and
   that response has real text.
2. **Out of steps** — `budget.exhausted`: `Agent.max_steps` (default
   `MAX_STEPS_DEFAULT`, `agents/limits.py`) LLM calls have been spent,
   including calls that failed.
3. **Out of time** — `budget.expired`: `TURN_DEADLINE_SECONDS` of wall
   clock have passed. Checked at the top of every iteration and again
   immediately before every tool call, never *during* one — see "Post-
   deadline latency" below.
4. **Two failures** — `budget.recoveries_left` reached zero (see "The
   recovery policy" below) and the loop stops rather than guess at an
   answer, naming every failure it saw.
5. **A tool-incapable model** — the bound engine reports (`False`, not
   `None`) that it cannot call tools, and the agent was granted at least
   one. Checked once, before the loop starts (`supports_tool_calling`),
   because a model that cannot call tools cannot usefully enter a loop
   built around calling them.

Every ending that has a tool result to show appends it: `_honest_ending`
never claims a tool "returned" something when none has run yet this turn.

## The recovery policy

`StepBudget.recoveries_left` (`agents/contracts/tools.py`) starts at `1`
— **one recovery per turn**, not per call. Every failed call spends a
step (LLM calls are the unit the budget is denominated in) whether or not
it also spends a recovery:

| Outcome | Retries | What happens next |
|---|---|---|
| `ok` | — | not a failure; the loop continues normally |
| `degraded` | — | not a failure (`ToolOutcome.failed` is `False`); an OPT-IN a runner declares with `data["degraded"] = True` — it cannot be inferred from a `ToolResult` generically. `agents.runtime.flow._degraded` (P3 Task 12) is its first shipped producer: a flow that ran out of time or budget partway through, whose already-completed steps' work is worth keeping rather than discarding |
| `refused` | **0** | no retry for THIS call — nothing the model could say would change the fact; the tool is DROPPED from the rest of the turn (`bars_retry`, enforcement by omission) so the model is never offered it again. But `loop.py` spends the turn's one recovery on it exactly like any other failure — a refusal is not exempt from `budget.recoveries_left -= 1` — so a LATER `param_error` in the same turn, with no recovery left to spend, is not retried either and ends the turn at ending 4. |
| `param_error` | **1**, if a recovery remains | the model is shown `.errors` (a per-arg reason) and may retry with corrected arguments |
| `error` | **0** | not repairable by rephrasing an argument — a missing row, a dead reference, an unclassified exception |

A second failure in the same turn — of ANY class, once
`recoveries_left` is already spent — ends the turn at ending 4 above
(`_two_failures_text`), naming every failure the model's calls produced.
A refusal still spends the single recovery even though it earns no retry
of its own: `loop.py` decrements `budget.recoveries_left` for every
failed outcome uniformly, so "0 retries" in the table above describes
what happens to THAT call, not whether the turn's one recovery survives
it.
See `agents/contracts/README.md`'s "Two failure classes, and two more
outcomes beside them" for the full except-ordering rule this table rests
on (`ToolRefused`/`ParamError` are both `ValueError` subclasses, and the
except clauses in `invoke.py` must catch the narrower two first).

**The catch-all `error` (C-6, security round 3, H38) does not hand the
model or the page a third-party library's own words.** `ToolRefused`,
`ParamError` and the plain `ValueError` branch above it are deliberate,
platform-authored copy — but `except Exception` fires only on a genuine
bug, and its exception's `str(exc)` is whatever a database driver, the
filesystem, or an HTTP client chose to say: a query error, an absolute
path, an engine endpoint. That string becomes `ToolOutcome.text`, is
written into the `Turn`, replays into every later prompt, and renders on
the tool card — reachable by a share recipient (C-1, H25). So `text` is
a fixed sentence naming the tool key and the exception's class only
(`"{tool} failed unexpectedly ({ExceptionClassName}). The operator can
look this up as invocation {id}."`), and the invocation id is what joins
it back to the full detail: the complete `str(exc)` still goes into
`ToolInvocation.error` (never `text` — see rule 2 below) and into the
log via `logger.exception`, at error level, both unredacted, both for
the operator only.

## Reading an invocation

`ToolInvocation` is created BEFORE its runner runs, with `outcome=ERROR`
as a placeholder `invoke.py::_finish` overwrites once it knows better —
so a row exists even if the process dies, but `outcome` alone is never
a readable state. Two rules, both in `audit.py`, and every reader (a
future renderer included) goes through them rather than reading the
columns directly:

1. **`finished_at IS NULL` means "running", whatever `outcome` says.**
   `invocation_state(invocation)` is the one place this is decided —
   `"running"` while unfinished, the row's own `outcome` once it is,
   `""` for no row at all (`Turn.invocation` is `SET_NULL`, and a pruned
   audit row's outcome is genuinely unknown, not a failure).
2. **On a failure, the words are in `error`, not `text`.** `_finish`
   writes `text` only when a `ToolResult` came back, so every refusal,
   param error, and raise leaves it blank. `invocation_message(invocation,
   turn_text)` reads `Turn.text` first (the fuller sentence the model was
   actually told), then falls back to `invocation.error`, never to
   `invocation.text`.

**`ToolInvocation.agent_slug` (IA-2) is stamped by `invoke_tool` itself**,
from `ToolContext.agent_slug` — the same field "`ToolContext.agent_slug`
and the acting rule" below threads through the loop and every delegation
hop. Before this column existed, "which agent made this call" survived
only in `Turn.tool_call["agent"]`; now the audit table can answer it
directly, for a root call and a delegate's alike, without joining back
through `Turn`.

A crashed call — the process dying between the runner returning and
`_finish`'s write — leaves a row open forever with nothing to close it.
`close_open_invocations(queue_job_id, reason=...)` is that closer: one
conditional UPDATE over every unfinished row stamped with a queue job
id, called from both terminal paths (`jobs.py::on_turn_terminal` when
the handler never started; `loop.py::run_turn`'s own `except` when it
ran and raised). It never raises — both callers are already handling a
failure of their own.

## Post-deadline latency is real, and is not fixed here

`budget.expired` is checked at the top of every loop iteration and
immediately before every tool call — **never during one**. A tool that
enters a long wait just under the deadline (`tools.vision.services.
wait_for` polls the image engine for up to its own timeout, longer than a
turn's remaining time can be) can return well after the turn's own
deadline has passed. The turn then ends at ending 3 above with the honest
deadline sentence **plus** whatever that tool's call actually returned.
Killing a tool mid-flight would strand a submitted generation and throw
away work that really happened; ending late and saying so honestly is the
better failure. This is a known, accepted latency bound, not a bug to
file — see `loop.py`'s own module docstring for the fuller reasoning.

## `ToolContext.agent_slug` and the acting rule (Tasks 10-11)

See [`identity/README.md`](../../identity/README.md) for `identity.
contracts.principals.principal_from_payload` and the acting-principal
seam this section relies on.

`agents/contracts/tools.py::ToolContext` carries a second identity field,
`agent_slug`, alongside `principal`: `principal` is WHO THIS IS BEING
DONE FOR, `agent_slug` is WHOSE TOOL DECLARATION IS IN FORCE — two facts
the acting rule (spec section 5.3) needs told apart, because a delegate
(`delegate.py::run_agent_tool`) carries the ROOT caller's `principal`
while running as its OWN agent's tool list, and one field cannot answer
both questions at once. See `agents/contracts/README.md`'s
"`ToolContext.agent_slug`" section for the full rationale.

**Task 10 added the field and stamped the acting principal into every
`agent.turn` (and sibling) job payload** (ADR 0013's 2026-08-30
amendment) — but nothing in this package read it back yet.

**Task 11 wires it up, end to end.** `loop.py::_run_turn` derives the
turn's `principal` from the payload with `identity.contracts.
principals.principal_from_payload` (replacing `bindings.py::
principal_for`, now deleted) and `run_loop`'s own `ToolContext` build
stamps `agent_slug=agent.slug` at every depth — the conversation's agent
for a root turn, the delegate's own agent for a nested one.
`delegate.py::run_agent_tool` deletes its own `principal_for(agent)`
call and passes `ctx.principal` straight through, both to
`available_tools` and into the nested `run_loop` call, so the delegate's
own `ToolContext` carries the SAME principal as the root's and only
`agent_slug` differs. `jobs.py::_tool_roles(agent, actor)` (was
`_tool_roles(agent)`) and `preflight.py::preflight_turn(agent,
connection, *, actor)` (was `preflight_turn(agent, connection)`) both
take the acting principal as an explicit argument rather than deriving
one from the agent, so the enqueue-time and run-time questions are
asked identically.

## `ToolAccess` and the acting rule's grant half (IA-2 Task 5)

`agent_slug` (above) is WHOSE DECLARATION IS IN FORCE; `ToolContext.
tool_access` is WHAT THAT DECLARATION IS ACTUALLY ALLOWED TO BECOME —
the acting principal's own entitlements, as the pure `agents.contracts.
tools.ToolAccess` value `agents.entitlements.tool_access_for(principal)`
builds. `jobs.py::_tool_roles(agent, actor)` became `_tool_roles(agent,
actor, access)`, `loop.py::available_tools(principal, agent)` became
`available_tools(principal, agent, access=UNRESTRICTED_TOOL_ACCESS)`,
and `run_loop` gained the same `access` keyword so every `ToolContext`
it builds stamps `tool_access=access` alongside `agent_slug=agent.slug`
— never recomputed per hop. `_run_turn` and `plan_turn` each build
`access = tool_access_for(principal)` once, beside the `principal`/
`actor` they already derive from the payload, and thread it down;
`delegate.py::run_agent_tool` reuses `ctx.tool_access` verbatim rather
than building a second one — the SAME "no hop widens it" shape
`agent_slug` already has, applied to what an agent may call rather than
whose declaration is in force. `available_tools`' own tool-list
computation was moved ahead of the delegate's conversation-id check
(neither depends on the other, and what a delegate may call should not
wait on a conversation existing).

`preflight.py::Preflight` gained `unentitled_tools: tuple[str, ...] =
()`, kept apart from `dropped_tools`: a key absent from the registry is
reported to an operator ("not available on this install"); a key the
acting principal simply lacks an entitlement for is rendered NOWHERE —
enforcement by omission, Decision 17 — because naming it would be a
false sentence about a tool the entitlement was meant to keep out of
that principal's way. `dropped_tool_notes` still reads `dropped_tools`
only.

### The tool-entitlement question is asked once, at the seam (C-2, security round 3, H31)

`agents.contracts.tools.granted_tools` is a FILTER for what an agent is
*offered* — it is what keeps an unentitled tool out of the list a model
ever sees, so the model is never tempted by a door it cannot open. It is
**not** the only gate on what may actually be *called*: `invoke_tool`
(`invoke.py`) asks `tool_ctx.tool_access.allows(spec.key)` itself,
immediately after the audit row is created and before `validate_tool_args`
runs, for every call that reaches it regardless of which caller built the
`args`. That is deliberate — `granted_tools` runs once, over an AGENT's
declared `tool_keys`, and never sees a `Flow`'s own `steps`; a flow's step
loop used to re-implement only the `mutates=True` half of that filter (the
"LOAD-BEARING" comment beside that guard in `flow.py` says why) and asked
the entitlement question nowhere, which meant a flow step could call a
tool the acting principal held no entitlement for, including inside a
walled workstream, where `tool_access_for`'s own `wall=` narrows `held` to
the intersection with the wall. Asking again in `invoke_tool` closes that
without a second, differently-worded check in every caller: `delegate.py`
already reused `ctx.tool_access` at its own hop (`agents/runtime/tests/
test_tool_access.py::TestTheDelegationHop`) and keeps doing so unchanged;
a flow step now gets the same answer for free, through the one function
every tool call already passes through.

The refusal is **returned**, never raised: `invoke_tool` never propagates
(see `invoke.py`'s own module docstring on the except-ordering contract),
and `ToolRefused` is itself a `ValueError` subclass, so a `raise` placed
inside `invoke_tool`'s own `try` would be silently reclassified by its own
chain rather than reaching a caller as a refusal. The check therefore sits
*before* that `try`, and finishes the audit row with
`ToolInvocation.Outcome.REFUSED` directly — the identical shape the
`except ToolRefused` branch already produces, so a call refused for lacking
an entitlement is audited exactly like a call a runner refused itself.

## The four structural rules

See [`../README.md`](../README.md)'s "The four structural rules this
column exists to hold" — this package is what those rules are actually
guarding: no `tools.*` import anywhere in it (checked by AST, not just at
module scope), no `get_job` call in any of its swept modules
(`foundation/ops/tests/test_column_boundaries.py::RUNTIME_MODULES` — one
entry per module in this package except `__init__.py`, derived from the
directory listing rather than hand-counted, so this sentence never has
to name a number that goes stale), and every dotted path it registers
(`agent.turn`'s four, `agent.library`, `agent.illustrator`, `flow.run`)
actually resolving (`models/registry/tests/test_registry_paths.py`).

Design: `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md`.
