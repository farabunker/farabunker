# Developer setup (macOS dev)

This is the exact path to get the stack running locally. It brings up
Postgres+pgvector in Docker and Ollama natively on the host (Docker on macOS
can't reach the Metal GPU — see [ADR 0006](adr/0006-containerization-and-isolation.md) §4).
Steps 1–7 get you to a running Django app with a reachable database and
inference gateway; ingest, ask, the document library, the queue, and the
media/image engines each have their own section further down.

## 1. Install Ollama

```bash
brew install ollama
brew services start ollama   # or: ollama serve
```

**No models are pre-configured, and none are pulled for you.** There is no
default model to set up — the platform never presumes one (owner ruling, see
[ADR 0010](adr/0010-model-management-framework.md)'s third amendment). You
pick what to install and which role it backs, in the model console at
`/inference/` once the stack is up (step 5 below). Its "Getting models"
checklist tells you exactly what each role still needs — one chat model and
one embedding model for RAG — and pulling a model is a one-time internet
step you take yourself, on this machine or on another one you transfer from.

Confirm Ollama is reachable:

```bash
curl http://localhost:11434/api/tags
```

## 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

`requirements-dev.txt` pulls in `requirements.txt` (`-r requirements.txt`)
plus the test toolchain, so this one command installs everything a
developer needs. `requirements.txt` alone is the runtime set: it's what
`Dockerfile` installs into the image the `web`, `watcher` and `worker`
services all share, and it deliberately carries nothing test-only
(dependency audit IMAGE-7) -- see
`foundation/ops/tests/test_repo_hygiene.py`.

**Adding a dependency.** Runtime code goes in `requirements.txt`, with a
floor (and a ceiling when there's a reason for one — a private attribute
this codebase depends on, a CVE fix, a breaking change upstream has
announced) and a comment naming the reason, in the style already used
there. Anything needed only for tests or local development (a test
runner, a linter, a debugging tool) goes in `requirements-dev.txt`
instead — never in `requirements.txt`, which is what ships. `lxml` and
`defusedxml` are declared this way as first-party *security*
dependencies (security audit B-7): both used to arrive only
transitively, and together they are what keeps `.docx`/`.xlsx` parsing
from resolving a file-reading external entity —
`tools/rag/tests/test_readers_xxe.py` is the test that backs that claim.

### Which Python

**3.12** — stated in `.python-version` and matching the `python:3.12-slim`
base the container actually runs. Create the virtualenv with that
interpreter:

```bash
python3.12 -m venv .venv
```

A `.venv` on a different minor is a real gap, not a cosmetic one: a
3.13-only construct, or a 3.12 deprecation a newer interpreter has
already dropped, passes every local run and misbehaves in the image
nobody develops against. `python -V` inside `.venv` is the check.

**This repository's own development environment is currently on 3.13 and
does not yet match this file.** That is a known gap, recorded rather than
hidden; closing it means recreating `.venv` with `python3.12`, which is
an owner action on a workspace several sessions share, not a step this
task performs.

**The test `.venv` also carries an older `python-dotenv` than the image
pins:** `constraints.txt` pins `python-dotenv==1.2.3` (the version the
production image installs), but the shared test `.venv` several sessions
run against has `1.0.1` actually installed -- so the four-run test
matrix (`docs/superpowers/plans/…`'s Global Constraint 3) never once
exercises the shipped version's behaviour, only an older one's.

### Bumping a dependency

`requirements.txt` is the **human** file: floors, ceilings and the
reasons for them. `constraints.txt` is **generated**: the exact set that
was installed when the change was verified, so a rebuild months later is
the same image and a pull request shows the version diff. The Dockerfile
installs against both: `pip install -r requirements.txt -c
constraints.txt`.

```bash
# 1. Edit requirements.txt (the floor/ceiling and its comment).
# 2. Regenerate constraints.txt from a THROWAWAY venv -- never the
#    shared dev .venv, and never one borrowed from another worktree --
#    so the generated file carries no local paths and stays reproducible.
#    LC_ALL=C pins the sort order to plain byte values regardless of the
#    machine's locale, so the same requirements.txt always regenerates
#    byte-identical output.
python3.12 -m venv /tmp/constraints-venv
/tmp/constraints-venv/bin/pip install --upgrade pip
/tmp/constraints-venv/bin/pip install -r requirements.txt
/tmp/constraints-venv/bin/pip freeze --exclude-editable | LC_ALL=C sort -f > constraints.txt
#    Restore the header comment at the top of constraints.txt (freeze
#    does not write one) naming this same procedure.
# 3. Run the suite (all four runs -- see "The verification ladder").
# 4. Rebuild the image, so the constraint set is proved against the
#    interpreter that actually ships.
docker compose -f compose.yaml build web
# 5. Commit requirements.txt and constraints.txt TOGETHER.
```

Both files in one commit, always: a `constraints.txt` that does not match
its `requirements.txt` is worse than neither, because it looks
authoritative.

**Known gap: the resolved set is not free of the test toolchain.**
`llama-index-embeddings-ollama` requires `pytest-asyncio` (and, through
it, `pytest`) **unconditionally** — not as an optional extra — so both
land in `constraints.txt`, and therefore in the production image, no
matter how cleanly `requirements.txt`/`requirements-dev.txt` are split
(dependency audit IMAGE-7). `foundation/ops/tests/test_repo_hygiene.py::
test_the_constraints_file_carries_no_test_toolchain` is marked
`xfail(strict=True)` specifically so this stays visible in every test run
rather than passing silently, and fails loudly (an unexpected pass) the
day it is actually fixed. Closing it — dropping
`llama-index-embeddings-ollama`'s async test helper from the runtime
dependency graph, or replacing it — is follow-up work (H15b), not a step
this task performs.

## 3. Configure environment

```bash
cp .env.example .env
```

The values in `.env.example` match the `docker compose` Postgres service and a
locally running Ollama — adjust `DATABASE_URL` or `OLLAMA_BASE_URL` if yours
differ.

`LLM_MODEL`, `EMBED_MODEL` and `EMBED_DIM` ship **commented out and empty on
purpose**: there is no default model, so there is nothing to pre-fill. Leave
them alone and assign your models in the console (step 5) — that is the normal
path. They exist only as optional explicit overrides for a deliberate pin
(a scripted install, a container image that must come up already bound); set
one and the console labels that role as an explicit environment override
rather than an assignment you can change from the page.

`FARABUNKER_DATA_DIR` (default `./data`) is the managed document store root — see
[ADR 0009](adr/0009-document-store-and-categories.md). Leave it alone for local dev; in
production it must point at a durable host-mounted volume (ADR 0006).

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `farabunker` | The database password, and the *only* place it is written — both compose files interpolate it into the `db` service and into every app service's `DATABASE_URL`, and `docker compose up` refuses to start without it. Change it on any box that is not a throwaway: `docs/OPERATIONS.md` §"Rotating the database password" is the sequence, and it is more than editing this line. |
| `SECRET_KEY` | `dev-insecure-change-me` | Signs every session cookie. **Must** be changed to a real value before switching posture away from `open` — `identity.services.set_posture` refuses the switch otherwise, and `identity.E002` fires at boot if accounts are ever forced on with the shipped key still in place. `docs/OPERATIONS.md` §"Leaving the open posture" is the ordered runbook that changes this alongside `DEBUG` and `ALLOWED_HOSTS` in one `.env` edit. |
| `DEBUG` | `1` | Django's debug flag. **Must** be `0` before switching posture away from `open`, for the same reason — a box with accounts must not render tracebacks, with settings and environment in them, to any visitor (`identity.E001`). Reachability before an administrator exists comes from the *open posture*, not from this flag — an open box with `DEBUG=0` is exactly as reachable and strictly safer (`identity.W003`). `docs/OPERATIONS.md` §"Leaving the open posture" is the ordered runbook. |
| `SECURE_COOKIES` | `0` | Whether this box is served over TLS. `1` marks the session and CSRF cookies `Secure`, so a browser will not send them over plain HTTP — leave it `0` on a plain-HTTP local-network box (a supported posture), set it `1` behind TLS. Accounts on without it produces a warning, `identity.W001`, not a refusal. |
| `ALLOWED_HOSTS` | loopback + this machine's hostname (in the containers: loopback + `web`) | The names this box answers to, comma-separated. Django refuses a request whose `Host:` header is not in this list — which is what stops a DNS name an attacker controls from resolving to this box, being served, and thereby becoming *same-origin* with the attacker's page in the visitor's browser. Add any other name that reaches the box (an mDNS `.local` alias, a CNAME, a reverse proxy's name); setting it in `.env` wins over the compose default. **Keep `localhost` and `127.0.0.1` in the list** whatever else you add — they are how the operator's own browser reaches this box (`http://localhost:8000/`, per this doc's own instructions above) and how `scripts/preview`'s health wait polls the container (`curl http://localhost:<port>/`); drop either and the ordinary local workflow breaks itself. **Never `*`**, and **never leave it empty** — `identity.E003` refuses a wildcard at boot whenever `DEBUG` is `0`, and `identity.E004` refuses an empty list the same way, because an empty `ALLOWED_HOSTS` refuses every `Host:` header including the operator's own. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Only needed behind TLS termination. On a direct plain-HTTP box Django compares a POST's `Origin` against `http://` + the request's host, which same-origin traffic already satisfies, so empty is correct. Behind a proxy that terminates HTTPS, name the browser-facing origins here **with their scheme** (`https://box.example.com`). |
| `FARABUNKER_MAX_REQUEST_BYTES` | `4294967296` (4 GiB) | The one bound on how many bytes a single request may declare in `Content-Length`, refused with `413` before anything reads the body — see "The request-body cap" in `docs/OPERATIONS.md`. |
| `FARABUNKER_TIME_ZONE` | `UTC` | The box's own wall clock — the zone every date and time this box *renders* is shown in, including the current-date line and per-message stamps a conversation's prompt carries while Settings → Chat → "Tell the model the date and time" is on. Storage is always UTC (`USE_TZ`), so this changes display only; set it to your own zone (`America/Denver`, `Europe/Madrid`) or the model is told a time that is correct but not the one on your wall, and for part of every day not even the same date. An unknown name fails at startup rather than falling back. |

Whether this box requires accounts at all is not any setting in this table — it
is `IdentitySettings.posture`, a database row (see "Accounts, and the
three postures" below), never an environment variable.

## 4. Run the stack

### Option A — everything in Docker (recommended)

```bash
docker compose up -d --build
```

> Postgres is published on **`127.0.0.1:5432` only** — loopback, not the LAN (security audit S4).
> `psql -h localhost` from the box itself works exactly as before; `psql -h <box>` from another
> machine no longer does, which is the point. Nothing in the compose topology needs the published
> port: the three app services reach `db` by service name on the compose network.

Runs four containers — `db` (pgvector), `web` (the Django app),
`watcher` (`manage.py ingest_watch /app/data/inbox`, ADR 0009-followup), and
`worker` (`manage.py run_jobs`, the execution queue's worker,
[ADR 0013](adr/0013-inference-execution-queue.md)): `watcher` watches the inbox continuously and ingests whatever
lands there, so uploads made via the library page (below) get processed
without any manual step; `worker` polls the execution queue, claims and
runs admitted jobs, and is the one process that writes a running job's
heartbeat. `run_jobs` also takes a `--once` flag (run exactly one claim+
launch tick then exit, no drain) for tests/diagnostics — it is never used
by the compose service itself, which always runs the full `run_forever()`
loop. **Durable data lives on the host, outside the containers**, under
`./data` (gitignored): `./data/postgres` is the bind-mounted Postgres data
directory, `./data/documents` is the managed document store, and
`./data/inbox` is the watch inbox uploads land in before `watcher` picks
them up. `docker compose down` and image rebuilds never wipe them (ADR
0006). The dev override (`compose.override.yaml`) runs Django with
auto-reload against the `./:/app` bind mount, so code edits apply
immediately with no rebuild; migrations run automatically on `web`
startup. Ollama stays native on the host — the `web`, `watcher`, and
`worker` containers reach it via `host.docker.internal`.

> **Ollama must listen on all interfaces** for the container to reach it: start it
> with `OLLAMA_HOST=0.0.0.0 ollama serve` (it binds `127.0.0.1` only by default, which
> rejects connections from the Docker network). On macOS you can make this persistent
> with `launchctl setenv OLLAMA_HOST 0.0.0.0` before `brew services start ollama`.

App: <http://localhost:8000/> — the front door, which offers an entry card for
every surface this box has a model bound for, and says so plainly when it has
none. Run management commands inside the container:

```bash
docker compose exec web python manage.py ingest /app/data/inbox
docker compose exec web python manage.py ask "…"
```

**Uploading documents via the browser** — go to
<http://localhost:8000/rag/documents/> and use the upload form (pick a file,
choose or type a category). This writes the file into `./data/inbox/<category>/`
on the host and returns immediately; the `watcher` service ingests it
out-of-band, typically within a couple of seconds, after which it shows up
in the library on refresh. If an upload doesn't appear:

```bash
docker compose ps watcher          # confirm it's Up, not Restarting/Exited
docker compose logs watcher        # look for ingest errors (bad file, DB down, etc.)
ls ./data/inbox/                   # confirm the file actually landed on the host
```

For a production-style run (uvicorn, no auto-reload) use the base file only:
`docker compose -f compose.yaml up -d`.

### Option B — native Django against the containerized db (fast iteration)

```bash
docker compose up -d db          # just Postgres
python manage.py migrate         # Django tables + pgvector extension
python manage.py runserver       # or: uvicorn config.asgi:application --reload
```

Migrations create the Django tables (`Document`, `DocumentRow`, `Category`
[seeded]) and enable the `vector` extension; the `rag_chunks` embedding table
is created by LlamaIndex's `PGVectorStore` on first ingest, not by a
migration.

## 5. Assign your models (first run)

A fresh install starts **empty**: no connection registered, no role assigned,
nothing presumed. That is the intended state, not a broken one — the model
console is where you fix it, and every role that has no model says so.

Open <http://localhost:8000/inference/> and work down the page:

1. **Install a model** if you haven't. The "Getting models" checklist names
   what each role still needs (RAG wants one chat model and one embedding
   model). Pull them with your model server's own tooling — e.g.
   `ollama pull <model>` — then reload the console; installed models are
   detected automatically. This is the one step that needs internet access;
   on an air-gapped box, pull elsewhere and transfer.
2. **Register** — each model found on the machine gets an "Add to registered"
   button. One click records it as a connection, keeping every detected
   setting (engine, exact model id, endpoint, capability, embedding
   dimension). Registration is deliberate: nothing on the page creates a
   connection as a side effect.
3. **Assign** — each role's "change" dropdown lists your registered
   connections. Pick one and Apply. Until you do, the role reads "No model
   assigned" and anything that needs it — `/rag/` included — reports
   unavailable with a link back here rather than failing the request.

## 6. Sanity checks

```bash
python manage.py check
DJANGO_SETTINGS_MODULE=config.settings python -c \
  "import django; django.setup(); from models.contracts import gateway; from tools.rag import index, models"
```

Both should succeed with no import errors. A Postgres connection error is
expected if `db` isn't running yet — `check` / imports don't need a live DB.

## 7. Running the tests

Per [ADR 0008](adr/0008-engineering-standards.md), unit tests are a required,
non-negotiable part of every change. The suite uses `pytest` +
`pytest-django` (installed via `requirements-dev.txt`, never shipped in the
production image — see §2);
tests for the RAG module live under `tools/rag/tests/` — every column has its
own `tests/`, and `pytest.ini:4` names all six roots. A column's own view
tests split into several `test_views_*.py` files by feature area once the
single-file suite grows past the in-repo precedent (`tools/vision/tests/`'s
own split), rather than growing one module without bound; `foundation/ops/
tests/test_column_boundaries.py::test_no_test_module_grows_past_the_split_
threshold` is the gate that holds that line.

```bash
.venv/bin/pytest
```

The database from step 4 must be up (`docker compose up -d db`) — tests
marked `@pytest.mark.django_db` run against a real Postgres+pgvector
database that pytest-django creates and destroys automatically, including
running the `CREATE EXTENSION vector` migration. **Ollama does not need to
be running**: every test mocks the Inference Gateway
(`models.contracts.gateway.get_llm`/`get_llm_for`/`get_embed_model`/
`get_embed_model_for` — `core/` has not existed since the P0 regroup; the
seam is `models.contracts.gateway` — each caller builds its own model
explicitly and threads it through per call, rather than a removed
`configure_settings()` writing a shared global; see
[ADR 0010](adr/0010-model-management-framework.md)'s 2026-08-23 amendment)
and the vector index/query engine, so the suite is fully deterministic and
offline.

**Where page CSS goes** is enforced, not just documented: a selector's home is
the deepest template that is an ancestor of every template that uses it —
shared design tokens and cross-column primitives in `foundation/templates/
_shell.html`, a column's own shared rules in that column's `base.html`, and a
single page's own rules in that page's own style block. A fragment (any
template whose filename starts with `_`) never carries its own `<style>`
— **except `foundation/templates/_shell.html` and
`foundation/templates/_settings.html`**, the template tree's two root
ancestors, which own the shared tokens and primitives every page
inherits. The gate itself (`foundation/ops/tests/
test_css_ownership.py::_is_fragment`) excludes exactly those two, so
"fragment" there means "a template included into another", not "a
filename beginning with `_`". Otherwise its rules belong in the nearest
common ancestor of every page that can render it, from the day the
fragment is created. `foundation/ops/tests/
test_css_ownership.py` polices this repo-wide — every template under every
column's own `templates/` directory, not one column in isolation — by
deriving each template's `{% extends %}` chain and `{% include %}` graph from
the tree's own text and flagging a rule or class that has drifted out of
place; a change that adds a second copy of an existing shared rule, or a new
page's CSS in the wrong tier, fails this suite.

## 8. The verification ladder

This is the ladder every change on this project actually climbs, in order.
No rung may be skipped, and **no success language is used before the last
one** — "done", "working", "fixed", and "passing" are claims about the live
box, not about a green terminal.

### Rung 0 — the host services are up

Nothing on this box auto-starts after a reboot. Before anything else, check
and start, as needed:

- **Docker Desktop** — the containers do not come back on their own.
- **Ollama** — `brew services start ollama` (or `ollama serve`).
- **The local speech-to-text server** — started by hand with its
  `--host 0.0.0.0` flag and its own `-m <model-file>` flag; see the
  "Transcription server" section below (which names the actual software
  to install) and, live, `/setup/`.
- **ComfyUI** — started by hand with `--listen 0.0.0.0`; see "Install
  ComfyUI" below and `/setup/`.

`/setup/` renders a live reachability check for every registered engine and
is the fastest way to see which of these is actually answering.

### Rung 1 — the test suite, natively, on a private database

**Tests run natively, never inside a container.** The containers exist to
serve the app; running pytest inside one adds a rebuild to every red-green
cycle and buys nothing.

**Every session gets its OWN database.** `pytest-django` creates and drops
its test database on every run, so two sessions pointed at the same one race
each other's create/drop lifecycle — that fails spuriously at best and can
take the shared Postgres server down under enough load. **Never point a run
at a bare `test_farabunker`.** Point `DATABASE_URL` at *your* branch's own
preview Postgres, with a database name nobody else is using:

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/<your-db-name>'
.venv/bin/pytest -q
```

(`5433` is this branch's preview Postgres — see the port table under
"Testing a branch before merge" below. The primary stack's `5432` is for the
app, not for tests.)

**Run it under both feature-flag states.** `FARABUNKER_FEATURES` gates role
registration, URL mounting, and which file extensions are accepted, so a
suite that only ever runs with one value proves half the behavior. The two
supported gate states for the suite are `'vision,media'` and `'vision'` —
`FARABUNKER_FEATURES=''` is NOT a supported configuration to run the suite
under (dozens of vision tests require the `vision` flag and fail without it,
a pre-existing gap, not something a single change is expected to close):

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision' .venv/bin/pytest -q
```

**And under both collection orders.** Roles, job kinds, and operations live
in module-global registries populated at app-ready time, and Django caches
resolved URLs — all of which can make a test pass only because something
earlier in the run happened to register something for it. Running the
testpaths in the reverse order catches that leakage:

```bash
.venv/bin/pytest -q                          # the configured order
.venv/bin/pytest -q scripts identity agents foundation models tools  # reversed
```

**The two axes compose: four runs make a branch green.** The flag states and
the collection orders are independent, so a branch's gate is the 2×2 of them,
not three commands:

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
```

[AGENTS.md](../AGENTS.md) quotes this block verbatim;
`foundation/ops/tests/test_agent_standards.py` pins that the two copies stay
identical.

**And, for Identity & Auth work, under both non-open postures.** A test
that reads correctly in `open` posture can still be quietly assuming it
— never pinning a posture of its own and passing only because the suite's
default happens to be `open`. `FARABUNKER_TEST_POSTURE` seeds
`IdentitySettings` before each test in a module that has OPTED IN via an
autouse fixture calling its own package's `seed_sweep_posture()` helper
— NOT every module in the tree. As of this writing that is the identity
tests themselves, plus each column's own visibility/access test module
(`grep -rl seed_sweep_posture --include=*.py .` finds every module that
imports the helper; only the ones below actually run it from an autouse
fixture):

- `identity/tests/test_access.py`
- `identity/tests/test_gate.py`
- `identity/tests/test_login.py`
- `identity/tests/test_middleware.py`
- `identity/tests/test_request.py`
- `identity/tests/test_settings_page.py`
- `identity/tests/test_shell_nav.py`
- `identity/tests/test_users_page.py`
- `identity/tests/test_entitlements_access.py`
- `identity/tests/test_entitlement_services.py`
- `identity/tests/test_group_services.py`
- `identity/tests/test_entitlement_pages.py`
- `identity/tests/test_group_pages.py`
- `models/queue/tests/test_visibility.py`
- `models/registry/tests/test_model_sets.py`
- `models/registry/tests/test_model_sets_page.py`
- `tools/rag/tests/test_access.py`
- `tools/rag/tests/test_access_documents.py`
- `tools/rag/tests/test_document_label_page.py`
- `tools/rag/tests/test_labels.py`
- `tools/vision/tests/test_views_queue.py`
- `tools/vision/tests/test_visibility.py`
- `agents/tests/test_entitlements.py`
- `agents/tests/test_shares.py`
- `agents/tests/test_tool_labels.py`
- `agents/chat/tests/test_conversation_share.py`
- `agents/chat/tests/test_tool_entitlements_page.py`
- `agents/chat/tests/test_agent_entitlements_page.py`

**One more module runs the same seed, without the autouse-fixture
machinery**: `foundation/setup/tests/test_views.py` (predates IA-2)
calls `seed_sweep_posture()` directly, inline, inside the two test
methods that need a real posture (`TestAccountsLink`,
`TestInventoryGatedOnAdmin`) — no `@pytest.fixture(autouse=True)` at
all, so it is not really a member of the list above so much as a third
pattern worth knowing about: call the helper by hand where only a
couple of tests in a module care, rather than paying every test in the
module for a fixture the rest do not need.

A module that does not opt in runs in `open` regardless of
`FARABUNKER_TEST_POSTURE` — `agents/chat/tests/test_visibility.py` and
`agents/chat/tests/test_conversation_actions.py` are both exactly this,
deliberately (see each module's own comment on why).
Broadening the sweep to reach every visibility/access module in the tree
without each one hand-wiring the fixture is IA-2 backlog, not done here.
A test that pins a posture with `identity.tests._helpers.posture(...)`
(or its per-package copy) always wins over the sweep either way, the
same precedence `FARABUNKER_FEATURES` already has over a test's own
flag — `tools/rag/tests/test_retrieval_visibility.py` is exactly this:
it never opts into the sweep at all, and instead pins
`posture(POSTURE_ENTERPRISE)` (with `library_posture=LIBRARY_LOCKED` for
its one locked-library case) directly around each assertion that needs
it:

```bash
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
```

**This is a status the reversed collection order already has: required
before merging identity work and before any release, not a per-change
gate.** Every ordinary change runs rungs 1–4 as always; the posture
sweep is the extra rung Identity & Auth work (or any change touching
`identity/`, or a visibility function in another column) climbs before
it merges, and it is part of the pre-release ladder regardless of which
column changed. A failure here is a test that assumed the open posture
without pinning it — fix the test by pinning, never by weakening the
assertion.

### Rung 2 — the branch's own preview stack

A feature branch is smoke-tested on its own isolated stack, with its own
database and its own ports — never by pointing the primary stack at it. See
"Testing a branch before merge" below for `scripts/preview`.

Every implementation plan carries a `## Smoke Checklist`: numbered,
browser-level steps with the expected result spelled out. A human drives it
against the preview, in a browser, after the code review passes and before
the merge decision.

**Restart `watcher` and `worker` by hand.** `web` runs under Django's dev
auto-reloader, so a code edit under the bind mount takes effect on the next
request. `watcher` and `worker` each run a single management command with no
reload machinery at all — **a change to ingest, queue, `agent.turn`, or
job-kind code in general does not reach them until you restart them:**

```bash
docker compose restart watcher worker
```

Forgetting this is the single most common way a change appears not to work
when it does.

**What Identity & Auth adds to that scope.** `identity/middleware.py`
(the gate every request passes through) and `identity/access.py` (the
posture/admin/content predicates every other column's visibility
functions call) are read once, at process start, by `web`, `worker` and
`watcher` alike — a code edit to either needs the same restart as any
other job-kind code, because the worker and watcher hold their own copy
in memory for the lifetime of the process. The acting-principal changes
to the job-kind runtime (every job payload's `actor_kind`/`actor_key`,
and the loop deriving its principal from them) live in
`agents/runtime/**`, already covered above.

**What P3 adds to that scope.** `agents/runtime/**` gained `flow.py` (the
flow runner), `flowtool.py` (the one `flow.run` spec and its per-turn
narrowing), and `preflight.py` (the shared preflight the page and the CLI
both call) — all three are job-kind code and need this same restart, like
every other runtime module. `agents/defaults.py` is read by
`AppConfig.ready()` at process start, so a code edit to the shipped
catalogue needs it too. **A new `Flow` ROW needs neither restart nor
deploy at all** — a flow is data, not code, and it reaches the next turn's
tool schema through `flow.run`'s own per-turn `choices`, computed fresh
every turn; adding, editing, or disabling a flow row takes effect on the
very next turn.

**What IA-2 adds to that scope.** `agents/entitlements.py` (the
`ToolAccess` builder), `agents/labels.py` and `agents/shares.py`
(tool/agent/flow labels and conversation shares), and `tools/rag/
access.py`, `tools/rag/labels.py` and `tools/rag/retrieval.py` (document
labels, the chunk-metadata cache, and the one retrieval filter point)
are all job-kind-adjacent code the worker holds in memory for the life
of the process — a turn, a tool call, or a re-encode run through the
worker's own loaded copy, not a fresh import — so an edit to any of them
needs the same `docker compose restart watcher worker` as any other
job-kind module.

**A catalogue change needs `install_defaults`, but never automatically
(ruling 2, 2026-08-28).** A code edit to `agents/defaults.py`'s
`DEFAULT_AGENTS`/`DEFAULT_FLOWS` — a new shipped default, a changed prompt
or tool list — is picked up by the restart above like any other code
change, but no ROW is written for you any more: `manage.py sync_agents`,
which re-applied every declaration to its row on every deploy, is retired.
A code change to the catalogue changes what the platform **offers**, never
what is **installed** — an operator who already installed `general` keeps
their `general`, including every edit they made to it, and a newly
shipped default appears on `/chat/` as an offer with its own "Add the
default X" button rather than as a row nobody asked for:

```bash
docker compose restart watcher worker
docker compose exec web python manage.py migrate
docker compose exec web python manage.py install_defaults
```

`install_defaults` is create-if-absent and idempotent — running it after
every deploy costs nothing on a box where nothing changed, and it never
touches a row the operator already has. `manage.py install_defaults
--reset <slug>` is the one command that ever overwrites an existing row:
it restores that slug's shipped text, deliberately, with the text in
front of the person who runs it.

**A change to the image, not just the code, needs a rebuild.** Anything in
`Dockerfile` or `requirements.txt` — a new apt package, a new Python
dependency — is baked into the image, and the bind mount does not carry it:

```bash
docker compose up -d --build
```

`.dockerignore` at the repo root keeps `docker build .`'s context from
carrying `.env`, `data/`, `.git/`, agent working directories, and everything
else it must not bake into an image layer — see `foundation/ops/tests/test_repo_hygiene.py`.

**This image now runs as a fixed non-root user, uid/gid `10001` (S12), not
`root`.** Files an already-running root container wrote under `./data` stay
root-owned until you fix that by hand — on a Linux box (existing or freshly
installed, if `./data` itself already exists and is root-owned), `chown` the
data tree once before this rebuild. **`docs/OPERATIONS.md` §"Upgrading to the
non-root container user" is the sequence to run — do not shortcut it with a
bare `chown -R ./data`:** it stops `web`/`watcher`/`worker` first, chowns
`./data` itself non-recursively (so uid `10001` can create brand-new entries
there later, such as `./data/backups`) and then chowns every existing child
directory recursively, all while it excludes `./data/postgres`, which must
never be `chown`'d (Postgres manages that directory's ownership itself and
can refuse to start, or worse, if it disagrees with what it finds there).
Skipping the `chown` on a Linux box is
not uniformly a quiet failure — `FILE_UPLOAD_TEMP_DIR` (`./data/tmp`) is
created at settings-import time, so a root-owned `./data/tmp` makes `web`,
`watcher`, and `worker` all refuse to start (`docker compose ps` reports them
`Restarting`, and `docker compose logs` names the exact directory and fix);
every other directory under `./data` fails quietly later, at first write, the
way OPERATIONS.md describes. macOS dev boxes never hit any of this — Docker
Desktop's virtiofs maps ownership transparently.

**`web` itself now refuses a misconfigured box, not only `migrate` (S13).**
Both `config/asgi.py` (uvicorn) and `config/wsgi.py` (the
`compose.override.yaml` dev `runserver` path) call the one shared helper,
`identity.checks.refuse_if_boot_problems()`, as soon as the application
object exists; it asks `identity.checks.serious_boot_problems()` and
raises `ImproperlyConfigured`, naming the exact conditions, if `DEBUG` is
on with accounts, the shipped `SECRET_KEY` is still in place with
accounts, or `ALLOWED_HOSTS` is a wildcard or empty while `DEBUG` is off
— see `docs/OPERATIONS.md`'s "Switching posture: the three refusals" for
the full list. If `web` fails to come up on this rung
with that message in `docker compose logs web`, the fix is the environment
variable the message names, not a rebuild: fix `.env` and
`docker compose up -d --force-recreate web`.

### Rung 3 — live, at `:8000`

The primary stack bind-mounts the **root checkout**: `compose.yaml`
serves `.:/app` to `web`, `watcher`, and `worker` alike, so those containers
are reading that one working tree live. Two rules follow, both learned the
hard way (see [ADR 0013](adr/0013-inference-execution-queue.md)'s
Consequences for the outage that set them):

- **The root checkout fast-forwards to `origin/dev` only.** Never merge a
  feature branch straight into `/app` to see what happens. A conflicted merge
  left in that tree is picked up by the autoreloader as a code change and
  takes the live web server down.
- **Serialize container git operations across sessions.** Two sessions must
  never be touching that working tree at the same moment.

So: merge the branch into `dev` first (pull request, review, the owner's
merge word), then fast-forward the root checkout to that `origin/dev` SHA,
then restart or rebuild per rung 2's rules. `main` plays no part in this —
it takes only batched, validated release pull requests from `dev`, on the
owner's word, owner-only.

### Rung 4 — fresh pixels

The change is verified when it has been seen working in a browser against
`:8000`, after the deploy — not when the tests pass, not when the preview
worked, and not when the container logs look right. Only then does success
language apply. A `/chat/` conversation — asking an agent to use a tool and
watching the card render — is an acceptable fresh-pixels subject, exactly
like an Ask answer or a generated image.

## Ingesting documents (Wave 2)

`tools/rag/ingest.py::ingest_path` copies every source file into the managed store
(`DOCUMENTS_DIR/<document_id>/<basename>`, ADR 0009) before parsing it — the original
upload/watch-folder file is left untouched. Re-running ingest on the same original path is
idempotent (skips if the SHA-256 is unchanged; otherwise deletes the prior chunks/rows/stored
copy and re-populates the same `Document` row).

```bash
# Ingest one file, explicit category:
python manage.py ingest ./some/report.pdf --category Engineering

# Ingest a directory: each top-level subfolder name becomes the category for
# files inside it (files directly under the dir are Uncategorized), unless
# --category is given to override for the whole run:
python manage.py ingest ./drop-folder

# Watch a folder continuously; category is derived the same way, live, from
# each file's immediate subfolder under the watched root:
python manage.py ingest_watch ./drop-folder
```

A `Category` is created on demand (`get_or_create`) the first time it's used; omitting a
category leaves the `Document` "Uncategorized" (`category=None`). Every chunk written to
pgvector carries the category name (or `"Uncategorized"`) in its metadata for retrieval to
filter on later.

`manage.py relabel_chunks [--document <id>]` repairs the ingest-time
failure path for entitlement labels: `tools/rag/labels.py::
restamp_document_chunks` logs rather than raises when the vector store
was briefly unreachable during ingest, which can leave a document whose
`DocumentEntitlement` rows say one thing and whose pgvector chunks say
nothing. The command re-runs the same stamp with `raising=True`, one
document at a time (or every document, with no `--document`), and is
cheap: no embedding, no engine, one `UPDATE` per document.

## Documents library & category filter (Wave 3b)

- `GET /rag/documents/` — the document library. Lists every ingested `Document`,
  grouped by `Category` (one group per category, ordered by name, including
  categories with no documents yet), plus a trailing "Uncategorized" group for
  `Document.category IS NULL`. Each row links to the document (opens inline via
  `/rag/documents/<id>/file/`), has a "Download" link (`?download=1`), shows the
  `doc_type`, and has a Delete button.
- `POST /rag/documents/<id>/delete/` — deletes one document. Calls
  `services.delete_document(document)` (vector chunks + managed-store files + the
  `Document` row, ADR 0009), then redirects back to `/rag/documents/`. 404s if the
  id doesn't exist. CSRF-protected, form POST only (no GET).
- **Re-uploading a filename that already exists in a category** (round-3 hardening,
  finding B-2): the inbox path (`data/inbox/<category>/<basename>`) carries no
  per-user prefix, so this can happen by accident between two different members. The
  upload form refuses the re-stage with a flash ("… is already in the library under
  this category and belongs to somebody else …") — once accounts are on; an open box
  has no principals to refuse between — unless the uploader is the document's own
  owner, or already holds administrator/entitlement-**owner** authority over it (a
  mere entitlement holder does not). A takeover that isn't by the uploader also
  clears the row's containment and re-stamps its owner. See `tools/rag/README.md`'s
  dedup section for the full mechanism, including why `manage.py ingest` AND the
  `watcher` service (whose real actor is a shared service principal, not "nobody") are
  both exempt from this check entirely.
- **The Ask flow is asynchronous** (ADR 0013, the execution queue): `POST
  /rag/ask/` no longer answers inline. CSRF-protected, like every other
  POST on the box (C-55) — `ask.html` sends `X-CSRFToken` from the form's
  own `csrfmiddlewaretoken`. It validates, pre-checks both roles,
  enqueues a `rag.ask` job, and returns `202
  {"job_id", "state": "queued", "position", "priority", "status_url"}`. The
  page's JS polls `status_url` (`GET /rag/ask/jobs/<job_id>/`,
  `AskJobStatusView`) until the job leaves `queued`/`running` — a `queued`
  response carries `position`/`priority`, a `running` one carries
  `started_at`/`answered_by`, and a `succeeded` one carries the full answer
  body (`{"answer", "citations", "answered_by", "summary", "state"}`) —
  `failed` carries `error`/`setup_url`. See "Execution queue" below for the
  queue itself.
- The ask page (`/rag/`) has a "Category" `<select>` next to the question
  box: "All categories" (default, omits the filter), one option per
  `Category`, and "Uncategorized". The selected value is sent as `category`
  in the `POST /rag/ask/` JSON body, carried through the job's payload to
  `retrieval.answer_question(question, session_id, category=category or
  None)` once the job actually runs. Leaving it on "All categories" sends no
  `category` key, which is the same as `category=None` — search everything.
- The ask page also has an optional "Priority" number field (blank uses the
  queue's own priority chain — the `rag.ask` job kind's default, then the
  queue-wide default) and a "Model" `<select>` when at least one
  chat-capable connection is registered or `rag.answer` rides an explicit
  environment override — options are chat connections in picker order
  (`models.registry.bindings.chat_connections_for_picker`), labeled
  `{name} — {descriptor}` (or bare `{name}`); whichever answers `rag.answer`
  right now is preselected and marked "(primary)". Picking a different one
  sends its pk as `connection` in the `POST /rag/ask/` body for THIS
  question only — the role binding is never rebound, nothing persists.
  Leaving it on the default (or the select being absent) is exactly today's
  role path. A pk that no longer names a usable chat connection gets a
  cause-accurate 503 at submit time ("That model is no longer registered —
  pick another in the model console."); a picked connection that resolves
  but fails its health check gets the same "chat model is unreachable"
  wording an unreachable role binding would (both checked again, fresh, by
  the job itself once it actually runs — the queue wait between submission
  and running may have outdated the pre-check). The succeeded job's body
  always carries `answered_by` — the connection (or role-path) name that
  actually answered — rendered as a small "answered by …" line once polling
  finishes; the embedding model is never overridable, since the pgvector
  store is already built at the role-bound embedding dimension.
- All top-level pages link to each other via the shared app bar at the top
  (`foundation/templates/_shell.html`): ONE row — brand, Chat, Ask, Search,
  Document library, Ask history, Images, Queue, Settings — with the account
  area on the right.
  - A **use-surface** entry renders only when the role behind it has a model
    bound — see `models/registry/README.md`, "The availability signal".
    Document library and Ask history are ungated: storage and a record are
    real with nothing bound at all.
  - **Queue** and **Settings** are ungated too. Queue is activity, and it
    shows each viewer their own rows; `Settings` points at `/settings/`,
    which redirects to the first settings section the viewer may open, so it
    is never a link to a refusal.
  - The **settings area** (`foundation/templates/_settings.html`,
    `foundation/settings_area.py`) is where every operator surface lives,
    behind a sidebar of three groups: **Setup** (Models, Library, Chat, Job
    execution, Engine files with the `vision` feature on, Install guides),
    **Access** (Accounts, Groups, Entitlements, Tool access, Agent access)
    and **Box** (Identity & security). Each entry is gated on
    standing, not availability — this is what an operator fixes "nothing is
    bound" *from*. Install guides is there for everybody; every other Setup
    entry is administrator-only (all class `S`, so a member clicking one
    would only be refused); the six identity/chat admin pages need accounts
    to be on as well. In the open posture everybody is an administrator, so a
    household box's sidebar is the Setup group, entire, and nothing else — a
    group whose every entry is hidden renders no heading at all.

## Ask history

`GET /rag/history/` — a running, owner-directed historical record of every
successfully-answered Ask question: "we should make sure we preserve/log the
inputs/model/outputs for the ask so we have a historical record and limit it
to 100 items or something that can be shortened/expanded."

- **What's recorded** — after the queued `rag.ask` job (ADR 0013)
  successfully answers, `tools.rag.jobs.run_ask` writes one `AskRecord`:
  the question, the category it was asked with ("" for "all categories"), a
  **snapshot** of the answering connection's name + model id (plain
  strings, captured at answer time from the same fresh re-resolve the job's
  own pre-run re-check just performed — not a foreign key to
  `models.registry.models.ModelConnection`, so history stays truthful
  after a connection is renamed or deleted), the answer text, and citations
  reduced to the two fields the Ask page itself shows per citation
  (`{"file": ..., "score": ...}`). A history-write failure is logged and
  never fails the job (wrapped in its own try/except in `run_ask`). Only a
  successful run is recorded — a job that fails its pre-run re-check (no
  model reachable) or whose retrieval call itself raises writes nothing; see
  [ADR 0010](adr/0010-model-management-framework.md) for the full write-up
  of this cut.
- **Retention** — `RagSettings` is a one-row singleton
  (`RagSettings.get_solo()`, created lazily with `history_limit=100` on first
  use; there's no other settings-record pattern anywhere else in the
  codebase to reuse, so this is the simplest "one row, get-or-create it"
  shape). `tools.rag.services.record_ask` prunes to the current
  `history_limit` after every insert (`_prune_ask_records`: one query for the
  cutoff pk, one bulk delete — never loads the kept/deleted rows into
  Python). The retention form (`POST
  /rag/settings/update/`, `field=history_limit`) edits `history_limit`
  directly: blank/zero/
  negative/non-numeric is a clean form error, never a 500; next to the field
  is the honest caveat "Lowering the limit deletes the oldest records on the
  next question; deleted history cannot be recovered." — lowering it prunes
  on the *next* recorded answer, not immediately on save.
- **The page** — newest first, each row a one-line summary (timestamp ·
  connection name · question, truncated to ~120 characters) with the full
  answer and citations behind a native `<details>` disclosure — zero JS on
  this page, by design. Empty state: "No questions asked yet." No
  pagination: the retention limit already bounds how many rows can exist.
- **Where the settings live** — not here. UI-1 split them off: retention,
  the upload cap, the media-duration cap, the document-page cap and every
  retrieval control render on **Library** (`GET /rag/settings/`,
  administrators only), which is where the settings sidebar's Setup group
  points. All seven forms
  post to the page's ONE settings endpoint (`POST /rag/settings/update/`,
  a hidden `field` input naming which form submitted), and each write
  redirects back to the page — the same single-dispatch shape the queue's
  own settings endpoint has, and the one `docs/EXTENDING.md` prescribes
  for a new settings page. Ask history itself is now a pure reading
  surface: a heading, a description, and the records.

