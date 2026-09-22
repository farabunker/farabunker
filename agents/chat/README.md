# agents/chat/ — the `/chat/` surface

A permanent chat **product** — a page an operator uses to talk to their
agents — not an agent builder and not a flow builder. `agents/` already
owns everything that plans and runs a turn (`agents/runtime/`); this app
is the UI in front of it: one Django app, `label = "chat"`, no models, no
migration, and no `ready()` (nothing here registers a role, a job kind,
or a tool — that all lives in `agents/apps.py::AgentsConfig`, which
already runs).

It has its own label rather than living inside `agents` because it needs
**template discovery** — `TEMPLATES[0]["APP_DIRS"] = True`
(`config/settings.py`) finds `agents/chat/templates/chat/` only for an
installed app — and its own `tests/` package with its own helpers. See
`agents/chat/apps.py` for the full reasoning.

Mounted **ungated** at `/chat/` (`config/urls.py`), unlike the
`vision/` mount: `docs/DEV.md:235-246` fixes the two supported suite
states at `'vision,media'` and `'vision'`, and a third
`FARABUNKER_FEATURES` token would either break that contract or leave
this whole surface untested in one of them.

## URL table

The full table this app grows into across Tasks 6–11.

| Path | Name | View | Ships in |
|---|---|---|---|
| `/chat/` | `chat-index` | `ChatIndexView` | Task 3, fleshed out in **Task 6** |
| `/chat/start/` | `chat-start` | `conversation_start` | **Task 6** |
| `/chat/defaults/install/` | `chat-default-install` | `default_install` | **Task 6** |
| `/chat/c/<uuid:conversation_id>/` | `chat-conversation` | `ConversationView` | **Task 6**, fleshed out in **Task 8** |
| `/chat/c/<uuid:conversation_id>/turn/` | `chat-turn` | `turn_create` | route mounted in **Task 8** (placeholder); real 202/302/503 body in **Task 9**, in its own `agents/chat/views/turns.py` |
| `/chat/c/<uuid:conversation_id>/delete/` | `chat-conversation-delete` | `conversation_delete` | route mounted in **Task 8** (placeholder — see below); real body in **Task 11**, now in `agents/chat/views/conversations.py` |
| `/chat/turns/<int:turn_id>/` | `chat-turn-status` | `turn_status` | route mounted in **Task 9** (placeholder — `turn_create`'s own 202 body must hand the poller a real URL, built with `reverse()`); real body in **Task 10** |
| `/chat/c/<uuid:conversation_id>/rename/` | `chat-conversation-rename` | `conversation_rename` | **UI-3b** |
| `/chat/c/<uuid:conversation_id>/duplicate/` | `chat-conversation-duplicate` | `conversation_duplicate` | **UI-3b** |
| `/chat/c/<uuid:conversation_id>/archive/` | `chat-conversation-archive` | `conversation_archive` | **UI-3b** |
| `/chat/c/<uuid:conversation_id>/unarchive/` | `chat-conversation-unarchive` | `conversation_unarchive` | **UI-3b** |
| `/chat/c/<uuid:conversation_id>/turns/<int:turn_id>/edit/` | `chat-turn-edit` | `turn_edit` | **chat cluster, feature C** — see "Editing a past prompt" below |

**`chat-conversation` shipped as a route, not a page, in Task 6.**
`agents.chat.service.conversation_url` and `conversation_start`'s
redirect both need a real URL name to build a link to — `Conversation.id`
is a UUID pk, so the route's converter had to match before the page did.
Task 8 (this page) extends `ConversationView` in place into the real
thread: every turn as a card, tool cards with citations and thumbnails,
delegated turns in a collapsed disclosure, and the message form.

**`chat-conversation-delete` had a placeholder route from Task 8; Task
11 gives it its real body,** moved to `agents/chat/views/
conversations.py` (the placeholder lived in `views/thread.py`, which
Task 11 leaves owning only the read path — see "Error surfaces and
deleting a conversation (Task 11)" below).

**`chat-turn-status` was a placeholder route in Task 9**, for the
identical reason: `turn_create`'s own 202 JSON body must hand the
poller a real `status_url`, built with `reverse("chat-turn-status",
...)`, so the name had to resolve before the view had a real body.
Task 10 gives it the actual polling contract — see "Polling a turn
(Task 10)" below.

## Pre-auth behaviour, stated in full

**Identity & Auth has since landed (both halves) — this section is now
a description of the `open` posture, one of the box's three, not of "the
box" in general.** It is preserved because the design rationale (ruling
4, and why the seams below were built while there was exactly one
caller of each) still explains why an `open` box behaves the way it
does; where a sentence below was written as if there were no accounts
at all, read it as "in `open` posture." `personal`/`enterprise` posture
and IA-2's entitlement labels are covered starting at "Agents and flows
carry their own labels" and "Tool access and conversation sharing"
below.

In `open` posture — the shipped default, and still what "no accounts"
means on this box (spec section 14 gap 4: `/chat/` was unauthenticated,
like every other surface — in open posture it still is; under accounts
`chat-index` is class `A`):

- **The index lists every conversation on the box, with no owner
  filter.** `ChatIndexView`'s queryset goes through `agents.visibility.
  visible_conversations(principal)`, which in open mode returns
  everything — the honest answer for a box with nobody to hide anything
  from, not a special case this view carries on its own.
- **`Conversation`/`Agent`/`Flow`'s `owner_kind`/`owner_key` are
  stamped on every create, today, in open mode.** `create_conversation`
  (`agents/visibility.py`) and the Add-default button both stamp the
  ACTING principal — `Principal("open", "box")` when nobody is signed
  in — rather than leaving the columns blank until accounts exist. A
  row written with blank owner columns is a row a later filter cannot
  reason about, and backfilling one is a migration nobody has the
  information to write; ruling 4 chose to pay that cost now, while
  every row's owner is knowably "open".
- **The visibility functions in `agents/visibility.py` are where a real
  filter went once auth landed** — an edit to those functions, never a
  new listing view and never a migration. `visible_conversations`'s own
  filter (ownership, `Share`) and the sibling functions' box-wide/
  resident carve-out (`box_wide=True` for `visible_agents`/
  `installed_agent_slugs` since task 5, chat cluster feature B;
  `resident=True` still for `visible_flows`) plus the label clause those
  three now carry are both covered below and in
  [`agents/README.md`](../README.md).

## The three auth seams (ruling 4)

See [`identity/README.md`](../../identity/README.md) for the column
these three seams now reach: the postures, `is_admin`/
`sees_all_content`, and the four modules every column may import.

Built now, while there is exactly one caller of each, so Identity & Auth
is a fill-in rather than a sweep across six views. In open mode all
three are true today and none of them change any current behaviour —
which is exactly why each one carries a guard rather than a convention.

1. **One request → principal point.** `identity/request.py::
   principal_for_request` is the only place in the codebase that turns
   an HTTP request into a `Principal`. In open posture it returns the
   SAME `OPEN_PRINCIPAL` object `identity/contracts/principals.py`
   declares — `Principal("open", "box")`, a real principal kind, not a
   `None` special case. `foundation/ops/tests/test_import_law.py` pins
   that a `Principal(...)` is constructed in only a small, named,
   closed set of files; this module is one of them.

2. **One branch point.** `IdentitySettings.posture` (the singleton row,
   read through `identity.access.accounts_on`) is that seam's switch,
   not an environment variable — a security posture read from the
   environment could be set on one process and unset on another in a
   box that runs three of them independently. `open` (the shipped
   default) never runs a permission query; `personal`/`enterprise`
   resolve the signed-in user, or `ANONYMOUS` for a request with no
   session. See `docs/DEV.md`'s "Accounts, and the three postures"
   section.

3. **Five functions, and one rule.** `agents/visibility.py`'s
   `visible_conversations` / `visible_agents` / `installed_agent_slugs` /
   `visible_flows` / `create_conversation` are the ONLY place this app
   (or any future MCP edge, or a management command) reaches
   `Conversation`, `Agent`, or `Flow` `.objects` — including the
   *create*, which lives there too rather than leaving a
   `Conversation.objects.create(...)` in a view for the guard to carve
   an exception around. In open mode every visibility function returns
   everything, so this changes no behaviour today; `foundation/ops/
   tests/test_column_boundaries.py::
   test_no_chat_module_queries_the_three_owned_models_directly` is the
   guard that keeps it that way — a view that grew its own
   `Agent.objects.filter(...)` would silently keep showing everybody
   everything once Identity & Auth adds a real filter, on the one page
   where that would matter most. `owner_fields` — the ownership stamp
   `create_conversation` applies — moved OUT of this module in Task 11,
   to `identity.access.owner_fields`: all five owner-carrying tables in
   three columns share that one definition now, rather than three that
   agreed by convention.

`identity.request.principal_for_request` reads the session and mints a
`user` principal once the posture leaves `open` (IA-1); `agents/
visibility.py`'s visibility functions carry a real `owner_kind`/
`owner_key` filter for `personal`/`enterprise` (plus, since IA-2, the
`Share` and entitlement-label clauses below) — and every caller in this
app, having already gone through them rather than querying `.objects`
directly, needed no change of its own when the filter arrived.

## The settings-surface exclusion (owner ruling 2)

The settings assistant (`agents.defaults.SETTINGS_SURFACE_SLUGS`, one
slug today: `"settings-helper"`) is a real `Agent` row an operator may
install, and `visible_agents`/`visible_conversations` correctly answer
YES for it — the settings panel (Task 10) reads exactly that row and
exactly its conversations, in the same request cycle a chat view would.
What ruling 2 asks for is not a permission change but a SURFACE one:
this agent and its conversations are never listed, offered, or reachable
under `/chat/`.

**Two thin wrappers, never a narrowing of the gate itself**
(`agents/visibility.py:217-268`, immediately after `visible_agent_slugs`).
The comparison is **case-insensitive** — `Lower("slug")` / `Lower("agent__
slug")` against a lower-cased `SETTINGS_SURFACE_SLUGS`, not an exact
`slug__in` — because a Task 6 review (Finding 4) caught that an exact
match would let a case-variant row (`Settings-Helper`) slip back onto
`/chat/` even though `install_default` would report it as already
installed:

```python
def chat_surface_agents(principal):
    from agents.defaults import SETTINGS_SURFACE_SLUGS
    surface_slugs = {slug.lower() for slug in SETTINGS_SURFACE_SLUGS}
    return visible_agents(principal).annotate(
        _settings_surface_slug_ci=Lower("slug")
    ).exclude(_settings_surface_slug_ci__in=surface_slugs)

def chat_surface_conversations(principal, *, settings_row=None):
    from agents.defaults import SETTINGS_SURFACE_SLUGS
    surface_slugs = {slug.lower() for slug in SETTINGS_SURFACE_SLUGS}
    return visible_conversations(principal, settings_row=settings_row).annotate(
        _settings_surface_slug_ci=Lower("agent__slug")
    ).exclude(_settings_surface_slug_ci__in=surface_slugs)
```

`SETTINGS_SURFACE_SLUGS` is imported inside each function body rather
than at module scope: `agents/defaults.py` is imported by `agents/
apps.py` and by management commands, and `agents/visibility.py` is
imported very early, so a module-scope import here would add an import
edge this module has never had. There is no `chat_surface_only=False`
escape hatch (spec decision 22) — nothing needs one, because the panel
never calls either wrapper; it reads the un-narrowed
`visible_conversations(principal).filter(agent__slug=…)` and
`visible_agents(principal).filter(slug__iexact=…)` directly, which is
the surface these wrappers exclude *from*.

**The five list call sites**, each a one-word rename to the matching
wrapper: `ChatIndexView`'s own agent list (`conversations.py:167`),
`_startable_agent` (`conversations.py:265`, so a hand-crafted `POST
/chat/start/` naming the assistant's slug is refused with a 400 rather
than silently creating an orphaned conversation), the workstream New-
chat card's agent list (`workstreams.py:401`), the sidebar's active/
pinned/archived base queryset (`sidebar.py:161`, one queryset three
`.filter()`s share), and `/chat/all/`'s rows-and-preview base queryset
(`all_conversations.py:137`).

`conversations.py`'s "Add the default X" offers and its
`nothing_installed` banner are the one call site with no wrapper to
call — `installed_agent_slugs` and `missing_defaults` answer in slugs
and catalogue entries, not querysets. Two separate subtractions, not
one, because one filtered list cannot serve both correctly at once:
`installed_slugs` stays the UNFILTERED set `missing_defaults` needs
(dropping the assistant's slug from it would make `missing_defaults`
treat an actually-installed assistant as "missing" and re-offer it,
which is the opposite of ruling 2), so the offers list instead strips
`SETTINGS_SURFACE_SLUGS` from `missing_defaults`'s own *result*,
unconditionally — never offered, installed or not. A second, separately
named `chat_surface_installed_slugs` (installed_slugs minus the
assistant's slug) answers the `nothing_installed` banner's own question
alone, so a box whose only agent row is the assistant says "nothing
installed" rather than the false "all installed agents are disabled".

**The row-addressed door.** `visible_conversation_or_404`
(`agents/chat/service.py`) is the one function all nine row-addressed
chat views (`thread.py`, `turns.py` ×2, `shares.py`,
`conversations.py` ×5 — render, post a turn, attachment_detach, rename,
duplicate, pin, unpin, archive, unarchive, delete, and share; `turns.py`'s
two sites are post and attachment_detach, and pin/unpin and
archive/unarchive each share one `conversations.py` site through a
shared helper, which is how the nine sites cover eleven actions)
resolve a conversation id through. It now asks `chat_surface_conversations`
unconditionally, so
every one of those nine answers 404 on a settings-surface conversation
— 404 rather than 403, by that function's own long-standing rule that a
403 on a row-addressed URL confirms the row exists. Lists make the
assistant merely invisible; this is what makes it unreachable, which is
what "settings-only surface" actually claims. Share is the case that
matters most: `Share.Target.CONVERSATION` exists and the share action
lives on the page this now 404s, so spec §15.5's "a shared assistant
conversation is deferred" is a shut door, not merely an unwalked one.
There is no escape-hatch keyword here either, for the same reason: the
panel never calls this helper.

**Two id-addressed reads are deliberately left un-narrowed** — recorded
here so a later reader does not mistake either for a hole:

- `visible_turn` / `chat-turn-status` (`agents/visibility.py`, backing
  the turn-status poll fragment): it filters
  `conversation__in=visible_conversations(principal)`, the identical
  READ gate, so it is already owner-only; and it exposes no affordance
  — a status fragment is not a page, and the settings panel does not
  poll it (it polls its own `settings-assistant-panel` route instead).
- `agents.workstreams.transcript_for` (`agents/workstreams.py:399`, the
  consolidation job's transcript read): same reasoning — it reads
  through the un-narrowed `visible_conversations` and is called from a
  worker job, not from any `/chat/` view, so there is no chat-surface
  affordance to close.

The claim this phase makes is precise: every row-addressed chat *view*
is closed, not that no code path anywhere can resolve the row by id.

`agents/chat/tests/test_settings_surface.py` covers both halves —
`TestTheListSurfaces` for invisibility, `TestTheRowAddressedDoor` for
unreachability (including a parametrized sweep of all nine views plus
the share case on its own) — and pins the two deliberate exceptions in
`TestWhatIsDeliberatelyLeftAlone` so they read as a decision, not an
oversight.

## The settings assistant panel (Task 10, the poller in Task 11)

`agents/chat/context_processors.py::settings_assistant` is a **context
processor** — the exception to "no context processor pays a per-page
cost", earned rather than assumed: `identity/`, which owns four of the
settings pages, may not import `agents/` at all, so a per-view
context would need an edit in every column that owns a settings page, and
this repository has no `templatetags` package to reach for instead. It
runs on every page in the box behind **two guard clauses**, both before
any row is read:

1. `resolver_match.url_name not in card_routes()` — a pure `frozenset`
   membership test. On every page that is not a settings page this costs
   one attribute read and one set lookup, and zero queries.
2. `not is_admin(principal, settings_row=row)` — a GATE, not an
   optimisation. `_settings.html`'s `{% if identity_is_admin %}` decides
   what RENDERS, not what gets BUILT, and `setup-index` ("Install
   guides") is a real settings-area entry gated `EVERYONE`, extending
   `_settings.html` — so without this second clause an anonymous visitor
   on an accounts-on box would have an agent row, a conversation lookup
   and several turns resolved for them before the template threw all of
   it away.

Passing both, it calls `panel_context`, the SAME builder
`settings-assistant-panel` (the poll route, below) renders from — the
inline render and the poll body come out of the same function over the
same rows, the `chat/_turn_block.html` precedent this app already uses
elsewhere.

**Three routes, all class S** (`identity/routes.py::ROUTE_RULES`), all in
`agents/chat/views/assistant.py`, mounted by `config/urls.py` at
`/settings/assistant/` — the composition root is the one module that
already imports every column, so nothing crosses a boundary to put a
settings-shaped URL in front of an `agents/chat` view:

- `settings-assistant-ask` (POST) — one question. Resolves or creates the
  asking principal's own conversation, OWNER-FILTERED unconditionally
  (never a bare `visible_conversations(...).first()`, which under
  `sees_all_content` could resolve to a DIFFERENT administrator's
  most-recently-updated thread), and starts a turn.
- `settings-assistant-reset` (POST) — "Start over". Archives the
  conversation; nothing is deleted.
- `settings-assistant-panel` (GET) — returns the same fragment the inline
  render produces, for the poller below. Writes nothing.

Each of the three carries its OWN `is_admin` check, in addition to the
class-S middleware gate, because `panel_context` itself carries no gate
of its own — the context processor above is the only *existing* caller
that gates before calling it, so a future caller that reaches one of
these views from somewhere the middleware does not run in front of (a
management command, a test that calls the view function directly) would
otherwise render for a non-admin. The repeated check costs nothing extra:
`is_admin` memoises the `auth_user` read on the same `settings_row`
instance every other call on the request already threads.

**The composer's one added parameter.** `chat/_composer.html` gained
exactly one new hidden field for this phase, `composer_next` — emitted as
`<input type="hidden" name="next">`, the identical shape
`composer_workstream` already uses. It is what lets the ask view's
redirect return the operator to the settings page they asked from, rather
than to the ask route's own URL. Every other chat view that includes the
composer supplies its routing context from its own builder
(`composer_attach_context`/`thread_context`); this panel is the one
caller with no such builder, so `_assistant_panel.html` passes
`composer_next=assistant.next` — and `attach_workstream=None` explicitly,
since `_composer.html`'s own `{% with routing_workstream=
composer_workstream|default:attach_workstream %}` resolves that name as a
filter argument, which Django does not give the normal
missing-variable leniency.

**Its own compact transcript, not `chat/_turn_block.html`.** The chat
turn block renders tool cards with arguments, thumbnails and citations,
artifact image and file strips, attachment rows and five turn states —
reusing that markup would drag roughly twenty `.turn*`/`.tool*` selectors
out of `chat/base.html` and into the global shell so they could render on
a page that then hides most of them. `context_processors.py::_card` is
its own compact view model instead, in the four states this surface has
(spec §6.4), reusing only `agents.chat.rendering.render_answer` — so a
model's `**bold**`, lists and code spans render identically here and on
`/chat/`, escaped first and emitting no `<a>`. The "Jump to" strip beside
it is the platform's, not the model's: every link is built from a
`route`/`anchor` pair re-validated against the help-card table, never
from anything a model wrote into its answer.

**The one script's scope.** `_assistant_panel.html` renders two
`<script>` tags, both sanctioned (Task 10 review, a1): `chat/
_enter_to_send.html`'s keydown handler, pulled in unmodified by the
shared `chat/_composer.html` include (it renders on every composer
surface in the app, not just this one, for the identical reason it
renders on `/chat/`); and this feature's OWN script, the poller at the
bottom of the fragment — the one script Task 11 adds, and the only one
that is this feature's own budget (spec §6.7): "asking never navigates"
is a capability nothing already on the page provided, where Enter-to-send
is infrastructure this feature merely inherits by including the composer
that carries it. The poller replaces ONE container
(`#assistant-body`) with server-rendered HTML on submit and on each poll
tick, and stops scheduling the next tick the moment the swapped-in body
carries no `data-assistant-pending` marker. It is not a copy of the
conversation poller (`chat/conversation.html`), which swaps individual
turn cards by `data-poll-block` id, de-duplicates tool cards, and reads
two different refusal body shapes. Consolidating the two scripts was
considered and rejected: Enter-to-send is keydown-only by its own
explicit scope ("No fetch, no polling, no other behaviour") and shared,
unmodified, by two composer surfaces that must never poll — folding the
poller into it would either break that scoping or fork the shared script
into a settings-only copy. Neither script reads or writes `localStorage`,
`sessionStorage` or `indexedDB`; this fragment's own tests pin that for
both.

## The index (Task 6)

`ChatIndexView` (`agents/chat/views/conversations.py`) renders the
conversation sidebar (see "The conversation sidebar" below), every
enabled agent the operator can start a conversation with (through
`chat_surface_agents` — see "The settings-surface exclusion" below),
and the shipped defaults not yet installed
(`agents.defaults.missing_defaults`).

**UI-3b removed this page's own conversation list.** It used to render
one in its main pane, capped at a `CONVERSATION_LIST_LIMIT` of 100. The
list is the SIDEBAR now — on this page and on every thread page alike —
so there is no longer a standalone list page to come back to, and the
constant is gone with it. What is left in the pane is the one thing
this page does that a thread page does not: start a conversation.

**A GET installs nothing, ever (ruling 2).** A fresh box's index shows
an honest empty state — "no agents are installed on this box yet" —
naming the CLI equivalent (`manage.py install_defaults`) alongside the
button, and offers each shipped default with its own `Add the default
X` button. That button is a CSRF-protected `<form method="post">`, not
a link, so it works with JavaScript off and cannot be triggered by a
GET, a prefetcher, or a crawler. It lands on `agents.defaults.
install_default`, which is create-if-absent: a double click installs
one row, not two, and an already-edited row is never overwritten.

**Installed agents and offered defaults are two disjoint lists.**
`offers` is computed FROM the installed agents' slugs
(`missing_defaults("agent", [a.slug for a in agents])`), so a default
can never appear as both a thing you can talk to and a thing you can
add — merging the two lists would mean either hiding what the platform
offers or pretending an offer is something you can talk to.

**Starting a conversation is `conversation_start`
(`POST /chat/start/`).** It records the acting principal on the new
row (`Principal("open", "box")` in open mode — ruling 4b) through
`agents.visibility.create_conversation`, refuses an unknown or disabled
agent with a 400 and writes nothing, and redirects to the new thread's
URL (`agents.chat.service.conversation_url`). The picked model
connection (deviation P3-D7) travels in the redirect's query string,
never a session or a column, so the link is a complete description of
what the thread page will show.

**A non-blank first message posts the conversation's first turn
(Task 9)**, through the SAME `agents.chat.service.start_turn` the
thread's own message form uses — one starter, two callers, so the two
paths cannot drift. A blank `text` (the ordinary "just open a thread"
case) still opens an empty thread exactly as before Task 9. When
`start_turn` refuses (an unbound role, a model that cannot call tools,
an unavailable queue, …), `conversation_start` **deletes the
just-created empty conversation** and re-renders the index with the
refusal named in a banner — a refused start must never leave an empty
thread sitting in the conversation list. On success the redirect
carries `?pending=<turn_id>` (and `&connection=` when one was picked),
which the thread page renders as a pending turn.

## Posting a turn (Task 9)

`turn_create` (`POST /chat/c/<uuid>/turn/`, `agents/chat/views/
turns.py`) and `conversation_start`'s second half both go through
`agents.chat.service.start_turn(conversation, text, *, connection="")`
— the HTTP-free turn starter that follows `manage.py agent_turn` step
for step, because that command is this page's REFERENCE CLIENT and its
exit codes are this page's states.

**The title is cut on a word boundary, with an ellipsis (chat-polish
P3.1, U5).** `_title_if_unset` calls `service.truncate_title(message)`
rather than the hard `message[:TITLE_MAX]` slice it used before —
`chat/index.html`'s conversation list and `chat/conversation.html`'s
own `<title>` both render `Conversation.title` verbatim, so the old
slice reached both of them mid-word with nothing marking the cut
("...searching the libr"). `truncate_title` falls back to the hard cut
only when its 60-character window holds no whitespace at all (one very
long "word").

**One shared preflight.** Before anything is written, `start_turn`
calls `agents.runtime.preflight.preflight_turn(agent, connection, actor=actor)`
— the SAME function `manage.py agent_turn`'s `_preflight` now delegates
to (with `actor=SERVICE_PRINCIPAL`), so the CLI and the page can never
refuse different turns for the same reason. `actor` is REQUIRED and
keyword-only (Task 11, the acting rule): the question preflight asks
is "may THIS PRINCIPAL take this turn", not "may the agent", and a
default would let a forgetful caller silently ask the wrong one. A
refusal there means nothing is queued and nothing is
written: an unbound chat role, a picked connection that no longer
resolves, or a bound model that cannot call tools an agent's granted
tools need. `agents/runtime/preflight.py`'s own README entry has the
full contract.

**Two rows, one transaction.** A successful preflight writes the USER
turn (`state=DONE`) and the placeholder ASSISTANT turn (`state=QUEUED`)
inside one `transaction.atomic()` block, THEN enqueues `agent.turn`,
THEN stamps `queue_job_id` on the placeholder. A failed enqueue (a
caught `QueueUnavailable`, a caught `IntegrityError` from
`uniq_turn_index`, or any other exception) rolls the whole transaction
back — the page never leaves a half-written thread the way a crash
mid-write would.

**One turn at a time per conversation.** `start_turn` refuses a second
turn with **409** while an earlier ASSISTANT turn on the same
conversation is still `QUEUED` or `RUNNING`. The form is still on the
page while an answer is pending, so a double-submit or an impatient
second message is the ordinary way two `agent.turn` jobs would race on
one thread — and `Turn.next_index`'s read-then-write is only safe
because "a turn is enqueued only after the previous one finished"
(`agents/models.py`). This refusal is what makes that sentence true
for the page, exactly as it already is for the synchronous CLI.
**A worker that dies mid-turn is a known, named gap, not a silent
one**: nothing here notices the crash, so the assistant row stays
`RUNNING` and the conversation stays 409'd until either Task 11's
delete gives the operator a way out, or the queue's own orphan sweep
notices the stalled job and fails it (which is what `agents.runtime.
audit.close_open_invocations` and `on_terminal`'s FAILED/CANCELLED
handling exist to fix up once the job kind reports it).

**Two answers, one path.** `turn_create` reads `_is_xhr(request)`
(`tools/vision/views.py:584-587`'s five-line header check, copied
rather than imported across the column boundary) to decide the shape:
an XHR POST gets **202** with a JSON body (`turn_id`, `state:
"queued"`, `position`, `priority`, `status_url`, `notes`, and — since
D1, chat-polish P3.1 — `html`, the same rendered group `turn_status`'s
own queued/running/done bodies carry, so the submit handler can insert
real markup, the user's own message included, instead of a hand-built
placeholder) — never a `status_url` the script builds itself, always
`reverse("chat-turn-status", args=[turn_id])`. A non-XHR POST gets a
**302** redirect carrying `?pending=<turn_id>` (and `&connection=`
when one was picked), via the same `conversation_url` builder
`conversation_start` uses. A refusal answers the same way: **400** for
a blank message, **409** for one already in flight, **503** for every
preflight/queue refusal — an XHR response carries the message in its
JSON body; a non-XHR response RE-RENDERS THE WHOLE THREAD PAGE (via
`thread.py::thread_context`) with the message in `#turn-errors`, never
a bare unstyled fragment.

**Tolerant tools are a note, not a refusal.** A granted tool key that
is not registered on this install does not block the turn — spec
section 8.3 step 3's tolerant half — it is named in the 202 body's
`notes` and the turn runs without it.

## The thread (Task 8)

`ConversationView` (`agents/chat/views/thread.py`) looks the
conversation up through `agents.visibility.visible_conversations`,
never `Conversation.objects` directly (ruling 4c), so an unknown id is
a real 404, not a 500. `thread_context(request, conversation, *,
selected=None)` builds everything the template needs in ONE place —
the agent, the render-ready cards (`agents.chat.rendering.thread_cards`),
the model picker, and the poller's three tuning constants — so Task 9's
503 re-render (a failed POST that still has to show the thread) can
call the same builder and never risk showing a different thread, or a
different picker selection, than a GET would.

**A GET here never 503s.** An unbound chat role, an unreachable engine,
and an unavailable queue are all reasons a NEW turn cannot be queued;
none of them are reasons the operator may not read what was already
said. Reading `conversation.agent` directly (never through
`visible_agents`, which applies `enabled=True`) is what keeps a
disabled agent's past conversation fully readable — `enabled` is a
startability rule for the index and the start form, not a readability
rule for a thread that already exists. `Conversation.agent` is
`on_delete=models.PROTECT`, so an agent can never be deleted out from
under a conversation that references it either; there is no "the
agent's row is gone" case for this view to degrade around.

**The poller's three numbers live in `agents/chat/service.py`**
(`POLL_INTERVAL_MS`, `MAX_TRANSPORT_RETRIES`, `MAX_POLL_DURATION_MS`),
not in `views/turns.py`: `thread_context` hands them to the template
and `thread.py` must never import `turns.py` (the package's import
direction is one-way — see `views/__init__.py`'s own docstring). Task
10's poll script and its own test both read the same three numbers
from here.

**`_turn_card.html` and `_tool_card.html` take a card dict and nothing
else.** `_turn_card.html` is rendered twice — inline by this page, and
standalone by `turn_status`'s poll body (Task 10) — which is why every
rule either fragment needs lives in `chat/base.html`'s CSS rather than
on this page, the same reason `tools/vision/templates/vision/base.html`
owns the job-card rules. A `depth > 0` turn never renders itself at
top level: `thread_cards` buffers it and hands it to the next `depth ==
0` turn, which renders it inside a collapsed `<details>` (spec section
8.4) — auditable without being mistaken for the thread itself.

**A pending card carries exactly ONE `<noscript>` line, plus a seeded,
state-aware `.no-js-note` (chat-polish P3.1, D3; fix round 1's R3
trimmed the noscript to one; chat-resume-poll re-introduced the seed;
chat-poller-cleanup made it state-aware).** `_turn_card.html`'s
`card.pending` branch (true for `Turn.State.QUEUED`/`RUNNING`,
`rendering.turn_card`'s own key) renders `<noscript><p>` reading "This
page does not update on its own — reload to see the answer." — nothing
else — AND a `<p class="muted no-js-note">` seeded with "Queued…" or
"Working…" depending on `card.state`. The two never duplicate each
other: the `<noscript>` line only ever states the reload hint, and the
seed only ever states progress, so a JS-off reader (who sees both,
permanently) reads two lines that say different things. With scripts
on, `conversation.html`'s own `updateNote()` finds the seed by its
`.no-js-note` class (`card.querySelector(".no-js-note")`) and
REWRITES it in place on the first poll tick rather than creating a
second element — the seed exists so a JS-enabled reader sees real
progress text from the very first paint, before that first tick can
land, instead of a blank pending `<article>` for one round trip. Only
a browser with scripts disabled ever shows the `<noscript>` content
at all; a finished, failed, or cancelled card never reaches this
branch, so neither line survives past the turn it describes.

**`chat-turn` gained its real body in Task 9** (see "Posting a turn"
below); **`chat-conversation-delete` was a placeholder route from this
task**, mounted so the delete disclosure had somewhere real to post
to, until Task 11 gave it its real body (see "Error surfaces and
deleting a conversation" below); see the URL table above for the full
history.

## Polling a turn (Task 10)

`turn_status` (`GET /chat/turns/<int:turn_id>/`, `agents/chat/views/
turns.py`) is the 202's other half — the `status_url` `turn_create`
hands the poller. It is **always 200 for a readable turn**: queued,
running, done, failed and cancelled all report their state in the
response *body*, never the HTTP status (`AskJobStatusView`'s rule,
`tools/rag/views.py:1150-1160`, applied to a turn). The turn is looked
up through `visible_conversations` exactly like `ConversationView`, so
a foreign or unknown turn id is a real 404. Exactly one other non-200
exists: **503**, when the queue itself cannot be read (`QueueUnavailable`)
for a turn still in flight.

The body is keyed on `Turn.State`, not the queue's vocabulary:

| `Turn.State` | body |
|---|---|
| `queued` | `{"state", "position", "priority", "html"}` |
| `running` | `{"state", "progress", "step", "label", "html"}` — `progress` verbatim; `step` is `progress["done"]`; `label` is `progress["label"]` |
| `done` | `{"state", "html"}` — every card the job wrote (M6), not one |
| `failed` | `{"state", "error", "setup_url"}` — `setup_url` on every failure |
| `cancelled` | `{"state", "error"}` — deviation P3-D9 |

**`html` on `queued`/`running` too, since D1 (chat-polish P3.1).** It
used to appear only on `done`; the poller's own JS updated a plain text
note for the two earlier states and inserted nothing until the turn
finished. That silently dropped the USER's own message from every state
the poller ever showed — see "The `html` group is the whole exchange,
not just the job's output" below.

`_BODY_BUILDERS` is a dict keyed on the five `Turn.State` values, and
`agents/chat/tests/test_turn_status.py::TestTheStateVocabulary::
test_the_five_states_are_the_only_ones_it_can_report` pins it **total**
over `Turn.State` — a sixth state added later fails there, where the
body shapes are decided, rather than falling through to a `done` body
with no `html` in it.

**Turn states are not queue states.** The queue's own vocabulary is
`queued`/`running`/**`succeeded`**/`failed`/`cancelled`
(`models/contracts/queue.py:69-80`); a turn's is
`queued`/`running`/**`done`**/`failed`/`cancelled`
(`agents/models.py::Turn.State`). This is not a stylistic choice: P2's
ledger records a real, live 960-second hang (2026-08-28) caused by
exactly this drift — a CLI poll loop compared a queue job's state
against the literal `"done"`, which the queue's vocabulary has never
had, and the loop spun until its own timeout. `turn_status` reads the
**turn row first** — the durable record — and hands its own `state`
through untranslated; the queue is consulted only to *enrich* a
non-terminal turn with a position or a progress dict, and never at all
for a `done`/`failed`/`cancelled` turn. `tools/vision/views.py::
queue_job_status`'s "THE GENERATION FIRST, the queue second" is the
same lesson for the same concrete reason: a queue row is pruned to
`JobSettings.retention_limit` on every enqueue, so a finished job can
vanish from under a card that is still polling it, while the turn row
never does.

**The `html` group is the whole exchange, not just the job's output
(M6, extended by D1).** `_group_html(turn, request)`
(`agents/chat/views/turns.py`) renders `chat/_turn_block.html` over
`agents.chat.rendering.turn_group_cards(turn)` — every `queued`/
`running`/`done` body calls it — and `turn_group_cards` is the ONE
place that assembles the group: the USER turn that provoked this
job (found as the nearest preceding `Turn.Role.USER` row) PLUS
`thread_cards(conversation, queue_job_id=turn.queue_job_id)`, the SAME
function, over the SAME rows, that `thread_context` calls for a full
page render. A finished turn is the USER's own message, the TOOL cards
the loop wrote, *plus* the assistant answer; swapping in anything less
would leave the polled thread showing strictly less than the same page
shows after F5. `turn_group_cards` stamps the user card's own
`poll_block` key to the SAME job id as the rest of the group (that
row's real `queue_job_id` column is always `NULL` — the USER turn is
written before any job exists), which is what lets the poller's
idempotent swap (below) remove the stale copy of the user's bubble
along with the stale tool/assistant cards on every re-swap, never
leaving two behind. A turn with no `queue_job_id` (never true in
practice — `service.start_turn` stamps every placeholder right after
enqueue) renders itself alone; grouping it with a nearby USER turn
found by index alone would not be grounded in anything the row itself
records.

**The inline `<script>` at the bottom of `conversation.html` is
progressive enhancement only.** The page above it already works with
JavaScript off: the message form is a plain POST, and its answer is a
redirect carrying `?pending=<turn_id>`, which the page renders as a
pending turn for the operator to refresh. With a script running, the
same submission goes through `fetch()` instead: a 202's `html` key
(D1) is inserted into the thread WHOLE — the user's own bubble and the
pending placeholder together — and polling starts on `data.status_url`
(never a URL the script builds itself); a 4xx/5xx renders the body into
`#turn-errors` and never into the thread, so a rejected message can
never be mistaken for a sent one. Every `queued`/`running` poll tick
re-swaps the SAME group `data.html` carries (`swapBlock`, the same
idempotent "find every node sharing `data-poll-block`, remove the
stale copies, insert the fresh group" logic Task 10 built for the
`done` swap, now shared by every state) — so a tool call that finishes
mid-poll, or the user's own message, shows up without waiting for the
turn to finish. **U4 (light):** the Send button and the textarea are
disabled for the duration (`setFormBusy`, called on a successful
insert/bootstrap and undone by `showCardError` and a successful `done`
swap — never on the 10-minute ceiling or a lost-contact give-up, since
the turn may genuinely still be running server-side), and a CSS-only
pulsing dot (`.pulse-dot`, `@keyframes chat-pulse`) sits beside
"Working — …" while `running`. No cancel button: a started job can't be
stopped (`/queue/`'s own rule). The poll loop mirrors `tools/rag/
templates/rag/ask.html:355-359`'s own tuning — 2000 ms between polls,
three transport retries, and a ten-minute ceiling — read from
`POLL_INTERVAL_MS`/`MAX_TRANSPORT_RETRIES`/`MAX_POLL_DURATION_MS` in
`agents/chat/service.py` (declared once, in Python, and handed to the template by
`thread_context`) rather than typed a second time into the script. The
bootstrap resumes polling with a SINGLE scan (chat-poller-cleanup): it
finds every rendered `.turn-pending[data-status-url]` card and starts
a poll loop on each, reading its own `data-status-url` (seeded per
card by `_turn_card.html`, only while `card.pending`) rather than
building a URL client-side. That one scan covers the no-JS redirect's
own pending turn identically to a plain refresh's, because both render
as the SAME `.turn-pending[data-status-url]` shape — there is no
separate redirect-only branch left to converge with. The wrapper's
`data-pending-turn`/`data-pending-status-url` attributes still render
on `.thread` regardless: they document the redirect's server-side
contract (`thread.py` still supplies them) even though the script no
longer reads them to decide anything.

Prior art, pointer only: the per-card resume scan above is the same
shape as `tools/vision/templates/vision/create.html`'s `data-job-poll`
scan. This poller descends from `tools/rag/templates/rag/ask.html`'s
(the `POLL_INTERVAL_MS`/`MAX_TRANSPORT_RETRIES`/`MAX_POLL_DURATION_MS`
tuning comparison above is the same lineage) but has deliberately
forked from it since — its own tuning constants read from
`service.py` rather than typed into the script, and the per-card
resume this section describes — so this is a note of the resemblance,
not a call to re-unify the three.

## What a card knows

`agents/chat/rendering.py` decides, once, over rows: (1) an open
`ToolInvocation` (`finished_at IS NULL`) reads as
`agents.runtime.audit.RUNNING`, never as its placeholder
`outcome=ERROR`; (2) a failed call's message comes from
`ToolInvocation.error`, never `.text`, via `invocation_message`; (3)
`reverse()` on a feature-gated artifact URL name
(`vision-output-file`/`vision-input-file`) can raise `NoReverseMatch`,
caught in exactly one place (`rendering._url_for`), so vision-off
installs still render the artifact as plain text; (4) RAG citations are
read from `data["citations"]` OR `data["results"]` (deviation P3-D6),
never the citations key alone. `agents/runtime/audit.py` is the sole
authority for judgements (1) and (2); `rendering.py` never reads
`ToolInvocation.outcome`/`.text` directly, and the templates — `chat/
_turn_card.html`, `chat/_tool_card.html` — do no thinking of their own:
they iterate the card dicts `rendering.py` already decided.

**An assistant turn's text renders a safe markdown subset, never raw
(chat-polish P3.1, U2).** `rendering.render_answer(text) -> SafeString`
escapes `text` FIRST (`django.utils.html.escape`), so no raw HTML the
model wrote — an XSS-shaped `<script>` included — ever reaches the page;
formatting (`**bold**`, `*em*`, backtick code spans, `> ` blockquotes,
`- `/`* `/`1. ` lists, fenced ``` blocks) is recognised SECOND, over the
already-escaped text, by regexes that only ever emit the `<strong>`/
`<em>`/`<code>`/`<ul>`/`<ol>`/`<blockquote>`/`<pre><code>` tags this
function itself writes. Three or more consecutive newlines collapse to
one paragraph break. **No links are ever auto-created** — no `<a>` comes
from a bare URL or `[text](url)` syntax — and this is deliberately NOT a
markdown library: a small, fixed, testable grammar, not a dependency.
`turn_card`'s `text_html` key holds the result for an ASSISTANT turn
only (`None` for `USER`/`SYSTEM`, which `_turn_card.html` falls back to
`text|linebreaks` for) — a user's own typed message is not something
this page reformats.

**A tool card collapses its own raw output (chat-polish P3.1, U1).**
`tool_card`'s `message_collapsed` key (`len(message) >
TOOL_OUTPUT_COLLAPSE_THRESHOLD`, 400 characters) tells `_tool_card.html`
whether to put `tool.message` behind a closed-by-default `<details>`
("Show output") — the decision is made once, in `rendering.py`, never
in the template. Citations and images render ABOVE the (possibly
collapsed) message: `Search the library` used to print the entire
concatenated result text, `<EOS> <pad>` tokens included, above its own
citation list, burying the structured answer under the raw one.
Argument rows split the same way — `_split_args` puts every row whose
RAW value is `None`/`""`/`[]`/`{}` behind a second, separate "Unused
arguments (N)" disclosure (renamed from "All arguments" in fix round
1's R3 — `N` is the HIDDEN row count, and the old label read as if it
were every argument the call declared), and shows everything else
(`args`) inline; a union `ToolSpec`'s call can carry twenty parameters
an operation like `upscale` never uses, and rendering all twenty made
the one or two that mattered hard to find.

**`turn_card`'s `files` key IS rendered — on the answer card only (fix
round 1's R6).** `_turn_card.html`'s non-tool branch includes
`chat/_artifact_files.html` with `files=card.files`; `_tool_card.html`
does not include it at all. A turn's `artifacts` column is the UNION of
every tool call the job made (`agents/runtime/loop.py`'s own `_finish`
extends one running list across the whole loop and saves it onto the
final ASSISTANT row), so a `document:<id>` a search tool cited was
ALREADY on both the TOOL row and the eventual ASSISTANT row — rendering
`files` from both cards showed every source document twice, once per
card. The tool card's own `tool.citations` list already links the same
document with a title, locator and score — strictly more than a bare
file link — so keeping the list on the answer card (the turn's own
sources) loses nothing. `_artifact_files.html` itself now heads the
list with a visible "Files" label (R2 — the template's own comment
claimed one long before any template actually rendered a label at all)
and shows each document's own TITLE rather than its bare `document:<id>`
reference (R5 — see "A document's file link shows its title" below).

**A document's file link shows its title, not a bare `document:<id>`
(fix round 1, R5).** `agents.contracts.artifacts.mint_artifact(kind, pk,
title="")` is the one place a `"document:<id>"` reference may grow a
`:<url-quoted-title>` suffix; `tools/rag/tools.py::_document_artifacts`
calls it with each citation's own `title` (`retrieval._search_result_
for`/`_vector_citations`'s `node_metadata.get("file_name") or
"(untitled)"`) AT THE MOMENT the reference is minted — the one point in
the whole pipeline this tool column can reach the title, since
`agents/chat/rendering.py`'s own module docstring forbids this app
importing anything from `tools.*` to look it up later. `parse_artifact`
still returns only `(kind, pk)` (the title suffix is `document`-only —
`output`/`input` still refuse a third `:` segment outright, unchanged),
and the new `artifact_title(reference)` is the one place that reads the
title back. `rendering.artifact_links` sets each file entry's `title`
key from it, falling back to `f"Document {pk}"` for a reference minted
before this addition or with no title recorded. Only `document` ever
gets a `title` this way; an image entry's `title` is always `""`
(images render as thumbnails, never a named link).

**Every image in a turn is capped to its card (round 2).** An image in
an assistant reply used to paint straight out through the card's right
edge — visible in the owner's live proof. The cause was a **missing
wrapper, not a missing markdown rule**: `_tool_card.html` wraps its
`_artifact_images.html` include in `.tool-images`, which carried the
`max-width`, while `_turn_card.html` includes the *same* fragment for an
answer card's own images with no wrapper at all, so those `<img>`s
inherited no constraint from anywhere and rendered at their intrinsic
pixel size. The guard is `.turn img { max-width: 100%; height: auto; }`
— scoped to `.turn` rather than to a wrapper class, which is what makes
it hold for the answer card, the tool card, a nested delegated turn, and
the next include that forgets a wrapper. It sets no `max-height`, so
`.tool-images img`'s own 360px thumbnail ceiling still governs the grid
it was written for.

Note that **`render_answer` never emits an `<img>`**: it escapes first
and recognises only bold, em, code spans, blockquotes, lists and fenced
blocks, so literal markdown image syntax in a reply stays literal text.
The images an assistant turn actually shows are its artifacts, which is
what this rule covers.

**An image card links to its own submission (UI-3c).** The owner's
words: *"for image generation in the chat, there should be a button to
take me to the vision page and to that submission directly. currently it
just shows me the image."* The tool card for an image generation now
carries a **View in Gallery** link to
`{% url 'vision-gallery' %}?job=<uuid>#job-<uuid>` — the filter makes it
LAND on the submission when it is far down the list, the anchor scrolls
to it once it is there. (The `?job=` filter and the per-figure anchors
are the image surface's own shipment; this href is correct either way —
without them it is the gallery's first page and a missed anchor, and it
becomes precise the moment they land.)

The uuid is read from the STRUCTURED result — `Turn.data["id"]`, which
is `services.job_json`'s payload, the same object the card's closing
sentence was formatted from — never by parsing that sentence back apart,
which would let a copy edit in another column silently break the link.
It is **not** `data["queue_job_id"]`, which is in the same payload and
is a different row in a different table. The value is validated as a
uuid before it is spliced into a URL, the same guard and the same reason
`_document_url` puts `isdecimal()` in front of its own id.

**Its feature guard is a `NoReverseMatch`, not a context boolean, and
that is the load-bearing part.** `vision-gallery` is mounted only while
`"vision"` is in `FARABUNKER_FEATURES`, and a conversation outlives a
feature flag — an unguarded `{% url %}` in the card would raise
mid-render and 500 a whole historical thread on a box where the flag was
later turned off. Caught in `rendering._generation_url`, the second site
on truth 3's existing rule, which is what makes the guard hold for
`turn_status`'s poll body as well: that view renders this same card
standalone, with no page context a boolean could have travelled in.
Deliberately NOT `surface_available.images`, which answers a different
question — whether an image MODEL is bound — and would wrongly hide the
link to a real, finished generation on a flag-on box whose binding was
since removed. Flag off, no job id, or any other tool: all three are the
same `""`, so the template asks one question and the card renders
exactly as it did before the button existed.

The generator's tool key is matched **by value** (`"vision.generate"`),
because `agents/` may not import `tools.*` at all (import-law rule 3) —
the same reason `artifact_url_name` maps an artifact kind to a URL name
by string rather than by import.

## Error surfaces and deleting a conversation (Task 11)

Most of the behaviour below shipped in Tasks 9–10. This task made it
*visible*, gave it shared copy, and pinned the two paths that are easy
to get subtly wrong: an XHR error's exact body, and what a delete does
and does not erase. `agents/chat/tests/test_errors.py` and
`agents/chat/tests/test_delete.py` are the pins.

### Spec section 10.1, mapped onto this tree

| Failure | Where caught | What the operator sees |
|---|---|---|
| Blank message | `service.start_turn` (400) | XHR: `chat/_form_errors.html`, a bare fragment for `#turn-errors`. Non-XHR: the whole thread page re-rendered, same message, same slot — never a bare fragment (the counterpart of `tools/vision/views.py:836`'s own named test) |
| Chat role unbound, or the picked connection no longer resolves | `agents.runtime.preflight.preflight_turn` | GET: the thread still renders 200, with `chat/_unavailable.html` naming the role and, **for an administrator only**, linking to `inference-console` (bind a model) and `setup-index` (the general walkthrough). POST: 503 with the identical sentence — **two different responses for one condition**, because reading is not queueing |
| Bound model cannot call tools this agent needs | `preflight_turn` | Same two shapes as above; the sentence names the **role**, never the model id |
| `AGENT_NOT_PERMITTED` — a member restricted from an entitlement-labelled agent | `preflight_turn` | The SAME `chat/_unavailable.html` fragment as the two rows above (`_AGENT_NOT_PERMITTED_MESSAGE`, ADR 0010's third amendment). IA-2 walkthrough finding W-4: the two links are gated on `identity_is_admin` in that one shared template rather than forked into a second fragment, because a member restricted by a label — the common case reaching this row — can act on neither `inference-console` (class S) nor a bound-model fix; an administrator viewing the identical banner for a genuinely unbound role still gets both |
| Queue tables missing or down | `service.start_turn` (`QueueUnavailable`) | POST: 503, "run database migrations" copy. GET: unaffected — a queue outage is not a reason to hide history, and `thread_context` never touches the queue at all |
| A granted tool key that is not registered on this install | `preflight_turn`'s tolerant drop (spec 8.3 step 3) | A NOTE, not a refusal: the 202 XHR body's `notes`, and the identical sentence on the thread page after a no-JS redirect — `thread_context` recomputes the same `preflight_turn(agent, connection, actor=...)` call `start_turn` just made, so the two can never disagree |
| Tool arguments fail validation / a tool raises / a tool is refused | `agents.runtime.invoke` | a tool card, `str(exc)` never a traceback (`agents/runtime/audit.py` is the sole authority on how it reads) |
| `run_turn` raises after it started | `run_turn`'s own `except` | `Turn.error` shown verbatim on the failed card, plus a link to `inference-console` — `_failed_body`'s JSON puts a setup link on **every** failure, not only model-shaped ones |
| Turn cancelled from `/queue/`, or orphaned twice | `agents.runtime.jobs.on_turn_terminal` | the cancelled card shows the exact sentence the hook wrote (`"Cancelled from the queue before it ran."`) — `test_errors.py`'s own test drives the real hook rather than hand-writing the row, so the hook's copy and the page's rendering are pinned together. **No setup link** (chat-polish P3.1, U6): `_turn_card.html` used to put the same "Models" link under a cancellation as under a genuine failure, which reads as a non sequitur — a cancel from the queue is never a reason to bind a model. The link stays on `failed` only; `_cancelled_body`'s JSON also carries no `setup_url` (unlike `_failed_body`'s) |
| An unknown conversation or turn id | `visible_conversations` + `get_object_or_404` | 404, never a 500 |

**Never a traceback, anywhere.** `test_errors.py::TestNeverATraceback`
sweeps every surface above for the literal strings `"Traceback"` and
`'File "'` and asserts neither appears.

**`thread_context` runs `preflight_turn` on every GET**, which for an
agent with granted tools and a bound connection can mean a real,
bounded (~5 s, degrading to `None` on any failure) call to the
engine's own `supports_tool_calling` probe on every page load — a
per-request TTL cache in front of that probe is a backlog item, not
built here.

### `conversation_delete`, and the asymmetry that matters

`POST /chat/c/<uuid>/delete/` (`agents/chat/views/conversations.py`)
looks the conversation up through `visible_conversations` (ruling 4c,
so a bad id is a real 404) and calls `.delete()`.

**The turns go with it.** `Turn.conversation` is `on_delete=CASCADE`
(`agents/models.py`), so every turn in the conversation is deleted in
the same statement.

**The audit does not.** `Turn.invocation` is `on_delete=SET_NULL` —
but the direction that actually matters here is that `ToolInvocation`
holds no foreign key back to `Turn` or `Conversation` at all; it is
`Turn` that points at it. Deleting a conversation's turns therefore
never reaches the `ToolInvocation` table by any cascade path — every
row this conversation's tool calls produced survives, `outcome`,
`principal_key`, `text`, and `error` all intact, exactly as the
2026-08-27 addendum's consequence 3 designed it: the audit row is not
owned by the conversation table, precisely so deleting a conversation
can never erase the record of what was called. `test_delete.py::
TestTheAuditSurvives` pins both an OK and an ERROR invocation surviving
with their fields unchanged.

**The agent is untouched.** `Conversation.agent` is `PROTECT` in the
*other* direction — an agent can never be deleted while a conversation
still references it — and deleting the conversation does not touch the
agent row either; `test_delete.py::TestTheAgentIsUntouched` pins it.

**The index confirms it (chat-polish P3.1, D4)** — `conversation_delete`
calls `django.contrib.messages.info(request, "Conversation deleted.")`
before its redirect, and `chat/index.html` renders `{% if messages %}`
the same way `tools/rag/templates/rag/documents.html` and
`rag/history.html` already do (the framework is installed platform-wide
— `config/settings.py`'s `MIDDLEWARE`/`INSTALLED_APPS`), rather than a
bespoke `?deleted=1` query flag. Before this, the row simply vanished
from the list with nothing telling the operator the click had worked.

**The control on the page** is a `<details>` disclosure in
`conversation.html`, modelled on `tools/vision/templates/vision/
_delete_control.html`: click "Delete this conversation" to reveal the
real POST form and a "Yes, delete" button, or "Cancel" (a one-line
`onclick` that is inert without JavaScript) to close it again. No
`confirm()`/`alert()`/`prompt()` anywhere.

## The conversation sidebar (UI-3b)

Every page under `/chat/` that shows conversations shows the SAME
sidebar — `chat/_sidebar.html`, built by `agents/chat/sidebar.py` and
included by `chat/index.html` and `chat/conversation.html` alike. That
is the whole point of the change: clicking from one thread to another
never goes back through a landing screen.

**It is server-rendered HTML and CSS, and the rail's own markup carries
no script.** The per-conversation menus are native `<details>` elements
and every action inside them is a plain CSRF-protected POST form — the
same idiom the thread page's delete confirm and the setup page's
platform disclosures already use. With JavaScript off, every one of
them still works.

**The one exception rides the fragment, after the `</nav>` it operates
on**: `chat/_menu_exclusive.html` (round 21) makes the rail's menus
behave like drop-downs — one open at a time, dismissed by an outside
click or Escape. It is a progressive enhancement, not a dependency, and
its mechanism and trade-offs live in that fragment's own comment rather
than here. Because it rides `_sidebar.html`, it renders on every page
that shows the rail, so every page that shows the rail carries at least
one `<script>`; each page's count is pinned by its own test. The chat
pages that do NOT show the rail are still script-free — the three that
extend the settings shell (`/chat/settings/`, `/chat/tools/`,
`/chat/access/`) and the dormant-workstream fence, which overrides
`content` outright.

**The per-row `may_manage` question reads the identity singleton
ONCE, not once per row (fix round 1).** The sidebar asks
`may_manage_conversation` for every listed conversation, which is
exactly the caller `identity.access.sees_all_content`'s own
`settings_row=` docstring names as the one that must not re-read
`IdentitySettings` per row — and it was worse than one extra read each,
because `identity.access._user_row` memoises on the settings-row
*instance*, so a fresh read per call defeated the `identity_user` cache
too. Measured on an enterprise box: 15 queries at one conversation, 63
at twenty-five — exactly +2 per row, on the two most-trafficked pages in
the app. Both callers now pass
`identity.request.settings_row_for(request)` — the row
`IdentityGateMiddleware` already read for this request — through
`sidebar_context` into the predicate, which is what
`models.queue.views.QueueView` already does for its own per-row content
check. It is flat at 14 queries whether the list holds one row or
thirty, pinned at both ends by `test_sidebar.py::
TestItCostsTheSameAtOneRowAndAtThirty` (pinned to `POSTURE_ENTERPRISE`,
because on an open box `sees_all_content` short-circuits and the pin
would pass against the very defect it exists to catch). One residual,
bounded and deliberate: `may_read_owned_row`'s service-owned branch
calls a bare `is_admin`, so a list made entirely of shell-path rows
would still cost one read each — that signature is shared with
`tools/vision`, so widening it is a cross-column question rather than
part of this fix.

**The builder lives in `agents/chat/sidebar.py`, not in either view
module**, for the reason `views/__init__.py` gives about the poller's
constants: `conversations.py` and `thread.py` both need it, and
`thread.py` importing `conversations.py` would be an import back up a
one-way chain. It reads rows through `visible_conversations` — the one
list gate, ruling 4c — and adds one `.filter()` for the archive.

**`SIDEBAR_LIMIT = 30`, and the cap says so out loud.** The thirty most
recently touched conversations render, and when there are more the
sidebar names how many older ones it is not showing ("7 older
conversations") instead of silently stopping. No pagination in v1;
archiving is the gesture that shortens the list.

**Titles are one line, ellipsized by CSS — and hovering shows the
whole thing.** `Conversation.title` is already word-boundary-truncated
with a trailing "…" at write time (`service.truncate_title`), so the
sidebar re-truncates nothing in Python — `text-overflow` does it, and
the full stored title rides along in the entry's `title=` attribute, so
a native tooltip (zero JS, autoescaped like any attribute value) shows
what the column cut off. An untitled row's tooltip says "Untitled
conversation" rather than being empty, matching the words the link
itself shows. The CURRENT row needs no separate handling: this sidebar
keeps it a real `<a>` — deliberately not taking the settings nav's
`pointer-events: none`, because clicking the conversation you are
already reading is a real gesture that drops `?connection=` /
`?pending=` — so one attribute covers both states.

### The ⋯ menu, and who gets one

**The panel is an OVERLAY (round 2, owner's ruling).** It shipped in
flow, which pushed every row below it down the list; the owner's words
were *"a menu that is above, not something that pushes all the other
html down"*. It is `position: absolute` on the row's own `<details>`
now (`top: 100%; right: 0; z-index: 5`), styled as a floating card —
`--panel` background, a `--border` hairline, a radius — which is the
same token recipe the one other floating panel on this box
(`tools/vision/.../create.html`'s `.tip`) already uses. Closing the
menu now costs no layout shift at all. Still zero JS: absolute
positioning changed the CSS only, the panel is the same markup inside
the same `<details>`, the native toggle still opens and closes it, and
the nested delete confirm still works. One-open-at-a-time is still not
enforced.

**It used to clip nothing, checked rather than assumed** — the question
the in-flow version was originally chosen to dodge, at a time the whole
page scrolled as one and the 30-row cap meant the sidebar never grew its
own scrollbar. **The owner-requested chat layout fix (below) changed
that**: `nav.chat-nav` now scrolls independently of the page above
`1000px`, which makes it a clipping context for an absolutely positioned
child near its bottom edge, exactly as the warning that used to sit here
predicted.

**The fix does NOT reopen option (c) that warning named** — dropping
the panel to an in-flow block would reverse the STANDING OWNER RULING
directly above ("a menu that is above, not something that pushes all
the other html down"), which governs until the owner changes it. A
first pass here did exactly that; caught in review and reverted.
`chat/base.html`'s `.chat-menu-body` rule is UNCHANGED: still
`position: absolute; top: 100%; right: 0; z-index: 5` at every width,
zero row shift, exactly the overlay this section describes above.

**The fix is on the clipping container instead**, gated by CSS `:has()`
to the one moment it matters: `nav.chat-nav:has(.chat-menu[open])`
drops the LIST's own `overflow-y` to `visible` while any one of its
menus is open, above `1000px` only (below it the sidebar stays
unscrolled document flow, so the rule has nothing to do there either).
Lifting the clip on the ancestor, rather than changing the panel's own
positioning, is what keeps the overlay ruling intact — closing the menu
(the native `<details>` toggle, same as always) restores the clip with
nothing to undo by hand. `:has()` is Baseline-supported across every
engine this box targets, the same standing this file's `color-mix()`
(the panel's own `box-shadow`, described above) already has.

**The trade, said out loud:** `overflow-y: visible` does not merely
unclip the panel — for as long as a menu stays open, it stops the WHOLE
list from scrolling, so on a list taller than the shell, rows outside
the current viewport can spill past `nav.chat-nav`'s own edges too.
That is judged the more faithful reading of the ruling (the menu is not
supposed to move anything, and this is what keeps it from moving) over
letting the panel itself go in-flow — and it is TRANSIENT and
OPEN-MENU-ONLY, gone the moment the menu closes.

Each entry carries a `<details>` menu with **Rename** (a nested
disclosure revealing a small POST form prefilled with the current
title), **Duplicate**, **Pin**/**Unpin** (round 20, owner: "can we add
the ability t[o] pin chats"), **Archive**/**Unarchive**, and **Delete**
(the existing `chat-conversation-delete` route behind the existing
`.delete-confirm` reveal). Every entry beside Delete is its own
POST-only, row-addressed route, classified **`O`** in
`identity/routes.py` beside the delete they sit next to.

**One predicate gates every menu action: `agents.visibility.
may_manage_conversation`.** It was `_may_delete` when delete was the
only mutation; the body is unchanged and the name now says what it
gates — round 20 added pin/unpin to this SAME predicate rather than a
new one, the identical "one gate, not one per action" rule that already
covered rename/duplicate/archive/unarchive. `sees_all_content`, or
`may_read_owned_row` — so:

* the owner may do every one of them;
* an administrator may, once `admin_sees_content` is on, and gets a 404
  while it is off — the same cell the route matrix asserts for every
  other class-`O` route;
* **a recipient of a share may not do any of them.** A `use`-level
  share is the widest this platform grants and it still only means
  "read this thread, and post into it" (`may_post_to`). Renaming,
  duplicating, pinning, archiving or deleting somebody else's thread is
  refused with 404 — never 403, because a 403 on a row-addressed URL
  confirms the row exists. The sidebar therefore renders **no menu at
  all** on a shared row rather than a set of controls that would each
  404, which is the render-vs-gate rule this surface already applies to
  its compose
  form.

**Duplicate copies the finished history and nothing else**
(`agents.visibility.duplicate_conversation`). The copy is owned by
whoever pressed the button, not by the original's owner. Three things
are deliberately left behind, each for its own reason:

* **any turn not in a terminal state.** A `queued`/`running` turn is
  mid-flight in the ORIGINAL's `agent.turn` job, which will write its
  answer back to the row it was handed; a copy of the placeholder could
  only ever sit in the new thread as a turn that never finishes.
  `failed` and `cancelled` ARE terminal and do come across — "terminal"
  is not "successful", and a tidied-up copy of a conversation that did
  not go that way would be a lie.
* **`Turn.invocation`.** A `ToolInvocation` is an audit record of a call
  that really happened, in the original thread, by a named principal
  (the 2026-08-27 addendum's consequence 3). Pointing a copied turn at
  it would file one audit row under two conversations; writing a second
  would invent a call that never ran. The copy keeps the tool card's own
  visible content and carries no audit link.
* **`Turn.queue_job_id`**, which names the job that produced the
  original row. A copy was produced by nothing.

**A duplicate made under `admin_sees_content` is a durable copy the
toggle no longer governs.** The copy is stamped with the ACTING
principal's owner columns, so an administrator who duplicates somebody
else's thread while the content toggle is on owns the result *by
ownership* — and ownership is not what that toggle gates. Switch it back
off and the original becomes unreadable to them while the copy stays.
That is accepted rather than overlooked: reading the thread is precisely
what the toggle permitted, anyone who could read it could already paste
it anywhere, and the original is untouched with its audit rows intact.
The alternative — stamping the copy with the ORIGINAL's owner — would
file a row under somebody who never asked for one, which is worse.

Indexes are renumbered from 0 over the surviving turns, so a copy with a
dropped mid-flight turn has no gap in it. The title is `"Copy of
<title>"` through `service.fit_title`, which spends the truncation
budget on the TITLE and never on the prefix — truncating the whole
string as one would find its only word boundary right after "Copy of".

### `archived_at`, and the archive view

`Conversation.archived_at` (migration `0005`) is a **timestamp, not a
boolean**: `null` means active and a value records when the row left the
list. A boolean would answer the first question and throw the second
away, and "when did this leave my list" is the one thing an operator
asks about an archived row.

Archiving destroys nothing and is reversible in one click. The thread
keeps every turn and stays readable at its own URL — it simply leaves
the default sidebar. That is the whole difference from delete, and it is
why the two sit next to each other in the same menu.

**The archive is the same page with the filter inverted:
`/chat/?archived=1`.** Not a second route and not a second template —
an archived conversation is not a different kind of thing, and a second
page would have been a second copy of the sidebar to keep in step.
Anything but exactly `archived=1` is the active list, so a hand-typed
query string can only ever land on one of the two lists that exist. The
active sidebar links to it as "Archived (N)", and only when N > 0; the
archive links back to the active list. Menus there offer **Unarchive**
and **Delete** only. Reading an archived thread renders the ARCHIVE's
sidebar, so the page you are on is always a row the sidebar can mark.

`updated_at` moves on rename and on archive/unarchive (it is `auto_now`),
so a renamed thread jumps to the top of the list. That is accepted
behaviour: the ordering column is "last touched", and both are touching
it.

### Where the actions land

Rename, archive and unarchive return to the page the form was posted
from, carried in a hidden `next` field and validated same-origin with
`url_has_allowed_host_and_scheme` — the same guard
`tools/vision/views.py::_validated_next_url` puts on its own delete
forms, because a `next` value is caller-supplied and an unchecked one is
an open redirect off this box. The sidebar is on two pages, so "where
does this land" genuinely has two right answers.

The field carries `request.get_full_path`, **not** `request.path`: on
`/chat/?archived=1` the bare path is `/chat/`, so renaming an archived
thread used to return the operator to the ACTIVE list — a list the row
they had just renamed is not even in. Renaming does not change which
list a conversation belongs to, so the answer must not change which list
you are looking at. Still relative, still same-origin, and
`_validated_next_url`'s check accepts it unchanged.

**Duplicate ignores
`next` and lands on the copy** (a duplicate the operator cannot see is
one they will make twice); **delete keeps its existing redirect to
`chat-index`** with its existing flash.

### Layout

Two columns on both chat pages, opt-in through a single
`{% block chat_sidebar %}` on `chat/base.html`. Flexbox rather than the
settings layout's grid, so that one block is the whole opt-in rather
than a block plus a column-template swap. **There is no sidebar-less
consumer of this base left** — `/chat/tools/` and `/chat/access/` moved
onto `_settings.html` in UI-2, and the only two templates that still
extend `chat/base.html` both fill the block — so the empty block is
headroom for a future page, not a live case being served.

**The sidebar is paid for out of new space, not out of the thread (fix
round 1).** Three numbers: `--chat-pane-width: 900px` (the thread's own
reading measure, unchanged from before this surface had a sidebar),
`--chat-nav-width: 240px`, and `--page-max-width: 1180px` — the sum,
plus the 2.5rem gutter. Left at the old 900, the block would take
`max(900px, 1000px)` = 1000px and the pane would come out at **720px**,
a 180px narrowing of the one column people actually read. Raising the
page token is the console's own pattern (`inference/console.html` sets
1730 for its grid), and the app bar takes the same `max()`
(`_shell.html`), so the bar widens with the block and the two keep
sharing their edges. Below ~1212px the block is viewport-capped anyway,
so a laptop sees what it saw before. At the shell's own `max-width:
1000px` breakpoint the sidebar stacks above the pane, the same way the
settings sidebar does.

**This arithmetic assumes exactly ONE extra 240px column, and for a
stretch it quietly stopped being true.** The F1/F3 walk-fix batch later
added a SECOND `--chat-nav-width` flex item (`nav.workstreams`, pinned
alongside `nav.chat-nav` to stop a real stream name from squeezing one
word per line) without revisiting this sum — two 240px columns plus the
900px pane is 1140px of CONTENT the 1180px block never budgeted the
gutters for, and is exactly the shape of the owner's chat layout fix
round 2 complaint below ("the two side-by-side sidebar columns...eat too
much space"). Folding Workstreams and Chats into the ONE `nav.chat-nav`
column that round 2 ships makes this sum true again, the same as it was
before the walk-fix batch.

It shares the shell's design language with `_settings.html` — the group
label, the `.current` treatment, the breakpoint — and reuses none of its
markup. The settings nav is a fixed list of sections; this one is a live
list of rows with a menu on each.

### One sidebar column, an accordion, and 5 (chat layout fix round 2)

**The owner's words:** *"workstream needs to be stacked over chats. and
it needs to be like an accordian and show the 5 most recent ones and the
ability to see more or collapse. this takes up too much space."*
Screenshot: the two side-by-side sidebar columns, Workstreams and Chats,
eating roughly half the page.

**`_sidebar.html` now renders exactly ONE top-level `<nav class="chat-
nav">`**, not the three siblings (`nav.workstreams`, a bare "Chats"
heading, `nav.chat-nav`) the walk-fix batch's own F1/F3 rules each gave
their own `--chat-nav-width` flex column. Workstreams is stacked ABOVE
Chats, both inside the same element — so there is nothing left for a
second column to occupy, and `chat/base.html`'s F1/F3 rules are removed
entirely (they named an element, `nav.workstreams`, that no longer
exists) rather than left as dead CSS.

**HISTORY, SUPERSEDED BY ROUND 14, ITSELF SUPERSEDED BY ROUND 15 — see
"ROUND 15 (owner feedback): top bar restored, rail slims to chat
domain — the CURRENT shape" below for the current shape.** At the time
this round shipped,
the whole column shared ONE `overflow-y: auto` the app shell section
above gave it, and a stream-scoped sidebar variant existed
(`sidebar_workstream` set collapsed the column to two lines — the
stream's own name and an "All chats" exit — instead of the Workstreams
accordion). Round 14 retired the scoped variant entirely (`sidebar_
context` no longer accepts a `workstream=` keyword at all — the ONE live
reference to `sidebar_workstream` left anywhere in the tree is a test
asserting the key is gone) and moved the scroll region from the whole
column onto `.chat-nav-chats` alone, one section among several now
rather than the column's only content.

**The accordion is a native `<details open>`, zero JS**, collapse and
re-expand both the browser's own toggle. `+ New` is `<summary>`'s
SIBLING, never its child: a `<summary>`'s activation behaviour toggles
its parent `<details>` on ANY click that reaches it, nested `<a>`
included, since nothing here can call `stopPropagation()` on that click
first — a link inside `<summary>` would fire its own navigation AND the
toggle, and nesting one interactive control inside another (`<summary>`'s
own implicit disclosure-button role) is an accessibility smell on top of
that. **Where exactly that sibling renders moved in the tightening round
below** — see "One type scale... (tightening round)" for the current
placement (pinned onto `<summary>`'s own row by CSS, not a row of its
own further down, which is what this paragraph originally shipped). The
browser's OWN default disclosure marker is left on the summary (unlike
`.chat-menu`/`.delete-disclosure`, which hide it with `list-style: none`
to look like a plain link) — this really is a content disclosure a
reader toggles to see more or less, not a button dressed up as one, the
same reasoning `.tool-output`/`.tool-args-all`'s own summaries already
use.

**`WORKSTREAM_SIDEBAR_LIMIT` is 5, down from 10** (`agents/chat/
sidebar.py`) — "the 5 most recent ones," the owner's own words. The
existing `sidebar_workstreams_older_count` mechanism is untouched: it
already renders however large the gap between the cap and the real
total is, honestly, as the "…N more" link to `chat-workstreams` (the
full stream list page) — "the ability to see more" the owner asked for,
already built, needing no new route.

### Tighter, cleaner (tightening round)

**The owner's pixel feedback on the accordion above:** *"it needs to be
tighter/cleaner than this."* Their screenshot: an oversized "Chats"
`<h2>`, "+ New" orphaned on its own line with a large gap under it, the
workstream list rendering with default disc bullets and `<ul>` indent, a
huge vertical gap, another gap before "New chat"/"CONVERSATIONS" — the
rail reading as loose page prose rather than a nav column.

**One type scale for the whole rail.** "Workstreams" (the accordion's
`<summary>`) and "Chats" (`.chat-nav-label`, also now the scoped
variant's own heading — "same treatment for consistency," point 5) both
match `_shell.html`'s `.nav-group-label` — the SAME look "CONVERSATIONS"
already has: `--muted`, `0.7rem`, uppercase, `0.09em` letter-spacing.
Matched by VALUE, not by sharing the class, because the summary keeps
one deliberate difference the owner's own words allowed ("Workstreams'
summary may keep slightly stronger weight since it's interactive"):
`font-weight: 700` where the plain label is `600`, plus a hover/`[open]`
colour shift (`.chat-menu > summary`'s own existing pattern) as the
toggle's affordance. No full-size `<h2>` renders anywhere in the sidebar
column any more — `.chat-nav-label`'s own `margin: 0` is what stops a
bare heading's UA default margin from padding the tightened `nav.chat-
nav` gap out further underneath it.

**"+ New" moved onto `<summary>`'s own row, positioned there by CSS
rather than laid out beside it.** The two constraints from the first
pass still hold — the link must stay OUTSIDE `<summary>` (a `<summary>`'s
click-to-toggle activation fires on any nested element, with no script
here to stop it) and `<summary>` must stay `<details>`'s literal first
child (or the browser stops treating it as the disclosure widget), which
together rule out wrapping the two in a shared flex row. `position:
relative` on `.chat-workstreams` plus `position: absolute; top: 0;
right: 0` on the link reaches "same row, right-aligned, outside
`<summary>`" within those constraints: the link overlays summary's own
row instead of claiming a second one below it in the flow, so the
workstream list starts right after `<summary>`'s own height with nothing
to close the gap on. `.chat-new` is reused outright for the link, never
merely matched — the owner's own words ("match its visual treatment to
'New chat'... consider unifying both to the same compact style") are
satisfied by there being ONE class for both rather than two rules that
could drift apart later.

**The workstream list is now `.chat-nav-row`/`.chat-nav-group`** — the
CONVERSATION list's own row markup (`chat/_sidebar_row.html`, round 20's
own extraction of what used to be inline in `_sidebar.html` itself) —
not a bare `<ul>`/`<li>`. Same row height, same font size, the same one-line
ellipsis truncation `.chat-nav-row > a`'s existing rule already gives
conversation titles, the same per-row rhythm, and no disc marker or list
indent because there is no `<ul>` left to carry either. The empty state
and the "…N more" link both move onto `.muted` — the SAME small quiet
line the conversation list's own empty state and "N older conversations"
line already use — rather than the bespoke, entirely unstyled `.hint`/
`.more` classes the first pass left them on (part of why the list read
as loose prose: nothing had ever actually styled those two).

**Compact vertical rhythm.** `nav.chat-nav`'s own `gap` drops from
`1.25rem` to `0.85rem` — inside the owner's requested 0.75–1rem range —
which alone tightens the space between EVERY top-level group in the
column (the accordion, "Chats", "New chat", the conversation list, the
archive footer) at once. The other half of "kill the multi-rem voids":
a blanket `nav.chat-nav p { margin: 0; }`, since a bare `<p>` (every
"muted" status line in this column) carries its own UA default margin
that the flex `gap` above is blind to and would otherwise stack on top
of, not replace.

**This round tightened SPACING; it did not yet answer CONTAINMENT** —
five separate top-level pieces, evenly spaced but with nothing marking
where one ended and the next began. The grouping round right below is
what answers that.

### Distinct segments (grouping round)

**The owner's next pixel round, on the tightened rail above:** *"can we
do a better job at visually grouping distinct segments."* Their
screenshot: the rail read as one undifferentiated run — "WORKSTREAMS" /
"Chats" / "CONVERSATIONS" as three near-identical small-caps labels with
no visual containment, "+ New" floating detached at the column's far
right edge with nothing framing it, and "Chats" plus the row group's own
"Conversations" sub-label double-naming what was really one list.

**`.chat-nav-section` is the one bounded-card recipe both groups now
share** — background/border/radius/padding, the SAME family `.tool-
card`/`.start-box`/`.banner`/`.chat-menu-body` already use elsewhere on
this box (`padding` matched to `.chat-menu-body`'s own `0.5rem 0.6rem`,
chosen there for this identical 240px column), kept deliberately subtle
— a soft panel and a hairline, not a heavy card — since a full-weight
card per group would fight the rail's own narrow width for attention.
`_sidebar.html` renders exactly ONE when scoped (see below); when
unscoped, its two FIXED cards are Workstreams and Chats, always in that
order, with one CONDITIONAL card — Pinned, round 20 — between them,
present only when the principal has at least one pinned conversation
and otherwise rendering nothing at all, never an uncontained thing
floating loose around the two fixed ones.

**Workstreams' `<details>` carries the card class ALONGSIDE its own**
(`class="chat-workstreams chat-nav-section"`): the accordion IS one of
the two bounded groups, not a separate thing sitting beside a card. "+
New" needed no new CSS at all to stop "floating detached" — its
`position: absolute; top: 0; right: 0` from the tightening round is
unchanged; it now simply resolves against the PADDING EDGE `.chat-nav-
section`'s own `padding` establishes instead of the raw column edge,
landing it inset from the border the same way `<summary>`'s own text
already sits inset on the opposite side.

**Chats' own header is a plain flex row — `.chat-nav-section-head`,
label left / action right via `justify-content: space-between`** — no
`<summary>` constraint on this side, so no positioning trick is needed:
"New chat" is renamed to "+ New" and given `.chat-new` outright (the
SAME class Workstreams' action already uses), landing both actions at
identical size/weight/alignment on their own group's header row, per the
owner's own words to "match its visual treatment to 'New chat'...
[and] unify both to the same compact style." Two links now read
identical text pointing at two different destinations, which is
ambiguous out of visual context (a screen reader's own link list, say) —
each carries a distinct `aria-label` ("New workstream", "New
conversation") so the two remain distinguishable by their accessible
name even though their visible text is now the same.

**The double label is gone, not merely hidden.** The separate
`<span class="nav-group-label">Conversations</span>`/`Archived` that
used to sit INSIDE the rows group no longer renders at all — the group's
OWN header (`.chat-nav-label`) does that job now, reading "Chats" or
"Archived" depending on `sidebar_archived`, the same conditional the
sub-label used to carry, promoted rather than duplicated. No information
is lost: the archived view still names itself, just once, in the one
place a header belongs.

**HISTORY, SUPERSEDED BY ROUND 14.** At the time this round shipped, a
scoped stream sidebar collapsed to one group (folding the stream's own
name-and-exit header, plus a long-name truncation guard on it, into the
Workstreams card's own position) rather than rendering the Workstreams
accordion and a second "Chats" heading unconditionally. Round 14 retired
the scoped variant outright — see "ROUND 14 (owner amendment): the app
rail" below — so there is no scoped header row left to truncate; the
Workstreams accordion and the flat, unfiltered Chats section both render
on every chat page now, stream or no stream.

### The app shell (owner-requested chat layout fix)

**The complaint, verbatim intent:** on the conversation page, a long
thread used to make the WHOLE page scroll — to type a new message you
had to scroll to the very bottom, to navigate you had to scroll back up,
and the sidebar scrolled away with everything else. Above `1000px` (the
same breakpoint the two-column layout already stacks at — narrow screens
are untouched, still today's plain document flow, per UX point 5 of the
brief) `.chat-layout` is capped to a height budget instead of growing to
fit its content — AT THE TIME THIS ROUND SHIPPED, `calc(100vh - 10rem)`,
the `10rem` being `<body>`'s own top/bottom padding plus `header.app-bar`'s
rendered height. **ROUND 14 (owner amendment) REMOVED THE TOP BAR FROM
EVERY CHAT PAGE** (see "ROUND 14 (owner amendment): the app rail"
below) — with no `header.app-bar` left to budget for, the number shrank
to `calc(100vh - 6.5rem)`; see the rule's own comment in `chat/base.html`
for the current arithmetic, which is the one to trust over this
paragraph's own now-historical `10rem`. `min-height: 0` undoes the flex
default that would otherwise silently defeat the cap, the same override
every column one level down needs too.

**Every column that can outgrow that cap gets its own `overflow-y:
auto` rather than letting the page grow to fit it:** AT THE TIME THIS
ROUND SHIPPED, that meant `nav.chat-nav`, the ONE sidebar column since
chat layout fix round 2 below folded Workstreams and Chats into it.
**ROUND 14 moved the scroll region onto `.chat-nav-chats` alone** — the
Chats section specifically, one of SIX sections the rail carried AT THE
TIME (brand, primary action, nav links, Workstreams, Chats, account) —
since brand/primary/nav-links/Workstreams/account all stayed pinned in
place while only the (potentially long) Chats list itself scrolled.
ROUND 15 (owner feedback) THEN REMOVED THREE OF THOSE SIX (brand,
nav links, account) OUTRIGHT, restoring them to the top bar instead —
the rail's own fixed sections (primary action, Workstreams, Chats) are
unchanged since; round 20 adds ONE MORE, Pinned, but only ever between
Workstreams and Chats and only when it has a row to show (empty, it
renders nothing at all — never a fourth fixed section to keep an
up-to-date count of). `.chat-nav-chats` remains the one that
scrolls; see "ROUND 15 (owner feedback): top bar restored, rail slims
to chat domain — the CURRENT shape" below for the current shape,
including what its own scrolling does to the ⋯ menu (unchanged in kind
since round 14, moved in target: `.chat-nav-chats:has(.chat-menu[open])`,
not `nav.chat-nav:has(...)`). `SIDEBAR_LIMIT = 30` conversation rows plus
`WORKSTREAM_SIDEBAR_LIMIT = 5` workstream rows (`agents/chat/
sidebar.py`) are unchanged by the move. `.chat-wrap` itself,
generically, for `index.html` and `workstream.html`: "their main content
columns may simply scroll internally" was the brief's own words for
those two, and that is the whole treatment they need.

**`conversation.html` needs one thing more precise than "the column
scrolls":** a long thread must not carry the composer out of view with
it. That page cancels `.chat-wrap`'s generic `overflow-y: auto` back to
`visible` and puts the scrolling boundary in a narrower place instead —
`.thread-scroll`, a wrapper around the transcript (`.thread`, otherwise
untouched). `#turn-errors` and `#turn-form` sit OUTSIDE it as plain
siblings after it — the one thing on the page that never scrolls,
exactly what "the composer is always visible" means. `.thread` itself
keeps no new class and no new wrapper of its own: the poller script
finds it with `document.querySelector(".thread")` and both
`insertBlock` and `swapBlock` call `thread.appendChild` /
`thread.querySelector` assuming it holds turn cards only.

**UPDATE, owner feedback round 9:** at the time this pass shipped,
`.thread-scroll` also wrapped the share panel and the delete disclosure
(the two sections that used to render below the composer), reachable
by scrolling past the transcript. Round 9 moved BOTH out to
`.thread-actions`, a quiet row below the composer ("the share panel"
further below has that row's current shape) — `.thread-scroll` holds
only `.thread` now, and the "share panel or delete disclosure" caveats
in the paragraph below describe history, not this page's current
render.

**On load, the transcript opens scrolled to its newest content, and a
poll tick that appends new turns keeps it pinned there only if the
reader was already at (or near) the bottom** — never yanking the view
out from under someone who scrolled up to read history. Both behaviours
are minimal additions to the SAME sanctioned poller script block
(`conversation.html` carries two `<script>` blocks total — this one,
plus round 10's drag-and-drop/staged-files script; see the correction
above "The conversation sidebar (UI-3b)"'s own note): `scrollThreadToBottom()` sets `.thread-scroll`'s
`scrollTop` to the offset that puts the BOTTOM OF `.thread` flush with
the viewport — computed off `.thread` itself rather than the scroll
region's own `scrollHeight` (today those read the same, since round 9
left `.thread` as `.thread-scroll`'s only child, but the expression
keeps working unchanged the day a second section that genuinely
belongs inside `.thread-scroll` shows up); `isNearThreadBottom()` reads that
same comparison BEFORE a swap or an append changes `.thread`'s height,
never after, and a 40px slack (`NEAR_BOTTOM_PX`) absorbs ordinary
subpixel scroll rounding. It runs once on load, after every
`swapBlock()` in the poller's `queued`/`running`/`done` branches (gated
on the reader having been at the bottom), and unconditionally after
`insertBlock()` on a freshly sent message — sending one is itself the
reader saying "I'm at the bottom".

## ROUND 14 (owner amendment): the app rail — PARTIALLY SUPERSEDED, ROUND 15

**ROUND 15 CORRECTION NOTE (truth discipline).** This section described
round 14's own six-section rail (brand, primary action, app-nav links,
Workstreams, Chats, account block) and its removal of the top bar as
"the CURRENT shape" — that claim is now WRONG for the top bar and the
rail's own brand/nav-link/account rows specifically. Round 15 (owner
feedback) partially reversed round 14: **see "ROUND 15 (owner
feedback): top bar restored, rail slims to chat domain" below for the
section to actually trust.** Everything in THIS section about the
Workstreams accordion, the Chats section's own global-list/stream-chip
behaviour, `/chat/all/`, and the scoped-variant retirement is still
accurate and unchanged by round 15 — only the paragraphs about the SIX
sections and the removed top bar are stale, and are marked inline below.

Everything ABOVE this point, back to "The conversation sidebar (UI-3b)",
describes the pre-round-14 evolution of a two-piece column (an
accordion of Workstreams, stacked over a Chats list, with a
stream-scoped variant that collapsed the two into one) and is marked
HISTORY inline wherever it went stale. Round 14 (owner, verbatim: "we
should ahve a UI that is similar to these [the mainstream chat apps'
sidebars — names redacted for release]... notice a similar structure in
both of these for the ui")
replaced that column with a full-height LEFT RAIL and, at the time,
removed the horizontal top bar from every chat page entirely.

**HISTORY, SUPERSEDED BY ROUND 15 — six sections, top to bottom, one
`<nav class="chat-nav">`, AS ROUND 14 SHIPPED IT:** brand mark
(`farabunker`, linking to the landing page); the ONE primary action
row, "+ New chat" (`.chat-rail-primary`, visually distinct — filled/
outlined, never a bare text link); the app's own navigation as quiet
rows (Ask, Search, Document library, Ask history, Queue, Settings —
deliberately NOT "Chat", since the rail already is that destination, and
NOT Images, absent from the owner amendment's own enumeration); the
Workstreams accordion; the Chats section; and an account block pinned
at the rail's own bottom (username, "Your password", sign-out — gated
on `identity_accounts_on`). Round 15 REMOVED the brand mark, the
app-nav rows, and the account block from the rail outright (not merely
hidden) — those three moved back to the restored top bar, which already
renders the identical markup and needed no new rules to keep doing so.
The Workstreams accordion (still a native `<details open>`, still
`WORKSTREAM_SIDEBAR_LIMIT = 5` with an honest "…N more", still
UNCONDITIONAL since there is no scoped variant left to suppress it) and
the Chats section (`SIDEBAR_LIMIT = 30`, still global/unscoped) are
UNCHANGED BY ROUND 15 — only their two former rail-mates are gone.

**The scoped sidebar variant is RETIRED, not merely hidden — UNCHANGED
BY ROUND 15.** `sidebar_context` (`agents/chat/sidebar.py`) no longer
accepts a `workstream=` keyword at all. The Chats section is ALWAYS the
flat, GLOBAL list of the principal's own most-recent conversations —
stream conversations included, each carrying its own small stream-name
chip (`row.conversation.workstream.name`, free off the `select_related
("workstream")` join `may_manage_conversation`'s own stream-owner branch
already paid for) — never scoped to one stream. A stream's own
conversation list still exists; it lives on `chat/workstream.html`
itself now (the working page's own "Conversations" section), never in
the rail.

**HISTORY, SUPERSEDED BY ROUND 15 — "no top bar on any page that
extends `chat/base.html`", AS ROUND 14 SHIPPED IT.** `chat/base.html`
overrode `{% templatetag openblock %} block nav {% templatetag
closeblock %}` empty — the one block `foundation/templates/_shell.html`
wraps its ENTIRE `<header class="app-bar">` in — so every chat page
rendered no top bar at all. Round 15 removed that override entirely; see
below for the current state.

**HISTORY, SUPERSEDED BY ROUND 15 — "the Chats section is the ONE
scrolling region, not the whole column", brand/nav-links/account
reference REMOVED.** `.chat-nav-chats` alone gets `flex: 1 1 auto;
min-height: 0; overflow-y: auto` — this part is STILL TRUE (see below)
— but the ORIGINAL sentence named brand, nav links, and the account
block as the OTHER pinned-in-place siblings; all three are gone from
the rail now, so "+ New chat" and Workstreams are the only siblings
left to name. The ⋯ menu's own clip-escape rule is UNCHANGED BY ROUND
15: `.chat-nav-chats:has(.chat-menu[open])` lifts the clip while a menu
is open — see "The ⋯ menu, and who gets one" above for the overlay
reasoning itself.

**`/chat/all/` (Part 2 of the same round) is the new "see every
conversation" page, UNCHANGED BY ROUND 15** — a searchable/filterable
table (`q=` on title, `workstream=`, `tag=` from the viewer's own
holdable entitlement set, `archived=`), replacing `/chat/?archived=1`
as a browsing surface (that query string now redirects here, carrying
the rest of the query string unchanged) and giving the rail's own
capped `SIDEBAR_LIMIT`/`Show more` link a real destination beyond the
30-row cap. A `?selected=<uuid>` preview pane server-renders the
selected conversation's last three non-TOOL turns through the SAME
`agents.chat.rendering.turn_card` builder the full thread page uses, so
a preview card can never drift from what opening the thread would show.
See `agents/chat/views/all_conversations.py`'s own module docstring for
the full contract (pagination, bounded-query pins, never-500 coverage).

## ROUND 15 (owner feedback): top bar restored, rail slims to chat domain — the CURRENT shape

**This is the section to trust for the top bar's and the rail's own
current state.** Owner, verbatim, a PARTIAL REVERSAL of round 14's own
amendment: "the navigation bar needs to be consistant on the top, you
should not override the main navigation template for main, what the
fuck, this looks like dog shit." The rail CONCEPT stays; round 14's
decision to remove the standard top bar and fold its contents into the
rail was wrong and is undone here.

**The top bar is back, byte-consistent with every other page.**
`chat/base.html` no longer overrides `{% templatetag openblock %} block
nav {% templatetag closeblock %}` at all — there is nothing left here
to override, so every chat page falls through to `foundation/templates/
_shell.html`'s own DEFAULT block content, the SAME `<header class=
"app-bar">` (brand, every "use" link, Queue, Settings, the account
area) every other page on the box already renders, in both directions:
`test_mount.py::TestTheNav::test_the_chat_page_has_the_standard_top_bar`
and its own `test_the_nav_stays_on_every_non_chat_page` pin the top bar
present on chat pages AND unaffected on non-chat pages, the honest
present-everywhere claim replacing round 14's own absent-on-chat-pages
one. `nav_current_chat`'s own override (`{% templatetag openblock %}
block nav_current_chat {% templatetag closeblock %}current{% templatetag
closeblock %}`, untouched by either round) is what still marks "Chat"
current in that restored bar on every chat page.

**The rail slims to chat-domain content only.** `chat/_sidebar.html`
now renders exactly THREE things inside `<nav class="chat-nav">`: the
"+ New chat" primary row (`.chat-rail-primary`, unchanged from round
14 — the one rail addition round 15 kept, since it was never a
duplicate of anything the top bar rendered), the Workstreams accordion,
and the Chats section. The brand mark, the app-nav rows (Ask, Search,
Document library, Ask history, Queue, Settings), and the account block
are GONE from `_sidebar.html` outright — `test_sidebar.py::
TestTheRailOrder` pins their absence explicitly, not merely their
absence from the visible order.

**The app-shell height arithmetic is RE-DERIVED, back to its
pre-round-14 figure.** `.chat-layout`'s own `height: calc(100vh -
10rem)` (`chat/base.html`) is the restored top bar's own arithmetic:
body's `2rem` top padding, plus the bar's own ~35px rendered height and
its `1.5rem` bottom margin (~3.7rem combined), plus body's `4rem`
bottom padding below the row — roughly 9.7rem, rounded UP to `10rem`
for the same margin-of-safety reasoning this figure has always used.
This is the IDENTICAL number the page used before round 14 ever
existed — round 14's own `6.5rem` (the bar-free estimate) is retired
along with the override that made it correct for one round. Both the
rail and the main content column sit UNDER the restored bar now, same
as before round 14, and the sub-1000px stacked layout (`@media
(max-width: 1000px)`) needed no changes of its own — it never depended
on whether the bar rendered, only on viewport width.

**Everything else is unchanged.** The rail's own independent scroll
region (`.chat-nav-chats`), the Chats section's flexible sizing, the
per-row stream chips, and `/chat/all/` are all exactly as round 14 (and
its own review fix waves) left them — round 15 touches only the top
bar's presence and the rail's own top/bottom bookends.

## The stream page (owner feedback: a full composition pass) — HISTORY

**This section is a HISTORICAL record of the pass as it shipped, not
this page's current shape.** A later round split the page in two (see
"The stream settings page" below, appended after this section): the
header's four controls are now one `.ws-settings-link` reading to a
separate page, and Instructions/Scope/Upload default/Tags/Shared with
no longer render here at all — they render on `chat/workstream_
settings.html` instead, in the identical `.ws-card` shape this pass
gave them (joined there, round 17, by a sixth card this pass never
had at all: Document library). What follows describes the page exactly
as this pass left
it, before that split.

**The owner's words, verbatim:** *"what the fuck is this… absolute
garbage."* `chat/workstream.html` (`/chat/w/<pk>/`) had never had a
design pass: four naked stacked disclosure/button rows under the `<h1>`
(▶ Rename / ▶ Description / [Archive] / ▶ Delete), a small bare
`<textarea>` floating loose with Save bolted beside it (Instructions),
bare checkboxes and an oversized inline button (Scope), a tiny
terminal-looking placeholder box with the select and Start misaligned
around it (New chat), bulleted paragraph rows with "Not consolidated"
prose jammed against jumbo Consolidate buttons (Conversations), and
full-width `<h2>`s everywhere with no containment at all — while the
rest of the app (the composer card on `/chat/`, the rail's own `.chat-
nav-section`, the document library's cards) already used a bounded-card
idiom this page never adopted.

**BEHAVIOUR-FROZEN, templates and CSS only.** Every `<form>` keeps its
own `method`/`action`/field `name`s byte-for-byte — this pass never
touches `agents/chat/views/workstreams.py`, `agents/visibility.py`, or
`tools.rag.workstreams.panel`. Every POST, every 302/400/404, every
flash message and every query-count pin from before this pass still
holds; only the markup around those forms, and the `<style>` that
governs it, changed.

**One bounded card per section, matching the house family by value.**
`.ws-card` (`background: var(--panel); border: 1px solid var(--border);
border-radius: 8px; padding: 1rem 1.25rem;`) is the SAME recipe `.tool-
card`/`.start-box`/`.banner`/`.chat-menu-body`/`.chat-nav-section`
already use elsewhere on this box — a new name rather than a shared
class, since this page's cards are full content-column width, not the
rail's 240px, but the SAME look is what makes the page read as the same
product as the rest of `/chat/` rather than something bolted on. Every
section — Instructions, Scope, New chat, Conversations, the Documents
panel (`chat/_workstream_panel.html`'s own wrapper, so a future
registered panel gets this for free), Upload default, Tags, Shared with
— is one `.ws-card`, each with a small-caps `.ws-card-head` label
INSIDE it (matching `.chat-nav-label`/`_shell.html`'s `.nav-group-label`/
the document library's `.shelf-label` by value: `0.7rem`, `700`,
uppercase, `0.09em` tracking, muted) rather than a bare full-width
`<h2>`. No section renders an `<h2>` without that class any more. (Five
of these — Instructions, Scope, Upload default, Tags, Shared with —
later moved onto their own page in the settings-page split below,
keeping this exact `.ws-card` recipe; only New chat, Conversations and
the Documents panel still render here. A sixth, Document library, joined
the settings page directly in round 17 — it never rendered on THIS page
at all, so it is not among the five that moved.)

**AT THE TIME OF THIS PASS, the four header controls collapsed into one
"Manage" disclosure**, reusing `chat/_sidebar.html`'s own `.chat-menu`/
`.chat-menu-body` overlay idiom OUTRIGHT — the identical shape the
per-conversation "…" menu already is, not a second kind of dropdown
invented for this page. (It was a zero-JS shape when this pass was
written and is still zero-JS *here*: round 21's exclusive-open script
below is scoped to the rail only, so this page-header control is
untouched by it and keeps the plain native `<details>` behaviour — no
exclusive-open, no outside-click dismissal, no Escape.) `.ws-manage` only
added what made the summary
read as a legible, named button ("Manage," bordered) rather than an
anonymous "…" glyph, which was the right call for a single page-header
control where five icons in a column would instead be noise. Rename and
Description kept their own nested disclosures (`.chat-rename-form`,
broadened in this pass to style a `<textarea>` the same way it already
styled an `<input>`, for Description's own field); Archive was a bare
form/button inside the panel, styled as a quiet text link the same as
every other button `.chat-menu-body` already governed; Delete kept its
existing nested `<details>` + `.delete-confirm`, its submit button now
carrying `class="danger"` to match the identical control the row-menu's
own Delete already used.

**THIS SHAPE IS GONE.** The settings-page split (below) replaced the
whole `.ws-manage` overlay with a plain `.ws-card` — a full-width card
has room for four controls an overlay never did — and a later review
fold-in unified Archive onto the same `<details>` trigger Rename/
Description/Delete already used, so today's Manage card is four matching
disclosures, not three-plus-a-bare-button. See "The stream settings
page" below for the current shape and its own reasoning.

**Scope's checkboxes move onto the chip-picker component**
(`_shell.html`'s `.chip-picker`/`.chip-check`) — the exact "pick any
number of entitlements" control this box already ships and the document
library's own upload card already uses, previously unused here for no
recorded reason. The checkbox and its visible `.chip` text share ONE
wrapping `<label>`, so the accessible name is the label's own text (the
entitlement's name) with no separate `id`/`for` pair needed — a11y
carried forward from the walk-fix batch's original F4 fix by a different
mechanism, not dropped.

**"New chat" reuses `.start-box`/`.start-controls` outright** — promoted
from `chat/index.html`'s own page-scoped `chat_style` into `chat/
base.html` in this same pass, since a SECOND page needing an identical
composer card is what actually moves a rule from page-scoped to shared.
"Match the /chat/ index composer card exactly in feel" (the owner's own
words) is satisfied by there being ONE card class both pages render,
never two copies that could drift.

**Every row list on the page — Conversations, the Documents panel's own
Contained/Pinned lists, Shared-with (moved to the settings page below,
keeping this same idiom) — is now `.ws-list`/`.ws-item`**, one
flex-row idiom (title truncates via `.ws-item-title`'s `min-width: 0` +
`text-overflow: ellipsis`, exactly `.chat-nav-row > a`'s own truncation
trick; trailing content sits in `.ws-item-meta`) rather than each
growing its own `<ul>`/`<li>` with a disc marker and an indent. NOT
`chat/base.html`'s own `.chat-nav-row`, deliberately: the rail (see
"ROUND 14 (owner amendment): the app rail" below) renders `.chat-nav-row`
for its own Chats section on THIS page too, and sharing the class would
make "how many conversation rows does this page have" ambiguous between
the rail's own global list and the main content's stream-scoped one —
worth a distinct name even though the two recipes are siblings by
design. Compact row actions
(Consolidate, Re-consolidate, Unpin, Remove) get `.btn-compact` — `.tool-
action`'s own recipe (`chat/conversation.html`'s compact bordered link,
used for "View in Gallery") adapted for a real `<button>`, since every
one of these posts a CSRF-protected form and cannot be a plain link — so
there is ONE consistent size scale on this page (point 9 of the brief):
compact for a row's own action, the shell's own default full-size accent
button for a form's real submit (Save, Start, Share, Create), never a
jumbo button sitting beside body text.

**The dormant marker reads as a warning consistently.** `.dormant`
(`chat/base.html`, already governing the sidebar's own workstream-list
marker) gained `color: var(--danger)` in this pass, and the Shared-with
list's own dormant text now carries the class too — one look for the
same word, wherever it appears, rather than each surface inventing its
own colour (or none at all).

**A form embedded in a flex row gets a real class, `.ws-inline-form`
(`display: inline; margin: 0;`), never a bare `style="display:inline"`
attribute** — the same reason every other rule on this box lives in one
`<style>` block instead of scattered inline styles. The placement
chooser fieldset (`rag/_placement_choice.html`, included by the
Documents panel's own Upload form) is tidied the same way, scoped to
THIS page's own `chat_style` only: it never had any styling on either of
the two pages that include it, and this pass fixes it here without
touching `rag/documents.html`'s separate, untouched render of the
identical fragment.

**`/chat/tools/`** (`chat-tool-entitlements`) — labels a registered tool
with an entitlement. It lives here, not in `identity/`, because
`identity/` may not import `agents/` (import-law rule 4) and `/chat/` is
this column's only URL mount, the same reason the document-label page
lives in `tools/rag`. Class `S`: a tool label is library-wide operator
policy with no entitlement-owner path, unlike a document label. GET
renders every registered tool with its current labels, marking every
`mutates=True` spec **page-only** — `grantable_tools()` already excludes
it from any agent's declared tool list, so labelling one here narrows
who may reach it from THIS page, never from a turn. Starting a turn with
an agent that declares a tool the acting principal lacks the entitlement
for does not add a "not available on this install" note to the thread
(decision 17, `agents/runtime/preflight.py::Preflight.unentitled_tools`,
kept apart from `dropped_tools` and never read by
`dropped_tool_notes`) — the tool is simply absent from the model's
prompt, because naming it would be a false sentence about a tool the
entitlement was meant to keep out of that principal's way in the first
place.

**`/chat/settings/`** (`chat-settings`) — the chat column's own page in
the settings area, registered as **Chat** in `foundation/settings_area.py`'s
Setup group. Class `S`, for the reason `rag-settings` records for itself: a
page whose whole body is an administrator-only form has nothing to render
for anybody else. GET and POST share the one route (the shape
`identity-settings` uses for its own singleton) rather than
`rag/settings.html`'s card-per-endpoint split, which exists there because
that page carries seven policies with genuinely different validation and
this one carries a single checkbox. It edits `agents.models.ChatSettings`
— today, `time_aware`: whether every conversation's prompt states the
current date and time and stamps each replayed message with when it was
sent (see [`agents/README.md`](../README.md) "The clock").

**`/chat/access/`** (`chat-agent-entitlements`) — the sibling page for
agents and flows, one page for both (`agents/chat/views/access.py`),
also class `S`. Labelling an agent or flow here is enforced through
`agents.visibility`'s label clause (see [`agents/README.md`](../README.md)
"Agents and flows carry their own labels"), which composes with the
box-wide carve-out (`box_wide=True` for an agent, `resident=True` for a
flow — see "`box_wide` and `resident` are two different facts" in
[`agents/README.md`](../README.md)) rather than being bypassed by it —
a shipped default is not exempt from being labelled.

**The share panel** (`_share_panel.html`, rendered inside
`conversation.html`) is `chat-conversation-share`'s UI, not a page of
its own: add or revoke a `Share` on the open conversation, `view` or
`use`, to a user or a group. Owner or `sees_all_content` only — a
recipient cannot re-share what was shared with them, which
`agents.visibility.may_read_conversation_shares` and the POST enforce
with the same predicate so the panel never offers a control the POST
would refuse.

**QUIET ACTIONS ROW, `.thread-actions` (owner feedback round 9,
verbatim: "when I'm on the chat screen the share shouldn't be a main
feature. it should be a suble side feature ... if i click on it I get
more options (via a popup)").** The share panel and the delete
disclosure used to render as an in-flow section between the transcript
and the composer; both moved to this small row below the composer
now. `Share…` and `+ Add files` are popup overlays — `<details
class="chat-menu">`, reusing `chat/base.html`'s own sidebar-menu
overlay recipe (`position: relative` on the `<details>`, an absolutely
positioned `.chat-menu-body` panel) rather than a new popup mechanism —
and `_share_panel.html`'s own content is UNCHANGED, only its wrapper
moved. `Delete this conversation` keeps its pre-existing in-flow
confirm, just muted into this row.

**The attach door, `chat/_attach_files.html`** (owner feedback round 9:
"I also don't have the ability in the chat to add any additional
objects like photos/documents/pdfs/ect"). ONE shared fragment, included
by `conversation.html` for a plain conversation and for one inside a
workstream alike (owner amendment: "There shouldn't be two different
code bases. Both should serve both purposes.") — never two per-kind
forms. **ROUND 13 REWRITE (message-bound attachments; owner feedback:
"we shouldn't process unless the message is actually submitted with the
file").** Through round 12 this fragment was its OWN `<form>`, posting
straight to `rag-document-upload` the instant "Add files" was pressed —
files uploaded and ingested before any message existed to carry them.
It is no longer a form at all: it renders only the file input, a
staged-files list, and the placement chooser, all now form CONTROLS
of `#turn-form` itself (`chat/conversation.html`, `enctype="multipart/
form-data"`, the `<details class="chat-menu attach-trigger">` moved
INSIDE the `<form>` — HTML forbade that while this fragment still
nested its own `<form>`, no longer an issue once it stopped being one).
Choosing or dropping files stages them in the browser only; the SAME
Send button that submits the message submits the files with it, in ONE
multipart POST. `agents.chat.views.turns.turn_create` reads `request.
FILES`; `agents.chat.service.start_turn` re-derives `may_attach_files`
(the identical `rag.ingest`-tool-access predicate this fragment's own
render-side gate uses — GENERAL, any holder, not merely the stream's
owner, narrowed inside a workstream to `workstream_scope(...).
may_upload`) and the placement vocabulary (`agents.contracts.
attachments.CHAT_PLACEMENTS_IN_STREAM`/`_LOOSE`) BEFORE writing a
single row — render-vs-gate, both sides recomputed independently, the
same discipline every other predicate on this surface keeps. `tools.rag.
services.stage_turn_attachments` (the sanctioned `agents.attachments`
seam, since `agents/` may not import `tools/` at all) is what actually
stages the file(s) and enqueues ingestion, called from inside `start_
turn`'s own transaction, AFTER the user's own `Turn` row is written and
BEFORE the agent turn is enqueued — `tools/rag/views.py::document_
upload`'s own chat-door branch (the `conversation`/`next` fields, the
three-check provenance validation) is RETIRED entirely; that view now
serves only the library page and the workstream Documents panel,
exactly as before round 12 ever touched it.

**Placement/scope, as of round 12 (owner ruling, verbatim: "if I submit
a document but have scope for chat, then it should only be used in that
chat... if I change the scope to workstream, it should be made
available via the rag framework"), UNCHANGED BY ROUND 13 — only the
request that carries the choice moved.** The door's own chooser is
INLINE in this fragment, not `rag/_placement_choice.html` (that
fragment remains the workstream Documents panel's own, unchanged):
THREE options inside a stream ("This chat only" — THE DEFAULT,
pre-checked — "This workstream only", "The universal library"), two
outside one ("This chat only" / "The universal library"). This door
ALWAYS ASKS — it never auto-applies a stream's own remembered `default_
upload_placement` the way the panel still does, and "remember" here
never captures "This chat only", only the workstream/universal pair.

**Pre-send remove (round 13, requirement E).** The file input is no
longer `required` (most messages carry no attachment at all now that it
shares a form with plain text messages). A `<ul class="staged-files">`,
populated by `chat/conversation.html`'s own script (the round-10
drag-and-drop script, extended, not a second one — the sanctioned-
script budget), shows one row per currently-staged file with a ✕ that
rebuilds `fileInput.files` via a fresh `DataTransfer` minus that one
file — the one documented way to edit a `FileList` at all. JS-off: this
list never renders, and the honest way to change a selection is the
browser's own native re-pick (choosing again REPLACES the prior
selection, documented in the fragment's own comment).

**Paste is attach (chat image artifacts, 2026-09-16; owner: "I should also be
able to copy an image from clipboard into the chat … as if I were to attach
them to the message").** `chat/_attach_dragdrop.html`'s script adds a `paste`
listener on the composer card. Each image item on the clipboard becomes a
`File` named `pasted-image-<YYYYMMDD-HHMMSS>[-<n>].<ext>` — `png` only when
the item carries no type at all; any other type keeps its own subtype as the
extension, so a webp or tiff item is refused by the server's own extension
allow-list rather than silently relabelled `.png` — and goes through
**the same accumulator** a drop and a click-to-browse pick go through — so it
gets the same chip, the same ✕, the same dedup, and rides the same `files`
field and placement chooser. **No server change at all**: the size cap, the
extension allow-list, the tool-access gate and the staging rollback apply to
it exactly as to a chosen file, because by the time it is posted the server
cannot tell which door it came through. The listener never calls
`preventDefault()`, so a mixed paste keeps its text and stages its image; it is
only wired when the attach door is rendered (the fragment itself is included
behind `may_attach_files`), so a principal without attach rights gets the
browser's own default paste and the refusal path is unchanged for anyone who
bypasses the UI. With image ingestion switched off at the platform level, the
server refuses the image at attach and the existing inline refusal is what the
operator sees — the same sentence a chosen image of that type has always got.

**Chips on the turn bubble, not a conversation-level strip (round 13;
owner feedback: "After submitting the document wasn't attached to that
message (at least not visually)").** `chat/_turn_card.html` renders
`card.attachments` — chip dicts (`tools.rag.access.attached_documents`'s
own shape) whose `"turn_id"` matches THIS card's own turn — as one
`.turn-attachments` block per USER card, title/status chip and (for the
uploader) a POST-SEND ✕ (`chat-attachment-detach`, uploader-only,
`tools.rag.access.may_detach_attachment`, no admin override). Grouped
once per render by `agents.chat.views.thread.thread_context`
(`attachments_by_turn`) and rebuilt fresh on every poll tick by `agents.
chat.views.turns._attachments_by_turn` — the SAME LIVE-STATUS mechanism
that already re-swaps the whole exchange on every "queued"/"running"
tick and once more on "done" now carries chip status with it, which is
what fixes the owner's other complaint ("it says its still processing"
after a grounded answer): a chip reads whatever the SERVER had for it
at the moment of the last swap, never a value cached at page load.
`_done_body`'s own `attachments_pending` key tells the poller to keep
ticking a little past "done" specifically when a chip is still
non-terminal, so a slow-to-ingest file's status is not stuck stale once
the ANSWER itself has finished. The underlying junction table, `tools.
rag.models.DocumentAttachment` (round 11 review I-5), lets one document
carry attachment rows from several conversations at once, and — ROUND
13 — a `turn_id` column (nullable, for legacy rows) says WHICH turn
carried it; a `Document.scope == "conversation"` row (round 12) still
carries EXACTLY one attachment row, enforced in the write path (`tools.
rag.services._attach`, moved from the now-retired `tools.rag.views.
_attach`). Deleting a conversation (`agents.visibility.
delete_conversation`) removes its attachment rows through the SAME
`agents.contracts.attachments` cleanup slot the provider registry uses
for reads, wrapped in its own savepoint (round 11 fix-2 Important N-1)
— for a chat-scoped attachment, that cascade deletes the DOCUMENT
itself (chunks, managed-store files, and the row); for a universal/
contained one, only the attachment row dies. Post-send removal
(`chat-attachment-detach`) follows the SAME split for a single row:
uploader-only, chat-scoped → full delete via `tools.rag.services.
delete_document`; universal/contained → unlink only, document
untouched.

An image row (`doc.is_image`, off the same provider) also renders a small
thumbnail inside its own chip, served by the very `rag-document-file` route the
title link beside it points at — the same `readable_document` gate, so the
thumbnail can never show bytes that link could not. The chip is the honest
place for this and `agents/chat/rendering.py` is deliberately unchanged: a
`document:<id>` reference in a *tool result* carries no media type, so
rendering cannot tell an image document from a prose one by reference alone,
while the chip has the row. The rules for it live in `chat/base.html` beside
the other chip rules, per the CSS-ownership gate. The `<img>` itself carries
`loading="lazy" decoding="async"` (final fix wave, Fix 1): the document file
view marks its response `Cache-Control: private, no-store`, so the browser
cannot cache the bytes, and the poller re-swaps this same card on every tick
while ingest runs — without lazy-loading, an offscreen chip in a long thread
would be refetched, at full resolution, on every one of those swaps.

**Direct-context injection (OWNER ADDENDUM, 2026-09-08, binding),
verbatim: "when I add a file to the chat, it shouldn't have to do rag
to find it. I thsould already associate my request with that file and
have that as a reference within the context."** The turn that CARRIES
the file gets its extracted text inlined directly into that turn's own
system prompt (`agents/runtime/prompt.py::_carrying_attachments_block`,
threaded through `build_messages`'s new `carrying_turn_id` keyword,
resolved by `agents.runtime.loop._run_turn` as the user turn
immediately preceding the assistant placeholder it is answering) —
`rag__search` stays available but is never REQUIRED for that turn to
answer correctly. Extraction is MODEL-FREE and SYNCHRONOUS (`tools.rag.
readers.read_prose_documents`, the sanctioned `agents.attachments.
inline_attachment_text` seam), bounded by two character caps (4,000 per
file, 12,000 total across the carrying turn's own files — a fixed
character budget, not a token count, the identical reasoning `agents.
limits.HISTORY_TURNS`'s own docstring gives), with an honest truncation
line naming `rag__search` as the escape hatch, and an honest "still
processing"/"could not be extracted" line for anything the model-free
readers cannot handle inline (tabular, media, a scanned PDF). A LATER
turn's own prompt build never re-inlines that same file's text — only
the round-11 steering sentence (`_attachments_block`) names it — the
"same builder, trimmed content" shape `tool_turn_messages`' own
`[artifacts: ...]` line already takes for a replayed tool result.

**`chat-turn`'s `use`-level rule.** `agents/chat/views/turns.py::
turn_create` resolves the conversation through `visible_conversations`
first (so an invisible one 404s, same as everywhere else), then checks
`agents.visibility.may_post_to(principal, conversation)` — true for the
owner, for `sees_all_content`, or for a share at **`use`** level;
**`view` is not enough**. A `view`-level recipient who posts anyway gets
403, not 404 — they can already see this conversation, so pretending it
does not exist would be a secrecy the surrounding page does not keep —
with the copy "This conversation was shared with you to read, not to
post in." Removing the share removes both the read and the post right
together; changing it from `view` to `use` is the one action that turns
a read-only recipient into one who can post.

## The stream settings page (owner feedback: a separate settings page)

**The owner's words, verbatim:** *"the workstream chat needs to keep the
settings that are not often updated on a separate settings page, we dont
need to see all the items when we go to a workstream."* Unlike the
composition pass above, this round is NOT behaviour-frozen: it adds a
route and a view, and retargets every redirect a stream-editing form
used to land on.

**Two pages now, not one.** `chat/workstream.html` (`chat-workstream`,
`/chat/w/<pk>/`) keeps only the WORKING sections — New chat,
Conversations, and the Documents panel (Upload, the placement chooser,
Contained, Pinned, Pin-a-document) — and stays what a share recipient
sees, `stream_access`'s own 403 exception for a live-but-dormant holder
included. `chat/workstream_settings.html` (`chat-workstream-settings`,
`/chat/w/<pk>/settings/`) is new, and hosts everything "not often
updated": Manage (Rename/Description/Archive/Delete — now a plain
`.ws-card`, not the composition pass's own "Manage" overlay disclosure,
since a whole card has room to breathe where a header dropdown did not),
Instructions, Scope, Upload default, Document library (round 17 —
the "use the full document library" toggle), Tags, and Shared with. The
working page's header keeps exactly one control where those four used to sit: a
single `.ws-settings-link` ("Settings") reading straight to the new
page, owner-gated the same way the sections behind it are.

**The settings page is OWNER-ONLY under the plain house 404 rule, and
deliberately NOT `chat-workstream`'s own 403 exception.** `workstream_
settings` (`agents/chat/views/workstreams.py`) 404s anyone who is not
`may_manage_workstream` — a live share holder included, not only a
dormant one — with no `stream_access` call and no 403 branch at all.
Every section this page hosts already needed ownership (or, for Tags,
was already safe for any viewer who could reach it), so there is nothing
behind it a recipient could legitimately be shown; `chat-workstream`
keeps its one platform exception because its own working content
(Conversations) really is something a live recipient reads.

**One builder per page, and the split made both cheaper, not merely
equal.** `_working_context` builds the working page's context and
computes nothing the settings page alone needs — no `accounts_on`, no
Scope, no Tags, no Shares — so its own query cost fell rather than
staying flat at the old combined-page number (measured: 62 queries for
both an owner and a recipient at any conversation count, a coincidence
of two different +2 costs — `staleness_for`'s own pair the owner alone
pays, and `stream_access`'s own pair a recipient alone pays — landing on
the same total; see `agents/chat/tests/test_workstream_page.py::
TestTheStreamPageCostsTheSameAtOneAndTenConversations`). `_settings_
context` builds the settings page's, and is the only builder `page_error`
still exists on: every POST handler that can refuse with a message
(`workstream_edit`'s rename/delete branches, `workstream_scope`,
`workstream_share`) now re-renders `chat/workstream_settings.html`, not
`chat/workstream.html` — `page_error` was removed from the working
builder and its template entirely, not merely left unused, since nothing
can populate it there any more.

**Every redirect that used to land on the combined page now lands on
settings**, except one. `workstream_edit`'s rename/description/
instructions/upload_default/archive/unarchive successes, `workstream_
scope`'s success, and `workstream_share`'s revoke and share successes
all redirect to `chat-workstream-settings` now. The one exception is
`workstream_edit`'s "delete" success: the row is gone, so there is no
settings page left to land on, and it still redirects to `chat-
workstreams` (the list), unchanged. `workstream_consolidate` also stays
untouched — it redirects to `chat-workstream`, because Consolidate is a
working-page action a recipient's own conversation can trigger.

**The upload form's own "change" link retargets too.** `tools/rag/
templates/rag/_placement_choice.html` — included by both `rag/
documents.html`'s own upload card and the Documents panel's in-stream
one — points its `#upload-default` anchor at `chat-workstream-settings`
now, since that section moved off the working page the panel itself
still renders on.

**The card recipe promoted a second time.** `.ws-head`/`.ws-card`/`.ws-
card-head`/`.ws-card-actions`/`.ws-textarea`/`.ws-list`/`.ws-item`/`.ws-
item-title`/`.ws-item-meta`/`.ws-inline-form`/`.btn-compact` all moved
from `chat/workstream.html`'s own page-scoped `chat_style` up into
`chat/base.html` in this same round — the identical "a second page
needing it is what promotes a rule from page-scoped to shared" test the
composition pass's own `.start-box` promotion already established, now
applied a second time because `chat/workstream_settings.html` needed the
same recipe. `.ws-subhead`/the placement-chooser fieldset rules/`.ws-
upload-form` stayed page-local to `chat/workstream.html`: the settings
page never includes the Documents panel, so it never needs them.

**Review fold-in: Manage's four actions now share one affordance.** The
Manage card, as this split first shipped it, still carried a composition-
pass leftover: Archive/Unarchive was a bare always-visible form sitting
between Rename's own `<details>`, Description's own `<details>`, and
Delete's own `<details>` — three disclosures and one exception, because
the bare form was styled for the OLD `.chat-menu-body` overlay's cramped
width, a reason that stopped applying the moment Manage became a
full-width card. `identity/templates/identity/entitlement.html`'s own
grouped owner actions (Rename and Delete, each its own always-visible
`<section>`) was the house convention offered as the alternative shape,
but four always-visible rows read worse inside one bounded card than
Delete's own existing "closed by default" habit already reads — so
Archive/Unarchive was wrapped in a matching `<details>` instead, closed
by default like its three siblings. No POST semantics moved: same
`action` field, same button, only the markup around it.

## The workstream setup screen (owner feedback round 7)

**The owner's words, verbatim:** *"should we have it on it's on screen
rather than chilling at the top. I.e. Workstream setup that has key
items and whos full extent?"* NOT behaviour-frozen, unlike the two
rounds just above: this adds a real route and a real view.
`chat-workstreams` (`/chat/w/`) used to open with an inline name+Create
row; that row is gone, along with the `create_error` banner it rendered
on a refusal — creation moved wholesale to its own screen, `chat-
workstream-new` (`/chat/w/new/`), and the list page's own job is purely
listing now.

**Five key items, one form, one submit.** Name (required — the exact
validation and refusal copy the retired list-page form always had:
"You already have a workstream called that, or the name was blank.
Pick another name."), Description, Instructions, Scope, and the upload
default all render as one submission before the stream exists at all,
rather than a bare name that then sends the owner hunting across the
settings page one section at a time for everything else. Every card
reuses `chat/workstream_settings.html`'s own field-level markup
verbatim where the sections match — the identical chip-picker, the
identical `.ws-textarea`, the identical three-way placement radios and
their copy — because the brief asked to reuse those card designs, not
redesign them. What could not be reused verbatim is the FORM BOUNDARY:
the settings page gives each section its own `<form>`/CSRF token/submit
button because each one saves independently; the setup screen has
exactly one submit for all five, so the per-section wrappers are gone
in favour of one outer `<form>` and one "Create workstream" button.

**`workstream_new` (`agents/chat/views/workstreams.py`) calls the SAME
writers the settings page's own three POST routes call**, in sequence
against the row this same request just created, inside one
`transaction.atomic()` block: `create_workstream`, then (only when
provided) `set_workstream_instructions`, `set_workstream_scope`,
`set_workstream_upload_default`. A mid-sequence refusal leaves nothing
behind — not the row, not its audit trail, not a half-set scope.
`may_manage_workstream`'s guard inside every one of those writers passes
trivially (the principal that just created the row is, by construction,
its owner), so the only two refusals actually reachable are a
duplicate/blank name and a scope submission naming an entitlement the
principal does not hold — checked the same way `workstream_scope`'s own
dedicated view checks it, and refused with the identical message, "A
workstream's scope may only name entitlements you hold." **Rendering
never happens from inside the `atomic()` block**: every `render()` call
sits strictly after it exits, because `_setup_context` runs queries of
its own (`accounts_on`, `entitlement_names`, `sidebar_context`), and
Django refuses to run a query on a connection already marked for
rollback — an early lesson this round's own first draft hit directly
(`TransactionManagementError`) before the fix moved every render call
outside the `with` block and threaded `error`/`row` out as plain local
state instead.

**Scope is gated on `accounts_on()` alone (ruling A), not on any
"is_owner" question** — `_setup_context`'s own docstring explains why:
every visitor to this screen is, by construction, about to own the
stream they are describing, so there is no second principal's ownership
left to ask. Absent entirely on an open box, exactly like the settings
page's own Scope gating.

**`chat-workstreams` is `@require_GET` now**, and a POST there answers a
clean 405 instead of the create it used to do — the retirement is real,
not merely a UI change; no dead endpoint was left half-classified.
`identity/routes.py` gives `chat-workstream-new` class `A`, the identical
shape `chat-workstreams` itself carries: there is no row until the POST
succeeds, so nothing here is row-addressed and nothing 404s.

**Every entry point that used to point at the list page's own create
form now points at the setup screen instead.** The rail's "+ New" and
its empty-state "No workstreams yet — New" link (`chat/_sidebar.html`)
both retarget to `chat-workstream-new`; the list page's own inline row
becomes a single link, `<a class="pill-link" href="{% url
'chat-workstream-new' %}">New workstream</a>`. The "…N more" link stays
pointed at `chat-workstreams` unchanged — that one is "see the rest of
the list," never "create," and creation was never its job.

**`.pill-link` is a promotion, not a new one-off class.** The working
page's own "Settings" link (`chat/workstream.html`) carried this
bordered-pill recipe under the page-local name `.ws-settings-link`
before this round; the list page needed the identical "one discoverable
navigating link, not a form" look for its own "New workstream"
affordance, the same "a second page needing it" test that already
promoted `.start-box` and the whole `.ws-card` family. The recipe moved
to `chat/base.html` under the generic name, and `chat/workstream.html`
was updated to use it instead of keeping its own now-redundant copy —
one rule, two pages, rather than two near-identical ones.

## The consolidation wave (current truth)

Three shapes changed under `agents/chat` in one pass, none of them worth
a history essay: audits A and B's own FIX-NOW set, an orchestrator S10
ruling, and a confirm-round polish pass on top of it. `thread_context`'s
context keys were disambiguated wherever two keys used to share one name
for a different shape: `showing_archived` (bool, `all_conversations.py`
— the `?archived=1` URL param itself is unchanged, only the internal
variable was renamed) versus `archived_streams` (list,
`workstreams.py`); `conversation_shares` (`thread.py`, one render site)
versus `stream_shares` (`workstreams.py`, read by
`workstream_settings.html` alone); `accounts_enabled` replaces the
function-name-shadowing `accounts_on` context key. The NULL-`turn_id`
legacy attachment renderer — `chat/_legacy_attachments.html`, its
context key, and the `None`-keyed bucket that fed it — is gone: no
migration on `origin/main` can produce a `DocumentAttachment` row with
that shape any more (S10), so the whole surface was
production-unreachable. And a new gate,
`foundation/ops/tests/test_css_ownership.py`, pins the placement rule
("a selector's home is the deepest template that is an ancestor of
every template that uses it") mechanically, on plain source text: no
`chat/_*.html` fragment may use a class defined only in one leaf page's
own `chat_style` override, and no page extending
`foundation/templates/_settings.html` may re-type a rule that template
already owns.

## One composer, three surfaces

`chat/_composer.html` is the ONE fragment `chat/index.html`'s own start
box, `chat/workstream.html`'s New-chat card, and `chat/conversation.
html`'s message form all render now (owner feedback: "I thought all the
chats used the same code base, why is there something using a different
sub-template?") — parameterized only where the surfaces genuinely
differ (`composer_mode`, the action URL, whether the agent/model picker
shows), with every attachment-related piece — the attach door
(`chat/_attach_files.html`), staging and drag-and-drop (`chat/_attach_
dragdrop.html`), and Enter-to-send (`chat/_enter_to_send.html`, now
selecting the generic `form[data-enter-submits]` hook rather than a
page-specific class or id) — included exactly once. `agents.chat.
service.composer_attach_context` is the two start surfaces' own
`may_attach_files`/`attach_workstream` gate, the START-side twin of
`thread_context`'s identical computation for an existing conversation.
`agents.chat.views.conversations.conversation_start` reads
`request.FILES`/`placement`/`remember_placement` the same way
`agents.chat.views.turns.turn_create` does and threads them through the
SAME `start_turn`, so a thread's first turn carries attachments exactly
like any later one. The attach door itself dropped its `<details>`
popup for a plain, always-in-flow chip stack with accumulate-on-pick
staging (owner feedback: "the box that allows me to add it just stays
hovered... there should be a button to add a document and then it
should stack").

## The rail's row menus

**Item B — they are dropdowns now, not toggles.** Owner feedback,
verbatim: *"when I click on the ..., the prior one doesn't go away when
I click on another one as seen in the image. it should be like a drop
down not a toggle menu."* Plain `<details>` elements are independent
toggles; opening one has no effect on any other.

`chat/_menu_exclusive.html` is this surface's third sanctioned
progressive-enhancement script, beside `chat/_enter_to_send.html` and
`chat/_attach_dragdrop.html`. It makes the rail's row menus behave the
way the owner asked: one open at a time, and dismissed by an outside
click or by Escape rather than only by clicking the summary again.
Included exactly once, by `chat/_sidebar.html`, so every rail surface
(index, thread, workstream pages, `/chat/all/`) gets it and no leaf
page includes it itself — which is why every page that shows the rail
carries at least one `<script>`. The chat pages that show no rail still
carry none.

**The two scopes are different, and deliberately so.** EXCLUSIVITY is
rail-scoped: closing the others when one opens is a rail behaviour, and
one delegated listener on `nav.chat-nav` is its whole extent, so
`chat/workstream_settings.html`'s "Manage" disclosure — which reuses
the same `.chat-menu` idiom outside the rail — never closes and is
never closed by a rail menu. DISMISSAL is document-wide, because an
outside click has to be recognised wherever it lands; its guard spares
any `details.chat-menu` on the page, so clicking that same "Manage"
disclosure leaves an open rail menu alone. Two menus the operator
opened on purpose, neither yanked away.

**With JavaScript off** every menu still opens, closes and posts
natively — two can be open at once and a click elsewhere leaves them
open, exactly the behaviour this surface shipped with before the round.
Nothing further is added: no focus trap, no roving tabindex. The menu
is a native `<details>` and stays one.

The mechanism — why the listener runs in the capture phase, what the
`chat-menu` class guard is for, why `pointerdown` rather than `click`,
and why closing a menu has to clear the nested `<details>` inside it —
lives in `chat/_menu_exclusive.html`'s own module comment, beside the
code it describes, and is not restated here.

**Item C — a pinned row's menu is no longer clipped.** Owner feedback,
verbatim: *"Also when i click the ... on pinned, it expands but it's
hidden and I'm not able to see the ... clicked."* Round-20-confirm R7
bounded the Pinned section's row group (`max-height: 14rem` +
`overflow-y: auto`) to fix a real spill defect — and thereby made it a
clipping context for exactly the absolutely-positioned menu panels
inside it, which is the landmine `.chat-menu-body`'s own warning comment
had named when the overlay landed.

**Both properties survive.** The bound stays (so no number of pins can
crush the Chats section or push the "Archived (N)" foot off screen);
only the CLIP lifts, and only while a menu inside that group is open:

```css
.chat-nav-pinned .chat-nav-group { max-height: 14rem; overflow-y: auto; }
@media (min-width: 1000.01px) {
  .chat-nav-pinned .chat-nav-group:has(.chat-menu[open]) { overflow-y: visible; }
}
```

This is the house idiom, not a new mechanism: `.chat-nav-chats:has(.chat-
menu[open])` has done exactly this for the Chats section since the chat
layout fix, and `.chat-wrap:has(.search-checklist[open])` is a third use.
The panel itself is untouched — still `position: absolute; z-index: 5`,
the standing owner ruling that a menu is *"above, not something that
pushes all the other html down"*.

**The trade has two halves**, both stated beside the rule and both only
reachable once there are more pins than the bound shows. While a menu is
open that ancestor stops scrolling, so its rows **paint over** their
neighbours until the menu closes; and switching a scrolled element from
`overflow-y: auto` to `visible` **discards its scroll offset**, so a
Pinned list the operator had scrolled down snaps back to the top the
instant the menu opens — the clicked row and the menu with it jump out
from under the cursor, and closing the menu does not put the offset
back. The pre-existing `.chat-nav-chats` rule has behaved identically
since the chat layout fix, but that ancestor is the full-height list
where this bound is 14rem (~5 rows), which makes "already scrolled when
you click" the common case here rather than an edge one — read `14rem`
as a floor to revisit if pin sets grow, not a number to shave. Item B
keeps all of this to at most one ancestor at a time.

## What this app owns

No models, no migration, no `ready()`. `agents/chat/apps.py` explains
why in full; the short version is that `agents/chat` holds no schema, so
it needs none of the machinery that exists to keep a schema safe across
a package move.

## What the context line means

Below the composer, the thread page renders one line: the estimated size of the
prompt the **next** turn will carry, the ceiling that prompt will be given, and
the percentage between them.

It is an **estimate**, and the page says so every time it is shown. There is no
tokenizer on this box and no per-render engine call; the number is characters
over four, and the `<details>` beside the line discloses exactly that.

It measures **what is sent, not what the conversation holds.** Only the last
`agents/limits.py::HISTORY_TURNS` replayable turns reach the prompt, so a long
conversation's meter plateaus — and the line then says how many of how many
messages are no longer sent. That clause is the first place this platform tells
a reader their conversation is already being shortened before it is sent.

Six things it does not count, every one an under-count; `agents/usage.py`'s
module docstring names them all. The two a reader can act on — files attached to
a message, and what a tool was asked and answered — are named in the disclosure
on the page.

The ceiling is the **operative** window: what the engine will actually be asked
to allocate. An operator's own per-connection value when there is one, the engine
adapter's bounded default otherwise — with a sentence saying so that only an
administrator's render builds. Nothing probes an engine for an architecture
maximum.

The line refreshes live through the page's existing poller, at the two moments
the replayed corpus grows: when a turn is queued, and when it finishes. The poll
body carries three integers and nothing else. The window, the bands and every
sentence stay with the page, which is the only place that knows the picker's
current selection.

## The agent pages

`/chat/agents/` lists the agents this principal may edit; `/settings/agents/` lists
every agent on the box, for an administrator. **One edit route serves both** —
`agents/chat/views/agents.py`, rendering `chat/_agent_form.html` from
`agents/chat/agentform.py::agent_form_context`. Nothing about editing an agent is
written twice; the two mounts differ only in which rows they list and who may open
them.

### Audience is two controls, not one radio

**Reach** — "the people I give it to" / "everyone on this box" — writes
`Agent.box_wide` and **touches no labels at all**. It is administrators-only, and
the POST re-checks that rather than trusting the render.

**Entitlement labels** — the same two-pane transfer panel `/chat/access/` renders,
posting through the same `parse_entitlement_diff` → `set_agent_labels` path. It is
an **add/remove operation over what is there now**, never a whole submitted set: a
label the actor may not label with is never in `choices`, therefore never in either
pane, therefore never in `submitted`, so it survives both directions untouched. An
administrator's label stands whatever a member does with their own.

The two **compose**. `visible_agents` is `(owned | box_wide | shared) AND
label_permitted_q`, which is a truth table, not an exclusive choice — a box-wide
agent narrowed to a department is the useful fourth row. Because both panes are
built from what the actor may label with, a member cannot see a label an
administrator set; the form says **how many** such restrictions the row carries and
never which, except for ones the reader already holds. That silence is the
entitlement non-disclosure gate, and the route matrix sweeps these routes for it.

`agent_form_context` computes the how-many sentence whenever `agent` is not
`None`, whatever this actor may label with — a member who owns no entitlements at
all still needs to be told their row carries an administrator-set restriction; only
the transfer panel itself is conditioned on there being something to offer
(`choices` non-empty). **The page renders that sentence outside the panel's own
condition** for the same reason: nested under the panel, the one reader it was
written for would never see it.

## The agent pages: the routes (chat cluster, feature B)

The section above is the FORM — one builder, two mounts. These are the routes
that mount it, and the two lists they sit on. No entry in the `## URL table` above:
that table's own preamble scopes it to the tasks it was written for, and
`/chat/w/`, `/chat/access/` and `/chat/tools/` are all documented in sections
instead. This follows that shape.

| route name | class | what it is |
| --- | --- | --- |
| `chat-agents` | A | `/chat/agents/` — the agents I work on |
| `chat-agent-new` | A | `/chat/agents/new/` — creation; there is no row yet |
| `chat-agent-edit` | O | `/chat/agents/<pk>/` — the ONE edit route both mounts share |
| `settings-agents` | S | `/settings/agents/` — the agent library, the second mount's list |

**`settings-agents` is not a `/chat/` route**, which is why it has its own
URLconf (`agents/chat/agent_admin_urls.py`) mounted from `config/urls.py` beside
`/settings/` rather than an entry in `agents/chat/urls.py` — the same call
`agents/chat/assistant_urls.py` already records for the settings assistant's own
three routes. Its view (`agents/chat/views/agents_admin.py`) reads through
`agents.visibility.labellable_agents`, the existing unfiltered read, for that
function's own stated reason: class S already means every caller is an
administrator, so `visible_agents(principal)` would hide a member's own agent
from the page that exists to administer the box's agents whenever
`admin_sees_content` is off. It has **no POST path at all** — `require_safe`, so
a POST is a declared 405 — and every row links to `chat-agent-edit` with this
page as its `?next=`. Because `agents/chat/tests/test_never_500.py` derives its
sweep from `agents.chat.urls.urlpatterns`, this route's never-500 proof lives in
`agents/chat/tests/test_settings_agents.py` instead.

**`chat-agent-edit` is class O, not S**, and that is the whole feature: an S
route refuses a non-admin at the middleware, which is exactly the person these
pages exist for. It is row-addressed, and the view turns
`agents.visibility.may_manage_agent` into the house 404 — the same shape
`chat-conversation-rename` and its siblings carry. It is also the ONE class-O
route whose administrator answer does not move with `admin_sees_content`:
`may_manage_agent` short-circuits on `is_admin` because managing an agent is
administering box inventory rather than reading somebody's content.
`identity/tests/test_route_matrix.py` names that one cell in
`_ADMIN_ALWAYS_ADMITTED_O`, and `identity.access.sees_all_content`'s docstring
records the distinction.

### Two sections on the list, and why the second exists

**"The agents I work on"** is `editable_agents` — every non-`box_wide` row this
principal may edit. On an accounts-on box an administrator sees their OWN rows
here, because `sees_all_content` is `is_admin AND admin_sees_content` and the
content setting is usually off; `/settings/agents/` is the box-wide view, one
click away.

**"Agents everyone on this box can use"** is `box_wide_agents_owned_by` — the
`box_wide` rows this principal owns. It exists because of a route that predates
this page: `chat-default-install` is class A and stamps the INSTALLING principal
as the owner, so a member can own a row everybody on the box can use and that
`may_manage_agent` refuses them. Without the section they would own a row that
is absent from their list and refused by the editor, with nothing anywhere
explaining why.

**Which sentence a row in that section gets is `may_manage_agent`'s answer for
THAT ROW**, never the section's. A member who owns one gets the read-only
sentence and no link — a link would be a link to a 404, which is the defect the
section exists to prevent, shipped in a different shape. An administrator gets
the link and a different declared sentence, saying that they administer this box
and an edit here changes the agent for everyone. That distinction is not
cosmetic: `box_wide_agents_owned_by` short-circuits on `sees_all_content`, which
answers True for **everybody on an open box** — the shipped default — so on a
default box the administrator is the only reader the section ever has, and a
single read-only sentence made it a false statement with the edit link withheld
from the one person the predicate admits.

**The restriction count is a bare number, never a name.** It is folded in the
view from two batch reads — one `agent_entitlement_ids()` and one
`labelling_entitlements` — shared by both sections through one closure, so
twenty-five rows cost what one row costs and no per-row held-entitlement read
happens here. A name would be the entitlement non-disclosure gate, which the
route matrix sweeps these routes for.

**And it is not asked at all on an open box.** Both list pages gate
`agent_entitlement_ids()` on `accounts_on()` alone — ruling A's shape, the one
`views/workstreams.py` already takes — because with accounts off a label
restricts nobody: `visible_agents` returns at its `sees_all_content`
short-circuit before the label clause is ever evaluated (spec §4.3.1). Without
the gate the fold's own meaning inverts: `mine` is empty, so "how many labels
this reader cannot manage" becomes "every label on the row", printed as
"N restrictions" to the single operator who could change all of them. The
number is **hidden, never zeroed** — a `0` would say "none" where the truth is
"not asked" — so `/chat/agents/` drops the chip (its `{% if row.restrictions %}`
already does) and `/settings/agents/` drops the column, the header cell and one
from the empty row's `colspan`. That gate is also what puts both routes in
`identity/tests/test_zero_queries.py::_MOUNTS`.

### `?next=` is echoed, never redirected to, on a GET

`agents.chat.service.validated_next_url` reads `request.POST` and nothing else,
so a `?next=` arriving on a GET link is NOT validated by it and a naive
`request.GET["next"]` redirect would be an open redirect off this box. So: the
list's own links carry `?next=`, the GET **echoes it into a hidden field and
does nothing else with it**, and the POST validates through
`validated_next_url`, falling back to the list.

**The one thing a reader can click goes through a second, stricter guard.**
`agents.chat.service.validated_next_link` requires same origin, same scheme
**and** a path on this box — `url_has_allowed_host_and_scheme` alone admits a
fully-qualified URL to this host, which is a correct answer for a redirect and a
needlessly wide one for an `href` — and `views/agents.py::_cancel_url` falls
back to `/chat/agents/` for anything else. The Cancel link used to go there
unconditionally, which was right while `/chat/agents/` was the only mount and
wrong the moment `/settings/agents/` became the second: an administrator who
cancelled landed on the member-facing list instead of the page they came from.
The raw value still reaches the hidden fields and nothing else.

**The entitlement panel carries its own copy of `next`.** It renders its own
`<form>` (it must — nested forms are illegal HTML), so the field form's hidden
`next` does not reach it; `agent_form_context` puts the mount's value in the
panel's `tp_fields` when there is one, and omits the key entirely when there is
not, so `/chat/agents/`'s own panel is byte-identical to what it was.

**A refused label save carries it too.** `_save_labels` builds the URL it hands
`parse_entitlement_diff` as that helper's `redirect_url` from the same
`validated_next_url`, so an unknown `op` or a typed id lands back on an editor
that still knows where it came from — otherwise the Cancel link on that
re-rendered page drops to `/chat/agents/`, which is the strand above reached by
mistyping rather than by cancelling. The success redirect falls back to the row
itself, not to that URL: a label edit leaves you where you were.

### One action per POST, named

`chat-agent-edit` reads an `action` field and accepts exactly two values,
`fields` and `labels` — one per control on the page. Anything else — an absent
field, a stale form, a hand-made body — is refused with the page re-rendered, a
declared sentence and a 400, never handled as a field save. That refusal is not
defensive decoration: a labels-shaped body handled as a field save would run an
empty `name` and `system_prompt` through `update_agent` and blank the row.

Each control ships its own spelling of its own name, because a template cannot
read a view constant and `agents/chat/agentform.py` is what the view imports, so
it cannot import back: `chat/_agent_form.html` writes `value="fields"` and
`agent_form_context` puts `"labels"` in the panel's own hidden `tp_fields`. The
two spellings are pinned against the view's `FIELDS_ACTION`/`LABELS_ACTION` by
test rather than trusted to agree.

The role vocabulary is refused twice on purpose: the form owns
which roles it offers (spec §4.4) and the writer refuses the same set at its own
seam, so a caller that saw no form cannot stamp an agent with a role no chat
turn can resolve. The one filter both ask lives in
`models.contracts.roles.chat_capable_roles`, below both columns, because
`agents/visibility.py` may not import `agents.chat`.

### The label editor — the edit route's second POST path

`action=labels` goes to `_save_labels`, which takes the **same four steps**
`agents/chat/views/access.py::_save` takes: build the return URL, parse the
submitted diff through `agents.chat.service.parse_entitlement_diff`, derive the
new set from what is on the row right now, and hand that whole set to
`agents.labels.set_agent_labels`.

**`parse_entitlement_diff` is the gate; `set_agent_labels` is not.** That writer
is raw — it takes an actor only to stamp the audit row, never consults
`labelling_entitlements`, and its remove closure deletes any row the diff names.
The check that a submitted id is one this principal may label with lives in the
parser, over the same predicate the form rendered from, so a stale form and a
hand-made request get the identical refusal. No caller on this surface reaches
the writer with an ungated diff, and none should.

**The new set is derived from what is there now**, never from what the form
showed: `before | submitted` for add, `before - submitted` for remove. A label
this actor may not label with is never in `submitted`, so neither expression can
touch it — which is why an administrator's label stands whatever a member does
with their own, by construction rather than by a check. A whole submitted set
would clobber, which is the same reason the two access pages post a diff.

**The refusal and the success both land back on this row**, and not through
`agents.chat.service.entitlement_row_url`: that helper builds
`reverse(route_name)` with no arguments plus an `?open=` anchor, for the two
access pages whose rows all share one URL. `chat-agent-edit` is row-addressed —
the helper cannot name it at all — and `reverse("chat-agent-edit", args=[pk])`
already *is* the row. `validated_next_url` is still honoured first, exactly as
the field save honours it, for a mount that carries one.

`AgentEntitlement.labelled_by` needs the real `User`, so the view reads it
through `identity.request.user_for_request` — never a bare `request.user`, which
`foundation/ops/tests` scans this column for.

### Where the CSS lives

**All of it in `chat/base.html`**, from the day the fragment was created —
`.chat-nav-link`, `.agent-row`, `.agent-row h2`, `.agent-group-head`,
`.agent-reach` and its two descendants, `.form-error`. `chat/_agent_form.html`
is a fragment with page consumers, and a fragment cannot own rules a page has to
load; Django template blocks do not cascade sideways, so a rule parked in one
leaf page's own style block is invisible in another's.
`foundation/ops/tests/test_css_ownership.py` enforces exactly that. The
selectors are prefixed (`.agent-row`, not `.row`) because the bare names already
mean a settings page's card and section heading under
`foundation/templates/_settings.html`.

**The transfer panel's own rules were promoted one tier** when the edit page
started rendering it. Every `.transfer-*` rule, `.filter-input` included, lived
in `foundation/templates/_settings.html` while every consumer of
`foundation/templates/_transfer_panel.html` extended it; `chat/agent_edit.html`
extends `chat/base.html`, which extends `_shell.html` directly and cannot reach
`_settings.html` at all, so `_shell.html` became the deepest common ancestor and
the block moved there — **moved, not copied**, which is what the ownership gate
fails the build on. They sit in the shell's unconditional `<style>` region, not
inside `extra_style`, because `chat/base.html` overrides that block without
`{{ block.super }}`. `.access-summary` stayed behind: it is the two access
pages' own `<summary>` class, never one the fragment writes, and the chat page
writes a plain `<summary>`.

**No `<style>` in any of the three templates, and one `<script>` on one of
them.** The pages are plain POST forms with CSRF tokens and lists of links. The
one exception is `foundation/templates/_filter_rows_script.html`, included
beside the panel and gated on the same condition its existing consumers gate it
on — so a page with no panel ships no script at all. It is pure progressive
enhancement: it hides already-rendered rows as the operator types, adds no row,
removes no checkbox and changes no `name=`/`value=` a JavaScript-off submission
relies on, and each filter input says so in its own `title=`. The only other
script these pages carry is the rail's own `chat/_menu_exclusive.html`, which
rides `chat/_sidebar.html`. `agents/chat/tests/test_agent_pages.py` pins both
halves — no script at all where there is no panel, and exactly that one include
where there is — slicing the content block away from the rail for the same
reason `test_sidebar.py` slices the other way.

## Editing a past prompt (chat cluster, feature C)

Any of your own earlier messages carries an **Edit and carry on from here**
disclosure. It opens a plain form — a textarea pre-filled with that message, an
optional file input, one button — and submitting it creates a **new conversation**
holding everything before that message, with your edited text as its newest turn.
The original is untouched.

It is a branch, not a rewind, and the reasons are recorded in the ADR: a rewind
cannot honestly un-taint (taint rows are additive-only by design, and deleting
one is precisely the laundering the platform forbids), it orphans audit rows and
attachments, and in a shared conversation it would destroy somebody else's work.
A branch you did not want is one delete away; a rewind you did not want is gone.

**What a branch does not carry:** attachments on the copied turns (there is no
copier seam; the form says so before the button), tool audit rows, pinned or
archived state, and shares. Files attached to the *edited* message itself work
normally — `start_turn` stages them exactly as any other send does, through the
same `composer_attachment_fields` reader the composer posts into.

**Who may:** the conversation's owner, or an administrator with content access.
**Not** a share recipient, even on their own message — a branch is a copy, so it
answers to the copy predicate (`agents.visibility.may_manage_conversation`, not
the wider `may_post_to`), and a recipient minting a durable conversation they own
that survives revocation of the share is a different decision from letting them
post. Nobody may edit while a turn is in flight, and that clause is
conversation-wide: the control is **hidden** for the whole thread while an answer
is running, not merely refused, because a button whose own POST answers 404 is a
page that lies. The POST refuses anyway — hiding a control is not a gate.

**Where the work is split, and why it has to be.** `agents/chat` imports
`agents/visibility`, one way, so `branch_conversation` cannot start a turn and
cannot redirect. It writes the branch and returns it; `turn_edit` validates the
text against the same `MAX_TURN_CHARS` constant `start_turn` uses **before**
calling it (so a refused edit writes nothing at all — no conversation row, no
turns, no taint), then starts the turn through the one existing turn-start
service and redirects. If that start refuses afterwards — an unbound role, an
unreachable engine, a queue that is not migrated — the branch exists with its
copied history and no answer, a state the thread page already renders honestly
with its existing banner. That is accepted and stated rather than papered over:
the alternative would be deleting a conversation the operator can already see in
their sidebar.

**The poller keeps up.** The `done` tick supplies the same three card keys the
page does, so a swapped exchange renders the disclosure exactly as a reload would
— the invariant `_done_body`, `_group_html`, `turn_group_cards` and
`_attachments_by_turn` each state in their own words. The queued and running
ticks pay nothing for it: the predicate is provably false while a turn of that
conversation is in flight, which on those two paths is the turn being polled. The
one value that cannot travel on the wire is the picker's own selection, which
lives in the thread page's query string and never reaches the poll endpoint — so
the poller carries it across on the page instead, copying the composer's own
server-rendered field into each swapped block. A no-JS page load never lost the
pick; the polled swap was the only path that did, and it no longer does.

**One rule, one definition.** Editability splits in two, and each half lives
once: `agents.visibility.is_editable_turn_row` is the per-turn half (a finished,
root-depth USER row — no principal, no query, which is why the card may ask it
too), and `may_edit_any_turn` is the conversation half (the manage predicate plus
"nothing in flight"), asked once per render rather than once per message.
`may_edit_turn` composes both, so the page's answer and the POST's answer are
built from the same two functions and cannot drift. That matters in both
directions: a card copy that grew wider would render a disclosure whose own POST
answers 404, and one that grew narrower would silently hide an available control.

**No new script, and two new template parameters.** The disclosure is a
`<details>` plus a plain form — the house's zero-JS idiom — and it includes
`chat/_attach_files.html` alone, never `chat/_composer.html`: that fragment ends
with two script blocks, and this disclosure renders once per eligible user turn,
so including it would multiply the page's pinned script counts (4 with the attach
door, 3 without) by the number of editable messages. `_attach_files.html` gains
one optional `attach_id`, defaulted so its three existing call sites are
byte-identical: without it every "+ Add files" inside every edit form would
resolve its `<label for="attach-files">` to the **composer's** input — the first
match in document order — and stage the chosen file onto a new turn instead of
onto the branch. The edit textarea carries a per-turn id on the same rule, for a
real `<label>`: the composer reaches for an `aria-label` only because the shared
fragment had dropped a visible label that used to exist, and nothing here forces
that compromise.

**A note for anyone reading `chat/_composer.html`'s own comments:** the invariant
"exactly one of these renders per page" now holds for the **composer**
(`#composer-text`, `#turn-form`, and the composer's own `#attach-files`) and no
longer for anything a turn card can also render. The edit disclosure is the first
thing to render `_attach_files.html` more than once, and it passes its own
`attach_id` precisely so the composer keeps the bare id.

**The provenance line.** A branch's thread page opens with a small banner --
"Branched from *the parent's title* at your message *N*" -- built by
`thread_context` and rendered above `.thread-scroll` in `chat/conversation.html`,
styled by that page's own `.branch-provenance` rule (Task 8's page-scoped-CSS
ruling: one consumer, so it lives in the page's own `chat_style` block, not
`chat/base.html`). It is static per conversation and sits outside every element
the poller's `insertBlock`/`swapBlock` ever touch, so the done-tick swap needs no
changes and carries no risk of clobbering it.

The title and the link are resolved through `agents.visibility.
visible_conversations`, **never a bare `Conversation.objects.get(pk=...)`** --
the same rule every other reader in this column follows, because a branch an
administrator made of somebody's thread must not hand the parent's title back to
a reader who was only given the branch.

**Two names, and the distinction matters.** `branched_from_id` is the COLUMN;
`branched_from` is the ROW the view resolved out of it, and it is `None` on two
different paths on purpose -- the parent was deleted (`SET_NULL` already cleared
the column, so `branched_from_id` is `None` too, though `branched_at_index`
survives it) and the parent still exists but this principal may not read it
(`branched_from_id` is non-null and the resolved row is `None`). Both are "the
honest version of this came from somewhere you cannot see".

On both of those paths the banner is a WHOLE sentence, never one with a hole in
it: the template's `{% if branched_from %}...{% else %}...{% endif %}` renders
the declared stand-in `BRANCH_PROVENANCE_UNNAMED` ("an earlier conversation") in
the title's place, and the `{% else %}` is load-bearing -- a bare `{% if %}` used
to render nothing there, leaving the literal "Branched from  at message 3". A
link appears only when the parent is both present and readable.

**The number is the reader's, not the row's** (whole-branch review I-2).
`branched_at_index` is `Turn.index`, a dense counter over EVERY row in the parent
-- assistant answers, tool cards, delegate turns at `depth >= 1` -- and `0` for
the first one, so rendering it said "at message 0" for a branch off the first
message and "at message 4" for the second message of a thread that had used a
tool. The column is unchanged; it is provenance, and queryable, which is the job
it was added for. What the banner renders is `agents.chat.service.
branch_point_ordinal`, a display-side count of the parent's own finished
root-depth USER turns up to that index -- the bubble a reader can point at.
Both fallback paths above carry NO number: the ordinal is counted over the
PARENT's rows, and a number counted over a deleted thread, or over one this
reader cannot open to check, is one nobody can verify. There the banner is
"Branched from an earlier conversation." and nothing more, which is why the
whole `<p>` is gated on `is_branch` rather than on the tail.

**Two queries, and only for a branch.** The parent lookup runs only when
`branched_from_id is not None`, the ordinal `.count()` only when that lookup
found a readable row -- both threaded through the same `settings_row` every
other visibility call on this page already reuses, so a thread of any length
pays them once, never once per turn. The ordinal is bounded by the PARENT's
length, not this conversation's. `agents/chat/tests/test_thread_meter.py::
TestTheContextMeter::test_the_meter_costs_the_same_on_a_short_and_a_long_
conversation` -- the page's existing flat-cost equality pin -- was extended
rather than copied: both the short and the long conversation it measures are
branches of the same parent, which now carries a real user turn so the ordinal
is a live number on both renders, and a later reader who moved either read onto
a per-turn path would turn this pin red rather than leaving it silently
unexercised.
