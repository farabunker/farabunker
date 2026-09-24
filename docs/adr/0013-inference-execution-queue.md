# ADR 0013 — The inference execution queue

**Status:** Accepted
**Date:** 2026-08-24
**Names generalised:** 2026-09-14 — illustrative model names in this ADR's prose were
replaced with capability language ahead of publication; the decision, its date, its options
and its consequences are unchanged.

## Context

Before this build, every model call in the platform ran synchronously,
inline in the request/response cycle: `AskView` called
`modules.rag.retrieval.answer_question` directly and returned its result in
the same HTTP response. `console/inference/views.py::role_reencode` did the
same for a re-encode. Two gaps followed directly from that shape:

1. **No ordering, no concurrency control.** Two Ask requests, or an Ask and
   a re-encode, could run at the same moment with no coordination at all —
   whatever memory both needed simply had to fit, with nothing checking that
   it did, and no way to say "this one matters more, let it go first."
2. **No seam for a second kind of model-consuming work.** A re-encode
   (`rag.embed`'s drift guard, ADR 0010 §1) and an eventual conversational
   agent (`chat.converse`, ADR 0010's 2026-08-22 amendment; ROADMAP.md Phase
   1.6) both need to run *against* models, on a schedule the platform
   controls, not merely inline with one HTTP request.

The owner's requirement, from the plan this ADR's tasks (T1–T8) implement:

> the queue [is] processed in order — [with] memory-aware concurrency: some
> things can run concurrently if there's memory available for small model
> loads, but need to run sequentially if a job is currently using memory.
> Priority matters too — if something comes in with a higher priority late,
> it can overtake lower-priority items still waiting. Chat is the first
> feature to move through the queue, expanding later to all model calls.

This ADR records the queue built to satisfy that requirement: a single,
Postgres-backed admission point every model execution in the platform now
flows through, a pure and independently-testable scheduling policy, a
memory-accounting model that distinguishes a hardware fact from an
operational bound, a job-kind registry that lets a feature app declare a
unit of queued work without importing across a trust boundary, and the
async Ask contract that is `rag.ask`'s first real consumer.

## Decision

### 1. One door: every model execution flows through the queue

`core/inference/queue.py::enqueue()`/`get_job()` is now the *only* way a
model runs. `AskView` (`modules/rag/views.py`) no longer calls
`answer_question` inline — it validates, pre-checks, enqueues a `rag.ask`
job, and returns `202`. The prior synchronous "call `answer_question` and
return its result in the same response" path is deleted outright, not kept
as a fallback. `console/inference/views.py::role_reencode` enqueues
`rag.reencode` the same way. There is exactly one code path in the platform
that ever calls `modules.rag.retrieval.answer_question`:
`modules.rag.jobs.run_ask`, run by the queue worker.

**Auto-routing is rejected.** The queue decides *when* an admitted job runs
and *how many* run together; it never decides *which model* answers a
request. That choice — the role binding, or the Ask-time picker's
per-question override (ADR 0010's per-question-selection amendment) — stays
the operator's, made explicitly in the console or the picker. The platform
does not guess a model on a caller's behalf anywhere in this build.

### 2. The priority grammar

`InferenceJob.priority` (`console/jobs/models.py`) is a positive integer;
lower runs first, and ties break by primary key (insertion order) —
`InferenceJob.Meta.ordering = ["priority", "id"]`, the same tuple
`console.jobs.scheduler.plan_admissions` sorts candidates by.

The value is resolved once, at enqueue time, by a zero-ceremony chain with
every rung visible to the operator (`console.jobs.backend._resolve_priority`):

1. an explicit `priority` argument to `enqueue()` — e.g. the Ask page's
   optional `priority` field, threaded through `AskView`;
2. else the job kind's own `JobKind.default_priority`
   (`core.inference.jobkinds`) — a **task-class prioritization**: `rag.ask`
   registers no override (rides the global default), while `rag.reencode`
   registers `default_priority=200` (`console/inference/apps.py`) — a fixed
   200-point handicap behind interactive Ask traffic, so a background
   re-encode a role's drift guard triggers never jumps ahead of a person
   waiting on an answer, without the operator having to remember to set a
   priority on it every time;
3. else `JobSettings.get_solo().default_priority` — the queue-wide default,
   `100` out of the box, operator-editable from the Queue page.

Positivity is enforced on whichever rung actually supplies the value: a bad
explicit argument is a caller bug, but a job kind registering a
non-positive `default_priority` is equally a bug, in that kind's own
registration — both fail loudly at enqueue time rather than silently
producing an invalid row.

### 3. Strict priority order, NO backfill

`console/jobs/scheduler.py::plan_admissions` — one pure function, stdlib +
dataclasses only, no Django import, no DB, no I/O, provable by table tests
alone — walks candidates in `(priority, id)` order and **stops at the
first one that cannot be admitted this round**. No candidate is ever
skipped in favor of a later, lower-priority one that would technically fit.

This is a deliberate v1 policy choice, not an oversight, made for one
reason: **it makes deadlock structurally impossible.** An unknown-footprint
or oversize head-of-queue job is "effectively exclusive" (rule 2 below),
which this policy always admits the instant the machine goes idle — it
never sits blocked forever behind a peer that will never yield, and never
waits behind an unbounded string of higher-priority jobs that keep it from
ever being reached, because it does not need to *fit alongside* anything to
run; it only needs the machine to itself, which idle guarantees.

The honest scoping, stated in the scheduler's own module docstring: this is
narrower than "starvation is impossible." Within one priority number's own
position in the order, this policy never reorders past it — but a sustained
inflow of jobs at a *lower* priority number can still keep arriving ahead of
an older, higher-priority-number job indefinitely and starve it. There is no
aging/anti-starvation knob in this policy, and each job kind's own
`default_priority` rung (§2) makes sustained lower-number inflow an entirely
reachable operational scenario, not a hypothetical one. The cost — a
blocking head, or a busy lower-priority stream, can leave technically-fitting
work waiting behind it — is accepted in exchange for the deadlock guarantee
and the simplicity of never backfilling. Backfill is a named fast-follow:
giving either property back requires a new ADR, not a quiet tweak to this
one's code.

### 4. Memory accounting

**Resident-set dedup.** Every model a job holds is keyed by
`(engine, endpoint_normalized, model_id_normalized)` — `SchedModel.key` in
`console/jobs/scheduler.py`, built by the claim code
(`console/jobs/claim.py::_sched_candidate`) with
`console.inference.discovery.norm_endpoint()`/`norm_tag()` so a bare tag
(`<name>-embed`) and its `:latest` spelling are recognized as the same
on-disk weights, never double-charged. Two jobs sharing a model key pay for
it once, at the largest known reading of that key seen this round
(None-dominant, then MAX-folded — `scheduler._dedup_bytes`), never at
whichever reading happened to be seen first.

**Provenance, not a stored guess.** Every footprint the scheduler reasons
about comes from `console.inference.bindings.footprint_for(engine, endpoint,
model_id)`, resolved **fresh** on every planning round
(`console/jobs/claim.py::claim_and_admit`, step 3) — never read once from
`InferenceJob.model_refs`'s stored snapshot and reused. That snapshot's own
`footprint_bytes` is `None` at enqueue time by design
(`core.inference.jobkinds.ModelRef`) and is only ever filled in later, at
claim time, for display/audit purposes; the scheduler itself never reads it
back.

**Two categories of number, never confused.** `JobSettings.memory_budget_bytes`
ships **unset** (`null`) — sequential mode, at most one job on the whole
machine ever (`plan_admissions` rule 1). This is a fact about the operator's
own hardware; there is no platform-wide number that could be right for
every box, so it is honestly unknown rather than guessed, the same "no baked
defaults" ruling ADR 0010's third amendment records for a model choice.
`JobSettings.max_concurrent_jobs`/`default_priority`/`retention_limit`, by
contrast, are **operational bounds** the platform enforces on its own
resource use — they ship *with* a value (4 / 100 / 100) and are visibly
changeable, never silently assumed, the same category `DEFAULT_CONTEXT_WINDOW`
(ADR 0010's context-window amendment) and `RagSettings.history_limit`
(ADR 0010's Ask-history section) already established as precedent.

**Unknown or oversize runs alone.** `effectively_exclusive()`
(`console/jobs/scheduler.py`) is true when a job declares itself exclusive,
holds any model with an unmeasured footprint (or declares no models at
all — a planner bug, treated the same as unknown), or its own dedup'd total
strictly exceeds the budget. Such a job is admissible *only* into a
genuinely idle machine, and is the only thing admitted that round when so —
nothing can be reasoned about safely running alongside a job whose true cost
isn't known. Oversize deliberately still gets this "admit alone when idle"
treatment rather than a permanent refusal: refusing it forever would
deadlock the owner's own job forever, which is worse than one job running
over the stated budget for its duration. The Queue page surfaces a
`runs alone` chip for the two *stable, storage-level* reasons —
declared-exclusive and unmeasured-footprint (`console/jobs/views.py::
_present_row`) — but deliberately not for the oversize reason, which would
require re-deriving the same dedup'd-footprint-vs-live-budget arithmetic
`plan_admissions` already owns a second, independent time inside the view;
see §8 for this as a named gap, not an oversight.

**Eviction reconciles residency to the plan.** Admission
(`claim_and_admit`) plans against *declared* footprints; eviction
(`console/jobs/worker.py::Worker._evict_to_match_plan`) makes the machine's
*actual* resident memory match that plan before any newly admitted job
launches. Two passes: an admitted-exclusive job's own endpoints are evicted
of everything not needed, **uncapped** (there is no later tick to catch up
on for that case, and the scheduler already blocks every other admission for
as long as an exclusive job runs); every other endpoint's non-exclusive,
budget-driven eviction is capped at `MAX_UNLOADS_PER_TICK` (2) `unload()`
calls per tick, so a slow/wedged engine call can never eat enough of this
worker's own heartbeat margin to have another worker's orphan sweep reclaim
jobs it still legitimately holds. An engine lacking `list_installed`/
`unload` degrades to today's idle-timeout-only behavior, logged once.

### 5. The kind registry

A **job kind** is a named, model-consuming unit of work
(`core.inference.jobkinds.JobKind`): a `planner` (resolves the `ModelRef`s a
job needs and whether it must run exclusively, called at enqueue time), a
`handler` (does the actual work, called once claimed with `(payload, models,
ctx)` — `ctx` is a `core.inference.jobkinds.JobContext`, the per-job
progress/checkpoint seam added 2026-08-24; a handler may ignore it), and a
`summarizer` (a one-line operator-facing row summary for the Queue page) —
each a dotted-path string, resolved lazily, never a live callable at
registration time. Enrollment is `register_job_kind(JobKind(...))`, called
from a feature app's `AppConfig.ready()`; `get_job_kind`/`all_job_kinds` are
the read side both the enqueue seam and the worker use.

Two kinds ship, proving the registry generalizes, and deliberately owned by
different apps:

- **`rag.ask`** (`modules/rag/jobs.py`) is **module-owned** — registered
  from `modules/rag/apps.py::RagConfig.ready()`. Its planner resolves both
  RAG roles (answer + embed); its handler re-resolves and re-health-checks
  both, fresh, at run time (a queued job can sit for a while; the models it
  declared at enqueue time may no longer be live by the time it runs) before
  calling `answer_question` and recording Ask history.
- **`rag.reencode`** (`console/inference/jobs.py`) is **console-owned** —
  registered from `console/inference/apps.py::InferenceConfig.ready()`, not
  from `modules/rag`. This is the boundary law, not an inconsistency: a
  re-encode's actual work
  (`console.inference.drift.run_rematerialize`) reads/writes
  `console.inference.models.Materialization`, a console-side table, and the
  module boundary (ADR 0010 §2) forbids a `modules/` app from importing
  `console.inference` beyond its one sanctioned seam
  (`console.inference.bindings`) — which `drift` is not. Rather than widen
  that seam or move console-owned state into `core/`, the kind is registered
  from the side that already owns the work; the job-kind **key**,
  `rag.reencode`, still names the RAG role it re-encodes — a key is just a
  string, not an import, so naming it crosses no boundary at all. It runs
  `exclusive=True`: a re-encode drops and rebuilds the very chunk table
  `rag.ask` reads from mid-run, so it must have the machine to itself for
  its full duration.
- **`vision.generate`** is the anticipated third kind (the vision track's
  image-generation work), expected to follow the same `rag.ask` shape — a
  module-owned planner/handler registered from its own feature app, riding
  this same registry with zero framework changes.

### 6. The async Ask contract

`POST /rag/ask/` now returns **202** immediately:
`{"job_id", "state": "queued", "position", "priority", "status_url"}`. The
page polls `GET /rag/ask/jobs/<job_id>/` (`AskJobStatusView`), which reports
`{"state", ...}` per state — `queued` (position/priority/submitted_at),
`running` (started_at/answered_by), `cancelled`, `failed`
(error/setup_url), or `succeeded`, whose body is `job.result` spread first
with `"state"` set **last** so it always wins the merge: `job.result` is a
strict **superset** of the pre-queue synchronous 200 body
(`{"answer", "citations", "answered_by", "summary"}`), so anything that read
that body before still finds every key it looked for.

**History is answers, the queue is attempts.** `modules.rag.jobs.run_ask`
stores its full result dict on the job row (`InferenceJob.result`) — the
poller's source of truth — and *separately* writes a trimmed
`modules.rag.models.AskRecord` snapshot via `services.record_ask`, exactly
as the pre-queue synchronous path did. This is a deliberate double-write,
not duplication by accident: the job row is retention-pruned by
`JobSettings.retention_limit` (default 100, an operational bound) and can
disappear once a job ages out, while `AskRecord` is the durable, browsable
Ask History page, pruned to its own independent `RagSettings.history_limit`
(also default 100). Both bounds default to the same number by convention,
not by a shared constant — they are two separate operator-editable knobs
that happen to start equal.

### 7. Retention, and the worker lifecycle in brief

`console/jobs/backend.py::_prune_finished_jobs` runs on every `enqueue()`
call, deleting terminal-state (`succeeded`/`failed`/`cancelled`)
`InferenceJob` rows beyond the newest `retention_limit` — the same
prune-on-insert grammar `modules.rag.services._prune_ask_records` already
established (ADR 0010's Ask-history section): enforced exactly once per
enqueue, synchronously, no background task, no window where the table can
grow past the limit.

The worker (`console/jobs/worker.py::Worker`) is documented in full in its
own module docstring; this ADR records only the shape: a heartbeat
(`HEARTBEAT_SECONDS`, throttled) is the one fact an orphan sweep trusts,
a job whose worker stops heartbeating is requeued once
(`attempts` bumped) and permanently failed on a second orphaning
(`STALE_AFTER_SECONDS`, 120s); `SIGTERM`/`SIGINT` trigger a bounded drain
(`SHUTDOWN_GRACE_SECONDS`, 20s) that hands back whatever is still running
rather than abandoning it silently; every terminal write (a finished job's
result, a requeue, a heartbeat) is conditioned on the exact `claim_token`
that job was claimed under, never a bare state check, so two workers racing
the same row can never clobber each other's outcome. See that module's
docstring for the full trace of why each of these exists.

### 8. Named tradeoffs and deferred work

- **CLI bypass.** `manage.py ask`/`manage.py reencode` (if still present)
  call the retrieval/rematerialize functions directly, bypassing the queue
  entirely — documented, not fixed here. A `--queue` flag that enqueues
  instead is a named fast-follow, not built by this ADR.
- **Job-id access is unauthenticated.** `GET /rag/ask/jobs/<job_id>/` and
  `POST /queue/<job_id>/cancel/` have no access control today — the same
  Identity-phase inheritance ADR 0010 already records for the `/inference/`
  console's mutation endpoints (Phase 1, no Identity & Auth layer yet). A
  job id is a sequential integer; enumerating other operators' questions is
  possible today on a multi-user box. This closes when Identity & Auth
  lands (Phase 2), same as every other unauthenticated surface this
  platform has today.
- **No-JS ask submission is deferred.** The Ask page's poll loop
  (`ask.html`) requires JavaScript to submit and watch a job; a no-JS
  submit-and-redirect-to-a-status-page fallback (matching the Queue page's
  own zero-JS posture) is not built in this ADR.
- **Per-endpoint budgets** are a named extension seam, not built:
  `JobSettings.memory_budget_bytes` is one number for the whole machine,
  not per engine endpoint. (This bullet originally paired that with
  "progress reporting" as a second, not-yet-built extension seam —
  per-job progress landed 2026-08-24, `core.inference.jobkinds.JobContext`;
  see [ADR 0014](0014-media-ingestion.md) for the fuller
  media-ingestion story it's part of, and this ADR's own 2026-08-24
  amendment below. `vision.generate` is the first kind to USE it
  (2026-08-25): its wait loop reports elapsed seconds with `total=None` and
  a phase label ("waiting on the image engine" / "generating"), because
  ComfyUI's HTTP surface exposes no fraction of a generation. The Queue
  page therefore shows a live `mm:ss` line and no bar, which is the honest
  rendering `_progress_text_and_percent` was written for.)
- **The oversize reason is unexplained on the Queue page.** §4's chip
  covers `declared`/`unmeasured` only; a job running alone because it is
  oversize relative to the *current* memory budget shows no chip and no
  explanation at all (`console/jobs/views.py::_present_row`'s own
  docstring records this narrowing deliberately, rather than risk showing a
  re-derived reason that could disagree with the scheduler's actual
  decision). An operator watching a job run alone with no visible cause has
  no way to tell "declared exclusive" apart from "oversize" from the page
  alone today; surfacing the true reason safely (reusing
  `effectively_exclusive()` itself, at render time, rather than
  re-implementing its arithmetic) is deferred, not built by this ADR.

## Consequences

- There is exactly one path in the platform that runs a model: through the
  queue. This is a real behavior change for every caller — an Ask no longer
  returns inline, and every future model-consuming feature (`chat.converse`,
  `vision.generate`) must register a job kind rather than reach for a
  synchronous call, by construction (there is no other seam left to reach
  for).
- **`JobStatus.summary`, added 2026-08-25.** The single-job read shape
  still carries no payload; it carries the string the job's OWN registered
  summarizer produced (`console.jobs.backend.summarize_job`, moved there
  from `console.jobs.views._summarize` so the Queue page and this seam
  cannot drift). It exists because a caller waiting on a job needs to name
  it: `/vision/`'s queued placeholder said "Image generation" — the job
  kind — for the whole queued window, on every path.
- **Operational: the worker container.** `compose.yaml` gained a fourth
  service, `worker` (`python manage.py run_jobs`), following the same
  same-image-different-command pattern the `watcher` service already
  established (ADR 0006 §6) — see that ADR's amendment below.
  `stop_grace_period: 30s` is set specifically to exceed
  `SHUTDOWN_GRACE_SECONDS` (20s), so Docker's own SIGKILL is never what ends
  the worker process; its own drain-then-exit always gets there first.
- **Operational: no autoreloader for `watcher`/`worker`.** `web` runs under
  Django's dev auto-reloader (`compose.override.yaml`), so a code edit
  under the bind mount takes effect immediately; `watcher` and `worker` run
  a single management command directly, with no reload machinery at all — a
  code change affecting either requires an explicit
  `docker compose restart watcher worker` (or a rebuild), which the dev
  workflow must remember to do on every deploy touching queue or ingest
  code.
- **Operational: live-deploy serialization.** `compose.yaml`'s bind mount
  (`.:/app`) serves whatever is checked out on the primary repo's `main`
  branch to `web`/`watcher`/`worker` alike — the live containers are just
  reading that one working tree, live, on every request/reload. On the
  night of 2026-08-24 (2026-08-23 local time) a session merged a feature
  branch directly into that live `/app` checkout rather than into `main`
  first; the merge conflicted (`config/urls.py`, `templates/_shell.html`)
  and was left with unresolved conflict markers in place, which
  `runserver`'s autoreloader picked up as a code change and crashed the
  live web server on — a real outage, restored by `git merge --abort`
  followed by a clean merge of the actual `main` SHA. The rule this
  incident fixes in place: **live `/app` merges `main` SHAs only** — a
  feature branch is tested via its own isolated `scripts/preview` stack
  (ADR 0011), never by merging straight into the primary checkout to see
  what happens — and **container git operations against that checkout are
  serialized across sessions**, so two sessions can never touch `/app`'s
  working tree at the same moment.
- Memory-aware concurrency is real but bounded: a correctly configured
  budget lets small jobs run together and lets a person's Ask return quickly
  even while a background re-encode would otherwise have blocked it — but an
  operator who leaves the budget unset gets fully sequential execution, by
  design, since no number is safe to guess on their behalf.
- The deadlock-proof, no-backfill scheduler trades a real possibility —
  sustained lower-priority-number inflow starving an older, higher-number
  job — for a guarantee that no configuration of priorities and budgets can
  wedge the queue permanently. Backfill, if ever added, needs its own ADR
  (§3).

## See also

- `console/jobs/scheduler.py` — the admission policy in full, with its own
  numbered-rule docstring this ADR's §3–4 summarize.
- `console/jobs/models.py`, `console/jobs/claim.py`, `console/jobs/worker.py`
  — the storage shape, the claim transaction, and the worker lifecycle, each
  documented in depth in their own module docstrings.
- [ADR 0010](0010-model-management-framework.md) — the role/binding
  framework this queue's job kinds resolve models through; amended below to
  cross-link this ADR.
- [ADR 0006](0006-containerization-and-isolation.md) §6 — the
  same-image-different-command precedent (`watcher`) the `worker` service
  follows; amended below.
- [ADR 0011](0011-branch-preview-stacks.md) — the preview-stack mechanism
  that exists so a feature branch is tested on its own isolated checkout,
  never by merging it straight into the primary stack's live `/app` to see
  what happens (§ Consequences' 2026-08-24 incident).

## Amendment (2026-08-24) — Amended by ADR 0014: progress/checkpoint built, the handler signature changed, `on_terminal` added

[ADR 0014](0014-media-ingestion.md) (media ingestion) is the build that
needed §8's deferred progress seam, and it built it — for every job kind at
once, not only its own. Three changes to this ADR's contracts, recorded here
rather than only there.

**§8's "progress reporting" extension seam is closed.** `InferenceJob` gained
two columns, deliberately separate so a display write can never clobber
resume state: `progress` (`{"done", "total", "unit", "label"}` — a dict, not
a percent, because `total: null` must be expressible and this platform never
fabricates a denominator) and `checkpoint` (opaque, kind-owned execution
state neither the worker nor the column ever interprets). The Queue page
renders progress text on a running row, and a bar **only** when the total is
genuinely known — never a guessed width. Per-endpoint budgets, the other
half of that §8 bullet, remain unbuilt.

**§5's handler signature changed, once, for every kind:
`handler(payload, models, ctx)`.** `ctx` is a `core.inference.jobkinds.
JobContext` — a frozen dataclass carrying `job_id`, `attempt`,
`checkpoint_state`, and the two write methods `report_progress(...)` and
`checkpoint(state)`. It holds no database access of its own; the two write
paths are plain closures the worker injects, so `core/` still imports
neither side's storage layer. A handler may ignore `ctx` entirely, exactly
as it may already ignore `models`. This break was taken deliberately while
four handlers existed, rather than repeatedly later; there is no un-`ctx`'d
handler shape left to write.

Two invariants this ADR already stated are preserved by construction, and it
is worth saying how:

- **§7's single-writer rule holds.** Progress and checkpoint writes are
  token-conditional single-row `UPDATE`s (`pk=… AND state=running AND
  claim_token=…`) touching only their own column — disjoint from the
  heartbeat's — so a stale worker cannot write progress for a job it no
  longer holds, and neither write clobbers the other. Progress writes are
  throttled on the WORKER side (`PROGRESS_INTERVAL_SECONDS`, 5s), so a
  handler may call `report_progress` on every loop iteration without
  reasoning about write cost; `checkpoint` is unthrottled, so a handler
  calls it only at a real resume boundary.
- **Requeue is the resume mechanism.** The drain, orphan, and unlaunched
  requeue paths never touch either column. A drain leaves `attempts`
  unchanged, so a long transcription survives arbitrarily many deploys,
  resuming each time; a crash still burns the job's one attempt, which is
  this ADR's existing and correct policy.

A thread-local ambient reporter was **rejected**, for the same
ambient-global reason ADR 0010's 2026-08-23 amendment records for the
deleted `configure_settings()`.

**§5's `JobKind` gained an optional `on_terminal` hook.** ADR 0014's
`rag.ingest` is the first kind whose durable side-effect *predates the job* —
a `Document` row created at PENDING before the job was ever enqueued — which
exposed a structural hole in this ADR's lifecycle: three paths reach a
terminal state without the handler ever running, so none of them reach a
handler's own writeback. They are: **cancelled while still queued**
(`backend.cancel_job`), **permanently failed by a second orphaning**
(`claim._sweep_orphans`), and **the handler never started** at all
(`worker.Worker._execute`'s guard — an unregistered kind, a bad dotted path,
a malformed model-ref dict).

`on_terminal` is a dotted path registered exactly like `planner`/`handler`/
`summarizer`, invoked through `jobkinds.invoke_on_terminal` from all three
sites. Three properties of it are load-bearing:

- It runs **after the job row's terminal write commits** — all three sites
  schedule it with `transaction.on_commit`, never inline, because
  `_sweep_orphans` runs inside `claim_and_admit`'s advisory-lock transaction
  and arbitrary feature-app code must never execute inside that window.
- **Exceptions never propagate.** `invoke_on_terminal` catches and logs
  them, so a broken hook can never break `cancel_job`'s return, the sweep's
  completion, or the worker's own failure writeback — the same tolerance
  `views._summarize` already applies to a kind's summarizer.
- A kind with no external state to fix up correctly registers **nothing**.
  `rag.ingest` is the only kind with a hook today; `rag.ask`,
  `rag.reencode`, and `vision.generate` all leave it `None`, and each of
  their registrations says why.

§8's other named gaps — the CLI's `--queue` bypass, unauthenticated job-id
access, no-JS ask submission, per-endpoint budgets, and the unexplained
oversize reason on the Queue page — are all unchanged by this amendment.

## Amendment (2026-08-25) — Reclaimed-attempt in-memory bookkeeping collisions fixed (two of them); three follow-ups named from live evidence

A peer session reproduced a defect live (InferenceJob 22, their preview
stack, 2026-08-25 04:50–05:10 UTC): `console/jobs/worker.py::Worker._execute`'s
`finally` popped `self._active_tokens[job_id]` **unconditionally**, keyed by
`job_id` alone. `self._active_tokens`/`self._futures` are in-process
bookkeeping, distinct from the on-row `claim_token` §7 already describes as
the single source of truth every DB write is conditioned on — but nothing
before this amendment conditioned the WORKER'S OWN in-memory cleanup on that
same token, and the two are supposed to agree.

The trace: attempt A runs long (its handler still genuinely alive) while its
heartbeat goes stale (e.g. this same process starved its own heartbeat
writer during a slow tick); `_sweep_orphans` (`console/jobs/claim.py`)
requeues the row; this **same worker's** very next `tick()` re-admits and
launches it as attempt B, under a brand-new `claim_token`, registered into
`self._active_tokens[job_id]` immediately (`tick()` already did this
correctly, per this ADR's original §5/§7 account). Attempt A's thread,
unaware, eventually reaches its own `finally` and — pre-fix — popped
`self._active_tokens[job_id]` regardless of whose token was actually stored
there, evicting attempt B's entry. `_maybe_heartbeat` (§7) reads
`self._active_tokens.values()` to decide which rows to refresh; with B's
token gone, B's row was never heartbeated again, orphaning it a **second**
time and permanently failing it while B's handler was still genuinely
running and writing progress.

**The fix: the pop is now token-conditional.** `_execute`'s `finally` only
removes `self._active_tokens[job_id]` when the stored value still equals
the `claim_token` *that specific attempt* was launched with — a stale
attempt (one this process has already re-claimed under a newer token)
leaves the dict alone entirely.

**A second defect, found reviewing this same fix (same day): `self._futures`
needed a matching change too, and did not originally get one.** The first
cut of this amendment reasoned that `self._futures[job_id]` needed nothing
extra because `_launch` only ever **overwrites** that entry (never pops
it), so a reclaimed attempt's own `_launch` call already replaces it with
that attempt's own future before the earlier attempt's thread can reach its
`finally`. That is true about *ordering* and wrong about *consequence*: the
superseded attempt's `Future` is real and still running on a live pool
thread — once its entry is overwritten, nothing anywhere still references
it. Two things then broke silently, specifically for a superseded attempt:
`_prune_finished_futures` could never surface an exception that escaped
`_execute`'s own never-raise guard for it (the entry it would have read is
already gone by the time that attempt finishes), and `_shutdown`'s
`executor.shutdown(wait=True)` branch could block on that same untracked
thread with no bound at all if `_drain_inflight`'s own `wait()` never even
looked for it — defeating `SHUTDOWN_GRACE_SECONDS` outright, the exact
failure mode this worker's SIGTERM handling exists to avoid.

**The fix: `self._futures` is now keyed by `(job_id, claim_token)`, not
bare `job_id`.** `_launch` INSERTS a fresh entry per attempt instead of
overwriting one (a reclaimed attempt's `claim_token` is always newly minted
by `claim_and_admit`, so this key can never collide with an earlier
attempt's own); `_execute` still never pops its own entry (cleanup stays
`_prune_finished_futures`'s job alone, on `future.done()`), now correctly
surfacing an escaped exception for *every* attempt independently rather
than only whichever one currently occupies `job_id`'s slot.
`_drain_inflight` reads each attempt's own token straight from its
`self._futures` key — no separate `self._active_tokens` lookup, and no
possibility of reading a stale/missing token for an already-superseded
attempt — so its requeue UPDATE stays exactly as token-conditional as
before, just now reachable for a superseded attempt too (it simply matches
zero rows once that attempt's row has moved on, the same "stale token, zero
rows" shape every other writeback in this module already uses). `_shutdown`
now checks `self._futures` directly for any still-not-`done()` future
rather than trusting `_drain_inflight`'s return value as the complete
account of what's in flight (that return value only ever reports rows it
successfully requeued, which a superseded attempt's late, no-op write never
is) — so a superseded attempt still running past the grace period correctly
routes to the bounded `os._exit` path instead of the unbounded
`wait=True` one. Regression coverage:
`console/jobs/tests/test_worker.py::TestReclaimedAttemptTokenCollision`,
`::TestSupersededAttemptExceptionNet`,
`::TestShutdownAccountsForSupersededAttempt`.

**Confirmed, not changed, by this incident:** the token-conditional DB
writes §7's single-writer account already describes — `JobContext`'s
`report_progress`/`checkpoint` writers and `_execute`'s own terminal
writeback, all filtered on `pk=… AND state=running AND claim_token=…` — were
never the defect. Attempt A's writes were correctly discarded as stale
throughout; the bug was entirely in-memory, confined to the worker's own
bookkeeping dicts.

**Three follow-ups, named here from the live evidence and this same-day
review, deliberately not built by this amendment.** No memory-governance
amendment or successor ADR names or schedules these today — recorded here,
honestly, as open and unscheduled rather than pointed at a document that
does not actually track them:

1. `STALE_AFTER_SECONDS` (120s) cannot survive a cold model load that
   starves the whole worker process — the live incident observed stalls of
   roughly 500s on unified-memory hardware during exactly that kind of load.
   A dedicated daemon heartbeat thread (decoupled from the tick loop a slow
   model load can starve) and/or kind-aware staleness thresholds (a job kind
   known to cold-load large models gets a longer allowance than one that
   never does) are both candidate fixes, neither built here.
2. A job kind's own wait-timeout returning `SUCCEEDED` while its engine is
   still actually sampling releases §4's exclusive slot early — the machine
   looks free to the next admission round while the prior exclusive job's
   engine is still doing real work underneath it. The slot must instead be
   held until the engine itself reports a terminal state, not until the
   kind's own wait gives up waiting. Not built here.
3. `Worker._evict_to_match_plan` derives `needed_keys` (the models eviction
   must leave alone) from currently-`RUNNING` job rows alone. An
   orphaned-but-not-yet-readmitted attempt — its row briefly back at
   `queued` between `_sweep_orphans`' requeue and the very next tick's
   re-admission, while its handler is STILL genuinely running and still
   holding a model loaded (the exact "attempt still alive after its row
   looks orphaned" shape this whole amendment is about) — is invisible to
   that derivation for the width of that window: another job's admission on
   that same tick can see the model as unneeded and evict it out from under
   the still-running handler. This is a distinct gap from the two
   in-memory bookkeeping defects fixed above (those were about the
   *worker's own accounting* surviving a reclaim; this one is about what
   *eviction* is allowed to see), and is not fixed here.

§8's named gaps, and the three follow-ups above, remain the honest list of
what this ADR still defers.

## Amendment (2026-08-29) — Amended by ADR 0015: an agent turn is a job, and a tool runner may never wait on one

**`agent.turn` is registered, and it is exclusive.** [ADR 0015](0015-agent-layer-and-tool-contract.md)
(the agent layer) is the build that made a tool runner a caller of this
queue for the first time; `agents/apps.py`'s `ready()` registers the turn
as an ordinary job kind (`key="agent.turn"`,
`agents/apps.py:57`), with a planner (`agents.runtime.jobs.plan_turn`) that
resolves the agent's own chat role plus the union of every tool role it may
reach and returns that set as the admission snapshot §4's `ModelRef`
already describes. `Worker._evict_to_match_plan` (`models/queue/
worker.py:801`) evicts to match that declared set before the turn launches,
exactly as §4 already requires for every job kind — §1's one-door rule is
consumed by a fifth job kind, not weakened for one.

**The new constraint, stated as new.** This ADR does not say "nested
enqueue is forbidden" anywhere, and ADR 0015 does not pretend it does
(spec §1.2's honesty note). The rule, in one sentence: *a tool runner must
never block on a queue job; it may enqueue one and return its id, but it
must never call `get_job` in a loop and never enqueue work whose result it
needs.* The derivation is this ADR's own §3–§4: sequential mode is the
shipped default (`JobSettings.memory_budget_bytes` ships `null`), and
`plan_admissions`' rule 1 (`models/queue/scheduler.py:374-380`) admits
nothing while any job is running — so a job waiting on a job holds the
machine's one slot against a job that can never be admitted while it
waits. `STALE_AFTER_SECONDS` (120s, `models/queue/worker.py:106`) then
times the waiter out as orphaned, and the orphan sweep turns a second
orphaning into a permanent failure. Deadlock, then data loss — certain,
not probable.

**Why inline is not a new seam.** Every tool a turn calls is a function the
platform already calls synchronously inside a job handler: `rag.search`/
`rag.ask`'s runners call the same `retrieve_nodes`/`answer_question`
functions `rag.ask`'s own job-kind handler already calls, and
`vision.generate`'s tool runner submits to the image engine and polls it
(`tools.vision.services.submit_job`/`wait_for`) exactly as `vision.
generate`'s own job-kind handler already does — a wait on an external
engine's HTTP status, never a wait on this platform's queue. `rag.ingest`
is the one fire-and-forget enqueue a tool call makes: it returns a job id
rather than waiting on one, so the "never block" rule above is never
exercised, not bent.

**What is unchanged, and one path correction.** §8's named gaps and the
2026-08-24 amendment's three follow-ups are untouched by this amendment —
none of them are about the agent layer, and none is resolved or
invalidated by it. One correction, in this ADR's own idiom, because the
`docs/adr/` sweep exclusion means nobody else will: the `## See also` paths
above predate the P0 regroup — `console/jobs/scheduler.py`,
`console/jobs/models.py`, `console/jobs/claim.py`, `console/jobs/worker.py`
(`0013:386-389`) are now `models/queue/`. An amendment saying so is the
honest fix; a silent rewrite of a dated record is not.

## Amendment (2026-08-30) — Identity & Auth IA-1 Task 10: every job payload now carries the acting principal

**Every payload for `agent.turn`, `rag.ask`, `rag.ingest`, `vision.generate`
and `rag.reencode` carries two new keys, `actor_kind`/`actor_key`**
(`identity.contracts.principals.payload_fields`/`principal_from_payload`).
This ADR's own §1 one-door rule — `models.contracts.queue.enqueue(kind,
payload, *, priority=None)` is frozen — is exactly why "who asked" had to
land INSIDE the payload rather than as a new parameter on that door: the
payload is already the record of what was asked for, and it is the one
thing every job kind's planner, handler, and the queue page's own row all
read back.

**The new obligation, stated as new.** Recorded here, on the queue's own
side, because this ADR's own precedent (the agent-layer amendment above)
is that a load-bearing rule about what a payload carries belongs where the
payload's shape is defined, not only where it happens to be produced.
**Every enqueuing surface now has an obligation to stamp an actor** — a
`payload_fields(actor)` merge into the dict handed to `enqueue()` — and
`actor` is a REQUIRED keyword on every function that builds one of these
five payloads (`agents.chat.service.start_turn`,
`tools.rag.ingest.enqueue_ingest`/`enqueue_reingest`, and the inline
dict-builders in `tools.rag.views.AskView`, `tools.vision.views.
generate`, and `models.registry.views.role_reencode`) — never a default,
because a default would be a fail-open default: the caller that forgot it
would silently enqueue a job attributed to nobody, and every visibility
rule downstream would then be reasoning about a blank.

**Who supplies it.** A browser-facing enqueue passes `identity.request.
principal_for_request(request)` — the signed-in user, or `identity.
contracts.principals.OPEN_PRINCIPAL` on a box with no accounts, never a
blank. A shell path (`manage.py agent_turn`, the RAG folder watcher inside
`tools.rag.ingest.watch_folder`) passes THE ONE SERVICE PRINCIPAL, `identity.
contracts.principals.SERVICE_PRINCIPAL` — one constant, not one per caller,
matching this codebase's existing "who acted" vocabulary (`identity/
contracts/principals.py`'s own module docstring). A tool runner that itself
enqueues a follow-on job (`tools.rag.tools.run_ingest`, the ONE
fire-and-forget enqueue described in the agent-layer amendment above)
passes `ctx.principal` — the turn's own acting principal, since the queued
job is done on behalf of the same party the turn itself is running for.

**The queue page will filter on them.** `identity.contracts.principals.
principal_from_payload(payload)` — NEVER RAISING, by its own design — is
the function a future queue-visibility filter reads a job's actor back
through, so a payload written before this amendment (no actor keys at
all) or a hand-edited/corrupt payload reads as `OPEN_PRINCIPAL`, the
least privileged answer, rather than a 500 on a never-500 surface. This
task only stamps the actor into the payload; the queue page itself does
not read it back yet.

**What this amendment does NOT do.** It does not change what the runtime
DOES with a payload's actor — `agents.contracts.tools.ToolContext.principal`
still carries whatever `invoke_tool`'s caller decided, and a turn does not
yet run AS the payload's `actor_kind`/`actor_key`. That reversal, and the
companion field `ToolContext.agent_slug` (WHOSE TOOL DECLARATION IS IN
FORCE, split apart from `principal`, WHO THIS IS BEING DONE FOR) this same
task adds to make the reversal expressible, are Task 11's work — recorded
in full in [ADR 0015](0015-agent-layer-and-tool-contract.md)'s own
2026-08-30 amendment. This amendment only makes the fact travel; nothing
read it back yet at the time it was written, except the queue page.

**Follow-up (Task 13): the queue page now does.** `models/queue/
visibility.py` — the sanctioned rule-2 seam `models/README.md` and
`foundation/README.md` both name — reads `principal_from_payload` back
through `may_see_job_id`/`may_read_job_content`/`visible_rows`, so
`/queue/` now shows a member their own rows (and every row's operational
facts, never another member's content) while an administrator sees every
row, gated the same way every other content surface is. The "will filter"
above is no longer future tense; left as originally written, per this
ADR's own precedent for a dated record, with this paragraph saying so.

## Amendment (2026-09-17) — the one-timeout task: `JobSettings.response_timeout_seconds` and one firing authority

**The incident.** A chat/agent turn was dying well before its own 900s
turn deadline (`agents.limits.TURN_DEADLINE_SECONDS`). The real cause was
a SECOND, invisible timeout: the ollama engine adapter's own
`DEFAULT_REQUEST_TIMEOUT` (300s, `models.contracts.engines.ollama`),
injected into the chat client's config by that adapter's own
`cfg.setdefault("request_timeout", ...)` and never overridden by anything
upstream. Whichever timeout was smaller won, silently — and it fired
whenever the chat model had to reload under memory pressure from a
concurrent image-generation job, which routinely took longer than 300s.
Two competing timeouts, one of them never surfaced to an operator at all.

**The fix: one operator-editable setting, one firing authority.**
`JobSettings` (`models/queue/models.py`) gains a sixth field,
`response_timeout_seconds` (`PositiveIntegerField`, default 1800 — 30
minutes, raised from the ollama adapter's invisible 300s — bounds
60..7200 enforced in the write path, `models/queue/migrations/
0004_jobsettings_response_timeout_seconds.py`). It is now the SINGLE
number both the turn's own step-budget deadline and the chat engine's
own inner request timeout are built from, so the engine's client can
never time out before the loop's own deadline check does — the loop's
own check is the only timeout that ever fires. Set it TO the turn's
value, never merely a fallback: `models.contracts.gateway.get_llm_for`
gained an optional `request_timeout` kwarg that lands in the engine's
config BEFORE that adapter's own `setdefault` ever runs, so the turn's
value always wins.

**The seam, matching this ADR's own §1/§8 division of labour.** `agents/`
may not import `models.queue` (the queue/agent import-law boundary this
ADR's earlier amendments already work within). The value travels via
`models.contracts.jobkinds.JobContext.response_timeout_seconds` (new
field, optional, defaults to `None`) — `models.queue.worker.Worker.
_build_job_context` (the worker already legally reads queue storage)
stamps it from `JobSettings.get_solo()` at claim time, and `agents.
runtime.loop._run_turn` reads it back, falling through to `agents.
limits.TURN_DEADLINE_SECONDS` only when it is `None` (a job claimed by a
worker from before this field existed, or any other caller with no real
queue behind it, e.g. `NULL_JOB_CONTEXT`). That constant is KEPT, not
deleted, and its own comment now says so: it is the fallback, no longer
the operative value on a migrated box.

**The admin-facing half.** A turn that ends because its own deadline
expired (never one that merely ran out of steps — that ending stays the
existing honest, non-failed DONE ending, unchanged) is now marked FAILED
with one generic, admin-info-free sentence,
`agents.limits.TURN_TIMEOUT_ERROR`, declared once in Python. An
administrator viewing that turn sees an additional sentence pointing at
the Job execution settings page (`agents/chat/templates/chat/
_turn_card.html`'s FAILED branch, gated on `identity_is_admin` AND the
turn's error matching that one constant by identity — never a duplicated
template string literal); a non-admin, or a turn that failed for any
other reason, sees no hint. This is the same content-withheld pattern
`models/queue/views.py`'s own `_present_row` already uses for a job row's
withheld content.

## Amendment (2026-09-17), fix round 1 — an adversarial review's blocking and important findings

**B1 — the worker's new settings read is now failure-isolated.**
`Worker._build_job_context` (`models/queue/worker.py`) is called from
`_execute` OUTSIDE that method's own never-raise `try`/`finally` (its
own docstring's contract). The `JobSettings.get_solo()` read this task
added there is now wrapped in its own `try`/`except Exception`, logging
and falling back to `response_timeout_seconds=None` (the SAME documented
fallback an unstamped `JobContext` already has) rather than ever letting
a DB blip escape `_build_job_context` uncaught — which would otherwise
strand the claimed job RUNNING forever (its claim-token slot never
released, the orphan sweep permanently unable to reclaim it, the pool
thread's own DB connection leaked). Pinned at both the unit level
(`_build_job_context` degrades to `None`) and the integration level (a
`tick()` whose second `JobSettings.get_solo()` call raises still reaches
a terminal `SUCCEEDED` row).

**B2 — the agent-as-tool delegate path now honors the same turn timeout.**
`agents.runtime.delegate.run_agent_tool` spends from the ROOT turn's
SHARED `StepBudget` and carries the root's own `JobContext`
(`ctx.job`), but built its own chat client with NO `request_timeout` at
all — keeping the engine's hidden 300s default while spending a budget
that can now run up to 7200s, the exact competing-timeout incident this
task exists to close, surviving on the one hop that forgot to ask. One
shared resolver, `agents.runtime.loop.resolved_turn_timeout(job_ctx)`,
now answers "the turn's operative wall clock" for both callers —
`_run_turn` for the root loop, `run_agent_tool` for a delegate hop — so
the fallback rule (`job_ctx.response_timeout_seconds`, or
`TURN_DEADLINE_SECONDS` when unstamped) is declared exactly once.

**B3 — the admin hint on the LIVE POLL path, and its limit.**
`conversation.html`'s poller (HELD by PR #84) never swaps in
`_turn_card.html`'s own rendered markup for a FAILED turn at all —
`showCardError` rebuilds the card from `data.error`/`data.setup_url`
alone and reads no `html` key for that state, unlike the "done"/
"queued"/"running" bodies. Verified against the installed template
before concluding this, not assumed. Delivering the FULL rendered card
(link included) on the poll path would require editing that held file,
which this task does not do.

Instead, `agents.chat.views.turns._failed_body` (not held) now appends
`agents.limits.TURN_TIMEOUT_ADMIN_HINT` — the SAME hint sentence,
as plain text, no markup — directly onto `data.error` for an admin
polling a turn that failed on `TURN_TIMEOUT_ERROR`. The poller already
renders `data.error` verbatim via `textContent`, so this reaches a
watching admin without any reload and without touching the held file;
the one thing it cannot do that a full page load can is render a real,
clickable link to the settings page. A non-admin, or a FAILED turn with
any other error, gets `turn.error` unchanged either way.

**Two named residuals, both reload-only, both for the identical held-file
reason** (final review MINOR 4 named the second explicitly; it was
previously true but unstated): the admin hint's own LINK, above, is one;
I1's preserved partial composed text (`card.text`/`card.text_html`,
rendered above the error line in `_turn_card.html`'s FAILED branch) is
the other — `_failed_body` returns no `html` key at all, and
`conversation.html`'s `showCardError` *wipes* the card before writing
the error line, so a reader watching a timed-out turn live sees neither
the partial text nor the linked hint until they reload. Closing either
fully is the same one-line JS change in `conversation.html`, once that
file is no longer held.

**I1 — a timed-out turn's partial content is preserved, not discarded.**
`_finish` (`agents/runtime/loop.py`) already wrote a timed-out turn's
composed text (`_OUT_OF_TIME`, plus the last tool's real result when one
ran) into `Turn.text` — but `_turn_card.html`'s FAILED branch rendered
only `card.error`, so that content was written and never shown. The
FAILED branch now renders `card.text`/`card.text_html` ABOVE the error
line when present — a no-op for every OTHER FAILED reason (a crash, a
stalled-worker writeback), since none of those ever populate `Turn.text`
at all. History replay is UNCHANGED and stays DONE-only
(`agents.runtime.prompt._REPLAYABLE_STATES = ("done",)`) — a timed-out
turn's partial answer is shown to a human reader but still never fed
back into a later turn's own prompt, a deliberate simplicity choice
rather than an oversight. That module's own comment claimed a FAILED
turn "never produced content", which this change makes false for the
timed-out case — **this fix round's own report and this paragraph both
originally claimed that comment was corrected here; it was not.** See
the fix-round-2 amendment below for the actual correction.

**Correction (fix round 3): "History replay is UNCHANGED... still never
fed back" is also incomplete, in a way the final whole-branch review
caught.** The RULE (`_REPLAYABLE_STATES = ("done",)`) is unchanged — but
the BEHAVIOUR for a timed-out turn specifically is not: before this
task, a turn that ran past its deadline still ended DONE, and a DONE
turn at root depth WAS fed back into a later turn's own prompt (via this
same `_REPLAYABLE_STATES` filter), into `agents.workstreams.
transcript_for` (the workstream transcript tool), and into `agents.
visibility`'s own consolidation index (`last_root_done_index`, all three
filtering on `state=Turn.State.DONE, depth=0`). Moving a timed-out turn
to FAILED silently drops it out of all three — an intended trade-off
this task makes (a timed-out turn's partial text is worth SHOWING a
human reader on the card, but is no longer treated as a completed
contribution the platform reasons about elsewhere), not a side effect
"unchanged" ever accurately described. See the fix-round-3 amendment
below for the full statement.

**I2 — the two on-timeout sentences are named as a pair, not left as
silent near-duplicates.** `_OUT_OF_TIME` (`Turn.text`, the loop's own
composed ending) and `TURN_TIMEOUT_ERROR` (`Turn.error`, the stored
failure sentence the admin hint keys off) are now declared adjacently
with a comment stating the split explicitly, and I1's fix means neither
is dead copy any more — both are visible to a reader, in two different
places on the same card.

**I3 — the client-side 10-minute poll cap no longer fires before the
real timeout can.** `agents.chat.service.MAX_POLL_DURATION_MS` — a
DIFFERENT axis from the turn's own deadline (a client-side "give up and
say reload" safety net; it never ends the turn) — was 10 minutes, while
`response_timeout_seconds` can now run up to 7200s (two hours). Raised
to 7500s, bound in a comment to `JobSettings.RESPONSE_TIMEOUT_SECONDS_
MAX` by name (never by import — `agents/` may not read `models.queue`).
**Known residual drift, left as-is because the constraint leaves no
other choice**: `conversation.html`'s own poller (held by PR #84)
hardcodes the WORDS "Still working after 10 minutes" next to this
constant's old value; raising the constant's value without being able
to touch that copy means the visible sentence now understates how long
the poller actually keeps watching. This is a smaller, honest cost of
closing the larger gap (the poller no longer gives up while a
legitimately long turn is still running) and is flagged here rather than
left to be discovered.

**I4 — the settings-help card's scope claim is now accurate.** "Every
queued job on this box obeys these six values" overstated
`response_timeout_seconds`'s reach: only `agents.runtime.loop` reads the
stamped value, so a `rag.ingest`/`rag.ask`/`vision.generate` job ignores
it entirely. The card's `purpose` prose now says so plainly: five of the
six govern every queued job; the response timeout governs one agent or
chat response.

## Amendment (2026-09-17), fix round 2 — the I1 comment fix that round 1 claimed but never made

A second review found I1 only PARTIAL: `agents/runtime/prompt.py:125-127`'s comment ("A
FAILED or CANCELLED turn never produced content; replaying it would feed the model an empty
assistant message and invite it to continue from nothing") was never actually edited in fix
round 1, even though that round's own report and the paragraph above both claimed it was.
`agents/runtime/prompt.py` was not among the files fix round 1 touched.

Corrected now. The comment above `_REPLAYABLE_STATES = ("done",)` states the true, current
reasoning: DONE is the only state ever replayed; a CANCELLED turn or a genuinely crashed/
stalled FAILED turn never produced content worth replaying, and a timed-out FAILED turn (which
CAN carry real, honestly-composed partial content since the one-timeout task) is deliberately
grouped with them rather than carved out as a third case — replaying a truncated mid-turn
answer would invite the model to continue from an ending it never reached. `_turn_card.html`'s
FAILED branch still SHOWS that partial content to a human reader (I1's actual behavior fix,
made correctly in fix round 1); it simply never feeds back into the next turn's own prompt.
Replay behavior itself is unchanged by this amendment — only the comment explaining it, and
the paragraph above, which no longer claims a fix that did not happen.

## Amendment (2026-09-17), fix round 3 — the final whole-branch review (0 blocking, 5 important, 8 minor)

**I-1 — the sibling-timeout claim `agents/limits.py` carried was false, and vision's own
in-turn wait was never surveyed.** The comment above `TURN_DEADLINE_SECONDS` claimed this
constant (and even `RESPONSE_TIMEOUT_SECONDS_MAX`, 7200s) was "deliberately LARGER than
vision's own `GENERATE_WAIT_TIMEOUT_SECONDS` (600.0)... so exactly one image generation fits
inside a turn". `GENERATE_WAIT_TIMEOUT_SECONDS` (`tools/vision/jobs.py`) is `4 * 3600.0` —
14400s — and has been since before this task; the claim was already false the moment that
constant was raised from 600s and was copied forward here unchecked. Corrected: neither this
constant nor the new ceiling is larger than vision's own wait, and that size relationship was
never what made an in-turn generation safe anyway — `tools.vision.tools`'s own generate runner
already clamps its wait to whatever remains of the TURN's budget
(`remaining = ctx.budget.deadline_monotonic - time.monotonic(); timeout = max(1.0,
min(GENERATE_WAIT_TIMEOUT_SECONDS, remaining))`), the SAME "derive from what's left, never a
fixed ceiling" pattern this task's own fix rounds extend to the RAG tool path below (I-3).
`GENERATE_WAIT_TIMEOUT_SECONDS` itself is a different axis entirely — how long ONE detached,
page-submitted generation job may run when nothing is waiting on it — never a sibling of the
turn's own deadline, and never compared against it for correctness. This closes the "any other
timeout on the same axis, unsurveyed" gap the same review raised as review dimension 1.

**I-2 — a hang inside one `llm.chat` call could still end a turn without the shared
sentence.** `budget.expired` is checked only BETWEEN steps everywhere else in `run_loop`
(top of iteration, before each tool call); a call already IN FLIGHT when the deadline passes
is the one place that check cannot run before the call itself raises. Since the engine's own
inner client timeout is now the FULL turn value (`models.contracts.gateway.get_llm_for`), a
model that hangs on a single call — the incident's own scenario, a chat-model reload under
memory pressure — raises from inside `llm.chat` right around when the deadline itself passes.
Before this fix, that exception reached `run_turn`'s own `except`, which wrote `state=FAILED,
error=str(exc)` — the raw, often-empty exception text, not `TURN_TIMEOUT_ERROR`, so neither the
admin hint nor the preserved partial text applied to this one path even though it is the
incident's OWN scenario. Fixed by wrapping the `llm.chat` call itself, inside `run_loop`:
catching any exception there and checking `budget.expired` (never the exception's TYPE — this
platform's gateway seam does not let `agents/runtime` know an engine's own exception
hierarchy). If the deadline had already passed, the ending IS composed exactly like the
loop's own `budget.expired` breaks — `timed_out=True`, `_OUT_OF_TIME` text (with the last
tool's own result preserved, when one ran), `TURN_TIMEOUT_ERROR` in `Turn.error` via `_finish`
— the SAME machinery, not a parallel copy. An exception raised BEFORE the deadline (a genuine
bug, an engine truly unreachable) is re-raised unchanged, reaching `run_turn`'s own `except`
exactly as it always has.

**I-3 — two in-turn clients still kept the engine's 300s default.** `tools/rag/retrieval.py`'s
`answer_question` built its answer LLM with no `request_timeout`, and `retrieve_nodes` built
its embedder the same way — both reached synchronously, in-turn, by the `rag.ask`/`rag.search`
tool runners (`tools/rag/tools.py`), the exact defect fix round 1's B2 closed on the delegate
hop, surviving on the RAG tool path: with the operator's turn timeout at 1800s, a model reload
under memory pressure could still kill an in-turn RAG tool call at 300s. `answer_question`
and `retrieve_nodes` both gained an optional, keyword-only `request_timeout`, threaded to
`gateway.get_llm_for`/the new `gateway.get_embed_model_for(..., request_timeout=...)` (the
embeddings sibling of `get_llm_for`'s own kwarg — `OllamaEmbedding` has no bare
`request_timeout` field; its adapter reads a nested `client_kwargs={"timeout": ...}` instead,
so this lands there, before the adapter's own `setdefault`). `tools/rag/tools.py`'s `run_search`/
`run_ask` — the IN-TURN callers — compute `max(1.0, ctx.budget.deadline_monotonic -
time.monotonic())` and pass it through, the identical "derive from what's left" pattern I-1
names. Every OTHER caller (the page-submitted search page, the standalone `rag.ask` job, the
`ask` CLI command) passes nothing and is unaffected — `None` changes nothing. `tools/rag` was
not part of this task's original zone grant; only these two call sites, on the in-turn path
only, were touched.

**I-4 — a real, undisclosed history change: a timed-out turn dropped out of the
conversation.** See the correction appended to the I1 paragraph above: moving a timed-out turn
DONE → FAILED removes it from `agents.runtime.prompt`'s replay filter, `agents.workstreams.
transcript_for`, and `agents.visibility`'s consolidation index — all three read `state=Turn.
State.DONE, depth=0`. Before this task, a timed-out turn was DONE and fed all three; after,
it does not. Stated here as the intended trade-off it is: a timed-out turn's partial content
is worth showing a human reader on its own card (I1), but is no longer treated as a completed
contribution the model, the transcript tool, or consolidation reason about elsewhere. No code
change from this finding — the behaviour is exactly what fix round 1 shipped; only the
record was incomplete.

**I-5 — the poll-cap/settings-max pairing is now guarded by a test, not only a comment.**
`agents.chat.service.MAX_POLL_DURATION_MS` (7500s) must stay above `JobSettings.
RESPONSE_TIMEOUT_SECONDS_MAX` (7200s) × 1000, or the poller re-arms fix round 1's own I3 defect
the moment either number moves. A three-line cross-module test (a TEST module may import both
sides; the production modules still may not) now pins
`MAX_POLL_DURATION_MS > RESPONSE_TIMEOUT_SECONDS_MAX * 1000` directly, beside the existing
poller-constant test.

**MINORs addressed**: a stray displaced code comment reordered in `agents/runtime/loop.py`; a
lingering `agents.runtime.loop.TURN_TIMEOUT_ERROR` reference in `_turn_card.html` repointed to
`agents.limits`; the "Job execution" page's own tagline narrowed to match `foundation.
settings_help`'s already-corrected scope claim (I-4's own overclaim, carried in a second
place); the preserved-partial-text residual named explicitly alongside the admin-hint residual
in the B3 paragraph (both are reload-only, for the identical held-file reason); the CLI
(`manage.py agent_turn`) now checks `turn.state` before printing success for a timed-out turn;
`models.contracts.engines.base`'s own `build_llm`/`build_embedder` contract docstrings now name
`request_timeout`/`client_kwargs` as part of the seam every adapter should expect; and the
operator-facing "end to end" copy (`foundation/settings_help.py`, `docs/OPERATIONS.md`) gained a
sentence naming the worst-case ~2× latency `agents/runtime/loop.py`'s own docstring on
post-deadline latency already discloses, so the claim matches what a caller sees.

**MINOR 7, and a real regression it caused, caught by the gate before this round shipped.**
A `0`/negative `response_timeout_seconds` is unreachable through the settings UI (the write
path refuses it), but reachable through a fixture, a hand edit, or a future migration
default. The FIRST version of this fix floored `resolved_turn_timeout`'s OWN return value at
`agents.limits.RESOLVED_TIMEOUT_FLOOR_SECONDS` — which broke this suite's own established
deadline-simulation technique (`monkeypatch.setattr(..."TURN_DEADLINE_SECONDS", -1.0)`,
`make_job_ctx(response_timeout_seconds=-1.0)`, used throughout `agents/runtime/tests/
test_loop.py` to make a `StepBudget` already-expired for a fast unit test): the floor turned
every one of those negative sentinels into a POSITIVE near-term deadline, and five previously-
green tests started failing (an already-expired budget silently became "an hour from now",
long enough for the scripted `FakeToolLLM` to answer normally before the deadline check ever
saw it as expired). Caught by running the `agents` gate BEFORE reporting this round done, not
assumed safe from the diff alone.

The actual fix floors `request_timeout` ONLY at the two call sites that hand it to
`gateway.get_llm_for` — `_run_turn` and `agents.runtime.delegate.run_agent_tool` — never
inside `resolved_turn_timeout` itself and never on the `StepBudget`'s own deadline. This is
also the more honest design, not merely the test-compatible one: an operator's row genuinely
stored as `0` should make the turn end immediately via the existing, graceful `budget.expired`
branch (which is what a floorless deadline already does correctly) — the real hazard MINOR 7
named is specifically a REAL httpx client built with `request_timeout=0`, which only ever
happens at the two `get_llm_for` call sites, both of which now floor there and nowhere else.

## Amendment (2026-09-21) — Queue memory governance: a four-rung footprint ladder, the eviction rewrite, affinity ordering, the worker lifecycle, and the kind registry

A live incident set this track going: the machine ran out of memory while the
queue believed it had the room, and the queue's own account of what was
resident — and of what an unload had actually freed — turned out to be a
belief nothing checked. What follows changes §3, §4, §5, §7 and §8 of this
ADR. Nothing above is rewritten; every paragraph this amendment supersedes is
named where it is superseded.

### 1. §4's memory accounting: four rungs, their own label vocabulary, and a recorder that keeps the maximum

**The footprint ladder now has four rungs, walked on `is not None`.**
`ModelConnection.effective_footprint_bytes` (`models/registry/models.py`)
resolves `footprint_override_bytes` (the operator's word, always final), then
`measured_footprint_bytes` (what this queue observed after a run it executed),
then the NEW `engine_reported_footprint_bytes` (what the engine said a loaded
copy occupied), then `None` — "unknown", which §4's "unknown or oversize runs
alone" rule already turns into an exclusive admission. The walk tests
`is not None`, never truthiness: a genuine zero is a value, and reading it as
unknown would silently make a zero-cost model exclusive.

Rung 3 exists because those numbers were already on the wire and thrown away.
`Worker._residency_snapshot` reads each engine's `list_installed` on every
eviction pass; the `loaded_size` a loaded model carries is now harvested
there, through `models.registry.bindings.record_engine_reported_footprint` —
**never a call made for this purpose**. Only a POSITIVE reading is written; a
missing or zero size writes nothing, because a stored zero reads back as a
real "this model is free" answer. It is a SEPARATE column from rung 2, not a
fold into it: the two are facts of different quality, the recorder compares a
reading only against its own standing value, and one column answering for both
would make the console's label untrue again.

**Its own label vocabulary, deliberately separate.** `footprint_source` now
answers `"override"` / `"measured"` / `"engine_reported"` / `None`, keyed to
`models.registry.views._FOOTPRINT_SOURCE_LABELS` — "set by the operator",
"measured after a run", "from the engine's residency snapshot", and "not
measured yet — this model runs alone". It is NOT keyed to `_SOURCE_LABELS`,
which stays the map for `DiscoveryRow.capability_source`: "detected from the
model server" is true of a capability probe and a lie about a post-run delta
measurement. The label is resolved in the view (`_footprint_source_label`),
not in the template, so the rendered label and the rendered value can no
longer be two independent decisions about one row; the date is localized
before formatting, because `date_format` alone does not and the difference is
a visibly wrong day on a non-UTC box.

**The recorder keeps the maximum, with no tolerance band.** Both recorders now
go through one writer, `models.registry.bindings._record_footprint`. No
standing value: write. A reading greater than or equal to the standing value:
write — a footprint going up is always believed, because under-counting is the
direction that crashes hosts. A reading BELOW it: refused entirely, neither
the bytes nor the timestamp, with one line naming the connection, the standing
value and the refused reading.

There is no tolerance band, and that is the whole reasoning rather than a
simplification. A percentage tolerance measured against the standing value
ratchets geometrically: five successive "within tolerance" writes at 0.75×
walk 26.4 GB down to 6.2 GB, which is the exact shape of the incident the rule
exists to refuse — and the condition is RECURRING (a model that under-measures
whenever it is warm), so repeated warm runs are the normal path, not an
adversarial one. Keeping the maximum makes the standing value a high-water
mark by construction, with no extra column and no constant to tune.
`FOOTPRINT_DIP_WARNING_RATIO` (0.75) is a LOG-LEVEL threshold only — a refusal
under three quarters of the standing value is an operator-actionable WARNING
naming the remedy (set `footprint_override_bytes`), a smaller dip is INFO —
and no reading below the standing value is ever written at any ratio. The
accepted cost is named: a genuinely shrunk model stays high until an operator
sets an override, which is the safe direction and is already the documented
correction path.

### 2. §4's eviction, rewritten: what an unload frees, what residency is worth, and what must never be taken

**§4's closing "Eviction reconciles residency to the plan" paragraph is
superseded by this section.** The guiding principle is unchanged — admission
plans against declared footprints; eviction makes the machine match the plan —
and everything else in that paragraph is now narrower, wider, or both.

**Two engine declarations, and the queue must know both.**
`models.contracts.engines.base.InferenceEngine` gains two OPTIONAL
declarations, read the same defensive `getattr` way the existing
`list_installed`/`unload` seams are, never called and never required:

- `unload_scope` — `"model"` means one `unload(endpoint, model_id)` call
  releases that model and leaves the others; `"endpoint"` means the call
  releases everything there and `model_id` is *addressing, not selection*.
- `residency_authority` — `"endpoint"` means the `loaded` flags come from a
  real residency endpoint the engine answers live, so "nothing resident" is a
  FACT; `"memo"` means they come from a TTL'd, process-local belief the
  adapter keeps, so "nothing resident" means "this process does not remember
  anything", which a restart alone produces.

**Absent or unrecognised reads as the SAFE member of each pair** — `"endpoint"`
for `unload_scope`, `"memo"` for `residency_authority` (`Worker._unload_scope`,
`Worker._residency_authority`, and `_UNLOAD_SCOPES`/`_RESIDENCY_AUTHORITIES`
whose last element is the safe one). The directions are not symmetric, which
is why the defaults are not: assuming an unload frees the whole endpoint costs
at worst a needless reload, while wrongly assuming per-model granularity
destroys a live cold load measured in minutes on this hardware; and treating
an empty residency answer as forgetfulness costs one precautionary call, while
trusting it as fact launches an exclusive job on top of memory nobody released.

Both shipped adapters take their lines. The **image engine** declares
`unload_scope = "endpoint"` (its free call frees every model there) and
`residency_authority = "memo"` (its `loaded` flags come from its own run memo).
The **text engine** declares `unload_scope = "model"` (its unload names one
model and releases that model alone) and `residency_authority = "endpoint"`
(its `loaded` flags come from a live residency call against the engine). The
second declaration is what spares an idle text engine a precautionary unload on
every exclusive admission.

**The protected-key rule, including the admitted batch.**
`Worker._protected_keys` is the one safety set: every model key held by a
RUNNING job, UNION every key held by an attempt still in flight in this
process (`_inflight_refs`). The first half covers this tick's own admitted
batch for free — `claim_and_admit` has already persisted the admissions as
`running` before eviction runs — and that matters concretely, because an agent
turn is planned exclusive, so every chat message runs this pass and an
unprotected definition would unload that turn's own warm model and cold-load it
again on every single message. The second half covers the window this ADR's
2026-08-25 amendment named as its third follow-up: an attempt the orphan sweep
requeued while its handler is genuinely still cold-loading has a row back at
`queued` and is invisible to the RUNNING query for exactly as long as that load
takes.

The rule is applied PER SCOPE. At `"model"` scope, protected keys are skipped
one by one and everything else at the endpoint is unloaded individually. At
`"endpoint"` scope, one call frees everything, so the WHOLE endpoint is skipped
if any protected key lives at it — **with one sanctioned exception, and its
boundary is the point**: the endpoint is still freed when every protected key
there belongs to the admitted exclusive job's OWN keys at its OWN endpoint.
Freeing that endpoint unavoidably takes its own model with it, there is no
per-model call to make instead, and the job pays at worst one reload. The
`own_keys` set is EMPTY for every other caller, which is what keeps the
exception to the one case it is written for; it is never applied to another
job's key or to a live attempt's.

**The fifth ordering rule: protection is checked before any HTTP.**
`_evict_to_match_plan` already had four ordering rules; the fifth is that
`_protection_refusal` runs BEFORE `_residency_snapshot`. The reasoning is cost,
not taste: the protected set comes from database rows and this process's own
maps and needs no network at all, while evaluating it after the snapshot would
pay a full cross-engine probe on every 0.5 s tick for the entire life of the
protecting attempt — a cold load measured in minutes. A tick refused for
protection has therefore made **no HTTP call at all**.

**The barrier, redesigned, and the ambiguity of `False`.** An admitted exclusive
job is not launched until the memory it was promised is actually free. Its
scope is the WHOLE swept set, not the job's own endpoints. It has four parts:
the protection refusal above; the real unload calls the exclusive pass makes
against believed-resident models; a PRECAUTIONARY call at any endpoint where
the belief is worth nothing; and then reading what those calls said.

`False` from an unload is ambiguous, and the whole design turns on telling its
two readings apart. A call made against a model the snapshot says is RESIDENT
is INFORMATIVE: `False` means what it says, and the barrier honours it. A
PRECAUTIONARY call — made precisely because the belief is worth nothing — gets
no such weight: the adapter cannot tell "nothing freed" from "nothing to
free", and a cold, empty endpoint is the common case after a restart, so a rule
that refused there would make an exclusive job unlaunchable not for one tick but
for ever. A precautionary `False` is logged at INFO and the job launches on that
same tick.

**The precautionary call is narrowed, and the narrowing is load-bearing.** It
is issued only where the belief is worth nothing — the snapshot has no entry
for that endpoint (unavailable, or the call raised), or the entry reports
nothing loaded AND the engine declares (or defaults to)
`residency_authority="memo"`. An engine declaring `"endpoint"` that reports
nothing gets NO call: it actually knows nothing is resident, so there is
nothing to barrier and nothing its answer could add. Without that narrowing an
endpoint-scope adapter runs its settle poll to the full timeout because nothing
rises, on every exclusive admission — i.e. on every chat turn. A per-turn
regression of tens of seconds is the symptom that the narrowing has stopped
working, and is to be caught in a timed smoke check rather than in use.

**The refusal is bounded in count AND in wall clock, with a durable hold-off.**
A refused job is handed back with `not_before = now + BARRIER_HOLDOFF_SECONDS`
(45 s, comfortably longer than one unload timeout) so it is not re-claimed on
every 0.5 s tick against an engine that is still holding memory; its `attempts`
is deliberately untouched, because it never ran. A job is FAILED only once
**both** `MAX_BARRIER_REFUSALS` (3) and `MIN_BARRIER_REFUSAL_SPAN_SECONDS`
(300 s) are satisfied — counting refusals alone is a trap, because an
informative `False` is exactly what a BUSY engine returns, so three of them
could elapse in barely more than the time three unload calls take and "three
attempts" would mean "a second and a half". A successful barrier RESETS the
count: an engine that released its memory this time has not been failing for
three spaced attempts. The failure is honest and names what an operator can
act on — `engine '<name>' at <endpoint> did not release memory for this
exclusive job after 3 attempts over N minutes; it was not retried again`.

**A PROTECTION refusal is never counted.** That wait is bounded by the live
attempt's own end, and since an agent turn is planned exclusive, counting it
would fail three consecutive chat turns for an ordinary long-running foreign
job at a shared endpoint — a job the queue was correctly waiting for.

**The sweep now sees the whole machine, on the ticks that are entitled to it.**
`_eviction_targets` starts from the RUNNING jobs' own endpoints and, **only on
a tick that admits an EXCLUSIVE job**, unions in
`models.registry.bindings.registered_endpoints()` — the one notion of "every
engine endpoint this box knows about", shared with the console's own per-engine
discovery map so a freshly configured engine cannot be visible to one and
invisible to the other. That endpoint set is the union of the configured
defaults and every connection row's endpoint, per engine; deriving it from
connection rows alone would miss a second engine running at its configured
address with no registered connection yet, which is exactly the case this
widening exists for. A model left warm on an IDLE engine was never visited
before; on a unified-memory box that model is the one that crashes the host.

The bound is the cost control: only the admission entitled to the whole machine
pays for the whole machine to be probed, and every other tick keeps exactly the
prior reach. **The stated consequence for the budget arithmetic**: `over_budget`
counts more resident bytes on those ticks than on others, so the number is not
comparable tick to tick. Deliberate — under-counting resident memory is the
direction that crashes hosts.

**The budget gate MOVED, and this is not a tidy-up.** Previously an unset
`memory_budget_bytes` returned early from the whole function, which made every
mechanism below it dead code exactly where it was needed — and "no budget set"
is the posture the field actually ran in. Now the endpoint targeting, the
protected set, the residency snapshot, the exclusive pass, the barrier and all
the logging run **unconditionally**; only the capped, budget-driven pass stays
gated, because it is the one mechanism whose decision is arithmetic against a
number that may not exist.

**The log vocabulary, fixed.** A successful eviction used to be silent and a
`False` was the only thing the pass ever logged — precisely the asymmetry that
left an operator watching a host fill up unable to tell "nothing needed
evicting" from "everything was skipped". Now:

- ONE INFO line per unload attempt: who (engine, endpoint, model), at what
  granularity (`model` / `endpoint`), WHY (`not needed at an exclusive
  endpoint` / `over budget` / `precautionary barrier`), and the RESULT
  (`accepted` / `refused`).
- ONE INFO line per tick that evicts at all, naming the trigger(s), how many
  endpoints were swept, the resident bytes, the admitted marginal bytes, and
  the budget — rendered as `unset` rather than `None`, because that is a
  posture, not a missing value.
- INFO for a skipped endpoint whose `list_installed` raised, and INFO once per
  engine+method for an engine offering no `list_installed`/`unload` at all
  (which degrades to the prior idle-timeout-only behaviour for it).
- **WARNING is reserved for what an operator can act on**: a whole endpoint
  skipped because live work is protected there, an exclusive job not launched
  this tick, a job failed after the refusal bounds, and the registry's own
  refused-lowering line.

**The unset budget is now loud rather than merely true.** The Queue page says
what the posture costs ("Jobs run one at a time, and nothing is offloaded for
budget reasons") and points an administrator at the settings page. The worker
process — never the console — writes what ITS machine reports as total memory
onto the settings row once at boot (`JobSettings.detected_memory_bytes`/
`detected_memory_at`), rendered as a labelled prefill naming the process that
measured it and the date. **Nothing is ever applied on the operator's behalf**:
the console renders in the web service and the budget governs the worker
service — separate containers — and a container sees the VM's allocation rather
than the host's, so a silently derived budget would be authoritative and wrong.

### 3. §3's ordering: model-affinity batching within a priority, and `not_before` as the one admission-order change

**Rule 10, affinity batching with an aging bound.** `models/queue/scheduler.py`
gains `affinity_order`, a pure function ordering candidates by
`(priority, not pinned, not affine, job_id)`. Among candidates AT THE SAME
PRIORITY NUMBER, one whose model keys are ALL already resident sorts ahead of
one that would have to load something — the owner's own requirement that like
work for one model runs together even when it was submitted at different times.
"Affine" means "would load NOTHING", not "would load less"; a candidate
declaring no models is never affine, since the subset test would be vacuously
true and such a job is already effectively exclusive under §4.

`resident_keys` is a plain frozen set the CALLER hands in — the worker's last
residency snapshot with that pass's own unloads subtracted — reached through a
new `claim_and_admit(resident_keys=...)` keyword. It is neither a live
`list_installed` (that would put engine HTTP inside the claim's advisory-lock
transaction and stall every other admitter) nor this module's own fold over
running jobs (that would make affinity a no-op in sequential mode, exactly the
posture where batching matters most). It is therefore deliberately STALE — in
sequential mode it can be minutes old, and a fresh worker has none at all,
which falls back to plain `(priority, id)` order. That is precisely why it
drives an ordering PREFERENCE and nothing else: a wrong guess costs one
suboptimal ordering decision, never a wrong admission and never a wrong
eviction. `affinity_order` is the seam a different policy would replace; the
claim code and `plan_admissions` both walk the same helper, so the order
reasoned about and the order walked can never drift.

**The aging bound counts ROUNDS, not peers.** `InferenceJob.passed_over` is a
durable column (a worker restart must not reset a job's age), incremented by
one bulk `UPDATE` inside the claim's existing transaction, once per round in
which a later-by-`(priority, id)` peer was admitted ahead of the candidate —
**one increment however many peers went past it**. At
`scheduler.MAX_PASSOVERS` (3) the candidate is PINNED.

**What "pinned" does, stated precisely, because the shipped key term is weaker
than the phrase this design was first written with.** Pinning sets the second
element of the sort key, so **a pinned candidate sorts by id ahead of every
UNPINNED peer at its priority and is never reordered behind one again**. It is
NOT true that a pinned candidate is never reordered behind *any* peer: the
`not affine` term still discriminates *among pinned candidates*, so a pinned
non-affine job can still sort behind a pinned affine one. Within one priority
number a job can therefore be passed over at most `MAX_PASSOVERS` rounds by
unpinned peers, which is the bound that matters, while the affinity preference
survives among the aged. **This is an owner-visible decision point, recorded
rather than smoothed over**: dropping the affine term for pinned candidates
(making pinning a strict `(priority, id)` tail) is a one-line change to
`affinity_order`'s key and would trade the last of the batching benefit for a
simpler promise. It is not made here.

**Priority is never crossed, and neither proof moves.** §3's no-backfill
deadlock proof is untouched: the walk still stops at the first candidate it
cannot admit, and whichever candidate heads a round is still admitted alone the
instant the machine is idle. Across priority numbers nothing changed, so §3's
honestly-scoped starvation caveat — a sustained inflow at a lower priority
number can still starve an older, higher-number job — is neither improved nor
worsened, and is restated here unchanged.

**`not_before` is the one admission-order change this track makes.**
`InferenceJob.not_before` is a durable timestamp written by the worker when an
exclusive launch is refused; the claim's candidate query gains a single extra
`WHERE` on the SAME `SELECT` (`not_before IS NULL OR not_before <= now`), not a
second statement. It is durable rather than in-process deliberately: the
restart that would clear an in-memory hold-off is the same restart that clears
the barrier's refusal count, and the two together would drop a freshly
restarted worker straight back into 0.5 s-tick churn against an endpoint that
is still holding memory.

**Why the deadlock proof survives it**, in one sentence, because a new
admission-side filter is exactly the kind of change that quietly breaks it: the
exclusion is TIME-BOUNDED and SELF-CLEARING — the job returns to its own head
position the moment the hold-off expires, and an effectively-exclusive head is
still admitted alone the instant the machine is idle — so nothing can wait
behind it for ever. The hold-off renders on the Queue page's waiting row
("waiting for engine memory — retries at HH:MM") and only while it is still in
the future; a past `not_before` is a hold-off that has expired, and rendering
it would read as a delay still in force.

### 4. §7's worker lifecycle: a heartbeat thread, kind-aware staleness, sleep detection, the duplicate-submit refusal, and boot tolerance

**The heartbeat has its own thread.** §7's account of the heartbeat as "one
fact an orphan sweep trusts" is unchanged; what changed is who writes it. It
used to be written from the tick thread alone, so a tick that BLOCKS — a
synchronous eviction pass, a slow engine call — starved the very signal that
says this worker is alive, and the sweep then reclaimed jobs the worker still
legitimately held. This ADR's 2026-08-25 amendment named that as its first
follow-up; `Worker._heartbeat_forever` is it. It is a daemon thread, it waits
`HEARTBEAT_SECONDS / 2` between iterations (no floor — a floor makes the thread
unprovable in a test), and it calls `close_old_connections()` at the top of
every iteration for the same reason the tick loop and each job thread do:
Django's connection model is per-thread, so a third long-lived thread otherwise
holds a third connection open indefinitely, past `CONN_MAX_AGE` and straight
through a database restart. It survives a transient write error rather than
dying quietly, and it is not started for a single-tick run.

**Staleness belongs to the kind.** `JobKind` gains a code-declared
`stale_after_seconds`; `claim._sweep_orphans` resolves a whole kind→threshold
map and sweeps with ONE query grouped by DISTINCT threshold, never one query
per kind. A kind the registry does not know (deregistered since the row was
enqueued) falls back to the worker's global cutoff, so no row is left running
for ever merely because nothing declares a number for it. This discharges the
other half of the 2026-08-25 follow-up 1: a 120 s global cutoff could never be
right for both a sub-second embed and a job that cold-loads a large model for
minutes.

**`claim_and_admit` gains two keyword arguments**, both defaulted so every
existing call site is unchanged: `stale_after_seconds` is now the *default*
threshold rather than the only one, `sweep_orphans=True` lets the caller skip
the sweep for one round, and `resident_keys=frozenset()` carries rule 10's
ordering snapshot.

**Sleep detection.** A tick whose WALL-CLOCK delta exceeds its MONOTONIC delta
by more than `SLEEP_DETECT_SECONDS` (60) did not take that long — the host (or
the container VM) was suspended, since a monotonic clock does not advance
across a sleep and wall clock does. On wake every running row looks stale at
once, and a sweep at that instant mass-orphans healthy work. The worker writes
an immediate heartbeat and passes `sweep_orphans=False` for
`SLEEP_GRACE_SECONDS` (30), long enough for every live worker's rows to
re-stamp themselves and short enough that a genuinely dead job is still
reclaimed promptly. It is a heuristic, and the cost of a false positive is
exactly one skipped orphan sweep — deliberately the cheap direction.

**The duplicate-submit refusal restores the row to the live attempt.** This is
the unbuilt half of the 2026-08-25 reclaim fix, which repaired the bookkeeping
but never stopped the second execution: a job this process is already executing
can be orphaned (its own cold load starved the heartbeat), re-admitted, and
launched a SECOND time in the same process — two handlers, one job, one set of
models. `_launch` now refuses to submit a job id that still has a live
(not-done) future. The naive requeue is not what it does, because a requeue
would leave the row `queued` with no token tracked, strip the live attempt of
heartbeat protection, invite re-admission 0.5 s later for the whole length of
the cold load this exists for, and finally discard the live attempt's own
token-conditional writeback so the job ran a third time. Instead a conditional
`UPDATE` puts the row back to `running` under the LIVE attempt's own claim
token, claimed by this worker, freshly heartbeaten; that token is re-registered
in `_active_tokens`, and the superseding claim is discarded with one WARNING
line.

**And when that conditional `UPDATE` matches zero rows, the fresh claim is
simply DISCARDED — it is not requeued.** The design text said "requeued the
ordinary way"; the shipped code cannot do that and should not. The restore
filters on the fresh claim's own token; the requeue fallback beneath it filters
on that same token AND `state=running`, a strictly stronger predicate — so any
row the restore failed to match, the requeue cannot match either. The fallback
is dead by construction, the fresh claim's only effect is releasing its own
in-memory token, and **a row another worker legitimately owns is never touched
by this one**, which is the correct outcome. Recorded as a correction to the
design note rather than left as a discrepancy a reader would have to find.

**Boot tolerance at both settings reads.** A worker starting against a database
whose `migrate` is still running used to traceback. The constructor now waits
`BOOT_SCHEMA_WAIT_ATTEMPTS` × `BOOT_SCHEMA_WAIT_SECONDS` for the row and then
sizes its pool from the documented default rather than blocking a compose boot;
the tick loop tolerates the same failure independently, at its own read. Both
sites catch both exception classes (`ProgrammingError`, `OperationalError`) and
emit ONE waiting line, no traceback. A cold boot is quiet.

### 5. §5's kind registry: two new declarations, an operator-editable ceiling, and the rule that comes with it

`models.contracts.jobkinds.JobKind` gains two optional, code-declared,
trailing-and-defaulted fields, so every existing registration is unchanged:

- **`stale_after_seconds`** — §4 above; read by the orphan sweep alone.
- **`default_wait_seconds`** — how long this kind's handler may wait on its
  ENGINE before giving up, or `None` for "this kind does not wait".

**The operator edits the ceiling, never the staleness.** `JobSettings` gains
`kind_wait_seconds`, a `{kind key: seconds}` map edited on the Job execution
page's fourth form, one row per registered kind rendered straight from
`all_job_kinds()` so a feature app registering a new kind needs no template
edit. The registry is the allowlist: only keys the registry names are ever read
out of the POST, and a blank field CLEARS that kind's override rather than
pinning the code default into the database. The resolved value — operator
override first, else the kind's own declaration, else `None`, never a guess —
is stamped onto `JobContext.wait_seconds` by `Worker._build_job_context`,
**riding the settings row that method already fetches for
`response_timeout_seconds`**; a second `get_solo()` would be a query-count
regression and is explicitly not how this is read.

Staleness stays code-declared because a kind's staleness is a property of what
the work DOES, in the same spirit as `default_priority` (§2); the ceiling is
operator-editable because how patient this box should be is an operational
judgement.

**THE RULE THAT COMES WITH THE CEILING, and it is not optional: a wait ceiling
may never release the exclusive slot early.** A handler whose ceiling expires
MUST NOT write a terminal outcome while its own engine still reports the work
running. The exclusive slot is released the instant the job row goes terminal,
so a handler that gives up waiting and reports success hands the machine to the
next admission while its own engine is still sampling — which is the 2026-08-25
follow-up 2, and is exactly the state the incident ran into. Such a handler
must either keep holding (continuing to report progress, which keeps the row
alive under the worker's heartbeat) or cancel the engine-side work and confirm
the engine is terminal, and only then return. The rule is published on
`JobKind.handler` and `JobKind.default_wait_seconds` and in
[`docs/EXTENDING.md`](../EXTENDING.md), because the queue can only enforce ONE
side of it: the barrier catches a still-working engine before the next
EXCLUSIVE job launches, since the barrier-polling adapter withholds `True`
while a prompt is still running — but the exclusive→non-exclusive case and the
cross-engine case stay unenforceable from the queue side (§8 below).

**No shipped kind declares either field today.** The registry carries them, the
worker and the sweep read them, the settings page edits the ceiling, and every
shipped kind leaves both `None` — which is exactly the prior behaviour. Said
plainly so nobody reads this section as a claim about a kind that has not made
one.

### 6. §8's named gaps: the three 2026-08-25 follow-ups are DISCHARGED, and the residuals that replace them

**The 2026-08-25 amendment's three follow-ups are closed by this track.**

1. **`STALE_AFTER_SECONDS` cannot survive a cold model load that starves the
   worker.** Discharged, by both candidate fixes at once: the heartbeat now has
   its own thread, decoupled from the tick loop a slow model load can starve
   (§4 above), and staleness is now kind-aware, so a kind that cold-loads large
   models declares its own allowance instead of sharing one number with a
   sub-second embed.
2. **A kind's own wait timeout releasing the exclusive slot early.**
   Discharged on both halves that are reachable from here: the rule is now
   published as part of the kind contract (§5 above), and the barrier enforces
   it where the queue can — a still-working engine withholds `True`, and the
   next exclusive job is not launched. Its unenforceable remainder is named
   below rather than claimed as closed.
3. **Eviction's blindness to a live attempt.** Discharged: the protected set is
   no longer derived from RUNNING rows alone but from RUNNING rows UNION this
   process's own in-flight attempts (§2 above), which is precisely the
   orphaned-but-still-running window that amendment described.

**The residuals that replace them, recorded honestly and fixed nowhere.**

- **Three of them are ADAPTER-side, contributed by the engine steward, and this
  track changes no adapter internals.** (a) The image adapter's no-baseline
  path returns `True` having observed NOTHING: when its own system-stats read
  is unavailable as the call starts, it cannot compute a memory rise at all and
  degrades to the pre-barrier contract — a 2xx plus one queue-idle check. That
  is exactly the precautionary-call case, so a `True` there is a confirmation of
  nothing, and the queue cannot tell it from a real one; only the adapter can.
  (b) Machine-wide memory drift can fake the settle rise: the threshold falls
  back to a floor read from machine-wide available memory, so an unrelated
  process finishing during the poll window reads as a settled eviction. (c) The
  adapter's residency memo is LRU-capped at eight endpoints, so on a box with
  many endpoints a belief can age out and an endpoint report "nothing resident"
  purely for that reason — which, under `residency_authority="memo"`, buys an
  extra precautionary call rather than a wrong decision. Named, not fixed: all
  three are adapter-side.
- **A configured endpoint with no connection row cannot be barriered.** The
  unload seam takes a `model_id`, and such an endpoint supplies none; its
  believed-resident models are still evicted by the per-model loop, but an
  empty or unavailable belief there leaves it unbarriered. Never papered over
  with a synthetic id the engine would not recognise.
- **`unload_scope` and `residency_authority` are declarations, not probes.**
  The queue reads what an adapter says and never asks the engine to prove it.
  Until an adapter takes its lines the safe defaults apply — coarser eviction
  on a per-model engine, and needless precautionary calls to an engine with a
  real residency endpoint. Named costs, not correctness gaps.
- **The refusal count and its span are process-local.** The `not_before`
  hold-off is durable; the consecutive-refusal count is not, so a restart
  resets it and a pathological endpoint could refuse three times per worker
  lifetime rather than three times ever.
- **Wait-ceiling enforcement is partial** — §5's exclusive→non-exclusive and
  cross-engine cases, above.
- **Cross-process live attempts stay invisible to eviction.** The protected set
  uses THIS worker's own in-flight map; with more than one worker, another
  worker's loading model could still be unloaded. There is one worker today,
  and a durable claim-level signal is the fix if that changes.
- **An exclusive job waiting behind a protected endpoint has a cost as well as
  a duration.** The wait is bounded by the live attempt's own end; the per-tick
  price is bounded by the fifth ordering rule (no HTTP before the protection
  check) and by the hold-off (one claim round-trip per hold-off period rather
  than a full cross-engine probe every 0.5 s). It is not zero.
- **The §2 endpoint-scope exception does cost a reload**, bounded to that one
  case.
- **`over_budget` is computed over a wider endpoint set on exclusive-admitting
  ticks**, so the number is not comparable tick to tick.
- **A genuinely shrunk model stays high for ever** until an operator sets an
  override.
- **Residency belief remains a belief.** A restart loses the memo; the barrier
  mitigates, it does not cure.
- **The widened sweep adds HTTP to an admitting tick, and the precautionary
  call is the expensive half** — bounded by the exclusive-only condition, by
  the narrowing, and by the heartbeat thread, but real seconds on a box with
  several endpoint-scope engines and no residency endpoints.
- **Affinity reads a pre-eviction snapshot** that can be minutes old in
  sequential mode, and none at all on a fresh worker.
- **Sleep detection is a heuristic** (§4 above).
- **Measurement is still not process-peak and still not accelerator-upcast
  aware.** This track makes the estimate honest and monotonic, not exact.

§8's own original gaps — the CLI's `--queue` bypass, unauthenticated job-id
access, no-JS ask submission, per-endpoint budgets, and the unexplained
oversize reason on the Queue page — are all unchanged by this amendment.

### 7. ADR 0015's agent-layer gaps are untouched

This track fixes **two chat-surface defects** and changes **no tool contract**,
no tool result shape, and nothing about what an agent may do.

The first: a turn whose job row vanished (a database crash rolling back the row
that `on_turn_terminal` is driven by) sat at "working" for ever, and recovery
was "delete the conversation and resend". `agents/reconcile.py` — deliberately
at the column root, because `reconcile_stranded_turn` must call
`models.contracts.queue.get_job` and every module under `agents/runtime/` is
swept for exactly that call — closes such a turn honestly with one conditional,
idempotent `UPDATE`, from the poll view that was going to answer "queued" for
ever and from `manage.py reconcile_turns` for the row nobody is polling. There
is no background sweeper: the condition is rare and the read surface already
visits exactly the row that matters.

The second: the chat page's poll loop could tick silently for ever on a
response it could not act on — a parseable body with no recognized state, or a
5xx that happened to parse. Forward compatibility is kept (an unknown state is
still retried, not treated as an error on the first tick); what it no longer
buys is an unbounded loop, because every answer the page cannot act on is now
counted against the same bounded transport ceiling a dropped connection uses,
and only a recognized state clears the count. A recovering database now answers
a RETRYABLE 503 the loop retries, told apart from the terminal "the queue is
not configured" 503 that keeps its setup link and stops polling — **on both of
the paths a database blip can take into that view**, which took one correction
to be true of. `_queued_body`/`_running_body` read the queue through
`models.contracts.queue.get_job`, and the backend's `_guarded` converts every
ORM `ProgrammingError`/`OperationalError` into `QueueUnavailable`, so on exactly
the queued/running turn this feature exists for the blip arrives wearing the
terminal exception's clothes. `turn_status` therefore reads the chained
`__cause__` rather than the exception class alone: an `OperationalError` cause
is the retryable answer, while a `ProgrammingError` cause (tables not migrated)
and a causeless `QueueUnavailable` (no backend configured) both stay terminal
and keep the setup link. Teaching `_guarded` itself to tell "table missing"
from "database unreachable" is the better fix and remains open — it is a
queue-contract change across fifteen `except QueueUnavailable` sites in three
columns, and is not made here.

[ADR 0015](0015-agent-layer-and-tool-contract.md)'s own named gaps are
unchanged, and none of them is addressed here.
