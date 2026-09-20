# ADR 0007 — Reference hardware

**Status:** Accepted
**Date:** 2026-08-17

## Context

The design is deliberately hardware-agnostic ([ARCHITECTURE.md §3](../ARCHITECTURE.md#3-plug-and-play-how-we-avoid-betting-on-hardware)),
but we need concrete targets to develop and test against. The maintainer has a MacBook Pro
(Apple M4 Max, 48 GB unified memory) and a desktop with an NVIDIA GPU.

## Decision

- **Primary dev target:** MacBook Pro **M4 Max, 48 GB**. Apple Silicon's unified memory runs
  7B–34B models comfortably (up to ~70B quantized), which covers all of Phase 1 and a capable
  offline assistant. **Ollama runs natively** (Metal); Postgres/pgvector + Django run in Docker,
  pointing at native Ollama via the configurable URL ([ADR 0006](0006-containerization-and-isolation.md)).
- **CUDA / appliance validation target:** the NVIDIA desktop — used to validate the Linux +
  GPU-passthrough container path, not as a daily driver.
- **Appliance profile (documented in `deploy/` later):** a hardened Linux mini-PC + NVIDIA GPU.

## Consequences

- Development is fully possible on the laptop; the desktop is a secondary validation box, not a
  requirement.
- Closes the last open Phase 0 item — the platform is ready for Phase 1 implementation.
