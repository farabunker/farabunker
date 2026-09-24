"""What `tools/vision` destroys for a deleted conversation.

REGISTERED THROUGH `agents.contracts.artifacts.register_artifact_purge`,
a single slot, because the agents column COMPUTES the references and
exactly one tool column knows what they mean -- the same shape
`agents.contracts.attachments.register_attachment_cleanup` already has.

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
"""
from __future__ import annotations

import logging
import uuid

from agents.contracts.artifacts import parse_artifact
from tools.vision import services
from tools.vision.models import GeneratedOutput, JobInput

logger = logging.getLogger(__name__)


def resolve_artifact_jobs(refs, generation_ids) -> list[str]:
    """The generation jobs these references and ids name, as primary
    keys in string form, deduped.

    RESOLVES; DESTROYS NOTHING. The conversation that owns these
    references hands them over so each job can be given a DELETION OF
    ITS OWN -- its own ticket, its own date on the Deleted page, its
    own restore -- rather than being destroyed silently on somebody
    else's date. `purge_job` below is what finally removes one, on the
    date that job's own ticket printed.

    Strings, not UUIDs: a ticket key is text, and the caller is
    building `(kind, key)` pairs for a registry, not a queryset.

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
            # THE RAW REFERENCE NEVER REACHES THE LOG: this path runs
            # inside a deletion, and a deletion must not write what it
            # is destroying somewhere new. The fact that one reference
            # failed to parse and was dropped is the whole of what a
            # reader needs.
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
            # SAME RULE AS THE REFERENCE BRANCH ABOVE: no raw value in
            # the log, only the fact that one generation id could not
            # be parsed and was dropped.
            logger.warning(
                "tools.vision.retention: one generation id failed to parse; ignored.")

    return sorted(services.existing_job_ids(job_ids))


def purge_artifacts(refs, generation_ids) -> int:
    """Today's conversation-purge slot: resolve, then delete. The next
    change retires this in favour of the child tickets
    `resolve_artifact_jobs` feeds."""
    return services.delete_jobs(resolve_artifact_jobs(refs, generation_ids))


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
