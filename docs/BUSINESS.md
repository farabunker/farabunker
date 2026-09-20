# farabunker — Open Source Direction & Business Model

This document records the project's licensing direction, the reasoning behind going open
source, and how an open project with **no subscription** can still be sustainable.

- [1. Why open source](#1-why-open-source)
- [2. Licensing: AGPL vs. BSL](#2-licensing-agpl-vs-bsl)
- [3. The three levers we keep](#3-the-three-levers-we-keep)
- [4. Monetization model](#4-monetization-model)
- [5. Market & timing](#5-market--timing)
- [6. Open decisions](#6-open-decisions)

---

## 1. Why open source

farabunker is **open source by design**, not by default. The reasoning is specific to
this product:

- **Trust is the product.** The pitch is "assume everything is compromised, so run it on a
  box that can't phone home." That claim is not credible from a black box. For a security
  product, **auditability is the value proposition** — the security-conscious buyer is
  exactly the person who won't trust a binary they can't inspect. The strongest security
  tools in the world (OpenSSL, WireGuard, Signal's protocol, GrapheneOS, Home Assistant)
  are open for precisely this reason.
- **The platform is built to be extended.** The module contract (see
  [ARCHITECTURE.md](ARCHITECTURE.md#5-the-module-contract)) invites a community to add
  cameras, voice, home automation, and more. An open platform grows far faster than a
  solo closed product.
- **It matches the brand.** "The trustworthy, no-subscription, no-phone-home option" only
  works if people can verify the claim.

Open source here is **deliberate, not naive.** The naive version (permissive license,
everything free, no plan) gives away all leverage. The deliberate version keeps the levers
in §3.

---

## 2. Licensing: AGPL vs. BSL

Two license families fit a "source is visible, but I keep commercial leverage" strategy.
The one-line distinction:

> **AGPL is certified open source that protects you through copyleft; BSL is
> source-available that protects you through direct commercial restrictions but isn't
> "open source" until it later converts.**

| | **AGPL-3.0** (GNU Affero GPL) | **BSL** (Business Source License) |
|---|---|---|
| **Category** | True open source, OSI-approved | Source-available (not OSI open source until it converts) |
| **How it protects you** | Copyleft: any derivative — *including running it as a network service* — must publish its source under AGPL | You write the terms: typically free for everything *except* selling a competing product/hosted service |
| **Time element** | Perpetual, unchanging | Each version auto-converts to real open source (Apache/GPL) after a "Change Date," usually 3–4 years |
| **Commercial control** | Competitors *can* sell it — but must keep their version open | Competitors *cannot* sell a competing farabunker during the restricted window |
| **Revenue lever** | Dual-license: sell a commercial license to firms that don't want AGPL's obligations (MongoDB/MySQL model) | The license itself reserves the commercial product for you |
| **Used by** | Mastodon, Nextcloud, Grafana (historically), Ghost | HashiCorp (Terraform), Sentry, CockroachDB, MariaDB MaxScale |

### AGPL-3.0

**Pros**

- Genuinely open source, which maximizes community trust and goodwill — and for a product
  whose entire pitch is auditability, the "certified open source" label carries real weight.
- Closes the SaaS loophole: nobody can run a closed, hosted fork without publishing source.
- Clean dual-licensing revenue path (sell commercial licenses to those who can't accept
  copyleft).

**Cons**

- Some companies ban AGPL internally (Google's policy is the well-known example), which can
  dampen enterprise adoption and contributions.
- Copyleft scares off a few commercial integrators.
- Does not stop a competitor from *selling* the software — only forces them to keep their
  version open.

### BSL (Business Source License)

**Pros**

- Source stays visible and auditable, so it still supports the trust brand.
- Strongest *direct* commercial control — a hardware or hosting competitor cannot undercut
  you with your own software during the restricted window.
- Still becomes fully open eventually (not a permanent enclosure).
- Maximum flexibility: you author the "Additional Use Grant" that defines what's free.

**Cons**

- **Not open source** during the restricted window — purists, some communities, and Linux
  distros will say so, which slightly undercuts the "we're the trustworthy open option"
  narrative even though the code is readable.
- You carry the burden of writing and defending custom terms.
- Less automatic goodwill than AGPL's OSI stamp.

### Decision: dual-licensing under a copyleft core ("Path A")

**Direction (decided): AGPL-family copyleft + a broad-grant CLA + a registered trademark +
a commercial "close-the-loop" license.** The remaining sub-decision is the exact license
text — **AGPL-3.0 vs. BSL** — leaning AGPL because trust is the product and the OSI "open
source" badge outweighs BSL's tighter lock. Choose BSL only if the dominant fear becomes a
company mass-producing competing bunker *hardware* off our code. Tracked in
[adr/0002-license-direction.md](adr/0002-license-direction.md) (license text) and
[adr/0003-dual-licensing-and-cla.md](adr/0003-dual-licensing-and-cla.md) (the model).

### How dual-licensing works

The copyright holder can license the *same* code under multiple licenses simultaneously.
farabunker is offered two ways:

1. **Free, under AGPL** — anyone may use, modify, and ship it. AGPL's copyleft is the
   "poison pill": any derivative, *including a hosted network service*, must also be
   released open-source under AGPL.
2. **Paid commercial license** — companies that want to embed farabunker in a *closed*
   product, or run it as a service without publishing their code, buy an exception that
   waives the copyleft obligation.

Precise framing: commercial use isn't what costs money — commercial use is also free under
AGPL *if the company keeps its derivative open*. What they pay for is the right to **close
the loop**. Framed to users: *"closing the loop is possible, but you pay to support the
project."* The AGPL copyleft is what *creates* this revenue — without the poison pill,
nobody would ever need to pay. This is the MySQL / MongoDB / Qt / Grafana model.

**Common users are fully served.** Individuals, hobbyists, homelabbers, and off-grid users
never pay — they get the entire platform free, forever, with source. Only commercial
closers pay. The "give it to the people" goal and the revenue goal are satisfied at once.

### Contributions and the CLA

By default a contributor owns the copyright to their contribution, delivered under AGPL.
That lets us *use* it in the open project but **not** relicense it commercially — which
would quietly kill dual-licensing as the community grows. The fix is a **Contributor
License Agreement with a broad license grant**: contributors keep their copyright but grant
us an irrevocable right to relicense/sublicense (including proprietary). We prefer this over
full copyright assignment — same commercial freedom, far less friction for contributors.

The CLA must be in place **before the first external contribution**; retrofitting it later
means chasing down every past contributor and often fails permanently.

### Why we start at "A", not "B"

"Path B" would be AGPL with no CLA (inbound = outbound) — maximum purity, no dual-licensing.
We start at A because of a one-way door:

- **A → B is always possible.** Holding all commercial rights (our copyright + the CLA
  grant) means we can loosen anytime — stop selling commercial licenses, drop the CLA, even
  relicense more permissively.
- **B → A is usually impossible.** Without a CLA, contributions are owned by their authors
  under AGPL only; moving to dual-licensing later would require every past contributor to
  sign on. One holdout welds the door shut.

A is the superset of futures — it preserves every option, including becoming B. It also
keeps the founder as steward of direction. Both AGPL protections (no one may close-source a
derivative) are identical in A and B; A merely adds the ability to sell the exception.

### The gap the license can't close

AGPL only monetizes those who want to *close* a fork. It does nothing against a competitor
who forks the open code and competes while keeping their fork **open** — they owe nothing
and break no rules. In a commoditized-software world that (not closed forks) is the real
threat. What defends against it is **not the license but the trademark, brand, ecosystem,
and audience** — they can ship the code but can't be "farabunker." Register the mark early.

---

## 3. The three levers we keep

Open source does not mean giving away leverage. We retain three:

1. **License choice** (§2) — AGPL or BSL keeps the code auditable while blocking a
   closed-source competitor from simply taking and reselling it.
2. **Trademark** — the *code* is open; the name "farabunker" is a trademark we own.
   Anyone may fork the code; nobody else may sell "a farabunker." (The Red Hat model.)
3. **Open-core boundary** — the core platform and basic modules are open; certain premium
   modules, a polished management console, or hardened enterprise features can be
   separately licensed. See the `tools/` split.

---

## 4. Monetization model

No subscription — that's a deliberate demand driver. Revenue therefore comes from paths
that an open project *strengthens* rather than undermines, ranked by fit:

1. **Hardware / "bunker in a box."** Pre-built, pre-flashed appliances and kits at tiers —
   a prosumer mini-PC box, a solar-friendly off-grid unit, a camera/NVR bundle. People pay
   for convenience and a known-good config even when the software is free. (Home Assistant
   Green/Yellow, System76, Framework model.) **This is the most direct revenue of the
   five.**
2. **The content channel (YouTube).** Build-in-public content monetizes independently of the
   code — ad revenue, sponsorships (storage, GPU, SBC, solar, privacy brands), and it is the
   top of the funnel for everything else. The channel is expected to be a larger revenue
   source than the software in the first year.
3. **Courses / paid guides / community.** "Build your own farabunker" course, hardening
   workshops, a paid community tier. The audience is doing a hands-on project; they buy
   learning.
4. **Premium modules & support.** Open core, paid pro modules (advanced camera analytics,
   multi-site sync, management console), plus paid setup/support and consulting for
   done-for-you installs, and eventually SMB/enterprise deployments.
5. **Sponsorship / donations.** GitHub Sponsors, Patreon, Open Collective — small but real
   for a mission-driven security project, and a traction signal to bigger partners.

**The shape:** open-core software + paid hardware + a content channel that funds and feeds
both. Home Assistant is the closest template — fully open, no subscription, monetized
through a hardware line and an optional add-on, backed by a large community. We swap their
optional cloud for our content + hardware.

---

## 5. Market & timing

**Thesis:** as AI grows more powerful and breaches more common (even frontier labs get
compromised), demand for capable-but-offline AI rises, and a no-subscription option is in
high demand.

Directionally sound, with two honest caveats to plan around:

- **The beachhead is prosumer, not mass-market — yet.** Near-term demand is homelabbers,
  privacy advocates, preppers, off-grid users, security pros, and compliance-driven SMBs.
  That's a real, monetizable, sponsor-rich niche — but plan for the niche and let the
  mainstream "offline AI" moment be upside, not the base case.
- **Offline capability is a trend we ride, not fight.** The architecture already bets on
  models getting more capable on modest hardware (see the plug-and-play abstractions in
  [ARCHITECTURE.md](ARCHITECTURE.md#3-plug-and-play-how-we-avoid-betting-on-hardware)),
  which is exactly what makes this more viable each year. Keep that abstraction honest so
  the product improves automatically as the curve moves.

---

## 6. Open decisions

**Decided:**

- **Licensing direction** — dual-licensing under a copyleft core (Path A): AGPL-family +
  broad-grant CLA + trademark + commercial "close-the-loop" license.
- **Business entity** — pc analytics LLC holds the project, and is the entity that will hold
  the trademark and sell hardware.

Still open:

- **Final license text** — AGPL-3.0 vs. BSL, leaning AGPL (see [adr/0002-license-direction.md](adr/0002-license-direction.md)).
- **CLA tooling** — which CLA (e.g. an Apache-style ICLA/CCLA) and how it's collected (CLA bot on PRs). Must be live before the first external contribution.
- **Trademark registration** — when and in which jurisdictions. (The holder is settled; the
  registration is not.)
- **Open-core boundary** — exactly which modules/features are premium.