## Execution queue

`GET /queue/` — the operator's window into the execution queue
([ADR 0013](adr/0013-inference-execution-queue.md)). Every model execution
in the platform — every `rag.ask` question, every `rag.reencode` triggered
from the drift banner, and (in the future) every other registered job kind
— runs through here; there is no other path left that runs a model.

- **What it is** — a Postgres-backed, priority-ordered admission queue.
  Jobs are claimed and admitted in `(priority, id)` order (lower priority
  number first, ties by submission order), with **no backfill**: the
  scheduler stops at the first job it cannot admit this round rather than
  skipping ahead to a later one that would fit. It is memory-aware when a
  budget is configured — small jobs run together while their combined
  footprint fits, oversize/unmeasured jobs and anything declared exclusive
  (a re-encode) run alone.
- **`/queue/`** (server-rendered, zero JavaScript, like the history page)
  shows Running / Waiting / Finished sections, each row's model(s) and
  measured footprint, a "runs alone" chip where that applies, and a Cancel
  button on waiting jobs (`POST /queue/<job_id>/cancel/` — only a still-
  queued job can be cancelled; a running job runs to completion). A
  "Refresh" link is the only way the page updates — no polling.
- **Priorities** — every job resolves a positive integer priority once, at
  enqueue time: an explicit value (the Ask page's optional Priority field)
  wins, else the job kind's own registered default (`rag.reencode` is 200,
  well behind interactive Ask traffic), else the queue-wide default (100 out
  of the box), which is edited on **Settings → Job execution**
  (`/queue/settings/`). Lower runs first.
