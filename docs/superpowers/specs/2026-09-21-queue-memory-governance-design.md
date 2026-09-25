# Queue memory governance — design

**Date:** 2026-09-21
**Status:** Final, revision 5 (review rounds 1–3 + engine-steward amendment applied — see §13)
**Columns touched:** `models/queue`, `models/registry` (bindings, connection facts,
console labels), `models/contracts` (job-kind registry, engine seam docs),
`agents/chat`, `agents/runtime`
**Consumes, never modifies:** the engine adapters behind `loaded_footprint` /
`unload` / `list_installed`. Two one-line adapter declarations are **flagged** to
their stewards as a cross-column dependency (§3.3a); nothing in this spec edits
an adapter.

## 1. Why this exists

The queue (ADR 0013) admits work against *declared* footprints and then asks the
engines to make the machine match that plan. Every mechanism in that sentence has
now been exercised against real hardware, and each one produced a field incident:
a host out-of-memory crash, a declared footprint that silently fell from ~26 GB to
~6 GB, a cold load that stalled the whole worker process for roughly 500 s and got
its own job orphaned, an exclusive job launched before the memory it was promised
had actually been released, a chat turn stranded at "working" forever after a
database crash, and an eviction pass that is a complete no-op whenever the operator
has not set a memory budget.

Findings Q1–Q16 came out of those incidents, plus one boot-time defect. **Q7
(reclaimed-attempt bookkeeping) already landed on 2026-08-25**; §3.4(d) is its one
remaining half, so the table below groups the fifteen still-open findings. This
spec is the design authority for fixing all of them as one coherent track: they are
four mechanisms (*what a model costs*, *what is actually resident*, *what keeps a
job alive*, and *what a waiting surface says when the answer is late*) seen from
fifteen angles.

Every mechanism below traces to a numbered finding. Nothing here is built because
it would be nice to have.

### 1.1 The findings, grouped as they are designed

| Group | Findings | One-line shape |
|---|---|---|
| A. What a model costs | Q1, Q9 | A four-rung provenance ladder, labelled honestly, that keeps the largest plausible reading and can never be walked down. |
| B. What is actually resident | Q2, Q4, Q5, Q11, Q13, Q14 | Eviction sweeps every registered engine, respects what `unload` actually does on each engine, barriers before an exclusive launch without destroying live work, protects a still-loading attempt, and says out loud what it did. |
| C. What keeps a job alive | Q8, Q6, Q10, boot race | A heartbeat that survives a cold load and a sleeping host, staleness that knows what kind of work it is watching, wait ceilings owned by the kind, and a worker that tolerates racing `migrate`. |
| D. What a waiting surface says | Q12, Q15, Q16 | A loud unset budget, reconciliation for turns whose job row vanished, and a poll loop that cannot tick silently forever. |
| E. Ordering | Q3 | Model-affinity batching within a priority, bounded by an aging rule. |

## 2. What exists today (so nobody rebuilds it)

- **Footprint resolution.** `models/registry/bindings.py::footprint_for` is the one
  path from a model ref to a number; a connection's `effective_footprint_bytes` is
  `footprint_override_bytes` (operator) else `measured_footprint_bytes` (written by
  the worker after every run through `record_measured_footprint`) else `None`.
  `None` means unknown, and the scheduler's rule 2b makes an unknown-footprint job
  effectively exclusive. `ModelConnection.footprint_source` returns
  `"connection"` / `"engine"` / `None`, keyed to `models/registry/views.py`'s
  `_SOURCE_LABELS` — a dictionary whose **first** job is labelling
  `DiscoveryRow.capability_source` for the capability/embed-dim disclosure, reused
  for footprints.
- **Admission.** `models/queue/scheduler.py::plan_admissions` is pure, stdlib-only,
  and walks candidates in strict `(priority, id)` order (it sorts defensively
  inside itself), stopping at the first one that cannot be admitted (no backfill —
  the deadlock proof rests on it). It returns admitted ids and nothing else.
  `models/queue/claim.py::claim_and_admit` builds its inputs from live rows inside
  one advisory-lock transaction, re-resolving every footprint each round, over a
  `CANDIDATE_WINDOW` of 200 rows; it also runs `_sweep_orphans` inside that same
  transaction, against **one scalar** cutoff threaded in from the worker.
- **Eviction.** `Worker._evict_to_match_plan` **returns immediately when
  `memory_budget_bytes is None`**, and otherwise runs four phases: targets from
  `RUNNING` rows, a residency snapshot via `list_installed`, an uncapped unload pass
  over the admitted-exclusive job's *own* endpoints, and a capped budget-driven pass
  over the rest. Every unload result except `False` is silent.
- **What `unload` actually does.** The seam's base contract is a floor ("best-effort
  request… `True` only if the engine ACCEPTED"), and the two live adapters sit at
  opposite ends of it. The image engine's adapter **ignores `model_id` and frees
  every model at the endpoint**, then polls until free memory rises by
  `max(256 MiB, known footprint // 2)` or 30 s elapse; it returns `False` both when
  memory is genuinely still held **and when nothing rose because there was nothing
  to free**. Its residency belief is a TTL'd, process-local memo a restart loses.
  The text engine's adapter unloads one named model. Nothing in the seam tells a
  caller which of the two it is holding.
- **Liveness.** One heartbeat writer (`Worker._maybe_heartbeat`), called from the
  tick thread and from inside the uncapped eviction pass, throttled to 10 s;
  `STALE_AFTER_SECONDS = 120` is one global cutoff. `_active_tokens` is keyed by job
  id with a token-conditional pop; `_futures` is keyed per attempt;
  `_requeue_unlaunched` pops the job id from `_active_tokens` entirely.
  `close_old_connections()` runs once per tick, on the tick thread; Django
  connections are thread-local.
- **Wait ceilings.** `JobSettings.response_timeout_seconds` is the one *response*
  timeout, read in `_build_job_context` (one `get_solo()` per job execution) and
  stamped onto `JobContext`. There is no per-kind *wait* ceiling.
- **Chat.** `agent.turn` is an exclusive job kind with an `on_terminal` hook
  (`on_turn_terminal`) that closes a placeholder turn and its open invocation rows
  for the three handler-never-started paths. `turn_status` answers the pending
  card's poll and touches the database (principal resolution, turn visibility)
  before any body builder runs. The chat page runs its own inline poll loop under a
  dated, self-deleting exemption in `foundation/ops/tests/test_shared_poller.py`.

**What does not exist today:** any rung between "measured" and "unknown"; any rule
about lowering a measurement; any awareness of an idle engine holding memory; any
awareness of what `unload` frees; any unload log line on success; any heartbeat that
survives its own process being starved; any per-kind staleness or wait ceiling; any
reconciliation for a turn whose job row disappeared; and any loud treatment of an
unset budget.

## 3. Design

### 3.1 Footprint provenance: four rungs, labelled honestly (Q1)

A connection carries three independent facts and derives one answer:

1. `footprint_override_bytes` — **the operator's word.** Unchanged, always wins.
2. `measured_footprint_bytes` / `measured_footprint_at` — **observed**: what the
   engine reported the model occupied right after a run this queue executed.
   Unchanged storage; new write rule (§3.2) and new label.
3. `engine_reported_footprint_bytes` / `engine_reported_footprint_at` — **new,
   third rung**: the size an engine reports for a *loaded* model in a residency
   snapshot the worker is taking anyway (`InstalledModel.loaded_size` inside
   `_residency_snapshot`). No new HTTP call ever: this rung exists precisely because
   the snapshot is already on the wire and its numbers are currently discarded.
   §3.3(f) removes the budget gate that would otherwise have made this rung
   unreachable on the very boxes that need it.
4. `None` — **unknown**, which the scheduler already reads as "this job runs alone".

`effective_footprint_bytes` becomes `override ?? measured ?? engine_reported ?? None`.
`ModelConnection.footprint_source` returns four values instead of three:
`"override"` / `"measured"` / `"engine_reported"` / `None`.

**A separate vocabulary, not the capability one.** `_SOURCE_LABELS` stays exactly as
it is: it is the label map for `DiscoveryRow.capability_source`, where "detected from
the model server" is *true*, and retiring that phrase there would relabel a shipped,
unrelated disclosure. A new `_FOOTPRINT_SOURCE_LABELS` dictionary sits beside it:

| `footprint_source` | Label |
|---|---|
| `override` | set by the operator |
| `measured` | measured after a run on *date* |
| `engine_reported` | from the engine's residency snapshot on *date* |
| `None` | not measured yet — this model runs alone |

The measured rung's label changes because it is the one place the console currently
says something untrue: a post-run delta measurement is *our* observation of the
engine, not the engine's declaration. That is the whole of Q1's "labelled honestly"
requirement.

**Rung 3's label says "residency snapshot", not "reported by the model server", because
the stronger phrasing would be untrue on one of the two live engines.** The steward's
line-by-line check found that the image engine reports no per-model residency or size
at all (`list_installed` carries `size=None` by design); its `loaded_size` is the
adapter's *own* post-run delta, kept in the same TTL'd memo — so on that engine rung 3
is very nearly rung 2 restated, with the same provenance and the same memo lifetime,
and is close to valueless as an independent fact. On the text engine, which answers a
real residency endpoint, rung 3 is genuinely engine-reported and is worth having. One
label has to be honest on both; this one is.

**Named template edit.** `models/registry/templates/inference/_registered_connection.html`
renders `footprint_source_labels.connection` / `.engine` *directly* rather than
through the connection's own `footprint_source`. That block is rewritten to render
one `item.footprint_source_label` resolved in the view from the new dictionary, so
the label and the fact can no longer disagree. Four shipped assertions pin the
current strings (two in `models/registry/tests/test_views_tables_and_picker.py`,
two in `test_views_connection_edit.py`, plus their neighbours at the cited lines) —
they are deliberately re-pinned in §5, and the capability-source assertions among
them must keep their current wording.

The rung-3 write obeys the same recorder rule as rung 2 (§3.2) and is written only
when the snapshot reports a loaded size; a `None` or zero reading writes nothing,
never a zero.

### 3.2 The recorder keeps the maximum (Q9)

