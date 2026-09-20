"""
The vision extraction call (media-into-RAG plan T8; ADR 0014) --
ONE image in, ONE model call, ONE string out. `tools.rag.media.
extract_to_sidecar` is the loop/progress/checkpoint/sidecar driver (the
scanned-PDF-page and single-image callers, T8's own structural mirror of
`tools.rag.media.transcribe_to_sidecar`); this module is the one thing
that loop calls per page/image, kept separate the same way `tools.rag.
transcode`'s pure bytes-to-bytes conversion is kept separate from
`tools.rag.media`'s model-consuming half -- this file talks to
`models.contracts.gateway`, nothing else in this pipeline needs to.

`RAG_EXTRACT_ROLE` (`models/contracts/roles.py`) is the "vision" capability
role a `rag.extract` job resolves -- registered by `tools/rag/apps.py`
only while "media" is enabled, the same gate `RAG_TRANSCRIBE_ROLE` gets.
"""
from __future__ import annotations

from pathlib import Path

from llama_index.core.llms import ChatMessage, ImageBlock, MessageRole, TextBlock

from models.contracts import gateway
from models.contracts.roles import RAG_EXTRACT_ROLE

# The extraction instruction sent to the vision model alongside every page/
# image -- PLATFORM BEHAVIOR, not a model default: this exact wording is
# what makes "transcribe the visible text, verbatim, no commentary" true of
# every engine this role could ever be bound to, regardless of that model's
# own out-of-the-box chat manners (a raw vision-capable chat model, asked
# with no prompt at all, might narrate the image instead of transcribing
# it). A module constant, not buried inline in `extract_image_text` below,
# so an operator auditing or overriding platform behavior has exactly one
# place to look -- and one place to change, a deliberate, NAMED deferral:
# making this operator-editable (a `RagSettings` field, say) is real future
# work this constant's existence flags, not a decision this task makes.
EXTRACTION_PROMPT = (
    "Transcribe all text visible in this image exactly as it appears, preserving reading "
    "order and line breaks. Output only the transcribed text, with no commentary. If the "
    "image contains no text, output nothing."
)

# The DESCRIPTION instruction, the OCR one's sibling (preview UAT,
# 2026-09-17). PLATFORM BEHAVIOR for the same reason `EXTRACTION_PROMPT`
# above is, and a SECOND constant rather than a widening of that one:
# every scanned-PDF page on this box goes through that prompt, its
# stored text is what a citation points back at, and a page's OCR must
# not start carrying narration because images needed describing.
#
# WHY THIS EXISTS AT ALL: `EXTRACTION_PROMPT` ends "If the image
# contains no text, output nothing" -- correct for a page of a scanned
# contract, and exactly wrong for a photo. A textless image produced an
# empty sidecar, no caption, and a prompt line that read "description
# not ready yet" forever; the chat model dutifully told the operator to
# keep waiting for work that had already finished.
#
# SHORT, AND SAID SO TWICE (a sentence count AND a word ceiling): this
# text becomes ONE LINE of an attachments block that may hold a dozen
# rows, and is capped at `tools.rag.access._CAPTION_CHAR_CAP` (600
# characters) downstream regardless. A model asked to "describe an
# image" with no bound will write four paragraphs and the cap will cut
# it mid-word -- a bound stated to the model produces a better
# description than a bound applied afterwards with an ellipsis.
#
# INSTRUCTION-SHAPED AND VENDOR-NEUTRAL: no persona, no model name, no
# "you are a helpful ..." framing. It must read the same way to any
# model this role could ever be bound to.
DESCRIPTION_PROMPT = (
    "Describe what this image shows, in one or two plain sentences and at most 40 words. "
    "State the subject, the setting, and anything written on it that a reader would need "
    "to identify it. Output only the description, with no preamble and no commentary."
)


def _ask_vision(image_path: Path, prompt: str, llm) -> str:
    """One vision call: `prompt`, then `image_path`, and whatever the
    model said back -- stripped, never rewritten.

    THE SHARED BODY of `extract_image_text` and `describe_image`, which
    differ in exactly one thing: WHICH platform-behaviour constant they
    send. Everything else was identical the moment the second one
    existed -- the `llm is None` resolve, the block ORDER (instruction
    before the pixels it applies to), the local-file `ImageBlock(path=
    ...)` an offline-first box needs, and the VERBATIM STORAGE contract
    (`content or ""` guards a `None` content, `.strip()` collapses a
    whitespace-only answer to `""`, and nothing else touches the text).
    Two copies of that would be two places for the verbatim rule to
    drift, and the rule is the one this module exists to keep.

    PRIVATE, and the two public names stay: each carries its own
    docstring explaining WHY its own prompt says what it says, and each
    is what a caller (and a test) names. This helper is mechanism; they
    are the policy.
    """
    if llm is None:
        llm = gateway.get_llm(RAG_EXTRACT_ROLE)

    message = ChatMessage(
        role=MessageRole.USER,
        blocks=[TextBlock(text=prompt), ImageBlock(path=image_path)],
    )
    response = llm.chat([message])
    return str(response.message.content or "").strip()


