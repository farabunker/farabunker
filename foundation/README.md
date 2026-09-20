# foundation/ — shared, feature-agnostic platform code

What every column may reach for, and the two Django apps that belong to
no feature.

- **`format.py`** — display formatting shared across module boundaries
  (`format_timecode` and siblings), plus `single_line` (S10) — the shared
  filename/title sanitiser: an uploaded filename becomes a document
  title and a chunk's `file_name` metadata verbatim, Django's own
  upload-name sanitizer keeps embedded newlines, and two columns
  (`agents.runtime`, `tools.rag`) that may not import each other both
  need to neutralise that, so this pure leaf is the only shared home. A
  rule-1 **pure leaf**: no Django models, no views, no database, no
  import of any non-pure module. ADR 0014 states the criterion for a
  shared leaf — four modules across a module boundary call it, "which is
  precisely the condition for a shared leaf"
  (`docs/adr/0014-media-ingestion.md:623-634`). Measured with
  `git grep -ln "foundation.format" -- '*.py'`: 25 files reference it, 17
  of them non-test, across 7 packages (`foundation`, `foundation.ops`,
  `models.queue`, `models.registry`, `tools.rag`, `tools.vision`,
  `agents.runtime`) — corrected from a stale "12 production import sites
  across 6 packages" (S10).
- **`files.py`** — the same, for filesystem helpers (3 production import
  sites).
