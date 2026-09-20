"""The CSS-ownership gate (CONSOLIDATION WAVE, audit B S25; generalised
repo-wide, Task 33 of the 2026-09-09 hygiene sweep).

**THE RULE, in one sentence:** a selector's home is the deepest template
that is an ancestor of every template that uses it. Three tiers is the
common shape -- `foundation/templates/_shell.html` (tokens/primitives two
or more COLUMNS need), a column's own `<column>/base.html` (every rule
any fragment of that column needs, plus every rule two or more of that
column's pages need), and a page's own `{% templatetag openblock %}
block <name>_style {% templatetag closeblock %}` (rules whose only
consumer is that page's own body markup) -- but it is not the only shape:
`_shell.html` -> `_settings.html` -> `inference/base.html` ->
`inference/console.html` is a real FOUR-tier chain, and `rag/base.html`'s
own children sit one tier below `_shell.html` under their own base, same
as `chat/`'s. Two corollaries do the work everywhere in this tree: a
fragment (any template whose own filename starts with `_`) never carries
its own `<style>` (the house convention this file's own docstring
restates); and BECAUSE a fragment can be included by a page nobody has
read yet, its rules belong in the nearest common ancestor of every page
that could render it from the day the fragment is created -- not from the
day a second page happens to include it, which is the version of the rule
this codebase had been trying to follow and the one a page-level style
override can silently defeat (Django template blocks do not cascade
sideways: a leaf page overriding its own style block inherits NONE of
another leaf page's own rules inside it, however loudly a comment states
the intent to share).

**WHY THIS GATE USED TO WALK ONE DIRECTORY, AND WHY THAT WAS WRONG.** The
gate stated the rule above correctly and then applied it to exactly one
hardcoded path: `_CHAT_TEMPLATES = REPO_ROOT / "agents" / "chat" /
"templates" / "chat"`, `_INCLUDE_RE` matching only `include "chat/_..."`,
and every glob walking that one directory. **That is structurally why
C-19 through C-24 -- four shared primitives duplicated across `_shell.
html`'s own descendants -- survived two prior consolidation passes: none
of them is in `agents/chat/`.** Fixing twenty-eight sites without fixing
the gate buys one pass, not a rule; the next column to grow a second
fragment reopens the exact same hole this gate exists to close. This
version walks every template in the repo -- nine directories as of this
writing (`agents/chat/templates/chat`, `foundation/landing/templates/
landing`, `foundation/setup/templates/setup`, `foundation/templates`,
`identity/templates/identity`, `models/queue/templates/jobs`, `models/
registry/templates/inference`, `tools/rag/templates/rag` (and its own
`panels/` subdirectory), `tools/vision/templates/vision`) -- by deriving
the extends graph and the include graph from the template text itself,
rather than assuming their shape.

**WHAT THIS GATE CHECKS**, mechanically, on plain file text (no Django
template engine, no ORM, no database -- the same "a filter only testable
end to end is a filter tested rarely" reasoning `tools/rag/retrieval.
py::_visibility_filters`'s own docstring states for a chunk filter,
applied here to a CSS filter instead):

1. **The extends graph** (`_extends_graph`, built from every template's
   own `{% extends "..." %}` line) and **the include graph**
   (`_consumer_graph`, built from every template's own `{% include
   "..." %}` sites) -- both DERIVED from the tree's own text, not
   assumed, so a regex that stops matching fails LOUDLY (see
   `test_the_extends_graph_is_the_shape_this_repo_actually_has`) rather
   than quietly narrowing the gate's own reach back down to nothing, the
   exact failure mode a hardcoded path list would reintroduce by a
   different route.
2. **Every class a fragment's own markup uses**, checked against **every
   class defined ONLY in one of that fragment's actual consumer pages'
   own style block** (`test_no_fragment_uses_a_class_defined_only_in_
   one_consumer_pages_style_block`) -- the generalised form of the
   original three-fragment finding.
3. **Every whole rule (selector AND body) a template's own style block
   defines**, checked against every rule each of its ANCESTORS' own
   style blocks already define
   (`test_no_template_retypes_a_rule_one_of_its_ancestors_already_owns`)
   -- the generalised form of consolidation confirm residual 3's
   settings-only check.
4. `_shell.html`'s own shared tokens, the flash recipe, and four shared
   primitives, each checked against the WHOLE tree (kept from the
   consolidation wave itself, already repo-wide before this task).

**EVERYTHING THE OLD GATE GOT RIGHT, KEPT:**

- **comment- and `{{ ... }}`-stripping before parsing.** A `{% comment
  %}` in this tree routinely contains `.selector`-shaped prose describing
  the very rule below it (`rag/_placement_choice.html`'s own comment
  literally spells the tag it is describing, `{% templatetag openblock
  %}` and all, immediately followed by its own quoted template name), and
  `{{ block.super }}` reads as a `.super` selector to a naive scan.
  Generalising also meant stripping comments before extracting a
  FRAGMENT'S OWN used classes (`_used_classes`), which the original gate
  did not do -- harmless while the gate covered five chat fragments none
  of which needed it, but real once the walk reaches `_connection_edit.
  html`/`_thread_actions.html`/`_sidebar.html`/`_menu_exclusive.html`/
  `_messages.html`, each of which carries a `class="..."`-shaped example
  inside its own doc comment.
- **whole-rule comparison (selector AND body) for every retyping check**:
  an early draft of check 3 above's settings-specific ancestor of it
  matched on class name alone and flagged `identity/entitlement.html`'s
  own `.warn { ...; font-size: .88rem; }` against `_settings.html`'s
  `.warn { ...; font-size: .85rem; }` -- the SAME name, a DIFFERENT rule,
  on a page the finding never touched. Two pages using one class name for
  unrelated rules is not duplication.
- **the S27 scoping**: a fragment whose only ACTUAL consumer is the page
  defining the class has nowhere else to render unstyled and is not a
  finding.
- **`_KNOWN_FALSE_POSITIVES`** as a real, empty set -- keyed (fragment
  FILENAME, bare class name), read only by check 2 above, left exactly as
  it was: an empty set with somewhere for the next genuinely irreducible
  compound-selector case to land, not deleted just because it is empty
  today.
- **`test_the_gate_is_not_vacuous`**, generalised to prove the repo-wide
  machinery found non-trivial facts, not just the chat-specific ones it
  used to prove alone.

**`_KNOWN_DUPLICATE_EXEMPTIONS`**, separately: keyed (repo-relative
TEMPLATE PATH, selector), read only by `test_a_shared_primitive_is_not_
retyped_below_the_shell`, and a DIFFERENT shape from `_KNOWN_FALSE_
POSITIVES` on purpose -- one set would make both lookups lie, since they
answer two different questions (one is "is this class-name collision
real duplication or a compound-selector artefact", the other is "is this
selector's exact retyped copy allowed to exist anyway"). It carries the
one entry Task 32 seeded it with, verbatim: `.delete-disclosure >
summary` and its `::-webkit-details-marker` sibling, at `agents/chat/
templates/chat/base.html:1531-1533`. That block is DEAD on `main` and
LIVE on PR #84 (`conversation.html:224`) -- Global Constraint 11 forbids
editing it here, so the gate exempts that ONE file for that ONE reason,
and the exemption dies the moment #84 lands or is abandoned (at which
point the chat copy is either redundant, and gets deleted, or the whole
`.delete-disclosure` question reopens on that branch's own terms).
Without carrying this set across unmodified, the gate cannot go green.

**WHAT GENERALISING TO THE WHOLE REPO DID *NOT* FIND** that an earlier
task in this plan should have caught: the repo-wide extends-graph walk
(check 3 above) and the repo-wide fragment/consumer walk (check 2 above)
both ran clean against every column outside `agents/chat/` on first run
-- Tasks 29 through 32 had already promoted every genuinely shared
primitive `_shell.html`-ward, and no OTHER column had grown the
`_tool_card.html`/`_turn_card.html`/`_attach_files.html` shape check 2
exists to catch. The only place generalising the walk changed an
assertion's OUTCOME (not just its reach) is `test_no_settings_page_
retypes_a_rule_settings_html_already_owns`, folded into check 3 above
because a template-name-keyed ancestor walk supersedes a hand-picked
`_settings.html`-only one outright.

**RUN AGAINST THE PRE-CONSOLIDATION TREE** (`07b90b7`, re-derived by hand
by running this file's own extraction logic against that commit's own
text, before the wave's own CSS moves landed), the original, chat-only
gate failed on exactly three fragment/page pairs: `_tool_card.html`
against `chat/conversation.html`'s own `chat_style` (10 selectors --
`tool-action`/`tool-actions`/`tool-args`/`tool-args-all`/`tool-card`/
`tool-citations`/`tool-head`/`tool-images`/`tool-message`/`tool-output`);
`_turn_card.html` against the SAME page's `chat_style` (5 selectors --
`turn`/`turn-error`/`turn-nested`/`turn-pending`/`turn-text`); and
`_attach_files.html` against `chat/workstream.html`'s own `chat_style`
(3 selectors -- `hint`/`placement`/`remember`). All three moves are
already made; check 2 above is what pins them from reopening (see
`test_the_chat_regression_the_old_gate_pinned_is_still_catchable`).

**A FRAGMENT USING A CLASS DEFINED NOWHERE AT ALL is not this gate's
business** (a bare hook class for a test or a script, with no CSS rule
anywhere, is not a placement defect) -- only a class DEFINED SOMEWHERE in
one consumer page's own style block, which is exactly the shape that
renders correctly on that one page and silently unstyled everywhere else
that page is not the fragment's only known consumer.

**TWO KINDS OF FALSE POSITIVE THE NAIVE SCAN ITSELF NAMES:**

1. **A fragment reachable from fewer than two real pages.** `_reachable_
   pages`, below, resolves this TRANSITIVELY through `_consumer_graph`
   -- a fragment included only from ANOTHER fragment (`chat/_composer.
   html` including `chat/_attach_files.html`/`chat/_attach_dragdrop.
   html`/`chat/_enter_to_send.html`/`chat/_picker.html`; `inference/
   _registered_connection.html` including `inference/_connection_edit.
   html`; `vision/_job_card.html` including `vision/_delete_control.
   html`/`vision/_output_actions.html`/`vision/_job_facts.html`) is
   walked up to whatever real PAGE renders that outer fragment, however
   many hops away, rather than stopping at zero. `chat/_offers.html`
   (reachable from exactly one page, `chat/index.html`, which defines
   `.offer`/`.offers-list`) is audit B's own S27 finding, and the same
   "fewer than two, no finding" rule covers it: one reachable page has
   nowhere else to render unstyled; zero means this text-only scan found
   no page to compare against at all, which is the CONSERVATIVE
   direction (nothing gets a false pass, but nothing gets flagged either,
   since there is nothing to flag it against) rather than a false credit
   this scan cannot actually verify. A namespace/column-shaped
   approximation was tried and rejected here on purpose: it over-reaches
   (`inference/model_sets.html` shares a column with `inference/console.
   html` but can never render `inference/_connection_edit.html` at all,
   directly or transitively, so treating it as a candidate produced two
   false findings the first time this gate ran repo-wide) in exactly the
   way the PRECISE transitive walk does not.
2. **A compound/descendant selector the naive per-token regex cannot tell
   apart from a bare one.** `_KNOWN_FALSE_POSITIVES`, below, is an empty
   set -- see its own comment for the one entry it used to carry and why
   that case retired itself. Left as a real (empty) set, not deleted, so
   the next genuinely irreducible case has somewhere to land.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import pytest

from foundation.ops.tests._helpers import REPO_ROOT

# Used only by the chat-specific regression pin
# (`test_the_chat_regression_the_old_gate_pinned_is_still_catchable`) and
# the anti-vacuous sanity check below -- the gate's own WALK no longer
# hardcodes this path anywhere; see `_template_files`.
_CHAT_TEMPLATES = REPO_ROOT / "agents" / "chat" / "templates" / "chat"

# (fragment filename, class) pairs the naive per-token regex flags that
# are not real placement defects -- kind 2's own shape, this module's
# docstring, though EMPTY as of round 18 and unchanged by generalising
# the gate to the whole repo: no NEW compound-selector ambiguity turned
# up in the nine directories this walk added. Left as a real (empty) set,
# not deleted, so the NEXT genuinely irreducible compound-selector case
# has somewhere to land without inventing the mechanism fresh.
_KNOWN_FALSE_POSITIVES: set[tuple[str, str]] = set()

# EXEMPTIONS FOR THE DUPLICATE-RULE CHECKS, keyed (repo-relative template
# path, selector) -- a DIFFERENT shape from `_KNOWN_FALSE_POSITIVES`
# above, which is keyed (fragment filename, bare class name) and is read
# only by the fragment/consumer gate. Two shapes because they answer two
# different questions; one set would make both lookups lie.
_KNOWN_DUPLICATE_EXEMPTIONS: set[tuple[str, str]] = {
    # C-24, and the ONE entry this set carries. `.delete-disclosure >
    # summary` and its `::-webkit-details-marker` sibling moved up to
    # `_shell.html`; `agents/chat/templates/chat/base.html:1531-1533`
    # still spells them out, and that block may not be touched here --
    # it is DEAD on `main` and LIVE on PR #84 (`conversation.html:224`),
    # so the #84 decision owns it. DELETE THIS EXEMPTION the moment #84
    # lands or is abandoned: at that point the chat copy is either
    # redundant (delete it) or the whole `.delete-disclosure` question
    # reopens on that branch's terms.
    ("agents/chat/templates/chat/base.html", ".delete-disclosure > summary"),
    ("agents/chat/templates/chat/base.html",
     ".delete-disclosure > summary::-webkit-details-marker"),
}

# Fragments rendered DIRECTLY by a Django view (an XHR/partial-response
# body), never through any page's own `{% include %}` -- so
# `_reachable_pages` (built purely from `{% include %}` text) finds ZERO
# pages for every one of them, which `_fragment_violations` and
# `_unexplained_zero_consumer_fragments`, below, treat very differently:
# a fragment named HERE is compared against every leaf page in its own
# column (`_column_leaf_pages`) -- the brief's own conservative default,
# the same population the pre-generalised chat-only gate checked its own
# fragments against unconditionally; a fragment with zero reachable
# pages that is NOT named here fails the gate outright, on the
# reasoning that a fragment this scan cannot place anywhere is not
# proven safe by having nothing to compare against.
_VIEW_RENDERED_FRAGMENTS: frozenset[str] = frozenset({
    # agents/chat/views/turns.py:100 -- `render(request,
    # "chat/_form_errors.html", {"message": start.error})`, a turn-start
    # failure's own 400 body.
    "chat/_form_errors.html",
    # tools/vision/views.py:1563 -- `render(request,
    # "vision/_form_errors.html", {"form": form}, status=status)`, the
    # generate form's own XHR validation-error response.
    "vision/_form_errors.html",
    # tools/vision/views.py:927, :944 -- `render(request,
    # "vision/_forbidden.html", ...)`, the tool-access refusal for a job
    # this principal may not act on.
    "vision/_forbidden.html",
    # tools/vision/views.py:314, :972, :1130, :1152 -- `render(request,
    # "vision/_unavailable.html", {"message": ...}, status=503)` at
    # every queue-unavailable call site.
    "vision/_unavailable.html",
})

# `vision/_job_card.html` and `vision/_queued_card.html` are ALSO
# rendered standalone by a view (`tools/vision/views.py::_render_card`,
# ~:862, called from `job_status`/`queue_job_status` on every poll;
# `generate` and `queue_job_status` render `vision/_queued_card.html`
# directly the same way) -- their true render sites are understated by
# the include scan for the SAME reason as the four fragments above.
# Deliberately NOT added to `_VIEW_RENDERED_FRAGMENTS`: unlike those
# four, every one of these standalone renders refreshes the SAME page
# the template-include scan already found, never a different one.
# `_render_card`'s own docstring names its two callers as "the create
# page's Recent list" (inline) and "job_status, queue_job_status, i.e.
# every poll" (standalone) -- and every poll fires from the
# `data-job-poll` attribute INSIDE `vision/_job_card.html`'s own markup
# (`tools/vision/templates/vision/_job_card.html:28`), which only ever
# renders on `vision/create.html` in the first place (`_job_card.html`
# is `{% include %}`d nowhere else in this tree, and `_queued_card.html`
# the same way, at `create.html:361`). The poll response lands back on
# the exact page that already had it. `_reachable_pages` already credits
# both fragments with exactly that one page (`vision/create.html`), so
# S27's ordinary exemption -- nowhere else to render unstyled -- already
# covers them correctly; naming them here would change nothing they get
# checked against and would wrongly suggest `vision/gallery.html` (the
# column's other leaf page, which never renders either fragment) needs
# considering too.

# `{% extends "..." %}`/`{% include "..." %}` -- ANY template, not just
# `chat/base.html`/`chat/_...`. What each one names is the template-
# LOADER name (`_template_files`, below), not a filesystem path.
_EXTENDS_RE = re.compile(r'{%\s*extends\s+"([^"]+)"\s*%}')
_INCLUDE_RE = re.compile(r'{%\s*include\s+"([^"]+)"')

# Strips a whole `{% comment %}...{% endcomment %}` region -- the same
# thing every source-text CSS pin elsewhere in this suite has to do
# first, since a comment can (and constantly does, in this column)
# contain a `.selector`-shaped substring describing the rule below it.
_COMMENT_RE = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)

# Strips `{{ ... }}` variable tags too. `{{ block.super }}` is not CSS,
# but `_CLASS_DEF_RE` below reads `.super` out of it as a DEFINED
# selector, quietly seeding the comparison set with a token no
# stylesheet anywhere contains.
_VARIABLE_RE = re.compile(r"{{.*?}}", re.DOTALL)

_CLASS_DEF_RE = re.compile(r"\.([a-zA-Z][\w-]*)")
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
_TOKEN_RE = re.compile(r"^[a-zA-Z][\w-]*$")

# `_template_files`/`_style_block_names`/`_style_block_re` are each a
# plain re-read of the repo's own template tree -- pure functions of
# disk state that does not change mid test-run -- but `_style_block`
# (called once per template in most of the checks below) would otherwise
# re-derive the SAME repo-wide block-name vocabulary from scratch on
# every single call. Memoised for this module's own runtime; every
# OTHER `REPO_ROOT.rglob(...)` in this file stays a plain, un-cached
# walk, matching the rest of this suite's convention of reading the
# tree fresh rather than trusting a stale cache.


@lru_cache(maxsize=None)
def _template_files() -> MappingProxyType[str, Path]:
    """Every tracked `.html` file in the repo, keyed by its TEMPLATE-
    LOADER name -- `chat/base.html`, `_shell.html`, `rag/panels/
    documents.html` -- which is what an `{% extends %}`/`{% include %}`
    string in this tree actually names, and is NOT the same as the
    filesystem path. Django resolves a template name against every
    directory registered as a template root: `config/settings.py`'s
    `TEMPLATES[0]["DIRS"]` names `foundation/templates/` directly, and
    every installed app's own `templates/` directory is added by
    `APP_DIRS=True` -- so in every case the name is whatever comes AFTER
    the `templates/` path segment, regardless of which of the two
    mechanisms put that directory on the loader's search path.
    `agents/chat/templates/chat/base.html` therefore resolves to
    `chat/base.html`, and `foundation/templates/_shell.html` to
    `_shell.html` -- the exact strings this tree's own `{% extends %}`/
    `{% include %}` tags already use.

    Excludes `.claude/` -- this implementation worktree lives at
    `<repo>/.claude/worktrees/<branch>`, so `REPO_ROOT` itself sits under
    a `.claude` ancestor, and a bare substring check against the
    absolute path would match every file this scan would ever see (see
    this module's shared-token/primitive pins for the same caveat) --
    checked by path PARTS, not a substring. Excludes `.venv/` for a
    different reason: Django's own `django/contrib/admin/templates/`
    ships inside the virtualenv and would otherwise be read as part of
    this repo's own template tree (`test_no_template_writes_its_own_
    flash_loop`'s own docstring names the exact phrase this bit them
    first).

    Returns an immutable `MappingProxyType`, not the plain dict this
    builds -- `@lru_cache` hands back the SAME dict object on every
    call, so a caller mutating what looks like its own local `files`
    would silently corrupt every OTHER test's view of the tree for the
    rest of this process. Every caller in this module already only
    reads it (`grep`-checked), but the type itself is what keeps that
    true going forward rather than by convention."""
    files: dict[str, Path] = {}
    for path in sorted(REPO_ROOT.rglob("*.html")):
        parts = path.relative_to(REPO_ROOT).parts
        if ".claude" in parts or ".venv" in parts:
            continue
        if "templates" not in parts:
            continue
        name = "/".join(parts[parts.index("templates") + 1:])
        files[name] = path
    return MappingProxyType(files)


def _extends_graph() -> dict[str, str | None]:
    """{template name: parent template name, or None for a root} for
    every template `_template_files` finds. Three tiers deep in this
    tree (`_shell.html` -> `_settings.html` -> `inference/base.html` ->
    `inference/console.html`), which is why the old one-base-and-its-
    leaves shape could not see outside `agents/chat/`: it never walked
    past one hop, and the tree outside chat needed more than one.
    DERIVED from each template's own `{% extends %}` line, not assumed
    -- see `test_the_extends_graph_is_the_shape_this_repo_actually_has`
    for why a derived-but-unpinned graph would not be trustworthy on its
    own: a regex that silently stopped matching would just as silently
    turn this gate back into a tautology, the same failure mode the
    hardcoded path it replaces already had."""
    graph: dict[str, str | None] = {}
    for name, path in _template_files().items():
        text = _COMMENT_RE.sub("", path.read_text())
        match = _EXTENDS_RE.search(text)
        graph[name] = match.group(1) if match else None
    return graph


def _ancestors(name: str, graph: dict[str, str | None]) -> list[str]:
    """`name`'s extends chain up to the root, nearest first --
    `["inference/base.html", "_settings.html", "_shell.html"]` for
    `inference/console.html`. Guards against a cycle (none exists in
    this tree) rather than looping forever on one."""
    chain: list[str] = []
    seen = {name}
    current = graph.get(name)
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = graph.get(current)
    return chain


def test_the_extends_graph_is_the_shape_this_repo_actually_has():
    """Anti-vacuous, and the reason the generalised gate can be trusted.
    Derived, not hardcoded -- but pinned at several known chains so a
    regex that stops matching cannot quietly turn the gate into a
    tautology."""
    graph = _extends_graph()
    assert graph["_settings.html"] == "_shell.html"
    assert graph["inference/console.html"] == "inference/base.html"
    assert graph["inference/base.html"] == "_settings.html"
    assert graph["rag/documents.html"] == "rag/base.html"
    assert graph["chat/base.html"] == "_shell.html"
    assert graph["_shell.html"] is None
    assert len(graph) >= 40


# The block-name vocabulary is DERIVED, not hardcoded to `extra_style`/
# `chat_style`: `vision/base.html` nests an empty `{% block vision_style
# %}{% endblock %}` placeholder INSIDE its own `extra_style` block --
# the exact shape `chat/base.html` already used for `chat_style`, one
# tier further down -- and `vision/gallery.html` overrides `vision_style`
# directly, a name the old hardcoded `_BLOCK_RE` (`extra_style|
# chat_style`) could not match at all.
_BLOCK_NAME_RE = re.compile(r"{%\s*block\s+(\w*_style)\s*%}")


@lru_cache(maxsize=None)
def _style_block_names() -> set[str]:
    """Every block name declared anywhere in the tree that ends
    `_style`, plus `extra_style` itself (the one name this convention
    uses that does not end that way but still needs covering: it is
    `_shell.html`'s own vocabulary for "the one style region a direct
    child may define")."""
    names = {"extra_style"}
    for path in _template_files().values():
        text = _COMMENT_RE.sub("", path.read_text())
        names.update(_BLOCK_NAME_RE.findall(text))
    return names


def test_every_style_block_name_in_the_tree_is_parsed():
    assert _style_block_names() >= {"extra_style", "chat_style", "vision_style"}


@lru_cache(maxsize=None)
def _style_block_re() -> re.Pattern[str]:
    # Longest names first, so a name that is a substring of another
    # (none exist today, but the alternation should not depend on that)
    # cannot shadow it.
    names = sorted(_style_block_names(), key=len, reverse=True)
    alternation = "|".join(re.escape(name) for name in names)
    return re.compile(
        r"{%\s*block\s+(?:" + alternation + r")\s*%}(.*?){%\s*endblock\s*%}", re.DOTALL)


def _style_block(text: str) -> str:
    """The template's own style block body -- whichever DERIVED
    `_style_block_names()` name appears first in the text, `{%
    templatetag openblock %} comment {% templatetag closeblock %}`
    regions AND `{{ ... }}` variable tags stripped first -- `""` if the
    template defines none of them at all.

    NON-GREEDY, stopping at the FIRST `{% endblock %}` found -- which,
    for a template whose style block NESTS an empty placeholder for a
    narrower one (`chat/base.html`'s `extra_style` block wraps an empty
    `{% block chat_style %}{% endblock %}`; `vision/base.html`'s
    `extra_style` wraps `vision_style` the same way), is the INNER
    block's own `{% endblock %}`, not the outer one. Both placeholders
    happen to sit as the LAST line before their outer `{% endblock %}`
    today, so nothing is currently lost -- but a rule added AFTER that
    placeholder line and before the outer close would be silently
    invisible to every reader in this module, since none of them look
    past the first `{% endblock %}` either.
    `test_no_rule_is_silently_dropped_after_a_nested_style_placeholder`,
    below, is the pin for exactly that regression, reading the region
    this function's own match stops short of directly rather than
    trusting this function to see it."""
    stripped = _VARIABLE_RE.sub("", _COMMENT_RE.sub("", text))
    match = _style_block_re().search(stripped)
    return match.group(1) if match else ""


def test_no_rule_is_silently_dropped_after_a_nested_style_placeholder():
    """See `_style_block`'s own docstring for the mechanism this pins.
    `chat/base.html` and `vision/base.html` are the two templates in
    this tree whose style block wraps an empty nested placeholder
    (`chat_style`/`vision_style` respectively) -- for each, this reads
    the LITERAL text between that placeholder's own `{% endblock %}` and
    the OUTER block's `{% endblock %}` directly (not through `_style_
    block`, which is the reader this pin exists to check) and fails if
    that region parses as containing a rule at all. A balanced-parse
    check, not a length floor: a floor would need updating every time
    either file's real content grows, and would not name WHERE the
    drop happened the way reading the exact dropped region does."""
    cases = [
        (REPO_ROOT / "agents/chat/templates/chat/base.html", "extra_style", "chat_style"),
        (REPO_ROOT / "tools/vision/templates/vision/base.html", "extra_style", "vision_style"),
    ]
    for path, outer_name, inner_name in cases:
        stripped = _VARIABLE_RE.sub("", _COMMENT_RE.sub("", path.read_text()))
        outer_open = re.search(r"{%\s*block\s+" + re.escape(outer_name) + r"\s*%}", stripped)
        assert outer_open, f"{path}: outer block {outer_name!r} not found -- this pin is broken"
        inner_pair = re.search(
            r"{%\s*block\s+" + re.escape(inner_name) + r"\s*%}.*?{%\s*endblock\s*%}",
            stripped, re.DOTALL)
        assert inner_pair and inner_pair.start() > outer_open.end(), (
            f"{path}: nested block {inner_name!r} not found inside {outer_name!r} -- "
            "this pin's own assumption about the file's shape no longer holds"
        )
        outer_close = re.compile(r"{%\s*endblock\s*%}").search(stripped, inner_pair.end())
        assert outer_close, (
            f"{path}: no {{% endblock %}} found after the nested {inner_name!r} placeholder")
        trailing = stripped[inner_pair.end():outer_close.start()]
        dropped = _defined_classes(trailing)
        assert not dropped, (
            f"{path}: a rule exists between the nested {inner_name!r} placeholder's own "
            f"{{% endblock %}} and {outer_name!r}'s own closing tag -- _style_block's "
            f"non-greedy match stops at the INNER endblock and would silently drop it. "
            f"Classes found there: {sorted(dropped)}"
        )


def _defined_classes(style_text: str) -> set[str]:
    return set(_CLASS_DEF_RE.findall(style_text))


def _used_classes(markup_text: str) -> set[str]:
    """Every literal class token a fragment's own markup uses. `{%
    templatetag openblock %} comment {% templatetag closeblock %}`
    stripped first (generalising this gate reaches fragments -- `_
    connection_edit.html`, `_thread_actions.html`, `_sidebar.html`, `_
    menu_exclusive.html`, `_messages.html` -- whose own doc comments
    carry a `class="..."`-shaped markup example describing themselves;
    the original five-fragment gate never needed this, since none of
    chat's own fragments happened to)."""
    stripped = _COMMENT_RE.sub("", markup_text)
    classes: set[str] = set()
    for attr_value in _CLASS_ATTR_RE.findall(stripped):
        for token in re.split(r"[\s{}%]+", attr_value):
            token = token.strip()
            if token and _TOKEN_RE.match(token):
                classes.add(token)
    return classes


def _is_fragment(name: str, graph: dict[str, str | None]) -> bool:
    """A template whose own filename starts with `_` AND is never itself
    the TARGET of an `{% extends %}` -- the second clause is what keeps
    `_shell.html`/`_settings.html` out of the fragment population: both
    happen to spell their own filename with a leading underscore, but
    both are BASES (extended, never included), the opposite of what this
    gate means by "fragment." A template only ever appears as a value in
    the extends graph if something extends it."""
    if not name.rsplit("/", 1)[-1].startswith("_"):
        return False
    return name not in set(graph.values())


def _consumer_graph() -> dict[str, set[str]]:
    """{included template name: set of template names whose OWN markup
    directly `{% include %}`s it} -- generalised from the old gate's
    `fragment_consumers`, which was built ONLY from chat's own leaf
    pages' text. Built here from EVERY template's own text, so a
    fragment included from another fragment (`chat/_composer.html`
    including `chat/_attach_files.html`; `inference/_registered_
    connection.html` including `inference/_connection_edit.html`;
    `vision/_job_card.html` including `vision/_delete_control.html`) is
    tracked too -- ONE HOP at a time; `_reachable_pages`, below, is what
    walks this graph transitively to find every actual PAGE a fragment
    can reach, however many fragment-to-fragment hops away."""
    consumers: dict[str, set[str]] = {}
    for name, path in _template_files().items():
        text = _COMMENT_RE.sub("", path.read_text())
        for included in _INCLUDE_RE.findall(text):
            consumers.setdefault(included, set()).add(name)
    return consumers


def _reachable_pages(
    fragment_name: str, consumer_graph: dict[str, set[str]], graph: dict[str, str | None]
) -> set[str]:
    """Every actual PAGE (non-fragment template) that could render this
    fragment, resolved TRANSITIVELY through `_consumer_graph` -- a
    fragment nested inside another fragment (`chat/_turn_card.html`
    inside `chat/_turn_block.html`; `inference/_connection_edit.html`
    inside `inference/_registered_connection.html`; `vision/_job_facts.
    html` inside `vision/_job_card.html`) reaches whatever PAGE includes
    that intermediate fragment, however many hops away -- exactly what
    THE RULE means by "every template that uses it": not just the
    fragment's own direct includer, but every page a chain of includes
    can actually reach.

    This is the PRECISE form of that reach, not an approximation: a
    coarser same-directory/same-column guess over-reaches (`inference/
    model_sets.html` sits in the same column as `console.html` but never
    includes `_installed_rows_table.html`/`_registered_connection.html`
    -- directly or transitively -- so it must not be treated as able to
    render `_installed_row.html`/`_connection_edit.html` at all); a
    direct-includer-only guess under-reaches (misses every fragment
    nested inside another one, which this tree has several of). BFS,
    stopping at the first non-fragment template on each branch and
    cycle-guarded (`chat/_turn_card.html` includes itself, for nested
    tool-call cards)."""
    pages: set[str] = set()
    seen = {fragment_name}
    frontier = {fragment_name}
    while frontier:
        next_frontier: set[str] = set()
        for name in frontier:
            for includer in consumer_graph.get(name, set()):
                if includer in seen:
                    continue
                seen.add(includer)
                if _is_fragment(includer, graph):
                    next_frontier.add(includer)
                else:
                    pages.add(includer)
        frontier = next_frontier
    return pages


def _column_leaf_pages(
    fragment_name: str, graph: dict[str, str | None], files: MappingProxyType[str, Path]
) -> set[str]:
    """Every LEAF page in the SAME column as `fragment_name` -- every
    template that shares its namespace prefix (`"chat"` for `"chat/
    _form_errors.html"`) AND whose own extends chain passes through that
    column's own `<prefix>/base.html`, excluding `base.html` itself
    (the SHARED tier, not a page) and every other fragment.

    Used ONLY for `_VIEW_RENDERED_FRAGMENTS` members: a Django view
    calling `render()` directly leaves no `{% include %}` text anywhere,
    so `_reachable_pages` (built purely from that text) can prove
    nothing about where such a fragment actually renders. Comparing it
    against every page in its own column is the brief's own conservative
    default for that shape -- the same population the pre-generalised
    chat-only gate checked its own fragments against unconditionally,
    scoped down from "every page in the repo" to "every page in the one
    column this fragment's own name places it in."

    A page reached only by extending `_settings.html`/`_shell.html`
    directly -- `chat/settings.html`, `vision/engine_files.html` (audit
    2's own F9 finding, generalised) -- is excluded: it shares a
    directory with this fragment's column but could never render it,
    the same reasoning `_shell_outside_block_classes`'s callers already
    apply one tier up."""
    if "/" not in fragment_name:
        return set()
    prefix = fragment_name.split("/", 1)[0]
    base_name = f"{prefix}/base.html"
    if base_name not in files:
        return set()
    pages: set[str] = set()
    for name in files:
        if name == base_name or _is_fragment(name, graph):
            continue
        if "/" not in name or name.split("/", 1)[0] != prefix:
            continue
        if base_name in _ancestors(name, graph):
            pages.add(name)
    return pages


def _unexplained_zero_consumer_fragments(
    graph: dict[str, str | None],
    files: MappingProxyType[str, Path],
    consumer_graph: dict[str, set[str]],
) -> list[str]:
    """Fragments `_reachable_pages` finds ZERO pages for, and that are
    NOT named in `_VIEW_RENDERED_FRAGMENTS`. Zero reachable pages means
    one of two things: a view renders the fragment directly, bypassing
    `{% include %}` entirely (the four known cases, each named in
    `_VIEW_RENDERED_FRAGMENTS`'s own comment along with the view that
    renders it) -- or the fragment is genuinely dead/unreferenced, which
    is itself worth knowing about. Either way, treating "zero reachable"
    the same as the S27 "one reachable" exemption (as a bare
    `len(reachable) < 2` check does) is wrong: S27 says a fragment with
    exactly one home has nowhere ELSE to render unstyled, which is a
    fact this scan has PROVEN; zero reachable pages proves nothing at
    all -- it means this scan could not find where the fragment goes,
    which is not the same claim. This is the gate's own coverage check:
    it must stay empty, and the fix for a new entry is either to name
    the fragment (with the view that renders it) in `_VIEW_RENDERED_
    FRAGMENTS`, or to fix the include graph so the fragment is
    reachable."""
    unexplained = []
    for name in sorted(files):
        if not _is_fragment(name, graph):
            continue
        if name in _VIEW_RENDERED_FRAGMENTS:
            continue
        if not _reachable_pages(name, consumer_graph, graph):
            unexplained.append(name)
    return unexplained


@lru_cache(maxsize=None)
def _shell_outside_block_classes() -> frozenset[str]:
    """Classes `_shell.html` defines OUTSIDE any block -- in its own
    `<style>` tag, before `{% templatetag openblock %} block extra_style
    {% templatetag closeblock %}` opens -- and therefore inherited
    UNCONDITIONALLY by every descendant regardless of `{{ block.super
    }}` (see `_shell_style_outside_block`'s own docstring, below, for
    why the flash recipe lives there on purpose). `own_defined`
    (`_style_block`-based, reading only INSIDE a named block) cannot see
    this region at all, so without folding it in here, `.messages`/
    `.msg` -- both defined there per C-21/C-52 -- would misread as
    "locally owned" by every page that ALSO writes its own partial `.
    msg`/`.messages` variant inside its own style block, which is
    exactly what most pages this gate reaches do (`console.html`'s own
    `--ok-bg` `.msg`, `search.html`'s own padding -- see `test_a_kept_
    local_variant_does_not_half_override_a_shared_property_set`, which
    exists BECAUSE that layering is legitimate, not a duplication)."""
    return frozenset(_defined_classes(_shell_style_outside_block(
        (REPO_ROOT / "foundation/templates/_shell.html").read_text())))


def _local_classes(
    graph: dict[str, str | None], own_defined: dict[str, set[str]]
) -> dict[str, set[str]]:
    """{template name: classes it defines in its OWN style block that no
    ANCESTOR also defines, and that `_shell.html`'s own outside-block
    region (`_shell_outside_block_classes`, above) does not already
    cover unconditionally} -- genuinely page/base-scoped selectors. The
    generalised form of the old gate's `page_only = defined -
    shared_classes`, where `shared_classes` was hardcoded to `chat/base.
    html`'s own set: that only worked because every page it checked was
    exactly one extends-tier below it. Here the subtraction walks each
    template's own chain, whatever its depth -- `inference/console.
    html`'s local classes exclude anything `inference/base.html`,
    `_settings.html`, OR `_shell.html` already defines, not just the one
    immediately above it -- plus the shell's outside-block set, which
    every template in this tree inherits regardless of chain depth. That
    outside-block set is not one or two names: 28 as of this writing,
    every one of them a generic-enough word (`.empty`, `.banner`,
    `.chip`, `.current`, `.muted`, `.messages`, `.msg`... the full list is
    `_shell_outside_block_classes()`'s own return value) that a page's
    own unrelated rule could otherwise collide with it by coincidence,
    which is exactly the false-positive `test_no_fragment_uses_a_class_
    defined_only_in_one_consumer_pages_style_block` hit for `.messages`/
    `.msg` before this subtraction existed."""
    shell_outside = _shell_outside_block_classes()
    local: dict[str, set[str]] = {}
    for name, defined in own_defined.items():
        ancestor_defined: set[str] = set()
        for ancestor in _ancestors(name, graph):
            ancestor_defined |= own_defined.get(ancestor, set())
        local[name] = defined - ancestor_defined - shell_outside
    return local


def _fragment_violations(
    graph: dict[str, str | None],
    files: MappingProxyType[str, Path],
    local_classes: dict[str, set[str]],
    consumer_graph: dict[str, set[str]],
) -> list[tuple[str, str, list[str]]]:
    """The shared machinery THE GATE and its permanent regression pin
    both run.

    For a fragment named in `_VIEW_RENDERED_FRAGMENTS` (a view renders
    it directly; `_reachable_pages` proves nothing about it): compared
    against EVERY leaf page in its own column (`_column_leaf_pages`),
    unconditionally -- no S27 exemption, the brief's own conservative
    default for a fragment this scan cannot place via the include graph
    at all.

    For every other fragment: compared against every page `_reachable_
    pages` finds for it (TRANSITIVELY, through any intermediate
    fragment). S27's own scoping falls out of that set directly: fewer
    than two reachable pages means nowhere else to render unstyled (0:
    no page to compare against either way; 1: that page's own local
    override is exactly the fragment's only home) -- a fragment with
    ZERO reachable pages that is NOT in `_VIEW_RENDERED_FRAGMENTS` is
    a SEPARATE finding, `_unexplained_zero_consumer_fragments` below,
    not folded into this one.

    Either way: does the fragment use a class that page defines LOCALLY
    (`_local_classes`)?"""
    fragments = sorted(name for name in files if _is_fragment(name, graph))
    violations: list[tuple[str, str, list[str]]] = []
    for frag_name in fragments:
        used = _used_classes(files[frag_name].read_text())
        if not used:
            continue
        if frag_name in _VIEW_RENDERED_FRAGMENTS:
            pages = _column_leaf_pages(frag_name, graph, files)
        else:
            reachable = _reachable_pages(frag_name, consumer_graph, graph)
            if len(reachable) < 2:
                continue
            pages = reachable
        for page in sorted(pages):
            local = local_classes.get(page, set())
            hit = sorted(
                cls for cls in (used & local)
                if (files[frag_name].name, cls) not in _KNOWN_FALSE_POSITIVES
            )
            if hit:
                violations.append((frag_name, page, hit))
    return violations


def test_no_fragment_uses_a_class_defined_only_in_one_consumer_pages_style_block():
    """THE GATE. See this module's own docstring for the rule, the
    method, the two known-false-positive shapes, and the pre-fix
    failure this pins shut.

    For every fragment, for every page `_reachable_pages` can actually
    reach it from (TRANSITIVELY, through any chain of includes) -- or,
    for a fragment `_VIEW_RENDERED_FRAGMENTS` names, every leaf page in
    its own column: is any class the fragment's own markup uses defined
    ONLY in that page's own local style region (`_local_classes`, above
    -- i.e. NOT also defined by a template that is an ancestor of EVERY
    page reaching this fragment, which would make it properly shared)?
    If so, that page's local override renders the fragment unstyled on
    every OTHER page that reaches it -- S27's own scoping (a fragment
    reachable from fewer than two pages has nowhere else to render
    unstyled, and is not a finding).

    ALSO asserts `_unexplained_zero_consumer_fragments` is empty: a
    fragment with ZERO reachable pages that is not named in
    `_VIEW_RENDERED_FRAGMENTS` is a gap in this gate's own coverage, not
    a pass -- see that function's own docstring for why 0 and 1 known
    consumers are NOT the same claim."""
    graph = _extends_graph()
    files = _template_files()
    own_defined = {
        name: _defined_classes(_style_block(path.read_text())) for name, path in files.items()
    }
    assert any(own_defined.values()), (
        "not one template's own style block parsed a single class -- the gate itself is broken")

    local_classes = _local_classes(graph, own_defined)
    consumer_graph = _consumer_graph()

    fragments = sorted(name for name in files if _is_fragment(name, graph))
    assert fragments, "no fragment templates found -- the gate itself is broken"

    unexplained = _unexplained_zero_consumer_fragments(graph, files, consumer_graph)
    assert not unexplained, (
        "these fragments have ZERO reachable pages via the include graph and are not "
        "declared in _VIEW_RENDERED_FRAGMENTS -- either a view renders them directly (name "
        "the fragment AND the view/line in a comment there) or the include graph itself needs "
        f"fixing: {unexplained}"
    )

    violations = _fragment_violations(graph, files, local_classes, consumer_graph)

    assert not violations, (
        "a fragment uses a class defined only in one page's own style region, never in a "
        "template that is an ancestor of every page that reaches this fragment -- it renders "
        f"unstyled on every OTHER page that includes it: {violations}"
    )


def test_the_view_rendered_fragments_set_names_real_fragments():
    """Sanity pin for `_VIEW_RENDERED_FRAGMENTS` itself: every entry
    must be a real template that IS a fragment (catches a typo, or a
    stale entry for a fragment since deleted or renamed) -- and, since
    the whole POINT of the set is "the include graph cannot see this
    fragment's real render sites," every entry should currently have a
    non-empty `_column_leaf_pages` result, or the set is naming a
    fragment with no column to check it against at all, which would
    make its own membership here a no-op."""
    graph = _extends_graph()
    files = _template_files()
    assert _VIEW_RENDERED_FRAGMENTS, "_VIEW_RENDERED_FRAGMENTS is empty -- this pin is vacuous"
    for name in sorted(_VIEW_RENDERED_FRAGMENTS):
        assert name in files, (
            f"{name} is not a real template -- _VIEW_RENDERED_FRAGMENTS has a stale entry")
        assert _is_fragment(name, graph), f"{name} is named but is not a fragment"
        assert _column_leaf_pages(name, graph, files), (
            f"{name} has no column leaf pages to check against -- _column_leaf_pages "
            "or this entry itself may be broken"
        )


def test_the_chat_regression_the_old_gate_pinned_is_still_catchable():
    """Step 7's own regression, kept as a permanent pin rather than only
    a by-hand ritual. `chat/_composer.html` is reachable from THREE real
    pages (`index.html`, `conversation.html`, `workstream.html`) and its
    own markup uses `agent-picker`, a class `chat/base.html` owns in its
    shared region -- the same "fragment uses a base-owned class" shape as
    the three pre-consolidation failures this gate pinned (`_tool_card.
    html` x10, `_turn_card.html` x5, `_attach_files.html` x3; see this
    module's own docstring). This proves the generalised gate still finds
    that shape, by ACTUALLY mutating the in-memory facts the gate itself
    runs on -- moving `agent-picker` off `chat/base.html`'s own defined
    set and onto `chat/workstream.html`'s, the exact shape the
    pre-consolidation bug had -- and re-running the SAME `_fragment_
    violations` the gate above calls, not a hand-rolled parallel check
    that could quietly drift from what the gate actually does.

    `agent-picker`, NOT `composer-card` (this pin's example class until
    the settings assistant): `composer-card`/`composer-textarea`/
    `composer-toolbar`/`composer-toolbar-left`/`composer-toolbar-right`
    all promoted to `_shell.html` (`test_the_shared_composers_selectors_
    live_in_the_shell_not_in_chat_base`, above) once `_settings.html`'s
    own panel started including this same fragment, so `chat/base.html`
    no longer owns any of them -- `agent-picker` is the one selector
    `_composer.html` still uses that spec §6.5's orchestrator ruling
    deliberately left chat-only (the panel always passes `composer_mode=
    "turn"`, which never emits it), so it is still `chat/base.html`'s to
    lose."""
    graph = _extends_graph()
    files = _template_files()
    own_defined = {
        name: _defined_classes(_style_block(path.read_text())) for name, path in files.items()
    }
    consumer_graph = _consumer_graph()

    composer_used = _used_classes((_CHAT_TEMPLATES / "_composer.html").read_text())
    moved = {"agent-picker"} & composer_used
    assert moved, (
        "_composer.html's own markup no longer uses agent-picker -- this pin itself is stale")
    assert moved <= own_defined["chat/base.html"], (
        "chat/base.html no longer owns agent-picker -- the consolidation this pin "
        "regression-tests has itself regressed"
    )
    reachable = _reachable_pages("chat/_composer.html", consumer_graph, graph)
    assert len(reachable) >= 2, (
        "_composer.html is reachable from fewer than two pages -- the S27 exemption would "
        "swallow this regression silently and this pin would no longer prove anything"
    )

    # THE MUTATION: exactly what Step 7 asks for by hand, done here to
    # the in-memory facts instead of the file on disk -- move `moved`
    # out of `chat/base.html`'s own defined set and into `chat/
    # workstream.html`'s, the same shape the pre-consolidation bug had.
    mutated_defined = dict(own_defined)
    mutated_defined["chat/base.html"] = own_defined["chat/base.html"] - moved
    mutated_defined["chat/workstream.html"] = own_defined["chat/workstream.html"] | moved
    mutated_local = _local_classes(graph, mutated_defined)

    violations = _fragment_violations(graph, files, mutated_local, consumer_graph)
    # THE FULL TRIPLE, not merely "some violation mentions _composer.html":
    # fragment, the SPECIFIC page the mutation moved agent-picker onto,
    # and agent-picker itself -- so a violation this pin did not ask for
    # (a different fragment/page/class the mutation happened to also
    # disturb) cannot pass this assertion by coincidence.
    expected = ("chat/_composer.html", "chat/workstream.html", ["agent-picker"])
    assert expected in violations, (
        "moving chat/base.html's shared agent-picker class into chat/workstream.html's own "
        "block did not reopen the EXACT finding expected -- the generalised gate would not "
        f"catch the pre-consolidation regression shape it exists to pin. expected {expected}, "
        f"violations were: {violations}"
    )


# --- The same check, for BARE ELEMENT selectors (fix round 1, M1) ----------
#
# CHECK 2 ABOVE MATCHES CLASS NAMES, AND A FRAGMENT CAN BORROW CHROME
# WITHOUT ONE. `foundation/templates/_transfer_panel.html` rendered
# `<section class="transfer-panel">` and took its whole card -- panel
# background, border, radius, padding -- from the bare `section { ... }`
# rule in `identity/templates/identity/entitlement.html`'s OWN style
# block: chromed on the one page that has such a rule, and bare on the
# next page to include the fragment. That is exactly the defect check 2
# exists to catch, wearing an element selector instead of a class, and
# the gate could not see it.
#
# ONLY BARE ELEMENT SELECTORS, and only ones a fragment's own markup
# really opens. A descendant selector (`section h2`, `.card p`) is a
# deeper question this check deliberately does not answer: a fragment
# nested inside a page's own `<section>` is styled by that page's
# `section h2` whether the fragment asks for it or not, and calling every
# such inheritance a finding would flag half the tree. The bare form is
# the one that reads as "this element type looks like THIS here", which
# is what a fragment ends up depending on.

_ELEMENT_NAMES = frozenset({
    "a", "article", "aside", "audio", "blockquote", "button", "canvas", "code", "dd",
    "details", "div", "dl", "dt", "em", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "iframe", "img",
    "input", "label", "legend", "li", "main", "meter", "nav", "ol", "output", "p",
    "pre", "progress", "section", "select", "small", "span", "strong", "summary",
    "svg", "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "time", "tr",
    "ul", "video",
})

_TAG_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)\b")


def _used_elements(markup_text: str) -> set[str]:
    """Every HTML element a fragment's own markup opens, restricted to
    the names a stylesheet in this tree plausibly selects on.
    `{% comment %}` regions stripped first, the same reason
    `_used_classes` strips them: several fragments carry a markup
    example inside their own doc comment."""
    stripped = _COMMENT_RE.sub("", markup_text)
    return {name.lower() for name in _TAG_RE.findall(stripped)} & _ELEMENT_NAMES


def _defined_elements(style_text: str) -> set[str]:
    """Every BARE element selector a style block defines, including each
    member of a comma-separated list (`td, th { ... }` defines both).
    Anything with a class, id, attribute, pseudo or descendant part is
    not a bare element selector and is skipped."""
    defined: set[str] = set()
    for selector, _body in _RULE_RE.findall(style_text):
        for part in selector.split(","):
            part = " ".join(part.split()).lower()
            if part in _ELEMENT_NAMES:
                defined.add(part)
    return defined


def _local_elements(
    graph: dict[str, str | None], own_defined: dict[str, set[str]]
) -> dict[str, set[str]]:
    """{template name: bare element selectors it defines in its OWN
    style block that no ANCESTOR defines, and that `_shell.html`'s
    unconditional outside-block region does not already cover} -- the
    element twin of `_local_classes`, and subtracting the same two
    things for the same two reasons."""
    shell_outside = _defined_elements(_shell_style_outside_block(
        (REPO_ROOT / "foundation/templates/_shell.html").read_text()))
    local: dict[str, set[str]] = {}
    for name, defined in own_defined.items():
        inherited: set[str] = set()
        for ancestor in _ancestors(name, graph):
            inherited |= own_defined.get(ancestor, set())
        local[name] = defined - inherited - shell_outside
    return local


def _element_violations(
    graph: dict[str, str | None],
    files: MappingProxyType[str, Path],
    local_elements: dict[str, set[str]],
    consumer_graph: dict[str, set[str]],
) -> list[tuple[str, str, list[str]]]:
    """`_fragment_violations`' element twin, scoped identically -- the
    `_VIEW_RENDERED_FRAGMENTS` branch, the S27 fewer-than-two-pages
    exemption, and the same (fragment, page, names) triple."""
    fragments = sorted(name for name in files if _is_fragment(name, graph))
    violations: list[tuple[str, str, list[str]]] = []
    for frag_name in fragments:
        used = _used_elements(files[frag_name].read_text())
        if not used:
            continue
        if frag_name in _VIEW_RENDERED_FRAGMENTS:
            pages = _column_leaf_pages(frag_name, graph, files)
        else:
            reachable = _reachable_pages(frag_name, consumer_graph, graph)
            if len(reachable) < 2:
                continue
            pages = reachable
        for page in sorted(pages):
            hit = sorted(used & local_elements.get(page, set()))
            if hit:
                violations.append((frag_name, page, hit))
    return violations


def _element_facts():
    graph = _extends_graph()
    files = _template_files()
    own_defined = {
        name: _defined_elements(_style_block(path.read_text()))
        for name, path in files.items()
    }
    return graph, files, own_defined, _consumer_graph()


def test_no_fragment_relies_on_an_element_rule_defined_only_in_one_consumer_page():
    """CHECK 2's TWIN, for bare element selectors. See the block comment
    above for why this shape escaped the class-matching gate and what it
    deliberately does not cover."""
    graph, files, own_defined, consumer_graph = _element_facts()
    assert any(own_defined.values()), (
        "not one style block parsed a single bare element selector -- this check is broken")
    violations = _element_violations(
        graph, files, _local_elements(graph, own_defined), consumer_graph)
    assert not violations, (
        "a fragment relies on a bare element rule defined only in one page's own style "
        "region -- it renders unstyled on every OTHER page that reaches this fragment: "
        f"{violations}"
    )


def test_the_element_check_would_actually_catch_the_transfer_panel_shape():
    """ANTI-VACUOUS PIN, and it has to be SYNTHETIC rather than the
    historical mutation `test_the_chat_regression_the_old_gate_pinned_
    is_still_catchable` uses: no fragment in this tree currently uses an
    element that one of its own ancestors styles, so there is no real
    shared element rule to move down into a page.

    IT HAS ALREADY EARNED ITS KEEP ONCE, which is worth recording
    because the fix-round-1 version of this docstring could only predict
    it would. `_transfer_panel.html` had exactly ONE reachable page when
    this check landed, so the S27 exemption skipped it and the check
    found nothing. Phase 2 gave the fragment two more consumer pages,
    the exemption stopped applying, and this check immediately went red
    on the real thing: the fragment opened a `<section>`, and
    `identity/entitlement.html` styles bare `section` in its own block.
    The fragment now opens a `<div>`.

    The mutation below injects the defect directly: take a fragment with
    two real consumer pages, take an element its own markup really
    opens, and declare that element as one consumer page's OWN local
    rule. The check must then report that exact triple -- proving the
    machinery fires rather than passing by having nothing to look at."""
    graph, files, own_defined, consumer_graph = _element_facts()

    subject = "chat/_tool_card.html"
    page = "chat/all.html"
    assert subject in files and page in files
    used = _used_elements(files[subject].read_text())
    assert "dl" in used, (
        f"{subject} no longer opens a <dl> -- this pin itself is stale")
    reachable = _reachable_pages(subject, consumer_graph, graph)
    assert len(reachable) >= 2 and page in reachable, sorted(reachable)

    mutated = dict(own_defined)
    mutated[page] = own_defined.get(page, set()) | {"dl"}
    violations = _element_violations(
        graph, files, _local_elements(graph, mutated), consumer_graph)
    expected = (subject, page, ["dl"])
    assert expected in violations, (
        "declaring a bare `dl` rule in one consumer page's own style block did not reopen "
        f"the expected finding -- the element check would not fire. expected {expected}, "
        f"violations were: {violations}"
    )


def test_the_transfer_panel_brings_its_own_card_chrome():
    """FIX ROUND 1, M1. It was pinned directly because the element check
    above could not see it at the time -- `_transfer_panel.html` had
    exactly one reachable page, which the S27 exemption skips. Phase 2
    gave it three, so the check covers the element half now; this stays
    because it pins the other half, which no derived check states: that
    the panel's CARD comes from the sanctioned shared home rather than
    from whichever consumer page happens to style its container."""
    files = _template_files()
    panel = files["_transfer_panel.html"].read_text()
    # STARTSWITH, not the whole attribute: the class list carries an
    # optional `transfer-panel-bare` branch for the phase-2 consumers.
    assert 'class="transfer-panel' in panel, "the panel's root class was renamed"
    settings_rules = dict(_rules(_style_block(files["_settings.html"].read_text())))
    chrome = settings_rules.get(".transfer-panel")
    assert chrome is not None, (
        ".transfer-panel is not defined in _settings.html -- the panel is borrowing its "
        "card from a consumer page again")
    for declaration in ("background:", "border:", "padding:"):
        assert declaration in chrome, (declaration, chrome)

    # ITS HEADING TOO (re-review nit NR2). The card moved but the
    # panel's own `<h2>` was still sized by `identity/entitlement.html`'s
    # `section h2` -- the same borrowing, one selector deeper, and
    # invisible to the element check above, which reads BARE element
    # selectors only and deliberately says nothing about descendant
    # ones. Pinned directly for the same reason the card is.
    heading = settings_rules.get(".transfer-panel h2")
    assert heading is not None, (
        ".transfer-panel h2 is not defined in _settings.html -- the panel's heading is "
        "borrowing its size from whichever consumer styles `section h2`")
    assert "font-size:" in heading, heading


def test_both_transfer_panes_are_one_fixed_size_whatever_they_hold():
    """OWNER FEEDBACK, 2026-09-16 (pre-merge on PR #97): a pane holding
    one row rendered as a short box beside a full one, and the two
    Add/Remove buttons sat at different heights. The owner asked for the
    same box regardless -- empty space under a short list rather than a
    box that shrinks to fit it.

    `height`, NOT `max-height`, is the whole of that change and the one
    thing worth pinning: a max-height grows to fit and stops, which is
    exactly the ragged behaviour reported. The value is deliberately NOT
    asserted -- 16rem is taste and may be retuned; what must not
    silently regress is the KIND of constraint.

    IN THE SHARED HOME, so it holds for every consumer at once: the
    entitlement page's panels and both access pages' expanded rows are
    the same fragment, and a leaf page re-stating a height would put the
    two directions back out of step.
    """
    rules = dict(_rules(_style_block(_template_files()["_settings.html"].read_text())))
    pane_list = rules.get(".transfer-list")
    assert pane_list is not None, (
        ".transfer-list is not defined in _settings.html -- the panes are taking their "
        "size from somewhere this gate cannot see")
    assert re.search(r"(^|;)\s*height:", pane_list), (
        ".transfer-list has no fixed `height` -- with `max-height` alone a pane shrinks "
        f"to its content and the two panes render ragged again: {pane_list}")
    assert "max-height:" not in pane_list, (
        ".transfer-list is back on `max-height`, which is the reported defect: "
        f"{pane_list}")
    # AND IT IS STILL A SCROLL BOX. A fixed height without this would
    # CLIP a long list instead of scrolling it, which trades the
    # reported defect for a worse one -- rows an operator cannot reach.
    assert "overflow-y: auto" in pane_list, pane_list


def test_the_gate_is_not_vacuous():
    """Anti-vacuous pin: a gate that found zero fragments, zero
    consumers, zero local classes, or zero fragment class usage at all
    would pass the tests above by having nothing to check -- the failure
    direction that matters least for a report whose whole point is to
    catch a real defect. Proven here by re-deriving non-empty facts
    repo-wide, not just the chat-specific ones the pre-generalised
    version of this pin proved alone."""
    files = _template_files()
    assert len(files) >= 40

    graph = _extends_graph()
    fragments = [name for name in files if _is_fragment(name, graph)]
    assert len(fragments) >= 20

    consumer_graph = _consumer_graph()
    total_include_sites = sum(len(v) for v in consumer_graph.values())
    assert total_include_sites >= 30, (
        "fewer than 30 total {% include %} sites found across the repo -- "
        "the include-graph extraction itself may be broken"
    )
    outside_chat = {
        name for name, consumers in consumer_graph.items()
        if not name.startswith("chat/") and consumers
    }
    assert len(outside_chat) >= 10, (
        "fewer than 10 included templates outside chat/ -- the whole point of "
        "generalising this gate was to see the other eight directories at all"
    )

    multiply_reachable = [
        frag for frag in fragments
        if len(_reachable_pages(frag, consumer_graph, graph)) >= 2
    ]
    assert len(multiply_reachable) >= 5, (
        "fewer than 5 fragments are reachable from two or more real pages -- "
        "_reachable_pages itself may be broken"
    )

    base_text = (_CHAT_TEMPLATES / "base.html").read_text()
    shared_classes = _defined_classes(_style_block(base_text))
    assert len(shared_classes) > 20, "chat/base.html's own shared class count looks too small"

    turn_card = (_CHAT_TEMPLATES / "_turn_card.html").read_text()
    used = _used_classes(turn_card)
    assert "tool-card" in used or "turn" in used or used, (
        "_turn_card.html's own markup carries no class= attribute at all -- "
        "the USED-class extraction itself is broken"
    )


# CONSOLIDATION CONFIRM RESIDUAL 3, second half. `chat/
# tool_entitlements.html` and `chat/agent_entitlements.html` used to
# carry byte-identical `.warn`/`.lede`/`.label-row`/`.label-row
# button`/`.no-labels` blocks -- a DIFFERENT shape from the gate above:
# two SIBLING pages, neither a `chat/` fragment, each independently
# duplicating a rule block, rather than a fragment using a class only
# one leaf page's own override defines. The gate above is scoped to
# `agents/chat/templates/chat/` and cannot see it (`_settings.html`,
# their common ancestor, sits outside that directory entirely).
#
# EXTENDING THE GATE ABOVE TO THIS SHAPE TOO would mean generalising it
# from "one base, its fragments, its leaf pages" to an arbitrary
# ancestor/descendant walk over every template in the repo -- a second,
# much larger gate for one already-fixed pair. The check below is the
# NARROWER alternative the fix itself already implies: `_settings.html`
# now OWNS these five selectors in its own `extra_style` block (this
# same commit), so the check that would have caught residual 3, and
# catches any future page repeating it, is simply that no page
# extending `_settings.html` RE-TYPES A RULE `_settings.html` itself
# already defines.
#
# MATCHED ON THE WHOLE RULE (selector AND body), NOT ON THE BARE CLASS
# NAME: an early draft of this check matched on class name alone and
# flagged `identity/entitlement.html`'s own `.warn { ...; font-size:
# .88rem; }` against `_settings.html`'s `.warn { ...; font-size:
# .85rem; }` -- the SAME name, a DIFFERENT rule, on a page this
# residual never touched. Real duplication is the same text typed
# twice, not merely the same class name reused for an unrelated rule on
# an unrelated page (harmless: each page renders its own independent
# `<style>` block, so two pages can never collide with each other at
# runtime -- this check is about maintenance, not runtime conflict).
# F2 (surface audit) / Coherence Wave B: this used to match only a
# DIRECT `{% extends "_settings.html" %}`, which is exactly why it never
# saw `inference/console.html` (extends `inference/base.html`, which
# extends `_settings.html` -- two hops) or `inference/model_sets.html`
# (the same chain) at all: 2 of 11 registered settings pages were
# outside this guard entirely. Before F5 (Coherence Wave A) fixed it,
# `console.html` carried its OWN `.messages { list-style: none; margin:
# 0 0 1rem; padding: 0; }` rule -- byte-for-byte identical to
# `_settings.html`'s own (the `.messages` rule inside that file's
# `extra_style` block -- named, not line-numbered: this citation carried
# a line number that Wave D's own additions to the same file silently
# moved, final whole-delta review M6) --
# which is precisely the shape this test exists to catch, and the direct
# -extends-only walk could not reach the page carrying it. `_template_
# name_map`/`_transitively_extends_settings_html` below (added for the
# SAME blind spot by the `block.super` check, review M1/Coherence Wave
# A) already do the general walk this needs; this function now shares
# that walk rather than repeating a narrower, direct-only version of it.
_SETTINGS_HTML = REPO_ROOT / "foundation" / "templates" / "_settings.html"
_RULE_RE = re.compile(r"([^{}]+?)\{([^{}]*)\}", re.DOTALL)


def _templates_extending_settings_html():
    """Every tracked template that (transitively) extends
    `_settings.html` -- directly, or through any number of intermediate
    base templates -- paired with its own source text. Shares `_template_
    name_map`/`_transitively_extends_settings_html` (defined below) with
    `test_every_template_reaching_settings_html_writes_block_super_first`,
    so a template reachable by one walk is reachable by both."""
    name_map = _template_name_map()
    for name, path in sorted(name_map.items()):
        if path == _SETTINGS_HTML:
            continue
        if _transitively_extends_settings_html(path, name_map):
            yield path, path.read_text()


def _rules(style_text: str) -> set[tuple[str, str]]:
    """(selector, declaration-body) pairs, whitespace-normalised, from a
    FLAT style block -- the shape every style block in this tree is
    written in (no nested rules), so a non-nested-aware split is
    enough."""
    rules = set()
    for selector, body in _RULE_RE.findall(style_text):
        selector_norm = " ".join(selector.split())
        body_norm = " ".join(body.split())
        if selector_norm:
            rules.add((selector_norm, body_norm))
    return rules


_RULE_RE = re.compile(r"([^{}]+?)\{([^{}]*)\}", re.DOTALL)


def test_no_template_retypes_a_rule_one_of_its_ancestors_already_owns():
    """Consolidation confirm residual 3's own check, GENERALISED from
    "no page extending `_settings.html` retypes a rule `_settings.html`
    owns" to "no template retypes a rule ANY of its ancestors already
    own" -- the shape needed the moment the tree has real multi-tier
    chains (`inference/console.html` reaches `_settings.html` through
    `inference/base.html`, two hops, which a direct-`{% extends
    %}`-only check can never see). MATCHED ON THE WHOLE RULE (selector
    AND body), NOT ON THE BARE CLASS NAME, for the exact reason this
    module's own docstring quotes: an early draft matched on class name
    alone and flagged `identity/entitlement.html`'s own `.warn { ...;
    font-size: .88rem; }` against `_settings.html`'s `.warn { ...;
    font-size: .85rem; }` -- the SAME name, a DIFFERENT rule. Real
    duplication is the same text typed twice, not merely the same class
    name reused for an unrelated rule on an unrelated page (harmless:
    each page renders its own independent `<style>` block, so two pages
    can never collide with each other at runtime -- this check is about
    maintenance, not a runtime conflict).

    Reads each template's OWN style block only (`_style_block`, not the
    shell's outside-block region `_shell_style_outside_block` reads) --
    `_shell.html`'s own shared primitives are already covered, repo-wide,
    by `test_a_shared_primitive_is_not_retyped_below_the_shell` and its
    neighbours below; this check is about the OTHER tiers of the chain,
    where a template's ancestor is not the shell itself."""
    graph = _extends_graph()
    files = _template_files()
    own_rules = {
        name: _rules(_style_block(path.read_text())) for name, path in files.items()
    }
    assert any(own_rules.values()), (
        "not one template's own style block parsed a single rule -- this check itself is broken")

    violations: list[tuple[str, str, list[str]]] = []
    for name, rules in own_rules.items():
        if not rules:
            continue
        for ancestor in _ancestors(name, graph):
            ancestor_rules = own_rules.get(ancestor, set())
            hit = sorted(selector for selector, body in (rules & ancestor_rules))
            if hit:
                violations.append((name, ancestor, hit))

    assert not violations, (
        "a template retypes a whole rule (selector AND body) one of its ancestors already "
        f"owns in its own style block -- inherit it via {{{{ block.super }}}} instead: {violations}"
    )


# The selector(s), PER TEMPLATE, this finding is actually about -- NOT
# the full set `_settings.html`'s own `extra_style` block defines (that
# block also carries CONSOLIDATION CONFIRM RESIDUAL 3's `.warn`/`.lede`/
# `.label-row`/etc., see the whole-rule check just above), and NOT the
# same set for both pages. Each page keeps a genuinely different local
# rule under a name `_settings.html` also uses, and a blanket selector
# set would flag those forever:
#   - `console.html` keeps its own `.msg` (`--ok-bg`/`--ok-text`, not
#     `_settings.html`'s `var(--panel)`/`var(--border)` recipe), so only
#     `.messages` -- the actual restatement -- is checked there.
#   - `model_sets.html` keeps its own `.warn` (`.88rem`, not
#     `_settings.html`'s `.85rem` -- the exact same-name-different-rule
#     shape documented above for `identity/entitlement.html`); its own
#     `.msg.error` is gone (Coherence Wave D, C3 -- see `_settings.html`'s
#     own comment), so only `.msg` is checked there.
# A NEW registry-style page needs its OWN entry here, not a shared one:
# see the two bullets above for why a blanket set across pages is wrong.
_REGISTRY_PAGE_RESTATED_SELECTORS = {
    "models/registry/templates/inference/console.html": {".messages"},
    "models/registry/templates/inference/model_sets.html": {".msg"},
}


@pytest.mark.parametrize("template", sorted(_REGISTRY_PAGE_RESTATED_SELECTORS))
def test_the_registry_pages_inherit_the_settings_flash_rules(template):
    """C-04. Both pages extend `inference/base.html`, which extends
    `_settings.html` -- and both opened `extra_style` WITHOUT `{{
    block.super }}`, discarding `_settings.html`'s own `extra_style`
    content and then restating part of the flash recipe locally.
    `.messages`/`.msg` themselves have since moved on again (C-21/C-52):
    they live in `foundation/templates/_shell.html`'s own `<style>` tag
    now, outside any block, so every page reaches them regardless of
    `block.super` -- but `_settings.html:70-76` still records the OTHER
    convention this pin polices ("Every one of the ten now writes `{{
    block.super }}` first" for `_settings.html`'s own still-block-scoped
    rules, like `.warn`/`.lede`/`.label-row`); these two pages were not
    among the ten.

    A SOURCE-TEXT PIN, not `test_no_template_retypes_a_rule_one_of_its_
    ancestors_already_owns` above: that check compares WHOLE rules
    (selector and body), and `model_sets.html:60-61`'s own `.msg` body
    is not byte-equal to `_settings.html`'s (see this pin's own note
    just below) -- so a whole-rule check sees no overlap at all and
    would pass while the restatement is still there. This pin is
    SELECTOR-level on purpose, and is what makes the underlying fix
    red-green regardless of how close the retyped body happens to be.

    See `_REGISTRY_PAGE_RESTATED_SELECTORS` just above for which
    selector is checked on which page, and why each page's kept local
    variant is deliberately excluded rather than flagged."""
    text = (REPO_ROOT / template).read_text()
    style = _style_block(text)
    assert "{% block extra_style %}{{ block.super }}" in text, (
        f"{template} overrides extra_style without inheriting it")
    settings_selectors = _REGISTRY_PAGE_RESTATED_SELECTORS[template]
    # SELECTOR-LEVEL, NOT RULE-LEVEL, and that is the whole point of
    # writing a new pin rather than reusing the whole-rule ancestor
    # check above. `_rules` compares whitespace-normalised (selector,
    # BODY) pairs, and `model_sets.html:60-61`'s `.msg` body -- the rule
    # Task 31 removed -- was not byte-equal to `_settings.html:93-101`'s:
    # it spelled `.6rem` where the parent spelled `0.6rem`, ordered
    # `border` before `border-radius`, and omitted `color: var(--text)`
    # entirely. A rule-level check therefore saw no overlap at all on
    # that page and went green while the restatement was still there --
    # exactly the failure this pin exists to catch, reproduced here on
    # purpose. What was wrong there was not that the bodies matched; it
    # was that the page redeclared a selector its parent already owned,
    # having inherited it.
    redeclared = sorted(
        selector for selector, _ in _rules(style) if selector in settings_selectors)
    assert not redeclared, (
        f"{template} redeclares a selector _settings.html already owns: {redeclared}")


_SHARED_TOKENS = ("--error-bg", "--error-text", "--danger-text", "--mono")


@pytest.mark.parametrize("token", _SHARED_TOKENS)
def test_a_shared_token_is_declared_only_in_the_shell(token):
    """C-20. Four tokens were retyped per page -- `--error-bg`/
    `--error-text` in five templates with identical light AND dark values,
    `--mono` in three, `--danger-text` in two -- and `console.html`
    hardcoded the error hexes rather than tokenising them at all. This is
    exactly what `_shell.html` already did once for `--danger`; a token
    declared per page is a token whose dark override a page can forget."""
    declarations = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        # A REPO-RELATIVE check, not a substring of the absolute path: this
        # implementation worktree lives under `.../<repo>/.claude/worktrees/
        # <branch>`, so `REPO_ROOT` itself carries `.claude` as one of ITS
        # OWN ancestors. A plain `"/.claude/" in str(path)` therefore matches
        # every file under this REPO_ROOT and empties the scan outright --
        # the exclusion is meant for a `.claude/` directory INSIDE the repo
        # (none exists today), not for where the checkout itself happens to
        # sit on disk.
        if ".claude" in path.relative_to(REPO_ROOT).parts:
            continue
        for line in path.read_text().splitlines():
            if line.strip().startswith(token + ":"):
                declarations.append(str(path.relative_to(REPO_ROOT)))
    assert set(declarations) == {"foundation/templates/_shell.html"}, (
        f"{token} is declared outside the shell: {sorted(set(declarations))}")


@pytest.mark.parametrize("hex_literal", ["#fdeaea", "#922020"])
def test_the_error_hexes_appear_only_where_the_token_is_declared(hex_literal):
    """The other half of C-20: `console.html` never tokenised these, so
    its error rows had no dark-scheme override at all."""
    sites = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        # A REPO-RELATIVE check, not a substring of the absolute path: this
        # implementation worktree lives under `.../<repo>/.claude/worktrees/
        # <branch>`, so `REPO_ROOT` itself carries `.claude` as one of ITS
        # OWN ancestors. A plain `"/.claude/" in str(path)` therefore matches
        # every file under this REPO_ROOT and empties the scan outright --
        # the exclusion is meant for a `.claude/` directory INSIDE the repo
        # (none exists today), not for where the checkout itself happens to
        # sit on disk.
        if ".claude" in path.relative_to(REPO_ROOT).parts:
            continue
        if hex_literal in path.read_text():
            sites.append(str(path.relative_to(REPO_ROOT)))
    assert sites == ["foundation/templates/_shell.html"], (
        f"{hex_literal} appears outside the shell where it is declared: {sites}")


_STYLE_TAG_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_EXTRA_STYLE_OPEN_RE = re.compile(r"{%\s*block\s+extra_style\s*%}")


def _shell_style_outside_block(text: str) -> str:
    """`_shell.html`'s own `<style>` tag content that sits BEFORE `{%
    templatetag openblock %} block extra_style {% templatetag
    closeblock %}` opens -- `{% templatetag comment %}...{% templatetag
    endcomment %}` regions and `{{ ... }}` variable tags stripped first,
    same convention as `_style_block`.

    THIS IS WHERE THE FLASH RECIPE ACTUALLY LIVES (C-21/C-52, review
    round 2): placed here rather than inside `extra_style` itself
    DELIBERATELY, because most of `_shell.html`'s descendants override
    that block WITHOUT `{{ block.super }}` (`landing/index.html`,
    `setup/index.html`, `identity/login.html` and its two password-change
    siblings, `rag/ask.html`, `rag/transcript.html`, `rag/history.html`)
    -- a rule placed inside the block would render unstyled the day any
    one of those pages grows a `{% templatetag openblock %} if messages
    {% templatetag closeblock %}`. `_style_block` reads only INSIDE that
    block, so it cannot see this region at all; this is the matching
    reader for the region outside it."""
    stripped = _VARIABLE_RE.sub("", _COMMENT_RE.sub("", text))
    style_match = _STYLE_TAG_RE.search(stripped)
    if not style_match:
        return ""
    style_text = style_match.group(1)
    block_match = _EXTRA_STYLE_OPEN_RE.search(style_text)
    return style_text[:block_match.start()] if block_match else style_text


@pytest.mark.parametrize("selector", [".messages", ".msg"])
def test_the_shared_flash_recipe_lives_in_the_shell(selector):
    """C-21/C-52. `.messages`'s list-reset was restated at six sites and
    `.msg`'s recipe at several more. `_settings.html` held the canonical
    copy, but `chat/base.html`, `jobs/base.html`, `rag/base.html` and
    `vision/base.html` extend BARE `_shell.html` and cannot reach it --
    which is exactly why each grew its own. The deepest common ancestor
    is the shell.

    Reads `_shell.html` with `_shell_style_outside_block` (see its own
    docstring), NOT `_style_block`: the recipe lives in the shell's own
    `<style>` tag, before `{% templatetag openblock %} block extra_style
    {% templatetag closeblock %}` opens, so it renders unconditionally
    for every descendant regardless of `{{ block.super }}` -- most of
    which do not call it. Other templates are still read with
    `_style_block`, since a retyped copy would sit inside THEIR OWN
    style block override, exactly where that reader looks.

    Compares whole rules, selector AND body, the way `test_no_template_
    retypes_a_rule_one_of_its_ancestors_already_owns` does: two pages
    using one class name for unrelated rules is not duplication, and a
    page's genuine variant (`console.html`'s `--ok-bg` `.msg`, `search.
    html`'s own padding) is not a copy."""
    shell_rules = _rules(_shell_style_outside_block(
        (REPO_ROOT / "foundation/templates/_shell.html").read_text()))
    shared = {body for sel, body in shell_rules if sel == selector}
    assert shared, f"{selector} is not owned by the shell"
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        # See `test_a_shared_token_is_declared_only_in_the_shell`'s own
        # comment just above: a bare `"/.claude/" in str(path)` matches
        # every file in THIS worktree (it lives under `.claude/worktrees/
        # <branch>`), so the exclusion has to be relative-path-based.
        if path.name == "_shell.html" or ".claude" in path.relative_to(REPO_ROOT).parts:
            continue
        for sel, body in _rules(_style_block(path.read_text())):
            if sel == selector and body in shared:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"{selector}'s shared rule is retyped in: {offenders}"


@pytest.mark.parametrize("selector", [
    ".banner", ".banner a", ".muted", ".empty",
    ".delete-disclosure > summary", ".delete-disclosure > summary::-webkit-details-marker",
])
def test_a_shared_primitive_is_not_retyped_below_the_shell(selector):
    """C-22/C-23/C-24. Four primitives written out at three, eight, two
    and two sites. Whole-rule comparison, so a page's genuine variant
    (`documents.html` has no `.banner.warn`; `search.html`'s `.empty` has
    its own margin and an explicit `padding: 0` -- see review round 2 and
    `test_a_kept_local_variant_does_not_half_override_a_shared_property_
    set`, just below, for why that `padding: 0` has to be there at all;
    `setup/index.html`'s `.muted` is a different size) is not counted as
    a copy -- the same distinction `test_no_template_retypes_a_rule_one_
    of_its_ancestors_already_owns` already draws for the other tiers of
    the chain.

    `_KNOWN_DUPLICATE_EXEMPTIONS` carries the one #84-gated exemption -- see
    C-24 above and that set's own comment.

    Uses `_shell_style_outside_block`/`_rules`, the same reader
    `test_the_shared_flash_recipe_lives_in_the_shell` uses just above,
    for the same reason: these rules render unconditionally from the
    shell's own `<style>` tag, before `{% templatetag openblock %} block
    extra_style {% templatetag closeblock %}` opens, so every descendant
    reaches them regardless of `{{ block.super }}`."""
    shell_rules = _rules(_shell_style_outside_block(
        (REPO_ROOT / "foundation/templates/_shell.html").read_text()))
    shared = {body for sel, body in shell_rules if sel == selector}
    assert shared, f"{selector} is not owned by the shell"
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        # See `test_a_shared_token_is_declared_only_in_the_shell`'s own
        # comment: a bare `"/.claude/" in str(path)` matches every file
        # in THIS worktree, so the exclusion has to be relative-path-based.
        if path.name == "_shell.html" or ".claude" in path.relative_to(REPO_ROOT).parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for sel, body in _rules(_style_block(path.read_text())):
            if sel == selector and body in shared and (rel, selector) not in _KNOWN_DUPLICATE_EXEMPTIONS:
                offenders.append(rel)
    assert not offenders, f"{selector}'s shared rule is retyped in: {offenders}"


def _properties(body: str) -> set[str]:
    """Bare property NAMES (not values) a flat declaration body sets --
    `"color: var(--muted); font-size: .85rem;"` -> `{"color", "font-size"}`.
    Used only to compare WHICH properties two same-selector rules touch,
    never their values -- see `test_a_kept_local_variant_does_not_half_
    override_a_shared_property_set`, just below, for why property
    presence (not value) is what matters here."""
    props = set()
    for decl in body.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        name = decl.split(":", 1)[0].strip()
        if name:
            props.add(name)
    return props


@pytest.mark.parametrize("selector", [
    ".banner", ".banner a", ".muted", ".empty",
    ".delete-disclosure > summary", ".delete-disclosure > summary::-webkit-details-marker",
])
def test_a_kept_local_variant_does_not_half_override_a_shared_property_set(selector):
    """THE GENERAL FORM of a bug review round 2 found: two rules for the
    SAME selector at the SAME specificity do not replace each other --
    the cascade UNIONS them per property, later source order winning only
    for properties BOTH declare. `rag/search.html`'s own `.empty`
    (C-22/C-23/C-24) declared `color`/`font-size` (matching the shell's
    values) and its own `margin-top`, but never `padding` -- so once
    `.empty` moved to `_shell.html`, this page's empty-results box
    silently started rendering the shell's `padding: 1rem 0.25rem
    1.25rem`, which it had never had. Fixed by giving that rule an
    explicit `padding: 0` (see its own comment); this pin is what keeps
    the next promotion from reopening the same hole.

    THE DISTINCTION THIS PIN DRAWS, so it does not also flag the
    DELIBERATE partial overrides this same task wrote
    (`chat/agent_entitlements.html`/`tool_entitlements.html`'s `.muted {
    margin: 0 0 .3rem; }`, `identity/login.html`'s `.muted { margin-top:
    1.25rem; }`): a local rule that shares NONE of the shell rule's
    property NAMES is unambiguously additive -- by construction (see
    C-23's own "keeps" table in the task brief), every one of those
    pages' OMITTED properties already equalled the shell's value, which
    is WHY those pages qualified for promotion in the first place, so
    inheriting them via the cascade changes nothing. A local rule that
    redeclares SOME but not ALL of the shell's properties is the
    dangerous shape: it reads as a considered, complete override (this is
    exactly what `search.html`'s own rule looked like) while silently
    leaving one property to the cascade. `setup/index.html`'s `.muted`
    (redeclares BOTH `color` and `font-size` -- a full override that
    deliberately differs, `0.9rem` not `0.85rem`) and the exact duplicate
    at `agents/chat/templates/chat/base.html`'s `#84`-gated
    `.delete-disclosure > summary` (which redeclares every property the
    shell's copy does) both fall on the SAFE side of the same rule:
    declare ALL of the shell's properties, or NONE of them -- never some.
    No need to consult `_KNOWN_DUPLICATE_EXEMPTIONS` here: an exact
    duplicate, by definition, redeclares every property, so it is
    already on the safe side of this check without being named."""
    shell_rules = _rules(_shell_style_outside_block(
        (REPO_ROOT / "foundation/templates/_shell.html").read_text()))
    shell_props: set[str] = set()
    for sel, body in shell_rules:
        if sel == selector:
            shell_props |= _properties(body)
    assert shell_props, f"{selector} is not owned by the shell"

    violations = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        # See `test_a_shared_token_is_declared_only_in_the_shell`'s own
        # comment: a bare `"/.claude/" in str(path)` matches every file
        # in THIS worktree, so the exclusion has to be relative-path-based.
        if path.name == "_shell.html" or ".claude" in path.relative_to(REPO_ROOT).parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        for sel, body in _rules(_style_block(path.read_text())):
            if sel != selector:
                continue
            local_props = _properties(body)
            overlap = local_props & shell_props
            missing = sorted(shell_props - local_props)
            if overlap and missing:
                violations.append((rel, missing))

    assert not violations, (
        f"{selector}: a page-local rule redeclares SOME of the shell's properties but "
        "not all, so the cascade silently supplies the shell's value for whatever it "
        f"left out -- declare all of the shell's properties, or none of them: {violations}"
    )


def test_no_template_writes_its_own_flash_loop():
    """C-19. The same flash markup appeared in fifteen templates and had
    already drifted three ways: five omitted the `{% templatetag
    openblock %} if message.tags {% templatetag closeblock %}` guard, so
    a tagless message rendered `class="msg "`, and one rendered loose
    `<div>`s with no list wrapper and no empty-messages guard at all. One
    partial, `foundation/templates/_messages.html`, included everywhere.

    `_COMMENT_RE` stripped first, same convention as every other pin in
    this module: `_settings.html` and `rag/search.html` each carry this
    exact phrase inside a `{% templatetag comment %}` block as prose,
    describing the very consolidation this pin polices, and a naive scan
    would flag both as if they still rendered their own loop.

    Excluded by `path.relative_to(REPO_ROOT).parts`, NOT a bare `"/.claude/"
    in str(path)` substring check: this test's own `REPO_ROOT` sits under
    `.claude/worktrees/<branch>` on disk (see this module's own
    `test_a_shared_token_is_declared_only_in_the_shell` for the same
    caveat), so a substring check against the absolute path matches every
    file this scan would ever see and passes vacuously, having excluded
    everything before checking anything.

    `.venv` excluded the same way, for a different reason: this is the
    first pin in this module whose target phrase happens to also appear
    in a vendored file -- Django's own `admin/base.html` renders its
    admin-site flash with `{% templatetag openblock %} for message in
    messages {% templatetag closeblock %}` too, and that template is
    neither one of the fifteen sites this task repoints nor a file this
    repo owns."""
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        if path.name == "_messages.html":
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if ".claude" in parts or ".venv" in parts:
            continue
        text = _COMMENT_RE.sub("", path.read_text())
        if "for message in messages" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"templates writing their own flash loop: {offenders}"
def test_the_widened_walk_reaches_the_two_indirect_pages_f2_named():
    """Anti-vacuous pin (F2, Coherence Wave B): a regression that quietly
    narrowed `_templates_extending_settings_html()` back to direct-only
    would still pass the test above -- both pages inherit `_settings.
    html` cleanly today (F5), so an empty violations list is also what a
    BROKEN, narrower walk that never saw them at all would produce.
    Asserted here against the walk's OWN output, independently of
    whether either page currently has a violation to report."""
    reached = {str(path.relative_to(REPO_ROOT)) for path, _text in _templates_extending_settings_html()}
    assert "models/registry/templates/inference/console.html" in reached
    assert "models/registry/templates/inference/model_sets.html" in reached


# --- Transitive block.super coverage (review M1, Coherence Wave A) -------
#
# F5 found `console.html` and `setup/index.html` dropping `_settings.html`'s
# shared `extra_style` rules by overriding the block with no
# `{{ block.super }}`; the fix-round review found a THIRD offender,
# `inference/model_sets.html`, that `_templates_extending_settings_html()`
# above -- DIRECT-EXTENDS ONLY at the time -- could not see, because
# `model_sets.html` extends `inference/base.html` instead: the exact
# blind spot F2 already named for `console.html` itself, recurring one
# level further down an extends chain. This walks the FULL extends
# graph, not just the first hop, so a fourth page behind a second layer
# of indirection cannot repeat it. Coherence Wave B (F2 re-review) later
# widened `_templates_extending_settings_html()` above to share this SAME
# walk, so both checks now see the identical page set -- see that
# function's own comment for the concrete duplicate this closes.

_EXTENDS_ANY_RE = re.compile(r'{%\s*extends\s+["\']([^"\']+)["\']\s*%}')
_EXTRA_STYLE_BLOCK_RE = re.compile(
    r'{%\s*block\s+extra_style\s*%}(.*?){%\s*endblock\s*%}', re.DOTALL
)
_COMMENT_RE = re.compile(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', re.DOTALL)


def _template_name_map() -> dict:
    """Every tracked `*.html`, keyed by its DJANGO TEMPLATE NAME -- the
    path relative to the nearest ancestor directory literally called
    `templates`, which is what a `{% extends "..." %}` string names and
    what Django's own loader resolves against. Two templates sharing a
    name is a Django misconfiguration this test does not need to guard
    against separately: the loader would already be broken."""
    mapping = {}
    for path in sorted(REPO_ROOT.rglob("*.html")):
        parts = path.relative_to(REPO_ROOT).parts
        if "templates" not in parts:
            continue
        idx = len(parts) - 1 - parts[::-1].index("templates")
        mapping["/".join(parts[idx + 1:])] = path
    return mapping


def _extends_target(path):
    match = _EXTENDS_ANY_RE.search(path.read_text())
    return match.group(1) if match else None


def _transitively_extends_settings_html(path, name_map, seen=None) -> bool:
    """Follows `{% extends %}` up the chain, through as many intermediate
    base templates as it takes, rather than matching only a direct
    extends -- the fix for the blind spot named above."""
    seen = seen if seen is not None else set()
    if path in seen:
        return False  # a cycle, which would be a separate bug this test is not about
    seen.add(path)
    target = _extends_target(path)
    if target is None:
        return False
    if target == "_settings.html":
        return True
    target_path = name_map.get(target)
    if target_path is None:
        return False
    return _transitively_extends_settings_html(target_path, name_map, seen)


def test_every_template_reaching_settings_html_writes_block_super_first():
    """Every template that (transitively) extends `_settings.html` and
    overrides `{% block extra_style %}` must open that override with
    `{{ block.super }}` -- the rule `docs/EXTENDING.md`'s "Two more that
    are easy to get wrong and cheap to state" section states in prose,
    now asserted rather than only documented. A template with no
    `extra_style` override of its own is not a violation: it inherits
    the block whole, which is the point.

    NOT LIMITED TO A DIRECT EXTENDS -- and neither is the CSS-ownership
    check any more (final whole-delta review I6). BOTH checks now run
    THIS SAME transitive walk: F2/Coherence Wave B widened
    `_templates_extending_settings_html()` above onto
    `_template_name_map`/`_transitively_extends_settings_html` (defined
    just above this function), so a template reachable by one walk is
    reachable by both -- see that function's own docstring. Each check
    still does its own work on the templates the shared walk yields (a
    byte-for-byte rule comparison there, a `{{ block.super }}` prefix
    assertion here); what they no longer do is disagree about which
    templates those are. `inference/base.html` -- a pure intermediate
    template with no `extra_style` override of its own -- is why the walk
    has to be transitive at all: `console.html` and `model_sets.html`
    both reach `_settings.html` THROUGH it, and a direct-extends match
    sees neither.
    """
    name_map = _template_name_map()
    violations = []
    for name, path in sorted(name_map.items()):
        if path.name == "_settings.html":
            continue
        if not _transitively_extends_settings_html(path, name_map):
            continue
        text = path.read_text()
        match = _EXTRA_STYLE_BLOCK_RE.search(text)
        if match is None:
            continue  # no override at all -- inherits the whole block, fine
        body = _COMMENT_RE.sub("", match.group(1)).strip()
        if not body.startswith("{{ block.super }}"):
            violations.append(str(path.relative_to(REPO_ROOT)))

    assert not violations, (
        "template(s) transitively extending _settings.html override extra_style "
        f"without writing {{{{ block.super }}}} first, silently dropping its shared "
        f"rules: {violations}"
    )


def test_the_transitive_walk_is_not_vacuous():
    """Anti-vacuous pin: a broken `{% extends %}` regex or name-mapping
    would make the test above pass by walking nothing. `inference/
    console.html` is known, by hand, to reach `_settings.html` through
    `inference/base.html` -- two hops, not one -- so this is also the
    one assertion in this file that the transitive walk (not just a
    direct-extends walk) is doing real work."""
    name_map = _template_name_map()
    console = name_map["inference/console.html"]
    assert _extends_target(console) == "inference/base.html"
    assert _transitively_extends_settings_html(console, name_map)


# --- The shared composer, included from OUTSIDE `chat/` ------------------
#
# THE SETTINGS ASSISTANT PANEL (spec §6.5, decision 11) includes
# `chat/_composer.html` from `foundation/templates/_settings.html`. That
# makes `_shell.html` -- the deepest template that is an ancestor of BOTH
# `chat/base.html` and `_settings.html` -- the home of every selector that
# fragment uses, by this module's own placement rule.
#
# NO EXISTING GATE IN THIS FILE CAN SEE THAT SHAPE. The fragment/leaf-page
# gate above is scoped to `agents/chat/templates/chat/` (`_CHAT_TEMPLATES`)
# and its page set is the pages extending `chat/base.html`; the settings-side
# check below compares a leaf page's own block against `_settings.html`'s and
# never looks at `chat/base.html` at all. So the check the settings assistant
# needs is this one, written by that phase, in the narrower shape this file
# already prefers over generalising a gate to a shape it was never built for.
# FIVE, NOT SPEC §6.5's SIX (orchestrator ruling, 2026-09-10).
# `.agent-picker` is NOT here: `chat/_composer.html:105-114` emits
# `label.agent-picker` only under `{% if composer_mode == "start" %}` and
# the panel passes `"turn"`, so that class still has exactly ONE consumer
# -- `chat/base.html`'s own surfaces -- and the single-consumer rule
# keeps it there. `test_the_chat_only_composer_rules_stayed_behind` below
# is what pins that it stays.
_COMPOSER_SELECTORS = (
    ".composer-card",
    ".composer-textarea",
    ".composer-toolbar",
    ".composer-toolbar-left",
    ".composer-toolbar-right",
)

_SHELL_HTML = REPO_ROOT / "foundation" / "templates" / "_shell.html"
_CHAT_BASE_HTML = _CHAT_TEMPLATES / "base.html"

_INLINE_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def _inline_style(text: str) -> str:
    """`_shell.html`'s CSS, which `_style_block` above cannot read.

    THAT HELPER EXTRACTS AN `extra_style`/`chat_style` BLOCK TAG's body
    (`_BLOCK_RE`, `:175-176`), which is exactly right for `_settings.html`
    and for `chat/base.html` -- both of them fill a block their own base
    declares. `_shell.html` IS that base: its CSS is a plain `<style>`
    element opening at `:88`, and the only `{% block extra_style %}` in
    the file is the EMPTY hook at `:563`, sitting inside that element. So
    `_style_block(_shell.html)` matches that empty hook and answers `""`
    -- correct for what that helper is for, and useless for what this
    gate needs.

    `findall` + join rather than `search`: taking only the first element
    would silently stop reading at the first `</style>` if this file ever
    grows a second one.
    """
    stripped = _VARIABLE_RE.sub("", _COMMENT_RE.sub("", text))
    return "\n".join(_INLINE_STYLE_RE.findall(stripped))


def _defines(style_text: str, selector: str) -> bool:
    """Does `style_text` define a rule whose selector list contains
    exactly `selector`?

    MATCHED ON THE SELECTOR LIST, NOT ON A SUBSTRING, and that is
    load-bearing here: `.composer-card-drop-target` and
    `.composer-card:focus-within` both CONTAIN `.composer-card`, and a
    naive `in` test would call either one a definition of it. The first
    of those is `chat/_attach_dragdrop.html`'s own class and STAYS in
    `chat/base.html`; the second is `.composer-card`'s own state rule and
    MOVES with it.
    """
    for raw_selector, _body in _rules(style_text):
        parts = [part.strip() for part in raw_selector.split(",")]
        if selector in parts:
            return True
    return False


def test_the_shared_composers_selectors_live_in_the_shell_not_in_chat_base():
    """SPEC §6.5, MINUS `.agent-picker` (orchestrator ruling,
    2026-09-10 -- see `_COMPOSER_SELECTORS` above). The five selectors
    `chat/_composer.html` uses ON THIS SURFACE, plus the structural
    `.composer-card > form` rule its own comment depends on, live in
    `_shell.html` and in NO `chat/` block -- because that fragment is
    included by a `foundation/` template now, and rules move to the
    deepest common ancestor rather than being copied.

    BYTE-IDENTICAL RULES TO A HIGHER HOME: nothing about the CSS changes,
    only where it is declared, so no pixel moves.
    """
    shell = _inline_style(_SHELL_HTML.read_text())
    assert shell, "_shell.html's own <style> parsed empty -- this check itself is broken"
    chat_base = _style_block(_CHAT_BASE_HTML.read_text())
    assert chat_base, "chat/base.html's own block parsed empty -- this check itself is broken"

    missing = [s for s in _COMPOSER_SELECTORS if not _defines(shell, s)]
    assert not missing, (
        f"the shared composer's selectors must be defined in _shell.html: {missing}")

    strays = [s for s in _COMPOSER_SELECTORS if _defines(chat_base, s)]
    assert not strays, (
        "these selectors moved to _shell.html and must not be re-declared in chat/base.html "
        f"-- a second copy is exactly the drift this gate exists for: {strays}")


def test_the_structural_form_rule_moved_with_them():
    """`.composer-card > form { display: contents; }` is not decoration:
    `chat/_composer.html:68-74` explicitly depends on it -- it is why the
    form element generates no box and its children become direct flex
    items of `.composer-card`. Left behind, the panel's composer lays out
    wrongly on every settings page."""
    shell = _inline_style(_SHELL_HTML.read_text())
    chat_base = _style_block(_CHAT_BASE_HTML.read_text())
    assert _defines(shell, ".composer-card > form")
    assert not _defines(chat_base, ".composer-card > form")


def test_the_chat_only_composer_rules_stayed_behind():
    """THE NEGATIVE HALF, without which the two tests above would pass on
    a commit that moved the WHOLE composer region up. `.composer-toolbar
    .picker` is the MODEL picker, which the settings panel never renders
    (`composer_show_model_picker=False`), and `.composer-card-drop-target`
    is `chat/_attach_dragdrop.html`'s own class, which the panel never
    includes (`may_attach_files` is absent from its context). Both are
    chat-only, and promoting them would put rules in the global shell that
    only one column can ever use."""
    chat_base = _style_block(_CHAT_BASE_HTML.read_text())
    shell = _inline_style(_SHELL_HTML.read_text())
    for selector in (".composer-toolbar .picker", ".composer-card-drop-target"):
        assert _defines(chat_base, selector), selector
        assert not _defines(shell, selector), selector
