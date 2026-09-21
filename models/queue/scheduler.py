"""
The execution queue's admission scheduler (ADR 0013) -- ONE pure
function, `plan_admissions`, deciding which queued jobs to admit this
round. This module is deliberately stdlib + dataclasses only: NO Django
imports of any kind, no DB, no worker, no I/O. The entire scheduling
policy of the platform lives here so it is provable by table tests alone
-- feed it candidate/running snapshots, read back the admitted job ids,
no fixture, no migration, no event loop required to reason about whether
the policy is correct. `models/queue/claim.py`'s claim code translates live
`InferenceJob` rows into `SchedCandidate`/`SchedModel` inputs and calls
this module; this module never reaches back for a row.

Footprint/key provenance contract (binding on the claim code that builds
this module's inputs, not on this module itself -- this module trusts
whatever `SchedModel` values it is handed):

- Every `SchedModel.footprint_bytes` must come from
  `models.registry.bindings.footprint_for(engine, endpoint, model_id)`,
  resolved FRESH for that model ref on every planning round this module is
  called for -- never read once from a stored snapshot and reused
  indefinitely. `InferenceJob.model_refs` (`models/queue/models.py`) IS
  such a snapshot (`footprint_bytes` there is `None` at enqueue time by
  design -- see `models.contracts.jobkinds.ModelRef.footprint_bytes`'s
  docstring -- and only ever filled in "later, by console-side code at
  claim time"). Mapping that stored dict straight through into
  `SchedModel` unchanged, instead of re-resolving each ref through
  `footprint_for()` each round, would leave every job's footprint `None`
  forever: `effectively_exclusive()` would then treat every job as
  unknown-footprint (rule 2b below) permanently, and the platform would
  silently run fully sequential in practice no matter what `budget_bytes`
  an operator configures. This is the one fact this module cannot check
  for itself (it never touches the DB) -- it is exclusively the claim
  code's obligation.
- Every `SchedModel.key`'s `(engine, endpoint_normalized, model_id)`
  triple must be built with `models.registry.discovery.norm_endpoint()`
  for the endpoint segment (the same trailing-slash/scheme-case
  insensitive comparison `footprint_for()` itself already uses to match a
  `ModelConnection` row) and with `models.contracts.catalog.norm_tag()`
  (canonical home; re-exported as `models.registry.discovery.norm_tag`)
  for the model_id segment. A bare tag (`an-embedding-model`) and its
  `:latest`-suffixed spelling (`an-embedding-model:latest`) name the same
  on-disk weights; building `SchedModel.key` from the raw, un-normalized
  strings would silently mint two distinct keys for one real model, and
  rule 4's dedup would then double-charge that one model's footprint
  against the budget instead of counting it once. This module treats
  `key` as an already-comparable opaque tuple and performs no
  normalization of its own -- normalization happens exactly once, at the
  one place that already owns it, matching how the rest of this codebase
  treats connection identity.

Policy summary (the numbered rules below are the actual admission
algorithm -- read them in order, they run in order; every cross-reference
elsewhere in this module cites one of these numbers):

1. No budget (`budget_bytes is None`) means sequential mode: at most one
   job runs on the whole machine, ever -- admit the head candidate only,
   and only if nothing is running (a job already running IS that one
   job).

2. "Effectively exclusive" -- the single predicate `effectively_exclusive()`
   implements, applied to both running jobs (rule 3) and candidates
   (rules 5/6) -- is true when ANY of:
     (a) the planner declared the job exclusive (`SchedCandidate.exclusive`);
     (b) the job holds any unknown-footprint model (a `None`
         `footprint_bytes`, after rule 4's own None-dominant fold);
     (c) the job's `models` tuple is empty -- treated the same as (b),
         since a job kind's planner declaring no models at all is a bug
         in that planner, not a zero-cost job entitled to free
         concurrency;
     (d) given a budget, the job's own dedup'd (rule 4) footprint total
         strictly exceeds it ("oversize" -- note the strict `>`, not
         `>=`: a total exactly equal to the budget is NOT oversize).

3. A running job that is effectively exclusive (rule 2) blocks ALL
   admissions this round -- it has the machine to itself, full stop. This
   check uses rule 2's FULL (broad) predicate, including (d) oversize,
   even though a narrower predicate limited to (a)/(b)/(c) would produce
   an IDENTICAL admitted set: an oversize running job's own resident
   footprint alone already exceeds `budget_bytes`, so `used > budget_bytes`
   holds before any candidate is even considered, and rule 7's check
   (`used + marginal > budget_bytes`) then fails for every candidate
   regardless of `marginal` -- `marginal` is always `>= 0` by construction
   (rule 7), so it can never claw `used` back under budget. Broad and
   narrow are behaviorally equivalent here; broad is used anyway because
   it is cheaper (short-circuits before any resident-set arithmetic runs
   at all) and more honest -- `effectively_exclusive()` is the same
   function `models/queue/views.py`'s queue page calls to answer "why is
   this job running alone," where an oversize running job needs the true
   answer, not a narrower predicate that merely happens to produce the
   same admission decision this round.

4. Resident-set accounting is deduplicated by `SchedModel.key`,
   MAX-folded across every reading of that key seen this round (every
   running job's stamp, and -- once admitted -- every admitted
   candidate's own stamp) -- never first-wins. A key with even one
   unknown (`None`) reading anywhere it appears this round is unknown as
   a whole (None-dominant); otherwise the dedup'd value is the LARGEST
   known reading seen for it. This reconciles a stale or pre-override
   stamp (e.g. a running job's snapshot taken before `footprint_for()`
   later re-measured that same model larger) with a fresher, larger one
   instead of silently under-counting real memory pressure -- two jobs
   (or a running job and a candidate) sharing a model key pay for it
   once, at its true known size, never at whichever size was read first.

5. A candidate that is effectively exclusive (rule 2) is admissible ONLY
   into a genuinely idle machine -- nothing running, and nothing admitted
   yet this round -- and is the only thing admitted that round when so
   (nothing is measured/bounded about it yet, so nothing can be reasoned
   about safely running alongside it).

6. Oversize (rule 2d) deliberately still gets rule 5's "admit alone when
   idle" treatment, not a permanent refusal: refusing it forever would
   deadlock the owner's own job forever, which is worse than one job
   running over the operator's stated budget. The UI surfaces the fact
   (`models.queue.views._present_row`'s `runs_alone` flag); this module
   only ever decides admission.

7. A non-exclusive candidate is admitted only if its marginal cost fits
   the remaining budget: `used + marginal <= budget_bytes`, where
   `marginal` is the candidate's own dedup'd (rule 4) footprint total for
   keys not already resident, keyed against the CURRENT known size of any
   key that IS already resident (`max(0, size - already_resident)` per
   key -- never negative). Because `marginal` can never be negative, this
   check is never made "easier" by a candidate whose keys are already
   resident, only cheaper -- and it stays correct even when `used` has
   already exceeded `budget_bytes` going into this round (e.g. the budget
   was lowered by an operator after jobs were already running): the
   comparison is always `used + marginal > budget_bytes`, never a
   subtraction (`budget_bytes - used`) that could go negative and
   silently manufacture false headroom -- with `used` already over
   budget, `used + marginal` stays over budget for every non-negative
   `marginal`, so every candidate is correctly blocked.

8. `max_concurrent` is a hard cap on `len(running) + admitted-so-far`,
   checked before every single admission (`max_concurrent <= 0` admits
   nothing, defensively).

9. Candidates are walked in the order rule 10 produces -- `(priority,
   not pinned, not affine, job_id)`, which IS strict `(priority, job_id)`
   whenever no affinity snapshot is supplied -- and the walk
   STOPS at the first one that cannot be admitted this round -- no
   candidate is ever skipped in favor of a later one that would fit
   ("backfill"). This is a deliberate v1 policy choice (see the plan this
   task implements), not an oversight: it makes deadlock structurally
   impossible (an unknown/oversize head is effectively exclusive, which
   this policy always admits the instant the machine goes idle -- it
   never sits blocked forever waiting on a peer that will never yield).
   It also means no candidate is ever skipped in favor of a later,
   lower-priority one within a single round -- but this is narrower than
   "starvation is impossible": within one priority NUMBER's own position
   in the order this policy never reorders past it, yet a steady inflow
   of jobs at a lower priority number can still keep arriving ahead of an
   older, higher-priority-number job indefinitely and starve it -- there
   is no aging/anti-starvation knob in this policy (each job kind's own
   `default_priority` rung, `models.contracts.jobkinds.JobKind
   .default_priority`, makes sustained lower-number inflow an entirely
   reachable operational scenario, not a hypothetical one). The cost --
   a blocking head (or a busy lower-priority stream) can leave technically-
   fitting work waiting behind it -- is accepted in exchange for the
   deadlock guarantee and the simplicity of never backfilling; giving
   either property back later needs a new ADR, not a quiet tweak here.

10. MODEL-AFFINITY BATCHING, with an aging bound (`affinity_order`, spec
   3.6). Among candidates AT THE SAME PRIORITY NUMBER, one whose model
   keys are ALL already resident sorts ahead of one that would have to
   load something -- the owner's own requirement that like requests for a
   given model run together even when they were submitted at different
   times. PRIORITY IS NEVER CROSSED, and rule 9's walk still stops at the
   first candidate it cannot admit, so the no-backfill deadlock proof is
   untouched: whichever candidate heads a round is still admitted alone
   the instant the machine is idle. THE AGING BOUND: a candidate whose
   `passed_over` count has reached `max_passovers` is PINNED and sorts
   strictly by id within its priority from then on, so within one
   priority number a job can be passed over at most that many ROUNDS --
   the count is of OCCASIONS a job lost its turn, one per round however
   many peers went ahead, not of peers. Across priority numbers nothing
   changed, so rule 9's honestly-scoped starvation caveat is neither
   improved nor worsened. The believed-
   resident set is a plain frozen set the CALLER hands in (the worker's
   last residency snapshot -- see `affinity_order`'s own docstring); this
   module never asks an engine anything and never touches a row.

Preconditions this module assumes but does not defend against everywhere
(caller responsibility -- the claim code, T4):

- `candidates` and `running` are disjoint by `job_id` -- a job is queued
  or running, never modeled as both at once in the same call. Asserted
  (debug builds) in `plan_admissions` -- `assert` is a no-op under
  `python -O`/`PYTHONOPTIMIZE`, so this is a documented invariant plus a
  cheap development-time trip-wire that catches a caller bug immediately
  in ordinary runs, not a guarantee this module enforces in an optimized
  deployment.
- `SchedModel.footprint_bytes`, where not `None`, is `>= 0`. Enforced at
  construction (`SchedModel.__post_init__`) rather than re-checked here:
  a negative footprint would manufacture free budget headroom for every
  key it touches (rule 7's `max(0, size - already_resident)` folds a
  negative `size` toward 0 headroom-side, but a negative value stored in
  `resident_sizes` would still understate `used` for every later
  candidate) -- refusing it at the one place `SchedModel` values are
  created removes the bug at its source instead of merely tolerating it
  downstream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

_Key = tuple[str, str, str]

# How many times model-affinity reordering may put a later-by-(priority,
# id) peer ahead of one candidate before that candidate is PINNED --
# ordered strictly by id within its priority from then on, and never
# reordered behind a peer again (owner decision 4). Supplied by the claim
# code as a keyword argument so table tests can vary it; this is the
# default the queue actually runs with.
MAX_PASSOVERS = 3


@dataclass(frozen=True)
class SchedModel:
    """One model a job holds (or would hold) resident.

    `key` is `(engine, endpoint_normalized, model_id)` -- the same triple
    that identifies "the same model connection" elsewhere in the codebase
    (`models.contracts.jobkinds.ModelRef` minus `role`/`connection_name`,
    which don't affect whether two jobs are sharing the same resident
    weights). The caller building this value (the claim code, a later
    task) MUST normalize both segments before constructing it --
    `models.registry.discovery.norm_endpoint()` for the endpoint and
    `models.contracts.catalog.norm_tag()` (re-exported as
    `models.registry.discovery.norm_tag`) for the model_id -- see the
    module docstring's provenance contract for why an un-normalized key
    double-charges one real model's footprint. This module treats `key`
    as an opaque, already-comparable tuple and does no normalization of
    its own, matching the "one place already owns normalization" rule the
    rest of this codebase follows for connection identity.

    `footprint_bytes` is `None` for "unknown" (not yet measured) and,
    when not `None`, must be the value `models.registry.bindings.
    footprint_for(engine, endpoint, model_id)` resolves for this exact
    ref -- see the module docstring's provenance contract for why a
    stored/serialized snapshot is never an acceptable substitute. See
    also `InferenceJob.footprint_bytes`'s docstring for why a job with
    any unsized model must never be treated as sized at all, let alone
    partially.
    """

    key: _Key
    footprint_bytes: int | None

    def __post_init__(self) -> None:
        if self.footprint_bytes is not None and self.footprint_bytes < 0:
            raise ValueError(
                f"SchedModel.footprint_bytes must be >= 0 or None, got "
                f"{self.footprint_bytes!r}"
            )


@dataclass(frozen=True)
class SchedCandidate:
    """One job's admission-relevant facts -- a queued job when passed in
    `candidates`, a running job when passed in `running`. Same shape for
    both: a running job's "candidacy" is only ever inspected for its
    resident footprint and its exclusivity, never re-admitted. A given
    `job_id` must appear in at most one of `candidates`/`running` in a
    single `plan_admissions` call -- see the module docstring's
    preconditions."""

    job_id: int
    priority: int
    exclusive: bool
    models: tuple[SchedModel, ...]
    # How many admission ROUNDS this candidate has lost to model-affinity
    # reordering -- rounds in which at least one later-by-(priority, id)
    # peer was admitted ahead of it (`InferenceJob.passed_over`). ONE per
    # round, however many peers went ahead: the aging bound counts
    # occasions a job lost its turn, not peers (spec 3.6). Durable, not
    # in-memory: a worker restart must not reset a job's age. Trailing and
    # defaulted, so every existing construction of this dataclass is
    # unchanged.
    passed_over: int = 0


def _dedup_bytes(models: Iterable[SchedModel]) -> tuple[dict[_Key, int | None], bool]:
    """Reduce `models` to one entry per distinct `key` (rule 4): None-
    dominant, then MAX-folded. For each key, if ANY reading of it among
    `models` is unknown (`None`), the dedup'd value is `None` regardless
    of any other, differently-sized reading of the same key; otherwise
    the dedup'd value is the largest known reading. This is
    order-independent by construction (every reading is collected before
    any decision is made per key), unlike a first-wins fold, which would
    give a different answer depending on which reading of a duplicated
    key happened to appear first.

    Used both for a single candidate's own total (rule 2d) and for
    resident-set accounting across several jobs (rule 4's cross-job case)
    -- same fold either way, the caller decides which models it points
    at. Takes `Iterable` (not `Sequence`): every call site here passes a
    generator expression folding over multiple jobs' `.models`, which is
    consumed once and is never indexed or re-iterated.
    """
    readings: dict[_Key, list[int | None]] = {}
    for model in models:
        readings.setdefault(model.key, []).append(model.footprint_bytes)

    by_key: dict[_Key, int | None] = {}
    for key, values in readings.items():
        by_key[key] = None if any(v is None for v in values) else max(values)

    has_unknown = any(size is None for size in by_key.values())
    return by_key, has_unknown


def resident_bytes(running: Sequence[SchedCandidate]) -> int:
    """Total resident memory of `running`, deduplicated and MAX-folded by
    `SchedModel.key` across ALL running jobs (rule 4) -- two running jobs
    sharing a model pay for it once between them, at its true known size,
    not once each and not at whichever reading happened to be seen first.

    Public (not a private helper) because the claim code (`models.queue.
    claim.claim_and_admit`, via this module's own `plan_admissions`) and
    the queue page (`models.queue.backend.queue_snapshot`'s own
    `running_known_bytes`, calling this function directly, "why is this
    job alone"/"how much headroom is left") both need the same number the
    scheduler itself used -- keeping the fold in this one function is
    what keeps those two call sites from silently drifting apart from the
    scheduler's own idea of resident memory.

    An unknown-footprint resident model contributes 0 bytes to the sum.
    This is correct AS USED inside `plan_admissions`: such a job is
    already effectively exclusive (rule 2b), so rule 3 has already
    returned `[]` before this function's result is ever consulted for
    admission arithmetic, and there is no budget number that would change
    that outcome. It is NOT a safe number to present to an operator as
    "free memory" -- a caller rendering this value on `models/queue/
    views.py`'s queue page must not treat it as real headroom while an
    unmeasured model is loaded; the true resident size in that case is
    simply unknown, not zero.
    """
    by_key, _ = _dedup_bytes(model for job in running for model in job.models)
    return sum(size for size in by_key.values() if size is not None)


def effectively_exclusive(candidate: SchedCandidate, budget_bytes: int | None) -> str | None:
    """The reason `candidate` must run alone (rule 2's full predicate), or
    `None` if it is not effectively exclusive. Truthiness still works at
    every call site (`if effectively_exclusive(...):` / `any(...)`) since
    every reason is a non-empty string and the "not exclusive" case is
    `None`, not `""`.

    One of:
        "declared"          -- rule 2a: the planner declared it exclusive.
        "unmeasured model"  -- rule 2b/2c: an unknown-footprint model, or
                                no models declared at all (treated the
                                same as unknown -- see rule 2c).
        "exceeds memory budget" -- rule 2d: given a budget, its own
                                dedup'd (rule 4) total strictly exceeds
                                it ("oversize").
        `None`              -- none of the above; not exclusive.

    Public for the same reason `resident_bytes` is: `models/queue/claim.py`
    and `models/queue/views.py`'s queue page both need to answer "why is
    this job alone" using the EXACT reason `plan_admissions` itself used,
    not a re-derived approximation that could disagree with it.
    """
    if candidate.exclusive:
        return "declared"
    by_key, has_unknown = _dedup_bytes(candidate.models)
    if not by_key or has_unknown:
        return "unmeasured model"
    if budget_bytes is not None and sum(by_key.values()) > budget_bytes:
        return "exceeds memory budget"
    return None


def affinity_order(
    candidates: Sequence[SchedCandidate], *, resident_keys: frozenset, max_passovers: int,
) -> list[SchedCandidate]:
    """Order `candidates` for admission: `(priority, not pinned, not
    affine, job_id)` (rule 10, spec 3.6).

    THE OWNER'S OWN REQUIREMENT: "we should bundle like requests for a
    given model even if they were submitted at different times... This
    should be a feature of the queue." Among candidates AT THE SAME
    PRIORITY NUMBER, one whose model keys are ALL already resident sorts
    ahead of one that would have to load something.

    PRIORITY IS NEVER CROSSED, and `plan_admissions` still stops at the
    first candidate it cannot admit, so the no-backfill deadlock proof is
    untouched: whichever candidate heads a round is still admitted alone
    the instant the machine is idle.

    THE AGING BOUND: a candidate whose `passed_over` has reached
    `max_passovers` is PINNED -- it sorts strictly by id within its
    priority and can never be reordered behind a peer again. Within one
    priority number a job can therefore be passed over at most that many
    ROUNDS; `passed_over` counts OCCASIONS a job lost its turn, not peers,
    so a round admitting four later peers ahead of it still costs it one.
    Across priority numbers nothing changed, so the ADR's existing,
    honestly-scoped starvation caveat is neither improved nor worsened.

    "AFFINE" MEANS "WOULD LOAD NOTHING", not "would load less": every one
    of the candidate's keys must be in `resident_keys`. A candidate
    declaring NO models is never affine -- the set-subset test would
    otherwise be vacuously true, and such a job is already effectively
    exclusive (rule 2c) rather than a free rider entitled to jump a queue.

    WHERE `resident_keys` COMES FROM, and the two rejected alternatives:
    the WORKER'S most recent residency snapshot (`models.queue.worker.
    Worker._resident_keys`), cached on the worker and handed down through
    `claim_and_admit` as a plain frozen set. NOT this module's own
    resident fold over `running` -- that is derived from running jobs and
    would make affinity a no-op in sequential mode, exactly the posture
    where batching matters most. NOT a live `list_installed` -- that would
    put engine HTTP inside the claim's advisory-lock transaction and stall
    every other admitter.

    It is therefore deliberately STALE: snapshots are only taken on ticks
    that admit, so in sequential mode the cached set can be minutes old;
    it is the pre-eviction belief with that pass's own unloads subtracted;
    and a fresh worker has none at all, which simply falls back to plain
    `(priority, job_id)` order. That is exactly why it drives an ordering
    PREFERENCE and nothing else -- a wrong guess costs one suboptimal
    ordering decision, never a wrong admission or a wrong eviction.

    PURE. `resident_keys` is a plain frozen set the CALLER believes is
    resident; this module never asks an engine anything and never touches
    a row (see the module docstring).
    """
    def _key(candidate: SchedCandidate) -> tuple:
        pinned = candidate.passed_over >= max_passovers
        keys = {model.key for model in candidate.models}
        affine = bool(keys) and keys <= resident_keys
        return (candidate.priority, not pinned, not affine, candidate.job_id)

    return sorted(candidates, key=_key)


def plan_admissions(
    candidates: Sequence[SchedCandidate],
    running: Sequence[SchedCandidate],
    *,
    budget_bytes: int | None,
    max_concurrent: int,
    resident_keys: frozenset = frozenset(),
    max_passovers: int = MAX_PASSOVERS,
) -> list[int]:
    """Decide which of `candidates` (queued jobs) to admit this round,
    given `running` (currently running jobs), a memory `budget_bytes`
    (`None` = no budget = sequential mode), and a hard `max_concurrent`
    cap. Returns the admitted job ids, in the order they were admitted
    (== priority order, since admission never skips ahead -- rule 9).

    `candidates` is ordered defensively inside this function rather than
    trusted as a caller contract: the cost is one cheap sort, and it
    removes an entire class of "caller forgot to order the queryset" bug
    from every call site forever. `running` needs no such sort -- nothing
    about this function's rules depends on the order running jobs are
    visited in (rules 3/4 only ever fold over the whole set), only on the
    order candidates are admitted in.

    `resident_keys`/`max_passovers` are rule 10's two knobs, handed
    straight to `affinity_order` -- the SAME helper the claim code uses to
    derive who was passed over, so the order walked here and the order
    reasoned about there can never drift. Both are defaulted (an empty
    snapshot, `MAX_PASSOVERS`), and an empty snapshot makes `affinity_order`
    plain `(priority, job_id)` order: every existing call site, this
    module's table tests included, behaves exactly as before.
    """
    candidate_ids = {c.job_id for c in candidates}
    running_ids = {r.job_id for r in running}
    assert candidate_ids.isdisjoint(running_ids), (
        "a job cannot appear in both `candidates` (queued) and `running` "
        "in the same plan_admissions call -- see module docstring"
    )

    if max_concurrent <= 0:
        # Rule 8, degenerate case: a non-positive cap can never be
        # satisfied by admitting anything, in any mode. Checked first
        # since every rule below assumes at least one admission is
        # theoretically possible.
        return []

    ordered = affinity_order(
        candidates, resident_keys=resident_keys, max_passovers=max_passovers,
    )

    if budget_bytes is None:
        # Rule 1: sequential mode. At most one job on the whole machine,
        # ever -- admit the head candidate only, and only if nothing is
        # running (a job already running IS that one job).
        if running or not ordered:
            return []
        return [ordered[0].job_id]

    if any(effectively_exclusive(job, budget_bytes) for job in running):
        # Rule 3: a running job holding the machine to itself (broad
        # predicate -- declared, unmeasured, or oversize; see rule 3's
        # equivalence note in the module docstring) admits nothing else,
        # no matter how much budget looks free on paper.
        return []

    # Resident SIZES, keyed by `SchedModel.key`, MAX-folded across every
    # running job (rule 4). After the rule-3 check above, no running job
    # is effectively exclusive, so every running model's footprint is
    # known -- this dict never holds a `None` value from here on. The
    # `assert` below documents that invariant as a debug-build trip-wire
    # rather than silently trusting it -- `assert` is a no-op under
    # `python -O`/`PYTHONOPTIMIZE`, so it is not a runtime guarantee in an
    # optimized deployment, only a check that fires in ordinary runs.
    resident_sizes: dict[_Key, int | None] = dict(
        _dedup_bytes(model for job in running for model in job.models)[0]
    )
    assert all(size is not None for size in resident_sizes.values()), (
        "rule 3 already ruled out any running job with an unmeasured "
        "model, so no resident key should be unknown here"
    )
    used = resident_bytes(running)

    admitted: list[int] = []
    occupied = len(running)

    for job in ordered:
        if occupied + len(admitted) >= max_concurrent:
            # Rule 8: cap reached -- and by rule 9, reaching the cap stops
            # the walk here rather than skipping this job and trying the
            # next (a later candidate fitting under the cap is exactly
            # the backfill this policy rejects).
            break

        reason = effectively_exclusive(job, budget_bytes)
        if reason is not None:
            # Rules 2/5/6: an effectively-exclusive candidate is only
            # ever admissible into a genuinely idle machine -- nothing
            # running, and nothing admitted yet this round. If idle, it
            # is admitted alone and the walk stops right after (nothing
            # else joins it this round); otherwise it -- and by rule 9,
            # everything behind it -- is blocked this round.
            if not running and not admitted:
                admitted.append(job.job_id)
            break

        job_keys, _ = _dedup_bytes(job.models)
        # `job` is not effectively exclusive here (ruled out above), so
        # every value in `job_keys` is a known int, never `None`.
        marginal = sum(
            max(0, size - resident_sizes.get(key, 0))
            for key, size in job_keys.items()
        )

        if used + marginal > budget_bytes:
            # Rule 7: doesn't fit in remaining budget -- rule 9 stops the
            # walk rather than trying a later candidate that might.
            # Deliberately `used + marginal > budget_bytes`, never a
            # `budget_bytes - used` subtraction -- see rule 7's
            # negative-headroom note in the module docstring.
            break

        admitted.append(job.job_id)
        for key, size in job_keys.items():
            resident_sizes[key] = max(resident_sizes.get(key, 0), size)
        used += marginal

    return admitted
