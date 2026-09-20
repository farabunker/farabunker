"""
Managed generated-media store (spec §5).

Single source of truth for where a job's files live: one directory per job,
`<GENERATED_DIR>/<job-uuid>/`, mirroring the document store's
`<DOCUMENTS_DIR>/<doc-id>/` shape (ADR 0009). Outputs are COPIED out of the
engine and never read from the engine's own output folder afterwards -- the
engine may be on another machine, or wiped.

Filenames coming back from an engine are untrusted input: only their
basename is ever used, so an engine (or a tampered response) can never write
outside a job's directory.
"""
from __future__ import annotations

import logging
import os
import shutil
import struct
import uuid
from pathlib import Path

from django.conf import settings

from foundation.files import create_locked_file, create_owner_only_dir, lock_down_file

logger = logging.getLogger(__name__)

# The engine's OWN output/input folders (Engine files feature) -- two rw
# bind mounts `compose.yaml` exposes at these exact IN-CONTAINER paths
# (its own `COMFYUI_OUTPUT_DIR`/`COMFYUI_INPUT_DIR`, a DIFFERENT pair of
# env var names on the HOST side, name the real directories on the
# operator's machine; see that file's header comment for why the names
# differ). NOT `config/settings.py`: that module is a peer no-touch zone
# for this change, so these live here instead, read straight off the
# environment the same way `config/settings.py` reads every other path
# setting. `ENGINE_OUTPUT_DIR`/`ENGINE_INPUT_DIR` are themselves also
# pinned in `compose.yaml`'s own `environment:` block for `web`, so an
# operator's `.env` can never accidentally override where THIS module
# looks -- the env-var read below is what lets a test (or a future
# non-compose deployment) still override it directly. Module-level
# `Path`s, not a function: every caller (the maintenance views, their
# tests via `monkeypatch.setattr`) reads/overrides a plain attribute,
# exactly like `settings.GENERATED_DIR` above.
ENGINE_OUTPUT_DIR = Path(os.environ.get("ENGINE_OUTPUT_DIR", "/engine/output"))
ENGINE_INPUT_DIR = Path(os.environ.get("ENGINE_INPUT_DIR", "/engine/input"))

# PNG magic + the fixed offsets of the IHDR width/height fields. Reading two
# integers out of a header is cheaper and far lighter than adding an image
# decoding dependency, and PNG is what our one operation produces.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_SIZE_OFFSET = 16
_IHDR_SIZE_END = 24


def job_dir(job_id) -> Path:
    """This job's managed directory, `<GENERATED_DIR>/<job_id>/`. Does not
    create it -- the store functions below do that when they write."""
    return settings.GENERATED_DIR / str(job_id)


# Where an upload with no job yet lives: `<GENERATED_DIR>/uploads/<uuid>/`.
# A sibling of the per-job directories and never confusable with one --
# a job directory is named by the job's UUID, and "uploads" is not one.
STAGING_DIRNAME = "uploads"


def _basename(name: str) -> str:
    """The last path component of `name`, whichever separator style it
    uses.

    An uploaded filename is untrusted input, and on a POSIX host
    `Path(name).name` (and `os.path.basename`) only splits on `/` -- a
    Windows-style path in the filename survives unsanitized. Normalizing
    backslashes to forward slashes first closes that gap the same way
    Django's own multipart `sanitize_file_name` does.
    """
    return Path(name.replace("\\", "/")).name


# EXTENSION -> MIME, SERVER-SIDE. `tools.rag.ingest._media_type_for` takes
# the same approach for the same reason and is deliberately NOT imported:
# `tools.rag` is column-private to `tools.vision` under the import law's
# rule 2, and a shared table would be a cross-column import for nine lines
# of data. The two tables also answer different questions -- rag's covers
# every ingestable medium, this one covers the raster images a generation
# can take as an input or produce as an output.
_MEDIA_TYPE_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}


def media_type_for_upload(uploaded) -> str:
    """The MIME type to record for `uploaded`, derived from its filename
    extension -- never from `uploaded.content_type`, which is whatever the
    posting client chose to send and is what gets re-served to the next
    viewer.

    `""` for any extension not in `_MEDIA_TYPE_BY_EXT`, `.svg` included
    (this platform neither generates SVG nor accepts it as a generation
    input). An empty media type makes `views._serve_stored_file` fall
    through to `application/octet-stream`, which downloads rather than
    renders -- the safe default, reached by omission rather than by a
    special case.
    """
    name = _basename(getattr(uploaded, "name", "") or "")
    return _MEDIA_TYPE_BY_EXT.get(Path(name).suffix.lower(), "")


