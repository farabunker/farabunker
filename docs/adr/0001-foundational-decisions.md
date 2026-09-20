# ADR 0001 — Foundational architecture decisions

**Status:** Accepted (Phase 0)
**Date:** 2026-08-17

Architecture Decision Records capture *why* we chose something, so future changes are
made with the original context in view. This first record collects the foundational
decisions from the initial design session.

## Context

farabunker is an offline-first platform for running capable AI services with no
dependency on the public internet, for both urban-privacy and off-grid-autonomy users.
Key constraints from the project owner:

- It must support the **full offline spectrum** — from true air-gap to an isolated LAN
  subnet with no WAN — as a *universal* tool, not a single fixed posture.
- It must **not bet on specific hardware**; as models and silicon mature, the platform
  should improve by plugging in better implementations, not by rewrites.
- The first concrete service is **offline RAG**, but a proper platform structure matters.

## Decisions

1. **Abstraction is the product.** Hardware, model, inference engine, and vector store
   are swappable implementations behind stable internal contracts. We commit to the
   OpenAI-compatible inference API and a "vector store + embeddings" abstraction as the
   stable seams; everything under them is replaceable.

2. **The platform is the core, not the apps.** We build the CORE service substrate and
   define a **module contract**. RAG and home automation are the first two modules.

3. **Offline posture is a selectable profile, one codebase.** `airgap`, `isolated-lan`,
   and `gated-sync` are profiles over the same build. Isolation is enforced *below* the
   application layer so modules cannot escape it.

4. **Walking-skeleton-first delivery.** Rather than a pure skeleton (risks over-
   abstraction) or RAG-as-monolith (risks a rewrite), we build a thin end-to-end RAG
   slice that exercises every core contract, proving the interfaces are real before
   broadening.

5. **No phone-home; updates via a verified airlock.** The only inbound path is signed,
   verified update packages through the airlock. Nothing initiates outbound on its own.

## Consequences

- Module authors get a stable, capability-scoped contract and never touch engine/hardware
  details — enabling third-party and future modules.
- We accept some upfront cost defining contracts before they're all exercised; the RAG
  skeleton is the mitigation.
- Choosing the reference box, base OS, and v1 implementations (inference engine, vector
  store) remains open and is the exit work for Phase 0.
