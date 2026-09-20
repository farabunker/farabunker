"""Task 3 (native image input, gated): `agents.runtime.prompt.
native_media_types` and the native-image branch of `_carrying_
attachments_block` -- split out of `test_prompt.py` (C-56, `foundation/
ops/tests/test_column_boundaries.py::test_no_test_module_grows_past_
the_split_threshold`) once Task 3's own 15 tests pushed that module past
the 2,100-line threshold. `TestTheCarryingAttachmentsBlock` and every
other pre-existing class in `test_prompt.py` stay put -- this module
adds no coverage of its own, it only relocates Task 3's own two classes
verbatim.

NEW MODULE, THE SAME SHAPE the in-repo split precedent already takes
(`tools/rag/tests/test_views_ask_priority.py`'s own docstring): a whole
file another session could be editing is not this fix's to touch beyond
the one clean extraction, so nothing in `test_prompt.py` itself changes
except the removal of these two classes.

BOTH CLASSES ARE FULLY SELF-CONTAINED -- neither one reaches a helper
defined elsewhere in `test_prompt.py` (no shared `_image()`/`_row()`
module-level function, unlike `TestTheImageAttachmentLines`'s own
`_image`); `TestNativeImageBlocks`' `_row`/`_resolved`/`_open_gate`/
`_text`/`_images` are its own instance methods. Moved verbatim, with no
import across test modules (the repo's own convention: a split module
either imports from a shared `_helpers.py`, or -- as here -- needs
nothing from its sibling at all).
"""
from __future__ import annotations

from types import SimpleNamespace

from llama_index.core.llms import ImageBlock, TextBlock

from agents.runtime import prompt
from identity.contracts.principals import OPEN_PRINCIPAL


class TestNativeMediaTypes:
    """`native_media_types(resolved)` -- Task 3's own capability gate:
    `models.contracts.catalog.find(resolved.model_id).accepts`, read
    LIVE off the real catalog every call, never cached or mocked, so a
    box that adds a multimodal chat connection sees the gate open
    without a code change. Empty for anything the catalog does not
    recognise -- an unbound model is text-only until proven otherwise,
    the same direction `supports_tool_calling`'s own `None`-means-
    "assume nothing" reads (`agents/runtime/loop.py`)."""

    def test_resolved_none_answers_an_empty_set(self):
        assert prompt.native_media_types(None) == frozenset()

    def test_an_unknown_model_id_answers_an_empty_set(self):
        resolved = SimpleNamespace(model_id="totally-unknown-model:1b")
        assert prompt.native_media_types(resolved) == frozenset()

    def test_a_known_text_only_entry_answers_an_empty_set(self):
        """`qwen2.5:7b`'s own catalog entry predates `accepts` and
        carries the field's own default, `()` -- `CatalogEntry.accepts`'s
        own docstring promises this is additive-only."""
        resolved = SimpleNamespace(model_id="qwen2.5:7b")
        assert prompt.native_media_types(resolved) == frozenset()

    def test_a_known_vision_entry_reports_image(self):
        resolved = SimpleNamespace(model_id="llava:7b")
        assert prompt.native_media_types(resolved) == frozenset({"image"})

    def test_a_temporary_catalog_entry_is_read_live_not_a_frozen_snapshot(self):
        """Proven with a FRESH entry (`capability="chat"`, never
        `"vision"` -- the two permanent entries above happen to be
        vision-only, which is not the shape a real gated multimodal
        CHAT connection takes) appended and removed in `finally`, rather
        than relying only on the two permanent catalog rows: this
        confirms the gate reads `models.contracts.catalog.CATALOG`
        itself at call time, not a copy this module took at import."""
        from models.contracts.catalog import CATALOG, CatalogEntry

        entry = CatalogEntry(name="Test Multimodal Chat", engine="ollama",
                             model_id="test-multimodal-chat:1b", capability="chat",
                             accepts=("image",))
        CATALOG.append(entry)
        try:
            resolved = SimpleNamespace(model_id="test-multimodal-chat:1b")
            assert prompt.native_media_types(resolved) == frozenset({"image"})
        finally:
            CATALOG.remove(entry)


