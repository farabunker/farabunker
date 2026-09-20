"""
Pure conversion layer for media ingestion (media-into-RAG plan T5;
ADR 0014).

The layering law: {readers, transcode, extract, sidecar} ← media ← ingest
← jobs. This module is a SIBLING of `tools.rag.readers` (which knows
nothing about media), `tools.rag.extract` (T8's one-image-one-call
vision module), and `tools.rag.sidecar` (T10 review MAJOR 1's model-
free sidecar reader -- see that module's own docstring for why it, alone
among these four leaves, is ALSO imported from `tools/rag/views.py`) --
all four are consumed by `tools.rag.media` (T7/T8), never by each other.

Every function here is a deterministic LOCAL conversion via an external
tool (`ffmpeg`/`ffprobe` on PATH) or a wheel (`pypdfium2`, `Pillow`) --
never a model, never the database, never the execution queue. That
boundary is deliberate and permanent: this module converts bytes into
other bytes; `tools.rag.media` is where conversion output meets a
Document row and a queue job.

Every `ffmpeg`/`ffprobe` invocation below is `subprocess.run` given an
argv LIST (never `shell=True`, never a shell string) -- the standard
shell-injection-proof shape for shelling out to an external binary.
Deliberately no `ffmpeg-python` (or similar) dependency: every invocation
here is a handful of fixed, hand-written argv lists, not a video-editing
pipeline -- building one from scratch each time isn't enough work to
justify carrying a third-party argv builder as a dependency.
"""
from __future__ import annotations

import io
import math
import shutil
import subprocess
from pathlib import Path

from foundation.files import create_owner_only_dir

# --- internal cadence: subprocess timeouts (matches models/queue/worker.py's
# own "Internal tuning constants" precedent -- plain module attributes, not
# Django settings, since these are this module's own implementation detail,
# not an operator-editable policy knob) -----------------------------------

# ffprobe is a metadata read, not a decode -- it should return almost
# instantly even for a large file. 30s is generous headroom for a slow/
# network-mounted source file, not an expectation of how long it normally
# takes.
PROBE_TIMEOUT_SECONDS = 30

# ffmpeg audio extraction has to read the WHOLE source file (a full decode
# of the video/audio track), so this bound has to cover the slowest
# realistic input, not the typical one: a 2-hour video, decoded on
# modest hardware, comfortably fits inside 600s (10 minutes) -- well short
# of actually taking that long in the common case, but a hard ceiling
# rather than an unbounded subprocess.run() the caller has to babysit.
EXTRACT_TIMEOUT_SECONDS = 600

# Slice-window default for `slice_audio`: 300s (5 minutes) of audio per
# transcription pass. Two things this size balances: progress granularity
# (a caller wants to report "slice 3 of 12", not "still transcribing" for
# an entire multi-hour file) and headroom under whatever downstream
# transcriber timeout T7 wires up (a stated 900s transcriber timeout means
# this leaves 3x margin per slice, not a near-miss). No overlap between
# slices -- deduplicating overlapped transcribed text would be a guess this
# module refuses to make; a slice boundary landing mid-word is an accepted
# cost of that choice, not a bug.
DEFAULT_SLICE_SECONDS = 300

# `rasterize_pdf_page`'s render scale before any `max_edge` downscale: 2.0
# is pypdfium2's multiplier on the PDF's native 72-DPI point size (so
# ~144 DPI) -- comfortably higher resolution than most `max_edge` values
# actually keep, so the FINAL image quality is governed by the max_edge
# downscale below, not by upscaling artifacts from an under-rendered page.
_PDF_RENDER_SCALE = 2.0