- **Budget** — the memory budget (GB) and max-concurrent-jobs fields, also
  on **Settings → Job execution**; the Queue page shows what the budget is
  and how much of it is in use, and links there. The budget ships
  **unset**, meaning strictly sequential (one job at a time) — there is no
  safe machine-wide number to guess, so it is never silently assumed; set
  it once you know how much memory you want the platform to use for
  concurrent model runs. Engine-measured
  footprints (see "Install ComfyUI" for where they appear) are what this
  budget is compared against.
- **Response timeout** — how long a single agent/chat turn may take end to
  end (60–7200 seconds, default 1800 = 30 minutes), also on **Settings →
  Job execution** (one-timeout task, 2026-09-17). This is the ONLY timeout
  that ends a turn: the chat model's own inner request timeout is now built
  from this same value, so an engine reload under memory pressure can no
  longer end a turn before this setting does — the same bound now reaches
  the in-turn RAG tool clients too (`tools/rag/tools.py`, fix round 3). It
  bounds an agent/chat turn only, not every queued job (see
  `docs/OPERATIONS.md`'s own entry for the full field-by-field scope, and
  for the vision-generation and worst-case-latency notes fix round 3
  added). The no-JS thread page's own client-side poll
  cap (`agents.chat.service.MAX_POLL_DURATION_MS`, fix round 1) is set
  above this field's own 7200s ceiling for the identical reason.