class TestNativeImageBlocks:
    """Task 3: native image input, gated by `native_media_types`, with
    `_INLINE_STILL_PROCESSING_LINE` as the honest fallback for every
    failure mode -- missing/unreadable/oversized/over-count/broken file,
    the gate closed, or no principal at all.

    `agents.attachments.attachment_image_path` is monkeypatched
    throughout -- the SAME "LOCAL ON PURPOSE" patchability seam
    `inline_attachment_text` already relies on in `TestTheCarrying
    AttachmentsBlock` (`test_prompt.py`), so a test replacing `agents.
    attachments.attachment_image_path` actually reaches the replacement
    (`_carrying_attachments_block`'s own comment on why the import
    stays local). What the REAL resolver does for an unreadable row or
    a `LookupError` is `agents/tests/test_attachment_seam.py`'s job, not
    this module's -- these tests take `attachment_image_path`'s own
    contract as given and exercise the CALLER'S reaction to it.
    """

    def _row(self, *, doc_id=1, title="photo.png", status="processing",
             turn_id=1, readable=True):
        return {"id": doc_id, "title": title, "status": status, "turn_id": turn_id,
               "is_image": True, "readable": readable}

    def _resolved(self, model_id="native-image-model:1b"):
        return SimpleNamespace(model_id=model_id)

    def _open_gate(self, model_id="native-image-model:1b"):
        """A temporary `CatalogEntry` (`capability="chat"`, `accepts=
        ("image",)`), appended to the REAL `models.contracts.catalog.
        CATALOG` -- the caller removes it, in `finally`, by calling the
        returned callable. `TestNativeMediaTypes` above already proves
        `native_media_types` reads this live; here it is only the
        cheapest way to open Task 3's own gate for real rather than by
        mocking the gate function itself."""
        from models.contracts.catalog import CATALOG, CatalogEntry

        entry = CatalogEntry(name="t", engine="ollama", model_id=model_id,
                             capability="chat", accepts=("image",))
        CATALOG.append(entry)
        return lambda: CATALOG.remove(entry)

    def _text(self, blocks):
        texts = [b.text for b in blocks if isinstance(b, TextBlock)]
        assert len(texts) == 1, "exactly one TextBlock per carrying message"
        return texts[0]

    def _images(self, blocks):
        return [b for b in blocks if isinstance(b, ImageBlock)]

    def test_gate_closed_no_principal_yields_one_textblock_no_images_resolver_never_called(
            self, monkeypatch, tmp_path):
        """`principal=None`/`resolved=None` are the default for every
        caller before this round -- the native branch must not run at
        all, proven by making the resolver raise if it is even called.

        Tightened (final review, deliberate re-pin): the prior assertion
        built its own "expected" out of the very `blocks` it was
        checking (`blocks == [TextBlock(text=self._text(blocks))]`) --
        that can never fail on the TextBlock's own text, since the text
        came from `blocks` itself; only the `len(blocks) == 1` the
        equality happened to imply was doing real work. This asserts the
        same shape directly instead: exactly one block, and it is a
        TextBlock -- no ImageBlock and nothing else beside it."""
        def _boom(doc_id, principal):
            raise AssertionError("attachment_image_path must not be called: gate is closed")

        monkeypatch.setattr("agents.attachments.attachment_image_path", _boom)
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        blocks = prompt._carrying_attachments_block([self._row()], 1)
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextBlock)
        assert self._images(blocks) == []
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)

    def test_gate_closed_by_the_catalog_still_falls_back_honestly(
            self, monkeypatch, tmp_path):
        """A principal IS present, but `resolved`'s own model_id accepts
        nothing natively (the default/unbound catalog answer) -- the
        native branch still never runs."""
        path = tmp_path / "photo.png"
        path.write_bytes(b"bytes")
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: str(path))
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        blocks = prompt._carrying_attachments_block(
            [self._row()], 1, resolved=self._resolved("unknown-model:1b"),
            principal=OPEN_PRINCIPAL)
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)
        assert self._images(blocks) == []

    def test_gate_open_with_a_loadable_path_attaches_the_image_and_replaces_the_line(
            self, monkeypatch, tmp_path):
        path = tmp_path / "photo.png"
        path.write_bytes(b"stand-in image bytes")
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: str(path))
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row()], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        images = self._images(blocks)
        text = self._text(blocks)
        assert len(images) == 1
        assert images[0].path is not None and str(images[0].path) == str(path)
        assert prompt._INLINE_STILL_PROCESSING_LINE not in text
        assert "photo.png" in text

    def test_a_row_whose_readable_is_false_contributes_no_block(self, monkeypatch, tmp_path):
        """`attachment_image_path`'s own gate (`artifact_file_for` ->
        `readable_document`, `agents/tests/test_attachment_seam.py`'s
        job to pin for real) is what turns an unreadable row into
        `None` -- simulated here at this module's own seam, the
        identical level every other test in this class works at."""
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: None)
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row(readable=False)], 1, resolved=self._resolved(),
                principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert self._images(blocks) == []
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)

    def test_a_missing_path_falls_back_honestly(self, monkeypatch):
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: None)
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row()], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert self._images(blocks) == []
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)

    def test_an_unloadable_file_falls_back_without_raising(self, monkeypatch, tmp_path):
        """A path that PASSES `attachment_image_path`'s own on-disk
        check (it exists) but fails `resolve_image()` -- an empty file
        is the simplest real trigger (`ValueError("resolve_image
        returned zero bytes")`) -- must degrade to the honest line
        rather than raise or leave a broken `ImageBlock` in the list."""
        path = tmp_path / "empty.png"
        path.write_bytes(b"")
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: str(path))
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row()], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert self._images(blocks) == []
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)

    def test_an_oversized_file_falls_back_like_any_other_failure(self, monkeypatch, tmp_path):
        path = tmp_path / "huge.png"
        path.write_bytes(b"x" * (prompt._NATIVE_IMAGE_BYTE_CAP + 1))
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: str(path))
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row()], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert self._images(blocks) == []
        assert prompt._INLINE_STILL_PROCESSING_LINE in self._text(blocks)

    def test_a_file_exactly_at_the_byte_cap_still_attaches(self, monkeypatch, tmp_path):
        """Ruling 4's own "at cap doesn't, cap+1 does" pairing, applied
        to the byte bound the same way it was already applied to the
        text budgets above."""
        path = tmp_path / "atcap.png"
        path.write_bytes(b"x" * prompt._NATIVE_IMAGE_BYTE_CAP)
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: str(path))
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [self._row()], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert len(self._images(blocks)) == 1

    def test_over_count_images_fall_back_past_the_cap(self, monkeypatch, tmp_path):
        cap = prompt._NATIVE_IMAGE_COUNT_CAP
        rows = []
        for i in range(cap + 2):
            path = tmp_path / f"photo{i}.png"
            path.write_bytes(b"bytes")
            rows.append(self._row(doc_id=i, title=f"photo{i}.png"))

        paths = {row["id"]: str(tmp_path / f"photo{row['id']}.png") for row in rows}
        monkeypatch.setattr("agents.attachments.attachment_image_path",
                            lambda doc_id, principal: paths[doc_id])
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                rows, 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert len(self._images(blocks)) == cap
        text = self._text(blocks)
        assert text.count(prompt._INLINE_STILL_PROCESSING_LINE) == 2

    def test_an_older_turns_image_contributes_no_block(self, monkeypatch, tmp_path):
        """Ruling 3, applied to the native branch: only the CARRYING
        turn's own rows are even candidates -- an earlier turn's image
        is filtered out before the gate is ever consulted, proven by
        making the resolver raise if it is reached at all."""
        def _boom(doc_id, principal):
            raise AssertionError("attachment_image_path must not be called for another turn")

        monkeypatch.setattr("agents.attachments.attachment_image_path", _boom)
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        row = self._row(turn_id=999)   # NOT the carrying turn (1)
        close_gate = self._open_gate()
        try:
            blocks = prompt._carrying_attachments_block(
                [row], 1, resolved=self._resolved(), principal=OPEN_PRINCIPAL)
        finally:
            close_gate()
        assert blocks == []   # nothing this turn carried at all