# B-4 (round-3 hardening H29; review round 1 finding 1). `_PDF_RENDER_
# SCALE` is a multiplier on whatever a PDF page's own dictionary CLAIMS
# its size is (`PdfPage.get_size()`, read before rendering) -- nothing in
# `pypdfium2` or this module ever checked that against the format's own
# 14400-point (200in) page-size ceiling, so a hand-built page declaring an
# absurd MediaBox rendered at the full fixed scale regardless (a
# 4000x4000pt page: 8000x8000 RGB, ~457MB peak RSS; a 200000x200000pt one:
# an unrefused 400000x400000-pixel request). `rasterize_pdf_page` now
# derives its render scale from the page's OWN declared area, GRADUATED
# rather than a step function: `scale = min(_PDF_RENDER_SCALE, sqrt
# (MAX_RENDER_PIXELS / (width_pt * height_pt)))`. For any page whose
# fixed-scale render would exceed the ceiling, the second term is smaller
# than `_PDF_RENDER_SCALE` and shrinks CONTINUOUSLY as the declared area
# grows -- landing the render at (up to rounding) exactly
# `MAX_RENDER_PIXELS`, rather than a size unrelated to how large the page
# actually claims to be. (Round 1's `_OVERSAMPLE`-based formula, since
# removed, collapsed to one CONSTANT render size for every page past its
# own crossover point, making a page ten times too large indistinguishable
# from one a million times too large; this derivation keeps shrinking as
# the declared area grows, so it never does that.) `RENDER_SCALE_FLOOR`
# below is the point past which even that shrinking scale stops being
# worth rendering at all.

# Below this scale (review round 1 finding 1), a page is refused rather
# than rendered: 0.25x is a quarter of `_PDF_RENDER_SCALE`'s ~144 DPI
# baseline, ~18 DPI -- an image this coarse is already illegible to a
# vision model, so paying for the render (and the vision call after it)
# would buy nothing an operator could call a usable page image; refusing
# is honest, a silent illegible render is not. Combined with
# `MAX_RENDER_PIXELS` below, this floor is reached only by a page
# declaring itself larger than roughly 20,000 points (~278in, ~23ft) on a
# side -- comfortably past the audit's own 200000-point reproduction, and
# past any real paper or engineering-drawing size.
RENDER_SCALE_FLOOR = 0.25

# The stated ceiling (B-4; review round 1 finding 2) on a single rendered
# page's pixel count: `rasterize_pdf_page` refuses (via `RENDER_SCALE_
# FLOOR` above) rather than ever rendering past this, and `_require_
# pillow` sets `PIL.Image.MAX_IMAGE_PIXELS` to the SAME number, so
# Pillow's own decompression-bomb guard -- which otherwise defaults to
# ~89.5 megapixels, a platform default this module did not choose and
# does not rely on -- enforces an identical ceiling on `normalize_
# image`'s uploaded-image path. 25,000,000 (25 megapixels): `pypdfium2`'s
# own render buffer is BGRA (4 bytes/pixel) and `bitmap.to_pil()` produces
# a SECOND, RGB (3 bytes/pixel) copy before the first is released -- worst
# case, both coexist for one page at the ceiling: ~100MB (BGRA) + ~75MB
# (RGB) = ~175MB transient, a bound this platform chose rather than
# inherited from Pillow's own default. A page whose declared size would
# still exceed this ceiling at `_PDF_RENDER_SCALE` renders at a SMALLER,
# graduated scale instead of being refused outright (see the comment on
# the formula above) -- a very large but legitimate page (an
# architectural drawing, a poster) still renders, just not at full
# fixed-scale resolution; only a page whose graduated scale falls below
# `RENDER_SCALE_FLOOR` is refused.
MAX_RENDER_PIXELS = 25_000_000


class RenderAreaExceededError(ValueError):
    """`rasterize_pdf_page`'s refusal for a page whose declared point size
    is either degenerate (zero or negative area) or so large that the
    graduated scale (`MAX_RENDER_PIXELS`'s own comment) needed to stay
    under the pixel ceiling falls below `RENDER_SCALE_FLOOR` (B-4,
    round-3 hardening H29; review round 1 finding 1) -- the exact
    `tools.rag.ingest.DocumentPageCapExceededError` pattern, one field
    over: a `ValueError` subclass so every existing `except ValueError`
    call site still catches it exactly as before, while a caller that
    wants to treat "this page is too large to render" differently from
    every other refusal can catch this name specifically.

    Deliberately its OWN type, not a reuse of `rasterize_pdf_page`'s
    existing bare `ValueError` for an out-of-range `page_number` -- that
    refusal ("page 9 of a 3-page file") and this one ("this page is too
    big to draw") are different failures a caller may want to tell apart,
    so this subclasses `ValueError` rather than raising the same bare type
    for both.
    """


