"""
Pure, dependency-free helpers for wrapping third-party text handed to an
LLM inside an unpredictable BEGIN/END marker, so a hostile body cannot
forge a boundary and escape into the surrounding prompt.

MOVED HERE (H5 review round 1, CRITICAL): both functions originated in
`agents/runtime/prompt.py` as the attachment path's own fix for I-1
(round 13 review) and were later reused, unmodified, by the S2
tool-result fence added to the same module. Import law rule 2 makes
`agents/` column-private -- another column may not import a MODULE
inside it -- and `foundation/` is this codebase's base layer, universally
importable by every column, the identical role `agents/contracts/`,
`models/contracts/`, and `identity/contracts/` play for their own
columns. A future fence caller outside `agents/` (round-3 transcript
distillation, C-3/H32) reaches these two functions here rather than
reaching into `agents.runtime.prompt`, which `agents/`'s own
column-privacy would otherwise forbid.

`carrying_block`, below, MOVED HERE TOO (H32 review round 1, IMPORTANT
3): C-3's own distillation fence started as a SECOND, hand-built
assembly of the two primitives above, in `tools/rag/distil.py`, reusing
only the primitives and not the WRAP -- exactly the duplication `agents.
runtime.prompt._carrying_block`'s own docstring already warned a future
caller off ("one fence, one home" now applies to the wrap itself, not
only to the neutralizer and the delimiter it is built from). Moving the
wrap here, alongside the primitives it is built from, closes that gap:
every caller of the WRAP -- `agents/runtime/prompt.py`'s S2 tool-result
and C-1 foreign-turn fences, and `tools/rag/distil.py`'s transcript
fence -- now reaches the identical function, imported, never a local
copy.

NO Django import, NO project import: this module is pure text
manipulation and the standard library's own CSPRNG, nothing else.
"""
from __future__ import annotations

import re
import secrets

# A line-leading run of three or more dashes, alone on its own line
# (whitespace either side tolerated) -- the shape both a Markdown
# thematic break/fence and a naive fixed delimiter take, which is
# exactly what let a hostile body close such a delimiter early (the
# round-13 review's own I-1 finding). Multiline (`re.M`) so `^`/`$` bind
# to each line inside the body, not the string as a whole.
_FENCE_LIKE_LINE_RE = re.compile(r"^([ \t]*-{3,}[ \t]*)$", re.MULTILINE)


def neutralize_fence_lines(text: str) -> str:
    """Escape any line inside a fenced body that could be mistaken for a
    fence/delimiter line -- a leading backslash before the dash run, the
    same convention Markdown itself uses to escape a would-be thematic
    break, so the text stays legible to a model (and to a human reading
    a transcript) rather than being silently stripped or replaced with
    an unrelated placeholder. Defense in depth: a caller's own delimiter
    is per-call random (`carrying_delimiter`, below), so an ordinary
    `---` in a body no longer collides with it at all -- this still runs
    regardless, since a bare dash-run could otherwise be read as SOME
    kind of section boundary by the model even without matching the real
    marker.
    """
    return _FENCE_LIKE_LINE_RE.sub(lambda m: "\\" + m.group(1).strip(), text)


def carrying_delimiter() -> str:
    """A fresh, per-call, unpredictable token -- `secrets.token_hex`,
    never `uuid4`/`random` (a CSPRNG, the same standard library module
    `identity` reaches for anywhere unpredictability is a security
    property, not merely a nicety). NEVER PERSISTED and NEVER REPRODUCED
    by any caller of this module: the fence is construction-only,
    discarded the moment the caller's own work is done, so a fresh value
    on every single call -- including a hypothetical second call over
    the exact same underlying content -- costs nothing and closes the
    door on an attacker who could otherwise learn or guess it.
    """
    return secrets.token_hex(8)


def carrying_block(header: str, label: str, text: str) -> str:
    """THE ONE WRAP (H25 review round 1, IMPORTANT 3; moved here from
    `agents/runtime/prompt.py` by H32 review round 1, IMPORTANT 3, once a
    second column needed the identical shape): `header`, then `text`
    between a per-call random BEGIN/END marker (`carrying_delimiter`)
    named `label`, with every fence-like line inside `text` neutralized
    first (`neutralize_fence_lines`). Every caller across every column
    reaches this one function rather than building their own copy of the
    same three lines -- "one fence, one home" applies to the WRAP
    itself, not only to the two primitives above it is built from.

    ALWAYS WRAPS. NEVER SNIFFS THE CONTENT FOR A REASON TO SKIP (H5
    review round 3, finding 1, reversing round 2's own finding 2 for the
    tool-result caller): a caller that returned `text` unchanged when it
    already started with its own header would hand a hostile body a
    forgeable bypass -- a fixed, documented header sentence it could
    simply begin with to skip both the random marker and the neutralizer
    entirely. A doubled fence costs nothing a reader needs: the OUTER
    marker is fresh on every call, so it is never the token a forged or
    coincidental leading sentence could have matched, and the entire
    previous text ends up as ordinary DATA nested inside the new, real
    boundary.

    A CALLER WANTING MULTIPLE PER-FILE BLOCKS UNDER ONE SHARED HEADER
    AND A RUNNING BUDGET (`agents.runtime.prompt._carrying_attachments_
    block`) stays a SEPARATE function on purpose -- a genuinely different
    shape than this function's one-header-one-block wrap, and forcing it
    through here would either lose the shared header or require this
    function to grow a multi-block mode most callers do not need.
    """
    marker = carrying_delimiter()
    return (
        f"{header}\n"
        f"--- BEGIN {label} (marker {marker}) ---\n"
        f"{neutralize_fence_lines(text)}\n"
        f"--- END {label} (marker {marker}) ---"
    )
