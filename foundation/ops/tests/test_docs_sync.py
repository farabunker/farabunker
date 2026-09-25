"""Three doc gates, all pure file-text -- no Django, no network.

ONE: DOC-VS-CONSTANT SYNC (W3 review M1). Every printed sequence or
warning `foundation.ops.backup`/`foundation.ops.restore` carry as a
Python constant is documented in `docs/OPERATIONS.md` as "reproduced
identically" or "printed verbatim"; the classes at the top of this
module are what actually enforce that claim, rather than trusting the
comment next to each constant. The same shape covers `identity/checks.py`
and `identity/services.py`'s named OPERATIONS section, and ADR 0009's
record of a removed field.

TWO: THE QUEUE-GOVERNANCE LOG VOCABULARY (F6, queue memory governance
review). `TestQueueGovernanceLogVocabularyMatchesDocs` holds up
`docs/OPERATIONS.md`'s claim that its queue-governance tables carry
*every* log line that track added or changed -- in both directions, so
neither a reworded `logger.info` in `models/queue/worker.py` nor a new
one with no row in the document can pass. It reads the three sources
through the AST (`_string_constants`), which is why the reason strings
`_unload_endpoint` and `_log_unload` pass as CALL ARGUMENTS are pinned
and their docstrings deliberately are not.

THREE: THE DECISION RECORD IS NAVIGABLE (chat cluster, Task 15 review).
`TestTheDecisionRecordIsNavigable` at the foot of this module asserts
that ADR numbers are unique and dense and that every relative link inside
`docs/adr/` resolves -- the file, and the heading anchor when the link
names one. It shares this module because it is the same kind of check
(read the docs as text, compare them with the tree) and nothing here
needs a database.
"""
from __future__ import annotations

import ast
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


WORKER_PY = Path(settings.BASE_DIR) / "models" / "queue" / "worker.py"
CLAIM_PY = Path(settings.BASE_DIR) / "models" / "queue" / "claim.py"
BINDINGS_PY = Path(settings.BASE_DIR) / "models" / "registry" / "bindings.py"

_QUEUE_GOVERNANCE_SOURCES = (WORKER_PY, CLAIM_PY, BINDINGS_PY)

# The completeness claim this class holds up, quoted from the section it
# polices so a rewording of the claim itself is visible here too.
_COMPLETENESS_CLAIM = (
    "the list above is **every log line this track added or changed**, not a\nselection"
)

# EVERY STABLE FRAGMENT `docs/OPERATIONS.md`'s queue-governance tables
# quote, each of which must appear BOTH in one of the three sources above
# and in the document. Fragments rather than whole lines because the
# document renders `%s` as `<model>`/`<endpoint>`/`N` for a reader and
# escapes `|` inside its tables -- the fragments are the parts that are
# genuinely the same bytes on both sides, which is what can drift.
#
# A `%`-conversion inside a fragment is matched against the SOURCE
# verbatim and against the DOCUMENT as a wildcard (the literal parts
# either side must appear, in order), so a format string that keeps its
# words while changing its placeholders is still caught source-side.
_QUOTED_LOG_FRAGMENTS = (
    # --- the unload line and its three reasons. The reasons are CALL
    # ARGUMENTS, not format strings, which is exactly why they need a pin
    # of their own: nothing else in this file would notice one being
    # reworded.
    "worker: unload %s at %s (%s), scope %s, %s -- %s",
    "not needed at an exclusive endpoint",
    "over budget",
    "precautionary barrier",
    # --- the eviction pass, and the two things it can fail to do
    "worker: eviction pass (%s): swept %s endpoints, %s resident, "
    "%s admitted marginal, budget %s",
    "worker: eviction could not list installed models at %s (%s) -- "
    "that endpoint is skipped this tick",
    "worker: no precautionary barrier call at %s (%s) -- nothing registered "
    "there supplies a model id",
    "eviction degrades to today's idle-timeout behavior for it",
    "worker: eviction skipped the whole endpoint %s (%s) -- one unload there "
    "frees everything, and %s is protected by live work",
    # --- the two barrier WARNING stems, and the failure they bound
    "is protected by live work; the job is queued again for",
    "refused to release %s; the job is queued again for",
    "worker: job %s failed -- ",
    "did not release memory for this exclusive job after",
    # --- the registry's refused-lowering pair (one message, two levels)
    "registry: refused to lower %s on connection %s (%r): standing %s bytes, "
    "refused reading %s bytes",
    # --- the worker-lifecycle table
    "the host appears to have slept for about",
    "worker: waiting for the database schema (the queue's tables are not there yet",
    "database schema not ready yet; skipping this tick",
    "heartbeat thread started",
    "heartbeat thread exiting -- every running row this worker holds now "
    "depends on the tick thread alone",
    "heartbeat write failed; retrying next iteration",
    "refusing to submit job %s twice -- an attempt is still in flight here",
    "budget-driven eviction hit its cap of %s unload calls this tick",
    "could not read the response timeout or the per-kind wait ceiling",
)

