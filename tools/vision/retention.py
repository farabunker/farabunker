"""What `tools/vision` destroys, for a deleted conversation's images and
for one deleted generation on its own ticket.

TWO THINGS ARE REGISTERED HERE. `resolve_artifact_jobs` answers
`agents.contracts.artifacts.register_artifact_children`, a single slot,
because the agents column COMPUTES the references and exactly one tool
column knows what they mean -- the same shape
`agents.contracts.attachments.register_attachment_cleanup` already has.
It RESOLVES and destroys nothing: the job keys it answers are what
`agents.retention.conversation_children` turns into tickets of their
own, one per generation, rather than a silent destruction on the
conversation's own date. `purge_job` answers the `vision_job` kind's own
retention handler (`identity.contracts.cascades.register_retention_handler`,
`tools/vision/apps.py::VisionConfig.ready()`), for a generation deleted
on its own -- through the gallery, or through the child ticket a
conversation's delete wrote for it.

NO FUNCTION IN THIS MODULE QUERIES `GenerationJob.objects` ITSELF:
both resolve to job ids or keys and hand them to `tools.vision.services`
for the two unscoped reads a deletion needs (`services.existing_job_ids`,
`services.delete_jobs`) -- `foundation/ops/tests/
test_column_boundaries.py`'s IA-1 gate pins `visibility.py` and
`services.py` as the only two files in this column allowed to touch
that manager, and a third site here is exactly the drift it exists to
catch.

TWO CHANNELS REACH A CONVERSATION'S GENERATED IMAGES, and one of them
has a hole the other closes:

* ARTIFACT REFERENCES. `tools.vision.tools.run_generate` returns
  `artifacts = tuple(f"output:{output['id']}" ...)`, which
  `agents/runtime/loop.py` writes onto the tool turn verbatim.
  `output:<id>` is a `GeneratedOutput` pk and `input:<id>` a `JobInput`
  pk -- each ONE FK HOP from its job, and several outputs share one job,
  so the mapping DEDUPES BY JOB.
* GENERATION IDS. The same line of `agents/runtime/loop.py` writes
  `data=outcome.result.data`, which for this tool IS `job_json(job)`,
  whose first key is `"id"`. A job that reached the engine and FAILED
  mints no output at all, so the first channel finds nothing -- and
  `run_generate` reaches `job_json` on every terminal outcome, so this
  channel names the job anyway. A failed chat-created job IS caught.

WHAT IS NOT CAUGHT, named as accepted residue (spec section 10.7): a
generation whose tool turn was never written at all -- the job row was
created and the turn died before it (a crash, a kill, a cancelled
turn). It stays visible to its own owner in the gallery, where the
`vision_job` kind deletes it.

A SECOND, RELATED RESIDUE: a `vision_job` CHILD TICKET written while
the feature is on can outlive its own handler if the feature is turned
off before the ticket's purge date. `tools/vision/apps.py::ready()`
registers nothing at all -- not `resolve_artifact_jobs`, not this
module's own retention handler -- when the flag is off, so
`identity.cascades.run_retention("vision_job", key)` resolves an empty
handler list for a ticket that already exists. The sweep still writes
its `content.purged` event and deletes the ticket, with `removed={}`,
because no handler answered; the `GenerationJob` row survives, and it
becomes visible again in the gallery, because the exclusion in
`tools.vision.visibility` is gone with the handler that would have run.
Turning the feature back on re-registers the handler and the exclusion,
but does not retroactively purge what the flag-off window already
un-ticketed. A rare box-configuration window, not a code path any
handler here can close on its own -- named so a future reader does not
mistake a silently un-ticketed image for the box's promise kept.
"""
from __future__ import annotations

import logging
import uuid

from agents.contracts.artifacts import parse_artifact
from tools.vision import services
from tools.vision.models import GeneratedOutput, JobInput

logger = logging.getLogger(__name__)