def _require_tool(binary: str) -> None:
    """Guard for `binary` ("ffmpeg" or "ffprobe") being present on PATH, in
    the same message grammar as `tools.rag.readers._read_docx`'s
    soft-dependency guard: name the missing tool, say what's unavailable
    without it, and say how to get it. Called at the top of every entry
    point below that shells out, so a missing binary surfaces as this
    honest `RuntimeError` rather than a bare `FileNotFoundError` bubbling
    up out of `subprocess.run`.
    """
    if shutil.which(binary) is not None:
        return
    raise RuntimeError(
        f"{binary} is not installed; audio/video transcoding is unavailable in this "
        "environment. The container image ships it (the Dockerfile's apt-get layer installs "
        "the `ffmpeg` package, which provides both the `ffmpeg` and `ffprobe` binaries); on a "
        "bare host, install the `ffmpeg` package yourself via your OS package manager (e.g. "
        "`brew install ffmpeg` on macOS, `apt-get install ffmpeg` on Debian/Ubuntu) to enable it."
    )


def require_ffmpeg() -> None:
    """Public guard: raise `RuntimeError` (see `_require_tool`) if the
    `ffmpeg` binary isn't on PATH. Called at the top of `extract_audio` and
    `slice_audio` -- the two entry points below that shell out to `ffmpeg`
    itself, as opposed to `ffprobe` (see `probe_duration`, which guards on
    `_require_tool("ffprobe")` directly)."""
    _require_tool("ffmpeg")


def ffprobe_available() -> bool:
    """True when the `ffprobe` binary is on PATH -- a pure boolean
    predicate, unlike `probe_duration`'s own guard (`_require_tool`, which
    RAISES) for a caller that wants to check availability WITHOUT
    provoking an exception it would just catch and discard again
    immediately. `tools.rag.ingest._check_media_duration`'s own
    (formerly hand-rolled `shutil.which("ffprobe") is None`) check is this
    function's one caller."""
    return shutil.which("ffprobe") is not None


def _run_tool(argv: list[str], *, timeout: float, tool: str, doing: str) -> subprocess.CompletedProcess:
    """Run `argv`, or raise `RuntimeError` naming what failed (C-31).

    `tool` and `doing` are the caller's own words -- "ffprobe" /
    "probing the duration of /x.mp4", "ffmpeg" / "slicing /y.wav". An
    operator reading a failed ingest needs to know which tool was doing
    what to which file; a generic "subprocess failed" would be a
    regression wearing a de-duplication's clothes. `doing` therefore
    already carries the path -- this function never takes one as a
    separate argument, only as words the caller chose, so the three
    call sites keep deciding their own wording (`probe_duration`:
    "probing the duration of"; `extract_audio`: "extracting audio
    from"; `slice_audio`: "slicing") rather than this function guessing
    a shared phrase that would fit none of them well.
    """
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{tool} timed out after {timeout}s {doing}") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"{tool} failed {doing} (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result


def probe_duration(path: Path) -> float:
    """Return `path`'s media duration in seconds, via `ffprobe`.

    Runs `ffprobe -v error -show_entries format=duration -of
    default=nw=1:nk=1 <path>` -- the bare-number-only output shape avoids
    any parsing beyond `float()`. Raises `RuntimeError` (never returns a
    guess) if `ffprobe` isn't on PATH, exits non-zero, times out
    (`PROBE_TIMEOUT_SECONDS`), or prints something that isn't a plain
    float -- every failure mode includes `ffprobe`'s own stderr, since a
    silently-swallowed stderr is exactly the kind of guess this function
    refuses to make.
    """
    _require_tool("ffprobe")
    argv = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(path),
    ]
    result = _run_tool(
        argv, timeout=PROBE_TIMEOUT_SECONDS, tool="ffprobe", doing=f"probing the duration of {path}"
    )

    output = result.stdout.strip()
    try:
        return float(output)
    except ValueError as exc:
        raise RuntimeError(
            f"ffprobe returned an unparseable duration for {path}: {output!r} "
            f"(stderr: {result.stderr.strip()!r})"
        ) from exc


