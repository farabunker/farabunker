# Settings Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-09-09
**Branch:** `settings-assistant`, in the branch worktree under `.claude/worktrees/model-management-framework`
**Branch base:** `origin/main` at `08e28c6` ("Merge pull request #87"), the tree the spec was written and re-verified against; the branch carries three docs-only commits on top, ending at `308c6ec`
**Phase:** the settings assistant — a small phase on the settings area (UI-2) and the agent runtime (ADR 0015), after Identity & Auth (ADR 0016) and Workstreams (ADR 0017)
**Status:** plan, not executed

## Goal

Give an administrator a **guide** that lives on every settings page: they type a question into a
panel, and get back an answer grounded in what this box's settings actually are and where they
actually live — plus a working link that jumps to the exact control and highlights it. It never
changes a setting, and it is structurally incapable of doing so.

## Architecture

**Fourteen tasks, 110 steps, plus a 12-item Smoke Checklist.** The order de-risks deliberately: the code-side registry and its
drift guards land *first*, so every later task is measured against a table the suite already
polices; and the two gates the adversarial review found missing — the tool's own audience refusal
and the context processor's admin clause — land **in the same task and the same commit as the
surface they protect**, never as a later hardening pass.

**Tasks 1–3 are the code-side truth.** Task 1 adds `foundation/settings_help.py` — a rule-1 pure
leaf carrying one `HelpCard` per settings page, the gate constants moved up from
`foundation/settings_area.py`, and a `sha256` content hash — together with drift assertions 1 and
2, which are what drive the table into existence. Task 2 adds drift assertion 3 (a *rendered-body*
sweep) and the stable `id=` anchors in the settings templates that assertion demands. Task 3 is
the `:target` highlight and its `--target-wash` token, in `_shell.html`.

**Tasks 4–6 are the agent half.** Task 4 is `agents/settings_tools.py`: two read-only `ToolSpec`s,
`settings.overview`'s own `ToolRefused` audience gate, registration in `agents/apps.py::ready()`
and both column-boundary guard lists — all in one commit, per `docs/EXTENDING.md`. Task 5 is the
catalogue entry, the guide-only pin and the settings-surface slug set. Task 6 spends that slug set:
two thin wrappers on the one visibility gate, six call-site edits including the narrowing of
`visible_conversation_or_404`, and the tests that prove the assistant's own thread is not merely
unlisted but unreachable on `/chat/`.

**Tasks 7–11 are the surface.** Task 7 promotes five of the shared composer's six selectors to
`_shell.html` (after merging the hygiene sweep) and writes this phase's own placement assertion,
because no existing gate can see the shape. Task 8 is the context processor with **both** guard
clauses and the two zero-query promises that let it exist at all. Task 9 is the two small shared
changes the panel needs: `composer_next` on the one composer, and a validated `next` on
`chat-default-install`. Task 10 is the panel itself — three class-S routes, the fragment, the
transcript, the whitelisted "Jump to" links, and the query budget measured and pinned by equality
in both postures. Task 11 is the one sanctioned script.

**Tasks 12–14 close the phase.** Task 12 is documentation, including the recipe that binds future
authors and the six stale `docs/EXTENDING.md` citations §18 lists by line. Task 13 is ADR 0018.
Task 14 merges `origin/main`, resolves, runs the whole suite in both orders, both feature-flag
states and all three postures, and walks the Smoke Checklist before the whole-branch review.

The suite is green at every task boundary **except 5→6**, which Task 5 Step 6 names explicitly:
adding a fourth catalogue entry changes what `/chat/`'s "Add the default X" offers list holds, and
Task 6 is the task that takes the assistant back out of it. Weakening the offending test at Task 5
would delete the evidence Task 6 exists to act on, so the plan leaves it red for one commit and says
so rather than pretending otherwise. Every task ends with an independently testable deliverable.

## Tech Stack

Django 5.1+ (server-rendered), Postgres, pytest + pytest-django. `dataclasses` and `hashlib` in a
rule-1 pure leaf; the platform's existing `agents.contracts.tools` registry, its dotted-path
runner strings and its `Param(kind="choice")` → JSON-schema `enum` path; Django context
processors; CSS `:target` and `color-mix()`. One inline `<script>` of roughly forty lines, whose
three tuning constants are declared once in Python and handed to the template.

## Spec

`docs/superpowers/specs/2026-09-09-settings-assistant-design.md` — the binding authority, 1,718
lines, **final at `308c6ec` after three adversarial review rounds**. Read it in full alongside this
plan. It carries twenty-four numbered author decisions (§16) and six owner flags (§17); every one
of them is settled. This plan implements them and never re-litigates them.

**In scope:** §14's nine phase steps, in that order, as fourteen tasks.

**Out of scope, named so it reads as scoped-out rather than forgotten** — all of §15:

- **A retrieval leg over the help content** (§15.1). `CONTENT_HASH` is its hook and this phase
  builds the hash; it builds no index, no corpus and no stored repository.
- **Act-mode** (§15.2). Owner ruling 1. No `mutates=True` spec, no confirmation flow, no button.
- **A non-admin variant** (§15.3). `HelpCard.gate` is carried per card so the card set *can* be
  narrowed per principal later; nothing narrows it today.
- **Live values from other columns** (§15.4, §5.4). The library's numeric caps live on
  `tools.rag.models.RagSettings` and `agents/` may not import `tools/` at all. The assistant names
  the page and links to the control instead. **Owner flag 2.**
- **A shared or shareable assistant conversation** (§15.5). Task 6 is what makes this a shut door
  rather than an unwalked one.
- **Search over settings text, screenshots, guided multi-step walkthroughs with state** (§15.6).

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the
spec at the section named beside each.

1. **GUIDE-ONLY (owner ruling 1, spec §2).** *"The assistant explains, answers, and deep-links; it
   NEVER mutates settings. No mutating tools; the tool registry already refuses mutates=True
   grants — the spec pins it."* Neither new `ToolSpec` declares `mutates`, so both default to
   `False` (`agents/contracts/tools.py:125`). Task 5 pins that **every** key in the
   `settings-helper` spec's `tool_keys` resolves to a registered spec with `mutates=False` (§5.5).

2. **SETTINGS-ONLY surface (owner ruling 2, spec §2).** *"A persistent panel across settings
   screens; the assistant is NOT in the main chat's agent picker, and its conversations do NOT
   appear in the main chat sidebar or /chat/all/."* Task 6 reads that as **unreachable**, not
   merely unlisted: `visible_conversation_or_404` asks `chat_surface_conversations`
   unconditionally, so every one of its **nine** call sites answers 404 on a settings-surface
   conversation — render, post, rename, duplicate, pin, archive, delete and **share** (§7.1.1,
   decision 22).

3. **Admin-gated, including the open-posture truth (spec §8.2, §1.4 fact 3).** All three new routes
   are class **S** in `identity/routes.py::ROUTE_RULES`. `is_admin` is **True for everybody on an
   open box** (`identity/access.py:116-119`), so a household box shows the panel — and the one
   shared transcript behind it — to whoever is at the keyboard. That is correct, it matches the
   whole settings area, and it is **owner flag 3**. An answer, a test or a docstring that says the
   panel is "administrator-only" without that half is wrong.

4. **Zero new JavaScript beyond the sanctioned budget (spec §4.2, §6.7).** The highlight is a pure
   CSS `:target` rule — no script, no class toggling, no state. The panel ships **exactly one**
   inline `<script>`, roughly forty lines (Task 11), whose three constants —
   `POLL_INTERVAL_MS`, `MAX_TRANSPORT_RETRIES`, `MAX_POLL_DURATION_MS` — are the ones already
   declared once in Python at `agents/chat/service.py:93-95` and handed to the template, never
   re-typed in JS. Everything works with **no script at all**: a plain POST, a redirect carrying
   `?assistant=1`, the queued turn showing "Thinking…", and a reload showing the answer.
   **Persistence is server-side only** — no `localStorage`, no `sessionStorage`, no draft cache;
   the panel's own test asserts the string does not appear in its script.

5. **No AI model, product or vendor names or versions anywhere** — code, comments, tests,
   docstrings, docs, or commit messages. The repository is going public and ADR 0010's third
   amendment forbids the platform from naming a model for the operator. Describe models
   generically. The spec itself contains none; neither may this plan's output.

6. **The query budget is pinned by EQUALITY, at two scales, in BOTH postures (spec §11).** Never a
   `<=` bound — *"a bound that only forbids growth is a place for a regression to hide"*. The
   invariant the pin protects is that the number is **FLAT** in the number of turns, of
   conversations and of agents. Pin with `django_assert_num_queries` at **0 turns and at N turns**,
   in both postures, and separately with the assistant **not installed** (which must equal the
   collapsed number, not the open one) and for a **non-admin** (which must be +0). The two zeroes
   that are the same in both postures are the promise that lets the panel exist:

   | State | Open posture | Accounts-on posture |
   |---|---|---|
   | Not a settings page | **+0** | **+0** |
   | Settings page, non-admin | **+0** | **+0** |
   | Settings page, panel collapsed | **+1** | measured at implementation, then pinned by equality |
   | Settings page, panel open | **+3** | measured, then pinned |
   | Panel fragment route | **3** | measured, then pinned |

   The `IdentitySettings` row is read **once** per request and threaded
   (`identity.request.settings_row_for(request)`), and the transcript is **one** query for N turns
   including the tool turns, so the "Jump to" strip is built from rows already fetched.

7. **The drift-guard contract — three assertions, one module (spec §10.1).** They live in
   `foundation/tests/test_settings_help.py`, they are the owner's sustainability requirement, and
   they must fail the suite:
   - **1.** `{c.route_name for c in CARDS} == {e.url_name for _g, es in SETTINGS_GROUPS for e in es}`,
     symmetric, **plus `card.gate == entry.gate` for every route**.
   - **2.** every card names a route this box can `reverse()`.
   - **3.** every anchor a card cites exists **in the RENDERED page body**, never in template
     source — `console.html`'s `{% if cold_start %}`/`{% else %}` split means a source grep can
     pass on a page whose live HTML has no such id (§1.2, §10.1, decision 16).

   Assertion 3 sweeps all eleven pages under **one** condition set: enterprise posture, signed in
   as an administrator, every role bound, and the sweep **pins its own `FARABUNKER_FEATURES`**
   rather than inheriting the ambient one.

8. **Two card rules no test can catch, so they are stated and must be honoured (spec §3.1, §13.1).**
   **(a)** A card describes the controls **its own page renders, and no others** — the worked case
   is `library_posture`, an `IdentitySettings` column edited only on **Identity & security**, so
   the `identity-settings` card carries it and the `rag-settings` card **must not**.
   **(b)** A `HelpField` whose control **only renders in some postures says so in its `meaning`** —
   `identity/templates/identity/settings.html:122` branches on `posture == "personal"` only, so
   library posture is a real select on an open **and** an enterprise box and a hidden input with an
   explanation on a personal one. Deep links are unaffected: the `.field` wrapper that carries the
   anchor renders in all three postures and only its contents branch.

9. **The import law, unchanged (ADR 0015:92-120).** `foundation/settings_help.py` is **pure** — no
   Django import of any kind, no database, no import of any non-pure module — which is what makes
   it importable from `agents/`. `agents/` may not import `tools/` at all. `identity/` is reachable
   only through `identity.contracts`, `identity.access`, `identity.request`, `identity.audit`.
   `agents/runtime/prompt.py` **is not modified by this phase** (§5.2, decision 6).

10. **New tools follow `docs/EXTENDING.md`'s three steps, and the two guard-list edits land in the
    SAME commit** (spec §5.5): `TOOL_MODULES` (`foundation/ops/tests/test_column_boundaries.py:185-189`)
    and `_REGISTRATION_MODULES` (`:217-228`).

11. **No migration in this phase (spec §12).** Verified against the tree at plan time:
    `agents/migrations/` ends at `0010_chat_settings.py`; `identity/migrations/` ends at
    `0003_entitlement_and_grant.py`. This phase adds no model and no column, so `agents/0011` is
    **not** written by it. If a reviewer ever overrules §7's mechanism and wants a
    `Conversation.surface` column, that is one migration and it would land as `agents/0011_…`.
    `makemigrations --check --dry-run` exits 0 at every task boundary.

12. **Every task ships unit tests and updated docs in the same commit** (ADR 0008). TDD step
    order: the failing test comes first, is run and **seen to fail**, and only then is the
    implementation written. A task with code and no test is not done.

13. **Tests that depend on posture or feature-flag state pin their own state.**
    `identity.testing.posture(...)` is the context manager; `settings.FARABUNKER_FEATURES` is
    overridden explicitly. No test inherits a posture from another test's leftovers.

14. **Never-500.** Every response on every surface, for every principal, in every posture, is an
    honest status with no `"Traceback"` in the body. A row-addressed URL a principal may not see
    answers **404**, never 403 — the rule `agents/chat/service.py:486-488` states.

---

## Conventions for the executing agents

These are how this branch is worked, not what is built. They bind every task.

1. **Run the suite against this branch's OWN private database.** Export, in the host venv:

   ```bash
   export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
   ```

   Port **5433** is this worktree's preview Postgres; `farabunker_impl` is this session's own
   name. **Never** `farabunker` or `test_farabunker` — concurrent sessions race each other's
   create/drop-database lifecycle (`docs/DEV.md:911-928`).

2. **Every gate runs in the FOREGROUND, in the host venv.** `.venv/bin/pytest`, never a background
   job, never inside a container. A gate whose output nobody read is not a gate.

3. **The full-suite cap is shared with another session: at most ONE concurrent full suite** until
   the orchestrator lifts it. Per-task gates are narrow (`.venv/bin/pytest -q <paths>`); the whole
   suite runs only at the task boundaries that call for it, and at Task 14.

4. **One git command per Bash call.** No `&&`-chained git.

5. **Do not push.** The orchestrator says when. Commit locally, one commit per task, message shape
   `<type>(settings): <what>` as each task's final step spells out.

6. **No hand database edits.** Schema and bookkeeping go through migrations and tested commands;
   this phase writes neither.

7. **Work in the worktree named above.** Never in the repository's root checkout,
   which is production's bind mount.

---

## The acceptance gate

Spec §14's ten done-when drills are the acceptance gate, verbatim. They are reproduced in this
plan's **Smoke Checklist** at the end and are Task 14's work. Three of them are *about* a posture
and cannot be run anywhere else — **1b** and **2** need an **open-posture** box, **1c** a
**personal** one — and drills **3, 4, 9 and 10 are suite runs**, not browser drills.

The two that most often get built wrongly, restated because an implementer reading one task in
isolation would miss them:

- **Drill 1 — the flagship.** From `/inference/`, "how do I make the library admin-only?" must be
  answered with **Identity & security** and its **library posture** control, and the "Jump to"
  strip must link that control's anchor on `identity-settings`. **An answer that says "Library"
  fails this criterion** — `rag-settings` owns the seven library *forms*, none of which is a
  posture. This is the drill the whole feature is judged by, so it must not be satisfiable by a
  wrong answer.
- **Drill 7 — the gate drill's second half.** Sign out **entirely** and open `/setup/`, the box's
  one public settings page: it renders, and **the panel context key is absent**, `?assistant=1` in
  the URL included. Asserted on the context, not on the rendered HTML, because the defect being
  guarded is work done before a template discards it.

---

## File Structure

**New files (11):**

| Path | Responsibility |
|---|---|
| `foundation/settings_help.py` | **Rule-1 pure leaf.** The gate constants, `HelpField`, `HelpCard`, `CARDS` (one per settings page), `CONTENT_HASH`, and three accessors — `card_for`, `card_routes`, `page_choices`. No Django, no DB, no non-pure import. |
| `foundation/tests/test_settings_help.py` | The three drift assertions, the hash tests, the purity test. |
| `agents/settings_tools.py` | The two `ToolSpec`s and their runners. Reads `foundation.settings_help` and `identity.access`; imports no service layer at module scope. |
| `agents/tests/test_settings_tools.py` | Registration, the enum-is-the-index pin, the audience refusal, the guide-only pin, the prompt-untouched pin. |
| `agents/chat/context_processors.py` | `settings_assistant(request)` — the panel's context, behind two guard clauses. |
| `agents/chat/views/assistant.py` | The three class-S views: `assistant_ask`, `assistant_reset`, `assistant_panel`. |
| `agents/chat/assistant_urls.py` | The three routes, mounted at `/settings/assistant/` by `config/urls.py`. |
| `agents/chat/templates/chat/_assistant_panel.html` | The panel fragment: transcript, "Jump to" strip, composer or install offer, error slot, and (Task 11) the one script. |
| `agents/chat/tests/test_settings_surface.py` | The two wrappers, the five list surfaces, the row-addressed door's ten actions, and what is deliberately left un-narrowed. |
| `agents/chat/tests/test_assistant_panel.py` | The processor's guards, the three routes, the gate matrix, the links whitelist, the JS-off path, the query budget, the one script. |
| `docs/adr/0018-settings-assistant.md` | **Task 13**, written last, after the number is re-checked. |

**Modified files (23):**

| Path | What changes |
|---|---|
| `foundation/settings_area.py:52-54` | The three gate constants become an import from `foundation.settings_help` (re-exported, so every existing importer keeps working). |
| `foundation/templates/_shell.html` | `--target-wash` in both token blocks; the `.settings-main :target` rule; the six promoted composer selectors and the `.composer-card > form` rule. |
| `foundation/templates/_settings.html` | The `{% if identity_is_admin %}`-gated panel include, inside `.settings-main`. |
| Eleven settings templates | Stable `id=` anchors on the sections each card's fields name (Task 2 names each file and line). |
| `agents/apps.py` | One `register_tool` pair in `ready()`, beside the flow tool. |
| `agents/defaults.py` | The `settings-helper` `AgentSpec` and `SETTINGS_SURFACE_SLUGS`. |
| `agents/visibility.py` | `chat_surface_agents` and `chat_surface_conversations` — two wrappers on the one gate. |
| `agents/chat/views/conversations.py:156,165,234` | Three call sites move to the wrappers. |
| `agents/chat/views/workstreams.py:401` | One call site moves to `chat_surface_agents`. |
| `agents/chat/sidebar.py:161` | The one `base` queryset moves to `chat_surface_conversations`. |
| `agents/chat/views/all_conversations.py:137` | The one `base` queryset moves to `chat_surface_conversations`. |
| `agents/chat/service.py:493` | `visible_conversation_or_404`'s single call, unconditionally narrowed. |
| `agents/chat/templates/chat/_composer.html` | One new optional parameter, `composer_next`. |
| `agents/chat/templates/chat/base.html` | The six composer selectors and the structural rule are **removed** (they move to `_shell.html`). |
| `agents/chat/views/defaults.py:54-55` | Both return paths honour a validated `next`. |
| `agents/chat/views/__init__.py` | The three assistant views exported. |
| `config/settings.py:356-379` | The context processor registered beside the three column processors already there. |
| `config/urls.py:34` | `path("settings/assistant/", include("agents.chat.assistant_urls"))`. |
| `identity/routes.py` | Three `ROUTE_RULES` entries, class **S**. |
| `identity/tests/test_route_matrix.py:92` | Three `_DRIVERS` entries. |
| `foundation/ops/tests/test_column_boundaries.py:185-189, :217-228` | `agents/settings_tools.py` added to both lists. |
| `foundation/ops/tests/test_css_ownership.py` | This phase's own composer-placement assertion. |
| `docs/EXTENDING.md`, `README.md`, `agents/README.md`, `agents/chat/README.md`, `foundation/README.md`, `docs/DEV.md` | §13.2's documentation pass. |

---

## Decisions this plan makes on top of the spec

The spec settles the design. These are the four execution calls the spec left to a plan author,
each stated so a reviewer can reject it rather than discover it.

1. **Drift assertions 1 and 2 land in Task 1; assertion 3 lands in Task 2, with the anchors.**
   The spec's §14 step 1 bundles all three with the anchors. Splitting on the *rendered-body* line
   is what makes both halves real TDD: assertion 1 is the failing test that drives `CARDS` into
   existence (it needs no anchors and no rendering), and assertion 3 is the failing test that
   drives the eleven templates' `id=` attributes in. Bundled, assertion 1 would be written already
   green. Nothing is deferred: both tasks are in the same plan, back to back, and Task 2 is not
   optional.

2. **The collapsed-state query pins ride with Task 8 (the processor); the open-state and
   fragment-route pins ride with Task 10 (the panel).** The spec mandates the numbers, not their
   task. A pin lives with the code whose cost it measures, so a reviewer rejecting Task 8 rejects
   its own budget rather than a budget task three commits later.

3. **Task 7 merges `origin/main` before it moves any CSS**, because the hygiene sweep lands
   CSS-block-only changes to `_settings.html` and the settings leaves on `main` and this phase's
   §14 step 6 is explicitly "after the hygiene sweep lands". Task 14 merges again — that one is the
   final pre-review merge and is expected to be trivial or empty.

4. **`agents/chat/views/workstreams.py:792` is left un-narrowed, deliberately, and pinned.** The
   spec's §7.1.2 names `:373` as the seventh call site that is safe only by accident. There is a
   second of the identical shape at `:792` (`workstream_consolidate`'s own
   `visible_conversations(principal).filter(pk=raw, workstream_id=stream.pk)`), safe for the same
   reason: an assistant conversation always carries `workstream_id IS NULL`, so neither can ever
   match one. Task 6 pins the null directly, which covers both, and names both in the pin's
   docstring so a later reader does not think one was missed.

---

## Spec-vs-tree reconciliations

Every `file:line` in the spec was re-read in the worktree at plan time. Twelve line numbers had
drifted by a line or two; the tree wins and this plan cites the verified number. None changes a
mechanic.

| Spec says | Tree says | Note |
|---|---|---|
| `tier_for` at `identity/routes.py:300` | **`:302`** | `ROUTE_RULES` is still `:43`; `chat-settings: "S"` still `:99`; `setup-index: "P"` still `:254`. |
| `--accent` at `_shell.html:95` (light) | **`:96`** | The dark block's `--accent` **is** `:155`, as cited. Light `:root` opens at `:89`; the dark block is `:148-162`. |
| `visible_conversation_or_404` at `service.py:493` | `def` at **`:479`**, the `get_object_or_404(...)` line at **`:493`** | Both are right about different lines; the edit is on `:493`. |
| `config/settings.py`'s "the two that are already there" | **three** column processors, `:367`, `:371`, `:378` | Vision features, identity, models availability. The new one goes after `:378`, inside the list that ends at `:379`. |
| `identity/templates/identity/settings.html:120-135` | The `.field` wrapper is **`:110-135`**; the `{% if posture == "personal" %}` is **`:122`**, `{% else %}` `:126`, `{% endif %}` `:133` | The hidden-input branch is `:123-125`, as cited. |
| §7.1's table lists seven `visible_conversations` call sites | there are **eight** | The eighth, `workstreams.py:792`, is the same accidentally-safe shape as `:373`. See decision 4 above. |
| §18 item 4: `docs/EXTENDING.md:134` should cite `_REGISTRATION_MODULES` as `:217-231` | the tuple is **`:217-228`** — `:228` is its closing `)`, `:229` is blank, `:230` opens `RUNTIME_MODULES`' comment | **`EXTENDING.md:134` is CORRECT today** and applying the spec's number would break it. Task 12 Step 2 re-derives the span instead. |
| §18 item 4 lists **six** stale citations | there is a **seventh** | `docs/EXTENDING.md:138`'s `:486`, which names the same test as `:104`'s `:485`. Task 12 Step 2 covers it. |
| §11 / §6.2: the row is read once and threaded | `principal_for_request` re-reads the singleton itself when it is not handed one (`identity/request.py:91` → `identity/access.py:67`) | Every caller in this plan passes `settings_row=` — the processor, `assistant_ask` and `assistant_reset`. `foundation/setup/views.py:185-187` is the in-tree precedent. |
| `visible_conversation_or_404`'s "404 rather than 403" sentence at `service.py:481-484` | **`:486-488`** | `:480-482` is the "written once … for the six copies across four modules" half. |
| `test_css_ownership.py`'s "the gate above is scoped to `agents/chat/templates/chat/`" at `:309-313` | begins at **`:310`** | `_CHAT_TEMPLATES` at `:107` and `test_no_settings_page_retypes_a_rule_settings_html_already_owns` at `:367` are exact. |