- **The worker container** — `compose.yaml`'s `worker` service
  (`python manage.py run_jobs`) is the one process that claims, runs, and
  heartbeats jobs; it follows the same same-image-different-command
  pattern as the `watcher` service (ADR 0006 §6). `run_jobs --once` (one
  claim+admit+launch tick, no drain) exists for tests/diagnostics only — the
  compose service always runs the full, signal-handling `run_forever()`
  loop.
- **Restart on deploy** — unlike `web`, neither `watcher` nor `worker` runs
  under Django's dev auto-reloader (see "Run the stack" above); a code
  change touching queue or job-kind code needs an explicit
  `docker compose restart worker` (or `watcher`, `web`, as applicable) to
  take effect, not merely a saved file.

## Models console

`GET /inference/` — the operator-facing model registry
([ADR 0010](adr/0010-model-management-framework.md)). Shows which engine+model backs
each registered role (`rag.answer`, `rag.embed`), lets you register a connection and
bind/rebind a role to it, and surfaces drift plus a guided re-encode when an embeddings
role's binding changes.

- **The pipeline** — found → registered → in use. A model the scan finds on the machine
  is a *fact*; clicking its "Add to registered" button turns it into a *registered
  connection* (one click, every detected setting kept — engine, exact model id, endpoint,
  capability, embedding dimension); the role dropdowns then offer *registered connections
  only*, and assigning one is what puts it *in use*. Registration is always an intentional
  act — nothing on the page creates a connection as a side effect. A found model whose
  capability the server didn't report links to the manual form prefilled instead (you
  supply the capability — the console never guesses). Each machine row also shows the
  live memory state: "● loaded — X GB in memory" vs "idle (loads on demand)".