def extract_audio(src: Path, dest_wav: Path) -> Path:
    """Extract `src`'s audio track to `dest_wav` as 16kHz mono 16-bit PCM
    WAV -- the whisper.cpp input contract -- and return `dest_wav`.

    Normalized HERE, once, so every container format T7 hands this
    function (mp4/mov/mkv/webm video, or mp3/wav/m4a audio) takes exactly
    one path downstream: a transcriber never has to know or care what the
    original container/codec was.

    Runs `ffmpeg -nostdin -v error -y -i <src> -vn -ac 1 -ar 16000 -c:a
    pcm_s16le -f wav <dest_wav>`. `-nostdin` keeps ffmpeg from trying to
    read interactive input (this always runs unattended); `-y` overwrites
    `dest_wav` if it already exists. Raises `RuntimeError` (including
    ffmpeg's own stderr) on a non-zero exit or a timeout
    (`EXTRACT_TIMEOUT_SECONDS`).
    """
    require_ffmpeg()
    argv = [
        "ffmpeg",
        "-nostdin",
        "-v", "error",
        "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(dest_wav),
    ]
    _run_tool(argv, timeout=EXTRACT_TIMEOUT_SECONDS, tool="ffmpeg", doing=f"extracting audio from {src}")
    return dest_wav


def slice_audio(wav: Path, out_dir: Path, *, window_seconds: int = DEFAULT_SLICE_SECONDS) -> list[Path]:
    """Split `wav` (already 16kHz mono PCM, i.e. `extract_audio`'s own
    output) into ordered `window_seconds`-long slice files under `out_dir`,
    and return their paths sorted in playback order.

    Runs ffmpeg's segment muxer (`-f segment -segment_time <window> -c
    copy`) -- `-c copy` because the input is already in the target PCM
    format, so this is a pure re-mux (fast, lossless), not a re-encode.
    `window_seconds` is the silence-aware-slicing upgrade seam: a future
    caller wanting slices that break on silence instead of a fixed clock
    only has to change how THIS parameter is computed, not this function's
    shape. No overlap between slices -- see `DEFAULT_SLICE_SECONDS`'s
    comment for why that's accepted, not an oversight. Raises
    `RuntimeError` (including ffmpeg's own stderr) on a non-zero exit or a
    timeout (reuses `EXTRACT_TIMEOUT_SECONDS`: segment muxing is a copy, not
    a decode, so it's cheaper than `extract_audio`'s own bound, but that
    bound is already a generous ceiling, not a tight one, so there's no
    need for a second constant just to shave it down).
    """
    require_ffmpeg()
    # H13 review round 1, finding 10: `out_dir` is a subdirectory of a
    # document's own scratch `work_dir` -- owned exclusively.
    create_owner_only_dir(out_dir)
    pattern = out_dir / "slice-%05d.wav"
    argv = [
        "ffmpeg",
        "-nostdin",
        "-v", "error",
        "-y",
        "-i", str(wav),
        "-f", "segment",
        "-segment_time", str(window_seconds),
        "-c", "copy",
        str(pattern),
    ]
    _run_tool(argv, timeout=EXTRACT_TIMEOUT_SECONDS, tool="ffmpeg", doing=f"slicing {wav}")
    return sorted(out_dir.glob("slice-*.wav"))


