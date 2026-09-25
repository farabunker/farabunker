"""Invariants about the repository's own standards documents.

A sibling of `test_import_law.py`, `test_column_boundaries.py`,
`test_css_ownership.py` and `test_docs_sync.py`: those walk the tree to
enforce a rule about the code; this one walks it to enforce a rule about
what the tree TELLS a new contributor. All of them live in
`foundation/ops/tests/` because `foundation/ops/` is the column with no
dependents, so a repo-wide walk cannot become a cyclic import.

These are gates, not behaviour tests -- nothing here imports application
code or touches the database.
"""
from __future__ import annotations

import json
import re
import subprocess

from foundation.ops.tests._helpers import REPO_ROOT

# An absolute path out of somebody's home directory -- the thing no
# committed document may name, because it names a person and a machine and
# is noise to every reader on a different one.
#
# The lookbehind requires the separator to BEGIN a path, so the `home/`
# column and `/home/` nested under another directory do not match. The
# trailing segment requires a user name followed by more path, so a
# document may still name the shape it forbids: `/Users/<owner>/src`,
# `/Users/` and `/Users/...` all pass.
_ABSOLUTE_HOME_PATH = re.compile(r"(?<![\w/.:-])/(?:Users|home)/[A-Za-z0-9_.-]+/")

# The SAME leak, in the form an agent harness writes it. A scratchpad
# directory encodes the project path by replacing every separator with a
# hyphen, so the account name survives as `-Users-<name>-...` inside what
# looks like a single path segment. `_ABSOLUTE_HOME_PATH` cannot see it --
# there is no `/Users/` left to match -- and neither can `git grep
# "/Users/"`, which is how six of these sat in the planning archive through
# a scrub that was specifically looking for them (round-1 review, finding
# I4). Same rule, second spelling.
_MANGLED_HOME_PATH = re.compile(r"-(?:Users|home)-[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+")

# An agent harness stamps a `Claude-Session:` trailer naming the session it
# worked in. Those ids are ACCOUNT-TIED -- they identify a person's
# workspace, not a machine -- so non-negotiable 4's "no personal data" half
# covers them exactly as its "no absolute paths" half covers the two
# patterns above. The planning archive quoted the trailer inside fenced
# commit templates 197 times across 17 documents before the 2026-09-20
# release round scrubbed them (review nit N5, owner-delegated ruling).
#
# The template itself is fine and stays: it is the ID that is personal. So
# this matches a REAL id -- 20+ alphanumerics -- and deliberately passes
# the `session_<id>` placeholder those templates now carry.
_ACCOUNT_SESSION_ID = re.compile(r"session_[A-Za-z0-9]{20,}")

# The branch gate, declared once here and quoted in two documents.
# `docs/DEV.md` §8 rung 1 owns it; `AGENTS.md` repeats it because it is the
# one thing a newcomer needs before their first commit. This constant is
# what stops the two copies drifting -- the same trick `test_docs_sync.py`
# plays on the backup and restore sequences.
_FOUR_RUN_MATRIX = "\n".join((
    "FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q",
    "FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q",
    "FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools",
    "FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools",
))

# The sections of AGENTS.md that other documents promise by name
# ("see AGENTS.md for the working loop"). A heading may gain text after
# it, but one that quietly disappears turns those promises into dead
# references.
_REQUIRED_AGENTS_HEADINGS = (
    "## Quick reference",
    "## The non-negotiables",
    "## The working loop",
    "## Subagent-driven development",
    "## Merge readiness",
    "## Code style and commits",
)