# Logger calls in those three modules that PRE-DATE the queue
# memory-governance track (2026-09-21) and are therefore outside the
# completeness claim above. Verified against the track's own commit
# range: not one of these lines is added or changed by it. This roster is
# what turns the fragment list into a COMPLETENESS gate rather than a
# sample -- a logger call that is neither matched by a fragment nor
# listed here is a new or reworded line with no row in
# `docs/OPERATIONS.md`, which is the drift the section's own claim
# forbids. Adding a line here is a deliberate act that says "this one is
# not part of that vocabulary", not a way to quiet the test.
_LOG_LINES_PREDATING_THE_TRACK = frozenset({
    "worker %s: starting",
    "worker %s: received signal %s, stopping",
    "worker %s: tick() raised -- stopping",
    "worker %s: drained cleanly, exiting",
    "worker %s: exiting with %d job attempt(s) still running past "
    "the %ss grace period (requeued: %s)",
    "worker %s: job %s still running after %ss grace -- requeued "
    "(claim_token cleared; attempts unchanged, a drained job is not a crashed job)",
    "worker: stale writeback discarded for job %s",
    "worker: job %s's future raised past _execute's own guard -- this is a bug "
    "(_execute is designed to never raise); its row is left however _execute's "
    "own writeback (or lack of one) already left it",
    "worker: job %s handler raised",
    "worker: post-execution measurement raised for job %s",
    "worker: malformed model ref skipped during measurement: %r",
    "worker: loaded_footprint measurement raised for %s at %s",
    "worker: record_measured_footprint raised for %s at %s",
    "claim: job %s orphaned (worker %r stopped heartbeating) -- requeued, attempts=1",
    "claim: job %s orphaned a second time (worker %r) -- failed permanently",
})


def _string_constants(path: Path) -> list[str]:
    """Every string literal in `path` that is part of the program rather
    than its prose, with implicit concatenation already flattened.

    THE AST, NOT THE FILE TEXT, for the flattening: almost every line in
    the queue's vocabulary is written as adjacent literals across three
    or four source lines, so a plain `fragment in source_text` would fail
    on wrapping alone -- and a test that fails on wrapping is a test
    people delete.

    DOCSTRINGS AND BARE STRING STATEMENTS ARE EXCLUDED, which is not a
    nicety: `_unload_endpoint`'s and `_log_unload`'s docstrings quote the
    reason strings `_evict_exclusive_endpoints` and `_evict_for_budget`
    pass, so counting prose would let the ACTUAL argument be reworded
    while the pin went on passing against the sentences describing it.
    Proven by red-proofing exactly that edit."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]


def _logger_format_strings(path: Path) -> list[tuple[int, str]]:
    """`(lineno, format string)` for every `logger.*()` call in `path`.

    Resolves a first argument that is a NAME to an assignment of a string
    literal, because `models/registry/bindings.py` builds its
    refused-lowering message into a local and then logs it at one of two
    levels -- a shape the naive "first argument is a literal" reading
    would skip silently, which is the one failure mode a completeness
    gate must not have. A call this cannot read raises rather than being
    skipped, for the same reason."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value.value

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
            and node.args
        ):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((node.lineno, first.value))
        elif isinstance(first, ast.Name) and first.id in assigned:
            found.append((node.lineno, assigned[first.id]))
        else:
            raise AssertionError(
                f"{path.name}:{node.lineno} logs a format string this gate cannot "
                "read (not a literal, and not a local assigned one literal). Give "
                "it a literal, or teach `_logger_format_strings` the new shape -- "
                "do not leave a log line this test cannot see."
            )
    return found


