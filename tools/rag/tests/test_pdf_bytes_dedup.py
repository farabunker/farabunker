"""C-61 pin: `_make_pdf_bytes` used to be written out, byte-identically,
in five separate modules of `tools/rag/tests`; it is now one function,
`make_pdf_bytes`, in this package's own `_helpers.py`.

Deliberately its own file, not folded into any of the five modules it
checks: this test reads each of those five files' own source text and
asserts neither the old private name nor a local re-definition of the new
one appears in it -- literally spelling `def _make_pdf_bytes`/
`def make_pdf_bytes` inside one of those five files' own source would
make this test see itself and fail on its own assertion string.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings


def test_no_rag_test_module_builds_its_own_pdf_bytes():
    """C-61. Five modules in one package carried a byte-identical PDF
    builder. This package has a `_helpers.py`, and that is where a
    package's shared scaffolding goes."""
    for name in ("test_ingest", "test_jobs", "test_readers", "test_services", "test_transcode"):
        text = (Path(settings.BASE_DIR) / f"tools/rag/tests/{name}.py").read_text()
        assert "def _make_pdf_bytes" not in text
        assert "def make_pdf_bytes" not in text
