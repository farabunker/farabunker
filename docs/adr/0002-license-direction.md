# ADR 0002 — License direction

**Status:** Accepted
**Date:** 2026-08-17

> Accepted 2026-09-20 by owner delegation during release preparation; AGPL-3.0 + dual
> licensing as shipped. The copyright is held by **pc analytics LLC**, the entity that grants
> both licenses. CLA legal review remains an open action.

## Context

farabunker will be **open source** — see [../BUSINESS.md](../BUSINESS.md#1-why-open-source).
For a product whose value proposition is trust and auditability, a black box is not
credible; open source is the mechanism that makes the security claim believable, and the
module contract is built to invite community extension.

Open source must be *deliberate*, keeping commercial leverage. Two license families fit a
"source is auditable, but we retain commercial control" strategy: **AGPL-3.0** (true open
source, protects via copyleft) and **BSL** (source-available, protects via direct
commercial restriction, converts to open source after a change date). A full pros/cons
comparison is in [../BUSINESS.md](../BUSINESS.md#2-licensing-agpl-vs-bsl).

## Options

1. **AGPL-3.0 + dual-licensing + trademark** *(recommended)* — certified open source; the
   OSI badge reinforces the trust brand; dual-licensing and the trademark carry commercial
   leverage. Does not stop a competitor selling it, only forces them to stay open.
2. **BSL + trademark** — strongest direct commercial lock (competitors can't sell a
   competing bunker during the restricted window); source stays auditable; but it is not
   "open source" until it converts, which slightly undercuts the trust narrative.
3. **Permissive (MIT/Apache)** — rejected: gives away all leverage; lets anyone close and
   resell without contributing back.

## Decision

The **dual-licensing structure is decided** in [0003-dual-licensing-and-cla.md](0003-dual-licensing-and-cla.md)
(copyleft core + broad-grant CLA + trademark + commercial license). This ADR covers only the
remaining sub-choice: the **exact copyleft license text**.

**AGPL-3.0** (Option 1), because trust is the product and the "certified open source" label
is worth more than BSL's tighter lock; the real moat is the brand + hardware, not the
license. BSL (Option 2) was the alternative had the dominant concern become a company
mass-producing competing bunker *hardware* from our code; it did not, and the text is now
settled.

## Consequences

- License file at repo root, carrying the project's copyright notice above the verbatim
  AGPL-3.0 text. Per-file SPDX headers and a `NOTICE` file are backlog, not shipped.
- Set up the dual-licensing/CLA path so commercial licenses can be sold. The CLA is drafted
  ([CLA.md](../../CLA.md)) but not legally reviewed, so external contributions are held
  until it is finalized; that review is the one open action this ADR leaves behind.
- Register the "farabunker" trademark.
