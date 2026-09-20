"""The `identity/` column: WHO is acting, and under what posture.

A DJANGO APP (`IdentityConfig`, `identity/apps.py`), the base column
every other column may import from. It owns:

- `contracts/` -- the rule-1 pure leaf: `Principal` (the platform's
  base identity type, moved here from `agents/contracts/tools.py`),
  the posture names, the closed audit-action catalogue, and the
  owned-rows registry. NO DJANGO, NO DATABASE, NO I/O -- pinned by
  `identity/tests/test_purity.py`.
- `models.py` -- the swapped-in `User` (`AUTH_USER_MODEL`),
  `IdentitySettings` (the one-row posture singleton), and
  `AuditEvent` (the append-only trail of every guarded write).
- `access.py` -- the NAMED SEAM every column reads to answer
  "may this principal be here", "may they read that content", "what
  do I stamp as owner" -- `identity.models`, `.views`, `.services`,
  `.forms` and `.middleware` stay off-limits to every other column.
- `request.py` -- the one place an HTTP request becomes a `Principal`.
- `middleware.py` -- the gate: coarse route-tier enforcement plus the
  rolling session window, run once per request.
- `routes.py` -- the route -> tier table the gate enforces.
- `gate.py` -- explicit per-view decorators for the handful of callers
  that do not arrive through the middleware.
- `services.py` -- the guarded writes (posture switch, promote/demote,
  password reset) with their refusals and audit rows.
- `cascades.py`/`axes.py` -- the two registry RESOLVERS, one per
  direction of the same seam: what each column LOSES when an
  entitlement is deleted, and which of each column's rows CARRY one (the
  entitlement pages' reach columns and transfer panels). Both resolve
  dotted-path strings the columns register from their own
  `AppConfig.ready()`, because rule 4 below forbids naming those
  columns here; neither ever swallows a resolution failure.
- `audit.py` -- the one writer and one reader of `AuditEvent`, a NAMED
  SEAM every column may import; the only module allowed any
  `AuditEvent.objects` attribute access at all.
- `admin.py` -- the break-glass `/admin/` surface (user model only,
  routed through `identity.services`'s own guards); the only `admin.py`
  in the repository.
- `checks.py` -- the system checks `manage.py check` runs at boot.
- `views.py`/`forms.py`/`urls.py`/`templates/` -- login, logout,
  password change, the users page, and the posture settings page.
- `context_processors.py` -- `identity_posture`/`identity_is_admin`/
  `identity_accounts_on` for the shared shell template.
- `management/commands/` -- `identity_posture` (read/switch posture),
  `adopt_open_rows` (first-admin claims an open box's history),
  `reassign_owner` (hand rows to somebody else), and
  `identity_repair_migration_history` (the one-time `django_migrations`
  bookkeeping fix an existing database needs before IA-1's `migrate`
  will run at all).

Import-law rule 4 (spec section 4.2): `identity/` imports `foundation`,
Django, and its own `contracts` -- and nothing from `agents`, `tools` or
`models`. Pinned by `foundation/ops/tests/test_import_law.py`.
"""
