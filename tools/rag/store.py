"""
Managed document store (ADR 0009).

Single source of truth for *where a document's files live on disk*. The box
owns its data: rather than referencing an arbitrary/transient upload or
watch-folder path, ingest copies each source file into a per-document
directory under `settings.DOCUMENTS_DIR`
(`<FARABUNKER_DATA_DIR>/documents/<doc_id>/<basename>`), and later waves
(ingest, the library UI's delete endpoint) go through the helpers here
instead of touching the filesystem layout directly.

`FARABUNKER_DATA_DIR` should point at a durable host-mounted volume in
production (ADR 0006); `data/` is gitignored (see .gitignore).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from django.conf import settings

from foundation.files import create_owner_only_dir, lock_down_file

logger = logging.getLogger(__name__)


def document_dir(doc_id: int) -> Path:
    """Return the managed store directory for a document, e.g.
    `<DOCUMENTS_DIR>/<doc_id>/`. Does not create it -- see `store_file`."""
    return settings.DOCUMENTS_DIR / str(doc_id)


def sidecar_path_for_dir(dir_: Path) -> Path:
    """The extraction sidecar that lives in `dir_` (C-18).

    A directory-keyed twin of `sidecar_path`, for the one caller
    (`tools.rag.ingest._source_documents`) that resolves its sidecar from
    an already-resolved stored-file path rather than a `doc_id`: that
    function's own docstring explains why it keys off `path.parent`
    instead of `document_dir(doc.id)` (the two agree in every real run,
    but not in `TestSourceDocuments`'s synthetic fixtures). Routing that
    call through this module too, instead of leaving it a bare literal,
    means the filename is spelled in exactly one place either way.
    """
    return dir_ / "extract.json"


def sidecar_path(doc_id: int) -> Path:
    """The extraction sidecar for `doc_id` (C-18).

    The filename was written out at five production sites across four
    modules, each independently spelling a layout decision this module
    owns. `tools.rag.sidecar` reads and writes the file's CONTENT; this
    says where it is.
    """
    return sidecar_path_for_dir(document_dir(doc_id))


def work_dir(doc_id: int) -> Path:
    """The scratch directory a media driver uses while extracting
    `doc_id`, and deletes when it is done (C-18).

    Three sites spelled `/ "work"` by hand. Same reasoning as
    `sidecar_path`: the store owns its own layout.
    """
    return document_dir(doc_id) / "work"


def store_file(src_path: str, doc_id: int) -> str:
    """Copy `src_path` into the managed store for `doc_id`, creating its
    document directory if needed, and return the stored file's absolute
    path as a string.

    This *copies* rather than moves the source file -- the original at
    `src_path` (e.g. a watch-folder drop) is left untouched. The stored
    file keeps the source's basename. Callers (ingest) are expected to set
    `Document.source_path` to the returned path so it becomes the single
    place downstream code (document_file view, delete_document, etc.)
    reads the file from.
    """
    src = Path(src_path)
    dest_dir = document_dir(doc_id)
    create_owner_only_dir(dest_dir)
    dest_path = dest_dir / src.name
    shutil.copy2(src, dest_path)
    # S14/B-8: `copy2` carries SRC's mode across -- tighten after, not
    # before (see `foundation.files.lock_down_file`'s docstring).
    lock_down_file(dest_path)
    return str(dest_path)


def move_file(src_path: str, doc_id: int) -> str:
    """Move `src_path` into the managed store for `doc_id`, creating its
    document directory if needed, and return the stored file's absolute
    path as a string -- same layout, same return shape as `store_file`,
    but a MOVE, not a COPY.

    A sibling verb, not a flag on `store_file`: an inbox-originated ingest
    (a browser upload landed in the watch-inbox, T2+'s media pipeline)
    moves its source into the managed store, because the inbox copy has no
    purpose once ingest owns the file -- leaving it behind would just
    double the bytes on disk for no reason. `manage.py ingest` (an operator
    naming their OWN file directly) must keep using `store_file`'s copy:
    the platform never deletes a file it doesn't own. Two named verbs, so
    which one a call site means is visible there, not buried in a keyword
    argument. Implemented via `shutil.move`, so it's an atomic rename
    whenever `src_path` and the managed store share a filesystem (true for
    every deployment topology this platform supports today) and only falls
    back to copy-then-delete if they don't.
    """
    src = Path(src_path)
    dest_dir = document_dir(doc_id)
    create_owner_only_dir(dest_dir)
    dest_path = dest_dir / src.name
    shutil.move(str(src), str(dest_path))
    # S14/B-8: a rename (the same-filesystem fast path `shutil.move`
    # takes here) carries SRC's mode across just like `copy2` -- tighten
    # after, not before.
    lock_down_file(dest_path)
    return str(dest_path)


def assert_inside_inbox(resolved: Path) -> None:
    """Raise `ValueError` unless `resolved` -- an ALREADY-RESOLVED,
    symlink-free path, i.e. the return of `Path(...).resolve()` -- lies
    inside `settings.INGEST_INBOX_DIR`.

    B-3 (round-3 hardening), review round 1 ruling: the WATCHER's own
    containment root is the inbox ONLY, narrower than `assert_inside_
    platform_dirs` below. `tools.rag.ingest.stage_document` calls this
    for `actor == SERVICE_PRINCIPAL` specifically -- a resolved path
    under `settings.DOCUMENTS_DIR` or `settings.CHAT_STAGING_DIR` is
    REFUSED for the watcher, not merely "unrecognized": the audit's own
    attack plants a symlink in the inbox pointing at a file already
    living in the managed store (or, just as easily, a chat attachment
    already staged), and the watcher's move-semantics enqueue would
    still take that file's bytes and relocate them again. A path outside
    the inbox reaching the watcher's real actor means a symlink (or some
    other path confusion) resolved somewhere the watcher was never
    supposed to reach, `settings.DOCUMENTS_DIR`/`CHAT_STAGING_DIR`
    included.
    """
    root = Path(settings.INGEST_INBOX_DIR).resolve()
    if not resolved.is_relative_to(root):
        # round-3 final wave: the resolved path is a host filesystem
        # detail an operator needs, not something a browser-facing flash
        # or an end-to-end response body should ever echo back -- logged
        # here, at error level, so the operator still has it; the raised
        # message below names only the file's own name.
        logger.error("%s is outside the upload area -- refusing to stage it", resolved)
        raise ValueError(
            f"{resolved.name} is outside the upload area -- refusing to stage it"
        )


def assert_inside_platform_dirs(resolved: Path) -> None:
    """Raise `ValueError` unless `resolved` -- an ALREADY-RESOLVED,
    symlink-free path, i.e. the return of `Path(...).resolve()` -- lies
    inside one of the TWO directories a browser-upload door owns: the
    watch inbox (`settings.INGEST_INBOX_DIR`) or the chat attachment
    staging directory (`settings.CHAT_STAGING_DIR`).

    B-3 (round-3 hardening; review round 1 ruling narrowed this from
    THREE roots to two -- `settings.DOCUMENTS_DIR`, the managed store, is
    never a valid destination for either upload door and is deliberately
    NOT admitted here; see `assert_inside_inbox`'s own docstring for why
    the watcher's own, even narrower, check refuses it explicitly rather
    than relying on this function simply not recognizing it).
    `tools.rag.ingest.stage_document` calls this for every non-`None`,
    non-`SERVICE_PRINCIPAL` actor -- a browser upload -- right after it
    resolves the path it was given; never for `manage.py ingest`
    (`ingest_path` passes `actor=None`, an operator naming their OWN
    file directly), the same "two doors, one trusted" split `store_file`/
    `move_file` above already keep: an operator's shell command is
    trusted to name a path outside these roots on purpose; a browser
    upload is not -- the only path it should ever be handed is one THIS
    platform already wrote (its own staged copy in the inbox or chat
    staging), so anything else reaching this check is refused before a
    `Document` row, or `store.move_file`, ever treats it as legitimate
    content.

    A named verb, not a flag on `store_file`/`move_file` themselves: this
    module's own "two named verbs, not a keyword argument" discipline
    (see `move_file`'s docstring) applies here too -- the containment
    question is orthogonal to copy-vs-move, so it gets its own function
    rather than a parameter threaded through both.
    """
    roots = (
        Path(settings.INGEST_INBOX_DIR).resolve(),
        Path(settings.CHAT_STAGING_DIR).resolve(),
    )
    if not any(resolved.is_relative_to(root) for root in roots):
        # round-3 final wave: see `assert_inside_inbox`'s own comment
        # just above its matching pair of lines -- same split, same reason.
        logger.error("%s is outside the upload area -- refusing to stage it", resolved)
        raise ValueError(
            f"{resolved.name} is outside the upload area -- refusing to stage it"
        )


def remove_document_files(doc_id: int) -> None:
    """Delete a document's entire managed-store directory tree, if present.
    Safe to call when the directory doesn't exist (e.g. the document was
    never ingested via the managed store, or was already cleaned up)."""
    dest_dir = document_dir(doc_id)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