Confirmed exactly as cited and relied on unchanged: `agents/migrations/` ends at `0010`;
`identity/migrations/` ends at `0003`; `docs/adr/` ends at `0017-workstreams.md`, so **0018 is
free**; `SETTINGS_GROUPS` and its eleven entries at `foundation/settings_area.py:84-110`, with
`Install guides` at `:98` and `_may_see` at `:113`; the gate constants at `:52-54`;
`FARABUNKER_FEATURES` defaulting to `"vision"` at `config/settings.py:170`; `config/urls.py:34`
mounting `foundation.settings_area`; `library_posture` at `identity/models.py:76`,
`admin_sees_content` at `:90`, `session_idle_minutes` at `:95`; `identity/access.py:346`'s
"`library_posture` is only EDITABLE on the enterprise page"; `is_admin` at `identity/access.py:109`
and its open-box branch at `:116-119`; `settings_row_for` at `identity/request.py:21`;
`visible_conversations` `:64`, `visible_agents` `:142`, `installed_agent_slugs` `:166`,
`visible_turn` `:222`, `set_conversation_archived` `:411`, `may_post_to` `:545`,
`create_conversation` `:761` and the never-touch-these-managers ruling at `:765-771`;
`start_turn` at `agents/chat/service.py:174` with its required keyword-only `actor` at `:179-183`;
`validated_next_url` at `:662` reading `request.POST.get("next", "").strip()` at `:693`; the three
poller constants at `:93-95`; `render_answer` at `agents/chat/rendering.py:598` and its
never-a-link sentence at `:608-609`; `_is_xhr` at `agents/chat/views/turns.py:44-50`;
`default_install`'s two `redirect(reverse("chat-index"))` returns at
`agents/chat/views/defaults.py:54-55`; `TOOL_MODULES` at
`foundation/ops/tests/test_column_boundaries.py:185-189` and `_REGISTRATION_MODULES` at `:217-228`
(**not** the spec's `:217-231` — see the row for it in the table above);
`test_no_tool_runner_blocks_on_a_queue_job` at `:352`,
`test_every_registered_runner_lives_in_a_swept_module` at `:449`,
`test_no_tool_module_imports_its_service_layer_at_module_scope` at `:492` and
`test_the_registration_modules_list_is_not_silently_empty` at `:516` — the four numbers §18's
correction table needs; `_DRIVERS` at `identity/tests/test_route_matrix.py:92` and
`test_every_route_has_a_driver` at `:633`; the console's six anchor occurrences at
`console.html:1007/1067`, `:1016/1084`, `:1021/1123`; the cross-request deep link at
`_installed_row.html:145`; `_viewing` at `foundation/tests/test_page_names.py:113-126` and
`_every_surface_available` at `:134-139`; `test_settings_area.py`'s
`test_every_entry_names_a_route_this_box_owns` at `:43-48`; the sidebar drift test at
`foundation/tests/test_shell.py:381-437`; `_composer.html`'s `composer_workstream` hidden field at
`:79-83` and its `PARAMETERIZED ONLY WHERE THE SURFACES GENUINELY DIFFER` rule at `:16-17`; and the
`localStorage` idiom at `tools/vision/tests/test_views_create.py:1000-1004`.

---

### Task 1: `foundation/settings_help.py` — the pure leaf, the card table, the content hash, and drift assertions 1 and 2

**Files:**
- Create: `foundation/settings_help.py`
- Create: `foundation/tests/test_settings_help.py`
- Modify: `foundation/settings_area.py:52-54` (the three constants become an import)
- Modify: `foundation/README.md` (the new pure leaf, in the same commit)

**Interfaces:**
- Consumes: `foundation.settings_area.SETTINGS_GROUPS` — **in the test only**. The module itself
  imports nothing from `settings_area`; the dependency runs the other way, which is what keeps
  `settings_help` pure.
- Produces, for Tasks 2, 4, 8 and 10:
  - `EVERYONE: str = "everyone"`, `ADMIN: str = "admin"`, `ACCOUNTS_ADMIN: str = "accounts-admin"`
  - `HelpField(name: str, anchor: str, meaning: str, effects: str)` — frozen dataclass
  - `HelpCard(route_name: str, title: str, gate: str, purpose: str, fields: tuple[HelpField, ...])`
    — frozen dataclass
  - `CARDS: tuple[HelpCard, ...]`
  - `CONTENT_HASH: str` — 12 lowercase hex characters, computed at import
  - `card_for(route_name: str) -> HelpCard | None`
  - `card_routes() -> frozenset[str]`
  - `page_choices() -> tuple[str, ...]` — route names in table order

- [ ] **Step 1: Write the failing drift tests (assertions 1 and 2) and the hash tests**

Create `foundation/tests/test_settings_help.py`:

```python
"""The help-card registry, its content hash, and the first two drift
assertions (spec §10.1, §10.2).

NEXT TO `test_settings_area.py` ON PURPOSE, and following its
`TestTheTable` shape (`:42-53`): that module holds `SETTINGS_GROUPS`
honest, this one holds `CARDS` honest against it. The THIRD assertion --
every cited anchor exists in the RENDERED page -- needs a real client and
eleven real GETs, so it lives in its own class added by Task 2.
"""
from __future__ import annotations

import dataclasses

import pytest
from django.urls import reverse

from foundation.settings_area import SETTINGS_GROUPS
from foundation.settings_help import (
    ACCOUNTS_ADMIN, ADMIN, CARDS, CONTENT_HASH, EVERYONE, HelpCard, HelpField,
    _content_hash, card_for, card_routes, page_choices,
)

# `django_db` PER CLASS, not module-wide: `TestTheContentHash` and
# `TestPurity` touch no database at all -- one hashes tuples and the
# other reads a file -- and a module-level mark would wrap both in a
# transaction for nothing. The three classes that DO need it are the ones
# that `reverse()` a route or drive the client.


def _entries():
    return [entry for _group, entries in SETTINGS_GROUPS for entry in entries]


@pytest.mark.django_db
class TestTheDriftGuard:
    def test_every_settings_entry_has_a_card_and_every_card_has_an_entry(self):
        """ASSERTION 1 (spec §10.1), and it is SYMMETRIC on purpose: a
        page added without a card fails, and a card left behind by a page
        that was removed fails too."""
        assert {card.route_name for card in CARDS} == {e.url_name for e in _entries()}

    def test_every_card_carries_the_same_gate_its_sidebar_entry_does(self):
        """The other half of assertion 1 -- what makes the spec's
        accepted duplication safe. `route_name`, `title`-versus-`label`
        and `gate` are stated in two tables; this is the one that closes
        `gate`."""
        gates = {e.url_name: e.gate for e in _entries()}
        assert {card.route_name: card.gate for card in CARDS} == gates

    def test_every_card_names_a_route_this_box_owns(self):
        """ASSERTION 2 (spec §10.1) -- `test_settings_area.py:43-48`'s
        anti-rot test, restated for the card table: a table of url names
        nothing reverses is a sidebar of 500s waiting for the first admin
        to open it."""
        for card in CARDS:
            assert reverse(card.route_name)

    def test_every_gate_is_one_the_settings_area_understands(self):
        assert {card.gate for card in CARDS} <= {EVERYONE, ADMIN, ACCOUNTS_ADMIN}

    def test_every_card_has_at_least_one_field(self):
        """A card that named no control at all would be help text with a
        hole in it, and assertion 3 would sweep nothing for that page."""
        for card in CARDS:
            assert card.fields, card.route_name

    def test_no_two_fields_on_one_card_share_an_anchor(self):
        for card in CARDS:
            anchors = [field.anchor for field in card.fields]
            assert len(anchors) == len(set(anchors)), card.route_name


@pytest.mark.django_db
class TestTheAccessors:
    def test_card_for_finds_a_card_and_answers_none_for_a_stranger(self):
        assert card_for("identity-settings").route_name == "identity-settings"
        assert card_for("not-a-route") is None

    def test_card_routes_is_a_frozenset_of_every_route_name(self):
        routes = card_routes()
        assert isinstance(routes, frozenset)
        assert routes == {card.route_name for card in CARDS}

    def test_page_choices_is_the_table_order_not_a_sorted_set(self):
        """The tool schema's `enum` is this tuple (spec §5.2), and the
        order a model reads the pages in is the order the sidebar offers
        them -- so it is the TABLE's order, never `sorted()`."""
        assert page_choices() == tuple(card.route_name for card in CARDS)


class TestTheContentHash:
    def test_the_hash_is_twelve_lowercase_hex_characters(self):
        assert len(CONTENT_HASH) == 12
        assert all(character in "0123456789abcdef" for character in CONTENT_HASH)

    def test_the_hash_changes_when_any_card_text_changes(self):
        """THE ANTI-VACUOUS PIN, without which the hash is decoration
        (spec §10.2). A COPY of the table is mutated and rehashed -- the
        module's own `CARDS` is never touched, so no other test in this
        run sees a different hash than the one at import."""
        first = list(CARDS)
        changed = (dataclasses.replace(first[0], purpose=first[0].purpose + " And one more word."),
                   *first[1:])
        assert _content_hash(changed) != CONTENT_HASH

    def test_the_hash_changes_when_a_field_changes(self):
        card = CARDS[0]
        field = card.fields[0]
        moved = dataclasses.replace(
            card, fields=(dataclasses.replace(field, effects=field.effects + " Also this."),
                          *card.fields[1:]))
        assert _content_hash((moved, *CARDS[1:])) != CONTENT_HASH

    def test_the_hash_changes_when_the_order_changes(self):
        """Order is part of the content: `page_choices()` is derived from
        it and the tool schema's `enum` carries it to the model."""
        assert _content_hash(tuple(reversed(CARDS))) != CONTENT_HASH

    def test_the_hash_is_stable_across_processes(self):
        """`hashlib`, never Python's built-in `hash()`, which is SALTED
        PER PROCESS and would answer differently on every boot (spec
        §3.3, decision 3). Asserted by recomputing from the same input
        rather than by trusting the docstring."""
        assert _content_hash(CARDS) == CONTENT_HASH
        assert _content_hash(CARDS) == _content_hash(tuple(CARDS))

    def test_a_separator_that_could_appear_in_a_route_name_is_not_used(self):
        """Two DIFFERENT tables must not serialize to the same string.
        A field boundary a route name could contain would let a rename
        cancel out an edit.

        THE PAIR HAS TO ACTUALLY COLLIDE UNDER THE BAD SEPARATOR or this
        test proves nothing: `route_name="a|b", title="T"` and
        `route_name="a", title="b|T"` both join to `"a|b|T|…"` under a
        naive `|`, which is the whole point. (A pair like `"a-b"`/`"b|T"`
        differs under `|` too, so it would pass with the very separator
        it claims to forbid.) With `_SEP="\\x1f"` they differ, and this
        stays green for the right reason."""
        one = (HelpCard(route_name="a|b", title="T", gate=ADMIN, purpose="P",
                        fields=(HelpField(name="n", anchor="x", meaning="m", effects="e"),)),)
        two = (HelpCard(route_name="a", title="b|T", gate=ADMIN, purpose="P",
                        fields=(HelpField(name="n", anchor="x", meaning="m", effects="e"),)),)
        assert _content_hash(one) != _content_hash(two)


class TestPurity:
    def test_the_module_imports_no_django_and_no_other_module_in_this_repository(self):
        """SPEC §3.1, and it is LOAD-BEARING rather than stylistic: the
        tool runners in `agents/` read this table, and a module that
        imported `django.urls` or `identity.access` -- as
        `foundation/settings_area.py:45-50` does -- would make that read a
        cross-column import of a non-pure module.

        Asserted on the module's own SOURCE, the file-text idiom
        `foundation/ops/tests/test_column_boundaries.py` uses throughout,
        because an import that has already happened cannot be un-imported
        inside a test process.

        `"foundation"` IS IN THE DENYLIST, and that is not pedantry: a
        `from foundation.settings_area import ...` here would be a real
        import CYCLE, because that module imports this one. This module
        imports NO sibling at all -- there is nothing in `foundation/`
        it needs, and any future need is a sign the thing being reached
        for belongs here instead.
        """
        import ast
        import pathlib

        import foundation.settings_help as module

        source = pathlib.Path(module.__file__).read_text()
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = [name for name in imported
                     if name.split(".")[0] in {
                         "django", "identity", "agents", "models", "tools", "config",
                         "foundation"}]
        assert not forbidden, f"foundation/settings_help.py must stay pure; found {forbidden}"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest foundation/tests/test_settings_help.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'foundation.settings_help'`.

- [ ] **Step 3: Write `foundation/settings_help.py`**

Create the file. **Every card below is real content, not a sketch**: eleven cards, twenty-six
fields, one per settings page in `SETTINGS_GROUPS` order. Global Constraint 8's two rules are
honoured throughout — in particular `library_posture` is on the `identity-settings` card and
**not** on the `rag-settings` one, and its `meaning` says out loud that the control's *rendering*
depends on the posture.

```python
"""The help-card registry -- what the settings assistant knows about
this box's settings pages (spec §3).

PURE, IN THE RULE-1 SENSE, AND THAT IS LOAD-BEARING RATHER THAN A STYLE
PREFERENCE. No Django import of any kind, no database, no import of any
non-pure module. The tool runners that read this table live in
`agents/settings_tools.py`, and a module that imported `django.urls` or
`identity.access` -- as `foundation/settings_area.py` does -- would make
that read a cross-column import of a NON-PURE module, which ADR 0015's
import law forbids. Pure, it is importable by any column exactly as
`foundation/format.py` and `foundation/files.py` already are.

TWO TABLES, ONE TRUTH. `foundation/settings_area.py::SETTINGS_GROUPS`
decides what the settings area IS -- the order, the gates, the labels.
This table says what each of those pages is FOR and what every control on
it means. `route_name`, `title`-versus-`label` and `gate` are therefore
stated twice, which is the identical shape `SETTINGS_GROUPS` and
`_settings.html` already have, and it is closed the identical way: the
drift test in `foundation/tests/test_settings_help.py` asserts the two
tables name EXACTLY the same routes with EQUAL gates.

TWO RULES NO TEST CAN CATCH, so they are written here and in
`docs/EXTENDING.md`'s "Adding a settings page" recipe:

  1. A CARD DESCRIBES THE CONTROLS ITS OWN PAGE RENDERS, AND NO OTHERS.
     The drift tests only ask whether the anchor exists on the page the
     card names, so a card that claimed a neighbouring page's control
     would pass every one of them and misroute every answer about it.
     The worked case is the one this feature is judged by: library
     posture is an `IdentitySettings` column edited ONLY on Identity &
     security, so the `identity-settings` card carries it and the
     `rag-settings` card must not -- that page owns the library's
     numeric limits, none of which is a posture.

  2. A FIELD WHOSE CONTROL ONLY RENDERS IN SOME POSTURES SAYS SO IN ITS
     `meaning`. The rendered-body assertion drives ONE posture and cannot
     see the others. Library posture is the worked case again: a real
     select on an open and an enterprise box, a hidden input with an
     explanation on a personal one. Deep links are unaffected either way
     -- the section wrapper that carries the anchor renders in all three
     postures and only its contents branch -- so this is a rule about
     what the card SAYS, never about whether the link lands.

THE GATE CONSTANTS LIVE HERE, and `foundation/settings_area.py` imports
them (an intra-column import, one line). ONE definition, so "admin"
cannot come to mean two things. `HelpCard.gate` is carried as DATA THE
MODEL READS, never as a filter this module applies: the gate-evaluation
logic (`_may_see`) stays in `settings_area.py` and is not duplicated
here. It has NO runtime consumer in v1 -- the drift test is its only
reader -- and that is worth saying so a reviewer does not go hunting for
one. It is carried because a card that did not say who may open its page
would be help text with a hole in it, and because a member-facing variant
would need it on day one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

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


# ONE CARD PER `SETTINGS_GROUPS` ENTRY, IN THE SAME ORDER. The drift test
# is symmetric, so this table and that one add and retire pages together.
CARDS: tuple[HelpCard, ...] = (
    # --- Setup --------------------------------------------------------
    HelpCard(
        route_name="inference-console",
        title="Models",
        gate=ADMIN,
        purpose=(
            "Where this box learns about the models it can use and decides which one answers "
            "each job. A connection is one reachable model on one model server; a role is a job "
            "this platform needs a model for -- conversation, library answers, library "
            "embeddings, image generation. Nothing on this box runs until at least one role has "
            "a connection assigned to it."
        ),
        fields=(
            HelpField(
                name="In use",
                anchor="in-use",
                meaning=(
                    "Every role this box registers, and which registered connection currently "
                    "answers it. Each row picks a connection for one role."
                ),
                effects=(
                    "Assigning a connection to a role is what makes that surface work: with the "
                    "conversation role unassigned there is no chat, with the embedding role "
                    "unassigned nothing can be added to the library. Changing an assignment "
                    "takes effect on the next job; it never re-runs an old one."
                ),
            ),
            HelpField(
                name="Getting models",
                anchor="getting-models",
                meaning=(
                    "What each role needs from a model, and how to install one on a supported "
                    "model server. This section explains rather than changes anything."
                ),
                effects=(
                    "Nothing. It is the checklist that tells you which capability a role wants "
                    "before you go and install something that has it."
                ),
            ),
            HelpField(
                name="Add a connection manually",
                anchor="add-connection",
                meaning=(
                    "Registers a model that is running somewhere this box cannot detect on its "
                    "own -- another machine, or one whose model server does not report its "
                    "capability or embedding dimension."
                ),
                effects=(
                    "Adds a row to Registered connections. It does not assign the connection to "
                    "any role: it becomes available in the role pickers, and stays unused until "
                    "you pick it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="rag-settings",
        title="Library",
        gate=ADMIN,
        purpose=(
            "The limits this box puts on its own document library: how much history it keeps, "
            "how large an upload may be, how long a media file may run, how many pages a "
            "document may have, and how retrieval searches. It sets no permissions -- who may "
            "READ an unlabelled document is decided on Identity & security, not here."
        ),
        fields=(
            HelpField(
                name="Retention",
                anchor="retention",
                meaning="How many recent question-and-answer entries the library keeps.",
                effects=(
                    "Older entries beyond the limit stop being listed. It never deletes a "
                    "document or its chunks -- only the history of what was asked."
                ),
            ),
            HelpField(
                name="Maximum upload size (GB)",
                anchor="upload-limit",
                meaning="The largest single file this box accepts into the library.",
                effects=(
                    "A larger file is refused at upload with an honest message. Files already "
                    "ingested are untouched."
                ),
            ),
            HelpField(
                name="Maximum media duration (minutes)",
                anchor="media-duration-limit",
                meaning=(
                    "The longest audio or video file this box will transcribe. Media is "
                    "transcribed before it becomes searchable text."
                ),
                effects=(
                    "A longer file is refused before any transcription job is queued, so it "
                    "cannot occupy the machine's one execution slot for hours."
                ),
            ),
            HelpField(
                name="Maximum document pages",
                anchor="document-page-limit",
                meaning="The largest page count this box will extract text from.",
                effects=(
                    "A longer document is refused at ingest rather than part-way through, so the "
                    "library never holds half a book."
                ),
            ),
            HelpField(
                name="Retrieval",
                anchor="retrieval",
                meaning=(
                    "Three controls that decide what a library answer is built from: how many "
                    "chunks are retrieved per question, the minimum similarity a chunk must "
                    "reach to be used at all, and whether keyword matching runs alongside "
                    "semantic search."
                ),
                effects=(
                    "They change what the next library answer sees. Turning keyword matching on "
                    "saves immediately but changes nothing about what is actually searched until "
                    "the index is re-encoded, which is a job started from Models."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="chat-settings",
        title="Chat",
        gate=ADMIN,
        purpose=(
            "How a conversation's prompt is built on this box. One box-wide operator policy, not "
            "a per-person preference -- it applies to every conversation and every agent, "
            "including delegated ones."
        ),
        fields=(
            HelpField(
                name="Tell the model the date and time",
                anchor="time-aware",
                meaning=(
                    "Adds one line to every conversation's prompt naming the current day, date, "
                    "time and UTC offset from this box's own clock, and stamps each earlier "
                    "message with when it was sent."
                ),
                effects=(
                    "With it off, a model falls back on whatever its training implies \"now\" is "
                    "and can report the current year as a set of future dates. Turning it off "
                    "removes both the date line and the message stamps. The clock reads in this "
                    "box's configured time zone."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="vision-engine-files",
        title="Engine files",
        gate=ADMIN,
        purpose=(
            "The files the image engine has left on disk -- what it was given to work from, and "
            "what it produced. A housekeeping page: it removes files, and changes no setting."
        ),
        fields=(
            HelpField(
                name="Output folder",
                anchor="output-folder",
                meaning="Images the engine has generated, newest first, with their sizes.",
                effects=(
                    "Selecting files and confirming the delete removes them from disk "
                    "permanently. A generated image that was saved into the library is a "
                    "separate copy and is not affected."
                ),
            ),
            HelpField(
                name="Input folder",
                anchor="input-folder",
                meaning="Files handed to the engine as inputs -- source images for an edit.",
                effects=(
                    "The same permanent delete. Removing an input does not change any image "
                    "already generated from it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="setup-index",
        title="Install guides",
        gate=EVERYONE,
        purpose=(
            "The one page in the settings area that everybody can open, signed in or not, "
            "because it is what a person needs BEFORE they can sign in to a box whose engines "
            "are not running. It explains rather than configures: there is no control on it and "
            "nothing on it writes anything."
        ),
        fields=(
            HelpField(
                name="How models reach farabunker",
                anchor="how-models-reach-farabunker",
                meaning=(
                    "What a model server is, how this box talks to one, and what has to be true "
                    "before a model can be assigned to a role."
                ),
                effects="Nothing. It is reading.",
            ),
            HelpField(
                name="What each feature needs",
                anchor="what-each-feature-needs",
                meaning=(
                    "Per-surface checklist: which role each part of the box needs bound before "
                    "it will work. Shown to administrators only, on a page anybody may open."
                ),
                effects=(
                    "Nothing. It tells you which role is missing; Models is where you assign it."
                ),
            ),
        ),
    ),
    # --- Access -------------------------------------------------------
    HelpCard(
        route_name="identity-users",
        title="Accounts",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The people who may sign in to this box. This whole group of pages exists only once "
            "the box has left the open posture: with no accounts there is nobody to administer, "
            "so the sidebar does not offer it at all."
        ),
        fields=(
            HelpField(
                name="Create an account",
                anchor="create-account",
                meaning=(
                    "Adds one account with a username and a password, optionally an "
                    "administrator. The administrator checkbox is not offered in the personal "
                    "posture, where every account is an administrator already."
                ),
                effects=(
                    "The account can sign in immediately. It owns nothing until it creates "
                    "something, and it holds no entitlements until it is granted some."
                ),
            ),
            HelpField(
                name="The accounts table",
                anchor="account-list",
                meaning=(
                    "Every account, with the controls that act on one: deactivate or reactivate, "
                    "promote to administrator or demote, and set a new password."
                ),
                effects=(
                    "Deactivating an account ends its ability to sign in and leaves everything it "
                    "owns in place. Demoting removes administrator surfaces from it; it does not "
                    "remove what it can read, which entitlements decide."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="identity-groups",
        title="Groups",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "Named sets of accounts, so an entitlement or a share can be given to several people "
            "at once instead of one at a time. A group grants nothing by itself -- it is who, not "
            "what."
        ),
        fields=(
            HelpField(
                name="New group",
                anchor="new-group",
                meaning="Creates an empty group with a name.",
                effects="Nothing changes for anybody until the group has members and a grant.",
            ),
            HelpField(
                name="A group's own controls",
                anchor="group-list",
                meaning=(
                    "For each group: add a member, remove a member, and delete the group. Every "
                    "existing group is listed here, so on a box with no groups yet this section "
                    "is present but empty."
                ),
                effects=(
                    "Adding a member gives that account everything the group holds. Deleting a "
                    "group removes the grants that named it; it never deletes the accounts in it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="identity-entitlements",
        title="Entitlements",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The labels this box uses to decide who may read what. An entitlement is a name; "
            "labelling a document, a tool or an agent with it narrows that thing to the people "
            "who hold it. Granting one to an account or a group is done from that entitlement's "
            "own page."
        ),
        fields=(
            HelpField(
                name="New entitlement",
                anchor="new-entitlement",
                meaning="Creates a label with a name and a description.",
                effects=(
                    "Nothing is restricted by creating one. It starts mattering when something "
                    "is labelled with it."
                ),
            ),
            HelpField(
                name="The entitlements table",
                anchor="entitlement-list",
                meaning="Every entitlement on this box, each linking to its own page.",
                effects="Reading only. The per-entitlement page is where grants are edited.",
            ),
        ),
    ),
    HelpCard(
        route_name="chat-tool-entitlements",
        title="Tool access",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "Which entitlements a tool requires. An agent declares the tools it wants; this page "
            "decides which of them a given person actually gets. A tool with no entitlement is "
            "callable by everyone signed in."
        ),
        fields=(
            HelpField(
                name="A tool's labels",
                anchor="tool-labels",
                meaning=(
                    "One row per registered tool, each with the set of entitlements that tool "
                    "requires. Saving a row replaces that tool's labels."
                ),
                effects=(
                    "A labelled tool disappears from every agent run by somebody who holds none "
                    "of its entitlements -- and from the watcher and the command line entirely, "
                    "because automated callers hold no entitlements on this box."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="chat-agent-entitlements",
        title="Agent access",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The same labelling, one level up: which entitlements an agent or a saved flow "
            "requires before a person may run it at all. Tool access narrows what an agent can "
            "do; this narrows who can start one."
        ),
        fields=(
            HelpField(
                name="An agent's labels",
                anchor="agent-labels",
                meaning="One row per agent on this box, each with the entitlements it requires.",
                effects=(
                    "A labelled agent stops appearing in the picker for somebody who holds none "
                    "of its entitlements -- including the account that created it. Conversations "
                    "they already started stay readable."
                ),
            ),
            HelpField(
                name="A flow's labels",
                anchor="flow-labels",
                meaning="The same, for each saved flow.",
                effects=(
                    "A labelled flow is no longer offered to, or runnable by, somebody who holds "
                    "none of its entitlements."
                ),
            ),
        ),
    ),
    # --- Box ----------------------------------------------------------
    HelpCard(
        route_name="identity-settings",
        title="Identity & security",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "This box's own security posture: whether it has accounts at all, who may read a "
            "document carrying no label, whether an administrator may read other people's "
            "content, and how long a signed-in session may sit idle. Every one of these is "
            "box-wide, and the posture is the one that decides whether the rest of the Access "
            "group exists."
        ),
        fields=(
            HelpField(
                name="Posture",
                anchor="posture",
                meaning=(
                    "Open, personal or enterprise. Open means no accounts: everybody at the "
                    "keyboard is an administrator and there is nobody for anything to be hidden "
                    "from. Personal means accounts, all of them administrators. Enterprise means "
                    "accounts with ordinary members among them."
                ),
                effects=(
                    "Leaving open is what makes the Accounts, Groups, Entitlements, Tool access "
                    "and Agent access pages appear in the sidebar. Moving to personal resets "
                    "Library posture to open, because there is nobody to lock the library "
                    "against."
                ),
            ),
            HelpField(
                name="Library posture",
                anchor="library-posture",
                meaning=(
                    "Who may READ a document that carries no entitlement label: everybody signed "
                    "in while this is open, and only an administrator with content access while "
                    "it is locked. A labelled document is never affected -- the people who hold "
                    "its entitlements read it either way. THIS IS THE CONTROL THAT MAKES THE "
                    "LIBRARY ADMINISTRATOR-ONLY, and it lives here rather than on the Library "
                    "page, which owns the library's numeric limits and no permission at all. "
                    "How it is OFFERED depends on the posture: it is a real selector on an open "
                    "and on an enterprise box, and on a personal box it is not offered as a "
                    "choice at all -- the page shows it fixed at open, with its own explanation, "
                    "because every account there is an administrator already."
                ),
                effects=(
                    "Setting it to locked stops every ordinary member reading unlabelled "
                    "documents, immediately, on the next request. On an OPEN box it has no "
                    "effect at all -- everybody there already reads everything, so a lock has "
                    "nobody to lock out; it starts meaning something the moment the box leaves "
                    "the open posture."
                ),
            ),
            HelpField(
                name="Administrators may read other users' conversations and files",
                anchor="admin-sees-content",
                meaning=(
                    "Whether an administrator may read other people's CONTENT -- their "
                    "conversations, generated images, document bytes and job payloads -- as "
                    "opposed to merely administering the rows."
                ),
                effects=(
                    "Off by default, and that default is the decision: an administrator already "
                    "sees every row they need to run the box without it. Turning it on is "
                    "audited, and takes effect on the next request without a restart."
                ),
            ),
            HelpField(
                name="Session idle window (minutes)",
                anchor="session-idle",
                meaning=(
                    "How long a signed-in session may sit idle before it expires. Rolling: every "
                    "request resets the clock."
                ),
                effects=(
                    "Zero means the session ends when the browser closes. A change applies from "
                    "the next request; it does not sign anybody out retroactively."
                ),
            ),
        ),
    ),
)


# The separator is a control character, so it cannot appear in a route
# name, a title, a gate, a prose field, or anything else a card holds --
# which is what makes the serialization below UNAMBIGUOUS. A separator a
# field could contain would let one edit cancel another out.
_SEP = "\x1f"


def _content_hash(cards: tuple[HelpCard, ...] | None = None) -> str:
    """`sha256` over a DETERMINISTIC CANONICAL SERIALIZATION of `cards`,
    truncated to 12 hex characters.

    NEVER Python's built-in `hash()`, which is salted per process and
    would answer differently on every boot -- the one mistake that would
    make every job below silently useless.

    Takes the table as an argument, defaulting to `CARDS`, for exactly
    one reason: the anti-vacuous test mutates a COPY and rehashes it. No
    production caller passes anything.

    Twelve hex characters, because this is a CHANGE DETECTOR, not a
    signature. Its three jobs (spec §3.3): the assistant can cite its
    context version, so an answer in a transcript can be tied to the
    content that produced it; it is the key any future cached or derived
    artifact compares against before trusting itself; and a test pins
    that it CHANGES when any card changes, which is what makes the other
    two worth anything.
    """
    if cards is None:
        cards = CARDS
    parts: list[str] = []
    for card in cards:
        parts += [card.route_name, card.title, card.gate, card.purpose]
        for field in card.fields:
            parts += [field.name, field.anchor, field.meaning, field.effects]
        # A per-card terminator, so two adjacent cards cannot be
        # re-partitioned into a different pair with the same joined text.
        parts.append("")
    digest = hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


CONTENT_HASH: str = _content_hash()


def card_for(route_name: str) -> HelpCard | None:
    """The card for `route_name`, or `None`. A linear scan over eleven
    frozen rows -- no index, because building one would be a second copy
    of the same table to keep honest."""
    for card in CARDS:
        if card.route_name == route_name:
            return card
    return None


def card_routes() -> frozenset[str]:
    """Every route this registry covers.

    A `frozenset` because its hottest caller is a MEMBERSHIP TEST that
    runs on every page in the box: the panel's context processor checks
    `request.resolver_match.url_name not in card_routes()` before it
    reads anything at all, and that test must cost nothing.
    """
    return frozenset(card.route_name for card in CARDS)


def page_choices() -> tuple[str, ...]:
    """The route names the `settings.card` tool schema enumerates, IN
    TABLE ORDER -- which is sidebar order, not alphabetical. It becomes
    an `enum` in the JSON schema both wire adapters build, so a page name
    a model invents comes back as a parameter error rather than as a
    confident answer about a page that does not exist."""
    return tuple(card.route_name for card in CARDS)
```

- [ ] **Step 4: Move the gate constants and re-export them from `settings_area`**

`foundation/settings_area.py:52-54` today reads:

```python
EVERYONE = "everyone"
ADMIN = "admin"
ACCOUNTS_ADMIN = "accounts-admin"
```

Replace those three lines with the import, keeping the names importable from here so
`foundation/tests/test_settings_area.py:20-22` and every other existing importer is byte-identical
in behaviour:

```python
# THE GATE VOCABULARY LIVES IN `foundation/settings_help.py` (spec §3.1,
# author decision 1). ONE definition, so "admin" cannot come to mean two
# things in the two tables that both use it -- this module's `Entry.gate`
# and that module's `HelpCard.gate`, which a drift test compares for
# EQUALITY. An intra-column import, and re-exported here because every
# existing caller names this module.
from foundation.settings_help import (  # noqa: F401 -- re-exported on purpose
    ACCOUNTS_ADMIN, ADMIN, EVERYONE,
)
```

Place it with the other imports, **after** the `identity` imports at `:49-50`, so the import block
stays grouped standard-library / Django / first-party. It cannot create a cycle:
`settings_help` imports nothing at all.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/pytest foundation/tests/test_settings_help.py foundation/tests/test_settings_area.py foundation/tests/test_shell.py -q
```

Expected: PASS, all of them. `test_settings_area.py` and `test_shell.py` are run here on purpose —
they are the two modules that import the gate constants, and Step 4 is exactly the kind of move
that breaks an importer nobody thought about.

- [ ] **Step 6: Run the import-law and column-boundary gates**

```bash
.venv/bin/pytest foundation/ops/tests/ -q
```

Expected: PASS. A new `foundation/` module is exactly what those gates sweep.

- [ ] **Step 7: Update `foundation/README.md`**

Add `settings_help.py` beside `format.py` and `files.py` in the section that names this column's
pure leaves, in one or two sentences: it is the third `foundation/` module other columns import,
it is pure for that reason, and it is the registry `agents/settings_tools.py` reads.

- [ ] **Step 8: Commit**

```bash
git add foundation/settings_help.py foundation/tests/test_settings_help.py foundation/settings_area.py foundation/README.md
git commit -m "feat(settings): the help-card registry, its content hash, and the first two drift guards"
```

(Two commands, two Bash calls — see Conventions 4.)

---

### Task 2: drift assertion 3 — the rendered-body sweep, and the anchors it drives into eleven templates

**Files:**
- Modify: `foundation/tests/test_settings_help.py` (append `TestTheAnchors`)
- Modify: `models/registry/templates/inference/console.html:1005` (new) and `:1058` (new id)
- Modify: `tools/rag/templates/rag/settings.html:111, 127, 142, 157, 172`
- Modify: `agents/chat/templates/chat/settings.html:72`
- Modify: `tools/vision/templates/vision/engine_files.html:89, 120`
- Modify: `identity/templates/identity/users.html:198, 230`
- Modify: `identity/templates/identity/groups.html:89, 111`
- Modify: `identity/templates/identity/entitlements.html:76, 99`
- Modify: `agents/chat/templates/chat/tool_entitlements.html:62`
- Modify: `agents/chat/templates/chat/agent_entitlements.html:65, 93`
- Modify: `identity/templates/identity/settings.html:104, 110, 136, 171`
- Not modified: `foundation/setup/templates/setup/index.html` — its two cited anchors,
  `how-models-reach-farabunker` (`:47`) and `what-each-feature-needs` (`:130`), **already exist**.

**Interfaces:**
- Consumes: `foundation.settings_help.CARDS` (Task 1), and every `HelpField.anchor` in it.
- Produces: nothing importable. It produces the *invariant* every later task relies on — that every
  anchor a card cites really renders — and the parametrized test that keeps producing it.

**The rule the anchors follow** (spec §4.1):

- The id names the **control's own section wrapper**, not a label and not an input.
- It is **stable vocabulary**, not a slugified heading: renaming a heading must not silently break
  a link, and this task's own test turns an actual rename red.
- **A page with mutually exclusive branches carries the id in EACH branch** — exactly as
  `console.html` already does at `:1007`/`:1067`.
- Where a page has **no section wrapper to name** — a `{% for %}` loop with no container — the
  anchor is an empty `<span id="…"></span>` immediately before it. That is not a new mechanism:
  `console.html:1021` and `:1123` are exactly that, and they are the tree's own anchor precedent.

**Two pages need a decision spelled out, because a naive reading gets them wrong:**

1. **`inference-console` deliberately does NOT cite `on-this-machine`.** That id exists in both
   branches (`:1007`, `:1067`) — but the cold-start occurrence sits **inside
   `{% if installed_rows %}`** (`console.html:1006`), and this test's world has no live model
   server, so `cold_start` is True (`models/registry/views.py:1120`:
   `cold_start = not connections or not healthy`) and `installed_rows` is empty. A card citing it
   would go red on every box with no reachable engine — which is most test boxes and some real
   ones. The console card cites `getting-models` and `add-connection`, both of which render
   **unconditionally in both branches**, plus a new `in-use` this task adds to both.
2. **`setup-index` deliberately does NOT cite `accounts`.** That section (`setup/index.html:71-77`)
   is inside `{% if identity_posture == "open" %}` (`:60`), and this sweep runs in the
   **enterprise** posture. The card cites the two that always render for an administrator.

- [ ] **Step 1: Write the failing rendered-body sweep**

Append to `foundation/tests/test_settings_help.py`:

```python
@pytest.mark.django_db
class TestTheAnchors:
    """ASSERTION 3 (spec §10.1): every anchor a card cites exists in the
    RENDERED page.

    AGAINST A RENDERED BODY, NEVER TEMPLATE SOURCE, following
    `models/registry/tests/test_views.py:4373-4375` ("The anchor's target
    exists on the page"). That is not a stylistic preference:
    `inference/console.html` has a `{% if cold_start %}`/`{% else %}`
    split in which each anchor appears twice and exactly one branch
    renders, so a source grep can pass on a page whose live HTML has no
    such id.

    ONE CONDITION SET FOR ALL ELEVEN: the enterprise posture, signed in
    as an administrator, every model-consuming role bound. That is
    deliberately NOT `foundation/tests/test_page_names.py::_viewing`'s
    own arrangement (`:113-126`), which splits its sweep by name and runs
    everything that is not admin-only in the OPEN posture, anonymous --
    because that module is checking a page's NAME and an open box is the
    cheapest place to read one. This test needs a rendered BODY, and
    every one of the eleven renders 200 for an enterprise administrator,
    so one condition set is both sufficient and the thing to build.

    THE SWEEP PINS ITS OWN FEATURE STATE rather than inheriting the
    ambient one, per this branch's rule that a flag-dependent test states
    the flag it needs. What that does NOT do is make `vision-engine-files`
    reachable: `config/urls.py:38-39` mounts `/vision/` at IMPORT time, so
    overriding the setting inside a test cannot conjure a route that was
    never mounted. Both supported suite states carry `"vision"`
    (`'vision,media'` and `'vision'`, `docs/DEV.md:251-261`), so the entry
    is always mounted in practice. The pin is here so the sweep does not
    silently depend on `"media"` being on, and so the world it renders is
    stated rather than inherited.
    """

    @pytest.fixture(autouse=True)
    def _a_box_with_every_surface(self, db, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        seed_sweep_posture()
        clear_bindings()
        for role in (CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE):
            bind_role(role)

    @pytest.mark.parametrize("card", CARDS, ids=lambda card: card.route_name)
    def test_every_anchor_a_card_cites_exists_on_the_rendered_page(self, client, card):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse(card.route_name))
            assert response.status_code == 200, (card.route_name, response.status_code)
            body = response.content.decode()

        for field in card.fields:
            assert f'id="{field.anchor}"' in body, (
                f'{card.route_name}: the card cites anchor "{field.anchor}" for the control '
                f'"{field.name}", and the rendered page has no such id. Either the id was '
                f'renamed or deleted in that page\'s template, or the card names a control that '
                f'lives on a different page.'
            )
```

and extend the module's import block at the top of the file with the names the new class uses:

```python
from foundation.tests._helpers import (
    bind_role, clear_bindings, make_admin, posture, seed_sweep_posture, sign_in,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)
```

The role names and the `bind_role`/`clear_bindings` pair are borrowed exactly as
`foundation/tests/test_page_names.py:134-139` uses them — the roles-bound half of
`_every_surface_available`.

- [ ] **Step 2: Run it to verify it fails, and read WHICH anchors it names**

```bash
.venv/bin/pytest foundation/tests/test_settings_help.py::TestTheAnchors -q
```

Expected: **10 of the 11 parametrized cases FAIL**, each naming its own missing anchors —
`inference-console` missing `in-use`; `rag-settings` missing all five; `chat-settings` missing
`time-aware`; `vision-engine-files` missing both; `identity-users`, `identity-groups`,
`identity-entitlements`, `chat-agent-entitlements` missing both of theirs;
`chat-tool-entitlements` missing `tool-labels`; `identity-settings` missing all four.
**`setup-index` is the ONE that passes** — its two ids already exist — so the count is ten failing,
one passing. (`inference-console` fails on `in-use` alone; its other two anchors,
`getting-models` and `add-connection`, are already there.) Read the failure list and confirm it
matches this paragraph before touching a template. A case that passes for a page you expected to
fail means the card cites an id that means something else on that page.

- [ ] **Step 3: Add the anchors — `inference-console`, both branches**

`models/registry/templates/inference/console.html`. **Both branches, and the cold one needs an
empty `<span>`** because the roles it renders sit inside `{% if installed_rows %}` and the section
that would carry the id does not exist when nothing is installed.

Cold branch — insert immediately **after** the `</form>` that closes the scan form (`:1004`, with
`:1005` blank) and **before** `{% if installed_rows %}` (`:1006`), at the same indentation as the
surrounding children of that `<section class="panel">`:

```django
    {% comment %}
    THE ROLE-ASSIGNMENT ANCHOR, in the COLD branch, as an empty span --
    the same shape `#add-connection` below already uses, and for the same
    reason: the section that renders the role rows here
    (`.ready-section`, just below) is itself inside `{% templatetag
    openblock %} if installed_rows {% templatetag closeblock %}`, so an
    id on it would vanish on a box with nothing installed. The warm
    branch puts the identical id on its own `In use` section, which
    always renders. `foundation/settings_help.py`'s card for this page
    cites it, and `foundation/tests/test_settings_help.py::TestTheAnchors`
    renders THIS branch, so both halves are asserted.
    {% endcomment %}
    <span id="in-use"></span>
```

Warm branch — `:1058` today reads `  <section class="panel">` above `<h2>In use</h2>`. Give it the
id:

```django
  <section class="panel" id="in-use">
    <h2>In use</h2>
```

- [ ] **Step 4: Add the anchors — `rag-settings`, five sections**

`tools/rag/templates/rag/settings.html`. Each of the five `<section class="settings">` wrappers
gains an id. They are at `:111`, `:127`, `:142`, `:157` and `:172`; each is followed by its own
`<h2>`, which is how you confirm you have the right one:

```django
  <section class="settings" id="retention">
    <h2>Retention</h2>
```
```django
  <section class="settings" id="upload-limit">
    <h2>Upload limit</h2>
```
```django
  <section class="settings" id="media-duration-limit">
    <h2>Media duration limit</h2>
```
```django
  <section class="settings" id="document-page-limit">
    <h2>Document page limit</h2>
```
```django
  <section class="settings" id="retrieval">
    <h2>Retrieval</h2>
```

The ids are **stable vocabulary, not slugified headings**: `upload-limit` rather than
`maximum-upload-size-gb`, so changing the visible label from "Upload limit" to "Upload size" does
not break every link the assistant has ever emitted.

- [ ] **Step 5: Add the anchors — `chat-settings`, `vision-engine-files`**

`agents/chat/templates/chat/settings.html:72`:

```django
    <div class="checkbox-field" id="time-aware">
      {{ form.time_aware }}
```

`tools/vision/templates/vision/engine_files.html:89` and `:120`:

```django
<div class="engine-section" id="output-folder">
  <h2>Output folder</h2>
```
```django
<div class="engine-section" id="input-folder">
  <h2>Input folder</h2>
```

- [ ] **Step 6: Add the anchors — the three identity list pages**

`identity/templates/identity/users.html`. `:198` is `<section class="create">` and `:230` is the
`<div class="table-wrap">` holding the accounts table:

```django
  <section class="create" id="create-account">
    <h2>Create an account</h2>
```
```django
  <div class="table-wrap" id="account-list">
```

`identity/templates/identity/groups.html`. `:89` is a bare `<section>` — `:90` opens a
`{% comment %}`, and the `<h2>New group</h2>` is further down, so match on the tag, not on what
follows it. The per-group loop at `:111` has **no wrapper**, so it takes a `<span>` immediately
before it:

```django
  <section id="new-group">
    {% comment %}
```
```django
  {% comment %}
  A SPAN, not an id on the loop's first `<section>`: the loop can be
  empty (a box with no groups yet renders only the `{% templatetag
  openblock %} empty {% templatetag closeblock %}` fallback), and an
  anchor that disappears on an empty box is an anchor the settings
  assistant's own drift test cannot rely on. The empty-span anchor is
  `inference/console.html:1021`'s own shape.
  {% endcomment %}
  <span id="group-list"></span>
  {% for group in groups %}
```

`identity/templates/identity/entitlements.html`. `:76` is the `<section>` holding the New
entitlement form; `:99` is the `<div class="table-wrap">`:

```django
  <section id="new-entitlement">
    <h2>New entitlement</h2>
```
```django
  <div class="table-wrap" id="entitlement-list">
```

Adjust the exact tag text to whatever is on those lines — the point is the `id=`, and every one of
these wrappers already exists; **do not add a wrapper**.

- [ ] **Step 7: Add the anchors — the two entitlement-labelling pages**

`agents/chat/templates/chat/tool_entitlements.html`. The `{% for row in rows %}` at `:62` has no
wrapper, so the anchor goes immediately before it:

```django
  <span id="tool-labels"></span>
  {% for row in rows %}
```

`agents/chat/templates/chat/agent_entitlements.html`. Two loops, two anchors, each immediately
before its own `<h2 class="group-head">` (`:65` and `:93`) — before the heading rather than on it,
because the rule is that an id names a section, not a label:

```django
  <span id="agent-labels"></span>
  <h2 class="group-head">Agents</h2>
```
```django
  <span id="flow-labels"></span>
  <h2 class="group-head">Flows</h2>
```

- [ ] **Step 8: Add the anchors — `identity-settings`, the four fields**

`identity/templates/identity/settings.html`. Four `<div class="field">` wrappers, at `:104`, `:110`,
`:136` and `:171`. The second one is the flagship:

```django
    <div class="field" id="posture">
      <label for="{{ form.posture.id_for_label }}">Posture</label>
```
```django
    {% comment %}
    THE ANCHOR IS ON THE WRAPPER, WHICH RENDERS IN ALL THREE POSTURES --
    only its CONTENTS branch (`{% templatetag openblock %} if posture ==
    "personal" {% templatetag closeblock %}` below). That is what makes
    the settings assistant's deep link to this control land on a personal
    box exactly as it lands on an enterprise one, and it is why
    `foundation/settings_help.py`'s own second card rule is about what
    the card SAYS rather than about whether the link works.
    {% endcomment %}
    <div class="field" id="library-posture">
```
```django
    <div class="field checkbox-field" id="admin-sees-content">
      {{ form.admin_sees_content }}
```
```django
    <div class="field" id="session-idle">
      <label for="{{ form.session_idle_minutes.id_for_label }}">Session idle window (minutes)</label>
```

- [ ] **Step 9: Run the sweep to verify it passes**

```bash
.venv/bin/pytest foundation/tests/test_settings_help.py -q
```

Expected: PASS, all eleven parametrized cases plus everything Task 1 added.

- [ ] **Step 10: Prove the sweep is not vacuous — the anchor-rename drill, in miniature**

This is spec §14 drill 4, run once here so the guard is known to work before eleven later tasks
lean on it. Do it by hand and do not commit either edit:

```bash
sed -i '' 's/id="library-posture"/id="library-posture-renamed"/' identity/templates/identity/settings.html
```
```bash
.venv/bin/pytest "foundation/tests/test_settings_help.py::TestTheAnchors::test_every_anchor_a_card_cites_exists_on_the_rendered_page[identity-settings]" -q
```

Expected: **FAIL**, and the message names the card, the control ("Library posture") and the anchor.
Then restore it:

```bash
sed -i '' 's/id="library-posture-renamed"/id="library-posture"/' identity/templates/identity/settings.html
```
```bash
.venv/bin/pytest foundation/tests/test_settings_help.py -q
```

Expected: PASS again.

- [ ] **Step 11: Run every suite that renders one of these eleven pages**

```bash
.venv/bin/pytest foundation identity agents models tools -q -k "settings or console or entitlement or group or user or engine_files or page_names or shell"
```

Expected: PASS. Adding an `id=` attribute changes no behaviour, but three of these pages have
tests that assert on their own markup, and `foundation/tests/test_page_names.py` sweeps all eleven.

- [ ] **Step 12: Commit**

```bash
git add foundation/tests/test_settings_help.py models/registry/templates/inference/console.html tools/rag/templates/rag/settings.html agents/chat/templates/chat/settings.html tools/vision/templates/vision/engine_files.html identity/templates/identity/users.html identity/templates/identity/groups.html identity/templates/identity/entitlements.html agents/chat/templates/chat/tool_entitlements.html agents/chat/templates/chat/agent_entitlements.html identity/templates/identity/settings.html
git commit -m "feat(settings): stable anchors on every settings section a help card names, and the rendered-body drift guard that proves them"
```

---

### Task 3: the deep-link highlight — `:target` and its own token, in `_shell.html`

**Files:**
- Modify: `foundation/templates/_shell.html:96` area (light `:root`), `:155` area (dark `:root`),
  and the settings-layout CSS region beginning at `:306`
- Modify: `foundation/tests/test_shell.py` (append `TestTheTargetHighlight`)

**Interfaces:**
- Consumes: nothing. It is CSS.
- Produces: the `.settings-main :target` rule and the `--target-wash` token that Task 10's "Jump
  to" links and spec §14 drill 1's word "highlighted" both depend on.

**Why `_shell.html` and not `_settings.html`** (spec §4.2, decision 4): `foundation/setup/
templates/setup/index.html:16` overrides `extra_style` **without `{{ block.super }}`**, so a rule
added to `_settings.html`'s block would silently not apply on Install guides — the one settings
page whose sections are already painted `--panel` and therefore the one where an invisible
highlight would be least noticed. Putting the rule where the settings layout's own CSS already
lives (`_shell.html`, beside `.settings-layout` at `:306`) sidesteps the shadowing entirely and
needs no edit to any leaf page. The `block.super` gap itself is **owner flag 1** and is **not
fixed here**.

- [ ] **Step 1: Write the failing test**

Append to `foundation/tests/test_shell.py`:

```python
class TestTheTargetHighlight:
    """The settings area's deep-link highlight (spec §4.2).

    ASSERTED ON THE RENDERED SHELL, not on the template file: the rule
    has to reach a real settings page, and the reason it lives in
    `_shell.html` rather than in `_settings.html`'s own `extra_style` is
    precisely that ONE settings leaf (`setup/index.html:16`) overrides
    that block with no `{{ block.super }}` and would silently drop it.
    So the page this asserts against is that leaf.
    """

    def test_the_target_rule_reaches_the_page_that_drops_block_super(self, client):
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        assert ".settings-main :target" in body
        assert "var(--target-wash)" in body
        assert "scroll-margin-top" in body

    def test_the_wash_token_is_declared_in_both_theme_blocks(self, client):
        """ONE declaration per theme, so the highlight follows whichever
        is in force instead of hard-coding a colour that is right in only
        one of them. Two occurrences of the DECLARATION, and the rule
        above uses it without redeclaring it."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        assert body.count("--target-wash:") == 2

    def test_the_wash_is_not_plain_panel(self, client):
        """NOT `background: var(--panel)` (spec §4.2, decision 24):
        several settings sections are ALREADY painted `--panel` --
        `setup/index.html`'s own `.card`, `_settings.html`'s `.msg` -- so
        a `--panel` highlight is invisible on exactly the pages a person
        is most likely to be sent to, and §14's "highlighted" would be a
        criterion a human could not honestly sign off."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        rule_start = body.index(".settings-main :target")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "background: var(--target-wash)" in rule
        assert "background: var(--panel)" not in rule
        assert "box-shadow" in rule

    def test_a_settings_page_fetched_with_a_fragment_still_carries_the_anchor(self, client):
        """The other half of the feature: the browser does the scrolling
        and the CSS says which thing it scrolled to, so the only thing
        the SERVER has to get right is that the id is on the page. A URL
        fragment is never sent to the server, which is exactly why this
        needs no view change at all."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("setup-index")).content.decode()
        assert 'id="how-models-reach-farabunker"' in body
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/pytest foundation/tests/test_shell.py::TestTheTargetHighlight -q
```

Expected: FAIL on the first assertion — `.settings-main :target` is not in the body. There is **no
`:target` rule anywhere in the tree today**.

- [ ] **Step 3: Declare `--target-wash` in both token blocks**

`foundation/templates/_shell.html`. In the light `:root` block, immediately after `--accent`
(`:96`) and its `--accent-text` neighbour, add:

```css
    {% comment %}
    THE SETTINGS AREA'S DEEP-LINK HIGHLIGHT WASH, declared here with
    every other token so the rule below never hard-codes a hex. A MIX,
    not a fixed colour: `color-mix` is already in this tree for exactly
    this reason (`agents/chat/templates/chat/base.html:715-720` mixes
    over `--text` so a shadow "follows the theme instead"), and a
    highlight that did not follow the theme would be right in one scheme
    and wrong in the other.
    {% endcomment %}
    --target-wash: color-mix(in srgb, var(--accent) 12%, var(--panel));
```

In the dark block (`@media (prefers-color-scheme: dark)`, `:148-162`), immediately after its own
`--accent` (`:155`) / `--accent-text` pair, add the **same declaration text** — the tokens it mixes
are already redefined in that block, so the line is byte-identical and the result is not:

```css
      --target-wash: color-mix(in srgb, var(--accent) 12%, var(--panel));
```

(Note the indentation: the dark block's tokens sit one level deeper than the light block's.)

- [ ] **Step 4: Add the rule beside the settings layout's own CSS**

`foundation/templates/_shell.html`, in the settings-layout region that begins at `:306` with
`.settings-layout`. Put it immediately **after** the `.settings-layout` rule and before
`nav.settings-nav`, so the two `.settings-*` families stay together:

```css
  {% comment %}
  THE SETTINGS AREA'S DEEP-LINK HIGHLIGHT. `:target` only -- the browser
  scrolls and this says which thing it scrolled to. No script, no class
  toggling, no state, and no view change: a URL fragment is never sent to
  the server.

  IT LIVES HERE, NOT IN `_settings.html`'s OWN `extra_style` BLOCK, and
  that is a decision rather than a habit: `foundation/setup/templates/
  setup/index.html` overrides `extra_style` WITHOUT `{{ block.super }}`,
  so a rule added there would silently not apply on Install guides --
  the one settings page whose sections are already painted `--panel` and
  therefore the one where an invisible highlight would be least noticed.
  The settings layout's own CSS is already here, so this needs no edit to
  any leaf page at all.

  NOT `background: var(--panel)`: several settings sections are ALREADY
  painted `--panel` (`setup/index.html`'s `.card`, `_settings.html`'s own
  `.msg`), so a `--panel` highlight is invisible on exactly the pages a
  person is most likely to be sent to. The ring plus its own tinted wash
  is what makes "highlighted" true on every settings page rather than
  most of them.

  `scroll-margin-top` is part of the feature, not decoration: without it
  the targeted section lands flush against the top of the viewport under
  the app bar.
  {% endcomment %}
  .settings-main :target {
    background: var(--target-wash);
    box-shadow: 0 0 0 2px var(--accent);
    border-radius: 6px;
    scroll-margin-top: 1rem;
  }
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
.venv/bin/pytest foundation/tests/test_shell.py -q
```

Expected: PASS, the whole module — the sidebar drift test included, because this edit is in the
same file it renders.

- [ ] **Step 6: Confirm it by eye on the page the decision is about**

Bring up this branch's preview stack and open, in a browser:

```
http://localhost:8001/setup/#how-models-reach-farabunker
```

Expected: the "How models reach farabunker" card is scrolled to, sits clear of the app bar, and
carries a visible accent ring plus a tint that is distinguishable from the card's own `--panel`
fill. **Check it in both themes** (the OS light/dark switch is enough — the tokens follow
`prefers-color-scheme`). Install guides is the page to check because it is the one whose sections
are already `--panel`; a highlight that reads on `/identity/settings/` and vanishes here is the
exact defect decision 24 exists to prevent.

- [ ] **Step 7: Run the CSS-ownership gate**

```bash
.venv/bin/pytest foundation/ops/tests/test_css_ownership.py -q
```

Expected: PASS. The rule went into `_shell.html`, which is where a rule two columns' pages need
belongs; nothing was added to a leaf page's block.

- [ ] **Step 8: Commit**

```bash
git add foundation/templates/_shell.html foundation/tests/test_shell.py
git commit -m "feat(settings): a theme-following :target highlight for the settings area's deep links"
```

---

### Task 4: `agents/settings_tools.py` — two read-only tools, the audience gate, registration, both guard lists

**Files:**
- Create: `agents/settings_tools.py`
- Create: `agents/tests/test_settings_tools.py`
- Modify: `agents/apps.py` (`ready()`, beside the flow tool)
- Modify: `foundation/ops/tests/test_column_boundaries.py:185-189` and `:217-228` — **both, in this
  same commit**
- Modify: `docs/EXTENDING.md` (the tool inventory line; the full recipe pass is Task 12)

**Interfaces:**
- Consumes: `foundation.settings_help.{CARDS, CONTENT_HASH, card_for, page_choices}` (Task 1);
  `agents.contracts.tools.{Param, ToolContext, ToolRefused, ToolResult, ToolSpec,
  validate_tool_args}`; `identity.access.{is_admin, settings_row}`; `agents.models.ChatSettings`.
- Produces, for Task 5 (`tool_keys`) and Task 10 (the panel's link builder):
  - `SETTINGS_CARD: ToolSpec` with `key="settings.card"`, runner
    `"agents.settings_tools.run_card"`, one required `choice` param `page`
  - `SETTINGS_OVERVIEW: ToolSpec` with `key="settings.overview"`, runner
    `"agents.settings_tools.run_overview"`, no params
  - `run_card(args: dict, ctx: ToolContext) -> ToolResult` — `data` is
    `{"page": str, "content_hash": str, "links": [{"route": str, "anchor": str, "label": str}, …]}`
  - `run_overview(args: dict, ctx: ToolContext) -> ToolResult` — `data` is
    `{"content_hash": str, "posture": str, "library_posture": str, "admin_sees_content": bool,
    "session_idle_minutes": int, "time_aware": bool}`
  - `_PAGE_INDEX: str` — the `<route> — <title>` block built from `CARDS` at import

**The two gates that differ, and why** (spec §5.3, decision 23). `settings.overview` **refuses a
non-admin in the runner, before it reads anything about this box**; `settings.card` deliberately
does not. The asymmetry is the point: `register_tool` puts a spec in the **platform-wide**
registry and grants are per-**agent** rows plus the Tool access page, so nothing stops an operator
adding `settings.overview` to the general assistant's `tool_keys` — at which point a **member** on
an accounts-on box, chatting on `/chat/`, learns this box's posture, library posture, whether
administrators read other people's content, and the session idle timeout. Read-only is not
admin-only. `settings.card` reports **no value of any kind about this box** — it is
platform-authored help text about pages the reader may or may not be able to open, the same text
this repository is about to publish in `docs/EXTENDING.md` — so gating it would be security
theatre that also broke the non-admin variant §15.3 defers.

- [ ] **Step 1: Write the failing tests**

Create `agents/tests/test_settings_tools.py`:

```python
"""The settings assistant's two read-only tools (spec §5.2, §5.3, §10.3).

BOTH ARE READ-ONLY AND ONLY ONE IS GATED, deliberately -- see the module
under test for the reasoning. The gate that matters is asserted here as a
REFUSAL WITH NO VALUE IN IT, not merely as a non-200: a refusal that
leaked the posture in its message would be the same defect wearing an
exception.

`make_tool_ctx` (`agents/tests/_helpers.py:192-206`) IS THIS PACKAGE'S
`ToolContext` BUILDER and is used rather than re-derived. `ToolContext`'s
first five fields carry NO defaults (`agents/contracts/tools.py:292-296`
-- `conversation_id`, `principal`, `depth`, `budget`, `job`), so a
hand-rolled three-argument construction raises `TypeError` before any
assertion runs. Its default principal is `make_principal()`, kind
`"resident_agent"`, which `is_admin` answers False for -- which is
exactly the caller the refusal test needs.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolRefused, all_tools, get_tool
from agents.contracts.toolschema import openai_tool_dict
from agents.settings_tools import SETTINGS_CARD, SETTINGS_OVERVIEW, run_card, run_overview
# ONE IMPORT LINE FOR THE WHOLE HELPER FAMILY: `agents/tests/_helpers.py:36-39`
# re-exports `make_admin`/`posture`/`seed_sweep_posture`/`user_principal` from
# `identity.testing` "for this package's tests", so naming both modules would
# split one family across two import statements for no reason.
from agents.tests._helpers import (
    make_admin, make_tool_ctx, make_user, posture, seed_sweep_posture, user_principal,
)
from foundation.settings_help import CARDS, CONTENT_HASH, card_for, page_choices
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from models.contracts.operations import ParamError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


class TestRegistration:
    def test_both_keys_resolve_through_the_platform_registry(self):
        keys = {spec.key for spec in all_tools()}
        assert {"settings.card", "settings.overview"} <= keys

    def test_both_runners_are_dotted_paths_that_resolve(self):
        """The runner is a STRING so `AppConfig.ready()` never imports
        the implementation. `models/registry/tests/test_registry_paths.py
        ::test_every_registered_dotted_path_resolves` sweeps this for the
        whole platform; asserted here too so a broken path fails in the
        module's own test rather than three columns away."""
        from django.utils.module_loading import import_string

        assert callable(import_string(get_tool("settings.card").runner))
        assert callable(import_string(get_tool("settings.overview").runner))

    def test_neither_tool_mutates(self):
        """GUIDE-ONLY IS STRUCTURAL (owner ruling 1). Neither spec
        declares `mutates`, so both default to False -- which is also
        what makes them grantable at all."""
        assert get_tool("settings.card").mutates is False
        assert get_tool("settings.overview").mutates is False

    def test_neither_tool_binds_a_role(self):
        """`models.status` already reports which model answers each role
        and this agent holds it; a second tool answering the same
        question is the duplication owner ruling 5 forbids."""
        assert get_tool("settings.card").roles == ()
        assert get_tool("settings.overview").roles == ()


class TestTheEnumIsTheIndex:
    def test_the_page_param_enumerates_every_card_in_table_order(self):
        """SPEC §5.2: the index rides the TOOL SCHEMA, not the prompt.
        `Param(kind="choice", choices=...)` becomes an `enum` in the JSON
        schema both wire adapters build, so it cannot go stale and
        `agents/runtime/prompt.py` needs no settings-shaped special
        case."""
        schema = openai_tool_dict(SETTINGS_CARD)
        assert schema["function"]["parameters"]["properties"]["page"]["enum"] == list(
            page_choices())

    def test_the_description_names_every_page_and_cites_the_content_version(self):
        for card in CARDS:
            assert card.route_name in SETTINGS_CARD.description
            assert card.title in SETTINGS_CARD.description
        assert CONTENT_HASH in SETTINGS_CARD.description

    def test_an_invented_page_is_a_param_error_not_a_confident_answer(self):
        """"The one failure class worth handing back for a retry"
        (`docs/EXTENDING.md`). A page name a model made up must not reach
        the runner as a lookup miss."""
        with pytest.raises(ParamError):
            run_card({"page": "settings-nonesuch"}, make_tool_ctx())

    def test_a_missing_page_is_a_param_error(self):
        with pytest.raises(ParamError):
            run_card({}, make_tool_ctx())


class TestTheCard:
    def test_it_returns_the_page_its_caller_asked_for(self):
        result = run_card({"page": "identity-settings"}, make_tool_ctx())
        card = card_for("identity-settings")
        assert card.title in result.text
        assert card.purpose in result.text
        for field in card.fields:
            assert field.name in result.text
            assert field.meaning in result.text
            assert field.effects in result.text

    def test_its_data_carries_the_page_the_hash_and_the_links(self):
        result = run_card({"page": "identity-settings"}, make_tool_ctx())
        assert result.data["page"] == "identity-settings"
        assert result.data["content_hash"] == CONTENT_HASH
        assert result.data["links"] == [
            {"route": "identity-settings", "anchor": field.anchor, "label": field.name}
            for field in card_for("identity-settings").fields
        ]

    def test_its_links_are_route_anchor_pairs_and_never_urls(self):
        """SPEC §4.3: the model never produces a link, and neither does
        this. A URL built here would be a URL the panel had to trust; a
        `route`/`anchor` pair is one the panel re-validates against
        `CARDS` before it reverses anything."""
        for entry in run_card({"page": "rag-settings"}, make_tool_ctx()).data["links"]:
            assert set(entry) == {"route", "anchor", "label"}
            assert "://" not in entry["anchor"]
            assert "/" not in entry["anchor"]

    def test_it_reads_no_database_at_all(self, django_assert_num_queries):
        """Its cost is CONSTANT in the number of settings pages and ZERO
        in rows -- which is the whole point of an index-plus-on-demand
        shape. It reads `foundation.settings_help` only: no database, no
        request, no live value."""
        with django_assert_num_queries(0):
            run_card({"page": "chat-settings"}, make_tool_ctx())

    def test_it_answers_a_member_exactly_as_it_answers_an_administrator(self):
        """DELIBERATELY UNGATED (spec §5.3's last paragraph, decision
        23), and asserted rather than left to a docstring: this content
        is platform-authored help text about pages, and it reports no
        value about this box. It is also what keeps a member-facing
        variant possible."""
        member = user_principal(make_user())
        with posture(POSTURE_ENTERPRISE):
            admin_answer = run_card(
                {"page": "rag-settings"},
                make_tool_ctx(principal=user_principal(make_admin())))
            member_answer = run_card({"page": "rag-settings"},
                                     make_tool_ctx(principal=member))
        assert member_answer.text == admin_answer.text
        assert member_answer.data == admin_answer.data


class TestTheOverview:
    def test_an_administrator_gets_this_boxs_own_configuration(self):
        with posture(POSTURE_ENTERPRISE):
            result = run_overview({}, make_tool_ctx(principal=user_principal(make_admin())))
        assert result.data["posture"] == POSTURE_ENTERPRISE
        assert result.data["admin_sees_content"] is False
        assert result.data["time_aware"] is True
        assert result.data["content_hash"] == CONTENT_HASH
        assert "enterprise" in result.text

    def test_the_open_posture_is_reported_as_the_open_posture(self):
        """DRILL 1b AND DRILL 2 BOTH DEPEND ON THIS VALUE BEING REAL.
        The honest answer to "how do I make the library admin-only?" on
        an open box is sourced from here, not guessed."""
        with posture(POSTURE_OPEN):
            result = run_overview({}, make_tool_ctx())
        assert result.data["posture"] == POSTURE_OPEN

    def test_a_member_is_refused_and_the_refusal_names_no_value(self):
        """SPEC §5.3, DECISION 23 -- THE TOOL'S OWN GATE, not the
        surface's. A grant is per-AGENT, so an operator adding this key
        to another agent's `tool_keys` would otherwise hand a member this
        box's posture and security configuration. `ToolRefused`
        classifies as `refused` -- no retry -- which is the honest ending
        for a question no rephrasing fixes.

        THE MESSAGE IS ASSERTED TOO: a refusal that leaked the value in
        its own text would be the same defect wearing an exception.

        THE LEAK LIST INCLUDES `"open"` AND THAT IS THE POINT -- it is
        the posture value drills 1b and 2 turn on. The refusal copy in
        `agents/settings_tools.py` is worded around this list rather than
        the other way round; if this assertion goes red, fix the
        sentence, never the list."""
        member = user_principal(make_user())
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(ToolRefused) as caught:
                run_overview({}, make_tool_ctx(principal=member))
        message = str(caught.value).lower()
        for leak in ("enterprise", "personal", "locked", "open", "true", "false"):
            assert leak not in message.split(), message

    def test_it_reads_the_settings_row_once_not_once_per_value(
            self, django_assert_num_queries):
        """The per-call reuse norm `agents/entitlements.py:30-36` states,
        applied to the ROW: every value is read off ONE fetched instance,
        not one fetch per value.

        THREE, AND EACH ONE IS NAMED, because a number nobody can account
        for is a number that drifts: (1) `settings_row()` ->
        `IdentitySettings.get_solo()`; (2) the GATE's own `_user_row`
        read of `auth_user` inside `is_admin` -- memoised on the
        settings-row INSTANCE (`identity/access.py:100-103`), and this
        runner fetches a fresh instance every call, so that memo is
        always cold; (3) `ChatSettings.get_solo()`.

        THE SECOND ONE IS THE COST OF BEING A RUNNER RATHER THAN A VIEW.
        A view threads the request-scoped row the gate middleware already
        stashed and pays nothing for the gate; a tool runner has no
        request to thread, so it pays for its own. That is a fact about
        where the seam is, not an N+1 to fix -- and it is FLAT: it does
        not grow with anything.

        Pinned by EQUALITY. A `<=` here would hide a fourth read the day
        somebody adds a value without reading it off `row`."""
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(make_admin())
            run_overview({}, make_tool_ctx(principal=principal))   # prime the singletons
            with django_assert_num_queries(3):
                run_overview({}, make_tool_ctx(principal=principal))

    def test_it_declares_no_params_so_any_argument_is_a_bug(self):
        assert SETTINGS_OVERVIEW.params == ()
        with pytest.raises(ParamError):
            run_overview({"page": "rag-settings"}, make_tool_ctx())

    def test_it_reports_no_free_text_user_supplied_value(self):
        """SPEC §9: every value it reports is an ENUMERATED or NUMERIC
        column. No account name, group name, entitlement name, workstream
        name or conversation title appears -- which is also why it
        reports no counts and no name lists."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin(username="a-very-distinctive-name")
            result = run_overview({}, make_tool_ctx(principal=user_principal(admin)))
        assert "a-very-distinctive-name" not in result.text
        assert set(result.data) == {
            "content_hash", "posture", "library_posture", "admin_sees_content",
            "session_idle_minutes", "time_aware",
        }


class TestThePromptIsUntouched:
    def test_the_prompt_builder_names_no_settings_identifier(self):
        """A PHASE PIN WHOSE OWN DOCSTRING SAYS TO DELETE IT (spec §10.3).

        Unlike `models/registry/tools.py:23-27`, whose text guard protects
        a call that module must NEVER make, this one protects a DECISION
        (spec §5.2, decision 6): the page index rides the tool schema, so
        `agents/runtime/prompt.py` gets no settings-shaped special case
        and no slug conditional inside the one function every agent on
        this platform goes through. A later phase's retrieval leg
        (§15.1) could legitimately revisit that. WHEN IT DOES, DELETE
        THIS TEST -- do not weaken it, and do not add an exception to it.
        """
        import pathlib

        import agents.runtime.prompt as prompt

        source = pathlib.Path(prompt.__file__).read_text()
        for identifier in ("settings_help", "settings-helper", "settings.card",
                           "settings.overview", "SETTINGS_CARD", "SETTINGS_OVERVIEW"):
            assert identifier not in source, identifier
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest agents/tests/test_settings_tools.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'agents.settings_tools'`.
(`TestThePromptIsUntouched` would pass on its own, which is correct: it is a pin on a decision that
is already true and must stay true.)

- [ ] **Step 3: Write `agents/settings_tools.py`**

```python
"""The settings assistant's two tools (spec §5.2, §5.3).

BOTH READ-ONLY, NEITHER MUTATING, and that is structural rather than
intended: neither spec declares `mutates`, so both default to False,
and `grantable_tools` would refuse a row naming a mutating key anyway
(`docs/EXTENDING.md`). Owner ruling 1 is guide-only; this module is
where it is true.

TWO TOOLS THAT LOOK ALIKE ARE GATED DIFFERENTLY, ON PURPOSE, and it is
said out loud here so a reviewer does not read the asymmetry as an
oversight:

  * `settings.overview` REFUSES A NON-ADMIN, in the runner, before it
    reads anything. `register_tool` puts a spec in the PLATFORM-WIDE
    registry and grants are per-AGENT rows plus the Tool access page, so
    nothing stops an operator adding this key to the general assistant's
    `tool_keys` -- at which point a MEMBER on an accounts-on box, chatting
    on `/chat/`, learns this box's posture, library posture, whether
    administrators read other people's content, and the session idle
    timeout. Read-only is not admin-only, and the owner's ruling is about
    the INFORMATION, not only about the panel. The route class is the
    SURFACE's gate; this is the TOOL's. "Every layer keeps its own
    defensive call" is already this repository's doctrine
    (`agents/chat/templates/chat/_composer.html:56-62`), and
    `ToolContext` carries the acting principal precisely so a runner can
    ask (`agents/runtime/loop.py:432-446`).

  * `settings.card` DOES NOT, and the difference is the point. Its
    content is platform-authored help text about pages the reader may or
    may not be able to open -- the same text this repository is about to
    publish in `docs/EXTENDING.md` -- and it reports NO VALUE OF ANY KIND
    about this box. Gating it would be security theatre that also broke
    the member-facing variant the spec defers.

MODULE-SCOPE IMPORTS STAY PURE, so `AgentsConfig.ready()` keeps its
no-DB-no-heavy-imports promise: `foundation.settings_help` is a rule-1
pure leaf and `agents.contracts.tools` is one too. `identity.access` and
`agents.models` are imported LAZILY, inside `run_overview`'s body, the
same shape `models/registry/tools.py::run_status` uses.

WHAT THE OVERVIEW DOES NOT REPORT, AND WHY (spec §5.4, owner flag 2):
the library's own live caps -- upload cap, page cap, media cap,
retrieval top-k, score floor -- live on `tools.rag.models.RagSettings`,
and `agents/` MAY NOT IMPORT `tools/` AT ALL (ADR 0015, swept by
`foundation/ops/tests/test_import_law.py`). Guide-only is what makes
that refusal safe: the assistant's job is to say "the upload cap is on
the Library page, under Uploads" and link there. Reciting the number is
not the feature.
"""
from __future__ import annotations

from agents.contracts.tools import (
    Param, ToolContext, ToolRefused, ToolResult, ToolSpec, validate_tool_args,
)
from foundation.settings_help import CARDS, CONTENT_HASH, card_for, page_choices

# ONE `<route> -- <title>` LINE PER CARD, BUILT AT IMPORT, so the index a
# model reads cannot go stale relative to the table it is derived from.
# It rides the TOOL SCHEMA rather than the prompt (spec §5.2, decision
# 6): a contributor appended in `build_messages` would need a condition
# on the agent's slug inside a function every agent on this platform goes
# through, to inject a paragraph a tool schema already carries; and the
# agent ROW's `system_prompt` is a COPY, which goes stale until somebody
# re-runs `install_defaults --reset`.
_PAGE_INDEX = "\n\nThe settings pages on this box:\n" + "\n".join(
    f"  {card.route_name} -- {card.title}" for card in CARDS
)


SETTINGS_CARD = ToolSpec(
    key="settings.card",
    label="Settings page help",
    description=(
        "Explain ONE settings page on this box: what it is for, every control on it, what "
        "each control means and what changes when it changes, and the anchor to link to. "
        "Call this before answering any question about a settings page rather than "
        "answering from memory."
        + _PAGE_INDEX
        + f"\n\nSettings context version: {CONTENT_HASH}."
    ),
    params=(
        Param("page", "choice", "Settings page", required=True,
              choices=page_choices(),
              description="Which page to explain."),
    ),
    roles=(),
    runner="agents.settings_tools.run_card",
)


SETTINGS_OVERVIEW = ToolSpec(
    key="settings.overview",
    label="This box's current settings",
    description=(
        "Report how THIS box is configured right now: its posture, whether administrators "
        "may read other people's content, the session idle timeout, the library posture, "
        "and whether conversations are told the current date and time. Read-only. Call it "
        "when the answer depends on how this box is set up, not on what a page could do."
    ),
    params=(),
    roles=(),
    runner="agents.settings_tools.run_overview",
)


def run_card(args: dict, ctx: ToolContext) -> ToolResult:
    """One page's help card, as text a model reads well and as data the
    panel builds links from.

    NO DATABASE, NO REQUEST, NO LIVE VALUE. It reads
    `foundation.settings_help` only, so its cost is constant in the
    number of settings pages -- which is the point of an
    index-plus-on-demand shape.

    `links` are `route`/`anchor`/`label` TRIPLES, never URLs. The panel
    re-validates every one of them against `CARDS` before it reverses
    anything (spec §4.3), so nothing that is not already in the code-side
    table can become a link on a settings page. That is a whitelist by
    construction, and it is the property that matters most here, because
    a link is the one thing on that surface an operator will click.
    """
    clean = validate_tool_args(SETTINGS_CARD, args)
    card = card_for(clean["page"])
    # `validate_tool_args` has already refused anything outside
    # `page_choices()`, which is derived from `CARDS` at import -- so this
    # cannot be None. Asserted rather than branched: a None here would
    # mean the enum and the table had drifted inside one process, which is
    # not a runtime condition to handle, it is a bug to surface.
    assert card is not None, clean["page"]

    lines = [f"{card.title} ({card.route_name})", "", card.purpose, ""]
    for field in card.fields:
        lines += [
            f"- {field.name}",
            f"    What it is: {field.meaning}",
            f"    What it changes: {field.effects}",
            f"    Link anchor: {field.anchor}",
        ]

    return ToolResult(
        text="\n".join(lines),
        data={
            "page": card.route_name,
            "content_hash": CONTENT_HASH,
            "links": [
                {"route": card.route_name, "anchor": field.anchor, "label": field.name}
                for field in card.fields
            ],
        },
    )


def run_overview(args: dict, ctx: ToolContext) -> ToolResult:
    """How THIS box is configured right now -- administrator only.

    THE REFUSAL COMES BEFORE ANY VALUE IS READ OFF THE ROW, and its
    message names none of them: a refusal that leaked the posture in its
    own text would be the same defect wearing an exception.

    ONE ROW READ, not one per value: `identity.access.settings_row()` is
    fetched once and every answer read off that instance -- the per-call
    reuse norm `agents/entitlements.py:30-36` states -- and threaded into
    `is_admin` so the gate does not fetch it a second time.

    LAZY IMPORTS, inside the body, so this module stays importable from
    `AppConfig.ready()` with no database and no heavy import, exactly as
    `models/registry/tools.py::run_status` does.
    """
    from agents.models import ChatSettings
    from identity.access import is_admin, settings_row

    validate_tool_args(SETTINGS_OVERVIEW, args)   # declares no params: any arg is a bug

    row = settings_row()
    if not is_admin(ctx.principal, settings_row=row):
        # THE WORDING IS CONSTRAINED BY THE TEST BESIDE IT, deliberately.
        # `test_a_member_is_refused_and_the_refusal_names_no_value` bans
        # every posture token from this string, and "open" IS a posture
        # value -- the one drills 1b and 2 turn on, and the one a refusal
        # must never leak. The spec's own §5.3 draft used "open Settings"
        # as an ordinary verb; that reads fine and fails the assertion,
        # so the sentence -- not the assertion -- is what moved.
        raise ToolRefused(
            "This box's configuration is administrator-only. Ask an administrator, or sign "
            "in as one and read it on the settings pages."
        )

    chat = ChatSettings.get_solo()
    data = {
        "content_hash": CONTENT_HASH,
        "posture": row.posture,
        "library_posture": row.library_posture,
        "admin_sees_content": bool(row.admin_sees_content),
        "session_idle_minutes": int(row.session_idle_minutes),
        "time_aware": bool(chat.time_aware),
    }
    # EVERY VALUE HERE IS AN ENUMERATED OR NUMERIC COLUMN (spec §9). No
    # free-text, user-supplied string is interpolated into this text in
    # v1 -- no account name, group name, entitlement name, workstream name
    # or conversation title -- which is also why nothing here is a count
    # or a name list. A later version that wants to report such a value
    # goes through the platform's existing doctrine
    # (`agents/runtime/prompt.py::_sanitize_attachment_title`, the
    # DATA-not-instructions header, and the per-call random-delimited
    # block for anything longer than a name), never a new one.
    text = "\n".join([
        f"Posture: {data['posture']}",
        f"Library posture: {data['library_posture']}",
        f"Administrators may read other people's content: {data['admin_sees_content']}",
        f"Session idle window (minutes): {data['session_idle_minutes']}",
        f"Conversations are told the current date and time: {data['time_aware']}",
        f"Settings context version: {data['content_hash']}",
    ])
    return ToolResult(text=text, data=data)
```

- [ ] **Step 4: Register both specs in `agents/apps.py::ready()`**

Add, immediately after the `FLOW_RUN` registration block in `AgentsConfig.ready()`:

```python
        # THE SETTINGS ASSISTANT'S TWO TOOLS (spec §5.5). Both read-only,
        # neither declaring `mutates`, and `agents.settings_tools` imports
        # nothing heavy at module scope -- so this method still touches no
        # database and still imports no implementation module. Both
        # runners stay dotted-path STRINGS.
        from agents.settings_tools import SETTINGS_CARD, SETTINGS_OVERVIEW

        register_tool(SETTINGS_CARD)
        register_tool(SETTINGS_OVERVIEW)
```

`register_tool` is already imported a few lines above for the agent-as-tool loop; do not import it
twice.

- [ ] **Step 5: Add the module to BOTH guard lists, in this same commit**

`foundation/ops/tests/test_column_boundaries.py`. In `TOOL_MODULES` (`:185-189`):

```python
TOOL_MODULES = (
    "tools/rag/tools.py",
    "tools/vision/tools.py",
    "models/registry/tools.py",
    # The settings assistant's two read-only tools.
    "agents/settings_tools.py",
)
```

and in `_REGISTRATION_MODULES` (`:217-228`), which is an explicit positive list rather than a
derived one:

```python
    "agents/runtime/flowtool.py",
    # `agents/apps.py::ready()` imports this to register `settings.card`
    # and `settings.overview`. It is BOTH a tool module and a
    # registration module, so it appears in both lists -- the same shape
    # `agents/runtime/flowtool.py` above already has.
    "agents/settings_tools.py",
)
```

**Skipping either is a red test, and it is worth recognising which:**
`test_every_registered_runner_lives_in_a_swept_module`
(`foundation/ops/tests/test_column_boundaries.py:449`) fails if `TOOL_MODULES` is missed;
`test_no_tool_module_imports_its_service_layer_at_module_scope` (`:492`) is the sweep
`_REGISTRATION_MODULES` feeds.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/pytest agents/tests/test_settings_tools.py -q
```

Expected: PASS.

- [ ] **Step 7: Run every gate a new tool module trips**

```bash
.venv/bin/pytest foundation/ops/tests/ agents/contracts/tests/ models/registry/tests/test_registry_paths.py -q
```

Expected: PASS. Three of these earn their place here rather than at Task 14:
`test_every_registered_runner_lives_in_a_swept_module` and
`test_no_tool_module_imports_its_service_layer_at_module_scope` are the two Step 5 exists for;
`models/registry/tests/test_registry_paths.py::test_every_registered_dotted_path_resolves` walks
both new runner strings for free. The two anti-vacuous floors —
`test_registry_paths.py:80`'s `>= 26` and `agents/contracts/tests/test_toolschema.py:203`'s
`>= 4` — are floors, and **two new tools do not move either**; do not edit them.

- [ ] **Step 8: Add the two tools to `docs/EXTENDING.md`'s inventory**

Wherever that document lists the tools the platform ships, add `settings.card` and
`settings.overview` with one line each, and say that the second one refuses a non-admin **in its
runner** — one sentence, so an author reading the tool list learns the asymmetry exists. The full
recipe pass and the six stale citations are Task 12.

- [ ] **Step 9: Commit**

```bash
git add agents/settings_tools.py agents/tests/test_settings_tools.py agents/apps.py foundation/ops/tests/test_column_boundaries.py docs/EXTENDING.md
git commit -m "feat(settings): two read-only settings tools, one of them administrator-gated in its own runner"
```

---

### Task 5: the catalogue entry, the settings-surface slug set, and the guide-only pin

**Files:**
- Modify: `agents/defaults.py:308-400` (a fourth `AgentSpec`) and the module tail
  (`SETTINGS_SURFACE_SLUGS`)
- Modify: `agents/tests/test_settings_tools.py` (append `TestTheCatalogueEntry`)
- Modify: `agents/README.md` (the shipped defaults list, in the same commit)

**Interfaces:**
- Consumes: `settings.card` and `settings.overview` (Task 4); the existing `models.status`.
- Produces, for Task 6 and Task 10:
  - a `DEFAULT_AGENTS` entry with `slug="settings-helper"`, `name="Settings assistant"`,
    `tool_keys=("settings.card", "settings.overview", "models.status")`, `as_tool=False`
  - `SETTINGS_SURFACE_SLUGS: frozenset[str] = frozenset({"settings-helper"})`

**No auto-row.** Ruling 2 (`agents/defaults.py:14-18`) stands: `install_default` is create-if-absent
and is the ONE way a row appears. This task adds a **catalogue** entry, not a row. The panel renders
the offer (Task 10).

- [ ] **Step 1: Write the failing tests**

Append to `agents/tests/test_settings_tools.py`:

```python
class TestTheCatalogueEntry:
    def test_the_settings_assistant_is_in_the_catalogue_and_is_not_a_tool(self):
        """`as_tool=False` IS DELIBERATE: an `agent.settings-helper` tool
        key would put this agent inside other agents' reach, which is the
        opposite of settings-only."""
        from agents.defaults import DEFAULT_AGENTS

        spec = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper")
        assert spec.name == "Settings assistant"
        assert spec.as_tool is False
        assert spec.tool_keys == ("settings.card", "settings.overview", "models.status")

    def test_every_tool_it_holds_is_registered_and_none_of_them_mutates(self):
        """SPEC §5.5 -- BELT OVER THE STRUCTURAL BRACES. `Agent.save()`
        would already refuse a row naming a mutating key, but the ruling
        is the owner's, and a structural guarantee nobody restates is a
        guarantee somebody removes."""
        from agents.contracts.tools import get_tool
        from agents.defaults import DEFAULT_AGENTS

        spec = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper")
        for key in spec.tool_keys:
            assert get_tool(key).mutates is False, key

    def test_its_system_prompt_carries_doctrine_and_never_lists_the_pages(self):
        """SPEC §5.1: the index rides the tool SCHEMA. A row is a COPY,
        and a copy goes stale until somebody re-runs `install_defaults
        --reset` -- which is exactly the staleness the owner asked about.
        So no route name and no page title appears in the prompt."""
        from agents.defaults import DEFAULT_AGENTS
        from foundation.settings_help import CARDS

        prompt = next(s for s in DEFAULT_AGENTS if s.slug == "settings-helper").system_prompt
        for card in CARDS:
            assert card.route_name not in prompt, card.route_name
        assert "settings.card" in prompt
        assert "settings.overview" in prompt

    def test_no_row_appears_just_because_the_catalogue_has_an_entry(self):
        """RULING 2 (`agents/defaults.py:14-18`): `install_default` is
        create-if-absent and is the ONE way a row appears. The panel
        renders the offer; a deploy installs nothing."""
        from agents.models import Agent

        assert not Agent.objects.filter(slug="settings-helper").exists()

    def test_installing_it_creates_exactly_one_row_and_is_idempotent(self):
        from agents.defaults import install_default
        from agents.models import Agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        install_default("agent", "settings-helper", OPEN_PRINCIPAL)
        install_default("agent", "settings-helper", OPEN_PRINCIPAL)
        assert Agent.objects.filter(slug="settings-helper").count() == 1


class TestTheSurfaceSlugSet:
    def test_every_slug_in_the_set_names_a_real_catalogue_entry(self):
        """SPEC §10.3, "slug set is honest". The frozenset is one line
        beside the spec it names, and this is what keeps it from
        outliving it."""
        from agents.defaults import DEFAULT_AGENTS, SETTINGS_SURFACE_SLUGS

        catalogue = {spec.slug for spec in DEFAULT_AGENTS}
        assert SETTINGS_SURFACE_SLUGS <= catalogue
        assert SETTINGS_SURFACE_SLUGS == {"settings-helper"}

    def test_it_is_a_frozenset_so_no_caller_can_widen_it_in_place(self):
        from agents.defaults import SETTINGS_SURFACE_SLUGS

        assert isinstance(SETTINGS_SURFACE_SLUGS, frozenset)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest agents/tests/test_settings_tools.py -q -k "Catalogue or SurfaceSlug"
```

Expected: FAIL — `StopIteration` on the `next(...)` lookups, and `ImportError` for
`SETTINGS_SURFACE_SLUGS`.

- [ ] **Step 3: Add the `AgentSpec` to `DEFAULT_AGENTS`**

`agents/defaults.py`, appended as the fourth entry inside the existing tuple (after
`illustrator`, before the closing `)` at `:400`). **The system prompt carries doctrine and nothing
else — it never lists the pages:**

```python
    AgentSpec(
        slug="settings-helper",
        name="Settings assistant",
        description="Explains this box's settings and points at the exact control.",
        # DOCTRINE ONLY, AND THAT IS THE DECISION (spec §5.1, §5.2).
        # The list of settings pages rides the `settings.card` tool's own
        # SCHEMA -- a `choice` param's `enum` plus a description built
        # from the card table at import -- so it cannot go stale. Putting
        # it here instead would put a COPY on a ROW, and a row goes stale
        # until somebody re-runs `install_defaults --reset`, which is
        # exactly the staleness this feature was asked to rule out. This
        # text IS the row's, so an operator may tune it, and
        # `install_defaults --reset settings-helper` restores it.
        system_prompt=(
            "You are the settings guide on a private, offline-first box. You explain what "
            "this box's settings are, what each control does, and where it lives. YOU NEVER "
            "CHANGE A SETTING, and you cannot: every tool you hold is read-only.\n\n"
            "Call `settings.card` for the page in question rather than answering from "
            "memory. It lists every control on that page, what each one means, what changes "
            "when it changes, and the anchor the platform turns into a link. The set of "
            "pages you may ask about is the set the tool itself offers -- never invent "
            "one.\n\n"
            "Call `settings.overview` whenever the answer depends on how THIS box is "
            "configured rather than on what a page could do -- its posture, its library "
            "posture, whether administrators may read other people's content, the session "
            "idle window, whether conversations are told the date and time. Answer from what "
            "it returns, never from a guess about how a box is usually set up.\n\n"
            "Quote the page's own words for a control's name, so the person can find it by "
            "reading. Name the page that OWNS a control, which is not always the page whose "
            "subject it sounds like.\n\n"
            "When you do not know, say so and name the page you would look at. Never invent "
            "a setting, a control or a page. Do not write out a link yourself -- the "
            "platform builds the links from what the tool returned, beneath your answer."
        ),
        tool_keys=("settings.card", "settings.overview", "models.status"),
        # `llm_role` defaults to CHAT_CONVERSE_ROLE (`:97`); `max_steps`
        # to MAX_STEPS_DEFAULT (`:98`); `as_tool` stays False (`:99`) --
        # an `agent.settings-helper` tool key would put this agent inside
        # OTHER agents' reach, which is the opposite of settings-only.
    ),
```

- [ ] **Step 4: Add the slug set beside the spec it names**

`agents/defaults.py`, immediately after the closing `)` of `DEFAULT_AGENTS`:

```python
# THE SETTINGS-ONLY SURFACE, EXPRESSED AS A SLUG SET (owner ruling 2,
# spec §7). ONE LINE BESIDE THE SPEC IT NAMES, and `agents/visibility.py`
# wraps the platform's one gate with it -- rather than a `surface` column
# on `Conversation` (a migration, a write path and back-fill reasoning,
# to express a fact the `agent` FK already carries), a `surface` member on
# `AgentSpec` (one more concept, with `"chat"` as a silent default), or a
# narrowing of `visible_conversations`/`visible_agents` themselves (which
# must keep answering YES -- the panel reads the same rows).
#
# `agents/tests/test_settings_tools.py::TestTheSurfaceSlugSet` pins that
# every slug here names a real catalogue entry, so this set cannot
# outlive the agent it was written for.
SETTINGS_SURFACE_SLUGS: frozenset[str] = frozenset({"settings-helper"})
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/pytest agents/tests/test_settings_tools.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the catalogue's own existing tests**

```bash
.venv/bin/pytest agents/tests/ agents/chat/tests/ -q
```

Expected: PASS. A fourth catalogue entry changes what `/chat/`'s "Add the default X" offers list
holds — Task 6 is what takes it back out — so any test that counts those offers fails **here** and
is the reason Task 6 exists. **If one does, do not weaken it: record which, and fix it in Task 6.**

- [ ] **Step 7: Update `agents/README.md`**

Add the settings assistant to the shipped-defaults list: what it is, its three read-only tools, and
that its surface is the settings panel rather than `/chat/` — one short paragraph, with the
settings-only property named as ruling 2's.

- [ ] **Step 8: Commit**

```bash
git add agents/defaults.py agents/tests/test_settings_tools.py agents/README.md
git commit -m "feat(settings): the settings assistant catalogue entry, its guide-only pin, and the settings-surface slug set"
```

---

### Task 6: the chat-surface exclusion — two wrappers, six call sites, and the row-addressed door

**Files:**
- Modify: `agents/visibility.py` (two wrappers, beside `visible_agents`/`visible_conversations`)
- Modify: `agents/chat/views/conversations.py:156`, `:165`, `:234`
- Modify: `agents/chat/views/workstreams.py:401`
- Modify: `agents/chat/sidebar.py:161`
- Modify: `agents/chat/views/all_conversations.py:137`
- Modify: `agents/chat/service.py:493` (inside `visible_conversation_or_404`)
- Create: `agents/chat/tests/test_settings_surface.py`
- Modify: `agents/chat/README.md` (the exclusion, in the same commit)

**Interfaces:**
- Consumes: `agents.defaults.SETTINGS_SURFACE_SLUGS` (Task 5).
- Produces, for Task 10:
  - `chat_surface_agents(principal)` — a queryset, the same shape `visible_agents` returns
  - `chat_surface_conversations(principal, *, settings_row=None)` — a queryset, the same shape
    `visible_conversations` returns
  Task 10 uses **neither**: the panel reads through the un-narrowed
  `visible_conversations(principal).filter(agent__slug=…)` and
  `visible_agents(principal).filter(slug__iexact=…)`, deliberately, because it is the surface the
  exclusion excludes *from*.

**Owner ruling 2 needs three things to be true** — the assistant is not in the `/chat/` agent
picker, its conversations are not in the chat sidebar, and they are not in `/chat/all/` — and a
fourth the review found: **it is not reachable at its own row URL either.** Lists make it
invisible; only the fourth makes it unreachable, and "settings-only surface" is a claim about
reachability. Without it the thread is fully live at `/chat/c/<uuid>/` — postable, renamable,
duplicable, pinnable, archivable and **shareable** — and §15.5's "nothing here uses it" would be
false the day this ships.

**No `chat_surface_only=False` escape hatch** (decision 22). Nothing needs one: the panel does not
use `visible_conversation_or_404` at all. A keyword defaulting to `True` with no caller passing
`False` would be a knob with no reader, and it would invite the first author who hits a 404 to flip
it rather than ask why. A seventh row-addressed chat view added later inherits the exclusion by
doing nothing.

- [ ] **Step 1: Write the failing tests**

Create `agents/chat/tests/test_settings_surface.py`:

```python
"""The settings assistant is off the chat surface (owner ruling 2, spec
§7) -- not merely unlisted, but unreachable.

TWO HALVES, AND BOTH ARE ASSERTED HERE. The five list-shaped call sites
make it INVISIBLE on `/chat/`; narrowing `visible_conversation_or_404`
makes every row-addressed chat view answer 404 on it, INCLUDING SHARE,
which is what turns the spec's "a shared assistant conversation is
deferred" into a shut door rather than one nobody has walked through.

WHAT IS DELIBERATELY *NOT* NARROWED IS RECORDED HERE TOO, in
`TestWhatIsDeliberatelyLeftAlone`, so a later reader does not mistake its
200 for a hole.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_turn, make_user, posture, seed_sweep_posture, sign_in, user_principal,
)
from agents.defaults import SETTINGS_SURFACE_SLUGS, install_default
from agents.models import Agent, Conversation, Turn
from agents.visibility import (
    chat_surface_agents, chat_surface_conversations, create_conversation, visible_agents,
    visible_conversations,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN

pytestmark = pytest.mark.django_db

SLUG = "settings-helper"


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


def _installed(principal):
    """The assistant's ROW plus this principal's own conversation on it --
    the world every test below needs. Installed through the ONE way a row
    appears (`install_default`, create-if-absent), never
    `Agent.objects.create`, so this world is the world an operator gets."""
    install_default("agent", SLUG, principal)
    agent = Agent.objects.get(slug=SLUG)
    conversation = create_conversation(principal, agent)
    make_turn(conversation=conversation, role=Turn.Role.USER, text="what is the posture?")
    make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="Enterprise.",
              state=Turn.State.DONE)
    return agent, conversation