- **Unassigned roles** — a role with no connection and no explicit environment override
  renders "No model assigned" in the warning style, with the next step on it: register a
  model from the machine list, then choose it here. Nothing is presumed on your behalf
  (owner ruling, [ADR 0010](adr/0010-model-management-framework.md) third amendment); the
  `LLM_MODEL`/`EMBED_MODEL`/`EMBED_DIM` variables have no defaults and a role riding one
  is labeled an *explicit environment override*, not a default.
- **Cold start** — with no connection registered or the engine unreachable, the page
  starts with onboarding copy. On a healthy endpoint with installed models, the same
  machine rows lead with their "Add to registered" buttons above the role rows; when
  nothing is installed, the "Getting models" checklist guides you through pulling
  something first. `/rag/` degrades the same way: with nothing bound yet it shows a
  friendly "set up a model" message linking back to `/inference/` instead of failing the
  question.
- **Rebinding a role** — the "In use" section's per-role "change" dropdown offers
  registered connections; while one is bound, an *unassign* option is also available, so
  binding is never a one-way door. Unassigning means exactly that: with no explicit
  environment override set, the role is left with **no model at all** (it reports
  unavailable until you assign one) — there is no default to fall back to. The dropdown
  label, the confirm gate and the success message all say which of those two outcomes
  applies to you. Rebinding `rag.embed` to a different model asks for confirmation first,
  since it can invalidate already-embedded documents.