- **`fence.py`** — `neutralize_fence_lines`, `carrying_delimiter`, and
  `carrying_block`: the BEGIN/END-marker machinery an LLM prompt uses to
  wrap third-party text (an attached file's content, a retrieved
  document's chunk text, a distilled conversation transcript) so a
  hostile body cannot forge a boundary and escape into the surrounding
  prompt (round-13 review I-1). The two primitives moved here from
  `agents/runtime/prompt.py` (H5 review round 1, CRITICAL): that
  module's own attachment-path fix was later reused, unmodified, by the
  S2 tool-result fence in the same file, and round-3's transcript
  distillation (C-3/H32) needed the same two functions with no
  chat-runtime dependency at all — exactly the "more than one column
  reaches for this" condition ADR 0014 states for a shared leaf.
  `carrying_block` — THE WRAP ITSELF, built from the two primitives —
  moved here too (H32 review round 1, IMPORTANT 3), once `tools/rag/
  distil.py` needed the identical wrap and a local re-assembly of it was
  found duplicating logic the primitives' own move was meant to head
  off: every caller of the wrap, in every column, now reaches this one
  function. A rule-1 **pure leaf**, like `format.py`/`files.py` above:
  no Django import, no project import, `re`/`secrets` only.
- **`settings_bounds.py`** — the same, for settings-field upper-bound
  checks (S1, Coherence Wave B): `BIGINT_FIELD_MAX`/`POSITIVE_INT_FIELD_MAX`,
  the real Postgres column ceilings, plus `exceeds_field_ceiling`. Promoted
  out of a `tools/rag`-private pair of constants that had already fixed
  this exact overflow-to-500 bug once and could not reach the columns that
  needed the same fix, because a private helper cannot cross a column
  boundary. A rule-1 pure leaf, same as `format.py`/`files.py`.
- **`settings_help.py`** — a fifth `foundation/` module other columns
  import: a rule-1 **pure leaf**, same as `format.py`, `files.py`,
  `fence.py` and `settings_bounds.py`, and
  the help-card registry `agents/settings_tools.py` reads to answer
  questions about a settings page. It also owns the gate vocabulary
  (`EVERYONE`, `ADMIN`, `ACCOUNTS_ADMIN`), which `settings_area.py`
  imports rather than redefines.
- **`uploads.py`** — `RequestBodyLimitMiddleware` (S9): refuses a request whose declared
  `Content-Length` exceeds `settings.MAX_REQUEST_BODY_BYTES` (default 4 GiB, `docs/OPERATIONS.md`
  §"The request-body cap"), before anything reads the body. `foundation/`, because a request-body
  bound is a deployment fact the base layer states rather than a library policy `tools.rag` would
  own — and `foundation` may not import `tools.rag` in any case (rule 2 below). Placed immediately
  after `SecurityMiddleware` in `config.settings.MIDDLEWARE`, before `CsrfViewMiddleware` and
  `SessionMiddleware`, both of which read POST data for a form-encoded request. It does not catch a
  lying `Content-Length` or a headerless chunked body — that needs a reverse proxy in front, which
  this box does not have today (`deploy/` is a README); see `docs/OPERATIONS.md` §"The
  request-body cap".
- **`http.py`** — `mark_private(response)` (B-8, round-3 hardening): sets `Cache-Control:
  private, no-store, max-age=0` and adds `Cookie` to `Vary` (additively, via
  `django.utils.cache.patch_vary_headers`). Unlike `format.py`/`files.py`/`fence.py` above,
  this module DOES import Django — it operates on an `HttpResponse` — but stays in
  `foundation/` for the same reason ADR 0014 gives those three a shared home: `tools.rag`
  and `tools.vision` both call it for their own content routes. See `docs/OPERATIONS.md`
  §"Private content never gets cached".
- **[`ops/`](ops/)** — operator tooling: `manage.py backup` / `restore`.
  A Django app, label `ops`, and **column-private** under rule 2.
- **[`setup/`](setup/README.md)** — the universal engine-install page. A
  Django app, label `setup`, column-private under rule 2.
- **[`landing/`](landing/README.md)** — the front door at `/`: one entry card per
  surface this box can actually use, and an honest empty state when nothing is
  bound. A Django app, label `landing`, column-private under rule 2. Owns no
  model, issues no query, and imports nothing from another column — it renders
  entirely off the availability context processor the shell already runs.
- **`foundation/templates/_shell.html`** — the shared page shell every app's base
  template extends, reached through `TEMPLATES[0]["DIRS"]`. It owns the design
  tokens and the ONE app bar: a single row — brand, Chat, Ask, Search,
  Document library, Ask history, Images, Queue, Settings — with the account
  area at its right. A use-surface entry renders only when the role behind it
  has a model bound — `surface_available`, see `models/registry/README.md`,
  "The availability signal". Queue and Settings are not availability-gated:
  Queue is activity, and Settings is where an operator fixes "nothing is
  bound" FROM. Zero JS; a page marks itself current by overriding the matching
  `nav_current_*` block.
- **`foundation/templates/_settings.html`** — the settings area's own shell,
  a thin extension of `_shell.html`. It owns the two-column layout and the
  sidebar every operator surface now lives behind: **Setup** (Models, Library,
  Install guides), **Access** (Accounts, Groups, Entitlements, Tool access,
  Agent access) and **Box** (Identity & security). Each entry keeps the gate
  it had in UI-1's Manage row, and a group whose every entry is hidden renders
  nothing at all. A settings page extends this instead of `_shell.html`,
  writes into `settings_content`, and marks its own `side_current_*` block;
  the bar's `Settings` highlight is set here, once, for all of them.
- **`foundation/settings_area.py`** — the same sidebar table as Python, and
  the `/settings/` landing route that redirects to the first section the
  viewer may open (`Install guides` is ungated, so there is always one). The
  two readers are held together by a drift test in
  `foundation/tests/test_shell.py`.

  `_shell.html` also owns the two controls more than one page renders:
  the status `.chip`, and the **chip picker** (`.chip-picker` / `.chip-check`) —
  a `<label>`-wrapped checkbox that looks like the chip it toggles, coloured off
  `:checked` with no JS. That picker is how this box asks "pick any number of
  entitlements" on the document library, Tool access and Agent access; no page
  renders a raw `<select multiple>` for it any more.
- **`foundation/templates/_messages.html`** — the shared flash-message partial, included at ~15
  sites across chat, identity, the model console, the queue, rag and vision. Before it existed each
  of those carried its own copy of the `.messages`/`.msg` markup, and they had drifted.
- **`_shell.html` owns the shared tokens and primitives.** Beyond the palette it started with, it
  now carries the `--danger-text` and `--mono` custom properties and four shared primitives
  (`.banner`, `.muted`, `.empty`, and the `.delete-disclosure > summary` rule), promoted here as
  the deepest common ancestor of the pages that use them.
- **`foundation/ops/tests/test_css_ownership.py`** is the CSS counterpart of the import law: it
  walks every template in the repository and enforces *which* CSS may live *where* — a shared rule
  belongs in the shell, a page-specific one belongs on the page, and a fragment carries no `<style>`
  at all (with `_shell.html` and `_settings.html` excluded as root ancestors, not fragments). Same
  shape as the Python import-law gates above: a structural invariant with a test that fails loudly
  rather than a convention that erodes.
- **`foundation/templates/_poller.html`** — the one poll loop
  (`pollUntilTerminal(url, opts)`), included by the pages that watch a
  queued job: `rag/ask.html` and `vision/create.html`. An **include, not
  part of the shell**: a script in `_shell.html` would render on every
  page in the box, and most pages never poll. The helper owns *when* to
  poll — interval, transport-retry ceiling, give-up ceiling, scheduling —
  and never what a response means, which is why Ask can keep a JSON state
  machine while Images swaps an HTML fragment. Tuning lives in one
  `window.POLL_DEFAULTS` object so a page can override it.
  `chat/conversation.html` still has its own copy, held for PR #88.

  `_shell.html` also owns the settings area's deep-link highlight: a bare
  `.settings-main :target` rule, no script and no class toggling, painted with
  the `--target-wash` token (`color-mix(in srgb, var(--accent) 12%, var(--panel))`,
  plus an `--accent` ring) rather than `--panel` — several settings sections
  (Install guides' cards, `_settings.html`'s own `.msg`) are already painted
  `--panel`, so a `--panel` highlight would be invisible on exactly the pages a
  deep link is most likely to land on. It lives here rather than in
  `_settings.html`'s own `extra_style` block because a leaf page that overrides
  `extra_style` without `{{ block.super }}` would otherwise silently drop the
  rule on that page -- `setup/index.html` did, until Coherence Wave A fixed it;
  the rule stays in this unconditional region rather than moving back, since
  it is robust to any future leaf page making the same mistake.

## The import law

- **Rule 1 — pure leaves are universally importable.** `format.py`,
  `files.py`, `fence.py`, `settings_bounds.py`, everything under
  `models/contracts/`, everything under `agents/contracts/`, and
  everything under `identity/contracts/`. Any column, any direction.
- **Rule 2 — Django apps are column-private.** `ops/` and `setup/` are
  not importable from `tools/`, `models/`, or `agents/`. There are
  **four** named exceptions in the whole repo: `models.registry.bindings`,
  importable by `tools/*` and `agents/*` and by nothing else in this
  column; `models.queue.visibility` (IA-1), importable by `tools/*` and
  `agents/*` and by nothing else in `models.queue` -- the rows-versus-
  content answers (`visible_jobs`, `visible_rows`, `may_see_job_id`,
  `may_read_job_content`) `tools/rag/views.py::AskJobStatusView` and
  `tools/vision/views.py::queue_job_status` need for their poll routes,
  named so that neither ever reaches `models.queue.backend` or
  `models.queue.models` directly for an answer this module already owns;
  `foundation.ops` → `models.queue.models`, only for the
  active-work refusal (RUNNING jobs), and nothing else; and `agents.
  entitlements` (IA-2), importable by `tools/vision` and `tools/rag` and
  by nothing else outside `agents` -- the door each of their two direct-
  surface routes (`vision-generate`, the library's own upload route)
  asks through: `tool_access_for(principal).allows(<tool key>)` is the
  one column-owned answer to "may this principal call this labelled
  tool", asked identically by the POST gate and by the page's own
  render, so the two can never drift into two different truths.
  **Said plainly: unlike the other three exceptions above, no AST guard
  polices this `tools → agents` direction today** — `foundation/ops/
  tests/test_import_law.py` sweeps the `agents → tools` direction
  (`test_no_agents_module_imports_a_tools_package`) and closes the three
  `models.registry`/`models.queue`/`foundation.ops` exceptions to named
  callers, but `agents.entitlements`'s own callers are recorded here and
  in the two call sites' own docstrings rather than enforced by a sweep;
  inventing one to police a single edge would be its own change (decision
  20), not something this phase does.

  `identity` is also a Django app, and is column-private under the same
  rule -- **with named seams**: `identity.contracts.*`, `identity.access`
  (posture, admin-ness, `owner_fields`), `identity.request` (this HTTP
  request's principal) and `identity.audit` (write one audit event) are
  the four modules every column may import -- and only those four:
  enforced as an ALLOWLIST, `foundation/ops/tests/
  test_import_law.py::IDENTITY_PERMITTED`, the same gate shape
  `models.registry.models`/`.views` already have. Every other module
  under `identity/` -- `identity.models`, `identity.views`, `identity.
  services`, `identity.forms`, `identity.middleware`, and any later
  addition -- is off-limits to every other column with no exception and
  no separate list to update, because a module absent from the allowlist
  is closed by construction. See [`identity/README.md`](../identity/README.md)
  for the column as a whole.
- **Rule 3 — cross-column *work* goes through a seam, never an import:**
  the queue (`models.contracts.queue`), the gateway
  (`models.contracts.gateway`), and the tool registry
  (`agents.contracts.tools`).

Named `foundation/` and not `platform/`: `platform` is a stdlib module
name, `manage.py` puts the repo root on `sys.path[0]`, and a `platform/`
package here would break `manage.py collectstatic` if it were ever run —
and does break a bare `import platform` today, which is the argument
that actually decides the name. See ADR 0010 §2 and the spec's §2.1.
