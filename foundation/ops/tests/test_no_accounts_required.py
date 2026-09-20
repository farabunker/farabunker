"""`ACCOUNTS_REQUIRED` appears nowhere in the tree.

A grep gate, not a style rule. The setting's `True` branch never worked
-- `agents/chat/principal.py::principal_for_request` raised on it -- and
the design deleted it rather than flipping it, because two truths about
whether permissions are enforced would be two answers in a box that runs
three processes with independently supplied environments.

Historical mentions in `docs/adr/` are EXEMPT: an ADR records what was
decided at the time, and rewriting one to hide a retired name would be
falsifying the record. The amendment at ADR 0015's foot is what says the
setting is gone.

Two files are ALSO exempt by exact path, never by prefix: this module
itself (the needle is its own docstring/literal/assertion text -- a
grep gate that failed on naming the thing it greps for would be no
gate at all) and `identity/tests/test_request.py` (whose
`assert not hasattr(settings, "ACCOUNTS_REQUIRED")` names the setting
to prove it is gone, not to use it). Both are pinned present and
non-vacuous below.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings

REPO_ROOT = Path(settings.BASE_DIR)

NEEDLE = "ACCOUNTS_REQUIRED"
# ADRs and executed plans are historical records, not live descriptions.
_EXEMPT_PREFIXES = ("docs/adr/", "docs/superpowers/plans/", "docs/superpowers/specs/")

# Exact paths, never a prefix: the only two files where NEEDLE is legitimate
# TEXT ABOUT THIS GATE (this module's own docstring/literal, and the
# hasattr pin proving the setting is gone) rather than a live reference to
# a setting that no longer exists. Widening this to a prefix would hide a
# real offender anywhere under `foundation/ops/tests/` or `identity/tests/`.
_EXEMPT_LITERAL_FILES = (
    "foundation/ops/tests/test_no_accounts_required.py",
    "identity/tests/test_request.py",
)


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_the_setting_is_gone_from_every_live_file():
    offenders = []
    for relative in _tracked_files():
        if relative.startswith(_EXEMPT_PREFIXES) or relative in _EXEMPT_LITERAL_FILES:
            continue
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, IsADirectoryError):
            continue
        if NEEDLE in text:
            offenders.append(relative)
    assert offenders == [], offenders


def test_the_gate_is_reading_real_files():
    """Anti-vacuous pin: a broken `git ls-files` call would make the
    test above pass by looking at nothing."""
    files = _tracked_files()
    assert len(files) > 100, len(files)
    assert "config/settings.py" in files


def test_the_exemption_is_narrow_and_real():
    """The ADRs really do still mention it -- which is why the
    exemption exists and why it must not widen to all of `docs/`."""
    adr = (REPO_ROOT / "docs/adr/0015-agent-layer-and-tool-contract.md")
    assert NEEDLE in adr.read_text(encoding="utf-8")
    assert _EXEMPT_PREFIXES == (
        "docs/adr/", "docs/superpowers/plans/", "docs/superpowers/specs/",
    )
    assert "docs/adr/0015-agent-layer-and-tool-contract.md".startswith(_EXEMPT_PREFIXES)


def test_the_literal_exemption_is_exactly_two_files_and_both_really_hold_the_needle():
    """Anti-vacuous pin on `_EXEMPT_LITERAL_FILES`: it must name exactly the
    two files that legitimately hold `NEEDLE` as text ABOUT this gate, and
    both must actually contain it -- a stale entry here would silently
    exempt a file that no longer needs it, or (worse) one that never did.
    """
    assert _EXEMPT_LITERAL_FILES == (
        "foundation/ops/tests/test_no_accounts_required.py",
        "identity/tests/test_request.py",
    )
    for relative in _EXEMPT_LITERAL_FILES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert NEEDLE in text, relative
