# ADR 0018 — The settings assistant: guide-only by structure, the card table as its own repository

**Status:** Accepted
**Date:** 2026-09-10

Written against `settings-assistant` at **`cee31f0`**, with the phase's twelve implementation
tasks landed and reviewed. Its argument lives in
[`docs/superpowers/specs/2026-09-09-settings-assistant-design.md`](../superpowers/specs/2026-09-09-settings-assistant-design.md),
cited where a decision's reasoning is there rather than here. **Where the spec and the code
differ, the code is what this ADR records** — including the corrections this phase's own review
rounds forced onto the tree after the design spec was written — and every claim below is
checkable against the tree it describes.

**Amendment, 2026-09-14 (F1, Coherence Wave C).** The settings area gained a twelfth
registered leaf and a twelfth card: **Job execution** (`jobs-settings`,
`GET /queue/settings/`), which took the four `JobSettings` controls off the Queue page — an
activity surface, never a registered settings leaf
(`foundation/settings_area.py::SETTINGS_GROUPS`), and still not one. Two sentences below therefore state a count that was true at `cee31f0` and
is not now: *"all eleven settings leaves' own `extra_style` blocks"* (Decision, the
fixed-position audit) and *"it ships all eleven cards"* (the card-table decision). **Both
arguments are unchanged by the count** — the audit's finding is that no settings leaf creates
a containing block for the panel, which the twelfth leaf also does not; and the card table
still ships every card it has, at import, with no per-page fetch. The original text is left
as written rather than silently renumbered, this being a dated record of a decision and not a
description of today's tree.

## Context

Every settings page on this platform already has a sidebar entry, a gate and a route
(`foundation/settings_area.py::SETTINGS_GROUPS`, ADR 0016's route classes). What it did not
have was any answer to "how do I do X" that pointed at the actual control, on the actual page,
as this box is actually configured — an administrator either already knew where a setting
lived or went looking, page by page, for it.

The owner asked for a **guide**: a persistent panel on every settings page, answering
questions grounded in this box's own settings and linking straight to the control, that can
never itself change a setting. Two rulings constrained the whole design and are restated
verbatim, as the brief requires:

> **1. GUIDE-ONLY. The assistant explains, answers, and deep-links; it NEVER mutates
> settings. No mutating tools; the tool registry already refuses `mutates=True` grants — the
> spec pins it.**

> **2. SETTINGS-ONLY surface. A persistent panel across settings screens; the assistant is
> NOT in the main chat's agent picker, and its conversations do NOT appear in the main chat
> sidebar or `/chat/all/`.**

Four facts already in the tree constrained every part of the answer (design spec §1.4):

1. **The import law.** `agents/` may not import `tools/` at all, and `identity/` is reachable
   only through its four named seams (`identity.contracts`, `identity.access`,
   `identity.request`, `identity.audit`). No single module can read the identity singleton,
   `ChatSettings` and `RagSettings` together — the design does not try, and decision 12 below
   records what that costs.
2. **`render_answer` never produces a link.** A URL a model types is inert text
   (`agents/chat/rendering.py:598-609`). Any deep link the owner asked for has to be built by
   the platform, not typed by the model.
3. **`is_admin` is True for everybody on an open box** (`identity/access.py:109-138`). The
   panel is admin-gated exactly as the rest of the settings area already is, which means every
   viewer on a household box sees it.
4. **`Agent.tool_keys` cannot hold a mutating tool.** `grantable_tools()`
   (`agents/contracts/tools.py:393-401`) filters on `spec.mutates`, and `granted_tools`
   (`:404-450`) drops a mutating key silently even if one were named. Guide-only is therefore
   structural before it is a policy.

This ADR is a **record**, written after the phase's tasks merged and their reviews closed. It
carries the six decisions the brief names, six more that the build's own review rounds forced
onto the tree after the design spec was written, and a named-gaps section in ADR 0017's shape —
not a restatement of the spec's full eighteen sections.

## Decision

### 1. Guide-only as a structural property, not a promise

Neither `SETTINGS_CARD` nor `SETTINGS_OVERVIEW` (`agents/settings_tools.py`) declares
`mutates`, so both default to `False` (`agents/contracts/tools.py:125`). That is not an
omission a future edit could quietly reverse without consequence: `grantable_tools()` is the
set an agent row may name at all, and `granted_tools()` drops a `mutates=True` key from a
turn's actual tool list even if `Agent.tool_keys` somehow named one — "ADR 0010's rule that a
settings-mutating tool is registered but not grantable" (`agents/contracts/tools.py:397-399,
414-417`). "It cannot change a setting" is therefore a fact about the type system and the tool
registry, not a claim about this feature's intentions.

A test restates it anyway, twice: `test_neither_tool_mutates`
(`agents/tests/test_settings_tools.py:60`) asserts `get_tool("settings.card").mutates is
False` and the same for `settings.overview`; `test_every_tool_it_holds_is_registered_and_none_
of_them_mutates` (`:285`) walks the `settings-helper` `AgentSpec`'s own `tool_keys` and asserts
the same for every key the row actually carries, not just the two this phase added. A
structural guarantee nobody restates is a guarantee somebody removes — the belt is there
because the brace is invisible to a reviewer skimming a diff.

### 2. The card table plus its content hash as the answer to "keep the context relevant"

`foundation/settings_help.py` is a **pure** module — no Django import, no database, no import
of any non-pure module — carrying `CARDS: tuple[HelpCard, ...]` (`:88`), one entry per
`SETTINGS_GROUPS` route, each with a tuple of `HelpField`s naming a control's anchor, meaning
and effect (`:66-86`). There is **no stored repository**: the cards are the code, and code
cannot be stale relative to itself. Purity is what makes that true rather than aspirational —
the tool runners live in `agents/` and read this table directly, and a module that imported
`django.urls` or `identity.access` the way `foundation/settings_area.py` does would make that
read a cross-column import of a non-pure module.

`CONTENT_HASH` (`:579`) is `sha256` over a deterministic, explicitly ordered serialization of
`CARDS` — never Python's built-in `hash()`, which is salted per process and would answer
differently on every boot — truncated to twelve hex characters. It is a change detector, not a
signature, and it does three real jobs: it lets a transcript answer cite the context version it
was produced from (it rides both tools' `ToolResult.data["content_hash"]` and the
`settings.card` tool description); it is the key any future derived artifact — the retrieval
leg named gap 3 below — compares against before trusting itself; and it lets a test pin that
the hash *changes* whenever any card's text changes
(`TestTheContentHash.test_the_hash_changes_when_any_card_text_changes`,
`foundation/tests/test_settings_help.py:103-111`, mutating a **copy** of the table so the
running suite's own `CARDS` is never touched), which is what makes the first two jobs worth
anything.

Two drift assertions do the sustainability work the owner asked for twice over
(`TestTheDriftGuard`, `foundation/tests/test_settings_help.py:42-77`): every settings entry has
a card and every card names a route this box owns
(`test_every_settings_entry_has_a_card_and_every_card_has_an_entry`, `:43`, symmetric —
a page added with no card fails, and a card left behind by a removed page fails too), and every
anchor a card cites exists on the **rendered** page, never template source
(`TestTheAnchors.test_every_anchor_a_card_cites_exists_on_the_rendered_page`, `:195-237`,
following the `{% if cold_start %}` precedent at
`models/registry/templates/inference/console.html`, where a source grep would pass on a branch
that never renders). A page changed without its card, or a card citing a dead anchor, fails the
suite rather than shipping a wrong answer.

### 3. The tool schema as the injection channel

`settings.card`'s `page` parameter is declared `kind="choice"` with `choices=page_choices()`
(`agents/settings_tools.py`, `foundation/settings_help.py::page_choices`) — the ordered tuple
of every card's `route_name`, built from `CARDS` at import. That tuple becomes an `enum` in the
JSON schema both wire adapters build (`agents/contracts/toolschema.py`), and the tool's own
`description` is composed at import time from the same table, one `route — title` line per
card, with `Settings context version: {CONTENT_HASH}` appended. Both cannot go stale, because
both are recomputed from `CARDS` on every import rather than copied anywhere.

This needs **no change to `agents/runtime/prompt.py`**, where a slug-conditional paragraph
would be a special case in the one function every agent on the platform goes through
(`build_messages`, `agents/runtime/prompt.py:837`). `test_the_prompt_builder_names_no_settings_
identifier` (`agents/tests/test_settings_tools.py:251`) pins the behavioural half of that claim
— `build_messages` for a `settings-helper` conversation returns the same message list any other
agent with the same rows and system prompt returns — as a phase pin whose own docstring says to
delete it when a later phase (the retrieval leg, named gap 3) legitimately revisits the
decision. An enum also turns a page name the model invents into a `param_error` rather than a
confident answer about a page that does not exist
(`test_an_invented_page_is_a_param_error_not_a_confident_answer`,
`agents/tests/test_settings_tools.py:92`).

### 4. Links built by the platform, because `render_answer` makes no link

`agents/chat/rendering.py:598-609` states it outright: `render_answer` escapes first and never
produces an `<a>`, from a URL or otherwise — a deliberate posture, since an `<a href>` a model
can be talked into writing is a phishing primitive, and this feature does not weaken it. So
`settings.card` returns, in `ToolResult.data["links"]`, `{"route": …, "anchor": …}` pairs —
never URLs — every value drawn from `CARDS`.

**THE PANEL'S CONTEXT BUILDER HOLDS THREE LINK-EMITTING LEGS, ADDED ACROSS THREE
SEPARATE ROUNDS — this section originally named one and called it "the one place"; that
sentence is now false and is corrected here rather than left standing.** The first, and the one
this decision was written about, is the "Jump to" strip below the answer:
`agents/chat/context_processors.py::_links` reads the **most recent tool turn's**
`data["links"]` and drops any entry whose `route` is not in `card_routes()` or whose `anchor`
is not one of that route's own card's `HelpField.anchor` values — checking `isinstance` on both
before the membership test, so a malformed stored value cannot 500 the page rather than merely
fail to link — before calling `reverse(route)` and appending `?assistant=1#<anchor>`. Nothing
that is not already in the code-side table can become a link on a settings page: a whitelist by
construction, not by convention, and the same property `_links`'s own docstring names as "the
property that matters most here, because a link is the one thing on this surface an operator
will click." A test pins the negative case directly: a tool result naming an unknown route or a
foreign anchor renders no anchor at all.

**A SECOND LEG, ADDED BY THE PLACEMENT ROUND'S OWN FOLLOW-UP (owner-found defect,
screenshot-confirmed, 2026-09-12).** A turn whose answer NAMED "Identity & security" was observed
live showing a jump strip of `rag-settings` anchors only — the model had made just its first
`settings.card` call (the documented, already-drilled variance: a model that calls the tool for
one page and answers a second page from memory, the very failure class the prompt-discipline fix
closed on the MODEL side), and the leg above links only a page the model actually fetched THIS
turn. The answer pointed the reader at the right page; the strip pointed at the wrong one
entirely.

