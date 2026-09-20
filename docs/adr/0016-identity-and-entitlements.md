# ADR 0016 — Identity, entitlements and sharing: one base column, four labelled axes

**Status:** Accepted
**Date:** 2026-09-01

## Context

Before this build the platform had four columns (ADR 0015 §1) and no
accounts. Every request on the box ran as one shared principal —
`Principal("open", "box")`, declared in `agents/contracts/tools.py` and
resolved by `agents/chat/principal.py::principal_for_request` — and ADR
0015 §10 recorded that posture honestly: *"accounts are off, and that is
a posture with a name rather than an absence."* Ownership columns
existed on three tables, the visibility functions that read them
returned everything, `settings.ACCOUNTS_REQUIRED`'s `True` branch raised
`NotImplementedError`, and `/inference/`'s mutation surface was
reachable by anybody who could reach the box at all. ADR 0015's G13
named the order of what came next, and Identity & Auth was item 1.

The owner asked for two things, in two halves. **IA-1**: real accounts,
a login, three security postures, an audit trail, and the acting rule
that says a turn runs as the person who asked for it. **IA-2**: the
machinery that makes "signed in" finer than "signed in" — entitlements
granted to accounts and groups, labels on documents, tools, models,
agents and flows, and sharing of a conversation with a named colleague.

Three facts already in the tree constrained the whole design:

1. **A box runs three processes from one image.** `compose.yaml` starts
   `web`, `worker` and `watcher` with independently supplied
   environments, and the worker is where turns actually run tools. A
   security posture carried in the environment could therefore be set on
   one process and unset on another.
2. **The open box is the shipped default and must stay free.** A
   household box with no accounts must behave exactly as it did before
   this phase — not approximately, and not at the cost of a permission
   query per page.
3. **The import law is three rules** (ADR 0015 §1), and the column that
   owns identity is imported by every other column. Whatever shape it
   took, it could not become a hub that knows about documents, jobs and
   conversations.

This ADR records what IA-1 and IA-2 built, and is written after both
merged. It is a record, not a plan: every claim below is checkable
against the tree it describes. Its argument lives in
`docs/superpowers/specs/2026-08-29-identity-and-auth-design.md`, which
is cited where a decision's reasoning is there rather than here; where
the spec and the code differ, the code is what this ADR records.

## Decision

### 1. `identity/` is the fifth column, and it is a BASE rather than a hub

