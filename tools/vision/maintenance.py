"""The "Engine files" admin page (Engine files spec, 2026-09-02) --
`/vision/engine-files/`.

THE PROBLEM THIS EXISTS FOR: the image engine keeps its OWN copies of
every rendered output and uploaded input, in host folders farabunker
never manages (`tools.vision.store` copies bytes OUT of the engine and
never reads its folders back -- see that module's own docstring).
Gallery deletion only ever removes farabunker's managed copy
(`<GENERATED_DIR>/<job-uuid>/`), so the engine's own folders accumulate
"phantom" files invisibly. This page is how an administrator SEES and
DELETES everything actually on disk in those two folders.

TWO RW BIND MOUNTS, reachability only. `tools.vision.store.
ENGINE_OUTPUT_DIR`/`ENGINE_INPUT_DIR` name the two directories this
module reads; compose.yaml's own comment documents how an operator
points them at the engine's real folders. A box that never mounted them
(a preview stack, most dev boxes) reads an empty, honestly-labelled
list rather than crashing -- see `_list_dir` below.

ACCOUNTED VS ORPHANED IS A PREFIX MATCH, NOT A JOIN. A file is
"accounted" when its basename STARTS WITH a UUID this platform already
knows about: a `GenerationJob` primary key (any status -- a failed job
can still have left engine-side bytes behind) for a file in the output
folder, or that same set UNIONED with every staged upload's own UUID
(`tools.vision.models.JobInput` rows with no job yet -- see
`store.stage_input`'s directory shape) for a file in the input folder.
This is deliberately a STRING PREFIX TEST, not a real relationship: two
files that happen to share a prefix are indistinguishable, and a
filename the engine invented with no UUID in it at all always reads as
orphaned. Documented here because it is the whole of the rule, not an
implementation detail.

THUMBNAILS ARE CONTENT. `identity.access.sees_all_content` -- not
`is_admin` alone -- gates every `<img>` this page or its thumbnail
route would otherwise render, for the same reason `models.queue`'s job
cards withhold another user's payload (that module's own "Content
hidden." note): an engine-side file belongs to no row farabunker's own
tables track ownership of, but its PIXELS can still depict somebody's
private generation, so seeing them costs the same standing as reading
anyone else's images. `is_admin` (enforced by the route's class "S" at
the middleware -- `identity/routes.py`) is what gets a caller to the
PAGE and to the delete action; `sees_all_content` is a SECOND, narrower
gate this module applies itself, on top, only for pixels. Without it: a
filename, its size, its mtime, and its accounted/orphaned tag -- never
the bytes. Filenames here are UUID-SHAPED (an engine-produced job
prefix, or entirely engine-invented) and therefore SAFE to render as
plain text with no further escaping beyond Django's own auto-escape.

DELETION NEVER TOUCHES A DIRECTORY, only a named FILE directly under one
of the two configured directories, resolved strictly by basename
(`_safe_name`) -- the same no-traversal shape `tools.vision.views.
_serve_stored_file` gives a request that never supplies a filesystem
path of its own.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.contrib import messages
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_POST

from foundation.settings_area import settings_redirect
from identity import audit
from identity.access import sees_all_content
from identity.contracts import actions
from identity.request import principal_for_request
from tools.vision import store
from tools.vision.models import JobInput
from tools.vision.views import _serve_stored_file, _validated_next_url
from tools.vision.visibility import known_job_uuids

logger = logging.getLogger(__name__)

# How many deleted names `engine_files_delete` samples into one audit
# row's `detail["names"]` -- `detail["count"]` (unconditionally the real
# total) is the number a report totals against; this is a bound on the
# JSON payload, not a second count.
_AUDIT_NAMES_LIMIT = 50


@dataclass(frozen=True)
class EngineFile:
    """One file exactly as `os.scandir` reported it -- name, size, mtime
    -- plus the accounted/orphaned tag `_list_dir` derives for it.

    `modified` is `mtime` rendered once, here, as a plain local-time
    string: the template needs something to print, and Django's own
    `date` filter cannot format a raw UNIX timestamp -- `mtime` itself
    stays a `float` so sorting by it (`engine_files`'s "newest first")
    costs no parsing back out of a string.
    """

    name: str
    size: int
    mtime: float
    modified: str
    accounted: bool


def _known_staged_uuids() -> set[str]:
    """Every staged upload's own UUID -- the directory name
    `store.stage_input` writes under `<GENERATED_DIR>/uploads/`, read
    back off `JobInput.path` for every row with no job yet
    (`tools.vision.models.JobInput`'s own docstring on what a null
    `job` means)."""
    return {
        Path(path).parent.name
        for path in JobInput.objects.filter(job__isnull=True).values_list("path", flat=True)
    }


def _list_dir(path: Path, known_prefixes: set[str]) -> list[EngineFile]:
    """Every FILE directly under `path`, each tagged accounted/orphaned
    by a prefix match against `known_prefixes` (module docstring).

    `os.scandir`, FILES ONLY -- a subdirectory (the engine's own
    per-job upload subfolders, anything else it keeps) is skipped, never
    descended into; this page's whole job is the flat top level.

    An unreadable directory -- the bind mount is absent (a preview
    stack, most dev boxes), or the path was simply never created -- is
    an HONEST EMPTY LIST, never a 500: `OSError` is caught once, here,
    so `engine_files` never has to guess whether `[]` meant "really
    empty" or "could not be read" -- callers pass `path.is_dir()`
    through to the template separately for that distinction.
    """
    files: list[EngineFile] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                # `known_prefixes` is a set of UUID strings, EXACTLY 36
                # characters each (`str(uuid.uuid4())`, both for a
                # `GenerationJob` pk and for a staged upload's own
                # directory name -- `_known_staged_uuids`/`tools.vision.
                # visibility.known_job_uuids`). Accounting is therefore a
                # fixed-width prefix test: slicing `entry.name` to the
                # same 36 characters and doing a set lookup is the exact
                # same answer `any(entry.name.startswith(p) for p in
                # known_prefixes)` would give, but O(1) per file instead
                # of O(len(known_prefixes)) -- this loop can run over
                # thousands of files against thousands of jobs.
                accounted = entry.name[:36] in known_prefixes
                files.append(
                    EngineFile(
                        name=entry.name, size=stat.st_size, mtime=stat.st_mtime,
                        modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        accounted=accounted,
                    )
                )
    except OSError:
        return []
    return files


def engine_files(request):
    """GET /vision/engine-files/ -- list the engine's output and input
    folders. ADMIN ONLY (route class "S", `identity/routes.py`) --
    enforced at the middleware, not re-checked here.

    `?select=1` is select mode with nothing preselected, `?select=all`
    preselects every row and `?select=orphans` preselects only the
    orphaned ones -- the same GET-state grammar the gallery's own select
    mode uses (`tools/vision/views.py::gallery`), extended with the one
    preselection this page's own "orphans only" link needs.
    """
    principal = principal_for_request(request)
    job_uuids = known_job_uuids()
    outputs = _list_dir(store.ENGINE_OUTPUT_DIR, job_uuids)
    inputs = _list_dir(store.ENGINE_INPUT_DIR, job_uuids | _known_staged_uuids())
    select_param = request.GET.get("select")
    return render(
        request,
        "vision/engine_files.html",
        {
            "outputs": sorted(outputs, key=lambda f: f.mtime, reverse=True),
            "inputs": sorted(inputs, key=lambda f: f.mtime, reverse=True),
            "outputs_reachable": store.ENGINE_OUTPUT_DIR.is_dir(),
            "inputs_reachable": store.ENGINE_INPUT_DIR.is_dir(),
            "show_thumbnails": sees_all_content(principal),
            "select_mode": select_param in ("1", "all", "orphans"),
            "select_all": select_param == "all",
            "select_orphans": select_param == "orphans",
        },
    )


def _dir_for_kind(kind: str) -> Path | None:
    """The configured directory `kind` ("output"/"input") names, or
    `None` for anything else -- read off `tools.vision.store` on EVERY
    call (never cached at import) so a test's `monkeypatch.setattr`
    on the module attribute is honoured."""
    if kind == "output":
        return store.ENGINE_OUTPUT_DIR
    if kind == "input":
        return store.ENGINE_INPUT_DIR
    return None


def _safe_name(name: str) -> str | None:
    """`name`, or `None` when it is not a bare basename this page may
    resolve under one of its two directories.

    No separator of EITHER style (`tools.vision.store._basename`'s own
    reason for normalizing backslashes too), never `.`/`..`, never
    empty. The same guarantee `_serve_stored_file`'s by-primary-key
    shape gets for free -- this page takes a NAME off the request
    instead, so it re-earns "no path-traversal surface" here rather
    than inheriting it.

    A NUL byte is refused too: `pathlib`/`os` raise `ValueError` (not
    `OSError`) the moment a `\\x00`-carrying name reaches a filesystem
    call -- `Path.unlink`, `os.scandir`'s own `entry.stat()`, a `open()`
    -- and neither `engine_files_delete`'s `except OSError` nor
    `_serve_stored_file`'s never-500 guarantee catches `ValueError`. A
    request cannot construct one through the URL path itself (Django's
    URL resolver already refuses a NUL in the path), so this matters
    only for the POST body's `files` list, where `name` is a raw form
    value -- refusing it here, before any filesystem call sees it, is
    what keeps this a silent drop instead of a 500.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        return None
    return name


# ONE fixed 404 body for EVERY refusal `engine_file_thumbnail` can give
# -- hidden by the content setting, an unsafe/symlinked name, or a name
# that simply is not there. Not just the same STATUS CODE: the same
# BYTES, with no `name` interpolated in either, so an administrator with
# `sees_all_content` off cannot distinguish "you may not see this" from
# "there is no such file" by diffing two responses for the same name --
# the same non-disclosure `vision-output-file`/`vision-input-file`
# already give a principal with no standing over a row.
_NO_SUCH_ENGINE_FILE = "No such engine file."


def engine_file_thumbnail(request, kind: str, name: str):
    """GET /vision/engine-files/thumb/<kind>/<name>/ -- one thumbnail's
    bytes, straight off the engine's own folder. Route class "S"
    (`identity/routes.py`) gets an administrator to this URL; THIS
    predicate is the second, narrower gate the module docstring
    describes -- gated on `sees_all_content`, never `is_admin` alone,
    because a thumbnail's PIXELS are content.

    `Http404(_NO_SUCH_ENGINE_FILE)` for every refusal -- see that
    constant's own docstring for why the body never varies.
    """
    principal = principal_for_request(request)
    if not sees_all_content(principal):
        raise Http404(_NO_SUCH_ENGINE_FILE)
    directory = _dir_for_kind(kind)
    safe_name = _safe_name(name)
    if directory is None or safe_name is None:
        raise Http404(_NO_SUCH_ENGINE_FILE)
    path = directory / safe_name
    if path.is_symlink():
        # `_list_dir` already skips a symlink (`entry.is_file(follow_
        # symlinks=False)`), so a symlinked name never appears as a row
        # to click -- this is the SAME refusal for a name typed or
        # replayed directly, closing the gap `follow_symlinks=False`
        # only closed for the listing. A symlink could point anywhere on
        # the host filesystem, well outside either configured directory,
        # so this is refused before ANY read of what it points to.
        raise Http404(_NO_SUCH_ENGINE_FILE)
    media_type = mimetypes.guess_type(safe_name)[0] or ""
    return _serve_stored_file(request, str(path), media_type, _NO_SUCH_ENGINE_FILE)


@require_POST
def engine_files_delete(request):
    """POST /vision/engine-files/delete/ -- bulk-delete named engine
    files, straight off disk. Route class "S".

    Nothing this deletes is tracked by any farabunker row -- `tools.
    vision.store` never manages the engine's own folders -- so this is a
    plain `Path.unlink(missing_ok=True)` by basename under one of the
    two configured directories, never a directory removal.

    `files` is `request.POST.getlist("files")`, each `"<kind>:<name>"`
    (the page's own checkbox value). An unsafe name -- a traversal
    attempt ("../x"), an absolute path, an unknown `kind` -- fails
    `_safe_name`/`_dir_for_kind` and is silently DROPPED, the same shape
    the gallery's own bulk delete drops a foreign id in
    (`tools.vision.views.jobs_delete_selected`'s own docstring): a bulk
    action addresses many names behind one submit, so there is no single
    "which one" a 404 could confirm or deny.

    ONE audit line for the WHOLE request (`identity.audit.record`),
    naming how many files actually went -- not one per file, which would
    flood the trail for what is, from the operator's chair, a single
    action clicked once. `detail["names"]` is capped at
    `_AUDIT_NAMES_LIMIT` -- `count` (already unconditionally the REAL
    total) is what a report should ever total against; `names` is a
    convenience sample for a human reading one row, not a second,
    competing total, and an unbounded list here would let one giant
    selection bloat a single `AuditEvent.detail` JSON blob.
    """
    principal = principal_for_request(request)
    removed: list[str] = []
    for entry in request.POST.getlist("files"):
        kind, _sep, name = entry.partition(":")
        directory = _dir_for_kind(kind)
        safe_name = _safe_name(name)
        if directory is None or safe_name is None:
            continue
        target = directory / safe_name
        try:
            target.unlink(missing_ok=True)
        except OSError:
            # A failed unlink (permission, a mid-flight race) must not
            # fail the whole bulk request -- log and move on, the same
            # forgiveness the delete-hook (`tools.vision.services.
            # delete_job`) gives its own best-effort engine-side unlink.
            logger.warning("Could not delete engine file %s", target, exc_info=True)
            continue
        removed.append(f"{kind}:{safe_name}")

    if removed:
        audit.record(
            principal, actions.ENGINE_FILE_DELETED,
            target_type="engine_file", target_key="", target_label="",
            names=removed[:_AUDIT_NAMES_LIMIT], count=len(removed),
        )
        noun = "file" if len(removed) == 1 else "files"
        messages.success(request, f"Deleted {len(removed)} engine {noun}.")
    else:
        messages.info(request, "Nothing selected.")

    # `settings_redirect` on BOTH legs: Engine files is a settings page,
    # so a delete must land back on it with the assistant panel in the
    # state the operator left it in (persistence round). The validated
    # `next` still decides WHERE; this only decides whether the panel's
    # own flag rides along, and only when the submitting form carried it.
    next_url = _validated_next_url(request)
    return settings_redirect(request, next_url or "vision-engine-files")
