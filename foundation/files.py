"""
Pure, dependency-free file-hashing helper shared across every column
(`models/`, `tools/`, `agents/`, and `foundation/` itself; W3, backup/
restore; ADR 0014 §18).

NO Django import, NO project import -- same charter as `foundation/format.py`:
`foundation/` is the platform's base layer (see `foundation/README.md`), so
both `models.*` apps and `tools.rag` can import this module freely without
either one reaching into the other's trust domain.
"""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path

# The block size every hash this platform stores or compares agrees on:
# `Document.file_hash` at stage time (`tools/rag/ingest.py::stage_document`),
# the post-stage re-verify (`tools/rag/ingest.py::run_ingest_for`), the
# unchanged-re-upload check inside `tools/rag/ingest.py::stage_and_enqueue_one`,
# and now every `manifest.files[].sha256` a backup records. SHA-256 is of
# course block-size-independent -- the digest is identical no matter how it
# is chunked -- so this constant is not a performance knob. It exists so the
# docstrings that already promise "same algorithm, same chunk size" (e.g.
# `tools.rag.ingest._sha256`) stay true, including transitively through
# that alias's delegation to this function, and so nobody "optimizes" the
# block size into a needless divergence between them.
_BLOCK_SIZE = 1024 * 1024  # 1 MiB


