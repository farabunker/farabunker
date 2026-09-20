# deploy/ — Provisioning the bunker box

Everything needed to turn a bare machine into a sealed Farabunker: reference images,
compose/orchestration files, and host-hardening automation. Targeted for Phase 4.

The design stays hardware-agnostic (see the plug-and-play abstractions in
[../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#3-plug-and-play-how-we-avoid-betting-on-hardware)),
but this directory will document and test against a chosen **reference box** so there is a
known-good path from bare metal to a working, sealed install.

## Deployment shape ([ADR 0006](../docs/adr/0006-containerization-and-isolation.md))

The appliance ships as a **Compose stack** (Podman preferred; Docker supported):

- Services: `web` (Django/ASGI), `db` (Postgres + pgvector), `inference` (Ollama), and — later —
  one container per module on an **internal-only, no-WAN network** that enforces default-deny egress.
- **Durable data lives on host-mounted volumes** (Postgres data, ingested documents, model
  weights) — never on a container's writable layer — so restarts, rebuilds, and airlock updates
  never wipe operator data. The DB is reached via a configurable `DATABASE_URL` and may instead
  point at a native/external Postgres on the host.
- Hardened containers: rootless (**landed** — `Dockerfile` runs the app as a fixed non-root user,
  uid/gid `10001`, not `root`; see `docs/OPERATIONS.md` §"Upgrading to the non-root container
  user") and `no-new-privileges` (**landed** — set via `security_opt` on every service in both
  compose files), dropped capabilities, seccomp, read-only rootfs. Stronger isolation (gVisor /
  microVMs) is a deferred option for strict `airgap` deployments.
