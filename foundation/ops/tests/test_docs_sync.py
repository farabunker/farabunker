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


ADR_DIR = Path(settings.BASE_DIR) / "docs" / "adr"

# `0019-chat-cluster.md`: four digits, a hyphen, a lower-case slug.
_ADR_FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# An inline Markdown link's target. Reference-style links (`[x]: url`) are
# not used anywhere in `docs/adr/` and are deliberately not matched -- a
# pattern that matched them and resolved them wrongly would be worse than
# one that says what it covers.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

_OFF_BOX_SCHEMES = ("http://", "https://", "mailto:")


def _anchor_slug(heading: str) -> str:
    """A Markdown heading, as the fragment a renderer will link it by.

    The rule the common renderers share: drop the leading `#`s, lower-case,
    remove every character that is not a letter, digit, underscore, space or
    hyphen, then turn each remaining space into a hyphen. Runs are NOT
    collapsed -- "A — B" becomes `a--b`, because the em dash is removed and
    both of its spaces survive. Collapsing them here would make this gate
    accept an anchor no renderer produces, which is the wrong direction for
    a test whose whole job is to answer "does this link land".
    """
    text = heading.strip().lstrip("#").strip()
    return re.sub(r"[^\w\- ]", "", text).lower().replace(" ", "-")


def _heading_slugs(path: Path) -> set[str]:
    return {
        _anchor_slug(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    }


def _adr_links() -> list[tuple[Path, str]]:
    """Every inline link in every ADR, paired with the file it is written in
    (which is what a relative target resolves against)."""
    found: list[tuple[Path, str]] = []
    for adr in sorted(ADR_DIR.glob("*.md")):
        for match in _MARKDOWN_LINK.finditer(adr.read_text(encoding="utf-8")):
            found.append((adr, match.group(1)))
    return found


class TestTheDecisionRecordIsNavigable:
    """The decision record is a numbered series that cross-references
    itself, and nothing checked either half until now (chat cluster, Task 15
    review). ADR 0019 was hand-verified at review time -- its number against
    `ls`, its nine links by eye -- which is exactly the check a gate should
    be doing.

    TWO CLAIMS, DELIBERATELY NARROW.

    (1) The NUMBERS are unique and dense. A duplicate means two decisions
    wearing one identity, and every `[ADR NNNN]` reference in the tree then
    points at both; a hole means a reader cannot tell a decision that was
    never written from one that was deleted. "Take the next number; do not
    guess" is the instruction every ADR-writing brief carries, and this is
    what makes it checkable.

    (2) Every relative LINK inside `docs/adr/` resolves -- the file, and the
    heading anchor when the link names one. ADRs link each other, the column
    READMEs, the design docs and the planning archive; a rename anywhere
    else in the tree breaks them silently, and a decision record whose
    citations do not land is a record you have to verify by hand before you
    can trust it.

    SCOPED TO `docs/adr/`, not to `docs/**`. The planning archive is a
    historical record of thousands of links written against trees that have
    since moved; holding it to this rule is a different and much larger
    claim, and one nobody has decided to make. The ADRs are current
    documentation and are expected to stay correct, which is why they are
    the half worth gating. Widening the walk is a one-line change to
    `_adr_links` when somebody wants to make that claim.

    Off-box links (`http`, `https`, `mailto`) are skipped: resolving them
    would be a network call, and non-negotiable 5 forbids one in any code
    path.
    """

    def test_every_adr_filename_is_a_number_and_a_slug(self):
        offenders = [
            path.name for path in sorted(ADR_DIR.glob("*.md"))
            if not _ADR_FILENAME.match(path.name)
        ]
        assert offenders == [], offenders

    def test_the_adr_numbers_are_unique_and_dense(self):
        numbers = sorted(
            int(_ADR_FILENAME.match(path.name).group(1))
            for path in ADR_DIR.glob("*.md")
            if _ADR_FILENAME.match(path.name)
        )
        duplicates = [n for n in numbers if numbers.count(n) > 1]
        assert duplicates == [], (
            f"two ADRs share a number: {sorted(set(duplicates))} -- one of "
            "them took a number instead of the next free one."
        )
        assert numbers == list(range(1, len(numbers) + 1)), (
            "the ADR numbers are not dense from 0001: "
            f"{numbers}. A hole means a reader cannot tell a decision that "
            "was never written from one that was deleted."
        )

    def test_every_relative_link_in_an_adr_resolves(self):
        offenders = []
        for adr, target in _adr_links():
            if target.startswith(_OFF_BOX_SCHEMES):
                continue
            path_part, _, fragment = target.partition("#")
            resolved = adr.parent if path_part == "" else adr.parent / path_part
            if not resolved.exists():
                offenders.append(f"{adr.name} -> {target} (no such file)")
                continue
            if fragment and resolved.is_file():
                if fragment not in _heading_slugs(resolved):
                    offenders.append(f"{adr.name} -> {target} (no such heading)")
        assert offenders == [], offenders

    def test_the_walk_is_reading_a_real_decision_record(self):
        """Anti-vacuous pin: both assertions above pass trivially on an empty
        directory or on a set of documents carrying no links at all."""
        assert len(list(ADR_DIR.glob("*.md"))) >= 15
        on_box = [
            target for _adr, target in _adr_links()
            if not target.startswith(_OFF_BOX_SCHEMES)
        ]
        assert len(on_box) >= 40, len(on_box)

    def test_the_anchor_slug_matches_the_renderers_rule(self):
        """The slug function is the load-bearing half of the link check, and
        a wrong one fails OPEN -- it would accept an anchor no renderer
        produces and reject one every renderer does. Pinned directly,
        including the run-preserving case the docstring argues for."""
        assert _anchor_slug("## The Decision") == "the-decision"
        assert _anchor_slug("### 2. Security posture: the offline spectrum") == (
            "2-security-posture-the-offline-spectrum")
        assert _anchor_slug("## `box_wide` and `resident`") == "box_wide-and-resident"
        assert _anchor_slug("## A — B") == "a--b"
