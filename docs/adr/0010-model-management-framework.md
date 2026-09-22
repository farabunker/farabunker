# ADR 0010 — Model management framework

**Status:** Accepted
**Date:** 2026-08-20
**Names generalised:** 2026-09-14 — illustrative model names in this ADR's prose were
replaced with capability language ahead of publication; the decision, its date, its options
and its consequences are unchanged.

## Context

The Wave 1 walking skeleton picked models purely by env var
(`LLM_MODEL`/`EMBED_MODEL`/`OLLAMA_BASE_URL`, read once by
`core/inference/gateway.py`). That satisfied Phase 1's "config-driven
models" exit criterion, but left real gaps once the platform needs an
operator-facing model registry (ARCHITECTURE §3c, §4):

1. There was no way to change a model without editing `.env` and restarting
   — no hot-swap, no UI, no record of what's actually installed on the
   engine versus merely configured.
2. There was exactly one chat purpose and one embeddings purpose, both
   implicit in `gateway.py`'s function signatures. A future feature that
   needs its own model (e.g. a vision purpose for image description) had no
   place to declare that need without hardcoding another model name.
3. Nothing detected when an embeddings rebind silently invalidated already-
   encoded data: swapping `EMBED_MODEL` mid-life left every previously
   embedded chunk in `pgvector` computed against the *old* embedding space,
   with no signal that a re-encode was needed.
4. There was no place for operator-facing management UI (browse available
   models, bind a role, watch health) to live that was clearly distinct from
   a sandboxed §5 module — this UI has to *manage* the platform's own
   inference config, which a capability-scoped module must never be trusted
   to do.

This ADR records the framework built to close those gaps: engines-as-code /
models-as-data, a role registry feature apps declare against, a DB-backed
binding provider with an env fallback, and a generic drift/re-encode guard.
It also settles where the operator-facing console code lives relative to
the §5 module contract, and records what's deliberately deferred to Phase 2.

## Decision

### 1. The framework: engines-as-code, models-as-data

- **Engines are code.** Each inference engine (Ollama today; vLLM /
  llama.cpp / other later) implements the `InferenceEngine` Protocol
  (`core/inference/engines/base.py`): `is_healthy`, `list_installed`,
  `build_llm`, `build_embedder`. Engines are stateless — `endpoint` is
  passed in per call, never read from Django settings — so one adapter
  instance serves every connection using that engine and is trivial to unit
  test without a live server. `core/inference/engines/ollama.py` is the one
  v1 implementation; adding a second engine is one new file plus a
  `register()` call.
- **Models are data**, resolved per **role**. A role is a named,
  model-consuming purpose a feature app declares
  (`core/inference/roles.py::RoleSpec(key, label, capability,
  rematerialize=None)`) — e.g. `rag.answer` (chat) or `rag.embed`
  (embeddings). `register_role()`/`all_roles()`/`get_role()` are a pure,
  in-memory, module-level registry: no Django, no DB, so a `RoleSpec` stays
  trivially serializable and `core/` stays free of any dependency on
  `modules/` or `console/`. Feature apps register their roles at import
  time — e.g. `modules/rag/apps.py::RagConfig.ready()` registers
  `rag.answer` and `rag.embed` (the latter with
  `rematerialize="modules.rag.services.reencode_all"`).
