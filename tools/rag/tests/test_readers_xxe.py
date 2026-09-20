"""B-7 (security audit round 3, folded into the dependency-pin task,
H15/H41). Hand-builds a `.docx` and an `.xlsx` whose XML declares a
file-reading external entity and proves `tools/rag/readers.py` never
resolves it.

Neither `lxml` (python-docx's parser) nor `defusedxml` (openpyxl's, via
its `OPENPYXL_DEFUSEDXML` default) used to be a *declared* dependency --
both arrived only transitively, so a resolver dropping the transitive
provider, or a pin back to a permissive parser, could silently reopen
file-read XXE with nothing here to catch it. `requirements.txt` now
declares both with a floor; this is the negative test that backs the
claim.

No binary fixtures are committed, matching `tools.rag.tests.test_readers`:
a minimal valid `.docx`/`.xlsx` is built with `python-docx`/`openpyxl`
themselves, then its one XML member is rewritten in place (inside the
zip) to prepend a `<!DOCTYPE ... [<!ENTITY xxe SYSTEM "file://...">]>`
and reference `&xxe;` where the placeholder text was.

The two formats are verified to fail CLOSED in different ways -- neither
leaks the target file's contents, which is the only thing this test
cares about:

- `.docx` (python-docx's `lxml.etree.XMLParser(resolve_entities=False)`):
  parses cleanly: the entity reference is left unresolved rather than
  substituted, so the paragraph text extracted is empty.
- `.xlsx` (openpyxl's `defusedxml`-backed worksheet reader): refuses to
  parse at all, raising `defusedxml.common.EntitiesForbidden` (wrapped by
  openpyxl/pandas in a `ValueError`) the moment it sees the `<!ENTITY>`
  declaration -- fewer rows read, not one with a substituted secret.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import docx
import openpyxl
import pytest
from defusedxml.common import EntitiesForbidden

from tools.rag import readers

# A marker no legitimate fixture would ever produce, standing in for
# "the attacker's target file got read" -- checked for ABSENCE in
# whatever text/frame comes back, rather than depending on the content of
# a real file like /etc/hostname (which varies by machine and platform).
_SENTINEL = "xxe-should-never-see-this-8f3c1e"


def _rewrite_member_with_external_entity(path: Path, member: str, *, root_tag: str, target: Path) -> None:
    """Open `path` (a zip: `.docx`/`.xlsx`), rewrite the single XML
    `member` inside it to declare a SYSTEM external entity pointing at
    `target` and reference it where the fixture's placeholder text was,
    and rewrite every other member back unchanged."""
    with zipfile.ZipFile(path, "r") as zin:
        contents = {name: zin.read(name) for name in zin.namelist()}

    xml = contents[member].decode("utf-8")
    doctype = f'<!DOCTYPE {root_tag} [<!ENTITY xxe SYSTEM "file://{target}">]>\n'
    if xml.startswith("<?xml"):
        declaration, _, rest = xml.partition("?>")
        xml = f"{declaration}?>\n{doctype}" + rest.lstrip("\n")
    else:
        xml = doctype + xml
    contents[member] = xml.replace("placeholder", "&xxe;").encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in contents.items():
            zout.writestr(name, data)


def _docx_with_external_entity(tmp_path: Path, *, target: Path) -> Path:
    path = tmp_path / "evil.docx"
    d = docx.Document()
    d.add_paragraph("placeholder")
    d.save(path)
    _rewrite_member_with_external_entity(path, "word/document.xml", root_tag="w:document", target=target)
    return path


def _xlsx_with_external_entity(tmp_path: Path, *, target: Path) -> Path:
    path = tmp_path / "evil.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = "placeholder"
    wb.save(path)
    _rewrite_member_with_external_entity(path, "xl/worksheets/sheet1.xml", root_tag="worksheet", target=target)
    return path


def test_a_file_reading_external_entity_is_never_expanded_by_docx_or_xlsx_reading(tmp_path):
    """A REGRESSION GUARD, not a red-then-green fix: both refusals below
    are already-true guarantees of the underlying parsers as declared in
    `requirements.txt` today (`lxml`'s `resolve_entities=False`,
    `defusedxml`'s `EntitiesForbidden`) -- this test never failed against
    the code it runs against; its job is to keep failing this way if a
    future resolver change or version bump ever reopens either path."""
    target = tmp_path / "secret.txt"
    target.write_text(_SENTINEL, encoding="utf-8")

    # .docx: parses cleanly, entity left unresolved -- empty text, not a leak.
    docx_path = _docx_with_external_entity(tmp_path, target=target)
    docs = readers.read_prose_documents(docx_path)
    assert len(docs) == 1
    assert _SENTINEL not in docs[0].text
    assert docs[0].text.strip() == ""

    # .xlsx: refuses to parse at all -- defusedxml raises before any row
    # is handed back, so there is nothing for the sentinel to leak into.
    xlsx_path = _xlsx_with_external_entity(tmp_path, target=target)
    with pytest.raises(ValueError) as excinfo:
        readers.read_tabular_dataframe(xlsx_path)
    cause = excinfo.value.__cause__
    assert isinstance(cause, EntitiesForbidden), (
        f"expected openpyxl's read to be refused by defusedxml.EntitiesForbidden, got {cause!r}"
    )
