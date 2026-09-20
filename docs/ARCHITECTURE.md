# farabunker — Architecture

This document describes the layered architecture, the security/offline model, and the
module contract that makes farabunker a *platform* rather than a single app.

- [1. Layered architecture](#1-layered-architecture)
- [2. Security posture: the offline spectrum](#2-security-posture-the-offline-spectrum)
- [3. Plug-and-play: how we avoid betting on hardware](#3-plug-and-play-how-we-avoid-betting-on-hardware)
- [4. The core services](#4-the-core-services)
- [5. The module contract](#5-the-module-contract)
- [6. The update airlock](#6-the-update-airlock)
- [7. First module: offline RAG](#7-first-module-offline-rag)
- [8. Open questions](#8-open-questions)

---

## 1. Layered architecture

farabunker is a stack of layers, each with a **stable interface** so the layer
below can be replaced as technology matures.

```
┌──────────────────────────────────────────────────────────────┐
│  ACCESS        Local web UI · LAN app · voice · CLI            │  how humans reach it
├──────────────────────────────────────────────────────────────┤
│  AGENTS        Tool contract · Turn runtime · /chat/          │  the agent layer
├──────────────────────────────────────────────────────────────┤
│  TOOLS         RAG · Image generation ·                       │  the services
│                Home automation (planned)                      │
├──────────────────────────────────────────────────────────────┤
│  CORE          Inference GW · Vector · Storage · Auth ·        │  the platform  ← we build this
│                Service bus · Config/Secrets · Airlock · Obs.   │
├──────────────────────────────────────────────────────────────┤
│  RUNTIME       Model registry · Inference engine(s) ·         │  swappable impls
│                Embeddings · Accelerator abstraction           │
├──────────────────────────────────────────────────────────────┤
│  ISOLATION     WAN-deny firewall · sandboxing · containers    │  enforced posture
├──────────────────────────────────────────────────────────────┤
│  OS + HARDWARE Hardened Linux/macOS · CPU/GPU/NPU             │  the bunker box
└──────────────────────────────────────────────────────────────┘
```

**The line we own** is the CORE. Everything below it is "bring the best available
implementation"; everything above it is "tools built against our contracts."

---

## 2. Security posture: the offline spectrum

"Offline" is not one thing. farabunker treats it as a **posture profile** — a named,
versioned network+OS policy the operator selects. The *same* codebase runs at any point
on this spectrum; only the profile changes.

| Profile | Radio / network | Update path | Best for |
|---|---|---|---|
| **`airgap`** | No WAN, no inbound LAN routing; optionally no radios at all | Physical media only, through the airlock | Highest-assurance sites, sensitive data |
| **`isolated-lan`** | Own subnet/VLAN, **default-deny egress** to WAN, trusted LAN for devices & UI | Airlock or a gated pull | Home automation, multi-device homes/offices |
| **`gated-sync`** | Normally offline; a deliberate, operator-initiated, time-boxed WAN window | Verified pull during the open window, then re-sealed | Users who want periodic knowledge/model refresh |

Rules that hold in **every** profile:

- **Default-deny egress.** Nothing reaches the public internet unless a posture profile
  explicitly opens a gated window, and even then only the airlock manager may use it.
- **Enforcement is below the app.** Isolation lives in the ISOLATION layer (host
  firewall rules, network namespaces, per-container egress policy) so a compromised
  module *cannot* punch out even if it tries. Modules are never trusted to self-police.
- **No implicit phone-home.** No telemetry, license checks, model downloads, or crash
  reports leave the box.
- **The subnet is a security boundary.** In `isolated-lan`, farabunker runs its own
  VLAN/subnet with no route to the WAN gateway — devices join *its* network, it does not
  join theirs.

> Design note: because home automation needs a LAN to reach Zigbee/Z-Wave/Wi-Fi devices,
> `isolated-lan` (not true air-gap) is the expected default for most installs. Air-gap
> remains a first-class, fully supported profile for those who need it.

---

## 3. Plug-and-play: how we avoid betting on hardware

The explicit goal is that farabunker gets *better automatically* as hardware and
models mature — without rewriting tools. Three abstractions deliver that:

**a) Accelerator abstraction.** The runtime auto-detects CPU / CUDA / Metal / ROCm / NPU
and picks the best available backend. Modules never know or care what silicon they're on.

**b) Inference engine behind an OpenAI-compatible contract.** The Inference Gateway
exposes a stable, well-known API (chat/completions + embeddings). Behind it, the engine
can be `llama.cpp` today, `vLLM` or something newer tomorrow — swapped by config. Using
the de-facto-standard API shape means the whole ecosystem of tooling already speaks it.

**c) Model registry.** Models are declared in config **or the operator-managed registry
(DB)** — never hardcoded. A profile might say "use a 3B model on a laptop, a 70B on the
workstation" — same module code, different registry entry, whether that entry is an env
var or a database row an operator edited from a console UI. Env config remains the
zero-DB path for an operator who deliberately pins a role from the environment
(`LLM_MODEL`/`EMBED_MODEL`/`OLLAMA_BASE_URL`, `models/registry/bindings.py`'s
`env_provider`), so the platform never *requires* the registry to run. But nothing is
presumed: those variables have **no default values**, and a role with neither a registry
row nor an explicit override is honestly *unassigned* rather than pointed at a model
nobody chose (owner ruling — see [ADR 0010](adr/0010-model-management-framework.md)'s
third amendment). Models are
content-addressed and signed so the airlock can verify them (Phase 2 — see
[ADR 0010](adr/0010-model-management-framework.md)).

```
Tool   ──▶ Inference Gateway (stable OpenAI-compatible API)
                    │
                    ├─ engine: llama.cpp | vLLM | ollama | future
                    ├─ model:  from signed registry (3B … 70B …)
                    └─ accel:  auto(CPU|CUDA|Metal|ROCm|NPU)
```

The bet we *are* making: the OpenAI-compatible request/response shape and "vector store +
embeddings" are stable enough abstractions to build on for years. Everything underneath
them is designed to be replaced.

**Generation axes.** Image generation ([ADR 0012](adr/0012-image-generation-engine-adapter.md))
adds three further axes to the Inference Gateway, each independently swappable and each
behind its own seam:

- **Engines** — how to talk to a generation server. `models/contracts/engines/comfyui.py` is
  the first one; a second engine adapter is a new file, same as an LLM engine.
- **Operations** — what can be generated, each with its own parameter schema
  (`models/contracts/operations.py`; five operations — `txt2img`, `img2img`, `inpaint`,
  `upscale`, `edit`). An operation is what the page renders its form from and what an engine
  maps onto its own template — adding a
  mode (img2img, inpainting, upscaling) is one new operation plus one new template —
  the page renders its form, its job-card facts, and its mode chooser from the
  operation's `Param` schema, the engine adapter moves any file input itself, and the
  templates share one graph vocabulary (`comfyui_workflows/_fragments.py`) so a mode
  is the handful of lines that are actually different. Assets (LoRAs, upscalers) are a
  second axis: parameters that adorn a job, filled from what the engine reports having.
- **Assets** — files that adorn a job without answering a role (LoRA, VAE, ControlNet,
  upscaler — `models/contracts/engines/base.py::Asset`). A second axis from role-bound
  models: an asset decorates one generation, it doesn't get bound to a role.

A **role** still binds a model to a capability, exactly as for chat/embeddings
(`vision.generate` binds a checkpoint to `image-generation`); an **operation** names what
to do with that bound model once it's resolved.

---

## 4. The core services

The platform provides these capabilities to tools. Each is itself an implementation
behind an interface.

| Service | Responsibility | Candidate impl (v1) |
|---|---|---|
| **Inference Gateway** | LLM chat + embeddings, OpenAI-compatible, model routing | **Ollama** (OpenAI-compatible); vLLM/llama.cpp later |
| **Vector Store** | Store & similarity-search embeddings | **Postgres + pgvector** |
| **Object & Doc Storage** | Files, ingested docs, module state | Local FS + **Postgres** |
| **Identity & Auth** | Local users, an account-and-session posture (open/personal/organisation), per-column capability grants, the audit log | **Django auth**, subclassed as `identity.User` (local, no cloud IdP); realized in `identity/` (IA-1 and IA-2, [ADR 0016](adr/0016-identity-and-entitlements.md)) |
| **Service Bus / Registry** | Module discovery, request routing, events | Local HTTP + lightweight broker |
| **Config & Secrets** | Posture profiles, model registry, a local secrets vault | Encrypted local store; model registry realized in `models/registry/` ([ADR 0010](adr/0010-model-management-framework.md)) |
| **Execution Queue** | Priority-ordered, memory-aware admission for every model execution (chat, re-encode, and future job kinds) | Postgres-backed queue realized in `models/queue/` ([ADR 0013](adr/0013-inference-execution-queue.md)) |
| **Agent runtime** | One tool contract every feature registers against; a bounded turn that drives tools by model decision or by a fixed flow | `agents/` — the registry in `agents/contracts/`, the turn in `agents/runtime/`, the product surface at `/chat/` ([ADR 0015](adr/0015-agent-layer-and-tool-contract.md)) |
| **Airlock / Update Mgr** | Verify & ingest signed update packages | See §6 |
| **Observability** | Local-only logs, metrics, audit trail | Local stack, no egress |

The word "posture" is doing two unrelated jobs in this document: §2's **posture
profile** (`airgap` / `isolated-lan` / `gated-sync`) is a network+OS policy the operator
selects for the whole box, while Identity & Auth's **security posture**
(`open` / `personal` / `enterprise`, `identity.IdentitySettings.posture`) is a database
row governing accounts and permissions — a box can run any security posture on any
posture profile, and the two vocabularies are not related beyond sharing an English word.
It is a database row rather than a setting for the general reason every operator-facing
settings singleton is: a value web, worker and watcher must agree on, and one that must be
validatable against database facts, belongs in the database, never in an environment
variable — see [`docs/EXTENDING.md`](EXTENDING.md)'s "Where a setting lives" for the full
tier-placement rule.

The repository is now **five** columns, not four: `tools/`, `models/`, `agents/` and
`foundation/` ([ADR 0015](adr/0015-agent-layer-and-tool-contract.md) §1) plus
`identity/` — who exists, what posture this box is in, and what has been done to it —
which joined as the fifth in IA-1.

### File-serving routes

Every route streaming bytes behind an entitlement check — a document's original file and
transcript (`tools/rag`), a vision job's generated output and stored input (`tools/vision`)
— answers `Cache-Control: private, no-store, max-age=0` with `Cookie` added to `Vary`, set
by the single shared `foundation.http.mark_private` helper both columns call: a shared
appliance browser's disk cache and a LAN caching proxy are both closed off (`docs/
OPERATIONS.md` §"Private content never gets cached"). `foundation/` is the shared home
because it is the platform's base layer every column may import (§4's table), so a helper
more than one column's view layer needs lives there rather than crossing a column boundary
neither `tools/rag` nor `tools/vision` may.

---

## 5. The module contract

A **module** is a sandboxed service that plugs into the core. It is the unit of
extensibility — RAG and home automation are just the first two. Operator-facing
*platform* apps (e.g. `models/registry/` — the model registry; `models/queue/` —
the execution queue and its `/queue/` page ([ADR 0013](adr/0013-inference-execution-queue.md));
`foundation/setup/` — a read-only page at `/setup/` assembled from `ENGINES` and the role
registry, where each adapter's own `SetupGuide` is the source of its install instructions,
the "self-describing engine" counterpart to this section's self-describing UI surface below;
`foundation/landing/` — the front door at `/`, which offers an entry card for exactly the
surfaces whose roles have a model bound and says so plainly when none do;
and posture/airlock consoles later) are trusted platform code that implements CORE services
and ACCESS surfaces; they are explicitly outside this contract and its sandbox (see
[ADR 0010](adr/0010-model-management-framework.md)).

### Tools today

- **`tools/rag`** (Phase 1's walking skeleton) — mounted at `/rag/`; registers
  `rag.answer` (`chat`) and `rag.embed` (`embeddings`). See
  [`tools/rag/README.md`](../tools/rag/README.md).
- **`tools/vision`** (image generation) — mounted at `/vision/`, gated by the
  `"vision"` feature flag; registers `vision.generate` (`image-generation`); stores each
  job's inputs and outputs under `data/generated/<job-uuid>/`. See
  [ADR 0012](adr/0012-image-generation-engine-adapter.md) and
  [`tools/vision/README.md`](../tools/vision/README.md).
- **`tools/home`** (home automation, planned) — no mount and no role registered yet; see
  [`tools/home/README.md`](../tools/home/README.md).

**Feature flags.** `FARABUNKER_FEATURES` (`config/settings.py`, ADR 0010's first
amendment) gates a whole feature's role registration, its URL mount, and its shared-nav
entry — `tools/vision/apps.py::VisionConfig.ready()` checks it once before registering
`vision.generate`; `config/urls.py` checks it again before mounting `/vision/`. The same
flag reaches every rendered template as `farabunker_features` through
`tools.vision.context_processors.features`, and it is half of what decides whether the
shared shell (`foundation/templates/_shell.html`) renders its **Images** nav entry at all —
with the feature off there is no `/vision/` route, so the link must not exist either. The
other half is UI-1's availability rule: a use-surface entry appears only when the role
behind it has a model bound. Both halves are answered by one key,
`surface_available.images`, from `models.registry.context_processors.availability`.

Each module ships a **manifest** declaring what it is and what capabilities it needs:

```yaml
# module.yaml (illustrative)
name: rag
version: 0.1.0
description: Offline document Q&A over a local knowledge base
capabilities:                 # what the core must grant; nothing else is reachable
  - inference:chat
  - inference:embeddings
  - vector:read
  - vector:write
  - storage:documents
network: none                 # modules are WAN-denied; this is asserted & enforced
ui:
  mount: /rag                 # optional web UI surface
```

Contract rules:

- **Capability-scoped.** A module receives *only* the core capabilities it declares.
  No ambient authority, no direct filesystem/network reach beyond its grants.
- **WAN-denied and enforced.** `network: none` is the norm; the ISOLATION layer makes it
  true regardless of what the module code attempts.
- **Talks only to the core.** Modules reach inference, vectors, and storage exclusively
  through the service bus — never a hardcoded engine or a raw socket.
- **Sandboxed.** Each module runs isolated (container / namespace) so a compromise is
  contained and cannot pivot to other modules or the host.
- **Self-describing UI.** A module may expose a UI surface the ACCESS layer mounts into
  the local web app.

This is what makes farabunker a platform: a third party (or future-you) can write a
new module against these contracts and drop it in without touching the core.

---

## 6. The update airlock

An offline box is only as current as its last update. The airlock is the *one* controlled
way anything enters the bunker — models, module updates, security patches, knowledge packs.

Flow:

1. **Package** is built outside the bunker: a bundle of artifacts + a signed manifest
   (hashes, versions, provenance).
2. **Transfer** into the bunker via the profile's allowed channel — physical media for
   `airgap`, a gated window for `gated-sync`.
3. **Verify** — signature and per-artifact hash checked against trusted keys *before*
   anything is unpacked. Reject on any mismatch.
4. **Stage & activate** — verified artifacts installed atomically, with rollback if
   activation fails.
5. **Re-seal** — the channel closes; the box returns to its default-deny state.

The airlock is the only component permitted to touch inbound artifacts, and it never
initiates an outbound connection on its own.

---

## 7. First module: offline RAG

RAG is the walking skeleton — the first vertical slice that exercises every core contract
end to end, proving the platform interfaces are real.

```
Documents ─▶ [ingest] ─▶ chunk ─▶ embed (Inference GW) ─▶ Vector Store
                                                              │
User question ─▶ embed ─▶ similarity search ◀─────────────────┘
                                │
                        retrieved context ─▶ LLM (Inference GW) ─▶ answer + citations
```

It touches: Inference Gateway (embeddings + chat), Vector Store (read/write), Storage
(documents), Auth (who may query), and the ACCESS layer (a query UI). If those seams are
right for RAG, home automation — which adds device I/O and event handling — becomes an
additive module rather than a redesign.

---

## 8. Open questions

Decisions to make as we move from design to build:

- ~~**Base OS & isolation tech**~~ — **decided** ([ADR 0006](adr/0006-containerization-and-isolation.md)):
  hardened Linux host running a hardened container stack (Compose; Podman preferred), modules
  isolated on an internal no-WAN network. Reference *hardware box* still open (below).
- **Home-automation device layer** — which protocols first (Zigbee/Z-Wave/Matter/Wi-Fi),
  and whether to bridge an existing local stack (e.g. Home Assistant) as a module vs.
  build native.
- ~~**Identity model**~~ — **decided and built, in two halves**: `identity/` ([its own
  README](../identity/README.md)) gives every box a real user model, three security
  postures (`open`/`personal`/`enterprise`), an append-only audit trail, and the
  owner-stamping/adoption machinery every owned row needs (IA-1); per-principal grants
  in their own table, groups, document/tool/model/agent/flow labels, and conversation
  sharing landed in IA-2. Recorded by
  [ADR 0016](adr/0016-identity-and-entitlements.md), which also names what is still
  open (service-account tokens, SSO, tenancy).
- **Signing & trust roots** — how the airlock's trusted keys are provisioned and rotated
  in an offline setting.
- **Minimum viable box** — the reference hardware we test/document against first, even
  though the design stays hardware-agnostic.