- **Re-encode guard** — rebinding `rag.embed` to a model with a different embedding
  dimension requires a full document-store rebuild; a same-dimension model swap can be
  re-encoded in place. Confirming the rebind only changes the binding (and surfaces the
  drift banner) — it does not re-encode anything itself. Trigger the re-encode from the
  drift banner's button, or directly:
  `python manage.py reencode --role rag.embed` (or
  `docker compose exec web python manage.py reencode --role rag.embed`).
- **Context window** — every chat connection is built with a bounded, operational default
  cap on the KV cache the engine allocates (`models/contracts/engines/ollama.py`'s
  `DEFAULT_CONTEXT_WINDOW`), so the adapter never lets a model server's architecture-max
  context length dictate memory use on the platform's behalf — see
  [ADR 0010](adr/0010-model-management-framework.md)'s context-window amendment for the
  incident that motivated it. Any registered connection's own edit form (or the manual
  "Add a connection" form) can set an explicit context window in tokens instead — chat
  models only; leaving it blank keeps the adapter default. A set value shows in that
  connection's facts area alongside Capability/Embedding dimension.
- **Descriptor and rank** — any registered connection's edit form (or the manual "Add a
  connection" form) can carry an optional, free-text descriptor — whatever wording helps
  you tell your models apart — and a plain rank number where lower is listed first —
  self-assigned qualifiers, never auto-filled or suggested. A set
  descriptor shows next to the connection's name in "Registered connections" and leads its
  label in every role dropdown; rank sorts those dropdowns (and the Ask-time model picker)
  and shows in the connection's facts area when set.
- **Resolution seam for a per-request override** — `models.registry.bindings.resolve_connection(pk,
  capability)` resolves one specific registered connection by pk (not a role), for a caller-supplied
  choice; `models.contracts.gateway.get_llm_for(resolved)` builds an LLM from that already-resolved
  binding. The role binding stays the durable "primary" either way. This seam wires into the Ask
  page's Model picker (see "Documents library & category filter" above) —
  `tools.rag.retrieval.answer_question` takes already-resolved `answer_resolved`/`embed_resolved`
  `ResolvedModel` arguments (built via `get_llm_for`/`get_embed_model_for`) rather than resolving
  either role itself; the queued `rag.ask` job (see "Execution queue" below) resolves the picked
  connection — or `rag.answer`'s own role binding when no override was picked — fresh at both plan
  and run time and passes the result straight through. `rag.embed` is never overridable.
- **Finding your model server** — the page never probes anything beyond the one configured
  endpoint on load, cold or warm. The "Scan for model servers" button runs a short,
  user-triggered sweep of each registered engine's own well-known addresses (localhost,
  `127.0.0.1`, `host.docker.internal`, plus its compose-service hostname) — engine-agnostic
  by construction, so it never assumes Ollama specifically. A hit's "Use this endpoint" link
  reloads the console pointed at that server. Health and discovery answers are served from a
  **30-second cache** (`models/registry/probe_cache.py`), not probed fresh on every GET and
  POST. If you have just fixed an unreachable engine and the console still says otherwise,
  wait out the window — the page is not lying, it is remembering. `/setup/` deliberately
  bypasses the cache and always probes live.
- **The endpoint override is route-gated and allowlisted** (S5/S19) — `/inference/*` is an
  admin-only route, so a signed-in non-admin gets a 403 for the whole page once accounts are on
  (`personal`/`enterprise`) before `?endpoint=<url>` (or its POST-carried twin,
  `endpoint_override`) is ever read. In every posture, including the default `open` one (where
  that route gate does nothing — there are no accounts to gate), the requested host must also be
  this box's own loopback interface (`localhost`, `127.0.0.0/8`, `::1` — **any port**, no
  registration needed) or the exact scheme+host+port of a host this box already knows — a
  registered `ModelConnection` or a configured default engine endpoint; an unregistered
  private-LAN address is refused just like a public one. See
  [docs/OPERATIONS.md](OPERATIONS.md)'s "The model console's endpoint override" for the
  operator-facing symptom (a scan hit the console then refuses to switch to) and the fix
  (register the connection, which adds its host — never a setting to edit).

**The platform never downloads models.** Every `ollama pull ...` command shown by the
catalog/console is copy-paste guidance for the operator to run themselves, on the host,
ahead of time — application code never initiates a model download. In bunker postures,
models are expected to arrive as signed, content-addressed airlock packages instead
(Phase 2, [ADR 0010](adr/0010-model-management-framework.md) §5); `ollama pull` is a
development-posture convenience only.

## Using `/chat/`

`/chat/` is the surface: mounted ungated, with a nav entry beside every
other page. Assign a tool-capable chat model to the `chat.converse` role
at `/inference/` first (the "Models console" section above). Open
`/chat/` — on a box with no agents installed yet it shows an honest empty
state naming the shipped defaults; press **"Add the default General
assistant"** (or run `docker compose exec web python manage.py
install_defaults`, see the restart section above) — then send a message.
`/chat/` never writes an agent row on its own (ruling 2): the button, or
the management command, are the only two things that ever do.

## Driving an agent turn from the CLI

`/chat/` is the surface (see "Using `/chat/`" above); `manage.py
agent_turn` remains the CLI equivalent and the scriptable one — how the
agents design's own worked example (a request that touches both `rag.*`
and `vision.generate` through one registry) is proven, and how a script
or a health check can drive a whole turn without a browser. Assign a
tool-capable chat model to the `chat.converse` role at `/inference/` first
(the "Models console" section above), then:

```bash
docker compose exec web python manage.py agent_turn general \
  "What does the library say about attention, and make me a small watercolor of a lighthouse"
```

It preflights (refuses before enqueuing if the role is unbound or the
bound model cannot call tools — nothing is written on that path), enqueues
one `agent.turn` job, polls it, and prints each tool call with its outcome
and any artifact, then the final answer:

```
queued job 41 for conversation 5f6e...

  [tool] rag.search -> ok
         artifact: document:3
  [tool] vision.generate -> ok
         artifact: output:12

The library discusses attention as... Here is your watercolor lighthouse.

