# ADR 0004 — Application & data stack

**Status:** Accepted
**Date:** 2026-08-17

## Context

Phase 1 (the RAG walking skeleton) needs a concrete stack. Constraints from the project:

- **Offline-native** — everything must run and be packageable with no internet at runtime.
- **Plug-and-play models** — the stack must not hardcode a model or inference engine
  (see [ARCHITECTURE.md §3](../ARCHITECTURE.md#3-plug-and-play-how-we-avoid-betting-on-hardware)).
- **One primary language** for the core, with room to add others for specific features.
- Fast to build a real vertical slice; batteries-included is a plus for a solo/small team.

## Decision

- **Language:** **Python** for the core (AI, ingestion, retrieval all live naturally here).
- **Web / API / UI:** **Django** (with Django REST Framework for the module/service APIs).
  Node/TypeScript may be added later for richer frontend features (the ACCESS layer), but the
  core and first UI are Django. Django's built-in admin, auth, ORM, and migrations directly
  serve the core's **Identity & Auth** and **Storage** services for free.

  > **Superseded 2026-09-10 (C-55):** Django REST Framework was removed; the two JSON views
  > are plain Django.
- **Data:** **PostgreSQL** as the single datastore, with the **pgvector** extension for
  embeddings. One database holds vector chunks, document metadata, tabular rows, and chat
  memory. (This is the local, self-hosted equivalent of the Supabase the reference template
  used — Supabase *is* Postgres + pgvector.)
- **Inference:** **Ollama** as the v1 local engine behind the **Inference Gateway**, exposing
  an **OpenAI-compatible** chat + embeddings API. Models are config-driven and swappable;
  the engine can later become vLLM / llama.cpp without touching module code.
- **RAG framework:** **LlamaIndex** — see [0005-rag-module-architecture.md](0005-rag-module-architecture.md).

## Consequences

- **Single-language core** → simpler offline packaging and a smaller cognitive/attack surface
  than a polyglot core.
- **Postgres + pgvector is one dependency** doing four jobs (vectors, metadata, tabular rows,
  memory), which keeps the appliance simple.
- **Ollama's OpenAI-compatible API** means the RAG module targets a stable contract, not a
  specific engine — the plug-and-play bet, realized.
- **Django gives us the core services cheaply** (admin UI for document/user management, auth,
  ORM), accelerating Phase 1.
- **Watch-outs:** LLM responses want **streaming**, so we will use Django's async views / ASGI
  (and possibly Channels) for the query endpoint rather than classic sync views. Long-running
  ingestion should run out-of-band (a management command / task worker), not in a request.
- **Still open (later ADR):** base OS, containerization, and module-sandboxing mechanics
  (see [ARCHITECTURE.md §8](../ARCHITECTURE.md#8-open-questions)).