The fix is platform-side and model-independent, because the prompt cannot be made to guarantee a
second tool call on every run (the flagship re-drill this same round measured roughly a
one-in-three-to-one-in-four per-run rate for exactly that follow-up call). `_links` now also
matches the final answer's own raw text against `foundation.settings_help.CARDS`'s **canonical
`title` and `route_name` spellings** — `"Identity & security"` and `"identity-settings"` both
resolve to the same card — word-bounded and case-insensitive, EXACT rather than fuzzy, so a
near-miss spelling ("Identity and security") or an invented page name never resolves to the wrong
card or to no card at all landing on a real one. A match yields a PAGE-LEVEL link only — no
anchor, because the model never fetched that card and there is no `HelpField` to point at — and
only for a route the first leg did not already cover, so a page fetched AND named earns the one
sharper anchor link, never a duplicate. The whitelist is unchanged in kind: still matched only
against the platform's own table, never against anything the model wrote that is not in it, and a
test battery pins the owner's own scenario, a registry miss, a near-miss spelling, hostile/HTML-
shaped text naming invented pages, and a cap (`MAX_JUMP_LINKS`) against an answer that names every
page on the box.

**A THIRD LEG, INLINE IN THE ANSWER'S OWN PROSE (owner feedback, live, screenshot-verified,
2026-09-13).** The two legs above build a separate strip below the answer; the sharper
complaint was that the answer itself "reads glitched" — it speaks in internal route names
("chat-tool-entitlements", "identity-settings (identity-settings)") inside the sentence, while
the only clickable thing on the page sits underneath it. `agents/chat/context_processors.py::
_linkify_named_pages`, called from `_card`, is the
fix: it runs on `render_answer`'s OWN OUTPUT — escaped first, minimal markdown second — and
wraps the FIRST occurrence of each registry card's own `title` or `route_name` spelling, found
IN THAT ALREADY-RENDERED STRING, in an `<a href="{reverse(route)}?assistant=1">` built from the
identical table-sourced `HelpCard.route_name` every other link on this surface already uses,
never from anything the model wrote. Matched against the ALREADY-ESCAPED text (`escape(card.
title)`, never the raw spelling), so `"Identity & security"` is searched for as `"Identity &amp;
security"` and no double-escaping of the matched span's own `&` is possible. Running strictly
AFTER `render_answer`, never inside it, is what makes this safe: every `<`, `>` and `&` in the
model's own words is already an HTML entity by the time this function's regex ever sees the
string, so nothing it can match was ever live markup, and the ONE `mark_safe` call is over a
string built entirely from verbatim slices of already-safe input plus anchor tags this function
built itself from a reversed, table-sourced URL — the visible text stays exactly what the model
wrote, never rewritten to the canonical title.

Two selection rules keep it bounded and unambiguous. FIRST OCCURRENCE PER PAGE, never per alias:
a card named twice, by either spelling, earns one link, not two. NON-OVERLAPPING, LONGEST-MATCH-
ON-A-TIE: every card's one candidate span is computed against the same original string and a
later candidate is dropped if it starts before an earlier-claimed span ends, so a short title
that is also a hyphen-delimited token inside a longer, unrelated route name — `"Chat"` inside
`"chat-tool-entitlements"` — never wins over the longer, more specific match starting at the
same position; the two can never produce overlapping or nested anchor tags.

