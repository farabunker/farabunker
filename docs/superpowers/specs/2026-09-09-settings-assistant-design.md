# Settings assistant — design spec

**Date:** 2026-09-09
**Amended (3):** 2026-09-09 — round-3 confirm: all mechanics verified against the tree and
both round-2 residuals closed, leaving two cosmetic ones, applied here. §14's preamble had
over-swept — it claimed "every other drill" runs in the enterprise world as an administrator,
which is wrong for the four **suite runs** (3, 4, 9, 10 — they have no preview world) and for
drill 7's second half (**deliberately signed out**), and it contradicted the budget drill's
own "in both postures". Each drill now states its own world. Formatting: the pseudo-markers
`1b.`/`1c.`/`7b.`/`4b.` are not CommonMark ordered-list markers and rendered as loose
paragraphs — 1b and 1c are now nested items under drill 1 (they are the same question in
another posture), the tool-audience drill is its own number 8 (the old 8 and 9 became 9 and
10), and §18's item runs 1-8; decision 22's stray eight-space continuation is re-wrapped to
four. No mechanics changed. Sections touched: §14, §16, §18.
**Amended (2):** 2026-09-09 — round-2 re-check of the amendments below (1 Major, 2 minor,
1 nit residual; all five round-1 Majors verified closed, and the
`settings.card`/`settings.overview` asymmetry adjudicated in the spec's favour with no
change). **The Major:** done-when 1b told the implementer to accept an answer the tree
contradicts — `identity/templates/identity/settings.html:120-135` branches on
`posture == "personal"` **only**, so an open box renders the real library-posture select and
"the control appears once accounts are on" is false; and the **personal** posture, where the
control is a hidden input with no page to lock, was uncovered entirely. §14 done-when 1b is
reworded to what is true (the page is reachable, the control is there, and the *setting* has
no effect on an open box), **done-when 1c** covers personal, and §3.2/§13.1 gain the second
untestable card rule: a control whose presence is posture-dependent says so in its `meaning`,
because assertion 3 renders one posture and cannot see the others. Also: the
`visible_conversation_or_404` sweep is **nine** call sites, not eleven, and §7.1.1 now names
the two id-addressed reads it deliberately leaves un-narrowed (`visible_turn`/
`chat-turn-status`, `agents/workstreams.py:399`) and why neither is a leak; §14's preamble
reconciled with done-when 2's own open-posture requirement; `--target-wash` declared once, in
the token blocks rather than in the highlight rule; `settings_area.py:99` → `:98`. Sections
touched: §3.2, §4.2, §7.1.1, §10.3, §13.1, §14, §16, §18.
**Amended:** 2026-09-09 — adversarial review (5 Major, 6 minor, 5 nit), applied **in place**
rather than appended, following the identity and workstreams specs' own handling of a review:
the sections that carry the mechanics are amended where the mechanics live. **M1** the
flagship acceptance criterion named the wrong page (`library_posture` lives on Identity &
security, not Library) — §2.1, §3.2, §13.1 and §14 done-when 1/1b; **M2** the context
processor had no admin gate, so it built panel state for an anonymous visitor on the box's one
public settings page — §6.2, decision 21; **M3** the query budget was not achievable on an
accounts-on box while §11 mandated equality — §11 rewritten per posture; **M4** the
row-addressed door `/chat/c/<uuid>/` was left open, making the assistant's thread postable,
renamable and shareable — §7.1.1 (`visible_conversation_or_404` narrowed), decision 22, §15.5;
**M5** neither tool checked its
audience, so a grant to another agent would hand a member this box's configuration — §5.3,
decision 23. Minors and nits landed in §4.2 (decision 24), §6.5, §6.6, §7.1.2, §9, §10.1,
§10.3, §17 flag 3 and §18. Sections touched: §2.1, §3.1, §3.2, §4.2, §5.3, §6.2, §6.5, §6.6,
§7.1, §8.3, §9, §10.1, §10.3, §11, §13.1, §14, §15.5, §16, §17, §18.
**Status:** Design. Nothing built. Written against `HEAD = 08e28c6` on branch
`settings-assistant`; every file and line cited below was read in that tree at write time, and
re-read at amendment time for every line the review disputed.
**Phase:** a small phase on the settings area UI-2 built (`foundation/settings_area.py`,
`foundation/templates/_settings.html`) and the agent runtime P2/P3 built (ADR 0015), after
Identity & Auth (ADR 0016) and Workstreams (ADR 0017).
**Lands as:** one plan-sized phase (§14), then ADR 0018, written last.
**Sequencing note:** the hygiene-sweep session (branch `hygiene-sweep`) lands CSS-block-only
changes to `_settings.html` and the settings leaf templates FIRST. This phase branches after
it. There is no other coupling between the two.

A **settings assistant** is a guide. An administrator opens any page in the settings area,
types a question into a panel that is on every one of those pages, and gets back an answer
grounded in what this box's settings actually are and where they actually live — with a
working link that jumps to the exact section and highlights it. It never changes a setting.

No model or product names appear anywhere in this document. The repository is going public
and ADR 0010's third amendment forbids the platform from naming a model for the operator.

There are **zero open questions** in this document. Every call is made. The calls that are
judgment rather than deduction are numbered in §16 and listed again, in one place, in §17.

---

## Table of contents