def sha256_file(path: Path | str) -> str:
    """Compute the SHA-256 hash of the file at `path`, streamed in
    `_BLOCK_SIZE` (1 MiB) chunks so hashing never loads a whole file (a
    multi-GB video, a large `pg_dump`) into memory at once.

    Pure `hashlib` + `pathlib` -- no Django import, no project import (see
    module docstring). Raises whatever `open()`/`read()` raise (e.g.
    `FileNotFoundError`, `PermissionError`) -- callers that need a
    friendlier refusal message (backup's "unreadable file" abort, restore's
    per-file verification) catch and re-wrap at the call site.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def create_owner_only_dir(path: Path, *, only_if_created: bool = False) -> Path:
    """`mkdir(parents=True, exist_ok=True)`, then guarantee `path` itself
    ends up `0700` -- owner read/write/execute, nothing for group or
    other (S14/B-8).

    `mode=` on `mkdir` only takes effect the moment `mkdir` actually
    creates the leaf directory -- `exist_ok=True` frequently finds it
    already there instead (an operator re-running a backup into the same
    destination, a second upload into the same document/job/conversation
    directory), in which case `mode` is silently ignored and the
    directory keeps whatever mode it already had, however wide. The
    explicit `chmod` after is what actually guarantees `0700` regardless
    of that history; `mkdir`'s own `mode=` is passed too so a BRAND-NEW
    directory is never briefly world-readable between the two calls.
    Parent directories `mkdir` creates along the way keep their own
    default mode -- `pathlib` never applies `mode` to them -- so only the
    leaf `path` is tightened.

    `only_if_created=True` -- H13 review round 1, finding 4: this helper
    must NEVER chmod a directory it did not itself create. Most callers
    own their leaf exclusively (a document's own directory, a job's own
    directory, a conversation's own staging directory, a backup's own
    destination) and want the DEFAULT, aggressive behaviour: retighten it
    on every call, in case an earlier, unpatched run left it wider. A
    caller that is sometimes handed a pre-existing SHARED directory it
    does NOT own -- `tools.rag.views.document_upload` passing
    `settings.INGEST_INBOX_DIR` itself when no category is given, or
    `foundation.ops.restore` passing `settings.DOCUMENTS_DIR`/
    `settings.GENERATED_DIR` themselves -- passes `only_if_created=True`
    instead: a MISSING leaf is still created and tightened (there is
    nothing pre-existing to disturb), but an ALREADY-existing leaf is left
    at whatever mode it already had, untouched. Combined with the parent
    behaviour above, this is what makes the "shared parents are never
    touched" claim below actually true for every caller, not just the
    ones that happen to pass a leaf one level down from a shared root.

    One helper, one home: every writer in this platform that creates a
    destination directory for bytes it is about to write calls this
    instead of a bare `mkdir` -- `foundation.ops.backup`,
    `foundation.ops.restore`, `tools.rag.store`, `tools.rag.views`,
    `tools.rag.services`, `tools.rag.jobs`, `tools.rag.media`,
    `tools.rag.transcode`, `tools.rag.management.commands.ingest_watch`,
    `tools.vision.store` -- so the discipline lives in exactly one place
    to audit or fix, not one per-site copy each. (`tools.rag.ingest`
    itself calls `create_locked_file`, the FILE-tightening sibling below,
    not this one -- it is not in this list.)
    """
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (only_if_created and existed):
        path.chmod(0o700)
    return path


def lock_down_file(path: Path) -> Path:
    """`chmod(path, 0o600)` -- owner read/write only, nothing for group or
    other (S14/B-8).

    Call this AFTER the write that produced `path`, never before: a fresh
    `open(path, "wb")` takes the OS default create mode (masked only by
    the process umask, typically `0644`), and `shutil.copy2`/
    `shutil.move`/`shutil.copystat` all carry the SOURCE file's mode
    across onto the destination -- tightening earlier is silently undone
    by whichever of those runs after it. See `create_owner_only_dir`'s
    docstring for the same reasoning on the directory side, and the
    module list of callers there.

    For a FRESH file this call itself creates (never a copy/move/rename
    of something else), `create_locked_file` below creates it AT `0600`
    directly and needs no follow-up call here.
    """
    path.chmod(0o600)
    return path


class SymlinkRefused(OSError):
    """`create_locked_file` raised this because `path` was already a
    symlink at open time (B-3, round-3 hardening).

    An `OSError` subclass, not a bare `ValueError`: every existing caller
    that wraps its own write in a broad `except OSError` (e.g.
    `foundation.ops.backup._copy_and_hash`'s "Cannot read/write" refusal)
    keeps catching this without a code change, exactly as it would catch
    the `ELOOP` this replaces.
    """


def create_locked_file(path: Path):
    """Open `path` for writing, created at `0600` directly -- owner
    read/write only, nothing for group or other -- via `os.open`'s own
    `mode` argument, plus an explicit `os.fchmod`, and with no follow-up
    `lock_down_file` call needed. Binary, write, truncating: the same
    contract as `open(path, "wb")`, and usable the same way (including as
    a context manager) -- `os.fdopen` wraps the raw descriptor in a
    regular file object.

    BOTH `mode=` and the explicit `fchmod` matter, same two-step reason as
    `create_owner_only_dir`'s directory side: `mode=` on `os.open` is
    reliable for a BRAND-NEW file regardless of the process umask (a
    umask can only CLEAR bits from the requested mode, never add one, and
    `0600` already has nothing set for group or other for a umask to
    clear) -- but `O_CREAT` only APPLIES that mode at creation time; if
    `path` already exists, `os.open` just opens it (and `O_TRUNC`
    truncates its content) without touching its mode at all, silently
    leaving an already-existing file at whatever wider mode it already
    had. The `fchmod` after is what actually guarantees `0600` either
    way, exactly like `create_owner_only_dir`'s `chmod` after its own
    `mkdir`.

    NOT a replacement for `lock_down_file`: this only helps at a FRESH
    write of a file THIS call creates. `shutil.copy2`/`shutil.move`/
    `shutil.copystat` still carry the SOURCE's mode across and still need
    `lock_down_file` after -- see its own docstring.

    B-3 (round-3 hardening): also passes `O_NOFOLLOW`, so a symlink
    already sitting at `path` -- planted by anyone who can write into a
    directory this call's caller does not exclusively own (the watch
    inbox, a chat-attachment staging directory) -- is never opened
    THROUGH. The kernel follows symlinks for every path COMPONENT except
    the last one regardless of this flag; `O_NOFOLLOW` only changes what
    happens when the LEAF itself -- `path` -- is a symlink: instead of
    opening (and, with `O_TRUNC`, truncating) whatever it points at, the
    `open` call fails with `ELOOP`, caught below and re-raised as the
    more legible `SymlinkRefused`.

    Deliberately still `O_TRUNC`, not `O_EXCL`: this helper is also the
    RE-STAGE path's own write (an unchanged or changed re-upload landing
    at a path this platform already staged something at once before --
    `tools.rag.ingest.stage_document`'s own re-stage branch is exactly
    that, authorised on purpose), and `O_EXCL` would turn every one of
    those legitimate second writes into a spurious "file exists" failure
    -- this helper has no way to tell "a legitimate second write at a
    name already used" from "an attacker's file already squatting that
    name" apart from whether the thing already there is a SYMLINK, which
    is exactly what `O_NOFOLLOW` alone already settles.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SymlinkRefused(
                exc.errno, f"refusing to write through a symlink at {path}"
            ) from exc
        raise
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")
