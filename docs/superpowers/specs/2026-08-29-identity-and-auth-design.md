# Identity & Auth — design spec

**Date:** 2026-08-29
**Amended (2):** 2026-08-30 — second owner directive, in three parts: model labels become model
**SETS** (§6.10, §9.5, §22.33 — "so one change can impact many different entitlements/users");
**agents and flows** become entitlement-restrictable through the existing visibility seams
(§6.11, §9.6); and the **library upload door** joins the vision door under §9.3. A supplement the
same day added the **admin-ergonomics** requirements (§15, §22.34): bulk document labelling, an
entitlement page that shows its full reach, and a per-user effective-access panel. Sections
touched: §6.10, §6.11, §9.3, §9.5, §9.6, §11.3, §13.2, §15, §16.4, §17, §18.2, §19, §21, §22.
**Amended:** 2026-08-30 — owner directive on least-privilege TOOL and MODEL access. Integrated
in place rather than appended as an addendum, following this document's own handling of the
2026-08-29 owner decision at §22.29: the sections that carry the mechanics are amended where
the mechanics live, and the decision itself is recorded in §22 (items 31 and 32). The sections
touched are §6.10 (new), §9.3, §9.4, §9.5 (new), §11.3, §13.2, §16.4, §17, §18.2, §19 and §22.
**Status:** Design. IA-1 built and merged; IA-2 planned. Written against `HEAD = c00c3b5`.
**Phase:** the first of the three phases ADR 0015 G13 names as following it
(`docs/ROADMAP.md`, "Identity & Auth" under Phase 1.6).
**Lands as:** two plan-sized halves — **IA-1** and **IA-2** (§18) — then ADR 0016, written last.

This spec designs accounts, groups, entitlements, ownership and sharing for a platform that
today has none: every surface on the box is unauthenticated, and
`agents/chat/principal.py::principal_for_request` answers every request with one
`Principal("open", "box")`. It adds a fifth top-level column, `identity/`, and fills in the
mechanics — tables, constraints, the request→principal flow, the one retrieval filter point,
a route-by-route rule, an audit vocabulary, and a test strategy — that an implementer needs.

Two things shape every decision below. The platform must serve an enterprise that wants
individual logins, groups, entitlements and single sign-on; and it must serve one person
running the box at home who wants none of that and should never see it. The answer is
**postures, not editions**: one codebase, one set of tables, and machinery that is invisible
until a row says otherwise.

No model or product names appear anywhere in this document. The repository is going public and
ADR 0010's third amendment already forbids the platform from naming a model for the operator.

---

## Table of contents

