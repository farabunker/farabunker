# tools/ — one folder per tool-owning feature

A future tool is one new folder here, not a new top-level directory —
and the fact that every folder here is the SAME KIND OF THING (a feature
that registers capability-scoped operations an agent may eventually call
as tools) is the thing this column exists to keep visible in the tree.

- **[`rag/`](rag/README.md)** — offline document Q&A (text, tabular,
  video, audio, images, scans). Farabunker's first module. Django app,
  label `rag`.
- **[`vision/`](vision/README.md)** — local image generation. Farabunker's
  second feature module. Django app, label `vision`.
- **[`home/`](home/README.md)** — home automation, planned for Phase 3.
  Django app, label `home`.

## The import law

- **Rule 1 — pure leaves are universally importable.** A `tools/*` app
  may freely import `models/contracts/`, `foundation/format.py`,
  `foundation/files.py`, and `agents/contracts/`.
- **Rule 2 — Django apps are column-private.** Each folder here is a
  Django app and is COLUMN-PRIVATE: `tools.rag` does not import
  `tools.vision`, and neither is importable from `models/`, `agents/`,
  or `foundation/`. The one sanctioned cross-column import a `tools/*`
  app may make is `models.registry.bindings` — and only that module;
  `models.registry.models` and `models.registry.views` stay off-limits.
- **Rule 3 — cross-column *work* goes through a seam, never an import.**
  A `tools/*` app reaches model-management work through the queue
  (`models.contracts.queue.enqueue`/`get_job`) and the gateway
  (`models.contracts.gateway.get_llm*`/`get_embed_model*`/
  `get_image_generator*`/`get_transcriber*`), never by importing
  `models/registry/` or `models/queue/` directly.

Each feature is also a [§5 module](../docs/ARCHITECTURE.md#5-the-module-contract)
in the platform's own sense: it talks only to declared core capabilities
(inference, vector, storage) and is `network: none` by default — see
each folder's own README for what it consumes and produces.