def _require_pypdfium2():
    """Lazy-import `pypdfium2`, in the same soft-dependency guard grammar
    as `tools.rag.readers._read_docx` (missing package -> `RuntimeError`
    naming it and how to install it). `rasterize_pdf_page`'s own guard --
    `normalize_image` never needs this import at all (it has no PDF
    involved), so it is kept separate from `_require_pillow` rather than
    one shared guard that would make a Pillow-only host wrongly fail a
    `normalize_image` call over a PDF library it never needed."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "pypdfium2 is not installed; PDF page rasterization is unavailable in this "
            "environment. Install the `pypdfium2` package to enable it."
        ) from exc
    return pdfium


def _require_pillow():
    """Lazy-import `PIL.Image`, in the same soft-dependency guard grammar
    as `tools.rag.readers._read_docx`. Shared by `rasterize_pdf_page`
    (downscaling the pypdfium2 render) and `normalize_image` (the whole
    job).

    Also where `Image.MAX_IMAGE_PIXELS` is set to `MAX_RENDER_PIXELS` (B-4,
    round-3 hardening H29), every time this runs -- cheap and idempotent,
    and the one place both callers are guaranteed to pass through before
    touching `Image` at all. Pillow's own default (~89.5 megapixels) is a
    library default this platform never chose to rely on, not a bound
    picked for `normalize_image`'s own uploaded-image path; setting it
    explicitly here means an arbitrary phone-photo/crafted-file upload is
    held to the SAME stated ceiling `rasterize_pdf_page` enforces on a
    PDF's declared page size, via Pillow's own decompression-bomb guard
    (`PIL.Image.DecompressionBombError`) rather than a second check this
    module would have to maintain."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Pillow is not installed; image normalization/rasterization is unavailable in this "
            "environment. Install the `Pillow` package to enable it."
        ) from exc
    Image.MAX_IMAGE_PIXELS = MAX_RENDER_PIXELS
    return Image


def _downscale_to_png(image, max_edge: int, Image) -> bytes:
    """Downscale `image` (a `PIL.Image`, never upscaled) so neither
    dimension exceeds `max_edge`, then encode it as PNG bytes -- the
    verbatim 10-line tail `rasterize_pdf_page` and `normalize_image` each
    used to duplicate. `Image` (the `PIL.Image` module, not an instance) is
    threaded in rather than imported here, so this stays a plain helper of
    whichever caller already ran its own `_require_pillow()` guard, not a
    second import site of its own."""
    width, height = image.size
    longest = max(width, height)
    if longest > max_edge:
        scale = max_edge / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _choose_pdf_render_scale(width_pt: float, height_pt: float, *, page_number: int, pdf: Path) -> float:
    """The graduated scale-or-refuse decision `rasterize_pdf_page` makes
    from a page's own declared point size (B-4, round-3 hardening H29;
    review round 1 finding 1) -- factored out as a PURE function (no
    `pypdfium2` object touched) so the degenerate-size refusal (`area_pt
    <= 0`) is directly testable without needing a real PDF page that
    actually reports a zero/negative size: in practice `pypdfium2` itself
    silently substitutes a default page size (Letter, 612x792pt) for a
    degenerate declared `MediaBox` rather than ever returning `get_size()
    == (0, 0)`, so this guard is defensive -- it exists for whatever
    reaches this function with a genuinely degenerate size, not for a
    reproducible-via-a-crafted-PDF-file case today.

    Returns the CHOSEN scale (`min(_PDF_RENDER_SCALE, sqrt(MAX_RENDER_
    PIXELS / (width_pt * height_pt)))`) when it is usable, or raises
    `RenderAreaExceededError` -- naming the page's own declared point
    size, `MAX_RENDER_PIXELS`, and `RENDER_SCALE_FLOOR` -- for a
    degenerate size or one whose chosen scale falls below the floor.
    `page_number`/`pdf` are carried only for the refusal's own message,
    matching `rasterize_pdf_page`'s existing out-of-range-page-number
    message's own grammar (names the page and the file, no trailing
    period).
    """
    area_pt = width_pt * height_pt
    if area_pt <= 0:
        raise RenderAreaExceededError(
            f"page {page_number} of {pdf} has a degenerate declared size "
            f"({width_pt:g}x{height_pt:g} points) and cannot be rendered"
        )

    scale = min(_PDF_RENDER_SCALE, math.sqrt(MAX_RENDER_PIXELS / area_pt))
    if scale < RENDER_SCALE_FLOOR:
        raise RenderAreaExceededError(
            f"page {page_number} of {pdf} is too large to render: "
            f"{width_pt:g}x{height_pt:g} points would need scale {scale:.4g} to "
            f"stay under the {MAX_RENDER_PIXELS}-pixel ceiling, below the "
            f"{RENDER_SCALE_FLOOR} floor"
        )
    return scale