def _documents_fragment(docs: str, fragment: str) -> str | None:
    """`None` when `fragment` is documented, else the first literal piece
    of it that is missing.

    `%s`/`%r`/`%d`/`%.0f` are wildcards HERE and only here: the document
    writes `<model>`, `<endpoint>`, `N` where the code writes a
    conversion, so the literal pieces either side are what must match,
    in order. Table cells escape `|`, so that is undone first."""
    plain = docs.replace("\\|", "|")
    index = 0
    for piece in re.split(r"%[srd]|%\.0f", fragment):
        if not piece.strip():
            continue
        found_at = plain.find(piece, index)
        if found_at < 0:
            return piece
        index = found_at + len(piece)
    return None


class TestQueueGovernanceLogVocabularyMatchesDocs:
    """F6 (whole-branch review, queue memory governance): `docs/
    OPERATIONS.md`'s queue-governance section claims its tables are
    "every log line this track added or changed, not a selection", and
    until this class nothing held that claim up -- eighteen quoted
    strings, and no failure when the next `logger.info` in
    `models/queue/worker.py` was added or a wording tweaked.

    Same pure file-text style as the classes above (no ORM, no database),
    and the same doctrine: the test is the thing that enforces the claim,
    rather than trusting the comment next to each line.

    TWO DIRECTIONS, because a one-directional pin catches half the drift:
    every fragment the document quotes must still be in the code, AND
    every logger call in the three modules must be either covered by a
    fragment or explicitly rostered as pre-dating the track.
    """

    def test_the_completeness_claim_is_still_the_claim_being_held_up(self):
        """If the section stops claiming completeness, this class is
        holding up a promise nobody made any more -- worth noticing
        rather than silently continuing to enforce."""
        assert _COMPLETENESS_CLAIM in _docs_text(), (
            "docs/OPERATIONS.md's queue-governance section no longer claims its "
            "tables are every log line the track added or changed. If that was "
            "deliberate, this whole class should be reconsidered; if not, "
            "restore the claim."
        )

    def test_every_quoted_fragment_is_verbatim_in_the_source(self):
        constants = [
            s for path in _QUEUE_GOVERNANCE_SOURCES for s in _string_constants(path)
        ]
        for fragment in _QUOTED_LOG_FRAGMENTS:
            assert any(fragment in constant for constant in constants), (
                f"docs/OPERATIONS.md quotes {fragment!r}, but no string literal in "
                "models/queue/worker.py, models/queue/claim.py or "
                "models/registry/bindings.py contains it any more. Either the code "
                "was reworded (update the document AND this pin) or the pin is "
                "stale."
            )

    def test_every_quoted_fragment_is_verbatim_in_the_document(self):
        docs = _docs_text()
        for fragment in _QUOTED_LOG_FRAGMENTS:
            missing = _documents_fragment(docs, fragment)
            assert missing is None, (
                f"the queue logs {fragment!r}, but docs/OPERATIONS.md no longer "
                f"documents it -- the piece it is missing is {missing!r}. The "
                "section promises to carry every line this track added or changed."
            )

    def test_every_logger_call_in_the_three_modules_is_accounted_for(self):
        """THE COMPLETENESS HALF. A new `logger.info` in the worker with
        no row in `docs/OPERATIONS.md` fails here, which is the whole
        point: the eighteen quoted strings were a snapshot, and a
        snapshot is what the section's own claim says it is not."""
        undocumented: list[str] = []
        for path in _QUEUE_GOVERNANCE_SOURCES:
            for lineno, format_string in _logger_format_strings(path):
                if format_string in _LOG_LINES_PREDATING_THE_TRACK:
                    continue
                if any(f in format_string for f in _QUOTED_LOG_FRAGMENTS):
                    continue
                undocumented.append(f"{path.name}:{lineno} {format_string!r}")
        assert not undocumented, (
            "these log lines have no row in docs/OPERATIONS.md's queue-governance "
            "tables and are not rostered as pre-dating the track:\n  "
            + "\n  ".join(undocumented)
            + "\n\nDocument each one (and add its stable fragment to "
            "_QUOTED_LOG_FRAGMENTS), or -- if it genuinely is not part of that "
            "vocabulary -- say so by adding it to _LOG_LINES_PREDATING_THE_TRACK."
        )