Live evidence, twice: a measurement taken while another model was resident, and a
measurement taken after a worker restart lost its residency memo, each *lowered* a
standing footprint (26.4 → 6.4 GB; 15.2 → 11.1 GB). The next admission planned
against the lowered number, evicted nothing, and the machine went down. The engine
half is already fixed engine-side (a model resident at submit makes
`loaded_footprint` return `None`). This track owns the recorder half.

`record_measured_footprint` gains one rule, applied to both the measured and the
engine-reported columns:

- No standing value → write.
- New reading **≥** standing value → write. A footprint going up is always believed;
  under-counting is the direction that crashes hosts.
- New reading **<** standing value → **refuse**. Nothing is written — not the bytes,
  not the timestamp — and one line records the connection, the standing value and
  the refused value. Below a documented "obviously broken" ratio the line is a
  WARNING naming the override as the correction path; a small dip is INFO.

**No tolerance band, deliberately.** A percentage tolerance measured against the
*standing* value ratchets geometrically: five successive "within tolerance" 0.75×
writes walk 26.4 GB down to 6.2 GB and reproduce the exact shape the rule exists to
refuse — and the Q9 condition is *recurring* (that model under-measures whenever it
is warm), so repeated warm runs are the normal path, not an adversarial one. Keeping
the maximum makes the standing value a high-water mark by construction, with no extra
column and no constant to tune. The cost — a genuinely shrunk model stays high until
an operator sets an override — is the safe direction and is already the documented
correction path. §10 item 3 offers the tolerance-band-against-a-high-water-column
variant if the owner prefers it.

The function stays opportunistic and silent-on-ambiguity in every other respect: it
never raises, no-ops on no match or an ambiguous match, and tells no caller whether
the write landed.

### 3.3 Eviction (Q2, Q4, Q5, Q11, Q13, Q14)

The pass, its four-phase split and its degradation idiom are reused. Six changes.

#### (a) The queue must know what `unload` frees, and what a residency report is worth

Everything below depends on a fact the seam does not currently expose: the image
adapter's `unload` **frees every model at the endpoint and ignores `model_id`**,
while the text adapter's frees one named model. A caller that assumes per-model
granularity will evict a needed checkpoint out from under a live attempt; a caller
that assumes endpoint-wide granularity will refuse to evict an unneeded model that
shares an endpoint with a needed one.

The seam gains an **optional, documented class attribute**, read the same way every
other optional engine capability is read (`getattr`, degrade, never `hasattr`
branching):

```
InferenceEngine.unload_scope: str          # "model" | "endpoint"
InferenceEngine.residency_authority: str   # "endpoint" | "memo"
```

`unload_scope` — what a call frees:

- `"model"` — `unload(endpoint, model_id)` releases that model and leaves others.
- `"endpoint"` — the call releases everything at the endpoint; `model_id` is
  addressing, not selection.
- **Absent → the queue assumes `"endpoint"`**, the safe assumption: it can cost a
  needless reload, never a destroyed cold load.

`residency_authority` — **how much the engine's own residency report is worth**, which
§3.3(d)(3)'s narrowing depends on and which nothing else carries:

- `"endpoint"` — `list_installed`'s `loaded` flags come from a real residency endpoint
  the engine answers live. "Nothing resident" from such an engine is a fact.
- `"memo"` — they come from a TTL'd, process-local belief the adapter maintains itself.
  "Nothing resident" from such an engine means "this process does not remember
  anything", which a restart alone can produce.
- **Absent → the queue assumes `"memo"`**, the safe default: it triggers the
  precautionary barrier call rather than trusting an empty answer.

This second attribute exists because the round-2 narrowing as first written was
**unimplementable**: "a TTL'd memo rather than a real residency endpoint" is knowable
only by reading adapter source, and no seam carried it. A declaration does.

**Cross-column, flagged not specced — and pre-cleared.** Declaring these on an adapter
is a one-line-each edit inside code this track does not own. The **image adapter's two
lines (`unload_scope="endpoint"`, `residency_authority="memo"`) are PRE-CLEARED by the
engine steward**, who verified §3.1–§3.3 against the adapters and will take those lines
in their own column when the plan lands. The **text adapter's lines are that engine
steward's call**; factually they are `"model"` and `"endpoint"`-authority, and the
queue's behaviour is consistent either way. Until any declaration lands, the safe
defaults apply: eviction is coarser on a per-model engine, and an authoritative engine
is given a precautionary call it does not need — both accepted, named interim costs
(§11), neither a correctness gap.

#### (b) Residency is a belief, and unloading is not free (Q5)

`loaded` from `list_installed` is documented, in the pass and in the ADR amendment,
as a **belief**: on an engine with no residency endpoint it is a TTL'd, process-local
memo that a restart loses and that can name one model for an endpoint holding
several files. "Nothing believed resident" is therefore never evidence that an
endpoint is empty.

The corollary the first draft of this spec got **wrong** and this revision corrects:
*unloading a model that is not actually resident is not always a harmless no-op.*
On an `unload_scope="endpoint"` engine the call frees whatever else is there, and
returns `False` after a 30 s poll when nothing rises. Both facts are load-bearing
below.

#### (c) Protected keys: the one safety rule every unload obeys

`protected_keys` = the model keys of **every currently-`RUNNING` job — this tick's
admitted batch included** — unioned with the model keys of **every attempt this
worker process still has in flight** (the per-attempt futures map, filtered to
futures that are not done).

Both halves matter, for different reasons:

- **The admitted batch is protected.** By the time this pass runs, the claim has
  committed and one `RUNNING` query already covers "running ∪ admitted" — which is
  how today's `needed_keys` is built, and today's code protects the admitted job's
  own model because of it. Excluding the batch would be a **regression against
  shipped behaviour**, and a severe one on this box: an agent turn is planned
  exclusive, so *every chat turn is an exclusive admission that runs the barrier*.
  Its chat model normally sits believed-resident and warm at a model-scope endpoint;
  an unprotected definition would unload it moments before the handler needs it and
  cold-load it again on every single message, on hardware whose cold loads are
  measured in minutes.
- **Live in-flight attempts are protected.** This is Q11: an orphaned-but-not-yet-
  readmitted attempt whose row is briefly back at `queued` while its handler is
  genuinely still mid-cold-load is invisible to a `RUNNING`-derived set, and the
  ~500 s cold load is exactly that window.

No unload call is ever made that could release a protected key:

- at an `unload_scope="model"` endpoint, a protected key is skipped per key —
  today's behaviour, now derived from a set that includes live attempts;
- at an `unload_scope="endpoint"` endpoint, **the whole endpoint is skipped** if any
  protected key lives there, because one call would take them all.

**One sanctioned exception, stated rather than implied.** An
`unload_scope="endpoint"` endpoint that the admitted exclusive job **itself owns** is
barriered even though the job's own model key sits there, because freeing that
endpoint unavoidably takes its own model with it and there is no per-model call to
make instead. That is the "at worst one reload" cost revision 1 accepted, written
here as an explicit exception with a named boundary: it applies **only** to the
admitted job's own keys at its own endpoint, never to another job's key and never to
a live in-flight attempt's key, either of which still skips the endpoint and refuses
the launch (§3.3d(2)).

A skipped endpoint logs one WARNING naming what protected it. This rule governs the
budget-driven pass, the exclusive pass and the barrier alike; it is the single place
the "don't destroy live work" property lives.

#### (d) The exclusive barrier, redesigned against the real contract (Q13)

The requirement is real: the field saw an exclusive job's handler start before the
memory it was promised had been released, loading ≈33 GB on top of a resident
checkpoint. But `False` from the barrier-polling adapter is **ambiguous** — its own
docstring lists "a server that never actually freed anything" *and* the no-rise
timeout under the same return value, and an empty endpoint (the first job after a
restart — the common case) produces `False` after burning 30 s. A rule that refuses
to launch on any `False` means an exclusive job can never start on a clean endpoint:
not "one tick", but every tick, forever.

So the barrier is defined by what the queue actually knows:

1. **Scope.** On a tick that admits an exclusive job, the barrier covers **every
   endpoint in the swept set** (§3.3e), not only the endpoints the admitted job
   owns. A warm model on an idle *foreign* engine is the literal host-crash shape;
   leaving it unbarriered would mean the sweep does not close the incident this
   spec opens with.
2. **Protection first — and before any HTTP.** Any endpoint holding a protected key
   (§3.3c, exception included) means the admitted job is **not launched** this tick:
   something live is still using the machine an exclusive job is entitled to have
   alone. This wait is genuinely bounded (the live attempt ends), unlike the
   empty-endpoint refusal it replaces.

   This check is evaluated **before phase 2's residency snapshot**, not inside the
   barrier loop, and `_evict_to_match_plan`'s four-phase ordering note gains it as a
   **fifth ordering rule**. Nothing about the check needs the network:
   `protected_keys` comes from database rows and the in-process futures map, and each
   key carries its own `(engine, endpoint)`. Evaluating it after the snapshot would
   pay a full widened cross-engine probe — one `list_installed` per registered
   endpoint, each up to a discovery timeout, plus precautionary unload calls — on
   every 0.5 s tick, for the entire life of the protecting attempt, which in the case
   this rule exists for is a ~16-minute cold load.
3. **The calls.** At each remaining endpoint: every believed-resident, non-protected
   model is unloaded (one call at a `"model"` endpoint per model; **one call total**
   at an `"endpoint"` endpoint, since the first frees everything).

   **The precautionary call is narrowed by `residency_authority` (§3.3a), the one
   thing that makes the narrowing implementable.** When `list_installed` was
   unavailable or raised, or when it reports nothing resident *and the endpoint's
   engine declares (or defaults to) `residency_authority="memo"`*, one precautionary
   call is made anyway, addressed with any `model_id` registered for that endpoint
   (`registered_endpoints()` supplies one — §3.3e) or the admitted job's own ref when
   the endpoint is its own. An engine declaring `residency_authority="endpoint"` that
   reports nothing resident gets **no** precautionary call: it actually knows, so
   there is nothing to barrier and nothing its answer could add.

   This narrowing is a real cost, not a tidy-up. At an `unload_scope="endpoint"`
   engine with an empty belief — the case the precautionary call exists for — the
   call runs its settle poll to the **full 30 s unload timeout**, because nothing
   rises, on the tick thread; widening the barrier to the whole swept set (correctly,
   §3.3d(1)) multiplies that by the number of such endpoints on **every** exclusive
   admission, i.e. on every chat turn. §11 prices it, and done-when proof 2(ii)
   carries a timing expectation so a 30 s-per-turn regression is caught on the preview
   rather than in use.

   *(2026-09-24, amended — the regression reached live, on a box registering a
   memo-authority image engine.)* A precautionary call at an endpoint that is **not**
   the admitted job's own is now made with `unload(..., wait=False)`: its answer is
   discarded by §3.3d(4), so the settle poll buys nothing. The job's own endpoints keep
   the poll (the load-on-top race), and so does every believed-resident call. See ADR
   0013's 2026-09-24 amendment, which also corrects proof 2(iii)'s premise.
