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


def purge_artifacts(refs, generation_ids) -> int:
    """Delete every generation job these references and ids name.
    Returns the number of JOBS deleted, deduped.

    THIS MODULE NEVER QUERIES `GenerationJob.objects` ITSELF: it only
    maps references and generation ids to job ids, and hands them to
    `tools.vision.services.delete_jobs` for the one unscoped read and
    the actual deletion -- `foundation/ops/tests/
    test_column_boundaries.py`'s IA-1 gate pins `visibility.py` and
    `services.py` as the only two files in this column allowed to touch
    that manager, and a third site here is exactly the drift it exists
    to catch. Each job goes through `services.delete_job` (inside
    `delete_jobs`), so its child rows cascade, its managed directory
    goes, and its best-effort engine-side sweep runs.

    With two empty lists this returns 0 having run no query at all
    beyond what parsing an empty sequence costs (none) and without
    calling `delete_jobs` -- every conversation purge on a vision box
    reaches this function, most of them for a conversation with no
    images, and that common case must cost nothing.

    Idempotent: a reference whose row is already gone contributes
    nothing, and so does a job id that matches no row.
    """
    job_ids: set = set()

    output_pks: list[int] = []
    input_pks: list[int] = []
    for reference in refs or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            logger.warning(
                "tools.vision.retention: unusable artifact reference %r; ignored.",
                reference)
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
                "tools.vision.retention: unusable generation id %r; ignored.", raw)

    if not job_ids:
        return 0

    return services.delete_jobs(job_ids)
