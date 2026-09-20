# ADR 0003 — Dual-licensing model and CLA ("Path A")

**Status:** Accepted
**Date:** 2026-08-17

> 2026-09-20, release preparation: the holding entity is named. **pc analytics LLC** owns
> farabunker, holds the copyright and the trademark, is the grantee named in
> [CLA.md](../../CLA.md), and is the party that sells the commercial license. The broad
> grant below runs to that entity -- a CLA naming only "the project" would not let an LLC
> relicense, which is the mechanism this ADR exists to protect.

## Context

farabunker is open source (ADR 0001, ADR 0002). Given that coding agents are
commoditizing software, code secrecy is not a durable moat — but the project is also
pursued partly for revenue, and the owner wants to prevent others from taking the work
proprietary and to earn from commercial use. Two structures were considered:

- **Path A** — copyleft (AGPL-family) + a Contributor License Agreement (CLA) granting the
  project relicensing rights + a paid commercial license for those who want to "close the
  loop." Founder-stewarded; dual-licensing is possible.
- **Path B** — the same copyleft license with *no* CLA (inbound = outbound). Maximum
  community purity; dual-licensing is not possible.

Key facts that drove the decision:

1. **Copyleft, not the CLA, is what stops others from closing a derivative.** That
   protection is identical in A and B. The CLA only determines whether *we* can additionally
   sell commercial exceptions.
2. **Directionality is a one-way door.** A → B is always possible (we hold the rights and
   can loosen anytime). B → A is usually impossible (contributions owned by their authors
   under copyleft can't be retroactively dual-licensed without every contributor's consent).
3. **Common users are served either way** — under AGPL the free tier gives individuals the
   full platform forever; only commercial closers pay.

## Decision

Adopt **Path A**:

- License the core under an **AGPL-family copyleft** license (exact text decided in
  ADR 0002: AGPL-3.0, ratified 2026-09-20).
- Require a **CLA with a broad license grant** (contributors keep copyright, grant the
  project irrevocable relicensing/sublicensing rights) — *not* full copyright assignment,
  to minimize contributor friction. Must be enforced from the first external contribution.
- Offer a **paid commercial license** ("closing the loop is possible, but you pay to support
  the project") for proprietary/closed use.
- **Register the "farabunker" trademark** — the real defense against *open* competing
  forks, which the license does not prevent.

## Consequences

- We preserve maximum future optionality: we can always relax to Path B (or a more
  permissive license) later; we could never move the other way.
- The founder remains steward of direction and holds the commercial rights across the whole
  codebase, including community contributions.
- We accept a mild contributor-fairness asymmetry (contributors' work can be commercialized
  by the project but not by them); mitigated by the broad-grant CLA (vs. assignment),
  transparency, and the fact that the AGPL side stays open forever.
- Operational setup required: choose CLA text, add a CLA bot to the contribution flow, and
  stand up the commercial-license offering. Tracked in [../BUSINESS.md](../BUSINESS.md#6-open-decisions).
- **The license alone does not stop open forks competing on hardware/service** — brand,
  trademark, ecosystem, and audience do. Register the mark early.
