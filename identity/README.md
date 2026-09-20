# identity/ — who exists, what posture this box is in, and what has been done to it

The fifth column, and the base of the platform: it answers questions about
**principals**, not questions about content. IA-1 ([the design
spec](../docs/superpowers/specs/2026-08-29-identity-and-auth-design.md))
gives every box a real user model, three security postures, and an
append-only audit trail; IA-2 is what turns "signed in" into "may see
this specific row" for anything finer than ownership.

## 1. What this column is

`identity/` imports `foundation`, Django, and its own `contracts` — and
**nothing** from `agents/`, `tools/` or `models/`. That single rule has
two consequences a reader of this column has to hold onto, because
everything else follows from them:

- **Identity cannot answer "which documents".** It has no way to import
  `tools/rag`, so it can never build a `Document` queryset. What it
  answers instead is "which principal" — `is_admin`, `sees_all_content`,
  `owner_fields` — and every other column turns that answer into a
  queryset of its *own* rows. That is what makes this column a **base**
  rather than a **hub**: a hub would know about documents, jobs and
  conversations; a base only knows about principals and postures, and
  every feature column builds its own visibility rule on top of that.
- **The owned-rows registry names its models as strings.** `identity/
  contracts/ownership.py::OwnedRows.model` is an `"app_label.ModelName"`
  string, resolved by `django.apps.apps.get_model` at command time —
  never imported — because `adopt_open_rows`/`reassign_owner` need to
  walk every owned table in `agents`, `tools` and `models`, and this
  column may import none of them. Each owning column registers its own
  tables from its own `AppConfig.ready()`, exactly the way roles, job
  kinds and tools already register themselves — a new owned table is a
  **registration**, not an edit to a command that would otherwise
  silently skip it.

## 2. The four seams every column may import

`identity.contracts.*`, `identity.access`, `identity.request` and
`identity.audit` are the four modules every column may import — a NAMED
exception to rule 2 of the import law, the same shape `models.registry.
bindings` already has. Enforced as an ALLOWLIST, `foundation/ops/tests/
test_import_law.py::IDENTITY_PERMITTED`: every other module under
`identity/` — `identity.models`, `identity.views`, `identity.services`,
`identity.forms`, `identity.middleware`, and any later addition
(`identity.ownership`, `identity.gate`, and so on) — is off-limits to
every other column with **no exception** and no separate list to update,
because a module absent from the allowlist is closed by construction.
`identity.testing` (test support, `identity/testing.py`) is likewise
absent from the allowlist — reachable by every column's TEST files, which
this gate exempts entirely, and closed to production code by the same
mechanism as everything else.

| Seam | What it answers |
|---|---|
| `identity.contracts.*` | Pure data: `Principal` (and the two principal-only-constructor files, itself and `identity/request.py`), the posture names, the closed audit-action vocabulary, and the owned-rows registry. A rule-1 pure leaf — no Django, no database, no I/O — pinned by `identity/tests/test_purity.py`, a subprocess with no settings module configured at all. |
| `identity.access` | "May this principal administer this box" (`is_admin`), "may this principal read other people's content" (`sees_all_content`), "is this box requiring accounts at all" (`accounts_on`), and "what two columns do I stamp on a row this principal is creating" (`owner_fields`). Every function tests `accounts_on()` FIRST and returns its open branch before touching another table — that is how "an open box never runs a permission query" is structural rather than promised. |
| `identity.request` | `principal_for_request(request)` — the ONE place in the codebase that turns an HTTP request into a `Principal`. Every view reaches it; none of them builds a principal itself. |
| `identity.audit` | `record(actor, action, ...)` — the ONE writer of `AuditEvent` rows, and the only module that may touch `AuditEvent.objects` at all (not `.create`, not `.filter`, not `.update`, not `.delete` — an AST guard forbids the whole manager, because `.update()`/`.delete()` never call `save()` and a guard that only watched for `create` would let either one through). `recent`/`for_target` are its two readers. |

