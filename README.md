# farabunker

> A self-contained, offline-first platform for running capable AI services with **no dependency on the public internet**.

farabunker is a **local service substrate** — a small hardened core that provides AI
inference, storage, identity, and a service bus — plus a set of **plug-in modules**
(offline RAG, home automation, and more) that run entirely on hardware you control.

The guiding premise: **assume the network is compromised, so remove the network.**
As open-weight models get good enough to be genuinely useful without a cloud API,
you no longer have to trade capability for isolation. farabunker is the box that
makes that trade unnecessary.

It's designed for two audiences with the same technical need:

- **Urban resilience & privacy** — an apartment or office that wants full AI services
  with zero data leaving the premises.
- **Off-grid autonomy** — a cabin, boat, RV, or remote site with no reliable WAN,
  running the same stack on whatever compute is available.

---

## Design principles

1. **Offline by default, WAN-denied by policy.** No module reaches the public internet,
   and none is written to. The enforcement *seam* is deliberately below the application —
   network isolation belongs at the OS/network layer, not to code that a compromised
   module could talk its way past — but that layer is **not built yet**: `posture/` is a
   stub, and today the property rests on policy and review, not on something the box
   enforces for you. See [Security Posture](docs/ARCHITECTURE.md#2-security-posture-the-offline-spectrum).

2. **Abstraction is the product.** Hardware, model, inference engine, and vector store
   are all swappable implementations behind stable internal contracts. Upgrade any of
   them as the ecosystem matures without rewriting modules. This is the answer to
   "don't bet on today's hardware."

3. **The platform is the API, not the apps.** `tools/rag`, `tools/vision`, and
   `tools/home` (planned) are the first tools. The durable value is the core they plug
   into.

4. **Configurable posture, one codebase.** One build is meant to run as a strict air-gap
   appliance or as an isolated-LAN subnet with no WAN — chosen by a *posture profile*,
   not a different product. The profiles are designed and named; see [`posture/`](posture/)
   for which ones are planned and what each one is for.

5. **No phone-home, ever.** Telemetry, logs, and updates stay local. Updates arrive
   through a deliberate, verified **airlock**, never an automatic outbound connection.

6. **Open source, because trust is the product.** A security appliance that claims "it
   can't phone home" is not credible as a black box — auditability *is* the value. The
   platform is open and community-extensible. See [docs/BUSINESS.md](docs/BUSINESS.md).

---

## License & sustainability

farabunker is **open source under a dual-license model**: a copyleft core (AGPL-family)
that everyone gets free forever, plus a paid commercial license for anyone who wants to
"close the loop" and use it in a closed product — *closing the loop is possible, but you pay
to support the project*. Contributions come in under a broad-grant CLA so the project can
sustain that model. There is **no subscription**. Other revenue: paid hardware ("bunker in a
box"), a build-in-public content channel, courses, and premium open-core modules. The exact
copyleft text (AGPL-3.0 vs. BSL) is the one remaining license sub-decision. Full reasoning,
comparison, and monetization model in [docs/BUSINESS.md](docs/BUSINESS.md).

The source is currently under **AGPL-3.0** ([LICENSE](LICENSE)) — provisional pending that
sub-decision. See [LICENSING.md](LICENSING.md) for the dual-license terms, and
[CONTRIBUTING.md](CONTRIBUTING.md) + [CLA.md](CLA.md) to contribute.

---

## Quickstart

You need Docker and a local model server for inference. **The box ships no models and
presumes none** — that is deliberate ([ADR 0010](docs/adr/0010-model-management-framework.md));
you choose what to install and which role it backs.

```bash
git clone <this repository> && cd farabunker
cp .env.example .env      # set POSTGRES_PASSWORD and SECRET_KEY before exposing the box to anything
# .env.example ships POSTGRES_PASSWORD commented out -- uncomment it in your .env and choose
# your own value; docker compose refuses to start without it.
docker compose up -d --build
```

Then visit http://localhost:8000/ in a browser.

> **On the database credentials:** the compose stack fixes the Postgres user and database
> name to `farabunker`/`farabunker` — dev defaults, fine for a box on your own machine. The
> password is *not* defaulted: `POSTGRES_PASSWORD` comes from your `.env` and compose
> refuses to start without it. Set a real one before this box is reachable by anyone but
> you.

That brings up four containers — the database, the web app, the ingest watcher, and the job
worker — with durable data on the host under `./data`, outside the containers, where a
rebuild cannot touch it. Your model server stays native on the host and must listen on all
interfaces for the containers to reach it — for the default engine, start it with
`OLLAMA_HOST=0.0.0.0` (see [docs/DEV.md](docs/DEV.md) §1).

First run: assign a model to each role at `/inference/`, then drop a document into the
library and ask a question. A surface whose role has nothing assigned says so plainly rather
than pretending.

**[docs/DEV.md](docs/DEV.md) is the full guide** — the environment variables, running
natively against the containerised database, the test suite, the verification ladder, and
the optional image-generation and transcription engines.

**AI agents and contributors: read [AGENTS.md](AGENTS.md)** — the working standards for any
change to this repository.

---

## Status

**Phase 1 — the walking skeleton runs.** Architecture and design (Phase 0) are
complete and recorded in the ADRs; the platform is running code, not a plan.

What works today, on a developer machine:

- **Offline RAG, end to end** — drop documents in, ask questions, get grounded answers
  that cite where they came from. Documents, categories, and a browsable library are
  all first-class ([ADR 0005](docs/adr/0005-rag-module-architecture.md),
  [ADR 0009](docs/adr/0009-document-store-and-categories.md)).
- **An operator-managed model registry** — register engines and models, bind them to
  roles, and swap any of them at runtime without editing config or restarting
  ([ADR 0010](docs/adr/0010-model-management-framework.md)).
- **Image generation** — the first non-RAG capability, proving the model framework
  extends to a second engine with no framework changes
  ([ADR 0012](docs/adr/0012-image-generation-engine-adapter.md)).
- **An execution queue** — every model call in the platform now runs through a single,
  priority-ordered, memory-aware admission point, visible and operable at `/queue/`
  ([ADR 0013](docs/adr/0013-inference-execution-queue.md)).
- **Media ingestion** — video and audio are transcribed, images and scanned PDFs are
  read by a vision model, and the resulting text is searchable and citable *with a
  timestamp or a page number*. Long jobs show live progress and resume after a restart
  ([ADR 0014](docs/adr/0014-media-ingestion.md)). It is off by default: set
  `FARABUNKER_FEATURES` to include `media` (the default is `vision` alone), and video
  and audio additionally need the transcription server running natively on the host.
- **A conversational agent with tools** — a permanent chat surface at `/chat/` where an
  agent answers by *using* the platform: searching and asking the document library,
  generating an image, reporting what models are bound, delegating to another agent, or
  running a fixed multi-step flow. Every capability is registered once, as a tool, and the
  same registry will one day serve external callers ([ADR 0015](docs/adr/0015-agent-layer-and-tool-contract.md)).
- **Identity & Auth (IA-1 and IA-2)** — the box runs **open by default: no
  accounts, no login, every page reachable exactly as before.** An operator can
  switch it to accounts (a `personal` household posture, or an `enterprise` one)
  with a four-command sequence; from then on, every conversation, generated
  image, and Ask answer belongs to whoever made it, an append-only audit trail
  records who did what, and administering the box (running jobs, managing
  documents) is a separate permission from reading other people's content, off
  by default ([`identity/README.md`](identity/README.md)). In the `enterprise`
  posture, the box can also be **partitioned with entitlements**: named labels
  granted to accounts and groups restrict which documents a person may read,
  which tools and models an agent turn may reach on their behalf, and which
  agents and flows they see at all — and a conversation can be shared, to read
  or to post in, without widening either of those.

Next up: sharper retrieval (a relevance floor, a tunable result count, hybrid
keyword+vector search, and a search page that skips the model entirely), honest copy
about what tabular data can and can't answer, and per-page routing for PDFs that mix
typed and scanned pages. Then the MCP edge, then tenancy, then Phase 2's posture and
isolation work. See:

- [docs/DEV.md](docs/DEV.md) — the full path from a clone to a running stack, and the test suite
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the layers, contracts, and security model
- [docs/ROADMAP.md](docs/ROADMAP.md) — the phased build order (walking-skeleton-first)
- [docs/EXTENDING.md](docs/EXTENDING.md) — add a tool, a workstream panel, or a settings page
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — back up and restore your data
- [docs/BUSINESS.md](docs/BUSINESS.md) — open-source direction, licensing, monetization
- [docs/BRAND.md](docs/BRAND.md) — the name, its rationale, and the availability check

---

## Repository layout

```
farabunker/
├── identity/        # Who exists, what posture this box is in, and what has been
│   │                #   done to it: users, the open/personal/enterprise posture,
│   │                #   the audit trail. Base of the platform — imports nothing
│   │                #   from agents/, tools/ or models/.
│   └── contracts/   # Pure, Django-free: Principal, the posture names, the
│                    #   closed audit-action vocabulary, the owned-rows registry
├── foundation/      # Shared, feature-agnostic platform code: format/files helpers,
│   │                #   the ops (backup/restore) and setup (engine-install) apps, the
│   │                #   shared page shell
│   ├── ops/         # Operator tooling: manage.py backup / restore
│   └── setup/       # The universal engine-install page
├── models/          # Model-management framework: contracts, registry, execution queue
│   ├── contracts/   # Pure, Django-free platform contracts: roles, job kinds, the
│   │                #   queue seam, registry bindings, the inference gateway
│   ├── registry/    # Model registry (Config & Secrets): which engine + model backs
│   │                #   each role, and the ACCESS console UI to manage it
│   └── queue/       # The execution queue: job kinds, scheduling, the worker
├── agents/          # The agent layer: the tool contract, the turn runtime, and /chat/
│   ├── contracts/   # The tool contract and registry every feature registers against
│   ├── runtime/     # The bounded turn and the flow runner
│   └── chat/        # The `/chat/` surface
├── tools/           # Plug-in services
│   ├── rag/         # First module: offline document Q&A (text, tabular, video, audio, images, scans)
│   ├── vision/      # Second module: local image generation
│   └── home/        # Future: local home automation
├── config/          # Django settings and the URL root
├── scripts/         # Developer and operator tooling (preview stacks, test helpers)
├── posture/         # Network/OS posture profiles (airgap, isolated-lan, gated-sync)
├── deploy/          # Provisioning for a bunker box (compose, images, hardening)
├── docs/            # Architecture, decisions, roadmap, business, brand
├── AGENTS.md        # Working standards for any contributor, human or AI
├── LICENSE          # AGPL-3.0 (provisional)
├── LICENSING.md     # Dual-license terms (open source + commercial)
├── CONTRIBUTING.md  # How to contribute + the CLA process
├── CLA.md           # Contributor License Agreement (draft, needs legal review)
├── SECURITY.md      # How to report vulnerabilities
└── CODE_OF_CONDUCT.md
```