4. **Reading the answer.** `False` is honoured **only for a call made against a
   believed-resident model** — the one case where it carries information ("memory
   might still be held"). A `False` from a *precautionary* call is logged at INFO
   and **does not block the launch**: there was no reason to believe anything was
   there, and the adapter cannot distinguish "nothing freed" from "nothing to free".
5. **Bounded refusal, bounded in time as well as in count.** Informative refusals for
   the same job are counted (in-process; a restart resets the count, named in §11),
   and **a successful barrier resets the count to zero**.

   Counting ticks alone would be a trap: ticks are half a second apart, and an
   informative `False` is exactly what a *busy* engine returns, since the adapter
   withholds `True` while a prompt is still running — the very property §3.5(b) leans
   on for its partial Q10 enforcement. Three refusals could elapse in barely more
   than the time three unload calls take, so one foreign long-running generation
   would fail every queued exclusive job, i.e. every chat turn, within a couple of
   minutes. Therefore:

   - a barrier-refused job takes a **hold-off** before it may be claimed again
     (`BARRIER_HOLDOFF_SECONDS`, comfortably longer than one unload timeout), so it is
     not re-claimed on every 0.5 s tick and the retries are genuinely spaced;
   - the job may be failed only once it has accumulated `MAX_BARRIER_REFUSALS`
     (**3**) refusals **and** `MIN_BARRIER_REFUSAL_SPAN_SECONDS` of wall clock has
     passed since the first of them, so "three attempts" can never mean "a second and
     a half".

   On both bounds the job is **failed** with an operator-readable error naming the
   engine and endpoint ("the image engine did not release memory after three attempts
   over N minutes"), never silently retried forever. Failing is chosen over
   launching-anyway because launching into genuinely held memory is the host crash
   this whole track exists to prevent; §10 item 6 puts the alternative to the owner.

A refused launch — whether for a protected endpoint or an informative barrier refusal
— hands the claim back through the existing unlaunched-requeue write (state to
`queued`, token cleared, `attempts` untouched — it never ran), applies the hold-off,
and logs one WARNING naming the job, engine and endpoint. The hold-off is the same
mechanism in both cases, so neither refusal can spin the tick loop.

**The hold-off mechanism, named so a plan author does not have to invent one.** A
`not_before` timestamp column on the job row, honoured by the claim code's candidate
query (`not_before IS NULL OR not_before <= now`) alongside the ordering it already
applies. Durable rather than in-process, because the alternative — an in-memory map —
is lost on exactly the restart that also resets the refusal count, and would let a
freshly-restarted worker re-enter the 0.5 s churn immediately. It is written by the
same conditional update that requeues the refused claim, and it is the *only* new
admission-side filter this track adds.

#### (e) The cross-engine sweep (Q4)

Today the endpoint set is derived from the running jobs' own model refs, so a model
left warm on an *idle* engine is never visited. When this tick admits an **exclusive**
job, the endpoint set becomes the union of the running jobs' endpoints and **every
registered engine endpoint**, from a new tolerant seam beside `footprint_for`:

```
models.registry.bindings.registered_endpoints() -> list[tuple[str, str, tuple[str, ...]]]
```

— `(engine, normalized endpoint, model_ids registered there)`. The third element is
what makes §3.3(d)(3) addressable at a *foreign* endpoint, which is otherwise
unreachable by any barrier call. It unions the **configured default endpoints map**
with the endpoints on connection rows, per engine — deriving the set from connection
rows alone would miss a freshly-installed second engine running at its configured
address with no registered connection yet, which is precisely the Q4 scenario. The
existing console helper that already performs that union is refactored to share this
one implementation, so there is a single notion of "every engine endpoint" in the
repository. The helper tolerates the same mid-`migrate` database errors its
neighbours already swallow; endpoints whose engine does not resolve, or offers no
`list_installed`/`unload`, drop out exactly as today (warned once per engine+method).

The wide sweep runs **only on a tick that admits an exclusive job** — the only
admission entitled to the whole machine — which bounds the added HTTP to the rare
case rather than paying it every 0.5 s.

**A stated consequence.** Widening the snapshot also widens the budget arithmetic:
`actual_resident_bytes` (and therefore `over_budget`) now includes idle foreign
endpoints on exclusive-admitting ticks, and the capped pass ranges over them too.
`over_budget` consequently means something slightly more complete on those ticks than
on others. This is taken deliberately — under-counting resident memory is the
direction that crashes hosts — and is named as a residual (§11) rather than left
invisible.

#### (f) The budget gate moves (Q13/Q14 are unconditional)

`_evict_to_match_plan` returns before phase 1 when `memory_budget_bytes is None`, so
in the posture the field actually ran in — no budget set, "nothing offloaded at all
until a budget was finally set" — the barrier, the sweep, the live-attempt protection
and every new log line would be dead code. Q13 and Q14 are unconditional
requirements.

Therefore: **phases 1 and 2 and the exclusive pass (including the barrier) run
regardless of the budget.** Only the *capped, budget-driven* pass 2 stays gated on a
budget, because it is the only mechanism whose decision is arithmetic against a
number that does not exist. `over_budget` is computed only when a budget is set and
is `False` otherwise. §3.1's third rung is filled from the snapshot on every
admitting tick, sequential-mode boxes included — without this change, owner decision
2's stated benefit would never materialise on the boxes that need it most.

#### (g) Say what happened (Q14)

A successful eviction is silent today; `False` is the only thing ever logged. Every
decision and every result gets one INFO line with a stable vocabulary:

- one line per tick that evicts at all: the trigger (`exclusive admission` /
  `over budget`), the endpoint count swept, resident bytes seen, admitted marginal
  bytes, and the budget (or `unset`);
- one line per unload attempt: engine, endpoint, model, scope (`model`/`endpoint`),
  why (`not needed at an exclusive endpoint` / `over budget` / `precautionary
  barrier`), and the result (`accepted` / `refused` / `unavailable`);
- one line per skip a human would otherwise have to infer: an endpoint protected by
  a live attempt, an engine offering no `unload`, a snapshot call that raised, the
  capped pass hitting its cap.

WARNING is reserved for what an operator can act on: an informative barrier refusal,
a protected endpoint blocking an exclusive launch, a job failed after
`MAX_BARRIER_REFUSALS`, and a refused footprint lowering.

### 3.4 Liveness (Q8, and the host-sleep reframe)

**(a) A heartbeat that does not share a thread with the work.** The heartbeat is
written from the tick thread, so a tick that blocks — a synchronous eviction pass, or
a whole process starved during a ~16-minute cold load — stops the heartbeat too, and
the sweep orphans a healthy job. The widened snapshot phase makes this worse before
it makes it better: `_residency_snapshot` contains **no heartbeat call at all**, and
each `list_installed` can cost a full discovery timeout.

A **dedicated daemon heartbeat thread** is started by `run_forever` (never by the
constructor, so a single-tick diagnostic run and the test suite spawn nothing) and
calls the same single writer every `HEARTBEAT_SECONDS`. Its connection story is
explicit, because it is the difference between fixing Q8 and re-creating it: the
thread calls `close_old_connections()` at the top of each iteration (Django
connections are thread-local and nothing else would ever close or health-check this
one), wraps its write so a transient database error is logged and retried on the next
iteration rather than killing the thread, and logs loudly if it ever exits — a
silently dead heartbeat thread would mass-orphan healthy work after a database
restart, which is precisely the failure Q8 exists to remove. The tick's own calls
remain, harmlessly throttled, so `--once` keeps today's protection. The single-writer
rule is preserved by construction: the method stays the only writer of that column,
and its throttle state moves under the existing lock so the two threads cannot
interleave a read-modify-write.

**(b) Staleness that knows what it is watching.** A global 120 s cutoff cannot be
right for both a sub-second embed and a kind that cold-loads a large model. `JobKind`
gains an optional, **code-declared** `stale_after_seconds`.

The sweep is not the worker's to parameterise ad hoc: `_sweep_orphans` runs inside
`claim_and_admit`'s advisory-lock transaction and takes one scalar cutoff threaded in
from the worker precisely because the claim module may not import the worker. Two
named signature changes:

- `claim_and_admit(..., stale_after_seconds: int, sweep_orphans: bool = True)`;
- the kind→threshold map is resolved **inside the claim module**, from the job-kind
  registry it already imports, with `stale_after_seconds` as the fallback for every
  kind that declares none. `_sweep_orphans` groups its query by distinct threshold —
  a handful of kinds, bounded and pinned by a query-count test.

Code-declared, not operator-editable: a kind's staleness is a property of what the
work *does*, in the same spirit as its `default_priority` rung.

**(c) A host that went to sleep.** The container VM ballooning after a host sleep, and
the host sleeping mid-job, produce the same trap: the process's monotonic clock barely
advances while wall-clock hours pass, so on wake every running row looks stale at once
and the sweep mass-orphans healthy work. The worker detects a tick whose **wall-clock**
delta vastly exceeds its **monotonic** delta (`SLEEP_DETECT_SECONDS`), logs it
honestly, writes a fresh heartbeat immediately, and passes `sweep_orphans=False` for
one grace period so live rows can re-stamp themselves before anything judges them.

**(d) No duplicate submit on reclaim — and what the refusal leaves behind.** A job
this process is already executing can be orphaned, re-admitted and launched a second
time *in the same process*: two handlers, one job, one set of models. This is the
unbuilt half of the 2026-08-25 reclaim fix, which repaired the bookkeeping but never
stopped the second execution.

`_launch` refuses to submit a job id that still has a live (not-done) future. The
naive requeue is **not** what it does, because a requeue would leave the row `queued`
with no token tracked, strip the live attempt of heartbeat protection, invite
re-admission 0.5 s later for the entire ~16-minute cold load this exists for (each
tick paying a widened eviction pass), and ultimately discard the live attempt's own
token-conditional writeback so the job runs a third time.

Instead the refusal **restores the row to the live attempt**: a conditional `UPDATE`
puts the row back to `running` under the **live attempt's own claim token**, with
`claimed_by` this worker and a fresh `heartbeat_at`; that token is re-registered in
`_active_tokens`; and the superseding claim is discarded with one WARNING line. The
row is then not a candidate, the live attempt is heartbeat-protected again, and its
eventual writeback matches the row it is writing to. If the conditional update
matches zero rows (another worker legitimately owns it now), the fresh claim is
simply **discarded**: the live attempt is left to finish and discover its writeback
is stale — the existing, documented behaviour — and the foreign owner's row is never
touched.

> **Correction, 2026-09-21 (written against the shipped code, Task 19).** This
> paragraph originally said the zero-rows case "requeued the fresh claim the ordinary
> way". It does not, and cannot. `_restore_to_live_attempt` filters on
> `pk AND claim_token=<the fresh claim's token>`; the fallback it calls,
> `_requeue_unlaunched`, filters on `pk AND state=running AND claim_token=<that same
> token>` — a *strictly stronger* predicate. Any row the restore failed to match, the
> requeue cannot match either, so the fallback writes nothing and the fresh claim's
> only real effect is the `_active_tokens` pop the requeue path performs first. That
> is the correct outcome (a row another worker legitimately owns must not be rewritten
> by this one), so the code is right and this sentence was wrong; the ADR amendment
> records the discard, not a requeue.

### 3.5 Kind-owned wait ceilings, and the exclusive slot (Q6, Q10)

A fixed 600 s wait inside a job kind fired while a first-load generation was still
genuinely running and finished ~15 minutes later. Two rules follow.

**(a) The ceiling belongs to the kind and the operator, never to the scheduler.**
`JobKind` gains a code-declared `default_wait_seconds`, and `JobSettings` gains a
`kind_wait_seconds` map (kind key → seconds) that the operator edits on the
job-execution settings page — one row per registered kind, rendered from the registry
so a new kind appears without a template edit. The worker resolves the ceiling for the
job's kind and stamps it on `JobContext` beside the response timeout. **This costs no
additional query:** `_build_job_context` already fetches the settings row for
`response_timeout_seconds`, and the map rides on that same row — a second `get_solo()`
would be a query-count regression, and is explicitly not what this specifies.

**(b) A wait ceiling may never release the exclusive slot while the engine is still
working.** The slot is released the instant the job row goes terminal, so a kind that
gives up waiting and reports success hands the machine to the next admission while its
engine is still sampling. The rule, written into the ADR and onto the seam's contract:

> A job kind whose wait ceiling expires must not write a terminal outcome while its
> engine still reports the work running. It must either keep holding (continuing to
> report progress, which keeps the row alive under §3.4's heartbeat), or cancel the
> engine-side work and confirm the engine is terminal, and only then return.

The queue publishes the rule and the seam. It also *partially enforces* it from the
other side: §3.3(d)'s barrier is exactly what catches a still-working engine before
the next exclusive job launches, because the barrier-polling adapter withholds `True`
while a prompt is still running. What stays unenforceable is the
exclusive→non-exclusive case and the cross-engine case (§11).

### 3.6 Model-affinity batching, with an aging bound (Q3)

The owner's requirement: "we should bundle like requests for a given model even if
they were submitted at different times… This should be a feature of the queue."

Among candidates **at the same priority number**, a candidate all of whose model keys
are already resident sorts ahead of one that would have to load something. Priority is
never crossed; the walk still stops at the first candidate that cannot be admitted, so
the no-backfill deadlock proof is untouched.

**What "resident" means here, and where it comes from.** Not the pure scheduler's own
resident fold (that is derived from currently-running jobs and would make affinity a
no-op in sequential mode — exactly the posture where batching matters most, since one
job runs at a time and ordering is the *only* lever). Not a live `list_installed` call
either: that would put engine HTTP inside the advisory-lock transaction and stall every
other admitter. It is the **worker's most recent residency snapshot** — the believed-
resident key set from the last eviction pass, cached on the worker and handed to
`claim_and_admit` as a plain frozen set.

**How stale it really is, said plainly.** Not "one tick": the tick loop returns early
when nothing is claimed, so a snapshot is only ever taken on a tick that *admits*.
In sequential mode the cached set can therefore be **minutes** old — as old as the
last admission. It is also the **pre-eviction** belief, naming models the same pass
then went on to unload, so the worker **subtracts the keys that pass unloaded** before
caching it. Even so it remains a belief about the past, which is exactly why it is
used for an ordering *preference* and nothing else: a wrong guess costs one
suboptimal ordering decision, never a wrong admission or a wrong eviction. It falls
back to the running jobs'
declared keys when no snapshot exists yet (a fresh worker's first ticks).

> **Correction, 2026-09-21 (written against the shipped code, whole-branch review F4).**
> The last sentence contradicts this same section three paragraphs earlier, and the
> shipped code follows the earlier ruling. With no snapshot there is **no fallback fold
> at all**: the worker hands `claim_and_admit` an empty frozen set
> (`models/queue/claim.py`), and `affinity_order` with an empty resident set makes
> `not affine` true for every candidate, so the ordering degrades to plain
> `(priority, id)` — exactly the order that was in force before this section existed.
> Falling back to the running jobs' declared keys is the "pure scheduler's own resident
> fold" this section explicitly rejects above, because it would make affinity a no-op
> in sequential mode. `affinity_order`'s own docstring and the ADR both state the real
> fallback correctly; this sentence was the stale one.

**The seam, stated exactly.** `plan_admissions` returns only admitted ids and sorts
defensively inside itself, so neither the ordering nor the pass-over signal can be
smuggled through it as it stands. A new pure helper in the same module:

```
affinity_order(candidates, *, resident_keys, max_passovers) -> list[SchedCandidate]
```

`plan_admissions` takes `resident_keys` and `max_passovers` and uses this helper in
place of its inline sort, so the order it walks and the order the claim code reasons
about are the same function. The claim code calls the helper too, compares that order
against strict `(priority, id)`, and increments `passed_over` for exactly those
candidates that a later-by-`(priority, id)` peer was admitted ahead of. That is one
additional bulk `UPDATE` inside the existing transaction, added to §5's query-count
pins.

The ordering key is `(priority, not pinned, not affine, job_id)`. A candidate is
**pinned** once `passed_over` reaches `MAX_PASSOVERS` (**3**, supplied by the claim
code as a keyword argument so table tests can vary it), after which it sorts strictly
by id within its priority and can never be reordered behind a peer again. The count is
a durable small integer column on the job row, so a worker restart cannot reset a
job's age.

The starvation argument, stated so a reviewer can check it: within one priority
number a job can be passed over at most `MAX_PASSOVERS` times before it is pinned and
ordered by insertion; across priority numbers nothing changed, so the ADR's existing,
honestly-scoped starvation caveat is neither improved nor worsened. The deadlock
guarantee is untouched because whichever candidate heads a round is still admitted
alone the instant the machine is idle.

> **Correction, 2026-09-21 (written against the shipped code, whole-branch review F2).**
> The two sentences above overstate what pinning does, in the same way four code
> comments did until this fix round. Pinning sets the **second** element of the sort
> key `(priority, not pinned, not affine, job_id)`, so the precise statement is: a
> pinned candidate **sorts by id ahead of every UNPINNED peer at its priority and is
> never reordered behind one again**. It is NOT true that it "sorts strictly by id
> within its priority and can never be reordered behind a peer again", nor that it is
> "ordered by insertion": the `not affine` term still discriminates *among pinned
> candidates*, so a pinned non-affine job can still sort behind a pinned affine one.
> The starvation bound therefore reads: within one priority number a job can be passed
> over at most `MAX_PASSOVERS` **rounds by unpinned peers**, which is the bound that
> matters, while the affinity preference survives among the aged. ADR 0013 §3 records
> the same correction, and records that dropping the affine term for pinned candidates
> (making pinning a strict `(priority, id)` tail) is an open one-line owner decision
> that was deliberately **not** taken here.

The Queue page shows the count on a waiting row ("passed over twice") — an invisible
reordering is exactly the kind of thing that gets blamed for an unrelated delay.

**The same argument binds the hold-off, and the ruling is that it must be visible too.**
A job the queue is deliberately declining to consider for `BARRIER_HOLDOFF_SECONDS`
(§3.3d) is a stronger case of an unexplained delay than a reordering, and today it would
surface only as a log line — which fails this track's own Q14 observability thesis. A
waiting row whose `not_before` is in the future renders that state plainly: *"waiting for
engine memory — retries at HH:MM"*, with the reason in the same voice as the rest of the
page. **Display only**: server-rendered from the row the page already loads, revealed by
the page's existing refresh, no new query, no polling, no script — the zero-JS doctrine
governs here as everywhere else.

### 3.7 The unset memory budget (Q12)

With §3.3(f), an unset budget no longer switches the whole apparatus off: the
barrier, the cross-engine sweep and the log lines all run. What remains true is that
the *capped, budget-driven* pass has no number to work against, and that the operator
has no way to know it.

Recommendation (owner decision, §10): **make the unset state loud; do not silently
default it.** The Queue page's budget block becomes a real callout in the page's
existing notice style: jobs run one at a time, nothing is offloaded *for budget
reasons*, and here is the control that changes it. The settings control grows an
honest prefill.

**The prefill is measured where it matters.** The console renders in the web service;
the budget governs the worker service — separate containers, so memory detected in
the web process describes the wrong machine, in precisely the way this section warns
about. The **worker** writes the detected figure (`detected_memory_bytes` /
`detected_memory_at` on the settings row) once at boot, and the settings page renders
it labelled with what it is: "detected by the worker process on *date*". Nothing is
applied on the operator's behalf; the container sees the VM's allocation rather than
the host's, and a silently derived budget would be authoritative and wrong.

### 3.8 Stranded turns (Q15)

A chat turn whose job row was rolled back by a database crash sits at "working"
forever: the `on_terminal` hook that closes a placeholder turn is driven by the job
row's own terminal write, and there is no row left to write. Recovery today is
"delete the conversation and resend".

A reconciliation, owned by the agent column (the queue cannot know which turn points
at which job, and may not import the agent layer):

```
agents.runtime.jobs.reconcile_stranded_turn(turn) -> bool
agents.runtime.jobs.reconcile_stranded_turns(grace_seconds) -> int
```

> **Correction, 2026-09-21 (written against the shipped code, whole-branch review F5).**
> The module path above is wrong and could not have shipped. The reconciliation lives
> in `agents/reconcile.py`, not under `agents/runtime/`, because its whole condition is
> a `models.contracts.queue.get_job` call and
> `foundation/ops/tests/test_column_boundaries.py::test_no_runtime_module_blocks_on_a_queue_job`
> forbids exactly that call anywhere in the `agents/runtime/` package — the guard test
> is what forces the placement, so this is a boundary ruling and not a filing
> preference. Three public functions shipped, not two: `reconcile_stranded_turn(turn)`,
> `reconcile_stranded_turns(grace_seconds)` and `count_stranded_turns(grace_seconds)`,
> the last being what the management command's `--dry-run` answers with. The plan, ADR
> 0013, `agents/README.md`, `agents/runtime/README.md` and `models/queue/README.md`
> were all corrected in Task 19; this spec paragraph was the one place left pointing at
> the module that cannot exist.

The condition, deliberately narrow: an **assistant** turn still `queued`/`running`,
whose `queue_job_id` is null or names a job row that **no longer exists**, and whose
row has been in that state longer than a grace period
(`STRANDED_TURN_GRACE_SECONDS`, comfortably longer than the enqueue-then-commit
window so a turn mid-creation is never touched). The write reuses the existing hook's
exact shape — one conditional `UPDATE` filtered on the two non-terminal states, an
honest error sentence ("The queue lost this turn before it ran; nothing was
retried."), and the same closing of the turn's open invocation rows, **which closes
rows where a job id was stamped and has nothing to close on the null-`queue_job_id`
half of the condition** (the helper returns zero for a falsy job id by design).

Two call sites, both bounded:

- `turn_status`, for the **one** turn being polled, when it resolves to a missing job
  row — the surface where the strand is visible, turning a permanent
  "Queued — waiting…" into an honest failed card within one poll tick, with JS on or
  off (a plain reload takes the same path through the rendered card);
- `manage.py reconcile_turns`, the operator/diagnostic sweep, with `--dry-run`.

No periodic background sweeper: the condition is rare, the read surfaces already visit
exactly the rows that matter, and an always-on sweeper is machinery this evidence does
not justify.

### 3.9 A poll loop that cannot tick silently forever (Q16)

Two post-crash turns polled forever at "Queued — waiting…" while the status endpoint
was erroring through the database's recovery.

**Where the incident actually went.** A 5xx whose body is not JSON — Django's error
page during a database outage — already rejects inside `response.json()` and lands in
the loop's `.catch()`, where it is counted against the transport ceiling and bounded.
The genuinely uncounted branch is a **parseable body carrying no recognized state**,
which falls into the forward-compatible final `else` and re-ticks silently until the
duration ceiling hours later. That is the silent stall, and closing it is the
load-bearing half of this section.

**Change 1 (load-bearing).** A response the page cannot act on is a failure, counted:
a body with no recognized state (and any `5xx` that does parse) increments the same
bounded counter a dropped connection does, and exhausting it shows the same honest
card note rather than ticking on in silence.

**Change 2 (best-effort).** `turn_status` answers `503` with a body marked
*retryable* when the database is momentarily unavailable, distinct from the existing
"the queue is not configured" `503`, which is terminal and keeps its setup link; the
loop retries the retryable one (bounded) and gives up honestly. This is explicitly
best-effort and cannot be a view-local `try` around the body builders: the view
touches the database in principal resolution and turn visibility *before* any builder
runs, and session middleware touches it before the view is entered at all. The guard
is therefore written around the whole view body and names `OperationalError`
explicitly, and this spec states plainly that a truly unavailable database can still
produce a non-JSON 5xx that only change 1 handles.

**The gate this touches.** The chat page's hand-rolled loop is held in `_EXEMPT` in
`foundation/ops/tests/test_shared_poller.py` under a dated, self-deleting exemption
whose companion test *asserts the hand-rolled loop still exists*. Fixing in place is
therefore correct today, and this spec records the obligation it creates: when the
held follow-up repoints the page onto the shared loop, **change 1 must be carried
onto `foundation/templates/_poller.html`** or it is silently lost. The repo-wide half
of the gate (no other template rolling its own poll loop) must stay green.

Zero-JS doctrine is unchanged: the page works without the script, and §3.8 makes the
no-JS card eventually honest.

### 3.10 Worker boot tolerates a racing `migrate` (boot race)

The worker reads the settings row (a `get_or_create`) in **two** places: the
constructor, to size its pool, and every tick, plus `claim_and_admit`'s own
transaction. On a cold compose boot either can win the race against `migrate`. An
untolerated `ProgrammingError` escaping the tick is treated by the run loop as a
**crash** — traceback, loop stopped, non-zero exit — which is the very traceback this
section exists to remove.

Both sites are tolerated, naming `ProgrammingError` and `OperationalError`
explicitly: the constructor's read is wrapped in a bounded wait (a small number of
retries, one INFO line "waiting for the database schema", never a traceback) and
falls back to the documented default pool size if the wait expires; the **first ticks
return quietly** on either exception rather than raising, so the loop survives to try
again once `migrate` finishes.

## 4. ADR 0013 amendment — exactly what changes

One dated amendment, "Queue memory governance", recording:

1. **§4 (memory accounting)** — the four-rung ladder and its own label vocabulary
   (§3.1), and the keep-the-maximum recorder rule with the geometric-ratchet
   reasoning for having no tolerance band (§3.2).
2. **§4/§7 (eviction)** — `unload_scope` and why the queue must know it; the
   protected-key rule, **including that the admitted batch is protected** (which is
   what today's `needed_keys` already does) and the single endpoint-scope exception;
   the **fifth ordering rule** in `_evict_to_match_plan`'s phase note — the protection
   check runs before the residency snapshot, so a refusal costs no HTTP; the redesigned
   barrier including the ambiguity of `False`, the narrowed precautionary call, and the
   refusal bounded in count **and** wall clock with a `not_before` hold-off; the
   cross-engine sweep and its effect on the budget arithmetic; **the budget gate moving
   so that the barrier, the sweep and the logging are unconditional while only the
   capped pass stays gated**; and the log vocabulary (§3.3).
3. **§3/§9 (ordering)** — affinity batching within a priority with an aging bound, the
   `affinity_order` seam, and the explicit note that the no-backfill deadlock proof and
   the honestly-scoped starvation caveat are both unchanged (§3.6). **`not_before`
   (§3.3d) is recorded here too, as the one admission-order change this track makes:**
   a held-off job is excluded from the candidate query, so a later-by-`(priority, id)`
   peer can be admitted ahead of it. The proof survives because the exclusion is
   time-bounded and self-clearing — the job returns to its own head position the moment
   the hold-off expires, and an effectively-exclusive head is still admitted alone the
   instant the machine is idle — so nothing can wait behind it forever.
4. **§7 (worker lifecycle)** — the heartbeat thread and its connection story, kind-aware
   staleness and the two `claim_and_admit` signature changes, sleep detection, the
   restore-to-the-live-attempt refusal, and boot tolerance (§3.4, §3.10).
5. **§5 (kind registry)** — `stale_after_seconds`, `default_wait_seconds`, the
   operator-editable per-kind wait map riding the existing settings read, and the rule
   that a wait ceiling may never release the exclusive slot early (§3.5).
6. **§8 (named gaps)** — the three follow-ups named on 2026-08-25 (staleness, early slot
   release, eviction's blindness to a live attempt) are **discharged**, with a sentence
   each saying how; §11's residuals are added in their place.
7. A closing note that ADR 0015's agent-layer gaps are untouched: §3.8 and §3.9 fix two
   chat-surface defects and change no tool contract.

## 5. Tests (same commits as the code)

**Scheduler and claim** (`models/queue/tests`): `affinity_order` table tests (affine
first within a priority, never across priorities, pinned candidate ordered by id,
fallback when no snapshot exists) and the pass-over increment derived in the claim
code; `plan_admissions` invariants re-pinned (no backfill, exclusive-alone-when-idle,
sequential mode) under the new ordering; the protected-key rule at both
`unload_scope` values, including an endpoint skipped whole because a live in-flight
attempt's model sits there; **the admitted job's own believed-resident model is NOT
unloaded at a `"model"`-scope endpoint, and IS at its own `"endpoint"`-scope endpoint**
(§3.3c's rule and its one sanctioned exception, pinned as a pair so neither can drift);
the protection check evaluated **before** the residency snapshot, asserted by a test
that fails if any `list_installed` is called on a tick refused for a protected
endpoint; the barrier's precautionary call distinguished from the per-model loop, and
**not issued** at an endpoint whose engine declares `residency_authority="endpoint"`
and reports nothing resident, **and issued** where that attribute is absent or `"memo"`
(the safe default pinned as its own case);
`False` honoured for a believed-resident call and ignored for a precautionary one;
the bounded-refusal failure requiring **both** `MAX_BARRIER_REFUSALS` and
`MIN_BARRIER_REFUSAL_SPAN_SECONDS` (a test that three refusals inside the span do not
fail the job, and that a successful barrier resets the count); the `not_before`
hold-off written on both refusal paths and honoured by the candidate query; the
duplicate-submit refusal path restoring the row to the live attempt's token (and the
zero-rows fallback); consumption of `registered_endpoints()`; kind-aware staleness and
`sweep_orphans=False` in the sweep; sleep detection; the heartbeat thread's
single-writer behaviour, its `close_old_connections()` call, its survival of a
transient write error, and its absence under a single-tick run; boot tolerance at both
sites for both exception classes; query-count pins for the per-tick settings read, the
widened sweep, and the new pass-over `UPDATE`.

**Registry** (`models/registry/tests`): the four-rung ladder and the new
`_FOOTPRINT_SOURCE_LABELS` vocabulary; the capability-source disclosure **unchanged**
(its pinned strings still assert today's wording) alongside the four deliberately
re-pinned footprint assertions and the template's switch to a resolved
`footprint_source_label`; keep-the-maximum — accepted raise, refused dip, **"three
successive 0.8× readings do not walk the standing value down"**, first write with no
standing value, and the engine-reported column obeying the same rule;
`registered_endpoints` derivation (connection rows **unioned with the configured
default endpoints**, model_ids carried) and its tolerance for a missing table.

**Queue surfaces** (`models/queue/tests`): the loud unset-budget callout; the prefill
labelled with the process that measured it; the "passed over" reading on a waiting
row; **the hold-off state on a waiting row whose `not_before` is in the future, and its
absence once that time has passed** (one line beside the passed-over reading, server-
rendered, no added query); never-500 on both pages.

**Chat** (`agents/chat/tests`, `agents/runtime/tests`): reconciliation of a turn whose
job row is gone, and *no* reconciliation inside the grace, for a terminal turn, or for
a turn whose job row exists; invocation rows closed where a job id was stamped and the
null case a documented no-op; the management command including `--dry-run`;
`turn_status` answering a retryable `503` distinct from the terminal configuration
`503`; the poll loop counting a parseable body with no recognized state, asserted at
the rendered-script level the existing poller tests use.

**Gates that must stay green:** the four runs (both feature-flag states × both
collection orders); the shared-poller gate including its repo-wide half and its
still-valid exemption entry; column boundaries; import law; CSS ownership; agent
standards (no absolute paths, no model or vendor names in tracked files).
Flag-dependent behaviour pins its own feature state rather than assuming the ambient
one.

## 6. Docs (same commits)

`models/queue/README.md` (the ladder, eviction's reach, `unload_scope`, the protected-
key rule, the log vocabulary, what an unset budget does and no longer does);
`models/registry/README.md` (the three stored facts, the keep-the-maximum rule, the
two label vocabularies and why they are separate); `models/contracts/engines`
docstrings (the `unload_scope` attribute and its safe default);
`docs/OPERATIONS.md` (each new log line and what to do about it; the failed-after-
three-refusals error; the `reconcile_turns` command); `agents/chat/README.md`
(stranded-turn reconciliation, the poll loop's failure vocabulary, the obligation to
carry change 1 onto the shared loop); `docs/EXTENDING.md` (a kind declaring its
staleness and wait ceiling, and the rule about not releasing the slot early); ADR
0013's amendment (§4). No model or vendor names anywhere — engine names only.

## 7. Delivery and coordination

One branch, tasks in this order, each reviewed before the next:

1. Registry: the ladder, the keep-the-maximum rule, `registered_endpoints()` (with the
   console helper refactored onto it), the separate label vocabulary and the template
   edit. **Migration 1** (registry app): two columns.
2. Liveness: heartbeat thread and its connection story, kind-aware staleness and the
   two `claim_and_admit` signature changes, sleep detection, the restore-to-the-live-
   attempt refusal, boot tolerance. **Before** task 3, and the dependency is stronger
   than the first draft claimed: the phase the wide sweep actually lengthens — the
   residency snapshot — contains no heartbeat call at all, and each `list_installed`
   can cost a full discovery timeout.
3. Eviction: `unload_scope` consumption, the protected-key rule and its one sanctioned
   exception, the protection check ordered before the snapshot, the redesigned barrier
   with its hold-off and time-bounded refusal, the cross-engine sweep, the budget-gate
   move, the log vocabulary, and the Queue page's hold-off row state (§3.6). **Migration
   2** (queue app, shared with tasks 4 and 5) begins here with `not_before`.
4. Ordering: `affinity_order`, the claim-side increment, the Queue-page reading.
   **Migration 2** also carries `passed_over`.
5. Budget loudness and the worker-written detected-memory fact; per-kind wait ceilings
   and the seam. **Migration 2** also carries `kind_wait_seconds`,
   `detected_memory_bytes`, `detected_memory_at`.
6. Chat: stranded-turn reconciliation, the command, `turn_status`, the poll loop.
7. Docs and the ADR amendment.

**Two migrations across two apps, seven columns** — registry gains
`engine_reported_footprint_bytes`/`_at`; the queue app gains `not_before` and
`passed_over` on the job row and `kind_wait_seconds`, `detected_memory_bytes`,
`detected_memory_at` on the settings row. `not_before` is also the one new
admission-side query filter this track adds, so task 3 and task 4 must re-pin the
claim's query counts together.

**Cross-column:** no adapter or vision-column file is edited by this track. The
dependency is **flagged, not specced** — the `unload_scope` and `residency_authority`
declarations (§3.3a), the image adapter's pair **pre-cleared by its steward** — and the safe default keeps the track correct if they slip. If any finding
turns out to need more than that, it goes to the steward, not into this branch.

**Deploy:** two migrations, a new settings shape, and a worker restart (the pool, the
heartbeat thread and the detected-memory write are all process-level).

## 8. Done when

Suite-level: the four runs green; every test in §5 present and passing; query-count
pins unchanged or deliberately re-pinned.

Live proofs, on the preview stack that has two real engines, captured as pixels or log
excerpts:

1. **A real cross-engine eviction.** With a model left warm on an idle engine,
   admitting an exclusive job on the other engine unloads it — visible in the new INFO
   lines and in the Queue page's "running now" reading falling.
2. **A barrier refusal is honoured, and told apart from an empty endpoint.** Three
   captures: (i) a *believed-resident* endpoint that does not release → the job is
   requeued with the WARNING line, takes its hold-off, and fails honestly only after
   three attempts spanning the minimum wall clock; (ii) a **cold, empty** endpoint →
   the precautionary call's `False` is logged at INFO and **the job launches on that
   same tick**; (iii) **timing**: the wall-clock delay the barrier adds to an ordinary
   exclusive admission (a chat turn) is recorded, and is expected to stay within a
   second or two on a box whose engines report residency authoritatively — a 30 s
   per-turn regression means the precautionary narrowing (§3.3d(3)) is not working and
   must be caught here, not in use. Without (ii) and (iii) the mechanism is not proven.
3. **The barrier runs with no budget set.** Proof 1 or 2 repeated on a box with
   `memory_budget_bytes` unset, showing the sweep, the barrier and the log lines all
   active.
4. **A cold load survives staleness.** A kind declaring a long threshold completes a
   multi-minute cold load without being orphaned, and the job finishes on the page.
5. **A lowering is refused.** A measurement taken while another model was resident does
   not lower the standing footprint; the console shows the higher value with its new
   label; the refusal line names both numbers.
6. **Affinity batches, visibly.** Three same-model jobs and one other, all at one
   priority, submitted interleaved: the same-model jobs run consecutively, and the
   passed-over job shows its "passed over" reading on the Queue page and then runs once
   pinned.
7. **A wait ceiling holds the slot.** A kind whose ceiling expires while its engine is
   still working does not go terminal: the Queue page still shows it running, no second
   job is admitted, and the job finishes when the engine does.
8. **The unset budget is loud.** The Queue page callout, and the prefill labelled with
   the process that measured it.
9. **A stranded turn recovers.** A turn whose job row is removed under it resolves to an
   honest failed card within one poll instead of "Queued — waiting…" forever.
10. **A recovering database does not strand the card.** A parseable body with no
    recognized state produces bounded retries and then an honest note, never a silent
    tick.
11. **A cold boot is quiet.** Starting the worker against an unmigrated database logs
    one waiting line and no traceback, at both read sites.

No success language before these pixels exist.

## 9. Author decisions (made here, open to challenge)

1. **A third footprint rung, with its own columns**, rather than folding engine-reported
   readings into the measured column. Folding would make the recorder rule compare two
   facts of different quality and would make the console's label a lie again.
2. **Keep the maximum; no tolerance band.** A percentage band against the standing value
   ratchets geometrically and reproduces the exact incident it is meant to refuse
   (§3.2). The stricter rule needs no column and no constant.
3. **The queue learns `unload_scope` from the engine, with a safe default**, rather than
   assuming per-model granularity (destroys live work on an endpoint-wide engine) or
   assuming endpoint-wide for everyone (over-protects a per-model engine). The two
   declarations are flagged cross-column, never edited here.
4. **`False` is honoured only where it is informative.** Against the real adapter,
   `False` from a precautionary call on an empty endpoint is the *common* case and
   blocking on it makes an exclusive job unlaunchable forever.
5. **A bounded refusal ends in failing the job, not launching anyway.** Launching into
   genuinely held memory is the host crash this track exists to prevent; §10 item 6
   puts the alternative to the owner.
6. **The barrier covers the whole swept set**, not only the admitted job's own
   endpoints — otherwise the sweep does not close the incident that opens this spec.
7. **The admitted batch is protected, with one written exception.** Today's
   `needed_keys` already protects it (one `RUNNING` query covers "running ∪ admitted"),
   and an exclusive-by-default agent turn means an unprotected definition would
   cold-load the chat model on every message. The endpoint-scope-own-endpoint case is
   written as an explicit exception rather than left to fall out of a set definition.
8. **A refused launch takes a durable `not_before` hold-off**, not an in-process one:
   the restart that clears an in-memory hold-off is the same restart that clears the
   refusal count, and the two together would re-enter the 0.5 s churn immediately.
9. **The refusal bound is count *and* time.** Half-second ticks make a pure count
   meaningless against a busy engine, which returns an informative `False` precisely
   because it is still working.
10. **The precautionary call is narrowed to unauthoritative beliefs, through a second
    declared attribute.** An engine that genuinely knows nothing is resident gets no
    30 s no-rise poll on every exclusive admission for no information — and because
    "genuinely knows" is not derivable from any existing seam, `residency_authority`
    carries it, in the same getattr/degrade idiom and with the safe default.
11. **The duplicate-submit refusal restores the row to the live attempt** rather than
   requeueing it, which would strip heartbeat protection, churn every tick for the
   length of a cold load, and discard the live attempt's result. *(Corrected
   2026-09-21, Task 19: when the restore matches zero rows — another worker
   legitimately owns the row — the fresh claim is **discarded**, not requeued. The
   requeue fallback's predicate is strictly stronger than the restore's, so it is dead
   by construction and the foreign owner's row is never touched. See §3.4(d)'s own
   correction note.)*
12. **Affinity reads the worker's last residency snapshot**, not a live engine call
   (HTTP inside the advisory lock) and not the running-jobs fold (dead in sequential
   mode, where batching matters most).
13. **Kind staleness and kind wait defaults are code-declared**; only the wait *ceiling*
   is operator-editable.
14. **Pass-over counting is a durable column**, not in-memory state.
15. **No background sweeper for stranded turns**; reconcile where the strand is visible,
    plus a command.
16. **The chat page's poll loop is fixed in place**, under its existing exemption, with
    an explicit obligation to carry the fix onto the shared loop when the held follow-up
    repoints the page.

## 10. Owner decisions (flagged, with a recommendation)

1. **Unset budget (Q12).** Loud unset with a worker-measured prefill (**recommended**)
   vs. silently defaulting the budget. The container sees the VM's allocation, not the
   host's; a silent default would be authoritative and wrong.
2. **The third rung (Q1).** Accept the engine-reported rung and its columns
   (**recommended** — with §3.3(f) it now works on sequential-mode boxes too) or keep
   two rungs and leave those models exclusive.
3. **The recorder rule (Q9).** Keep-the-maximum, no tolerance (**recommended**) vs. a
   tolerance band measured against a separate high-water column (one more column, one
   more constant, same protection).
4. **Aging bound (Q3).** `MAX_PASSOVERS = 3` (**recommended**), and whether affinity is
   on by default (**recommended: yes** — it is the owner's own stated requirement).
5. **Per-kind wait ceilings on the settings page (Q6).** One editable row per registered
   kind (**recommended**) vs. code-declared defaults only.
6. **After the refusal bound is reached (Q13).** Fail the job with an operator-readable
   error (**recommended**) vs. launch anyway with a loud warning. The first can strand
   one job; the second can take the host down. The bound itself is now two conditions
   (three refusals **and** a minimum wall-clock span), so "three attempts" can never
   mean "a second and a half" on a busy engine — the span value is the knob if the
   owner wants the queue more or less patient.
7. **Stranded-turn reconciliation writing from a polled GET (Q15).** Recommended: one
   conditional, idempotent write that only ever fires on a row already broken; the
   alternative is command-only recovery.

## 11. Open risks and residual gaps (recorded honestly)

- **Foreign endpoints with no registered model id cannot take a precautionary barrier
  call.** A configured default endpoint with no connection row yields no addressable
  `model_id`; its believed-resident models are still evicted by the per-model loop, but
  an empty or unavailable belief there leaves the endpoint unbarriered.
- **`unload_scope` and `residency_authority` are declarations, not probes.** Until an
  adapter takes its lines, the safe defaults apply: eviction is coarser on a per-model
  engine (an unneeded model sharing an endpoint with a needed one survives), and an
  engine with a real residency endpoint is given precautionary calls it does not need.
  Named costs, not correctness gaps.
- **A `True` from the image adapter's no-baseline path observed nothing.** When
  `/system_stats` is unreadable at the moment the call starts, that adapter cannot
  compute a rise at all, so it degrades to the pre-barrier contract: the POST's own 2xx
  plus one queue-idle check, and returns `True`. That is *exactly* the precautionary-call
  case (§3.3d(3)), and a `True` there is a confirmation of nothing — the queue launches
  an exclusive job into memory that may still be held, which is the crash shape this
  track exists to close. The queue cannot tell that `True` from a real one; only the
  adapter knows, and this spec does not change adapters.
- **Machine-wide memory drift can fake the rise.** The settle threshold falls back to a
  256 MiB floor read from machine-wide available memory, so an unrelated process
  finishing work during the poll window can clear it and read as a settled eviction —
  the adapter's own documented false-positive, which additionally drops its run memo and
  therefore its residency belief. A barrier that returns `True` on drift launches on an
  endpoint nothing actually freed.
- **The residency memo is LRU-capped at eight endpoints.** A ninth endpoint evicts the
  oldest belief, so on a box with many registered endpoints an endpoint can report
  "nothing resident" purely because its belief aged out — which, under
  `residency_authority="memo"`, buys an extra precautionary call (and its full no-rise
  poll) rather than a wrong decision. Safe direction, real cost; it compounds with the
  timing note below.
- **The barrier refusal count is in-process.** A worker restart resets it, so a
  pathological endpoint could refuse three times per worker lifetime rather than three
  times ever.
- **Wait-ceiling enforcement is partial.** §3.3(d)'s barrier catches a still-working
  engine before the next *exclusive* job launches; the exclusive→non-exclusive case and
  the cross-engine case remain unenforceable from the queue side.
- **Cross-process live attempts stay invisible to eviction.** §3.3(c)'s protected set
  uses this worker's own in-flight map. With more than one worker, another worker's
  loading model can still be unloaded. There is one worker; a durable claim-level signal
  is the fix if that changes.
- **An exclusive job waits behind a protected endpoint, and the wait has a cost as well
  as a duration.** When a live attempt holds a model at an endpoint the barrier must
  cover, the exclusive job is not launched that tick. The wait is bounded by the live
  attempt's own end (a wedged attempt delays it until the orphan sweep resolves), and
  the per-tick cost is bounded by two things and no more: the protection check runs
  before any HTTP (§3.3d(2)) and the refused job takes a `not_before` hold-off, so the
  repeated price is one claim round-trip per hold-off period rather than a full
  cross-engine probe every 0.5 s. It is not zero.
- **The exception in §3.3(c) does cost a reload.** An exclusive job at its own
  endpoint-scope endpoint has its own warm model freed with everything else, and
  reloads it. Unavoidable without a per-model call the engine does not offer, bounded
  to that one case, and never applied to another job's or a live attempt's key.
- **`over_budget` is computed over a wider endpoint set on exclusive-admitting ticks**
  than on others (§3.3e). Deliberate — under-counting resident memory is what crashes
  hosts — but it means the number is not comparable tick to tick.
- **A genuinely shrunk model stays high forever** until an operator sets an override.
- **Residency belief remains a belief.** A restart loses the memo; the barrier mitigates,
  it does not cure.
- **The wide sweep adds HTTP to an admitting tick — and the precautionary call is the
  expensive half.** The `list_installed` probes are bounded by the exclusive-only
  condition and by the heartbeat thread. The precautionary unload is the larger cost:
  at an endpoint-scope engine with an empty belief it runs its settle poll to the full
  30 s timeout because nothing rises, multiplied by the number of such endpoints, on
  every exclusive admission — i.e. on every chat turn. §3.3(d)(3) narrows it to
  unauthoritative beliefs for exactly this reason, and done-when proof 2(ii) times it,
  but a box with several endpoint-scope engines and no residency endpoints still pays
  real seconds per exclusive admission — and the eight-endpoint memo cap above can add
  calls on a busy box by aging a belief out rather than by anything changing.
- **The barrier's refusal bounds are process-local where the count is concerned.** The
  `not_before` hold-off is durable; the consecutive-refusal count and its wall-clock
  span are in-process, so a restart resets them (see the bullet above on the count).
- **Affinity reads a pre-eviction snapshot that can be minutes old** in sequential mode
  (snapshots are only taken on admitting ticks), with the last pass's unloads
  subtracted, and none at all on a fresh worker's first ticks, where it falls back to
  declared keys.
- **Sleep detection is a heuristic.** A long stop-the-world pause could trip it; the cost
  of a false positive is one skipped orphan sweep, deliberately the cheap direction.
- **Change 2 of §3.9 is best-effort.** A truly unavailable database can still produce a
  non-JSON 5xx before the view is entered; only change 1 covers that, and it does so by
  giving up honestly rather than by answering.
- **Measurement is still not process-peak and still not accelerator-upcast aware.** This
  track makes the estimate honest and monotonic, not exact.

## 12. Out of scope (named so nobody builds them by accident)

- Backfill, preemption of a running job, or any change to the no-backfill deadlock proof
  beyond §3.6's within-priority reordering and §3.3(d)'s time-bounded, self-clearing
  `not_before` hold-off.
- Multi-worker coordination, distributed claims, or cross-host scheduling.
- A real memory manager: process-peak measurement, accelerator-upcast modelling, or
  per-layer accounting.
- Probing an engine for its unload granularity or the authority of its residency
  report, rather than reading a declaration.
- Automatic budget tuning, or any code path that changes an operator's posture without
  them saying so.
- Engine adapter internals — residency endpoints, unload semantics, measurement methods.
  The `unload_scope` / `residency_authority` declarations are flagged to their stewards
  (the image adapter's pre-cleared), not written here. In particular, closing the
  no-baseline `True` and the drift false-positive named in §11 is adapter work this
  track names and does not attempt.
- Migrating the chat page onto the shared poll loop, and any other poller consolidation.
- Streaming turn output, or any change to the rule that a tool runner never waits on a
  queue job.
- Retrying a job automatically after a barrier refusal beyond the bounded attempts in
  §3.3(d)(5).

## 13. Review response (round 1)

Adversarial review of revision 1 returned **AMEND — 7 Major / 12 Minor / 3 Nit**. All
22 findings are applied. Three are applied as a **variant** of the suggested edit; each
is recorded here for the re-check to adjudicate rather than left silent. No finding is
skipped.

| Finding | Applied in | Note |
|---|---|---|
| M1 fallback destroys what §3.3(d) protected | §3.3(a)(c)(d) | **Variant.** Generalised beyond the suggested fix — see below. |
| M2 `False` livelocks against an empty endpoint | §3.3(d)(4)(5), §8 proof 2 | **Variant** on the bound: fail after three, not launch-anyway; §10 item 6. |
| M3 wide sweep leaves foreign endpoints unbarriered | §3.3(d)(1), §3.3(e) | `registered_endpoints()` now carries model ids; residual named in §11. |
| M4 §3.3 is dead with no budget | §3.3(f), §4.2, §8 proof 3 | Adopted as written. |
| M5 never-lower ratchets geometrically | §3.2, §9.2, §10.3 | **Variant.** Strict maximum instead of a high-water column — see below. |
| M6 wrong dictionary; relabel breaks a shipped disclosure | §3.1, §5 | Adopted: separate vocabulary, named template edit, re-pinned assertions. |
| M7 duplicate-submit refusal leaves state unspecified | §3.4(d) | Adopted, option (a): restore the row to the live attempt's token. |
| m1 sweep signature and ownership | §3.4(b)(c) | Both changes named; kind map resolved inside the claim module. |
| m2 "resident set the claim code computes" does not exist | §3.6 | Worker's last snapshot; both rejected alternatives stated. |
| m3 pass-over has no seam | §3.6 | `affinity_order` helper; claim derives the increment; pin added. |
| m4 `registered_endpoints` too narrow | §3.3(e) | Unions the configured default endpoints; console helper refactored onto it. |
| m5 widened set changes budget arithmetic | §3.3(e), §11 | Stated and accepted. |
| m6 §3.9 diagnosis and guard site wrong | §3.9 | Corrected; change 1 named load-bearing; guard widened and marked best-effort. |
| m7 wait ceiling needs no extra read | §3.5(a) | Corrected. |
| m8 prefill measures the wrong container | §3.7 | Worker writes the fact; label names the process. |
| m9 boot tolerance sites and exceptions | §3.10 | Both sites, both exception classes, quiet first ticks. |
| m10 Q7 missing | §1 | Named; the table is fifteen open findings. |
| m11 poller exemption unnamed | §3.9, §5 | Gate named; carry-forward obligation recorded. |
| m12 heartbeat thread connection story | §3.4(a) | Adopted in full. |
| m13 two behaviours have no live proof | §8 proofs 6, 7 | Added. |
| m14 §11 residual over-stated | §3.5(b), §11 | Barrier's partial enforcement stated. |
| n1 invocation-closing no-op | §3.8 | Stated. |
| n2 migration count short | §7 | Two migrations, two apps, six columns. |
| n3 test filed in the wrong column | §5 | Moved to registry; fallback-vs-loop and refusal-path tests added. |
| "Confirmed" correction: "harmless no-op" is false | §3.3(b) | Corrected in place, not footnoted. |

**Three variants, recorded for adjudication:**

1. **M1 — generalised rather than narrowed.** The review asked for a narrower fallback
   *condition*. This spec instead introduces `protected_keys` and applies it to **every**
   unload call, with per-endpoint granularity on an endpoint-wide engine (§3.3a/c).
   Reason: the destructive-fallback bug is a special case of a wider one the review's
   own tree evidence exposes — the *existing* per-model loop already calls an
   endpoint-wide `unload` for a non-needed model at an endpoint that also hosts a needed
   one, and that loop is untouched by a fallback-only fix. Narrowing only the fallback
   would have left the same defect in the pass beside it.
2. **M5 — strict maximum instead of a tolerance band against a high-water column.** The
   review's edit offers either a `footprint_high_water_bytes` column or "within tolerance
   of the highest value ever recorded". Setting that tolerance to zero makes the standing
   value a high-water mark by construction, needs no column and no constant, and is
   strictly safer. The review's pinned test ("three successive 0.8× readings do not walk
   the standing value down") is adopted verbatim and passes under this rule. §10 item 3
   preserves the banded variant as an owner choice.
3. **M2 — bounded refusal ends in failure, not a forced launch.** The review offered
   "launch and log, or fail the job". This spec chooses failure, because launching into
   memory an engine has thrice declined to release is the host-crash path the track
   exists to close; a stranded job is recoverable by an operator, a downed host is not.
   Surfaced as owner decision 6 rather than settled unilaterally.

### Round 2 — scoped re-check (revision 2)

Round 2 returned **AMEND — 1 Major / 3 Minor / 1 Nit**, all newly surfaced against the
redesigned barrier, and **adjudicated all three round-1 variants as ACCEPTED** (V1 the
`protected_keys` generalisation, V2 the strict maximum, V3 the bounded refusal ending
in job failure as owner decision 6). Both round-1 directions of the barrier were
confirmed closed: no livelock on an empty endpoint, and no destruction of live work.
All five residuals are applied; none is disputed.

| Finding | Applied in | Note |
|---|---|---|
| R2-1 (Major) `protected_keys` excludes this tick's admitted batch | §3.3(c), §4.2, §5, §9.7 | Widened to include the admitted batch — matching what today's `needed_keys` already does — with the endpoint-scope-own-endpoint case written as an explicit exception. Paired test pinned. |
| R2-2 (Minor) `MAX_BARRIER_REFUSALS` counts ticks, not time | §3.3(d)(5), §10.6, §5, §9.9 | Refusal now needs three refusals **and** `MIN_BARRIER_REFUSAL_SPAN_SECONDS`; a successful barrier resets the count; a `not_before` hold-off spaces the attempts. |
| R2-3 (Minor) protected refusal costs a full HTTP sweep every tick | §3.3(d)(2), §4.2, §7, §11, §5 | Protection check moved **before** the residency snapshot as a fifth ordering rule; `not_before` hold-off added; §11 now names the per-tick cost, not just the wait. |
| R2-4 (Minor) the precautionary call's cost is not priced | §3.3(d)(3), §8 proof 2(iii), §11, §9.10 | Narrowed to unauthoritative beliefs; the 30 s no-rise poll named explicitly; a timing expectation added to the live proof. |
| R2-5 (Nit) "one tick stale" undersells the affinity snapshot | §3.6, §11 | Restated: minutes old in sequential mode, pre-eviction, with the pass's unloads subtracted before caching. |

**The Major, in one sentence, because it is the one that mattered:** an agent turn is
planned exclusive, so every chat turn runs the barrier — and a `protected_keys` set that
excluded the admitted batch would have unloaded that turn's own warm chat model and
cold-loaded it again on every message, which is both a regression against shipped
behaviour and the worst possible outcome on hardware whose cold loads are measured in
minutes.

**One structural consequence to carry into the plan:** the hold-off is a durable
`not_before` column, which makes seven columns across two migrations and adds the one
new admission-side query filter in this track — tasks 3 and 4 re-pin the claim's query
counts together (§7).

### Round 3 — final confirm (revision 3)

Round 3 returned **CLEAN on all three confirms** — the barrier holds in both
directions, neither refusal bound can spin the tick loop, and the protected set matches
shipped behaviour with its one exception written down and pinned — with two nit-grade
residuals of record, both adjudicated ACCEPT by the orchestrator and applied here. No
round 4.

| Finding | Applied in | Note |
|---|---|---|
| R3-1 (Nit) `not_before` is a genuine admission-order change that two places still call unchanged | §4 item 3, §12 | Recorded in the ordering half of the amendment, where a reader looks for an admission-order fact, with one sentence on why the proof survives (the exclusion is time-bounded and self-clearing, and an effectively-exclusive head is still admitted alone the instant the machine is idle); §12's carve-out widened to name it. |
| R3-2 (Nit) a held-off job is invisible on the Queue page | §3.6, §5, §7 task 3 | **Ruling applied:** the hold-off renders on the waiting row ("waiting for engine memory — retries at HH:MM"), display only — server-rendered from the row the page already loads, revealed by the existing refresh, no new query and no script. Pinned beside the passed-over reading. |

Both are consistency-of-record rather than mechanism, and they close the same argument
§3.6 already makes and Q14 already commits to: a delay the queue imposes deliberately is
never left for an operator to infer. **The spec was closed at revision 4**, and reopened once for the steward amendment below.

## Owner rulings (2026-09-21)

All seven flagged decisions in §10 were ruled by the owner as one word — **"accept all
recommendations"** — making the recommended option binding in each: loud unset budget with
measured prefill (1), the engine-reported third rung with its columns (2), keep-the-maximum
recorder with zero tolerance (3), affinity on by default with `MAX_PASSOVERS = 3` (4),
per-kind wait ceilings editable on the settings page (5), refusal bound ends in an honest
job failure (6), and idempotent stranded-turn auto-repair (7). Plan authors treat these as
settled; none remains open.

### Steward amendment (2026-09-21)

The engine-owning session verified §3.1–§3.3 line-by-line against the real adapters and
returned three findings; the orchestrator accepted all three. This is a targeted
amendment, not a review round — two of the three correct statements this spec could not
have checked from the queue column, and the third adds residuals it could not have known.

| Finding | Applied in | Note |
|---|---|---|
| S-1 §3.3(d)(3)'s narrowing was **unimplementable**: "TTL'd memo vs real residency endpoint" is knowable only from adapter source, and no seam carried it | §3.3(a), §3.3(d)(3), §5, §9.10, §12 | A second optional declaration, `residency_authority: "endpoint" \| "memo"`, in the same getattr/degrade idiom as `unload_scope`, absent → `"memo"` (safe: triggers the precautionary call). The image adapter's two lines (`unload_scope="endpoint"`, `residency_authority="memo"`) are **PRE-CLEARED by the engine steward** and land in their column with the plan; the text adapter's are that steward's call, factually `"model"` / `"endpoint"`-authority. |
| S-2 the rung-3 label was **untrue on the image engine**: it reports no per-model residency or size (`list_installed` `size=None` by design), so its `loaded_size` is the adapter's own post-run delta from the same TTL'd memo | §3.1 | Label changed to "from the engine's residency snapshot on *date*", which is honest on both engines, plus one paragraph naming the rung-3 ≈ rung-2 equivalence there (near-valueless on the image engine, genuinely engine-reported on the text engine). |
| S-3 three adapter-level residuals the queue column could not have known | §11 (three new bullets), §12 | (a) the no-baseline path returns `True` on a 2xx plus one queue-idle check when `/system_stats` is unreadable at call start — a `True` that observed nothing, in exactly the precautionary-call case, launching into possibly-held memory; (b) machine-wide memory drift ≥ 256 MiB during the poll fakes the rise and drops the run memo; (c) the residency memo is LRU-capped at eight endpoints, so a ninth ages the oldest belief out and buys extra precautionary calls. **Named, not fixed:** all three are adapter-side, and §12 says so. |

S-1 is the load-bearing one: without a declaration the round-2 narrowing could not have
been built at all, and a plan author would have discovered that only after reading
adapter source. S-3 changes no mechanism — it makes the barrier's honest failure modes
visible in the document that claims the barrier is safe. **Revision 5; closed again.**
