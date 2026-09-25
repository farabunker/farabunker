"""
Django models for the execution queue's storage (ADR 0013):
`InferenceJob` (one row per enqueued unit of work) and `JobSettings` (the
operator-editable queue-wide singleton).

`models/queue/backend.py` -- the module `models/contracts/queue.py` dispatches
to via `settings.INFERENCE_QUEUE_BACKEND` -- is the only code that writes
these tables at runtime; this module stays plain Django models, same
division as `tools/rag/models.py` alongside `tools/rag/services.py`.
"""
from __future__ import annotations

from django.db import models

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

STATE_CHOICES = [
    (QUEUED, "Queued"),
    (RUNNING, "Running"),
    (SUCCEEDED, "Succeeded"),
    (FAILED, "Failed"),
    (CANCELLED, "Cancelled"),
]

# A job in any of these states is done and will never be claimed, run, or
# cancelled again -- the set `_prune_finished_jobs` (backend.py) scopes
# deletes to, and the complement of what the claim scan / orphan sweep
# (`models.queue.claim.claim_and_admit`/`_sweep_orphans`) ever touch.
TERMINAL_STATES = (SUCCEEDED, FAILED, CANCELLED)


class InferenceJob(models.Model):
    """One row per unit of work enqueued against the execution queue
    (ADR 0013) -- created by `models.queue.backend.enqueue`, claimed
    and run by `models.queue.worker.Worker`, read back through `get_job`/
    the Queue page (`models.queue.views.QueueView`).

    `kind` names a `models.contracts.jobkinds.JobKind` by key -- not a FK,
    since the registry is an in-memory, code-defined dict (`models.contracts.
    jobkinds._JOB_KINDS`), not a DB table there is anything to reference.

    `priority` is resolved once, at enqueue time, via the priority chain
    (explicit arg -> job kind's `default_priority` -> `JobSettings.
    get_solo().default_priority`) -- see `backend.enqueue`. Lower runs
    first; ties break by `id` (insertion order), matching `Meta.ordering`.

    `claimed_by` is a free-text worker identifier, kept solely so the
    orphan sweep (`models.queue.claim._sweep_orphans`) and drain log lines
    (`models.queue.worker.Worker._drain_inflight`) can say *which* worker
    a stuck/abandoned job was last seen under -- it is never read back to
    decide anything about the job itself (that's `claim_token` below).

    `claim_token` is the actual claim mechanism (a fresh UUID stamped by
    the conditional UPDATE `models.queue.claim.claim_and_admit` uses to
    claim a queued row) -- `claimed_by` is bookkeeping alongside it, not a
    substitute for it.

    `model_refs` is the admission snapshot: one entry per model this job
    holds resident while running, as plain dicts -- `{"role", "engine",
    "endpoint", "model_id", "connection_name", "footprint_bytes",
    "synchronous"}` (a row written before `synchronous` existed simply
    has no such key, and every reader defaults it to `True`) --
    copied at claim time from the enqueue-time `ModelRef`s (`core.
    inference.jobkinds.ModelRef`), never a FK to `models.registry.models.
    ModelConnection`. Same two reasons `tools.rag.models.AskRecord`
    already snapshots instead of referencing: a job row must not import
    across the module/trust boundary the FK would cross, and it must stay
    truthful about what actually ran even after the connection behind it
    is renamed or deleted -- a live FK would silently reread today's name
    onto yesterday's job. Named `model_refs`, not `models`, so the field
    never shadows this module's own `from django.db import models` import
    (no other file in this codebase gives a field that kind of name).

    `exclusive` stores ONLY the planner's own declaration (the `bool` half
    of `ModelRef`'s `(model_refs, exclusive)` planner return) -- never the
    scheduler's own reasons for running a job alone (unknown footprint,
    oversize relative to the memory budget). Those are computed fresh at
    admission time by `models.queue.scheduler.plan_admissions` and never
    written back here: one fact, one home, so this column can't drift out
    of sync with the admission logic that actually decided.

    `footprint_bytes` is a derived `@property`, not a column -- see below.
    """

    kind = models.CharField(max_length=64, db_index=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=QUEUED)
    priority = models.PositiveIntegerField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_by = models.CharField(max_length=64, blank=True, default="")
    claim_token = models.UUIDField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    model_refs = models.JSONField(default=list)
    exclusive = models.BooleanField(default=False)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    # `progress` and `checkpoint` (T3) are TWO SEPARATE columns,
    # deliberately -- a throttled progress DISPLAY write
    # (`models.queue.worker`'s token-conditional UPDATE, throttled by
    # `PROGRESS_INTERVAL_SECONDS`) must never share a column with resume
    # STATE a handler wrote at a checkpoint boundary; one UPDATE that
    # touches only `progress` can never clobber a `checkpoint` a moment
    # before or after it, because they are two independent columns, not
    # two keys folded into one JSON blob a display write would have to
    # read-modify-write around.
    #
    # `progress` is display-only, kind-owned: `{"done": num,
    # "total": num | None, "unit": "seconds"|"pages"|"items", "label":
    # str}`. `total=None` must stay expressible -- a job that genuinely
    # doesn't know its own total yet (a page count not discovered until
    # partway through, say) reports that honestly rather than guessing:
    # `models.contracts.engines.base.JobStatus.progress`'s own docstring
    # already states the same rule for an engine's reported progress ("a
    # fabricated progress bar is worse than none").
    progress = models.JSONField(null=True, blank=True)
    # `checkpoint` is execution state, kind-owned and OPAQUE to this
    # model, the worker, and the claim/scheduler code -- none of them
    # ever read or interpret its shape, only pass it through. Written by
    # a handler's own `ctx.checkpoint(state)` call
    # (`models.contracts.jobkinds.JobContext`) at a natural resume
    # boundary, read back as `checkpoint_state` on the NEXT attempt
    # (`models.queue.worker.Worker._execute` reads it off the claim
    # descriptor -- see `models.queue.claim.claim_and_admit`). A requeue
    # (voluntary drain, orphan sweep) deliberately leaves both this
    # column and `progress` untouched -- their PRESERVATION across a
    # requeue is the resume mechanism itself, not an oversight.
    checkpoint = models.JSONField(null=True, blank=True)

    # THE HOLD-OFF (spec §3.3d). A job the queue has DELIBERATELY declined
    # to consider until this moment -- written by the worker when an
    # exclusive launch is refused, either because a protected endpoint is
    # still busy or because the barrier got an informative refusal. The
    # claim's candidate query honours it (`not_before IS NULL OR
    # not_before <= now`), which makes it the ONE new admission-side
    # filter this track adds.
    #
    # DURABLE rather than in-process, deliberately: the restart that would
    # clear an in-memory hold-off is the same restart that clears the
    # barrier's refusal count, and the two together would drop a freshly
    # restarted worker straight back into 0.5s-tick churn against an
    # endpoint that is still holding memory.
    #
    # The deadlock proof survives because the exclusion is time-bounded and
    # SELF-CLEARING: the job returns to its own head position the moment
    # the hold-off expires, and an effectively-exclusive head is still
    # admitted alone the instant the machine is idle.
    not_before = models.DateTimeField(null=True, blank=True)

    # HOW MANY TIMES MODEL-AFFINITY REORDERING HAS PUT A LATER PEER AHEAD
    # OF THIS JOB (spec §3.6). Durable, not in-memory state: a worker
    # restart must not reset a job's age and let it be passed over for
    # ever. At `models.queue.scheduler.MAX_PASSOVERS` the job is PINNED --
    # it sorts by id ahead of every UNPINNED peer at its priority from
    # then on and is never reordered behind one again. Not ahead of every
    # peer: pinning sets the second element of the sort key, so the
    # affinity term still discriminates among pinned candidates.
    passed_over = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["priority", "id"]
        indexes = [
            # The claim scan (`models.queue.claim.claim_and_admit`): find
            # the next queued job in priority order without a table scan.
            models.Index(fields=["state", "priority", "id"], name="jobs_claim_scan"),
            # The orphan sweep (`models.queue.claim._sweep_orphans`): find
            # running jobs whose heartbeat has gone stale, without scanning
            # every state.
            models.Index(fields=["state", "heartbeat_at"], name="jobs_orphan_sweep"),
            models.Index(fields=["state", "not_before", "priority", "id"],
                         name="jobs_claim_scan_holdoff"),
        ]

    @property
    def footprint_bytes(self) -> int | None:
        """Sum of `model_refs` entries' `footprint_bytes`, or `None` for an
        *unsized* job -- no model entries at all, or any single entry whose
        `footprint_bytes` is itself unknown (`None`, the planner's own "not
        sized yet" value -- see `models.contracts.jobkinds.ModelRef`). A
        partial sum would understate real memory pressure, so this
        deliberately refuses to guess: `models.queue.scheduler.
        plan_admissions` treats `None` as "cannot reason about this job's
        footprint", not zero.
        """
        entries = self.model_refs
        if not entries:
            return None
        total = 0
        for entry in entries:
            size = entry.get("footprint_bytes")
            if size is None:
                return None
            total += size
        return total

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"InferenceJob({self.kind}#{self.pk}, {self.state})"