conversation 5f6e2c9a-...
```

Continue the same thread with `--conversation <id>` (printed on the last
line above), or override the agent's own chat model for one turn with
`--connection <ModelConnection pk>`. `manage.py install_defaults` (see the
restart section above) must have run at least once — an install with no
agent rows yet has no `general` to run.

## The settings assistant

A guide to this box's own settings, rendered as a collapsible panel at
the bottom of every settings page. It answers questions about what a
setting does and where it lives, from three read-only tools
(`settings.card`, `settings.overview`, `models.status` — see
`docs/EXTENDING.md`'s "Two tools already in the tree"); it never changes
a setting itself.

**Installing it on a dev box:** open any settings page and use the
panel's own "Add the settings assistant" button — no separate
`manage.py` step. It never appears on its own: `agents/README.md`'s
"Shipped defaults are a catalogue, not a deploy step" section states the
rule this follows (ruling 2), the same one every other shipped default
follows. (`manage.py install_defaults`, the CLI equivalent that section
also describes, installs it too, alongside every other catalogue entry
not yet present — the panel's button is simply the direct route while
you are already looking at a settings page.)

It is deliberately absent from `/chat/`: `agents/chat/README.md`'s "The
settings-surface exclusion" section is the full account of why, and
`agents.defaults.SETTINGS_SURFACE_SLUGS` is the one line that names it.

**Three drift assertions to expect** when a settings page changes shape
— each is documented at length in `docs/EXTENDING.md`'s "Adding a
settings page" recipe, and each is a fast, specific failure rather than
a silent one:

1. `identity/tests/test_route_matrix.py::test_every_route_has_a_driver`
   — every route needs a class (`identity/routes.py::ROUTE_RULES`) and a
   driver; an unclassified route is treated as admin **and logged**, not
   silently opened.
2. `foundation/tests/test_shell.py`'s sidebar drift test — the sidebar
   table (`foundation/settings_area.py::SETTINGS_GROUPS`) and the routes
   that actually exist must agree, or the test fails rather than leaving
   a page unreachable from the sidebar.
3. `foundation/tests/test_settings_help.py` — every settings route needs
   a `HelpCard`, and every anchor a card cites must exist on the page it
   names, rendered. **Changing a settings template's `id=` or removing a
   help card fails `foundation/tests/test_settings_help.py`, and that is
   deliberate:** those two are the drift guard for the one thing that
   cannot otherwise be tested — whether a `HelpCard`'s own *wording*
   still describes the page it points to.

## Accounts, and the three postures

This box ships in **open** posture: no accounts, every request answered
as the single shared open principal (`Principal("open", "box")`), and
`/chat/` — like `/rag/`, `/vision/`, `/inference/` and `/queue/` — asks
no permission question at all.

The posture is a DATABASE ROW (`IdentitySettings.posture`), never an
environment variable: `compose.yaml` starts three processes from one
image with independently supplied environments, so a posture carried in
the environment could be set on the web process and unset on the
worker, and the worker is where turns actually run tools. `manage.py
identity_posture` prints the current posture with no arguments, or
switches it — `manage.py identity_posture personal` (or `enterprise`).
It is the break-glass path for when the posture page itself is
unreachable, and it reaches the exact same `identity.services.
set_posture` function the page does, so it is not a way around any of
that function's three refusals (see "Switching posture: the three
refusals" in [docs/OPERATIONS.md](OPERATIONS.md)). `identity.
access.accounts_on()` is what every access function tests first, and
`identity.request.principal_for_request` — the only place in the
codebase that turns an HTTP request into a principal — resolves the
signed-in user once the posture leaves `open`, or answers `ANONYMOUS`
for a request with no session.

There is no email server on this box, so a forgotten password is reset
by an administrator on the machine, with Django's own `manage.py
changepassword <username>` — not a "forgot password" page. That command
bypasses `identity.services` entirely (no `ServiceRefused` guard, no
audit row) — it is Django's own, unchanged.

**Operator note: losing the `IdentitySettings` row silently re-opens the
box.** `IdentitySettings.get_solo()` is a `get_or_create(pk=1)` — the
same "never raise `DoesNotExist`" shape `RagSettings.get_solo` already
has, so a backup restored from before this phase behaves identically to
a fresh install. The consequence is real: if that one row is ever
deleted directly (never through the posture page or `manage.py
identity_posture`, which only ever update it), the very next request
recreates it with its model default, `posture="open"` — a silent
reset to no-accounts, not an error. Nothing in this platform deletes
that row on its own; this is stated so a hand-edited database is never
mistaken for a bug when it happens. `manage.py check`'s `identity.W002`
(see [docs/OPERATIONS.md](OPERATIONS.md)) catches the visible symptom:
it warns whenever the posture is `open` but `identity_user` already has
a row in it, which is exactly what a reset like this looks like from
the outside.

## Testing a branch before merge

`compose.yaml` bind-mounts the root repo checkout, so it can never run a
`git worktree` branch — the container always serves the root checkout's code
regardless of which checkout you invoke `docker compose` from. `scripts/preview` +
`compose.preview.yaml` (see [ADR 0011](adr/0011-branch-preview-stacks.md))
solve this: a disposable, isolated stack that builds and runs a worktree
branch's actual code against its own fresh database, side by side with the
primary stack, on its own ports.

```bash
# Bring up a preview for a worktree under .claude/worktrees/<branch>
# (or pass an explicit path to any worktree checkout instead of a bare name):
scripts/preview up model-management-framework

# ... smoke-test it in a browser at the URL it prints ...

# Stop it — data under data/preview/<branch>/ is left in place:
scripts/preview down model-management-framework

# Stop it AND permanently delete data/preview/<branch>/ (requires --yes):
scripts/preview reset model-management-framework --yes
```

`up` seeds a `.env` for the worktree if it doesn't already have one, creates
`data/preview/<branch>/{postgres,documents,inbox}` if this is the branch's
first preview, then runs `docker compose -p farabunker-preview-<branch> up
-d --build` and waits for the web container to answer before printing the
URL. When no main repo `.env` exists to copy, that seed falls back to
`.env.example` — set `POSTGRES_PASSWORD` in the new `.env` before running
any compose command, since compose refuses to start with it unset. The
preview's database starts **empty** (not a copy of the primary
stack's data) — that's deliberate, since it's what proves the branch's
migrations actually work on a virgin install. Ollama is **not** isolated:
the preview reaches the same native host Ollama as the primary stack, since
the platform never pulls models itself (ADR 0010 §5) and a preview should
reuse whatever's already pulled, not require re-downloading it.

| | Primary stack | Preview default | Override |
|---|---|---|---|
| Web | `localhost:8000` | `localhost:8001` | `--port PORT` |
| Postgres | `localhost:5432` | `localhost:5433` | `--db-port PORT` |

If the default preview port is already taken by something else on your
machine, `scripts/preview up` fails fast and names a free port to retry
with; pass `--port`/`--db-port` explicitly to pick your own.

**Each stack gets its own cookie jar, and it has to.** Cookies are scoped by
scheme + host + path and **not by port** (RFC 6265 §8.5), so `localhost:8000`
and `localhost:8001` share one jar in the browser: with every stack writing
`sessionid` and `csrftoken`, whichever tab rolls its session last wins, and
the others are silently signed out mid-click — while their server-side session
rows are still perfectly alive, which is what makes the symptom so confusing
to read. `compose.preview.yaml` therefore names both cookies after the
preview's own web port (`sessionid_8001`, `csrftoken_8001`), so a preview
never collides with a sibling or with `:8000`. Nothing to set by hand; to
choose the names yourself, set `FARABUNKER_SESSION_COOKIE_NAME` and
`FARABUNKER_CSRF_COOKIE_NAME` in the preview's `.env`. Both default to
Django's own names when unset, so the primary stack and CI are unaffected.

**Running `pytest` for a worktree branch** is rung 1 above, and rung 1's
rule is the one that applies: point `DATABASE_URL` at this branch's own
preview Postgres from the port table above, with a database name nobody
else is using. Rung 2 above owns the `## Smoke Checklist` walk that follows
a green suite.

## Status

Phase 1 is implemented end to end: ingest (into the managed store, with
categories), semantic retrieval with optional category scoping, cited
answers, the ask UI, and the documents library. On top of it: the model
registry and `/inference/` console ([ADR 0010](adr/0010-model-management-framework.md)),
image generation ([ADR 0012](adr/0012-image-generation-engine-adapter.md)),
the execution queue at `/queue/` ([ADR 0013](adr/0013-inference-execution-queue.md)),
and media ingestion — video/audio transcription plus vision text extraction
from images and scanned PDFs, with timestamped/page citations
([ADR 0014](adr/0014-media-ingestion.md)).

Still deferred: hybrid text-to-SQL routing over tabular data, so **tabular
data is stored but not searchable today** (see `tools/rag/retrieval.py` and
[ADR 0005](adr/0005-rag-module-architecture.md)). Retrieval is also an exact
scan — there is no approximate-nearest-neighbour index — and `top_k` is a
fixed constant, both recorded in [ADR 0014](adr/0014-media-ingestion.md) §18
alongside the approved next wave.

**`STATIC_ROOT` is deliberately unset** — nothing in this repository runs
`collectstatic` today; see [ADR 0015](adr/0015-agent-layer-and-tool-contract.md)
Decision 1 for the ruling and what would reopen it.

## Install ComfyUI (image generation)

> The in-app version of this section lives at **`/setup/`** — it renders the
> same steps from the engine adapter's own `setup_guide`, plus a live
> reachability check for each engine. Prefer it when the box is running; this
> section is the copy you can read before it is.

ComfyUI runs **natively on the host** so it can use the GPU (Metal on macOS,
CUDA/ROCm on Linux, CUDA on Windows). Docker cannot reach the Metal GPU, so
the `web` container talks to it over `host.docker.internal`, exactly like
Ollama. Start it with `--listen 0.0.0.0` or it binds to localhost only and
the container cannot reach it.

**macOS (Apple Silicon)**

```bash
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python3 -m venv venv && source venv/bin/activate
pip install torch torchvision torchaudio     # the default PyPI wheels carry MPS support
pip install -r requirements.txt
python main.py --listen 0.0.0.0 --port 8188
```

**Windows** — download the portable build from the project's releases page,
unzip it, and edit `run_nvidia_gpu.bat` (or `run_cpu.bat`) to add
`--listen 0.0.0.0` to the python line it runs. Checkpoint ids will come back
with a backslash when they sit in a subfolder (`subdir\<checkpoint>.safetensors`);
that is normal and farabunker passes them through unchanged. Prefer a source
checkout instead (same steps as macOS/Linux) if you want a checkout you
control.

**Linux** — clone as on macOS, then install torch for your accelerator
(pick the CUDA or ROCm wheel index matching your GPU driver):

```bash
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python3 -m venv venv && source venv/bin/activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
python main.py --listen 0.0.0.0 --port 8188
```

**Models are yours to place.** farabunker never downloads a checkpoint.
Copy `.safetensors` files into `ComfyUI/models/checkpoints/` — community
checkpoints come from Civitai or Hugging Face, downloaded by you, out of
band. Whatever is in that folder is what `/inference/` will list. Verify
ComfyUI itself is up with `curl http://localhost:8188/system_stats` before
checking farabunker's side.

**A multi-file model family (GGUF, or a bare UNet) goes in three folders,
not one.** Instruction-based editing (see "Generate images", below) runs on
models shipped as a diffusion-model file plus its own text encoder and VAE, rather than one
self-contained checkpoint: the diffusion-model file goes in
`ComfyUI/models/unet/`, its text encoder(s) in
`ComfyUI/models/text_encoders/`, and its VAE in `ComfyUI/models/vae/`
(alongside any checkpoint-family VAEs already there). `/inference/` lists
each file separately; register the connection from the manual form (not the
one-click "Add to registered" a self-contained checkpoint gets) and declare
which installed file plays which role, plus the model family — nothing here
is detected, see [ADR 0012](adr/0012-image-generation-engine-adapter.md)'s
"Instruction-based editing across model families".