**THE Q7 EXACT-CASE RULE (owner ruling, final-code resilience battery, 2026-09-13).** The
drilled scenario asked "libary admin onyl??", got an answer entirely about library ACCESS
POSTURE (`identity-settings`, never `rag-settings`), and this leg still wrapped the stray word
"library" in a link to `rag-settings` — because that card's own title happens to BE the single,
ordinary English word "Library". `agents/chat/context_processors.py::_requires_exact_case`
narrows this: a ONE-WORD `HelpCard.title` ("Models", "Library", "Chat", "Accounts", "Groups",
"Entitlements") now matches only on the registry's own exact capitalisation, or
case-insensitively when that page's own card was actually `settings.card`-fetched THIS turn —
at that point a lowercase mention is no longer a coincidental word collision, it is the page the
model is visibly, groundedly discussing. Multi-word titles ("Identity & security", "Tool
access") and every route name are unchanged, because ordinary English so rarely collides with
either. ONE SHARED PREDICATE SERVES BOTH LEGS — the strip's `_named_page_links` and this inline
leg — so the rule can never drift into two different answers to "does this title need exact
case here." This narrows the residual; it does not eliminate it — see named gap G11 below.

### 5. The settings-only surface as a wrapper on the one gate rather than a column

`Conversation` gained **no** new field. `agents/defaults.py::SETTINGS_SURFACE_SLUGS: frozenset[str]`
names the one slug this phase ships, and two thin wrappers —
`chat_surface_agents` and `chat_surface_conversations` (`agents/visibility.py:217-267`) —
`.exclude()` on that frozenset over `visible_agents`/`visible_conversations`, the platform's
**one** list gate. No migration, no `Conversation.surface` column, no narrowing of
`visible_agents`/`visible_conversations` themselves — those functions must keep answering
*yes*, because the panel reads the **same** rows through the **unwrapped** gate
(`agents/chat/context_processors.py`, §6.3 of the design spec). Narrowing the platform's one
gate to express a surface preference would make it claim an administrator may not run an agent
they are, at that moment, running.

Every list-shaped surface on `/chat/` — the picker, the sidebar, `/chat/all/`, the offers list,
the start-conversation guard — now reads through one of those two wrappers, which closes the
assistant off the chat surface's **lists**. That alone is "unlisted", not "unreachable": every
row-addressed chat view goes through one function,
`agents/chat/service.py::visible_conversation_or_404` (`:479`), which asked the un-wrapped gate.
Left alone, `/chat/c/<uuid>/` would still render the assistant's own conversation on the full
chat surface — model picker, attach door, post, rename, duplicate, pin, archive, and **share**
one click away. `visible_conversation_or_404` now asks `chat_surface_conversations`
unconditionally — one word changed inside the function, no keyword, no per-call-site edit — so
all nine of its call sites answer **404** on a settings-surface conversation, 404 rather than
403 because a 403 on a row-addressed URL would confirm the row exists. Two id-addressed reads
that ask the same underlying gate through a different name are deliberately left un-narrowed —
named gap 1 below states exactly which two, and why.

That is the reading of ruling 2 this phase commits to: **unreachable**, not merely unlisted,
which is what shuts the sharing door. `Share.Target.CONVERSATION` still exists on the generic
share table (ADR 0016), but the one page that could reach it for this conversation now 404s
before the share action is ever rendered.

### 6. The composer's CSS: five selectors promoted, one left behind

The panel reuses `chat/_composer.html` directly (spec §6.5) rather than a second, panel-owned
composer — the owner had already ruled against exactly that shape ("There shouldn't be two
different code bases. Both should serve both purposes"). Doing so needed `.composer-card`,
`.composer-textarea`, `.composer-toolbar`, `.composer-toolbar-left`, `.composer-toolbar-right`
and the structural `.composer-card > form { display: contents; }` rule promoted from
`chat/base.html` to `foundation/templates/_shell.html`, the deepest template that is an
ancestor of both `chat/base.html` and `_settings.html` — byte-identical rules to a higher home,
no pixel changes, following the placement rule `foundation/ops/tests/test_css_ownership.py`
states but cannot itself enforce across this column boundary (its own sweep is scoped to
`agents/chat/templates/chat/`), so this phase wrote that placement check itself.

`.agent-picker` was the sixth selector the spec's own letter named for promotion, and it did
**not** move (orchestrator ruling, 2026-09-10, recorded in `_shell.html`'s own comment,
`:862-892`): the panel always renders the composer with `composer_mode="turn"`, and
`.agent-picker` is emitted only when `composer_mode == "start"` — a mode the panel never uses.
Promoting a rule with no consumer on the page it would newly reach is exactly the shape the
single-consumer house rule exists to catch — the same reasoning that keeps
`.composer-toolbar .picker` (the model picker) and the drag-drop staging class
`.composer-card-drop-target` chat-only — and it outranks the spec's own enumerated list here: a
recorded deviation, not an open question. Five selectors move; three, including
`.agent-picker`, stay exactly where they were.

### 7. The install door stays shared, refused by slug rather than closed

Owner ruling 2 closes the chat surface's lists (decision 5) and the row-addressed door
(decision 5). It does not, on its own, say what should happen to
`POST /chat/defaults/install/` (`chat-default-install`) — the one route that turns the
catalogue's `settings-helper` entry into a row. That route is class A — any signed-in caller,
no row rule — and the surface exclusion only removed the assistant from the `/chat/` offers
list; the door itself stayed reachable to a crafted POST from any member regardless of whether
they could see a button for it (Task 6 review, advisory 5).

The first-pass ruling on that gap was to close the door outright: refuse the route for every
settings-surface slug. Building it out found that ruling's letter self-contradictory — the
panel's **own** "Add the settings assistant" consent offer
(`agents/chat/templates/chat/_assistant_panel.html`) posts to that **same** shared route, on
purpose (spec §6.5 decision 12: "three lines on an existing route beats a fourth one"). A route
closed to every settings-surface slug would also refuse the panel's own consent flow.
`agents/chat/views/defaults.py::default_install` (`:71-100`) is the shape that shipped instead
(Task 10 amendment 5, adjudicated "UPHELD-WITH-REASONING" against the closure ruling's own
letter): the route stays shared, and `is_admin` is checked — costing nothing for an ordinary
install, since the check runs only when `slug` names a `SETTINGS_SURFACE_SLUGS` entry —
**only** for a settings-surface slug, refusing anybody who is not an administrator with
`_SETTINGS_SURFACE_REFUSAL`. The panel's own offer, rendered only for an administrator, keeps
working; the identical slug posted by a crafted request from a member does not.

### 8. Card and overview: two tools that look alike, gated differently on purpose

Both `settings.card` and `settings.overview` are read-only, and neither is protected by the
surface's route class alone — a grant is per-**agent**, so nothing stops an operator adding
either key to another agent's `tool_keys`, at which point a member on `/chat/` could reach it
(spec §5.3 decision 23). `run_overview` (`agents/settings_tools.py:153-186`) refuses a
non-admin **inside the runner**, before it reads a single value off the identity row:
`ToolRefused` (`agents/contracts/tools.py`), classified `refused` — no retry — because a member
is never the right audience for this box's posture, its `admin_sees_content` setting or its
session idle timeout, regardless of which agent holds the key. `run_card`
(`:106-149`) carries **no such check**, deliberately: its content is platform-authored help
text about pages the reader may or may not be able to open — the same text this repository is
about to publish in `docs/EXTENDING.md` — and it reports no value of any kind about this box.
Gating it would be security theatre that would also foreclose named gap 5's non-admin variant.
`HelpCard.gate` is carried as data for exactly that future — a card that did not say who may
open its page would be help text with a hole in it — with no runtime consumer today.

### 9. The owner-filtered binding, on the read, the write and the archive

`create_conversation(principal, agent)` stamps a conversation's `owner_kind`/`owner_key` from
the asking principal, and every place this phase resolves "this administrator's own assistant
conversation" filters on that stamp explicitly: `**owner_fields(principal)`, with **no
`sees_all_content` branch**, on the panel's own read
(`agents/chat/context_processors.py:268`), on `assistant_ask`'s conversation lookup
(`agents/chat/views/assistant.py:234`), and on `assistant_reset`'s (`:286`).

That filter exists because of a leak class this build's own review caught rather than one the
design spec anticipated: `visible_conversations` alone answers "may this principal **read**
this row", and on an accounts-on box with `admin_sees_content=True` that is **every**
administrator's assistant thread, ordered newest-first (`Conversation.Meta.ordering`) — so an
unfiltered `.filter(...).first()` binds whichever administrator's panel loads to whichever
administrator's thread was most recently updated, and `assistant_ask` would then **post a new
turn into a conversation its own caller does not own**. `sees_all_content` authorises reading
somebody else's thread; it does not authorise writing into it or archiving it, and
`may_post_to` — not `sees_all_content` — is the separate predicate that would have to say so
for that to be sound. The owner filter is the exact dict `create_conversation` stamps a new
thread with, so what each of the three call sites resolves is "my own assistant conversation",
never "any conversation I merely may read." A two-administrator regression test proves the fix
load-bearing by reverting it and watching the wrong thread bind.

The read side narrows one step further than the write side needs to, and the narrowing is
itself named rather than left to be noticed: an `owner_kind="service"` assistant thread — which
`owned_rows_q` would admit to an administrator's **read** access today — is invisible to the
panel's own lookup too, even though nothing on this platform creates one (`create_conversation`
always stamps the calling principal). Named gap 2 below records that as a narrowing with no
current effect, not a live gap.

### 10. Two scripts, kept deliberately separate

The panel fragment renders two `<script>` tags, and both are sanctioned, but they are not the
same script and were not merged into one. `chat/_enter_to_send.html`'s keydown handler arrives
with the shared composer include and is not specific to this panel — it is the identical script
every composer surface in the app renders, present here only because the panel reuses
`chat/_composer.html` (decision 6). The panel's own poller — the "asking never navigates"
mechanism, spec §6.7 — is the second, and it is the only script this feature's own budget owns.
Folding the two together was considered and rejected: Enter-to-send is keydown-only by its own
explicit scoping ("No fetch, no polling, no other behaviour") and is shared, unmodified, by
other composer surfaces that must never poll; combining them would either break that scoping
for those surfaces or fork Enter-to-send into a settings-only copy, trading one sanctioned
script for two near-duplicates. The fragment's own docstring
(`agents/chat/templates/chat/_assistant_panel.html`) used to disclaim only `<style>` and stayed
silent about the script the composer include already carried — technically true, practically
misleading — and now names both (Task 10 review, finding a1), following the identical
two-scripts-stay-separate precedent `chat/conversation.html:12-14` already sets for its inline
render and its poll body sharing one context builder.

### 11. The one-read rule, threaded through three call sites and measured rather than assumed

`identity.request.settings_row_for(request)` is read once per request and threaded explicitly
into every access call the panel's context builder and the three assistant views make —
`is_admin`, `visible_agents`, `visible_conversations` — so none of them re-fetches the
`IdentitySettings` singleton the gate middleware already stashed. That threading was not
complete at the design spec's own query-budget table (§11): a review round found
`visible_conversations`'s own `owned_rows_q` leg reading the singleton a second time, on top of
the `visible_agents` threading already in place, and closed it with one keyword at
`agents/visibility.py:107` rather than leaving the spec's own placeholder unmeasured. The
accounts-on, panel-open count that closure produced is **16, not the 17 the fix's own reasoning
predicted**: two queries dropped rather than one, because `is_admin`'s `_user_row` memoises on
the settings-row **instance** (`identity/access.py:80-93`), so closing the second
`IdentitySettings` read also removed a fresh `auth_user` re-read the memoisation would
otherwise have missed. `agents/chat/tests/test_assistant_panel.py:379` pins the measured number
as an **equality**, with the 16-not-17 reasoning in the test's own comment — a `<=` bound would
have hidden a regression exactly this shaped.

### 12. The six owner flags, named as decided

The design spec's §17 carries six judgment calls, each decided rather than left open, each the
kind of call the owner may want to overrule later. They are restated here rather than left
buried in a spec amendment, because a flag nobody re-surfaces at the record stage is a flag
nobody sees again:

1. **The `block.super` gap this phase works around rather than fixes.**
   `foundation/setup/templates/setup/index.html` overrides `{% block extra_style %}` without
   `{{ block.super }}`, so a rule added to `_settings.html`'s own style block would silently not
   apply on Install guides. The `:target` highlight is placed in `foundation/templates/
   _shell.html` instead, beside the rest of the settings layout's CSS, sidestepping the
   shadowing rather than fixing the leaf template — the CSS-ownership gate does not reach
   `foundation/` templates, so nothing catches the underlying gap today.
2. **The library caps the assistant will not recite.** The upload cap, page cap, media cap,
   retrieval top-k and score floor live on `tools.rag.models.RagSettings`, and `agents/` may
   not import `tools/` at all. `settings.overview` reports only what `identity.access` and
   `agents.models.ChatSettings` legally hand it; the assistant names the page and links there
   instead of reciting a number it cannot legally read.
3. **The one shared transcript on an open box — and its accounts-on counterpart.** `is_admin`
   is True for everyone there, and `visible_conversations` returns every row for the single
   `OPEN_PRINCIPAL` every viewer is — so a household box has **one** assistant conversation
   that whoever sits down continues, reads the history of, and can "start over" on for
   everyone. Correct for a box with no accounts, and the same thing already true of every
   other settings page on an open box, followed through to the transcript. **On an accounts-on
   box the opposite is true, by construction, and specifically because of decision 9's
   owner-filtered binding**: every administrator's `**owner_fields(principal)` lookup resolves
   to a thread only they own, so two administrators on the same accounts-on box get two
   isolated conversations, never one shared thread bleeding across `admin_sees_content`. A
   reader who saw only the open-box half of this flag could wrongly infer the sharing it
   describes is universal; it is not — it is the open posture's own `OPEN_PRINCIPAL`
   singularity, and nowhere else.
4. **The JS-off navigation cost.** Asking a question with JavaScript disabled is a plain POST
   and a redirect, which discards a half-filled settings form on the page it was asked from.
   The one sanctioned script this phase ships exists to prevent that in the ordinary case; never
   navigating at all is not achievable without one.
5. **The second transcript renderer.** The panel renders its own compact transcript fragment
   rather than reusing `chat/_turn_block.html` — a deliberate second renderer, justified by what
   it does not render (tool-card arguments, thumbnails, citations, attachment rows, five turn
   states). Reusing the chat markup would have dragged roughly twenty chat-scoped selectors into
   the global shell to render machinery this surface then hides.
6. **The assistant's own thread is not openable in `/chat/`.** Decision 5 above is the
   mechanism; the cost is named here in the owner's terms: an administrator who wants the
   assistant's history on the ordinary chat surface — to share an explanation with a colleague,
   say — cannot get it there. "Start over" archives, and the panel is the only reader.
   Overruling this means deciding, action by action, what `/chat/c/<uuid>/` may do for this
   conversation, with sharing the one that matters most.

**No AI model, product or vendor name appears anywhere in this ADR** (Global Constraint 5).

### 13. The panel becomes a floating corner widget, and the collapsed bubble stops being a local toggle

**PLACEMENT ROUND, RULING 1 (owner walk, 2026-09-11, verbatim: "I don't see a chat window").**
The panel rendered IN FLOW at the bottom of `.settings-main` — a 24px collapsed bar below
whatever the page's own content ran to, which on the widest settings page (the models console)
sat roughly four screens below the fold. This is the largest single change the placement round
made: `position: fixed`, not `sticky` or in-flow, pins the collapsed state to the viewport's
bottom-right corner as a bubble and the open state to an overlay card anchored just above it,
in `foundation/templates/_shell.html`'s own unconditional `<style>` block (`:381-387`). The
`<details class="assistant-panel">` markup itself did not move; only its CSS declaration site
and rules did. `position: fixed` was chosen specifically because it removes the box from
`.settings-main`'s flow entirely, so it can never push a page's own Save button down and can
never be pushed by one — audited against every ancestor in `_shell.html`, `_settings.html` and
all eleven settings leaves' own `extra_style` blocks for `transform`/`filter`/`perspective`/
`will-change: transform`/`contain` (any of which would anchor the fixed box to that ancestor
instead of the viewport) and for an `overflow: hidden` container around the panel's own include
(which would not clip it regardless, since a fixed box's containing block is the viewport unless
a nearer one exists) — recorded in `_shell.html`'s own comment so a future page that adds one of
those properties finds the warning before it silently breaks the corner placement.

**PLACEMENT ROUND FOLLOW-UP (owner review, 2026-09-11) — the bubble-as-link structural fix.**
Ruling 1's first draft kept the `<details>` element and restyled both its states with CSS
alone. The review caught a real defect this made newly reachable: a floating bubble invites
exactly the click a 24px in-flow bar rarely got, and that click is the native `<summary>`
toggle, which opens the SAME element LOCALLY, with no navigation — revealing whatever the
server had already rendered into the DOM, which for the collapsed state is nothing, because
`panel_context`'s own "COLLAPSED COSTS ONE QUERY" guarantee (decision 2's read-budget
discipline, restated) means the collapsed render never reads a single `Conversation` or `Turn`
row. An administrator with real history who clicked the bubble got an empty panel. The fix,
in `chat/_assistant_panel.html` and `_shell.html` (`:389-411`): the CLOSED state is now a plain
navigating `<a href="?assistant=1">`, not a `<details>` at all, so there is no local toggle left
to open with — the click is a real request through the SAME `panel_context` builder that reads
the conversation. The OPEN state stays `<details open>`, because by the time a page renders it
the transcript has already been fetched for THIS request, so a native LOCAL toggle back to
closed (and open again) is genuinely harmless — no read happens either way at that point.

