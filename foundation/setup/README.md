# foundation/setup — the universal `/setup/` page

Answers one question for an operator with a fresh box: *how do I get a
model into this thing?* farabunker runs no model itself and downloads
nothing — the operator installs an engine, places model files where that
engine can see them, and farabunker connects to what it finds. `/setup/`
(`GET`, view name `setup-index`) walks that flow once and then renders one
section per registered engine.

## What's on the page

Three sections, in order:

1. **How models reach farabunker** — the register-then-bind flow, spelled
   out once: install an engine, register what it reports at
   [Models](/inference/), assign the registered model to a role. It
   links to the console rather than repeating the console's own UI.
2. **One section per registered engine**, anchored at `#engine-<name>` —
   built entirely from `models.contracts.engines.ENGINES` and each adapter's
   own declarations (below). A newly registered adapter appears here with
   no change to this app: the view iterates `ENGINES.values()`, and the
   template names no engine, port, path, or command of its own.
3. **What each feature needs** — one row per `models.contracts.roles.RoleSpec`
   (from `all_roles()`), showing which registered engines declare they can
   serve that role's capability and whether the role is bound right now
   (through `models.contracts.bindings.resolve()`).

## How an adapter opts in

An `InferenceEngine` (`models/contracts/engines/base.py`) declares two
optional class members read via `getattr(engine, name, default)`, so an
adapter that sets neither still renders — see "Adapters without a guide"
below.

- **`setup_guide: SetupGuide | None`** — everything the page needs to
  explain how to install and reach this one engine:
  - `summary` — one sentence shown at the top of the engine's section.
  - `platforms: dict[str, tuple[SetupStep, ...]]` — keyed by the three
    platform keys in `SETUP_PLATFORMS` (`"macos"`, `"windows"`, `"linux"`,
    labelled by `SETUP_PLATFORM_LABELS`); a platform the engine doesn't
    support is simply absent from the dict. Rendered as one `<details>`
    per platform, in `SETUP_PLATFORMS` order regardless of the dict's own
    key order, with the FIRST present platform open by default (macOS,
    if the adapter supports it) and the rest collapsed. Each `SetupStep`
    is `(title, body, command)` — `command` is `None` for a step with
    nothing to copy (download a zip, edit a file), never a fake shell
    line.
  - `network_note` — how to make the engine reachable from farabunker
    (e.g. bind address, container-to-host networking).
  - `models_note` — where this engine's model files go.
  - `verify_url_path` — the path the page appends to the engine's default
    endpoint (`settings.INFERENCE_DEFAULT_ENDPOINTS[engine.name]`) for its
    "Verify" line — the SAME path the adapter's own `is_healthy` checks,
    so what an operator is told to open is exactly what the platform
    checks.
- **`serves_capabilities: tuple[str, ...]`** — which
  `models.contracts.roles.CAPABILITIES` this engine can serve at all. Read by
  the "What each feature needs" table's "Served by" column; an engine that
  declares none simply doesn't appear in any row.

## Adapters without a guide

An adapter with no `setup_guide` still gets a card, built from the facts
every adapter has: `api_description`, `well_known_ports`,
`install_cmd_template`, and `library_url` (all read via `getattr`, each
degrading to empty/absent rather than raising). It has no platform
`<details>`, no network/models notes, but keeps its own anchored section
and its own live health line.

## Health

Each engine's section reports one of three honest states, never a fourth:

- **Reachable** / **Not reachable** — `settings.INFERENCE_DEFAULT_ENDPOINTS`
  has an entry for this engine, and `engine.is_healthy(endpoint)` returned
  `True` / `False` (or raised — a health check that blows up reads as
  "Not reachable", never a 500; the exception is logged at `DEBUG`).
- **not checked** — no default endpoint is configured for this engine, so
  `is_healthy` is never called.

The same honesty applies to the needs table's "Assigned?" column: it calls
the real `models.contracts.bindings.resolve()` and reads an exception (role
unbound, or a provider hiccup) as "not yet" rather than raising.

## Read-only, never-500, unauthenticated

Like the rest of Phase 1, this page requires no login and makes no writes
— it only reads `ENGINES`, `settings.INFERENCE_DEFAULT_ENDPOINTS`,
`all_roles()`, and `resolve()`. Every call to an engine (`is_healthy`) or
the binding resolver is wrapped, because this is the page an operator
lands on precisely WHEN something isn't working — it must answer 200
regardless of what's broken downstream.

## Nav

This page is called **Install guides** (UI-2; it was "Setup" while it lived in
the app bar's Manage row, and the url name, the path and the view are all
unchanged). The settings shell (`foundation/templates/_settings.html`) links to
`/setup/` under the sidebar's **Setup** group, and it is shown to everybody, and
always — unlike **Images**, which appears only when the vision feature is on
*and* a model is bound to its role, and unlike its two group-mates Models and
Library, which are class `S` and render only for an administrator. Install
guides is class `P` and explains the engines themselves, which exist regardless
of which features are turned on and which an operator needs *before* they can
sign in to a box whose engines are down.

Being ungated is also what makes the settings area safe to offer to everybody:
`/settings/` redirects to the first section the viewer may open, and this is
the one that can never be hidden, so the app bar's `Settings` entry is never a
link to a refusal.

## Tests

```
.venv/bin/pytest foundation/setup/tests/test_views.py -q
```

`test_views.py` reads the SAME registries the page does (`ENGINES`,
`all_roles()`), so it stays correct as adapters are added, and separately
registers throwaway stub engines (a fully-guided one, and one with no
`setup_guide` at all) to prove a new/incomplete adapter renders correctly
with no template change.