The private modules — `identity.models` (the three tables this
column owns), `identity.services` (the writes that can lock an operator
out of their own box: `create_user`, `deactivate_user`, `set_superuser`,
`set_posture`, all raising `ServiceRefused` rather than failing silently),
`identity.views`/`identity.forms` (the pages), `identity.middleware`
(the gate), and `identity.ownership` (the owned-rows walk, resolving the
pure registry's model strings against a configured Django) — are reached
only by this column's own pages, its own management commands, and
`identity/admin.py`.

**`/admin/` reaches the user model only** (A-2, security round 3).
`auth.Group` is unregistered at module level in `identity/admin.py`
rather than left as Django ships it or subclassed to behave: the
identity page's own groups page is already the one door for a group's
create and delete (groups have no rename primitive in `identity.
services`), each writing the audit row `identity.services` writes for
it, and the stock `Group` admin's forms wrote none
of them while also being able to cascade-delete — via
`EntitlementGrant.group` and `Share.group`, the two reverse foreign
keys this platform hung off the model — every entitlement grant and
every share whose subject was that group. `AuditEvent` stays
unregistered for the reason already given above (an append-only table
with a delete button is not append-only); together the two exclusions
mean `/admin/` is `identity.User` and nothing else.

## 3. The three postures

`IdentitySettings.posture` (a database row, singleton, never an
environment variable — `compose.yaml` starts `web`, `worker` and
`watcher` from one image with independently supplied environments, and
the worker is where turns actually run tools, so a posture set on one
process and unset on another would be a security posture that is only
half-enforced):

| Posture | Accounts required | Every account is | What the operator sees |
|---|---|---|---|
| `open` (shipped default) | No — one shared `Principal("open", "box")` answers every request | — | No login, no nav change, no 403. Identical to a box with no identity column at all. |
| `personal` | Yes | a **superuser** | A users page with no superuser toggle (there is nothing to toggle), a settings page, and every content-visibility question answered by `is_admin`. |
| `enterprise` | Yes | whatever the operator chooses | The same pages, with the superuser toggle visible on the users page. |

**Every account in `personal` is a superuser.** `identity.views.
user_create` forces `is_superuser=True` on that page in `personal`
posture, and `identity.admin.IdentityUserAdmin.save_model` does the same
for a brand-new account created through `/admin/` — a household box with
three accounts has no use for a "member" who cannot administer it.

**Flipping to `enterprise` demotes nobody.** Moving from `personal` to
`enterprise` is a pure switch on one settings row: no user's
`is_superuser` flag is touched. An operator who wants a narrower
membership demotes accounts by hand, one at a time, through the users
page — the posture switch itself never does it for them.

**The users page (`/identity/users/`) creates, deactivates/reactivates,
promotes/demotes, and resets a password.** `identity.services.
create_user`/`deactivate_user`/`reactivate_user`/`set_superuser`/
`set_password` are its whole vocabulary — every one of them audited,
every one of them refusing to leave the box with zero active
superusers. **Renaming a user is not in IA-1**: there is no catalogue
action for it, and neither this page nor `identity/admin.py` offers
one — `Principal.key` is a user's primary key, never their username, for
exactly this reason (a rename must not orphan every row a person owns),
so a username is display-only today and changing it is a gap this
column carries rather than a choice hidden behind the page.

`identity.services.set_posture` refuses to leave `open` unless an active
superuser exists, `DEBUG` is off, and `SECRET_KEY` is a real value —
three conditions checked at request time (not only at boot), because the
image starts with `migrate && <server>` and flipping the posture on a
*running* box with `DEBUG=1` would otherwise change nothing until the
next restart. **Reducing** a posture (moving back towards `open`) is
never refused by any of them.

## 4. Administering is not reading

`is_admin(principal)` and `sees_all_content(principal)` answer two
different questions, and the whole point of this column's content model
is that a page must ask the right one:

- **`is_admin`** — may this principal reach an ADMIN SURFACE: the model
  console, the queue settings, the users page, the posture page,
  `/admin/`. True for `OPEN_PRINCIPAL` and an active superuser; false for
  `ANONYMOUS` and every service principal.
- **`sees_all_content`** — may this principal read OTHER PEOPLE'S
  CONTENT: their conversations, Ask history, generated images, agent and
  flow bodies, document bytes, and the payload/answer text of a job they
  did not start. `open` → always true. Otherwise → `is_admin(principal)
  and admin_sees_content` — a SETTING, not a posture branch:
  `personal` and `enterprise` answer this identically, because the
  choice belongs to the setting, not to which posture chose to switch it
  on.

`IdentitySettings.admin_sees_content` **defaults to `False`**, and that
default is the decision: administering a box already means seeing every
ROW it needs to run — a job's kind, owner, state and progress; a
document's title, category and status — without reading anyone's
content. Turning content visibility on is a deliberate act on the
posture page, audited exactly like the posture itself, and read per
request so it takes effect with no restart.

| | Rows (kind, owner, state, progress) | Content (the payload, the bytes, the text) |
|---|---|---|
| Member | Their own only | Their own only |
| Administrator, `admin_sees_content` off | **Every** row on the box | Their own only |
| Administrator, `admin_sees_content` on | Every row | **Every** row's content too |

## 4b. Deploying to an existing database

`manage.py identity_repair_migration_history` — STEP 0 of the IA-1
deploy on any box that existed before this column did (see
[docs/OPERATIONS.md](../docs/OPERATIONS.md)'s deploy section for the
full contract and the exact refusal text). Such a box already has
`django.contrib.admin`'s migrations applied, and `admin.0001_initial`'s
`swappable_dependency` on the user model's own first migration makes
`manage.py migrate` refuse outright, before applying anything at all.
This command writes no row to `auth_user` or `django_admin_log`. Inside
one transaction, it forgets those rows, applies `identity.0001_initial`,
and recreates them; then it tells you to run `manage.py migrate` for
everything else, including `identity.0002_repoint_admin_log_fk`. It is
not otherwise data-free — the nested `migrate` it runs creates the
`ContentType`/`Permission` rows any `migrate` run creates for new
models, via Django's own `post_migrate` signal. It also refuses by name
on shapes it was never written to guess about (some `identity.*`
migration applied without `identity.0001_initial` itself; `identity_
user` existing with no `identity.*` row recorded at all — a half-run).
A fresh install needs none of this: nothing has applied `admin.
0001_initial` yet, so `migrate`'s own plan already applies `identity.
0001_initial` first.

## 5. Turning accounts on

The four-command sequence, verbatim (matching `docs/OPERATIONS.md`'s
"Turning on accounts" section — read that section for the full contract
each command carries):

```bash
manage.py createsuperuser
manage.py adopt_open_rows --user <username> --dry-run
manage.py adopt_open_rows --user <username>
manage.py identity_posture personal        # or: enterprise
```

**Order matters.** Create the superuser, adopt the box's existing
history, *then* switch the posture — switching first leaves the new
administrator looking at their own empty box until they adopt.

## 5b. Rows a command made

A row a **shell path** creates — `manage.py agent_turn`, `ingest`,
`ask`, the folder watcher — is stamped `("service", "local")`, on the
payload and on the row alike, never `("open", "box")`. Those rows are
visible to administrators and to nobody else, whatever the content
setting says (`identity.access.owned_rows_q`/`may_read_owned_row`'s one
carve-out from the administer/read split — a row nobody is behind has
nobody for anything to be private from). `adopt_open_rows` never claims a service-owned row — an
automated action attributed to a person who never performed it would be
a false record, not a convenience. `reassign_owner --from service` is
the one deliberate way to hand one of those rows to a person instead.

## 6. What IA-2 did add

**Two tables, in this column.** `Entitlement` (a named label, e.g.
"Finance") and `EntitlementGrant` (who holds one, USER XOR GROUP, and in
what role — `owner` or `member`, `identity.models.EntitlementGrant.Role`).
Grants stop living on the `Agent` row; `agents.ToolEntitlement` and
`tools.rag.DocumentEntitlement` hold the tool and document labels in
their own columns, both pointing back at `identity.Entitlement`.

**Three access questions, all answered from `identity/access.py`:**
`held_entitlement_ids(principal)` (direct grants plus every group's), `may_
see_unlabelled(principal)` (the library-posture question — open lets
everyone see an unlabelled document, locked restricts it to
administrators), and `owned_entitlement_ids(principal)` (which
entitlements this principal may administer — grant, revoke, label with —
without being a superuser, the entitlement-owner role). `sees_all_content`
and `is_admin` are IA-1's; IA-2 adds no third predicate for "may
administer", it reuses the owner role's own grant row.

**Two picker readers**, because a label or share picker needs
entitlement and account **names**, which no column outside identity may
read: `identity.access.labelling_entitlements(principal)` (every
entitlement this principal may label with, `(id, name)` pairs) and
`identity.access.share_subjects(principal)`/`grant_subjects(principal)`
(the users and groups a conversation may be shared with, or an
entitlement granted to — two readers because `share_subjects` excludes
the caller and `grant_subjects` does not, decision 18).

**The cascade registry**, so a delete can reach columns `identity/` may
not import (rule 4): `identity/contracts/cascades.py::EntitlementCascade`
is a pure dataclass (`key`, `label`, a dotted-path `handler` string) that
`agents/` and `tools/rag/` register themselves from their own
`AppConfig.ready()`; `identity/cascades.py::cascade_counts`/`run_cascades`
resolve and run them, inside `identity.services.delete_entitlement`'s one
transaction, so nothing is half-deleted and the confirmation's counts
can never drift from what actually happens.

**Four pages**: `/identity/groups/` and `/identity/groups/<pk>/edit/`
(create/rename/delete a group, manage membership); `/identity/
entitlements/` and `/identity/entitlements/<pk>/` (create/rename/delete
an entitlement, grant/revoke it, and — for an owner as well as an
administrator — the page an owner reaches to label documents with it).

**Four routes, and their classes**: `identity-groups` and
`identity-group-edit` are `S` (superuser only — groups are operator
structure, not something an owner administers); `identity-entitlements`
is `S` (creating/renaming/deleting the label itself, and seeing every
entitlement on the box, is operator policy); `identity-entitlement-edit`
is **`R`**, not `S` — an entitlement **owner** reaches this same URL to
grant, revoke and label with the entitlement they own, and `R`'s rule
(`is_admin` sees every row; a non-admin's view is narrowed inside the
view itself, by `owned_entitlement_ids`) is exactly the shape that needs:
classifying it `S` would have the gate middleware refuse a legitimate
owner before the view ever ran, which is why this one route is the
finer class rather than the coarser one every other page here uses.

**The retrieval visibility argument** (`tools/rag/access.py::
readable_documents`/`listable_documents`, the one place a document list
gets filtered below "signed in") and the **chunk-metadata cache** a
label-aware retrieval filter needs to avoid a join per search result are
both `tools/rag/`'s own — see that column's README.

**Two read-only admin-ergonomics panels** (spec §22.34), both views over
data this column already owns:

- **The entitlement-reach panel**, on `/identity/entitlements/<pk>/`
  itself — `identity.services.entitlement_reach(entitlement)` names
  every grant, document, tool, model set, agent and flow the
  entitlement touches. It is an **alias** of the same delete
  confirmation's own counter (`entitlement_delete_counts`), not a
  reimplementation, so the reach a reader sees on the page and the
  counts they see when they start a delete **can never disagree** —
  the moment this function grew a body of its own is the moment it
  became the second counter this decision exists to prevent.
- **The per-user effective-access panel**, on `/identity/users/` —
  `identity.access.effective_entitlements(user)` names, per account,
  every entitlement it holds and whether it is DIRECT or VIA a named
  group, with that entitlement's own reach (the same `entitlement_reach`
  above, memoised per entitlement rather than per account in the view,
  since two accounts sharing an entitlement should not pay for its
  cascade queries twice in one request).

## 7. Workstreams v1: two more access functions, and one route that is new

**Workstreams are not an identity concept**, even though a stream carries an entitlement WALL and
an entitlement TAINT set. `agents.Workstream`, `agents.WorkstreamScopeEntitlement` and
`agents.WorkstreamTaint` all live in `agents/`, not here — this column answers questions about
PRINCIPALS and SUBJECTS, and a workstream is neither; it is a row another column owns that happens
to carry two sets of entitlement ids. `identity/access.py` grows exactly two functions for the
sharing phase, both plain readers with no write of their own and no new table:

- **`entitlement_ids_for_subject(*, user=None, group=None) -> frozenset[int]`** — every
  entitlement id a **subject** (a `User` row or a `Group` row) holds, as opposed to
  `held_entitlement_ids(principal)`, which answers for the **caller**. The workstream share
  gate's subject is the recipient a sharer picked off a `<select>`, not the principal asking the
  question, so the existing function's shape does not fit. Same join `_grant_ids` already runs
  (`EntitlementGrant.objects.filter(user=...)` or `filter(group=...)`), keyed by subject instead
  of by principal — a fifth sanctioned identity import for one function would have been the
  alternative, and this is the one join every column may already reach through `_grant_ids`'s own
  seam. **A group is checked against its own row**, never against an aggregate of its members':
  a group is the subject of a grant in this codebase (`grant_user_xor_group`), so "does this group
  hold E" is a row, not a computation over membership. Empty when accounts are off, exactly as
  `held_entitlement_ids` is.
- **`entitlement_names(ids) -> tuple[tuple[int, str], ...]`** — `((id, name), ...)` for `ids`, in
  name order, an id with no row silently dropped. The second function the sharing gates read from
  this module — `agents.visibility.name_for_viewer` calls it to turn a missing-id set into
  `((id, name), ...)` pairs for whichever gate is asking. **Also new here, not IA-2**: its first
  caller is WS-1's wall editor (`chat-workstream-scope`'s page — the stream's SETTINGS page since
  the later owner-driven settings-page split, `agents/chat/README.md`'s own "The stream settings
  page" section — rendering `held_entitlement_ids` as names), and WS-2's sharing gates are its
  second — not the function's origin, contrary to an earlier draft of this section.

**`agents/visibility.py` and `agents/workstreams.py` build the two gates on top of those readers**
(full design in `docs/superpowers/specs/2026-09-03-workstreams-design.md` §12): a **share-time**
gate (`share_workstream`) refuses a share when the recipient lacks any of the stream's tagged
entitlements, naming the ones the **sharer** themselves holds (ruling B — the sharer does not
necessarily hold every tag a previous recipient's own turn may have brought in); and a
**read-time** gate (`agents.workstreams.stream_access`) re-checks the reader's **current** grants
against the stream's tags on every non-owner page view, because tags grow and grants are revoked
after a share is made. A failing share goes **dormant** — computed, never stored, so there is
nothing here for a moved grant to leave stale.

**One new route, and it is the platform's one scoped exception to the 404 house rule.**
`chat-workstream` (class `O`) may now answer **403**, not only 200 and 404: a principal holding a
real, live `Share` row whose grants no longer cover the stream's tags gets an explicit page naming
what is missing, capped at five names with the rest counted
(`agents.visibility.name_for_viewer(..., disclose_all=True)`). **This is the first route on the
platform that discloses an entitlement's name to somebody who neither owns nor holds it anywhere
else** — every other name a non-admin sees comes from `labelling_entitlements`, filtered to
`owned_entitlement_ids`, or is behind class `S` (`identity-entitlements`, the accounts page's
`effective_entitlements`). `identity/tests/test_route_matrix.py::
test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder` sweeps every
non-`S` route with a signed-in non-holder and pins that `chat-workstream` stays the only one. The
new route, `chat-workstream-share` (class `O`, share-and-revoke on one URL, the same shape
`chat-conversation-share` already uses), is added to `ROUTE_RULES` in the same commit.

**Eighteen workstreams-phase audit-action constants, and sharing adds none of its own.**
`share_workstream`/`revoke_workstream_share` reuse `SHARE_ADDED`/`SHARE_REVOKED` with
`target_type="workstream"` — one pair for both conversations and streams, rather than a second
pair splitting "who was this shared with" across two vocabularies. The eighteen, all in
`identity/contracts/actions.py`: `DOCUMENT_CONTAINED` (a document's containment set or cleared);
the stream's own lifecycle, ten — `WORKSTREAM_CREATED`, `_RENAMED`, `_DELETED`, `_ARCHIVED`,
`_UNARCHIVED`, `_INSTRUCTIONS_SET`, `_SCOPE_ADDED`, `_SCOPE_REMOVED`, `_UPLOAD_DEFAULT_SET`,
`_INCLUDE_UNIVERSAL_SET` (round 17, the "use the full document library" toggle — an access
consequence, same reasoning as `_UPLOAD_DEFAULT_SET`, not a label like the un-audited
description field); the pin pair, `DOCUMENT_PINNED`/`DOCUMENT_UNPINNED`; the taint/untaint four,
`WORKSTREAM_TAINTED`/`CONVERSATION_TAINTED`/`WORKSTREAM_UNTAINTED`/`CONVERSATION_UNTAINTED`; and
`WORKSTREAM_CONSOLIDATED`, which landed with consolidation. All eighteen are on the tree and in
`AUDIT_ACTIONS` as of this phase's close.

## 8. Login lockout (S7)

`identity/throttle.py` refuses a sign-in after **5 failed attempts for the same username within
15 minutes** (`FAILURE_THRESHOLD`, `WINDOW`) — `identity.views.LoginView.post` checks
`throttle.locked_out(username)` before Django's own `AuthenticationForm` ever runs, and
`identity/views.py::LoginView.form_invalid`'s comment about a deferred lockout hook is now filled
in.

**No new state, no new dependency.** The count comes from rows this page already writes on every
failure — `identity.audit.failed_logins_since(username, since)`, a case-insensitive `.count()`
query added beside `recent`/`for_target` in `identity/audit.py`, the one module the AST sweep in
`foundation/ops/tests/test_column_boundaries.py` allows to touch `AuditEvent.objects` at all. No
table, no migration, no `django-axes`: that package is a fine one and the wrong fit here, bringing
its own models, migrations, admin registration and settings surface to do what a read over rows
already on disk can do.

**One key, at the write and the read (review round 1).** `identity.throttle.canonical_username`
strips surrounding whitespace and applies Unicode NFKC normalisation — the same pipeline
`django.contrib.auth.forms.UsernameField.to_python` already runs a username through on its way
into `AuthenticationForm`. `LoginView.form_invalid` canonicalises **before** writing the
`login_failed` row, and `throttle.locked_out` canonicalises before reading rows back, so the value
on disk and the value being matched are always the same string — a failed run submitted as
`" alice "`, or under any other Unicode spelling that collapses to the same name, counts toward
`alice`'s own lockout exactly as five attempts spelled identically would. Casefolding stays the
query's job (`target_label__iexact`), so the audit row still shows what was actually typed.
Audit rows are therefore genuinely written **under the canonical key** — the claim above ("rows
this page already writes") holds because both sides of the comparison now agree on what that row's
key is.

**The message is identical for every username — real, misspelled, or never created.** This is
load-bearing, not a copywriting choice: the login page's one enumeration-resistant property is that
`AuthenticationForm`'s generic error covers wrong-password, no-such-user and inactive alike, and a
lockout message that only appeared for accounts that exist would hand that oracle straight back.
`locked_out` answers off audit rows alone, so a non-existent username locks out exactly like a real
one that has drawn the same number of failures. The refusal message names how many minutes remain
(`throttle.seconds_remaining`, formatted into `_LOCKED_OUT_MESSAGE`) — this adds no signal, because
`seconds_remaining` reports the same fixed `WINDOW` for any locked-out username, real or not; it
gives that function its first production caller rather than leaving it write-only.

**Refused before the password is hashed.** A locked-out `POST` never binds `request.POST` into
`AuthenticationForm` — doing so would run `authenticate()` and pay the password hash Django's
`AuthenticationForm.clean()` computes, which is both the CPU-exhaustion half of this finding and a
timing oracle the refusal exists to remove. The refusal itself is **not** audited as a `login_failed`
row: counting it would extend the window on every subsequent knock and the account's real owner
would never get back in during an active attack. The attempts that caused the lockout are already
in the log.

**A successful sign-in does not clear the counter.** `AuditEvent` is append-only (`identity/
models.py`'s own docstring), so there is no row to delete or flip on a correct password, and
`locked_out` counts failures within the window regardless of a `LOGIN` row landing in between. In
practice this rarely matters — a correct password before the threshold means there is nothing to
clear — but it is stated here rather than left to be discovered: an account can still lock a few
minutes after a successful sign-in if failures from just before it are still inside the window.

**The accepted trade-off, in full.** A per-username window lets someone who knows a username lock
its owner out for the window — that is inherent to username lockout, and the alternative (per
source address) is worse on a shared network address. Concretely: an attacker who knows (or
guesses) a real username can keep it locked out indefinitely by sending `FAILURE_THRESHOLD` (5)
requests every `WINDOW` (15 minutes) — a small, low-cost denial of service against one account,
traded for the CPU-exhaustion and credential-guessing protection the lockout buys everyone. The
window is short and every attempt is audited either way. There is no unlock command (see
`docs/OPERATIONS.md`'s accounts section): waiting out the window is the only way to clear a
lockout, deliberately, because an unlock command is a second authentication path.

## Tests

`identity/tests/` covers every module above, plus two whole-repo
guards worth knowing about: `identity/tests/test_route_matrix.py`
sweeps every registered route name against three principals (anonymous,
member, admin) and both settings of `admin_sees_content` — the open
principal is covered separately by `identity/tests/test_zero_queries.py`
(no identity table is queried at all in `open` posture) and `identity/
tests/test_middleware.py::TestOpenPosture` (every route answers exactly
as it does today) — and `identity/tests/test_purity.py` pins
`identity/contracts/` as a rule-1 pure leaf the same way `agents/
contracts/tests/test_purity.py` does for its own column.

**Spec §4.3's eight structural guards: all eight now built.**
`foundation/ops/tests` carries the two that grow
(`test_a_principal_is_only_constructed_in_the_files_that_may`'s
`_PRINCIPAL_CONSTRUCTORS`, and `test_no_chat_module_queries_the_three_
owned_models_directly`) and five that are new to IA-1:
`test_the_principal_sweep_reaches_every_column`,
`test_no_identity_module_imports_another_column`,
`test_no_column_imports_identitys_private_modules`,
`test_no_module_outside_audit_touches_auditevent_objects` (with
`test_the_audit_gate_catches_update_and_delete_not_only_create` as its
own anti-vacuous pin), and
`test_no_view_outside_identity_reads_request_user`
(`foundation/ops/tests/test_column_boundaries.py`) — an AST sweep over
`tools/`, `models/` and `agents/` for a direct `request.user`/
`.is_superuser`/`.is_staff` read outside this column, with an empty
allowlist today (nothing outside `identity/` does this) and its own
anti-vacuous pin proving the sweep would catch one. The eighth,
`test_rag_views_reads_documents_through_the_access_module`, guards
`tools/rag/access.py::readable_documents`/`listable_documents` — deferred
through IA-1, when that module did not exist yet because document-level
visibility was IA-2's. **Built now**: `tools/rag/access.py` exists, this
guard is live, and every document-serving view goes through it rather
than a bare `Document.objects` query.

Design: `docs/superpowers/specs/2026-08-29-identity-and-auth-design.md`.
See also `docs/adr/0013-inference-execution-queue.md` (the acting
principal travelling in every job payload) and `docs/adr/
0015-agent-layer-and-tool-contract.md` (the acting rule reversing who a
turn runs as).