class TestTheWrappers:
    def test_the_gate_itself_still_answers_yes(self):
        """SPEC §7.2: `visible_agents`/`visible_conversations` answer "may
        this principal READ this row", and the answer here is YES -- the
        panel reads both. Narrowing the platform's one gate to express a
        SURFACE preference would make `visible_agent_slugs` claim an
        administrator may not run an agent they are, at that moment,
        running."""
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            agent, conversation = _installed(admin)
            assert agent in visible_agents(admin)
            assert conversation in visible_conversations(admin)

    def test_the_wrappers_exclude_it(self):
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            agent, conversation = _installed(admin)
            assert agent not in chat_surface_agents(admin)
            assert conversation not in chat_surface_conversations(admin)

    def test_the_wrappers_exclude_nothing_else(self):
        """A wrapper that narrowed more than the one slug set would be a
        surface preference quietly becoming a permission."""
        from agents.chat.tests._helpers import make_agent, make_conversation

        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            _installed(admin)
            other = make_agent(slug="ordinary", resident=True)
            thread = make_conversation(agent=other, **_owner(admin))
            assert other in chat_surface_agents(admin)
            assert thread in chat_surface_conversations(admin)


def _owner(principal):
    from identity.access import owner_fields

    return owner_fields(principal)


class TestTheListSurfaces:
    def test_it_is_absent_from_the_chat_picker_and_from_the_offers(self, client):
        """Two consumers fixed by ONE edit at `conversations.py:165`: the
        "Add the default X" offers (which would otherwise offer the
        settings assistant on `/chat/` forever, installed or not) and the
        `nothing_installed` banner (which would otherwise call a box whose
        only agent row is the settings assistant "all installed agents are
        disabled")."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _installed(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse("chat-index")).content.decode()
        assert SLUG not in body
        assert "Settings assistant" not in body

    def test_it_is_absent_from_the_sidebar_active_pinned_and_archived(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            principal = user_principal(admin)
            _agent, conversation = _installed(principal)
            sign_in(client, admin)
            body = client.get(reverse("chat-index")).content.decode()
            assert str(conversation.pk) not in body

            Conversation.objects.filter(pk=conversation.pk).update(pinned_at="2026-01-01T00:00Z")
            assert str(conversation.pk) not in client.get(
                reverse("chat-index")).content.decode()

            Conversation.objects.filter(pk=conversation.pk).update(
                pinned_at=None, archived_at="2026-01-01T00:00Z")
            assert str(conversation.pk) not in client.get(
                reverse("chat-index")).content.decode()

    def test_it_is_absent_from_chat_all_rows_and_from_its_preview(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse("chat-all")).content.decode()
            assert str(conversation.pk) not in body
            selected = client.get(f"{reverse('chat-all')}?selected={conversation.pk}")
            assert str(conversation.pk) not in selected.content.decode()

    def test_starting_one_by_hand_is_refused(self, client):
        """`_startable_agent` matters for a reason worth naming: without
        it, a hand-crafted POST to `/chat/start/` naming this slug would
        create a conversation that then appears on no list at all.
        Closing the picker without closing the start path leaves
        orphans."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _installed(user_principal(admin))
            before = Conversation.objects.count()
            sign_in(client, admin)
            response = client.post(reverse("chat-start"), {"agent": SLUG, "text": "hello"})
        assert response.status_code == 400
        assert Conversation.objects.count() == before

    def test_no_workstream_card_can_ever_list_it(self):
        """SPEC §7.1.2 -- THE CALL SITES THAT NEED NO CHANGE, AND WHY,
        pinned directly rather than left to luck.

        `agents/chat/views/workstreams.py:373` (a stream card's
        conversation list) and `:792` (`workstream_consolidate`'s own
        row lookup) both filter on `workstream_id`, and an assistant
        conversation always carries NULL there: the ask flow calls
        `create_conversation(principal, agent)` with no `workstream`, and
        that column "IS STAMPED ONCE AND NEVER WRITTEN AGAIN"
        (`agents/visibility.py:777-784`) -- no route writes it after
        creation and the module exposes no setter. A future author who
        threaded a workstream into the ask flow would put one there with
        nothing complaining, which is what this pins.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            _agent, conversation = _installed(admin)
        conversation.refresh_from_db()
        assert conversation.workstream_id is None


class TestTheRowAddressedDoor:
    """SPEC §7.1.1 / DECISION 22. Nine call sites, one function, one word
    changed -- so every row-addressed chat view answers 404 on a
    settings-surface conversation. 404 rather than 403 by
    `visible_conversation_or_404`'s own docstring rule: "a 403 on a
    row-addressed URL confirms the row exists"."""

    @pytest.mark.parametrize("name,method,body", [
        ("chat-conversation", "get", {}),
        ("chat-turn", "post", {"text": "hello"}),
        ("chat-conversation-rename", "post", {"title": "renamed"}),
        ("chat-conversation-duplicate", "post", {}),
        ("chat-conversation-pin", "post", {}),
        ("chat-conversation-unpin", "post", {}),
        ("chat-conversation-archive", "post", {}),
        ("chat-conversation-unarchive", "post", {}),
        ("chat-conversation-delete", "post", {}),
        ("chat-conversation-share", "post", {}),
    ])
    def test_every_row_addressed_chat_view_answers_404_for_its_own_owner(
            self, client, name, method, body):
        """FOR ITS OWN OWNER, which is what makes this a surface rule
        rather than a permission one: this principal may read the row --
        the panel does, on the settings page, in the same request cycle
        -- and still cannot reach it here."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            url = reverse(name, args=[conversation.pk])
            response = getattr(client, method)(url, body)
        assert response.status_code == 404, (name, response.status_code)

    def test_sharing_it_is_the_one_that_matters_most(self, client):
        """§15.5's deferral is a SHUT DOOR because of this, not an
        unwalked one: `Share.Target.CONVERSATION` exists and the share
        action lives on the page that now 404s."""
        from agents.models import Share

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            other = make_user()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"user": other.pk, "level": "view"})
        assert response.status_code == 404
        assert not Share.objects.filter(target_key=str(conversation.pk)).exists()

    def test_an_ordinary_conversation_is_untouched_by_the_narrowing(self, client):
        """The negative case, without which the parametrized sweep above
        could pass on a helper that 404s everything."""
        from agents.chat.tests._helpers import make_agent, make_conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            principal = user_principal(admin)
            thread = make_conversation(agent=make_agent(slug="ordinary", resident=True),
                                       **_owner(principal))
            sign_in(client, admin)
            response = client.get(reverse("chat-conversation", args=[thread.pk]))
        assert response.status_code == 200


class TestWhatIsDeliberatelyLeftAlone:
    def test_the_turn_status_fragment_is_not_narrowed_and_that_is_deliberate(self, client):
        """SPEC §7.1.1, RECORDED SO A LATER READER DOES NOT MISTAKE THIS
        200 FOR A HOLE. `visible_turn` (`agents/visibility.py:222-234`)
        filters `conversation__in=visible_conversations(principal)` and
        backs `chat-turn-status`. It asks the identical READ gate, so it
        is owner-only; and it exposes no affordance -- a status fragment
        is not a page, and the panel does not use it (it polls
        `settings-assistant-panel`, its own route). Narrowing it would
        mean a third wrapper for no reachable action.

        THE CLAIM THIS PHASE MAKES IS PRECISE: every row-addressed chat
        VIEW is closed, not that no code path anywhere can resolve the
        row by id.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            turn = conversation.turns.order_by("index").last()
            sign_in(client, admin)
            response = client.get(reverse("chat-turn-status", args=[turn.pk]))
        assert response.status_code == 200

    def test_a_stranger_still_cannot_read_that_fragment(self, client):
        """Which is the half that makes leaving it alone safe."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            turn = conversation.turns.order_by("index").last()
            sign_in(client, make_user())
            response = client.get(reverse("chat-turn-status", args=[turn.pk]))
        assert response.status_code == 404


class TestTheOpenPosture:
    def test_the_exclusion_holds_on_a_box_with_no_accounts(self, client):
        """`visible_conversations` short-circuits on `sees_all_content`
        and returns `.all()` on an open box, so the exclusion has to work
        on a queryset that was never filtered by ownership at all."""
        from identity.contracts.principals import OPEN_PRINCIPAL

        with posture(POSTURE_OPEN):
            _agent, conversation = _installed(OPEN_PRINCIPAL)
            assert conversation not in chat_surface_conversations(OPEN_PRINCIPAL)
            body = client.get(reverse("chat-all")).content.decode()
            assert str(conversation.pk) not in body
            assert client.get(
                reverse("chat-conversation", args=[conversation.pk])).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest agents/chat/tests/test_settings_surface.py -q
```

Expected: FAIL — `ImportError` for `chat_surface_agents`/`chat_surface_conversations`, and once
those exist, the list and row-addressed assertions fail because nothing excludes anything yet.

- [ ] **Step 3: Add the two wrappers to `agents/visibility.py`**

Immediately after `visible_agent_slugs` (`:181-186`), so the three surface-shaped readers sit
together:

```python
def chat_surface_agents(principal):
    """`visible_agents`, minus the agents whose surface is not `/chat/`
    (owner ruling 2, spec §7).

    A THIN WRAPPER ON THE ONE GATE, never a narrowing of it. The gate
    itself must keep answering YES for these rows -- the settings panel
    reads exactly the same agent and the same conversation, in the same
    request cycle -- so what this expresses is a SURFACE preference, and a
    surface preference has no business inside a function whose question is
    "may this principal read this row".
    """
    from agents.defaults import SETTINGS_SURFACE_SLUGS

    return visible_agents(principal).exclude(slug__in=SETTINGS_SURFACE_SLUGS)


def chat_surface_conversations(principal, *, settings_row=None):
    """`visible_conversations`, minus the conversations of agents whose
    surface is not `/chat/`. The conversation half of the wrapper above,
    and it threads `settings_row` unchanged so its callers keep their
    one-read-per-render property.

    NO `chat_surface_only=False` ESCAPE HATCH (spec decision 22): nothing
    needs one. The panel reads the un-narrowed gate directly, so a keyword
    defaulting to True with no caller passing False would be a knob with
    no reader -- and it would invite the first author who hits a 404 to
    flip it rather than ask why.
    """
    from agents.defaults import SETTINGS_SURFACE_SLUGS

    return visible_conversations(principal, settings_row=settings_row).exclude(
        agent__slug__in=SETTINGS_SURFACE_SLUGS)
```

The `SETTINGS_SURFACE_SLUGS` import is **inside the function bodies deliberately**:
`agents/defaults.py` is imported by `agents/apps.py` and by management commands, and
`agents/visibility.py` is imported very early; a module-scope import here would add an import edge
this module has never had. It costs a dictionary lookup per call, on a module already doing
queries.

- [ ] **Step 4: Move the six list call sites, in four files**

Five of the six are one word. **Change the import at the top of each module too** — four modules
import a wrapper, and `conversations.py` also imports `SETTINGS_SURFACE_SLUGS`, because its `:165`
site filters a set of slugs rather than a queryset and so has no wrapper to call. A stale import is
the way this step gets half-done.

`agents/chat/views/conversations.py:156`:

```python
    agents = list(chat_surface_agents(principal))
```

`agents/chat/views/conversations.py:165` — this one is not a rename, because
`installed_agent_slugs` has no wrapper of its own (it answers a set of strings, not a queryset):

```python
    # THE SETTINGS ASSISTANT IS NOT OFFERED HERE (owner ruling 2). This
    # single subtraction fixes TWO consumers: the "Add the default X"
    # offers below, which would otherwise offer it on `/chat/` forever,
    # installed or not; and the `nothing_installed` banner, which would
    # otherwise call a box whose only agent row IS the settings assistant
    # "all installed agents are disabled".
    installed_slugs = [slug for slug in installed_agent_slugs(principal)
                       if slug not in SETTINGS_SURFACE_SLUGS]
```

`agents/chat/views/conversations.py:234` (`_startable_agent`):

```python
    return chat_surface_agents(principal).filter(slug__iexact=(slug or "").strip()).first()
```

`agents/chat/views/workstreams.py:401`:

```python
        "agents": list(chat_surface_agents(principal)),
```

`agents/chat/sidebar.py:161` — the `base` queryset that is `.filter()`d several times below, so
one edit covers the active list, the pinned section **and** the archived count with no extra query:

```python
    base = chat_surface_conversations(principal, settings_row=settings_row)
```

`agents/chat/views/all_conversations.py:137` — the same shape, covering rows **and** preview:

```python
    base = chat_surface_conversations(principal, settings_row=settings_row) \
        .select_related("workstream", "agent")
```

- [ ] **Step 5: Narrow `visible_conversation_or_404` — one word, inside the function**

`agents/chat/service.py:493`. The `def` is at `:479`; only the return line changes, and **no call
site is edited**:

```python
    return get_object_or_404(chat_surface_conversations(principal), pk=conversation_id)
```

Extend that function's docstring with the reason, because a reader arriving from one of its nine
call sites has to be able to learn it here:

```python
    ASKS `chat_surface_conversations`, UNCONDITIONALLY (owner ruling 2,
    spec §7.1.1). All nine of this function's call sites are `/chat/`
    views -- which is what this function's own name and the paragraph
    above already say it is -- so all nine answer 404 on a
    settings-surface conversation: render, post, rename, duplicate, pin,
    unpin, archive, unarchive, delete and SHARE. Lists make that
    assistant invisible on `/chat/`; only this makes it UNREACHABLE, and
    unreachable is what "settings-only surface" claims. The share action
    is the one that matters most: it lives on the page this now 404s, and
    a shared assistant conversation is a deferral whose door is shut
    rather than merely unwalked.

    The settings panel does NOT use this helper -- it reads through
    `visible_conversations(principal).filter(agent__slug=...)`, the
    un-narrowed gate -- so there is no escape-hatch keyword here and
    nothing needs one.
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/pytest agents/chat/tests/test_settings_surface.py -q
```

Expected: PASS.

- [ ] **Step 7: Run every chat-surface suite, and fix whatever Task 5 broke**

```bash
.venv/bin/pytest agents/ identity/ -q
```

Expected: PASS. Any test that Step 6 of Task 5 recorded as newly red — a count of the "Add the
default X" offers, or of `DEFAULT_AGENTS` — comes back green **here**, because this is the task
that takes the assistant back out of those lists. If one is still red, it is asserting on the
catalogue rather than on the surface; update it to name the three chat-surface defaults explicitly
rather than counting, and say so in its docstring.

- [ ] **Step 8: Update `agents/chat/README.md`**

Document the exclusion: the slug set, the two wrappers, the five list call sites, the narrowing of
`visible_conversation_or_404` and its nine call sites, and — explicitly — the two id-addressed
reads that are **deliberately not narrowed** (`visible_turn`/`chat-turn-status`, and
`agents/workstreams.py:399`'s transcript read) with the one-sentence reason each: both ask the same
read gate, and neither exposes an affordance.

- [ ] **Step 9: Commit**

```bash
git add agents/visibility.py agents/chat/views/conversations.py agents/chat/views/workstreams.py agents/chat/sidebar.py agents/chat/views/all_conversations.py agents/chat/service.py agents/chat/tests/test_settings_surface.py agents/chat/README.md
git commit -m "feat(settings): keep the settings assistant off the chat surface -- unlisted, and unreachable at its own row url"
```

---

### Task 7: promote the shared composer's CSS to `_shell.html`, and write the gate that can see it

**Files:**
- Modify: `agents/chat/templates/chat/base.html:1073-1195` (five selectors and one structural rule
  are **removed**; `.agent-picker` and the four combined picker rules stay — see the ruling below)
- Modify: `foundation/templates/_shell.html` (the same rules, byte-identical, in a new region)
- Modify: `foundation/ops/tests/test_css_ownership.py` (append this phase's own assertion)

**Interfaces:**
- Consumes: nothing.
- Produces: the styling `chat/_composer.html` needs when it is included from a `foundation/`
  template — which is what Task 10 does.

**This task begins by merging `origin/main`** (plan decision 3, spec §14 step 6, §Sequencing note).
The hygiene-sweep session lands CSS-block-only changes to `_settings.html` and the settings leaf
templates on `main`, and this phase's CSS move is explicitly "after the hygiene sweep lands".

**Why the move is required at all** (spec §6.5, decision 11). `_composer.html` uses
`.composer-card`, `.composer-textarea`, `.composer-toolbar`, `.composer-toolbar-left`,
`.composer-toolbar-right` and `.agent-picker`, plus the structural `.composer-card > form {
display: contents; }` rule its own comment at `:68-74` explicitly depends on — all of them defined
in `chat/base.html`. `chat/base.html` and `foundation/templates/_settings.html` both extend
`_shell.html`, so the **deepest common ancestor is `_shell.html`** and the placement rule
(`test_css_ownership.py`'s own docstring: "a selector's home is the deepest template that is an
ancestor of every template that uses it") puts them there. Skipping it reproduces the one live
rendering defect the CSS-ownership gate was written for — an unstyled composer, this time on eleven
settings pages. **Five of those six are promoted; the sixth is not — read the ruling immediately
below before touching any CSS.**

**Deviation from spec §6.5, recorded — orchestrator ruling, 2026-09-10.** `.agent-picker` is
**not** promoted. `chat/_composer.html:105-114` emits `label.agent-picker` only under
`{% if composer_mode == "start" %}` and the panel passes `"turn"`, so it has exactly one consumer —
`chat/base.html`'s own surfaces — and the house single-consumer rule keeps it there. Five of §6.5's
six selectors are load-bearing for this phase and are promoted; the sixth is not. This is also what
removes the four combined-rule splits at `chat/base.html:1167`, `:1171` (a **three**-selector rule
whose third arm belongs to the attach door), `:1176` and `:1187`, which were the most delicate edit
in the plan.

**Why this phase writes the check itself.** `test_css_ownership.py` walks
`_CHAT_TEMPLATES = agents/chat/templates/chat` only (`:107`) and its page set is the pages that
extend `chat/base.html`; `_settings.html` sits outside that directory entirely, which that module
says in its own words at `:310-313`. The one settings-side check,
`test_no_settings_page_retypes_a_rule_settings_html_already_owns` (`:367-405`), compares a leaf
page's `extra_style` against `_settings.html`'s and never looks at `chat/base.html`. So leaving the
selectors where they are would be a real defect with **no red test**, and this phase must not claim
a guard it does not have.

- [ ] **Step 1: Merge `origin/main` and run the suite**

```bash
git fetch origin
```
```bash
git merge origin/main
```

If the hygiene sweep touched `_settings.html`'s `extra_style` block or a settings leaf's, take
**their** version of every CSS-only hunk — this branch has changed no CSS in those files (Task 2
added `id=` attributes to page bodies, not rules) — and keep this branch's `id=` attributes, which
are in different hunks. Then:

```bash
.venv/bin/pytest -q
```

Expected: PASS. This is one of the two full-suite runs in the plan; observe the one-concurrent-suite
cap (Conventions 3).

- [ ] **Step 2: Write the failing placement assertion**

Append to `foundation/ops/tests/test_css_ownership.py`:

```python
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
    wrongly on eleven settings pages."""
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
```

- [ ] **Step 3: Run it to verify it fails**

```bash
.venv/bin/pytest foundation/ops/tests/test_css_ownership.py -q
```

Expected: FAIL — and **on the right assertion**. `test_the_shared_composers_selectors_live_in_the_shell_not_in_chat_base`
must fail on `"the shared composer's selectors must be defined in _shell.html: [all five]"`, **not**
on the `assert shell, "…parsed empty…"` guard above it. If it fails on the guard, `_inline_style` is
not reading `_shell.html` — check that `_INLINE_STYLE_RE` matched, because `_style_block` answers
`""` for this file and reaching for it by habit is the mistake this reader exists to prevent.
`test_the_structural_form_rule_moved_with_them` fails the same way.
`test_the_chat_only_composer_rules_stayed_behind` passes already, which is correct: it is the pin
that says how far the move goes, and its `not _defines(shell, …)` half is only meaningful because
`shell` is now a real string.

- [ ] **Step 4: Move the rules**

**Cut** these from `agents/chat/templates/chat/base.html` and **paste them unchanged** into
`foundation/templates/_shell.html`, in a new region placed after the settings-layout family that
Task 3 added to (so the shell's own reading order stays: tokens, primitives, app bar, settings
layout, shared composer):

```css
  .composer-card { border: 1px solid var(--border); border-radius: 8px;
                   background: var(--panel); padding: 0.75rem;
                   display: flex; flex-wrap: wrap; align-items: end; gap: 1rem;
                   margin-bottom: 1.5rem; }
  .composer-card:focus-within { border-color: var(--accent); }
  .composer-card > form { display: contents; }
  .composer-textarea {
    width: 100%; min-height: 4.5rem; font: inherit;
    border: none; background: transparent; color: var(--text);
    resize: vertical; padding: 0;
  }
  .composer-textarea:focus { outline: none; }
  .composer-toolbar { display: flex; gap: 1rem; align-items: end; flex-wrap: wrap;
                      justify-content: space-between; flex: 1 1 auto; min-width: 0; }
  .composer-toolbar-left, .composer-toolbar-right {
    display: flex; align-items: end; gap: 1rem; flex-wrap: wrap;
  }
