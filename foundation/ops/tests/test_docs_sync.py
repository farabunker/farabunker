"""Doc-vs-constant sync test (W3 review M1): every printed sequence /
warning `foundation.ops.backup`/`foundation.ops.restore` carry as a Python
constant is documented as claiming to be "reproduced identically" or
"printed verbatim" in `docs/OPERATIONS.md` -- this test is the thing that
actually enforces that claim stays true, rather than trusting the comment
next to each constant. No Django needed -- pure file-text comparison."""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from foundation.ops.backup import DOCUMENTED_BACKUP_SEQUENCE, LIVE_POSTGRES_WARNING
from foundation.ops.restore import RECOVERY_SEQUENCE, RESTORE_DB_SEQUENCE

OPERATIONS_MD = Path(settings.BASE_DIR) / "docs" / "OPERATIONS.md"
IDENTITY_CHECKS_PY = Path(settings.BASE_DIR) / "identity" / "checks.py"
IDENTITY_SERVICES_PY = Path(settings.BASE_DIR) / "identity" / "services.py"


def _docs_text() -> str:
    return OPERATIONS_MD.read_text()


class TestBackupSequenceMatchesDocs:
    def test_documented_backup_sequence_is_verbatim_in_docs(self):
        assert DOCUMENTED_BACKUP_SEQUENCE in _docs_text()


class TestRestoreSequencesMatchDocs:
    def test_restore_db_sequence_is_verbatim_in_docs(self):
        assert RESTORE_DB_SEQUENCE in _docs_text()

    def test_recovery_sequence_is_verbatim_in_docs(self):
        assert RECOVERY_SEQUENCE in _docs_text()


class TestLivePostgresWarningMatchesDocs:
    def test_live_postgres_warning_is_verbatim_in_docs(self):
        assert LIVE_POSTGRES_WARNING in _docs_text()


_OPEN_POSTURE_SECTION = "“Leaving the open posture”"


class TestOpenPostureRunbookMatchesDocs:
    """H22 review round 1, MINOR: `identity/checks.py`'s W003 hint and
    `identity/services.py`'s DEBUG refusal each point an operator at
    `docs/OPERATIONS.md`'s "Leaving the open posture" section by its
    exact quoted title -- pure file-text, same as the class above, so
    that a rename on either side (the section heading, or either
    source string) is caught rather than trusted by inspection.
    """

    def test_the_section_the_two_source_strings_name_exists_in_the_docs(self):
        checks_src = IDENTITY_CHECKS_PY.read_text()
        services_src = IDENTITY_SERVICES_PY.read_text()
        assert _OPEN_POSTURE_SECTION in checks_src, (
            "identity/checks.py's W003 hint no longer names "
            f"{_OPEN_POSTURE_SECTION!r} -- update this pin if the section "
            "was deliberately renamed."
        )
        assert _OPEN_POSTURE_SECTION in services_src, (
            "identity/services.py's DEBUG refusal no longer names "
            f"{_OPEN_POSTURE_SECTION!r} -- update this pin if the section "
            "was deliberately renamed."
        )
        assert "## Leaving the open posture" in _docs_text(), (
            "docs/OPERATIONS.md has no \"Leaving the open posture\" section, "
            "but identity/checks.py and identity/services.py both point an "
            "operator at one."
        )

    def test_the_debug_refusal_is_verbatim_in_the_docs_blockquote(self):
        """The message `_refuse_a_switch_away_from_open` actually raises
        when `settings.DEBUG` is on, reconstructed from the Python
        source's own string-literal concatenation, must read identically
        (whitespace/line-wrap aside) to the blockquote
        "Switching posture: the three refusals" quotes for an operator --
        the two are the same sentence in two files, and only this test
        notices when they drift."""
        services_src = IDENTITY_SERVICES_PY.read_text()
        code_match = re.search(
            r"if settings\.DEBUG:\s*raise ServiceRefused\(\s*((?:\"[^\"]*\"\s*)+)\)",
            services_src,
        )
        assert code_match, (
            "identity/services.py's DEBUG-branch `raise ServiceRefused(...)` "
            "was not found where this pin expects it -- re-derive and update "
            "the regex if the branch moved or was rewritten."
        )
        message = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', code_match.group(1)))

        docs_match = re.search(r"> DEBUG is on\..*?(?=\n\n)", _docs_text(), re.DOTALL)
        assert docs_match, (
            "docs/OPERATIONS.md's \"DEBUG is on\" blockquote (under "
            "\"Switching posture: the three refusals\") is missing."
        )
        blockquote = " ".join(
            line.strip().lstrip(">").strip()
            for line in docs_match.group(0).splitlines()
        )

        def _normalize(text: str) -> str:
            return re.sub(r"\s+", " ", text).strip()

        assert _normalize(message) == _normalize(blockquote), (
            "identity/services.py's DEBUG refusal and docs/OPERATIONS.md's "
            "matching blockquote have drifted apart:\n"
            f"  code: {_normalize(message)!r}\n"
            f"  docs: {_normalize(blockquote)!r}"
        )


ADR_0009 = Path(settings.BASE_DIR) / "docs" / "adr" / "0009-document-store-and-categories.md"


class TestCategoryDescriptionRemovalStaysDocumented:
    """D1: ADR 0009 listed `Category.description` for weeks after the
    column was dropped. A doc that names a field is only as good as the
    model, so this pins the ONE claim that went wrong rather than
    inventing a general field-scanner nobody would maintain. Both halves
    matter: the field must actually be gone, AND the ADR must still show
    its own struck-through record of the removal -- either one
    regressing silently is the drift this test exists to catch."""

    def test_category_has_no_description_field(self):
        from tools.rag.models import Category

        field_names = {f.name for f in Category._meta.get_fields()}
        assert "description" not in field_names, (
            "Category.description is back; docs/adr/0009 must say so again."
        )

    def test_adr_0009_still_shows_the_struck_through_removed_field(self):
        adr = ADR_0009.read_text(encoding="utf-8")
        assert "~~`description` (optional)~~" in adr, (
            "ADR 0009's strikethrough marker for the removed "
            "Category.description field is gone -- restore it, or "
            "replace it with an equally honest record that the field "
            "was removed."
        )