**"Offline" here** means: once the checkpoints are on disk, the whole path
(browser → farabunker → ComfyUI → image) runs with the network off. Verify
it by disabling Wi-Fi and generating.

**One GPU, two engines.** Ollama and ComfyUI both want VRAM. On a
single-GPU box (or an Apple Silicon machine, where GPU and system memory
are the same pool), a large LLM held in memory can starve a generation and
vice versa. Neither engine gives its memory back on its own: `ollama stop
<model>` frees Ollama's, and ComfyUI holds a checkpoint resident until
something asks it to let go.

For jobs that go through the queue, farabunker now does some of that
asking. After each generation it asks the ComfyUI adapter what the run
cost and records that against the connection; when a later admission needs
the room, it POSTs ComfyUI's `/free`. This is best-effort housekeeping,
not an arbiter: it only sees work the queue itself started, which is one
more reason to run generations through `/vision/` rather than ComfyUI's
own web UI.

**Where the footprint shows up.** After the first queued generation with a
given checkpoint, that connection on `/inference/` grows a **Last
measured** row naming a size and the date it was measured. It is a
measurement, not a promise — it is taken by comparing free memory before
and after the run, so another busy application on the machine can skew it.
The **Memory footprint override (GB)** field on the same connection wins
whenever you know better.

**Setting the budget is your step.** A measured footprint changes nothing
about concurrency by itself: the memory budget ships unset, meaning
strictly sequential, and stays that way until you set it. Decide how much
memory you want the platform to use for concurrent model runs, set it on
**Settings → Job execution**, and the measured footprints become the numbers admission
does its arithmetic with.

**What the "loaded" chip means for ComfyUI.** For an Ollama model, that
chip on `/inference/` is a live reading from the engine. ComfyUI has no
equivalent to read, so for a ComfyUI checkpoint the meaning it carries is
**believed resident — up to six hours since the last run on this
endpoint**: farabunker reporting a checkpoint it ran and has not since
freed. ComfyUI may have evicted it internally in the meantime, and a
generation started from ComfyUI's own web UI is invisible to farabunker
entirely, so it never appears there either. In practice this chip never
lights up for a ComfyUI checkpoint on `/inference/` today: the belief
lives only in the WORKER process that actually ran the generation —
`/vision/` always queues a generation rather than submitting it directly,
so there is no path that stamps the belief anywhere else — and
`/inference/` renders in the separate WEB process, which has no belief of
its own to show. What the belief IS load-bearing for is release: the
worker's own eviction pass reads a checkpoint's `loaded` flag from inside
that same worker process, off its own belief, before deciding what to
free, and the worst case there is one unnecessary (and harmless) request.

**Uploads.** An operation that takes an image (img2img, inpainting) sends the file to
ComfyUI over its own `/upload/image` endpoint, into a subfolder named after the job.
farabunker keeps its own copy under `data/generated/<job>/inputs/`, so a ComfyUI restart
that clears its `input/` folder costs nothing: the job reports as lost and a resubmit
re-sends the file. Nothing has to be shared between the container and the host.

**Five modes, narrowed to what the selected model can run.** `/vision/` registers
text-to-image, image-to-image, inpainting, upscaling, and instruction-based editing.
Which of them the mode chooser actually shows depends on the model picked in the
create page's own **Model** dropdown: a plain single-file checkpoint offers
text-to-image, image-to-image, inpainting, and upscaling; a declared multi-file family
(see above) offers instruction-based editing instead — image-to-image and editing
answer the same "change this picture" intent and are never both offered by one
selected model, so the page presents them as ONE "Image to image" tab whose form
adapts (Instruction + Guidance + Steps for an edit-family model; Prompt + Denoise +
sampling for a checkpoint). Inpainting also takes a mask — white marks the area to
repaint; upscaling takes an image and one of the upscale models you placed in
`ComfyUI/models/upscale_models/`. Any result in the gallery can be fed straight back
in with its "Use in …" link, with no download-and-re-upload round trip. LoRAs are a
control on the checkpoint modes, listed from `ComfyUI/models/loras/` — the
platform never downloads one, and which LoRA to run is your decision.

## Transcription server (media ingestion)

> As with ComfyUI, the in-app version of this lives at **`/setup/`** — it
> renders the steps from the engine adapter's own `setup_guide`, plus a live
> reachability check. Prefer it when the box is running.

Video and audio ingestion ([ADR 0014](adr/0014-media-ingestion.md)) needs
the local speech-to-text server running **natively on the host**, the same
contract Ollama and ComfyUI already have: Docker can't reach the Metal GPU,
so the `web`, `watcher`, and `worker` containers reach it over
`host.docker.internal` (`WHISPER_BASE_URL`, wired into all three services in
`compose.yaml`).

**macOS** — `brew install whisper-cpp`, or build the project from source.
Then start it, binding to all interfaces so the containers can reach it:

```bash
whisper-server --host 0.0.0.0 --port 8080 -m /path/to/your/model-file
```

**The start command *is* the model choice.** `whisper-server` loads exactly
one model file, named by `-m`, for the life of the process, and exposes no
route that reports which one — so farabunker's "installed models" list for
this engine is honestly **empty**, and you register the connection manually
at `/inference/` with whatever id you want to call it. A second model means
a second server on another port, registered as its own connection.

**The model file is yours to place.** farabunker never downloads one. Fetch
a `ggml`/`gguf` speech model out of band and point `-m` at it.

**Port 8080 is commonly squatted** by other local dev servers, so the health
check sniffs the response body for a marker before trusting it. If something
else is listening there, farabunker reports the engine as unreachable rather
than showing a false "Reachable" — that is the guard working, not a bug.
Verify with `curl http://localhost:8080/` and check you get whisper.cpp's
own status page.

**No footprint reporting.** The server reports nothing about how much memory
it is using, so the queue treats a transcription job as exclusive and runs it
alone (ADR 0013 §4). If you want transcription to run alongside other work,
set a **memory footprint override** on that connection at `/inference/` —
that supplies the number the engine cannot, and the scheduler starts
reasoning about the job normally.

**`ffmpeg` is in the image, not on the host.** Duration probing, audio
extraction, and slicing all run inside the containers. If you are running
Django natively (Option B above) and touching media ingestion, install
`ffmpeg` on the host too (`brew install ffmpeg`) — without it, the duration
cap is skipped with a logged warning and transcription fails honestly rather
than silently.

## Generate images (`/vision/`)

1. Start ComfyUI (above) and make sure `docker compose ps` shows `web` up.
   Open **Settings → Install guides** from the app bar to confirm the engine
   reads *Reachable*.
2. Open `/inference/`. A self-contained checkpoint ComfyUI reports appears
   under **On this machine**, tagged with the `comfyui` engine — click
   **Add to registered** on the one you want; it registers against
   ComfyUI's own endpoint, not Ollama's. A multi-file family (GGUF/UNet,
   see above) is not a one-click add: use the manual "Add a connection"
   form, or an already-registered connection's own **Edit** disclosure, to
   declare the model family and its text-encoder/VAE companions from the
   engine's own reported lists.
3. In the **Image generation** role row, choose a connection and apply --
   this is the DEFAULT model the page opens with; the create page's own
   per-generation picker (next step) can run a different registered
   connection without touching this binding.
4. Click **Images** in the app bar (it appears on every page once the vision
   feature is enabled AND a model is bound to the Image generation role — an
   entry for a surface with no model behind it would only lead to an
   apology). The create page shows a **Model** picker
   (every registered `image-generation` connection, the role binding
   preselected) and a mode chooser above the form, both narrowed to what
   the PICKED model's engine actually supports (see "Five modes", above).
   Type a prompt (or, for an edit-family model, an instruction) and press
   **Generate** — the card polls until the image appears. Every finished
   card — on the Generate page and in the **Gallery** — offers the same
   actions: **Use in …** for each mode the picked model supports and takes
   an image, **Reuse settings** to send that job's parameters back into
   the form, and **Download**. Both surfaces render one shared partial
   (`tools/vision/templates/vision/_output_actions.html`), so a mode
   registered tomorrow appears on both without a template edit.

While a generation runs, `/queue/` shows a live elapsed line for it, and
the job card — on a card the page is polling — names the engine's own
queue position when the image engine still has the job waiting. Neither
shows a percentage: ComfyUI reports no per-step progress over HTTP, and
a made-up bar is worse than none.

Two different "position N" numbers can appear, and they count different
queues. *Waiting in the queue — position N* on a queued placeholder is
farabunker's OWN execution queue (`/queue/`): how many submissions are
ahead of yours here. *Waiting on the image engine — position N* on a job
card is ComfyUI's queue: farabunker has already handed the job over, and
the engine has N-1 other prompts — possibly submitted from ComfyUI's own
interface — to run first.

If the page says *No model assigned for Image generation*, step 3 has not
been done. If it says the engine is not reachable, check that ComfyUI is
running with `--listen 0.0.0.0` and that `COMFYUI_BASE_URL` matches (inside
the containers it must be `http://host.docker.internal:8188`).

To turn the whole feature off, set `FARABUNKER_FEATURES=` (empty) — the
role disappears from Models, the **Images** entry disappears from
the app bar, and `/vision/` stops existing.

## Backup and restore

`manage.py backup`/`manage.py restore` (W3) are operator commands, not a
contributor workflow -- the full procedure, the exact `pg_dump`/`pg_restore`
lines, and what is and isn't safe to copy live in
[docs/OPERATIONS.md](OPERATIONS.md), not duplicated here.