def _write_upload(dest_dir: Path, filename: str, uploaded) -> str:
    """Write `uploaded` into `dest_dir` as `filename`, creating the
    directory, and return the absolute path as a string.

    The one chunked write in this module: a job's input and a staged
    upload are the same operation to two different places, and two copies
    of the loop would be two places to get the chunking wrong.
    """
    create_owner_only_dir(dest_dir)  # S14/B-8
    dest_path = dest_dir / filename
    with open(dest_path, "wb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
    lock_down_file(dest_path)  # S14/B-8
    return str(dest_path)


def store_input(job_id, param_key: str, uploaded) -> str:
    """Write an uploaded file for `param_key` into the job's `inputs/`
    subdirectory and return its absolute path as a string."""
    return _write_upload(
        job_dir(job_id) / "inputs", f"{param_key}-{_basename(uploaded.name)}", uploaded
    )


def stage_input(uploaded) -> str:
    """Write an upload that has NO JOB YET, and return its absolute path.

    The page must get a browser file into the managed store before it
    enqueues, because a queue payload is JSON: what travels in the payload
    is the `input:<id>` reference of the `JobInput` row
    `tools.vision.services.stage_upload` records for this path.

    Its own directory per upload, named by a fresh UUID, so two files with
    the same name never collide and no param prefix is needed in the
    filename -- the job's own copy gets the `<param>-<name>` form when
    `submit_job` stores it under the job (see `store_input`).
    """
    return _write_upload(
        settings.GENERATED_DIR / STAGING_DIRNAME / str(uuid.uuid4()),
        _basename(uploaded.name),
        uploaded,
    )


def remove_staged_input(path: str) -> None:
    """Delete one staged upload's own directory, if it is really one.

    The path comes off a database row, so this checks that it lives
    directly inside the staging directory before removing anything: a
    job's own input must never be deletable through this function, no
    matter what a row says.
    """
    staged = Path(path)
    if staged.parent.parent != settings.GENERATED_DIR / STAGING_DIRNAME:
        return
    if staged.parent.exists():
        shutil.rmtree(staged.parent)


def store_output(job_id, index: int, filename: str, content: bytes) -> str:
    """Write one engine output into the job's directory and return its
    absolute path as a string.

    The stored name is `<index>-<basename>`: the index keeps a batch's files
    ordered and collision-free, and taking only the basename means an
    engine-supplied path can never escape the job directory.
    """
    dest_dir = job_dir(job_id)
    create_owner_only_dir(dest_dir)  # H13 review round 1, finding 3 (vision-steward grant)
    dest_path = dest_dir / f"{index}-{Path(filename).name}"
    with create_locked_file(dest_path) as handle:
        handle.write(content)
    return str(dest_path)


def remove_job_files(job_id) -> None:
    """Delete a job's entire directory tree, if present. Safe to call when
    it never existed (a job that failed before producing anything)."""
    dest_dir = job_dir(job_id)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)


def remove_engine_files(job_id) -> None:
    """Best-effort delete of the ENGINE'S OWN files for `job_id`, in both
    `ENGINE_OUTPUT_DIR` and `ENGINE_INPUT_DIR` -- files this module never
    wrote and does not otherwise manage (module docstring: outputs are
    COPIED out of the engine, never read back from its folders). Called
    by `services.delete_job` so a job's engine-side files stop
    accumulating the moment its own row is deleted, rather than only
    ever going away through an administrator's later visit to the
    Engine files page (`tools/vision/maintenance.py`).

    Top-level files only, matched by NAME PREFIX (`<job_id>*`) -- the
    same prefix rule that page uses for its own accounted/orphaned tag,
    so a file this sweep removes is exactly one that page would have
    called accounted.

    NEVER RAISES. A missing bind mount, a permission error, a race with
    another deleter -- none of it may fail the job delete already in
    progress (`services.delete_job` deletes the ROW first); every
    failure is swallowed with a log line instead.
    """
    for directory in (ENGINE_OUTPUT_DIR, ENGINE_INPUT_DIR):
        try:
            matches = list(directory.glob(f"{job_id}*"))
        except OSError:
            # The directory itself is unreadable or was never created (no
            # bind mount) -- nothing to sweep, and not worth a log line on
            # every single job delete on a box that never mounted it.
            continue
        for match in matches:
            try:
                if match.is_file():
                    match.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete engine file %s", match, exc_info=True)


def png_dimensions(content: bytes) -> tuple[int | None, int | None]:
    """`(width, height)` from a PNG header, or `(None, None)`.

    Anything that isn't a PNG long enough to carry an IHDR reads as unknown
    rather than raising: a size we cannot measure is displayed as unknown,
    never guessed, and a future non-PNG output must not break job refresh.
    """
    if not content.startswith(_PNG_SIGNATURE) or len(content) < _IHDR_SIZE_END:
        return (None, None)
    width, height = struct.unpack(">II", content[_IHDR_SIZE_OFFSET:_IHDR_SIZE_END])
    return (width, height)


# How much of a stored file to read at a time when handing it back as an
# upload-shaped object. Matches Django's own `UploadedFile.DEFAULT_CHUNK_SIZE`
# so a re-used input costs the same memory as a fresh upload.
_CHUNK_SIZE = 64 * 2 ** 10


class StoredFile:
    """A file ALREADY in the managed store, presented as the small slice of
    Django's uploaded-file API the rest of this module uses: `.name`,
    `.content_type`, `.chunks()`.

    This is what lets an image the platform already holds -- a gallery
    output, another job's input -- reach `services.submit_job(files=...)`
    through exactly the path a browser upload takes, instead of a second
    "submit from a stored file" code path with its own storing, its own
    `JobInput` write, and its own bugs.

    Each job still gets its OWN copy under its own directory: deleting a
    job deletes its directory, and a job whose input lived in another
    job's folder would lose it.
    """

    def __init__(self, path, name: str | None = None, content_type: str = "") -> None:
        self.path = Path(path)
        self.name = name or self.path.name
        self.content_type = content_type

    def chunks(self, chunk_size: int = _CHUNK_SIZE):
        """Yield the file's bytes, matching `UploadedFile.chunks()`."""
        with open(self.path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                yield chunk
