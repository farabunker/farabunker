# models/queue/ — The execution queue (jobs, scheduling, the worker)

**Every model execution on this box goes through here, in order, and nothing runs until
the memory it was promised is actually free.**

This app is the execution half of the [§4 Config & Secrets](../../docs/ARCHITECTURE.md#4-the-core-services)
core service's model story: `models/registry/` answers *which* engine and model back a
role, and this app answers *when* that model may run and *what else has to get out of its
way first*. The decision record is [ADR 0013](../../docs/adr/0013-inference-execution-queue.md),
including its 2026-09-21 memory-governance amendment, which this README summarises for a
reader standing in the column.

## Not a tool

This app is **trusted platform code**, not a
[§5 module](../../docs/ARCHITECTURE.md#5-the-module-contract) — the same posture
[`models/registry/README.md`](../registry/README.md) states for itself, and for the same
reasons: no manifest, no capability grants, no sandbox. It is a Django app with the label
`jobs`, and it is **column-private under rule 2** ([`models/README.md`](../README.md)'s
import law): a `tools/*` or `agents/*` app never imports `models.queue` at all. It reaches
the queue through `models.contracts.queue` (`enqueue`/`get_job`) and declares its work
through `models.contracts.jobkinds`, both of which are pure and import neither side's
storage. Two narrow exceptions are named in the import law itself and nowhere else:
`models.queue.visibility` (importable for row-visibility filtering) and
`models.queue.models` (importable by `foundation.ops`, for backup/restore only).

The one-way rule runs in both directions: **`models/queue` never imports `agents`.** That
is why a stranded chat turn is repaired by `agents/reconcile.py` and never by this column —
the queue cannot know which turn points at which job, and may not look.

## The four seams

- **`models.py`** — `InferenceJob` (the row every execution is), and `JobSettings` (the one
  operator-editable settings row, `pk=1`).
- **`scheduler.py`** — the admission policy: one pure function, stdlib and dataclasses
  only, no Django import, no database, no I/O, provable by table tests alone. Its numbered
  rule docstring is the authority; ADR 0013 §3–4 summarise it.
- **`claim.py`** — the claim transaction: one advisory lock, the orphan sweep, the
  candidate window, the call into the scheduler, and the conditional `UPDATE` that turns a
  planned admission into a claimed job.
- **`worker.py`** — the worker process: the tick loop, the thread pool, the heartbeat
  thread, the eviction pass, and the barrier. `manage.py run_jobs` is a thin wrapper around
  it; `--once` is exactly one tick.

Each of those modules carries a long module docstring that is the real reference. What
follows is what an operator or a contributor needs before reading them.

## The footprint ladder, as the queue consumes it

The queue never stores a footprint of its own. It asks
`models.registry.bindings.footprint_for(engine, endpoint, model_id)` **fresh on every
planning round**, and that resolves a **four-rung ladder** on the connection row
(`ModelConnection.effective_footprint_bytes`, walked on `is not None` so a genuine zero is
a value and not "unknown"):

1. **`footprint_override_bytes`** — the operator's word, always final.
2. **`measured_footprint_bytes`** — what this queue observed after a run it executed.
3. **`engine_reported_footprint_bytes`** — what the engine said a loaded copy occupied,
   harvested from the residency snapshot the worker is taking anyway. Never a call made
   for this purpose, and only ever written from a POSITIVE reading.
4. **`None` — unknown**, which is not a hole in the accounting: a job holding any
   unmeasured model is **effectively exclusive** and is admitted only into a genuinely
   idle machine, alone.

Rungs 2 and 3 are written through one recorder that **keeps the maximum** — a reading
below the column's own standing value is refused outright, bytes and timestamp alike. See
[`models/registry/README.md`](../registry/README.md#the-footprint-ladder-and-the-recorder-that-keeps-the-maximum)
for the rule and the reasoning; the queue is only ever a reader of it.

**The queue's own reading of the ladder is deliberately conservative.** Two jobs sharing a
model key pay for it once, at the largest known reading seen that round. A job declaring
no models at all is treated exactly like an unknown-footprint job, because a planner that
declared nothing is a bug and must not buy free concurrency.

## Eviction: how far it reaches, and what it may never take

Admission plans against *declared* footprints. Eviction (`Worker._evict_to_match_plan`)
makes the machine's *actual* resident memory match that plan before any newly admitted job
launches. Five things about it are worth knowing before reading the code.

**The swept set.** Ordinarily it is the endpoints the RUNNING jobs themselves use. **On a
tick that admits an EXCLUSIVE job**, and only then, it is unioned with
`models.registry.bindings.registered_endpoints()` — every engine endpoint this box knows
about, configured defaults and connection rows alike. Only the admission entitled to the
whole machine pays for the whole machine to be probed. The consequence is stated rather
than hidden: the budget arithmetic counts more resident bytes on those ticks, so the
number is not comparable tick to tick. Under-counting resident memory is what crashes
hosts, so that is the safe direction.

**The protected-key rule.** A model key held by a RUNNING job, or by an attempt still in
flight in this process, is never unloaded. The first half covers this tick's own admitted
batch for free, because the claim commits before eviction runs — which matters concretely,
since an agent turn is planned exclusive, so every chat message runs this pass. The second
half covers an attempt the orphan sweep requeued while its handler is genuinely still
cold-loading: its row is back at `queued` and invisible to the first half for exactly as
long as that load takes.

**`unload_scope` and `residency_authority`.** Two OPTIONAL declarations on the engine
(`models.contracts.engines.base.InferenceEngine`), read with `getattr` and never called:

| Declaration | Values | What the queue does with it |
|---|---|---|
| `unload_scope` | `"model"` / `"endpoint"` | `"model"`: skip protected keys one by one, unload the rest individually. `"endpoint"`: one call frees everything there, so the whole endpoint is skipped if anything protected lives at it. |
| `residency_authority` | `"endpoint"` / `"memo"` | `"endpoint"`: an empty residency report is a FACT, so no precautionary call is made. `"memo"`: an empty report only means this process does not remember, so a precautionary call is made. |

**The safe default when either is absent or unrecognised is the LAST value in each pair —
`"endpoint"` and `"memo"`.** The asymmetry is the point: assuming an unload frees the whole
endpoint costs at worst a needless reload, while wrongly assuming per-model granularity
destroys a live cold load measured in minutes; and treating an empty residency answer as
forgetfulness costs one precautionary call, while trusting it launches an exclusive job on
top of memory nobody released.

**The one sanctioned exception to the protection rule**, and its boundary: an
`"endpoint"`-scope endpoint IS freed when every protected key there belongs to the admitted
exclusive job's OWN keys at its OWN endpoint. Freeing it unavoidably takes its own model
with it and there is no per-model call to make instead, so the job pays at worst one
reload. The own-key set is empty for every other caller, which is what confines the
exception to the one case it is written for.

**The barrier.** An admitted exclusive job is not launched until the memory it was promised
is free. A `False` from an unload against a model the snapshot says is RESIDENT is
*informative* and refuses the launch; a `False` from a *precautionary* call — one made
precisely because the belief was worth nothing — is logged and ignored, because an adapter
cannot tell "nothing freed" from "nothing to free" and a cold endpoint is the common case
after a restart. A refused job takes a durable hold-off (`not_before`, 45 s) and is failed
honestly only once **both** three refusals and five minutes of wall clock have elapsed. A
successful barrier resets the count. A **protection** refusal is never counted at all — that
wait is bounded by the live attempt's own end.

## The log vocabulary an operator will actually see

Every line below is real and stable; [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md#the-execution-queues-memory-governance-what-the-logs-say-and-what-to-do)
says what to do about each one.

| Level | Line | Means |
|---|---|---|
| INFO | `unload <model> at <endpoint> (<engine>), scope <model\|endpoint>, <reason> -- accepted\|refused` | One line per unload attempt. `<reason>` is `not needed at an exclusive endpoint`, `over budget`, or `precautionary barrier`. |
| INFO | `eviction pass (<triggers>): swept N endpoints, R resident, M admitted marginal, budget <bytes\|unset>` | One line per tick that evicts at all. `unset` is a posture, not a missing value. |
| INFO | `eviction could not list installed models at <endpoint> (<engine>) -- that endpoint is skipped this tick` | One engine briefly unreachable. Nothing to do. |
| INFO | `engine <name> has no <method>() -- eviction degrades to today's idle-timeout behavior for it` | Once per engine+method, for an adapter offering no `list_installed`/`unload`. |
| WARNING | `eviction skipped the whole endpoint <endpoint> (<engine>) -- one unload there frees everything, and <model> is protected by live work (<reason>)` | Correct refusal. Only actionable if it repeats while nothing is running. |
| WARNING | `not launching exclusive job N this tick -- ... is protected by live work` | The job waits for the live attempt to end. |
| WARNING | `not launching exclusive job N this tick -- engine <name> at <endpoint> refused to release <model>` | The informative refusal. Three of these, spanning five minutes, fail the job. |
| WARNING | `job N failed -- engine '<name>' at <endpoint> did not release memory ... after 3 attempts` | The honest failure, also on the job row. |
| WARNING | `registry: refused to lower <column> on connection N` | A footprint reading below the standing value. The remedy is named in the line. |

**WARNING is reserved for what an operator can act on.** A successful eviction used to be
silent and a refusal was the only thing this pass ever logged, which is exactly why an
operator watching a host fill up could not tell "nothing needed evicting" from "everything
was skipped".

That table is the **eviction and barrier** vocabulary. The worker's own lifecycle lines —
sleep detection, a failed heartbeat write, the duplicate-submit refusal, the capped pass
stopping short, the two cold-boot lines, the heartbeat thread's start and exit, and the
degraded job-context read — are in
[`docs/OPERATIONS.md`](../../docs/OPERATIONS.md#worker-lifecycle--the-other-new-lines)
alongside these, with the same "what to do" treatment.

## What an unset memory budget does, and what it no longer does

`JobSettings.memory_budget_bytes` ships **unset**, and that stays deliberate: it is a fact
about the operator's own hardware, and no platform-wide number could be right for every
box (ADR 0013 §4's "two categories of number").

**It still means fully sequential execution** — at most one job on the whole machine,
ever.

**It no longer means eviction is switched off.** The budget used to gate the whole
eviction function, which made every mechanism inside it dead code exactly on the posture
the field actually ran in. Today the endpoint targeting, the protected set, the residency
snapshot, the exclusive pass, the barrier and all of the logging run **unconditionally**;
only the capped, budget-driven pass is gated, because it is the one decision that is
arithmetic against a number that may not exist.

The Queue page says so out loud, and the worker process — never the console — writes what
ITS machine reports as total memory onto the settings row once at boot, rendered as a
labelled prefill naming the process and the date. **Nothing is ever applied on the
operator's behalf**: the console renders in the web service and the budget governs the
worker service, separate containers, and a container sees the VM's allocation rather than
the host's, so a silently derived budget would be authoritative and wrong.

## Admission order, and the two things that bend it

Strict `(priority, id)` order with **no backfill** is still the policy, and the
deadlock proof that buys is still intact. Two additions bend the order without touching it:

- **Model-affinity batching** (`scheduler.affinity_order`) sorts candidates by
  `(priority, not pinned, not affine, job_id)`. Within one priority number, a job whose
  models are all already resident runs ahead of one that would have to load something.
  Priority is never crossed. A candidate passed over `MAX_PASSOVERS` (3) admission
  **rounds** — one count per round, however many peers went ahead — is PINNED and sorts by
  id ahead of every unpinned peer from then on. The Queue page shows "passed over
  once/twice/N times" on the waiting row.
- **`not_before`** excludes a barrier-refused job from the candidate query until its
  hold-off expires — one extra `WHERE` on the same `SELECT`, not a second statement. The
  deadlock proof survives because the exclusion is time-bounded and self-clearing.

## Testing notes

**Every test module that drives the worker tick MUST import the engine-endpoint fence.**

```python
from models.contracts.testing import hermetic_engine_endpoints  # noqa: F401
```

The reason is concrete, not hygienic. The widened sweep resolves **real configured
endpoints** from `settings.INFERENCE_DEFAULT_ENDPOINTS`, against the **real adapters**
registered at import time. A test that drives `Worker.tick()`, `_evict_to_match_plan()` or
`run_jobs --once` with an exclusive admission will therefore probe whatever engine happens
to be listening on the contributor's own machine and then call `unload()` against it —
dropping a warm model mid-run while the suite reports all green. That is not hypothetical;
it was proven against a shipped test module whose planner declares `exclusive=True`.

The fixture is `autouse=True`, so the import alone fences the module (hence the `noqa`).
It blanks the endpoint map rather than stubbing the engine registry, deliberately: real
engine resolution stays under test, which is what the widened sweep's own tests and its
query-count pin depend on. A test that genuinely wants addresses assigns
`settings.INFERENCE_DEFAULT_ENDPOINTS` itself and that assignment wins.

**The fence covers the configured half of the union, not both halves.** `registered_
endpoints()` unions `INFERENCE_DEFAULT_ENDPOINTS` with every `ModelConnection` row's
endpoint, and the fixture blanks only the first. A test that creates a connection row
naming a real registered engine at a real address is still unfenced — no import can stop
that, since the row is the test's own write. The connection-row half is the test author's
responsibility, and the house pattern already discharges it: name a **fake** engine
(`test-inference`) at an address nothing listens on, which is what every shipped ticking
module does.

`foundation/ops/tests/test_engine_endpoint_fence.py` is the gate that stops the next module
forgetting — it is a plain importable fixture rather than a `conftest.py`, which this repo
forbids anywhere, so the fence is visible in the file it protects. **The gate walks
`git ls-files`, so a brand new test module is unpoliced until it is staged**; that is
stated in the gate's own docstring too, because the window is real (a module written and
run in a working tree can go green without the fence) even though it closes before any
commit or review can carry it anywhere.

## See also

- [ADR 0013](../../docs/adr/0013-inference-execution-queue.md) — the queue's decision
  record, including the 2026-09-21 memory-governance amendment this README summarises.
- [`models/registry/README.md`](../registry/README.md) — the footprint ladder's storage
  half, the keep-the-maximum recorder, and `registered_endpoints()`.
- [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md) — the operator's side: every new log
  line and what to do about it, and the queue's own limits.
- [`docs/EXTENDING.md`](../../docs/EXTENDING.md) — how a feature app declares a job kind,
  including `stale_after_seconds`, `default_wait_seconds`, and the rule that a wait
  ceiling may never release the exclusive slot early.
