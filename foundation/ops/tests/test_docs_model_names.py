"""Invariants about MODEL NAMES in the repository's own documentation.

A sibling of `test_agent_standards.py`'s `TestDocumentsCarryNoLocalPaths` and
`test_repo_hygiene.py`: those walk the tree to enforce what a document may
say about a MACHINE or an IMAGE; this one walks the repository's own
documentation to enforce what it may say about a MODEL. Global Constraint 9
(2026-09-10 hardening plan): the repository is going public, so its own
prose describes capabilities generically -- "the default image model", "a
distilled few-step family" -- and never names a model family, checkpoint,
or reference implementation. CODE identifiers (a template module name, an
operation key, a config key, a workflow JSON filename the code actually
loads, a family slug the database stores) are code, not docs, and stay --
the distinction is not "is this string a model name" but "is this string
naming a thing in our code, or describing a model to a reader".

This gate lives in its own module rather than extending
`test_repo_hygiene.py` (task H2's home for tree-vs-artefact invariants).
Task H17's plan document and brief both said "extend `test_repo_hygiene.py`"
-- that reference is STALE the moment this module exists; the orchestrator's
H17 addendum put the gate here instead, since `tools/vision/README.md` and
the ADRs this module also walks are files other in-flight sessions steward.
H17 shipped this module with `docs/DEV.md` and ADRs 0010/0012 still dirty
and an `xfail` covering them; H18 scrubbed those three plus ADR 0013 and
removed that marker.

WHAT THE WALK COVERS (2026-09-20, release preparation). The walked set was
a hand-maintained list of six files; it is now DERIVED -- every `docs/**`
document, the root `README.md`, and every column `README.md` in the tree.
`docs/superpowers/**` used to be carved out as "a historical planning
record under separate owner review (constraint 25)"; that carve-out is
GONE. The owner delegated the pre-open-source scrub on 2026-09-20 and
ruled the planning archive IN SCOPE, so it is walked like any other
document and constraint 25's deferred-scrub note is discharged.

WHAT THE WALK EXEMPTS, and why (the same 2026-09-20 rulings; this docstring
is where those exemptions are recorded, so a future contributor reading the
gate reads the reasoning with it):

* **Functional identifiers are code, not prose.** Test fixtures carrying
  engine-format strings, `model_id` values, workflow module names
  (`flux2_txt2img.py`), and every entry in `models/contracts/catalog.py`
  INCLUDING its display names ("Qwen 2.5 7B") stay as they are. The
  catalog's whole function is naming real models an operator can install;
  genericizing it would break its purpose. This gate walks documents, not
  code, so those surfaces are outside it by construction -- and a
  backticked identifier inside a document is exempt by
  `_is_code_identifier_line` for the same reason.
* **Protocol descriptors are allowed, like engine names.**
  "OpenAI-compatible" and "OpenAI-style" name an industry WIRE PROTOCOL an
  adapter targets, not a model and not an endorsement -- the same
  reasoning that already lets a deployment instruction name `whisper.cpp`,
  which is a SERVER an operator installs. `_ALLOWED_PHRASES` below holds
  them, checked before a match is flagged.
* **The agent harness is software, not a model.** This repository ships a
  `CLAUDE.md` and keeps worktrees under `.claude/`; documentation that
  says so is naming a tool a contributor installs, exactly like
  `whisper.cpp`. Those phrases are allowlisted. Bare model-shaped prose
  ("Claude 3.5 renders...") is NOT, and the vacuity test below pins that.

WHAT GREEN HERE DOES AND DOES NOT MEAN (2026-09-20, round-1 review). Two
honesty notes a future reader is owed, because both were mistakes this
gate actually made:

* **The pattern list is a SAMPLE of the rule, not the rule.** It first
  shipped with versioned spellings only -- `flux.2`, `qwen-image` --
  so "we use Flux for images" passed a gate whose entire subject is that
  sentence, and a scrub built on it reported clean over 13 lines of prose
  that named models. The bare names are in the list now; the lesson is
  that green means "no name on THIS list", and the list is finite.
* **The one deferral this gate ever carried is gone.** Finding I1's
  patterns reached six lines in `docs/adr/0012` and `tools/vision/README.md`
  that the release round had been fenced out of editing, so they were
  listed by exact line number with a test that FAILED once they stopped
  offending. The vision steward cleared them on 2026-09-20, that test
  fired, and the list was deleted -- which is the whole point of writing
  an exemption so that it cannot outlive its reason. There is no exemption
  in this gate today.
"""
from __future__ import annotations

import re
import subprocess

from foundation.ops.tests._helpers import REPO_ROOT