def resolve_artifact_jobs(refs, generation_ids) -> list[tuple[str, str, str]]:
    """The generation jobs these references and ids name, as `(job_id,
    owner_kind, owner_key)` triples, deduped by job.

    RESOLVES; DESTROYS NOTHING. The conversation that owns these
    references hands them over so each job can be given a DELETION OF
    ITS OWN -- its own ticket, its own date on the Deleted page, its
    own restore -- rather than being destroyed silently on somebody
    else's date. `purge_job` below is what finally removes one, on the
    date that job's own ticket printed.

    `job_id` A STRING, NOT A UUID: a ticket key is text, and the caller
    is building `(kind, key, owner_kind, owner_key)` quadruples for a
    registry, not a queryset. THE OWNER RIDES ALONG SO `identity.
    retention.delete_content` CAN STAMP THE CHILD TICKET FROM THE
    IMAGE'S OWN OWNER, never the conversation's -- a share that let a
    second principal post and generate inside somebody else's
    conversation, or an administrator's duplicate, means the two can
    differ, and the ticket's owner columns are what every downstream
    surface (`visible_tickets`, `may_purge`) keys off.

    ONLY JOBS THAT STILL EXIST. A conversation's stored generation ids
    outlive the jobs they name -- the turn keeps the id after the
    picture was deleted from the gallery -- and every key this answers
    becomes a TICKET. A ticket for a job nobody has would be a
    "Generated image" row on the Deleted page with a date and a Restore
    button, naming a picture nobody can restore and nothing will ever
    destroy. So both channels' ids go through
    `services.existing_job_ids` once, together.

    With two empty lists this answers `[]` having run no query at all
    -- every conversation delete on a box with this column installed
    reaches this function, and most conversations have no images.

    An unparseable reference or generation id is DROPPED and logged
    WITHOUT its raw value: this runs inside a deletion, and a deletion
    must not write what it is destroying somewhere new.
    """
    job_ids: set = set()

    output_pks: list[int] = []
    input_pks: list[int] = []
    for reference in refs or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            logger.warning(
                "tools.vision.retention: one artifact reference failed to parse; ignored.")
            continue
        if kind == "output":
            output_pks.append(pk)
        elif kind == "input":
            input_pks.append(pk)

    if output_pks:
        job_ids.update(GeneratedOutput.objects.filter(pk__in=output_pks)
                       .values_list("job_id", flat=True))
    if input_pks:
        job_ids.update(JobInput.objects.filter(pk__in=input_pks)
                       .values_list("job_id", flat=True))
    for raw in generation_ids or ():
        try:
            job_ids.add(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                "tools.vision.retention: one generation id failed to parse; ignored.")

    return sorted(services.existing_job_ids(job_ids))


def resolve_artifact_job_ids(refs) -> dict[str, str]:
    """EVERY one of `refs` that still names a job, mapped to that job's
    id -- a string, `identity.retention.content_status`'s own key shape.

    RENDER TIME'S OWN QUESTION, a sibling to `resolve_artifact_jobs`
    above rather than a replacement for it: that function answers
    "which GENERATIONS does a conversation's DELETE reach" (deduped,
    carrying owner columns, one call per delete, for `agents.retention.
    conversation_children`). This one answers "which job does EACH
    reference name", so `agents.chat.rendering` can resolve every image
    reference a whole PAGE is about to render in one call instead of
    one per turn -- the N+1 the placeholder wave (2026-09-29) left
    behind: `_placeholder_for` used to call `resolve_artifact_jobs`
    and then `identity.retention.content_status` once per image-bearing
    turn, and TWICE for a TOOL turn (`agents.chat.rendering.turn_card`'s
    own call and `tool_card`'s second one over the same artifacts).

    NO SECOND QUERY TO CONFIRM THE JOB ITSELF STILL EXISTS, unlike
    `resolve_artifact_jobs`'s own `services.existing_job_ids` call:
    `GeneratedOutput.job` is `on_delete=CASCADE` from `GenerationJob`
    (`tools/vision/models.py`), so a `GeneratedOutput`/`JobInput` row
    found here at all already proves its job survives -- the extra
    check on the sibling function's own OTHER channel (generation ids
    read straight off `Turn.data`, no FK behind them) is not something
    this function is ever asked about: the render path only ever holds
    artifact reference strings, never bare generation ids.

    A reference that fails to parse, or whose row is already gone, is
    simply ABSENT from the answer -- the same "no job for this
    reference" fact an empty result already meant to every caller of
    `resolve_artifact_jobs`.

    AT MOST ONE QUERY PER KIND ACTUALLY PRESENT in `refs` (never one per
    reference), and none at all for an empty `refs`.
    """
    output_pks: dict[int, str] = {}
    input_pks: dict[int, str] = {}
    for reference in refs or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            logger.info(
                "tools.vision.retention: one artifact reference failed to parse; ignored.")
            continue
        if kind == "output":
            output_pks[pk] = reference
        elif kind == "input":
            input_pks[pk] = reference

    job_of: dict[str, str] = {}
    if output_pks:
        for pk, job_id in GeneratedOutput.objects.filter(
                pk__in=output_pks).values_list("pk", "job_id"):
            job_of[output_pks[pk]] = str(job_id)
    if input_pks:
        for pk, job_id in JobInput.objects.filter(
                pk__in=input_pks, job__isnull=False).values_list("pk", "job_id"):
            job_of[input_pks[pk]] = str(job_id)
    return job_of


def purge_job(key: str) -> int:
    """Destroy ONE generation on its own ticket's date: the row, its
    `JobInput`/`GeneratedOutput` children by CASCADE, its managed
    directory, and its best-effort engine-side sweep. Returns 1 or 0.

    THROUGH `services.delete_jobs`, never `GenerationJob.objects`: only
    `visibility.py` and `services.py` may query that manager in this
    column, and a purge must reach a job nobody may currently see,
    which is not a visibility question at all.

    Idempotent: a job already gone, or a key that is not a UUID at all,
    answers 0 rather than raising -- the ticket may outlive a row an
    older path removed.

    FILES band: it removes bytes.
    """
    try:
        job_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0
    return services.delete_jobs([job_id])