`identity/` joins `tools/`, `models/`, `agents/` and `foundation/`. It
owns three IA-1 tables (`User`, `IdentitySettings`, `AuditEvent`) and
two IA-2 ones (`Entitlement`, `EntitlementGrant`), the request→principal
point, the gate middleware, the route table, the guarded writes, and the
audit writer (`identity/__init__.py` is the column's own inventory).

**The import law gains a fourth rule** (spec §4.2), amending ADR 0015
§1's three rather than replacing them:

> **Rule 4 — `identity/` imports nothing from `agents/`, `tools/` or
> `models/`.** It may import `foundation`, Django, and its own
> `contracts`.

That single rule is what makes this column a base. Its consequence is
load-bearing and is stated here rather than discovered later:
**identity cannot answer "which documents".** It has no way to build a
`Document` queryset, so what it answers is *which principal* —
`is_admin`, `sees_all_content`, `held_entitlement_ids`, `owner_fields` —
and every feature column turns that answer into a queryset of its own
rows. A hub would know about documents, jobs and conversations; a base
knows about principals and postures only.

**Rule 2 gains a closed set of four named seams**, the same shape
`models.registry.bindings` already had: `identity.contracts.*`,
`identity.access`, `identity.request` and `identity.audit` are importable
by every column; `identity.models`, `.views`, `.services`, `.forms`,
`.middleware` and everything else are not. It is enforced as an
**allowlist**, not a blocklist —
`foundation/ops/tests/test_import_law.py::IDENTITY_PERMITTED` — so a
private module added later is closed by construction rather than by
somebody remembering to add it to a list. `identity/testing.py` is
deliberately absent from the allowlist and reachable by every column's
test files, which the gate exempts entirely: that is the mechanism, not
a carve-out.

**`Principal` moved here, verbatim, and only two files may construct
one.** `identity/contracts/principals.py` is the rule-1 pure leaf that
now holds it — no Django, no database, no I/O, pinned by
`identity/tests/test_purity.py`, a subprocess with no settings module
configured at all. It and `identity/request.py` are the only two
PRODUCTION files permitted to write `Principal(...)`, enforced by an AST
guard (`foundation/ops/tests/test_import_law.py`'s
`_PRINCIPAL_CONSTRUCTORS`, whose third and last entry is
`identity/testing.py` — test support, closed to production code by the
same mechanism that keeps it out of `IDENTITY_PERMITTED`). A principal's
kind is the field a grants
table joins on, so a third place minting one would be a third opinion
about who is acting. `PRINCIPAL_KINDS` keeps the two historical
agent-minted kinds (`resident_agent`, `user_agent`) because
`ToolInvocation` rows written before the acting rule carry them, and a
closed vocabulary that cannot describe its own stored data is a
vocabulary that lies.

**`ANONYMOUS` is a separate type, not a fifth kind.**
`identity.contracts.principals._Anonymous` carries `.kind`/`.key` so the
access functions read it with the same two attribute lookups, but
`"anonymous"` is not in `PRINCIPAL_KINDS` — so `Principal("anonymous",
…)` raises, and the value is structurally incapable of reaching an owner
column or a grants join. Making it a kind would mean the first person to
write `Q(owner_kind="anonymous")` gave "nobody" rows to own.

**The user model is a custom `AbstractUser` with no extra fields**
(`identity/models.py:29`), created now precisely because
`AUTH_USER_MODEL` cannot be changed once rows reference it: adding a
field later to a model we own costs one migration, while swapping the
model later is database surgery on a box holding a document library.
Groups are Django's own `auth.Group`, unchanged — membership is not
customised, which is what leaves room for a group-manager role later
without a data migration. `is_superuser` is what "administrator of this
box" means, and **Django's `Permission` model is a named non-goal**
(owner decision 16, spec §20.2): it is model-level, it comes with its own
admin UI and its own semantics, and it would be a second grant mechanism
standing beside the entitlements IA-2 adds.

**Cross-column foreign keys are declared as strings, never imports.**
`EntitlementGrant.user` and `Share.user` declare
`settings.AUTH_USER_MODEL`; `DocumentEntitlement.entitlement`,
`ToolEntitlement.entitlement` and `ModelSetEntitlement.entitlement`
declare `"identity.Entitlement"`; group FKs declare `"auth.Group"`. No
column ever imports `identity.models`, and rule 4 survives a schema that
points at identity from three other columns.

**One standing fact is retired by this phase, and it is named rather
than left to a reader's diff.** The 2026-08-25 regroup recorded that
this repository had *no* cross-app migration dependencies and no
`swappable_dependency` anywhere — the fact that made that regroup
DB-free. It has both now: `tools/rag/migrations/0015_documententitlement.py`,
`agents/migrations/0003_toolentitlement_and_share.py` and
`models/registry/migrations/0008_modelset.py` each depend on
`identity.0003_entitlement_and_grant` and on
`swappable_dependency(settings.AUTH_USER_MODEL)`. App labels are now
load-bearing *across* columns as well as within them, which is one more
reason every `AppConfig` still sets `label` explicitly (ADR 0015 §1).

### 2. The posture is a ROW, and an open box never runs a permission query

`IdentitySettings.posture` (`identity/models.py:47`, the field itself at
`:69`) is a database
singleton with three values — `open` (the shipped default), `personal`,
`enterprise` — and it is **not** an environment variable (spec §3.2).
Three processes with independently supplied environments would make an
environment-carried posture enforceable on one and not another; the
database is the one thing all three provably share. A row is also
*validatable*: `identity.services.set_posture` refuses to leave `open`
unless an active superuser exists, `DEBUG` is off, and `SECRET_KEY` is a
real value. An environment variable cannot be refused.

**`settings.ACCOUNTS_REQUIRED` was deleted, not flipped.** Its `True`
branch never worked, so there was no behaviour to preserve by flipping
it — only a documented non-feature to remove, and a second answer to
"what posture is this box in" to foreclose.
`foundation/ops/tests/test_no_accounts_required.py` is a grep gate
pinning that the name appears nowhere left to flip. (Recorded as an
amendment on ADR 0015 §10, whose text named that setting as the branch
point.)

**Every access function tests `accounts_on()` FIRST**
(`identity/access.py:52`) and returns its open branch before touching
another table. That is how "an open box never runs a permission query"
is structural rather than promised — and the same discipline is repeated
verbatim in every column: `agents/entitlements.py::tool_access_for`,
`models/registry/access.py::model_access_for`,
`tools/rag/access.py::document_visibility`, and every body in
`agents/visibility.py`.

**It is pinned as a testable claim, not a promise.**
`identity/tests/test_zero_queries.py` drives a representative GET of
every mount in `open` posture and asserts that **eleven** tables are
named in no captured query: `identity_user`, the two entitlement/grant
tables, the five per-column entitlement and share tables
(`rag_documententitlement`, `agents_toolentitlement`, `agents_share`,
`agents_agententitlement`, `agents_flowentitlement`) and the three
model-set tables. The names come from each model's own
`_meta.db_table` rather than being typed twice, so a rename cannot make
the sweep drift from the schema. `identity-settings` is in the mount
list alongside `chat-index`, `rag-ask-page`, `rag-documents`,
`jobs-queue`, `inference-console`, `setup-index`, `identity-groups` and
(flag permitting) `vision-gallery`.

**One read is allowed, and the module says which.**
`identity_identitysettings` is read once per request by
`IdentityGateMiddleware.process_view`, which stashes the row on the
request and threads it through every `settings_row=` keyword below it —
`accounts_on`, `is_admin`, `principal_for_request`, and the context
processor the shared shell template runs. It is not a permission query:
it is the query that answers *which posture*, and the box must ask it
before it can skip anything else. There is **no cache**, deliberately: a
cache would be a second truth with a staleness window, and the staleness
window of a security posture is exactly the interval in which the box is
wrong.

**The gate is a middleware, and it runs after CSRF.**
`IdentityGateMiddleware` (`config/settings.py:258-271`) is installed
after `AuthenticationMiddleware` because it needs `request.user`, and
therefore after `CsrfViewMiddleware` too — moving it ahead of CSRF would
mean an unauthenticated cross-site POST was evaluated for
**authorisation** before it was evaluated for **forgery**, which is the
wrong order to fail in. The consequence is stated rather than
discovered: an anonymous POST with no CSRF cookie gets 403 from CSRF,
and one with a valid cookie and no session reaches the gate and gets
302/401. Both are correct refusals and neither is a 500. It uses
`process_view`, so `request.resolver_match` is already populated and the
URL name is known without re-resolving it.

**Two boot checks make a posture with accounts and a development
configuration mutually exclusive.** `identity.E001` fails the build when
`DEBUG` is on while the posture is not `open`; `identity.E002` fails it
when `SECRET_KEY` is still `DEV_SECRET_KEY`, the key published in this
repository — a box whose signing key is public has accounts in name
only, because anybody who can read the repository can mint a session for
any account on it. Two warnings sit beside them: `identity.W001`
(accounts on, cookies not marked Secure — a warning because plain HTTP
on a local network is a supported posture) and `identity.W002` (the
posture is `open` but accounts already exist, which is what a lost or
restored settings row looks like from the outside). **All four swallow
`django.db.Error` and return nothing**: a box mid-migration has no
settings row and is not in violation of anything. A check that runs only
at boot is not enough on its own — flipping a posture on a running box
would change nothing until the next restart — so `set_posture` enforces
the same three conditions at request time. The two are halves of one
rule, and `manage.py identity_posture` reaches the same function, so the
break-glass path is not a way around any of them.

### 3. ADMINISTERING is not READING, and the split has a default

`identity.access.is_admin` (`:109`) and
`identity.access.sees_all_content` (`:141`) answer two different
questions, and this column's whole content model is that a page must ask
the right one:

- **`is_admin`** — may this principal reach an ADMIN SURFACE: the model
  console, the queue settings, the users page, the posture page,
  `/admin/`. True for `OPEN_PRINCIPAL` and for an active superuser;
  false for `ANONYMOUS` and for every service principal. `OPEN_PRINCIPAL`
  is an admin because on a box with no accounts there is nobody for
  anything to be hidden from; a **service** principal never is, because a
  machine caller that could reach the model console would make the shell
  a privilege-escalation path with no login behind it.
- **`sees_all_content`** — may this principal read OTHER PEOPLE'S
  CONTENT: their conversations, Ask history, generated images, agent and
  flow bodies, document bytes, and the payload/answer text of a job they
  did not start. `open` → always true. Otherwise → `is_admin(principal)
  and admin_sees_content`.

**`IdentitySettings.admin_sees_content` defaults to `False`, and the
default is the decision.** Administering a box already means seeing every
ROW it needs to run — a job's kind, owner, state and progress; a
document's title, category and status — without reading anybody's
content. Turning content visibility on is a deliberate act on the
posture page, audited exactly like the posture itself, and read per
request so it takes effect with no restart.

**No posture branch, deliberately.** `personal` and `enterprise` answer
`sees_all_content` identically; they differ only in which pages exist. A
predicate that read the posture would mean the same administrator saw
different content on two boxes that had made the same choice — and the
choice is the setting, not the posture.

The clearest place the split does real work is the library.
`tools/rag/access.py::listable_documents` (`:127`) is `is_admin`, while
`::readable_documents` (`:109`) is the entitlement rule: an
administrator with the content setting off sees every document ROW —
title, category, labels, status — and may label, delete or re-ingest it,
while reading none of its bytes. That is the right way round.
Otherwise the first label on a sensitive document could only be applied
by somebody already cleared to read it.

**One carve-out, and it is the only one.** `owned_rows_q` (`:174`) and
its row-predicate mirror `may_read_owned_row` widen an administrator's
view to rows a SHELL PATH created (`owner_kind="service"`) whatever the
content setting says, because a row nobody is behind has nobody for
anything to be private from. That branch tests `is_admin`, not
`sees_all_content`, for the same reason: pruning what an automated path
left behind is administration.

### 4. A grant is a row on an entitlement, and OWNER is a role on that row

`identity.Entitlement` (`:178`) is a named permission — "Finance" —
case-insensitively unique, the same house convention `Agent.slug` and
`ModelConnection.name` already use, because two rows that read
identically on a grant form are two rows somebody will grant the wrong
one of. **Entitlements partition one library among the people who share
one machine.** They do not partition machines: nothing here is
multi-tenant, and a second box is a second box (spec §20.10).

`identity.EntitlementGrant` (`:216`) is who holds one. Three properties
are database constraints rather than conventions:

- **A USER XOR A GROUP**, never both and never neither — because a grant
  naming both would make "revoke this person's access" a question with
  two answers.
- **`role` is a column on the row, not a second row.** Promoting a
  member to owner is an `UPDATE`, which is why the unique constraints
  exclude it: two rows for one `(entitlement, user)` pair would make
  "does this person hold E" ambiguous, and the ambiguity would show as a
  duplicate on every grant page.
- **Two PARTIAL uniques, not one plain unique across three columns.**
  NULLs do not collide in Postgres, so a plain three-column unique would
  happily store the same group grant a thousand times.

**An OWNER of E is a member of E plus exactly three capabilities** —
grant/revoke E, label/unlabel with E, and see everything in E. The third
needs no code at all: an owner grant is a grant, so it already appears
in `held_entitlement_ids`. `identity.access.owned_entitlement_ids`
(`:318`) is the same query narrowed to `role=owner`, and `identity.services.
may_administer_entitlement` (`:371`) is the one RULE, asked in three
places. `identity.services.grant`/`revoke` call the function itself (its
only caller, through `_refuse_unless_may_administer` at `:522`);
`tools.rag.views.document_labels_update` cannot — it is column-private —
so it asks the same two facts through the sanctioned seam, `is_admin` +
`owned_entitlement_ids`, and says so at the point it does it
(`tools/rag/access.py::may_label_document`). **`agents.chat.views.tools.
tool_entitlements` no longer asks this predicate at all**: TODO(owner) —
this ADR described it asking `is_admin` + `owned_entitlement_ids` through
a "sanctioned seam" in `agents/labels.py`, but `agents/labels.py` carries
no such text today and a tool label has no entitlement owner (spec
§7.4); the route is Class `S`, already 403'd for a non-admin by
`IdentityGateMiddleware` before the view runs. Confirm whether this
ADR's "same two facts" claim ever held for tool labels, or rewrite it to
describe the route-gate mechanism the view actually relies on today. IA-2
adds no third predicate for "may administer" on the document side: it
reuses the owner role's own grant row.

**`EntitlementGrant.source` is written by nothing today, and ships
anyway.** `manual` versus `sso` is the column an identity-provider
reconciliation joins on — "revoke every grant this provider used to
assert and no longer does" is answerable only if provider-asserted
grants are distinguishable from hand-made ones, and nobody can classify
a row after the fact. Backfilling that distinction later is impossible,
which is why the column is present from day one rather than deferred
with the phase that reads it.

**One route in this column is class `R`, not `S`, and the difference is
load-bearing.** `identity-groups`, `identity-group-edit` and
`identity-entitlements` are `S` — groups are operator structure, and
creating, renaming or deleting the label itself is operator policy. But
`identity-entitlement-edit` is `R` (`identity/routes.py`), because an
entitlement **owner** reaches that same URL to grant, revoke and label
with the entitlement they own. `tier_for` maps `S` to the ADMIN tier and
the middleware refuses a non-admin there before the view resolves
anything, so classifying this one `S` would have refused a legitimate
owner at the door. `R` is AUTHENTICATED at the middleware and keeps its
real rule in the view (`identity/views.py:455`), which is the definition
of the class: admitted, then answered 404 for a row this principal has
no standing over. Rename and delete re-check `is_admin` inside
`identity.services`.

### 5. Labelling is OR within an axis and AND across axes — four axes, one shape

A label is an `Entitlement` attached to a thing, in the thing's own
column. There are four axes, and they share a shape rather than a
mechanism:

| Axis | Table | Where it is spent |
|---|---|---|
| documents | `tools.rag.DocumentEntitlement` (`tools/rag/models.py:615`) | the one retrieval filter point, and every document-serving view |
| tools | `agents.ToolEntitlement` (`agents/models.py:494`) | `granted_tools`, and the two direct UI doors |
| models | `models.registry.ModelSetEntitlement`, through a SET (`models/registry/models.py:272`) | every picker, and every pk-addressed resolution |
| agents and flows | `agents.AgentEntitlement`/`FlowEntitlement` | `visible_agents`, `installed_agent_slugs`, `visible_flows`, and turn preflight |

Two rules hold across all four:

- **OR within an axis.** Holding ANY one of a row's entitlements is
  enough. **AND-matching is a named non-goal** (spec §20.1): the answer
  to "must hold both" is a more specific entitlement — one row, visible
  on a page — rather than a filter algebra nobody can read.
- **AND across axes.** A turn that reaches a labelled tool that resolves
  a set-restricted model needs both. Nothing composes the axes into one
  value; each is asked where it is spent, which is why there are four
  small access values and not one god-object.

And one rule holds for every one of them: **unlabelled means permitted**.
A tool key absent from `ToolEntitlement` is callable by every signed-in
user; a connection in no set is usable by everyone who may use its
capability; an unlabelled agent or flow is visible exactly as it is
today; an unlabelled document follows the library posture. That is what
keeps a box that never labels anything behaving exactly as it did before
IA-2, and it is obtained by construction in each axis rather than by a
branch each one has to remember.

### 6. Documents: ONE filter point, and a chunk-metadata cache with ONE writer

**The tables are truth and the cache is a copy, and the module that owns
both says so.** `DocumentEntitlement` decides who may read a document.
The `entitlements` key in the live chunk table's `metadata_` column is a
COPY of that fact, kept because the retriever filters on chunk metadata
and has no way to express a join back to the label table. A visibility
change therefore costs **one `UPDATE`**, never a re-encode.

`tools/rag/labels.py::restamp_document_chunks` (`:97`) is the only
function that writes that key. **Two callers write it in the normal
course**: ingest (`tools/rag/ingest.py:726`), immediately after the
nodes are inserted, so a re-ingest RESTORES the labels the indexing
library's own row rewrite would otherwise drop; and every label
add/remove (`tools/rag/labels.py::set_document_labels`), inside the same
transaction as the `DocumentEntitlement` write. **Two more reach it for
cascade and repair**: `unlabel_all_for_entitlement` (`:275`, §11) and
`manage.py relabel_chunks` (`:44`).

Those callers differ in one keyword and the difference is the decision:
`raising=False` — ingest's, the default — logs and returns, because a
failed re-stamp must not fail an ingest; `raising=True` — every other
caller's — raises, so the label write rolls back with it. A label that
appears saved and is not enforced is worse than a label that refuses to
save. `manage.py relabel_chunks [--document <id>]` is the documented
repair for the logged case, and for the rarer case of an `Entitlement`
row deleted straight from a shell.

**The key is never node metadata**, which is the trap this design walks
around: the indexing library prepends metadata keys onto a node's text
before embedding unless they are excluded. `entitlements` sidesteps the
question by being written into the `metadata_` column *after* insert, so
it is never part of what gets embedded, and there is only one shape of
the fact rather than two (one inside the store's serialised node blob,
one in the filterable column) whose disagreement is invisible until
somebody debugs it.

**`tools/rag/access.py::DocumentVisibility` (`:45`) is the value that
travels.** It is built ONCE per request or per turn by
`document_visibility` (`:92`) and threaded down — never re-derived inside
a runner, which is the drift a single filter point exists to prevent.
`unrestricted` is `sees_all_content`, so an administrator with the
content setting off is filtered exactly like a member. `permits(document)`
is its in-memory mirror for a caller that already has the row and its
labels prefetched, so a listing page does not run a query per row just to
decide whether to render a link the route would 404 on.

**`tools/rag/retrieval.py::retrieve_nodes` (`:478`) takes `visibility`
as a REQUIRED, keyword-only argument, never defaulted.** That is the one
filter point ADR 0015 §9 promised a visibility scope would land at, and
it did: every caller — the Ask path, the retrieval-only search page, the
`rag.search` tool, the job handlers — builds its own `DocumentVisibility`
from whatever subject it has (a request principal, a job payload's
actor, a tool call's `ctx.principal`) and hands it in. No runner grows a
second copy of the rule.

`_visibility_filters` (`:433`) is factored out of the retriever
precisely so it can be asserted directly rather than through a live
vector store — a filter only testable end to end is a filter tested
rarely. **An unrestricted principal asking without a category builds no
filter at all**
(`tools/rag/tests/test_retrieval_visibility.py::TestTheFilterShape`);
with a category it gets that category clause and nothing else — which is
what the pre-phase code built too, so either way an open box runs the
query it ran before this phase. A holder gets an
`ANY` clause of decimal STRINGS, which is why the stamped ids are
strings; an unlabelled-permitted principal gets an `IS_EMPTY` clause,
true exactly when the key is ABSENT — which is what every chunk in every
existing store already looks like, and why no back-fill was needed.
Both halves of a hybrid query apply the same filters, so a hybrid search
cannot leak past a clause the dense search obeys.

**The most dangerous line in the phase is an early return, and it has
its own test.** `DocumentVisibility.sees_nothing` — a signed-in
principal with no entitlements on a LOCKED library — cannot be expressed
as a filter, because `MetadataFilters(filters=[])` means *no filter*,
which means EVERYTHING. `retrieve_nodes` returns `([], hybrid, index)`
before it ever reaches the retriever.

**The library posture decides the unlabelled case.**
`identity.access.may_see_unlabelled` (`:300`) answers True in `open`
posture; True for anybody not anonymous when `library_posture=open` (the
default) — **including the service principal**, because a document the
watcher created arrives unlabelled and inventing a default label would
be inventing a policy; and `sees_all_content` only when the library is
`locked`. The read path carries no posture branch: `library_posture` is
editable on the enterprise page and reset to `open` by `set_posture`
when a box moves to `personal`, so that branch lives in the write path
on purpose.

### 7. Tools: availability is DECLARATION ∩ REGISTRY ∩ ENTITLEMENTS

ADR 0015 §9 recorded that when grants moved to their own table,
`granted_tools`' `tool_keys` argument would disappear and "the principal
alone decides". **Owner decision 13 keeps the declaration, so the
argument survives and its MEANING changed**: `tool_keys` is what this
agent WANTS to be able to do; the grant is `ToolEntitlement` plus the
acting user's entitlements, and it arrives as a third argument.

Both halves of the original sentence could not hold at once. If an agent
still declares what it wants, that declaration has to reach the one
function that decides availability — and removing the argument would
mean either that the declaration stops being consulted (so every user's
full entitlement set is offered to every agent) or that `granted_tools`
reads the `Agent` row itself, which would make a Django-free rule-1 leaf
query the database. The declaration is the half worth keeping.

`agents.contracts.tools.ToolAccess` (`:199`) is how a pure leaf asks a
database question: plain data, built once per turn by
`agents/entitlements.py::tool_access_for` (`:22`) — an `agents` module,
which may import `identity.access` through the named seam and its own
models freely. `required` maps a tool key to the entitlement ids that
LABEL it; a key absent from that mapping is unlabelled and therefore
callable. It **defaults to unrestricted** (`UNRESTRICTED_TOOL_ACCESS`,
`:234`) so that every call site and test that does not care keeps
working unchanged — a fail-open default that is safe here and only here,
because **no production path relies on the default**. The two runtime
paths hand it in explicitly (`agents/runtime/jobs.py:145`,
`loop.py:472`, both pinned in
`agents/runtime/tests/test_acting_rule.py:199` and `:219`), and the
third, `agents/runtime/preflight.py:247`, builds its own `ToolAccess`
and applies `access.allows` over the result of ONE deliberately
log-free `granted_tools` call — one call rather than two, so an
unregistered key is not logged twice per preflight for no second fact
(`preflight.py:238-245`). (`agents/contracts/tools.py:218-220`'s own
docstring states the simpler "explicit on all three" version; the tree
is what this ADR records.)

`granted_tools` (`agents/contracts/tools.py:384`) is still the ONE
function that answers "may this caller call this tool", and it now makes
**three** drops rather than two: a key absent from the registry (dropped
and logged at info — normal, not exceptional: a feature-gated tool with
its flag off), a key whose spec is `mutates=True` (dropped outright —
ADR 0010's rule stays in force through IA-2), and a key the acting
principal's `ToolAccess` does not permit (dropped and logged at **debug**,
because on a labelled install it is the ordinary case and an info line
per turn per tool would drown the log).

**`tool_access_for` asks `sees_all_content`, not `is_admin`**: calling
somebody's labelled tool is reading, not administering, so an
administrator with the content setting off is filtered exactly like a
member.

**The two direct UI doors ask the same predicate the tool does.**
`vision.generate` and `rag.ingest` are reachable from pages as well as
from an agent, so `tools/vision/views.py::_may_generate` (`:817`) and
`tools/rag/views.py::_may_upload` (`:733`) each call
`tool_access_for(principal).allows(<key>)` through the named
cross-column seam — one predicate, two callers (the POST and the render),
so a page can never offer a form whose submission answers 403. **A
refused upload stages nothing**: `document_upload` checks the predicate
at `tools/rag/views.py:859`, before a single file is read out of
`request.FILES`, so nothing is written into the inbox and no `Document`
row exists to clean up. `rag.ingest` is `mutates=True` and therefore
grantable to no agent — a different question from whether a PERSON
reaches the form, and one the tool-label page keeps apart by listing
mutating tools as page-only.

### 8. Models are gated by SETS, and the ROLE path is never gated

A label on each connection was rejected in favour of a level of
indirection: `ModelSet` (`models/registry/models.py:225`) is a named
group of connections, `ModelSetMember` (`:254`) maps connections into
sets, and `ModelSetEntitlement` (`:272`) attaches entitlements to sets.
Two edges, edited independently and on two different pages: the console
owns "put this connection in a set" (the edge an operator touches when a
new model arrives), and the sets page owns "attach this entitlement".
One change then reaches everything on the other side of the set. **A set
is not an entitlement and not a capability**: it answers "which models is
this", once, so "who may use them" can be answered somewhere else and
changed without touching it.

`models/registry/access.py::model_access_for` (`:92`) builds the
`ModelAccess` value the same way `tool_access_for` builds its own, and
`connection_entitlement_ids` (`:71`) flattens the two edges into one
mapping in a single query — the sets exist so an administrator can
change many grants at once, and no read path benefits from re-walking
them. **A connection whose every set is unattached is absent from that
mapping**, because the join finds no attachment for it: "a set with
nothing attached restricts nothing" is obtained by construction rather
than by a second branch. It is also stated loudly where an operator will
meet it mid-workflow, on the sets page and beside the console's
membership control, worded to read correctly in either state — creating
the set, adding the models, then attaching the entitlement is the
routine order, so the unattached state is the middle of the workflow
rather than an edge case.

The gate has two halves and both live in `models/registry/bindings.py`,
the one submodule of that package another column may import (which is
why `models/registry/access.py` is re-exported from it rather than
imported directly): **the render half** is `picker_options`, which drops
a connection the principal may not use so it is never in the `<select>`;
**the gate half** is `resolve_connection_named` (and its thin wrapper
`resolve_connection`), which raises the same `ValueError` it already
raises for a pk that names nothing usable. One `access` argument on two
functions covers every submission path on the box at once. The refusal
copy is one sentence homed once —
`models.registry.bindings.MODEL_FORBIDDEN_MESSAGE` — after three columns
were found carrying byte-identical private copies of it under two
different names.

**A model resolved through a ROLE is never gated, and this is the one
place "least privilege" is deliberately not applied to a resolution
path** (spec §9.5, §22.32, flagged for the owner there). The role path
answers for the embedding, transcription and extraction roles and for
the default answering binding; none of those is a user's choice, they
are the box's own wiring, and gating them would mean a set containing the
embedding connection silently breaking ingestion, retrieval and
re-encode for everybody — including the watcher and every management
command. `resolve_chat` consults `access` only when a connection was
picked, which is what makes `agents/runtime/delegate.py:148`'s explicit
`access=UNRESTRICTED_MODEL_ACCESS` correct rather than a hole: a
delegate never carries a picked connection, so it takes the role path,
and the argument is passed explicitly **with its reason** rather than
defaulted, because the parameter is required precisely so nobody omits
it by accident. An operator who wants a model unreachable by everybody
unbinds it or removes it; that is what the console is for.

### 9. Agents and flows: the label clause is AND-ed onto the carve-out, and a refusal never eats history

`AgentEntitlement` and `FlowEntitlement` gate not "may this principal
call this tool" but "may this principal use this agent or flow at all".
`agents/visibility.py::label_permitted_q` (`:76`) is ONE clause used by
all three bodies — `visible_agents` (`:96`), `installed_agent_slugs`,
`visible_flows` (`:143`) — so the three cannot come to disagree about
what a label means. Both halves are spelled out (`entitlement_labels IS
NULL` OR `entitlement_labels.entitlement_id IN held`) because the short
spelling is wrong: a bare negation would exclude a labelled row from
EVERYBODY.

**The clause is AND-ed onto the ownership OR, not OR-ed into it** (the
IA-2 plan's decision 33). The ownership clause is `own rows OR
resident=True OR shared`; the label clause narrows that whole group. Two
consequences follow, and both are the point:

- **A shipped `resident=True` agent that has been labelled is restricted
  exactly like an operator-created one.** Labelling a default is not a
  case the rule quietly exempts.
- **An owner's own labelled agent hides from them too** (the plan's
  decision 35, the consequence the parentheses do not announce).
  Ownership plays no part in the label test either way, which is what
  makes labelling one's own agent actually restrict it rather than being
  bypassable by the person it is aimed at.

**A label that arrives after a conversation exists does not eat the
conversation.** `agents/runtime/preflight.py` refuses a NEW turn with
the closed reason `AGENT_NOT_PERMITTED` and a 403 — before any `Turn`
row is written — and the shipped copy says exactly what happened:

> "This agent needs an entitlement this account does not hold. The
> conversation is still readable; new turns are not."

Nothing about `visible_conversations` changed, and the thread view never
calls `visible_agents` at all (it reads `conversation.agent` directly),
so the history renders in full. A member is never told a conversation
they can still open has vanished.

**Preflight asks `label_permitted_q` alone, not `visible_agents`, and
the difference is not an optimisation.** This function is reached for a
turn on a conversation the actor is ALREADY posting to — a `use`-level
share recipient among them — while `visible_agents` also encodes the
ownership/resident/share OR that decides whether somebody may START a
conversation with an agent from scratch. Asking the fuller function here
would 403 a shared-conversation poster the moment their turn reached
preflight, for an agent they were never meant to own or discover,
breaking sharing for every unlabelled agent.

`sees_all_content` short-circuits first in every one of these bodies, so
an open box pays nothing for any of it.

### 10. Sharing is ONE generic table with two levels

`agents.Share` (`agents/models.py:598`) carries a target TYPE and a
target KEY as text, four target types (`conversation`, `agent`, `flow`,
`vision_output`) and two levels (`view`, `use`). One table, so adding
the second sharing UI later is a form and a template rather than a
second table and a second set of visibility functions. **IA-2 ships
exactly one UI** — the conversation share panel on the thread page — and
`vision_output` has no writer at all, so it cannot orphan.

The key is text rather than a foreign key because the four targets live
in three apps across two key types (a conversation's UUID, an integer
for the other three), a real FK would be a
cross-column import, and a `GenericForeignKey` would make `contenttypes`
a second identity for a row this platform already identifies by pk. **The
key is parsed on the way in and again on the way out**: `save()` refuses
a key the target's own parser rejects, and `agents/shares.py::shared_keys`
drops one on the way out, because a row can also arrive from a shell or
from an older schema and an unparseable key reaching `Q(pk__in=[...])`
raises inside a listing queryset — a 500 on a never-500 surface,
reachable by one bad row.

`agents/shares.py` is a SEPARATE module from `agents/visibility.py`
because the column-boundary guard forbids any module under `agents/chat`
from touching `Conversation`/`Agent`/`Flow` `.objects` directly, and IA-2
adds `Share` to that same set. One module owns the manager; everything
else asks it a question.

Three rulings are worth recording:

- **The widest level wins** when a direct share and a group share both
  reach the same row. The answer has to be ONE level, and anything but
  the widest would mean adding somebody to a group silently REMOVED an
  ability they already had.
- **A recipient may not re-share.** `share_conversation`/`revoke_share`
  and the panel that renders them share one predicate
  (`may_read_conversation_shares`), so the page cannot render a control
  the POST refuses; a recipient passing a thread on would make the
  owner's own list of who is reading it wrong.
- **`update_or_create`, not `create`.** The two partial uniques mean a
  second share to a subject already sharing the row would raise
  `IntegrityError` from a plain create — a 500 reachable by a double
  submit, a back-button re-POST, or the ordinary "change this share from
  view to use" gesture. Last-write-wins on `level` is not a compromise
  here; it IS that gesture.

`may_post_to` is what tells a `view` share from a `use` one, and it is
the whole reason `Share.Level` exists rather than the level being
implied. Orphans are inert and no signal sweeps them — this repository
uses no Django signals anywhere, every reader resolves the target first,
and the one shipped delete surface
(`agents.visibility.delete_conversation`) removes a conversation's shares
in the same transaction.

**Two picker readers, not one with a flag.**
`identity.access.share_subjects` (`:393`) excludes the caller;
`::grant_subjects` (`:416`) does not. Sharing a row with yourself is a
no-op a form should not offer; granting yourself an entitlement is not —
an administrator with the content setting off reads nothing labelled, so
holding the entitlement is their only way to read a document under it,
and a grant form built from `share_subjects` would leave them with no UI
path to grant themselves one. The bodies differ by one `.exclude()`, and
that clause is the whole of what each answer means. Both live in
`identity/access.py` because a label or share picker needs entitlement
and account NAMES, which no column outside identity may read.

### 11. A delete reaches columns identity may not import, and the counts are the SAME object the page prints

Deleting an entitlement removes its grants (a `CASCADE` inside this
column), its document labels, its tool labels, its agent and flow
labels, and its model-set attachments — and because a document that
loses its last label becomes unlabelled and follows the library posture,
it must RE-STAMP every affected document's chunk metadata inside the
same transaction. Rule 4 means the delete cannot name any of those
functions.

`identity/contracts/cascades.py::EntitlementCascade` is the registry
that dissolves it: a pure frozen dataclass of `key`, `label` and a
**dotted-path `handler` string**, registered by each owning column from
its own `AppConfig.ready()` — exactly as roles, job kinds, tools and
`OwnedRows` already register themselves, and for the same reason: a
sixth labelled thing later is a REGISTRATION, not an edit to a service
that would otherwise silently skip it. Four are registered today:
`rag.document_labels`, `agents.tool_labels`, `agents.runnable_labels`
and `inference.model_sets`.

**One handler, two modes, deliberately** — rather than a counter and a
detacher registered separately. `commit=False` COUNTS what this column
would remove; `commit=True` removes it, repairs whatever cache hung off
it, and returns the same count. Two registrations would be two things to
keep in agreement about what "affected" means.

`identity/cascades.py` resolves the path with
`django.utils.module_loading.import_string` and **never swallows**: a
cascade whose handler cannot be imported, or which raises, takes the
whole delete down with it — inside
`identity.services.delete_entitlement`'s (`:490`) one
`transaction.atomic()`, so nothing is half-deleted. A delete that
reported success while leaving orphan labels and a stale chunk cache
behind is the exact failure this registry exists to prevent. The
cascades run BEFORE `entitlement.delete()`, because the re-stamp has to
see a document with its label already gone and Django's own collector
offers no hook between "rows deleted" and "transaction committed". The
database's `CASCADE` stays in place as the net: a shell that deletes the
row directly still leaves no orphan labels, only a stale cache, and
`manage.py relabel_chunks` is the documented repair.

**The delete confirmation names the counts first**, and it must: every
document the entitlement was the LAST label on becomes unlabelled and
follows the library posture from that point on. This is the one
administrator action in this column that **widens** access rather than
narrowing it, and a confirmation that did not say so would be a
confirmation of the wrong thing.

The same registry underlies the owned-rows walk.
`identity/contracts/ownership.py::OwnedRows` names each owned table as an
`"app_label.ModelName"` STRING, resolved by `apps.get_model` at command
time and never imported, so `manage.py adopt_open_rows` and
`manage.py reassign_owner` can walk five owned tables — `Agent`, `Flow`,
`Conversation`, `AskRecord` and `GenerationJob` — in columns `identity/`
may not import. `identity/ownership.py` is where that
resolution lives, exactly as `identity/cascades.py` is where
`import_string` lives.

### 12. A turn acts as the USER, at every depth

The acting rule (spec §5.3) reverses what ADR 0015 §10 recorded. A turn
no longer runs as the chosen agent's own minted principal: it runs as the
USER named in its job payload
(`identity.contracts.principals.principal_from_payload`, reading the
`actor_kind`/`actor_key` every enqueuing surface now writes), and
`agents/runtime/bindings.py::principal_for` was deleted outright — not
moved, not renamed — because the question it answered ("who does a turn
by this agent run as") is the wrong one once an agent is understood as a
tool a person wields rather than a second person with its own identity.
It never raises: a payload with no actor (every turn enqueued before this
phase) or a hand-edited one with an unrecognised kind both still run,
honestly, as `OPEN_PRINCIPAL`.

**No hop widens the acting principal.** `delegate.py::run_agent_tool`
inherits `ctx.principal` verbatim and passes it into the nested loop.
What DOES change at a delegation hop is `ToolContext.agent_slug`: WHOSE
TOOL DECLARATION IS IN FORCE, a separate fact from WHO THIS IS BEING
DONE FOR that the acting rule needs told apart.

The consequence for grants is the reason this decision is here rather
than only in ADR 0015's amendment: **a turn's tool list is the agent's
declaration intersected with the acting USER's entitlements.** A
delegate that inherited its own agent's principal would be a hop at which
an entitlement rule could be walked around by delegating — so
`visible_flows`' 2026-08-28 delegate ruling is reversed by the same
logic, and a delegate sees the flows the ROOT USER may run. Flipping the
posture changes what an agent can *run*, not merely what a page lists.

**The service principal is the honest edge of this rule.** Every shell
path — the watcher, `manage.py ingest`, `manage.py ask`,
`manage.py agent_turn` — acts as ONE `SERVICE_PRINCIPAL`, rather than
one kind per caller, which would quietly turn "the shell" into four
grantable subjects. What a shell path's audit row *does* record is
`source="cli"` (`identity/contracts/actions.py`'s `SOURCE_CLI`, written
by `identity_posture`, `adopt_open_rows` and `reassign_owner`), which tells the shell apart
from the web and from `/admin/` but not one command from another.
Recording WHICH command is the shape
`identity/contracts/principals.py:30` reserves —
`AuditEvent.detail["source_command"]`, which `identity.audit.record`'s
`**detail` would accept — and **nothing writes it today**; the other
shell paths named above write no audit row of their own at all. A
service principal holds no entitlement, because grants
attach to a user or a group by the XOR constraint. It therefore gets
**unlabelled tools only**, cannot resolve a picked connection that is in
a set, and cannot run a labelled agent or flow — including a shipped
`resident=True` one. All three refuse with the same copy a member gets,
and all three stand until service-account tokens land (G1). The role
path is unaffected, which is what keeps the watcher and `manage.py
ingest` working, and the consequence is written on the pages where an
operator will cause it: the tool-label page, the model console, the sets
page, and beside any `resident=True` row on the agent/flow access page.

### 13. Provenance is a COLUMN, and the `User` instance travels through ONE seam

Every label and membership writer records who did it, twice over:

- **A column on the row.** `DocumentEntitlement.labelled_by`,
  `ToolEntitlement.labelled_by`, `AgentEntitlement`/`FlowEntitlement`'s
  own, `ModelSetMember.added_by`, `ModelSetEntitlement.attached_by` — all
  `SET_NULL`, so deleting a user never deletes the fact it recorded.
- **An audit row.** Every writer writes the DIFFERENCE, not the
  submitted set, so the trail records what changed rather than what was
  resubmitted. `AuditEvent`'s action vocabulary is closed and validated
  in `save()` — 42 actions today (`identity/contracts/actions.py`) — and
  the table is append-only: `save()` refuses a row that already exists,
  and the real guard is an AST sweep, because a queryset `.update()` or
  `.delete()` never calls `save()`. No module outside `identity/audit.py`
  may touch `AuditEvent.objects` **at all** — not `.create`, not
  `.filter` — with its own anti-vacuous pin proving the sweep catches
  `update` and `delete` and not only `create`. `AuditEvent` carries **no
  foreign key to `User`**, deliberately: an audit row a cascade could
  delete is not an audit row, and `actor_label` is denormalised so a line
  still reads correctly after the account is renamed or deactivated.

**No column outside identity imports `identity.models` to obtain a
`User`.** `identity.request.user_for_request(request)` is the one seam
that hands back a real row for a foreign key, and every label writer
takes it as a keyword-only `labelled_by`/`added_by`/`attached_by`
argument and does nothing with it but assign it. It returns `None`
rather than `AnonymousUser` for an unauthenticated request, so a
`ForeignKey` always gets a value it can actually take. A shell path or
the entitlement-delete cascade passes `None` and leaves the audit row to
carry the provenance.

**`foundation/ops/tests/test_column_boundaries.py:731`'s
`_REQUEST_USER_ALLOWED` is EMPTY and stays honest.** An AST sweep over
`tools/`, `models/` and `agents/` fails on any direct
`request.user`/`.is_superuser`/`.is_staff` read, with an anti-vacuous
pin on the file list (so a typo'd column name cannot make it pass by
looking at nothing) and a second pin on the allowlist itself.
`identity/request.py` is deliberately outside the scanned set: reading
`request.user` THERE, once, is the seam.

`ToolInvocation` gained `agent_slug`
(`agents/migrations/0003_toolentitlement_and_share.py`), written by
`invoke_tool` from `ToolContext.agent_slug`, so the audit table itself
answers "which agent made this call" rather than the answer surviving
only in `Turn.tool_call["agent"]`. Its `principal_kind`/`principal_key`
columns are `Principal`'s two fields stored flat — WHO THIS WAS DONE
FOR, a different question from whose declaration was in force.

### 14. The guard rails ARE the phase

An access-control change is only as good as what fails when somebody
widens it by accident. Six guards carry this one, and each is written so
it cannot pass by looking at nothing:

1. **The route matrix** (`identity/tests/test_route_matrix.py`) sweeps
   **every** registered route × **four principals** (anonymous, member,
   administrator, entitlement owner) × both settings of
   `admin_sees_content`. **The route list is derived, not typed**: a
   route added to any `urls.py` with no `ROUTE_RULES` entry fails
   immediately naming itself, and a route with no driver raises a
   `KeyError` naming itself. Every row-addressed route points at a row
   owned by a third account that appears in no test as a caller, which is
   what gives "an administrator with the setting off gets the member's
   answer" something to be about. The owner column's base mapping is the
   MEMBER's, with the routes where owning changes the answer named in a
   separate set — a cell that said "admitted" for every `R` route would
   pass whether or not the owner rule was implemented at all.
2. **The zero-query pins** (§2): eleven tables, every mount, including
   `identity-settings`.
3. **The isdecimal sweep**
   (`foundation/ops/tests/test_isdecimal_sweep.py`) is permanent and
   repo-wide. `"²".isdigit()` is `True` and `int("²")` raises, and two
   reproduced 500s came from exactly that gap — one anonymous-reachable,
   one member-reachable. `.isdigit(` is banned OUTRIGHT in production
   code rather than narrowed call site by call site, with one documented
   exception carrying its reason, because the failure mode is a 500 on
   whatever surface a reviewer forgets to check next.
4. **The import-law allowlist** (§1), which closes a private module
   nobody has written yet.
5. **The `request.user` sweep** (§13), whose allowlist is empty.
6. **The `.objects` guards**: no module under `agents/chat` reaches
   `Conversation`/`Agent`/`Flow`/`Share` directly, no module outside
   `identity/audit.py` reaches `AuditEvent`, and
   `tools/rag/views.py` reads documents through `tools/rag/access.py`.

One negative rule is worth stating because it looks like an omission.
**A class-`S` route outside `identity/` carries no `@require_admin`
decorator.** `identity/routes.py` classifies it `S`, `tier_for` maps `S`
to the ADMIN tier, and `IdentityGateMiddleware` refuses a non-admin
before the view function runs — so the decorator would be a second,
redundant belt, and `identity.gate` is not one of the four seams another
column may import. `identity/gate.py` exists for the callers that do not
arrive through the middleware at all (a management command's future HTTP
counterpart, the MCP edge) and is used inside `identity/views.py`; that
it refuses through the SAME `refuse_anonymous`/`refuse_not_admin`
functions the middleware uses is what stops a poll getting a different
answer depending on which door refused it.

**An unclassified route is treated as ADMIN and logged.** Forgetting to
classify a new route fails closed and loudly, and the matrix fails
immediately naming it — which is what keeps `ROUTE_RULES` a live artefact
rather than a document that rots. `/admin/` is classified WHOLESALE by
its namespace rather than name by name, which is also how it becomes
superuser-only rather than Django's default staff-only with no
`AdminSite` subclass.

### 15. Admin ergonomics: ONE action, and ONE counter

Two decisions here are the owner's (spec §22.34), and both are about a
box an administrator actually has to run.

**Bulk labelling, because the inbox gap makes it routine rather than
occasional.** A watcher-ingested document arrives unlabelled, and an
administrator labelling forty of them one at a time would be doing
exactly the tedium the owner named as a failure.
`rag-document-labels-bulk` is a SECOND URL rather than a wider
`rag-document-labels`, because that route sets a document's labels to
EXACTLY what was submitted and this one ADDS OR REMOVES across many;
folding both into one route would mean a hidden field deciding whether a
POST is destructive, which is the one thing a bulk control must never be
ambiguous about. It never sets-to-exactly: somebody selecting forty
documents to add one label must not silently strip the labels those
documents already carry. Permission is checked PER DOCUMENT with the
same `may_label_document` the single-document route uses, and a document
the caller has no standing over is **skipped and counted** rather than
filtered out of the candidate set — a document that silently disappeared
from the count is the one thing this route promises an operator not to
do.

**Apply-to-category stores NO RULE.** Targeting a whole category is the
same action at a different cardinality; a document added to that category
afterwards is unlabelled, deliberately. A stored category→entitlement
mapping was considered and is not wanted: it would be a second,
invisible labelling authority beside `DocumentEntitlement`, applying
itself to rows nobody reviewed.

**The entitlement-reach panel is an ALIAS of the delete confirmation's
own counter.** `identity.services.entitlement_reach` (`:462`) is one
line returning `entitlement_delete_counts` (`:448`), so what a reader
sees on the page and what they see when they start a delete can never
disagree. Its docstring says the quiet part: the moment this function
grows a body of its own, it has become the second counter the decision
exists to prevent.

**The per-user effective-access panel** on the users page
(`identity.access.effective_entitlements`, `:360`) names, per account,
every entitlement it holds and whether it is DIRECT or VIA a named
group, with that entitlement's own reach — memoised by the view's own
`_reach_cache` (`identity/views.py:189`) per ENTITLEMENT rather than per
account, so two accounts sharing one entitlement do not pay for its
cascade queries twice in a request. **A direct grant wins
over a group one and the row appears once**: the direct grant is the
stronger fact (it survives the person leaving the group), and showing the
same entitlement twice would make an administrator count it twice.

## Named gaps and deferred work

Recorded as their own section, following ADR 0013 §8's and ADR 0015's
shape, because these span the whole build rather than one decision in
it.

**G1 — service accounts and the MCP edge are IA-3, and the hooks are
built.** A `ServiceAccount` table and issued tokens are deferred *as a
table*, not merely as issuance: it would have no reader and no writer in
IA-1 or IA-2, and its shape (hash algorithm, scope vocabulary, rotation)
should be decided against a real authenticator rather than guessed. The
consequence stands today and is permanent until then: **a labelled tool
is uncallable from the watcher or the command line**, along with a
set-restricted picked connection and a labelled agent or flow (§12). The
MCP edge's own two hooks are in place — `granted_tools` takes the acting
principal's `ToolAccess` as a third argument, and `ToolInvocation` (with
`agent_slug`) is the audit table an external caller's calls land in
unchanged.

**G2 — tenancy is not this.** Entitlements partition one library among
the people who share one machine; they do not partition machines.
Multi-tenancy is a named non-goal (spec §20.10). ADR 0015's G13 item 3
called for "visibility scopes on documents and categories": the
**documents** half shipped, applied at the one filter point. **Categories
stay taxonomy** — a category is library-wide, `rag-category-rename`/
`-delete` are class `S`, and no entitlement narrows which principals see
a given category.

**G3 — SSO/OIDC is IA-4, and SCIM is later.** `EntitlementGrant.source`
is the column both land on, and `identity.request.principal_for_request`
is the one request→principal seam a claim-mapping layer would fill. An
`SsoGroupMapping` table is added then, not now. Local MFA is an identity
provider's job (spec §20.6).

**G4 — login lockout and throttling are not built.**
`identity.login_failed` audit events are the signal a policy would read;
no policy reads them today.

**G5 — per-inbox default labels for the watcher are not built.** A
document the watcher ingests arrives **unlabelled** and follows the
library posture until somebody labels it. The mitigation is §15's bulk
labelling; the alternative (a stored category rule) is refused there for
cause.

**G6 — renaming a user has no audited path.** There is no catalogue
action for it and the users page offers none. The break-glass form DOES
leave `username` editable (`identity/admin.py:107`, whose
`readonly_fields` covers `is_staff` only), and `IdentityUserAdmin.
save_model` routes only `is_superuser` and `is_active` through
`identity.services` — so a rename there falls through to
`super().save_model` and writes **no audit row**, because `AUDIT_ACTIONS`
has no rename action for it to write. That is the gap, not a feature.
`Principal.key` is a user's primary key and never their username for the
related reason — a rename must not orphan every row a person owns — so a
username is display-only everywhere the product surfaces it.

**G7 — a staged vision upload's preview is administrator-only, and IA-2
did not close it.** A `JobInput` recorded before the generation that will
consume it (`job_id is None`) carries no owner column, so on a box with
accounts its bytes are served to `is_admin` only and a member gets the
same 404 an invisible row gets everywhere else. On an open box nothing
changes. Closing it needs the shape every other blank-owner gap was
closed with — an `owner_kind`/`owner_key` column and its migration, a
sixth `OwnedRows` registration, and `adopt_open_rows` extended to claim
the pre-phase blanks — and it is a later follow-up rather than a
regression: spec §17's IA-2 migration table names five migrations and
none of them touches that table. It is a parked owner decision, recorded
in full at `tools/vision/README.md`.

**G8 — sharing has one UI.** `Share` carries four target types and IA-2
ships the conversation panel only; agents, flows and generated images
have no share control, and `vision_output` has no writer at all.
**Time-limited or public shares are NOT PLANNED**, recorded so nobody
builds one by accident: a link that works without a session is a hole in
the posture model, and an expiry column implies a sweeper.

**G9 — `/admin/` is unstyled on a deployed box.** `STATIC_ROOT` is
deliberately unset and nothing in this repository runs `collectstatic`
(the ruling is ADR 0015 §1's, unchanged here). There is no WhiteNoise
and no `static()` pattern in `config/urls.py`, and the image's `CMD` is
`migrate` + `uvicorn`, so **nothing in the deployed stack serves static
files at any `DEBUG` setting** — `django.contrib.staticfiles` only
serves bytes through `runserver`, which is itself DEBUG-gated, so
`DEBUG` is the determinant on a development box only. Django's admin
CSS therefore does not arrive. That is acceptable
precisely because `/admin/` is a break-glass tool and never a product
surface: every guarded write it performs is delegated to
`identity.services`, so the last-admin guard and the audit trail apply
there exactly as they do on the pages. It is fixed by whoever first puts
a real static-serving story in front of the app, in that same change.

**G10 — Django's `Permission` model, Postgres row-level security, and a
per-object permission framework are all named non-goals** (spec §20.2–4),
not omissions. Each would be a second enforcement mechanism that has to
agree with the functions in §3 and §5 forever — one of them invisible
from the application entirely.

**G11 — the mutating-tool grantability gate is UNCHANGED.**
`grantable_tools()` still excludes every `mutates=True` spec
unconditionally rather than consulting a grant. ADR 0010's rule stays in
force through IA-2 and is lifted by a later phase, against a real policy
decision about which entitlement such a tool requires by default. The two
builder UIs (agent and flow) that ADR 0015's G13 item 1 also named are
likewise not built. All three stay open in ADR 0015's G12, not quietly
recounted as done.

**G12 — deferred with the hook each relies on**, listed rather than
re-litigated (spec §21): "keep me signed in" (one `set_expiry` call and
`session_idle_minutes`); audit CSV export and retention (the closed
action vocabulary and the `at` index); group managers (a through-model on
membership, which is why membership is uncustomised now); an
ownership-reassignment page (`manage.py reassign_owner` and the
owned-rows registry); per-user data export (the same registry); and
per-user rate limiting (`ToolInvocation`'s principal columns).

**G13 — a box ships in `open` posture with no accounts, and the
machinery sleeps until an operator turns it on.** Nothing in this ADR
changes what an operator sees
until they create the first administrator and switch the posture — in
that order, because switching first leaves a new administrator looking at
their own empty box until they adopt (`identity/README.md` §5, and
`docs/OPERATIONS.md`'s "Turning on accounts"). Until then every claim
above is inert, which is exactly the property §2's zero-query pins
assert.

## Consequences

- **The repository is five columns, and the fifth is the base.** Every
  column may ask identity who is acting and what they may do; identity
  may ask no column anything. Two commands, four cascades and five owned
  tables cross that line through registries of strings rather than
  imports.
- **An open box is unchanged, and that is tested rather than asserted.**
  Eleven tables are named in no query on any mount; the retrieval filter
  is the one it was before; the label, share and set clauses are all
  short-circuited before they are built.
- **There is exactly one place a request becomes a principal, one place
  availability of a tool is decided, one place a document query is
  filtered, and one place an entitlement's reach is counted.** Each of
  those four is asserted at, not merely documented.
- **Administering and reading came apart, with the conservative
  default.** An operator can run a box — cancel jobs, label documents,
  prune what the shell produced — without reading anybody's
  conversations, and turning that on is one audited act on one page.
- **A grant now exists as a row, so the tool contract's principal
  argument finally reads something.** `granted_tools`' `tool_keys`
  survived as a declaration rather than disappearing, and availability is
  the intersection of three facts instead of two.
- **A label composes with the carve-outs instead of being bypassed by
  them.** A shipped default agent, an owner's own agent, and a
  conversation's history each behave the way the rule says rather than
  the way the exception would.
- **The audit trail is append-only in code and guarded by an AST sweep**,
  with a closed 42-action vocabulary a later report can be built on
  without parsing prose.
- **A deploy onto an existing database is a documented, refusable
  sequence**, not a hopeful `migrate`: `manage.py
  identity_repair_migration_history` is step 0, it refuses by name on
  every shape it was not written to guess about, and a fresh install
  needs none of it.

## See also

- `identity/README.md` — the column as a whole: its import law, the three
  postures, the access questions, and what IA-2 added.
- `identity/contracts/principals.py` — the principal kinds, including the
  two historical ones and why they stay.
- `agents/README.md`, `agents/runtime/README.md` — the acting rule,
  `ToolAccess`, `AGENT_NOT_PERMITTED`, and the delegation knock-on.
- `tools/rag/README.md` — document labels, the library posture, the one
  filter point, and the chunk-metadata cache.
- `models/registry/README.md` — model sets, `model_access_for`, why it
  is re-exported from `bindings`, and the role-path exemption stated in
  full (`models/README.md` carries the three-line summary and points
  here).
- `docs/OPERATIONS.md` — turning accounts on, the IA-1 deploy onto an
  existing database, `relabel_chunks`, and why a backup is now a
  credential store.
- [ADR 0015](0015-agent-layer-and-tool-contract.md) — the four columns
  this ADR's §1 extends to five, the tool contract §7 amends, and the
  §10 seams IA-1 filled in; amended at its own foot for both halves of
  this phase.
- [ADR 0013](0013-inference-execution-queue.md) — the queue whose job
  payloads now carry the acting principal (§12), amended at its own foot.
- [ADR 0010](0010-model-management-framework.md) — roles and bindings,
  whose role path §8 exempts; amended at its own foot for `/inference/`'s
  closed mutation gap and for the mutating-tool gate this phase does not
  lift (G11).
- [ADR 0009](0009-document-store-and-categories.md) — the categories §15
  bulk-applies to and deliberately stores no rule against.
- `docs/superpowers/specs/2026-08-29-identity-and-auth-design.md` — the
  design this build was written against, including the owner decisions,
  the non-goals G10 lists, and the deferral table G12 draws from.

## Amendment (2026-09-05) — Workstreams: the 404 house rule gains one scoped exception, and `Share` a fifth target

**The 404 house rule (§3's own statement above, and the identity spec's §11.1) now has ONE
scoped exception, and it is fenced three ways.** `chat-workstream` — the one route in the
platform addressed by a row, GET `w/<int:pk>/` — answers **403**, not the usual 404, for a
principal holding a real, live `Share` row on a workstream whose taint set has outrun their
grants: a recipient shared into a stream, told about it, and later denied one of the
entitlements the stream's own retrievals have since touched. The 403 names the missing
entitlements and nothing else the caller could not already infer, and the page reads:

> This workstream is not readable right now.
> It contains material from entitlements you don't hold: **Finance**, **Legal**.
> Ask an administrator for those entitlements, or ask the workstream's owner.

**The three fences that make the disclosure the smallest one still useful, and the reason it
does not widen the house rule everywhere else:**

1. **Reachable only through a real `Share` row.** Without one, `agents.visibility.
   visible_workstreams` excludes the stream from the caller's own queryset and the view raises
   `Http404` **before** the 403 predicate (`agents.workstreams.stream_access`) is ever called —
   there is no input a stranger can supply that reaches the 403 branch. `identity/tests/
   test_route_matrix.py::test_chat_workstream_answers_403_for_a_live_share_holder_and_404_
   without_the_row` pins the pair together, because the exception is only safe if the negative
   case holds.
2. **Every missing entitlement is named, and nothing else is.** The one caller of
   `agents.visibility.name_for_viewer`'s `disclose_all=True` mode, and the set named is exactly
   this stream's taint set minus what the reader holds — not their other streams, not the
   catalogue, not who else holds the missing entitlements.
3. **Capped at five names**, with "and N more", so a long-tagged stream cannot be turned into a
   bulk enumeration by sharing it and letting the grant lapse.

**It discloses entitlement NAMES to a non-admin, and §4's decision above (Labelling) had never
done that before this phase.** `identity-entitlements` is class **S**; the accounts page
rendering `effective_entitlements` is class **S** too; the only entitlement names a non-admin
saw anywhere came from `labelling_entitlements`, filtered to `owned_entitlement_ids` for a
non-admin. This 403 is the first route to name an entitlement to a **holder who neither owns
nor holds** it, and it is a deliberate trade — a name for actionability — rather than an
oversight; `identity/tests/test_route_matrix.py::
test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder` pins
that it stays the only one.

**`agents.Share.Target` gains a fifth value, `WORKSTREAM`**, beside `CONVERSATION`, `AGENT`,
`FLOW` and `VISION_OUTPUT` (§10 above). The key is an integer workstream pk, parsed by the same
per-target parser table §10 describes, and `share_workstream`/`revoke_workstream_share`/
`share_list_for` (`agents/visibility.py`) are the workstream-shaped twins of
`share_conversation`/`revoke_share`/the conversation share panel's own reader — one more spelling
of the one generic table, not a second one. The three rulings §10 records — the widest level
wins, a recipient may not re-share, `update_or_create` rather than `create` — hold for this
target exactly as they do for the other four.

Recorded in full, alongside every other Workstreams mechanism, by the binding spec,
[`docs/superpowers/specs/2026-09-03-workstreams-design.md`](../superpowers/specs/2026-09-03-workstreams-design.md)
(§12 in full for the two share gates and this exception; §10 for consolidation). ADR 0017, the
architectural record of the Workstreams programme itself, is written after both of its halves
merge to `main` — the ADR 0016-after-IA precedent — and is the next piece of work this
amendment hands off to.

## Amendment (2026-09-14) — Security round 3 (H40, A-2): `auth.Group` leaves the admin; the identity page is the one door

**§2 above records that groups are Django's own `auth.Group`, unchanged, and left that way in
`identity/admin.py` too** — the docstring's own reasoning was that this platform never reads
`Group.permissions`, so there was nothing there to hide and nothing gained by subclassing the
stock admin. That reasoning covers permissions only. It does not cover the two reverse foreign
keys this platform hung off the model since — `EntitlementGrant.group` and `agents.Share.group`
— so deleting a group through `/admin/` cascaded away every entitlement grant and every share
whose subject was that group, and wrote none of the audit rows `identity.services.delete_group`
writes for the identical change through the identity page's own groups page. Creating or
renaming a group through the admin would have skipped its audit row the same way, had Django's
stock forms offered a rename at all.

**`identity/admin.py` now calls `admin.site.unregister(Group)` at module level**, rather than
subclassing it to route through `identity.services` the way `IdentityUserAdmin` already does for
the user model. The identity page is already the one door — its own create, add/remove-member
and delete paths, each audited — and a subclass that behaved correctly would still be a *second*
door to a guarded write, which is exactly the shape `identity/admin.py`'s own module docstring
already names as a guard that does not exist. A subclass remains the right call if the platform
ever needs break-glass group editing to survive an unreachable identity page; that is a
preference the owner has not asked for, not a security requirement, so it is not built.

**Superuser only, so this closes an audit-trail gap, not a privilege-escalation one** — every
account that could reach `/admin/auth/group/` before this amendment could already delete the
same group, its grants and its shares through the identity page, just with an audit row. `identity/
tests/test_admin_doors.py` pins the unregistration, that `/admin/auth/group/` answers 404, that
the identity page's groups page still creates and deletes groups (groups have no rename
primitive in `identity.services` to skip), and that deleting a group through it still writes the
`group.deleted` audit row. `identity.User` stays registered — this amendment narrows one model,
not the break-glass surface itself.

## Amendment (2026-09-16) — Entitlements are manageable from both directions: an axis registry, and two projections of it

**§4 above records the four labelled axes and the pages that write them, and every one of those
pages is RESOURCE-major.** `/chat/tools/` asks a tool which entitlements label it;
`/chat/access/` asks an agent and a flow; the library asks a document; `/inference/sets/` asks a
model set. That is the right question when you are administering the resource. It is the wrong
question when you are administering the ENTITLEMENT — and a box that runs to fifty of them is
administered entitlement-major most of the time: *which tools does Finance cover, and add two
more.* Before this amendment the answer to the first half was a count panel
(`entitlement_reach`, §4's own reach decision) and the answer to the second half was "open five
other pages".

**The entitlement is one object with two projections, and both now exist.**
`identity-entitlements` is the manage-ALL screen: a plain GET search over name and description,
and one reach column per kind beside the holder count. `identity-entitlement-edit` is the
manage-ONE screen: the reach panel it always had, plus one two-pane transfer panel per editable
kind.

### The axis registry — the only lawful seam

Rule 4 (§1 above) is unchanged, and it is what forces the shape: `identity/` cannot name
`ToolEntitlement`, `AgentEntitlement`, `FlowEntitlement`, `ModelSetEntitlement` or
`DocumentEntitlement`, so it cannot count them and cannot write them. Each column therefore
**registers** one `EntitlementAxis` from its own `AppConfig.ready()` — a pure descriptor of
dotted-path strings in `identity/contracts/axes.py`, resolved by `identity/axes.py` with
`import_string`, never swallowing. **This is the delete-cascade registry of §4 pointed the other
way**, deliberately the same mechanism rather than a second one: the cascade registry answers
"this entitlement is going away, what do you lose", the axis registry answers "which of your
rows carry it, and change that". Five axes are registered today — tools, agents, flows and model
sets from `agents/` and `models/registry/`, documents from `tools/rag/`.

**Two capabilities in one descriptor, because they are two different commitments.** Every axis
must be able to COUNT itself across all entitlements at once (`counts`, `{entitlement_id: n}`,
one aggregate). An axis MAY additionally be EDITABLE (`rows`/`ids_for`/`set_for`, all three or
none), which is what earns it a transfer panel. The list page's query count is therefore flat in
the number of rows — one aggregate per axis, not five counts per row — and
`identity/tests/test_entitlement_pages.py` pins it equal at one entitlement and at thirty, which
is the sidebar-N+1 lesson applied before rather than after.

**Registration order does not decide display order.** Unlike the cascades, these descriptors
carry an explicit `order`: nobody reads a delete confirmation for its column ordering, and
everybody reads a table heading. Inheriting the order from `INSTALLED_APPS` would mean moving an
app in the settings file silently reshuffles a page.

### ADD/REMOVE, never a submitted whole set

`set_for` takes `add` and `remove`, and the transfer panel posts exactly that: one form spans
both panes, and the button pressed (`op=add`/`op=remove`) says which side to honour. **A whole
wanted set clobbers, by construction.** Two administrators with the page open each hold a
snapshot of the other's pane; whichever saves second ships the first one's stale state back and
silently reverts it. A difference cannot do that: it touches only the rows that were ticked.
Honouring only the pressed side is the second half of the same care — a browser submits every
checked box in the form, including a stray tick left in the pane the operator did not act on.

**Every write still goes through the column's OWN single writer.** `agents.labels.
set_tool_labels`, `set_agent_labels`, `set_flow_labels`, `models.registry.labels.attach` and
`detach` are unchanged and are the only things that touch the join tables — `agents/axes.py` and
`models/registry/axes.py` compute the transpose (one entitlement across many resources) as one
call per resource that really changes, and nothing else. Those writers are where the
`tool.labelled`/`agent.labelled`/`modelset.attached` audit rows are written, `/chat/access/`
reads that trail, and a second write path would have been a second place to forget it.

**A BATCH IS N TRANSACTIONS, NOT ONE, and that is a consequence of the sentence above rather
than an oversight.** Each of those single writers opens its own `transaction.atomic()` around
one resource's own label diff (`agents/labels.py::_set_labels`;
`models/registry/labels.py::attach`/`detach`), so ticking twenty rows and pressing **Add** is
twenty transactions, applied in a stable sorted order, one per resource that really changes. A
crash, a lost connection or a refusal part-way through therefore leaves a PARTIAL change — and
an honest audit trail for exactly the part that landed, which is what an operator re-reads to
find out where it stopped. Nothing is corrupted and no row is half-written; the batch is simply
not atomic as a batch.

**Making it atomic would cost the seam.** A single transaction spanning the whole batch means
either wrapping the writers from outside — which `identity/` may not do, because it may not
import them (rule 4) and `identity/axes.py` only ever resolves dotted paths — or a bulk write
that bypasses them, which is the second write path this whole design exists to prevent: the one
that eventually forgets an audit row. The trade was taken deliberately, in the operator's
favour: a partial change you can see in the trail beats a whole change written by a path nobody
audits. It is also the behaviour every label page on this box already has, where one save is one
resource. If a later round wants batch atomicity, the change is to the writers themselves — an
optional caller-supplied transaction — not to this seam.

### Axis editing is ADMINISTRATOR-only, on a class-R page

`identity-entitlement-edit` is the one class-R route in this column (§3), classified that way
precisely so an entitlement OWNER can reach it. **The transfer panels are not for them.** §4's
own reading of an owner's three capabilities — grant it, revoke it, label DOCUMENTS with it —
is what decides this: a tool, an agent, a flow and a model set are box inventory, not somebody's
library shelf, which is what `models/registry/labels.py` and `agents/chat/views/tools.py`
already say from their own side ("a tool label has no entitlement owner"). So
`identity.services.set_entitlement_axis` refuses a non-administrator outright, and
`identity/views.py` builds no panels for an owner at all — a page must not offer a control whose
POST answers "no". An owner's entitlement page is exactly what it was: reach, grants, no rename
and no delete. Both halves are pinned.

**Documents are counted from this direction and edited from the library's own page** — the one
axis registered without the editing trio. A document label is the one labelled kind an owner may
write; the library page owns that write with its own posture warnings; and a transfer panel
listing every document in a library that scales to thousands is the wrong control. The Documents
column exists on the list because a missing column reads as "labels no documents" rather than as
"ask the library".

**The reach panel stays the single home for counts on the detail page** (§4's own one-page-one-
answer decision). The transfer panels complement it; they do not restate it. The list page's
reach columns are a different granularity on purpose — per AXIS, which is the editing
granularity, where the reach panel is per CASCADE, which is the delete granularity (agents and
flows are two axes and one cascade, because a delete confirmation reads better as one line and
an editor reads worse as one pane holding two kinds of thing).

### The component, and the one script

The panel is `foundation/templates/_transfer_panel.html`: two fixed-height scroll boxes with
counted headings, a type-to-filter box on each, and honest empty states. **Fully server-rendered
and fully functional with JavaScript off** — every row, checkbox and button works; the script
only hides non-matching rows as you type. That script is the round-16 tags filter reused for a
THIRD time rather than copied, and so moved from `agents/chat/templates/chat/
_search_checklist_script.html` to `foundation/templates/_filter_rows_script.html`, where all four
columns may reach it; it was generalised to read the two selectors it filters by off each
input's own `data-filter-scope`/`data-filter-rows` attributes instead of hardcoding the chat
dropdown's private class names. Its two existing consumers are unchanged in behaviour. No client
storage, no document-delegated listener, no `<form target=>`. The panel's CSS lives in
`foundation/templates/_settings.html`, the deepest ancestor of its only consumer today; a
consumer outside the settings area promotes it one tier to `_shell.html` rather than copying it,
the same move `.messages`/`.msg` already made.

### Recorded thresholds, not gaps

- **The entitlements list does not paginate.** Fifty rows in one table is the case this round
  was asked for and is comfortable; the flat query count is what makes it safe, not the row
  count. A box that passes a few hundred wants pagination, and the search is what carries it
  until then.
- **The transfer panes do not virtualise.** A catalogue of a few hundred rows in a 16rem scroll
  box with a filter is fine; a catalogue of thousands is not, which is exactly the reason
  documents are counted rather than edited here.
- **Owner-managed workstream walls are not registered as an axis at all.** They are not a label
  an administrator applies to a resource; they are a stream owner's own scope, and the reach
  panel already names them.

### Phase 2 — the resource-major pages, and the library's door

**The four label pages were the OTHER projection all along, and they had the same defect from
the other side.** `/chat/tools/` and `/chat/access/` rendered one checkbox per entitlement on
every resource row — resources × entitlements checkboxes in one page, which stops being readable
long before the fifty entitlements the entitlement pages above were rebuilt to hold. Each row is
now a **collapsed `<details>`** whose summary states the truth in one line (the count, the
leading names, "+N more", or "No entitlements — everyone signed in"), expanding into the **same
transfer panel**, flipped: panes of entitlements for one resource, where the entitlement page
shows panes of resources for one entitlement. `<details>` is native, so the collapse costs no
script and both pages stay fully usable with JavaScript off.

**One component, not a second variant.** `foundation/templates/_transfer_panel.html` grew four
parameters rather than a copy — `tp_fields` (the hidden inputs naming the POST's target, built
by the view, which is the whole of the direction flip), `tp_anchor`, `tp_bare`, and
`tp_empty_catalogue` — and `tp_label` became optional, because the `<details>` summary already
names the row. Two directions, one file; a third consumer later is four parameters, not a fork.

**The POSTs keep their routes, their one-resource-per-post shape and their single writers.**
`parse_entitlement_post` became `parse_entitlement_diff`: the panel's `op` + `add`/`remove`
instead of a whole `entitlements` set, validated against the same `labelling_entitlements`
predicate the form renders from. The new set is derived from what the table holds NOW, so the
anti-clobber property this ADR records for the entitlement-major direction is now true in both.
A refusal lands back on the row being edited, exactly as a success does.

**`?open=<anchor>` re-opens the edited row.** A `<details>` closes on every reload, so an anchor
alone would be half a return on a page built for a run of edits. It is a query parameter for the
same reason `?assistant=1` is one: no script, no cookie, nothing stored on the client.

**The workstream scope editor deliberately keeps its chips.** It is not the same control wearing
an older shape: its catalogue is one principal's own holdings rather than the whole box's, it is
chosen once when a stream is created rather than edited in a run, and a wall is a different
question from a label. `agents/chat/tests/test_workstream_page.py`'s "settings-page parity"
docstring now names `chat/workstream_settings.html` as the parity it means, so it does not read
as a claim about the two access pages that have moved on.

**The library filter is the door phase 1 left as a count.** `rag-documents` takes
`?entitlement=<id>`, and it **composes with visibility rather than widening it**: the filter
narrows the queryset `listable_documents` already returned, so an id naming something the viewer
may not see yields the empty honest state, never a row. It is applied before every count, so the
shelf counts and the rows still come off one queryset. An unknown or non-numeric id matches
NOTHING rather than everything — a filter that silently showed the whole library back would tell
an operator who mistyped an id that the entitlement covers four hundred documents. The banner
names the entitlement only where this page would already have named it (`labelling_entitlements`,
the predicate its own "With selected…" card renders from), so it cannot become a way to read the
catalogue off a page that never offered it.

**The entitlement page's Documents section is a DOOR, registered like everything else, and
states no count.** The reach panel remains the single home for counts. The link carries no
assistant flag, deliberately: the panel's gate keys on the route being a settings CARD route and
`rag-documents` is not one, so the flag would be a parameter in the address bar of a page that
renders no panel — the identical reasoning recorded for `inference-server-scan`.

**`EntitlementAxis` gained an optional `link` for it, and that was a correction rather than a
feature.** The first version of this door was a hand-written `<section>` in
`identity/templates/identity/entitlement.html` plus a `reverse("rag-documents")` in
`identity/views.py` — **the only place in the whole of `identity/` that named another column's
route**, eleven lines below a comment promising that a sixth labelled kind would be "a
REGISTRATION in that column's own `apps.py`, not an edit to this view and not a new template
block". The claim was stated and false. A column that wants a door now registers a dotted path
returning its own URL, `identity/axes.py::axis_doors` resolves it at render time and never
swallows, and the column supplies the heading and the sentence too, so `identity/` composes a
door without knowing what is behind it. **Rule 4 has a second half — `identity/` names no other
column's ROUTE — and it is a gate now rather than a habit**
(`foundation/ops/tests/test_import_law.py::test_identity_names_no_other_columns_route`, which
parses Python with `ast` and strips template comments, so the rule's own documentation does not
trip it).

**A recorded threshold, which phase 2 otherwise lacked.** The collapse makes the access pages
READABLE; it does not make them SMALLER. Every entitlement still renders one checkbox per
resource row — in one pane or the other — so the node count is unchanged, and deliberately so:
rendering a pane lazily needs either JavaScript or a per-row route, and this design takes
neither. Measured on the tool page (15 tools): **45,113 bytes with no entitlements, 106,790 with
thirty** — roughly 450 checkboxes and 61KB added by the entitlements alone; at fifty, about 750
checkboxes. The threshold to revisit is resources × entitlements in the low thousands, and the
answer then is the one phase 1's own thresholds already name: paginate, or fetch the pane, not
shrink the row. Phase 1 records two (no list pagination, no pane virtualisation); this is the
third and belongs beside them.

**One gate earned its keep in between.** The element-selector check added to
`foundation/ops/tests/test_css_ownership.py` in phase 1's fix round found nothing at the time,
because the transfer panel had a single consumer and the S27 exemption skipped it. The moment
these two access pages became consumers two and three it went red on the real thing: the fragment
opened a `<section>`, and `identity/entitlement.html` styles bare `section` in its own block. The
fragment opens a `<div>` now.