# Model families, checkpoints and reference implementations that must not
# appear in DOCUMENTATION PROSE (owner ruling; the repo is going public).
# Case-insensitive. CODE identifiers are exempt: `_is_code_identifier_line`
# skips a match that sits inside backticks or inside an allowlisted phrase,
# and `test_the_modelname_gate_is_not_vacuous` pins BOTH directions so the
# exemption cannot quietly grow to cover everything.
_MODELNAME_PATTERNS = (
    r"flux\.?2", r"klein", r"qwen[- ]?image", r"qwen2\.5", r"llava",
    r"llama[- ]?3", r"mistral", r"stable diffusion", r"\bsdxl\b", r"\bsd1\.5\b",
    r"nomic-embed", r"gemma",
    # Added 2026-09-20 (release preparation): the commercial-model vendors
    # and products the pre-open-source sweep found in prose.
    r"openai", r"anthropic", r"claude", r"chatgpt", r"gpt-\d", r"deepseek",
    # Added 2026-09-20 (round-1 review, finding I1): the BARE family names.
    # The list above only caught VERSIONED spellings -- `flux\.?2` misses
    # "Flux", `qwen[- ]?image` misses "Qwen" -- so "we use Flux for images"
    # sailed through a gate whose entire subject is that sentence. An
    # enumerated list is a SAMPLE of the rule, never the rule; green meant
    # very little until these were in it, and it still means "no name on
    # this list", not "no model named".
    r"\bflux\b", r"\bqwen\b", r"\bsd3\b", r"lightning", r"kontext",
)
# NOT `whisper\.cpp`: that names a SERVER an operator installs, not a
# model. Deployment instructions may name software; the rule is about
# model families and checkpoints, not the software that runs them.

# Phrases in which a pattern match is NOT a model name. Two kinds, both
# argued in this module's docstring: a WIRE-PROTOCOL descriptor an adapter
# targets, and the agent harness this repository ships a config file for.
# Case-insensitive, matched as spans exactly like backticks are -- a match
# that sits inside one of these spans is not flagged.
_ALLOWED_PHRASES = (
    r"openai-compatible", r"openai-style",
    r"claude code", r"claude\.md", r"claude-specific", r"\.claude/",
)
# KNOWN NARROW HOLE (round-1 review, nit N1): a line whose only match is the
# literal harness phrase used as a prefix -- "the Claude Code model family
# renders quickly" -- passes, because the `claude code` span covers the only
# matching token on the line. Closing it would mean parsing intent, which a
# regex gate cannot do honestly. It is reachable only by writing the exact
# harness phrase, so it is RECORDED here rather than papered over. Every
# other direction is pinned by the vacuity tests below.



def _exempt_spans(line: str) -> list[tuple[int, int]]:
    """Every span on this line in which a model-name-shaped match does not
    count: a backtick-delimited span (a config key, a filename, a class
    name) or an allowlisted phrase (a wire protocol, the agent harness)."""
    spans = [match.span() for match in re.finditer(r"`[^`]*`", line)]
    for phrase in _ALLOWED_PHRASES:
        spans.extend(match.span() for match in re.finditer(phrase, line, re.IGNORECASE))
    return spans


def _is_code_identifier_line(line: str) -> bool:
    """True when every model-name-shaped match on this line sits inside an
    exempt span -- False the moment even one sits in plain prose.
    Fenced-code-block state is the WALK's job (`_offending_lines`, below);
    this helper looks at one line in isolation, which is exactly what the
    vacuity test below pins."""
    spans = _exempt_spans(line)
    for pattern in _MODELNAME_PATTERNS:
        for match in re.finditer(pattern, line, re.IGNORECASE):
            if not any(start <= match.start() < end for start, end in spans):
                return False
    return True


def _tracked_files() -> tuple[str, ...]:
    """Every file git tracks, as repo-relative POSIX paths. The walk below
    derives from THIS rather than from `Path.glob`, so a vendored
    `node_modules/<pkg>/README.md` on a contributor's machine cannot join
    the gate and red it for a reason that has nothing to do with this
    repository (round-1 review, nit N4). Untracked scratch is likewise
    invisible, which is the correct answer for a gate about what the
    repository SAYS."""
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    )
    return tuple(name for name in completed.stdout.split("\0") if name)


def _walked_documents() -> tuple[str, ...]:
    """Every document this gate walks: all of `docs/**`, the root
    `README.md`, every column `README.md`, and every project skill's
    `SKILL.md` under `.claude/skills/`. DERIVED from the git index, not a
    hand-kept list -- a new ADR, a new column README, or a new skill is
    covered the day it lands, which a hand-kept list could not promise."""
    return tuple(sorted(
        name for name in _tracked_files()
        if name == "README.md"
        or (name.startswith("docs/") and name.endswith(".md"))
        or name.endswith("/README.md")
        or (name.startswith(".claude/skills/") and name.endswith("SKILL.md"))
    ))