```

Head the new region with a comment that says why it is here, so the next reader of `_shell.html`
does not think a chat rule wandered in:

```css
  {% comment %}
  THE ONE COMPOSER'S OWN RECIPE, PROMOTED (settings assistant, spec §6.5,
  decision 11). `chat/_composer.html` is included by
  `agents/chat/templates/chat/base.html`'s three surfaces AND, since the
  settings assistant, by `foundation/templates/_settings.html`'s panel.
  The deepest template that is an ancestor of both is THIS one, so this
  is where these rules live -- `foundation/ops/tests/test_css_ownership.
  py`'s own placement rule, applied across a column boundary that gate
  cannot itself see (its sweep is scoped to `agents/chat/templates/chat/`,
  which that module states at `:310-313`), which is why that phase wrote
  the check for this shape itself.

  RULES MOVED BYTE-IDENTICAL. No pixel changes; only the declaration site
  did. THREE THINGS DELIBERATELY DID NOT MOVE, all for the same reason --
  the settings panel does not render any of them, so each still has
  exactly one consumer: the MODEL picker (`.composer-toolbar .picker`,
  `chat/_picker.html`; the panel passes
  `composer_show_model_picker=False`); the AGENT picker (`.agent-picker`,
  emitted only under `{% templatetag openblock %} if composer_mode ==
  "start" {% templatetag closeblock %}`, and the panel passes `"turn"` --
  orchestrator ruling, 2026-09-10, deviating from spec §6.5's letter);
  and the drag-and-drop staging class (`.composer-card-drop-target`,
  `chat/_attach_dragdrop.html`; `may_attach_files` is absent from the
  panel's context). All three stay chat-only, and the four combined
  picker rules that pair the two pickers stay whole.

  `.composer-card > form { display: contents; }` is STRUCTURAL, not
  decoration: `chat/_composer.html:68-74` depends on it by name -- it is
  what makes the form element generate no box of its own so its children
  become direct flex items of `.composer-card`.
  {% endcomment %}
```

**Nothing else moves, and NOTHING IS SPLIT.** The four combined
`.composer-toolbar .picker, .agent-picker` rules at `chat/base.html:1167`, `:1171`, `:1176` and
`:1187` are **left exactly as they are** — the orchestrator's 2026-09-10 ruling (recorded at the top
of this task) keeps `.agent-picker` in `chat/base.html`, and with it there those rules have no half
to promote. An earlier draft of this task split all four; it does not any more, and that is the
single largest reduction in this phase's risk.

Keep every explanatory `{% comment %}` block that sits above the five rules you are moving, and
move it with them. **Do not delete a comment because its rule moved**, and do not move a comment
whose rule stayed.

- [ ] **Step 5: Run the gate to verify it passes**

```bash
.venv/bin/pytest foundation/ops/tests/test_css_ownership.py -q
```

Expected: PASS, including `test_the_chat_only_composer_rules_stayed_behind`.

- [ ] **Step 6: Confirm no pixel moved, by eye, on all three composer surfaces**

Bring up the preview stack and look at:

```
http://localhost:8001/chat/          — the start box: Agent picker + Model picker, side by side
http://localhost:8001/chat/c/<uuid>/ — a thread: Model picker only, attach door, textarea
http://localhost:8001/chat/w/        — a workstream page's New-chat card
```

Expected: **identical to before this task.** Rules moved to a higher home; nothing about their
text, specificity or order relative to each other changed, and no selector list was split. Pay
attention to the two-control toolbar on `/chat/` — the Agent-plus-Model row is the one shape where
`.composer-toolbar-left`'s wrapping is actually exercised, so it is the one to check at a narrow
window width. Both pickers' own rules stayed in `chat/base.html`, so if either one moves, a rule
was taken that should not have been.

- [ ] **Step 7: Run the chat suite**

```bash
.venv/bin/pytest agents/chat/ foundation/ -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agents/chat/templates/chat/base.html foundation/templates/_shell.html foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(settings): promote the shared composer's recipe to the shell, and gate the placement this phase's own include creates"
```

---

### Task 8: the context processor — two guard clauses, and the two zeroes that let the panel exist

**Files:**
- Create: `agents/chat/context_processors.py`
- Create: `agents/chat/tests/test_assistant_panel.py`
- Modify: `config/settings.py:356-379` (registered after the three column processors already there)

**Interfaces:**
- Consumes: `foundation.settings_help.card_routes` (Task 1); `agents.defaults.SETTINGS_SURFACE_SLUGS`
  is **not** used here — the panel reads the un-narrowed gate;
  `identity.request.{principal_for_request, settings_row_for}`; `identity.access.is_admin`;
  `agents.visibility.{visible_agents, visible_conversations}`; `agents.chat.rendering.render_answer`;
  `agents.chat.service.{MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS}`.
- Produces, for Task 10's template and Task 11's script:
  - `settings_assistant(request) -> dict` — returns `{}` or `{"assistant": <dict>}`
  - `ASSISTANT_PANEL_TURNS: int = 6`
  - `panel_context(request, *, principal=None, settings_row=None, error="", is_open=None,
    next_url="") -> dict` — the same builder the panel ROUTE calls, so the inline render and the
    poll body come out of one function. **`is_open` and `next_url` are what let it serve a request
    whose own URL is not the settings page** — the XHR ask (a POST with no query string) and the
    poll route (whose URL is not a settings page at all). Task 10's views pass both.
  - `ASSISTANT_SLUG: str = "settings-helper"` and `OPEN_PARAM: str = "assistant"`
  - the `assistant` dict's twelve keys, named once here and used verbatim by the template:
    `installed`, `open`, `agent`, `cards`, `links`, `pending`, `error`, `slug`, `next`,
    `poll_interval_ms`, `max_transport_retries`, `max_poll_duration_ms`
  - each entry of `cards`: `{"role": str, "state": str, "text": str, "text_html": SafeString | None,
    "error": str, "pending": bool}`
  - each entry of `links`: `{"url": str, "label": str}`
- **Deliberately NOT produced here:** `ask_url`, `reset_url`, `panel_url`, `install_url`. Those four
  are `reverse()` calls on routes **Task 10 creates**, and putting them here would make this task's
  own tests un-runnable until that one landed — a task boundary that is not green is not a
  boundary. Task 10 adds them to this same dict, in this same function, and its Interfaces block
  says so. `_links` below **does** call `reverse`, on the eleven settings routes, which all exist
  already.

**ONE context key, `assistant`.** That is what makes drill 7's criterion a single assertion —
`"assistant" not in response.context` — rather than a list of names a later phase would have to
keep in step.

**Both guard clauses run before any row is read, and the second is a GATE, not an optimisation.**
The template's `{% if identity_is_admin %}` decides what *renders*; it cannot decide what was
*built*, and a gate that lives one layer above the work it guards is render-vs-gate inverted. The
proving case is real: `setup-index` ("Install guides") is a settings-area entry gated `EVERYONE`
(`foundation/settings_area.py:98`) with route class **"P" — public** (`identity/routes.py:254`),
and `foundation/setup/templates/setup/index.html:1` extends `_settings.html`. So on an accounts-on
box an **anonymous** visitor renders a settings page, passes the `card_routes()` test, and — with a
hand-typed `?assistant=1` — would otherwise have an agent row, a conversation lookup and six turns
resolved for them before a `{% if %}` threw it all away. It is not a leak today; it is a leak
**shape**, on the box's one public settings page, and every field a later phase adds to this
context would inherit it.

- [ ] **Step 1: Write the failing tests**

Create `agents/chat/tests/test_assistant_panel.py`:

```python
"""The settings assistant panel's context, its two guards, and its
budget (spec §6.2, §6.3, §11).

THE BUDGET IS PINNED BY EQUALITY, NEVER BY `<=` -- "a bound that only
forbids growth is a place for a regression to hide". The invariant the
pins protect is that the number is FLAT: in the number of turns, of
conversations and of agents.

TWO OF THE NUMBERS ARE THE SAME IN BOTH POSTURES AND ARE THE PROMISE
THAT LETS THIS PROCESSOR EXIST AT ALL -- zero queries off the settings
area, and zero on a settings page for a principal the panel will not
render for. Those two are the objection `foundation/settings_area.py:
12-20` raised against a context processor for the sidebar, answered.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.context_processors import ASSISTANT_PANEL_TURNS, settings_assistant
# `_patch_queue` IS IN THIS LIST because Tasks 10 and 11 call it in ten
# tests: it patches `enqueue`/`get_job` at the `agents.chat.service` seam
# (`agents/chat/tests/_helpers.py:77-99`) so the ask flow's own code path
# runs for real against no queue.
from agents.chat.tests._helpers import (
    _patch_queue, make_admin, make_turn, make_user, posture, seed_sweep_posture, sign_in,
    user_principal,
)
from agents.defaults import install_default
from agents.models import Agent, Turn
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN

pytestmark = pytest.mark.django_db

SLUG = "settings-helper"
# The settings page these tests drive. `chat-settings` is class S,
# renders for an administrator in both postures, and belongs to this
# column -- so a change here never depends on another column's page.
A_SETTINGS_PAGE = "chat-settings"


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


def _install(principal):
    install_default("agent", SLUG, principal)
    return Agent.objects.get(slug=SLUG)


def _thread(principal, turns=0):
    agent = _install(principal)
    conversation = create_conversation(principal, agent)
    for index in range(turns):
        make_turn(conversation=conversation, role=Turn.Role.USER, text=f"q{index}")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text=f"a{index}",
                  state=Turn.State.DONE)
    return conversation


class TestTheFirstGuardCostsNothingOffTheSettingsArea:
    """THE PANEL'S OWN SHARE IS MEASURED BY CALLING THE PROCESSOR
    DIRECTLY, not by diffing two whole-page counts.

    That is the only non-vacuous way to state a `+N` claim here: a
    whole-page baseline taken with the processor already registered
    ALREADY CONTAINS the panel's cost, so `assert count == baseline + 1`
    would be asserting `n == n + 1` or, worse, passing by accident. The
    absolute whole-request numbers are pinned separately, by equality, in
    the `MEASURED_*` tests below.
    """

    @pytest.mark.parametrize("box", [POSTURE_OPEN, POSTURE_ENTERPRISE])
    def test_a_page_that_is_not_a_settings_page_costs_zero_and_yields_no_key(
            self, rf, django_assert_num_queries, box):
        """+0, IN BOTH POSTURES. On every page in the box that is not a
        settings page the processor costs one attribute read and one set
        membership test -- which is the precise objection
        `foundation/settings_area.py:12-20` raised against a context
        processor for the sidebar, answered."""
        with posture(box):
            request = _request(rf, "chat-index")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    @pytest.mark.parametrize("box", [POSTURE_OPEN, POSTURE_ENTERPRISE])
    def test_it_costs_nothing_even_with_the_open_parameter_typed_by_hand(
            self, rf, django_assert_num_queries, box):
        with posture(box):
            request = _request(rf, "chat-index", query="?assistant=1")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    def test_a_request_that_resolved_to_nothing_at_all_is_survived(self, rf):
        """`resolver_match` is `None` on a request the URL resolver never
        matched -- a 404 render, and any test rendering a template
        directly. The first clause reads it with `getattr`, so this is a
        return rather than an `AttributeError` on a page nobody asked the
        panel about."""
        request = rf.get("/no-such-path/")
        assert settings_assistant(request) == {}


class TestTheSecondGuardIsAtTheDataSeam:
    def test_an_anonymous_visitor_to_the_public_settings_page_gets_no_key(self, client):
        """SPEC §6.2, DECISION 21 -- AND IT IS ASSERTED ON THE CONTEXT,
        NOT ON THE HTML, because the defect being guarded is work done
        BEFORE a template discards it.

        `/setup/` is the proving case rather than a convenient one: it is
        a settings-area entry gated EVERYONE with route class P, it
        extends `_settings.html`, and it HAS a card (assertion 1 requires
        one) -- so it passes the first clause. Without the admin clause,
        an anonymous visitor on an accounts-on box would have an agent
        row, a conversation lookup and six turns resolved for them.
        """
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("setup-index"))
            assert response.status_code == 200
            assert "assistant" not in response.context

    def test_the_same_with_the_open_parameter_typed_by_hand(self, client):
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("setup-index") + "?assistant=1")
        assert response.status_code == 200
        assert "assistant" not in response.context

    def test_an_anonymous_visitor_costs_zero_panel_queries(
            self, rf, django_assert_num_queries):
        """+0 IN BOTH POSTURES for a principal the panel will not render
        for -- the second of the two zeroes that are the promise letting
        this processor exist at all.

        MEASURED ON THE PROCESSOR ITSELF, with the settings row PRIMED
        first: the admin clause reads that singleton, and on a fresh test
        database its very first read also CREATES it. Priming separates
        the one-time cost from the steady-state one, which is the number
        this pins.
        """
        with posture(POSTURE_ENTERPRISE):
            request = _request(rf, "setup-index", query="?assistant=1")
            settings_assistant(request)                       # prime the singleton
            request = _request(rf, "setup-index", query="?assistant=1")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    def test_a_member_on_a_settings_page_gets_no_key_either(self, client):
        """A member cannot reach `chat-settings` (class S), so the page
        this asserts on is the public one again -- signed in this time,
        which is a different principal down a different branch of
        `is_admin`."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("setup-index") + "?assistant=1")
        assert "assistant" not in response.context

    def test_a_member_costs_no_conversation_and_no_turn_read(self, client):
        """THE INVARIANT, STATED AS THE TABLES RATHER THAN AS A COUNT.

        A signed-in member's `is_admin` reads `auth_user` once -- a read
        the gate middleware has ALREADY made and memoised on the same
        settings-row instance -- so the honest claim for this principal
        is not a bare zero but "no panel row is read". That is exactly
        what the leak SHAPE was: an agent row, a conversation lookup and
        six turns resolved for somebody a `{% if %}` was about to throw
        them away from.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            url = reverse("setup-index") + "?assistant=1"
            client.get(url)
            with CaptureQueriesContext(connection) as captured:
                client.get(url)
        sql = " ".join(entry["sql"].lower() for entry in captured.captured_queries)
        for table in ("agents_agent", "agents_conversation", "agents_turn"):
            assert table not in sql, table


class TestTheCollapsedPanel:
    def test_an_administrator_on_a_settings_page_gets_the_key_collapsed(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse(A_SETTINGS_PAGE))
        assistant = response.context["assistant"]
        assert assistant["open"] is False
        assert assistant["installed"] is False
        assert assistant["cards"] == []
        assert assistant["links"] == []

    def test_a_collapsed_panel_reads_no_conversation_and_no_turn(self, client):
        """A `<details>` renders its children into the DOM even when
        closed, so NOT RENDERING THEM is the only honest way to not pay
        for them."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=3)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE))
        assert response.context["assistant"]["cards"] == []

    def test_the_open_posture_collapsed_panel_costs_exactly_one_query(
            self, rf, django_assert_num_queries):
        """+1: THE AGENT ROW, AND NOTHING ELSE. The one number spec §11
        states outright rather than leaving to measurement.

        Measured on the processor itself, with the settings row stashed
        the way `IdentityGateMiddleware` stashes it -- so what this counts
        is the panel's own share and not the page's."""
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            request = _request(rf, A_SETTINGS_PAGE)
            with django_assert_num_queries(1):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["open"] is False
        assert assistant["installed"] is True

    def test_the_not_installed_state_costs_the_collapsed_number_not_the_open_one(
            self, rf, django_assert_num_queries):
        """SPEC §11's explicitly separate case: with NO agent row, an
        `?assistant=1` request must still cost the COLLAPSED number --
        there is no conversation to look up, and looking anyway would be
        a query spent proving nothing exists."""
        with posture(POSTURE_OPEN):
            request = _request(rf, A_SETTINGS_PAGE, query="?assistant=1")
            with django_assert_num_queries(1):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["installed"] is False
        assert assistant["cards"] == []

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_collapsed_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        """MEASURE IT AT IMPLEMENTATION AND WRITE IT IN AS AN EQUALITY.
        Replace `MEASURED_COLLAPSED_ENTERPRISE` below with the number this
        test prints on its first run -- and never with a `<=`.

        WHY IT IS LARGER THAN THE OPEN ONE, AND WHY THAT IS IRREDUCIBLE
        HERE: an administrator on an accounts-on box is not
        `sees_all_content` (`admin_sees_content` defaults to False, and
        `is_admin` is emphatically not `sees_all_content`), so
        `visible_agents` does not take its cheap first branch -- it adds
        the agent-target `shared_keys` plus `label_permitted_q` ->
        `held_entitlement_ids` -> `_grant_ids`. Threading
        `settings_row_for(request)` removes the repeated singleton reads
        and is still required; it cannot remove the share/grant reads, and
        this phase does not widen a cross-column function to chase them.

        THE INVARIANT THE PIN PROTECTS IS FLATNESS: the same number at 0
        turns and at 12.
        """
        MEASURED_COLLAPSED_ENTERPRISE = None  # <-- fill in from the first run
        assert MEASURED_COLLAPSED_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse(A_SETTINGS_PAGE)
            client.get(url)                                   # prime session + singleton
            with django_assert_num_queries(MEASURED_COLLAPSED_ENTERPRISE):
                client.get(url)


class TestTheOpenPanel:
    def test_the_open_parameter_opens_it_and_shows_the_recent_turns(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=2)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["open"] is True
        assert assistant["installed"] is True
        assert [card["text"] for card in assistant["cards"]] == ["q0", "a0", "q1", "a1"]

    def test_it_shows_the_last_n_turns_not_the_whole_history(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=20)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        cards = response.context["assistant"]["cards"]
        assert len(cards) == ASSISTANT_PANEL_TURNS
        # NEWEST N, IN INDEX ORDER -- a panel that showed the OLDEST six
        # would be a panel that never shows the answer just given.
        assert cards[-1]["text"] == "a19"

    def test_an_assistant_turn_is_rendered_through_the_one_renderer(self, client):
        """SPEC §6.4: the panel reuses the thing that matters --
        `agents.chat.rendering.render_answer` -- so a model's `**bold**`,
        lists and code spans render identically here and in `/chat/`,
        ESCAPED FIRST. It does NOT reuse `chat/_turn_block.html`."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      text="**bold** and <script>alert(1)</script>", state=Turn.State.DONE)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        html = str(response.context["assistant"]["cards"][-1]["text_html"])
        assert "<strong>bold</strong>" in html
        assert "<script>" not in html

    def test_a_queued_turn_marks_the_panel_pending(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["pending"] is True
        assert assistant["cards"][-1]["pending"] is True

    def test_a_failed_turn_carries_its_own_honest_error_line(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.FAILED, error="the queue is down")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        card = response.context["assistant"]["cards"][-1]
        assert card["pending"] is False
        assert card["error"] == "the queue is down"

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_open_panel_is_flat_in_the_number_of_turns(
            self, rf, django_assert_num_queries, turns):
        """+3 IN THE OPEN POSTURE: the agent, the conversation, and its
        last N turns -- ONE query for N turns, INCLUDING the tool turns,
        which is where the Jump-to links live, so the strip is built from
        rows already fetched and never costs a read of its own.

        THE SAME 3 AT 0 TURNS AND AT 12. That flatness is the invariant;
        the number itself is only the shape it takes today."""
        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=turns)
            request = _request(rf, A_SETTINGS_PAGE, query="?assistant=1")
            with django_assert_num_queries(3):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["open"] is True
        assert len(assistant["cards"]) == min(2 * turns, ASSISTANT_PANEL_TURNS)

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_open_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        MEASURED_OPEN_ENTERPRISE = None  # <-- fill in from the first run
        assert MEASURED_OPEN_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(MEASURED_OPEN_ENTERPRISE):
                client.get(url)


class TestTheOpenBoxSharesOneThread:
    def test_two_viewers_on_an_open_box_continue_the_same_conversation(self, client):
        """OWNER FLAG 3, ASSERTED RATHER THAN LEFT IN A DOCSTRING.
        `visible_conversations` short-circuits on `sees_all_content` and
        returns `.all()`, and every viewer on an open box is the same
        `OPEN_PRINCIPAL` -- so a household box has ONE assistant thread
        that everyone at the keyboard continues and reads the history of.
        That is correct for a box with no accounts and it matches the
        whole settings area; it is also the half a person would be
        surprised by."""
        from django.test import Client

        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=1)
            first = Client().get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
            second = Client().get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert [c["text"] for c in first.context["assistant"]["cards"]] == \
               [c["text"] for c in second.context["assistant"]["cards"]]


class TestTwoAdministratorsDoNotShare:
    def test_each_administrator_sees_only_their_own_thread(self, client):
        from django.test import Client

        with posture(POSTURE_ENTERPRISE):
            one, two = make_admin(), make_admin()
            _thread(user_principal(one), turns=1)
            client_one, client_two = Client(), Client()
            sign_in(client_one, one)
            sign_in(client_two, two)
            url = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            assert client_one.get(url).context["assistant"]["cards"]
            assert client_two.get(url).context["assistant"]["cards"] == []


def _open_principal():
    from identity.contracts.principals import OPEN_PRINCIPAL

    return OPEN_PRINCIPAL


def _request(rf, name, *, query: str = "", user=None):
    """A request the processor can be called on directly, shaped exactly
    the way a real one reaches it.

    THREE THINGS THE MIDDLEWARE CHAIN NORMALLY PUTS THERE, and every one
    of them changes what is being measured if it is missing:

      * `resolver_match` -- the first guard clause reads its `url_name`.
        A `RequestFactory` request has none until something resolves the
        path, so this resolves it.
      * `identity_settings_row` -- the row `IdentityGateMiddleware`
        stashes. WITHOUT IT `settings_row_for` fetches the singleton
        itself, and the "+1" and "+3" pins above would each be measuring
        one extra read that no real request pays.
      * `user` -- what `principal_for_request` reads on an accounts-on
        box. `AnonymousUser` by default, which is the drill-7 principal.

    DIRECT CALLS RATHER THAN `client.get`, for the pins only: a
    whole-page count already contains the panel's cost, so a `+N` claim
    stated against one would be asserting a number against itself. The
    absolute whole-request numbers ARE pinned -- by equality, in the
    `MEASURED_*` tests, through the real client.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.urls import resolve

    from identity.models import IdentitySettings

    path = reverse(name)
    request = rf.get(path + query)
    request.resolver_match = resolve(path)
    request.identity_settings_row = IdentitySettings.get_solo()
    request.user = user or AnonymousUser()
    return request
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'agents.chat.context_processors'`.

- [ ] **Step 3: Write `agents/chat/context_processors.py`**

```python
"""The settings assistant panel's context (spec §6.2, §6.3).

A CONTEXT PROCESSOR, AND THE OBJECTION IS ANSWERED RATHER THAN IGNORED.
`foundation/settings_area.py:12-20` rejected a context processor for the
settings SIDEBAR, for a stated reason: one "would run on every page in
the box and cost a query the console's own query-count pin forbids". So
the FIRST clause below is a `resolver_match.url_name` membership test
against a pure `frozenset`, and on every page in the box that is not a
settings page this processor costs one attribute read, one set lookup and
ZERO queries.

THE SECOND CLAUSE IS A GATE, NOT AN OPTIMISATION, and it is this
repository's own named defect class. `_settings.html`'s `{% if
identity_is_admin %}` decides what RENDERS; it cannot decide what was
BUILT, and a gate that lives one layer above the work it guards is
render-vs-gate inverted. The proving case is real: `setup-index`
("Install guides") is a settings-area entry gated EVERYONE with route
class P -- PUBLIC -- and `foundation/setup/templates/setup/index.html`
extends `_settings.html`. So on an accounts-on box an ANONYMOUS visitor
renders a settings page, passes the first clause (Install guides has a
card; the drift test requires one), and, with a hand-typed
`?assistant=1`, would otherwise have an agent row, a conversation lookup
and six turns resolved for them before a `{% if %}` threw it all away. It
is not a leak today -- `visible_conversations(ANONYMOUS)` returns that
principal's own nothing -- it is a leak SHAPE, on the box's one public
settings page, and every field a later phase adds to this context would
inherit it.

NO PANEL CONTEXT KEY IS PRODUCED, AND NO CONVERSATION OR TURN IS READ,
FOR A PRINCIPAL THE PANEL WILL NOT RENDER FOR.

IT LIVES IN `agents/chat` because it reads `Conversation`/`Turn` rows,
and it reads them through `agents/visibility.py`, never through a manager
(`agents/visibility.py:765-771`). Registered in `config/settings.py`'s
`TEMPLATES` -- the composition root is the one place that already names
every column. The alternatives are worse and one is impossible: per-view
context would need an edit in every column that owns a settings page, and
`identity/` -- which owns four of the eleven -- may not import `agents/`
AT ALL; a custom template tag would work, but this repository has no
`templatetags` package anywhere, so it would be a brand-new mechanism
where an existing one fits.

IT READS THROUGH THE UN-NARROWED GATE, deliberately:
`visible_conversations(principal).filter(agent__slug=...)`, not
`chat_surface_conversations`. This IS the surface the chat-surface
exclusion excludes FROM.
"""
from __future__ import annotations

from django.urls import reverse

from agents.chat.rendering import render_answer
from agents.chat.service import (
    MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
)
from agents.models import Turn
from agents.visibility import visible_agents, visible_conversations
from foundation.settings_help import card_for, card_routes
from identity.access import is_admin
from identity.request import principal_for_request, settings_row_for

# The slug the catalogue declares (`agents/defaults.py`). Named here
# rather than imported from `SETTINGS_SURFACE_SLUGS`, which is a SET
# expressing a different question ("which agents are off `/chat/`"): this
# module needs the ONE agent it renders, and a `next(iter(...))` over a
# set would be that set silently deciding which agent the panel is for.
ASSISTANT_SLUG = "settings-helper"

# How many recent turns the open panel renders. SIX, not a page: this is
# a guide's last exchange or two, not a thread reader -- `/chat/` is where
# a whole history is read, and this surface has no scrollback affordance
# of its own to make more useful.
ASSISTANT_PANEL_TURNS = 6

# The panel's open-state parameter (spec §6.3, decision 9). ONE query
# parameter is what makes "it persists across screens" true with no
# script and no client storage: the ask/reset redirects carry it, and so
# does every link the assistant emits -- so a session of asking and
# following links keeps the panel open the whole way, and a plain
# navigation to a settings page starts collapsed.
OPEN_PARAM = "assistant"


def settings_assistant(request) -> dict:
    """The panel's context, or `{}` -- registered in
    `config/settings.py::TEMPLATES`.

    TWO CLAUSES, IN THIS ORDER, AND BOTH RUN BEFORE ANY ROW IS READ. See
    this module's docstring for why the second one is a gate rather than
    a tidiness.
    """
    match = getattr(request, "resolver_match", None)
    if match is None or match.url_name not in card_routes():
        return {}
    row = settings_row_for(request)
    # `settings_row=row` ON BOTH CALLS, and it is not a flourish:
    # `principal_for_request` reads the singleton itself through
    # `accounts_on()` when it is not given one (`identity/request.py:91`),
    # and `is_admin` reads it again. Threading the row the gate
    # middleware already stashed is what makes this whole clause cost
    # ZERO queries -- and `_user_row`'s memoisation on that same instance
    # (`identity/access.py:80-93`) is what keeps the administrator branch
    # from re-reading `auth_user` a second time in one request.
    principal = principal_for_request(request, settings_row=row)
    if not is_admin(principal, settings_row=row):
        return {}
    return panel_context(request, principal=principal, settings_row=row)


def panel_context(request, *, principal=None, settings_row=None, error: str = "",
                  is_open=None, next_url: str = "") -> dict:
    """`{"assistant": {...}}` -- the ONE key, built once.

    THE SAME BUILDER THE PANEL ROUTE CALLS, so the inline render and the
    poll body come out of the SAME function over the SAME rows -- the
    `chat/_turn_block.html` precedent, where "this page's inline render
    and `turn_status`'s poll body come out of the SAME loop over the SAME
    fragment".

    ONE KEY, not a dozen: "an anonymous visitor gets no panel context at
    all" is then a single assertion a later phase cannot erode by adding
    a twelfth name.

    `settings_row` is THREADED, never re-fetched: the row the gate
    middleware already stashed, handed to every access call this makes,
    so the panel reads the `IdentitySettings` singleton once per request.

    `is_open` AND `next_url` ARE EXPLICIT PARAMETERS, and that is the
    whole difference between a panel that works and one that looks like
    it does. Rendered INLINE on a settings page, "is the panel open" and
    "which page does it belong to" really are the current request's own
    query string and path. Rendered from `settings-assistant-ask` -- a
    POST with NO query string -- or from `settings-assistant-panel` --
    whose own URL is not a settings page at all -- they are not:
    `request.GET` is empty there, so the fragment would come back
    COLLAPSED, with no transcript and no pending marker, and the poller
    would stop on the first tick. That is the one path the script exists
    to serve. Reading `get_full_path()` there is the same defect on the
    other axis: it would hand the composer its own action URL as a
    `next`, so a transport-failure fallback would GET a `@require_POST`
    view (405) and each poll would re-encode the previous poll's URL
    into the next one until the query string ran away.
    """
    if settings_row is None:
        settings_row = settings_row_for(request)
    if principal is None:
        principal = principal_for_request(request, settings_row=settings_row)

    # THE PANEL'S STATE COMES FROM THE PAGE IT BELONGS TO, NOT FROM THE
    # URL OF THE REQUEST RENDERING IT. The two defaults below are right
    # for exactly one caller -- the context processor, on a settings page
    # -- and both assistant views override them. See the docstring.
    if is_open is None:
        is_open = request.GET.get(OPEN_PARAM) == "1"
    here = next_url or request.get_full_path()

    # NO `enabled=True`: `visible_agents` already applies it, and this
    # tree states the convention outright -- the `enabled` term "goes
    # with the manager call it came from -- no second filter needed here"
    # (`agents/runtime/delegate.py:99-102`).
    agent = visible_agents(principal).filter(slug__iexact=ASSISTANT_SLUG).first()

    cards: list[dict] = []
    links: list[dict] = []
    pending = False

    # COLLAPSED COSTS ONE QUERY -- the agent row -- AND READS NO
    # CONVERSATION AND NO TURN. A `<details>` renders its children into
    # the DOM even when closed, so not rendering them is the only honest
    # way to not pay for them. Not-installed costs the same as collapsed:
    # there is no conversation to look up, and looking anyway would be a
    # query spent proving nothing exists.
    if is_open and agent is not None:
        conversation = visible_conversations(
            principal, settings_row=settings_row).filter(
                agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True).first()
        if conversation is not None:
            # ONE QUERY FOR N TURNS, INCLUDING THE TOOL TURNS -- which is
            # where the Jump-to links live, so the strip below is built
            # from rows already fetched and never costs a read of its own.
            recent = list(
                conversation.turns.order_by("-index")[:ASSISTANT_PANEL_TURNS])[::-1]
            cards = [_card(turn) for turn in recent]
            pending = any(card["pending"] for card in cards)
            links = _links(recent)

    return {"assistant": {
        "installed": agent is not None,
        "open": is_open,
        "agent": agent,
        "cards": cards,
        "links": links,
        "pending": pending,
        "error": error,
        "slug": ASSISTANT_SLUG,
        "next": here,
        # THE FOUR URL KEYS -- `ask_url`, `reset_url`, `panel_url`,
        # `install_url` -- ARE ADDED BY THE TASK THAT CREATES THOSE
        # ROUTES, not here. `reverse()`-ing a name that does not exist yet
        # would make this module's own tests un-runnable until that task
        # landed, and a task boundary that is not green is not a
        # boundary. `_links` below reverses only the ELEVEN SETTINGS
        # ROUTES, every one of which already exists.
        # DECLARED ONCE, IN PYTHON (`agents/chat/service.py:93-95`), AND
        # HANDED TO THE TEMPLATE -- never a second copy typed into the
        # script, which is the rule `chat/conversation.html:396-407`
        # states for the poller this one is not a copy of.
        "poll_interval_ms": POLL_INTERVAL_MS,
        "max_transport_retries": MAX_TRANSPORT_RETRIES,
        "max_poll_duration_ms": MAX_POLL_DURATION_MS,
    }}


def _card(turn) -> dict:
    """One turn, in the four states this surface has (spec §6.4).

    ITS OWN COMPACT VIEW MODEL, NOT `agents.chat.rendering.turn_card`'s:
    that one carries tool cards with arguments, thumbnails and citations,
    artifact image and file strips, attachment rows and five turn states,
    and a settings panel needs none of it. Reusing the card MARKUP would
    drag roughly twenty `.turn*`/`.tool*` selectors out of
    `chat/base.html` and into the global shell so they could render on a
    page that then hides most of them.

    WHAT IT DOES REUSE IS THE THING THAT MATTERS: `render_answer`, so a
    model's `**bold**`, lists and code spans render identically here and
    in `/chat/`, ESCAPED FIRST -- and, as there, emitting no `<a>` from a
    URL or otherwise.
    """
    return {
        "role": turn.role,
        "state": turn.state,
        "text": turn.text,
        "text_html": (render_answer(turn.text)
                      if turn.role == Turn.Role.ASSISTANT else None),
        "error": turn.error,
        "pending": turn.state in (Turn.State.QUEUED, Turn.State.RUNNING),
    }


def _links(turns) -> list[dict]:
    """The "Jump to" strip, built from the MOST RECENT TOOL TURN's own
    `data["links"]` -- and whitelisted by construction (spec §4.3).

    THE MODEL NEVER PRODUCES A LINK. `render_answer` escapes first and
    emits no `<a>` from a URL or otherwise, which is a deliberate posture
    -- an `<a href>` a model can be talked into writing is a phishing
    primitive -- and this does not weaken it. So the links are the
    PLATFORM's, built from the platform's own table: every entry whose
    `route` is not in `card_routes()`, or whose `anchor` is not one of
    THAT CARD's own `HelpField.anchor` values, is dropped before anything
    is reversed.

    Nothing that is not already in the code-side table can become a link
    on a settings page. That is the property that matters most here,
    because a link is the one thing on this surface an operator will
    click.
    """
    routes = card_routes()
    for turn in reversed(turns):
        if turn.role != Turn.Role.TOOL or not turn.data:
            continue
        raw = turn.data.get("links") or []
        built: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            route, anchor = entry.get("route"), entry.get("anchor")
            if route not in routes:
                continue
            card = card_for(route)
            if card is None or anchor not in {f.anchor for f in card.fields}:
                continue
            label = next(f.name for f in card.fields if f.anchor == anchor)
            built.append({
                "url": f"{reverse(route)}?{OPEN_PARAM}=1#{anchor}",
                "label": label,
            })
        if built:
            return built
    return []
```

Note `_links` takes the label from the **card**, not from the tool result: a label is display text,
and taking it from the row would let a hostile tool result put arbitrary text under a real link.

- [ ] **Step 4: Register the processor**

`config/settings.py`, in `TEMPLATES[0]["OPTIONS"]["context_processors"]`, after
`"models.registry.context_processors.availability"` (`:378`) and before the closing `]` (`:379`):

```python
                # The settings assistant panel's context (spec §6.2).
                # Registered here for the same reason the three above are
                # -- the composition root is the one place that already
                # names every column, and `identity/`, which owns four of
                # the eleven settings pages, may not import `agents/` at
                # all. It returns `{}` on every page that is not a
                # settings page, at ZERO queries, and `{}` again for a
                # principal the panel will not render for.
                "agents.chat.context_processors.settings_assistant",
```

- [ ] **Step 5: Run the tests, and MEASURE the two accounts-on numbers**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Then, with `MEASURED_COLLAPSED_ENTERPRISE` and `MEASURED_OPEN_ENTERPRISE` still `None`, read the
real numbers off a failing equality by setting them to an obviously wrong value (`0`) and reading
what pytest reports as actual:

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q -k "accounts_on"
```

Write the two reported numbers into the test file **as equalities**, and add a one-line comment
beside each recording what they are made of, in the shape
`models/registry/tests/test_views.py:3540-3586` uses ("9, not 5, and every one of them is FLAT").
**Never a `<=`.** Both parametrized runs (0 turns and 12) must report the **same** number; if they
do not, the transcript read is not one query and that is the bug this pin exists to catch.

- [ ] **Step 6: Verify the whole module is green**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Expected: PASS.

- [ ] **Step 7: Confirm the two zeroes across the whole box**

```bash
.venv/bin/pytest -q
```

Expected: PASS. **Every page in this repository now runs this processor**, so a full suite is the
honest gate here rather than a narrow one — a missing `resolver_match` on some view, or a page
whose own query pin the processor moved, shows up nowhere else. Observe the one-concurrent-suite
cap.

- [ ] **Step 8: Commit**

```bash
git add agents/chat/context_processors.py agents/chat/tests/test_assistant_panel.py config/settings.py
git commit -m "feat(settings): the panel's context processor, gated at the data seam, with its budget pinned by equality"
```

---

### Task 9: `composer_next` on the one composer, and a validated `next` on the install route

**Files:**
- Modify: `agents/chat/templates/chat/_composer.html:79-83` area (one new optional parameter) and
  its parameter docstring at `:16-59`
- Modify: `agents/chat/views/defaults.py:54-55`
- Modify: `agents/chat/tests/test_assistant_panel.py` (append `TestTheSharedComposerParameter`)

**Interfaces:**
- Consumes: `agents.chat.service.validated_next_url` (`:662`), unchanged.
- Produces, for Task 10: the `composer_next` include parameter, and a `chat-default-install` that
  returns where it was posted from.

**Two small changes to shared code, together in one task** because they are the same shape — a
surface that needs to come back to where it started — and because a reviewer can accept or reject
them as one thing.

**`composer_next` is the only change this phase makes to that fragment.** It is needed because
`agents/chat/service.py::validated_next_url` reads `request.POST.get("next", "").strip()` (`:693`)
and the composer emits no such field. It is the right shape rather than a workaround: that
fragment already emits exactly one other hidden routing field on exactly this basis
(`composer_workstream`, `:79-83`), and its own rule is **"PARAMETERIZED ONLY WHERE THE SURFACES
GENUINELY DIFFER"** (`:16-17`). **Every existing include site omits it and is byte-identical to
today.** The alternative — putting `next` in the action URL's query string and teaching the view to
read `request.GET` — would fork the one `next` reader the codebase has.

- [ ] **Step 1: Write the failing tests**

Append to `agents/chat/tests/test_assistant_panel.py`:

```python
class TestTheSharedComposerParameter:
    """`composer_next` (spec §6.5) -- the ONE change this phase makes to
    `chat/_composer.html`, in the shape that fragment already uses for
    `composer_workstream`."""

    def test_it_emits_a_hidden_next_when_passed(self):
        from django.template.loader import render_to_string

        html = render_to_string("chat/_composer.html", {
            "composer_mode": "turn", "composer_action": "/settings/assistant/ask/",
            "composer_next": "/chat/settings/?assistant=1",
            "composer_placeholder": "Ask about these settings", "composer_button": "Ask",
        })
        assert '<input type="hidden" name="next" value="/chat/settings/?assistant=1">' in html

    def test_it_emits_nothing_when_omitted(self):
        """EVERY EXISTING INCLUDE SITE OMITS IT AND MUST BE
        BYTE-IDENTICAL TO TODAY."""
        from django.template.loader import render_to_string

        html = render_to_string("chat/_composer.html", {
            "composer_mode": "start", "composer_action": "/chat/start/",
            "composer_placeholder": "Message", "composer_button": "Send",
        })
        assert 'name="next"' not in html

    def test_the_three_existing_chat_surfaces_still_emit_no_next(self, client):
        """ALL THREE SURFACES THAT INCLUDE `chat/_composer.html`, through
        the real pages rather than the fragment, so a regression in an
        include site is caught as well as one in the fragment.

        THE CONVERSATION PAGE IS THE ONE THAT MATTERS MOST and is the one
        an earlier draft of this test left out: it posts to
        `turn_create`, which reads `next` through the same
        `validated_next_url` this phase is extending, so a stray hidden
        field there would not be inert -- it would redirect the operator
        somewhere `turn_create` never intended."""
        from agents.chat.tests._helpers import make_thread

        with posture(POSTURE_OPEN):
            conversation = make_thread()
            pages = (
                reverse("chat-index"),
                reverse("chat-workstreams"),
                reverse("chat-conversation", args=[conversation.pk]),
            )
            for url in pages:
                body = client.get(url).content.decode()
                # SCOPED TO THE COMPOSER, NOT THE WHOLE PAGE. The sidebar's
                # row menus have emitted their own `name="next"` since
                # rename/pin/archive landed (`chat/_sidebar_row.html:63,
                # 81,88,97,104`), and `chat/base.html` draws that rail on
                # all three of these pages -- so a whole-body assertion is
                # false against `main` itself, the moment there is one
                # conversation for the rail to list. What this phase must
                # not change is the COMPOSER's fields, and that is what
                # this reads: `_composer.html:66` opens `<div class=
                # "composer-card">` and `:126` closes its `</form>`.
                start = body.index('<div class="composer-card">')
                composer = body[start:body.index("</form>", start)]
                assert 'name="next"' not in composer, url


class TestTheInstallOfferComesBack:
    def test_installing_from_a_settings_page_returns_to_that_page(self, client):
        """SPEC §6.5, DECISION 12: three lines on an existing route
        against a fourth new route. `validated_next_url` already exists
        and is already used by two other views."""
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper",
                "next": reverse(A_SETTINGS_PAGE) + "?assistant=1",
            })
        assert response.status_code == 302
        assert response.headers["Location"] == reverse(A_SETTINGS_PAGE) + "?assistant=1"

    def test_a_next_off_this_host_is_refused_and_falls_back(self, client):
        """`validated_next_url` is the guard, unchanged: a `next` value is
        caller-supplied, and an unchecked one is an open redirect off this
        box."""
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper",
                "next": "https://example.invalid/steal",
            })
        assert response.headers["Location"] == reverse("chat-index")

    def test_with_no_next_it_behaves_exactly_as_before(self, client):
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "general"})
        assert response.headers["Location"] == reverse("chat-index")

    def test_the_race_path_honours_next_too(self, client):
        """`default_install` has TWO return paths -- the ordinary one and
        the `IntegrityError` one that catches two concurrent
        double-clicks. Both get the same treatment, because an operator
        who lost the race got the state they asked for and should land
        where the winner landed."""
        with posture(POSTURE_OPEN):
            target = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper", "next": target})
            again = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper", "next": target})
        assert again.headers["Location"] == target
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q -k "Composer or InstallOffer"
```

Expected: FAIL — the hidden field is not emitted, and both install redirects go to `chat-index`.

- [ ] **Step 3: Add `composer_next` to the fragment**

`agents/chat/templates/chat/_composer.html`. Immediately after the `{% endwith %}` that closes the
`routing_workstream` block (`:83`):

```django
    {% comment %}
    `composer_next` -- ONE MORE HIDDEN ROUTING FIELD, in the identical
    shape `composer_workstream` just above already uses, and it is the
    ONLY change the settings assistant phase makes to this fragment.

    IT IS NEEDED because `agents/chat/service.py::validated_next_url`
    reads `request.POST.get("next", "")` and this fragment emits no such
    input -- and it is the right shape rather than a workaround: the
    alternative (putting `next` in the action URL's query string and
    teaching the view to read `request.GET`) would FORK the one `next`
    reader this codebase has.

    EVERY EXISTING INCLUDE SITE OMITS IT and is byte-identical to before.
    The settings panel passes it because a settings page can carry a
    half-filled form, and a plain POST that navigated to `/chat/` and
    stayed there would be worse than one that comes back.
    {% endcomment %}
    {% if composer_next %}
    <input type="hidden" name="next" value="{{ composer_next }}">
    {% endif %}
