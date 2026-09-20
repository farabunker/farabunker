"""
Generation job records (spec §4.7, D6).

Deliberately MEDIA-GENERIC: a job records what was asked, which model
answered, the exact payload the engine received, and where the results
landed. Video or audio later are new `media_type` values on the same
tables, not new tables.

Every result is reproducible: `params` (validated, seed resolved),
`model_fingerprint` + `model_config` (the binding as it was AT SUBMIT
TIME, not as it is now), and `engine_payload` (the verbatim submission).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

from foundation.format import format_timecode
from models.contracts.operations import get_operation
from models.contracts.queue import QueueUnavailable, get_job


def _fact_value(value) -> str:
    """One param value as the card shows it.

    A list (an asset param's selection) reads as a list, not as a Python
    repr: `str(["a", "b"])` on a page is a bug the operator has to decode.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _seconds(start, end) -> float | None:
    """Elapsed seconds between two timestamps, or `None` when either is
    missing -- a duration with an unknown end is not a duration."""
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


class GenerationJob(models.Model):
    """One generation request and its lifecycle.

    The primary key is a UUID because it is also the engine-side filename
    prefix (`GenerationRequest.client_ref`) and the job directory name under
    `GENERATED_DIR` -- a sequential integer would leak ordering into the
    engine's own output folder and collide across machines.

    There is no "lost" status: an engine that no longer knows a job leaves
    the record `failed` with an explaining message, because from the
    operator's side the job did not produce anything.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    class FailureKind(models.TextChoices):
        """WHY a generation failed, as a value rather than a sentence.

        `error` is the operator's copy; this is the caller's. An agent
        needs to tell "fix your parameters and resubmit" from "the engine
        is down, try later" without pattern-matching prose that is allowed
        to change.

        Four of these are carried by an EXCEPTION rather than by a row,
        because they happen before a row exists -- `submit_job` refuses to
        write an orphan job for an unbound role or an invalid submission:
        `PARAMS_INVALID` is what a `models.contracts.operations.ParamError`
        means, and `ROLE_UNBOUND`/`ENGINE_UNREACHABLE`/
        `CONNECTION_UNAVAILABLE` are what `services.VisionUnavailable.
        failure_kind` reports. One vocabulary, two carriers -- so a caller
        reading either surface reads the same seven values.

        `CONNECTION_UNAVAILABLE` (final-review finding 4) is distinct from
        `ROLE_UNBOUND`: a payload that names a `connection` pk resolves
        that pk directly (`jobs._resolve_model`) and never consults the
        role at all, so a pk that no longer names a usable
        image-generation connection is never honestly "no model
        assigned" -- the role may well be bound, or may even be the exact
        connection the pk used to name before it was edited or removed.
        """

        PARAMS_INVALID = "params_invalid", "Parameters invalid"
        ROLE_UNBOUND = "role_unbound", "No image model assigned"
        CONNECTION_UNAVAILABLE = "connection_unavailable", "Picked model no longer available"
        ENGINE_UNREACHABLE = "engine_unreachable", "Image engine unreachable"
        ENGINE_REJECTED = "engine_rejected", "Image engine refused the job"
        ENGINE_FAILED = "engine_failed", "Image engine failed the job"
        LOST = "lost", "Image engine forgot the job"

    TERMINAL_STATUSES = (Status.DONE, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.CharField(max_length=64)
    # `validate_params` output -- coerced, range-checked, seed resolved.
    params = models.JSONField(default=dict)
    # The resolved seed, duplicated out of `params` because it is the one
    # value an operator reuses by hand ("same seed, one more step"). NULL
    # for an operation that has no seed at all -- upscaling is
    # deterministic, and recording a random number it never used would be
    # a fact that is not true.
    seed = models.BigIntegerField(null=True, blank=True)
    engine = models.CharField(max_length=64)
    model_id = models.CharField(max_length=255)
    # The address the job was actually submitted to, recorded beside the
    # engine and model so the D6 snapshot is COMPLETE: `refresh_job` polls
    # this, not whatever the role points at now. Blank on rows written
    # before this column existed -- those fall back to the role binding.
    endpoint = models.CharField(max_length=512, blank=True)
    # `ResolvedModel.fingerprint` at submit time -- rebinding the role later
    # must not rewrite history.
    model_fingerprint = models.CharField(max_length=255)
    model_config = models.JSONField(default=dict, blank=True)
    # The engine's own job handle (ComfyUI: prompt_id). Blank until accepted.
    engine_ref = models.CharField(max_length=255, blank=True)
    # The EXACT body the engine received (D6): reproducible and exportable.
    engine_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    # The machine-readable half of `error` -- see `FailureKind`. Blank on
    # every job that has not failed, which is the honest reading: a job
    # that succeeded has no failure kind, and `""` is not a member of the
    # vocabulary.
    failure_kind = models.CharField(
        max_length=32, blank=True, default="", choices=FailureKind.choices
    )
    # The execution-queue job (`models.queue.models.InferenceJob`) that
    # submitted this generation, or NULL for one submitted directly
    # (a management command, a test, a tool calling `services.submit_job`
    # itself). NOT a foreign key: the queue's tables live in `models/queue/`
    # and `tools/*` may not import `models.queue`/`models.registry` -- it
    # is the same snapshot-by-id relationship `InferenceJob.model_refs` already uses
    # in the other direction. Indexed because the page's queued-card poll
    # looks a generation up by it on every tick.
    queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    # WHO this row belongs to, stamped at create from
    # `identity.access.owner_fields(principal)`. Same two columns, same
    # widths and same blank default as `agents/models.py`'s -- one
    # definition of ownership across five tables in three columns, not
    # three that agree by convention.
    #
    # Existing rows get `""`, which means "written before accounts
    # existed" and is exactly what `manage.py adopt_open_rows` claims.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        # B8: every surface that lists jobs sorts on `-created_at` (the
        # create page's recent cards, the gallery's `-job__created_at`
        # join) and the queue-facing views filter on `status`. Fine at
        # hundreds of rows; batch generation and upscaling grow this table
        # fast.
        indexes = [
            models.Index(fields=["-created_at"], name="vision_job_created_idx"),
            models.Index(fields=["status"], name="vision_job_status_idx"),
            models.Index(fields=["owner_kind", "owner_key"], name="vision_job_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.operation} {self.id}"

    @property
    def is_terminal(self) -> bool:
        """True once the job can no longer change without a resubmit."""
        return self.status in self.TERMINAL_STATUSES

    @property
    def is_stale(self) -> bool:
        """True when a non-terminal job has been waiting longer than
        `settings.VISION_STALE_AFTER` -- a HINT for the card ("check that
        the image engine is running"), never a state change: it is still
        polled."""
        if self.is_terminal or self.created_at is None:
            return False
        return timezone.now() - self.created_at > settings.VISION_STALE_AFTER

    @cached_property
    def _submitted_at(self):
        """When the PLATFORM enqueued this generation -- the `InferenceJob`
        row's own `created_at`, for the queue job named by `queue_job_id`
        -- or `None` when that fact is not knowable (fix round 2026-08-25,
        ADR 0012 D-EDIT-11 amended).

        Read ONLY through the sanctioned `models.contracts.queue.get_job`
        seam (the same one `tools.vision.views` already calls for the
        SAME queue job) -- never a `models.queue`/`models.registry` import
        directly: `tools/*` may not import either (see `queue_job_id`'s own docstring,
        above). `None` for three genuinely different, equally honest,
        reasons this module does not distinguish further: a job submitted
        directly with no queue job at all (a management command, a test, a
        future tool calling `services.submit_job` itself), a queue row
        that has aged out of the queue's own retention limit, or the queue
        being unreachable right this moment (`QueueUnavailable`) -- a
        transient read failure here must never turn a job card into a 500
        (never-500).

        `cached_property`, not `property`: this fact, once knowable, is
        FIXED forever (both timestamps it is built from are already
        written by the time this row exists at all), and the same
        request/render commonly reads `durations` and then
        `durations_display` -- memoizing here is what keeps that to one
        queue lookup rather than two.
        """
        if not self.queue_job_id:
            return None
        try:
            job_status = get_job(self.queue_job_id)
        except QueueUnavailable:
            return None
        return getattr(job_status, "created_at", None) if job_status is not None else None

    @property
    def durations(self) -> dict[str, float | None]:
        """How long this generation waited to be picked up by the platform,
        how long it then waited for the ENGINE, how long it ran, and how
        long the whole thing took -- in SECONDS, `None` where the answer is
        genuinely unknown (owner requirement 2026-08-25, ADR 0012
        D-EDIT-11).

        DERIVED from timestamps this row (and, when knowable, the queue job
        that submitted it) already carry: a stored duration would be a
        third copy that can disagree with them. A non-terminal job's
        numbers are LIVE (they count against `now`), which is what makes
        the card's existing poll show a moving clock without a new
        endpoint.

        `processing` is `None` -- never `0` -- for a job that never started:
        a submission the engine refused waited and then failed, and "it ran
        for no time" is a different claim from "it never ran". `submitted`
        is `None` for the same reason whenever `_submitted_at` cannot say --
        never a made-up `0`.

        `queued` is the ENGINE's OWN wait, and ONLY that (fix round
        2026-08-25 corrects an earlier claim that it also covered the
        platform's queue): `created_at` on THIS row is stamped inside
        `services.submit_job`, which the worker calls only AFTER it has
        already claimed the platform's queue job -- so the platform-side
        wait (enqueue to claim) never lands inside `created_at` at all.
        `submitted` is that platform wait, `InferenceJob.created_at` (via
        `_submitted_at`) to THIS row's own `created_at` -- a fixed,
        historical number once this row exists, never live, because both
        ends are already stamped. `total` starts from `_submitted_at` when
        it is known (the honest end-to-end figure the owner asked for) and
        falls back to `created_at` -- exactly as before this fix round --
        when it is not.

        The run INCLUDES loading the weights: `started_at` is stamped when
        ComfyUI reports the prompt executing, and no HTTP surface it serves
        separates the load from the first sampler step (D-EDIT-11). A job
        that finished between two polls was never observed running and
        back-fills its start from its creation (`services.refresh_job`), so
        it honestly reports no engine-side wait.
        """
        now = timezone.now()
        # ONE end for the whole row, computed once: the stamp when there is
        # one, `now` while the job can still change, and `None` for a
        # terminal job that somehow never stamped one -- a duration with no
        # end is not a duration. Recomputing it per key is how two of these
        # numbers start disagreeing.
        end = self.finished_at or (None if self.is_terminal else now)
        started = self.started_at
        submitted_at = self._submitted_at
        return {
            "submitted": _seconds(submitted_at, self.created_at),
            "queued": _seconds(self.created_at, started or end),
            "processing": None if started is None else _seconds(started, end),
            "total": _seconds(submitted_at, end) if submitted_at is not None else _seconds(self.created_at, end),
        }

    @property
    def durations_display(self) -> dict[str, str]:
        """`durations`, rendered with the codebase's ONE duration
        formatter (`foundation.format.format_timecode`) -- which answers `""` for
        an unknown value, so a template shows nothing rather than a
        made-up `0:00`. Two properties, one truth: the numbers are the
        API's, the strings are the page's, and neither recomputes the
        other's arithmetic."""
        return {key: format_timecode(value) for key, value in self.durations.items()}

    @staticmethod
    def headline_text(operation_key: str, params: dict) -> str:
        """The one-line summary for an operation key + its params: the
        prompt or instruction when either is present, else the operation's
        own schema label -- never a blank heading for an operation
        (img2img's `denoise`-only variants, an upscale) that carries no
        `prompt`/`instruction` param at all.

        `instruction` (owner ruling 2026-08-24(b)) is `edit`'s own
        equivalent of `prompt` -- its schema has no `prompt` param at all,
        so before this an `edit` job fell all the way through to the
        operation's own schema label ("Edit an image") instead of naming
        what the job actually did. One function, no per-operation branch:
        every OTHER registered operation's params carry neither key, so
        this costs them nothing.

        A `staticmethod`, not a bound property, because `tools.vision.
        jobs.summarize_generate` needs the exact same logic for a QUEUED
        job's payload (`{"operation": str, "params": dict}`) -- before any
        `GenerationJob` row exists to read `.headline` off of -- and a
        second copy of this fallback chain would be a second place to keep
        the two in sync (final-review finding 6). `headline` (below) is
        this function called on `self`.

        Falls back to the operation's raw stored key when it is no longer
        registered (its feature was switched off, or it was renamed) --
        the same honest degradation `facts` uses, rather than a made-up
        label.
        """
        text = (params or {}).get("prompt") or (params or {}).get("instruction")
        if text:
            return text
        operation = get_operation(operation_key)
        return operation.label if operation is not None else operation_key

    @property
    def headline(self) -> str:
        """The one line that names this job on a card or in a gallery
        caption -- `headline_text` for THIS job's own operation/params.
        Reads the same registered-operation lookup `facts` does, so it
        degrades the same honest way."""
        return self.headline_text(self.operation, self.params)

    @property
    def facts(self) -> list[tuple[str, str]]:
        """This job's parameters as `(label, value)` pairs, read from its
        OPERATION's schema rather than from a txt2img-shaped literal.

        The job card and the gallery caption both render this, so an
        operation the templates have never seen (img2img's `denoise`, its
        `init_image`) describes itself with no template edit -- which is
        exactly what ADR 0012's Consequences promise, and the surface that
        did not yet keep it.

        `seed` leads because it is the one value an operator reuses by hand;
        the model trails because it answers "what produced this". `text`
        params are skipped: the prompt is already the card's heading and the
        gallery's caption headline, and repeating it here is noise, not
        information. A param the job never carried is skipped rather than
        rendered blank.

        A job whose operation is no longer registered (its feature was
        switched off, or the operation was renamed) falls back to its raw
        stored keys: showing the operator exactly what was recorded beats
        showing nothing.

        A property, not a free function and not a template tag: every
        surface that renders a job (the inline card, the standalone fragment
        `job_status` returns, the gallery caption) reaches it the same way,
        with no tag library and no per-view plumbing.
        """
        params = self.params or {}
        facts: list[tuple[str, str]] = []
        if self.seed is not None:
            facts.append(("Seed", str(self.seed)))
        operation = get_operation(self.operation)
        if operation is None:
            facts += [
                (key, _fact_value(value))
                for key, value in sorted(params.items())
                if key != "seed" and value is not None and value != "" and value != []
            ]
        else:
            for param in operation.params:
                if param.kind in ("seed", "text"):
                    continue
                value = params.get(param.key)
                if value is None or value == "" or value == []:
                    continue
                facts.append((param.label, _fact_value(value)))
        facts.append(("Model", self.model_id))
        return facts


class JobInput(models.Model):
    """A file parameter's stored input (img2img's init image, an inpaint
    mask). Empty for txt2img; the table exists so file params are a data
    addition later, not a schema change.

    `job` is NULLABLE, and a row with no job is a STAGED upload: a file
    the page recorded before the generation that will consume it exists.
    A queue payload is JSON and cannot carry an upload, so the page writes
    the bytes into the managed store, records them here, and puts the
    ordinary `input:<id>` reference in the payload -- the same reference
    kind, the same `services.stored_input` resolver, and the same
    `/vision/inputs/<id>/file/` view a job's own input uses. When the
    worker's `submit_job` runs, it copies those bytes into the job's own
    directory and writes the job's own row, exactly as it does for a
    browser upload; the staged row is then disposable and is swept by
    `services.prune_staged_inputs` after `settings.VISION_STAGED_UPLOAD_TTL`.
    """

    job = models.ForeignKey(
        GenerationJob, on_delete=models.CASCADE, related_name="inputs",
        null=True, blank=True,
    )
    param_key = models.CharField(max_length=64)
    path = models.CharField(max_length=1024)
    media_type = models.CharField(max_length=128, blank=True)
    # Nullable `auto_now_add`: rows written before this column existed
    # take NULL, and NULL never matches the staged-upload sweep's cutoff
    # -- which is right, since every one of them is attached to a job.
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["param_key"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.param_key} of {self.job_id}"


class GeneratedOutput(models.Model):
    """One produced file, copied into the managed store (spec §5).

    `width`/`height` are read from the file header when it is a format we
    can measure without a decoder dependency, and stay `None` otherwise --
    an unknown size is shown as unknown, never guessed.
    """

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name="outputs")
    index = models.PositiveIntegerField(default=0)
    path = models.CharField(max_length=1024)
    media_type = models.CharField(max_length=128, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"output {self.index} of {self.job_id}"
