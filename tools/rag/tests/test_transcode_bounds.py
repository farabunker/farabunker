"""
B-4 (round-3 hardening H29): `tools.rag.transcode.rasterize_pdf_page` no
longer renders a PDF page at a scale derived ONLY from a fixed multiplier
of the page's own declared point size -- a hand-built page can claim
whatever `MediaBox` it likes (`pypdfium2` does not enforce the PDF
format's own 14400-point page-size ceiling), and the old fixed
`_PDF_RENDER_SCALE` turned that claim directly into an allocation before
any `max_edge` downscale ever ran. Review round 1, finding 1: the render
scale is now GRADUATED (`min(_PDF_RENDER_SCALE, sqrt(MAX_RENDER_PIXELS /
(width_pt * height_pt)))`) rather than a step function -- a large-but-
legitimate page renders at a smaller, continuously-derived scale, and
only a page whose derived scale falls below `RENDER_SCALE_FLOOR` is
refused outright. This module is a NEW test module (round-2 constraint
18, restated in this plan as constraint 26): `tools/rag/tests/` is a
whole directory another session edits, so a task needing rag tests adds
a module rather than appending to an existing one.

A STANDALONE PDF/PNG-crafting kit, not an import of `tools.rag.tests.
_helpers.make_pdf_bytes` -- that helper's `MediaBox` is fixed at
`[0 0 200 200]` (too small to ever reach `rasterize_pdf_page`'s new area
ceiling), so this module needs its own builder parameterized on
declared page size instead.
"""
from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from tools.rag import transcode


