# posture/ — Network & OS posture profiles

A **posture profile** is a named, versioned network+OS policy the operator selects. The
same Farabunker codebase is meant to run at any point on the offline spectrum; only the
profile changes. Enforcement is designed to live here (and in the isolation layer),
*below* the application, so a compromised module cannot escape it — **this module is a
stub today**: the profiles below are planned, not implemented, and the property currently
rests on policy and review.

Planned profiles (see [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#2-security-posture-the-offline-spectrum)):

- **`airgap`** — no WAN, updates only via physical media through the airlock.
- **`isolated-lan`** — own subnet/VLAN, default-deny egress, trusted LAN for devices & UI.
- **`gated-sync`** — normally offline, with a deliberate operator-initiated update window.

Invariants enforced in every profile: default-deny egress, isolation below the app layer,
no implicit phone-home.