def _offending_lines(relative: str) -> list[str]:
    """Every line in `relative` naming a model family in prose. A line
    inside a fenced code block never counts (a Python snippet quoting
    `"qwen2.5:7b"` as a literal string is not "naming a model to a
    reader"), and neither does a line whose only match sits in backticks
    or in an allowlisted phrase."""
    offenders: list[str] = []
    in_fence = False
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or _is_code_identifier_line(line):
            continue
        for pattern in _MODELNAME_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                offenders.append(f"{relative}:{number}: {line.strip()[:90]}")
    return offenders


def test_no_model_family_is_named_in_documentation_prose():
    """Real gate, no exemption: every walked document is clean, including
    `docs/superpowers/**` -- carved out until the owner ruled the planning
    archive in scope on 2026-09-20."""
    offenders = []
    for relative in _walked_documents():
        offenders.extend(_offending_lines(relative))
    assert offenders == [], "\n".join(offenders)


def test_tools_vision_readme_names_no_model_family():
    """H17's own regression test, kept: RED against the 38-line offender
    list before that task's rewrite, GREEN after. Now redundant with the
    combined walk above (no longer `xfail`), but still gives a direct
    RED/GREEN signal scoped to exactly that task's change.

    The name is now literal again: the vision steward cleared `:1573` and
    `:1576` on 2026-09-20, so this file carries no deferral and green here
    means what it says."""
    assert _offending_lines("tools/vision/README.md") == []


def test_adr_0010_0012_0013_and_dev_md_name_no_model_family():
    """H18's own regression test: RED against the offenders these four
    documents carried before this task's rewrite, GREEN after -- the
    documents the H17 `xfail` used to cover, checked directly rather than
    only through the combined walk above."""
    for relative in (
        "docs/DEV.md",
        "docs/adr/0010-model-management-framework.md",
        "docs/adr/0012-image-generation-engine-adapter.md",
        "docs/adr/0013-inference-execution-queue.md",
    ):
        assert _offending_lines(relative) == [], relative


def test_the_planning_archive_and_column_readmes_are_walked():
    """The 2026-09-20 extension's own regression test: RED against the 69
    prose offenders the release sweep found across `docs/superpowers/**`,
    `docs/adr/0005`, and `agents/chat/README.md`, GREEN after the scrub --
    checked directly, not only through the derived walk above."""
    for relative in (
        "docs/adr/0005-rag-module-architecture.md",
        "agents/chat/README.md",
        "docs/superpowers/plans/2026-08-25-vision-distilled-fewstep.md",
        "docs/superpowers/plans/2026-08-25-vision-edit-capability.md",
        "docs/superpowers/specs/2026-08-27-vision-constant-form-design.md",
    ):
        assert _offending_lines(relative) == [], relative


def test_the_modelname_gate_is_not_vacuous():
    """Anti-vacuous pin, both directions: a gate that silently exempts
    everything backtick-adjacent is worse than no gate at all."""
    assert _is_code_identifier_line("the `flux2` family key") is True
    assert _is_code_identifier_line("the Flux.2 family renders quickly") is False
    # Finding I1: the BARE family names, the hole a versioned-only list left.
    assert _is_code_identifier_line("we use Flux for images") is False
    assert _is_code_identifier_line("Qwen is our default") is False
    assert _is_code_identifier_line("the Lightning LoRA speeds it up") is False
    assert _is_code_identifier_line("Kontext edits images") is False
    assert _is_code_identifier_line("an SD3 checkpoint") is False
    # ...without swallowing a real identifier.
    assert _is_code_identifier_line("the `qwen_image` family key") is True
    # KNOWN COST of `\bflux\b`, pinned so it is a decision and not a
    # surprise: the ordinary English noun trips it. Backtick it or reword.
    # Accepted -- "Flux" as a family name is far likelier in this repo's
    # prose than "flux" as a synonym for flow.
    assert _is_code_identifier_line("a flux of requests arrives") is False
    # The protocol descriptor passes; a bare vendor name-drop does not.
    assert _is_code_identifier_line("a stable OpenAI-compatible API") is True
    assert _is_code_identifier_line("we call the OpenAI chat model") is False
    # The harness passes; a model-shaped mention of the same vendor does not.
    assert _is_code_identifier_line("worktrees live under .claude/worktrees") is True
    assert _is_code_identifier_line("Claude 3.5 writes the summary") is False


def test_the_walked_document_set_is_real_and_broad():
    """Anti-vacuous pin: the walk above passes trivially if
    `_walked_documents` returned nothing, named files that do not exist,
    or quietly stopped covering the surfaces the owner put in scope."""
    walked = _walked_documents()
    assert len(walked) >= 40
    missing = [relative for relative in walked if not (REPO_ROOT / relative).exists()]
    assert missing == []
    for required in (
        "README.md",
        "tools/vision/README.md",
        "agents/chat/README.md",
        "models/contracts/README.md",
        "docs/ARCHITECTURE.md",
        "docs/adr/0005-rag-module-architecture.md",
    ):
        assert required in walked, required
    assert any(relative.startswith("docs/superpowers/") for relative in walked)