ADR_DIR = Path(settings.BASE_DIR) / "docs" / "adr"

# `0019-chat-cluster.md`: four digits, a hyphen, a lower-case slug.
_ADR_FILENAME = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# An inline Markdown link's target. Reference-style links (`[x]: url`) are
# not used anywhere in `docs/adr/` and are deliberately not matched -- a
# pattern that matched them and resolved them wrongly would be worse than
# one that says what it covers.
#
# BOTH HALVES TOLERATE ONE LEVEL OF NESTING (wave r11). The original
# `\[[^\]]*\]\(([^)\s]+)\)` under-matched in two directions, and an
# under-matching pattern in a gate is a gate that passes by looking at
# less: link TEXT carrying brackets (`[ADR 0010 [amended]](...)`) matched
# from the inner `[` and produced a target that is not one, and a TARGET
# carrying parentheses (`foo_(v2).md`) was truncated at the first `)`,
# so a real broken link could read as a resolvable one. Neither shape is
# hypothetical in a record that cites bracketed titles.
_MARKDOWN_LINK = re.compile(
    r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(((?:[^()\s]|\([^()\s]*\))+)\)")

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


def _atx_headings(text: str) -> list[str]:
    """Every REAL Markdown heading in `text`, in document order.

    TWO NARROWINGS, BOTH WAVE r11, and both in the direction that makes
    the gate stricter rather than looser.

    ATX SYNTAX, NOT "STARTS WITH A HASH". A heading is one to six `#`s
    followed by a SPACE and some content; `#!/bin/sh`, `#4`, and a bare
    `#` are not headings, and counting them invents anchors no renderer
    produces -- which is how a link to a heading that does not exist
    passes.

    OUTSIDE FENCED CODE. A shell comment or a Python comment inside a
    ``` block begins with `#` and is not a heading either. The ADRs are
    full of fenced blocks; this is not a corner case.
    """
    headings: list[str] = []
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if re.match(r"^#{1,6} \S", line):
            headings.append(line)
    return headings


def _heading_slugs(path: Path) -> set[str]:
    """Every anchor `path` offers, INCLUDING the numbered forms a
    repeated heading gets.

    WAVE r9. This used to be a set comprehension over `_anchor_slug`,
    which silently collapsed repeats -- and a document with two
    `### Consequence` headings (ADR 0010 has exactly that) offers
    `#consequence` AND `#consequence-1`, of which the second read as
    "no such heading". A link that lands was being reported as broken,
    which is the failure mode that gets a gate deleted.

    THE RULE IS github-slugger's, which is what GitHub and every
    renderer this record is read in use: the first occurrence keeps the
    bare slug, the Nth gets `-<N-1>` appended.
    """
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    for heading in _atx_headings(path.read_text(encoding="utf-8")):
        base = _anchor_slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


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
            # A BARE `#fragment` IS A LINK INTO THIS SAME DOCUMENT (wave
            # r10). It used to resolve to `adr.parent` -- the DIRECTORY
            # -- which exists, is not a file, and so skipped the anchor
            # check entirely: every same-document link in the record
            # failed OPEN, which is the one direction a gate must never
            # fail. Red-proven by
            # `test_a_same_document_fragment_is_really_checked`.
            resolved = adr if path_part == "" else adr.parent / path_part
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

    def test_a_repeated_heading_gets_the_sluggers_numbered_forms(self, tmp_path):
        """WAVE r9. `_heading_slugs` was a set comprehension, so a
        document with two identical headings offered ONE anchor and the
        second link into it read as broken -- a gate reporting a link
        that lands as one that does not. github-slugger's rule: the
        first keeps the bare slug, the Nth gets `-<N-1>`."""
        doc = tmp_path / "repeats.md"
        doc.write_text("# Title\n\n## Consequence\n\n## Consequence\n"
                       "\n## Consequence\n", encoding="utf-8")
        assert _heading_slugs(doc) == {"title", "consequence", "consequence-1",
                                       "consequence-2"}

    def test_the_record_really_contains_a_repeated_heading(self):
        """ANTI-VACUOUS COMPANION: the rule above would be untested
        against the real record if no ADR repeated a heading. ADR 0010
        does, which is how r9 was found."""
        slugs = _heading_slugs(ADR_DIR / "0010-model-management-framework.md")
        assert "consequence" in slugs
        assert "consequence-1" in slugs

    def test_only_real_markdown_headings_count(self, tmp_path):
        """WAVE r11. "Starts with a hash" counted a shell comment inside
        a fenced block, a `#4` cross-reference and a bare `#` as
        headings, inventing anchors no renderer produces -- so a link to
        a heading that does not exist could land on one of them."""
        doc = tmp_path / "fenced.md"
        doc.write_text(
            "# Real heading\n"
            "\n"
            "```sh\n"
            "# Not a heading, a shell comment\n"
            "```\n"
            "\n"
            "#4 is an issue reference, not a heading\n"
            "#\n"
            "####### Seven hashes is not a heading either\n"
            "\n"
            "## Second real heading\n",
            encoding="utf-8")
        assert _heading_slugs(doc) == {"real-heading", "second-real-heading"}

    def test_the_link_pattern_reads_brackets_and_parentheses(self):
        """WAVE r11. An under-matching pattern is a gate that passes by
        looking at less: bracketed link TEXT matched from the inner `[`
        and produced a target that is not one, and a parenthesised
        TARGET was truncated at the first `)`."""
        assert _MARKDOWN_LINK.findall("see [ADR 0010](0010-x.md#amendment)") == [
            "0010-x.md#amendment"]
        assert _MARKDOWN_LINK.findall("[ADR 0010 [amended]](0010-x.md)") == [
            "0010-x.md"]
        assert _MARKDOWN_LINK.findall("[the note](notes_(v2).md)") == [
            "notes_(v2).md"]
        # Still deliberately silent on reference-style links.
        assert _MARKDOWN_LINK.findall("[x]: 0010-x.md\n") == []

    def test_a_same_document_fragment_is_really_checked(self, tmp_path):
        """WAVE r10, RED-PROVED. A bare `#fragment` used to resolve to
        the containing DIRECTORY, which exists and is not a file, so the
        anchor check was skipped and every same-document link in the
        record failed OPEN. Driven here over a synthetic pair -- one link
        that lands, one that does not -- through the same resolution the
        gate runs.

        SYNTHETIC ON PURPOSE, AND SAID SO: no ADR writes a bare fragment
        TODAY, so the real walk cannot exercise this branch and an
        "is it used in the record" companion would fail for the right
        reason and get the fix reverted for the wrong one. The bug was
        latent, the fix closes it before the first same-document link is
        written, and this is what proves the branch works when one is.
        """
        doc = tmp_path / "0001-x.md"
        doc.write_text("# Title\n\n## The decision\n\n"
                       "[good](#the-decision) and [bad](#no-such-heading)\n",
                       encoding="utf-8")
        results = {}
        for target in _MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8")):
            path_part, _, fragment = target.partition("#")
            resolved = doc if path_part == "" else doc.parent / path_part
            results[target] = (resolved.is_file()
                               and fragment in _heading_slugs(resolved))
        assert results == {"#the-decision": True, "#no-such-heading": False}