def _read(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.exists(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


def _normalised(text: str) -> str:
    """Collapse every run of whitespace, so a pinned sentence may be
    re-wrapped in the document without breaking its pin."""
    return " ".join(text.split())


class TestAgentsFile:
    def test_agents_md_exists_and_has_every_required_section(self):
        text = _read("AGENTS.md")
        missing = [h for h in _REQUIRED_AGENTS_HEADINGS if h not in text]
        assert missing == [], missing

    def test_agents_md_stays_short_enough_to_be_read(self):
        """It is the single source of truth only while people read it to
        the end. When this trips, CUT -- summarise and link out to the
        document that owns the detail. Do not raise it."""
        assert len(_read("AGENTS.md").splitlines()) <= 190

    def test_the_branch_gate_is_identical_in_both_places(self):
        """A gate command that drifts between the two documents is worse
        than one stated in a single place, because both look
        authoritative."""
        assert _FOUR_RUN_MATRIX in _read("docs/DEV.md")
        assert _FOUR_RUN_MATRIX in _read("AGENTS.md")


class TestTheLocalPathPattern:
    def test_it_distinguishes_a_real_path_from_a_directory_name(self):
        """`tools/home/` is a column here and appears on hundreds of
        lines; matching it would make every gate built on this pattern
        unusable. The two positive fixtures are assembled from pieces so
        that this module does not itself contain what it forbids."""
        user_path = "/Users/" + "someone/src/project"
        home_path = "/home/" + "someone/.venv/bin/pytest"

        assert not _ABSOLUTE_HOME_PATH.search("see tools/home/README.md")
        assert not _ABSOLUTE_HOME_PATH.search("`identity/home/README.md` moves")
        assert not _ABSOLUTE_HOME_PATH.search("run it from /Users/<owner>/src")
        assert not _ABSOLUTE_HOME_PATH.search("no /Users/... paths, please")

        assert _ABSOLUTE_HOME_PATH.search(f"cd {user_path}")
        assert _ABSOLUTE_HOME_PATH.search(f"`{home_path}`")

    def test_it_distinguishes_a_real_session_id_from_the_placeholder(self):
        """Nit N5: the commit-message template is not the problem, the id
        in it is. Fixtures are assembled from pieces, like the ones above,
        so this module does not itself contain what it forbids."""
        real = "session_" + "01" + "A" * 22

        assert _ACCOUNT_SESSION_ID.search(f"Claude-Session: https://x/code/{real}")
        assert not _ACCOUNT_SESSION_ID.search(
            "Claude-Session: https://claude.ai/code/session_<id>"
        )
        assert not _ACCOUNT_SESSION_ID.search("a session_id column")
        assert not _ACCOUNT_SESSION_ID.search("the session_key helper")

    def test_it_also_catches_the_harness_mangled_spelling(self):
        """Finding I4: the hyphen-encoded form carries the same account
        name and defeats both the pattern above and a `/Users/` grep.
        Fixtures are assembled from pieces, like the ones above, so this
        module does not contain what it forbids."""
        mangled = "-Users-" + "someone" + "-Documents-Code-Project"

        assert not _ABSOLUTE_HOME_PATH.search(mangled)
        assert _MANGLED_HOME_PATH.search(f"/private/tmp/agent-1/{mangled}/x")

        assert not _MANGLED_HOME_PATH.search("a well-homed-value")
        assert not _MANGLED_HOME_PATH.search("see tools/home/README.md")
        assert not _MANGLED_HOME_PATH.search("the <scratchpad>/ placeholder")


# The one line CLAUDE.md exists to carry. Asserted literally, modulo line
# wrapping: the point of that file is that it does not become a second,
# drifting copy of AGENTS.md, and a paraphrase is how that starts.
_POINTER_LINE = (
    "**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for how "
    "work is done in this repository, and it applies to every agent and every "
    "contributor regardless of vendor."
)

# `.claude/*` and NOT `.claude/`: git will not re-include a file whose
# parent directory is excluded, so the bare-directory form would leave
# the committed settings.json permanently invisible.
_REQUIRED_GITIGNORE_LINES = (
    ".claude/*",
    "!.claude/settings.json",
    ".superpowers/",
    ".DS_Store",
)


def _gitignore_lines() -> list[str]:
    return [
        line.strip()
        for line in _read(".gitignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _tracked_files(*paths: str) -> list[str]:
    """What git has in its INDEX -- not what is on disk. Use
    `splitlines()`, as the sibling gate modules do: `split()` would break
    a path containing a space."""
    out = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


class TestClaudeFile:
    def test_claude_md_exists_and_points_at_agents_md(self):
        assert _normalised(_POINTER_LINE) in _normalised(_read("CLAUDE.md"))

    def test_claude_md_is_a_pointer_not_a_second_rulebook(self):
        """Two files carrying the same rules means one of them is wrong
        and nobody knows which."""
        assert len(_read("CLAUDE.md").splitlines()) <= 40


class TestIgnoreRules:
    def test_gitignore_carries_the_rules_the_repository_depends_on(self):
        lines = _gitignore_lines()
        missing = [rule for rule in _REQUIRED_GITIGNORE_LINES if rule not in lines]
        assert missing == [], missing

    def test_the_settings_negation_follows_its_directory_rule(self):
        """Order matters in .gitignore: a negation is only honoured after
        the pattern it negates."""
        lines = _gitignore_lines()
        assert lines.index(".claude/*") < lines.index("!.claude/settings.json")

    def test_the_committed_settings_file_is_actually_visible_to_git(self):
        """Anti-vacuous check: gitignore rules never affect an
        already-tracked file, so this cannot fail because of the ignore
        pattern -- it fails if the committed file leaves the index."""
        assert _tracked_files(".claude/settings.json") == [".claude/settings.json"]

    def test_the_skills_directory_is_actually_visible_to_git(self):
        """Same anti-vacuous shape as the settings check above, for the
        `!.claude/skills/` negation: it fails if the directory's own
        exclusion (`.claude/*`) wins, leaving every skill untracked."""
        assert _tracked_files(".claude/skills") != []

    def test_no_session_ledger_is_tracked(self):
        """`.superpowers/` is per-session working state written for one
        reader. It stays on disk and out of the history."""
        assert _tracked_files(".superpowers") == []

    def test_the_tracked_file_walk_is_reading_a_real_repository(self):
        """Anti-vacuous pin: the two assertions above both pass trivially
        if `git ls-files` returns nothing at all."""
        assert len(_tracked_files()) > 500


class TestCommittedPermissions:
    def test_the_allowlist_grants_nothing_that_mutates(self):
        """Prefix-matched; it cannot catch every spelling: an allowlist
        entry can still be unsafe in a way no fixed word-list anticipates,
        so reviewing every new entry by eye is still required."""
        settings = json.loads(_read(".claude/settings.json"))
        allow = settings["permissions"]["allow"]
        # `stash list` is a read verb, so the forbidden token names the
        # mutating subcommands rather than the word `stash`.
        forbidden = (
            "push", "commit", "merge", "rebase", "reset", "checkout", "clean",
            "rm", "restore", "stash push", "stash pop", "stash apply",
            "stash drop", "branch -d", "branch -D", "worktree add",
            "worktree remove", "docker compose up", "docker compose down",
            "docker compose restart", "docker compose exec", "docker compose build",
            "psql", "createdb", "dropdb", "curl", "pip install", "chmod", "sudo",
        )
        offenders = [
            entry for entry in allow
            if any(word in entry for word in forbidden)
        ]
        assert offenders == [], offenders

    def test_the_narrowed_entries_stay_out_of_the_allowlist(self):
        """Each of these grants more than it appears to: a write flag
        (`git diff --output=`, `grep --pre`) or the bare, ambient-database
        pytest form the branch gate forbids."""
        settings = json.loads(_read(".claude/settings.json"))
        allow = settings["permissions"]["allow"]
        assert "Bash(git diff:*)" not in allow
        assert "Bash(.venv/bin/pytest:*)" not in allow
        assert "Bash(git grep:*)" not in allow
        assert "Bash(rg:*)" not in allow

    def test_a_minimal_deny_block_blocks_the_truly_destructive_commands(self):
        """Deny is deliberately narrow, and is a prefix-matched speed bump
        rather than a boundary: it does not catch `--force-with-lease`,
        `+ref` push specs, or `-C` redirected elsewhere."""
        settings = json.loads(_read(".claude/settings.json"))
        deny = settings["permissions"]["deny"]
        for entry in (
            "Bash(git push --force:*)",
            "Bash(git push -f:*)",
            "Bash(git reset --hard:*)",
        ):
            assert entry in deny, entry

    def test_the_allowlist_names_no_machine(self):
        """Machine-neutral: no absolute path, no port, no credential."""
        settings = json.loads(_read(".claude/settings.json"))
        blob = json.dumps(settings)
        for needle in ("/Users/", "/home/", "localhost:", "postgres://", "password"):
            assert needle not in blob, needle

    def test_the_allowlist_is_not_empty(self):
        settings = json.loads(_read(".claude/settings.json"))
        assert len(settings["permissions"]["allow"]) >= 10


def _scannable_documents() -> list[str]:
    """Every tracked Markdown file, not just `docs/`: a module README
    beside the code is exactly where a local path gets pasted while
    someone is debugging."""
    return [p for p in _tracked_files() if p.endswith(".md")]


class TestDocumentsCarryNoLocalPaths:
    """Non-negotiable 4, both halves: no absolute local path, and no
    personal data. The session-id walk below is the second half -- an
    account-tied identifier is personal data even though it is not a
    path."""

    def test_no_tracked_document_names_a_local_home_directory(self):
        offenders = []
        for relative in _scannable_documents():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if _ABSOLUTE_HOME_PATH.search(line) or _MANGLED_HOME_PATH.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()[:100]}")
        assert offenders == [], offenders

    def test_no_tracked_document_carries_an_account_session_id(self):
        """Nit N5 (owner-delegated ruling, 2026-09-20): the archive quoted
        a `Claude-Session:` trailer 197 times across 17 plans. The trailer
        stays in those templates; the id in it is now `session_<id>`."""
        offenders = []
        for relative in _scannable_documents():
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if _ACCOUNT_SESSION_ID.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()[:100]}")
        assert offenders == [], offenders

    def test_the_document_walk_is_reading_real_files(self):
        """Anti-vacuous pin: the assertion above passes trivially if the
        walk finds nothing."""
        assert len(_scannable_documents()) > 40


class TestPlanningArchiveExplainsItself:
    def test_the_archive_has_a_readme_that_says_what_it_is_not(self):
        text = _read("docs/superpowers/README.md")
        assert "AGENTS.md" in text
        assert "docs/adr/" in text
        assert "not current documentation" in text


class TestTheFrontDoor:
    def test_no_governance_document_still_claims_there_is_no_code(self):
        """Phase 0 ended hundreds of commits ago, and README's own Status
        section two files away says so."""
        for relative in ("CONTRIBUTING.md", "SECURITY.md"):
            text = _read(relative)
            assert "Phase 0" not in text, relative
            assert "no runtime code yet" not in text, relative

    def test_the_readme_offers_a_way_to_run_the_thing(self):
        readme = _read("README.md")
        assert "## Quickstart" in readme
        assert "docs/DEV.md" in readme
        assert "docker compose up" in readme

    def test_every_front_door_document_points_at_the_working_standards(self):
        """A contributor should reach AGENTS.md from whichever of the two
        files they opened first."""
        for relative in ("README.md", "CONTRIBUTING.md"):
            assert "AGENTS.md" in _read(relative), relative