def rasterize_pdf_page(pdf: Path, page_number: int, *, max_edge: int = 1600) -> bytes:
    """Render page `page_number` of `pdf` (1-based -- matches
    `tools.rag.readers._read_pdf`'s own page-metadata convention, where
    `page: i + 1`) to PNG bytes, via `pypdfium2`.

    The render scale is chosen from the page's OWN declared point size
    (B-4, round-3 hardening H29; review round 1 finding 1), read via
    `PdfPage.get_size()` before any rendering happens: `min(_PDF_RENDER_
    SCALE, sqrt(MAX_RENDER_PIXELS / (width_pt * height_pt)))` -- GRADUATED,
    not a step function: the second term shrinks continuously as the
    page's declared area grows, so the bitmap `pypdfium2` is asked to
    allocate lands at (up to rounding) exactly `MAX_RENDER_PIXELS` for any
    page whose fixed-scale render would have exceeded it, rather than at a
    size unrelated to how large the page actually claims to be. `max_edge`
    remains a SEPARATE resource bound on the resulting payload size (this
    image typically travels to a vision model as base64, in the
    `DEFAULT_CONTEXT_WINDOW`/`DEFAULT_REQUEST_TIMEOUT` register of "bound
    a resource, don't chase a quality target") -- not a quality claim: a
    vision model downsamples its own input internally regardless of what
    resolution it's handed. The chosen scale's own render is also
    downscaled (never upscaled) afterward so neither dimension exceeds
    `max_edge`, exactly as before.

    Before ever calling into `pypdfium2`'s render: a degenerate declared
    size (zero or negative area -- see `RenderAreaExceededError`'s own
    docstring) raises immediately; otherwise, once the chosen scale falls
    below `RENDER_SCALE_FLOOR`, the page is refused outright rather than
    rendered at an already-illegible resolution -- `RenderAreaExceededError`
    naming the page's declared point size, `MAX_RENDER_PIXELS`, and
    `RENDER_SCALE_FLOOR`, never a silent clamp and never the allocation
    itself. A very large but legitimate page renders at the reduced scale
    instead of being refused; only a page past the floor is.

    Raises `RuntimeError` if `pypdfium2`/`Pillow` aren't installed (see
    `_require_pypdfium2`/`_require_pillow`); `ValueError` if `page_number`
    is out of range for `pdf` (clear, 1-based bounds in the message --
    never a silent clamp); or `RenderAreaExceededError` (itself a
    `ValueError`, but a distinct name -- see its own docstring) for the
    area refusal above.
    """
    pdfium = _require_pypdfium2()
    Image = _require_pillow()

    with pdfium.PdfDocument(str(pdf)) as pdf_doc:
        page_count = len(pdf_doc)
        if page_number < 1 or page_number > page_count:
            raise ValueError(
                f"page_number={page_number} is out of range for {pdf} (1-based, "
                f"has {page_count} page(s))"
            )

        page = pdf_doc[page_number - 1]
        try:
            width_pt, height_pt = page.get_size()
            scale = _choose_pdf_render_scale(width_pt, height_pt, page_number=page_number, pdf=pdf)
            bitmap = page.render(scale=scale)
            try:
                pil_image = bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()

    return _downscale_to_png(pil_image, max_edge, Image)


def normalize_image(src: Path, *, max_edge: int = 1600) -> bytes:
    """Load `src` (any format Pillow can open), convert to RGB, downscale
    (never upscale) so neither dimension exceeds `max_edge`, and return PNG
    bytes -- the phone-photo case for T8: an arbitrary camera JPEG/HEIC/PNG
    upload, of arbitrary size and color mode (RGBA, palette, CMYK, ...),
    normalized to one predictable shape before it ever reaches a vision
    model.

    Raises `RuntimeError` if Pillow isn't installed (see
    `_require_pillow`).
    """
    Image = _require_pillow()

    with Image.open(src) as img:
        img = img.convert("RGB")
        return _downscale_to_png(img, max_edge, Image)