1. [Context: what already exists](#1-context-what-already-exists)
2. [Owner rulings, restated](#2-owner-rulings-restated)
3. [The help-card registry and its content hash](#3-the-help-card-registry-and-its-content-hash)
4. [Anchors and the highlight](#4-anchors-and-the-highlight)
5. [The agent and its two tools](#5-the-agent-and-its-two-tools)
6. [The panel](#6-the-panel)
7. [Keeping it off the chat surface](#7-keeping-it-off-the-chat-surface)
8. [Gating, routes, and the acting principal](#8-gating-routes-and-the-acting-principal)
9. [Injection posture](#9-injection-posture)
10. [Testing](#10-testing)
11. [Query budget](#11-query-budget)
12. [Migrations](#12-migrations)
13. [Documentation](#13-documentation)
14. [Phasing and done-when](#14-phasing-and-done-when)
15. [Non-goals and named deferrals](#15-non-goals-and-named-deferrals)
16. [Decisions the author made](#16-decisions-the-author-made)
17. [Flagged for owner](#17-flagged-for-owner)
18. [Brief-versus-tree corrections](#18-brief-versus-tree-corrections)

---

## 1. Context: what already exists

### 1.1 The settings area

`foundation/settings_area.py:84-110` is **the only table**: `SETTINGS_GROUPS`, three groups
(Setup / Access / Box) of `Entry(label, url_name, gate, feature)`
(`foundation/settings_area.py:57-77`), with `visible_entries()` (`:123`) and `first_entry()`
(`:143`) reading it and `/settings/` redirecting by it (`settings_index`, `:153`). Eleven
entries today. `foundation/templates/_settings.html:117-148` renders the same order and the
same gates as template `{% if %}`s, and the two are held together by a drift test that walks
a really-rendered sidebar and compares the whole `(href, label)` pair list against
`visible_entries()` — `foundation/tests/test_shell.py:381-437`. That test is the idiom every
drift test in §10.1 mirrors.

A context processor was **considered and rejected** for that sidebar, for a stated reason
this spec has to live inside: it "would run on every page in the box and cost a query the
console's own query-count pin forbids" (`foundation/settings_area.py:12-20`). §6.2 answers
that objection rather than ignoring it.

### 1.2 The one anchor precedent

`models/registry/templates/inference/console.html` carries three stable ids —
`on-this-machine` (`:1007`, `:1067`), `getting-models` (`:1016`, `:1084`) and `add-connection`
(`:1021`, `:1123`). Each appears **twice** because the page has a `{% if cold_start %}` /
`{% else %}` split and exactly one branch renders. A cross-request deep link is built at
`models/registry/templates/inference/_installed_row.html:145` — `{% url 'inference-console' %}
?prefill…#add-connection`. The pins are `models/registry/tests/test_views.py:325-326`,
`:4225,4228`, `:4373-4375` ("The anchor's target exists on the page") and `:4989`. **Every one
of those assertions is against a RENDERED body**, never against template source — which is
the only assertion shape that survives a two-branch template. §10.1 inherits that exactly.

There is **no** `:target` rule anywhere in the tree today; the highlight is new.

### 1.3 The agent runtime

- Catalogue: `agents/defaults.py:308-399` (`DEFAULT_AGENTS`, three `AgentSpec`s),
  `install_default` (`:474`) is create-if-absent and the ONE way a row appears
  (ruling 2, `agents/defaults.py:14-18`). `manage.py install_defaults` and the
  `chat-default-install` POST (`agents/chat/views/defaults.py:28`) are its two callers.
- Tools: `ToolSpec` + `register_tool`, runner as a **dotted-path string**, signature
  `(args, ctx) -> ToolResult`; the worked example is `models/registry/tools.py:37-82`
  (`MODELS_STATUS`), registered at `models/registry/apps.py:60`, and the three-step recipe is
  `docs/EXTENDING.md:18-164`. The two guard lists are `TOOL_MODULES`
  (`foundation/ops/tests/test_column_boundaries.py:185-189`) and `_REGISTRATION_MODULES`
  (`:217-231`).
- Prompt: `agents/runtime/prompt.py::build_messages` (`:837`), contributors joined by
  `append_paragraph` (`:654`), newest contributor `time_aware_now_line` (`:679`).
- Turns: `agents/chat/service.py::start_turn` (`:174`) is the one queue-a-turn seam;
  `agents/chat/views/turns.py::turn_create` (`:54`) is the POST that calls it, answering
  **either** 202 + JSON for an XHR **or** a redirect carrying `?pending=<turn_id>`
  (`:146-148`) — "Everything works without JS; this only decides whether to answer with JSON
  or a redirect" (`:44-50`).
- Rows: `agents/visibility.py` is the ONE list gate — `visible_conversations` (`:64`),
  `visible_agents` (`:142`), `create_conversation` (`:761`), `set_conversation_archived`
  (`:411`). No module under `agents/chat` touches those managers, for any reason
  (`agents/visibility.py:765-771`).

### 1.4 The four facts that constrain the whole design

1. **The import law** (ADR 0015:92-120). Pure leaves are universally importable; **Django
   apps are column-private** with two named exceptions; cross-column work goes through a seam,
   never an import. `identity/` is reachable only through `identity.contracts`,
   `identity.access`, `identity.request`, `identity.audit`
   (`foundation/ops/tests/test_import_law.py:606-608`, enforced as an allowlist at `:650`).
   `agents/` may not import `tools/` at all. **Consequence, §5.4:** no single module can read
   the identity singleton, `ChatSettings` and `RagSettings` together. The design does not try.
2. **`render_answer` never produces a link.** `agents/chat/rendering.py:598-609`: "Never
   creates a link from bare text — no `<a>` is ever produced here, from a URL or otherwise."
   A URL the model types is inert text. **Consequence, §4.3:** the deep links the owner asked
   for are rendered by the platform from tool-result data, not by the model from prose.
3. **`is_admin` is True for everybody on an open box** (`identity/access.py:109-138`,
   `:116-119`). The panel is admin-gated, so on a household box every viewer sees it — exactly
   as every viewer already sees Models, Library and Chat in the settings sidebar
   (`foundation/settings_area.py:26-30`).
4. **`Agent.tool_keys` cannot hold a mutating tool.** `grantable_tools` filters on
   `spec.mutates` and a row naming a mutating key is refused (`docs/EXTENDING.md:66-71`,
   `:170-172`; the `rag.ingest` precedent at `agents/defaults.py:350-353`). Guide-only is
   therefore **structural**, not merely intended — §5.5 pins it anyway.

---

## 2. Owner rulings, restated

Two of these are OWNER RULINGS and are restated **verbatim** from the brief, as required.

> **1. GUIDE-ONLY. The assistant explains, answers, and deep-links; it NEVER mutates
> settings. No mutating tools; the tool registry already refuses mutates=True grants — the
> spec pins it.**

> **2. SETTINGS-ONLY surface. A persistent panel across settings screens; the assistant is
> NOT in the main chat's agent picker, and its conversations do NOT appear in the main chat
> sidebar or /chat/all/.**

3. **Design defaults the owner accepted:** the assistant sees LIVE current values (read-only
   tools); each admin gets ONE persistent assistant conversation with a "start over" control.
4. **Sustainability, pressed twice, and therefore reviewer-verifiable rather than prose:**
   (a) help content lives in code, and drift-guard tests FAIL THE SUITE when a settings page
   changes without its help card or a card cites a dead anchor (§10.1); (b) an "adding a
   settings page" recipe binds future authors (§13.1); (c) the phase ends with an ADR (§13.3).
5. **No duplication, no needless complexity.** Every mechanism below composes with a named
   existing seam, cited `file:line`.

### 2.1 Goals

- An administrator can ask, in words, "how do I make the library admin-only?" from any
  settings page and get a grounded answer plus a working link that jumps to that control and
  highlights it — **on Identity & security, which is where that control lives** (§14 done-when
  1 spells the criterion out; the page the question names is not the page that owns it, and an
  answer that says "Library" is a wrong answer).
- The answer reflects **this box** — its posture, its toggles — not a generic manual. On an
  open box, the honest answer to that same question is that there is nobody to make the
  library admin-only *for* (§14 done-when 1b).
- A future author who adds a settings page cannot ship it without help content and anchors:
  the suite goes red.
- Nothing the assistant can do changes a setting.

### 2.2 Non-goals

- **Not an act-mode.** No mutating tool, no "do it for me" button, no confirmation flow
  (deferred, §15.2).
- **Not a second chat surface.** No sidebar, no thread list, no sharing, no attachments, no
  agent picker, no model picker, no workstreams (§6.4).
- **Not a documentation site.** Help cards describe the settings this box HAS; they are not
  a manual for features it does not.
- **Not a retrieval leg.** v1 injects nothing from the library and stores no derived index
  (deferred, §15.1).
- **Not for non-admins.** No member-facing variant (deferred, §15.3).

---

## 3. The help-card registry and its content hash

### 3.1 `foundation/settings_help.py` — pure, and that is load-bearing

A new module, **pure** in the rule-1 sense: no Django import of any kind, no database, no
import of any non-pure module. That is not a style preference. The tool runners in §5 live in
`agents/` and must read this table; a module that imported `django.urls` or `identity.access`
(as `foundation/settings_area.py:45-50` does) would make that read a cross-column import of a
non-pure module. Pure, it is importable by any column exactly as `foundation/format.py` and
`foundation/files.py` already are — the only two foundation modules any other column imports
today (verified: `tools/rag/ingest.py:101-102`, `tools/vision/models.py:22`,
`models/registry/models.py:23`, and nine more, every one of them `foundation.format` or
`foundation.files`).

```python
# foundation/settings_help.py -- PURE. No Django, no DB, no non-pure import.

EVERYONE = "everyone"
ADMIN = "admin"
ACCOUNTS_ADMIN = "accounts-admin"


@dataclass(frozen=True)
class HelpField:
    """One control on a settings page, as the assistant describes it."""
    name: str        # what the page calls it, verbatim
    anchor: str      # the id= on that control's own section, in that page's template
    meaning: str     # what it is, in one or two sentences
    effects: str     # what changes on this box when it changes


@dataclass(frozen=True)
class HelpCard:
    """One settings page, as the assistant describes it."""
    route_name: str              # the SAME url_name `SETTINGS_GROUPS` names
    title: str                   # help title; may be longer than the sidebar label
    gate: str                    # EVERYONE / ADMIN / ACCOUNTS_ADMIN -- data, not a filter
    purpose: str                 # why this page exists, in two or three sentences
    fields: tuple[HelpField, ...]


CARDS: tuple[HelpCard, ...] = (...)   # one per SETTINGS_GROUPS entry, in the same order
```

`EVERYONE`/`ADMIN`/`ACCOUNTS_ADMIN` **move here** and `foundation/settings_area.py` imports
them (an intra-column import, one line, replacing its own `:52-54`). One definition, so
"admin" cannot come to mean two things; author decision 1.

`gate` is carried as **data the model reads**, never as a filter this module applies. The
gate-evaluation logic (`_may_see`, `foundation/settings_area.py:113`) stays where it is and is
not duplicated here — §5.2 says why the injected index is not gate-filtered at all.

Three accessors, all pure: `card_for(route_name)`, `card_routes()` (a `frozenset`), and
`page_choices()` (the ordered `tuple` of route names the tool schema enumerates, §5.2).

`HelpCard.gate` has **no runtime consumer in v1**, and that is worth saying so a reviewer does
not go hunting for one: drift assertion 1 (§10.1) is its only reader, and §15.3 names the
future one. It is carried because a card that did not say who may open its page would be
help text with a hole in it.

**A card describes the controls its own page renders, and no others.** The drift tests cannot
catch a card that describes a control living on a different page — assertion 3 only asks
whether the anchor exists on the page the card names — so this is a rule the recipe states
(§13.1) rather than a test. The worked case is the one this feature is judged by:
`library_posture` is an `IdentitySettings` column (`identity/models.py:76`) edited only on
**Identity & security** (`identity/forms.py:47`, `identity/views.py:344`;
`identity/access.py:346`: "`library_posture` is only EDITABLE on the enterprise page"), so the
`identity-settings` card carries a `HelpField` for it and the `rag-settings` card **must not**
— that page owns the seven library forms (`tools/rag/models.py:441-513`), none of which is a
posture. A `rag-settings` card that claimed it would send every "make the library admin-only"
answer to the wrong page, with a green suite.

**And a `HelpField` whose control only renders in some postures says so in `meaning`.**
Assertion 3 renders **one** posture and cannot see the others, so this is the second thing no
test catches. The same field is the worked case: `identity/templates/identity/settings.html:
120-135` branches on `posture == "personal"` only, so library posture is a real select on an
open **and** an enterprise box and a **hidden input** with an explanation on a personal one
(`:122-125`). A card that described it flatly would be wrong in one posture out of three and
right-looking in every test run. Deep links are unaffected either way — the `.field` wrapper
that carries the anchor renders in all three postures and only its contents branch — so this
is a rule about what the card **says**, never about whether the link lands.

### 3.2 Where the content lives, and the one duplication this accepts

The cards live in **one central table beside the settings area's own table**, not scattered
one-per-column. The owner's requirement is "help content lives in code beside the settings it
describes"; this table is beside `SETTINGS_GROUPS`, which is the module that already decides
what the settings area IS. A per-column card registry would need a third registration seam
plus an `AppConfig.ready()` line in every column, for data that is pure text and no column's
models. Author decision 2.

The cost is honest and named: `route_name`, `title`-versus-`label` and `gate` are stated in
two tables. That is the identical shape `SETTINGS_GROUPS` and `_settings.html` already have,
and it is closed the identical way — the drift test in §10.1, which asserts the two tables
name **exactly** the same routes with **equal** gates.

### 3.3 The content hash

```python
CONTENT_HASH: str = _content_hash()   # 12 hex chars, computed at import
```

Computed with `hashlib.sha256` over a **deterministic canonical serialization** of `CARDS` —
the route names in table order, and for each card its title, gate, purpose and every field's
name/anchor/meaning/effects, joined with a separator that cannot appear in a route name.
Never Python's built-in `hash()`, which is salted per process and would answer differently on
every boot. Truncated to 12 hex characters: this is a change detector, not a signature.

**What it is for, plainly.** The owner asked for a hash so a stored context repository could
be caught out of date. This design has **no stored repository** — the cards are the code, and
code cannot be stale relative to itself. The hash makes that trivially true and then does
three real jobs: (a) the assistant can cite its context version, so an answer in a transcript
can be tied to the content that produced it; (b) it is the key any future cached or derived
artifact compares against before trusting itself (§15.1 names the first such artifact); (c) a
test pins that it CHANGES when any card changes, which is what makes (a) and (b) worth
anything. Author decision 3.

It reaches the model twice: appended to the `settings.card` tool description
(`…settings context version: <hash>`) and returned in `ToolResult.data["content_hash"]` by
both tools.

---

## 4. Anchors and the highlight

### 4.1 Anchors

Every section a card names carries a stable `id=` on the settings page that owns it,
generalizing the console's three (§1.2) to the whole area. The rule for an author:

- The id names the **control's own section wrapper**, not a label and not an input.
- It is stable vocabulary, not a slugified heading: renaming a heading must not silently
  break a link, and the drift test in §10.1 turns an actual rename red.
- A page whose template has mutually exclusive branches (the console does) carries the id in
  **each** branch, exactly as `console.html` already does at `:1007`/`:1067`. The drift test
  renders the page and asserts on the body, so a branch that forgot one fails.

### 4.2 The highlight

A pure CSS `:target` rule. **Zero new JavaScript**, per the brief.

It lives in **`foundation/templates/_shell.html`**, beside `.settings-layout` and the rest of
the settings layout's CSS (`foundation/templates/_shell.html:306-370`) — not in
`_settings.html`'s `extra_style` block. The reason is a defect this spec found rather than
inherited: `foundation/setup/templates/setup/index.html:16` overrides `extra_style` **without
`{{ block.super }}`**, so a rule added to `_settings.html`'s block would silently not apply on
Install guides. (Ten other settings leaves do write `block.super`;
`models/registry/templates/inference/base.html` overrides the block not at all and would have
been fine.) Putting the rule where the settings layout's own CSS already lives sidesteps the
shadowing entirely and needs no edit to any leaf page. Author decision 4; the `block.super`
gap itself is §17 flag 1.

```css
  /* The settings area's deep-link highlight. `:target` only -- the browser
     scrolls and this says which thing it scrolled to. No script, no class
     toggling, no state.

     NOT `background: var(--panel)`: several settings sections are ALREADY
     painted `--panel` (`setup/index.html:19-21`'s `.card`, `_settings.html`'s
     own `.msg`), so a `--panel` highlight is invisible on exactly the pages
     a person is most likely to be sent to. The ring plus its own tinted
     wash is what makes the drill in §14 ("highlighted") true on every
     settings page rather than most of them. */
  .settings-main :target {
    background: var(--target-wash);
    box-shadow: 0 0 0 2px var(--accent);
    border-radius: 6px;
    scroll-margin-top: 1rem;
  }
```

**`--target-wash` is declared once, in the token blocks, and not in the rule above** — the
snippet uses it and does not define it, so an implementer reading this section does not write
the declaration twice:

```css
  /* _shell.html, in BOTH token blocks: the light `:root` (`:89-96`,
     `--accent` at `:95`) and the dark one (`:149-160`, `--accent` at
     `:155`), so the wash follows whichever theme is in force instead of
     hard-coding a colour that is right in only one of them. */
  --target-wash: color-mix(in srgb, var(--accent) 12%, var(--panel));
```

`color-mix` is already in this tree and for this exact reason —
`agents/chat/templates/chat/base.html:715-720` uses it over `--text` so a shadow "follows the
theme instead" of being a fixed colour. Author decision 24.

`scroll-margin-top` is part of the feature, not decoration: without it the targeted section
lands flush against the top of the viewport under the app bar.

The implementer confirms this by eye on **Install guides** — the page whose sections are
already `--panel` — not only on the page the flagship drill lands on.

### 4.3 The link the operator actually clicks

**The model never produces a link.** `agents/chat/rendering.py:598-609` states it outright:
`render_answer` escapes first and emits no `<a>` from a URL or otherwise. That is a deliberate
posture — an `<a href>` a model can be talked into writing is a phishing primitive — and this
spec does not weaken it.

So the links are the **platform's**, built from the platform's own table:

1. `settings.card` returns, in `ToolResult.data`, a `links` list of
   `{"route": …, "anchor": …, "label": …}` triples — **not** URLs. Every value comes from
   `CARDS`.
2. The turn's tool result is stored on the tool `Turn` row, as every tool result already is.
3. The panel's context builder (§6.2) reads the **most recent tool turn's** `data["links"]`,
   drops any entry whose `route` is not in `card_routes()` or whose `anchor` is not one of
   that card's own `HelpField.anchor` values, and calls `django.urls.reverse(route)` for the
   survivors, appending `?assistant=1#<anchor>`.
4. The panel template renders those as real anchors, in a small "Jump to" strip beneath the
   answer.

Nothing that is not already in the code-side table can become a link on that page. This is a
whitelist by construction, and it is the same property §9 needs for the injection posture.
Author decision 5.

`?assistant=1` is the panel's own open-state parameter (§6.3): following a link from the
assistant lands on the target page with the panel still open, which is what "it should persist
across screens" means for a zero-JS surface.

---

## 5. The agent and its two tools

### 5.1 The catalogue entry

One new `AgentSpec` in `agents/defaults.py::DEFAULT_AGENTS`, slug **`settings-helper`**:

```python
    AgentSpec(
        slug="settings-helper",
        name="Settings assistant",
        description="Explains this box's settings and points at the exact control.",
        system_prompt=(...),                     # doctrine only -- see below
        tool_keys=("settings.card", "settings.overview", "models.status"),
        # llm_role defaults to CHAT_CONVERSE_ROLE (agents/defaults.py:97);
        # max_steps defaults to MAX_STEPS_DEFAULT (:98); as_tool stays False (:99).
    ),
```

`as_tool=False` is deliberate: an `agent.settings-helper` tool key would put this agent inside
other agents' reach, which is the opposite of settings-only.

**No auto-row.** Ruling 2 (`agents/defaults.py:14-18`) stands: the row appears when somebody
installs it. The panel renders the offer (§6.5).

**The system prompt carries doctrine and nothing else** — it never lists the pages. Doctrine
is: you are a guide, you never change a setting and cannot; call `settings.card` for the page
in question rather than answering from memory; call `settings.overview` when the answer
depends on how this box is configured; when you do not know, say so and name the page you
would look at; quote the page's own words for a control's name; never invent a setting, a
control or a page. It is the ROW's text, so an operator may tune it, and
`install_defaults --reset settings-helper` restores it.

### 5.2 `settings.card`

```python
SETTINGS_CARD = ToolSpec(
    key="settings.card",
    label="Settings page help",
    description=(
        "Explain ONE settings page on this box: what it is for, every control on it, what "
        "each control means and what changes when it changes, and the anchor to link to. "
        "Call this before answering any question about a settings page rather than "
        "answering from memory.\n"
        + _PAGE_INDEX          # "<route> — <title>" per card, built from CARDS at import
        + f"\nSettings context version: {CONTENT_HASH}."
    ),
    params=(Param("page", "choice", "Settings page", required=True,
                  choices=page_choices(),
                  description="Which page to explain."),),
    roles=(),
    runner="agents.settings_tools.run_card",
)
```

**The index rides the tool schema, not the prompt.** `Param(kind="choice", choices=…)` becomes
an `enum` in the JSON schema both wire adapters build
(`agents/contracts/toolschema.py:69-70`), and the description carries one `route — title` line
per page. Both are derived from `CARDS` **at import**, so they cannot go stale, and the
platform's one prompt builder gets no settings-shaped special case at all:
**`agents/runtime/prompt.py` is not modified by this phase.** The alternative the brief hinted
at — a contributor appended in `build_messages` via `append_paragraph` (`:654`, the
`time_aware_now_line` shape at `:679`) — would need a condition on the agent's slug inside a
function every agent on the platform goes through, to inject a paragraph that a tool schema
already carries. The third alternative, putting the index in the agent ROW's `system_prompt`,
is the one the owner's staleness worry rules out: a row is a copy, and a copy goes stale until
somebody re-runs `install_defaults --reset`. Author decision 6; brief deviation, §18.

An enum also means a page name the model invents comes back as a `param_error` — "the one
failure class worth handing back for a retry" (`docs/EXTENDING.md:110`) — rather than as a
confident answer about a page that does not exist.

`run_card(args, ctx)` calls `validate_tool_args(SETTINGS_CARD, args)` first (the recipe's own
order, `models/registry/tools.py:63`), then returns the card as `text` (a compact rendering a
model reads well) and as `data` (`{"page": …, "content_hash": …, "links": [...]}`, §4.3). It
reads `foundation.settings_help` **only** — no database, no request, no live value. Its cost
is constant in the number of settings pages, which is the point of an index-plus-on-demand
shape and is binding per the brief.

### 5.3 `settings.overview`

```python
SETTINGS_OVERVIEW = ToolSpec(
    key="settings.overview",
    label="This box's current settings",
    description=(
        "Report how THIS box is configured right now: its posture, whether administrators "
        "may read other people's content, the session idle timeout, the library posture, "
        "and whether conversations are told the current date and time. Read-only. Call it "
        "when the answer depends on how this box is set up, not on what a page could do."
    ),
    params=(), roles=(),
    runner="agents.settings_tools.run_overview",
)
```

Values, and the legal seam each comes through:

| Value | Source | Legal because |
|---|---|---|
| `posture` | `identity.access.settings_row().posture` | `identity.access` is a sanctioned seam (`test_import_law.py:606-608`) |
| `library_posture` | same row (`identity/models.py:76`) | same |
| `admin_sees_content` | same row (`identity/models.py:90`) | same |
| `session_idle_minutes` | same row (`identity/models.py:95`) | same |
| `time_aware` | `agents.models.ChatSettings.get_solo()` | in-column |
| assistant install state | the agent row this panel already resolved | in-column |

**One row read**, not one per value: `identity.access.settings_row()` is fetched once and
every answer read off that instance — the per-call reuse norm `agents/entitlements.py:30-36`
states.

**The runner refuses a non-admin, before it reads anything:**

```python
def run_overview(args: dict, ctx: ToolContext) -> ToolResult:
    validate_tool_args(SETTINGS_OVERVIEW, args)     # declares no params: any arg is a bug
    row = settings_row()
    if not is_admin(ctx.principal, settings_row=row):
        raise ToolRefused(
            "This box's configuration is administrator-only. Ask an administrator, or "
            "open Settings if you are one."
        )
```

`ToolRefused` (`agents/contracts/tools.py:50`) classifies as `refused` — "no retry"
(`docs/EXTENDING.md:111`) — which is the honest ending for a question no rephrasing fixes.

**Why the surface's gate is not enough.** `register_tool` puts a spec in the platform-wide
registry (`agents/apps.py:81-95`), and grants are per-**agent** rows plus the Tool access page
(`chat-tool-entitlements`, `identity/routes.py:102`). Nothing stops an operator adding
`settings.overview` to the general assistant's `tool_keys` — at which point a **member** on an
accounts-on box, chatting on `/chat/`, learns this box's posture, library posture, whether
administrators read other people's content, and the session idle timeout. Read-only is not
admin-only, and the owner's "it should only be available for admins" is a ruling about the
*information*, not only about the panel. The house doctrine is already this: "every layer
keeps its own defensive call" (`agents/chat/templates/chat/_composer.html:56-62`, quoting
`agents.contracts.attachments.placement_is_valid`), and `ToolContext` carries the acting
principal precisely so a runner can ask (`agents/runtime/loop.py:432-446`). The route class is
the *surface's* gate; this is the *tool's*. Author decision 23.

**`settings.card` deliberately has no such check, and the difference is the point.** Its
content is platform-authored help text about pages the reader may or may not be able to open —
the same text this repository is about to publish in `docs/EXTENDING.md` — and it reports no
value of any kind about this box. Gating it would be security theatre that also broke the
non-admin variant §15.3 defers. Two tools that look alike are gated differently on purpose,
and that is said out loud here so a reviewer does not read the asymmetry as an oversight.

Role bindings are **deliberately absent** — `models.status` already reports exactly that
(`models/registry/tools.py:37-49`) and the agent holds it. Two tools answering the same
question is the duplication the owner's ruling 5 forbids.

### 5.4 What the overview does NOT report, and why

The library's own live caps — upload cap, page cap, media cap, retrieval top-k, score floor
(`tools/rag/models.py:441-513`) — are **not** in v1. They cannot be: they live on
`tools.rag.models.RagSettings`, and **`agents/` may not import `tools/` at all** (ADR
0015:112-120, swept by `foundation/ops/tests/test_import_law.py:308`). Three ways exist to
cross that seam and all three are refused for v1:

- a **registry of dotted-path readers**, one per column, resolved at call time (the shape
  `docs/EXTENDING.md:190-230` already documents for workstream panels) — a whole new
  registration seam for one contributor;
- a **second tool in `tools/rag`** granted to this agent — real, cheap, and the platform's
  existing answer, but it is scope in another column for a value the assistant does not need
  to recite;
- **moving the values**, which is not on the table.

Guide-only is what makes the refusal safe: the assistant's job is to say *"the upload cap is
on the Library page, under Uploads"* and link there. Reciting the number is not the feature.
Author decision 7, deferral §15.4, flagged §17 flag 2.

### 5.5 Guide-only, pinned

Registration lives in `agents/apps.py::ready()` beside the flow tool
(`agents/apps.py:87-95`), imports `agents.settings_tools` (which imports nothing heavy at
module scope), and both guard lists gain the module **in the same commit** — `TOOL_MODULES`
(`foundation/ops/tests/test_column_boundaries.py:185-189`) and `_REGISTRATION_MODULES`
(`:217-231`), per `docs/EXTENDING.md:125-164`.

Neither spec declares `mutates`, so both default to `False`. A test asserts that **every key
in the `settings-helper` spec's `tool_keys` resolves to a registered spec with
`mutates=False`** — belt over the structural braces of §1.4 fact 4, because the ruling is the
owner's and a structural guarantee nobody restates is a guarantee somebody removes.

---

## 6. The panel

### 6.1 What it is

An admin-gated, collapsed-by-default `<details>` in `foundation/templates/_settings.html`,
rendered on **every** settings page, below the content column. Inside it: the last turns of
this administrator's own assistant conversation, the "Jump to" links from the most recent tool
result (§4.3), the shared composer, and a "Start over" control.

### 6.2 How its context gets there

A **context processor** registered by `agents.chat`:
`agents/chat/context_processors.py::settings_assistant(request)`.

Its guard is **two clauses, in this order**, and both of them run before any row is read:

```python
    if getattr(request, "resolver_match", None) is None or \
            request.resolver_match.url_name not in card_routes():
        return {}
    row = settings_row_for(request)                       # identity/request.py:21
    principal = principal_for_request(request)
    if not is_admin(principal, settings_row=row):         # identity/access.py:109
        return {}
```

`card_routes()` is the pure frozenset from §3.1. On every page in the box that is not a
settings page the first clause costs one attribute read and one set membership test and
**zero queries**, which is the precise objection `foundation/settings_area.py:12-20` raised
against a context processor for the sidebar.

**The second clause is a gate, not an optimisation, and it is the repo's own named defect
class.** The template's `{% if identity_is_admin %}` (§8.2) decides what *renders*; it cannot
decide what was *built*, and a gate that lives one layer above the work it guards is
render-vs-gate inverted. The proving case is real: `setup-index` ("Install guides") is a
settings-area entry gated `EVERYONE` (`foundation/settings_area.py:98`) with route class
**"P" — public** (`identity/routes.py:254`), and `foundation/setup/templates/setup/index.html:1`
extends `_settings.html`. So on an accounts-on box an **anonymous** visitor renders a settings
page, passes the `card_routes()` test (Install guides has a card — assertion 1 requires it),
and, with a hand-typed `?assistant=1`, would otherwise have an agent row, a conversation
lookup and six turns resolved for them before a `{% if %}` threw it all away. It is not a leak
today (`visible_conversations(ANONYMOUS)` returns that principal's own nothing) — it is a leak
*shape*, on the box's one public settings page, and every field a later phase adds to this
context would inherit it. **No panel context key is produced, and no conversation or turn is
read, for a principal the panel will not render for.**

Both seams the clause uses are sanctioned (`identity.access`, `identity.request` —
`foundation/ops/tests/test_import_law.py:606-608`), and the row is the one
`IdentityGateMiddleware` already stashed, so the check costs nothing a rendered settings page
had not already paid. Author decision 21.

The two existing processors — `identity.context_processors.identity` and
`models.registry.context_processors.availability` — establish that a processor is a legitimate
seam here; what was never legitimate was one that costs a query on every page.

The alternatives are worse, and one is impossible: per-view context would need an edit in
every column that owns a settings page, and `identity/` — which owns four of the eleven — may
not import `agents/` **at all** (ADR 0015 rule 4, swept at
`foundation/ops/tests/test_import_law.py:516`). A custom template tag would work, but the
repository has **no** `templatetags` package anywhere (verified: none exists), so it would be
a brand-new mechanism where an existing one fits. Author decision 8.

The processor lives in `agents/chat` because it reads `Conversation`/`Turn` rows, and it reads
them through `agents/visibility.py`, never through a manager
(`agents/visibility.py:765-771`). It is registered in
`config/settings.py::TEMPLATES["OPTIONS"]["context_processors"]`, beside the two that are
already there — the composition root is the one place that already names every column.

### 6.3 What it renders, and when it costs anything

The panel has two states and the URL says which:

- **Collapsed** (no `?assistant=1`): summary line only. The processor resolves the agent row
  and returns — **one query**, and no conversation or turn read at all. A `<details>` renders
  its children into the DOM even when closed, so not rendering them is the only honest way to
  not pay for them.
- **Open** (`?assistant=1`): `<details open>`, plus the transcript. The processor resolves the
  conversation (`visible_conversations(principal).filter(agent__slug=…,
  archived_at__isnull=True).first()` — a `.filter()` on the ONE gate, the identical shape
  `agents/chat/sidebar.py:161-162` uses) and its last `ASSISTANT_PANEL_TURNS = 6` turns.

`?assistant=1` is set by the ask/reset redirects and by every link the assistant emits (§4.3).
So a session of asking and following links keeps the panel open the whole way, and a plain
navigation to a settings page starts collapsed. Author decision 9.

### 6.4 The transcript fragment, and what is deliberately not reused

The panel renders **its own compact transcript fragment**, not `chat/_turn_block.html`.

It reuses the thing that matters: `agents.chat.rendering.render_answer` (`:598`), so a model's
`**bold**`, lists and code spans render identically here and in `/chat/`, escaped first.

It does not reuse the card markup, and this is a decision rather than an oversight. The chat
turn block renders tool cards with arguments, thumbnails and citations, artifact image and
file strips, attachment rows and five turn states. A settings panel needs none of it — and
because CSS ownership is enforced ("a selector's home is the deepest template that is an
ancestor of every template that uses it", `foundation/ops/tests/test_css_ownership.py:1-22`),
reusing it would drag roughly twenty `.turn*`/`.tool*` selectors out of `chat/base.html` and
into the global `_shell.html` so they could render on a page that then hides most of them.
Four states, rendered in about fifteen lines of the panel's own template: queued/running →
"Thinking…"; done → `render_answer`; failed/cancelled → the turn's own honest error line.
Author decision 10.

### 6.5 The composer, and the CSS this actually costs

The panel includes **the one composer**, `agents/chat/templates/chat/_composer.html`, in
`turn` mode:

```django
{% include "chat/_composer.html" with composer_mode="turn"
   composer_action=assistant_ask_url composer_form_id="assistant-form"
   composer_next=request.get_full_path
   composer_placeholder="Ask about these settings" composer_button="Ask"
   composer_show_model_picker=False composer_workstream=None %}
```

`composer_mode="turn"` gives a `required` textarea and no agent picker
(`_composer.html:99-114`). `may_attach_files` is absent from the panel's context, so the
attach door and the drag-drop fragment do not render (`:84-86`, `:128`). The Enter-to-send
fragment (`:129`) does render — an existing sanctioned script, and the right behaviour here.

**`composer_next` is one new optional parameter on the shared composer**, and it is the only
change this phase makes to that fragment: when passed, the form emits
`<input type="hidden" name="next" value="…">`. It is needed because
`agents/chat/service.py::validated_next_url` (`:662`, `:693`) reads `request.POST.get("next",
"").strip()`
and the composer emits no such field, and it is the right shape rather than a workaround —
that fragment already emits exactly one other hidden routing field on exactly this basis
(`composer_workstream`, `_composer.html:79-83`), and its own rule is "PARAMETERIZED ONLY WHERE
THE SURFACES GENUINELY DIFFER" (`:16-17`). Every existing include site omits it and is
byte-identical to today. The alternative — putting `next` in the action URL's query string and
teaching the view to read `request.GET` — would fork the one `next` reader the codebase has.

**The price, stated plainly.** `_composer.html` uses `.composer-card`, `.composer-textarea`,
`.composer-toolbar`, `.composer-toolbar-left`, `.composer-toolbar-right` and `.agent-picker`,
all defined in `chat/base.html`, plus the structural `.composer-card > form { display:
contents; }` rule that `_composer.html:68-74` explicitly depends on. `chat/base.html` and
`foundation/templates/_settings.html` both extend `_shell.html` (verified:
`chat/base.html:1`), so the deepest common ancestor is `_shell.html` and those six selectors
plus the structural rule **move there**. Skipping it reproduces the one live rendering defect
the CSS-ownership gate was written for — `/chat/all/`'s preview pane rendering every turn card
completely unstyled (`foundation/ops/tests/test_css_ownership.py:34-52`) — this time as an
unstyled composer on eleven settings pages.

**The placement *rule* demands the move; no existing *gate* can see this shape, so this phase
writes the check itself.** `test_css_ownership.py` walks
`_CHAT_TEMPLATES = agents/chat/templates/chat` only (`:107`) and its page set is the pages
that extend `chat/base.html`; `_settings.html` sits outside that directory entirely, which
that module says in its own words (`:309-313`: "The gate above is scoped to
`agents/chat/templates/chat/` and cannot see it") and which `_settings.html:55-65` restates
from the other side. The one settings-side check,
`test_no_settings_page_retypes_a_rule_settings_html_already_owns` (`:367-405`), compares a
leaf page's `extra_style` against `_settings.html`'s and never looks at `chat/base.html`. So
leaving the selectors where they are would be a real defect with **no red test**, and this
spec must not claim a guard it does not have: §10.3 carries this phase's own assertion that
the six selectors and the `.composer-card > form` rule live in `_shell.html` and appear in no
`chat/` block.

The move is byte-identical rules to a higher home — no pixel changes — and it lands in this
phase, after the hygiene sweep. Author
decision 11; the alternative (a second, panel-owned composer) is refused because the owner has
already ruled on exactly that shape: *"There shouldn't be two different code bases. Both
should serve both purposes"* (`_composer.html:9-11`).

Exactly one composer renders per settings page, so `id="composer-text"`'s accessible-name
invariant (`_composer.html:87-97`) holds unchanged. The panel introduces **no `<h1>`** —
`foundation/tests/test_page_names.py`'s one-name-per-page rule sweeps every settings page and
the panel must not add a second page name.

When the assistant is **not installed**, the panel renders the consent offer instead of the
composer: one sentence and one POST button to `chat-default-install` with
`kind=agent&slug=settings-helper` and a `next` field carrying the current page.
`agents/chat/views/defaults.py:54-55` currently redirects to `chat-index` unconditionally; it
gains a `validated_next_url(request) or reverse("chat-index")` on both of its two return paths
— `validated_next_url` already exists at `agents/chat/service.py:662` and is already used by
two other views. One three-line change beats a fourth route. Author decision 12.

### 6.6 The ask flow

`POST /settings/assistant/ask/` (`settings-assistant-ask`), fields `text` and `next`:

1. `principal_for_request(request)`; the route is class S, so the middleware has already
   refused a non-admin (§8). `next` arrives as the hidden field the composer now emits
   (§6.5) and is read through the one existing reader, `validated_next_url` (`:662`).
2. Resolve the agent through
   `visible_agents(principal).filter(slug__iexact="settings-helper", enabled=True).first()`.
   Absent → 400 naming the offer.
3. Resolve **or create** this principal's conversation: the §6.3 filter, else
   `create_conversation(principal, agent)` (`agents/visibility.py:761`), which stamps
   `owner_fields` and no `workstream` (§7.1.2 depends on that null). On an accounts-on box
   that is one conversation per administrator, owned, and invisible to every other principal
   by the ordinary gate.

   **On an open box it is one conversation for the whole box, deliberately, and that is worth
   knowing.** `visible_conversations` short-circuits on `sees_all_content` and returns `.all()`
   (`agents/visibility.py:93-95`), and every viewer there is the same `OPEN_PRINCIPAL`
   (`identity/access.py:116-119`) — so a household box has one assistant thread that everyone
   at the keyboard continues, reads the history of, and can "start over" for all of them. That
   is the same thing being true of `is_admin` that §8.2 already states, followed through to
   the transcript; it is correct for a box with no accounts, and §17 flag 3 says it in the
   owner's terms rather than leaving it to be discovered.
4. `start_turn(conversation, text, actor=principal)` (`agents/chat/service.py:174`). No
   `connection`, no `files`, no `placement`: this surface has no picker and no attach door.
5. **Answer.** For an XHR (`X-Requested-With: XMLHttpRequest`, the same five-line read as
   `agents/chat/views/turns.py:44-50`): the rendered panel fragment, `200`, `text/html`. For
   anything else: `redirect(next + "?assistant=1")`, `next` through `validated_next_url` and
   falling back to `settings-index`.

   **No `?pending=<turn_id>` on this surface, deliberately.** On the chat page that parameter
   is the **hand-off from a script-free POST to a script-enabled reload**: "`data-pending-turn`
   on the wrapper is the poller's bootstrap value — the no-JS redirect's `?pending=<turn_id>`
   answer to 'where did my message go', read back out here for a JS-enabled load to pick up"
   (`agents/chat/templates/chat/conversation.html:29-33`; the poller itself swaps by
   `data-poll-block`). The panel needs no hand-off: it re-renders its whole recent transcript
   on every render, so the queued turn is already on the page, and the "is anything still
   running" marker (`data-assistant-pending`, §6.7) is derived server-side from those rows'
   own states. Carrying a turn id nothing reads would be cargo. Author decision 9; brief
   deviation, §18.
6. A refusal from `start_turn` (blank text, queue down) re-renders or returns the same
   fragment with the message in the panel's own error slot — never a bare fragment for a
   non-XHR caller, the rule `agents/chat/views/turns.py:64-67` states.

`POST /settings/assistant/reset/` (`settings-assistant-reset`) — "Start over":
`set_conversation_archived(principal, conversation, archived=True)`
(`agents/visibility.py:411`), then redirect back with `?assistant=1`. The next ask creates a
fresh one. Nothing is deleted: the old thread is archived, and §7 keeps it out of the archive
listing too.

`GET /settings/assistant/panel/` (`settings-assistant-panel`) returns the same fragment the
inline panel renders, from the same template and the same context builder — the
`_turn_block.html` precedent, where "this page's inline render and `turn_status`'s poll body
come out of the SAME loop over the SAME fragment"
(`agents/chat/templates/chat/conversation.html:12-14`). It takes `?next=` only to build its
links; it writes nothing.

### 6.7 JS-off, and the one script

**With no script at all**, everything works: the form is a plain POST, the answer is a
redirect carrying `?assistant=1`, the panel comes back open with the queued turn showing
"Thinking…", and a reload shows the answer. That is the same contract
`agents/chat/templates/chat/conversation.html:6-9` makes and the same POST/redirect shape
`turn_create` already implements (minus the turn id it does not need, §6.6 step 5).

**One sanctioned script**, roughly forty lines, in the panel fragment:

- intercept the composer's `submit`, POST it with `fetch` and `X-Requested-With`, and replace
  the panel's inner container with the HTML that comes back;
- while that HTML carries `data-assistant-pending`, re-`GET` `settings-assistant-panel` every
  `POLL_INTERVAL_MS` and replace again; stop on the first response without the marker, after
  `MAX_TRANSPORT_RETRIES` transport failures, or at `MAX_POLL_DURATION_MS`.

The three constants are the ones already declared once in Python
(`agents/chat/service.py:93-95`) and handed to the template, never re-typed in JS —
`agents/chat/templates/chat/conversation.html:396-407` states that rule and this follows it.

It is **not** a copy of the conversation poller
(`agents/chat/templates/chat/conversation.html:391-870`), which swaps individual turn cards by
`data-poll-block` id, de-duplicates tool cards, reads two different refusal body shapes and
handles attachment pending state. This one replaces one container with server-rendered HTML
and stops when the server stops saying "pending".

The submit interception earns its place: a settings page can have a half-filled form on it,
and a plain POST navigates away and discards it. With the script, asking a question never
navigates. Without it, it does — and that is the honest JS-off cost, stated here and in the
recipe. Author decision 13.

**Persistence is server-side only** — `Conversation`/`Turn` rows. No `localStorage`, no
`sessionStorage`, no client-side draft cache. The codebase tests **against** localStorage
appearing in a submit handler (`tools/vision/tests/test_views_create.py:1000-1004`), and the
panel's own test asserts the string does not appear in its script.

---

## 7. Keeping it off the chat surface

Owner ruling 2 needs three things to be true: the assistant is not in the `/chat/` agent
picker, its conversations are not in the chat sidebar, and they are not in `/chat/all/`.

### 7.1 The mechanism, and why it is the smallest honest one

```python
# agents/defaults.py, beside the AgentSpec it names
SETTINGS_SURFACE_SLUGS: frozenset[str] = frozenset({"settings-helper"})
```

```python
# agents/visibility.py -- two thin wrappers on the ONE gate, for ONE surface
def chat_surface_agents(principal):
    return visible_agents(principal).exclude(slug__in=SETTINGS_SURFACE_SLUGS)

def chat_surface_conversations(principal, *, settings_row=None):
    return visible_conversations(principal, settings_row=settings_row).exclude(
        agent__slug__in=SETTINGS_SURFACE_SLUGS)
```

Call sites, all one-liners:

| File | Today | Becomes |
|---|---|---|
| `agents/chat/views/conversations.py:156` | `visible_agents(principal)` | `chat_surface_agents(principal)` |
| `agents/chat/views/conversations.py:165` | `installed_agent_slugs(principal)` | the same, minus `SETTINGS_SURFACE_SLUGS` |
| `agents/chat/views/conversations.py:234` | `visible_agents(principal).filter(slug__iexact=…)` | `chat_surface_agents(...)` |
| `agents/chat/views/workstreams.py:401` | `visible_agents(principal)` | `chat_surface_agents(principal)` |
| `agents/chat/sidebar.py:161` | `visible_conversations(...)` | `chat_surface_conversations(...)` |
| `agents/chat/views/all_conversations.py:137` | `visible_conversations(...)` | `chat_surface_conversations(...)` |
| `agents/chat/service.py:493` (`visible_conversation_or_404`) | `get_object_or_404(visible_conversations(principal), pk=…)` | the same over `chat_surface_conversations` — one word, no keyword, and it closes all nine of that helper's call sites at once — §7.1.1 |
| `agents/chat/views/workstreams.py:373` | `visible_conversations(...).filter(workstream_id=…)` | **no change**, and that is load-bearing — §7.1.2 |

Two of those need a word. `sidebar.py:161` and `all_conversations.py:137` each build **one**
`base` queryset and `.filter()` it several times (active list, pinned section, archived count;
rows and preview) precisely because the base call pays its identity reads at call time
(`agents/chat/sidebar.py:153-160`, `agents/chat/views/all_conversations.py:129-136`) — so
excluding at `base` covers every derived list on both pages with one edit each and no extra
query. `conversations.py:165` filters `installed_agent_slugs` so that **two** consumers are
fixed at once: the "Add the default X" offers (which would otherwise offer the settings
assistant on `/chat/` forever, installed or not) and the `nothing_installed` banner (which
would otherwise call a box whose only agent row is the settings assistant "all installed
agents are disabled" — `agents/chat/views/conversations.py:167-173`).

`conversations.py:234` (`_startable_agent`) matters for a reason worth naming: without it, a
hand-crafted POST to `/chat/start/` with `agent=settings-helper` would create a conversation
that then appears on no list at all. Closing the picker without closing the start path leaves
orphans.

### 7.1.1 The row-addressed door, which lists do not close

The five list-shaped call sites above make the assistant invisible on `/chat/`. They do **not**
make it unreachable, and unreachable is what "settings-only surface" claims. Every
row-addressed chat view goes through one function — `agents/chat/service.py::visible_
conversation_or_404` (`:493`), "the first half of every row-addressed chat view, written once
… for the six copies across four modules" (`:474-484`) — and that function asks the **un-**
wrapped gate. So `/chat/c/<uuid>/` renders the assistant's conversation on the full chat
surface: the model picker and the attach door (`chat/_composer.html` in `turn` mode with
`composer_show_model_picker=True`), `turn_create` posting into it (`may_post_to` says yes,
`agents/visibility.py:545`), and the row menu's rename / duplicate / pin / archive / **share**.
§15.5 says a shared assistant conversation is a deferral with "nothing here uses it" — and
`Share.Target.CONVERSATION` is one click away on that page.

That is the identical orphan argument this section already makes for `_startable_agent`, one
route further along. So it gets the identical answer:

**`visible_conversation_or_404` asks `chat_surface_conversations`, unconditionally** — one
word changed inside the function, no keyword and no call-site edits. All **nine** of its call
sites are `/chat/` views (`thread.py:324`, `shares.py:41`, `turns.py:70` and `:414`,
`conversations.py:368`, `:403`, `:436`, `:479`, `:514`), which is what the function's own name
and its docstring already say it is: "the first half of every row-addressed **chat** view,
written once … for the six copies across four modules" (`:474-484`). So all of them answer
**404** on a settings-surface conversation — 404 rather than 403 by that same docstring's rule,
"a 403 on a row-addressed URL confirms the row exists" (`:481-484`).

**Nine is this helper's call sites, not every id-addressed read in the codebase, and the
difference is stated rather than left to be assumed.** Two id-addressed reads do not go
through it and stay un-narrowed:

- `visible_turn` (`agents/visibility.py:222-234`), which filters
  `conversation__in=visible_conversations(principal)` and backs **`chat-turn-status`**
  (`agents/chat/urls.py:61`, class "O", `agents/chat/views/turns.py:329`) — so an assistant
  turn's own poll fragment is still fetchable at `/chat/turns/<id>/` by the principal who owns
  the conversation;
- `agents/workstreams.py:399` (`transcript_for`), an id-addressed transcript read for the
  `tools/rag` seam, which resolves through the same `visible_conversations` gate.

**Neither is a leak and neither is narrowed here.** Both ask the identical read gate, so both
are owner-only, and neither exposes an affordance: a status fragment is not a page, a
transcript read is not a surface, and the panel needs neither (§6.7 polls
`settings-assistant-panel`, its own route). Narrowing them would mean a third and fourth
wrapper for no reachable action. What is claimed here is precise: **every row-addressed chat
view is closed**, not that no code path anywhere can resolve the row by id.

**No `chat_surface_only=False` escape hatch, because nothing needs one.** The panel does not
use this helper at all: §6.3 and §6.6 read through
`visible_conversations(principal).filter(agent__slug=…)` directly, the un-narrowed gate, which
this change does not touch. A keyword defaulting to `True` with no caller passing `False`
would be a knob with no reader — the thing this spec refuses everywhere else — and it would
invite the first author who hits a 404 to flip it rather than ask why. A seventh row-addressed
chat view added later inherits the exclusion by doing nothing. Author decision 22.

The alternative — leave the door open and pin what may happen behind it — was rejected: it
would need a test row per action (render, post, rename, duplicate, pin, archive, share), and
the one that matters most (share) would then be *refused* by a rule written nowhere, while the
other six quietly worked on a surface the owner ruled the assistant off.

### 7.1.2 The seventh call site, safe only by accident — so name it

`agents/chat/views/workstreams.py:373` builds a workstream card's conversation list from
`visible_conversations(...).filter(workstream_id=stream.pk)`. It needs **no change**, and the
reason is worth writing down rather than leaving to luck: §6.6 step 3 calls
`create_conversation(principal, agent)` with **no `workstream`**, and that column "IS STAMPED
ONCE AND NEVER WRITTEN AGAIN" (`agents/visibility.py:777-784`) — no route writes it after
creation and the module exposes no setter. An assistant conversation therefore always carries
`workstream_id IS NULL` and can never appear on a stream card. A future author who threads a
workstream into the ask flow would put it there with nothing complaining, so §10.3's surface
row pins the null directly.

### 7.2 What was rejected

- **A column on `Conversation`** (`surface`, or a boolean). It would need a migration, a write
  path at creation, and back-fill reasoning — to express a fact the `agent` FK already
  carries.
- **Filtering inside `visible_agents`/`visible_conversations` themselves.** Those functions
  answer "may this principal read this row", and the answer here is *yes* — the panel reads
  both. Narrowing the platform's one gate to express a surface preference would make
  `visible_agent_slugs` (`agents/visibility.py:181`) claim an administrator may not run an
  agent they are, at that moment, running.
- **A `surface` field on `AgentSpec`.** One member, `"chat"` as a silent default, the same
  failure mode as the frozenset, one more concept. The frozenset is one line beside the spec
  it names, and §10.3 pins that every slug in it names a real catalogue entry.

Author decision 14. **No migration** (§12).

---

## 8. Gating, routes, and the acting principal

### 8.1 Three routes, all class S

| Name | Path | Method | Class |
|---|---|---|---|
| `settings-assistant-ask` | `/settings/assistant/ask/` | POST | **S** |
| `settings-assistant-reset` | `/settings/assistant/reset/` | POST | **S** |
| `settings-assistant-panel` | `/settings/assistant/panel/` | GET | **S** |

Class **S** in `identity/routes.py::ROUTE_RULES` (`:43`) — the middleware refuses a non-admin
before the view runs, and a name absent from that table is treated as admin and logged
(`identity/routes.py:26-31`, `tier_for` at `:300`). The class matches `chat-settings`'s own
reasoning: "a page whose entire body is an administrator-only form has nothing to show anybody
else" (`identity/routes.py:94-99`, `agents/chat/views/settings.py:19-23`).

Each new name also needs a `_DRIVERS` entry in `identity/tests/test_route_matrix.py:92`, or
`test_every_route_has_a_driver` (`:633`) fails — which is the intended way a new route
announces itself.

They are mounted by `config/urls.py` as
`path("settings/assistant/", include("agents.chat.assistant_urls"))`, beside
`path("settings/", include("foundation.settings_area"))` (`config/urls.py:34`). The views live
in `agents/chat` because they read `agents/` rows; `config/` is the composition root and is
the one module that already imports every column, so nothing crosses a boundary to mount them.

### 8.2 The template gate

`{% if identity_is_admin %}` around the whole panel in `_settings.html`, mirroring
`chat/settings.html`'s own gate and the sidebar's (`_settings.html:122`). `identity_is_admin`
is already in the shell's context on every page.

**On an open box every viewer is an administrator** (`identity/access.py:116-119`), so a
household box shows the panel to whoever is at the keyboard. That is consistent with the whole
settings area — the same viewer already gets Models, Library and Chat
(`foundation/settings_area.py:26-30`) — and it is stated here rather than discovered later.

### 8.3 The acting principal

Turns run **as the asking administrator**: `start_turn(..., actor=principal)`, whose `actor`
is required and keyword-only precisely so nobody enqueues a turn attributed to nobody
(`agents/chat/service.py:179-183`). The runtime then threads that principal unchanged through
every tool call and every hop (`agents/runtime/loop.py:432-455`, "the acting rule's whole
point"), and `tool_access_for` (`agents/entitlements.py:22`) scopes what the turn may call.

Neither new tool declares an entitlement label, so a grant is the only thing standing between
them and any agent on the box — which is exactly why **`settings.overview` asks the acting
principal itself** and refuses a non-admin (§5.3), while `settings.card` does not and says why
(§5.3's last paragraph). Both are read-only; only one of them reports anything about this box.

---

## 9. Injection posture

Three kinds of text meet in this feature, and they are not equally trusted.

**1. Card content is ours.** Every string in `CARDS` is platform-authored code, reviewed like
any other code. It rides the tool schema and the tool result, which is the same channel every
`ToolSpec.description` already rides. It needs no neutralization, for the reason
`agents/runtime/prompt.py:69-74` gives for the clock line: those values "are OURS, NOT
ANYBODY'S TEXT… so they sit OUTSIDE the fence-neutralization machinery… which exists for
third-party bytes and would only launder our own strings."

**2. The operator's question is the operator's.** It arrives as a USER-role message through
the ordinary turn path. Nothing new.

**3. Live values are the hazard, and they are handled by shape.** `settings.overview` returns
values off a settings row. Today every one of them is an **enumerated or numeric column** —
posture and library posture are `choices` fields, `admin_sees_content` is a boolean,
`session_idle_minutes` an integer, `time_aware` a boolean (`identity/models.py:69-97`;
`agents/models.py`'s `ChatSettings`). None is free text and none is user-controlled prose.
The rules that keep it that way:

- **No free-text, user-supplied string is interpolated into a tool result's `text` in v1.**
  Account names, group names, entitlement names, workstream names and conversation titles are
  all user-controlled and none of them appears — which is also why §5.3 reports no counts and
  no name lists.
- **If a later version does report such a value**, it goes through the platform's existing
  doctrine and not a new one: sanitize the value the way
  `agents/runtime/prompt.py::_sanitize_attachment_title` (`:424`) does (control characters
  collapsed to one space, length-capped), state in the surrounding line that what follows is
  DATA and never instructions (`_CARRYING_ATTACHMENTS_HEADER`, `:264-271`), and — for anything
  longer than a name — carry it in the per-call random-delimited block shape with fence-like
  lines neutralized (`_neutralize_fence_lines`, `:281`; `_carrying_delimiter`, `:297`), never
  in the system message. This is written down here so the next author inherits a rule instead
  of a judgment call. Author decision 15.
- **Links are whitelisted by construction** (§4.3): the panel builds every URL from
  `route`/`anchor` pairs it re-validates against `CARDS`, so even a tool result that somehow
  carried a hostile URL string could not put an anchor on the page. This is the property that
  matters most, because a link is the one thing on this surface an operator will click.

**And the answer itself stays inert:** `render_answer` escapes first and emits no `<a>`
(`agents/chat/rendering.py:598-609`), so a model talked into writing markup produces visible
text, not markup.

---

## 10. Testing

Every task ships tests and docs. The three assertions below are the owner's sustainability
requirement (a), and they are one test module: `foundation/tests/test_settings_help.py`, next
to `test_settings_area.py`, whose `TestTheTable` (`:42-53`) is the shape they follow.

### 10.1 The drift-guard contract — all three assertions

**Assertion 1 — every settings page has a card, and every card names a settings page.**

```python
def test_every_settings_entry_has_a_card_and_every_card_has_an_entry(self):
    entries = {e.url_name for _g, es in SETTINGS_GROUPS for e in es}
    assert {c.route_name for c in CARDS} == entries
```

Symmetric on purpose: a page added without a card fails, and a card left behind by a page that
was removed fails too. It also asserts `card.gate == entry.gate` for every route, which is
what makes §3.2's accepted duplication safe.

**Assertion 2 — every card cites a real, reversible page.**

```python
def test_every_card_names_a_route_this_box_owns(self):
    for card in CARDS:
        assert reverse(card.route_name)
```

The anti-rot test `foundation/tests/test_settings_area.py:43-48` already runs for
`SETTINGS_GROUPS`, restated for the card table: "a table of url names nothing reverses is a
sidebar of 500s waiting for the first admin to open it."

**Assertion 3 — every cited anchor exists in the rendered page.**

```python
@pytest.mark.parametrize("card", CARDS, ids=lambda c: c.route_name)
def test_every_anchor_a_card_cites_exists_on_the_rendered_page(self, client, card):
    body = _render_as_admin(client, card.route_name)     # real GET, admin, accounts-on box
    for field in card.fields:
        assert f'id="{field.anchor}"' in body
```

**Against a RENDERED body, never template source**, following
`models/registry/tests/test_views.py:4373-4375` ("The anchor's target exists on the page").
That is not a stylistic preference: `console.html` has a `{% if cold_start %}`/`{% else %}`
split in which each anchor appears twice and exactly one branch renders (§1.2), so a source
grep can pass on a page whose live HTML has no such id. The pages are driven in a state where
they render their real body — and `_render_as_admin` uses **one** condition set for all
eleven: the enterprise posture, signed in as an administrator, every role bound. That is
deliberately *not* `foundation/tests/test_page_names.py`'s own arrangement:
`_viewing` (`:113-126`) splits its sweep by name, running the six admin-only pages in
`POSTURE_ENTERPRISE` as an admin and *everything else* in `POSTURE_OPEN`, anonymous, because
that module is checking a page's NAME and an open box is the cheapest place to read one. This
test needs a rendered BODY, and every one of the eleven renders 200 for an enterprise admin,
so one condition set is both sufficient and the thing a plan author should build — not a
per-page conditions table nobody asked for. The roles-bound half is borrowed as cited:
`_every_surface_available` (`:134-139`). The sweep **pins its own feature state**
(`FARABUNKER_FEATURES`, whose
default already includes the one flag any settings entry is gated on,
`config/settings.py:170`) rather than inheriting the ambient one, matching this tree's own
recent rule that a flag-dependent test states the flag it needs.

**What each assertion catches, stated as the failure an author will actually see:** add a
settings page without a card → assertion 1, naming the route; rename or delete an anchor id in
a template → assertion 3, naming the card and the anchor; retire a page but leave its card →
assertions 1 and 2.

### 10.2 The content hash

- It **changes** when any card's text changes (mutate a copy of the table, recompute, assert
  inequality) — the anti-vacuous pin without which the hash is decoration.
- It is **stable across processes**: computed with `hashlib`, never Python's salted `hash()`,
  and derived from an explicit ordered serialization rather than any dict iteration.
- It **appears** in `settings.card`'s description and in both tools' `data`.

### 10.3 The rest

| Test | Asserts |
|---|---|
| Tools registered | both keys resolve through `all_tools()`; runner dotted paths import |
| Guide-only | every key in the `settings-helper` spec's `tool_keys` is registered with `mutates=False` (§5.5) |
| Enum is the index | `openai_tool_dict(SETTINGS_CARD)["function"]["parameters"]["properties"]["page"]["enum"]` equals `page_choices()`, and an unknown page raises `ParamError` |
| Prompt untouched | **behavioural:** `build_messages` for a `settings-helper` conversation returns the same message list any other agent with the same rows and the same system prompt returns. A source scan of `agents/runtime/prompt.py` for a settings identifier rides along as a **phase pin whose own docstring says to delete it** when a later phase justifies a contributor — unlike `models/registry/tools.py:23-27`, whose text guard protects a call that module must NEVER make, this one protects a decision (§5.2) that §15.1's retrieval leg could legitimately revisit |
| Surface exclusion | with the assistant installed and one assistant conversation: it is absent from the `/chat/` picker, from the offers list, from the sidebar (active, pinned and archived counts), and from `/chat/all/` (rows and preview); `/chat/start/` with `agent=settings-helper` refuses; and the row carries `workstream_id IS NULL`, so no workstream card can list it (§7.1.2) |
| The row-addressed door (§7.1.1) | the assistant conversation's own `/chat/c/<uuid>/` answers **404** for its owner — and so do turn-create, rename, duplicate, pin, archive, delete and **share** on that id — while the panel, which does not use that helper, still reads and posts to the same row. The same test records that `chat-turn-status` for an assistant turn is **deliberately not narrowed** (§7.1.1), so a later reader does not mistake its 200 for a hole |
| Slug set is honest | every slug in `SETTINGS_SURFACE_SLUGS` names a real `DEFAULT_AGENTS` entry |
| Panel gate | a member on an accounts-on box gets no panel and a 403 from all three routes; an admin gets the panel; an open box shows it to everybody |
| The gate is at the data seam (§6.2) | an **anonymous** visitor to `/setup/` on an accounts-on box — a public settings page — gets **no panel context key at all** and costs **zero** panel queries, `?assistant=1` in the URL included; asserted on the context, not on the rendered HTML, because the defect being guarded is work done before a template discards it |
| Tool audience (§5.3) | a member holding `settings.overview` — granted to any agent — gets `refused`, and the refusal names no value; the same member calling `settings.card` gets the card, deliberately |
| One conversation | two asks from the same admin produce one conversation and two turns; two different admins on an accounts-on box get two conversations; "start over" archives and the next ask creates a new one; **and on an open box two different viewers share one conversation, deliberately** (§6.6 step 3) |
| JS-off | a non-XHR ask returns a 302 whose `Location` is the posted `next` carrying `assistant=1`; that page renders the panel open with the queued turn; a `next` off this host is refused and falls back to `settings-index` |
| No client storage | `"localStorage"` does not appear in the panel fragment (`tools/vision/tests/test_views_create.py:1000-1004`'s idiom) |
| Links are whitelisted | a tool result whose `data["links"]` names an unknown route or a foreign anchor renders **no** anchor |
| Highlight | the `:target` rule is in `_shell.html`, and a settings page rendered with a fragment in the URL still carries the anchor's id |
| Composer CSS placement (§6.5) | the six composer selectors and the `.composer-card > form` rule appear in `_shell.html` and in **no** `chat/` block — this phase's own assertion, because no existing gate can see a `foundation/` template including a `chat/` fragment (`test_css_ownership.py:107`, `:309-313`) |
| Query budget | §11 |
| Route matrix | the three names are classified S and driven (`identity/tests/test_route_matrix.py:92`, `:633`) |
| Guard lists | `agents/settings_tools.py` is in both lists; `test_every_registered_runner_lives_in_a_swept_module` (`foundation/ops/tests/test_column_boundaries.py:449`) and `test_no_tool_module_imports_its_service_layer_at_module_scope` (`:492`) stay green |
| Purity | `foundation/settings_help.py` imports nothing outside the standard library — asserted on the module's own source, the same file-text idiom `test_column_boundaries.py` uses |

---

## 11. Query budget

The panel is on **every settings page**, so its cost is a budget, not an afterthought, and the
budget is pinned by **equality** assertions at two scales.

**The number is not the same in both postures, and pretending otherwise would hand a plan
author a test that cannot pass.**

| State | Open posture | Accounts-on posture | What they are |
|---|---|---|---|
| Not a settings page | **+0** | **+0** | the processor returns on the membership test |
| Settings page, non-admin | **+0** | **+0** | §6.2's admin clause, before any row is read |
| Settings page, panel collapsed | **+1** | **measured at implementation, then pinned by equality** | the agent row — plus, on an accounts-on box, the gate's own ownership/share/grant reads |
| Settings page, panel open | **+3** | **measured, then pinned** | the above, plus the conversation and its last N turns |
| Panel fragment route | **3** | **measured, then pinned** | the same reads as "panel open" |

**Why the accounts-on number is larger, and why it is irreducible here.** An administrator on
an accounts-on box is not `sees_all_content` — `admin_sees_content` defaults to `False`
(`identity/models.py:90`) and `is_admin` is emphatically not `sees_all_content`
(`identity/access.py:126-129`) — so neither gate takes its cheap first branch:

- `visible_conversations` (`agents/visibility.py:64-121`) calls `owned_rows_q(principal)`,
  whose internal `is_admin` is **deliberately not threaded** — that function's own docstring
  says so at `:86-90` ("widening it is a cross-column change and its own question") — and then
  materialises `shared_keys` for the conversation and the workstream targets, which
  `agents/shares.py:33-58` chooses on purpose ("Two small queries on a single-box install beat
  one clever one");
- `visible_agents` (`agents/visibility.py:142-163`) adds the agent-target `shared_keys` plus
  `label_permitted_q` → `held_entitlement_ids` → `_grant_ids` (`identity/access.py:299-315`).

Threading `settings_row_for(request)` (below) removes the repeated singleton reads and is
still required; it cannot remove the share/grant reads or that un-threaded `owned_rows_q`
call, and this phase does not widen a cross-column function to chase them.

**So the mandate is: measure the accounts-on number when the panel is built, write it into the
test as an equality, and never as a `<=`.** A bound that only forbids growth is a place for a
regression to hide. The invariant the pin actually protects is that the number is **FLAT** —
in the number of turns, the number of conversations and the number of agents — which is
exactly what `models/registry/tests/test_views.py:3546-3548` means by "9, not 5, and every one
of them is FLAT". Pin it with `django_assert_num_queries` at **0 turns and at N turns**, in
**both postures**, and separately with the assistant **not installed** (which must be the
collapsed number, not the open one) and for a **non-admin** (which must be +0).

Two rules keep the panel's own share of those numbers true, both of them the N+1 lesson
`agents/chat/sidebar.py:105-139` already teaches:

- the `IdentitySettings` row is read **once** per request and threaded — the panel reads
  `identity.request.settings_row_for(request)` (`identity/request.py:21`), the row the gate
  middleware already stashed, and hands it to every access call it makes;
- the transcript is **one** query for N turns — **including the tool turns**, which is where
  §4.3's `links` live, so the "Jump to" strip is built from rows already fetched and never
  costs a read of its own. (The tool turns are read, not rendered as cards: §6.4.)

The panel adds **no** query to any page outside the settings area, and none on a settings page
for a principal it will not render for. Those two zeroes are the promise that lets it exist at
all, and they are the same in both postures.

The two-scale idiom to copy is `agents/chat/tests/test_sidebar.py`'s (the same count at 1 row
and at 25); the equality-with-a-stated-reason idiom is
`models/registry/tests/test_views.py:3540-3586`'s.

---

## 12. Migrations

**None.** Verified against the tree at write time: `agents/migrations/` ends at
`0010_chat_settings.py` and this phase adds no model and no column, so `0011` is not written
by it. `identity/migrations/` (latest `0003_entitlement_and_grant.py`) is untouched.

The three things that might have wanted a column do not get one: the assistant's conversation
is an ordinary `Conversation` row (`agent` FK plus `owner_kind`/`owner_key`, all existing
columns); "start over" writes the existing `archived_at`; and the chat-surface exclusion reads
the existing `agent` FK (§7.2). The content hash is computed, never stored.

If a reviewer disagrees with §7's mechanism and wants a `Conversation.surface` column, that is
**one** migration and it lands in this phase's numbering as `agents/migrations/0011_…` — noted
so the count is honest either way.

---

## 13. Documentation

### 13.1 The recipe that binds future authors

`docs/EXTENDING.md` gains a section: **"Adding a settings page"**. The file's `<h1>` becomes
"Extending farabunker" with "Adding a tool in three steps" as its first section — the file
already carries two recipes beyond the tool one ("Registering a workstream panel" `:190`,
"Joining the taint stamp" `:231`), so the title is already narrower than the document.
`README.md:121`'s one-line description is updated in the same commit.

The recipe is five steps, each naming the file and the test that fails if it is skipped:

1. **The page** — a view and a template extending `_settings.html`, writing its body into
   `settings_content` and overriding its own `side_current_*` block.
2. **The route rule** — an entry in `identity/routes.py::ROUTE_RULES` and a `_DRIVERS` entry
   in `identity/tests/test_route_matrix.py`. *Skip it → `test_every_route_has_a_driver`, and
   an unclassified name is treated as admin and logged.*
3. **The sidebar entry** — an `Entry` in `foundation/settings_area.py::SETTINGS_GROUPS`.
   *Skip it → the page is unreachable from the settings sidebar, and
   `foundation/tests/test_shell.py`'s drift test compares a list that no longer matches.*
4. **The help card** — a `HelpCard` in `foundation/settings_help.py`, one `HelpField` per
   control. Two rules, because neither is testable:
   **(a) describe the controls this page renders, and no others** — a card that claims a
   neighbouring page's control passes every drift test and misroutes every answer about it
   (§3.2's worked case — `library_posture` belongs to Identity & security, not Library);
   **(b) if a control only renders in some postures, say so in its `meaning`** — assertion 3
   renders one posture and cannot see the others (the same field is a select in two postures
   and a hidden input in the third).
   *Skip the card → assertion 1 (§10.1), naming your route. Get its contents wrong → no test
   catches you; these two rules are the only guard.*
5. **The anchors** — a stable `id=` on each section a field names, in every branch of the
   template. *Skip it → assertion 3 (§10.1), naming the card and the anchor.*

Plus the two rules that are easy to get wrong and cheap to state: the page's `<h1>` and
`<title>` must agree with the sidebar label (`foundation/tests/test_page_names.py`), and a
leaf page overriding `extra_style` writes `{{ block.super }}` first (`_settings.html:57-91`).

### 13.2 The rest of the documentation

| File | What changes |
|---|---|
| `docs/EXTENDING.md` | §13.1, plus the two new tools listed where the tool inventory is |
| `agents/README.md` | the settings assistant among the shipped defaults, and its settings-only surface |
| `agents/chat/README.md` | the panel, its context processor, its three routes, and the chat-surface exclusion |
| `foundation/README.md` | `settings_help.py` as a pure leaf other columns import |
| `docs/DEV.md` | how to install the assistant on a dev box, and the drift tests to expect |
| `README.md` | the `EXTENDING.md` line (§13.1) |

### 13.3 The ADR

**ADR 0018**, written last, closing the phase. `docs/adr/` ends at `0017-workstreams.md` and
no document in the tree claims `0018` (verified by search), so `0018` is free at write time;
the closing task re-checks the number before it writes, because another phase may land first.

It records: guide-only as a structural property, not a promise; the card table plus content
hash as the answer to "keep the context relevant"; the tool schema as the injection channel;
links built by the platform because `render_answer` makes no link; and the settings-only
surface as a wrapper on the one gate rather than a column.

---

## 14. Phasing and done-when

One plan-sized phase, in this order (each step leaves the suite green):

1. `foundation/settings_help.py` + the gate-constant move + the three drift tests (§10.1).
   **Anchors land with this step**, page by page, because assertion 3 is what proves them.
2. The `:target` rule in `_shell.html` (§4.2).
3. `agents/settings_tools.py`, the two `ToolSpec`s (including `run_overview`'s own
   `ToolRefused` audience gate, §5.3), registration, both guard lists (§5).
4. The `AgentSpec` and the guide-only pin (§5.1, §5.5).
5. The chat-surface exclusion and its tests (§7) — the five list call sites, the
   narrowing of `visible_conversation_or_404` (§7.1.1), and the `workstream_id IS NULL` pin
   (§7.1.2).
6. The composer CSS promotion to `_shell.html` (§6.5) — **after the hygiene sweep lands**.
7. The panel: the context processor with **both** guard clauses (§6.2), registered in
   `config/settings.py`'s `TEMPLATES`; the fragment; the `composer_next` parameter; the three
   routes; `ROUTE_RULES` + `_DRIVERS`; and the `chat-default-install` `next` (§6, §8) — with
   the query-budget numbers measured and pinned in both postures (§11).
8. The script (§6.7).
9. Docs (§13.1, §13.2), then ADR 0018 (§13.3).

**Done-when**, verified on the branch preview, not on the owner's live box. **Each drill names
the world it needs**, because three of them are *about* a posture and cannot be run anywhere
else: **1b** and **2** need an **open-posture** box, **1c** a **personal** one, and every
other **browser** drill runs at `:8001` in the enterprise world — as an administrator except
drill 7's second half, which is deliberately signed out. **Drills 3, 4, 9 and 10 are suite
runs** and state their own postures.

1. From `/inference/`, ask **"how do I make the library admin-only?"**. The answer names
   **Identity & security** and its **library posture** control by the page's own words, and
   the "Jump to" strip links that control's anchor on `identity-settings`. Clicking it lands
   there, scrolled to the control, **highlighted**, with the panel still open.
   **The page the question names is not the page that owns the control**
   (`library_posture` is an `IdentitySettings` column, `identity/models.py:76`, edited only
   through `identity/forms.py:47` and `identity/views.py:344`; `identity/access.py:346` says
   "`library_posture` is only EDITABLE on the enterprise page"). An answer that says "Library"
   — the page that owns the seven library *forms*, none of them a posture
   (`tools/rag/models.py:441-513`) — **fails this criterion**. This is the drill that proves
   the whole feature, so it is the one that must not be satisfiable by a wrong answer.

   - **1b — the same question on an open-posture box.** The accepted answer is: *while this
     box is open every viewer is an administrator, so a lock has nobody to lock out; the
     control is on Identity & security — not in this box's sidebar — and it starts meaning
     something once the box leaves the open posture.* Sourced from `settings.overview`'s
     `posture`, not guessed.

     **Two things that would be false, and neither may appear in the answer.** The page is not
     *unreachable*: `identity-settings` is class **S** and `is_admin` is True for everyone on
     an open box (`identity/access.py:116-119`), so it opens at its own URL — it is merely
     absent from the sidebar, because its `Entry` gate is `ACCOUNTS_ADMIN` and `_may_see`
     requires `posture != "open"` (`foundation/settings_area.py:108`, `_may_see` at
     `:113-120`). And the control is not *missing*:
     `identity/templates/identity/settings.html:120-135` branches on `posture == "personal"`
     **only**, so an open box renders the real library-posture select. An operator who
     followed a "it appears once accounts are on" answer to that page would find the control
     already sitting there. What is actually true is that the setting has no effect here —
     `library_posture=locked` resolves to `sees_all_content` only
     (`identity/access.py:335-355`) and the open principal has it.

   - **1c — the same question on a personal-posture box.** Here `identity-settings` *is* in
     the sidebar (`_may_see`: admin, and `posture != "open"`), and the library-posture control
     is rendered as a **hidden input** with the page's own explanation — "there is no page to
     lock it, since every account here is an administrator already"
     (`identity/templates/identity/settings.html:122-125`; `identity.services.set_posture`
     resets the column to `open` in this posture regardless). The accepted answer names the
     posture and says the control is **not offered** there — never that it is missing, and
     never a set of steps to change a control this box does not show. This is the third
     posture and the one an answer written against either of the others gets wrong.

2. Ask a question whose answer depends on the box: **"why don't I see Accounts in the
   sidebar?"** — on an **open-posture** box, like 1b. The answer names the posture, from
   `settings.overview`.
3. **Staleness drill** (a suite run). Change one card's text; `CONTENT_HASH` changes and the
   hash test goes red. Delete a card for a page that still exists; assertion 1 goes red
   naming the route.
4. **Anchor-rename drill** (a suite run). Rename one `id=` in one settings template;
   assertion 3 goes red naming the card and the anchor. Restore it; green.
5. **JS-off drill.** Disable JavaScript. Ask a question: the page redirects back to the page
   you asked from with `?assistant=1`, the panel is open, the queued turn is visible, and a
   reload shows the answer.
6. **Surface drill.** `/chat/` offers no settings assistant in its picker and no "Add" offer
   for it; the sidebar and `/chat/all/` list no assistant conversation, active or archived;
   a hand-built POST to `/chat/start/` naming its slug is refused; **opening the assistant
   conversation's own `/chat/c/<uuid>/` URL — copied out of the database — answers 404, and so
   does sharing it** (§7.1.1); and the panel on the settings page still works throughout.
7. **Gate drill.** A member on the accounts-on box sees no panel and gets 403 from all three
   routes. **Sign out entirely and open `/setup/`** — a public settings page: it renders, and
   the panel context is absent, `?assistant=1` in the URL included (§6.2).
8. **Tool audience drill.** Grant `settings.overview` to another agent, ask that agent as a
   member what this box's posture is: the tool answers `refused` and the reply names no value
   (§5.3).
9. **Budget drill** (a suite run, not a browser drill). The query-count pins pass at 0 turns
   and at N, **in both postures**, with the accounts-on numbers measured and written in as
   equalities (§11); the open-posture collapsed panel costs exactly one query; a non-admin and
   a non-settings page cost zero.
10. The whole suite is green, including the three drift assertions, on a run that includes
    `foundation/`, `agents/`, `identity/` and `models/`.

---

## 15. Non-goals and named deferrals

Each names the hook it lands on, so it is future work rather than a wish.

1. **A retrieval leg over the help content.** If the card table ever outgrows an
   index-plus-on-demand shape, the cards become an ingested corpus and retrieval answers
   instead of `settings.card`. **Hook:** `CONTENT_HASH` — the derived index stores the hash it
   was built from and refuses to answer, or rebuilds, when it does not match the code. This is
   the artifact the owner's staleness requirement was really about, and the hash exists so
   that it can be built later without redesigning anything.
2. **Act-mode.** An assistant that changes a setting after a confirmation. **Hook:** a
   `mutates=True` `ToolSpec`, which the registry deliberately refuses to grant until that is a
   decision somebody makes (`docs/EXTENDING.md:66-71`). Owner ruling 1 forbids it for now, and
   nothing here makes it harder later.
3. **A non-admin variant.** A member-facing helper for the settings a member can actually
   reach. **Hook:** `HelpCard.gate` is already carried per card, so the card set can be
   narrowed per principal the day there is a second audience; today the panel is class S and
   the question does not arise.
4. **Live values from other columns** (§5.4). **Hook:** either a second read-only tool in the
   owning column, granted to this agent, or a dotted-path reader registry in the shape
   `docs/EXTENDING.md:190-230` already documents. Neither is built.
5. **A shared or shareable assistant conversation.** One conversation per administrator
   (one per box in the open posture, §6.6 step 3), owned, unshared — and **unshareable**,
   which decision 22 is what makes true: the share action lives on `/chat/c/<uuid>/`, and
   that page 404s for a settings-surface conversation, so this deferral is a door that is
   shut rather than one nobody has walked through yet.
   **Hook:** `Share.Target` already has a conversation target, and the panel would need a
   share affordance of its own before any of it could be reached.
6. **Search over settings text**, screenshots, or guided multi-step walkthroughs with state.
   No hook needed: none of them is implied by anything above.

---

## 16. Decisions the author made

1. **The gate constants move to `foundation/settings_help.py`** and `settings_area.py` imports
   them. §3.1. One definition of "admin", intra-column, one line changed. The alternative — a
   second set of literals plus a drift test comparing them — is a test that exists only to
   protect a duplication nobody needed.
2. **The card table is central, beside `SETTINGS_GROUPS`, not per-column.** §3.2. It is beside
   the table that already decides what the settings area is; a per-column registry needs a
   third registration seam and an `AppConfig` line per column for data that is pure text. The
   accepted cost — route name, title and gate stated twice — is closed by assertion 1.
3. **The content hash is `sha256` over an explicit ordered serialization, truncated to 12
   hex.** §3.3. Never `hash()`, which is salted per process. Its v1 jobs are citation and
   change detection; its real job arrives with deferral 1.
4. **The `:target` rule lives in `_shell.html`, not `_settings.html`.** §4.2.
   `setup/index.html` overrides `extra_style` with no `{{ block.super }}`, so the rule would
   silently not apply on Install guides; the settings layout's own CSS is already in
   `_shell.html`.
5. **Deep links are built by the panel from validated `route`/`anchor` pairs.** §4.3.
   `render_answer` produces no `<a>` from model text, by design; whitelisting by construction
   is what makes a clickable link safe on a page an administrator trusts.
6. **The page index rides the tool schema, not the prompt.** §5.2. A `choice` param's `enum`
   plus a description built from `CARDS` at import cannot go stale and needs no change to
   `agents/runtime/prompt.py`, where a slug-conditional paragraph would be a special case in
   the one function every agent goes through. **Deviates from the brief's hint** — §18.
7. **`settings.overview` reports only what `agents/` may legally read.** §5.3, §5.4.
   **Flagged for owner.** The library's live caps are unreachable under the import law and are
   deferred rather than smuggled across; guide-only makes that a link, not a gap.
8. **The panel's context comes from a context processor registered by `agents.chat`, keyed on
   `resolver_match.url_name`.** §6.2. Zero queries off the settings area — which answers the
   objection `foundation/settings_area.py:12-20` raised — where per-view context is impossible
   for identity's four pages and a template tag would be a mechanism this repository has never
   used.
9. **`?assistant=1` is the panel's open state, the assistant's own links carry it, and it is
   the ONLY parameter the redirect carries.** §6.3, §6.6. One query parameter is what makes
   "persists across screens" true with no script and no client storage; it also keeps the
   collapsed panel down to one query. No `?pending=<turn_id>`: the panel re-renders its whole
   recent transcript, so the queued turn is a row it already read and the pending marker is
   derived server-side. **Deviates from the brief** — §18.
10. **The panel renders its own compact transcript, reusing `render_answer` but not
    `chat/_turn_block.html`.** §6.4. Reuse of the card markup would promote roughly twenty
    chat selectors into the global shell to render machinery this surface then hides.
11. **The shared composer is reused, and its six selectors plus the structural
    `.composer-card > form` rule are promoted from `chat/base.html` to `_shell.html`.** §6.5.
    The placement **rule** demands it — no existing gate can see a `foundation/` template
    including a `chat/` fragment, so this phase writes that assertion itself — and the owner
    has already ruled against a second composer. Rules move unchanged; no pixels move. The
    fragment itself gains exactly one
    optional parameter, `composer_next` — the same hidden-routing-field shape
    `composer_workstream` already has — so the one `next` reader the codebase has
    (`validated_next_url`) is reused rather than forked.
12. **The install offer reuses `chat-default-install`, extended with a validated `next`.**
    §6.5. Three lines on an existing route against a fourth new route; `validated_next_url`
    already exists and is already used twice.
13. **One sanctioned script (~40 lines): XHR submit plus fragment refresh.** §6.7. The submit
    interception is what stops asking a question from discarding a half-filled settings form;
    it is not a copy of the conversation poller, and its three constants come from
    `agents/chat/service.py:93-95`.
14. **The chat-surface exclusion is a frozenset of slugs plus two wrappers on the one gate.**
    §7. No migration, no column, no narrowing of the platform's visibility gate — which must
    keep answering *yes*, because the panel reads the same rows.
15. **The injection rule for live values is written down before it is needed.** §9. v1 reports
    only enumerated and numeric columns; the next author who wants to report a name inherits
    the sanitize/frame/fence doctrine by reference rather than re-deciding it.
16. **The drift tests assert against rendered bodies.** §10.1. A template with mutually
    exclusive branches — `console.html` is one — can pass a source grep and still render no
    anchor.
17. **The query budget is pinned by equality, at two scales, including the not-installed
    state.** §11. A `<=` bound is a place for a regression to hide.
18. **No migration in this phase**, and the one alternative that would need one is named with
    its number. §12.
19. **The recipe lands in `docs/EXTENDING.md`** rather than a new file, and the `<h1>`
    generalizes. §13.1. That document already carries three recipes.
20. **ADR 0018, number re-checked at write time.** §13.3.
21. **The context processor gates on `is_admin` at the data seam, not in the template.** §6.2.
    `setup-index` is a **public** settings page (`identity/routes.py:254`), so without the
    clause an anonymous visitor has an agent row, a conversation and six turns resolved for
    them before a `{% if %}` discards it — the render-vs-gate inversion this repository already
    has a name for. No context key and no row read for a principal the panel will not render
    for.
22. **`visible_conversation_or_404` asks `chat_surface_conversations`, unconditionally**, so
    every one of its nine call sites 404s on a settings-surface conversation — including
    share, which is what makes §15.5's deferral a shut door. §7.1.1. No escape-hatch keyword:
    the panel does not use that helper, so a `False` nobody passes would be a knob with no
    reader. The claim is bounded and §7.1.1 bounds it: **every row-addressed chat view** is
    closed, not every id-addressed read in the codebase — `visible_turn`/`chat-turn-status`
    and `agents/workstreams.py:399` ask the same read gate, expose no affordance, and are
    deliberately left alone. Without this the assistant's thread is fully live at its own
    `/chat/` URL — postable, renamable and **shareable** — and §15.5's "nothing here uses it"
    would be false the day this ships.
23. **`settings.overview` refuses a non-admin in the runner; `settings.card` does not.** §5.3.
    A grant is per-agent, so the surface's route class cannot protect the *information*: an
    operator adding the key to another agent would otherwise hand a member this box's posture
    and security configuration. `settings.card` reports no value about this box and is
    deliberately left open, which is also what keeps §15.3's non-admin variant possible.
24. **The highlight uses its own `--target-wash` token, not `--panel`.** §4.2. Several
    settings sections are already painted `--panel`, so a `--panel` highlight would be
    invisible on exactly the pages an operator gets sent to, and §14's "highlighted" would be
    a criterion a human could not honestly sign off.

---

## 17. Flagged for owner

Six judgment calls. Each is decided — nothing below is an open question — and each is the
kind of call the owner may want to overrule.

1. **A pre-existing gap this phase works around rather than fixes.**
   `foundation/setup/templates/setup/index.html:16` overrides `extra_style` without
   `{{ block.super }}`, so it silently drops `_settings.html`'s shared rules. Decision 4 routes
   around it (the highlight goes in `_shell.html`). The one-line fix is not in this phase's
   scope; the CSS-ownership gate does not reach `foundation/` templates, so nothing catches it
   today.
2. **The assistant will not recite the library's numeric caps** (decision 7). It will name the
   page and link to the control. Changing that means either a second read-only tool in
   `tools/rag` or a new cross-column reader registry — both real, neither free.
3. **On an open box, the panel is visible to everyone — and so is the transcript.** `is_admin`
   is True for everyone there (§8.2), and `visible_conversations` returns `.all()` for the one
   `OPEN_PRINCIPAL` every viewer is (`agents/visibility.py:93-95`), so a household box has
   **one shared assistant thread**: whoever sits down reads the previous person's questions,
   continues the same conversation, and can "start over" on all of it. That is correct for a
   box with no accounts and it matches the whole settings area — but it is the half a person
   would be surprised by, so it is here rather than in a docstring. §6.6 step 3.
4. **Asking a question with JavaScript off navigates**, and will discard a half-filled settings
   form on the page (§6.7). The script exists to prevent that in the ordinary case. The
   alternative — never navigating, ever — is not achievable without a script.
5. **One panel-owned transcript fragment now exists beside the chat one** (decision 10). It is
   a deliberate second renderer, justified by what it does not render; if the owner would
   rather have one renderer everywhere, the price is roughly twenty chat selectors in the
   global shell.
6. **The assistant's own thread is not openable in `/chat/`** (decision 22): its
   `/chat/c/<uuid>/` URL answers 404, so it cannot be renamed, duplicated, pinned, archived
   from there, or **shared**. That is what makes "settings-only surface" true rather than
   merely "not listed", and it is the reading of ruling 2 this spec commits to. The cost is
   that an administrator who wants the assistant's history in the ordinary chat surface — to
   share an explanation with a colleague, say — cannot get it there; "start over" archives
   and the panel is the only reader. Overruling this means deciding what that page may do,
   action by action, with sharing the one that matters.

---

## 18. Brief-versus-tree corrections

The brief's precedents were checked against the tree at `08e28c6`, and item 5 was added at
amendment time. Eight corrections; the architecture survives all eight.

1. **`render_answer` makes no links** (`agents/chat/rendering.py:598-609`). The brief's plan
   assumed the assistant could "provide links… with a hash/highlight" in its answer. It cannot
   — a URL it types is inert text. §4.3 rebuilds that requirement on platform-built,
   whitelisted links, which is both what the owner asked for and safer than what was assumed.
2. **The page index rides the tool schema, not `build_messages`.** The brief hinted at a code
   contributor via `append_paragraph` (`agents/runtime/prompt.py:654`). A `choice` param's
   `enum` plus an import-time description is the same "cannot go stale" property with **no**
   change to `agents/runtime/prompt.py` and no per-agent special case in it (decision 6).
3. **`settings.overview` cannot be "composed from existing accessors" across columns.** The
   import law forbids `agents/` → `tools/` outright (ADR 0015:112-120), so the library's caps
   are not reachable from any module that can also read the identity row and `ChatSettings`.
   §5.3 reports what is legally reachable and §15.4 defers the rest (decision 7).
4. **Line numbers.** `docs/EXTENDING.md` carries six stale citations of
   `foundation/ops/tests/test_column_boundaries.py`. This spec cites the tree; the docs pass
   (§13.2) fixes the source, and the list is given by line so that pass is mechanical rather
   than a hunt:

   | `EXTENDING.md` line | Cites | Should cite |
   |---|---|---|
   | `:104` | `:485` | **`:492`** (`test_no_tool_module_imports_its_service_layer_at_module_scope`) |
   | `:133` | `:346` | **`:352`** (`test_no_tool_runner_blocks_on_a_queue_job`) |
   | `:134` | `_REGISTRATION_MODULES` `:217-228` | **`:217-231`** |
   | `:139` | `:510` | **`:516`** (`test_the_registration_modules_list_is_not_silently_empty`) |
   | `:155` | `:442` | **`:449`** (`test_every_registered_runner_lives_in_a_swept_module`) |
   | `:284` | `:346` | **`:352`** (the same test, cited a second time) |

   Verified as correct and left alone: `TOOL_MODULES` at `:185-189`, and the `localStorage`
   pins at `tools/vision/tests/test_views_create.py:1000-1004`.
5. **The library-posture control's presence is posture-dependent, and the brief's mental
   model had it in one state.** `identity/templates/identity/settings.html:120-135` branches
   on `posture == "personal"` only: a real select on an open **and** an enterprise box, a
   hidden input with an explanation on a personal one (`:122-125`). Three postures, two
   renderings, one anchor — the `.field` wrapper renders in all three, so deep links are
   unaffected and only the card's *words* have to be careful. §3.2's second card rule and §14
   done-when 1b/1c exist because of this, and it is the reason "how do I make the library
   admin-only?" has three different correct answers rather than one.
6. **The console's anchors appear twice each**, in the two arms of a `{% if cold_start %}`
   split (`:1007`/`:1067`, `:1016`/`:1084`, `:1021`/`:1123`). The brief cited only the first
   set. This is why §10.1's assertion 3 renders the page instead of reading the template.
7. **No `?pending=` in the panel's redirect.** The brief carried the chat page's flow across
   whole. That parameter is the hand-off telling a JS-enabled reload which turn the
   script-free POST just queued (`agents/chat/templates/chat/conversation.html:29-33`); this
   panel re-renders its whole recent transcript on every render, so the queued turn is already
   in front of the operator and the pending marker is derived from the rows (decision 9). The
   POST/redirect half of the flow — the part that makes it work with no script — is kept
   exactly.
8. **The shared composer emits no `next` field.** The brief's "composer_mode parameterized"
   reuse is correct, but `validated_next_url` reads `request.POST.get("next", "").strip()`
   (`agents/chat/service.py:693`) and `chat/_composer.html` writes no such input. §6.5 adds
   one optional `composer_next` parameter, in the shape that fragment already uses for
   `composer_workstream`, rather than forking the `next` reader.

Also confirmed as cited, and relied on unchanged: `agents/migrations/` ends at `0010`;
`SETTINGS_GROUPS` and its gates; the sidebar drift test at `foundation/tests/test_shell.py:381`;
`chat-settings` as class S; `install_default`'s create-if-absent contract; `start_turn`'s
required `actor`; and `turn_create`'s two answers.