```

and add it to the parameter list in that file's own opening `{% comment %}` block (`:16-59`),
beside `composer_workstream`, in the same one-line-per-parameter style:

```
  `composer_next` -- OPTIONAL. When passed, emits `<input type="hidden"
  name="next">` so the posting view can return the operator to the page
  they posted from, through `agents.chat.service.validated_next_url`.
  Omitted by every `/chat/` surface, which posts to a view that already
  knows where to go.
```

- [ ] **Step 4: Honour `next` on both of `default_install`'s return paths**

`agents/chat/views/defaults.py`. Add the import:

```python
from agents.chat.service import validated_next_url
```

and replace the two `return redirect(reverse("chat-index"))` lines (`:54` and `:55`) with the same
expression, computed once above them:

```python
    except IntegrityError:
        # ... existing comment, unchanged ...
        return redirect(_back(request))
    return redirect(_back(request))
```

with the helper immediately above the view:

```python
def _back(request) -> str:
    """Where an install returns to.

    THE PANEL'S OFFER POSTS HERE (settings assistant, spec §6.5, decision
    12): three lines on an existing route beats a fourth route, and
    `validated_next_url` -- which already guards this exact question for
    `views/conversations.py` and `views/turns.py` -- is what keeps a
    caller-supplied `next` from being an open redirect off this box.
    `chat-index` when there is no usable value, which is every caller
    that existed before this phase.
    """
    return validated_next_url(request) or reverse("chat-index")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q -k "Composer or InstallOffer"