def one_page_pdf(tmp_path, *, width_pt: float = 612, height_pt: float = 792, name: str = "x.pdf"):
    """Hand-assemble a minimal valid one-page PDF whose `MediaBox` is
    exactly `[0 0 width_pt height_pt]` -- the one dial `rasterize_pdf_
    page`'s new area bound reads, via `PdfPage.get_size()`. No content
    stream (an empty page, the closer proxy for a scanned/image-only
    page's PDF structure `tools.rag.tests._helpers.make_pdf_bytes`'s own
    docstring already uses this same reasoning for): this module never
    needs to recover text from the page, only its declared size.
    """
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt:g} {height_pt:g}] "
            f"/Resources << >> /Contents 4 0 R >>"
        ).encode(),
        4: b"<< /Length 0 >>\nstream\n\nendstream",
    }
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n".encode())
        buf.write(objects[num])
        buf.write(b"\nendobj\n")

    xref_offset = buf.tell()
    count = len(objects) + 1
    buf.write(f"xref\n0 {count}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for num in sorted(objects):
        buf.write(f"{offsets[num]:010d} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())

    path = tmp_path / name
    path.write_bytes(buf.getvalue())
    return path


def png_size(png_bytes: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png_bytes)).size


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def crafted_png_claiming(width: int, height: int) -> bytes:
    """A syntactically valid PNG whose `IHDR` claims `width`x`height` but
    which carries no pixel data at all -- Pillow's own decompression-bomb
    guard (`Image._decompression_bomb_check`) reads the claimed size from
    the header alone, before ever decoding a pixel, so this file is a
    few dozen bytes regardless of how huge a size it declares (the exact
    "small file, huge claimed size" shape B-4's imaging-ceiling half
    covers for `normalize_image`, as opposed to `rasterize_pdf_page`'s
    own PDF-`MediaBox` half above)."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit depth, RGB colour type
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IEND", b"")


class TestB4ThePageAreaIsBoundedBeforeTheAllocation:
    def test_a_huge_declared_page_is_refused_by_name(self, tmp_path):
        pdf = one_page_pdf(tmp_path, width_pt=200000, height_pt=200000)
        with pytest.raises(transcode.RenderAreaExceededError) as excinfo:
            transcode.rasterize_pdf_page(pdf, 1)
        assert "too large to render" in str(excinfo.value)

    def test_the_new_type_is_a_value_error_so_existing_handlers_still_see_it(self):
        assert issubclass(transcode.RenderAreaExceededError, ValueError)

    def test_the_out_of_range_page_number_is_still_a_plain_value_error(self, tmp_path):
        """`transcode.py`'s existing out-of-range `page_number` refusal is
        a different refusal and keeps its own bare `ValueError`, so a
        caller can tell "page 9 of a 3-page file" from "this page is too
        big to draw"."""
        with pytest.raises(ValueError) as excinfo:
            transcode.rasterize_pdf_page(one_page_pdf(tmp_path), 9)
        assert not isinstance(excinfo.value, transcode.RenderAreaExceededError)

    def test_a_large_but_allowed_page_renders_at_a_chosen_scale_not_a_fixed_one(self, tmp_path):
        """The 4000x4000-point page the audit measured at 457 MB (8000x
        8000 at the old fixed `_PDF_RENDER_SCALE`): review round 1 finding
        1 -- the CHOSEN scale is `min(2.0, sqrt(25_000_000 / 16_000_000))
        == 1.25`, not the fixed 2.0, so the rendered bitmap is 5000x5000
        (25,000,000 pixels, exactly `MAX_RENDER_PIXELS`), a real reduction
        from the unbounded old behaviour -- not just a final size that
        happens to satisfy `max_edge` after a downscale. `max_edge=6000`
        (larger than 5000) is passed so `_downscale_to_png` performs no
        further resize, and the asserted size is the actual render, not a
        `max_edge`-driven one.
        """
        pdf = one_page_pdf(tmp_path, width_pt=4000, height_pt=4000)
        png = transcode.rasterize_pdf_page(pdf, 1, max_edge=6000)
        assert png_size(png) == (5000, 5000)

    def test_a_large_but_allowed_page_respects_max_edge_too(self, tmp_path):
        """The same page, but with the shipped default `max_edge=1600` --
        the reduced 5000x5000 render is then downscaled exactly as any
        other oversized render is."""
        pdf = one_page_pdf(tmp_path, width_pt=4000, height_pt=4000)
        png = transcode.rasterize_pdf_page(pdf, 1)
        assert max(png_size(png)) <= 1600

    def test_an_ordinary_page_is_unchanged(self, tmp_path):
        """A4 at the shipped scale, byte-comparable to today's output --
        this fix must not change what a normal document rasterises to.
        A4's declared area (595x842pt) is nowhere near large enough to
        move the newly-chosen scale off the shipped `_PDF_RENDER_SCALE`
        (the crossover is a declared area of `MAX_RENDER_PIXELS /
        _PDF_RENDER_SCALE**2` -- a 2500x2500pt square page, ~35in on a
        side), so the render call and the `_downscale_to_png` tail are
        byte-for-byte the same code path as before this fix.
        """
        pdf = one_page_pdf(tmp_path, width_pt=595, height_pt=842)
        actual = transcode.rasterize_pdf_page(pdf, 1)

        pdfium = transcode._require_pypdfium2()
        PILImage = transcode._require_pillow()
        with pdfium.PdfDocument(str(pdf)) as pdf_doc:
            page = pdf_doc[0]
            try:
                bitmap = page.render(scale=transcode._PDF_RENDER_SCALE)
                try:
                    pil_image = bitmap.to_pil()
                finally:
                    bitmap.close()
            finally:
                page.close()
        expected = transcode._downscale_to_png(pil_image, 1600, PILImage)

        assert actual == expected

    def test_the_imaging_pixel_ceiling_is_set_explicitly(self):
        """Inheriting the library's default still permits a ~1.5 GB peak
        for a small crafted image."""
        transcode._require_pillow()
        assert Image.MAX_IMAGE_PIXELS == transcode.MAX_RENDER_PIXELS

    def test_the_refusal_reads_like_the_out_of_range_page_number_one(self, tmp_path):
        """Same honest-error register the rasteriser already uses: names
        the page and the file, no trailing period -- `transcode.py`'s
        own out-of-range-page-number message (`page_number=... is out of
        range for {pdf} (1-based, has N page(s))`) is written the same
        way."""
        pdf = one_page_pdf(tmp_path, width_pt=200000, height_pt=200000)
        with pytest.raises(transcode.RenderAreaExceededError) as excinfo:
            transcode.rasterize_pdf_page(pdf, 1)
        message = str(excinfo.value)
        assert str(pdf) in message
        assert "200000" in message  # the page's own declared point size
        assert str(transcode.MAX_RENDER_PIXELS) in message
        assert str(transcode.RENDER_SCALE_FLOOR) in message
        assert not message.endswith(".")

    def test_a_zero_area_page_is_refused_by_name_not_a_zerodivisionerror(self, tmp_path):
        """Minor (review round 1): a degenerate declared page size would
        otherwise divide by zero computing `sqrt(MAX_RENDER_PIXELS /
        area_pt)` -- refused by NAME instead, the same
        `RenderAreaExceededError` every other refusal in this module
        raises, never a raw `ZeroDivisionError`.

        Exercised via `transcode._choose_pdf_render_scale` directly
        (`_choose_pdf_render_scale`'s own docstring), not a crafted PDF
        file: `pypdfium2` itself silently substitutes a default page size
        for a genuinely degenerate `MediaBox` (confirmed empirically --
        `[0 0 0 0]` reports `get_size() == (612.0, 792.0)`, Letter),
        so there is no real PDF file that reaches this guard with
        `area_pt <= 0` today; it is defensive, and this is the only way
        to actually exercise it.
        """
        pdf = tmp_path / "zero.pdf"
        with pytest.raises(transcode.RenderAreaExceededError) as excinfo:
            transcode._choose_pdf_render_scale(0.0, 0.0, page_number=1, pdf=pdf)
        assert not isinstance(excinfo.value, ZeroDivisionError)
        assert str(pdf) in str(excinfo.value)

        with pytest.raises(transcode.RenderAreaExceededError):
            transcode._choose_pdf_render_scale(100.0, -5.0, page_number=1, pdf=pdf)


class TestB4TheImagingCeilingAlsoGuardsNormalizeImage:
    def test_a_small_file_claiming_a_huge_size_is_refused(self, tmp_path):
        """The `normalize_image` half of B-4 (addendum; review round 1
        finding 3): a crafted PNG header can claim any size without the
        file itself being large -- `Image.MAX_IMAGE_PIXELS` set to
        `transcode.MAX_RENDER_PIXELS` (`_require_pillow`) is what makes
        Pillow's own decompression-bomb guard the enforcement point here,
        not a second check this module would have to maintain.

        8000x8000 (64,000,000 pixels) is chosen DELIBERATELY, not an
        arbitrary large number: it sits strictly between `2 *
        transcode.MAX_RENDER_PIXELS` (50,000,000) and `2 *` Pillow's own
        stock `MAX_IMAGE_PIXELS` default (~178,956,970) -- past the
        ceiling this platform explicitly chose, but comfortably UNDER
        Pillow's own un-overridden default (~89,478,485) entirely, so
        this claimed size would raise NOTHING at all without
        `_require_pillow`'s explicit override (see the fix-round-1
        report's RED evidence: the assertion below fails with
        `_require_pillow`'s `Image.MAX_IMAGE_PIXELS = MAX_RENDER_PIXELS`
        line commented out). A test using an arbitrarily larger claimed
        size (comfortably over Pillow's own default too) would pass
        either way, and would not prove this module's own explicit
        ceiling is the thing doing the refusing.
        """
        bomb = tmp_path / "bomb.png"
        bomb.write_bytes(crafted_png_claiming(8000, 8000))
        assert bomb.stat().st_size < 1024

        with pytest.raises(Image.DecompressionBombError):
            transcode.normalize_image(bomb)