def describe_image(image_path: Path, *, llm=None) -> str:
    """One short, plain-language description of `image_path` -- the
    OCR-only `extract_image_text`'s sibling, and the answer to "what IS
    this picture" that transcription cannot give for an image with no
    text on it.

    IDENTICAL MECHANICS, DELIBERATELY. Same `llm` threading (a caller
    that already resolved `RAG_EXTRACT_ROLE` for a whole document hands
    the built LLM in rather than re-resolving and re-health-checking),
    same two-block `ChatMessage` with the instruction before the pixels,
    same local-file `ImageBlock(path=...)` for an offline-first box, and
    the same VERBATIM STORAGE contract: `str(response.message.content or
    "").strip()` is the whole return. Whatever the model said is exactly
    what gets stored and eventually read back; rewriting it here would
    make the stored text disagree with what was actually produced.

    `""` for a blank or whitespace-only answer, exactly as
    `extract_image_text` answers for a page with no text --
    `tools.rag.media.extract_to_sidecar` treats that as "no description
    segment", never as an empty segment.

    IMAGES ONLY. `extract_to_sidecar` calls this from its single-image
    branch and never from its scanned-PDF page loop: a page of a
    contract needs its words, not a sentence about what a page of a
    contract looks like.
    """
    return _ask_vision(image_path, DESCRIPTION_PROMPT, llm)


def extract_image_text(image_path: Path, *, llm=None) -> str:
    """Ask the vision-extraction model to transcribe every piece of text
    visible in `image_path`, and return exactly what it said, stripped of
    leading/trailing whitespace -- nothing more.

    `llm`: an already-built LlamaIndex LLM to call, for a caller (`modules.
    rag.media.extract_to_sidecar`) that resolves `RAG_EXTRACT_ROLE` ONCE for
    a whole document's worth of pages/images and threads the same LLM
    through every call, rather than re-resolving (and re-health-checking)
    per page. `None` (the default) resolves fresh here, via `models.contracts.
    gateway.get_llm(RAG_EXTRACT_ROLE)` -- the ordinary role-resolve-then-
    build path, for a caller (a test, a one-off script) with no
    already-resolved LLM of its own to hand in.

    One `ChatMessage` (`role=USER`), two blocks: `TextBlock(EXTRACTION_PROMPT)`
    then `ImageBlock(path=image_path)` -- prompt first, image second, so a
    model reading its input in order sees the instruction before the pixels
    it applies to. `ImageBlock(path=...)` (not `image=...`/`url=...`):
    `image_path` is always a LOCAL file (a rasterized PDF page, or a
    normalized upload) -- this platform is offline-first, so the block reads
    the file itself rather than fetching a URL.

    VERBATIM STORAGE: `str(response.message.content or "").strip()` is the
    whole return -- no post-processing, no re-wording, no "clean up the
    model's answer" logic of any kind. Whatever the model said (or refused
    to say) is exactly what a citation eventually points back at; rewriting
    it here would make the stored text disagree with what was actually
    extracted. `content or ""` guards only against a `None` content (a
    response with no text at all -- some engines return that rather than an
    empty string for a truly blank answer); `.strip()` then collapses a
    whitespace-only answer (leading/trailing newlines a model appended
    around an otherwise-empty response) down to the empty string, which
    `extract_to_sidecar`'s own per-page loop treats as "no text on this
    page" -- skip it, log it (the exact `_read_pdf`-blank-page precedent,
    `tools.rag.readers._read_pdf`: a page an extraction produced nothing
    useful for is silently absent from the result, not stored as an empty
    segment).

    ONE function, ONE image, ONE call: no loop, no progress reporting, no
    sidecar/checkpoint writing, no metadata shape -- all of that is `modules.
    rag.media.extract_to_sidecar`'s own business, exactly as `tools.rag.
    media.transcribe_to_sidecar` owns the equivalent shape for whisper. A
    `GenerationRejected` or any other transport failure from the model call
    propagates unchanged -- this function never catches or rewrites an
    engine's own refusal/failure, the same "let it surface honestly" rule
    every model call in this platform follows.
    """
    return _ask_vision(image_path, EXTRACTION_PROMPT, llm)