```

Expected: PASS.

- [ ] **Step 6: Prove no chat surface moved**

```bash
.venv/bin/pytest agents/chat/ -q
```

Expected: PASS, with **no** test edited. Three surfaces include this fragment and none of them
passes `composer_next`; if a chat test goes red here, the `{% if %}` is emitting something when it
should not.

- [ ] **Step 7: Commit**

```bash
git add agents/chat/templates/chat/_composer.html agents/chat/views/defaults.py agents/chat/tests/test_assistant_panel.py
git commit -m "feat(settings): one optional next field on the shared composer, and an install route that returns where it was posted from"
```

---

### Task 10: the panel — three class-S routes, the fragment, and the whitelisted "Jump to" strip

**Files:**
- Create: `agents/chat/views/assistant.py`
- Create: `agents/chat/assistant_urls.py`
- Create: `agents/chat/templates/chat/_assistant_panel.html`
- Modify: `agents/chat/views/__init__.py` (export the three views)
- Modify: `config/urls.py:34` area (mount, inside `urlpatterns`, before the `]` at `:35`)
- Modify: `identity/routes.py::ROUTE_RULES` (three names, class **S**)
- Modify: `identity/tests/test_route_matrix.py:92` (three `_DRIVERS` entries)
- Modify: `foundation/templates/_settings.html:146` (the gated include, inside `.settings-main`)
- Modify: `agents/chat/tests/test_assistant_panel.py` (append the route and render classes)

**Files (continued):**
- Modify: `agents/chat/context_processors.py` (`panel_context` gains the four URL keys)

**Interfaces:**
- Consumes: `panel_context`, `ASSISTANT_SLUG`, `OPEN_PARAM` and the twelve-key `assistant` dict
  (Task 8); `composer_next` (Task 9); `agents.chat.service.{start_turn, validated_next_url}`;
  `agents.visibility.{create_conversation, set_conversation_archived, visible_agents,
  visible_conversations}`; `agents.chat.views.turns._is_xhr`'s five-line header read (copied, not
  imported — see below).
- Produces:
  - `settings-assistant-ask` → `POST /settings/assistant/ask/`
  - `settings-assistant-reset` → `POST /settings/assistant/reset/`
  - `settings-assistant-panel` → `GET /settings/assistant/panel/`
  - the four URL keys Task 8 deliberately left out, added to the **same** `assistant` dict in the
    **same** `panel_context`, bringing it to sixteen: `ask_url`, `reset_url`, `panel_url`,
    `install_url`
  - the template `chat/_assistant_panel.html`, whose one script Task 11 adds

**All three are class S** (`identity/routes.py::ROUTE_RULES`, `:43`) — the middleware refuses a
non-admin before the view runs, and a name absent from that table is treated as admin **and
logged** (`tier_for`, `:302`). The class matches `chat-settings`'s own reasoning at `:94-99`: "a
page whose entire body is an administrator-only form has nothing to show anybody else." Each new
name also needs a `_DRIVERS` entry or `test_every_route_has_a_driver` (`:633`) fails — which is the
intended way a new route announces itself.

**Mounted by `config/urls.py`**, beside `path("settings/", include("foundation.settings_area"))`
(`:34`). The views live in `agents/chat` because they read `agents/` rows; `config/` is the
composition root and is the one module that already imports every column, so nothing crosses a
boundary to mount them.

**No `?pending=<turn_id>` on this surface, deliberately** (§6.6 step 5, decision 9, §18 item 7). On
the chat page that parameter is the hand-off from a script-free POST to a script-enabled reload
(`chat/conversation.html:29-33`). The panel needs no hand-off: it re-renders its **whole** recent
transcript on every render, so the queued turn is already on the page, and the "is anything still
running" marker is derived server-side from those rows' own states. Carrying a turn id nothing
reads would be cargo.

- [ ] **Step 1: Write the failing tests**

Append to `agents/chat/tests/test_assistant_panel.py`:

```python
class TestTheRoutesAreGated:
    ROUTES = (
        ("settings-assistant-ask", "post", {"text": "hello"}),
        ("settings-assistant-reset", "post", {}),
        ("settings-assistant-panel", "get", {}),
    )

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_a_member_gets_403_from_all_three(self, client, name, method, body):
        """CLASS S: the middleware refuses before the view runs."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code == 403, name

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_an_anonymous_caller_never_gets_a_200(self, client, name, method, body):
        with posture(POSTURE_ENTERPRISE):
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code in (302, 401, 403), name

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_every_viewer_on_an_open_box_reaches_them(self, client, name, method, body):
        """OWNER FLAG 3 / GLOBAL CONSTRAINT 3: `is_admin` is True for
        everybody on an open box, so a household box's routes answer the
        person at the keyboard. That is consistent with the whole
        settings area."""
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code != 403, name

    def test_all_three_are_classified_s_and_driven(self):
        """`identity/routes.py:43` and
        `identity/tests/test_route_matrix.py:92`. A name absent from
        ROUTE_RULES is treated as admin AND LOGGED, so this is not
        cosmetic."""
        from identity.routes import ROUTE_RULES
        from identity.tests.test_route_matrix import _DRIVERS

        for name, _method, _body in self.ROUTES:
            assert ROUTE_RULES[name] == "S", name
            assert name in _DRIVERS, name


class TestTheAskFlow:
    def test_a_non_xhr_ask_redirects_back_carrying_the_open_parameter(self, client, monkeypatch):
        """SPEC §6.6 STEP 5, AND DRILL 5. With no script at all: a plain
        POST, a redirect back to the page you asked from with
        `?assistant=1`, and the queued turn visible on that page."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "how do I lock the library?", "next": here})
        assert response.status_code == 302
        assert response.headers["Location"] == f"{here}?assistant=1"

    def test_that_page_then_renders_the_panel_open_with_the_queued_turn(
            self, client, monkeypatch):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "hello", "next": here})
            response = client.get(f"{here}?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["open"] is True
        assert assistant["pending"] is True
        assert [card["text"] for card in assistant["cards"]][0] == "hello"
        body = response.content.decode()
        assert "Thinking" in body

    def test_the_redirect_carries_no_pending_turn_id(self, client, monkeypatch):
        """DECISION 9 / §18 ITEM 7: the panel re-renders its whole recent
        transcript on every render, so the queued turn is a row it already
        read and the pending marker is derived server-side. Carrying a
        turn id nothing reads would be cargo."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": reverse(A_SETTINGS_PAGE)})
        assert "pending=" not in response.headers["Location"]

    def test_a_next_off_this_host_falls_back_to_the_settings_index(self, client, monkeypatch):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": "https://example.invalid/x"})
        assert response.headers["Location"] == f"{reverse('settings-index')}?assistant=1"

    def test_an_xhr_ask_gets_the_panel_fragment_not_json(self, client, monkeypatch):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(
                reverse("settings-assistant-ask"),
                {"text": "hello", "next": reverse(A_SETTINGS_PAGE)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert "hello" in response.content.decode()

    def test_asking_with_no_agent_row_is_a_400_naming_the_offer(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": reverse(A_SETTINGS_PAGE)})
        assert response.status_code == 400
        assert "install" in response.content.decode().lower()

    def test_a_refusal_lands_in_the_panels_own_error_slot_never_a_bare_fragment(
            self, client, monkeypatch):
        """The rule `agents/chat/views/turns.py:64-67` states: a non-XHR
        refusal RE-RENDERS, it does not answer a bare fragment.

        `raises=` takes an EXCEPTION INSTANCE, not a flag: the helper
        does `raise raises` (`agents/chat/tests/_helpers.py:88-90`), so a
        bare `True` is `TypeError: exceptions must derive from
        BaseException`. `QueueUnavailable("down")` is the house value
        (`agents/chat/tests/_helpers.py:117-121`)."""
        from models.contracts.queue import QueueUnavailable

        _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": here}, follow=True)
        assert response.status_code == 200
        assert "settings-nav" in response.content.decode()   # the whole page, not a fragment

    def test_blank_text_is_refused_without_writing_a_turn(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            before = Turn.objects.count()
            client.post(reverse("settings-assistant-ask"),
                        {"text": "   ", "next": reverse(A_SETTINGS_PAGE)})
        assert Turn.objects.count() == before


class TestOneConversationPerAdministrator:
    def test_two_asks_produce_one_conversation_and_two_user_turns(self, client, monkeypatch):
        _patch_queue(monkeypatch)
        from agents.models import Conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            _finish_pending()
            client.post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 1
        assert Turn.objects.filter(role=Turn.Role.USER).count() == 2

    def test_two_administrators_get_two_conversations(self, client, monkeypatch):
        from django.test import Client
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            one, two = make_admin(), make_admin()
            _install(user_principal(one))
            here = reverse(A_SETTINGS_PAGE)
            for account in (one, two):
                per = Client()
                sign_in(per, account)
                per.post(reverse("settings-assistant-ask"), {"text": "hi", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 2

    def test_the_created_conversation_carries_no_workstream(self, client, monkeypatch):
        """SPEC §7.1.2 depends on this NULL, and Task 6 pins it from the
        other side. `create_conversation(principal, agent)` -- no
        `workstream` -- and that column is stamped once and never written
        again."""
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            client.post(reverse("settings-assistant-ask"),
                        {"text": "hi", "next": reverse(A_SETTINGS_PAGE)})
        assert Conversation.objects.get(agent__slug=SLUG).workstream_id is None

    def test_start_over_archives_and_the_next_ask_creates_a_fresh_one(
            self, client, monkeypatch):
        """NOTHING IS DELETED: the old thread is archived, and the
        chat-surface exclusion keeps it out of the archive listing too."""
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            reset = client.post(reverse("settings-assistant-reset"), {"next": here})
            assert reset.headers["Location"] == f"{here}?assistant=1"
            assert Conversation.objects.filter(
                agent__slug=SLUG, archived_at__isnull=False).count() == 1
            client.post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 2
        assert Conversation.objects.filter(
            agent__slug=SLUG, archived_at__isnull=True).count() == 1

    def test_on_an_open_box_two_viewers_share_one_conversation(self, client, monkeypatch):
        """DELIBERATELY (spec §6.6 step 3, owner flag 3)."""
        from django.test import Client
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            here = reverse(A_SETTINGS_PAGE)
            Client().post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            _finish_pending()
            Client().post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 1


class TestTheJumpToStrip:
    def _tool_turn(self, conversation, data):
        return make_turn(conversation=conversation, role=Turn.Role.TOOL, text="",
                         state=Turn.State.DONE, data=data,
                         tool_call={"tool": "settings.card", "args": {}, "agent": SLUG,
                                    "id": "", "discarded": []})

    def test_a_real_link_is_built_from_the_platforms_own_table(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"page": "identity-settings", "links": [
                {"route": "identity-settings", "anchor": "library-posture",
                 "label": "Library posture"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        links = response.context["assistant"]["links"]
        assert links == [{
            "url": f"{reverse('identity-settings')}?assistant=1#library-posture",
            "label": "Library posture",
        }]
        assert links[0]["url"] in response.content.decode()

    def test_an_unknown_route_renders_no_anchor(self, client):
        """SPEC §10.3 "links are whitelisted". A whitelist BY
        CONSTRUCTION: nothing that is not already in the code-side table
        can become a link on this page."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "admin:index", "anchor": "x", "label": "Django admin"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []
        assert "Django admin" not in response.content.decode()

    def test_a_foreign_anchor_on_a_real_route_renders_no_anchor(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "identity-settings", "anchor": "retention",
                 "label": "Retention"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []

    def test_the_label_comes_from_the_card_not_from_the_tool_result(self, client):
        """A label is DISPLAY TEXT. Taking it from the row would let a
        hostile tool result put arbitrary words under a real link."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "identity-settings", "anchor": "library-posture",
                 "label": "CLICK HERE TO WIN"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"][0]["label"] == "Library posture"
        assert "CLICK HERE TO WIN" not in response.content.decode()

    def test_a_tool_turn_with_malformed_data_renders_no_anchor_and_no_error(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": ["not-a-dict", {"route": None}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.status_code == 200
        assert response.context["assistant"]["links"] == []

    def test_the_most_recent_tool_turn_wins(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "rag-settings", "anchor": "retention", "label": "Retention"}]})
            self._tool_turn(conversation, {"links": [
                {"route": "chat-settings", "anchor": "time-aware", "label": "x"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"][0]["url"].startswith(
            reverse("chat-settings"))


class TestThePanelRoute:
    def test_it_returns_the_same_fragment_the_inline_panel_renders(self, client):
        """THE `_turn_block.html` PRECEDENT: this page's inline render and
        the poll body come out of the SAME builder over the SAME rows."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            response = client.get(
                reverse("settings-assistant-panel")
                + f"?assistant=1&next={reverse(A_SETTINGS_PAGE)}")
        assert response.status_code == 200
        assert "a0" in response.content.decode()

    def test_it_writes_nothing(self, client):
        from agents.models import Conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            sign_in(client, admin)
            before = Conversation.objects.count()
            client.get(reverse("settings-assistant-panel"))
        assert Conversation.objects.count() == before

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_open_posture_fragment_costs_exactly_three_queries(
            self, client, django_assert_num_queries, turns):
        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=turns)
            url = reverse("settings-assistant-panel") + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(3):
                client.get(url)

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_fragment_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        MEASURED_FRAGMENT_ENTERPRISE = None  # <-- fill in from the first run
        assert MEASURED_FRAGMENT_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse("settings-assistant-panel") + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(MEASURED_FRAGMENT_ENTERPRISE):
                client.get(url)


class TestThePanelOnThePage:
    def test_it_renders_on_every_settings_page_for_an_administrator(self, client, settings):
        """ALL ELEVEN, driven from `SETTINGS_GROUPS` itself rather than a
        hand-typed list, so a twelfth page added later is swept the day it
        is added. PINS ITS OWN FEATURE STATE (Global Constraint 13): one
        entry is gated on a feature token, and a test that inherited
        `FARABUNKER_FEATURES` from the environment would silently skip it
        in one of the two supported suite states."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    assert 'id="assistant-panel"' in body, entry.url_name
                    swept += 1
        assert swept == 11, swept

    def test_it_does_not_render_for_a_member_on_the_public_settings_page(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("setup-index")).content.decode()
        assert 'id="assistant-panel"' not in body

    def test_it_introduces_no_second_page_name(self, client):
        """`foundation/tests/test_page_names.py`'s one-name-per-page rule
        sweeps every settings page; the panel must add no `<h1>`."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert body.count("<h1") == 1

    def test_exactly_one_composer_renders_per_settings_page(self, client):
        """`id="composer-text"`'s accessible-name invariant
        (`chat/_composer.html:87-97`) holds unchanged only if this does."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert body.count('id="composer-text"') == 1

    def test_the_panel_offers_no_attach_door_and_no_model_picker(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert "attach-block" not in body
        assert 'name="connection"' not in body
        assert 'name="agent"' not in body

    def test_an_uninstalled_box_renders_the_offer_instead_of_the_composer(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert 'id="composer-text"' not in body
        assert f'value="{SLUG}"' in body
        assert reverse("chat-default-install") in body

    def test_no_client_side_storage_appears_anywhere_in_the_panel(self, client):
        """SPEC §6.7. PERSISTENCE IS SERVER-SIDE ONLY -- `Conversation`
        and `Turn` rows. The idiom is
        `tools/vision/tests/test_views_create.py:1000-1004`'s."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        for banned in ("localStorage", "sessionStorage", "indexedDB"):
            assert banned not in body, banned


def _finish_pending():
    """Settle every queued or running turn, so the NEXT ask in a test is
    not refused for a turn already in flight.

    `start_turn` refuses with a 409 while any ASSISTANT turn on the
    conversation is `QUEUED` or `RUNNING` (`agents/chat/service.py:249-256`)
    -- "one turn at a time per conversation", because `Turn.next_index`
    is a read-then-write whose safety rests on exactly that. The queue is
    patched at the `agents.chat.service` seam in these tests, so no
    worker exists to move these rows and nothing else will. This is not a
    shortcut around the rule; it is what the worker would have done.
    """
    Turn.objects.filter(
        role=Turn.Role.ASSISTANT,
        state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).update(state=Turn.State.DONE, text="an answer")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Expected: FAIL — `NoReverseMatch` for all three names.

- [ ] **Step 3: Write `agents/chat/views/assistant.py`**