- **`INFERENCE_BINDING_PROVIDER` + `env_provider` fallback chain.**
  `core/inference/bindings.py::resolve(role_key)` is the one function
  anything (a module, the gateway) calls to answer "which engine + model
  backs this role right now?": it returns a `ResolvedModel(engine, model_id,
  endpoint, embed_dim, config)`. If `settings.INFERENCE_BINDING_PROVIDER`
  (a dotted-path string, default `console.inference.bindings.db_provider`)
  is set, it is imported fresh on *every* call — not cached at module level
  — and tried first; if it returns `None` (no opinion: no binding row, an
  unbound connection, or the table not migrated yet) or the setting is the
  empty string, `resolve()` falls back to `env_provider`, the original
  settings-only path (`LLM_MODEL`/`EMBED_MODEL`/`OLLAMA_BASE_URL`). Nothing
  resolving the role raises `ValueError` naming it. The lazy per-call import
  is what keeps `core/` pure — it never imports `console/` at module scope;
  the only coupling is this dotted-path string, and tests can flip it
  mid-run.
- **The generic `Materialization` provenance table + `ResolvedModel.fingerprint`.**
  `ResolvedModel.fingerprint` (`f"{engine}:{model_id}:{embed_dim}"`) is a
  cheap identity string for "what a role is bound to right now."
  `console/inference/models.py::Materialization` records, per role, the
  fingerprint its data was last actually materialized against (e.g. last
  re-embedded with). It is generic — keyed by `role_key`, not by RAG
  specifically — so any future role with "stale-able" data reuses the same
  table without a schema change.
- **Dotted-path `rematerialize`.** `RoleSpec.rematerialize` is a string
  (e.g. `"modules.rag.services.reencode_all"`), never a live callable, so a
  `RoleSpec` stays plain data. `console/inference/drift.py::run_rematerialize`
  resolves it lazily via `import_string` only at the moment a re-encode is
  actually triggered — matching `resolve()`'s own lazy-import convention,
  and avoiding an import cycle (`reencode_all` reaches back into
  `modules/rag/ingest.py`, which imports its sibling `index`/`store`
  modules and, transitively, the app graph).
- **The two-severity embed-change invariant.** Rebinding an
  embeddings-capability role is classified by whether `embed_dim` changed
  between the prior materialization and the new binding:
  - **Dimension change ⇒ full document-store rebuild.** The vector
    column's width no longer matches what the new model produces; existing
    rows are structurally incompatible, not just semantically stale.
  - **Same dimension ⇒ re-encode in place.** The column width is still
    correct; only the vector *values* need recomputing against the new
    model, document by document, without touching the schema.
  This drives both the console's confirm-before-rebind copy
  (`console/inference/views.py::_severity_copy`) and the drift banner shown
  once a role has actually drifted (`_drift_severity`, comparing the active
  fingerprint's trailing `embed_dim` segment against the stored one via
  `rsplit(":", 1)` — `model_id` may itself contain colons, e.g. a real
  engine-reported id shaped like `"<vendor>/<name>:<size>"`).

### 2. Platform apps vs. modules

- `console/` hosts **operator-facing platform management**: the model
  registry now (`console/inference/`), posture and airlock consoles in
  Phase 2.
- Platform apps implement CORE services (§4 Config & Secrets: the model
  registry lives here) and ACCESS surfaces (the `/inference/` console UI).
  They are **trusted platform code** — no manifest, no capability grants,
  no `network: none` sandbox — running at the same trust level as `core/`
  itself, explicitly *outside* the §5 module contract. This is necessary,
  not incidental: an app that manages the platform's own inference
  configuration cannot itself be sandboxed away from what it manages.
  `core/` still never imports `console/` at module scope (only the
  dotted-path `INFERENCE_BINDING_PROVIDER` string couples them), and a §5
  module never imports `console/` either — a module only ever calls
  `core.inference.gateway`/`bindings`, which is what keeps it portable and
  sandboxable independent of whatever operator tooling the platform ships.
- `modules/` remains reserved for capability-scoped feature services (RAG
  today, home automation later) — sandboxed, manifest-declared,
  WAN-denied, granted only the core capabilities they explicitly request.
- **Why not `platform/`:** it shadows the Python standard library's
  `platform` module. `import platform` anywhere in this codebase would
  become ambiguous (or require every such import to guard against
  resolving to our package instead of the stdlib one), a footgun for zero
  naming benefit. `console/` was free of that collision and already reads
  naturally as "the operator's console into the box."

### 3. Capability vocabulary

- Role capability strings (`RoleSpec.capability`,
  `core.inference.roles.CAPABILITIES`) **are** the manifest vocabulary
  ARCHITECTURE §5 illustrates in its `module.yaml` example
  (`inference:chat`, `inference:embeddings`) — the same `"chat"` /
  `"embeddings"` words, just declared in code today instead of YAML.
- This ADR adds **`"vision"`** to that vocabulary
  (`CAPABILITIES = {"chat", "embeddings", "vision"}`), ahead of any role
  using it. The catalog (`core/inference/catalog.py`) already ships two
  vision-capability enrichment entries
  and the console's "Getting models" checklist derives a row per
  registered capability automatically, so the first feature to register a
  `vision.*` role (see the plan's Next Steps §1 — out of scope for this
  ADR) needs zero framework changes.

### 4. Manifest precursor

- Role registration in `ready()` — e.g. `RagConfig.ready()` calling
  `register_role(RoleSpec(...))` — is the **interim, code-side form** of
  the future §5 manifest's `capabilities:` declaration. It exists because
  the manifest loader doesn't exist yet; it is not a permanent parallel
  mechanism.
- When the manifest loader lands (Phase 2), it becomes the registrar
  reading each module's `module.yaml` `capabilities:` list, and `ready()`-
  based role registration is deleted — with **no change** to
  `core/inference/roles.py` itself. `RoleSpec` was deliberately kept
  dataclass-plain (no Django dependency, no live callables, only dotted-path
  strings) specifically so this migration is a no-op for the registry's own
  code; only the *caller* of `register_role()` changes.

### 5. Deferred / Phase 2 — model ingress & signing

- `ollama pull ...` — surfaced today as an install *shape* (each engine
  adapter's `install_cmd_template`, e.g. `ollama pull <model-name>`, in
  the console's "Getting models" section; the earlier per-model
  `CatalogEntry.install_cmd` copy-paste commands were removed with the
  starter cards, which pinned model names that age) — is a
  **development-posture convenience**, not the production ingress path.
- In bunker postures (§2: `airgap`, `isolated-lan`, `gated-sync`), models
  must enter as **signed, content-addressed airlock packages** (§3c: "models
  are content-addressed and signed so the airlock can verify them"; §6 the
  update airlock), the same controlled path as any other update artifact.
- `CatalogEntry.digest` (present in the dataclass today, unused — `None`
  for every v1 entry) is the future carrier of the trusted content digest a
  signed model package would ship with, for the airlock to verify before
  activation.
- The offline invariant holds regardless of posture: **the platform itself
  never pulls a model.** Every install shape the console shows is
  operator-run, out of band, on the host — never triggered by application
  code (`core/inference/catalog.py`'s own module docstring states this).
  The posture-gated "pull during a gated-sync window" console flow, and
  bunker-posture enforcement that blocks `ollama pull` outright, are Phase 2
  work riding on this same catalog data model (now enrichment-only:
  name/capability/embed_dim plus the reserved `config`/`digest` fields).

## Consequences

- Models are fully data-driven per role and hot-swappable through the
  console with no restart, while the platform still runs with **zero DB
  rows** via `env_provider` — the walking-skeleton's "config-driven models,
  no code changes" promise (ARCHITECTURE §3c) holds literally, whether the
  config lives in an env var or a database row.
- A new role is one `RoleSpec` registration away, regardless of which app
  registers it — validated for real by the first non-RAG role that lands
  (the plan's Next Steps §1, vision).
- The db → env chain adds one more failure surface (a DB error mid-migrate,
  an unreachable table), but `db_provider` is written to degrade to `None`
  rather than raise in every one of those cases, so it can never break the
  env-only fallback guarantee that makes `core/` usable standalone.
- The re-encode guard only protects roles that wire a `rematerialize`
  callback — today just `rag.embed`. A future embeddings-capability role
  that forgets to register one gets no drift protection; this is a
  per-role discipline cost the framework flags in documentation but cannot
  enforce structurally.
- `console/` living outside the module sandbox means its code is trusted at
  the platform's own level by design — recorded here explicitly rather than
  left as an implicit gap between "core" and "modules" in the layered
  architecture.
- The `/inference/` mutation endpoints (register a connection, bind/rebind
  a role, trigger a re-encode) are **unauthenticated** today — consistent
  with the rest of the app, which has no Identity & Auth layer yet
  (Phase 1). This is a known, accepted gap: these endpoints are explicitly
  in scope for Identity & Auth when it lands (Phase 2) — an operator
  console that can repoint the platform's own models must be among the
  first surfaces gated behind authentication.
- Deferring signed ingress to Phase 2 means today's models enter via an
  unverified `ollama pull` in every posture, including bunker profiles —
  acceptable as the current dev-posture default, but Phase 2 must close
  this before a bunker posture can claim the offline invariant is enforced
  for model ingress specifically, not just assumed.

## Amendment (2026-08-22) — Future: feature-driven model requirements

The project's long-term direction is a platform where an operator selects
which feature setups are enabled (RAG today; image generation, home
automation, and others later) — and enabling one must ensure the models
and settings that feature needs are actually present, not merely assumed.
This ADR records that the role registry (§1 above) is already the correct,
sufficient seam for that: a feature enables itself by registering its
`RoleSpec`s (§4's manifest precursor), each `RoleSpec` declares its own
capability need, and everything downstream — the console's "Getting
models" checklist (§3, and Task 18's table rendering of it) and the
role-to-connection binding flow alike — is already fully derived from
`all_roles()` with zero knowledge of which feature registered which role.
A future feature-toggle mechanism therefore has exactly one job: gate
*role registration* on whether its feature is enabled, and this framework
requires no redesign to serve it — the checklist and binding UI will pick
up a newly (or conditionally) registered role's needs automatically, the
same way they already pick up a new capability like `vision` today. No
toggle mechanism is built by this amendment; it only records the
constraint so a future implementer doesn't reach for a parallel
requirements system where this seam already suffices.

## Amendment (2026-08-22) — Constraints for a future conversational agent

The project's next phase after model management is a conversational agent —
not simply a chat window, but a conversation surface with tool access across
the platform: it can interact with other features, modify settings, and use
RAG as one tool among several ([ROADMAP.md](../ROADMAP.md) Phase 1.6).
Nothing here is built by this amendment; it records the constraints that
phase must honor so the framework isn't redesigned around it later.

- **Tool access is capability-scoped, not ambient.** An agent's tools map
  onto the same capability/manifest vocabulary ARCHITECTURE §5 already uses
  to gate modules (`inference:chat`, `inference:embeddings`, `vector:read`,
  ...; §3 above extends this same vocabulary to role capabilities). A tool
  that can modify settings is a declared capability like any other — never
  authority the agent gets simply by existing. This ADR introduces no
  second vocabulary for tools; they are gated the same way modules already
  are.
- **A settings-modifying tool requires Identity & Auth before it ships.**
  This ADR's Consequences section already records that the `/inference/`
  console's mutation endpoints are unauthenticated, accepted only because
  there is no Identity & Auth layer yet (Phase 1) and explicitly slated to
  close in Phase 2. An agent tool that can rebind a role, change a posture
  setting, or otherwise touch configuration must not open a second, wider
  door onto that same unauthenticated surface — it inherits the existing
  gap, it does not get to widen it. Whatever bunker posture rules apply to
  configuration changes (§2/Phase 2: `airgap`, `isolated-lan`,
  `gated-sync`) apply identically whether a human or an agent tool makes
  the change.
- **RAG-as-tool reuses the existing retrieval seam.**
  `tools/rag/retrieval.py::answer_question` is the retrieval path; an
  agent invoking RAG as a tool calls into that seam, it does not grow a
  parallel one.

> **Amendment (2026-08-27, agents P2).** This bullet originally named
> `modules/rag/retrieval.py::answer_question` "with its `session_id`-keyed
> grounding". Two corrections. The path moved in the P0 regroup:
> `modules/rag/` is now `tools/rag/`. And the `session_id` parameter was
> deleted along with the `ChatSession`/`ChatMessage` tables it wrote to,
> which nothing ever read. **The seam survives; the parameter does not.**
> Conversation memory now lives in `agents.models.Turn`, which records the
> tool call, its arguments, its data, its artifacts, and its delegation
> depth — none of which a two-column message table could hold — and which
> belongs to the agent column rather than the RAG one. `rag.search` and
> `rag.ask` (P1) call `retrieve_nodes` and `answer_question` respectively,
> exactly as this bullet requires.
- **The model-management framework needs no redesign.** An agent is a
  consumer of roles/bindings exactly like any other feature — `chat.converse`
  is registered, resolved, and (if it embeds) drift-guarded the same way
  `rag.answer`/`rag.embed` are today (§1). Nothing about per-role
  resolution, the db → env fallback chain, or the re-encode guard changes
  to accommodate an agent.
- Nothing in this amendment is implemented; it constrains the design of a
  phase that has not started.

## Amendment (2026-08-22) — No baked model defaults; a role is assigned or it is unassigned

Owner ruling, verbatim:

> "default shouldn't be in the env files, there should be no default in the
> env files as there isn't a default to setup. This is objectively
> incorrect."

The platform must never presume a model. This amendment removes every baked
model default, in config and in the seeded data alike, and makes the absence
of a choice a first-class, honestly-reported state.

### What changed

- **`config/settings.py`** — `LLM_MODEL` / `EMBED_MODEL` / `EMBED_DIM` have
  no fallback value: unset means `None`. A malformed `EMBED_DIM` reads as
  unset rather than becoming an invented number — including `0` and
  negatives, which are malformed for a vector width and would otherwise slip
  past the `None`-only fail-fast in `modules/rag/index.py` into an opaque
  `PGVectorStore` error instead of the clean, actionable one; nothing
  downstream guesses a dimension.
  `OLLAMA_BASE_URL` deliberately KEEPS its default — a server *location* is
  deploy convention (ADR 0006), not a model choice. This inverts §1's
  original settings↔catalog cross-reference, whose whole purpose was to make
  a fresh install bind to a catalog-known model; that pinning test is
  replaced by its opposite, a guard that fails if any model default (or any
  model name at all) reappears in `config/settings.py`.
- **`.env.example`** — no values for the three model variables. They remain
  documented, commented out and empty, as OPTIONAL explicit overrides. No
  model name appears anywhere, as a value or as a suggestion (standing
  ruling).
- **`core/inference/bindings.py::env_provider`** — with the corresponding
  setting `None`, the provider yields nothing for that role. No silent
  substitution; `resolve()` then raises and the role is genuinely
  unresolved. An explicit override still resolves exactly as before.
- **`console/inference/migrations/0002_seed_from_env.py`** — seeds nothing
  for a role whose override is unset, each half independent. A fresh install
  migrates into an empty registry and the console's cold-start onboarding —
  register a model, then assign it — becomes the real first-run path, not a
  branch most operators never see.
- **The console** — a role now has THREE honest states instead of
  two-and-a-fallback: backed by a registered connection, backed by an
  operator's EXPLICIT environment override, or **unassigned**. An unassigned
  role renders "No model assigned" in the warning style (existing
  `--warn-bg`/`--warn-text` tokens, no new colors) with copy that points at
  the pipeline: *"Nothing backs this role yet — anything that needs it
  reports unavailable. Register one from the machine list below, then choose
  it here."* An override is labeled as an operator's deliberate choice
  ("explicit environment override"), never as a "default". Every
  "environment defaults" string is gone from the surface.
- **Unbind means UNASSIGN.** The dropdown option, its confirm gate, the
  success message, and the connection-removal note all say what actually
  happens — usually "no model will back it and anything that needs it
  reports unavailable until you assign one", and "the explicit environment
  override for this role takes over" for the operator who set one. Gate and
  pre-change materialization-stamp *semantics* are unchanged; only the copy
  is now true.
- **Per-role, never averaged.** Which of those two outcomes applies is a
  fact about ONE role, so it is decided per role everywhere it is stated.
  Removing a connection clears every `RoleBinding` pointing at it
  (`SET_NULL`), and a single connection can be bound to roles with different
  outcomes — one falling onto its override and still working, another left
  with nothing. `connection_remove`'s message and the pre-removal note in
  `_registered_connection.html` therefore name each role's outcome
  separately (`_removal_note_short` / `_removal_note_title` build both
  strings in the view, and the message reuses `role_assign`'s own
  `_unassign_consequence`, so the two actions cannot describe one outcome
  two ways). The first cut of this amendment got that wrong: it said
  "unassigned" unconditionally, which a page could render directly above the
  same role's "explicit environment override" badge.
- **The Ask page** needed no change: its cause-accurate 503 matrix already
  distinguishes "not set up yet" from "unreachable", and an unassigned role
  now reaches the unbound branch through the real resolver rather than
  through a default that happened to be wrong.

### Why the honest empty state, rather than a "sensible" default

A default model is a claim the platform cannot honor: nothing is installed on
a fresh box, so a baked `LLM_MODEL` names a model that is not there. That
produced a first run where the console showed a binding to a model the
machine did not have, and the failure surfaced later, further from its cause.
An unassigned role that says so is both more truthful and more actionable —
it names the next step instead of implying the setup is already done.

### Consequence

The env variables remain supported for an operator who deliberately pins a
role from the environment (a container deployment, a scripted install), and
the seed migration still preserves such a setup across an upgrade. They are
overrides, not configuration the platform ships with values for.

## Amendment (2026-08-22) — A bounded `context_window` safety floor, and a per-connection override

A live incident on the owner's own machine: a locally-created Ollama model
variant carried a `PARAMETER num_ctx` in its Modelfile specifically to keep
its memory footprint small on a shared-use box. The console dutifully
resolved and used it — but `ollama ps` showed the loaded instance at a
context size, and a memory footprint, dramatically larger than the
Modelfile capped it to, and system free memory collapsed.

### Root cause

Read from the installed library source, not guessed: LlamaIndex's `Ollama`
LLM class defaults `context_window=-1`. Its `get_context_window()` treats
that sentinel as "find out for me" — it calls the model server's `show`
endpoint and adopts the model's reported architecture-maximum context
length, then sends that number as `num_ctx` in every request's options.
Ollama's per-request `options` **override** anything set in a Modelfile, so
a Modelfile's own `num_ctx` cap is silently defeated the moment a client
asks for more. `core/inference/engines/ollama.py::build_llm` passed no
`context_window` at all, so every chat request ended up demanding a KV
cache sized for the model's theoretical maximum — for a large model, tens
of gigabytes beyond what any RAG request actually needs.

Separately verified: LlamaIndex's `OllamaEmbedding` has no `context_window`
field and no equivalent probing behavior at all — its embed calls carry
only `ollama_additional_kwargs` (empty unless a caller sets it). There is no
matching incident on the embeddings side, and no matching guard was added
there.

### The fix

- **`core/inference/engines/ollama.py`** — `build_llm` now sets a module-level
  `DEFAULT_CONTEXT_WINDOW = 8192` cfg default whenever a caller doesn't
  supply one, via the same `cfg.setdefault(...)` pattern already used for
  `request_timeout`. This is an operational resource bound, not a model
  assumption — like the request timeout, it exists so the adapter can never
  again let a client probe a model server's architecture max on the
  platform's behalf. No model name or version appears in the constant's
  documentation; the bound applies uniformly, regardless of which model a
  connection names.
- **A per-connection override, visible in the console (owner's "nothing
  hidden" ruling)** — `ModelConnection` gained an optional
  `context_window` positive-integer field (migration
  `0003_modelconnection_context_window`), editable in the one edit home
  (`_connection_edit.html`, and the "Add a connection manually" create
  form) and threaded through `console.inference.bindings.db_provider` into
  `ResolvedModel.config`, from which the gateway spreads it straight into
  `build_llm`'s `Ollama(context_window=...)`. Blank/unset means no opinion
  — the adapter's own bounded default applies; it is never guessed or
  auto-filled ("Add to registered" from a machine row starts a new
  connection with it unset, same as every other operator-supplied field
  this pipeline never guesses). It is deliberately NOT part of the
  embeddings fingerprint (engine/model/dim/endpoint) — changing it alone
  never triggers the re-encode confirm gate, since it caps a resource, not
  a model identity.
- **Display** — a set value renders in the same facts area as Capability/
  Embedding dimension ("Context window: 16384 tokens"); nothing renders
  beyond the edit field's blank state when unset.
- **`env_provider` resolutions** (an operator's environment-only override,
  with no `ModelConnection` row) have nothing to hold a per-connection
  value — they simply get the adapter's bounded default, same as any other
  setting this provider has no opinion on.

### Consequence

Nothing changes for an operator who never runs into large architecture-max
context lengths: `DEFAULT_CONTEXT_WINDOW` is generous enough for ordinary
RAG chat/completion, and connections that want something different — larger
or smaller — set it explicitly, visibly, per connection.

## Amendment (2026-08-22) — Per-question model selection, the console import seam it required, and Ask history

Tasks 29–33 built the chain from "an operator can label their own registered
connections" through to "a question can be answered by a specific one of
them, on request, with a record kept of what happened." This amendment
records the grammar that chain settled on, closes a dependency-direction gap
the chain's own design required and that review flagged as debt, and
documents the Ask history feature it ends in.

### Per-question model selection grammar

Two mechanisms now exist for "which model answers," and they are
deliberately not the same mechanism wearing two hats:

- **A role binding (`RoleBinding`) is the durable default.** `rag.answer`
  resolved through `resolve(role_key)` (§1) — set once in the console, held
  until an operator changes it — is "primary": what answers a question when
  nothing more specific is asked.
- **The Ask page's Model picker is an ephemeral, per-question choice.**
  Task 31 threads an optional `connection` field through `AskView`,
  resolved via `console.inference.bindings.resolve_connection(pk, "chat")` —
  the pk-addressed twin of `resolve()` — into `answer_override`, which
  `retrieval.answer_question` uses for that one call only. Choosing a
  connection here writes nothing: no `RoleBinding` row is created, updated,
  or touched. The next question with no picker choice falls straight back to
  `rag.answer`'s durable binding, exactly as if the override had never
  happened.
- **The picker's contents are operator-authored, never platform-derived.**
  `chat_connections_for_picker()` lists registered connections in
  `descriptor`/`rank` order (Task 29) — a free-text label and a sort number
  the operator assigns themselves. Nothing about a connection's name,
  descriptor, or position is inferred from its `model_id`, its size, or any
  other model property; the picker can only ever show what an operator chose
  to say about their own registry.
- **Embeddings connections are not selectable here, deliberately.**
  `chat_connections_for_picker()` filters to `"chat"` capability only. An
  embeddings connection is welded to the document store it produced: its
  `embed_dim` fixes the vector column's width and its fingerprint is what
  `Materialization` compares against for the re-encode guard (§1). A
  per-question embeddings override would let one query embed against a
  different model than the one every stored chunk was embedded with —
  silently returning nonsense nearest-neighbors, not a clean error. There is
  no analogous per-request escape hatch for embeddings, and none is planned;
  `rag.embed`'s role binding is the only lever, and rebinding it goes through
  the full drift/re-encode machinery on purpose.
- **Owner's tentative stance, recorded for a future implementer:** per-
  question selection may later become "sticky" — remembered across
  questions in a session, or promoted to a new durable default — rather than
  resetting every time. Nothing here forecloses that. The seam that would
  carry it already exists with zero resolution-path changes: `answer_override`
  is already a fully-resolved `ResolvedModel` at the point it's used, so
  "remember the last pk chosen" is a storage concern (session, cookie, a new
  per-operator settings field) layered entirely on top of
  `resolve_connection`, never a change to how a chosen connection resolves
  or answers.

### Dependency-direction amendment (reviewer-flagged debt)

§2 states, literally: "a §5 module never imports `console/` either — a
module only ever calls `core.inference.gateway`/`bindings`." Task 31's Ask-
time picker made that literally false: `modules/rag/views.py` imports
`answer_role_primary`, `chat_connections_for_picker`, and
`resolve_connection_named` from `console.inference.bindings` at module scope
(Task 34 cleanup: folded the separate `connection_name(pk)` lookup this
paragraph originally listed into `resolve_connection_named`, which returns
the connection's display name alongside its `ResolvedModel` in the one
query `resolve_connection` was already making — one fewer function, one
fewer redundant query, same seam). This amendment corrects §2 rather than
papering over the gap:

> A §5 module MAY import from `console.inference.bindings` — and ONLY that
> one module — as the single sanctioned seam onto trusted platform code.
> `console.inference.models` and `console.inference.views` remain off-limits
> to every module, with no exception.

**Why this one seam, and why it's narrow.** `core.inference.bindings.resolve()`
answers "what backs this *role*, right now" — a question a module can always
ask without knowing anything console-owns. A per-request, operator-chosen
override is a different question: "resolve THIS specific, pk-addressed
`ModelConnection`" — and the only table that knows what pk `3` is, whether
it's chat-capable, and what its endpoint/context-window are is
`console.inference.models.ModelConnection`, which a module must never touch
directly (no manifest, no capability grant covers reaching into another
trust domain's tables). `console.inference.bindings` is where Task 30 put
exactly that lookup (`resolve_connection`, and its name-carrying twin
`resolve_connection_named`) plus the read-only helpers a picker UI needs
(`chat_connections_for_picker`, `answer_role_primary`) — already the
pattern `db_provider` itself follows one layer up (§1: `core/` never
imports `console/`; only the dotted-path provider string couples them).
Task 31 is what actually exercises the
import; Task 30 built the module for it to import. Restricting the sanctioned
seam to this one file — never `models.py`, never `views.py` — keeps the
coupling to exactly the read-only, already-designed-for-this-purpose surface,
not an open door onto the console's own tables or request handling.

### Ask history

Task 32 added `AskRecord`: one row per successfully-answered question,
kept for an operator-editable window.

- **Snapshot, not a live reference.** `connection_name` and `model_id` are
  plain `CharField`s copied at answer time, not a foreign key to
  `ModelConnection` — both for the module-boundary reason above
  (`modules/rag/models.py` must not import `console.inference.models`) and
  because a snapshot is what keeps history *truthful*: renaming or deleting
  the connection that answered a past question must never retroactively
  relabel or blank out that row. `category` is likewise the literal string
  the question was asked with, not a `Category` FK, for the same reason.
- **Errors are not recorded — the v1 cut.** Only the 200 path
  (`AskView`'s successful return) writes an `AskRecord`. A 503 ("no
  model reachable" — an unassigned role, an unhealthy engine, an unresolvable
  override pk) or a 500 (retrieval raised) writes nothing: there is no
  answer/model/citations triple worth preserving, and a failed-attempt log
  is future scope this task deliberately did not build. A history-write
  failure itself is symmetric with this: it is logged and swallowed, never
  allowed to turn a successful answer into a failed response.
- **Operator-editable retention, an operational bound.** `RagSettings.history_limit`
  (default `HISTORY_LIMIT_DEFAULT = 100`, editable from the history page's own
  settings form) is the same kind of number as `DEFAULT_CONTEXT_WINDOW` (this
  ADR's context-window amendment) — a cap the platform enforces on its own
  resource use, not a model name and not a hidden default; it ships with a
  value and is visibly changeable, never silently assumed.
- **Prune-on-insert, not a scheduled job.** `record_ask` creates the new
  `AskRecord` and then immediately prunes down to `history_limit` rows,
  oldest first (`_prune_ask_records`: one `LIMIT 1 OFFSET limit` query for
  the cutoff pk, ordered by `-pk` since pk — unlike `created_at` — can never
  tie, then one bulk delete). Retention is therefore enforced exactly once
  per question, synchronously, with no background task and no window where
  the table can grow past the limit between questions.

## Amendment (2026-08-23) — The execution queue consumes this ADR's resolution seam

[ADR 0013](0013-inference-execution-queue.md) (T1–T8, the execution-queue
build) is the platform-wide "one door" every model execution now flows
through — every job kind's planner/handler resolves a model exactly the way
this ADR's §1 already specified (`resolve()`, `resolve_connection`/
`resolve_connection_named`), unchanged. Two things worth recording here
rather than only there:

- **`configure_settings()` is gone.** `core/inference/gateway.py` no longer
  exposes it. It wrote a resolved model onto LlamaIndex's process-global
  `llama_index.core.Settings` — safe when the platform answered one
  question at a time, inline, but a real race the moment the queue lets
  more than one job build a model concurrently (the whole point of §4 of
  ADR 0013's memory-aware concurrency). Every caller now builds its own LLM/
  embed model explicitly via `get_llm`/`get_llm_for`/`get_embed_model`/
  `get_embed_model_for` and threads it through by hand — the same four
  functions this ADR's §1 already named, just now the *only* way anything
  is built, with no shared mutable global left to race.
- **A structural no-global-Settings guard** exists in the test suite
  precisely so this cannot silently regress: a job kind's handler that
  reached for `llama_index.core.Settings` again instead of an explicitly
  threaded model would reintroduce the exact cross-job race this amendment
  records as closed.

Nothing about role registration, the db → env fallback chain, or the
re-encode guard (§1) changed to accommodate the queue — a job kind is a
consumer of this ADR's resolution seam exactly like the synchronous callers
it replaced.

## Amendment (2026-08-29) — Amended by ADR 0015: the import law is three rules, and `platform/` stays rejected

**The import law is restated.** §2's `core/` / `console/` / `modules/`
vocabulary (`0010:108-133`) and the dependency-direction amendment
(`0010:520-526`, the amendment that opened the seam) described a tree that
no longer exists: [ADR 0015](0015-agent-layer-and-tool-contract.md)
Decision 1 replaces it with four columns and three import rules. The
mapping — `core/` split into `models/contracts` (pure engines/bindings/
gateway/queue leaves), `agents/contracts` (the pure tool contract), and
`foundation/format.py`/`files.py` (the remaining pure leaves);
`console/inference` → `models/registry`; `console/jobs` → `models/queue`;
`console/ops`, `console/setup` → `foundation/`; `modules/*` → `tools/*` —
plus the two named cross-column exceptions (a `tools/*` or `agents/*` app
may import `models.registry.bindings`, and only that module; `foundation.
ops` may import `models.queue.models`, and only for the active-work backup
refusal). **The sanctioned-seam sentence is the blockquote at
`0010:534-537`** — "A §5 module MAY import from `console.inference.bindings`
— and ONLY that one module…" — and it is that blockquote the amendment
carries over verbatim in substance, now naming `models.registry.bindings`
and admitting `agents/*` as a second importer family alongside `tools/*`.
`0010:520-526` is cited only as the amendment that opened the seam, never
as the sentence itself.

**`platform/` is re-confirmed as rejected, not reversed.** §2's "Why not
`platform/`" (`0010:128-133`) was re-verified three ways before P0 and
holds; the fourth column is `foundation/`. Re-confirmed cleanly here — no
`collectstatic` qualifier: `0010:128-133` argues stdlib shadowing and
import ambiguity and never mentions `collectstatic`, so there is nothing in
it to narrow. The narrowing belongs to spec §2.1's third proof and lives in
ADR 0015 Decision 1 alone.

**`session_id` — pointer, not restatement.** The amendment already
standing inside the RAG-as-tool bullet at `0010:282-293` records what
happened to `session_id`; ADR 0015 Decision 13 is the fuller account of
`ChatSession`/`ChatMessage`'s retirement.

## Amendment (2026-08-30) — Identity & Auth: the `/inference/` gap closes; the mutating-tool gate does not

**The standing unauthenticated-mutation gap on `/inference/` is closed.**
Every route in `models/registry/urls.py` — the console READ, the six
mutations, and IA-2's own three model-set routes — is classified `S` in
`identity/routes.py` and refused to anybody
who is not an administrator of the box. The read is `S` too because the
console displays endpoints, model identifiers and connection configuration:
an inventory of the box, which is operator information. This closes the gap
in full rather than half.

**A model connection can now carry an entitlement label, and the role path
cannot.** IA-2 adds `models.registry.ModelSet` and its two edges
(`ModelSetMember`, `ModelSetEntitlement`): a connection joins a SET, an
entitlement attaches to a SET, and either edge is edited without touching
the other. That gates which principals may SELECT that model in the chat,
vision and Ask pickers and which may have an agent turn resolve it. Using
model M through capability T requires the tool entitlement AND an
entitlement attached to any set M belongs to — an AND across two label
kinds, an OR within each, and a model in no set stays open to every holder
of the capability.

**Agents and flows carry direct entitlement labels too**
(`agents.AgentEntitlement`, `agents.FlowEntitlement`), filtered through the
`visible_agents`/`visible_flows` seams the agent phase already built — and
the label clause composes with the `resident=True` carve-out rather than
being bypassed by it, so a shipped agent can be restricted like any other.

**A model resolved through a ROLE is deliberately exempt.** `db_provider`
and `role_primary` — `rag.embed`, `rag.transcribe`, `rag.extract`, and the
default `chat.converse`/`vision.generate` bindings — answer as they always
have. They are the box's own wiring, not a per-request choice, and gating
them would mean a label on the embedding connection silently stopping
ingestion, retrieval and re-encode for everybody, including the folder
watcher and every management command, none of which holds a grant. An
operator who wants a model unreachable unbinds it or removes it in the
console. Recorded in the spec at §22.32 and flagged there for the owner.

**The mutating-tool grantability gate is NOT lifted.** This ADR's rule that a
settings-modifying tool is registered but not grantable stays in force
through Identity & Auth's second half:
`agents/contracts/tools.py::grantable_tools` still excludes every
`mutates=True` spec unconditionally, and `granted_tools` still drops one
silently. Lifting it means deciding which entitlement such a tool requires by
default, which is a policy question this phase does not need to answer and
should not guess at.

## Amendment (2026-09-20) — Known limit: chat's native-image gate reads the catalog, not a per-connection capability

Chat attachments (the attachments-integration land, Task 3) added
`agents/runtime/prompt.py::native_media_types(resolved)`: whether THIS turn's
bound model accepts an image natively in a chat message, so a carried image
can ride the prompt as a real `ImageBlock` instead of the "still processing"
text fallback. It answers by looking `resolved.model_id` up in THIS ADR's own
`CatalogEntry.accepts` field (`models/contracts/catalog.py`) — additive-only,
`()` for every entry that predates it, `("image",)` for the two vision
entries this ADR's catalog already carried.

**The gate is therefore bounded by the catalog's own fixed vocabulary, not by
what a connection can actually do.** `models.registry.discovery` may report a
freshly-registered connection's live vision capability, and `ResolvedModel`
(`models/contracts/bindings.py`) carries no capability field at all today — so
a real multimodal chat connection outside the catalog's known model ids still
gets the honest still-processing fallback, even though the box already knows
better. This is the SAME "known models get the enrichment, an unlisted one
gets nothing extra" shape §3 (Capability vocabulary) and the catalog's own
module docstring already state for `find()`'s console-form enrichment; Task 3
is one more caller taking that same trade rather than a new exception to it.

**Widening this is a models-column change, not a prompt-column one.** The fix
is putting the capability set on `ResolvedModel` itself (or resolving it at
bind time from `models.registry.discovery`) so `native_media_types` can read a
live per-connection fact instead of a static catalog lookup — deliberately
not done by this task, which owns `agents/`, not `models/`.

## Amendment (2026-09-22) — Amended by ADR 0019: the `context_window` column gains a READ direction

The 2026-08-22 amendment above added `ModelConnection.context_window` so the adapter could be
told what to ask for, and its own fix sentence forbids ever letting a client probe a model
server for an architecture maximum on the platform's behalf. That column was written to be
**sent**.

The chat cluster's context meter now also **reads** it, for display:
`models/contracts/bindings.py::effective_context_window(resolved)` returns the operator's own
per-connection value when there is one, the engine adapter's bounded default otherwise, and a
declared "unknown" when the bound engine declares no default of its own. It is a pure read —
it writes nothing, sends nothing, and never touches the network — and it never reports a
model's architecture maximum, because the bounded number the engine is actually asked for is
the number that truncates a conversation.

**This amendment adds a direction, not a rule.** Nothing about how the value is set, resolved
or spread into the engine call changes, and the prohibition the incident produced stands
unweakened: a display probe would still be a probe, one per page render, against a machine
that may be asleep.

The reasoning, the engine-default lookup, and why this reader deliberately answers differently
from the retrieval page's own context-window reader are recorded in
[ADR 0019](0019-chat-cluster.md), Decision 1; `models/registry/README.md` carries the
reconciliation between the two readers.