This is a structural decision about the interaction between decision 2's zero-read collapsed
guarantee and the markup that renders it, not a cosmetic follow-up: a mechanism that is safe
when reached by navigation (a fresh request, `panel_context` runs, the right branch executes)
is not automatically safe when the same DOM node is also reachable by a LOCAL, non-navigating
toggle that bypasses the server round-trip that guarantee depends on. The fix removes that
second reachability path entirely rather than teaching the collapsed branch to read
speculatively.

### 14. The settings chrome carries the panel's open flag

**PERSISTENCE ROUND (owner report, 2026-09-14, verbatim: "If I have the chat up and then I
click on a new settings link, the menu goes back into hiding. Preserve the scroll location and
the status of the chatbox so I can click through the settings while keeping the chat box
active.")** The panel's entire open/closed state is one query parameter (decision 9) — which is
what makes it script-free and storage-free, and is also why every link in the settings area
closed it: a bare `{% url %}` href carries no query string, so each click was a fresh, collapsed
page. The decision is to make the CHROME carry the flag rather than to give the panel a memory:
while the panel is open, every settings-sidebar link in `foundation/templates/_settings.html`
appends it, the app bar's one `Settings` entry in `foundation/templates/_shell.html` appends it
too — that bar renders on every settings page, so it is a settings link like any other, and it
is what makes the landing route's passthrough reachable at all — and the `/settings/` landing
redirect preserves the one it was given. The suffix is declared ONCE, in Python
(`panel_context`'s `open_query` key) and rendered as a single variable per href, so the
collapsed render stays byte-identical to the one that predates the panel and no template holds
a second copy of the parameter's name. The one link that writes the parameter as a literal is
the console's prefilled manual-add anchor, which already carries a query of its own and so
needs `&` and a position before its fragment.

Scroll needs nothing: the transcript is `column-reverse`, so every render of the open panel
already lands on the newest exchange. That is the whole of "preserve the scroll location" —
there is no client-side scroll restoration, no `localStorage`, no `sessionStorage`, no cookie
and no `request.session` anywhere in this feature, and the panel's script budget is unchanged.

The parameter's NAME and its append rule moved to `foundation/settings_area.py`
(`ASSISTANT_OPEN_PARAM`, `with_assistant_flag`), re-imported by the panel's own column under the
names it already used. The import law forces this and it is not an inconvenience: the chrome
that has to write the flag spans four columns, `identity/` may not import `agents/` at all, and
`foundation/` is the one column all of them may import — so the definition lives with the
settings area, exactly as the sidebar's gate vocabulary already does.

**AND THE SAVES, on the same rule.** A form is a link that writes: submitting one from a
settings page redirects, and a redirect with no flag closes the panel exactly as a bare href
did. So every settings form's `action` appends the same `open_query` suffix the sidebar does,
and every settings POST view returns through `foundation/settings_area.py::settings_redirect`
(over `preserve_assistant_flag`), which appends the flag to the target it was already
redirecting to — and only when the submitting request carried one, so a save made with the
panel shut redirects byte-for-byte where it always did. Pure string work over `request.GET`:
no query, no session, no cookie, nothing stored on the client. The console's own
`_redirect_console` shows why the rule is a function and not a literal: it already preserved
an active endpoint override, and the two parameters now ride the same redirect together.

**BOTH HALVES ARE SWEPT, and one alone is a green suite over a broken feature.** The
form-`action` half is asserted off the rendered HTML of every page in `card_routes()`, and the
redirect half off every POST endpoint whose 302 lands in the settings area — both derived, in
`identity/tests/test_route_matrix.py::TestEverySettingsSaveKeepsTheAssistantPanelOpen`, so a
settings page added later is covered by the driver row and the template it has to write anyway.
The first round of this work pinned only the redirect half, which left 35 of 36 shipped form
actions unguarded while reading green (review round 1, I1); the same round found the app bar's
own entry dropping the flag behind two tests that asserted the bar in prose and built their URL
by hand (I2). Both are why the rule is swept off RENDERED markup rather than off URLs a test
constructs.

**The named gap: the scan button** — recorded in full as G15 below, with the two non-card pages
a settings page links to. In short: `server_scan` re-renders rather than redirects, on a route
the panel's gate does not cover, so the panel cannot survive it and the two scan forms
deliberately do not pretend otherwise by carrying a flag that route cannot honour.

### 15. Unsaved settings input is guarded in the chrome, by a stateless scan at unload time

**OWNER REPORT, LIVE, 2026-09-16.** Decision 14 made the panel survive a click through the
settings area; what that same click does to a HALF-FILLED FORM it does not touch. Every
navigation this area offers — a sidebar link, the bar's `Settings` entry, the collapsed
bubble, the open panel's `Close` and its jump strip — is a full page load, and a page load
discards whatever the operator had typed and not yet saved, silently. The panel made that
shape common: a jump-link exists to be clicked *from* a page you were reading, and the bubble
sits in the corner of every page in the area.

The decision is a **dirty-form `beforeunload` guard, declared once in the settings chrome**
(`foundation/templates/_settings.html`) and nowhere else. Its scoping is structural, not
configured: every settings page extends that template and no page outside the settings area
does, so the guard ships exactly where the settings chrome renders — collapsed page or open
one, and for every viewer the area renders for, including the one public leaf.

**Stateless, and that is the design rather than an economy.** There are no `input` listeners
and no dirty flag anybody has to keep current. At unload, one document-delegated handler
compares each control to the DOM's own record of what the SERVER rendered — `defaultValue`,
`defaultChecked`, and for a select the option carrying `defaultSelected` (with the browser's
own "first non-disabled option" fallback when the server marked none). Three properties follow
from that and none had to be built: an operator who types into a field and then reverts it by
hand gets no prompt; markup the panel's own XHR swapped into `#assistant-body` after page load
is covered with no re-binding at all; and hidden inputs, the buttons and disabled controls are
excluded by name, so a CSRF token is never mistaken for somebody's typing.

**A save does not prompt about itself.** A delegated `submit` listener records the form being
submitted and the unload scan skips that one form; a DIFFERENT dirty form still prompts,
correctly, because that edit really would be discarded by the save's redirect. What separates
a real save from the panel's own XHR ask is the submit event's `defaultPrevented`, read after
the form's own handler has cancelled it — so the ask records no exemption it will never spend,
and no deferred clear has to be scheduled to undo one. The scan also consumes the record it
reads, so a navigation the operator cancels at the prompt leaves no standing exemption behind.
Scheduling nothing also keeps the guard clear of `foundation/ops/tests/test_shared_poller.py`'s
repo-wide ban on `setTimeout`/`setInterval` outside the shared poller, which it would otherwise
have had to argue its way past by name.

**Progressive enhancement, and no client storage — the same two rules the rest of this feature
is built on.** With JavaScript off, every settings page behaves exactly as it did before this
existed; the guard holds nothing between loads because a scan made at unload time needs to hold
nothing. The prompt itself is the browser's: `preventDefault()` plus `returnValue` is the whole
contract, every engine in service shows its own generic wording, and this does not try to write
one. The cost is one more `<script>` on every settings page — the third, beside the panel's
poller and the shared composer's Enter-to-send — which moved two existing exact-count pins
(`agents/chat/tests/test_assistant_panel.py`, 1 → 2 on an open page where the assistant is not
installed, and 2 → 3 on an installed one) and added no query to any page. A second, smaller cost
is recorded rather than discovered later: registering a `beforeunload` listener unconditionally
can disqualify back/forward cache in engines that treat one as a blocker. Settings pages are not
performance-sensitive, and the alternative — registering lazily on the first `input` — would
reintroduce exactly the stateful listener this design refuses, so the trade is taken knowingly.
Its own claims are pinned in
`foundation/tests/test_shell.py::TestTheUnsavedInputGuard`, swept off `SETTINGS_GROUPS` so a
settings page added later is covered the day it is registered.

**The exemption's invariant is stated and gated, not assumed** (fix round 1, review Important 1).
Its correctness rests on *every recorded submit being followed by an unload of this document* —
an unload is the only thing that spends the record. Two shapes break that, and both are closed
rather than left latent: a form with `target` fires `submit` and navigates elsewhere, so the
recorder refuses those outright; and a submit cancelled by a listener delegated to `document` and
registered LATER than this guard would be invisible to the `defaultPrevented` read, leaving that
form exempt for the life of the page. The second is the sharper one, because document delegation
is this repository's sanctioned script pattern and the guard renders in `content` while a leaf's
own `scripts` block renders after it. The rule — a settings-page script cancels a submit ON THE
FORM, never on `document` — is written into the template's comment, into the `EXTENDING.md`
recipe, and into `foundation/tests/test_shell.py::TestTheGuardsOrderingInvariant`, which derives
the settings area from `_settings.html`'s own `extends`/`include` graph and fails the build on
either shape.

## Named gaps and deferred work

Recorded as its own section, following ADR 0015's, ADR 0016's and ADR 0017's shape, because
these are costs and deferrals that stand beside the phase's decisions rather than inside any
one of them.

**G1 — two id-addressed reads ask the same underlying gate decision 5's narrowing excludes, and
stay un-narrowed.** `visible_turn` (backing `chat-turn-status`, an assistant turn's own poll
fragment) and `agents/workstreams.py::transcript_for` (the `tools/rag` seam's transcript read)
both resolve through `visible_conversations` directly rather than through
`visible_conversation_or_404`'s narrowed form, so both stay reachable by id to the principal who
owns the conversation. Neither is a page and neither is a navigable affordance — the claim
decision 5 makes is precise and bounded: every row-addressed **chat view** is closed, not every
id-addressed read in the codebase, and narrowing either of these two would add a wrapper for no
reachable action.

**G2 — the owner filter narrows out a service-owned thread nothing creates yet.**
`**owner_fields(principal)` on the panel's read binds only conversations this principal owns,
narrower than `owned_rows_q` would admit an administrator on the read side alone — an
`owner_kind="service"` assistant thread would be invisible to the panel even though an
administrator with `sees_all_content` could otherwise read it. Nothing on this platform creates
a service-owned assistant conversation today (`create_conversation` always stamps the calling
principal), so this is a narrowing with no current effect rather than a live gap — recorded so
a future service principal does not find its turn silently absent from the one surface built to
show it.

**G3 — a retrieval leg over the help content, keyed on `CONTENT_HASH`.** If the card table ever
outgrows an index-plus-on-demand shape, the cards become an ingested corpus and retrieval
answers instead of `settings.card`. `CONTENT_HASH` is the hook: a derived index would store the
hash it was built from and refuse to answer, or rebuild, when it no longer matches the code —
the artifact the owner's staleness requirement was really about, built into decision 2 before
anything needs it.

**G4 — act-mode.** An assistant that changes a setting after a confirmation is not built and
cannot be granted today: a `mutates=True` `ToolSpec` is the hook, and the registry deliberately
refuses to grant one until that is a decision somebody makes (decision 1). Owner ruling 1
forbids it for now; nothing here makes it harder later.

**G5 — a non-admin variant.** A member-facing helper for the settings a member can actually
reach does not exist. `HelpCard.gate` is already carried per card for exactly this future — the
card set can be narrowed per principal the day there is a second audience — but has no runtime
consumer today; decision 8's tool asymmetry (`settings.card` reporting no box-specific value)
is what keeps that door open rather than shut by this phase's own admin-only panel.

**G6 — with a feature off, the assistant still describes a page this box does not mount.**
`foundation/settings_help.py`'s `CARDS` is a pure leaf (decision 2): it ships all eleven cards,
including `vision-engine-files`, unconditionally, and cannot consult
`settings.FARABUNKER_FEATURES` to know `config/urls.py`'s `/vision/` mount is conditional on the
`"vision"` token. `settings.card`'s `page_choices()` therefore still offers "Engine files" as a
page name to the model on a box that has the feature off (re-check, R3: `run_overview` reports
five posture/policy values off `ChatSettings`/`IdentitySettings` -- posture, library posture,
content access, session idle minutes, time-awareness -- and never enumerates pages at all, so it
is not part of this residual; `page_choices()` is the only place the flag-off leak runs through).
The final review (finding C1) caught the live consequence of this — an unguarded `reverse()` in
`agents/chat/context_processors.py::_links` raised `NoReverseMatch` out of a context processor,
a 500 on whichever settings page a tool turn naming that page was open on — and the fix is a
`try/except NoReverseMatch` at the reversing site, which is also the correct and sufficient
place for the gate: `_links` already whitelists by route, so a route this box does not mount now
silently drops out of the link strip rather than crashing it. The residual this leaves is
narrower and accepted: with `vision` off, the assistant will still *describe* Engine files in
prose (the card exists; `settings.card`'s enum still names it), because a pure card table cannot
know the flag — it will simply never **link** to it, because there is nowhere to link. Closing
this fully would mean threading `FARABUNKER_FEATURES` into the pure leaf, which is exactly the
coupling decision 2 exists to avoid; the reversing-site guard is the smaller, correct fix, not a
stopgap for a larger one still owed.

**G7 — a standalone doctrine paragraph reproducibly broke the installed model; folding into an
existing sentence did not — and the same law held for three more folds after this one.** A
flagship-question drill (2026-09-11) found the settings-helper row's `system_prompt` answering
from memory instead of a second `settings.card` call when the first card's own text redirected
to another page. The fix stating that rule as its own third paragraph made the installed model
return an empty answer with zero tool calls on every run; the identical sentence appended to the
end of the existing `settings.card` paragraph instead did not reproduce that failure and
measurably increased second-page calls (fold 1, `cc52086`).

Three more findings, from later live drilling, hit the identical discipline gap from directions
the first sentence's own wording did not cover, and every one of them was closed the same way —
one more sentence, same paragraph, never a new one:

- **Fold 2 (`27a1034`)** — a multi-part user question naming a second page itself, and a
  negative claim ("no such setting exists") about a page never queried this turn, both closed
  by one sentence covering "before you name any page in a multi-part answer, or say a page
  lacks a control."
- **Fold 3 (`86c329e`)** — the final-code resilience battery's Q10 finding: asked why a
  teammate cannot see the Settings area, the model attributed visibility to entitlements, a
  fabricated mechanism, having called only `identity-entitlements`'s card. Closed by one
  sentence covering "before you say who can see or access something, which mechanism controls
  it."
- **Re-fold 3 (`4077e7f`)** — fold 3's own live re-drill (fresh conversation, reset preview row)
  closed the fabrication but then recommended "the Accounts page" as where to act, without ever
  calling `identity-users`'s card — the identical gap one layer deeper, at the ACTION rather
  than the DIAGNOSIS. Same sentence, widened to also cover "or where someone would go to act on
  it." See G9 below for the owner's ruling on this fold's own re-drilled result.

**Measured trajectory of the `settings.card` paragraph (paragraph 2) across all four folds:**

| SHA | Whole prompt | Paragraph 2 | Sentences in P2 |
|---|---|---|---|
| `a12d512` (READY baseline) | 1,311 ch | 305 ch | 3 |
| `cc52086` (fold 1 — card-redirect) | 1,425 | 419 | 4 |
| `27a1034` (fold 2 — multi-part / negative claims) | 1,758 | 596 | 5 |
| `4d48862` (human names — paragraph 4, not P2) | 1,895 | 596 | 5 |
| `86c329e` (fold 3 — who-can-see-what) | 2,095 | 796 | 6 |
| `4077e7f` (re-fold 3 — widened to where-to-act) | 2,115 | 816 | 6 |

The whole prompt grew 61% across the delta; paragraph 2 alone, the one the fold-in law targets,
grew 168% and is now 39% of the entire prompt. Recorded here, and at the edit site in
`agents/defaults.py`, because it is a finding about the installed model's prompt-structure
sensitivity — and about how far a single paragraph can be folded before a future tuner asks
whether folding is still the right move — that a future doctrine tuner has no other way to know
without re-drilling.

**G8 — the Q3 unfilled-placeholder blemish, owner-accepted, documented but not fixed.** The
final-code resilience battery's Q3 re-drill surfaced a literal, unfilled placeholder token —
`[value from settings__overview]` — in an otherwise grounded, true answer, naming a tool
(`settings__overview`) the turn never called. Not a false claim (no number is asserted) and not
present in the original battery's own Q3 run; a visible synthesis glitch, not a truth or
grounding defect. Owner ruling: **ACCEPT and document, no prompt or platform machinery built to
close it** — a cosmetic synthesis rough edge judged not worth a fix of its own. Revisit only if
the same shape recurs somewhere the answer's truth, not merely its polish, is at stake.

**G9 — the Q10 ceiling, owner-accepted as this local model's limit on a three-fold doctrine.**
G7's re-fold (`4077e7f`) closed the specific fabrication its own drill found (Settings-area
visibility attributed to entitlements instead of administrator status) but its own re-drilled
result still falls short of full completeness: the answer correctly names the mechanism
(administrator status) and is fully grounded in what it DOES say, but never calls
`settings.card(page='identity-users')` and so never tells the reader *how* to promote a
teammate. Owner ruling: **ACCEPT** — true, grounded, and incomplete is not the same defect class
as false or fabricated, the marginal return on a further prompt fold at this paragraph's current
size was already measured near zero (G7's trajectory table), and a fourth attempt was not made.
Revisit if a stronger model ever binds the `chat.converse` role this agent runs under; this is a
model-capability ceiling on the current one, not a prompt defect proven fixable by more wording.

**G10 — route-slug leakage into prose persists at a model-bound, partial-compliance rate.** The
human-names prompt fold (`4d48862`, decision on referring to a page by its human name only,
never an internal route name or a parenthesised slug) is pinned
(`agents/tests/test_settings_tools.py::test_its_system_prompt_asks_for_the_pages_human_name_
only`) and measurably changes behaviour, but does not eliminate the failure it targets: live
re-drills after the fold still occasionally show a raw route slug (e.g. `identity-users`)
surviving into an otherwise human-named answer, at a rate the drilling honestly reported rather
than suppressed. `_linkify_named_pages` (decision 4's third leg) makes the residual harmless
regardless of which spelling survives — both the human name and the raw slug are in the same
whitelist and both link to the same real page — so the consequence of this gap is cosmetic, not
a grounding or safety defect. Owner-accepted, model-bound: no prompt wording eliminated it
entirely across live drills, and closing it further is a model-capability question, not
something demonstrated fixable by another sentence.

**G11 — the Q7 exact-case rule narrows, and does not eliminate, generic-word auto-linking.**
Decision 4's third subsection states the rule; the residual it leaves is recorded here for the
same reason G6 records `vision-engine-files`'s: an exact-case occurrence of a one-word title
("Models", "Library", "Chat", "Accounts", "Groups", "Entitlements") still links from ANY answer
that happens to contain it in that exact spelling, whatever the surrounding sentence is actually
about — the rule bounds the false-positive rate (a lowercase, in-passing mention of a common
word no longer auto-links) without claiming to bound it to zero. The worst case this can produce
is an extra, whitelisted link to a real settings page the answer did not mean to point at — the
same worst-case shape the owner already accepted for the hyphen-compound residual
(`agents/chat/context_processors.py:621-630`, "KNOWN, ACCEPTED RESIDUAL"), never a link to the
wrong page's WRONG content or an unwhitelisted destination.

**G12 — S6: the per-request/per-tick read helper is named in three columns and absent from the
rest, by ruling (2026-09-14).** The settings-backend audit's S6 observed that only `identity/`
had a named once-per-unit-of-work read helper (`identity.request.settings_row_for`), while
`tools/rag` made fifteen independent `get_solo()` calls and the queue worker paid two
un-threaded reads per 0.5s tick. The closing ruling was **implement the cheap half, by
pattern-copy rather than invention**: the worker tick now reads `JobSettings` once and threads
it into `claim_and_admit` and `_evict_to_match_plan`, and `tools/rag/views.py` gained
`settings_row_for(request)` — the same name as identity's, because it is the same pattern with
one difference (nothing stashes `RagSettings`, so the helper does its own first fetch). Both
are pinned by query-count tests counted against their own table, each with a mutation that
reintroduces the removed read and goes red.

*What was deliberately NOT done, and why:* the reads in `tools/rag`'s `ingest.py`, `media.py`,
`index.py` and `services.py`'s prune path are untouched. Those run on the watcher and inside
job handlers, where there is no request or tick to scope a memo to, and inventing a second
scoping mechanism for them would be exactly the invention this ruling declined. **One
`services.py` site is the exception, named here rather than generalised over** (closing-wave
re-verify, C1): `stage_turn_attachments` reads the row *on a request path* — three chat POST
handlers reach it through `agents.chat.service.start_turn`. It is left alone for a different
reason: collapsing it would mean threading a settings row through `start_turn`'s cross-column
signature, which is a design change, not a query-count change. The audit's own words — "not
currently costly; the pattern to copy already exists" — are why the expensive half is not
attempted rather than deferred silently.

**G13 — S7: `ModelConnection.config` stays schemaless; only its discoverability is closed
(2026-09-14).** The fourth settings tier is a JSON blob per registered connection, splatted
into an engine adapter's builder. It has no schema, no validation, no migration per key and no
help card, which the audit correctly called "the path of least resistance for the next
per-engine knob". The closing ruling split the finding: **the discoverability half is closed
now, the validation half is an owner-call that is not this branch's to make.**

Closed: `docs/EXTENDING.md`'s "Where a setting lives" gained rule (d) — what the tier is, every
key any code reads today, and the rule that a new key must join that list in the same commit —
with `foundation/ops/tests/test_connection_config_keys.py` sweeping every production module for
the spellings a config key is read by and comparing both directions against the documented
table, so the list cannot rot in either direction.

**Open, and the owner's call:** whether this tier should get runtime schema validation at all.
Building it means deciding what a key's declaration looks like, where it lives (a per-engine
declaration on the adapter is the obvious shape, and would make the engine seam own its own
config contract), and what happens to a connection already storing a key the new schema
refuses. None of that is a mechanical fix, and a documented-and-guarded key list removes the
discoverability cost that made the question urgent. Recorded here beside the
`SettingsOverviewSource` seam as the second "lawful if built, deliberately not built" item this
programme leaves the owner.

**G14 — model and vendor names survive in one test file's fixtures, by ruling (2026-09-14).**
The final whole-delta review's N1 found ten added lines in `models/registry/tests/test_views.py`
carrying vendor-shaped fixture strings, extending a pattern that file already carried ~210 times
before this programme began. Zero hits in `docs/`, any README, any ADR, any template or any
production module — the house rule is clean everywhere it is load-bearing. Two of the shapes are
not prose at all (an engine name is a functional key that must match the adapter registry), and
a partial rewrite of ten lines inside a file with hundreds would create an inconsistent island
rather than close anything. Recorded as a backlog item for one repo-wide fixture sweep before
the repository goes public, which is what the reviewer recommended and explicitly did not hold
the branch for.

**G15 — the scan button is the one settings action the panel's open flag cannot ride
(persistence round, decision 14).** `models/registry/views.py::server_scan` does not redirect:
it re-renders the console from its own POST url, because scan results have nowhere stateless to
survive a redirect (that view's own docstring argues it in full). The panel's first gate is
whether the request's route is a settings CARD route, and a POST-only scan endpoint is not one,
so the re-render carries no panel context at all and the panel collapses there — no query
string can change that, which is why the two scan forms deliberately do NOT append the flag
(review round 1, M2: writing it would leave a parameter in the address bar of a panel-less page
and imply a coverage that does not exist). Closing it needs either a card-registry entry for a
route that is not a page or a cross-column import the import law forbids; neither is a
persistence-round change. Named by name in `identity/tests/test_route_matrix.py`'s exemption
table (`_UNFLAGGED_ACTIONS`) and in its settings-save floor, so the exemption is a recorded
decision rather than a silent hole. The same shape, for the same reason, applies to the two
non-card pages a settings page links to — model sets and the entitlement detail page: they are
not in `card_routes()`, so the panel does not render on them either.

## Consequences

- **The assistant cannot mutate a setting, and that is checked at two levels rather than
  claimed at one.** The tool registry refuses a mutating grant structurally; a test walks the
  agent row's actual `tool_keys` and restates it.
- **There is no context repository to go stale**, because there is no context repository. The
  card table is the code, `CONTENT_HASH` makes that checkable rather than assumed, and two
  drift tests turn a page changed without its help content into a red suite rather than a wrong
  answer six months later.
- **A future settings page cannot ship silently without help content or working anchors.**
  `docs/EXTENDING.md`'s "Adding a settings page" recipe binds the author, and the two rules the
  drift tests cannot catch — describing only the page's own controls, and naming a
  posture-dependent control's conditional presence in its `meaning` — are written into that
  recipe rather than left to be rediscovered.
- **Every link the assistant offers is one the platform already knew about.** A model cannot
  put an operator on a page or anchor this table does not name, because the model never builds
  the link at all.
- **The settings assistant is invisible and unreachable on `/chat/`**, not merely unlisted —
  closing the row-addressed door was the one gap that would have made "settings-only" false the
  moment somebody typed the URL by hand — and the one remaining door that could still create a
  row for a non-admin, the shared install route, refuses that door's own slug set rather than
  staying silent about it.
- **An administrator's assistant conversation cannot bind to another administrator's, on any
  path that reads, writes or archives it.** The leak this build's own review found — an
  unfiltered `.first()` resolving to whichever administrator's thread was most recently updated
  — is closed identically on all three call sites, and a two-administrator regression test
  keeps it closed.
- **Six owner-facing costs and fourteen named gaps ship recorded rather than hidden.** (Five
  when this ADR was first written; G6-G11 were added by the doc-truth wave that made this
  section the durable home for owner-accepted residuals, and G12-G14 by the coherence
  programme's closing wave.) None of them
  is a defect discovered later; each is a call the owner can revisit with the tree already
  pointing at exactly what would have to change.

## See also

- [`docs/superpowers/specs/2026-09-09-settings-assistant-design.md`](../superpowers/specs/2026-09-09-settings-assistant-design.md)
  — the binding design: the full help-card contract (§3), anchors and the highlight (§4), the
  two tools in full (§5), the panel's context processor and query budget (§6, §11), keeping the
  surface off `/chat/` (§7), gating and the acting principal (§8), injection posture (§9), the
  non-goals and deferrals (§15), the author's own decisions (§16), and the six flags restated in
  decision 12 above (§17).
- [ADR 0016](0016-identity-and-entitlements.md) — `is_admin`, route classes, and the generic
  `Share` table whose conversation target decision 5 makes unreachable for this conversation
  specifically.
- [ADR 0015](0015-agent-layer-and-tool-contract.md) — the columns, the import law, the tool
  contract's `mutates` field, and `build_messages`, the one function this phase deliberately
  does not modify.
- [ADR 0010](0010-model-management-framework.md) — the rule `grantable_tools()` still enforces:
  a settings-mutating tool is registered but never grantable.
- `agents/README.md` — the settings assistant's place among the platform's registered tools and
  its catalogue entry.
- `docs/EXTENDING.md` — "Adding a settings page", the five-step recipe this phase wrote,
  including the two rules its own drift tests cannot catch.