class JobSettings(models.Model):
    """A single, always-present row of operator-editable queue-wide
    settings -- the queue's twin of `tools.rag.models.RagSettings`, same
    "one row, `pk=1`, get-or-create it" singleton shape (see that model's
    docstring for the pattern's rationale).
    """

    MAX_CONCURRENT_JOBS_DEFAULT = 4
    DEFAULT_PRIORITY_DEFAULT = 100
    RETENTION_LIMIT_DEFAULT = 100

    # The one-timeout task (2026-09-17, ADR 0013's dated amendment).
    # RAISED from the ollama engine adapter's own hidden
    # `DEFAULT_REQUEST_TIMEOUT` (300s, `models.contracts.engines.ollama`)
    # -- the incident this field exists to fix: that inner, invisible
    # timeout fired before a turn's own 900s deadline
    # (`agents.limits.TURN_DEADLINE_SECONDS`) ever got a chance to,
    # whenever the chat model reloaded under memory pressure from an
    # image-generation job. 1800s (30 minutes) is a generous default for
    # a slow local reload plus a real generation. Bounds (60..7200s, 1
    # minute..2 hours) are enforced in the write path
    # (`models.queue.views._update_response_timeout`), the same "range
    # limits belong to the field's own writer" convention `foundation.
    # settings_bounds` documents -- not a DB constraint.
    RESPONSE_TIMEOUT_SECONDS_DEFAULT = 1800
    RESPONSE_TIMEOUT_SECONDS_MIN = 60
    RESPONSE_TIMEOUT_SECONDS_MAX = 7200

    # Null = no budget = strictly sequential (`models.queue.scheduler.
    # plan_admissions` runs one job at a time when it has no memory
    # ceiling to reason against). A fact about the operator's own
    # hardware -- there is no platform-wide number that could be right
    # for every box, so this ships
    # null rather than guessed, matching the "no model defaults" ruling
    # (ADR 0010 third amendment) for the same reason: an unset value here is
    # honestly UNKNOWN, not a silently-assumed capacity.
    memory_budget_bytes = models.BigIntegerField(null=True, blank=True)
    max_concurrent_jobs = models.PositiveIntegerField(default=MAX_CONCURRENT_JOBS_DEFAULT)
    default_priority = models.PositiveIntegerField(default=DEFAULT_PRIORITY_DEFAULT)
    retention_limit = models.PositiveIntegerField(default=RETENTION_LIMIT_DEFAULT)

    # C-7 (round-3 hardening, H39). Null = no cap -- the same "honestly
    # unknown, not silently assumed" convention `memory_budget_bytes`
    # documents above, applied to a different fact: there is no
    # platform-wide count of queued-or-running jobs that is right for
    # every box's member count, so this ships null rather than guessed.
    # Checked by `models.queue.backend.enqueue` against QUEUED-or-RUNNING
    # rows whose payload's actor matches the enqueuing payload's own
    # (`identity.contracts.principals.payload_fields`/`principal_from_
    # payload`) -- never against a `SERVICE_PRINCIPAL` actor, which is
    # the watcher, the rematerialize callback and the shell commands'
    # own shared identity: the operator's own work, not a principal's,
    # and never capped.
    max_queued_per_principal = models.PositiveIntegerField(null=True, blank=True)

    # ONE FIRING AUTHORITY for a chat/agent turn's wall clock (the
    # one-timeout task). Threaded to `agents.runtime.loop` via
    # `models.contracts.jobkinds.JobContext.response_timeout_seconds`
    # (`models.queue.worker.Worker._build_job_context` stamps it here,
    # since `agents/` may not import this table -- import law) and used
    # BOTH to build the turn's own step-budget deadline and to set the
    # chat engine's own request timeout (`models.contracts.gateway.
    # get_llm_for`) TO that same value, never lower -- so the loop's own
    # deadline check is the only timeout that ever fires, and the
    # engine's own hidden default (`models.contracts.engines.ollama.
    # DEFAULT_REQUEST_TIMEOUT`, 300s) no longer competes with it.
    response_timeout_seconds = models.PositiveIntegerField(
        default=RESPONSE_TIMEOUT_SECONDS_DEFAULT,
    )

    # PER-KIND WAIT CEILINGS (spec §3.5a), `{job kind key: seconds}`. The
    # OPERATOR-editable half of a kind's wait ceiling; the code-declared
    # default lives on `models.contracts.jobkinds.JobKind.
    # default_wait_seconds`. Rides on THIS row, which
    # `models.queue.worker.Worker._build_job_context` already fetches for
    # `response_timeout_seconds` -- a second `get_solo()` would be a
    # query-count regression and is explicitly not how this is read.
    kind_wait_seconds = models.JSONField(default=dict, blank=True)

    # WHAT THE WORKER PROCESS MEASURED, ONCE, AT BOOT (spec §3.7). The
    # console renders in the WEB service and the budget governs the WORKER
    # service -- separate containers -- so memory detected in the web
    # process describes the wrong machine. Written by the worker, rendered
    # on the settings page labelled with the process that measured it and
    # the date. NOTHING IS EVER APPLIED ON THE OPERATOR'S BEHALF: the
    # container sees the VM's allocation rather than the host's, and a
    # silently derived budget would be authoritative and wrong.
    detected_memory_bytes = models.BigIntegerField(null=True, blank=True)
    detected_memory_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get_solo(cls) -> "JobSettings":
        """The one `JobSettings` row (`pk=1`), creating it with defaults on
        first use. Never raises `DoesNotExist` -- callers never need their
        own get-or-create dance (mirrors `RagSettings.get_solo` exactly)."""
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "memory_budget_bytes": None,
                "max_concurrent_jobs": cls.MAX_CONCURRENT_JOBS_DEFAULT,
                "default_priority": cls.DEFAULT_PRIORITY_DEFAULT,
                "retention_limit": cls.RETENTION_LIMIT_DEFAULT,
                "max_queued_per_principal": None,
                "response_timeout_seconds": cls.RESPONSE_TIMEOUT_SECONDS_DEFAULT,
                "kind_wait_seconds": {},
                "detected_memory_bytes": None,
                "detected_memory_at": None,
            },
        )
        return obj

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"JobSettings(max_concurrent_jobs={self.max_concurrent_jobs})"
