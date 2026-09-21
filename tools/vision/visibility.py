"""What one principal may see of the generated images, in one place.

The same shape `agents/visibility.py` has, and for the same reason: a
future MCP edge and a future management command need the same answer,
and neither is a view.

A generated image is CONTENT, so the predicate is `sees_all_content`,
not `is_admin`: an administrator with the content setting off sees
none of somebody's pictures, and deleting one is not on the operator's
administer list either, so `job_delete` uses the same predicate.

`is_admin` IS READ HERE, in exactly one place and for exactly one
reason: `may_read_job`'s own carve-out for a row owned by `("service",
"local")` -- one a shell path produced. Full rule and rationale at its
canonical home, `identity.access.owned_rows_q`/`may_read_owned_row`
(identity/README.md §5b); `visible_jobs` and `may_read_job` below call
them rather than restating the rule.
"""
from __future__ import annotations

from identity.access import may_read_owned_row, owned_rows_q, sees_all_content
from identity.contracts.retention import KIND_VISION_JOB
from identity.retention import ticketed_keys
from tools.vision.models import GenerationJob


def visible_jobs(principal):
    # THE EXCLUSION IS ON THE BASE QUERYSET, BEFORE the
    # `sees_all_content` early return: that branch is every principal on
    # an open box, so an exclusion on the restricted leg alone would
    # leave deleted generations fully visible in the common posture.
    qs = GenerationJob.objects.exclude(pk__in=ticketed_keys(KIND_VISION_JOB))
    if sees_all_content(principal):
        return qs
    return qs.filter(owned_rows_q(principal))


def may_read_job(principal, job) -> bool:
    """Whether one already-loaded job may be read. `job_status`,
    `job_delete`, `output_file` and `input_file` resolve through this and
    answer 404 when it is False -- 404 rather than 403, because a 403 on
    a row-addressed URL confirms the row exists."""
    if sees_all_content(principal):
        return True
    return may_read_owned_row(principal, job)


def known_job_uuids() -> set[str]:
    """Every `GenerationJob` primary key, as a string, in ANY status.

    The Engine files admin page's own accounting seam
    (`tools.vision.maintenance`): it needs every job this box has ever
    recorded to tell an engine-side file "accounted" from "orphaned" --
    a failed or cancelled job can still have left bytes in the engine's
    own folders behind it -- and box inventory is not principal-scoped
    content the way `visible_jobs`/`may_read_job` above answer for.
    This module is still the ONE place `tools/vision` reaches
    `GenerationJob.objects` for a read (`foundation/ops/tests/
    test_column_boundaries.py` pins that as a closed set of two files,
    this one and `services.py`), so a THIRD module querying the table
    directly is exactly what this function exists to keep from
    happening.

    NOT narrowed by `ticketed_keys` the way `visible_jobs` is: this is
    the Engine files page's box-inventory accounting seam, not a
    content listing, and a deleted-but-not-yet-purged job's engine-side
    files are still accounted for here, not orphaned -- the row still
    exists, only hidden from `visible_jobs`, until the day its ticket
    is purged and this set stops naming it.
    """
    return {str(pk) for pk in GenerationJob.objects.values_list("id", flat=True)}