```python
"""The settings assistant panel's three views (spec §6.6, §8.1).

ALL THREE ARE CLASS S (`identity/routes.py::ROUTE_RULES`), so the
middleware has already refused a non-admin before any of them runs -- the
same reasoning `chat-settings` records for itself: "a page whose entire
body is an administrator-only form has nothing to show anybody else."
Each of them still resolves the acting principal, because the turn runs
AS THE ASKING ADMINISTRATOR and `start_turn`'s `actor` is required and
keyword-only precisely so nobody enqueues a turn attributed to nobody.

THEY LIVE IN `agents/chat` because they read `agents/` rows, and they
read them through `agents/visibility.py`, never through a manager. They
are MOUNTED by `config/urls.py` at `/settings/assistant/`, beside
`/settings/` itself: the composition root is the one module that already
imports every column, so nothing crosses a boundary to put a settings-
shaped URL in front of an agents-column view.

EVERYTHING WORKS WITH NO SCRIPT AT ALL: the form is a plain POST, the
answer is a redirect carrying `?assistant=1`, the panel comes back open
with the queued turn showing "Thinking...", and a reload shows the
answer. That is the same contract `chat/conversation.html:6-9` makes.

NO `?pending=<turn_id>`, deliberately (spec §6.6 step 5, decision 9). On
the chat page that parameter is the hand-off telling a JS-enabled reload
which turn the script-free POST just queued; this panel re-renders its
WHOLE recent transcript on every render, so the queued turn is already in
front of the operator and the pending marker is derived from the rows.
Carrying a turn id nothing reads would be cargo.
"""
from __future__ import annotations

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from agents.chat.context_processors import ASSISTANT_SLUG, OPEN_PARAM, panel_context
from agents.chat.service import start_turn, validated_next_url
from agents.visibility import (
    create_conversation, set_conversation_archived, visible_agents, visible_conversations,
)
from identity.request import principal_for_request, settings_row_for

_FRAGMENT = "chat/_assistant_panel.html"

_NOT_INSTALLED = (
    "The settings assistant is not installed on this box yet. Open any settings page and "
    "use the panel's install button to add it."
)


def _is_xhr(request) -> bool:
    """True for the panel's own `fetch()` calls.

    COPIED, NOT IMPORTED, from `agents/chat/views/turns.py:44-50`, which
    itself copied it from `tools/vision/views.py` and says why in those
    words: it is a five-line reading of one header, and this package's
    import direction is one-way -- `views/assistant.py` importing
    `views/turns.py` would be a new edge for five lines.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _here(request) -> str:
    """THE SETTINGS PAGE THIS PANEL BELONGS TO -- which is never this
    request's own URL, on any of these three routes.

    The composer POSTs it as `next`; the poll route receives it as
    `?next=`. The POST half goes through `validated_next_url` -- the ONE
    `next` reader this codebase has (`agents/chat/service.py:662`) --
    because a caller-supplied value is otherwise an open redirect off
    this box; the GET half applies the identical
    `url_has_allowed_host_and_scheme` guard rather than trusting a query
    string that guard has never seen.

    `settings-index` when there is no usable value: that route reads the
    posture row and redirects to whichever settings page this viewer may
    actually open, so it is a fallback that lands somewhere real in
    every posture.
    """
    posted = validated_next_url(request)
    if posted:
        return posted
    queried = request.GET.get("next", "")
    if queried and url_has_allowed_host_and_scheme(
            queried, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return queried
    return reverse("settings-index")


def _back(request) -> str:
    """Where a non-XHR POST redirects to, always carrying the panel's
    open state -- so the operator lands on the page they asked from with
    the panel still showing their question."""
    target = _here(request)
    joiner = "&" if "?" in target else "?"
    return f"{target}{joiner}{OPEN_PARAM}=1"


def _fragment(request, *, error: str = ""):
    """The panel, rendered from the SAME builder the inline panel uses.

    `is_open=True` UNCONDITIONALLY, because a fragment is only ever
    rendered BECAUSE the panel is open -- the XHR ask and the poll are
    both things that happen inside an open panel, and neither request
    carries the `?assistant=1` the inline render reads. Without this the
    fragment comes back collapsed, with no transcript and no pending
    marker, and the poller stops on its first tick.
    """
    return render(request, _FRAGMENT,
                  panel_context(request, error=error, is_open=True,
                                next_url=_here(request)))


@require_POST
def assistant_ask(request):
    """POST /settings/assistant/ask/ -- one question to the settings
    assistant.

    Resolve or CREATE this principal's own conversation. On an
    accounts-on box that is one conversation per administrator, owned, and
    invisible to every other principal by the ordinary gate.

    ON AN OPEN BOX IT IS ONE CONVERSATION FOR THE WHOLE BOX,
    DELIBERATELY, AND THAT IS WORTH KNOWING: `visible_conversations`
    short-circuits on `sees_all_content` and returns `.all()`, and every
    viewer there is the same `OPEN_PRINCIPAL` -- so a household box has
    one assistant thread that everyone at the keyboard continues, reads
    the history of, and can "start over" for all of them. That is the
    same thing being true of `is_admin` that the settings sidebar already
    is, followed through to the transcript.

    `create_conversation(principal, agent)` stamps `owner_fields` and NO
    `workstream`. That NULL is load-bearing: it is what keeps an
    assistant conversation off every workstream card, and
    `agents/chat/tests/test_settings_surface.py` pins it from the other
    side.
    """
    # THE ROW FIRST, THEN THE PRINCIPAL OFF IT.
    # `principal_for_request` reads the singleton ITSELF through
    # `accounts_on()` when it is not handed one (`identity/request.py:91`),
    # and the gate middleware has already stashed the row -- so calling
    # them in this order, with the keyword, is what keeps this view to the
    # one-read-per-request rule the whole panel is budgeted against.
    # `foundation/setup/views.py:185-187` is the in-tree precedent.
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)

    # NO `enabled=True` HERE: `visible_agents` already filters it, and
    # the term "goes with the manager call it came from -- no second
    # filter needed here" (`agents/runtime/delegate.py:99-102`, in those
    # words).
    agent = visible_agents(principal).filter(slug__iexact=ASSISTANT_SLUG).first()
    if agent is None:
        return HttpResponseBadRequest(_NOT_INSTALLED)

    conversation = visible_conversations(principal, settings_row=settings_row).filter(
        agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True).first()
    if conversation is None:
        conversation = create_conversation(principal, agent)

    # NO `connection`, NO `files`, NO `placement`: this surface has no
    # picker and no attach door, so passing any of them would be the view
    # inventing a capability its own template does not offer.
    start = start_turn(conversation, request.POST.get("text", ""), actor=principal)

    if not start.ok:
        # A refusal from `start_turn` -- blank text, a turn already in
        # flight, the queue down -- lands in the panel's OWN error slot.
        # NEVER a bare fragment for a non-XHR caller: the rule
        # `agents/chat/views/turns.py:64-67` states, applied here.
        if _is_xhr(request):
            return _fragment(request, error=start.error)
        return redirect(_back(request))

    if _is_xhr(request):
        return _fragment(request)
    return redirect(_back(request))


@require_POST
def assistant_reset(request):
    """POST /settings/assistant/reset/ -- "Start over".

    NOTHING IS DELETED: the old thread is ARCHIVED, and the chat-surface
    exclusion keeps it out of the archive listing too. The next ask
    creates a fresh one.
    """
    # The row first, then the principal off it -- `assistant_ask`'s own
    # comment says why, and this view pays the same avoidable query
    # without it.
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    conversation = visible_conversations(principal, settings_row=settings_row).filter(
        agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True).first()
    if conversation is not None:
        set_conversation_archived(principal, conversation, archived=True)
    return redirect(_back(request))


@require_GET
def assistant_panel(request):
    """GET /settings/assistant/panel/ -- the same fragment the inline
    panel renders, from the same template and the same context builder.

    THE `chat/_turn_block.html` PRECEDENT, where "this page's inline
    render and `turn_status`'s poll body come out of the SAME loop over
    the SAME fragment".

    It takes `?next=` -- read and host-checked by `_here` -- so the
    fragment it returns carries the settings page the panel belongs to,
    rather than this route's own URL. IT WRITES NOTHING.
    """
    return _fragment(request)
```

Note the ask view's non-XHR **refusal** path redirects rather than re-rendering: `_back(request)`
returns to the settings page with `?assistant=1`, and the panel's error slot is fed from the same
place on that render. That satisfies "never a bare fragment for a non-XHR caller" without this view
having to know how to re-render eleven different settings pages — which it cannot, and which is why
the redirect is the honest shape here rather than `turn_create`'s own whole-page re-render.

- [ ] **Step 4: Write `agents/chat/assistant_urls.py` and export the views**

```python
"""The settings assistant's three routes, mounted at
`/settings/assistant/` by `config/urls.py`.

ITS OWN URLconf rather than three more entries in `agents/chat/urls.py`,
because these are not `/chat/` routes: they are a settings-area surface
whose views happen to read `agents/` rows. Mounting them beside
`/settings/` is what makes the URL an operator sees match the page they
are on.
"""
from django.urls import path

from agents.chat.views import assistant_ask, assistant_panel, assistant_reset

urlpatterns = [
    path("ask/", assistant_ask, name="settings-assistant-ask"),
    path("reset/", assistant_reset, name="settings-assistant-reset"),
    path("panel/", assistant_panel, name="settings-assistant-panel"),
]
```

and in `agents/chat/views/__init__.py`, add to the existing re-export block:

```python
from agents.chat.views.assistant import assistant_ask, assistant_panel, assistant_reset
```

keeping whatever `__all__`-or-equivalent convention that file already uses.

- [ ] **Step 5: Mount it, classify it, and drive it**

`config/urls.py`, inside `urlpatterns`, immediately after `path("settings/", …)` (`:34`) and
**before** the closing `]` at `:35`:

```python
    # The settings assistant's three routes (spec §8.1). Mounted HERE,
    # beside `/settings/` itself, rather than under `/chat/`: they are a
    # settings-area surface whose views happen to read `agents/` rows,
    # and `config/` is the composition root that already imports every
    # column, so nothing crosses a boundary to put them in front.
    path("settings/assistant/", include("agents.chat.assistant_urls")),
```

`identity/routes.py::ROUTE_RULES`, in the `/settings/` region:

```python
    # --- /settings/assistant/ (settings assistant) ---------------------
    # S, for `chat-settings`'s own recorded reason: a surface whose whole
    # body is an administrator-only panel has nothing to show anybody
    # else, so it refuses at the gate rather than serving an empty shell.
    # `is_admin` is True for everybody on an open box, which is what makes
    # a household box's panel work for whoever is at the keyboard.
    "settings-assistant-ask": "S",
    "settings-assistant-reset": "S",
    "settings-assistant-panel": "S",
```

`identity/tests/test_route_matrix.py`, in `_DRIVERS` (`:92`). Every row-addressed route in that
table points at a row owned by `world.other`; **these three address no row**, so they follow
`chat-index`'s own "own list, no row addressed" convention:

```python
    # --- /settings/assistant/ -----------------------------------------
    # NO ROW IS ADDRESSED by any of the three, so no `world.other` row is
    # needed: the ask resolves the CALLER's own conversation (creating one
    # if there is none), reset archives the caller's own, and the panel
    # reads the caller's own. `text` is the field the ask view actually
    # reads -- a body naming a field the view ignores would exercise its
    # "you sent nothing" branch and the matrix would still pass.
    "settings-assistant-ask": lambda w: (
        "post", reverse("settings-assistant-ask"), {"text": "hello"}),
    "settings-assistant-reset": lambda w: (
        "post", reverse("settings-assistant-reset"), {}),
    "settings-assistant-panel": lambda w: (
        "get", reverse("settings-assistant-panel"), {}),
```

- [ ] **Step 6: Give `panel_context` the four URL keys it was waiting for**

`agents/chat/context_processors.py`. Replace the comment Task 8 left in place of them with the
four lines themselves, in the same dict literal:

```python
        # THE FOUR URL KEYS. Reversed in PYTHON, never `{% url %}` in the
        # fragment: this same context is what the panel ROUTE renders
        # from, and a template that reversed its own action would be a
        # second place the panel's URLs are decided.
        "ask_url": reverse("settings-assistant-ask"),
        "reset_url": reverse("settings-assistant-reset"),
        "panel_url": reverse("settings-assistant-panel"),
        # THE INSTALL OFFER POSTS TO THE ONE INSTALL ROUTE (spec §6.5,
        # decision 12), extended in Task 9 with a validated `next`. Three
        # lines on an existing route beat a fourth route.
        "install_url": reverse("chat-default-install"),
```

- [ ] **Step 7: Write the panel fragment**

Create `agents/chat/templates/chat/_assistant_panel.html`. **No `<style>` block** — a `chat/`
fragment never carries one (`test_css_ownership.py`'s own rule), and everything it needs is either
`_shell.html`'s (the composer, Task 7) or `_settings.html`'s own block. **No `<h1>`** — the
one-name-per-page rule sweeps every settings page.

```django
{% comment %}
THE SETTINGS ASSISTANT PANEL (spec §6.1, §6.4).

RENDERED ON EVERY SETTINGS PAGE, below the content column, from
`foundation/templates/_settings.html` -- and from
`settings-assistant-panel`, which returns THIS SAME fragment from THIS
SAME context builder, so the inline render and the poll body can never
disagree (`chat/conversation.html:12-14`'s own precedent).

COLLAPSED BY DEFAULT. `<details open>` only when `?assistant=1` is on the
URL, because a `<details>` renders its children into the DOM even when
closed -- so not rendering them is the only honest way to not pay for
them, and the context builder does not read a single conversation row
while it is shut.

ITS OWN COMPACT TRANSCRIPT, NOT `chat/_turn_block.html`, and that is a
decision rather than an oversight: the chat turn block renders tool cards
with arguments, thumbnails and citations, artifact image and file strips,
attachment rows and five turn states, and this surface needs none of it.
Reusing that markup would drag roughly twenty `.turn*`/`.tool*` selectors
out of `chat/base.html` and into the global shell so they could render on
a page that then hides most of them. What IS reused is the thing that
matters: `agents.chat.rendering.render_answer`, called in the context
builder, so a model's `**bold**`, lists and code spans render identically
here and in `/chat/` -- escaped first, and emitting no `<a>`.

THE "JUMP TO" STRIP IS THE PLATFORM'S, NOT THE MODEL'S. Every url in it
was built in Python from a `route`/`anchor` pair re-validated against the
help-card table; a url the model typed into its answer is inert text,
because `render_answer` creates no link from bare text, from a URL or
otherwise.

NO `<style>` HERE: a `chat/` fragment never carries one. Its composer
rules live in `_shell.html` (promoted there when this panel started
including that fragment from outside `chat/`), and its own few rules live
in `_settings.html`'s `extra_style`.
{% endcomment %}
<details class="assistant-panel" id="assistant-panel"{% if assistant.open %} open{% endif %}>
  <summary>Settings assistant</summary>
  <div id="assistant-body" {% if assistant.pending %}data-assistant-pending="1"{% endif %}
       data-assistant-panel-url="{{ assistant.panel_url }}"
       data-assistant-next="{{ assistant.next }}">

    {% if assistant.error %}
    <p class="msg error" id="assistant-error">{{ assistant.error }}</p>
    {% endif %}

    {% if not assistant.installed %}
    {% comment %}
    THE OFFER, not an auto-install: a row appears when somebody installs
    it (`agents/defaults.py`'s ruling 2), never because a deploy ran.
    It posts to the ONE install route, extended with a validated `next`
    so the operator lands back on the page they were reading.
    {% endcomment %}
    <p>A guide to this box's settings, on every settings page. It explains and links; it
      never changes a setting.</p>
    <form method="post" action="{{ assistant.install_url }}">
      {% csrf_token %}
      <input type="hidden" name="kind" value="agent">
      <input type="hidden" name="slug" value="{{ assistant.slug }}">
      <input type="hidden" name="next" value="{{ assistant.next }}">
      <button type="submit">Add the settings assistant</button>
    </form>

    {% else %}

    {% if assistant.open %}
    <div class="assistant-transcript">
      {% for card in assistant.cards %}
        {% if card.role == "user" %}
        <p class="assistant-turn assistant-you"><strong>You:</strong> {{ card.text }}</p>
        {% elif card.role == "assistant" %}
          {% if card.pending %}
          <p class="assistant-turn assistant-pending">Thinking…</p>
          {% elif card.error %}
          <p class="assistant-turn assistant-failed">{{ card.error }}</p>
          {% else %}
          <div class="assistant-turn assistant-answer">{{ card.text_html }}</div>
          {% endif %}
        {% endif %}
      {% empty %}
      <p class="muted">Ask about any setting on this box — what it does, and where it lives.</p>
      {% endfor %}
    </div>

    {% if assistant.links %}
    <p class="assistant-jump"><span class="muted">Jump to:</span>
      {% for link in assistant.links %}<a href="{{ link.url }}">{{ link.label }}</a>{% if not forloop.last %} · {% endif %}{% endfor %}
    </p>
    {% endif %}
    {% endif %}

    {% comment %}
    THE ONE COMPOSER, in `turn` mode: a `required` textarea and no agent
    picker. `may_attach_files` is absent from this context, so the attach
    door and the drag-drop fragment do not render at all; the model
    picker is off because this surface has no model choice to offer.
    `composer_next` is the one parameter this phase added to that
    fragment, and it is what lets the ask view return the operator to the
    page they asked from.
    {% endcomment %}
    {% include "chat/_composer.html" with composer_mode="turn" composer_action=assistant.ask_url composer_form_id="assistant-form" composer_next=assistant.next composer_placeholder="Ask about these settings" composer_button="Ask" composer_show_model_picker=False composer_workstream=None %}

    <form method="post" action="{{ assistant.reset_url }}" class="assistant-reset">
      {% csrf_token %}
      <input type="hidden" name="next" value="{{ assistant.next }}">
      <button type="submit">Start over</button>
    </form>

    {% endif %}
  </div>
</details>
```

- [ ] **Step 8: Include it, gated, in `_settings.html`**

`foundation/templates/_settings.html`. Replace the `.settings-main` line (`:146`) with:

```django
  <div class="settings-main">
    {% block settings_content %}{% endblock %}
    {% comment %}
    THE SETTINGS ASSISTANT PANEL (spec §6.1, §8.2). Below the content
    column, on every settings page, inside `.settings-main` so the
    `:target` highlight's own scope is unchanged.

    `{% templatetag openblock %} if identity_is_admin {% templatetag
    closeblock %}` mirrors `chat/settings.html`'s own gate and the
    sidebar's just above. IT IS THE SECOND OF TWO GATES, NOT THE ONLY
    ONE: `agents.chat.context_processors.settings_assistant` refuses the
    same principal at the DATA seam, before a single row is read, because
    a gate that lives one layer above the work it guards decides what
    RENDERS and not what was BUILT. `assistant` is absent from the
    context entirely for anybody this `{% templatetag openblock %} if
    {% templatetag closeblock %}` would have hidden it from.

    ON AN OPEN BOX EVERY VIEWER IS AN ADMINISTRATOR, so a household box
    shows this to whoever is at the keyboard -- consistent with the whole
    settings area, where the same viewer already gets Models, Library and
    Chat.
    {% endcomment %}
    {% if identity_is_admin and assistant %}
    {% include "chat/_assistant_panel.html" %}
    {% endif %}
  </div>
```

Add the panel's own few rules to `_settings.html`'s `extra_style` block (its own home: the deepest
template that is an ancestor of every template that uses them) — the transcript spacing, the
pending/failed line colours off `--muted`/`--danger`, and the "Jump to" strip. Keep them small;
everything the composer needs is already in `_shell.html`.

- [ ] **Step 9: Run the tests, and measure the third accounts-on number**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Fill `MEASURED_FRAGMENT_ENTERPRISE` in exactly as Task 8's two were filled: read the actual from a
failing equality, write it in **as an equality**, and confirm both parametrized runs (0 turns and
12) report the **same** number.

Expected, after that: PASS.

- [ ] **Step 10: Run the route matrix and the page-name sweep**

```bash
.venv/bin/pytest identity/tests/test_route_matrix.py foundation/tests/test_page_names.py foundation/tests/test_shell.py -q
```

Expected: PASS. `test_every_route_has_a_driver` is the one that fails if Step 5's `_DRIVERS` block
was skipped; `test_page_names.py` is the one that fails if the panel added an `<h1>`.

- [ ] **Step 11: Commit**

```bash
git add agents/chat/views/assistant.py agents/chat/assistant_urls.py agents/chat/context_processors.py agents/chat/templates/chat/_assistant_panel.html agents/chat/views/__init__.py config/urls.py identity/routes.py identity/tests/test_route_matrix.py foundation/templates/_settings.html agents/chat/tests/test_assistant_panel.py
git commit -m "feat(settings): the assistant panel -- three administrator-only routes, a compact transcript, and links the platform builds"
```

---

### Task 11: the one sanctioned script

**Files:**
- Modify: `agents/chat/templates/chat/_assistant_panel.html` (one `<script>`, ~40 lines)
- Modify: `agents/chat/tests/test_assistant_panel.py` (append `TestTheOneScript`)

**Interfaces:**
- Consumes: `assistant.poll_interval_ms`, `assistant.max_transport_retries`,
  `assistant.max_poll_duration_ms` (Task 8), and the `data-assistant-pending` /
  `data-assistant-panel-url` markers Task 10 rendered.
- Produces: nothing importable.

**It is progressive enhancement over a page that already works.** Everything works with **no script
at all** (Task 10, and drill 5 proves it). **The submit interception earns its place**: a settings
page can have a half-filled form on it, and a plain POST navigates away and discards it. With the
script, asking a question never navigates. Without it, it does — and that is the honest JS-off cost
(**owner flag 4**).

**It is not a copy of the conversation poller** (`chat/conversation.html:391-870`), which swaps
individual turn cards by `data-poll-block` id, de-duplicates tool cards, reads two different refusal
body shapes and handles attachment pending state. This one replaces **one container** with
server-rendered HTML and stops when the server stops saying "pending".

- [ ] **Step 1: Write the failing tests**

Append to `agents/chat/tests/test_assistant_panel.py`:

```python
class TestTheOneScript:
    def _panel(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            return client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()

    def test_the_three_constants_come_from_python_and_are_never_retyped(self, client):
        """SPEC §6.7: declared ONCE in `agents/chat/service.py:93-95` and
        handed to the template -- the rule
        `chat/conversation.html:396-407` states, followed here.

        ASSERTED AS THE VALUES, so a template that hard-coded 2000 would
        pass a name check and fail this."""
        from agents.chat.service import (
            MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
        )

        body = self._panel(client)
        script = body[body.index("<script"):]
        assert f"= {POLL_INTERVAL_MS};" in script
        assert f"= {MAX_TRANSPORT_RETRIES};" in script
        assert f"= {MAX_POLL_DURATION_MS};" in script

    def test_the_panel_carries_exactly_one_script_tag(self, client):
        """The budget is ONE. `chat/_enter_to_send.html`, included by the
        composer, is an EXISTING sanctioned script and is counted here so
        the assertion is honest about what is on the page."""
        body = self._panel(client)
        assert body.count("<script") == 2   # this panel's one, plus Enter-to-send's

    def test_it_stores_nothing_on_the_client(self, client):
        body = self._panel(client)
        script = body[body.index("<script"):]
        for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert banned not in script, banned

    def test_it_polls_the_panel_route_and_never_a_url_it_builds_itself(self, client):
        """Reversed in Python and read off a data attribute -- the same
        reason `chat/conversation.html:29-33` gives for
        `data-pending-status-url`: a script that built a URL client-side
        would be a second URLconf."""
        body = self._panel(client)
        script = body[body.index("<script"):]
        assert "data-assistant-panel-url" in body
        assert reverse("settings-assistant-panel") not in script

    def test_it_sends_the_xhr_header_the_view_reads(self, client):
        body = self._panel(client)
        assert "X-Requested-With" in body[body.index("<script"):]

    def test_the_pending_marker_is_server_side_and_absent_when_nothing_runs(self, client):
        body = self._panel(client)
        assert "data-assistant-pending" not in body

    def test_the_pending_marker_appears_when_a_turn_is_queued(self, client, monkeypatch):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "hi", "next": here})
            body = client.get(f"{here}?assistant=1").content.decode()
        assert 'data-assistant-pending="1"' in body
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py::TestTheOneScript -q
```

Expected: FAIL — there is one `<script>` on the page (Enter-to-send's) and none of the constants.

- [ ] **Step 3: Write the script, at the end of the panel fragment**

```django
{% comment %}
PROGRESSIVE ENHANCEMENT ONLY, and the budget for this whole feature is
this one script (spec §6.7). Everything above already works with no
script at all: the form is a plain POST, the answer is a redirect
carrying `?assistant=1`, the panel comes back open with the queued turn
showing "Thinking…", and a reload shows the answer.

THE SUBMIT INTERCEPTION IS WHAT EARNS THIS SCRIPT ITS PLACE. A settings
page can have a half-filled form on it, and a plain POST navigates away
and discards it. With this, asking a question never navigates. Without
it, it does -- and that is the honest JS-off cost.

IT IS NOT A COPY OF THE CONVERSATION POLLER
(`chat/conversation.html`), which swaps individual turn cards by
`data-poll-block` id, de-duplicates tool cards, reads two different
refusal body shapes and handles attachment pending state. This replaces
ONE container with server-rendered HTML and stops when the server stops
saying "pending".

THE THREE CONSTANTS ARE DECLARED ONCE, IN PYTHON
(`agents/chat/service.py`), and handed here -- never a second copy typed
into this template. The panel URL is reversed in Python too and read off
a data attribute, for the same reason: a script that built a URL
client-side would be a second URLconf.

NO CLIENT-SIDE STORAGE OF ANY KIND. Persistence is `Conversation` and
`Turn` rows; this holds nothing across a reload, on purpose.
{% endcomment %}
<script>
(function () {
  var POLL_INTERVAL_MS = {{ assistant.poll_interval_ms }};
  var MAX_TRANSPORT_RETRIES = {{ assistant.max_transport_retries }};
  var MAX_POLL_DURATION_MS = {{ assistant.max_poll_duration_ms }};

  var panel = document.getElementById("assistant-panel");
  if (!panel) { return; }

  var startedAt = Date.now();
  var failures = 0;
  var timer = null;

  function body() { return document.getElementById("assistant-body"); }

  function swap(html) {
    var holder = document.createElement("div");
    holder.innerHTML = html;
    var fresh = holder.querySelector("#assistant-body");
    var current = body();
    if (fresh && current) { current.replaceWith(fresh); bind(); }
  }

  function stop() { if (timer) { clearTimeout(timer); timer = null; } }

  function schedule() {
    var current = body();
    if (!current || !current.hasAttribute("data-assistant-pending")) { stop(); return; }
    if (Date.now() - startedAt > MAX_POLL_DURATION_MS) { stop(); return; }
    stop();
    timer = setTimeout(poll, POLL_INTERVAL_MS);
  }

  function poll() {
    var current = body();
    if (!current) { stop(); return; }
    var url = current.getAttribute("data-assistant-panel-url")
      + "?assistant=1&next=" + encodeURIComponent(current.getAttribute("data-assistant-next"));
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) { return response.text(); })
      .then(function (html) { failures = 0; swap(html); schedule(); })
      .catch(function () {
        failures += 1;
        if (failures >= MAX_TRANSPORT_RETRIES) { stop(); return; }
        schedule();
      });
  }

  function bind() {
    var form = document.getElementById("assistant-form");
    if (!form || form.dataset.assistantBound) { return; }
    form.dataset.assistantBound = "1";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (response) { return response.text(); })
        .then(function (html) { startedAt = Date.now(); failures = 0; swap(html); schedule(); })
        .catch(function () { form.submit(); });
    });
    schedule();
  }

  bind();
})();
</script>
```

The `.catch(function () { form.submit(); })` on the submit handler is deliberate: a transport
failure on the very first POST falls back to the **plain form submission** the page already
supports, so a flaky network degrades to the JS-off behaviour rather than to a silent no-op.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/pytest agents/chat/tests/test_assistant_panel.py -q
```

Expected: PASS.

- [ ] **Step 5: Drive it in a browser, both ways**

Bring up the preview stack, sign in as an administrator, open `/chat/settings/?assistant=1`.

1. **Script on.** Type a question and press Ask. Expected: the page does **not** navigate, the
   panel shows "Thinking…", and the answer replaces it a few seconds later with no reload. Then
   half-fill the "Tell the model the date and time" form above (toggle it without saving) and ask
   another question: **the toggle keeps its unsaved state**, which is the whole reason this script
   exists.
2. **Script off** (browser devtools → disable JavaScript). Ask a question. Expected: the page
   navigates back to itself with `?assistant=1`, the panel is open, the queued turn shows
   "Thinking…", and a reload shows the answer. This is drill 5 and it must pass with the script
   disabled.

- [ ] **Step 6: Commit**

```bash
git add agents/chat/templates/chat/_assistant_panel.html agents/chat/tests/test_assistant_panel.py
git commit -m "feat(settings): one sanctioned script -- ask without navigating, and stop polling when the server stops saying pending"
```

---

### Task 12: documentation — the recipe that binds future authors, and the six stale citations

**Files:**
- Modify: `docs/EXTENDING.md` — the `<h1>`, a new "Adding a settings page" section, and the six
  stale line citations §18 lists
- Modify: `README.md:121` (the `EXTENDING.md` one-liner, in the same commit as the `<h1>`)
- Modify: `agents/README.md`, `agents/chat/README.md`, `foundation/README.md`, `docs/DEV.md`

**Interfaces:**
- Consumes: everything Tasks 1–11 built.
- Produces: the recipe a future author is bound by, and the corrected citations that make the
  tool recipe navigable.

**This is the owner's sustainability requirement (b)**, and it is not decoration: two of the card
rules have **no test at all** behind them, and this recipe is their only guard.

- [ ] **Step 1: Generalize the `docs/EXTENDING.md` heading, and the README line that quotes it**

`docs/EXTENDING.md:1` becomes:

```markdown
# Extending farabunker
```

and its first section heading becomes the recipe it used to be titled after:

```markdown
## Adding a tool in three steps
```

with the existing opening paragraphs moved under it unchanged. **The file already carries two
recipes beyond the tool one** — "Registering a workstream panel" (`:190`) and "Joining the taint
stamp" (`:231`) — so the title was already narrower than the document before this phase added a
third.

`README.md:121` becomes:

```markdown
- [docs/EXTENDING.md](docs/EXTENDING.md) — add a tool, a workstream panel, or a settings page
```

- [ ] **Step 2: DERIVE the citations, then fix them — do not transcribe a number**

**Every line number in this step must be read out of the tree at execution time, not copied from
here or from the spec.** Two reasons, both of which have already bitten:

1. **Task 4 Step 5 moved four of these targets.** It inserted two lines into `TOOL_MODULES` (a
   comment plus an entry) and five into `_REGISTRATION_MODULES` (a four-line comment plus an
   entry) — **above** every test function in the file. So the four `def test_…` lines the spec's
   §18 table names have all shifted by roughly seven, and both guard-list spans have grown. A
   transcription would write four freshly-wrong numbers into the very document whose stale numbers
   this pass exists to fix.
2. **One of the spec's own "should cite" values is wrong.** Spec §18 item 4 says
   `docs/EXTENDING.md:134` should cite `` `:217-231` `` for `_REGISTRATION_MODULES`. It should not:
   `:228` is that tuple's closing `)`, `:229` is blank and `:230` onwards is `RUNTIME_MODULES`'
   own comment. **`EXTENDING.md:134` says `:217-228` today and that is CORRECT** — before Task 4.
   Do not apply the spec's number. Re-derive the span the same way as everything else.

**Derive them:**

```bash
grep -n "^TOOL_MODULES\|^_REGISTRATION_MODULES\|^RUNTIME_MODULES\|^)" foundation/ops/tests/test_column_boundaries.py | head -20
```
```bash
grep -n "def test_no_tool_runner_blocks_on_a_queue_job\|def test_every_registered_runner_lives_in_a_swept_module\|def test_no_tool_module_imports_its_service_layer_at_module_scope\|def test_the_registration_modules_list_is_not_silently_empty" foundation/ops/tests/test_column_boundaries.py
```

The first `)` after `^TOOL_MODULES` closes that tuple; likewise for `_REGISTRATION_MODULES`.

**Then fix each citation SITE.** The table below names which `EXTENDING.md` line carries which
citation and what it is a citation *of* — the numbers come from the grep, and the
`EXTENDING.md` line numbers are themselves approximate once Step 1 and Step 3 edit that file, so
locate each by its surrounding text rather than by line:

| Where in `EXTENDING.md` | What it cites | How to fix |
|---|---|---|
| the "permanently" sentence in "Which exception to raise" (`~:104`) | `test_no_tool_module_imports_its_service_layer_at_module_scope`, as a bare `:485` | the grep's number for that `def` |
| the `TOOL_MODULES` bullet (`~:131`) | the `TOOL_MODULES` span, `:185-189` | the grep's span — **Task 4 grew this tuple, so it is no longer `:185-189`** |
| the same bullet's parenthetical (`~:133`) | `test_no_tool_runner_blocks_on_a_queue_job`, as `:346` | the grep's number |
| the `_REGISTRATION_MODULES` bullet (`~:134`) | that tuple's span, `:217-228` | the grep's span — **correct today, wrong after Task 4; NOT the spec's `:217-231`** |
| the same bullet's "Swept for" parenthetical (`~:138`) | the same module-scope test, as `:486` | the grep's number. **This one is not in spec §18's table at all** — it is a seventh stale citation the spec missed, and it names the same test as `~:104` |
| the same bullet's closing sentence (`~:139`) | `test_the_registration_modules_list_is_not_silently_empty`, as `:510` | the grep's number |
| "What you get for free" (`~:155`) | `test_every_registered_runner_lives_in_a_swept_module`, as `:442` | the grep's number |
| "Testing it" (`~:284`) | `test_no_tool_runner_blocks_on_a_queue_job` again, as `:346` | the same number as `~:133` |

**Verified as correct and to be left alone:** the `localStorage` pins at
`tools/vision/tests/test_views_create.py:1000-1004`.

**The lesson worth carrying, and worth writing into the recipe in Step 3:** a bare line number in
prose is stale the first time anybody edits above it. Where a sentence can name the *test function*
instead of the line, it should.

- [ ] **Step 3: Write the "Adding a settings page" recipe**

A new `## Adding a settings page` section in `docs/EXTENDING.md`, after the tool recipe. **Five
steps, each naming the file and the test that fails if it is skipped:**

```markdown
## Adding a settings page

Five steps. Four of them have a test that goes red if you skip them; the
fifth is the one that does not, which is why it is written out at length.

### 1. The page

A view and a template extending `foundation/templates/_settings.html`,
writing its body into `{% block settings_content %}` and overriding its
own `side_current_*` block. A sub-page marks its *parent's* entry — model
sets marks Models, an entitlement's own page marks Entitlements — because
the sidebar names sections, not URLs.

### 2. The route rule

An entry in `identity/routes.py::ROUTE_RULES`, plus a `_DRIVERS` entry in
`identity/tests/test_route_matrix.py`.

*Skip it →* `test_every_route_has_a_driver` fails, and an unclassified
name is treated as admin **and logged**.

### 3. The sidebar entry

An `Entry(label, url_name, gate)` in
`foundation/settings_area.py::SETTINGS_GROUPS`.

*Skip it →* the page is unreachable from the settings sidebar, and
`foundation/tests/test_shell.py`'s drift test compares a list that no
longer matches.

### 4. The help card

A `HelpCard` in `foundation/settings_help.py`, one `HelpField` per
control. **Two rules, because neither is testable:**

**(a) Describe the controls THIS page renders, and no others.** The drift
tests only ask whether the anchor exists on the page the card names, so a
card that claims a neighbouring page's control passes every one of them
and misroutes every answer about it. The worked case is the one the
settings assistant is judged by: `library_posture` is an
`IdentitySettings` column edited only on **Identity & security**, so the
`identity-settings` card carries it and the `rag-settings` card must not
— that page owns the library's numeric limits, none of which is a
posture. A `rag-settings` card that claimed it would send every "make the
library admin-only" answer to the wrong page, with a green suite.

**(b) If a control only renders in some postures, say so in its
`meaning`.** The anchor assertion renders **one** posture and cannot see
the others. The same field is the worked case:
`identity/templates/identity/settings.html` branches on
`posture == "personal"` only, so library posture is a real select on an
open **and** an enterprise box and a hidden input with an explanation on
a personal one. A card that described it flatly would be wrong in one
posture out of three and right-looking in every test run. Deep links are
unaffected either way — the `.field` wrapper that carries the anchor
renders in all three postures and only its contents branch — so this is a
rule about what the card **says**, never about whether the link lands.

*Skip the card →* `test_every_settings_entry_has_a_card_and_every_card_has_an_entry`
(`foundation/tests/test_settings_help.py`), naming your route. *Get its
contents wrong →* **no test catches you; these two rules are the only
guard.**

### 5. The anchors

A stable `id=` on each section a field names, **in every branch of the
template**. The id names the control's own section wrapper, not a label
and not an input, and it is stable vocabulary rather than a slugified
heading — renaming a heading must not silently break a link. Where a page
has no section wrapper to name (a `{% for %}` loop with no container),
use an empty `<span id="…"></span>` immediately before it: that is the
tree's own anchor precedent, at
`models/registry/templates/inference/console.html`.

**Check the branch you are NOT looking at.** A page with mutually
exclusive branches carries the id in each; and an id inside a
conditional that a fresh box does not satisfy is an id the anchor
assertion will not find. If a section only sometimes renders, anchor the
unconditional point above it instead.

*Skip it →* `test_every_anchor_a_card_cites_exists_on_the_rendered_page`,
naming the card and the anchor.

### Two more that are easy to get wrong and cheap to state

- The page's `<h1>` and `<title>` must agree with the sidebar label
  (`foundation/tests/test_page_names.py`).
- A leaf page overriding `{% block extra_style %}` writes
  `{{ block.super }}` **first**, or it silently drops `_settings.html`'s
  shared rules.
```

- [ ] **Step 4: The four column READMEs and `docs/DEV.md`**

Each is short and each is checked against what actually shipped, not against this plan:

| File | What to write |
|---|---|
| `agents/README.md` | Already updated in Task 5. Verify it still describes what shipped, and add the two tools' names beside the agent's. |
| `agents/chat/README.md` | Already updated in Task 6 with the exclusion. Add the panel: the context processor and its two guard clauses, the three class-S routes, the one composer parameter, the compact transcript fragment and why it is not `_turn_block.html`, and the one script's scope. |
| `foundation/README.md` | Already updated in Task 1. Add the `:target` highlight and its token, since `_shell.html` is this column's. |
| `docs/DEV.md` | **New**: how to install the assistant on a dev box (open any settings page, use the panel's install button — there is no `manage.py` step and no auto-install), and the three drift assertions to expect, with the sentence a developer needs: *changing a settings template's `id=` or removing a help card fails `foundation/tests/test_settings_help.py`, and that is deliberate.* |

- [ ] **Step 5: Verify every citation this task wrote**

```bash
.venv/bin/pytest foundation/ops/tests/ -q
```

then read each of the six corrected lines back with `sed -n '<n>p'` on
`foundation/ops/tests/test_column_boundaries.py` and confirm the function name at that line is the
one `docs/EXTENDING.md` now claims. **A documentation pass that introduces its own stale citation
is worse than the one it fixed.**

- [ ] **Step 6: Commit**

```bash
git add docs/EXTENDING.md README.md agents/README.md agents/chat/README.md foundation/README.md docs/DEV.md
git commit -m "docs(settings): the adding-a-settings-page recipe, the panel's own documentation, and six citations corrected"
```

---

### Task 13: ADR 0018

**Files:**
- Create: `docs/adr/0018-settings-assistant.md` — **after re-checking the number**

**Interfaces:**
- Consumes: everything.
- Produces: the phase's own record.

- [ ] **Step 1: Re-check the number before writing**

```bash
ls docs/adr/
```

At plan time `docs/adr/` ends at `0017-workstreams.md` and no document in the tree claims `0018`,
so `0018` is free. **Re-check anyway** — another phase may have landed first. If `0018` is taken,
take the next free number and say so in the file's own header rather than renumbering anybody
else's.

- [ ] **Step 2: Write it**

Follow `docs/adr/0017-workstreams.md`'s shape exactly (status, context, decision, consequences,
and whatever sections that file carries). It records:

1. **Guide-only as a structural property, not a promise.** Neither tool declares `mutates`, and the
   registry refuses a mutating grant outright — so "it cannot change a setting" is a fact about the
   type system and the registry, not an intention. A test restates it because a structural guarantee
   nobody restates is a guarantee somebody removes.
2. **The card table plus its content hash as the answer to "keep the context relevant."** There is
   **no stored repository** — the cards are the code, and code cannot be stale relative to itself.
   The hash makes that trivially true and then does three real jobs: citation, change detection, and
   being the key any future derived artifact compares against before trusting itself.
3. **The tool schema as the injection channel.** A `choice` param's `enum` plus an import-time
   description cannot go stale and needs **no change to `agents/runtime/prompt.py`**, where a
   slug-conditional paragraph would be a special case in the one function every agent goes through.
4. **Links built by the platform, because `render_answer` makes no link.** A URL a model types is
   inert text; the panel re-validates every `route`/`anchor` pair against the card table before it
   reverses anything. A whitelist by construction.
5. **The settings-only surface as a wrapper on the one gate rather than a column.** No migration, no
   `Conversation.surface`, no narrowing of `visible_agents`/`visible_conversations` themselves —
   which must keep answering *yes*, because the panel reads the same rows. And the reading this
   phase commits to: **unreachable**, not merely unlisted, which is what shuts the sharing door.
6. **The six owner flags**, named as decided rather than open: the `block.super` gap this phase
   works around rather than fixes; the library caps the assistant will not recite; the one shared
   transcript on an open box; the JS-off navigation cost; the second transcript renderer; and the
   assistant's thread not being openable in `/chat/`.

**No AI model, product or vendor name appears anywhere in it** (Global Constraint 5).

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0018-settings-assistant.md
git commit -m "docs(settings): ADR 0018 -- the settings assistant"
```

---

### Task 14: merge `origin/main`, the whole suite, and the Smoke Checklist

**Files:**
- Whatever the merge touches. No new code.

**Interfaces:**
- Consumes: Tasks 1–13.
- Produces: a branch a whole-branch review can start on.

- [ ] **Step 1: Merge `origin/main`**

```bash
git fetch origin
```
```bash
git merge origin/main
```

Task 7 already merged once, so this one is expected to be trivial or empty. If the hygiene sweep
landed further CSS-block-only changes to `_settings.html`, take **their** version of every CSS-only
hunk and keep this branch's `id=` attributes and its panel include, which are different hunks.

```bash
git status
```

Expected: clean, no conflict markers anywhere. **Never leave a checkout conflicted.**

- [ ] **Step 2: Confirm no migration was written**

```bash
.venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: exit 0, "No changes detected". Global Constraint 11: this phase adds no model and no
column, so `agents/0011` must not exist.

```bash
ls agents/migrations/
```

Expected: still ends at `0010_chat_settings.py`.

- [ ] **Step 3: The whole suite, in both feature-flag states**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
```
```bash
FARABUNKER_FEATURES='vision' .venv/bin/pytest -q
```

Expected: PASS both. These are the two supported states (`docs/DEV.md:251-261`); `FARABUNKER_FEATURES=''`
is not one and is not run.

- [ ] **Step 4: The whole suite in both orders**

```bash
.venv/bin/pytest -q scripts identity agents foundation models tools
```

Expected: PASS. The reversed order (`docs/DEV.md:271-272`) is what catches a test that only passes
because another one ran first — and this phase added a **module-level import-time hash** and a
**context processor that runs on every page**, both of which are exactly the shapes that leak
across a suite.

- [ ] **Step 5: The three posture sweeps**

```bash
FARABUNKER_TEST_POSTURE=personal .venv/bin/pytest -q
```
```bash
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q
```

Expected: PASS both. The default (open) is Step 3's. `identity.testing.seed_sweep_posture` is what
makes a test that pins its own posture still win.

- [ ] **Step 6: Walk the Smoke Checklist**

The ten drills below, in the worlds each one names, on this branch's preview stack at `:8001` —
**not on the owner's live box**. Every one of them must pass before the branch is offered for
review.

- [ ] **Step 7: Commit anything the merge produced, and stop**

```bash
git status
```

If the merge produced a commit, it is already there; if it produced resolutions, commit them:

```bash
git commit -m "chore(settings): merge origin/main before the whole-branch review"
```

**Do not push.** The orchestrator says when (Conventions 5). The next step is the whole-feature UAT
and the whole-branch review, neither of which is this plan's work.

---

## Smoke Checklist

Spec §14's ten done-when drills, verbatim in substance. **Each drill names the world it needs**,
because three of them are *about* a posture and cannot be run anywhere else. Drills **3, 4, 9 and
10 are suite runs**, not browser drills, and state their own postures. Every browser drill runs at
`:8001` in the **enterprise** world as an **administrator**, except where it says otherwise.

- [ ] **1. The flagship — enterprise, as an administrator.**
      From `/inference/`, ask **"how do I make the library admin-only?"**. The answer names
      **Identity & security** and its **library posture** control by the page's own words, and the
      "Jump to" strip links that control's anchor on `identity-settings`. Clicking it lands there,
      scrolled to the control, **highlighted**, with the panel still open.
      **An answer that says "Library" fails this criterion.** This is the drill that proves the
      whole feature, so it is the one that must not be satisfiable by a wrong answer.

- [ ] **1b. The same question on an OPEN-posture box.**
      The accepted answer is: *while this box is open every viewer is an administrator, so a lock
      has nobody to lock out; the control is on Identity & security — not in this box's sidebar —
      and it starts meaning something once the box leaves the open posture.* Sourced from
      `settings.overview`'s `posture`, not guessed.
      **Two things that would be false, and neither may appear in the answer.** The page is not
      *unreachable*: `identity-settings` is class S and `is_admin` is True for everyone on an open
      box, so it opens at its own URL — it is merely absent from the sidebar, because its `Entry`
      gate is `ACCOUNTS_ADMIN`. And the control is not *missing*: an open box renders the real
      library-posture select. What is actually true is that the setting **has no effect here**.

- [ ] **1c. The same question on a PERSONAL-posture box.**
      Here `identity-settings` *is* in the sidebar, and the library-posture control is rendered as a
      **hidden input** with the page's own explanation. The accepted answer names the posture and
      says the control is **not offered** there — never that it is missing, and never a set of steps
      to change a control this box does not show.

- [ ] **2. A box-dependent answer — OPEN posture.**
      Ask **"why don't I see Accounts in the sidebar?"**. The answer names the posture, from
      `settings.overview`.

- [ ] **3. Staleness drill (a SUITE RUN).**
      Change one card's text: `CONTENT_HASH` changes and the hash test goes red. Delete a card for a
      page that still exists: drift assertion 1 goes red **naming the route**. Restore both; green.

- [ ] **4. Anchor-rename drill (a SUITE RUN).**
      Rename one `id=` in one settings template: drift assertion 3 goes red **naming the card and
      the anchor**. Restore it; green.

- [ ] **5. JS-off drill.**
      Disable JavaScript. Ask a question: the page redirects back to the page you asked from with
      `?assistant=1`, the panel is open, the queued turn is visible, and a reload shows the answer.

- [ ] **6. Surface drill.**
      `/chat/` offers no settings assistant in its picker and no "Add" offer for it; the sidebar and
      `/chat/all/` list no assistant conversation, active or archived; a hand-built POST to
      `/chat/start/` naming its slug is refused; **opening the assistant conversation's own
      `/chat/c/<uuid>/` URL — copied out of the database — answers 404, and so does sharing it**;
      and the panel on the settings page still works throughout.

- [ ] **7. Gate drill.**
      A member on the accounts-on box sees no panel and gets 403 from all three routes.
      **Sign out entirely and open `/setup/`** — a public settings page: it renders, and the panel
      context is absent, `?assistant=1` in the URL included.

- [ ] **8. Tool audience drill.**
      Grant `settings.overview` to another agent; ask that agent, **as a member**, what this box's
      posture is. The tool answers `refused` and the reply **names no value**.

- [ ] **9. Budget drill (a SUITE RUN, not a browser drill).**
      The query-count pins pass at 0 turns and at N, **in both postures**, with the accounts-on
      numbers written in as **equalities**; the open-posture collapsed panel costs **exactly one**
      query; a non-admin and a non-settings page cost **zero**.

- [ ] **10. The whole suite is green**, including the three drift assertions, on a run that includes
      `foundation/`, `agents/`, `identity/` and `models/` — Task 14 steps 3, 4 and 5.

---

## Self-Review

Run against the spec with fresh eyes after the plan was complete, per the writing-plans skill, and
**re-run after the round-1 hygiene review's six Majors landed**. Everything found is fixed inline;
nothing below is a note for later.

**What the re-run changed.** Round 1 found that five of six Majors were places where the plan's own
code, run as written, could not reach the plan's own green step — a `ToolContext` built without its
required fields, a leak assertion its own refusal string failed, a CSS gate whose reader returns
`""` for its primary input, a context builder that answered the XHR path collapsed, and a test
module naming three helpers it neither imported nor defined. The pass below therefore gained a
sixth dimension, **executability**, which is what those five have in common and what none of the
original five dimensions was looking for.

### 1. Spec coverage

Every section of the spec, and the task that implements it. `—` means the section is context or
prose that constrains rather than builds, and the column beside it says where that constraint
lives in this plan.

| Spec § | Task |
|---|---|
| §1 Context (the settings area, the anchor precedent, the runtime, the four facts) | — Global Constraints 2, 3, 9; the Spec-vs-tree table |
| §2 Owner rulings 1–5 | Global Constraints 1, 2; Tasks 4, 5, 6 |
| §2.1 Goals | The acceptance gate; Smoke Checklist drills 1, 1b, 1c |
| §2.2 Non-goals | The Spec section's out-of-scope list |
| §3.1 `foundation/settings_help.py`, pure; the gate constants move; three accessors | **Task 1** |
| §3.2 Central table, and the one accepted duplication | **Task 1** (assertion 1 closes it) |
| §3.3 The content hash | **Task 1** |
| §4.1 Anchors, and the rule for an author | **Task 2** |
| §4.2 The `:target` highlight and `--target-wash` | **Task 3** |
| §4.3 The link the operator clicks | **Task 4** (`data["links"]`), **Task 8** (`_links`), **Task 10** (the strip) |
| §5.1 The `AgentSpec`, the doctrine-only prompt, no auto-row | **Task 5** |
| §5.2 `settings.card`; the index rides the schema | **Task 4** |
| §5.3 `settings.overview` and its `ToolRefused` audience gate | **Task 4** |
| §5.4 What the overview does not report | **Task 4** (module docstring); out of scope |
| §5.5 Guide-only pinned; registration; both guard lists | **Task 4** (registration, lists), **Task 5** (the pin) |
| §6.1 What the panel is | **Task 10** |
| §6.2 The context processor and its two clauses | **Task 8** |
| §6.3 Collapsed and open, and what each costs | **Task 8** |
| §6.4 The compact transcript, and what is not reused | **Task 8** (`_card`), **Task 10** (the fragment) |
| §6.5 The composer, the CSS price, `composer_next`, the install offer | **Task 7** (CSS), **Task 9** (both parameters), **Task 10** (include, offer) |
| §6.6 The ask flow, reset, and the panel route | **Task 10** |
| §6.7 JS-off, and the one script | **Task 10** (JS-off), **Task 11** (the script) |
| §7.1 The mechanism and the seven call sites | **Task 6** |
| §7.1.1 The row-addressed door | **Task 6** |
| §7.1.2 The call site safe only by accident | **Task 6**, and plan decision 4 |
| §7.2 What was rejected | **Task 5**'s slug-set comment, **Task 6**'s wrapper docstrings |
| §8.1 Three routes, class S, `_DRIVERS` | **Task 10** |
| §8.2 The template gate, and the open-posture truth | **Task 10**, Global Constraint 3 |
| §8.3 The acting principal | **Task 10** |
| §9 Injection posture | **Task 4** (the no-free-text test and the doctrine comment), **Task 8** (`_links`) |
| §10.1 The three drift assertions | **Task 1** (1 and 2), **Task 2** (3) |
| §10.2 The content-hash tests | **Task 1** |
| §10.3 The twenty-row test table | see below — every row claimed |
| §11 The query budget | Global Constraint 6; **Tasks 8 and 10** |
| §12 Migrations: none | Global Constraint 11; **Task 14** step 2 |
| §13.1 The recipe that binds future authors | **Task 12** |
| §13.2 The rest of the documentation | **Task 12** |
| §13.3 ADR 0018 | **Task 13** |
| §14 Phasing and done-when | The whole plan; the Smoke Checklist; **Task 14** |
| §15 Non-goals and named deferrals | The Spec section's out-of-scope list |
| §16 The twenty-four author decisions | Honoured and cited at the task that implements each |
| §17 The six owner flags | GC 3 (flag 3); **Task 3** (flag 1), **Task 4** (flag 2), **Task 10** (flag 5), **Task 11** (flag 4), **Task 13** (all six, recorded), and flag 6 in **Task 6** |
| §18 Brief-versus-tree corrections | **Task 12** (the six citations); the plan's own Spec-vs-tree table |

**§10.3's table, row by row**, because it is the spec's own test inventory and a gap there is a gap
in the feature: tools registered → **4**; guide-only → **5**; enum is the index → **4**; prompt
untouched → **4**; surface exclusion → **6**; the row-addressed door → **6**; slug set is honest →
**5**; panel gate → **10**; the gate is at the data seam → **8**; tool audience → **4**; one
conversation → **10**; JS-off → **10**; no client storage → **10** and **11**; links are
whitelisted → **10**; highlight → **3**; composer CSS placement → **7**; query budget → **8** and
**10**; route matrix → **10**; guard lists → **4**; purity → **1**. **Twenty of twenty claimed.**

**One gap found and closed.** §14's phase step 1 bundles all three drift assertions with the
anchors, and writing it that way would have made assertion 1 a test that was green the moment it
was typed. It is split across Tasks 1 and 2 on the rendered-body line, so each half is driven by a
test that really fails first; plan decision 1 records it and Task 2 is not optional.

### 2. Placeholder scan

Searched for every pattern the skill names. **One elision in the whole document**, at Task 9's
`default_install` edit — `# ... existing comment, unchanged ...`, standing for lines that task does
not touch, which is the permitted kind. No `TBD`, no `TODO`, no "similar to Task N", no "add
appropriate error handling", no "write tests for the above", and no code step without a code block.

Three things that read like placeholders and are not, named so a reviewer does not file them as
such:

- **`MEASURED_COLLAPSED_ENTERPRISE`, `MEASURED_OPEN_ENTERPRISE`, `MEASURED_FRAGMENT_ENTERPRISE`
  are `None` with an assertion that refuses to run until they are filled in.** That is spec §11's
  own mandate — *"measure the accounts-on number when the panel is built, write it into the test as
  an equality, and never as a `<=`"* — and a plan author who guessed those numbers would be writing
  a `<=` in disguise. Each has a named step that measures it and a docstring saying what it is made
  of.
- **Task 13 says "re-check the number before writing".** That is the spec's own instruction, and
  `0018` being free is verified in the Spec-vs-tree table.
- **Task 12's README rows say "verify it still describes what shipped".** The content was written
  in Tasks 1, 5 and 6; this row is the check, not the writing.

### 3. Type consistency

Checked every name that crosses a task boundary against the Interfaces block that declares it.

- `HelpField(name, anchor, meaning, effects)` and `HelpCard(route_name, title, gate, purpose,
  fields)` — identical in the dataclass, in all eleven cards, in `_content_hash`, in `run_card`, in
  `_links`, and in every test.
- `CARDS`, `CONTENT_HASH`, `_content_hash`, `card_for`, `card_routes`, `page_choices`, `EVERYONE`,
  `ADMIN`, `ACCOUNTS_ADMIN` — one spelling each, everywhere.
- `SETTINGS_CARD` / `SETTINGS_OVERVIEW` / `run_card` / `run_overview` / `_PAGE_INDEX` — Task 4
  produces, Task 5's `tool_keys` names the two **keys** (`"settings.card"`, `"settings.overview"`),
  never the constants.
- **`data["links"]` entries are `{"route", "anchor", "label"}` triples; `assistant["links"]` entries
  are `{"url", "label"}` pairs.** Two different shapes with one name, on purpose — the first is what
  a tool returns and the second is what a template renders — and the transformation is `_links`.
  Both Interfaces blocks spell out which is which, because confusing them is exactly how a URL a
  model typed would end up in an `href`.
- `SETTINGS_SURFACE_SLUGS` (Task 5) → `chat_surface_agents` / `chat_surface_conversations`
  (Task 6). `ASSISTANT_SLUG` is a **separate** constant in Task 8, and Task 8's own comment says
  why: the set answers "which agents are off `/chat/`" and the panel needs "which agent this panel
  is for", and a `next(iter(...))` over a frozenset would let the set silently decide.
- The `assistant` dict: **twelve keys after Task 8, sixteen after Task 10.** Both Interfaces blocks
  name their own set, and Task 10's Step 6 is the step that adds the four.
- `_card`'s six keys — `role`, `state`, `text`, `text_html`, `error`, `pending` — match the template's
  `card.role` / `card.pending` / `card.error` / `card.text` / `card.text_html` exactly.
- `_back` exists in **two** modules — `agents/chat/views/defaults.py` (Task 9) and
  `agents/chat/views/assistant.py` (Task 10). Deliberate and checked: different files, different
  fallbacks (`chat-index` versus `settings-index` plus `?assistant=1`), and each docstring says
  which.
- `_is_xhr` is **copied**, not imported, from `agents/chat/views/turns.py` — five lines, and this
  package's import direction is one-way. Task 10 says so in the function's own docstring.

### 4. Dependency order and commit contents

- **Every task's `git add` stages every file its own green step needs.** Task 4 stages
  `test_column_boundaries.py` in the same commit as the module (the recipe requires it); Task 6
  stages all six call-site files plus `visibility.py`; Task 10 stages `context_processors.py`
  because Step 6 edits it.
- **No task's code reverses a URL name a later task creates.** This was a real defect on the first
  pass: Task 8's `panel_context` reversed the three assistant routes, which Task 10 creates — so
  Task 8's own tests could not have run at its own boundary. Fixed by moving those four keys to
  Task 10 Step 6, with both Interfaces blocks recording the split. `_links` reverses only the
  eleven settings routes, all of which exist before Task 1.
- **No task's test asserts on a template a later task writes.** Task 8 asserts on
  `response.context`; the fragment is Task 10's and Task 10's tests are the ones that read HTML.
- **The two gates the brief requires to land with their surface do.** M2 (the context processor's
  admin clause) is written and tested **in Task 8, in the same commit as the processor** — not as a
  later hardening pass. M4 (`visible_conversation_or_404`'s narrowing) is written and tested **in
  Task 6, in the same commit as the five list call sites.**
- **Task 5 knowingly turns a test red that Task 6 turns green again.** Adding a fourth catalogue
  entry changes what `/chat/`'s offers list holds. Task 5 Step 6 says to record which test, and
  Task 6 Step 7 says to fix it there — because that is where the fix belongs, and a task that
  weakened the test at Task 5 would have deleted the evidence.

### 5. Non-vacuity

Every new assertion can fail, and the ones that could not have were rewritten:

- **The `+0` / `+1` / `+3` query pins now call the processor directly**, with the settings row
  stashed the way `IdentityGateMiddleware` stashes it. On the first pass they were written as
  `django_assert_num_queries(baseline + 1)` where `baseline` was a whole-page count taken with the
  processor already registered — which is `n == n + 1`, or a pass by accident. The absolute
  whole-request numbers are still pinned, by equality, through the real client.
- **Threading the settings row into `principal_for_request` is a correction, not a flourish.** That
  function reads the singleton itself through `accounts_on()` when it is not given one
  (`identity/request.py:91`), so without the keyword the "zero queries for a principal the panel
  will not render for" claim was false by one. Both call sites now pass it.
- **The member case is pinned as tables, not as a count.** A signed-in member's `is_admin` reads
  `auth_user` once — a read the gate middleware has already made and memoised on the same row
  instance — so the honest assertion for that principal is "no panel row is read", checked against
  `agents_agent`, `agents_conversation` and `agents_turn` by name.
- **The content-hash test mutates a copy**, so no other test in the run sees a different hash than
  the one computed at import — and it asserts inequality three ways (text, field, order) plus a
  separator-collision case.
- **The anchor sweep is proved to fail before it is trusted**: Task 2 Step 10 renames one `id=`,
  watches assertion 3 go red naming the card and the control, and restores it. That is drill 4, run
  once at the task that creates the guard rather than only at the end.
- **The CSS placement gate has a negative half** — `test_the_chat_only_composer_rules_stayed_behind`
  — without which it would pass on a commit that moved the whole composer region into the shell.
- **`_defines` matches on the selector list, not on a substring**, because
  `.composer-card-drop-target` contains `.composer-card` and a naive `in` test would call one a
  definition of the other.
- **Two anchors the spec's own card set would have made red were caught and are not cited.** The
  console's `on-this-machine` sits inside `{% if installed_rows %}` in the cold-start branch, and
  `setup-index`'s `accounts` sits inside `{% if identity_posture == "open" %}`; the sweep runs a
  world with no reachable engine, in the enterprise posture, so a card citing either would fail on
  every honest test box. Task 2 names both, with the reason.

### 6. Calls this plan made that the spec left to a plan author

Not defects. All three were checked by the round-1 review and **confirmed correct as written**;
they are kept here because they are the places an executing agent is most likely to second-guess.

1. **Plan decision 1** — splitting the drift assertions across Tasks 1 and 2, on the rendered-body
   line, so each half is driven by a test that really fails first.
2. **Plan decision 4** — `agents/chat/views/workstreams.py:792`, an eighth `visible_conversations`
   call site the spec's §7.1 table does not list, left un-narrowed for the same reason `:373` is and
   covered by the same `workstream_id IS NULL` pin. Round 1 confirmed there are exactly seven
   non-test call sites plus this one, and that leaving it alone is right.
3. **Task 2's two un-cited anchors** — `on-this-machine` (inside `{% if installed_rows %}` in the
   cold branch, and the sweep's world has no reachable engine) and `accounts` (inside
   `{% if identity_posture == "open" %}`, and the sweep runs enterprise). Round 1 re-derived both
   from the templates and confirmed that citing either would be permanently red.

**Nothing here is open.** m11 — whether `.agent-picker` is promoted for a control the panel never
renders — was **settled by orchestrator ruling on 2026-09-10**: it is skipped, the single-consumer
rule outranks spec §6.5's letter, and Task 7 records the deviation and splits no rules.

---

### 7. Executability (added after round 1)

Every helper a test names is imported or defined in the same module; every constructor is called
with the fields its dataclass requires; every regex reader is checked against the file it is
pointed at; and every assertion is checked against the string the plan's own production code
produces. The five things that pass this now and did not before:

- **`make_tool_ctx` is used, not re-derived.** `ToolContext`'s first five fields carry no defaults
  (`agents/contracts/tools.py:292-296`), and `agents/tests/_helpers.py:192-206` already is this
  package's builder.
- **`run_overview`'s refusal copy and its leak assertion are consistent.** `"open"` stays in the
  leak list — it is the posture value drills 1b and 2 turn on — and the sentence was reworded
  around it.
- **`_inline_style` reads `_shell.html`.** `_style_block` extracts an `extra_style` **block tag**;
  `_shell.html` is the base, its CSS is a plain `<style>` at `:88`, and the only block tag in it is
  the empty hook at `:563`. The old reader answered `""`, which made all three new gate assertions
  unreachable — including the one guarding this plan's own most delicate edit.
- **`panel_context` takes `is_open` and `next_url`.** The XHR ask is a POST with no query string,
  and the poll route's own URL is not a settings page; deriving both from `request` there returned
  a collapsed fragment to the one path the script exists for, and fed the composer its own action
  URL as a `next`.
- **`_patch_queue` is imported, `raises=` takes an exception instance, and `_finish_pending` is
  written out.**

### 8. Counts, re-checked

**110 steps** (`- [ ] **Step N:`) across 14 tasks, plus a **12-item Smoke Checklist** (drills 1, 1b,
1c and 2–10) — 122 checkboxes in total. **Eleven cards, 26 fields, 26 anchors** (22 added by Task 2,
4 already in the tree), **11 new files**, **23 modified**. Task 2 Step 2 expects **10 of 11**
parametrized cases to fail, not 9: only `setup-index` passes.

---

## Plan review

### Round 1 — 2026-09-10, adversarial plan-hygiene review of `83f7cfa`

**Verdict: AMEND. 6 Major · 13 Minor · 6 Nit.** No deviation from the spec's twenty-four author
decisions or six owner flags was found, and no finding required re-opening the spec. The reviewer
re-read 40+ of this plan's `file:line` citations in the tree rather than trusting its quotations,
and confirmed clean: the eleven anchor insertion points, the card table's 26 anchors, the
`ROUTE_RULES`/`_DRIVERS`/`config/settings.py`/`config/urls.py` edit sites, the CSS split, the
migration numbering and the ADR number. All five of the spec's review-hardened mechanisms were
confirmed to survive into tasks **un-watered-down**, and the link whitelist was judged *stronger*
than the spec asked (the label comes from the card, not from the tool result).

**Disposition: all 25 findings applied.** The six Majors, and what each actually was:

- **M1** — `agents/tests/test_settings_tools.py`'s hand-rolled `_ctx()` could not construct a
  `ToolContext` (`budget` and `job` have no defaults), so **every test in the module** raised
  `TypeError` before its first assertion. It was also a duplication of
  `agents/tests/_helpers.py::make_tool_ctx`. Deleted; all 15 call sites now use the helper, and the
  import block collapsed to one line (n6).
- **M2** — the refusal-leak assertion bans every posture token including `"open"`, and the plan's
  own refusal copy read *"or **open** Settings if you are one"*. The **sentence** moved, never the
  list: `"open"` is the posture value drills 1b and 2 turn on.
- **M3** — the new CSS placement gate called `_style_block()` on `_shell.html`, whose CSS is a
  plain `<style>` element, not an `extra_style` block. The helper answered `""`, the gate's own
  "this check itself is broken" guard fired first, and all three assertions were unreachable — for
  the task the plan itself ranks as its riskiest edit. A dedicated `_inline_style` reader was added
  beside it, with the reason written down.
- **M4** — `panel_context` derived `is_open` and `next` from the rendering request's own URL, which
  is right inline on a settings page and wrong on both assistant routes. The XHR ask would have
  returned a **collapsed** panel with no transcript and no pending marker, so the poller would stop
  on its first tick — the one behaviour the script exists for. Both are now explicit parameters;
  `_fragment` passes `is_open=True` and a new `_here(request)`, which also fixed the runaway
  poll-URL nesting and the 405-on-fallback the reviewer traced from the same root.
- **M5** — the Task 10/11 test module called `_patch_queue` (never imported), passed it
  `raises=True` (the helper does `raise raises`; `raise True` is a `TypeError`), and called
  `_finish_pending` (defined nowhere in 6,411 lines). All three fixed.
- **M6** — Task 12's docs pass transcribed six line numbers that **Task 4 Step 5 had already
  moved**, and would have changed `docs/EXTENDING.md:134`'s `:217-228` — which is **correct** — to
  the spec's `:217-231`, which is not (`:228` is the tuple's closing paren). The step is now a
  **derivation**: two greps, then fix each citation site located by its surrounding text. A seventh
  stale citation the spec's §18 table missed (`:138`'s `:486`) is named too.

The Minors and Nits landed where they belong: the `run_overview` query pin corrected from 2 to 3
with the gate's own `_user_row` read named (m1); `settings_row` threaded into `principal_for_request`
in `assistant_ask` and `assistant_reset`, which is the correction this plan already made in the
processor (m2); the separator-collision test given a pair that actually collides (m3); Task 2 Step
2's expected-failure count corrected to 10 (m4); Task 6 Step 4's "five call sites in five files"
corrected to six in four (m5); the Architecture's green-at-every-boundary claim amended to name the
5→6 exception rather than contradict Task 5 Step 6 (m6); the `__import__`-in-a-walrus client
replaced with the fixture (m7); Task 9's "three chat surfaces" test given its third, which is the
one where a stray `next` would actually collide with `turn_create`'s own reader (m8); Task 2's
feature-pin docstring given the true reason, since both supported suite states carry `"vision"` and
the mount is import-time anyway (m9); the redundant `enabled=True` dropped at both new call sites,
citing `agents/runtime/delegate.py:99-102` (m10); the purity test renamed to what it asserts and
`"foundation"` added to its denylist, because a sibling import there would be a real cycle (m12);
the step counts stated honestly (m13); and the four off-by-one citations (n1, n2, n3, n4), the
per-class `django_db` marks (n5) and the single-import helper family (n6).

**One finding was raised to the orchestrator rather than applied at round 1. m11:** Task 7 promoted
`.agent-picker` for a control the panel never renders — `chat/_composer.html:105-114` emits it only
under `composer_mode == "start"`, and the panel passes `"turn"` — and that was the sole reason the
task split four combined rules. **The orchestrator ruled on 2026-09-10: skipped.** The
single-consumer rule outranks spec §6.5's letter, so the promotion is five selectors, the four
combined rules at `chat/base.html:1167`, `:1171`, `:1176` and `:1187` stay whole, and Task 7 carries
the deviation as a recorded ruling rather than an open question. Applied in round 2.

**Two of the author's own nine declared concerns were adjudicated as overblown** and are recorded
as settled rather than open: the three unfilled `MEASURED_*` constants (spec §11 mandates
measure-then-pin, and guessing them would be a `<=` in disguise) and `_back` existing in two
modules (different files, different fallbacks — and M4's fix collapsed one of them onto `_here`
anyway). Concerns 1, 2, 3, 5, 6 and 8 were confirmed correct as the plan had them; concern 7 became
m6.

### Round 2 — 2026-09-10, scoped re-check of `c4ece46`

**Verdict: 2 residuals.** 24 of round 1's 25 findings verified applied — M3 by executing the new
reader against the real `_shell.html`, M2 by hand-probing the reworded refusal against the leak
list, and M4/M5/M6 by re-reading the tree lines the amendments cite. The reviewer also confirmed the
round-1 record above overstates nothing, that no amendment contradicts an unamended task, that the
inline context-processor path is behaviourally unchanged by M4's new keywords, and that M4's fix
**strengthened** the panel's `next` handling by putting a host-and-scheme guard on the one path
(`?next=` on the poll route) that previously had none.

It also confirmed the **seventh stale citation** this plan caught beyond spec §18's table:
`docs/EXTENDING.md:138` cites `(:486)` for the no-service-import-at-module-scope rule and the real
`def` is `:492` — the same test `~:104`'s `:485` misnames. Task 12 Step 2 already covers it by
derivation.

**Both residuals applied here.**

- **R1 — the orchestrator's m11 ruling was not in the plan text.** Round 1 raised `.agent-picker`
  and the orchestrator ruled on it: **skipped**, because the panel never renders that control and
  the house single-consumer rule outranks spec §6.5's letter. `c4ece46` recorded the question but
  still did the opposite in three places — the selector was still the sixth entry of
  `_COMPOSER_SELECTORS`, Step 4 still split the four combined rules at `chat/base.html:1167`,
  `:1171`, `:1176` and `:1187` and still wrote four promoted `.agent-picker` rules into
  `_shell.html`, and three passages framed it as open. All now say the same thing: five selectors
  promoted, `.agent-picker` stays with the model picker in `chat/base.html`, **nothing is split**,
  and the deviation is a recorded ruling rather than a question. Step 3's prediction moved from
  "all six" to "all five"; `test_the_chat_only_composer_rules_stayed_behind` needed no change, since
  it already pins `.composer-toolbar .picker` staying and `.agent-picker` simply stays with it.
  **This removes what both reviews and this plan's own report called its most delicate edit.**

- **R2 — the m8 amendment introduced a test that cannot pass.** Round 1's m8 asked Task 9's
  `test_the_three_existing_chat_surfaces_still_emit_no_next` to cover its third surface, and adding
  `make_thread()` to seed it is what exposed the flaw: `chat/_sidebar_row.html:63,81,88,97,104`
  already emit five `name="next"` hidden fields for the row menu's rename / pin / archive / delete
  forms, and `chat/base.html` draws that rail on **all three** pages — so a whole-body assertion is
  false against unmodified `main` the moment there is one conversation to list. Round 1's version
  passed only because the open-posture sidebar had no rows. The assertion is now scoped to the
  composer's own form (`composer-card` div → its `</form>`), which is what the claim was always
  about; `test_it_emits_nothing_when_omitted` covers the same invariant from the fragment side and
  is unchanged.

**Nothing else changed.** No task was added or removed, no interface moved, and the spec was not
re-opened in either round.
