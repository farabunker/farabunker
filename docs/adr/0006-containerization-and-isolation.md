# ADR 0006 — Containerization, data persistence & isolation

**Status:** Accepted
**Date:** 2026-08-17

## Context

farabunker needs a deployment structure that (a) reproducibly packages the appliance, (b)
enforces the [module sandbox and default-deny egress](../ARCHITECTURE.md#2-security-posture-the-offline-spectrum)
the security model requires, and (c) keeps operator data durable across restarts and updates.
The stack is Python/Django + Postgres/pgvector + Ollama ([ADR 0004](0004-application-stack.md)).

## Decision

### 1. Containers are the deployment backbone

The appliance runs as a **container stack orchestrated by Compose**. Containers are how the
ISOLATION layer becomes real: modules run in their own containers on an **internal-only
network with no route to the WAN gateway**, so a compromised module *physically cannot* reach
the internet — this enforces default-deny egress below the application, exactly as the
architecture requires. A Compose stack is also the reproducible, signable unit the
[airlock](../ARCHITECTURE.md#6-the-update-airlock) ships as an update, and the artifact the
[`deploy/`](../../deploy/) layer packages as "bunker in a box."

### 2. Podman preferred for the appliance; Docker fine for dev

Both are supported (Compose-compatible). **Podman** is preferred for the appliance because it
is **rootless and daemonless** by default — a materially better security posture for a
security product. **Docker** is fine for development familiarity. Code and compose files target
the common subset so either works.

### 3. Durable data lives OUTSIDE the container lifecycle

**Rule: processes are ephemeral; data is durable.** All persistent state lives on **host-mounted
volumes**, never on a container's writable layer. A container restart, rebuild, or image upgrade
therefore never touches the data.

| Ephemeral (rebuildable containers) | Durable (host-mounted volumes) |
|---|---|
| Django/ASGI `web`, module containers, Ollama process | Postgres data dir, ingested documents, vector data, model files |

**Database specifics** (addresses "the database must not be wiped by a restart"):

- The DB is reached via a **configurable connection URL** (`DATABASE_URL`), the same
  swap-by-config pattern used for the Inference Gateway.
- **Default:** Postgres runs as a Compose service whose data directory is a **host bind-mount /
  named volume**. Restarts and updates preserve it.
- **Supported alternative:** point `DATABASE_URL` at a **native/external Postgres** running on
  the host (fully outside Docker) — no code change. Operators who want the DB process off-container
  entirely choose this.

Either way, the *data* lives on the host, outside any container's lifecycle.

### 4. Inference / GPU split

Ollama is reached via a **configurable URL** ([ADR 0004](0004-application-stack.md)):

- **Linux appliance:** Ollama in a container with GPU passthrough (NVIDIA Container Toolkit).
- **macOS dev:** Docker cannot reach the Metal GPU, so run **Ollama natively on the host** and
  point the Gateway at it. Same code both ways.

### 5. Isolation is a strong baseline, not a hard boundary

Containers share the host kernel, so v1 hardens them: **rootless (Podman), dropped capabilities,
seccomp, read-only rootfs, `no-new-privileges`, and `--network none` / internal networks** for
modules. Stronger isolation (gVisor, microVMs) is **deferred** for the strictest `airgap`
deployments and will be revisited as a later ADR.

### 6. Out-of-band ingestion via a dedicated `watcher` service

Browser uploads land in a **watch-inbox** (`settings.INGEST_INBOX_DIR`, under the host-mounted
`DATA_DIR` alongside the managed document store — see §3 above), rather than being ingested
synchronously inside the request/response cycle of the `web` process. A separate `watcher`
Compose service — same image as `web`, running `python manage.py ingest_watch /app/data/inbox` —
watches that folder and ingests new/changed files as they appear. It shares `web`'s `.:/app`
bind mount (so it sees the same host-mounted `data/inbox`), depends on `db` being healthy, and
runs with `restart: unless-stopped` so it keeps watching across restarts. Splitting ingestion
into its own long-running service keeps the `web` process request/response cycle free of
watch-loop/ingest work and keeps the ephemeral/durable split of §3 intact — the watcher container
is itself ephemeral; only the inbox contents it reads and the store it writes into are durable.

The execution queue's `worker` service (`python manage.py run_jobs`, [ADR 0013](0013-inference-execution-queue.md))
joined this same same-image-different-command pattern alongside `watcher`.

## Intended compose layout (illustrative — not yet built)

```
services:
  web        Django/ASGI — core + RAG UI/API        (ephemeral)
  db         Postgres + pgvector                     (process ephemeral; DATA on host volume)
  inference  Ollama — the Inference Gateway target   (GPU on Linux; native on macOS dev)
  # modules/*  one container per module, later, on an internal-only (no-WAN) network

volumes (host-mounted, durable):
  pgdata        → Postgres data directory
  documents     → ingested source documents
  models        → Ollama / model weights
```

## Consequences

- The module sandbox + default-deny egress are enforced by container networking, not trusted to
  app code — the core security promise, realized at the infra layer.
- Operator data survives restarts, rebuilds, and airlock updates because it never lives inside a
  container.
- Configurable `DATABASE_URL` / Ollama URL keep the stack flexible (containerized or native) and
  consistent with the plug-and-play principle.
- Podman + hardening gives a strong baseline; the door stays open to microVM-grade isolation for
  the strictest postures.
- Resolves most of the open "base OS & isolation tech" question ([ARCHITECTURE.md §8](../ARCHITECTURE.md#8-open-questions)):
  the appliance is a **hardened Linux host running a hardened container stack**; only the
  reference hardware box remains open.

## Amendment (2026-08-24) — Amended by ADR 0014: `ffmpeg` joins the one shared image (§6)

[ADR 0014](0014-media-ingestion.md) (media ingestion) added `ffmpeg` to the
single shared image §6's `watcher` (and, since ADR 0013, `worker`) service
runs from — installed by `apt` in the same `RUN` layer as `libpq5`.

The reasoning, in §6's own terms:

- **Same category as `libpq5`.** Codec tooling: no weights, no runtime
  downloads, no network access of its own. It is a decode library the
  application shells out to, not a model engine.
- **Same image, not a second one.** Both services that need it share this
  image already — the `watcher` probes a media file's duration when it
  stages one, and the `worker` extracts and slices audio when it transcribes
  one. Splitting them would break §6's same-image-different-command
  invariant to save nothing.
- **Not on the host.** The native-host rule (§4 above, and
  [ADR 0012](0012-image-generation-engine-adapter.md)'s Context, whose
  second property — "portable" — states it: the engine runs natively on the
  host, reached from the `web` container the way Ollama already is; D12 is
  where that host's base URL is recorded as the one default the platform
  bakes in) exists for engines that need the GPU. `ffmpeg` here is pure CPU decode, so it belongs
  in the image where the code that calls it lives.

**The cost, measured on this project's own images (arm64, Debian trixie,
`--no-install-recommends`), 2026-08-24 — two independent measurements:**

| Measurement | Result |
|---|---|
| The `apt-get install` layer, pre-media image vs. media image (`docker image history`) | 4.2 MB → 422 MB — **+418 MB** |
| Packages present only in the media image, `Installed-Size` summed (`dpkg-query`) | **196 packages, ≈407 MB** — of which the `ffmpeg` package itself is ≈2.8 MB |

Roughly **410–420 MB**, and essentially all of it is the codec dependency
closure rather than the binary. Recorded because §3's ephemeral/durable
split makes image size an operator-visible cost every rebuild and every
airlock update pays, and because a number this large should be a decision
someone made on purpose rather than a surprise found later.

**A note on the "Intended compose layout" section above.** Its heading still
says *illustrative — not yet built*, which was true when this ADR was
written and is no longer. The stack that exists is `web` + `db` +
`watcher` (§6) + `worker` ([ADR 0013](0013-inference-execution-queue.md)),
with `compose.preview.yaml` alongside it for per-branch isolated stacks
([ADR 0011](0011-branch-preview-stacks.md)); `inference` is not a service
here, because Ollama runs natively on the host per §4. The section is left
as written — it recorded an intention accurately — and this note records
what was actually built.

**Operational consequence:** deploying this change is a **rebuild**, not a
restart. The bind mount that normally makes a code change take effect
immediately does not carry an image-level package, so `docker compose up -d
--build` (plus the `watcher`/`worker` restarts ADR 0013's Consequences
already require for any queue/ingest change) is the deploy shape.
