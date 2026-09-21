"""Doc-vs-constant sync test (W3 review M1): every printed sequence /
warning `foundation.ops.backup`/`foundation.ops.restore` carry as a Python
constant is documented as claiming to be "reproduced identically" or
"printed verbatim" in `docs/OPERATIONS.md` -- this test is the thing that
actually enforces that claim stays true, rather than trusting the comment
next to each constant. No Django needed -- pure file-text comparison."""
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
    nicety: `_evict_exclusive_endpoints`'s own docstring quotes the
    reason string it passes, so counting prose would let the ACTUAL
    argument be reworded while the pin went on passing against the
    sentence describing it. Proven by red-proofing exactly that edit."""
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