1. [Context](#1-context)
2. [Owner decisions, restated](#2-owner-decisions-restated)
3. [Postures, and the one truth](#3-postures-and-the-one-truth)
4. [The `identity/` column and the import law](#4-the-identity-column-and-the-import-law)
5. [Principals, and the acting rule](#5-principals-and-the-acting-rule)
6. [Data model](#6-data-model)
7. [Access rules, and the visibility function bodies](#7-access-rules-and-the-visibility-function-bodies)
8. [Documents: labels, retrieval, and the chunk-metadata cache](#8-documents-labels-retrieval-and-the-chunk-metadata-cache)
9. [Tools: entitlements replace `tool_keys` as grant truth](#9-tools-entitlements-replace-tool_keys-as-grant-truth)
10. [Ownership, sharing, and adoption](#10-ownership-sharing-and-adoption)
11. [Every route, its class, and its rule](#11-every-route-its-class-and-its-rule)
12. [Deactivation, cascades, and the last-admin guard](#12-deactivation-cascades-and-the-last-admin-guard)
13. [Audit](#13-audit)
14. [Sessions, cookies, and the DEBUG check](#14-sessions-cookies-and-the-debug-check)
15. [Admin surfaces](#15-admin-surfaces)
16. [Testing](#16-testing)
17. [Migrations, in order](#17-migrations-in-order)
18. [Phasing: IA-1 and IA-2](#18-phasing-ia-1-and-ia-2)
19. [Documentation](#19-documentation)
20. [Non-goals](#20-non-goals)
21. [Deferred, with the hook each relies on](#21-deferred-with-the-hook-each-relies-on)
22. [Decisions the author made](#22-decisions-the-author-made)
23. [Author concerns](#23-author-concerns)

---

## 1. Context

### 1.1 What exists today

The box is unauthenticated end to end. `config/urls.py` mounts six URL trees plus Django's
`/admin/`, and none of them checks who is asking. `config/settings.py` declares
`ACCOUNTS_REQUIRED`, read in exactly one place —
`agents/chat/principal.py::principal_for_request`, whose `True` branch raises
`NotImplementedError` naming this phase. That was deliberate: a setting whose `True` branch
quietly returned an unauthenticated principal would be a security hole wearing a setting's name.

What the agent phase built and left ready, rather than open:

| Seam | Where | What it does today |
|---|---|---|
| request → principal | `agents/chat/principal.py::principal_for_request` | returns `OPEN_PRINCIPAL`; the only such point in the codebase, pinned by an AST guard |
| the principal type | `agents/contracts/tools.py::Principal`, `::PRINCIPAL_KINDS`, `::OPEN_PRINCIPAL` | a frozen, Django-free two-string value; a closed kind vocabulary |
| tool availability | `agents/contracts/tools.py::granted_tools` | the one function that answers "may this caller call this tool"; takes a `Principal` it does not yet read |
| per-kind visibility | `agents/visibility.py` | six functions (`visible_conversations`, `visible_agents`, `installed_agent_slugs`, `visible_flows`, `owner_fields`, `create_conversation`); every one returns everything in open mode |
| ownership columns | `agents/models.py` — `Agent`, `Flow`, `Conversation` | `owner_kind`/`owner_key`, stamped on every create, each with its own index |
| one retrieval filter point | `tools/rag/retrieval.py::retrieve_nodes` | all three retrieval callers go through it |
| the audit row | `agents/models.py::ToolInvocation` | one row per tool call, carrying `principal_kind`/`principal_key` |

Two permanent guards already police those seams and both grow in this phase:
`foundation/ops/tests/test_import_law.py` (which files may construct a `Principal`; which
modules a column may import) and `foundation/ops/tests/test_column_boundaries.py` (no module
under `agents/chat` touches `Conversation`/`Agent`/`Flow` `.objects` directly).

### 1.2 The four facts that constrain the whole design

**One box, one organisation.** Nothing here is multi-tenant. Entitlements partition a single
library among the people who share one machine; they do not partition machines.

**The database is the only durable state the platform trusts.** The queue's worker, the
watcher and the web process are three processes started from one image with independently
supplied environments (`compose.yaml`). A security posture carried in an environment variable
can therefore differ between two processes in the same box. §3 turns that fact into a decision.

**Django's auth is maintained; ours would not be.** Users, groups, password hashing and
validation, session invalidation on password change, `login_required` semantics, the
`ModelBackend`'s `is_active` check — all of it exists and is patched by somebody else. This
phase adds exactly what Django lacks: entitlements, and the postures that hide them.

**Retrieval has one filter point and it must stay one.** `retrieve_nodes` is called by
`tools/rag/retrieval.py::answer_question`, by `tools/rag/views.py::SearchView`, and by the
`rag.search` runner in `tools/rag/tools.py`. A visibility rule that lived in a runner instead
would be a second copy, and two copies of a visibility rule is how two surfaces come to
disagree about what a person may see.

---

## 2. Owner decisions, restated

Every row is binding. The "why" column is the owner's own reasoning where they gave one, and
the reasoning this spec supplies where they did not.

| # | Decision | Why | Realized in |
|---|---|---|---|
| 1 | Enterprise-grade capability — individual logins, groups, a user holding many entitlements, entitlements covering RAG resources, conversations private but shareable, SSO | the platform must be sellable into an organisation without a fork | §6, §7, §10, §21 |
| 2 | And it must serve a household running the box with no admin roles at all | "postures, not editions": one codebase, admin machinery invisible unless enabled | §3 |
| 3 | Do what is right, not what is easy, given today's code — better right now than larger changes later | the seams get harder to move once rows exist | §4, §5 |
| 4 | But do not overcomplicate: no minor items that are complex to implement | a security layer nobody understands is not a security layer | §20, §22 |
| 5 | Commercial licensing is a LICENSE matter, never a code gate | `docs/BUSINESS.md`'s "Decision: dual-licensing under a copyleft core"; a license check in code is a lie an operator can delete | §20 |
| 6 | Use Django auth wherever it does the job; add only what Django lacks | §1.2 | §6.1, §12, §15 |
| 7 | Three postures — `open` (today), `personal`, `enterprise` — as a DB row, admin-editable | an operator changes posture, not a deploy | §3 |
| 8 | `personal` → `enterprise` is a switch, not a migration | the tables exist in every posture; only the questions asked of them change | §3.3 |
| 9 | Open must keep working: every visibility function keeps its return-everything branch, and an open box never runs a permission query | the household box must not pay for the enterprise's machinery | §3.4, §7 |
| 10 | `Principal(kind, key)` moves to a new pure leaf `identity/contracts/`, Django-free | it is now the base type of the whole platform, not an agents-column detail | §4, §5 |
| 11 | `identity/` is a fifth top-level column below `agents`/`tools`/`models`; it imports only foundation + Django; every column may import `identity.contracts`; agents and tools import identity for request→principal and access questions | one direction, no cycles | §4 |
| 12 | `principal_for_request` moves into identity and stays the only request→principal point | §1.1 | §5.2 |
| 13 | **The acting rule.** A chat turn or queue job acts as the USER. An agent's tool list is intersected with the user's tool entitlements. Delegates inherit the root user. An agent is never a way around labels. The watcher and CLI ingest act as one named service principal | an agent is a tool a person wields, not a second person | §5.3, §9 |
| 14 | System admin = Django `is_superuser`, any number; the last active superuser cannot be demoted or deactivated; no two-tier admin; no break-glass "account" concept — just "a local superuser exists" | one role, one column, no invented hierarchy | §6.1, §12.3 |
| 15 | Entitlement owner = a `role` column on the grant row; an owner of E may grant/revoke E, label/unlabel documents with E, and see everything in E — nothing else | delegation without a second role table | §6.4, §7.4 |
| 16 | Django's `Permission` model is not used | it is model-level and would be a second mechanism beside entitlements | §20 |
| 17 | Groups are Django `auth.Group`, unchanged; group managers are later | §1.2 | §6, §21 |
| 18 | Tables: custom `AbstractUser` subclass in identity (created now because it cannot be changed later), `Entitlement`, `EntitlementGrant` (user XOR group, role, `source`), `AuditEvent` (append-only), `IdentitySettings` singleton; `DocumentEntitlement` in `tools/rag`; `ToolEntitlement` and `Share` in `agents` | each table lives beside the thing it protects | §6 |
| 19 | `EntitlementGrant.source` is present from day one though only `manual` is ever written | it is the column an SSO reconciliation joins on, and adding it later means backfilling rows nobody can classify | §6.4, §21 |
| 20 | Documents carry ≥1 entitlement labels; visible if the user holds ANY (OR-match). Unlabelled documents are visible to all signed-in users when `library_posture=open` (default), admins only when `locked`. Locked never hides a labelled document from a holder | AND-matching is a named non-goal: make a more specific entitlement instead | §8 |
| 21 | Retrieval keeps ONE filter point: `retrieve_nodes` gets a visibility argument. Chunk metadata carries a copy of the document's entitlement ids, stamped at ingest and re-stamped on label change by one SQL UPDATE keyed on `file_id` — not a job, not a re-encode. Tables are truth; metadata is a cache | a visibility change must not cost GPU hours | §8 |
| 22 | Conversations, agents, flows and outputs: the owner sees; `Share` rows extend; shipped defaults (resident rows) are visible to all; admins see all | as amended by the owner on 2026-08-29: an administrator sees all **content** only while `admin_sees_content` is on (§22.29); they see every operational **row** regardless | §7.5, §22.29 |
| 23 | Queue page: a member sees their own jobs, an admin all; cancel follows the same rule | §7.6 | §7.6 |
| 24 | Admin surfaces — `/inference/` mutations, model setup, queue controls, identity pages, posture — are superuser only. `/admin/` stays superuser-only break-glass. `/setup/` stays public. `DEBUG` must be `False` whenever posture ≠ open (a system check error) | the operator's console is not a user surface | §11, §14 |
| 25 | Cascades: deleting an entitlement removes its grants and labels (documents become unlabelled and follow the library posture); deleting a group removes its grants; users are deactivated, never deleted (sessions killed, grants dropped, rows survive); ownership reassignment is a CLI command | a deleted user with owned rows is an orphan nobody can reason about | §12 |
| 26 | Adoption: an open box's first admin claims the rows the open principal owns; audited; reversible by switching the posture back | §10.4 | §10.4 |
| 27 | The whole route surface gets a route × principal test matrix extending the never-500 sweep pattern, and the suite must pass in every posture | §16 | §16 |
| 28 | Backups now contain identity tables and are documented as sensitive | `docs/OPERATIONS.md` | §19 |

---

## 3. Postures, and the one truth

### 3.1 The three postures

| Posture | What exists | What the operator sees |
|---|---|---|
| `open` (default, and today's behaviour) | no login; the box acts as `OPEN_PRINCIPAL`; no permission query runs | nothing new: the app is exactly what it is today |
| `personal` | a handful of local accounts, a login page, conversations private and shareable | users, change password, and the posture page (including the administrator-content toggle) — no groups, no entitlements, no library posture, no entitlement pages |
| `enterprise` | users, groups, entitlements, entitlement owners, document labels, tool labels, library posture, audit, and (later) SSO | the full identity section |

`personal` → `enterprise` is a switch of one column value. Nothing migrates: `Entitlement`,
`EntitlementGrant`, `DocumentEntitlement`, `ToolEntitlement` and `Share` all exist in every
posture; in `personal` nothing writes to the four entitlement tables and no page renders them.

**In `personal`, every account is a superuser.** That is the owner's decision ("every account
may be admin") stated exactly: the user-creation form in `personal` posture sets
`is_superuser=True` and does not offer the choice. Flipping to `enterprise` does not demote
anybody — it starts *offering* the choice, and the first thing an administrator does is demote
the accounts that should be members. The users page says so in one sentence while the posture
is `personal`.

**ADMINISTERING is not READING** — owner decision, and the axis that makes both `personal`
lines true at once (§22.29). It is a distinction between two questions, not between two
postures:

- **Administer** is always `is_admin`, in **every** posture. Managing users, groups and
  entitlements; labelling, deleting and re-ingesting documents; cancelling or requeueing **any**
  job; changing the posture. An administrator needs to see the **row** to do these — a job's
  kind, owner, state and progress; a document's title, labels and status — and never needs its
  **contents**.
- **Read content** is gated by one new boolean, `IdentitySettings.admin_sees_content`,
  **default False**. Least privilege: an administrator who should also read other people's
  conversations, images, Ask history and documents is a deliberate choice somebody makes on the
  posture page, audited like the posture itself and effective without a restart. A household
  operator flips it once and forgets it; an organisation leaves it off.

`identity/access.py::sees_all_content` (§7.1) becomes `open → True; else is_admin(principal)
and admin_sees_content`. **There is no posture branch in any visibility function.** `personal`
and `enterprise` behave identically here; the postures differ only in *which pages exist*.

### 3.2 One truth: the database row

**`IdentitySettings.posture` is the only answer to "what posture is this box in".
`ACCOUNTS_REQUIRED` is deleted from `config/settings.py`.**

Four reasons, in order of weight:

1. **Two truths would be two answers in one box.** `compose.yaml` starts `web`, `worker` and
   `watcher` as three processes; a preview stack (`compose.preview.yaml`) supplies its own
   environment again. An environment variable that governs whether permissions are enforced
   could be set on `web` and unset on `worker` — and the worker is where turns actually run
   tools. The database is the one thing all three processes provably share.
2. **The posture must be validatable against database facts, atomically.** Switching to
   `personal` or `enterprise` is refused unless an active superuser exists. An environment
   variable cannot be refused; it is simply true on the next boot, and a box whose posture says
   "accounts required" while no account exists is a box nobody can log into.
3. **The owner decided the posture is admin-editable.** A page cannot edit an environment
   variable. A CLI break-glass (`manage.py identity_posture`) covers the case where the page is
   unreachable.
4. **Nothing is lost.** `ACCOUNTS_REQUIRED`'s `True` branch has never worked — it raises
   (`agents/chat/principal.py::principal_for_request`). Deleting it removes a documented
   non-feature, not a behaviour. ADR 0015 Decision 10 and `docs/ROADMAP.md` both describe it as
   "the branch point that phase flips"; this spec flips it by deleting it, and ADR 0016 records
   the amendment (§19).

**How the posture is read.** `identity/access.py::posture()` returns
`IdentitySettings.get_solo().posture` — the same singleton shape
`tools/rag/models.py::RagSettings.get_solo` uses (`get_or_create(pk=1)`, never raising
`DoesNotExist`). One primary-key read per request, on a connection Django already holds open
(`CONN_MAX_AGE=600`, `config/settings.py`). No cache layer: a cache would be a second truth
with a staleness window, and the staleness window of a security posture is exactly the interval
in which the box is wrong.

### 3.3 What "a switch, not a migration" means mechanically

Switching posture writes one column and one `AuditEvent`. It does **not**:

- create, delete or rewrite any user, grant, label or share;
- rewrite `owner_kind`/`owner_key` on any row (that is adoption, §10.4, and it is a separate,
  named, audited command);
- change what is stored in the chunk metadata cache (§8.4).

Switching *back* to `open` is therefore a complete reversal of enforcement: every visibility
function returns everything again, and the ownership stamps sit unread until somebody switches
forward. That is the reversibility the owner asked for in decision 26.

### 3.4 "An open box never runs a permission query"

Stated precisely, because it is a testable claim and it must not quietly become false:

> In `open` posture, no request executes a query against `EntitlementGrant`,
> `DocumentEntitlement`, `ToolEntitlement`, `Share`, or `identity_user`.

The one primary-key read of `IdentitySettings` is not a permission query — it is the query that
answers *which posture*, and the box must ask it before it can skip anything else. Every
function in §7 tests `accounts_on()` first and returns its open branch before touching another
table. Django's `AuthenticationMiddleware` runs but performs no query either, because with no
session cookie `request.user` stays a lazy `AnonymousUser`.

This is pinned by a test that counts queries (`django_assert_num_queries`) on a representative
GET of each mount in `open` posture and asserts the identity tables are untouched.

---

## 4. The `identity/` column and the import law

### 4.1 Layout

```
identity/
  contracts/                    pure, Django-free — a rule-1 leaf
    __init__.py
    principals.py               Principal, PRINCIPAL_KINDS, OPEN_PRINCIPAL,
                                SERVICE_PRINCIPAL, ANONYMOUS,
                                principal_from_payload, payload_fields
    postures.py                 POSTURE_OPEN/PERSONAL/ENTERPRISE, POSTURES,
                                LIBRARY_OPEN/LOCKED, pure predicates
    actions.py                  AUDIT_ACTIONS — the closed action vocabulary
    ownership.py                OwnedRows, register_owned_rows, all_owned_rows
  __init__.py
  apps.py                       AppConfig(name="identity", label="identity")
  models.py                     User, IdentitySettings, Entitlement,
                                EntitlementGrant, AuditEvent
  access.py                     the access questions (posture, is_admin,
                                sees_all_content, held/owned entitlement ids,
                                may_see_unlabelled, owner_fields)
  request.py                    principal_for_request  (moved here)
  audit.py                      record(...) — the one AuditEvent writer
  gate.py                       require_principal / require_admin decorators
  routes.py                     ROUTE_RULES — every URL name, classified
  middleware.py                 IdentityGateMiddleware
  services.py                   create_user, deactivate_user, set_superuser,
                                set_posture, grant, revoke  (the guarded writes)
  checks.py                     identity.E001 (DEBUG), identity.W001 (cookies)
  admin.py                      registers identity.User with Django's UserAdmin
  forms.py  views.py  urls.py
  templates/identity/
  management/commands/          identity_posture.py, adopt_open_rows.py,
                                reassign_owner.py
  migrations/
  tests/                        _helpers.py + test modules
  README.md
```

`identity/` is a Django app whose `AppConfig` sets `label = "identity"` explicitly, as all
eight existing app configs do — the property §3.5 of the 2026-08-25 spec proved the regroup
depends on. `INSTALLED_APPS` gains `"identity"`, placed **before** `django.contrib.admin` is
irrelevant (order does not matter for app loading) but **`AUTH_USER_MODEL = "identity.User"`
must be set in the same commit as the app**, or Django refuses to start.

### 4.2 The import law, amended

The three rules stated in ADR 0015 Decision 1 are amended, not replaced.

**Rule 1 — pure leaves are universally importable.** `identity/contracts/` joins
`foundation/format.py`, `foundation/files.py`, `models/contracts/` and `agents/contracts/`. No
Django models, no views, no database, no import of a non-pure module.

**Rule 2 — Django apps are column-private, with named seams.** `identity` is a Django app and
is therefore column-private *except* for a closed set of four modules every column may import:

| Module | What it answers |
|---|---|
| `identity.contracts.*` | the types (rule 1; listed here for completeness) |
| `identity.access` | posture, admin-ness, entitlement id sets, `owner_fields` |
| `identity.request` | this HTTP request's principal |
| `identity.audit` | write one audit event |

`identity.models`, `identity.views`, `identity.services`, `identity.forms` and
`identity.middleware` are **off-limits to every other column, with no exception** — exactly the
shape `models.registry.models`/`.views` already have, and enforced by the same gate
(`foundation/ops/tests/test_import_law.py::FORBIDDEN_MODULES` gains them).

**Rule 4 (new) — `identity/` imports nothing from `agents/`, `tools/`, or `models/`.** It may
import `foundation`, Django, and its own `contracts`. This is what makes the column a base
rather than a hub. Its two consequences are load-bearing and stated here so they are not
discovered later:

- **identity cannot answer "which documents"** — it answers "which entitlement ids", and
  `tools/rag` turns that into a queryset (§8.2). Same for tools (§9).
- **the document-label page lives in `tools/rag`, and the tool-label page in `agents/chat`**,
  not in identity (§15).

**Cross-column foreign keys use strings, never imports.** `Share.user` and
`EntitlementGrant.user` declare `settings.AUTH_USER_MODEL`; `DocumentEntitlement.entitlement`
declares `"identity.Entitlement"`; group FKs declare `"auth.Group"`. That is exactly what
`AUTH_USER_MODEL` exists for, and it means no column ever imports `identity.models`.

**This introduces the repository's first cross-app migration dependencies.** The 2026-08-25
spec recorded, as a fact that made the regroup DB-free, that "there are no cross-app
dependencies and no `swappable_dependency` anywhere". After IA-2 there are both (§17). The
consequence is that app *labels* are now load-bearing across columns as well as within them —
which they already were for table naming — and ADR 0016 records the retirement of that fact.

### 4.3 The guards that grow

| Guard | Today | After |
|---|---|---|
| `test_import_law.py::FORBIDDEN_MODULES` + `_SCANNED_COLUMNS` | `models.registry.models`, `.views`, `models.queue.models`, swept over `tools`/`foundation`/`agents` | **unchanged**. `models/` is excluded from this sweep because intra-column access is not a violation, and folding identity's forbidden names into the same constant would drag `models/` into the scan and start flagging `models/queue` importing its own `models.py` |
| new: `test_no_column_imports_identitys_private_modules` | — | its own sweep, over `tools`/`foundation`/`agents`/`models` (identity excluded, for the same intra-column reason), forbidding `identity.models`, `identity.views`, `identity.services`, `identity.forms`, `identity.middleware`; same AST shape, same anti-vacuous pins as the sweep above |
| `test_import_law.py::_PRINCIPAL_CONSTRUCTORS` | three files under `agents/` | exactly two: `identity/contracts/principals.py` (the type and the constants) and `identity/request.py` (who is on this request) |
| new: `test_no_identity_module_imports_another_column` | — | AST sweep over `git ls-files -- identity`, mirroring `test_no_agents_module_imports_a_tools_package`, with the same lazy-import anti-vacuous pin |
| new: `test_no_view_outside_identity_reads_request_user` | — | AST sweep over `tools`, `models`, `agents` for `request.user` and `.is_superuser`; every access question goes through `identity.access` or a visibility function |
| `test_column_boundaries.py` chat-`.objects` gate | `Conversation`/`Agent`/`Flow` | unchanged in shape; `Share` is added to `_VISIBILITY_MODELS` so no chat module queries shares directly either |
| new: `test_no_module_outside_audit_touches_auditevent_objects` | — | the same `ast.Attribute(value=ast.Name(id=...), attr="objects")` shape, over every column, forbidding **any** `AuditEvent.objects` access outside `identity/audit.py` — `.update()` and `.delete()` included, because append-only is a code-level property here and those two are the operations that destroy an audit trail (§6.5, §13.1) |
| new: `test_rag_views_reads_documents_through_the_access_module` | — | the same shape, forbidding `Document.objects` in `tools/rag/views.py`. `tools/rag/access.py::readable_documents` and `::listable_documents` are the only sanctioned readers, so the library totals, the category sidebar counts and the tabular-row count cannot drift back off the raw manager and cannot pick the wrong one of the two (§8.2) |

---

## 5. Principals, and the acting rule

### 5.1 The type, and the kinds

`Principal` moves verbatim from `agents/contracts/tools.py` to
`identity/contracts/principals.py` — same frozen two-string dataclass, same `__post_init__`
validation, same rationale (hashable so a grant cache can key on it; un-repointable so nothing
downstream can change who a half-finished turn is acting as). `agents/contracts/tools.py`
imports it and re-exports nothing: every call site is repointed.

```python
PRINCIPAL_KINDS = (
    "open",            # an unauthenticated box: one Principal("open", "box")
    "user",            # key = the user's PRIMARY KEY as a string
    "service",         # key = a named machine caller. ONE constant today.
    # HISTORICAL. Nothing constructs these after IA-1; they remain in the
    # vocabulary because ToolInvocation rows written before IA-1 carry them
    # and a closed vocabulary that cannot describe its own stored data is a
    # vocabulary that lies.
    "resident_agent",
    "user_agent",
)

OPEN_PRINCIPAL    = Principal("open", "box")
# ONE service principal, not one per caller (round-1 ruling m11, and the
# dossier's own wording: "one named service principal constant"). The
# watcher, `manage.py ingest`, `manage.py ask` and `manage.py agent_turn`
# all act as this. WHICH of them acted is recorded where a distinction is
# actually useful -- `AuditEvent.detail["source_command"]` and the audit
# row's `source="cli"` -- rather than in the principal's key, where it would
# become a grants-table join value and quietly turn "the shell" into four
# subjects a grant could be written against.
SERVICE_PRINCIPAL = Principal("service", "local")
```

**`api_client` is folded into `service`.** The dossier left this to the author. One kind for
machine callers, because: nothing has ever constructed an `api_client` (the AST guard's own
history shows the three constructor files, none of which makes one), so there is no stored data
to preserve; and two names meaning "a machine holding a credential" would make a future grants
join test two values where one fact exists. The MCP edge's caller is a service account.

**`owner_key` for a user is the primary key as a string, never the username.** A username is
renameable; a rename must not orphan every row a person owns. Display resolves pk → username at
render time.

### 5.2 Request → principal

`identity/request.py::principal_for_request(request)` — moved from
`agents/chat/principal.py`, which is deleted. It remains the only request→principal point, and
the AST guard still says so.

```python
def principal_for_request(request) -> Principal:
    """The principal acting on `request`.

    OPEN posture: one principal for the whole machine, and no query.
    PERSONAL/ENTERPRISE: the signed-in user, or ANONYMOUS_SENTINEL for a
    request with no session -- never the open principal, which would be a
    posture leak wearing an unauthenticated request's clothes.
    """
    if not accounts_on():
        return OPEN_PRINCIPAL
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ANONYMOUS
    return Principal("user", str(user.pk))
```

`ANONYMOUS` is **not** a `Principal`. It is one instance of a distinct frozen type in
`identity/contracts/principals.py`:

```python
@dataclass(frozen=True)
class _Anonymous:
    """Nobody. A SEPARATE TYPE from `Principal`, not a fifth kind.

    It carries `.kind`/`.key` so the access functions can read it with the
    same two attribute lookups they use for a real principal and need no
    isinstance branch -- but `"anonymous"` is deliberately NOT in
    PRINCIPAL_KINDS, so `Principal("anonymous", ...)` raises, and the type is
    structurally incapable of reaching a grants join or an owner column: it
    is never passed to `owner_fields` (the gate refuses the request first),
    and `Share`/`EntitlementGrant` join on a user id, never on a kind string.
    Making it a kind would mean the first person to write
    `Q(owner_kind="anonymous")` gave "nobody" a set of rows to own.
    """
    kind: str = "anonymous"
    key: str = ""

ANONYMOUS = _Anonymous()
```

Every access function in §7.1 recognises it and answers "nothing". The gate (§11) refuses an anonymous request before a view
ever sees it, so `ANONYMOUS` reaches an access function only through a direct call in a test.

Anonymous handling is the gate's job, not the view's: HTML requests get `302` to the login page
with `?next=`; requests carrying `X-Requested-With: XMLHttpRequest` get `401` with a JSON body
`{"error": "sign-in required", "login_url": ...}`. The existing polling clients
(`tools/rag/templates/rag/ask.html`, the chat poller) send that header already.

### 5.3 The acting rule, traced through the real call sites

> A chat turn or queue job acts as the **user**. An agent's tool list is intersected with the
> user's tool entitlements. Delegates inherit the root user. An agent is never a way around
> labels. The watcher and CLI ingest act as one named service principal.

Today the runtime acts as the *agent*: `agents/runtime/bindings.py::principal_for(agent)`
returns `Principal("resident_agent"|"user_agent", agent.slug)` and four call sites use it —
`agents/runtime/loop.py::_run_turn`, `agents/runtime/jobs.py::_tool_roles`,
`agents/runtime/delegate.py::run_agent_tool`, and `agents/runtime/preflight.py`. That is
exactly the design this rule reverses.

**The changes, in full:**

1. **`agents/runtime/bindings.py::principal_for` is deleted.** It has no callers left. The
   `_PRINCIPAL_CONSTRUCTORS` set in `test_import_law.py` shrinks to the two files in §4.3.

2. **`ToolContext` gains `agent_slug: str = ""`** (`agents/contracts/tools.py::ToolContext`).
   `principal` now always means *who this is being done for*; `agent_slug` means *whose tool
   declaration is in force*. Both facts were previously crammed into one field, which is why
   the acting rule could not be expressed before.

3. **The actor travels in the job payload.** `agent.turn`'s payload (built in
   `agents/chat/views/turns.py::turn_create` and in
   `agents/management/commands/agent_turn.py`) gains `"actor_kind"` and `"actor_key"`, written
   from `principal_for_request(request)` and from `SERVICE_PRINCIPAL` respectively.
   `identity/contracts/principals.py::payload_fields(principal) -> dict` mints them and
   `::principal_from_payload(payload) -> Principal` reads them back — one function each, in the
   one file allowed to construct a `Principal`, so the AST guard stays at two files.
   The same two keys are added to the `rag.ask`, `rag.ingest`, `vision.generate` and
   `rag.reencode` payloads (§7.6 explains why). `rag.reencode` is the re-encode kind's real
   key, registered by `models/registry/apps.py` — it lives in the `models/` column but is named
   for the role it re-encodes, and there is no `inference.reencode`.

4. **`agents/runtime/jobs.py::plan_turn` and `agents/runtime/loop.py::_run_turn`** both build
   `actor = principal_from_payload(payload)` and pass it where they passed
   `principal_for(agent)`. `_tool_roles(agent)` becomes `_tool_roles(agent, actor, access)`.
   The run-time call re-derives the access set from the actor, so a person whose entitlements
   were revoked between enqueue and run gets the narrower set — free, because that read has to
   happen at run time anyway.

5. **`agents/runtime/delegate.py::run_agent_tool` inherits.** It stops minting a principal for
   the sub-agent and builds the child `ToolContext` with `principal=ctx.principal` (unchanged)
   and `agent_slug=agent.slug` (the delegate's). Its `available_tools(...)` call passes the
   *root actor's* tool access. This is the whole of "an agent is never a way around labels":
   there is no hop at which the acting principal widens.

6. **`agents/visibility.py::visible_flows`** is asked with the acting principal, which is now
   the user. Its docstring's recorded 2026-08-28 ruling — "a delegate sees the flows IT may
   run, not the flows its caller may" — is reversed by owner decision 13, and the reversal is
   recorded in ADR 0016 and in an amendment to ADR 0015 §10. See §23, concern 2.

7. **`agents/models.py::ToolInvocation` gains `agent_slug = CharField(max_length=64, blank=True,
   default="")`**, written by `agents/runtime/invoke.py::invoke_tool` from
   `tool_ctx.agent_slug`. Without it the acting rule would *lose* the fact that an agent made
   the call, which is the fact an operator most wants when reading the audit trail.

8. **Every shell path acts as `SERVICE_PRINCIPAL`.** Four commands:
   `tools/rag/management/commands/ingest_watch.py` and `::ingest.py` stamp it into the
   `rag.ingest` payload; `agents/management/commands/agent_turn.py` into the `agent.turn`
   payload; and `tools/rag/management/commands/ask.py` — which calls
   `tools/rag/retrieval.py::answer_question` **directly**, not through the queue — builds its
   `DocumentVisibility` from it (§8.3). Documents the watcher creates arrive **unlabelled**:
   it has no way to know who a dropped file is for, and inventing a default would be inventing
   a policy. In `library_posture=open` an unlabelled document is visible to every signed-in
   user, which is the honest default for a file somebody dropped in a shared inbox; in `locked`
   readable only by `sees_all_content` until somebody labels it — though an administrator
   still sees its row and can label it (§8.2). Per-inbox default labels are deferred (§21).

   **A shell path that CREATES a row stamps that row too, not just the payload.** A
   conversation made by `manage.py agent_turn` is written with
   `owner_kind="service"`, `owner_key="local"` — `identity.access.owner_fields
   (SERVICE_PRINCIPAL)`, the same one function every other create uses (§10.2). The payload
   carries the actor so the worker knows who the turn acts as; the row carries the owner so a
   filter can reason about it afterwards, and those are two different needs.

   **A service-owned row is visible to `is_admin` and to nobody else.** Not to
   `sees_all_content`, and this is the one place where the administer/read split does not
   apply — because there is no human whose content is being protected. A shell-made
   conversation has no person behind it; if `is_admin` did not reach it, no page and no
   principal ever would, and the row would be orphaned by construction the moment it was
   written. Each of the four bodies in §7.2 therefore ORs in `_service_rows(principal)`, and
   §8.2's `listable_documents` already reaches every document row for the same reason.

   **`adopt_open_rows` claims `("open", "box")` rows and never service-owned ones** (§10.4).
   Adoption is about a box that used to have no accounts; a service principal is not an absence
   of an owner, it is an owner, and the command silently rewriting it would make "who made
   this" unanswerable. Handing a shell-made row to a person is `manage.py reassign_owner
   --from service` (§10.5), which names what it is doing.

   **The consequence for `manage.py ask` is stated rather than discovered:** in `enterprise`
   posture a service principal holds no entitlements (§9.4), so the command answers from
   unlabelled documents only, and it says so in one line of its own output rather than
   silently returning a thinner answer than the same question gets on the Ask page.

---

## 6. Data model

Every table below names the app that owns its migration. Field types match the house
conventions already in the tree: CI-unique names via `UniqueConstraint(Lower(...))`
(`agents/models.py`'s `uniq_agent_slug_ci`, `models/registry/models.py`'s
`uniq_modelconnection_name_ci`), owner columns as
`CharField(max_length=32|200, blank=True, default="")` exactly as `agents/models.py` declares
them, and named indexes as string literals.

### 6.1 `identity.User` — a custom `AbstractUser`, created now

```python
class User(AbstractUser):
    """The platform's user model.

    NO EXTRA FIELDS, deliberately. It exists now because AUTH_USER_MODEL
    cannot be changed once rows reference it, and the cost of adding a
    field later to a model we own is one migration -- while the cost of
    swapping the model later is a database surgery nobody wants to
    perform on a box holding a document library.
    """
```

`AUTH_USER_MODEL = "identity.User"`. Table `identity_user`, plus Django's own
`identity_user_groups` and `identity_user_user_permissions` join tables (created by
`AbstractUser`'s inherited M2M fields; the permissions one is never written to — see §20).

**The swap on an existing box.** Every deployed box already applied `auth` and `admin`
migrations against `auth.User`, so `django_admin_log.user_id` carries a foreign key to
`auth_user`. Django cannot fix that itself: the autodetector may not write migrations into a
third-party app. IA-1 therefore ships `identity/migrations/0002_repoint_admin_log_fk.py`:

- a `RunPython` precondition that raises with an operator-readable instruction if `auth_user`
  or `django_admin_log` holds any row (neither can, because no login has ever existed — but a
  migration that assumes it is safe without checking is a migration that eventually is not);
- a `RunSQL` that is a no-op when `to_regclass('auth_user') IS NULL` (a fresh install, where
  `admin.0001_initial`'s `swappable_dependency` already built the FK against `identity_user`),
  and otherwise drops the existing FK constraint found by catalogue lookup — the constraint
  name is hash-suffixed and is not a literal anybody may hard-code — and adds one pointing at
  `identity_user`;
- `reverse_sql = migrations.RunSQL.noop`, with the docstring saying so plainly: the reversal is
  restoring the backup, which is the same position `rag/0013_retire_chat_tables` took for a
  drop.

`auth_user`, `auth_user_groups` and `auth_user_user_permissions` are **left in place** as empty
orphan tables. Dropping them would put `django.contrib.auth`'s migration state and the database
out of agreement over a table Django still believes it manages. `docs/OPERATIONS.md` records
them as expected orphans so a future operator reading a dump does not think something is broken.

**`/admin/` after the swap.** Django's `AdminSite.register` silently ignores a swapped-out
model, so `django.contrib.auth.admin`'s `User` registration becomes inert and `/admin/` would
show Groups only. `identity/admin.py` re-registers the user model so the break-glass surface
keeps working — but **not with the stock `UserAdmin`** (round-1 ruling M3). Stock `UserAdmin`
would open two holes at once: its `save_model` calls `obj.save()` directly, bypassing
`identity/services.py` and therefore bypassing the last-admin guard (§12.3) entirely — an
administrator could demote the last superuser from `/admin/` and lock the box — and its
`fieldsets`/`filter_horizontal` expose `user_permissions` and the `Permission` catalogue, which
non-goal 2 rules out as a second grant mechanism.

```python
# identity/admin.py -- the ONLY admin.py in the repository.

@admin.register(User)
class IdentityUserAdmin(DjangoUserAdmin):
    """Break-glass, routed through the same guards the pages use.

    `save_model` DELEGATES: a change to `is_superuser` goes through
    `identity.services.set_superuser` and a change to `is_active` through
    `identity.services.deactivate_user` / `reactivate_user`, so the
    last-admin guard, its `select_for_update`, and the audit write all
    apply here exactly as they do on the users page. Everything else falls
    through to `super().save_model`. An admin form that could reach a
    protected column without its guard is a guard with a second door.

    `user_permissions` is dropped from `fieldsets` and from
    `filter_horizontal`, and `readonly_fields` pins it, so Django's
    per-user permission catalogue is not reachable from this surface --
    non-goal 2. `auth.Group`'s own admin is left exactly as Django ships
    it: this platform uses groups for membership only and never reads
    `Group.permissions`, so there is nothing there to hide and nothing
    gained by subclassing it.
    """
```

Pinned by three tests: demoting the last superuser through the admin form is refused with the
same message the page gives; deactivating through it drops grants and writes an audit row; and
`"user_permissions"` appears in neither the rendered form nor `get_fieldsets`.

### 6.2 `identity.IdentitySettings` — the posture singleton

```python
class IdentitySettings(models.Model):
    posture = models.CharField(max_length=16, choices=POSTURE_CHOICES, default=POSTURE_OPEN)
    library_posture = models.CharField(max_length=16, choices=LIBRARY_CHOICES,
                                       default=LIBRARY_OPEN)
    # Whether an ADMINISTRATOR may read other people's CONTENT -- their
    # conversations, Ask history, generated images, agent and flow bodies,
    # share lists, document bytes, and the payload/answer text of jobs they
    # did not start.
    #
    # DEFAULT FALSE, and that default is the decision (owner, 2026-08-29).
    # Administering is not reading: an admin already sees every ROW they
    # need to do their job -- a job's kind/owner/state/progress, a
    # document's title/labels/status -- without this. Turning it on is a
    # deliberate act on the posture page, audited exactly like the posture,
    # and read per request so it takes effect without a restart.
    admin_sees_content = models.BooleanField(default=False)
    # A ROLLING idle timeout, applied per request by IdentityGateMiddleware
    # via `request.session.set_expiry(...)` -- see section 14. Zero means
    # "expire when the browser closes".
    session_idle_minutes = models.PositiveIntegerField(default=SESSION_IDLE_MINUTES_DEFAULT)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "IdentitySettings": ...   # get_or_create(pk=1), as RagSettings does
```

`SESSION_IDLE_MINUTES_DEFAULT = 720` (twelve hours) — long enough that a person working through
a document library is not logged out mid-task, short enough that a browser left open on a shared
desk is not a standing session. No seed migration: `get_solo()` creates the row on first read,
which is the pattern `tools/rag/models.py::RagSettings.get_solo` established and which means a
restored backup taken before this phase behaves identically to a fresh install.

### 6.3 `identity.Entitlement`

```python
class Entitlement(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="entitlements_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_entitlement_name_ci")]
```

`SET_NULL` on `created_by`, not `CASCADE`: deleting a user must never delete an entitlement, and
users are deactivated rather than deleted anyway (§12.2) — the null branch exists for the
hypothetical shell deletion, not as a normal path.

### 6.4 `identity.EntitlementGrant`

```python
class EntitlementGrant(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        OWNER = "owner", "Owner"

    class Source(models.TextChoices):
        MANUAL = "manual", "Granted here"
        # WRITTEN BY NOTHING TODAY. Present from day one because it is the
        # column an SSO reconciliation joins on: "revoke every grant this
        # provider used to assert and no longer does" is answerable only if
        # provider-asserted grants are distinguishable from hand-made ones,
        # and backfilling that distinction later is impossible.
        SSO = "sso", "From the identity provider"

    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE,
                                    related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="entitlement_grants")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="entitlement_grants")
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.MEMBER)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="grants_made")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entitlement__name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="grant_user_xor_group",
            ),
            # PARTIAL uniques, not one plain unique across three columns:
            # NULLs do not collide in Postgres, so a plain
            # UniqueConstraint(entitlement, user, group) would happily store
            # the same group grant a thousand times.
            models.UniqueConstraint(fields=["entitlement", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_grant_entitlement_user"),
            models.UniqueConstraint(fields=["entitlement", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_grant_entitlement_group"),
        ]
        indexes = [
            models.Index(fields=["user"], name="identity_grant_user"),
            models.Index(fields=["group"], name="identity_grant_group"),
        ]
```

**`role` is a column on the grant, not a second row.** Promoting a member to owner is an
`UPDATE`, which is why the unique constraints do not include `role`: two rows for one
(entitlement, user) pair would make "does this person hold E" ambiguous.

**`CASCADE` on `entitlement`, `user` and `group`** is owner decision 25 made mechanical:
deleting an entitlement takes its grants; deleting a group takes its grants. Deleting a user
does not happen (§12.2), but if a shell forces it the grants go with them rather than pointing
at nothing.

### 6.5 `identity.AuditEvent` — append-only

```python
class AuditEvent(models.Model):
    actor_kind = models.CharField(max_length=32)
    actor_key = models.CharField(max_length=255)
    # The actor's DISPLAY NAME at the time of the event. Denormalised on
    # purpose: an audit line must still read correctly after the account is
    # renamed or deactivated, and a join that resolves a deleted pk to
    # "unknown" is an audit trail that forgets.
    actor_label = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_key = models.CharField(max_length=255, blank=True, default="")
    target_label = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="web")
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["actor_kind", "actor_key"], name="identity_audit_actor"),
            models.Index(fields=["target_type", "target_key"], name="identity_audit_target"),
        ]

    def save(self, *args, **kwargs):
        """Append-only, enforced rather than described: a row that already
        has a primary key may not be saved again. `action` must be a member
        of AUDIT_ACTIONS -- a typo'd action name is a construction error
        here, not a category that silently splits an audit report in two."""
```

No `delete()` path exists in any code, no page offers one, and the model is deliberately **not**
registered in `/admin/` (§15). Retention and CSV export are deferred (§21).

**`save()` is not the whole guard, and the spec says so rather than implying it.** A queryset
`.update()` or `.delete()` never calls `save()`, so append-only is enforced at the *code* level
by the AST guard in §13.1 — no module outside `identity/audit.py` may touch `AuditEvent.objects`
at all — and not at the database level. Making it append-only in Postgres would take a rule or a
trigger on the table, which is a second enforcement mechanism outside the application (the same
objection non-goal 3 raises against row-level security) and is out of scope. Recorded here so
nobody reads `save()` and believes the table is immutable.

**`AuditEvent` carries no foreign key to `User`.** Two strings and a label, exactly like
`ToolInvocation`'s principal columns. An audit row that a cascade could delete is not an audit
row, and this also means `identity/0001_initial.py` needs no `swappable_dependency` and can
create the audit table alongside the user model in one migration.

### 6.6 `tools.rag.DocumentEntitlement` — the label, beside the thing protected

```python
class DocumentEntitlement(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="document_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "entitlement"],
                                               name="uniq_document_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="rag_doclabel_entitlement")]
```

The `document` side needs no explicit index — its foreign key already has one. Migration owned
by `rag`.

### 6.7 `agents.ToolEntitlement`

```python
class ToolEntitlement(models.Model):
    # A STRING, not a foreign key: tools are code-registered
    # (`agents/contracts/tools.py::register_tool`) and there is no tool
    # table to point at -- the same reason `Turn.queue_job_id` is a plain
    # integer. A row naming a key that is not registered on this install
    # (a feature-gated tool with its flag off) is tolerated exactly as
    # `Agent.tool_keys` tolerates one.
    tool_key = models.CharField(max_length=255)
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="tool_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tool_key", "entitlement"],
                                               name="uniq_tool_entitlement")]
        indexes = [models.Index(fields=["tool_key"], name="agents_toollabel_key")]
```

### 6.8 `agents.Share` — generic target, one UI

```python
class Share(models.Model):
    class Target(models.TextChoices):
        CONVERSATION = "conversation", "Conversation"
        AGENT = "agent", "Agent"
        FLOW = "flow", "Flow"
        VISION_OUTPUT = "vision_output", "Generated image"

    class Level(models.TextChoices):
        VIEW = "view", "Can view"
        USE = "use", "Can use"

    target_type = models.CharField(max_length=16, choices=Target.choices)
    # The target's primary key AS TEXT. The four targets live in three apps
    # and have three different key types (UUID, int, int); a real FK would
    # be a cross-column import (rule 2) and a GenericForeignKey would make
    # `contenttypes` a second identity for a row this platform already
    # identifies by pk.
    target_key = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="shares_received")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="shares_received")
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.VIEW)
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="shares_made")
    shared_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="share_user_xor_group"),
            models.UniqueConstraint(fields=["target_type", "target_key", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_share_target_user"),
            models.UniqueConstraint(fields=["target_type", "target_key", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_share_target_group"),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_key"], name="agents_share_target"),
            models.Index(fields=["user"], name="agents_share_user"),
            models.Index(fields=["group"], name="agents_share_group"),
        ]
```

**Level semantics.** `view` reads the thread; `use` also posts turns into it (the `chat-turn`
route). For an agent or a flow, `use` means "may run it" and `view` means "may see it in a
list" — which is why the level exists at all rather than being implied.

**Orphans.** The one shipped delete surface, `agents/chat/views/conversations.py::
conversation_delete`, deletes the conversation's shares in the same transaction, through a new
`agents/visibility.py::delete_conversation(principal, conversation)` (the create already lives
there for the same reason). Agents and flows have no delete surface. No `post_delete` receiver
is added: this repository uses no Django signals anywhere today, and a stale `Share` row is
inert — every reader resolves the target first and a target that does not resolve contributes
nothing. `vision_output` has no writer in IA-2 (§10.3), so it cannot orphan.

### 6.9 Owner columns added elsewhere

| Model | App | Columns | Index |
|---|---|---|---|
| `tools.vision.GenerationJob` | vision | `owner_kind`, `owner_key` | `vision_job_owner` |
| `tools.rag.AskRecord` | rag | `owner_kind`, `owner_key` | `rag_askrecord_owner` |

Same declarations as `agents/models.py`'s: `CharField(max_length=32/200, blank=True,
default="")`. Existing rows get `""`, which means "written before adoption" and is what the
adoption command claims (§10.4).

**`GenerationJob`, not `GeneratedOutput`.** One owner per generation; `output_file` and
`input_file` resolve their owner through `output.job` / `input.job`. A per-output owner would
be a second answer to one question.

**`Document` gets no owner column.** A document's access is decided by its labels and the
library posture, and by nothing else. Giving it an owner would create a second, invisible rule
("the uploader can always see it") that no page displays and no admin can revoke.

**`InferenceJob` gets no owner column either** — see §7.6 for the mechanism that replaces it.

### 6.10 Model **sets** — `models.registry.ModelSet` and its two edges

Added by the 2026-08-30 owner directive (§22.31) and **reworked the same day** by its successor
(§22.33). The owner's reason is the whole of the design:

> "it may be a pain to reassign every entitlement for a given model… group models into sets that
> can be assigned to multiple entitlements, rather than models being assigned by entitlement. So
> one change can impact many different entitlements/users."

The first draft labelled a **connection** directly, `(connection, entitlement)`. That makes every
new model an N-row edit against every entitlement that should reach it, and every re-organisation
a migration of rows nobody can review. The label unit moves **up one level**: a connection joins a
**set**, and an **entitlement attaches to a set**. Adding a model to a set is one row and reaches
every entitlement already attached; attaching a set to a second entitlement is one row and reaches
every model already in it.

```python
class ModelSet(models.Model):
    """A named group of model connections -- the unit an entitlement
    attaches to.

    CI-UNIQUE BY NAME, like `Entitlement`, `Category` and
    `ModelConnection` before it: two sets that read identically on an
    attach form are two rows somebody will attach the wrong one of.

    A set is NOT an entitlement and NOT a capability. It answers "which
    models is this", once, so that "who may use them" can be answered
    somewhere else and changed without touching it.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="model_sets_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"),
                                               name="uniq_modelset_name_ci")]


class ModelSetMember(models.Model):
    """One connection's membership of one set. The edge that maps models
    into sets, and the one an operator edits when a new model arrives."""

    model_set = models.ForeignKey(ModelSet, on_delete=models.CASCADE,
                                  related_name="members")
    connection = models.ForeignKey(ModelConnection, on_delete=models.CASCADE,
                                   related_name="set_memberships")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["model_set", "connection"],
                                               name="uniq_modelset_member")]
        indexes = [models.Index(fields=["connection"], name="inference_setmember_conn")]


class ModelSetEntitlement(models.Model):
    """One entitlement's attachment to one set -- the LABEL row, moved up
    a level from the connection to the set.

    The foreign key to the entitlement is a STRING, exactly as
    `DocumentEntitlement`'s and `ToolEntitlement`'s are, which is what
    keeps `identity/` importable-from rather than importing (rule 4).
    """

    model_set = models.ForeignKey(ModelSet, on_delete=models.CASCADE,
                                  related_name="entitlement_attachments")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="model_set_attachments")
    attached_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["model_set", "entitlement"],
                                               name="uniq_modelset_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="inference_setent_ent")]
```

**Three tables, not one, and the third is the only one that mentions an entitlement.** That
separation is what makes the owner's "one change impacts many" true: the two edges are edited
independently and neither knows about the other's other end.

**A connection in NO set stays open to every holder of the capability.** Opt-in, zero-cost, and
unchanged from the first draft: a box that never creates a set behaves exactly as it does today,
and `model_access_for` short-circuits before it queries anything.

**A connection in ≥1 set is usable by a principal holding an entitlement attached to ANY of those
sets** — OR across sets, the same OR-match spirit document labels use, and for the same reason:
"must hold both" is a named non-goal, and the answer to it is a more specific set.

**Editing either edge is administrator-only**, consistent with tool labels and unlike document
labels: an entitlement owner's three capabilities (§7.4) are grants and documents, and a model
connection is box inventory rather than somebody's library shelf.

### 6.11 `agents.AgentEntitlement` and `agents.FlowEntitlement`

Added by the 2026-08-30 second directive: an agent or a flow can be restricted to the people who
need it. **Direct labels, not sets** — an agent is not a fungible resource an operator swaps
weekly the way a model under test is, and a set layer here would be machinery with no reason
(§22.33 records why the two answers differ).

```python
class AgentEntitlement(models.Model):
    """An agent's label -- beside the thing it protects.

    UNLABELLED = every signed-in principal, which is today's behaviour and
    which is what keeps a box that never labels an agent unchanged.
    LABELLED = holders of any of its entitlements, OR-matched.
    """

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE,
                              related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="agent_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["agent", "entitlement"],
                                               name="uniq_agent_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="agents_agentlabel_ent")]


class FlowEntitlement(models.Model):
    """The same, for a flow.

    A SEPARATE TABLE rather than one polymorphic label row: `Agent` and
    `Flow` are two models with two primary keys, and a `target_type`/
    `target_key` pair here would be `Share`'s shape solving a problem
    `Share` has and this does not -- these two labels are read by two
    functions that each already know which model they are filtering.
    """

    flow = models.ForeignKey(Flow, on_delete=models.CASCADE,
                             related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="flow_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["flow", "entitlement"],
                                               name="uniq_flow_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="agents_flowlabel_ent")]
```

**A `resident=True` row is not exempt.** The shipped defaults are visible to everybody *because
nobody owns them*, not because they are unrestrictable; an operator who labels the shipped
research agent means it. §9.6 states the composition, with the resident carve-out written out
precisely.

---

## 7. Access rules, and the visibility function bodies

### 7.1 `identity/access.py` — the questions identity can answer alone

```python
def posture() -> str:
    return IdentitySettings.get_solo().posture

def accounts_on() -> bool:
    return posture() != POSTURE_OPEN

def is_admin(principal) -> bool:
    """May this principal reach an ADMIN SURFACE -- the model console, the
    queue settings, the users page, the posture page, /admin/.

    True for OPEN_PRINCIPAL and for an active superuser. False for ANONYMOUS
    and for every service principal.

    OPEN_PRINCIPAL is an admin because on a box with no accounts there is
    nobody for anything to be hidden from, and answering `False` would make
    every admin surface unreachable in the posture that is the default.

    A SERVICE principal is never an admin, whatever it was issued: a machine
    caller that could reach the model console or the posture page would make
    the shell a privilege-escalation path with no login behind it.

    THIS IS NOT `sees_all_content`. The two were one function in the first
    draft, which is exactly how "conversations are private" quietly became
    "private from the people who are not administrators". Administering is
    what this answers; reading is what that one answers.
    """

def sees_all_content(principal) -> bool:
    """Whether this principal may read OTHER PEOPLE'S CONTENT -- their
    conversations, Ask history, generated images, agent and flow bodies,
    share lists, document bytes, and the payload/answer text of jobs they
    did not start.

    OPEN -> True. There is nobody for anything to be hidden from.
    OTHERWISE -> `is_admin(principal) and admin_sees_content`.

    NO POSTURE BRANCH, deliberately (owner, 2026-08-29). `personal` and
    `enterprise` answer this question identically; they differ only in which
    pages exist. A predicate that read the posture would mean the same
    administrator saw different content on two boxes that had made the same
    choice, and the choice is the setting, not the posture.

    THIS IS NOT `is_admin`, and the distinction is the whole point:
    administering needs ROWS, reading needs CONTENTS. An admin cancels any
    job, labels any document and deletes any document with `is_admin` alone
    (sections 7.6, 8.2); this predicate is only consulted where the answer
    would be somebody else's words, pixels or bytes.
    """
    if not accounts_on():
        return True
    return is_admin(principal) and IdentitySettings.get_solo().admin_sees_content

def held_entitlement_ids(principal) -> frozenset[int]:
    """Every entitlement id this principal holds, directly or through one of
    their groups. ONE query. Empty for open, service and anonymous
    principals -- the open branch never reaches here because every caller
    tests `accounts_on()` first."""
    if principal.kind != "user":
        return frozenset()
    return frozenset(EntitlementGrant.objects.filter(
        Q(user_id=principal.key) | Q(group__user__id=principal.key)
    ).values_list("entitlement_id", flat=True))

def owned_entitlement_ids(principal) -> frozenset[int]:
    """The subset of the above whose grant carries role=owner. An owner of E
    may grant it, label with it, and see everything in it -- the third of
    those needs no branch anywhere, because an owner grant is also a grant
    and therefore already appears in `held_entitlement_ids`."""

def may_see_unlabelled(principal) -> bool:
    """Whether this principal may see documents carrying no label.

    OPEN -> True.
    library_posture=open (the default) -> True for anyone signed in.
    library_posture=locked -> `sees_all_content` only. An administrator with
    the content setting OFF therefore does NOT read an unlabelled document's
    bytes on a locked library -- they still see its ROW, and can label or
    delete it (section 8.2), which is what administering it requires.

    NO POSTURE BRANCH here either. `library_posture` is only EDITABLE on the
    enterprise page, so `set_posture` resets it to `open` when a box moves
    to `personal` (a posture with no page to change it back must not be
    left holding a lock nobody can lift). The branch lives in that one write
    path, never in this read path.
    """

def owner_fields(principal) -> dict:
    """The two columns to stamp on a row this principal is creating. MOVED
    here from `agents/visibility.py` so all five owned tables in three
    columns share one definition rather than three that agree by
    convention."""
    return {"owner_kind": principal.kind, "owner_key": principal.key}
```

`agents/visibility.py::owner_fields` becomes a one-line re-export so its existing callers and
the `.objects` guard are untouched.

### 7.2 `agents/visibility.py` — the four bodies, filled in

Two private helpers first, in the same module:

```python
def _owned(principal) -> Q:
    return Q(owner_kind=principal.kind, owner_key=principal.key)

def _service_rows(principal) -> Q:
    """Rows a SHELL PATH created, for an administrator only.

    `manage.py agent_turn` and friends stamp `("service", "local")` on the
    rows they create (section 5.3 item 8). Nobody owns those rows in the
    human sense, so `_owned` matches them for no user and `_shared_keys`
    finds no share -- without this clause they would be invisible to every
    principal on the box the moment they were written.

    `is_admin`, NOT `sees_all_content`, and it is the one place the
    administer/read split does not apply: the split exists to keep an
    administrator out of somebody's private content, and a service-owned row
    has no somebody. An empty Q for everyone else, so a member's filter is
    unchanged.
    """
    if not is_admin(principal):
        return Q(pk__in=[])
    return Q(owner_kind="service")

def _shared_keys(target_type: str, principal) -> list[str]:
    """The `target_key` strings this principal reaches through a Share row,
    directly or through one of their groups.

    MATERIALISED into a list rather than left as a Subquery, deliberately:
    `Share.target_key` is text and the three target tables have three
    different primary-key types, so a subquery would need a per-type cast
    and would be a silent type mismatch waiting to happen. Two small queries
    on a single-box install beat one clever one.

    FILTERED THROUGH THE TARGET'S OWN KEY PARSER on the way out. A
    conversation's primary key is a UUID, so a `target_key` that is not a
    parseable one would make `Q(pk__in=[...])` raise inside the queryset and
    turn a listing page into a 500 -- a never-500 violation reachable by one
    bad row. `_TARGET_KEY_PARSERS` maps each target type to a parser that
    RETURNS None rather than raising (`uuid.UUID` for a conversation, `int`
    for the other three), and an unparseable row is dropped and logged.
    `Share.save()` validates the same way on the way IN; this is the second
    half of that rule, because a row can also arrive from a shell or from an
    older schema."""
    if principal.kind != "user":
        return []
    raw = Share.objects.filter(target_type=target_type).filter(
        Q(user_id=principal.key) | Q(group__user__id=principal.key)
    ).values_list("target_key", flat=True)
    parse = _TARGET_KEY_PARSERS[target_type]
    return [key for key in raw if parse(key) is not None]
```

```python
def visible_conversations(principal):
    qs = Conversation.objects.select_related("agent")
    if sees_all_content(principal):
        return qs.all()
    shared = _shared_keys(Share.Target.CONVERSATION, principal)
    return qs.filter(
        _owned(principal) | Q(pk__in=shared) | _service_rows(principal)
    ).distinct()


def visible_agents(principal):
    """Every ENABLED agent this principal may run. `resident=True` rows are
    the shipped defaults and are visible to everybody -- they are the
    platform's own offer, not somebody's private work."""
    qs = Agent.objects.filter(enabled=True)
    if sees_all_content(principal):
        return qs
    shared = _shared_keys(Share.Target.AGENT, principal)
    return qs.filter(
        _owned(principal) | Q(resident=True) | Q(pk__in=shared) | _service_rows(principal)
    ).distinct()


def installed_agent_slugs(principal):
    """Every slug this principal has installed, ENABLED or not -- the set the
    "Add the default X" offers are computed against. Same visibility rule as
    above, minus the `enabled` filter, for the reason this function's
    original docstring gives."""
    qs = Agent.objects.all()
    if not sees_all_content(principal):
        shared = _shared_keys(Share.Target.AGENT, principal)
        qs = qs.filter(
            _owned(principal) | Q(resident=True) | Q(pk__in=shared) | _service_rows(principal)
        )
    return qs.values_list("slug", flat=True).distinct()


def visible_flows(principal):
    """Every ENABLED flow this principal may run, ASKED WITH THE ACTING
    PRINCIPAL -- which after the acting rule (section 5.3) is the USER, not
    the agent. Its three runtime callers (`narrowed_flow_spec`,
    `flow_row_roles`, `run_flow`) therefore narrow a delegate to the flows
    the ROOT user may run, which is the whole point: flipping the posture
    changes what an agent can run, not merely what a page lists."""
    qs = Flow.objects.filter(enabled=True)
    if sees_all_content(principal):
        return qs
    shared = _shared_keys(Share.Target.FLOW, principal)
    return qs.filter(
        _owned(principal) | Q(resident=True) | Q(pk__in=shared) | _service_rows(principal)
    ).distinct()
```

Four new functions join them, so the `.objects` guard still needs exactly one exclusion:

```python
def visible_turn(principal, turn_id):
    """The Turn `turn_id`, or None. Resolved THROUGH the conversation:
    `chat-turn-status` takes a sequential integer, which is the enumeration
    exposure the agents spec's gap 4 recorded, and this is where it closes."""

def delete_conversation(principal, conversation): ...
def share_conversation(principal, conversation, *, user=None, group=None, level): ...
def revoke_share(principal, conversation, share_id):
    """Remove one Share row from THIS conversation, or answer None.

    TWO REFUSALS, both 404, and neither is optional:

    - `share_id` is parsed as an int FIRST. It arrives from a POST body, so
      a non-numeric value would otherwise reach `Share.objects.get(pk=...)`
      and raise `ValueError` -- a 500 on a never-500 surface, reachable by
      anybody who can post a form.
    - the row is then checked against THIS conversation:
      `share.target_type == Share.Target.CONVERSATION` and
      `share.target_key == str(conversation.pk)`. Without it, an owner of
      conversation A could revoke a share on conversation B by guessing a
      sequential `share_id` -- an IDOR, and one that reads as a legitimate
      action in the audit log. The conversation is already authorised by the
      route's O class; the share must be proven to belong to it.

    404 for both, never 403, for section 11.1's reason: a 403 would confirm
    that some other conversation's share carries that id.
    """
```

### 7.3 Why the open branch is first in every body

`sees_all_content(principal)` tests `accounts_on()` first and returns before it reads a second
table, so an open box executes zero share queries and zero grant queries — §3.4's claim, made
structural rather than promised.

### 7.4 Entitlement owners

An owner of E is a member of E plus three capabilities, and each is a separate check at the
place it applies:

| Capability | Where enforced |
|---|---|
| grant/revoke E to a user or group | `identity/services.py::grant`/`::revoke` — `entitlement_id in owned_entitlement_ids(principal) or is_admin(principal)` |
| label/unlabel a document with E | `tools/rag/views.py::document_labels_update` — same predicate |
| see everything in E | no code at all: an owner grant is a grant, so §7.1's `held_entitlement_ids` already includes it |

An owner may not create or delete an entitlement, rename it, change the library posture, or
touch anything else. That is the whole of owner decision 15.

### 7.5 Conversations, agents, flows and generated images

| Kind | Owner sees | Extended by | Always visible | `sees_all_content` |
|---|---|---|---|---|
| Conversation | yes | `Share(conversation)` | — | all |
| Agent | yes | `Share(agent)` | `resident=True` | all |
| Flow | yes | `Share(flow)` | `resident=True` | all |
| Generated image (`GenerationJob`) | yes | `Share(vision_output)` — no writer in IA-2 | — | all |

The last column is `sees_all_content`, not `is_admin` — these four kinds are CONTENT, so an
administrator reaches them only while `admin_sees_content` is on (§3.1, §7.1). Nothing here is
operational: an administrator has no job that requires reading somebody's conversation. The
kinds that ARE operational — queue rows and library rows — are `is_admin` and are covered in
§7.6 and §8.2.

A **new module**, `tools/vision/visibility.py`, carries `visible_jobs(principal)` and
`may_read_job(principal, job)` with the same body shape. `/vision/gallery/` lists `visible_jobs`; `job_status`, `job_delete`,
`output_file` and `input_file` resolve through it and answer `404` when it comes back empty.

### 7.6 The queue page, without an owner column

Owner decision 23 needs "own jobs" for a table (`models/queue/models.py::InferenceJob`) that has
no owner. **The actor travels in the payload** (§5.3, item 3): every enqueuing surface writes
`actor_kind`/`actor_key` into the JSON payload it already builds, and a **new module**,
`models/queue/visibility.py`, filters on them:

```python
def visible_jobs(principal):
    """The queue ROWS this principal may see.

    `is_admin`, NOT `sees_all_content`: a queue row is OPERATIONAL (owner,
    2026-08-29). Its kind, owner, state, progress, priority and timings are
    what an administrator needs to run the box -- to see that something is
    stuck, and to cancel it -- and none of them is somebody's words. An
    administrator sees every row in every posture, with the content setting
    on or off.
    """
    qs = InferenceJob.objects.all()
    if is_admin(principal):          # includes OPEN_PRINCIPAL
        return qs
    return qs.filter(payload__actor_kind=principal.kind, payload__actor_key=principal.key)


def may_read_job_content(principal, row) -> bool:
    """Whether the PAYLOAD and the ANSWER/RESULT text of `row` may be
    rendered to this principal: they own it, or `sees_all_content`.

    This is the second half of the rows-vs-content split. The queue page's
    `_present_row` calls it once per row and, when it is False, renders the
    row with its operational fields and an explicit "content hidden" marker
    in place of the payload -- not a blank, which would read as a job that
    carried nothing.
    """
```

Five consequences, all stated rather than discovered:

- **No migration and no change to the queue's frozen seam.** `models/contracts/queue.py::
  enqueue` keeps its exact signature; ADR 0013's one door is untouched. The payload is already
  the record of what was asked for, which is precisely where "who asked" belongs.
- **Fail closed for members, open for administrators.** A job whose payload carries neither
  key — which after IA-1 means only rows enqueued *before* this phase, since §5.3 stamps the
  actor into all five job kinds' payloads including `rag.reencode` — is visible to `is_admin`
  and to nobody else. A member sees nothing they did not cause.
- **Cancel is administer, so cancel is always available to an administrator.** An admin cancels
  or requeues **any** job in any posture, with the content setting off, because a stuck job is
  an operational fact and unsticking the box is the job. There is no posture, and no setting, in
  which the operator cannot clear their own queue.
- **The payload is content and is hidden by default.** For a job the administrator did not
  start, `may_read_job_content` is False while `admin_sees_content` is off, so the page shows
  the row and withholds the payload and the answer text — a prompt is somebody's words, and a
  RAG answer is somebody's answer.
- **A JSON filter, not an index.** `InferenceJob.payload` is a Django `JSONField` (Postgres
  `jsonb`), and the queue table is retention-pruned and page-limited already, so the scan is
  bounded. If a future box makes it hot, the fix is an expression index on
  `(payload->>'actor_key')`, named here so it is a follow-up rather than a redesign.

`queue_job_cancel` applies `visible_jobs`: a member may cancel a job it returns and gets `404`
otherwise, and an administrator may cancel anything, per the bullet above.
`queue_settings_update` is admin-only (§11).

---

## 8. Documents: labels, retrieval, and the chunk-metadata cache

### 8.1 The rule

A document carries zero or more `DocumentEntitlement` labels.

- **Labelled**: visible to a principal holding **any** of its entitlements. OR-match only.
- **Unlabelled**: readable by every signed-in principal when `library_posture=open` (the
  default); by `sees_all_content` only when `locked`.
- **Locked never hides a labelled document from a holder.** The library posture governs
  unlabelled documents and nothing else.
- **An administrator always sees every document ROW — title, category, labels, status, size,
  ingest state — and never reads a document's CONTENT without `admin_sees_content`.** That is
  the owner's administer-versus-read split (§3.1) applied to the library, and it is the split
  that makes labelling work at all: somebody has to be able to put a label on a document they
  are not cleared to read, or the first label can only ever be applied by a person who does not
  need it. §8.2 names the two functions.

AND-matching ("must hold both E1 and E2") is a named non-goal (§20): the answer is a more
specific entitlement, which is one row instead of a filter algebra nobody can read off a page.

### 8.2 `tools/rag/access.py`

```python
@dataclass(frozen=True)
class DocumentVisibility:
    """What one principal may see of the library, as plain data.

    Built ONCE per request or per turn and threaded down; never re-derived
    inside a runner, which is the drift the single filter point exists to
    prevent."""
    unrestricted: bool                 # `sees_all_content` (section 7.1)
    entitlement_ids: frozenset[int]
    unlabelled_allowed: bool

    @property
    def sees_nothing(self) -> bool:
        return not self.unrestricted and not self.entitlement_ids \
               and not self.unlabelled_allowed


def document_visibility(principal) -> DocumentVisibility: ...

def readable_documents(principal):
    """Documents whose CONTENT this principal may reach -- the bytes, the
    transcript, and the chunks retrieval may return.

    `unrestricted` here is `sees_all_content`, so an administrator with the
    content setting OFF is filtered exactly like a member: they read what
    their entitlements and the library posture allow, and nothing else.
    """
    v = document_visibility(principal)
    if v.unrestricted:
        return Document.objects.all()
    labelled = Q(entitlement_labels__entitlement_id__in=v.entitlement_ids)
    if v.unlabelled_allowed:
        return Document.objects.filter(labelled | Q(entitlement_labels__isnull=True)).distinct()
    return Document.objects.filter(labelled).distinct()

def listable_documents(principal):
    """Document ROWS this principal may see -- title, category, labels,
    status, size, ingest state.

    `is_admin`, NOT `sees_all_content` (owner, 2026-08-29). Labelling,
    deleting and re-ingesting a document are administration, and an
    administrator MUST be able to label a document they are not cleared to
    read -- otherwise the first label on a sensitive document can only be
    applied by somebody who does not need it, which is the wrong way round.
    A member's listing is `readable_documents`, so nothing widens for them.

    The row is deliberately the whole of what this widens: `readable_
    documents` still gates the bytes, the transcript and every chunk
    retrieval can return, so an administrator with the content setting off
    can put a document in an entitlement without ever seeing a word of it.
    """
    if is_admin(principal):
        return Document.objects.all()
    return readable_documents(principal)

def visible_ask_records(principal):
    """`/rag/history/`: own records, or all for a principal that
    `sees_all_content` -- an Ask record holds a question and an answer, so
    it is content, not an operational row."""
```

Two functions, and every surface picks the one that matches what it serves. **Content** —
`rag-document-file`, `rag-document-transcript`, and every retrieval path (§8.3) — goes through
`readable_documents`. **Rows** — the library listing and the label, delete and re-ingest actions
— go through `listable_documents`. §11.3 marks each route with the class that says which.

**Counts are a reading surface too.** `tools/rag/views.py` today computes its library totals and
its category sidebar straight off the manager — a `Document.objects.count()`, an
uncategorised-document count, a per-category `Count("documents")` annotation, and a tabular-row
count. Every one of them is recomputed off `listable_documents(principal)` — the listing's own
function, so the counts and the rows beneath them can never disagree: a sidebar that says
"Finance (14)" to a member who may open none of them is a leak of exactly the fact the labels
exist to hide, and the count is the easiest one to forget because it renders no document. An
administrator does see "Finance (14)" with the content setting off, and that is correct: a count
of rows is a row fact, and it is the fact they need in order to label and prune the library. A
category whose visible count is zero is omitted from the sidebar entirely rather than shown as
`(0)`, for the same reason. The AST guard that forbids `request.user` outside identity (§4.3) is
joined by a narrower one over `tools/rag/views.py`: no `Document.objects` access in that module,
mirroring the `agents/chat` `.objects` rule that already works.

### 8.3 The one filter point

`tools/rag/retrieval.py::retrieve_nodes` gains one **required** keyword-only argument:

```python
def retrieve_nodes(question, category, settings_row, *, embed_resolved, visibility): ...
```

Required, not defaulted. A default of "see everything" is a fail-open default, and a caller that
forgets it should fail at import-time signature checking rather than at a security review two
years later. The chain of signatures that changes with it is short and fully enumerated:

| Function | Change |
|---|---|
| `tools/rag/retrieval.py::retrieve_nodes` | new required `visibility` |
| `tools/rag/retrieval.py::answer_question` | new required `visibility`, passed straight through |
| `tools/rag/jobs.py` (the `rag.ask` handler) | builds it from the payload actor |
| `tools/rag/tools.py` (`rag.search`, `rag.ask` runners) | build it from `ctx.principal` |
| `tools/rag/views.py::SearchView`, `::AskView` | build it from `principal_for_request` |
| `tools/rag/management/commands/ask.py` | **calls `answer_question` directly, not through the queue** — it builds a `DocumentVisibility` from `SERVICE_PRINCIPAL`, which in `enterprise` posture holds no entitlements (§9.4) and therefore sees unlabelled documents only. The command prints one line saying so, rather than silently answering from a thinner library than the Ask page would. Without this row the required keyword-only argument would break `manage.py ask` outright |

**The body.** Today `retrieve_nodes` builds a `MetadataFilters` carrying at most the category
filter. It gains a visibility group, AND-combined with the category one (the installed
Postgres vector store recurses through nested `MetadataFilters`, so the nesting is supported,
not assumed):

```python
if visibility.sees_nothing:
    # A signed-in principal with no entitlements on a LOCKED library. The
    # honest answer is nothing, and it must be an EARLY RETURN rather than
    # an empty filter list -- `MetadataFilters(filters=[])` means "no
    # filter", which means EVERYTHING. This is the single most dangerous
    # line in the phase and it has its own test.
    # The index is still built and returned: `answer_question` synthesizes
    # from it even with zero nodes. The query EMBEDDING is skipped, because
    # it happens inside `retriever.retrieve`, which is what we skip.
    return [], hybrid, index

clauses = []
if category:
    clauses.append(MetadataFilter(key="category", value=..., operator=FilterOperator.EQ))
if not visibility.unrestricted:
    allowed = []
    if visibility.entitlement_ids:
        # ANY renders as `metadata_::jsonb->'entitlements' ?| array['3','7']`
        # in the installed store -- a JSON *string* array test, which is why
        # the stamped ids are strings (section 8.4).
        allowed.append(MetadataFilter(
            key="entitlements",
            value=sorted(str(i) for i in visibility.entitlement_ids),
            operator=FilterOperator.ANY))
    if visibility.unlabelled_allowed:
        # IS_EMPTY renders as `metadata_->>'entitlements' IS NULL`, which is
        # true exactly when the key is ABSENT -- which is what an unlabelled
        # chunk looks like, and what EVERY chunk in every existing store
        # already looks like. No back-fill is needed for the unlabelled case.
        allowed.append(MetadataFilter(key="entitlements", value="",
                                      operator=FilterOperator.IS_EMPTY))
    clauses.append(MetadataFilters(filters=allowed, condition=FilterCondition.OR))
filters = MetadataFilters(filters=clauses, condition=FilterCondition.AND) if clauses else None
```

Three facts about the installed store that this depends on, verified rather than assumed:

- both the dense and the sparse halves of a hybrid query apply the same filters (each calls the
  store's `_apply_filters_and_limit`), so a hybrid search cannot leak past the visibility
  clause while the dense search obeys it;
- the `ANY` clause builder interpolates its values into SQL as quoted strings rather than
  binding them. Our values are integers rendered as strings, straight off a database column,
  so the interpolation is safe **by construction**. A test pins that every value passed is
  `str.isdigit()`, so a future refactor that passes an entitlement *name* fails loudly;
- there is no index on `metadata_` keys on this platform (the store is created without
  `indexed_metadata_keys`), so the visibility clause is a sequential scan — the same cost
  profile the existing category filter already has. Named, not fixed.

### 8.4 The chunk-metadata cache: one writer, one SQL

Tables are truth. The chunk metadata carries a **copy** of a document's entitlement ids so the
retriever can filter without a join it has no way to express.

**One writer.** `tools/rag/labels.py::restamp_document_chunks(doc_id)` is the only function
that writes the `entitlements` key, and it is called from exactly two places:

1. `tools/rag/ingest.py::_ingest_prose`, immediately after `index.insert_nodes(nodes)` — so a
   re-ingest of an already-labelled document restores its labels rather than silently dropping
   them (the indexing library rewrites the row; the labels are not its business);
2. every label add/remove, inside the same transaction as the `DocumentEntitlement` write.

The alternative — stamping node metadata at ingest and SQL-updating on change — would produce
two shapes of the same fact: the ingest-time one lands inside the store's own serialised node
blob as well as the filterable column, the SQL one only in the column. Nothing filters on the
blob, so the difference is invisible until somebody debugs it. One writer, one shape.

**The key is never node metadata.** The indexing library prepends metadata keys onto a node's text before
embedding and before handing it to a model unless they are excluded — the pollution
`tools/rag/media.py::apply_chunk_metadata_exclusions` exists to fix. `entitlements` sidesteps
the question entirely by never being node metadata: it is written into the `metadata_` column
after insert. A test asserts no node this platform builds carries the key.

**The SQL**, against `tools/rag/index.py::LIVE_TABLE_NAME` (`data_rag_chunks`), a module
constant and never caller input:

```sql
-- document has labels
UPDATE data_rag_chunks
   SET metadata_ = jsonb_set(metadata_::jsonb, '{entitlements}', %s::jsonb, true){cast}
 WHERE metadata_->>'file_id' = %s;

-- document has no labels left: REMOVE the key, so IS_EMPTY still matches it
UPDATE data_rag_chunks
   SET metadata_ = (metadata_::jsonb - 'entitlements'){cast}
 WHERE metadata_->>'file_id' = %s;
```

`%s` binds `json.dumps([str(i) for i in ids])` and `str(doc_id)` — the same `file_id`-as-text
keying `tools/rag/index.py::delete_chunks_for_document` already relies on. `{cast}` is `""` or
`"::json"`, chosen from the existing fact `tools/rag/index.py::live_store_shape()["jsonb"]`:
**the live `metadata_` column is `json`, not `jsonb`, on any box that has never enabled hybrid
search**, because `desired_store_shape()` binds `use_jsonb` to the hybrid toggle. Both filter
operators used in §8.3 cast explicitly and work on either column type; only the UPDATE's
assignment needs the branch.

**Degradation, stated per caller.** When the chunk table does not exist (nothing prose has been
ingested), `restamp_document_chunks` is a no-op — the same defensive posture
`delete_chunks_for_document` documents. When it exists and the UPDATE fails, the two callers
differ deliberately: the **ingest-time** call logs and does not raise (a failed re-stamp must
not fail an ingest, and the label page can repair it), while the **label-change** call raises
and rolls the `DocumentEntitlement` write back with it. A label that appears to be saved and is
not enforced is worse than a label that refuses to save.

**Repair.** `manage.py relabel_chunks [--document <id>]` re-runs the stamp for one document or
all of them. It is a loop over the same function, exists for the ingest-time failure path, and
is cheap — no embedding, no engine, one UPDATE per document.

### 8.5 What a shared conversation exposes, and what it does not

A conversation's `Turn` rows record citation text, snippets and titles
(`agents/models.py::Turn.data`). Sharing a conversation shares **its recorded content**,
including quoted document text; it does **not** share the documents. A citation in a shared
thread renders its link, and the link `404`s for a recipient who may not read that document
(`rag-document-file` is in the LIBRARY class, §11).

This is a deliberate boundary and it is stated on the share form in one sentence, because it is
the one place a person can leak library content without meaning to. The alternative — refusing
to render citation text a recipient cannot independently read — would mean a shared thread that
silently disagrees with what the participants saw, which is worse.

---

## 9. Tools: entitlements replace `tool_keys` as grant truth

### 9.1 The change of meaning

`Agent.tool_keys` stops being a grant and becomes a **declaration**: what this agent wants to
be able to do. The grant is `ToolEntitlement` plus the acting user's entitlements. An
unlabelled tool is callable by every signed-in principal; a labelled tool is callable by a
principal holding any of its entitlements.

Availability is therefore the **intersection** of three things, and
`agents/contracts/tools.py::granted_tools` stays the one function that computes it:

1. the agent's declaration (`Agent.tool_keys`),
2. the registry (a key not registered on this install is dropped and logged — unchanged),
3. the acting principal's tool entitlements (new).

`mutates=True` keys are still dropped outright. ADR 0010's rule that a settings-mutating tool is
registered but not grantable **stays in force through IA-2** and is lifted in a later phase, not
here: `rag.ingest` remains ungrantable. Lifting it means designing which entitlement a mutating
tool requires by default, which is a policy question this phase does not need to answer.

### 9.2 How a pure leaf asks a database question

`granted_tools` lives in `agents/contracts/tools.py`, a Django-free rule-1 leaf. It cannot query
`ToolEntitlement`. The facts arrive as data:

```python
# agents/contracts/tools.py

@dataclass(frozen=True)
class ToolAccess:
    """What the ACTING principal may call, as plain data.

    `required` maps a tool key -> the entitlement ids that label it. A key
    ABSENT from this mapping is unlabelled, and therefore callable.
    `held` is the entitlement ids the acting principal holds.
    `unrestricted` short-circuits the whole check: open posture, or an
    admin. It defaults True so that UNRESTRICTED_TOOL_ACCESS is the
    zero-argument answer, and so every existing call site and every test
    that does not care keeps working unchanged.
    """
    required: Mapping[str, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, key: str) -> bool:
        if self.unrestricted:
            return True
        needed = self.required.get(key)
        return not needed or bool(needed & self.held)


UNRESTRICTED_TOOL_ACCESS = ToolAccess()


def granted_tools(principal, tool_keys, access=UNRESTRICTED_TOOL_ACCESS) -> list[str]:
    """... three drops now: unregistered (logged), mutates=True (silent),
    and not permitted by `access` (logged at debug, because on a labelled
    install it is the NORMAL case and an info line per turn per tool would
    drown the log)."""
```

The Django-side builder is `agents/entitlements.py::tool_access_for(principal) -> ToolAccess` —
an `agents` module, so it may import `identity.access` (rule 2's named seam) and its own
`ToolEntitlement`. It runs **once per turn**, not once per tool: one query over
`ToolEntitlement` (a small table) and one over the principal's grants.

### 9.3 The call sites, in full

| Call site | Change |
|---|---|
| `agents/runtime/jobs.py::_tool_roles` | takes the actor and one `ToolAccess`; passes it to `granted_tools` so the enqueue-time plan and the run-time prompt are the same filtered set — the property ADR 0015 §9 already relies on |
| `agents/runtime/loop.py::available_tools` | takes `access`; passes it through |
| `agents/runtime/loop.py::_run_turn` | builds `actor = principal_from_payload(payload)` and `access = tool_access_for(actor)` once |
| `agents/runtime/delegate.py::run_agent_tool` | reuses the **root** access from `ctx`, never rebuilds one for the sub-agent |
| `agents/runtime/preflight.py::preflight_turn` | builds it from the request principal, so the page refuses before it writes |
| `agents/runtime/invoke.py::invoke_tool` | unchanged — it never decided availability, and must not start |

**Direct UI surfaces obey the same answer** (2026-08-30 owner directive, §22.31). The rows above
are all agent-runtime seams; a tool that also has a **page of its own** would otherwise be
labelled and still reachable, which is the gap the directive names. There is exactly one such
tool today:

| Direct surface | Change |
|---|---|
| `tools/vision/views.py::generate` (`vision-generate` POST) | consults `tool_access_for(principal).allows("vision.generate")` before it validates, stages or queues anything, and refuses with honest copy — **403**, the same in-view gate `rag-document-delete`/`-reingest` already use, never a 500 and never a silent 503 |
| `tools/vision/views.py::CreatePageView` (the create page) | render-gates the generation form on the **same predicate**, so the page never offers a control whose POST answers 403 (the render-vs-gate rule) |
| `tools/rag/views.py::document_upload` (`rag-document-upload` POST) — *added by the 2026-08-30 second directive* | consults `tool_access_for(principal).allows("rag.ingest")` **before a byte is staged**, and refuses **403** with honest copy. It is the library's own door and the exact mirror of the vision one: the same predicate, the same status, the same first-thing-in-the-view position |
| `tools/rag/templates/rag/documents.html` (the upload form) | render-gates on the same predicate. The library listing, the search box and the label controls are untouched — the route is class **A** and somebody who may not add documents may still read the ones they may read |

The route's middleware class does not change: `vision-generate` and `rag-document-upload` both
stay **A**, and the entitlement rule lives in the view, exactly as §11.3's library mutations do. A
future tool that grows a page of its own inherits the same obligation, and §16.4 pins it.

**`rag.ingest` is `mutates=True`, and a label on it is still legitimate** — this needs saying,
because the two rules look like they collide and do not.
`agents/contracts/tools.py::grantable_tools` excludes every mutating spec, and `granted_tools`
drops one unconditionally: that is ADR 0010's rule, and it is about what an **agent** may be
handed. A label is the opposite direction — it can only ever **narrow** which *people* reach a
tool's own page, and it hands nothing to anybody. So the tool-label page (§15) lists mutating
tools too, marked as page-only, and §16.4 pins that labelling `rag.ingest` still leaves it
uncallable by every agent. Recorded as an author decision at §22.35.

**ADR 0015 §9 needs an amendment**, not a citation: it states that when grants move to their own
table the `tool_keys` argument "disappears and the principal alone decides". Under owner
decision 13 the agent's declaration survives as a declaration, so the argument survives too and
its *meaning* changes. §19 lists the amendment.

### 9.4 The service-principal gap

`SERVICE_PRINCIPAL` holds no entitlements — grants
attach to a user or a group, by the owner's own XOR constraint, and `ServiceAccount` is deferred
(§21). It therefore gets **unlabelled tools only**, permanently, with no way to grant it a
labelled one until service-account tokens land.

**The same rule reaches SET-RESTRICTED MODELS** (§9.5) **and LABELLED AGENTS AND FLOWS** (§9.6).
A service principal holds no entitlement, so: a connection in a set is unreachable from the shell
path when `manage.py agent_turn` names it as a picked connection; a labelled agent — **including a
shipped `resident=True` one** — is not in `visible_agents(SERVICE_PRINCIPAL)`, so
`manage.py agent_turn` refuses it outright; and a labelled flow is not offered to it. All three
refuse with the same honest copy a member gets, and all three are permanent until service-account
tokens land in IA-3.

The **role path is unaffected**, which is what keeps the watcher and `manage.py ingest` working:
role bindings are box-level service bindings and are exempt (§9.5).

Implemented as written. The tool-label page carries one line naming the consequence; the model
console and the sets page carry the same line; the agent/flow access page carries it beside any
`resident=True` row, which is the one an operator is most likely to label without realising the
shell reaches it; and the tool-label form warns when the tool being labelled is one the shell path
uses. See §23, concern 3.

### 9.5 Model **sets**, and the AND-composition

Added by the 2026-08-30 owner directive (§22.31), reworked the same day to sets (§22.33):
"everyone shouldn't have access to a model that is being tested and who only a limited number
need access to."

**The rule, in one line.** A use of model **M** through capability **T** requires

> `allows(T)` **AND** ( M is in **no** set **OR** the principal holds an entitlement attached to
> **any** set M belongs to )

— an **AND across the two label kinds**, and an **OR within each**. Holding the image-generation
tool entitlement does not, by itself, give you the model under test; holding an entitlement that
reaches its set does not give you image generation. The two questions are different questions and
both are asked. **Only the label unit changed** in the rework: everything below it — the AND, the
one choke point, the exemption, the 403 — is what it was.

**A connection in no set is open to every holder of the capability.** That is the "use all image
generation or all chat models" half of the directive, and it is also what makes this change inert
on a box with no sets: no membership, no narrowing, no new query on a page that has none.

#### The predicate, and where it lives

`models/registry/access.py::model_access_for(principal) -> ModelAccess` — a `models/` module, so
it may import `identity.access` (rule 2's named seam) and its own set tables. It returns the same
shape `ToolAccess` (§9.2) has and for the same reason: the value is plain data, built **once per
request or per turn**, and threaded down rather than re-derived.

```python
@dataclass(frozen=True)
class ModelAccess:
    """Which registered connections this principal may USE, as plain data.

    `required` maps a connection pk -> the entitlement ids that reach it
    THROUGH ANY SET IT BELONGS TO. A pk ABSENT from this mapping is in no
    set, and therefore usable. Flattening the two edges into one mapping
    here is deliberate: the sets exist so an ADMINISTRATOR can change many
    grants at once, and no read path benefits from re-walking them.

    `held` is the entitlement ids the principal holds. `unrestricted`
    short-circuits: open posture, or `sees_all_content`.

    Defaults to unrestricted, exactly as `ToolAccess` does, so
    `UNRESTRICTED_MODEL_ACCESS` is the zero-argument answer and every
    caller that has no principal in hand behaves as it does today.
    """
    required: Mapping[int, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, connection_pk: int) -> bool: ...
```

**One join builds `required`**, over `ModelSetMember` joined to `ModelSetEntitlement` on the set:
a connection in two sets whose entitlements differ collects both, which is the OR the rule asks
for. A set with **no** entitlement attached restricts nothing — a connection whose every set is
unattached is reachable by everyone, because "in a set nobody can reach" would be a model an
operator has hidden from themselves with no page saying so.

`sees_all_content`, not `is_admin`: using somebody's model is using, not administering, so an
administrator with the content setting off is filtered like a member. Editing a set is
administration and answers to `is_admin` (§6.10).

#### The three enforcement seams, and the two functions they share

Every user-selectable model choice on this box is built by **one** function and resolved by
**one** function, which is what makes this three edits rather than nine:

| Seam | Function | Change |
|---|---|---|
| every picker's option list | `models/registry/bindings.py::picker_options(capability, role_key, selected)` | gains a required keyword-only `access: ModelAccess`, and drops a connection `access` does not allow. Its three wrappers — `agents/chat/pickers.py::chat_picker_options` (the chat per-turn picker), `tools/vision/views.py::_connection_picker_options` (the vision page picker), `tools/rag/views.py::_connection_picker_options` (the Ask page picker) — build one from the request principal. **This is the render half**, and it is free: a connection a principal may not use is never in the `<select>` |
| every pk-addressed resolution | `models/registry/bindings.py::resolve_connection_named(pk, capability)` and its thin wrapper `::resolve_connection` | gain the same required keyword-only `access`, and raise the same `ValueError` they already raise for a pk that names nothing usable. **This is the gate half**, and it covers every submission path at once: `agents/runtime/bindings.py::resolve_chat`, `tools/vision/views.py::picked_connection`, `tools/vision/jobs.py`, `tools/rag/views.py::AskView`, `tools/rag/jobs.py` |
| the agent turn, at **all four** of `resolve_chat`'s callers | `agents/runtime/bindings.py::resolve_chat(agent, connection, *, access)` | `preflight_turn` (`preflight.py:135`) refuses **before the turn is written**, with a new closed reason `MODEL_NOT_PERMITTED` and a **403** from `agents/chat/service.py::start_turn` rather than the 503 an unavailable service gets. `plan_turn` (`jobs.py:78`) and `_run_turn` (`loop.py:177`) each build one from the payload actor they already derive — the first is the **enqueue** half the directive names, the second is the one place the model is actually built. `delegate.py:122` passes `resolve_chat(agent, None, access=UNRESTRICTED_MODEL_ACCESS)`: `None` takes the **role** path, which is exempt below, and a delegate never carries a picked connection |

`resolve_chat` consults `access` **only when `connection` is not `None`**; the role fallback is
untouched, which is what makes the `delegate.py` line above correct rather than a hole.

**`ValueError`, not a new exception type**, at the resolution seam: every caller already handles
it as "that model is not usable", and a second exception class would mean editing six `except`
blocks to say the same thing. The **message** differs — the caller renders "you may not use that
model" rather than "that model is no longer registered" — because the two causes are different
and honest copy is Global-constraint-level in this platform.

#### The exemption — box-level service bindings

**A model resolved through a ROLE is never gated.** `models/registry/bindings.py::db_provider`
and `::role_primary` — the role path — answer for `rag.embed`, `rag.transcribe`, `rag.extract`,
`vision.generate`'s and `chat.converse`'s **default** bindings, and every future role. None of
those is a user's choice; they are the box's own wiring, and gating them would mean a set
containing the embedding model silently breaking ingestion, retrieval and re-encode for
**everybody**, including the watcher and every management command.

So the rule is: **a set gates a user-selectable choice and an agent path's picked connection; it
never gates the role path.** An operator who wants a model unreachable by everybody unbinds it or
removes it; that is what the console is for. This is recorded as an author decision at §22.32 and
is **flagged for the owner's review**, because it is the one place where "limit access to only
what a user needs" is deliberately not applied to a resolution path.

#### Cascades, audit, and the shell

Deleting an **entitlement** detaches its set attachments exactly as it detaches tool labels
(§12.1): one more registered cascade, one more line in the delete confirmation's counts.
Deleting a **set** takes its memberships and its attachments with it by `CASCADE`, and is audited
as one event naming both counts. The audit catalogue gains the set vocabulary (§13.2).
`SERVICE_PRINCIPAL` reaches connections in no set only, per §9.4.

### 9.6 Agents and flows, through the seams that already exist

Added by the 2026-08-30 second directive. This one needs no new mechanism at all: the agents
column has had **one** function per question since IA-1, and both of them already take a
principal.

| Question | Function | Change |
|---|---|---|
| which agents may this principal run | `agents/visibility.py::visible_agents(principal)` | one more clause: exclude an agent carrying labels none of whose entitlements the principal holds |
| which agents has this principal installed | `::installed_agent_slugs(principal)` | the same clause, for the same reason its `enabled` filter differs |
| which flows may this principal run | `::visible_flows(principal)` | the same clause |

Written as an **exclusion**, not an inclusion, so that the unlabelled case costs nothing and
reads as what it is:

```python
def _label_permitted(model, principal) -> Q:
    """Rows this principal may reach past their entitlement labels.

    UNLABELLED ROWS PASS. `~Q(entitlement_labels__isnull=False)` on its
    own would exclude a labelled row from everybody, so the two halves are
    spelled out: no labels at all, OR a label the principal holds.

    An OR, never an AND: holding any one of a row's entitlements is
    enough, the same match documents and tools use.
    """
    return Q(entitlement_labels__isnull=True) | Q(
        entitlement_labels__entitlement_id__in=held_entitlement_ids(principal))
```

**The clause composes with `resident=True`, it does not bypass it.** Today a resident row is
returned to everybody by an unconditional `| Q(resident=True)`. After this, the resident carve-out
is **and-ed** with the label clause — `(Q(resident=True) | _owned | _shared) & _label_permitted` —
so an operator who labels the shipped research agent has actually restricted it. That is the
point: a carve-out that survived a label would make the shipped agents unrestrictable, which is
exactly the set of agents an operator most wants to restrict.

**OWNERSHIP DOES NOT EXEMPT A ROW FROM ITS OWN LABEL EITHER, and that is deliberate rather than
incidental.** The same AND means an account that **owns** an agent or a flow stops seeing it the
moment an administrator labels it with an entitlement that account does not hold: they cannot run
their own row. It is the only reading that makes labelling a *personal* agent useful — a label an
administrator applies must not be bypassable by the person it is aimed at, which is the whole of
"limit access to only what a user needs" — but it is a real change to a row somebody created, so
it is stated here rather than left to be discovered. Existing conversations stay **readable**
(knock-on 3 below); new turns are refused with `AGENT_NOT_PERMITTED`. The `/chat/access/` page
says so in one line beside the label control, because it is the only surface from which this can
be done and there is no agents admin page on which to notice it afterwards.

**Five knock-ons, each verified against the tree and each covered:**

1. **The `/chat/` agent picker and the "Add the default X" offers** (`agents/chat/views/
   conversations.py:120,129,171`) all read `visible_agents`/`installed_agent_slugs` already, so
   they narrow with no edit. `_startable_agent` returning `None` is already a 400.
2. **`flow.run`'s per-turn choices** (`agents/runtime/flowtool.py:92,127` and
   `agents/runtime/flow.py:212`) all read `visible_flows`, so a restricted flow is not offered to
   the model and is refused if named anyway — enforcement by omission plus a belt, which is the
   shape §9.3's tool drop already has.
3. **An existing conversation whose agent the person may no longer see stays READABLE.**
   `agents/chat/views/thread.py` reads `conversation.agent` directly and never calls
   `visible_agents` — its own docstring records that as deliberate. Access to one's own rows is
   ownership, not a label question, and retroactively hiding somebody's own history would be a
   worse answer than the one the label was asked for. **A NEW TURN is refused**: `preflight_turn`
   gains a second closed reason, `AGENT_NOT_PERMITTED`, and `start_turn` answers **403** with
   honest copy, the same shape `MODEL_NOT_PERMITTED` uses.
4. **Delegation cannot reach a restricted agent.** `agents/runtime/delegate.py:72` resolves
   `Agent.objects.filter(slug__iexact=slug, enabled=True)` **directly**; it resolves through
   `visible_agents(ctx.principal)` instead, so the acting rule's "an agent is never a way around
   labels" covers agents themselves and not only their tools. The `agent.<slug>` key is also
   dropped from the offered tool list (`agents/runtime/loop.py::available_tools`) and from the
   planner's role walk (`agents/runtime/jobs.py::_tool_roles`), so the model is not offered a
   tool that would always refuse.
5. **`manage.py install_defaults` is unaffected** — it installs rows, it does not read
   visibility. (There is no `sync_agents` command in this tree.) A labelled **resident** agent
   does become unreachable from `manage.py agent_turn`, per §9.4's rule, and §9.4 says so.

---

---

## 10. Ownership, sharing, and adoption

### 10.1 The owned-rows registry

Five tables in three columns carry owner columns, and two operations (adoption, reassignment)
must walk all of them. `identity/` may not import `agents` or `tools`, so the command cannot
name the models. They are registered, exactly as job kinds, roles and tools already are:

```python
# identity/contracts/ownership.py -- pure, Django-free

@dataclass(frozen=True)
class OwnedRows:
    key: str      # "agents.conversation"
    label: str    # "Conversations"
    model: str    # "agents.Conversation" -- app_label.ModelName, resolved
                  # at command time by django.apps.apps.get_model, never
                  # imported, exactly as a JobKind's handler is a string.

def register_owned_rows(spec: OwnedRows) -> None: ...
def all_owned_rows() -> list[OwnedRows]: ...
```

Registered from each column's `AppConfig.ready()`, which still touches no database and imports
no implementation module: `agents/apps.py` registers three (`Agent`, `Flow`, `Conversation`),
`tools/rag/apps.py` one (`AskRecord`), `tools/vision/apps.py` one (`GenerationJob`, inside its
existing feature-flag early exit). A sixth owned table later is a registration, not an edit to
a command that would otherwise silently skip it.

### 10.2 Ownership is stamped, never inferred

Every create stamps `identity.access.owner_fields(principal)`. The five surfaces:
`agents/visibility.py::create_conversation` (already), `agents/defaults.py::install_default`
(already), `tools/vision/services.py::submit_job`, `tools/rag/services.py`'s `AskRecord` write,
and any future agent/flow create. A row written with blank owner columns is a row no filter can
reason about, and backfilling one is a migration nobody has the information to write.

### 10.3 Sharing

IA-2 ships **one** share UI: conversations. `POST /chat/c/<uuid>/share/` adds a `Share` row for
a user or a group at a level; a second POST revokes one. The thread page renders the current
shares to the owner and to a principal that `sees_all_content` — a share list names who else is
reading somebody's thread, which is content about content, so an administrator reaches it only
with the setting on, for the same reason they do not reach the thread (§7.1).

Agents, flows and generated images are share-*able* by table and are not share-able by UI. That
is deliberate scope control, not an oversight: the `Share` table's generic shape means adding
the second UI is a form and a template, and the visibility functions already read the rows.

### 10.4 Adoption — an open box's first admin

```
manage.py adopt_open_rows --user <username> [--dry-run]
```

Lives at `identity/management/commands/adopt_open_rows.py` and walks `all_owned_rows()`.

- **Refuses** unless the named user exists, is active, and is a superuser.
- **Claims**, in one transaction, every row whose owner is `("open", "box")` **or** blank
  (`("", "")` — the pre-IA-1 rows in `vision` and `rag`), rewriting it to
  `("user", str(user.pk))`.
- **Leaves alone** every row already owned by a user, **and every row owned by
  `("service", "local")`** — a shell-made row has an owner, and adoption is for a box that had
  none. Rewriting it would erase the only record of where the row came from. `reassign_owner
  --from service` is the named path for handing one to a person (§10.5).
- **Idempotent**: a second run claims nothing.
- **`--dry-run`** prints the per-table counts and writes nothing.
- **Writes one `AuditEvent`**, `action="identity.adopted"`, `detail={"counts": {...},
  "user_id": ...}` — one row, not one per claimed row, because the interesting fact is the
  event and the counts, not five thousand line items.

**Reversibility** is the posture switch, exactly as owner decision 26 says. Switching back to
`open` makes every visibility function return everything, so the reassigned owner stops
mattering. The command does **not** have an `--undo`: rewriting owners back to `("open","box")`
would be a second, lossier operation that also erased any ownership recorded after adoption.

**Ordering guidance, printed by the command and documented in `docs/OPERATIONS.md`:** create
the superuser, run `adopt_open_rows`, *then* switch the posture. Switching first leaves the new
admin looking at their own empty box until they adopt — which is confusing, not dangerous.

### 10.5 Reassignment

```
manage.py reassign_owner --from <username|open|service> --to <username> [--kind <owned-rows key>]
```

Same registry, same transaction, one audit event (`identity.owner_reassigned`). It is the
documented follow-up to deactivating somebody who owned rows, and it is CLI-only in this phase
(owner decision 25). A page for it is deferred (§21).

---

## 11. Every route, its class, and its rule

### 11.1 Six classes

The classes encode the owner's administer-versus-read split directly: **R** is administration
and answers to `is_admin`; **O** and **L** are content and answer to `sees_all_content`.

| Class | Anonymous GET | Anonymous POST | Signed-in member | Entitlement owner | Superuser, content OFF | Superuser, content ON | Open posture |
|---|---|---|---|---|---|---|---|
| **P** public | as today | as today | as today | as today | as today | as today | as today |
| **A** authenticated | 302 login / 401 XHR | **403 or 302** (see below) | 200 | 200 | 200 | 200 | 200, no query |
| **O** owned *content* | 302 / 401 | 403 or 302 | 200 if visible, **404** otherwise | same | **404** unless owned or shared | 200 | 200 |
| **L** library *content* | 302 / 401 | 403 or 302 | 200 if the document is readable, **404** otherwise | 200 for documents under an owned entitlement | **404** unless an entitlement or the open library posture allows it | 200 | 200 |
| **R** operational *rows* / administration | 302 / 401 | 403 or 302 | 200 for their own rows, **404** otherwise | same, plus rows under an owned entitlement | **200** | 200 | 200 |
| **S** superuser | 302 / 401 | 403 or 302 | **403** | 403 | 200 | 200 | 200 |

**404, not 403, for O, L and R.** A `403` on a row-addressed URL confirms the row exists, which
is exactly the enumeration `chat-turn-status`'s sequential integer already exposes. A `403` on an
admin surface is fine and is used, because the *existence* of a model console is not a secret.

**The two superuser columns are the whole of the owner's decision, rendered as a table.** With
`admin_sees_content` off — the default — an administrator is answered like a member on every O
and L route and like an administrator on every R and S route: they run the box without reading
anybody's words. Turning the setting on collapses the two columns. **There is no posture
column, because there is no posture branch**: `personal` and `enterprise` produce identical
answers here (§7.1). The matrix asserts both columns, in both non-open postures (§16.2).

**An anonymous POST is refused by CSRF before the gate ever runs, and the matrix says so rather
than pretending otherwise.** `config/settings.py::MIDDLEWARE` orders
`django.middleware.csrf.CsrfViewMiddleware` **before**
`django.contrib.auth.middleware.AuthenticationMiddleware`, and
`IdentityGateMiddleware` sits after the latter because it needs `request.user`. So a browser
POST with no CSRF cookie gets `403` from CSRF; one that has a valid CSRF cookie but no session
reaches the gate and gets `302`/`401`. Both are correct refusals and neither is a 500, so the
matrix accepts either for an anonymous POST. This ordering is **not** changed: moving the gate
ahead of CSRF would mean an unauthenticated cross-site POST was evaluated for authorisation
before it was evaluated for forgery, which is the wrong order to fail in.

Django's test client disables CSRF enforcement by default, which would make this row vacuous, so
§16.2 runs at least one anonymous POST per class through a client built with
`enforce_csrf_checks=True` and asserts the `403` — the anti-vacuous pin the rest of the matrix
would otherwise be missing.

### 11.2 The gate

`identity/middleware.py::IdentityGateMiddleware`, installed after
`django.contrib.auth.middleware.AuthenticationMiddleware` in `config/settings.py::MIDDLEWARE`,
implements `process_view` (so the URL is already resolved) and enforces only the coarse tier —
`PUBLIC`, `AUTHENTICATED`, or `ADMIN`. The six classes of §11.1 map onto those three: **P** is
`PUBLIC`; **A**, **O**, **L** and **R** are all `AUTHENTICATED` at this layer; **S** is `ADMIN`.
O, L and R need the row itself, so their real rule stays in the view, through the visibility
functions — the middleware answers "may this principal be here at all", never "may they see this
row".

- In `open` posture it returns immediately.
- It looks up `request.resolver_match.url_name` in `identity/routes.py::ROUTE_RULES`.
- **A name absent from the table is treated as S** — the strictest tier — and logged. Forgetting
  to classify a new route fails closed and loudly, rather than shipping it open.
- Anything whose `resolver_match.app_names` contains `"admin"` is S, which is also how
  `/admin/` becomes superuser-only rather than Django's default staff-only, with no `AdminSite`
  subclass.
- It also applies the rolling session expiry (§14).

`identity/gate.py` carries `require_principal` and `require_admin` decorators for the handful of
views (management commands' HTTP counterparts, future MCP edge) that will want an explicit
local check; the middleware is the mechanism, the decorators are the belt.

### 11.3 The table

Every route that exists at `HEAD = c00c3b5`, from the six real `urls.py` modules and
`config/urls.py`. **48 routes**, plus Django's admin tree.

#### `/chat/` — `agents/chat/urls.py`

| Name | Method | Class | Rule |
|---|---|---|---|
| `chat-index` | GET | A | lists `visible_conversations`; the agent picker lists `visible_agents`; offers from `installed_agent_slugs` |
| `chat-start` | POST | A | the chosen agent must be in `visible_agents`, else 400; `create_conversation` stamps the actor |
| `chat-default-install` | POST | A | `install_default` stamps the actor as owner |
| `chat-conversation` | GET | O | `visible_conversations` |
| `chat-turn` | POST | O | `visible_conversations`, **and** the principal must be the owner, `sees_all_content`, or hold a `use`-level share; a `view` share gets 403 |
| `chat-conversation-delete` | POST | O | owner or `sees_all_content` only; deletes the conversation's `Share` rows with it |
| `chat-turn-status` | GET | O | resolved through `visible_turn`, never by bare pk |
| `chat-conversation-share` *(new, IA-2)* | POST | O | `POST /chat/c/<uuid>/share/` (§10.3). Owner or `sees_all_content` only — a recipient may not re-share. Adds a `Share` row through `agents/visibility.py::share_conversation`; the same route revokes one through `::revoke_share(principal, conversation, share_id)`, keyed on a `share_id` in the body, so unsharing is not a second URL. Revoke answers **404** for an unparseable `share_id` and **404** for a share row belonging to another conversation (§7.2) |
| `chat-tool-entitlements` *(new, IA-2)* | GET/POST | S | labels tools with entitlements; lives here because `/chat/` is the agents column's only mount. Lists **mutating** tools too, marked page-only (§9.3, §22.35) |
| `chat-agent-entitlements` *(new, IA-2)* | GET/POST | S | `/chat/access/` — labels **agents and flows** with entitlements (§6.11, §9.6). ONE page for both: they are two runnable row kinds in one column, read by two functions that already differ only in which model they filter, and two pages would be one rule rendered twice |

#### `/rag/` — `tools/rag/urls.py`

| Name | Method | Class | Rule |
|---|---|---|---|
| `rag-ask-page` | GET | A | the category list is narrowed to categories with at least one **readable** document — this page leads to content, so it uses `readable_documents`, not the listing's function |
| `rag-ask` | POST | A | payload carries the actor; retrieval filtered (§8.3) |
| `rag-ask-status` | GET | **R** | the job must be in `visible_jobs`, **and** the answer text is content: an administrator with the setting off gets the row's state, not somebody else's answer (`may_read_job_content`, §7.6). **R**, not O, because it resolves through the QUEUE's `visible_jobs`, which is `is_admin` — a content-off administrator gets 200 with the answer withheld, which is the `jobs-queue` pattern, and O would demand a 404 the view does not give |
| `rag-search` | GET | A | retrieval filtered |
| `rag-documents` | GET | **R** | lists `listable_documents` — an administrator sees every document ROW (title, category, labels, status) in every posture, with the content setting off, because labelling and pruning the library is administration (§8.2). The counts and the category sidebar come off the same function |
| `rag-document-upload` | POST | A | the uploader's document arrives **unlabelled** unless they pick a label the upload form offers them (IA-2). **2026-08-30:** the view first consults `tool_access_for(principal).allows("rag.ingest")` and answers **403** with honest copy before a byte is staged; the form render-gates on the same predicate (§9.3) |
| `rag-document-file` | GET | L | 404 outside `readable_documents` — **including for an administrator with the content setting off**: the row is theirs to manage, the bytes are not theirs to read |
| `rag-document-transcript` | GET | L | same |
| `rag-document-delete` | POST | **R** | resolved through `listable_documents`, plus `is_admin` or an owner of one of the document's entitlements. `is_admin` and not `sees_all_content`: removing a document is administration, and an administrator can remove one they cannot read |
| `rag-document-reingest` | POST | **R** | `is_admin`, or an owner of one of the document's entitlements — the same predicate as delete, written out rather than cross-referenced |
| `rag-document-labels` *(new, IA-2)* | POST | **R** | `is_admin`, or an owner of every entitlement being added or removed. This is the route the administer/read split exists for: an administrator labels a document they are not cleared to read, and the label takes effect on chunks whose text they never see (§8.2, §8.4). **2026-08-30:** the same route takes a **bulk** submission — N document ids, or one category — under the same predicate, per document (§15, §22.34). One route, because "label these" and "label this" are the same action at two cardinalities, and a second route would be a second predicate to keep in agreement |
| `rag-category-rename` | POST | S | a category is library-wide taxonomy, not a per-user object |
| `rag-category-delete` | POST | S | same |
| `rag-history` | GET | A | lists `visible_ask_records` — questions and answers are content, so an administrator with the setting off sees only their own |
| `rag-history-settings` | POST | S | operator policy |
| `rag-upload-cap-settings` | POST | S | operator policy |
| `rag-media-duration-settings` | POST | S | operator policy |
| `rag-document-pages-settings` | POST | S | operator policy |
| `rag-retrieval-top-k-settings` | POST | S | operator policy |
| `rag-retrieval-score-floor-settings` | POST | S | operator policy |
| `rag-hybrid-search-settings` | POST | S | operator policy, and it rebuilds the chunk table |

#### `/vision/` — `tools/vision/urls.py` (mounted only with the feature flag on)

| Name | Method | Class | Rule |
|---|---|---|---|
| `vision-create` | GET | A | |
| `vision-create-operation` | GET | A | the rendering alias; same view |
| `vision-gallery` | GET | A | lists `tools/vision/visibility.py::visible_jobs` — a generated image is content, so an administrator with the setting off sees only their own |
| `vision-operations` | GET | A | schema discovery; reveals no rows |
| `vision-generate` | POST | A | the job is stamped with the actor. **IA-2, 2026-08-30:** the view also consults `tool_access_for(principal).allows("vision.generate")` and answers **403** with honest copy when the tool is labelled and the principal holds none of its entitlements — an in-view gate, the same shape `rag-document-delete` uses, so the middleware class stays A (§9.3). A picked connection the principal may not use is refused by `resolve_connection_named` before anything is staged or queued (§9.5) |
| `vision-job-status` | GET | O | `tools/vision/visibility.py::visible_jobs` |
| `vision-job-delete` | POST | O | owner or `sees_all_content`. Deleting somebody's generated image is not on the owner's administer list, so it stays with the content predicate |
| `vision-output-file` | GET | O | resolved through `output.job` |
| `vision-input-file` | GET | O | resolved through `input.job` |
| `vision-queue-status` | GET | **R** | the queue job must be in the QUEUE's `visible_jobs` (`is_admin`), and its result is content (`may_read_job_content`) — same reasoning as `rag-ask-status`. Its sibling `vision-job-status` stays **O**, because that one resolves through `tools/vision/visibility.py::visible_jobs`, which is content |

#### `/inference/` — `models/registry/urls.py`

| Name | Method | Class |
|---|---|---|
| `inference-console` | GET | **S** |
| `inference-connection-add` | POST | S |
| `inference-connection-remove` | POST | S |
| `inference-machine-add` | POST | S |
| `inference-role-assign` | POST | S |
| `inference-role-reencode` | POST | S |
| `inference-server-scan` | POST | S |
| `inference-connection-sets` *(new, IA-2)* | POST | S | `POST /inference/connections/<int:pk>/sets/` — sets one connection's **set memberships** to exactly what was submitted (§6.10). **S, not R:** a model connection is box inventory, not somebody's library row, so an entitlement owner has no standing over it — the owner's three capabilities are grants and documents (§7.4) |
| `inference-model-sets` *(new, IA-2)* | GET, POST | S | `/inference/sets/` — every set with its members and its attached entitlements; POST creates one |
| `inference-model-set-edit` *(new, IA-2)* | POST | S | `/inference/sets/<int:pk>/` — one `action` field: rename, delete, attach an entitlement, detach one. Deleting names its member and attachment counts first, the same way an entitlement delete does (§12.1) |

The console **read** is S as well as the mutations. It displays endpoints, model identifiers
and connection configuration — an inventory of the box, which is operator information. This
closes ADR 0010's standing unauthenticated-mutation gap in full rather than half.

#### `/queue/` — `models/queue/urls.py`

| Name | Method | Class | Rule |
|---|---|---|---|
| `jobs-queue` | GET | **R** | lists `visible_jobs` (§7.6) — an administrator sees every ROW (kind, owner, state, progress, priority, timings) in every posture with the content setting off; each row's payload and answer text are withheld unless `may_read_job_content` says otherwise, and the page renders an explicit "content hidden" marker rather than a blank |
| `jobs-queue-settings` | POST | S | memory budget, concurrency, retention, priority — operator policy |
| `jobs-queue-cancel` | POST | **R** | a job in `visible_jobs`; 404 otherwise. **An administrator may cancel any job, always** — a stuck queue is an operational fact and clearing it is the operator's job, so this is never gated by the content setting |

#### `/setup/` — `foundation/setup/urls.py`

| Name | Method | Class | Rule |
|---|---|---|---|
| `setup-index` | GET | **P** | owner decision 24. It explains how to install engines and names no row, no model choice and no document. It is the page a person needs *before* they can log in to a box whose engines are not up. |

#### `/admin/`

Every route under it: **S**, via the `app_names` branch (§11.2). Django registers `auth.Group`
and — after `identity/admin.py` — `identity.User`. Nothing else is registered.

#### New in IA-1

| Path | Name | Method | Class |
|---|---|---|---|
| `/identity/login/` | `identity-login` | GET, POST | **P** |
| `/identity/logout/` | `identity-logout` | POST | A |
| `/identity/password/` | `identity-password-change` | GET, POST | A |
| `/identity/users/` | `identity-users` | GET | S |
| `/identity/users/new/` | `identity-user-create` | POST | S |
| `/identity/users/<int:user_id>/` | `identity-user-edit` | POST | S |
| `/identity/settings/` | `identity-settings` | GET, POST | S |

#### New in IA-2

| Path | Name | Method | Class |
|---|---|---|---|
| `/identity/groups/` | `identity-groups` | GET, POST | S |
| `/identity/groups/<int:group_id>/` | `identity-group-edit` | POST | S |
| `/identity/entitlements/` | `identity-entitlements` | GET, POST | S |
| `/identity/entitlements/<int:entitlement_id>/` | `identity-entitlement-edit` | GET, POST | S for create/rename/delete; an **owner** of that entitlement may reach the grant form (checked in the view, since the middleware's tiers are coarse) |

`LOGIN_URL = "identity-login"`, `LOGIN_REDIRECT_URL = "chat-index"`,
`LOGOUT_REDIRECT_URL = "identity-login"`.

### 11.4 Two notes on the surface as a whole

**There is no route at `/`.** `config/urls.py` mounts nothing at the root, so `/` is a 404
today and stays one. Adding a landing page is out of scope.

**Login uses Django's own views.** `django.contrib.auth.views.LoginView` and `LogoutView`
(POST-only in the installed Django 5.x) and `PasswordChangeView`, subclassed only to write the
audit event in `form_valid` and to render this platform's shell template. There is no email
server on the box, so the login page says so in one line: a forgotten password is reset by an
administrator running Django's own `manage.py changepassword <username>`. No password-reset
command is written — Django ships one.

---

## 12. Deactivation, cascades, and the last-admin guard

### 12.1 Cascades

| Deleting | Takes with it | Mechanism |
|---|---|---|
| an `Entitlement` | its grants, its document labels, its tool labels | `CASCADE` on all three FKs |
| — and therefore | documents that lose their last label become **unlabelled** and follow the library posture | the label rows are gone, so `restamp_document_chunks` is called for each affected document inside the delete transaction (a bounded loop over `entitlement.document_labels`) |
| a `Group` | its grants and its shares | `CASCADE` |
| a `User` | not offered (§12.2) | — |
| a `Conversation` | its turns (already `CASCADE`) and its shares | `visibility.delete_conversation` |

Deleting an entitlement is a superuser action, not an owner action, precisely because of the
second row: it silently widens access to every document it was the last label on. The delete
confirmation names the count.

### 12.2 Users are deactivated, never deleted

`identity/services.py::deactivate_user(actor, user)`:

1. refuses if `user` is the last active superuser (§12.3);
2. sets `is_active = False`;
3. deletes every `EntitlementGrant` for that user (owner decision 25: grants dropped);
4. leaves every owned row in place, and prints/returns the per-table counts so the operator
   knows to run `reassign_owner`;
5. writes `AuditEvent(action="identity.user_deactivated")`.

**Sessions die for free, and this is why no session-sweeping code is written.** Django's
`ModelBackend.get_user` calls `user_can_authenticate`, which returns `False` for an inactive
user, so `request.user` becomes `AnonymousUser` on the very next request that presents the old
session cookie. Writing a `Session`-table sweep on top of that would be a second, weaker copy of
a mechanism Django maintains. `docs/OPERATIONS.md` records the mechanism so an operator does
not wonder whether the sweep was forgotten.

Reactivation is the same page action in reverse, minus the grants — they are not restored,
because a dropped grant is a decision somebody made.

### 12.3 The last-admin guard

`identity/services.py::set_superuser` and `::deactivate_user` both refuse to leave the box with
zero active superusers, with an operator-readable message naming why.

**Concurrency.** The check cannot be a database constraint — "at least one row satisfying a
predicate" is not expressible as a `CheckConstraint`. Two simultaneous demotions could each see
two admins and each proceed. The guard therefore runs inside `transaction.atomic()` with
`User.objects.select_for_update().filter(is_active=True, is_superuser=True)`, which serialises
the two transactions and makes the second one see one admin and refuse. Named here because a
guard that is only correct single-threaded is a guard that fails on the one day it matters.

Switching the posture away from `open` runs the same check in reverse, as one of three:
`set_posture` refuses under any of §14's three conditions — no active superuser, `DEBUG` true, or
a default `SECRET_KEY` — of which this last-admin check is one (§3.2, reason 2).

---

## 13. Audit

### 13.1 The writer

One function, `identity/audit.py::record(actor, action, *, target_type="", target_key="",
target_label="", source="web", **detail)`. It resolves `actor_label` at write time and never
later. It is the only module in the codebase that may touch `AuditEvent.objects` **at all**,
pinned by an AST guard in `test_column_boundaries.py` in the same shape as the chat `.objects`
guard: `ast.Attribute(value=ast.Name(id="AuditEvent"), attr="objects")` anywhere outside
`identity/audit.py` is an offender.

**The guard forbids the whole manager, not just `.create`,** because append-only is a code-level
property here (§6.5): `.update()` and `.delete()` never call `save()`, so a guard that only
looked for `create` would let the two operations that actually destroy an audit trail through.
Reads go through named helpers in the same module (`recent(...)`, `for_target(...)`), so the
audit page needs no exception either — and a guard with an exception is a guard somebody
widens.

### 13.2 The catalogue

A closed tuple in `identity/contracts/actions.py`, validated by `AuditEvent.save()`.
**Forty-two** actions, in five families (3 + 7 + 5 + 10 + 17). Thirty-one were declared in IA-1;
the other eleven arrive with the two 2026-08-30 owner directives (§22.31, §22.33), which are the
one amendment to "the vocabulary is not amended twice" this document makes, and it is recorded
rather than quietly absorbed: three label kinds that existed in no plan when the catalogue was
closed could not have had actions reserved for them.

**The set vocabulary mirrors the group vocabulary**, deliberately — a model set is a group of
models, `auth.Group` is a group of people, and giving the two different verb shapes would make one
audit report read two ways. `modelset.attached`/`detached` have no group twin only because a group
needs none: a grant covers what an attachment covers there.

| Family | Actions |
|---|---|
| session | `identity.login`, `identity.login_failed`, `identity.logout` |
| account | `identity.user_created`, `identity.user_deactivated`, `identity.user_reactivated`, `identity.password_changed`, `identity.password_reset`, `identity.superuser_granted`, `identity.superuser_revoked` |
| posture & ownership | `identity.posture_changed`, `identity.library_posture_changed`, `identity.admin_content_access_changed`, `identity.adopted`, `identity.owner_reassigned` |
| entitlements & groups | `entitlement.created`, `entitlement.renamed`, `entitlement.deleted`, `grant.added`, `grant.role_changed`, `grant.revoked`, `group.created`, `group.deleted`, `group.member_added`, `group.member_removed` |
| labels & shares | `library.document_labelled`, `library.document_unlabelled`, `tool.labelled`, `tool.unlabelled`, `agent.labelled`, `agent.unlabelled`, `flow.labelled`, `flow.unlabelled`, `modelset.created`, `modelset.renamed`, `modelset.deleted`, `modelset.member_added`, `modelset.member_removed`, `modelset.attached`, `modelset.detached`, `share.added`, `share.revoked` |

`identity.admin_content_access_changed` records the new value in `detail`. It is audited
exactly like the posture and for the same reason: it is the one setting that changes what an
administrator can read, and "when did this box start letting admins read conversations" must be
answerable from the log rather than from memory.

`identity.login_failed` records the **submitted username** in `target_label` and never the
password, never a hash of it, and never the request body. It exists so a brute-force attempt is
visible; login lockout is deferred (§21).

### 13.3 What is deliberately not audited

Page views, document reads, search queries, and tool calls. The first three would produce an
audit log nobody reads, in which the interesting lines are invisible. Tool calls already have a
better record: `agents/models.py::ToolInvocation`, one row per call with its outcome class,
which after §5.3 carries both the acting principal and the agent that made the call.

`AuditEvent` and `ToolInvocation` are two tables on purpose. One answers "who changed what
about this box", the other "what did the machinery do". Merging them would make retention
policy impossible: the first must be kept, the second must be prunable.

---

## 14. Sessions, cookies, and the DEBUG check

**Rolling idle expiry.** `IdentityGateMiddleware` calls
`request.session.set_expiry(minutes * 60)` on every authenticated request, reading `minutes`
from the settings row it has already loaded; `SESSION_SAVE_EVERY_REQUEST = True` makes the
rolling window actually roll. `session_idle_minutes = 0` means "expire when the browser closes"
(`set_expiry(0)`), which is why one column covers both behaviours and no
`SESSION_EXPIRE_AT_BROWSER_CLOSE` setting is added. "Keep me signed in" is deferred (§21); its
hook is this same call.

**Cookies.** `SESSION_COOKIE_HTTPONLY = True` and `SESSION_COOKIE_SAMESITE = "Lax"` are stated
explicitly in `config/settings.py` rather than left to Django's defaults, because a default that
is right is still a default somebody can change without noticing. `SESSION_COOKIE_SECURE` and
`CSRF_COOKIE_SECURE` are bound to a new `SECURE_COOKIES` environment variable defaulting to
off — whether the box is behind TLS is a *deployment* fact, exactly as a server location is
(the same exception the engine base-URL settings in `config/settings.py` already carry under
ADR 0010's no-baked-defaults rule), and forcing Secure
cookies on would break login on a plain-HTTP LAN box, which is a supported posture
(`docs/ARCHITECTURE.md`'s `isolated-lan`).

**Three system checks**, registered in `identity/apps.py::ready()`:

- **`identity.E001` (error):** `DEBUG` is on while the posture is not `open`. Owner decision 24.
  `DEBUG=True` renders tracebacks with settings and environment to any visitor, which is a
  disclosure a posture with accounts must not permit.
- **`identity.E002` (error):** the posture is not `open` while `SECRET_KEY` still equals the
  shipped development default at `config/settings.py`. That key signs every session cookie and
  every password-reset token; a box whose signing key is published in a public repository has
  accounts in name only, and anybody who can read the repository can mint a session for any
  account on it. This check is the reason accounts and a default key cannot coexist.
- **`identity.W001` (warning):** accounts are on and `SECURE_COOKIES` is off. A warning, not an
  error, because HTTP-on-LAN is supported.

All three catch `DatabaseError` and return no message: a box mid-migration, or one running
`manage.py migrate` against an empty database, has no settings row and is not in violation. The
swallow is documented in each check's own docstring so it is not mistaken for a hole.

**A check that runs only at startup is not enough, and `set_posture` closes the gap.** The image
starts with `migrate && <server>`, so `manage.py check` runs once per boot: flipping the posture
on a running box with `DEBUG=1` or a default `SECRET_KEY` would change nothing until the next
restart, which is precisely the window in which the operator believes accounts are on.
`identity/services.py::set_posture` therefore **refuses a switch away from `open`** on three
conditions, in one place, with one refusal shape and one operator-readable message each:

1. no active superuser exists (§3.2, reason 2);
2. `settings.DEBUG` is true (the run-time half of `identity.E001`);
3. `SECRET_KEY` equals the shipped default (the run-time half of `identity.E002`).

`manage.py identity_posture` reaches the same function, so the break-glass path is not a way
around the three refusals. Switching *to* `open` is refused by none of them — reducing a
posture is always allowed.

`set_posture` also **resets `library_posture` to `open` when it moves a box to `personal`**, and
audits that reset. `personal` renders no library-posture control, so a box arriving there from a
`locked` `enterprise` would otherwise hold a lock with no page to lift it. This is the one place
a posture branch lives, and it lives in a write path on purpose: the read paths in §7.1 stay
posture-free.

**`admin_sees_content` is edited on the same page, audited the same way, and takes effect
immediately.** It is read per request through `get_solo()` like every other field on that row
(§3.2), so turning it off ends an administrator's access to content on their very next request —
no restart, no session flush. Turning it on is refused by nothing: it is a widening the operator
is entitled to make, and the audit row is the record that they made it.

---

## 15. Admin surfaces

Server-rendered, minimal, zero-JS, extending `foundation/templates/_shell.html`, matching every
other page on the box. All of them are class **S** except where noted.

| Page | Phase | Column | What it does |
|---|---|---|---|
| Users | IA-1 | identity | list, create, deactivate/reactivate, toggle superuser, reset password. In `personal` posture it hides the superuser toggle and says every account is an administrator (§3.1). (rename: later, needs a catalogue action — `identity.services` has no rename primitive and `AUDIT_ACTIONS` has no entry for it yet) |
| Change password | IA-1 | identity | the signed-in person's own; class A |
| Posture | IA-1 | identity | posture, library posture, session idle minutes, and the **administrator-content toggle** (`admin_sees_content`, default off) — the toggle is present in `personal` as well as `enterprise`, since the administer/read split has no posture branch (§7.1). Refuses a switch away from `open` on any of §14's three conditions — no active superuser, `DEBUG` true, or a default `SECRET_KEY` — and resets `library_posture` to `open` on a move to `personal` |
| Groups | IA-2 | identity | create, delete, add/remove members |
| Entitlements | IA-2 | identity | create, rename, delete, and per-entitlement grants (users and groups, member/owner). An **entitlement owner** may reach the grant form for entitlements they own; everything else is superuser |
| Document labels | IA-2 | **tools/rag** | on the document page: add/remove entitlement labels. Lives here because `identity` may not import `tools` (§4.2), and because a label belongs beside the thing it protects |
| Tool labels | IA-2 | **agents/chat** | the same, for registered tool keys. `/chat/tools/` because `/chat/` is the agents column's only URL mount |
| Conversation sharing | IA-2 | agents/chat | on the thread page, to the owner and to a principal that `sees_all_content` |
| Model sets | IA-2 (2026-08-30) | **models/registry** | `/inference/sets/` — create, rename, delete a set; attach and detach entitlements. Membership is edited per connection on the console itself, beside the row it belongs to, because that is where an operator is standing when a new model arrives |
| Agent & flow access | IA-2 (2026-08-30) | **agents/chat** | `/chat/access/` — one page, two sections, the labels that restrict which agents and flows a person may run |
| Bulk document labelling | IA-2 (2026-08-30) | tools/rag | on the library: a checkbox per row, one apply/remove control, and a "label every document in this category" action. **One action, not N** |
| Entitlement reach | IA-2 (2026-08-30) | identity | the entitlement page shows everything it touches: grants (users and groups), documents, tool labels, model-set attachments, agent and flow labels |
| Effective access | IA-2 (2026-08-30) | identity | the users page shows one account's effective entitlements — direct and via each group, each row saying which — and what they unlock |

**The last four exist because of one owner principle**, recorded at §22.34: *"good control over
the system… easy to set up now and easy to maintain for admins. A tedious maintenance process for
changes over time is a failure."* Every **routine** administrative change is **one action**, and
the two overview surfaces are the "see the state" half of the same requirement — an operator who
cannot answer "what does this entitlement reach" or "what can this person do" without opening six
pages does not have control, whatever the write paths allow.

A **nav link** appears in `foundation/templates/_shell.html` only when the posture is not `open`
**and** the viewer is an admin — the household box's shell is byte-identical to today's. The
precedent is `tools.vision.context_processors.features`, registered in
`config/settings.py::TEMPLATES` and supplying the `farabunker_features` context key the shell
already gates a nav link on. A second context processor,
`identity.context_processors.identity`, is registered beside it and supplies `identity_posture`
and `identity_is_admin`, so no page has to ask twice.

**Nothing new is registered in `/admin/`** beyond `identity.User` (§6.1). `/admin/` stays what
ADR 0015 already calls it: a break-glass tool, not a product surface.

---

## 16. Testing

Per ADR 0008, tests and docs ship in the same PR. Conventions from the 2026-08-25 spec §11.1
hold unchanged: no `conftest.py` anywhere, helpers duplicated per package in `_helpers.py`,
autouse fixtures defined per module delegating to `_helpers`, and any test overriding
`FARABUNKER_FEATURES` with a literal `frozenset` while doing HTTP or `reverse()` keeps
`"vision"` in the set.

### 16.1 New helpers and doubles

- `identity/tests/_helpers.py`: `posture(name)` (a context manager writing the singleton row and
  restoring it), `make_user(**overrides)`, `make_admin(...)`, `make_entitlement(...)`,
  `grant(...)`, `sign_in(client, user)`.
- `tools/rag/tests/_helpers.py`, `tools/vision/tests/_helpers.py`, `agents/tests/_helpers.py`,
  `models/queue/tests/_helpers.py` each get their own copies of `posture` and `make_user`, per
  the per-package duplication rule (the same rule ADR 0015 G11 and its amendment already
  justify for `isolated_tool_registry`).
- No new engine doubles: nothing in this phase talks to an engine.

### 16.2 The route × principal matrix

`identity/tests/test_route_matrix.py`, modelled directly on
`agents/chat/tests/test_never_500.py` — **the route list is derived, not typed.**

1. Walk `django.urls.get_resolver().url_patterns` recursively for every URL name in the
   project, recording each name's `app_names`.
2. **Two assertions, not one set-equality**, because a naive equality cannot hold: the resolver
   also yields Django's whole `/admin/` tree, whose names this platform does not classify one by
   one (§11.2 classifies it wholesale by `app_names`), and the `/vision/` names exist only when
   the `vision` feature flag is on (`config/urls.py` mounts that tree conditionally). So:
   - **every derived name whose `app_names` does not contain `"admin"` is in `ROUTE_RULES`** —
     the direction with the teeth. A route added to any `urls.py` without a classification fails
     here immediately, naming itself, which is what makes §11.3 a live artefact rather than a
     document that rots.
   - **every `ROUTE_RULES` key is in the derived set**, with the `/vision/` names skipped when
     the flag is off — the direction that catches a rule left behind by a deleted route.
   A third, small assertion pins that the admin exclusion is not swallowing everything: at least
   one `admin`-namespaced name was derived, and the non-admin derived set is non-empty and
   contains a known name from each of the six mounts.
3. For each name, a driver in `_DRIVERS` supplies a request (path arguments, method, body).
   A name with no driver raises `KeyError` naming it — the same "swept automatically" mechanism
   the chat never-500 sweep already uses.
4. Cross-product: **every route × {anonymous, member, entitlement-owner, admin} × {open,
   personal, enterprise} × {`admin_sees_content` off, on}**. Assert the status is the one
   §11.1 requires for that route's class — in particular that with the setting **off** an
   administrator gets the member answer on every **O** and **L** route and the administrator
   answer on every **R** and **S** route, and that turning it **on** changes the O and L answers
   and changes nothing else. Assert too that the two non-open postures give **identical**
   answers for the same principal and the same setting, which is the assertion that fails if a
   posture branch is ever reintroduced into a visibility function — and always that it is in `_NEVER_500_STATUSES` (the chat sweep's set plus `401` and
   `403`) and that the body contains no `"Traceback"`.
5. At least one anonymous POST per class runs through a client built with
   `enforce_csrf_checks=True` and asserts the `403` (§11.1). Django's test client disables CSRF
   by default, so without this the anonymous-POST column of the matrix would be vacuous.

### 16.3 Posture parametrisation, alongside the feature-flag gate matrix

`docs/DEV.md` fixes two supported feature-flag states (`'vision,media'` and `'vision'`) and two
collection orders — four runs. Multiplying that by three postures gives twelve, which is not a
sane per-change gate. The resolution has two halves and both are documented:

**Default posture for the suite is `open`.** It is the model default, so every existing test is
unaffected and the two supported suite states in `docs/DEV.md` stay two.

**A test that depends on posture pins it**, with `identity/tests/_helpers.py::posture(...)`.
This is the repository's existing rule for feature-dependent tests ("flag-dependent tests pin
their own feature state"), applied to a second axis. The three modules that parametrise across
all three postures explicitly are the route matrix (§16.2), the retrieval-visibility tests
(§16.4), and the tool-entitlement tests.

**A full-suite posture sweep exists and is defined.** An autouse fixture in each package's
`_helpers.py` seeds the settings row from `FARABUNKER_TEST_POSTURE` when it is set. That
variable is read by **test helpers only** — never by production code, which would reintroduce
the second truth §3.2 rejects — and a test that pins its own posture always wins over it, the
same precedence the feature flags use. `docs/DEV.md` gains the run:

```bash
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
```

documented as **required before merging identity work and before any release**, and not as a
per-change gate — the same status the reversed collection order already has for ordinary
changes.

### 16.4 What must be tested

**Administer versus read.** With `admin_sees_content` **off** — the default — a superuser
reads no other account's conversation, generated image, Ask history, agent or flow body, share
list, document bytes, transcript, retrieval hit, or job payload: one negative test per item.
With it **on**, every one of those flips to readable and nothing else changes. In **both**
states the same superuser sees every queue ROW, cancels any job, sees every document ROW, and
labels, deletes and re-ingests any document — including **a document they cannot read**, which
gets its own test because it is the case the whole split exists for: label a document with an
entitlement the admin does not hold, assert the label wrote, assert the chunk re-stamp ran, and
assert `rag-document-file` still answers 404 to that admin. The two non-open postures are
asserted to answer identically for the same principal and the same setting; a test that
reintroduced a posture branch would fail here. Toggling the setting takes effect on the next
request, with no restart and no re-login, and writes one
`identity.admin_content_access_changed` audit row. `open` executes zero queries against the five identity/permission tables on a
representative GET of each mount (`django_assert_num_queries` plus a query-capture assertion on
table names). Switching to `personal` with no active superuser is refused. Switching to
`enterprise` from `personal` changes no row but the one. `identity.E001` fires for
`DEBUG=True` + non-open and `identity.E002` for a default `SECRET_KEY` + non-open; both, and
`identity.W001`, are silent when the settings row cannot be read. **`set_posture` refuses a
switch away from `open` under each of §14's three conditions INDEPENDENTLY** — no active
superuser with `DEBUG` off and a real key; `DEBUG` on with an admin and a real key; a default
`SECRET_KEY` with an admin and `DEBUG` off — so a test cannot pass because a second condition
happened to fire. Each refusal is exercised twice, through the page and through
`manage.py identity_posture`, because the break-glass command must not be a way around any of
them. Switching *to* `open` succeeds under all three.

**Principals.** `principal_for_request` returns `OPEN_PRINCIPAL` in open, the user principal
when signed in, and `ANONYMOUS` otherwise — never the open principal in a posture with
accounts. `principal_from_payload` round-trips `payload_fields`. The AST guard's constructor set
is exactly two files, with its anti-vacuous pins.

**The acting rule.** A turn started by user A runs with `ctx.principal == Principal("user",
str(A.pk))` and `ctx.agent_slug == <agent>`. A delegate's `ToolContext` carries the **same**
principal and a different `agent_slug`. An agent declaring a tool the user is not entitled to
does not get it in its prompt — asserted at both the planner and the loop, so the declared model
set and the offered tool list are the same filtered set. `ToolInvocation` rows carry both.

**Tool entitlements.** `granted_tools` with `UNRESTRICTED_TOOL_ACCESS` behaves exactly as today
(the regression pin for open posture). A labelled tool is dropped for a principal without the
entitlement and kept for one with any of them. An unlabelled tool is kept for everybody. A
service principal gets unlabelled tools only. `mutates=True` is still dropped regardless.

**Tool entitlements at a DIRECT surface** (§9.3). `vision-generate` answers **403** to a
signed-in principal holding none of `vision.generate`'s labels, and **the create page does not
render the generation form** to that same principal — asserted as a pair, because a page that
renders a control whose POST refuses is the defect the render-vs-gate rule exists to remove.
Unlabelled, both answer exactly as they do today, and an open box is byte-identical. The route ×
principal matrix carries this route in both label states.

**Model sets** (§9.5). The **AND-composition** gets its own four-cell test: holder of the tool
label but not a set's entitlement is refused, holder of the set's entitlement but not the tool's
is refused, holder of both is admitted, holder of neither is refused. A set-restricted connection
is absent from all three pickers (chat, vision, Ask) for a non-holder and present for a holder; a
non-holder who posts its pk anyway is refused at `resolve_connection_named` on **every** submission
path; and an agent turn naming it refuses at **preflight** with `MODEL_NOT_PERMITTED` and a
**403**, before any turn row is written — asserted at all four `resolve_chat` callers, since three
of them are not the preflight one.

**The set indirection gets its own tests, because it is the thing that changed:** a connection in
two sets is reachable through **either**'s entitlement (the OR); adding one membership row makes a
model reachable by **every** entitlement already attached to that set, and attaching one
entitlement makes **every** member reachable — the two one-change-many properties the owner asked
for, each proved by a count rather than by a sentence; a set with **no** entitlement attached
restricts nothing; and a connection in **no** set is usable by every holder of the capability, with
a box holding zero `ModelSet` rows pinned by a query-count assertion on a picker render, so the
change is provably inert when unused.

**The exemption has its own test:** putting the `rag.embed` connection into a restricted set does
not break ingestion, retrieval or re-encode, and does not break `manage.py ingest` — the role path
is never gated (§9.5, §22.32).

**Agents and flows** (§9.6). A labelled agent is absent from `visible_agents` and from the
`/chat/` picker for a non-holder and present for a holder; the same for a flow and `visible_flows`,
and therefore for `flow.run`'s per-turn choices. A **`resident=True`** row is restricted like any
other — its own test, because the carve-out it composes with is the one a reader assumes wins.
An existing conversation whose agent the person may no longer see **still renders**, and a **new
turn** into it is refused with `AGENT_NOT_PERMITTED` and a **403** before any `Turn` row is
written. A delegate cannot reach a restricted agent: `run_agent_tool` refuses it, and the
`agent.<slug>` key is not offered to the model in the first place. `manage.py agent_turn` cannot
run a labelled resident agent. Unlabelled, every one of these answers exactly as it does today,
and an open box is byte-identical.

**The upload door** (§9.3). `rag-document-upload` answers **403** to a signed-in principal holding
none of `rag.ingest`'s labels, **nothing is staged into the inbox** (asserted on the directory,
not only on the response), and the library page does not render the upload form to that same
principal — the pair asserted together, as the vision pair is. Labelling `rag.ingest` **does not**
make it callable by an agent: `granted_tools` still drops it for `mutates=True`, which has its own
test so the two rules are seen not to collide (§22.35).

**Admin ergonomics** (§15). A bulk label applies to N documents in **one** POST and re-stamps each
one's chunks; a category bulk-apply labels every document in that category and **stores no rule** —
a second document added to the category afterwards is unlabelled, which is asserted, because the
absence of a stored mapping is the decision (§22.34). The entitlement page's reach panel is
sourced from the same function the delete confirmation counts with, so a test that changes one
count and reads the other proves they cannot disagree. The effective-access panel shows a
direct grant and a via-group grant distinctly, and shows nothing an administrator could not
already have derived — it is read-only, and a test asserts it writes nothing.

**Documents and retrieval.** `readable_documents` and `listable_documents` for each of: admin
with the content setting off, admin with it on, holder of one of two labels, holder of neither
with `library_posture=open`, same with `locked`. The pair that matters most:
`listable_documents` returns every row to an admin with the setting off while
`readable_documents` returns none of the labelled ones to the same admin — and the library page
renders the row, the count and the label chips from the first while `rag-document-file` refuses
from the second. `set_posture` to `personal` resets a `locked` `library_posture` to `open`. `retrieve_nodes`
with `sees_nothing` returns no nodes **and makes no retrieve call** (asserted by patching the
retriever) while still returning a usable index. The visibility filter is present on both the
dense and hybrid paths. The values passed to the `ANY` filter are all `str.isdigit()`. Two
documents, one labelled and one not, produce the four expected outcomes across the two library
postures.

**The chunk cache.** `restamp_document_chunks` writes the key for a labelled document, removes
it for one whose last label was taken, is a no-op when the chunk table is absent, raises from
the label-change path and only logs from the ingest path, and produces the same column content
whether it ran at ingest or on a later label change. No node built by ingest carries the key.
A re-ingest of a labelled document restores its labels.

**Ownership, adoption, sharing.** `all_owned_rows()` names five tables and every one resolves
through `apps.get_model`. `adopt_open_rows` claims open-owned and blank-owned rows, is
idempotent, refuses a non-superuser, `--dry-run` writes nothing, and writes exactly one audit
event. A shared conversation is visible to the recipient at `view` and postable at `use`; a
`view` share gets 403 on `chat-turn`. Deleting a conversation deletes its shares. **`revoke_share`
answers 404 for a non-integer `share_id`** (the 500 a bare `Share.objects.get` would raise on a
never-500 surface) **and 404 for a valid `share_id` belonging to another conversation** (the IDOR
an owner of one thread could otherwise use to unshare another) — two cases, two tests, both
asserting 404 rather than 403.

**Deactivation and the last-admin guard.** Deactivating drops grants and leaves rows. The next
request with the old session cookie is anonymous. The last active superuser cannot be demoted or
deactivated, and the concurrent case is exercised with two transactions.

**Audit.** Every action name written anywhere in the codebase is in `AUDIT_ACTIONS` (an AST
sweep for `record(` call sites, plus the model's own validation). Re-saving an `AuditEvent`
raises. **No module outside `identity/audit.py` performs any `AuditEvent.objects` attribute
access at all** — not `.create`, not `.filter`, not `.update`, not `.delete` — matching §13.1's
guard rather than a narrower reading of it. Anti-vacuous pin: planted sources containing
`AuditEvent.objects.update(...)` and `AuditEvent.objects.delete()` are both flagged by the same
AST walk, which is the case a `.create`-only guard would have waved through and the case that
actually destroys an audit trail.

**Structural guards** — the eight in §4.3, each with the anti-vacuous pin the house style
requires (the gate would actually catch a planted violation; the allow-list is not silently
empty; the sweep really reaches the directories it claims).

### 16.5 How to run it

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_<yours>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision' .venv/bin/pytest -q
.venv/bin/pytest -q                                                  # configured order
.venv/bin/pytest -q scripts agents foundation identity models tools  # reversed
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
```

`pytest.ini`'s `testpaths` gains `identity` **in the same commit that creates the directory** —
a stale or missing `testpaths` entry is silently ignored by pytest and stops collecting a whole
tree while the suite still exits 0, so the collected-test count is the only usable gate on that
edit (the correction the 2026-08-25 spec's addendum item 1 already recorded).

`docs/DEV.md`'s restart rule bites: `identity/middleware.py`, `identity/access.py` and the
runtime changes in §5.3 are job-kind-adjacent code the worker holds in memory.
`docker compose restart watcher worker` before every smoke test.

---

## 17. Migrations, in order

### IA-1

| # | Migration | Contents |
|---|---|---|
| 1 | `identity/0001_initial.py` | `User(AbstractUser)`, `IdentitySettings` (posture, library posture, session idle minutes, **`admin_sees_content` defaulting False**), `AuditEvent`. No `swappable_dependency` needed — `AuditEvent` deliberately has no user FK (§6.5) |
| 2 | `identity/0002_repoint_admin_log_fk.py` | the emptiness precondition and the catalogue-driven FK repoint (§6.1); no-op on a fresh install; `reverse_sql = noop` |
| 3 | `tools/vision/migrations/0006_generationjob_owner.py` | `owner_kind`, `owner_key`, index `vision_job_owner` |
| 4 | `tools/rag/migrations/0014_askrecord_owner.py` | `owner_kind`, `owner_key`, index `rag_askrecord_owner` |

`AUTH_USER_MODEL = "identity.User"` and `"identity"` in `INSTALLED_APPS` land in the same commit
as migration 1. Django resolves the ordering itself: `admin.0001_initial` already carries
`swappable_dependency(settings.AUTH_USER_MODEL)`, so on a fresh database `identity.0001` runs
first with no manual `run_before`.

### IA-2

| # | Migration | Contents |
|---|---|---|
| 5 | `identity/0003_entitlement_and_grant.py` | `Entitlement`, `EntitlementGrant`; `swappable_dependency(settings.AUTH_USER_MODEL)` and a dependency on `("auth", "__first__")` for `Group` |
| 6 | `tools/rag/migrations/0015_documententitlement.py` | depends on `("identity", "0003_entitlement_and_grant")` |
| 7 | `agents/migrations/0003_toolentitlement_and_share.py` | `ToolEntitlement`, `Share`, and `ToolInvocation.agent_slug`; same two dependencies |
| 8 | `models/registry/migrations/0008_modelset.py` | `ModelSet`, `ModelSetMember`, `ModelSetEntitlement` (§6.10, the 2026-08-30 directives); depends on `("identity", "0003_entitlement_and_grant")`, `swappable_dependency(settings.AUTH_USER_MODEL)`, and `("inference", "0007_modelconnection_config")` |
| 9 | `agents/migrations/0004_agent_flow_entitlements.py` | `AgentEntitlement`, `FlowEntitlement` (§6.11, the 2026-08-30 second directive); depends on `("agents", "0003_toolentitlement_and_share")`, `("identity", "0003_entitlement_and_grant")` and `swappable_dependency(settings.AUTH_USER_MODEL)` |

Migrations 5–9 are the repository's first cross-app migration dependencies (§4.2). The app label
is `inference`, not `registry` (`models/registry/apps.py`), so both `manage.py makemigrations
inference` and the migration's own dependency on its predecessor name `inference`.

**Two migrations in the `agents` app, not one.** The second directive arrived after migration 7
was designed and planned; folding `AgentEntitlement`/`FlowEntitlement` back into it would mean
rewriting a migration whose contents another task already depends on, to save a file. A second
numbered migration is what Django is for.

**One dependency change ships with migration 1.** `requirements.txt` pins `Django>=5.0,<6.0`,
but the `CheckConstraint(condition=...)` keyword used by `grant_user_xor_group` and
`share_user_xor_group` (§6.4, §6.8) requires **Django ≥ 5.1** — on 5.0 the keyword is `check`,
which 5.1 deprecates and 6.0 removes. Writing to the old spelling would mean shipping a
deprecation the next upgrade has to unpick. The pin becomes `Django>=5.1,<6.0` in the same PR as
IA-1's migrations. The installed version already satisfies it, so this is a floor being raised
to match what the code needs, not an upgrade.

**Gates on the migration set**, both phases: `manage.py migrate --plan` against a restored
production backup shows exactly these operations and no others; `manage.py makemigrations
--check --dry-run` exits 0 after each phase; and migration 2's precondition is exercised both
ways (a row present → raises with the message; no rows → repoints).

---

## 18. Phasing: IA-1 and IA-2

Two plans, two PRs, each independently mergeable, deployable and green.

### 18.1 IA-1 — accounts, login, postures, ownership, the route matrix

**Content.** The `identity/` column and its import-law amendment; `Principal` and its friends
moved to `identity/contracts/principals.py`; `identity.User` + the `AUTH_USER_MODEL` swap +
the admin-log repoint; `IdentitySettings`, the three postures and the `admin_sees_content`
toggle; `ACCOUNTS_REQUIRED` deleted;
`principal_for_request` moved and rewritten; `identity/access.py`, `identity/audit.py`,
`identity/routes.py`, `identity/middleware.py`, `identity/gate.py`, `identity/services.py`,
`identity/checks.py`, `identity/admin.py`; login/logout/change-password on Django's views; the
users page and the posture page; the superuser model and the last-admin guard; the acting rule
(§5.3) end to end, including `ToolContext.agent_slug`, `ToolInvocation.agent_slug`, the payload
actor keys on all five job kinds, and the deletion of `agents/runtime/bindings.py::
principal_for`; owner columns on `GenerationJob` and `AskRecord`; the owned-rows registry;
`adopt_open_rows` and `reassign_owner`; the AuditEvent skeleton and the full action catalogue
(actions IA-2 writes are declared now, so the vocabulary is not amended twice); the visibility
bodies for conversations, agents, flows, vision jobs, queue jobs and ask records (§7 minus
everything entitlement-shaped); session expiry, cookies and the three checks; the route matrix
over every route with the four principals, three postures and both settings of
`admin_sees_content`.

Entitlements do not exist in IA-1. Every `ToolAccess` is `UNRESTRICTED_TOOL_ACCESS`; every
`DocumentVisibility` is `unrestricted` only for a principal that `sees_all_content` — in IA-1
that means the open box, or an administrator with `admin_sees_content` on — and
`unlabelled_allowed` for anyone signed in; `library_posture` is stored and not yet consulted. That is deliberate: IA-1 must be a
complete, shippable posture on its own — a household box with three accounts and private
conversations — not half of a feature.

**Done when:**

1. `open` posture is byte-identical to today: the full suite is green in both feature states and
   both collection orders, and the zero-permission-query test passes on every mount.
2. `manage.py createsuperuser`, then `identity_posture personal`, then a browser login, then a
   private conversation that a second account cannot see or poll — driven on the branch preview
   stack.
3. The route matrix passes for all 48 existing routes plus IA-1's seven, across four principals,
   three postures and both settings of `admin_sees_content`, with no 500 and no traceback in any
   body.
4. `adopt_open_rows --dry-run` then `--user` on a restored production backup reports and then
   claims the real row counts, idempotently, with one audit row.
5. `ACCOUNTS_REQUIRED` appears nowhere in the tree (`grep` gate), and the eight structural
   guards in §4.3 are green with their anti-vacuous pins.
6. `manage.py migrate --plan` on a restored backup shows exactly IA-1's four migrations;
   `makemigrations --check --dry-run` exits 0.
7. `DEBUG=1` with a non-open posture fails `manage.py check` with `identity.E001`; a default
   `SECRET_KEY` with a non-open posture fails with `identity.E002`; and `set_posture` refuses a
   switch away from `open` under either condition, or with no active superuser, at run time as
   well as at boot (§14).
8. With `admin_sees_content` off, a superuser reads no other account's conversation, gallery or
   Ask history, **and** sees every queue row, cancels any job, and reaches every admin surface —
   driven in a browser as well as in the suite. Turning the toggle on flips the first half and
   nothing else, on the next request, with one audit row. Both non-open postures give identical
   answers.
9. The full ladder to fresh pixels on the live box, per `docs/DEV.md`.

### 18.2 IA-2 — groups, entitlements, labels, sharing

**Content.** `Entitlement`, `EntitlementGrant`, `DocumentEntitlement`, `ToolEntitlement`,
`Share`; the entitlement-owner role; `held_entitlement_ids` / `owned_entitlement_ids` /
`may_see_unlabelled` given bodies; `tools/rag/access.py` and the required `visibility` argument
threaded through `retrieve_nodes` → `answer_question` → the two runners → the two views;
`tools/rag/labels.py::restamp_document_chunks` and `manage.py relabel_chunks`; the library
posture given effect; `ToolAccess` and `agents/entitlements.py::tool_access_for`, with
`granted_tools`' third drop; `Share` and the conversation-sharing UI; the groups, entitlements,
document-label and tool-label pages; the remaining audit actions wired to their write sites;
the route matrix extended with the owner principal made meaningful and IA-2's seven routes
(`chat-conversation-share`, `chat-tool-entitlements`, `rag-document-labels`, and the four under
`/identity/`).

**Added by the 2026-08-30 owner directive (§22.31), and scoped into this same half** because it
is the same sentence of the owner's ("entitlements need to cover documents, what's created, and
which models/tools can be used") and because splitting it would ship a phase in which a labelled
tool is still reachable from its own page: **model sets** (§6.10 — `ModelSet`, `ModelSetMember`,
`ModelSetEntitlement`) and their migration; `models/registry/access.py::model_access_for` and the
`ModelAccess` value; the required `access` argument on `picker_options` and on
`resolve_connection`/`resolve_connection_named`, threaded from all three pickers and every
submission path including **all four** `resolve_chat` callers; `preflight_turn`'s new
`MODEL_NOT_PERMITTED` reason and `start_turn`'s 403 for it; the direct-surface tool gates on
**`vision-generate` and `rag-document-upload`** and their render-vs-gates (§9.3); the sets page and
the console's membership control (`inference-model-sets`, `inference-model-set-edit`,
`inference-connection-sets`); **agent and flow labels** (§6.11) filtered through
`visible_agents`/`visible_flows`, their `/chat/access/` page, `AGENT_NOT_PERMITTED`, and the
delegation and picker knock-ons (§9.6); the **admin-ergonomics** surfaces (§15) — bulk document
labelling, the entitlement-reach panel and the per-user effective-access panel; the eleven new
audit actions; and the two new cascades. This makes IA-2's routes **eleven** and its migrations
**five**.

**Done when:**

1. Two accounts, two entitlements, one group: a document labelled with E1 is retrievable by a
   holder of E1 through the search page, the Ask page and the `rag.search` and `rag.ask` tools,
   and is invisible on all four to a holder of E2 — verified in a browser, not only in tests.
2. Removing the last label from that document makes it follow the library posture, in both
   `open` and `locked`, without a re-encode and within one request.
3. A labelled tool disappears from an agent's prompt for a user without the entitlement, proven
   at both the planner and the loop, and the agent cannot reach it by delegating.
4. A conversation shared at `view` is readable and not postable; at `use` it is both; unsharing
   removes both.
5. An entitlement owner can grant, revoke and label within their entitlement and can do nothing
   else — one negative test per non-capability.
6. Deleting an entitlement removes its grants and labels, re-stamps every affected document's
   chunks, and the delete confirmation named the count first.
7. The full route matrix — all three postures, all four principals, both settings of
   `admin_sees_content` — green.
8. Both posture sweeps (`FARABUNKER_TEST_POSTURE=personal|enterprise`) green in both feature
   states.
9. The ladder to fresh pixels.

**Added by the 2026-08-30 owner directive.** The nine above are unchanged, word for word; these
three are appended rather than folded in, so a reader can see which criteria the phase was
originally accepted against:

10. A model connection in a **restricted set** is **invisible in all three pickers** — the chat
    per-turn picker, the vision page picker and the Ask page picker — to a signed-in account
    holding no entitlement attached to any of its sets, and visible to one that holds any; a
    non-holder who posts its pk anyway is refused on **every** submission path (chat turn, vision
    generate, Ask), and an **agent turn** naming it is refused at preflight with a 403 before any
    turn row is written — verified in a browser, not only in tests.
11. **The set indirection does what it was asked for**, proved by two one-action changes in the
    browser: adding one model to an existing set makes it reachable by **every** entitlement
    already attached to that set, and attaching one entitlement to that set makes **every** model
    in it reachable — neither requiring a per-entitlement edit. A connection in **no** set is
    usable by every holder of the relevant capability, a set with no entitlement attached
    restricts nothing, and the **AND-composition** holds: holding the image-generation tool
    entitlement without a set entitlement does not reach the model, and holding a set entitlement
    without the tool entitlement does not reach image generation.
12. `vision-generate` is **refused with honest 403 copy** for a signed-in account holding none of
    the `vision.generate` tool label's entitlements, and the create page **does not render the
    generation form** to that same account — the pair asserted together. Putting the connection
    bound to `rag.embed` into a restricted set breaks **nothing**: ingestion, retrieval, re-encode
    and `manage.py ingest` all still run, because the role path is exempt (§9.5).

**Added by the 2026-08-30 second directive and its supplement:**

13. A **labelled agent** is absent from the `/chat/` picker and from the "Add the default X"
    offers for an account holding none of its entitlements, and present for one that holds any —
    **including when the agent is `resident=True`**. A conversation that account already started
    with that agent **still opens and still renders**, and a **new turn** into it is refused with
    honest 403 copy before any turn row is written. An agent that delegates cannot reach it. The
    same, for a **labelled flow**, in `flow.run`'s per-turn choices.
14. `rag-document-upload` is **refused with honest 403 copy** for an account holding none of the
    `rag.ingest` tool label's entitlements, **nothing reaches the inbox directory**, and the
    library page does not render the upload form to that account — while the library listing,
    the search box and every document that account may read still render.
15. **One action, not many** (§22.34), in the browser: an administrator selects several documents
    in the library and labels them in **one** submit; labels **every document in a category** in
    one more; opens the entitlement page and sees its **full reach** in one place — grants,
    documents, tools, model sets, agents and flows — with the same counts the delete confirmation
    would name; and opens an account on the users page and sees its **effective access** —
    each entitlement marked direct or via which group, and what each unlocks.

### 18.3 ADR 0016

Written **after** IA-2 merges, describing what was built rather than what was planned — the
house sequence (`P4` in the agents phase did the same). Shape per the existing ADRs:
`# ADR 0016 — <title>`, `**Status:** Accepted`, `**Date:**`, `## Context`, `## Decision` split
into numbered `### N. <assertive claim>` subsections, `## Consequences`, a named-gaps section,
and `## See also`.

---

## 19. Documentation

| Document | Change | Phase |
|---|---|---|
| `docs/adr/0016-<slug>.md` | new, written last | after IA-2 |
| `docs/adr/0015-agent-layer-and-tool-contract.md` | an amendment at its foot: §9's "`tool_keys` disappears" is corrected (the argument survives; its meaning changes to a declaration, §9.1); §10's `ACCOUNTS_REQUIRED` branch point is corrected (the setting is deleted, not flipped, §3.2); §10's "`/chat/` runs turns as the chosen agent's own principal" is reversed by the acting rule, and `visible_flows`' 2026-08-28 delegate ruling with it (§5.3); G13's item 1 is marked **partially** closed — real principals, grants in their own table and `/inference/`'s mutation surface all land, but the two builder UIs and the grantable-mutating-tool gate that item also names do **not** (§9.1, §20.12), and they stay open in G12 rather than being quietly counted as done | IA-2 |
| `docs/adr/0010-model-management-framework.md` | an amendment: the standing unauthenticated-mutation gap on `/inference/` is closed; the mutating-tool grantability gate is **not** lifted here and stays open | IA-2 |
| `docs/adr/0013-inference-execution-queue.md` | an amendment: job payloads now carry the acting principal, and the queue page filters on it (§7.6) — a new obligation on every enqueuing surface, recorded on the queue's own side as ADR 0013 precedent requires | IA-1 |
| `docs/ROADMAP.md` | the Identity & Auth bullet rewritten as done in two halves; the MCP-edge and Tenancy bullets restated against what actually landed (tenancy's "visibility scopes on documents and categories" is **documents** here; categories stay taxonomy, §11.3) | IA-2 |
| `docs/ARCHITECTURE.md` | five columns, not four; an Identity row in the core-services table; the postures named beside the deployment postures they are not (a naming collision worth one sentence) | IA-1 |
| `docs/DEV.md` | §7/§8: `testpaths` gains `identity`; the reversed-order command gains it; the posture sweep added with its "before merging identity work" status (§16.3); the restart rule extended to `identity/` | both |
| `docs/OPERATIONS.md` | **backups are sensitive**: a dump now contains password hashes, session keys, entitlement grants, document labels and the audit log. It must be treated as a credential store — encrypted at rest, never mailed, never committed. Plus: the orphan `auth_user` tables and why they are left; the deactivation-kills-sessions mechanism; and the adoption ordering (§10.4) | IA-1 |
| `README.md` | one paragraph: the box runs open by default and can be switched to accounts | IA-1 |
| `identity/README.md` | new: the column, its import law, the postures, and the access questions | IA-1 |
| `agents/README.md`, `agents/contracts/README.md`, `agents/runtime/README.md`, `agents/chat/README.md` | the acting rule, `ToolAccess`, `agent_slug`, the moved `Principal` | IA-1 / IA-2 |
| `tools/rag/README.md` | labels, the library posture, the one filter point, and the chunk-metadata cache | IA-2 |
| `models/README.md`, `foundation/README.md` | the amended rule 2 seam list | IA-1 |
| `models/README.md` | a second edit: **model sets** (`ModelSet`/`ModelSetMember`/`ModelSetEntitlement`), `models/registry/access.py::model_access_for`, the `access` argument on `picker_options`/`resolve_connection*`, and the role-path exemption stated in full — it is the one rule a reader of this column will otherwise assume the other way round | IA-2 |
| `tools/vision/README.md` | the direct-surface tool gate on `vision-generate` and the create page's matching render gate | IA-2 |
| `agents/README.md` | a second edit: `AgentEntitlement`/`FlowEntitlement`, the label clause inside `visible_agents`/`visible_flows` and **that it composes with the `resident=True` carve-out rather than being bypassed by it**, `AGENT_NOT_PERMITTED`, and the delegation knock-on | IA-2 |
| `tools/rag/README.md` | a second edit: the upload door's `rag.ingest` gate, and **bulk labelling** — including that a category bulk-apply stores no rule (§22.34) | IA-2 |
| `identity/README.md` | a second edit: the entitlement-reach panel and the per-user effective-access panel, and that both are read-only views over data the column already owns | IA-2 |

`foundation/ops/tests/test_docs_sync.py` already polices some of this; check what it asserts
before writing.

---

## 20. Non-goals

Named so they read as scoped-out rather than forgotten.

1. **AND-matching on document labels.** OR only. The answer to "must hold both" is a more
   specific entitlement — one row, visible on a page, instead of a filter algebra.
2. **Django's `Permission` model and model-level permissions.** Owner decision 16. It would be a
   second grant mechanism with its own admin UI and its own semantics.
3. **Postgres row-level security.** Rejected: a second enforcement mechanism, invisible from the
   application, that would have to agree with §7's functions forever.
4. **A per-object permission framework** (third-party or home-grown). `Share` plus the owner
   columns is the whole model.
5. **Feature gating or license checks in code.** Owner decision 5. Commercial terms live in
   `LICENSE` and `docs/BUSINESS.md`. There is no license key, no key UI, and no code path that
   asks whether a feature is paid for.
6. **Local MFA.** An identity provider's job; the SSO phase is where it arrives.
7. **Email, of any kind.** No SMTP configuration, no password-reset mail, no invitations, no
   notifications. A password reset is `manage.py changepassword`, and the login page says so.
8. **A two-tier admin, or a break-glass "account" concept.** Owner decision 14: any number of
   superusers, and break-glass is "a local superuser exists".
9. **Anonymous read access to the library.** There is no posture in which an unauthenticated
   visitor reads a document. `/setup/` is the only public page and it names no row.
10. **Multi-tenancy.** One box is one organisation.
11. **Per-user model bindings, quotas, or rate limits.** The queue's admission is global and
    stays global.
12. **Lifting the mutating-tool grantability gate.** ADR 0010's rule stays in force through
    IA-2 (§9.1).
13. **Migrating existing `ToolInvocation` rows' principal kinds.** The historical kinds stay in
    the vocabulary (§5.1) and the rows stay as written.

---

## 21. Deferred, with the hook each relies on

| Item | Phase | The hook it lands on |
|---|---|---|
| **SSO / OIDC**, claim mapping, and reconciliation | IA-4 | `EntitlementGrant.source` (`manual`/`sso`, §6.4); `identity/request.py::principal_for_request` as the one request→principal seam; an `SsoGroupMapping` table added then, not now |
| **Service-account tokens** and the `ServiceAccount` table | IA-3, with the MCP edge | the `"service"` principal kind and the single `SERVICE_PRINCIPAL` constant (§5.1); `ROUTE_RULES`' tier vocabulary. **The table itself is deferred, not just its issuance**: it would have no reader and no writer in IA-1 or IA-2, and its shape (hash algorithm, scope vocabulary, rotation) should be decided against a real authenticator rather than guessed |
| **MCP edge** (`tools/list`, `tools/call`) | IA-3 | `granted_tools` + `ToolAccess`; `ToolInvocation` already being its own table; `mcp_tool_dict` already written |
| **"Keep me signed in"** | later | `IdentitySettings.session_idle_minutes` and the one `set_expiry` call (§14) |
| **Audit CSV export and retention** | later | `AuditEvent` and its closed action vocabulary (§13.2); the `at` index |
| **SCIM push** | later | the same `source` column as SSO |
| **Group managers** (a role on group membership) | later | `auth.Group` unchanged; the role would be a through-model, which is why membership is not customised now |
| **Login lockout / throttling** | later | `identity.login_failed` audit events are the signal (§13.2) |
| **Per-inbox default labels** for the watcher | later | `DocumentEntitlement`; the watcher's service principal (§5.3, item 8). **NAMED GAP, restated 2026-08-30:** a document the watcher ingests arrives **unlabelled** and follows the library posture until somebody labels it. The **mitigation is bulk labelling** (§15): an administrator selects what arrived, or a whole category, and labels it in one action. A stored category→entitlement rule was considered and **is not wanted** (§22.34) — it would be a second, invisible labelling authority beside `DocumentEntitlement`, applying itself to rows nobody reviewed |
| **An ownership-reassignment page** | later | `manage.py reassign_owner` and the owned-rows registry (§10.1, §10.5) |
| **Sharing UI for agents, flows and generated images** | later | the generic `Share` table, which already carries all four target types (§6.8) |
| **Time-limited or public shares** | **not planned** | recorded so nobody builds it by accident: a link that works without a session is a hole in the posture model, and an expiry column implies a sweeper |
| **Per-user data export** | later | the owned-rows registry names every table a person owns |
| **Per-user rate limiting** | later | `ToolInvocation`'s principal columns are the counter's source |

---

## 22. Decisions the author made

Everything the dossier left open, decided and recorded. **Thirty-five.** Two (§22.23, §22.32)
carry a **flagged for owner** marker — the two places where this spec and something the owner
said do not sit flush. Four (§22.29, §22.31, §22.33, §22.34) are not author decisions at all:
they are **owner decisions**, taken after the first draft, recorded here in the numbered list
rather than as appended addenda, which is this document's own way of absorbing one.

1. **`IdentitySettings.posture` is the only truth; `ACCOUNTS_REQUIRED` is deleted.** §3.2.
2. **The posture is read per request by `get_solo()`, with no cache**, because a cached security
   posture has a staleness window and that window is the interval in which the box is wrong.
   §3.2.
3. **"An open box never runs a permission query" is defined as: no query against the five
   identity/permission tables**, and the one `IdentitySettings` primary-key read is exempt
   because it is the query that answers *which posture*. §3.4, pinned by a query-count test.
4. **`api_client` is folded into `service`, and there is exactly ONE service principal
   constant** (`SERVICE_PRINCIPAL = Principal("service", "local")`) rather than one per shell
   entry point — one kind for machine callers, no stored data to preserve, and no way for "the
   shell" to become four subjects a grant could be written against. Which command acted is
   recorded in the audit row's `source` and `detail`, where it is information rather than a join
   key. §5.1.
5. **`resident_agent` and `user_agent` stay in `PRINCIPAL_KINDS` as historical values**, because
   `ToolInvocation` rows carry them and a closed vocabulary that cannot describe its own stored
   data lies. Nothing constructs them after IA-1. §5.1.
6. **`ANONYMOUS` is a sentinel, not a principal kind**, so "nobody" can never own a row or
   appear in a grants join. §5.2.
7. **Anonymous XHR gets `401` + JSON; anonymous HTML gets `302` + `?next=`.** §5.2.
8. **`owner_key` for a user is the primary key as a string, never the username**, so a rename
   cannot orphan rows. §5.1.
9. **`ToolContext` gains `agent_slug`, and `ToolInvocation` gains a column for it**, so the
   acting rule does not lose the fact that an agent made the call. §5.3.
10. **`agents/runtime/bindings.py::principal_for` is deleted** — the acting rule leaves it no
    callers, and the `Principal`-constructor guard shrinks to two files. §5.3.
11. **The actor travels in the job payload** (`actor_kind`/`actor_key`), minted and read by two
    functions in the one file allowed to construct a `Principal`. §5.3.
12. **`ServiceAccount` is deferred entirely, table included** — no reader, no writer, and a
    shape better decided against a real authenticator. §21.
13. **`AuditEvent` carries no foreign key to `User`**, only denormalised strings, so no cascade
    can delete an audit row and `identity/0001` needs no swappable dependency. §6.5.
14. **Users are `AbstractUser` with no extra fields**, created now purely because the model
    cannot be swapped later. §6.1.
15. **The admin-log foreign key is repointed by a catalogue-driven `RunSQL` with an
    emptiness precondition, and `auth_user` is left in place as an orphan.** §6.1.
16. **`identity/admin.py` re-registers the user model with a `UserAdmin` SUBCLASS**, because
    Django silently drops the swapped-out registration and `/admin/` would otherwise lose users
    — and because the stock class would bypass the last-admin guard (its `save_model` calls
    `obj.save()`) and expose the `Permission` catalogue non-goal 2 rules out. The subclass
    delegates `save_model` to `identity/services.py` and drops `user_permissions`. §6.1.
17. **`Document` gets no owner column**; labels and the library posture are its whole access
    story. §6.9.
18. **`InferenceJob` gets no owner column either**: the actor rides in the payload and the queue
    page filters on it, so the queue's frozen `enqueue` seam is untouched and there is no
    migration. A queue row is operational, so `visible_jobs` answers to `is_admin` and an
    administrator sees every row and cancels any job in every posture; a payload-less job is
    visible to `is_admin` and to nobody else — fail closed for members. The payload and answer
    text are content and go through `may_read_job_content`. §7.6.
19. **The library posture is enforced at one point per surface, and `sees_nothing` is an early
    return**, because an empty `MetadataFilters` means "everything". §8.3.
20. **`entitlements` is stamped as a list of decimal strings and unlabelled chunks carry no key
    at all**, which makes the store's `ANY` and `IS_EMPTY` operators exactly right and means
    every chunk already in every existing store needs no back-fill. §8.3, §8.4.
21. **One writer for the chunk cache**, called at ingest and on every label change, rather than
    node metadata at ingest plus SQL on change — two writers would produce two shapes of one
    fact. §8.4.
22. **The label-change re-stamp raises and rolls back; the ingest-time one logs.** A label that
    appears saved and is not enforced is worse than one that refuses to save. §8.4.
23. **`Agent.tool_keys` survives as a declaration**, so `granted_tools` keeps its `tool_keys`
    argument and ADR 0015 §9 is amended rather than cited. §9.1, §9.3.
    **Flagged for owner.** This overrides the dossier's own words: "ToolEntitlement … replaces
    `Agent.tool_keys` as grant TRUTH (agents still declare what they want; ADR 0015 §9's
    `tool_keys` argument to `granted_tools` disappears)". Its two halves cannot both hold. If an
    agent still declares what it wants, that declaration has to reach the one function that
    decides availability, and `tool_keys` is the argument it reaches it through — removing the
    argument would mean either the declaration stops being consulted (so every user's full
    entitlement set is offered to every agent, and the librarian agent gets the image tools) or
    `granted_tools` reads the `Agent` row itself, which would make a Django-free rule-1 leaf
    query the database. The first half is the one worth keeping, so the argument stays and its
    MEANING changes from grant to declaration. The dossier's second half is recorded here as
    overridden rather than silently dropped.
24. **`granted_tools` learns entitlements through a pure `ToolAccess` value defaulting to
    unrestricted**, keeping the leaf Django-free and every existing call site and test correct.
    §9.2.
25. **The owned-rows registry** (`register_owned_rows` from each `AppConfig.ready()`) is how one
    command walks five tables in three columns without identity importing any of them. §10.1.
26. **404, not 403, on row-addressed routes**; 403 only on admin surfaces, whose existence is
    not a secret. §11.1.
27. **A default-deny middleware with a derived, test-enforced route table**, so a new route ships
    closed and the suite names it. §11.2, §16.2.
28. **Posture is a per-test pin by default, with a defined full-suite sweep read only by test
    helpers**, rather than a twelve-run per-change gate or a production environment variable
    that would reintroduce the second truth. §16.3.
29. **OWNER DECISION, 2026-08-29 — administering is separated from reading content.** Recorded
    here rather than as an author decision, in the slot the round-1 ruling it replaces used to
    hold. **Administer** is always `is_admin`, in every posture: manage users, groups and
    entitlements; label, delete and re-ingest documents; cancel or requeue **any** job; change
    the posture. It needs to see ROWS — a job's kind, owner, state and progress; a document's
    title, labels and status — and never their contents. **Read content** is gated by one new
    boolean, `IdentitySettings.admin_sees_content`, **default False** on least-privilege
    grounds, edited on the posture page, audited like the posture, and effective without a
    restart. `sees_all_content` keeps its name and becomes `open -> True; else is_admin and
    admin_sees_content`, with **no posture branch anywhere in a visibility function**:
    `personal` and `enterprise` behave identically, and the postures differ only in which pages
    exist. §3.1, §6.2, §7.1, §7.6, §8.2, §11.1. It resolves §23's first concern and costs
    nothing operationally — an administrator still runs the box, cancels every job, and labels
    every document, including documents they cannot read.
30. **`set_posture` refuses a switch away from `open`** while there is no active superuser, while
    `DEBUG` is true, or while `SECRET_KEY` is still the shipped default — the run-time half of
    two startup checks that would otherwise only fire on the next restart, i.e. after the window
    in which the operator already believes accounts are on. §14.

31. **OWNER DECISION, 2026-08-30 — entitlements cover TOOLS at their own surfaces, and specific
    MODELS.** In the owner's words: *"entitlements should also limit what tools they have access
    to. I.e an entitlement may be necessary to use image generation or to use certain models…
    certain users may be limited in what they can do rather than give everyone keys to the
    kingdom… Entitlements need to cover documents, what's created, and which models/tools can be
    used, it may be use all image generation or all chat models, or it may be selective access…
    limit access to only what a user needs… everyone shouldn't have access to a model that is
    being tested and who only a limited number need access to."*

    Two things follow, and both land in IA-2 (§18.2). **First**, a tool entitlement binds its
    tool's **own page**, not only the agent-runtime seams §9.3 lists — `vision-generate` is the
    one such page today, it consults `tool_access_for` in the view, and the create page
    render-gates its form on the same predicate (§9.3). **Second**, a **model connection** is a
    labellable thing (§6.10), and using one requires the capability **and** the model:
    `allows(T) AND (M unlabelled OR principal holds a label on M)` (§9.5). An unlabelled model
    stays open to every holder of the capability, which is the owner's "use all image
    generation"; a labelled one is the owner's "a model that is being tested".

    Enforced at three seams that are really two functions — `picker_options` (what is offered)
    and `resolve_connection_named` (what is accepted) — plus `resolve_chat`'s four callers for the
    agent path, so a refusal lands **before** a turn is written rather than mid-run. §9.5 has the
    mechanics; §17 gains migration 8; §18.2 gains done-when 10–12.

    **Superseded in part by §22.33 the same day:** the *label unit* is a model **set**, not a
    connection. Everything else in this item stands.

32. **FLAGGED FOR OWNER — box-level service bindings are exempt from model entitlements.**
    A model resolved through a **role** — `rag.embed`, `rag.transcribe`, `rag.extract`, and the
    default `chat.converse`/`vision.generate` bindings — is never gated; only a **user-selected**
    connection and an **agent path's picked** connection are (§9.5).

    The reason is that the alternative is worse in a way the directive cannot have meant: a label
    on the embedding connection would stop ingestion, retrieval and re-encode for **everybody**
    on the box, including the folder watcher and every management command, none of which has a
    principal to hold a grant. "Limit access to only what a user needs" is about what a user
    **chooses**; the box's own wiring is not a choice anybody makes per request, and an operator
    who wants a model unreachable unbinds it or removes it in the console.

    Recorded as flagged because it is the one resolution path this directive deliberately does
    not reach, and the owner should see that stated rather than infer it from an absence.

33. **OWNER DECISION, 2026-08-30 (second) — model labels become model SETS; agents and flows
    become restrictable; the library's own door joins the vision one.** In the owner's words:
    *"it may be a pain to reassign every entitlement for a given model… group models into sets
    that can be assigned to multiple entitlements, rather than models being assigned by
    entitlement. So one change can impact many different entitlements/users."*

    The label unit moves up a level: a connection joins a **set**, an entitlement attaches to a
    **set**, and the two edges are edited independently (§6.10). One membership row reaches every
    entitlement already attached; one attachment reaches every member already in. **Only the unit
    changed** — the AND-composition, the one choke point, the 403 and the role-path exemption are
    what §22.31 made them.

    The same directive folds in the two gaps this spec had named and not closed: **agents and
    flows** carry direct labels, filtered through the `visible_agents`/`visible_flows` seams that
    have existed since IA-1 (§6.11, §9.6), and **`rag-document-upload`** gets the `rag.ingest`
    gate that mirrors the vision door (§9.3).

    **Sets for models, direct labels for agents and flows — and the asymmetry is the decision,
    not an oversight.** A set earns its indirection where the membership churns and the
    entitlements do not: models are swapped, tested and retired constantly, and the owner's
    complaint was precisely about re-editing entitlements each time. Agents and flows are the
    opposite shape — a handful of long-lived rows an operator names once — so a set layer there
    would be a table, a page and a join bought to save an edit nobody makes. If agent churn ever
    looks like model churn, `AgentEntitlement` is one migration away from becoming
    `AgentSetEntitlement`, and that is a better trade than shipping the machinery now.

34. **OWNER DECISION, 2026-08-30 (supplement) — a routine administrative change is ONE action.**
    In the owner's words: *"good control over the system… easy to set up now and easy to maintain
    for admins. A tedious maintenance process for changes over time is a failure."*

    Recorded as a decision, not as three features, because it is the rule the three features
    happen to satisfy and it binds every phase after this one: **if a change an administrator
    makes routinely takes N actions, the design is wrong.** Its three consequences here are
    bulk document labelling (multi-select, and label-a-whole-category), the entitlement page
    showing its **full reach** in one place, and the users page showing an account's **effective
    access** (§15). The two read-only panels are as much of this decision as the write path is:
    an operator who cannot see the state does not control it.

    **A category bulk-apply is an ACTION, not a stored rule** — the owner's own ruling. A stored
    category→entitlement mapping would be a second labelling authority beside
    `DocumentEntitlement`, applying itself to documents nobody reviewed and answering a question
    (`may X read this?`) in two places. The bulk apply writes ordinary label rows and then
    forgets it ran; a document added to that category afterwards is unlabelled, and §16.4 asserts
    exactly that.

    **The reach panel is sourced from the delete confirmation's own counter**
    (`entitlement_delete_counts`, §12.1's cascade registry read in count mode), so what the page
    *shows* and what the delete *warns* cannot disagree. Two functions answering "what does this
    entitlement touch" is how a page comes to reassure somebody about a delete that then removes
    something else.

35. **A `mutates=True` tool may be LABELLED even though it may not be GRANTED.** ADR 0010's rule
    — a settings-mutating tool is registered but not grantable — governs what an **agent** may be
    handed, and `granted_tools` still drops such a key unconditionally. A label runs the other
    way: it can only ever **narrow** which *people* reach that tool's own page, and it hands
    nothing to anybody. Without this, `rag.ingest` — the key behind the library's upload door —
    could not be labelled at all, and §9.3's upload gate would have nothing to consult. The
    tool-label page therefore lists mutating tools, marked page-only, and §16.4 pins that
    labelling `rag.ingest` leaves it exactly as uncallable by every agent as it was.

Smaller calls recorded in place rather than listed again: Django's `changepassword` instead of a
new command (§11.4); no session sweep on deactivation because `ModelBackend.get_user` already
does it (§12.2); `select_for_update` around the last-admin guard (§12.3); `SECURE_COOKIES` as a
deployment fact rather than a posture fact (§14); the document-label page in `tools/rag` and the
tool-label page in `agents/chat`, because identity may not import either column (§4.2, §15); no
Django signals, because this repository uses none (§6.8); and one share UI in IA-2, for
conversations only (§10.3).

---

## 23. Author concerns

Two open, one resolved in review. Each is implemented **as the dossier decided**; each is
recorded here with its evidence.

**Concern 1 — RESOLVED by an owner decision (§22.29), and kept here as a record rather than
deleted.** As drafted, this concern read: "every account may be admin" makes `personal` a weaker
posture than its name suggests, because `is_superuser` gated both the admin surfaces *and* the
"admins see all" branch in every visibility function — so `personal`'s private-conversation
boundary was enforced against people who could remove it in two clicks. A first review ruling
keyed that branch on the posture, which fixed the privacy and cost the operator the ability to
cancel another account's stuck job. The owner replaced it with the right axis: **administering
is not reading.** `is_admin` keeps every administrative capability in every posture — cancelling
any job and labelling any document included — and one setting,
`IdentitySettings.admin_sees_content` (default off), decides whether an administrator may read
other people's content. Both dossier lines are true, the operational cost is gone, and what
remains is a deliberate switch on a page with an audit row behind it.

**Concern 2 — the acting rule reverses a recorded ADR 0015 ruling and splits the audit table's
principal vocabulary at a time boundary.** `agents/visibility.py::visible_flows`'s own docstring
records the 2026-08-28 ruling that it is asked with the *agent's* principal and that "a delegate
sees the flows IT may run, not the flows its caller may", and
`agents/runtime/bindings.py::principal_for` is the function that implements it. Owner decision
13 reverses both. The consequence is that `Principal("resident_agent"|"user_agent", slug)` stops
being minted, so every `ToolInvocation` row on a live box carries a kind that no new row will —
one column, two vocabularies, either side of one deploy. Implemented as written, with three
mitigations named in the spec: the historical kinds stay in `PRINCIPAL_KINDS` (§5.1), the agent
identity is preserved in a new `agent_slug` column rather than lost (§5.3), and the boundary is
recorded in ADR 0016 and in the ADR 0015 amendment (§19). The reversal is right — an agent must
not be a way around labels — but it is a reversal, not a refinement, and reading the two
documents side by side without this note would look like a contradiction.

**Concern 3 — a labelled tool becomes permanently uncallable from the watcher and the CLI, with
no grant path.** The dossier's `EntitlementGrant` constrains a grant to a user XOR a group, and
`ServiceAccount` is deferred. A service principal therefore holds no entitlements and, under
§9.1's intersection, gets unlabelled tools only — forever, until service-account tokens land in
IA-3. Concretely: labelling `rag.search` and then running `manage.py agent_turn` produces a turn
whose agent silently has no search tool, and the honest failure appears as a model that cannot
find anything rather than as a refusal. Implemented as written: the tool-label form warns when
the key being labelled is one a shell path uses, and the tool-label page states the rule. The
narrow fix, if it is ever wanted before IA-3, is a third nullable column on the grant row
(`service_key`) and a third partial unique — but that is a table change the dossier did not
authorise and this spec does not make.
