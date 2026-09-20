"""
Model-free, worker-free sidecar reading (media-into-RAG plan T10 review
MAJOR 1).

The layering law (see `tools.rag.transcode`'s own docstring for the
fuller statement): `{readers, transcode, extract, sidecar} <- media <-
ingest <- jobs`. This module is a LEAF, a sibling of `readers.py`/
`transcode.py`/`extract.py` -- no Django import, no model import, no
worker/queue import; it only ever reads a plain JSON file some earlier
job already finished writing.

Unlike its three siblings, this module is imported from BOTH halves of
the pipeline: the worker/run half (`tools.rag.media`'s own
`_load_finished_sidecar_if_matching`, T7's "orphaned success" reuse
check) AND the HTTP half (`tools.rag.views.document_transcript`, T10)
-- reading an ALREADY-FINISHED sidecar is a pure, cheap, local file read
with no worker-only dependency (no ffmpeg, no transcriber client, no
model resolution), so an HTTP-request thread reading one carries none of
the risk `tools.rag.media`'s own module docstring warns against for
importing THAT module from a view.

T10 review MAJOR 1's actual reproduction: `document_transcript` used to
`json.loads(path.read_text())` and index straight into the result with no
guard at all -- a sidecar JSON that decoded to a list/str/int/`null`
(valid JSON, no `.get()`), a `segments` value that decoded to a non-list
(a dict/string/nested-list/int), a `segments` element that decoded to a
non-dict, and a non-UTF-8 file (`UnicodeDecodeError` -- a `ValueError`
subclass, NOT an `OSError`, so a bare `except OSError` around the read
missed it) each 500'd the view. `read_sidecar`/`display_segments` below
are the fix: both are total functions over "whatever bytes happen to be
on disk", NEVER raise, and reduce every one of those shapes to "nothing
usable here" rather than crashing.
"""
from __future__ import annotations

import json
from pathlib import Path

from foundation.format import format_timecode


def read_sidecar(path: Path) -> dict | None:
    """Read and parse the sidecar JSON file at `path`, returning it as a
    `dict`, or `None` for anything that isn't a well-formed JSON OBJECT on
    disk -- never raises.

    `None` covers, in the order checked:
    - the file doesn't exist or isn't readable (`OSError` -- e.g.
      `FileNotFoundError`, a permission error, a symlink loop);
    - its bytes aren't valid UTF-8 (`UnicodeDecodeError`, a `ValueError`
      subclass rather than an `OSError` -- T10 review MAJOR 1's specific
      reproduction: a caller that only catches `OSError` around the read
      still 500s on a non-UTF-8 file);
    - its bytes don't parse as JSON at all (`json.JSONDecodeError`, also
      a `ValueError` subclass);
    - they parse as valid JSON that ISN'T a JSON object at the top level
      -- a list, string, number, bool, or `null` all parse without error
      but have no `.get()`; a caller blindly doing `sidecar.get(...)` on
      any of those is an uncaught `AttributeError`, the other half of the
      same review finding.

    Callers (`tools.rag.views.document_transcript`, `tools.rag.media.
    _load_finished_sidecar_if_matching`) both treat `None` uniformly as
    "no usable sidecar here" -- there is no reason to distinguish
    "missing" from "corrupt" from "wrong shape" this far from the ingest
    job that would have produced a well-formed one; all four mean the
    same thing to a reader: nothing to show/reuse.
    """
    try:
        raw = Path(path).read_text()
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def display_segments(sidecar: dict) -> list[dict]:
    """`sidecar["segments"]` (a transcription's `[{start, text}, ...]` or a
    vision extraction's `[{page, text}, ...]`) rendered into the
    `[{"heading": ..., "text": ...}, ...]` shape `rag/transcript.html`
    (`tools.rag.views.document_transcript`, T10) iterates over -- a
    page-keyed segment (`"page" in segment`) renders `"-- p. N --"`,
    everything else renders `"[m:ss]"`/`"[h:mm:ss]"` via
    `foundation.format.format_timecode` (itself already `None`/negative/
    non-finite safe).

    Never raises, the same total-function contract `read_sidecar` above
    keeps (T10 review MAJOR 1): `sidecar.get("segments")` that isn't a
    `list` at all (a dict, a string, a number, `None`) renders as NO
    lines rather than crashing or silently iterating character-by-
    character/key-by-key; an element OF that list that isn't a `dict` (a
    nested list, a string, a number) is skipped rather than crashing on
    its own `.get()`.

    Escapes NOTHING -- `text`, the page number, and the timecode are
    returned exactly as read from the sidecar; `rag/transcript.html`
    autoescapes on render (Django templates escape by default), so
    double-escaping here would be the actual bug.

    `"kind": "description"` on a page-keyed segment (preview UAT,
    2026-09-17) renders `"— description —"` instead of `"— p. N —"`: an
    image's sidecar carries its description and its transcription as two
    page-1 segments, and two identical "— p. 1 —" headings read as two
    pages of a one-page thing. Additive: a segment with NO `kind` --
    every segment written before this, and every scanned-PDF page
    written after it -- renders exactly as it always did, and an
    UNRECOGNISED `kind` falls back to the page heading rather than
    inventing one out of pipeline-supplied data.
    """
    segments = sidecar.get("segments")
    if not isinstance(segments, list):
        return []
    lines = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text", "")
        if "page" in segment:
            if segment.get("kind") == "description":
                lines.append({"heading": "— description —", "text": text})
            else:
                lines.append({"heading": f"— p. {segment['page']} —", "text": text})
        else:
            lines.append({"heading": f"[{format_timecode(segment.get('start'))}]", "text": text})
    return lines
