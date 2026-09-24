# models/registry/ — Model registry (Config & Secrets)

**Which engine + model backs each role, right now, and is that still true after a swap.**

This app implements the [§4 Config & Secrets](../../docs/ARCHITECTURE.md#4-the-core-services)
core service's model registry, plus the ACCESS console UI (`views.py`) operators use to
manage it: see installed models and what each role needs, bind a connection to a role, and trigger
re-materialization when a binding drifts.

A future feature setup (image generation, home automation, etc.) needs nothing new from this app
to plug in: enabling a feature registers its roles, each role declares its own capability need, and
the "Getting models" checklist plus the binding flow above already derive entirely from
`all_roles()` — see [ADR 0010](../../docs/adr/0010-model-management-framework.md)'s 2026-08-22
amendment.

## Not a tool

This app is **trusted platform code**, not a
[§5 module](../../docs/ARCHITECTURE.md#5-the-module-contract) (the P0 regroup's
column terms call this a **tool**; see [`models/README.md`](../README.md)'s
import law). It sits in the `models/` column alongside `models/contracts/`,
not in `tools/` alongside `tools/rag/`:

- No manifest, no capability grants, no `network: none` sandbox — this code runs with the
  platform's own trust, the same as `models/contracts/`.
- `models/contracts/` still never imports `models/registry/` at module scope (see
  `models/contracts/bindings.py`'s docstring) — the only coupling is the dotted-path
  `settings.INFERENCE_BINDING_PROVIDER` string, resolved lazily per call so
  `models/contracts/` stays pure and importable standalone.
- A `tools/*` app (like `tools/rag/`) never imports `models/registry/` either; it only ever
  calls `models.contracts.gateway`/`bindings`, which is what keeps a tool portable and
  sandboxable independent of whatever operator tooling the platform happens to ship.

## The three scaling seams

This app is the DB-backed half of the [§3 plug-and-play](../../docs/ARCHITECTURE.md#3-plug-and-play-how-we-avoid-betting-on-hardware)
abstractions — the reason a module's code never has to change as hardware or models improve:

- **Accelerator** — out of scope here; the engine layer (`models/contracts/engines/`) is where
  CPU/CUDA/Metal/ROCm/NPU selection would live.
- **Inference engine** — `ModelConnection.engine` (e.g. `"ollama"`) picks which
  `models.contracts.engines` adapter serves a connection; swapping engines is a registry edit,
  not a code change.
- **Model registry** — this app, literally: `ModelConnection` rows are the registry §3
  describes ("models declared in config, not hardcoded"); `RoleBinding` is which registry
  entry a role currently points at.

## Data model

- **`ModelConnection`** — a concrete, reachable engine+model an operator has registered:
  `name` (operator label, CI-unique like `tools.rag.models.Category.name`), `engine`
  (default `"ollama"`), `endpoint`, `model_id`, `capabilities` (list drawn from
  `models.contracts.roles.CAPABILITIES`), `embed_dim` (nullable — only embeddings connections set
  it), `context_window` (nullable positive integer — an optional cap on the KV cache the
  engine allocates for this connection; unset means the engine adapter's own bounded
  default applies, see ADR 0010's context-window amendment), `config` (nullable JSON —
  see below), `descriptor` (free-text, ≤80 chars, blank by default — whatever wording helps
  the operator tell their models apart, self-assigned, never auto-filled or suggested),
  `rank` (nullable positive integer — lower sorts first in every picker; see
  `ModelConnectionQuerySet.picker_order`).
- **`RoleBinding`** — which `ModelConnection` backs a role key (e.g. `"rag.answer"`) right now.
  `role_key` is CI-unique (one binding per role); `connection` is nullable with `SET_NULL` so
  deleting a `ModelConnection` un-binds the role instead of deleting the binding or blocking
  the delete.
- **`Materialization`** — the fingerprint a role was last materialized (e.g. re-embedded)
  against, for the re-encode guard below.
- **`ModelSet`** / **`ModelSetMember`** / **`ModelSetEntitlement`** (IA-2 T14, spec §6.10) —
  a named group of connections (`ModelSet`, CI-unique by name), the edge that maps a
  connection INTO a set (`ModelSetMember`), and the edge that attaches an entitlement TO a
  set (`ModelSetEntitlement`). Two edges, edited independently: adding a model to a set is
  one row and reaches every entitlement already attached to it; attaching a second
  entitlement is one row and reaches every model already in the set. A connection in **no**
  set is usable by everyone who may use its capability; a set with **no** entitlement
  attached restricts **nothing** — both are the opt-in, zero-cost default, not an edge case.

## Model access (IA-2 T14)

`models/registry/access.py::model_access_for(principal)` answers "which registered
connections may `principal` USE" as a `ModelAccess(required, held, unrestricted)` value —
`UNRESTRICTED_MODEL_ACCESS` is the zero-argument, allow-everything default an open box (or a
box with no sets at all) gets for free, with no permission query run. It is re-exported from
`models.registry.bindings` (never imported from `.access` outside this package) because
`bindings` is the one submodule of this app `agents/` may import at all.

`access` is a **required, keyword-only** argument on `bindings.picker_options`,
`.resolve_connection` and `.resolve_connection_named` — every user-selectable model choice on
the box goes through one of the two: `picker_options` drops a connection this principal may
not use from the render, and `resolve_connection_named` refuses the same connection with a
`ValueError` if a caller submits its pk directly, so the render half and the accept half of
one seam can never disagree.

**THE ROLE PATH IS EXEMPT, ON PURPOSE — read this before assuming the opposite.** `db_provider`
and `role_primary` (this file's own "binding-resolution chain" above) take **no** `access`
parameter at all, not even an unrestricted default: a model resolved through a ROLE key
(`rag.embed`, `rag.transcribe`, `rag.extract`, the default answering binding, …) is never
narrowed by a model set, because none of those roles has a principal to check one against.
Putting the embedding connection in a restricted set must not break ingestion for the whole
box — that is the one property this exemption exists to protect (spec §9.5/§22.32).

## The binding-resolution chain (db → env)

`models.contracts.bindings.resolve(role_key)` is the one function anything (a module, the
gateway) calls to answer "which model backs this role". It tries
`settings.INFERENCE_BINDING_PROVIDER` first, falling back to `env_provider`
(`LLM_MODEL`/`EMBED_MODEL`/`OLLAMA_BASE_URL`) if that provider has no opinion:

```
resolve(role_key)
  │
  ├─▶ INFERENCE_BINDING_PROVIDER (default: models.registry.bindings.db_provider)
  │     look up RoleBinding.role_key__iexact -> ModelConnection
  │     no row / no connection / table not migrated yet ─▶ None
  │
  └─▶ (None) ─▶ env_provider  (LLM_MODEL / EMBED_MODEL / OLLAMA_BASE_URL, settings-only)
```

`db_provider` (this app's `bindings.py`) is the "db" half. It is deliberately forgiving:
`ProgrammingError`/`OperationalError` (the `inference_rolebinding` table not existing yet,
e.g. mid-`migrate` before this app's own migrations have run) is swallowed and treated the
same as "no opinion" — `None` — never a hard failure. That is also why the default in
`config/settings.py` is safe: a fresh install with zero `RoleBinding` rows still resolves
every role purely from env, exactly as it did before this app existed. Setting
`INFERENCE_BINDING_PROVIDER=""` explicitly (env var, not code) opts a deployment out of the
console entirely, back to pure `env_provider`.

## The availability signal (UI-1)

`availability.py` answers a different, much smaller question than `bindings.py` does:
**which roles have a model bound at all**, as a `frozenset` of lowercased role keys. The
shared shell and the landing page gate their entries on it, so a box with nothing bound
offers no dead ends.

**Bound by the DB *or* by the environment.** `resolve()` is a db → env chain, so a role an
operator pinned with `LLM_MODEL`/`EMBED_MODEL` is live even with no `RoleBinding` row — and the
Models page already reports it as bound. `availability.py` answers only the DB half (the
half that costs a query and therefore needs a cache); `context_processors.availability` unions
the two env-resolvable roles in off `settings`, which costs nothing and deliberately stays out
of the cached set so a settings change applies on the next render rather than after a TTL.

**Bound, never reachable.** No engine is resolved, no health check is run, no endpoint is
dialled — a nav bar that vanished whenever a workstation went to sleep would be a worse lie
than one that offers a page which then says, in its own words, that the model is
unreachable. Every page behind these entries already carries that second answer.

**Cached, because it renders on every page.** One `values_list("role_key")` per process,
invalidated on `RoleBinding` `post_save`/`post_delete` (and on `ModelConnection`
`post_delete`, because `SET_NULL` unbinds roles with a bulk `UPDATE` that emits no
`post_save`), with a 30-second TTL so a *second* web worker — which never saw the write —
converges by itself. Invalidation is registered in `apps.py::ready()` and runs
`on_commit`, so an uncommitted binding never clears the cache.

**The cache is bypassed inside a transaction**, in both directions: a set read inside an
open transaction may be rolled back, and a transaction that has just written a binding
would otherwise render a nav disagreeing with what it just saved.

`context_processors.availability` turns that set into `surface_available`, the derived map
templates read — so the composite rules ("Ask needs the answer role *and* the embedding
role"; "Images needs the vision feature *and* the generate role") live in one place instead
of being retyped in the shell and the landing page.

## The footprint ladder, and the recorder that keeps the maximum

A connection row stores **three** footprint facts, and they are three columns rather than
one on purpose. The execution queue (`models/queue/`) only ever READS them, through
`bindings.footprint_for`, resolved fresh on every planning round.

- **`footprint_override_bytes`** — the operator's own word. Always wins, never written by
  anything but an operator, and the documented correction path for every case below.
- **`measured_footprint_bytes`** / **`measured_footprint_at`** — what the queue observed
  after a run it executed, written opportunistically by the worker.
- **`engine_reported_footprint_bytes`** / **`engine_reported_footprint_at`** — what an
  engine said a loaded copy occupied, harvested from the residency snapshot the worker is
  taking anyway. **Never a call made for this purpose**, and never written from a missing
  or zero reading — a stored zero reads back as a real "this model is free" answer.

`effective_footprint_bytes` walks those in order and then `None` ("unknown", which makes
the job holding that model run alone). It walks on `is not None`, never on truthiness: a
genuine zero is a value.

**Why measured and engine-reported are separate columns.** They are facts of different
quality — ours-after-a-run versus the engine's-while-loaded — the recorder rule below
compares a reading only against its OWN standing value, and the console's label would be
untrue again the moment one column had to answer for both.

**The recorder keeps the maximum.** Both writes go through one function,
`bindings._record_footprint`. No standing value: write. A reading greater than or equal to
the standing value: write. A reading **below** it: refused outright — not the bytes, not
the timestamp — with one line naming the connection, the standing value and the refused
reading.

**There is deliberately no tolerance band**, and the reasoning is the rule rather than a
simplification. A percentage tolerance measured against the standing value ratchets
geometrically: five successive "within tolerance" writes at 0.75× walk 26.4 GB down to
6.2 GB, reproducing the exact incident the rule exists to refuse — and the condition is
RECURRING (a model that under-measures whenever it is warm), so repeated warm runs are the
normal path rather than an adversarial one. Keeping the maximum makes the standing value a
high-water mark by construction, with no extra column and no constant to tune.
`FOOTPRINT_DIP_WARNING_RATIO` (0.75) sets the LOG LEVEL only — a refusal under three
quarters of the standing value is a WARNING naming the remedy, a smaller dip is INFO — and
no reading below the standing value is written at any ratio. The accepted cost: a genuinely
shrunk model stays high until an operator sets an override, which is the safe direction.

### Two label vocabularies, and why they must stay apart

`ModelConnection.footprint_source` answers `"override"` / `"measured"` /
`"engine_reported"` / `None`, keyed to `views._FOOTPRINT_SOURCE_LABELS`:

| Value | Rendered |
|---|---|
| `"override"` | set by the operator |
| `"measured"` | measured after a run |
| `"engine_reported"` | from the engine's residency snapshot |
| `None` | not measured yet — this model runs alone |

It is **not** keyed to `views._SOURCE_LABELS`, which is and stays the vocabulary for
`DiscoveryRow.capability_source`. The two look interchangeable and are not: "detected from
the model server" is true of a capability probe and a **lie** about a post-run delta
measurement this platform took itself. Before the four-rung ladder, the footprint row
borrowed that map and said exactly that lie; separating them is what makes the label
honest, and folding them back together would re-introduce it.

The label is resolved in the view (`views._footprint_source_label`), not in the template.
The template used to key a raw map by hand beside a value it had chosen with its own
`{% if %}` — two independent decisions about the same row, free to disagree. The date is
localized (`timezone.localtime`) before formatting, because `date_format` alone does not
and the template's own `|date` filter does; on a non-UTC box the difference is a visibly
wrong day.

### `registered_endpoints()` — one notion of "every engine endpoint"

`bindings.registered_endpoints()` answers `(engine, normalized endpoint, model_ids
registered there)` for every endpoint this box knows about: the **union** of the configured
defaults (`settings.INFERENCE_DEFAULT_ENDPOINTS`) and every connection row's endpoint, per
engine. Deriving it from connection rows alone would miss a second engine running at its
configured address with no registered connection yet.

Both the console's own per-engine discovery map and the execution queue's cross-engine
eviction sweep read it, so a freshly configured engine cannot be visible to one and
invisible to the other. The third element is not decoration: an unload call has to be
addressed with SOME `model_id`, so a configured endpoint with no connection row yields an
empty tuple and simply cannot be addressed — a named residual, never papered over with a
synthetic id. The optional `connections` argument takes the caller's already-fetched rows,
so the console page (pinned at a fixed query count) does not pay a query for it.

## The re-encode guard

Binding a role to a different `ModelConnection` (a different model, or the same model at a
different `embed_dim`) does not by itself touch any data already computed under the old
binding — a `rag.embed` rebind, in particular, leaves every previously-embedded chunk in
`pgvector` computed against the *old* embedding space, silently. `Materialization` is the
guard against serving stale data under a new binding without anyone noticing:

- Each `ResolvedModel` carries a `.fingerprint` (`f"{engine}:{model_id}:{embed_dim}"`) — a
  cheap identity string for "what a role is currently bound to".
- `Materialization.fingerprint` records what a role was last *actually materialized* against
  (e.g. the fingerprint in effect the last time `rag.embed`'s documents were re-encoded).
- A role whose **active** fingerprint (`resolve(role).fingerprint`) no longer matches its
  **stored** `Materialization.fingerprint` has drifted — its data was computed for a binding
  that is no longer in effect.

This app owns the `Materialization` table; comparing the two fingerprints and driving the
actual re-encode (`tools.rag.services.reencode_all`, run via a role's
`RoleSpec.rematerialize` dotted path) is `models/registry/drift.py`.

## Finding your model server

The console health-checks the ONE configured/resolved endpoint on page load and discovers
against the endpoints already known for each registered engine (see the per-engine map
below) — nothing beyond those is probed automatically, cold or warm. If that address is
wrong for this install (a different host, a non-default port), the "Scan for model servers"
button (`models.registry.discovery.scan_for_servers`) runs a short, engine-agnostic sweep of
each registered engine's own well-known addresses — `COMMON_HOSTS`
(`localhost`/`127.0.0.1`/`host.docker.internal`) plus the engine's own registry name as a
compose-service hostname, crossed with that engine's declared `well_known_ports` — and ONLY
when clicked. Each engine (`models.contracts.engines.InferenceEngine.well_known_ports`) declares
its own detection surface; the scan loop itself (`models/registry/discovery.py`) names no
engine, no port, no path, so a newly registered engine is discoverable with zero changes to the
scan. A hit's "Use this endpoint" link reloads the console pointed at that server
(`?endpoint=`); registering a found model from there ("Add to registered") persists that
endpoint on the created `ModelConnection` the same way any registration does — no new state,
no auto-adoption, no LAN scanning or port ranges beyond what each engine declares.

**The `?endpoint=` override is route-gated and allowlisted** (S5/S19). Every `/inference/*` route
is admin-only (`identity.routes.ROUTE_RULES`, tier `S`), so `IdentityGateMiddleware` already
answers 403 for a signed-in non-admin in `personal`/`enterprise` before `_requested_override`
(`views.py`) ever runs — the in-view `identity.access.is_admin` check there is defence in depth,
not the primary gate, for that case. On an `open` box there is no route gate at all (`is_admin`
answers True for `OPEN_PRINCIPAL` by design), so `_override_is_permitted` is what actually closes
this on the posture that ships by default: the requested `(scheme, host, port)` triple must exactly
match a `ModelConnection` already registered here or a configured default engine endpoint, or the
host must be this box's own loopback interface (`localhost`, `127.0.0.0/8`, `::1` — reachable by
design, no registration needed). A private, unregistered LAN address is refused exactly like a
public one — there is deliberately no "any private address" carve-out; register the connection
instead. Refused values fall back to the default endpoint silently, exactly like a malformed value
always has. Without this, `?endpoint=` accepted any http(s) URL, and the console's health check +
`discover()` + catalogue calls (~10 outbound requests per render, with a reachability oracle
rendered back on the page) would fire at whatever host a query string named — reachable from a
plain `<img>` tag on any page a LAN user visits, no CSRF token required. `connection_add` and
`machine_model_add` validate a *stored* endpoint with the same syntax check
(`_endpoint_is_well_formed`) but never the allowlist — an operator deliberately registering a
connection on a routable host is a different question from a query string steering a probe.

Health and discovery answers are cached process-locally for 30 seconds (`models.registry.
probe_cache`, the same TTL pattern `models.registry.availability` already runs over the
role-binding read). Without it, every console GET *and* every mutating POST (which redirects
straight back into a fresh page render) re-probed each registered engine over HTTP — three
role edits cost six round trips to one model server. The cache is dropped the instant a
`RoleBinding` or `ModelConnection` row changes (create, update, or delete), so an operator's
own edit is never read back through a stale answer; a second web/worker process converges on
its own once the 30 seconds pass. `/setup/`'s own reachability line calls each engine's
`is_healthy()` directly rather than through this console's `_check_health`, so it is
unaffected by this cache and always reports the current instant.

### Per-engine discovery endpoints and connection config

- `discover()` takes a **per-engine endpoint map**, not one endpoint: each registered adapter
  is polled at its own default (`settings.INFERENCE_DEFAULT_ENDPOINTS`), at every registered
  connection's endpoint for that engine, and at whatever endpoint the page is currently
  viewing (`models.registry.views._engine_endpoints` builds the map). Before this, every
  engine was polled at the single Ollama endpoint, so a second engine's installed models could
  never appear. A `DiscoveryRow` carries the `endpoint` it was seen at — the FIRST one, when the
  same model id turns up at several endpoints of one engine (only `loaded` is unioned across
  them) — and both "Add to registered" and the manual-form link register against that address
  rather than whichever one the page happens to be viewing.
- `ModelConnection.config` (nullable JSON) rides through `db_provider` into
  `ResolvedModel.config` (`{}` when null) and is splatted into the engine's builder — the seam
  multi-file model families (UNet + CLIPs + VAE) use (see "Registering a multi-file model
  family" below), added with no later schema change.

## Registering a multi-file model family

Some image models are not one file. A diffusion-model file (`.gguf`, or a `.safetensors` the
native UNet loader reports) is half a pipeline: it needs a text encoder and a VAE named
alongside it, and it needs its model family declared.

The family cannot be detected. Nothing ComfyUI serves over HTTP reports what architecture a
weights file is, and this platform never infers one from a filename — so the operator declares
it, from a `<select>` whose options are the engine's OWN family vocabulary (`CLIPLoader.type`)
narrowed to the families the adapter has a graph template for. The encoder and VAE are picked
the same way, from what the engine reports having installed.

Some families also have a **variant** — a second graph for the SAME family, for weights
distilled differently than the family's ordinary build (ADR 0012 D-EDIT-6). Unlike family,
this is not detectable EITHER, and unlike family it is not even an engine vocabulary: ComfyUI
has no word for it at all. The `<select>` options come straight from the adapter's own template
package (`ComfyUIEngine.list_variants()`) — one list, not one per family, because this form has
not chosen a family yet — and a pairing that does not exist (the wrong variant for a family, or
a variant with no family at all) simply builds that family's ordinary graph rather than
rejecting the submission; nothing here is validated against the engine's live templates any
more than family is (see "Honest note at bind time" below).

All four land in `ModelConnection.config` (the JSONField ADR 0012 D8 added for exactly this)
and reach the graph template as `**ResolvedModel.config`. Leave all four blank for a
self-contained checkpoint.

### Which address those lists are read from, and what an empty one means

The console targets ONE endpoint at a time, and on a box with nothing registered yet that
address is the chat role's default — not the image engine's. So the family and companion
lists are **not** read from the page's endpoint: `views._family_endpoint` asks the
family-declaring adapter at its OWN address, taking the first of an active `?endpoint=`
override (an operator who explicitly steered this page means that address), any registered
connection's endpoint for that engine, that engine's configured default from
`settings.INFERENCE_DEFAULT_ENDPOINTS`, and finally the page's endpoint. No engine name is
written anywhere in that chain — the adapter's own `.name` keys it — and every candidate is
an address this box already probes, so nothing widens what the console can be steered into
reaching (see "Finding your model server" above). It is a ranked pick of ONE address rather
than `_engine_endpoints`' per-engine bucket: discovery polls every address it knows and
unions the answers, while these `<select>`s want a single authority, and the ranking — which
`_engine_endpoints` deliberately has no notion of — is the whole point.

Asking the page's endpoint instead was the fresh-registry onboarding gap: a brand-new
operator, with no scan row to click and so no `?endpoint=` in the URL, never saw these fields
at all.

Those three reads go over HTTP to an address this page does *not* health-check, so they are
cached the same way `discover()` is (`probe_cache`, 30s, keyed on the endpoint and the kind,
dropped by the same connection-write signal). Without that, a `COMFYUI_BASE_URL` naming a
host that blackholes would cost three full timeouts on every console render, forever — the
adapter memoizes successful `/object_info` reads only, never a failure. The variant list is
not cached because it never leaves the process.

An empty list is now an empty state, not a missing form. The fieldset renders whenever a
registered adapter declares a family vocabulary AT ALL — which, since the image adapter
registers unconditionally at import, means **every shipped install renders it**, chat-only
boxes included, inside the collapsed "Add a connection manually" disclosure. (The "no such
adapter" branch is for a build whose adapter set was cut down; no configuration this
repository ships is one.) An operator who only ever wants chat will therefore find a
"Multi-file model" fieldset and its empty state one disclosure away — a deliberate trade for
the operator who needs it and previously had no way to reach it at all.

The empty state says that server **reported none** and names both possibilities, rather than
announcing an outage: `_engine_options` swallows every exception, so an empty list means
either the server did not answer or it answered and the intersection with this platform's
runnable templates was empty. The second needs no outage at all, and telling a running
server's operator to go restart it would be the same conflation this section exists to
describe, one layer up.

Two more consequences worth stating: the variant `<select>` stays usable while the engine is
down, because that list is read offline from the adapter's own template package; and each
`<select>` carries a stored-but-unlisted value as its own selected option, because an
always-rendered fieldset always posts `family` — without that, saving an unrelated edit
against a down engine would silently erase the declaration. (That guard also closes a
pre-existing erasure: a *healthy* engine whose vocabulary no longer lists a stored value —
an upgraded adapter, a removed template — used to render the select without it, so any save
wiped the declaration.)

**Open, and a steward decision, not this column's:** the empty state still cannot be filled
while the engine is down. Families could be offered offline — the adapter already knows which
templates it can run — but that means a new optional member on the engine protocol
(`models/contracts/engines/base.py`) and its implementation in the adapter, which is outside
`models/registry`. Recorded here rather than closed unilaterally.

The "Add to registered" button on an "On this machine" row is detection-only, so it refuses a
diffusion-model file and points at this form — it cannot supply the facts detection does not
have.

An already-registered connection declares or changes these later too, from its own Edit
disclosure (`_connection_edit.html`) — the same `inference/_family_fields.html` partial, the
same option lists, the same four field names, reused rather than copied. A submission that
never mentions the group at all (an older caller, never either rendered form) leaves the
stored config untouched; one that does, blank or not, replaces it — the Edit form always posts
`family` once these fields exist, so an unrelated edit (the descriptor, the rank) round-trips
whatever family/variant the connection already had.

### Honest note at bind time

Registering a connection never validates the declared family against the engine's live graph
templates (ADR 0012 D-EDIT-2 — storing a family this adapter has no graph for is not a lie,
`supported_operations` honestly reports no operations for it). But a role's "change" dropdown
binding that connection is a different moment — the operator has just committed to using it —
so `role_assign` adds one more honest, **non-blocking** check there:

- a connection whose declared family has no graph template for any operation yet, or
- a connection with NO declared family whose model is reported by a loader other than the
  self-contained one (a diffusion-model file quietly left unconfigured)

both get a `messages.warning` alongside the normal "Assigned" success message, pointing back at
this form. The bind itself always applies — the note is purely informational, and any failure
reading the engine's live facts (unreachable server, an adapter with no `supported_operations`
member) degrades to no note at all rather than a 500.

## Tools

`models/registry/tools.py` registers one `agents.contracts.tools.ToolSpec`,
`models.status`, in `InferenceConfig.ready()`, unconditionally. This is a
different sense of "tool" than the "## Not a tool" section above — that
section is about the [§5 module](../../docs/ARCHITECTURE.md#5-the-module-contract)
sandboxed-plugin sense; this is the P1 agent tool contract
(`agents/contracts/tools.py`'s `ToolSpec`/`ToolResult`/`register_tool`),
which any `models/`- or `tools/`-column app may register into. Without
`models.status`, `models/` would be the one column that adopts the
contract nowhere at all — the tool's whole reason for existing
(ruling R2).

`run_status` reports, for every registered role (`models.contracts.roles.
all_roles()`), whatever `models.registry.bindings.role_primary(role_key)`
says currently answers it — a bound connection's name and pk, an explicit
environment-override name with no pk, or genuinely unassigned — without
collapsing any of those three honest states. It is **DB-only**: it makes
no health probe of an engine and no engine call of any kind. Reachability
is a live network fact with unbounded cost, and its latency would land
inside a turn already holding the machine's one execution slot; `/setup/`
is the surface that answers "is the engine up", on demand, for a human,
outside the queue. If a role is bound but its engine is down, the agent
finds out the honest way: the tool that actually needs the model fails
and surfaces the error.

`models.status` declares `roles=()` (it consumes no model itself),
`params=()` (any argument is rejected, not ignored), and `mutates=False`
(it is read-only and grantable). `models.registry.bindings` is an
in-column import here and crosses no import-law boundary — the other
half of why this tool lives in this column rather than being bolted onto
`agents/`.
