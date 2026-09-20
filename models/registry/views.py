"""
Models (/inference/) -- HTTP surface.

This is `models/registry`'s own `ui.mount: /inference` page: a single
`ConsoleView` (GET) plus six `@require_POST` FBVs (`connection_add`,
`connection_remove`, `machine_model_add`, `role_assign`, `role_reencode`,
`server_scan`), mirroring `tools/rag/views.py`'s idioms (plain forms,
`django.contrib.messages`, redirect-after-POST, no JS required). It is the
operator-facing surface for which engine+model backs each role
(`models.contracts.roles.all_roles`), which models are on the machine or in
the catalog (`models.registry.discovery.discover`), and whether a role's
materialized data has drifted from its current binding
(`models.registry.drift`).

IA-2 T14 adds model **sets** (spec section 6.10) and three more routes:
`connection_sets` (`@require_POST`, beside `connection_remove` on the
console partial -- the edge an operator edits when a new model arrives),
`model_sets` (GET lists, POST creates -- `identity/views.py::entitlements`'s
own shape) and `model_set_edit` (`@require_POST`; rename/delete/attach/
detach, dispatched the same way `identity/views.py::_entitlement_action`
is). All three are class S: a model connection is box inventory, not
somebody's library row, so `identity/routes.py` classifies them S rather
than R and `IdentityGateMiddleware` refuses a non-admin before any of
these functions runs -- none of them imports `identity.gate` (the Task 6
precedent).

`connection_remove` deletes a `ModelConnection`; `RoleBinding.connection`'s
`on_delete=SET_NULL` (models.py) means any role bound to it is left
unassigned -- the honest DB behavior, surfaced rather than fought (a
chat-bound removal applies immediately with a visible note; an
embeddings-bound one reuses `role_assign`'s unbind confirm gate and
first-materialization stamp, since an embeddings-model change is
drift-relevant either way). The "On this machine" and "Registered
connections" rows are real aligned-column grids (the needs-table's
`display: contents` idiom, extended) -- see `_installed_rows_table.html`
and `_registered_connection.html`.

One pipeline: **found -> registered -> in use**. Registration is an
INTENTIONAL act -- config records must never appear as side effects (a
one-step dropdown that silently creates a connection leaves behind an
unexplained orphan). Three states, one flow direction:

- **Found** (auto-detected fact): an "On this machine" row. An unregistered
  row with a detected capability carries an "Add to registered" button
  (`machine_model_add`) that creates the connection auto-filled from
  detection -- idempotent via the same norm_tag + normalized-endpoint
  identity used everywhere else, so a double submit reuses, never
  duplicates. A row whose capability the engine did NOT report gets a link
  to the manual form prefilled with model_id + endpoint instead -- the
  console never guesses a capability.
- **Registered** (config; the ONLY dropdown source): the role rows' "change"
  dropdowns list capability-matching registered connections plus an
  unassign option -- nothing else. `role_assign` only binds (`conn:<pk>`)
  or unassigns, it never creates a connection.
- **In use** (role pointers): confirm gates, pre-rebind stamps, and the
  endpoint-scoped display logic.

There are NO baked model defaults anywhere. A role is backed by a
registered connection, or by an operator's EXPLICIT environment override,
or it is genuinely UNASSIGNED and says so ("No model assigned",
warning-styled, pointing at the pipeline). "Unbind" is therefore UNASSIGN
-- it usually leaves the role with no model at all rather than dropping it
onto a default that no longer exists -- and every "environment defaults"
string on this surface is gone.

Settings transparency: every row (role or inventory) carries a native
`<details>` "Connection details" disclosure showing engine / exact model
id / endpoint / capability / embed_dim AND the source of each (detected
from engine, from catalog, manual, environment defaults).

One source of truth: editing lives in exactly one place, the "Registered
connections" section (`_connection_views`), where every `ModelConnection`
is listed once with its one edit form (`connection_add`'s update mode,
anchored `id="conn-<pk>"`); the role-row and installed-row disclosures are
read-only reflections that link there ("Edit in Registered connections ↓")
for anything actually registered -- never a second, independently
submittable copy of the same connection's edit form.

Two states, one page:

- **Cold start** -- no `ModelConnection` registered yet, or the default
  engine is unreachable: the console can't usefully show role bindings
  (there is nothing to bind). When the default endpoint IS reachable and
  `discover()` finds models already installed there, the page leads with
  the SAME machine rows (Add buttons first) and role rows the warm page
  uses (first run teaches the permanent pipeline), instead of telling an
  operator with a stocked engine to go pull something. The "Getting
  models" block (needs checklist with live status + one adapter-derived
  facts line per model server; no pinned model names anywhere) renders in
  every state. The manual "register a connection" form is always present
  too, as the advanced path.
- **Warm** -- at least one connection exists and the engine is reachable:
  the console shows three plain sections:
    - **In use** -- each role's current binding, the drift banner + guarded
      re-encode, when the role's data was last materialized, the "change"
      dropdown (registered connections only), and the details disclosure.
    - **On this machine** -- only models `discover()` reports as
      `installed=True`, each with its pipeline-state control (Add button /
      manual link / registered chip), the loaded-now chip, and its details
      disclosure.
    - **Getting models** -- the needs checklist and per-model-server facts,
      all derived from the role registry and the engine adapters.
  The advanced/custom-endpoint form (`connection_add`) is the only other way
  to create a connection (e.g. a model on another machine), and stays
  available in both states; editing an existing connection happens on that
  connection's own details disclosure (same view, update mode).

Health failures degrade to cold start rather than 500ing; a discovery
failure on an otherwise-healthy engine stays warm and only empties the
on-this-machine/catalog sections (see `_build_context`). Every registered
engine's `is_healthy` and `discover()` already swallow HTTP errors
internally, and the `try/except` here is defense in depth for anything else
so a broken connection can never take the whole page down.

`server_scan` is the ONLY call site for
`models.registry.discovery.scan_for_servers`. Nothing on this page's GET
path (`ConsoleView`) ever calls it; the "Scan for model servers" button is
a plain form POST, so the sweep only ever runs on an explicit click, same
as every other action here. `ConsoleView` also supports `?endpoint=`
(`_endpoint_context`) -- a scan hit's "Use this endpoint" link reloads the
console pointed at a server the default health check never would have
found.
"""
from __future__ import annotations

import inspect
import ipaddress
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, OperationalError, ProgrammingError, transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from identity import audit
from identity.access import is_admin, labelling_entitlements
from identity.contracts import actions
from identity.contracts.principals import payload_fields
from identity.request import principal_for_request, settings_row_for, user_for_request
from models.registry.bindings import resolved_from_connection
from models.registry.discovery import discover, norm_endpoint, norm_tag, scan_for_servers
from models.registry.labels import (
    SetRefused, attach, create_set, delete_set, detach, rename_set, set_delete_counts,
    set_memberships_for,
)
from models.registry.models import (
    Materialization, ModelConnection, ModelSet, ModelSetMember, RoleBinding,
)
from models.registry import probe_cache
from models.queue.models import QUEUED, RUNNING, InferenceJob
from foundation.format import gb_to_bytes, human_bytes
from foundation.settings_area import preserve_assistant_flag, settings_redirect
from foundation.settings_bounds import BIGINT_FIELD_MAX, exceeds_field_ceiling
from models.contracts.bindings import ResolvedModel, embed_dim_from_fingerprint, env_provider, resolve
from models.contracts.catalog import find
from models.contracts.engines import ENGINES
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable, enqueue
from models.contracts.roles import (
    CAPABILITIES,
    RAG_ANSWER_ROLE,
    RAG_EMBED_ROLE,
    RoleSpec,
    all_roles,
    get_role,
)

logger = logging.getLogger(__name__)


def _default_endpoint() -> str:
    """The endpoint the console health-checks/discovers against by default.

    A fresh box has NO model assigned (no baked model defaults), so
    `resolve(RAG_ANSWER_ROLE)` legitimately raises here and
    `env_provider` legitimately answers None -- the console must still have
    an address to scan, or first run would have nothing to show. It falls
    back to `settings.OLLAMA_BASE_URL`, which is exactly the one setting the
    ruling KEEPS a default for: a server *location* is deploy convention
    (ADR 0006 -- host Ollama on macOS dev, the Ollama container on the
    appliance), not a model choice. Nothing about a model is presumed by
    this; an unassigned role still reads "No model assigned".
    """
    try:
        return resolve(RAG_ANSWER_ROLE).endpoint
    except Exception:  # noqa: BLE001 -- degrade, never 500
        pass
    resolved = None
    try:
        resolved = env_provider(RAG_ANSWER_ROLE)
    except Exception:  # noqa: BLE001 -- degrade, never 500
        resolved = None
    return resolved.endpoint if resolved is not None else settings.OLLAMA_BASE_URL


def _engine_endpoints(connections: list[ModelConnection], endpoint: str) -> dict[str, list[str]]:
    """`discover()`'s per-engine endpoint map (spec §4.6 / D11).

    Per engine, the union of: its default endpoint from
    `settings.INFERENCE_DEFAULT_ENDPOINTS`, every registered connection's
    endpoint for that engine, and `endpoint` -- the address this page is
    currently viewing (the resolved default, or an operator's `?endpoint=`
    override), added for every REGISTERED engine. Including `endpoint`
    that way preserves exactly the pre-D11 behaviour (every engine polled
    at the one viewed address) as a superset, so an override still scans
    for every engine.

    The defaults pass reads the settings map itself rather than
    `ENGINES`, so an engine whose default address is configured but whose
    adapter is not registered yet still gets a bucket; `discover()` only
    ever polls engines that ARE registered (it iterates `ENGINES` and
    looks the name up here), so an unused key costs nothing.

    De-anchored by construction: no engine name, port, or path is written
    here -- keys come off the settings map, the connections, and each
    adapter's own `.name`.
    """
    engine_map: dict[str, list[str]] = {}

    def _add(engine_name: str, candidate: str) -> None:
        if not candidate:
            return
        bucket = engine_map.setdefault(engine_name, [])
        if all(norm_endpoint(candidate) != norm_endpoint(seen) for seen in bucket):
            bucket.append(candidate)

    for engine_name, default in settings.INFERENCE_DEFAULT_ENDPOINTS.items():
        _add(engine_name, default)
    for connection in connections:
        _add(connection.engine, connection.endpoint)
    for engine in ENGINES.values():
        _add(engine.name, endpoint)

    return engine_map


# S5: the closed set of hosts the console may be steered into probing.
# `is_healthy` + `discover()` + three catalogue calls is ~10 outbound
# requests per rendered page, with a boolean reachability oracle and
# partial response reflection coming back -- so the question "which hosts
# may this box be asked to speak to" needs a real answer rather than "any
# host with an http scheme".
#
# REVIEW ROUND 1 (orchestrator ruling): the first pass also permitted any
# address in a private IP range (RFC1918), reasoning that a colleague's
# LAN box is a legitimate target. That clause is DROPPED -- it is a much
# bigger set than "hosts this box may legitimately speak to", and it is
# exactly what let the drive-by example below (`http://10.0.0.5:22`) sail
# past the allowlist even with the admin gate in place: `10.0.0.5` is
# private, so the old clause admitted it unconditionally. Only two things
# are permitted now: the box's own loopback interface (`_host_is_loopback`
# below), and a host already known here -- a registered `ModelConnection`
# or a configured default endpoint. A private-but-unregistered address is
# refused exactly like a public one; the fix for a real engine on such an
# address is to register it (the console's own "Advanced" form), which is
# a workflow an operator must complete anyway before a role can bind to
# it -- never an env-level relaxation of this check.
_LOOPBACK_HOSTNAMES = frozenset({"localhost"})

# The two schemes `_endpoint_is_well_formed` accepts, and each one's
# default port -- so "http://host" and "http://host:80" compare equal.
# An exact-string port comparison would treat an implicit default port as
# a different endpoint than the same address spelled with one explicit.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_is_loopback(host: str) -> bool:
    """True for the "localhost" name and for any address in 127.0.0.0/8
    or `::1` -- the box's own loopback interface, reachable from the
    console by design regardless of what else is registered (an operator
    running the engine on the same host as the web process needs no
    registration step to reach it). `ipaddress...is_loopback` already
    covers the whole 127.0.0.0/8 block, not just `127.0.0.1` -- a bare
    string allowlist of just that one address would miss the rest of it.
    """
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback


def _endpoint_is_well_formed(raw: str) -> tuple[bool, str]:
    """`(is this a plain http/https URL with a host, that host lowercased)`.

    S19's half of the question, extracted so there is ONE parser: a
    stored `ModelConnection.endpoint` and a `?endpoint=` override are
    different POLICY questions over the same SYNTAX, and two parsers
    would be two chances to disagree about what a host is.

    `(False, "")` for anything this refuses, including a malformed IPv6
    literal. On the installed interpreter a bad bracketed host (an
    unbalanced `[`, or one that isn't a valid IPv6 literal) makes
    `urlsplit()` itself raise `ValueError` -- not `.hostname` -- so both
    are caught: an attacker chooses this string, so it is refused rather
    than allowed to 500.
    """
    try:
        parsed = urlsplit((raw or "").strip())
    except ValueError:
        return False, ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, ""
    try:
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False, ""
    return bool(host), host


def _endpoint_triple(raw: str) -> tuple[str, str, int] | None:
    """The normalised `(scheme, host, port)` triple for `raw`, or `None`.

    Built ON `_endpoint_is_well_formed` (the one parser, review-round-1
    ruling) rather than a second `urlsplit()` call of its own -- there is
    still exactly one place that decides what counts as a well-formed
    endpoint. `None` for anything that parser refuses, AND for a
    syntactically-http(s) URL whose port is not a number
    (`urlsplit(...).port` raises `ValueError` for that, same never-500
    shape as everywhere else in this pair of functions) -- a stored
    `ModelConnection.endpoint` may predate this task's own validation, or
    be edited by hand, so this is reached on data this process did not
    just validate, not only on a fresh `?endpoint=` query string.

    Ports are normalised to the scheme's default when the URL omits one,
    so a triple comparison treats `http://host` and `http://host:80` as
    the SAME endpoint, and `https://host:22` as a DIFFERENT one from
    `http://host:11434` even though the host matches -- comparing host
    strings alone would conflate a registered engine's real port with any
    other port an attacker names at the same host.
    """
    ok, host = _endpoint_is_well_formed(raw)
    if not ok:
        return None
    parsed = urlsplit(raw.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(parsed.scheme)
    return parsed.scheme, host, port


def _override_is_permitted(raw: str) -> bool:
    """Whether `raw` is an endpoint this box may be steered into probing.

    Permitted: the box's own loopback interface (`_host_is_loopback`), or
    a `(scheme, host, port)` triple that exactly matches a `ModelConnection`
    already registered here or a configured default endpoint
    (`_endpoint_triple` on each side of the comparison -- never a bare
    host-string match, which would let `https://gpu-box.example:22`
    through just because `http://gpu-box.example:11434` is registered).

    Refused: everything else -- a public address, a colleague's
    unregistered LAN box, and **the cloud metadata service on
    169.254.169.254** (link-local, and `ipaddress...is_loopback` -- unlike
    the dropped `is_private` clause from the first pass -- correctly
    answers False for it, so no explicit carve-out is needed here).

    NOT A DNS-REBINDING DEFENCE, and this docstring says so rather than
    letting the name imply it: the name is resolved by httpx at request
    time, after this check, so a hostname that resolves to a permitted
    address now and a different one a second later would pass. That is a
    materially harder attack than the one this closes (a query string in
    an <img> tag), and closing it needs resolve-then-connect-by-IP, which
    httpx does not offer. Recorded, not fixed.
    """
    triple = _endpoint_triple(raw)
    if triple is None:
        return False
    _scheme, host, _port = triple
    if _host_is_loopback(host):
        return True
    known = set()
    for endpoint in [
        *settings.INFERENCE_DEFAULT_ENDPOINTS.values(),
        *ModelConnection.objects.values_list("endpoint", flat=True),
    ]:
        if not endpoint:
            continue
        # Reuses `_endpoint_triple` (the one parser) rather than a bare
        # `urlsplit()` -- a malformed STORED endpoint (legacy data typed
        # before S19's own validation existed, or edited by hand) is
        # skipped here exactly like a malformed override is refused
        # above, never allowed to raise and 500 the console.
        known_triple = _endpoint_triple(endpoint)
        if known_triple is not None:
            known.add(known_triple)
    return triple in known


def _requested_override(request) -> str | None:
    """The operator's endpoint override for this request, or None.

    A scan hit's "Use this endpoint" link reloads the console with
    `?endpoint=<url>`. That override lives only in the URL, so the page's
    plain forms would silently drop it on every POST -- the scan and assign
    forms therefore round-trip it in a hidden
    `endpoint_override` field, read here (POST only) as a fallback when the
    query param is absent or invalid. Both sources go through the SAME
    validation.

    S5, half one -- ADMIN ONLY, and STATED HONESTLY (review round 1): every
    route that reaches this function (`inference-console`,
    `inference-role-assign`, `inference-connection-add`,
    `inference-machine-add`, `inference-server-scan`,
    `inference-connection-remove`, `inference-connection-sets`) is tier
    `S` in `identity.routes.ROUTE_RULES`, so `IdentityGateMiddleware.
    process_view` already answers
    403 for a signed-in NON-ADMIN in `personal`/`enterprise` **before the
    view runs at all** -- this check never fires for that case in
    practice. It is DEFENCE IN DEPTH here, not the primary gate: it keeps
    this function correct if it is ever called from a differently-gated
    path, or directly (as this task's own tests do, bypassing the
    middleware on purpose to exercise the function in isolation). On the
    default `open` box there is no route gate to speak of either way --
    `identity.access.is_admin` is True for `OPEN_PRINCIPAL` by design ("on
    a box with no accounts there is nobody for anything to be hidden
    from") -- so the ALLOWLIST below is what actually closes the
    default-install case, on every posture.

    S5, half two, AND THE ONE THAT MATTERS ON A DEFAULT INSTALL: the
    allowlist (`_override_is_permitted`) replaces the old "http/https
    scheme and a non-empty netloc" check, which admitted every address on
    the internet. `<img src="http://box.lan:8000/inference/?endpoint=
    http://10.0.0.5:22">` on any page a LAN user visits made this box
    speak to arbitrary internal addresses, ~10 outbound probes per render,
    with a reachability oracle coming back -- `10.0.0.5:22` is now
    genuinely refused (review round 1 dropped the private-IP-range clause
    the first pass had, which admitted it unconditionally): loopback
    remains reachable from the console by design, but a private,
    unregistered LAN address does not.

    Deliberately forgiving, not strict form validation: a missing/blank/
    malformed/disallowed value is just ignored silently, exactly like an
    unresolvable role falls back to "unbound" elsewhere on this page --
    never a 400/500 over a query string.
    """
    # `settings_row_for` reuses `IdentityGateMiddleware`'s own stashed read
    # when it already ran (every real request) rather than fetching the
    # singleton twice -- once for `principal_for_request`'s `accounts_on`
    # check and again for `is_admin`'s -- on every console render, whether
    # or not an override is even being requested (C-56a's query-count
    # test covers the no-override case).
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if not is_admin(principal, settings_row=settings_row):
        return None
    candidates = [request.GET.get("endpoint", "")]
    if request.method == "POST":
        candidates.append(request.POST.get("endpoint_override", ""))
    for raw in candidates:
        raw = raw.strip()
        if raw and _override_is_permitted(raw):
            return raw
    return None


def _endpoint_context(request) -> tuple[str, str]:
    """(the endpoint this request's console targets, the active override
    or "" when none).

    The override counts as *active* only while it still differs from the
    (possibly moved) default endpoint -- an override that now equals the
    default collapses to no-override, so the forms stop round-tripping it
    and the redirects stop appending it. No session state anywhere: the
    override exists only in the URL and the forms' hidden field.
    """
    override = _requested_override(request)
    default = _default_endpoint()
    if override is not None and norm_endpoint(override) != norm_endpoint(default):
        return override, override
    return default, ""


def _redirect_console(request):
    """Redirect-after-POST back to the console, preserving an active
    endpoint override: without this, role_assign's
    embeddings confirm-gate would bounce the operator back to the DEFAULT
    endpoint's page -- losing the very row they were just told to resubmit
    on -- and a successful assign would likewise dump them off the
    override page they acted from. No override active (or it now equals
    the default) -> the plain console URL, exactly as before.

    AND THE ASSISTANT PANEL'S OPEN FLAG, on the same principle and by
    the same mechanism (persistence round): what the operator had on
    screen when they submitted is what they get back. Two parameters,
    one join -- `preserve_assistant_flag` reads the query it is handed
    and appends with `&`, so an override and an open panel survive the
    same redirect together.
    """
    _, override = _endpoint_context(request)
    url = reverse("inference-console")
    if override:
        url = f"{url}?endpoint={quote(override)}"
    return redirect(preserve_assistant_flag(request, url))


def _check_health(endpoint: str) -> bool:
    """True if ANY registered engine reports `endpoint` reachable.

    Cached for `probe_cache.CACHE_TTL_SECONDS` (C-07): this runs on every
    console GET and on every POST -> `_redirect_console()` -> GET round
    trip, so an operator doing three edits used to pay six HTTP round
    trips to the same model server. The cache is dropped whenever a
    registry row changes, so an edit is never read through a stale answer.
    """
    return probe_cache.health(endpoint, _probe_health)


def _probe_health(endpoint: str) -> bool:
    """The uncached probe -- every registered engine, first `True` wins,
    an engine that raises counts as unreachable rather than as a 500.

    Iterates `ENGINES` rather than hardcoding a single engine, with the same
    per-engine try/except isolation as `discover()`'s installed-scan, so one
    broken/unregistered engine can't make an otherwise-healthy endpoint read
    as unreachable.
    """
    for engine in ENGINES.values():
        try:
            if engine.is_healthy(endpoint):
                return True
        except Exception:  # noqa: BLE001 -- unreachable/broken engine: try the next one, never 500
            continue
    return False


def _engine_source_path(engine) -> str:
    """The registered engine adapter's own module file, relative to the
    repo root (e.g. "models/contracts/engines/ollama.py") -- the
    "Supported APIs" disclosure and its engine dropdowns want the
    implementing file visible, not just a name. Falls back to the bare
    dotted module path when the file can't be resolved relative to
    `settings.BASE_DIR` (a test double registered from outside the repo
    tree, e.g. a `MagicMock` stub) -- never raises.
    """
    try:
        file_path = Path(inspect.getfile(type(engine))).resolve()
        return str(file_path.relative_to(settings.BASE_DIR))
    except (TypeError, ValueError, OSError):
        return type(engine).__module__


def _format_well_known_ports(ports: tuple[int, ...]) -> str:
    """"default port 11434" / "default ports 11434, 11435" / "no
    well-known port" -- the plain-English tail of a "Supported APIs" line."""
    if not ports:
        return "no well-known port"
    if len(ports) == 1:
        return f"default port {ports[0]}"
    return f"default ports {', '.join(str(p) for p in ports)}"


def _supported_engines() -> list[dict]:
    """View-models for the "Supported APIs" disclosure and the engine
    dropdowns in the create/edit forms. The support boundary is the engine
    ADAPTER (one .py per API protocol),
    not the model catalog -- so this reads name/description/well-known
    ports straight off `ENGINES` and derives the adapter's own file path,
    with NOTHING about a specific engine hardcoded here. A second
    registered adapter appears in both places automatically. One pass over
    `ENGINES.values()` -- no query, no state.

    `library_url` and `default_endpoint`/`install_cmd_template` ride along
    on the same pass -- the "Getting models" facts line and the connection
    forms' endpoint autofill
    (`data-endpoint` per dropdown option, plus the server-rendered prefill)
    read them straight off here, so a second registered engine brings its
    own facts line, dropdown option, and autofill with zero template
    change. `default_endpoint` is the engine's usual address, derived from
    its first well-known port; an engine declaring no ports gets "" and
    the templates omit the corresponding fragments.

    This registry pass is also the support boundary made visible:
    the engine dropdowns and the "Supported APIs" listing
    render EXACTLY these entries -- no placeholder, example, or
    coming-soon entries exist anywhere in the UI.
    """
    entries = []
    for engine in ENGINES.values():
        ports = getattr(engine, "well_known_ports", ())
        entries.append(
            {
                "name": engine.name,
                "description": getattr(engine, "api_description", ""),
                "source_path": _engine_source_path(engine),
                "ports": _format_well_known_ports(ports),
                "library_url": getattr(engine, "library_url", ""),
                "default_endpoint": f"http://localhost:{ports[0]}" if ports else "",
                "install_cmd_template": getattr(engine, "install_cmd_template", ""),
            }
        )
    return entries


# The durable "This system needs:" phrasing, one line per distinct
# capability among registered roles. Singular, human
# phrasing per platform capability (`models.contracts.roles.CAPABILITIES`);
# a capability outside this map (none exist today) still renders a plain
# fallback rather than raising.
_CAPABILITY_NEED_PHRASES = {
    "chat": "a chat model",
    "embeddings": "an embedding model",
    "vision": "a vision model",
    "image-generation": "an image-generation model",
    "transcription": "a speech-to-text model",
}


def _needs_checklist(roles: list[RoleSpec], installed_rows: list[dict]) -> list[dict]:
    """One checklist row per DISTINCT capability among `roles`, with LIVE
    status derived from the SAME installed rows the page already computed
    (`_installed_views`' output; no new query, no re-scan -- the
    query-count contract holds). Separate `requirement`/`used_by` fields
    instead of one pre-joined `line` string -- the template renders these
    as a 3-column table
    (Requirement / Used by / Status) so a row's status always lands on the
    same column, rather than a prose sentence whose ✓ landed at a different
    horizontal spot depending on how long the requirement/role text was.

    `satisfied` means a model with that capability is INSTALLED on the
    machine being viewed -- deliberately not "bound to a role": binding
    already has its own section ("In use"), and this checklist answers the
    getting-models question ("do I have one to get?"), not the wiring one.
    An installed row whose capability the model server didn't report
    satisfies nothing (registering it manually, with an operator-supplied
    capability, is what decides it). No model
    names ride along: pinned names were rejected twice (they age), and
    the live names already appear in the role dropdowns and "On this
    machine".

    Fully derived from the role registry: a future role registering a new
    capability (e.g. vision) adds its own row here with zero code change.
    Two roles sharing a capability still produce exactly one row,
    attributed to whichever of them registered first.

    T9: unions each row's FULL `capabilities` set, not just its single
    `capability` tie-break -- an installed vision-capable chat model
    reporting `("chat", "vision")` satisfies BOTH needs, not only whichever
    one the tie-break happened to pick as primary/display. `row.capability`
    is still folded in too (belt-and-suspenders for any row whose
    `capabilities` wasn't populated -- a stub/older `DiscoveryRow` in a
    test, or a catalog/connection edge case -- so this never regresses
    to under-counting what `capability` alone already proved satisfied).
    """
    installed_capabilities: set[str] = set()
    for item in installed_rows:
        row = item["row"]
        if row.capability:
            installed_capabilities.add(row.capability)
        installed_capabilities.update(row.capabilities)
    seen: set[str] = set()
    checklist: list[dict] = []
    for role in roles:
        if role.capability in seen:
            continue
        seen.add(role.capability)
        phrase = _CAPABILITY_NEED_PHRASES.get(role.capability, f"a {role.capability} model")
        checklist.append(
            {
                "requirement": phrase,
                "used_by": role.label,
                "satisfied": role.capability in installed_capabilities,
            }
        )
    return checklist


def _human_size(num_bytes: int | None) -> str | None:
    """Human-readable size for an installed model's on-disk footprint (e.g.
    "4.7 GB"), or None if the engine didn't report one. Shown on "on this
    machine" rows only -- `DiscoveryRow.size` is None for anything not
    `installed`.

    The byte-count-to-string ladder itself is `foundation.format.human_bytes`
    (T10 -- this function used to carry its own copy, one of three
    identical duplicates); this wrapper keeps its own `None`-for-falsy
    contract, which `human_bytes` itself doesn't have an opinion about --
    "no footprint reported" and "a footprint of zero bytes" are the same
    honest "nothing to show" here, but that's this call site's meaning to
    assign, not the shared formatter's.
    """
    if not num_bytes:
        return None
    return human_bytes(num_bytes)


# Operator-facing labels for `DiscoveryRow.capability_source` (the
# "nothing hidden" disclosure): where a row's capability/embed_dim actually
# came from. "connection" reads as "manual" -- a connection-stored value was
# entered (or accepted) by an operator, as opposed to detected live.
_SOURCE_LABELS = {
    "engine": "detected from the model server",
    "catalog": "from catalog",
    "connection": "manual",
}


def _installed_views(
    discovered: list,
    in_use_keys: set[tuple[str, str]],
    connections: list[ModelConnection],
    endpoint: str,
) -> list[dict]:
    """The "on this machine" (installed) view-models from `discover()`'s
    merged rows. Rows that are not installed here are simply not shown
    anywhere: the catalog is enrichment data only, never display.

    Each row is the head of the found -> registered -> in-use
    pipeline: an unregistered row with a detected capability renders the
    "Add to registered" button (posting to `machine_model_add`), an
    unregistered row with NO detected capability renders the
    prefilled-manual-form link instead (never a guessed registration), and
    a registered row shows a "registered" chip -- all keyed off
    `connection`, the matching registered `ModelConnection` under the same
    norm_tag + normalized-endpoint identity `machine_model_add`'s
    create-or-reuse applies. That endpoint is now the row's OWN
    (`DiscoveryRow.endpoint`, D11) rather than the page's, so a row found
    at a second engine's address matches (and registers against) that
    address; `endpoint` remains the fallback for a row that carries none. The row also carries its details-disclosure
    data (the source label for its capability/embed_dim, the connection's
    name/created date, the link to its one edit form) and the
    loaded-now truth, `loaded_size_display`, humanized from the ps
    report's already-fetched loaded size (no new call; `getattr` so a
    stub row predating the field degrades to no size). `in_use_keys` is
    empty for the cold branch by construction: `cold_start` is only True
    while healthy if there are NO registered connections yet (see
    `_build_context`), so nothing can already be "in use".
    """
    local_connections: dict[tuple[str, str, str], ModelConnection] = {}
    for connection in connections:
        local_connections.setdefault(
            (connection.engine, norm_tag(connection.model_id), norm_endpoint(connection.endpoint)),
            connection,
        )

    rows = []
    for row in discovered:
        if not row.installed:
            continue
        # A row now knows the endpoint it was actually seen at (D11); fall
        # back to the page's endpoint for any row a stub/older adapter
        # produced without one.
        row_endpoint = row.endpoint or endpoint
        rows.append(
            {
                "row": row,
                "endpoint": row_endpoint,
                "size_display": _human_size(row.size),
                "loaded_size_display": _human_size(getattr(row, "loaded_size", None)),
                "in_use": (row.engine, norm_tag(row.model_id)) in in_use_keys,
                "connection": local_connections.get(
                    (row.engine, norm_tag(row.model_id), norm_endpoint(row_endpoint))
                ),
                "source_label": _SOURCE_LABELS.get(row.capability_source),
            }
        )
    return rows


def _connection_source_label(connection: ModelConnection, discovered: list, endpoint: str) -> str:
    """Operator-facing source label for `connection`'s capability/embed_dim:
    the SAME label the installed row shows
    (`_SOURCE_LABELS`, keyed by `discover()`'s own `capability_source`) when
    this connection's model is actually installed at the endpoint being
    viewed, else "manual" -- there is nothing to detect against, so the
    connection's stored value is the only fact there is.

    Fixes the role-row reflection's old hardcoded "manual (stored on this
    connection)", which was wrong whenever the same model was ALSO
    engine-detected on this machine (the installed row already got this
    right; the role row didn't). Endpoint-scoped like every other identity
    check on this page (`found_on_machine`, `in_use`): a same-tag model at a
    DIFFERENT endpoint is not the same installed fact, so it still reads as
    "manual" rather than borrowing an unrelated machine's detection.
    """
    if norm_endpoint(connection.endpoint) == norm_endpoint(endpoint):
        key = (connection.engine, norm_tag(connection.model_id))
        for row in discovered:
            if row.installed and (row.engine, norm_tag(row.model_id)) == key:
                return _SOURCE_LABELS.get(row.capability_source, "manual")
    return "manual"


def _connection_views(
    connections: list[ModelConnection], discovered: list, endpoint: str, role_views: list[dict]
) -> list[dict]:
    """View-models for the "Registered connections" section -- the ONE
    editing home for every `ModelConnection`: the role-row and installed-row
    disclosures never carry their own edit form, only a read-only reflection
    that links here, so the same connection never has two independently
    submittable edit forms live at once. Every connection is listed once,
    with:

    - its capability/embed_dim source label (`_connection_source_label`,
      the same logic the installed row uses -- no hardcoded "manual");
    - its bound-roles note ("used by: <role label>, ..."), derived from the
      already-built `role_views` -- no new query, same query-count
      absorption discipline as the rest of this module.

    This also gives a connection that is bound to NO role and installed
    NOWHERE the console scans (e.g. a remote-endpoint connection) its first
    ever editing surface -- before this it appeared on no disclosure at
    all.

    `bound_has_embeddings`: whether any of those bound roles is
    "embeddings"-capability -- `connection_remove`'s template gate for the
    confirm checkbox, the same signal `connection_add`'s own fingerprint-
    change gate derives straight from the DB (`role_bindings`) rather than
    this already-built view-model, since the two call sites want the same
    fact for different reasons and neither should have to re-derive it from
    the other.

    `member_set_ids` (IA-2 T14): the `ModelSet` ids this connection
    already belongs to -- `_registered_connection.html`'s multiselect
    prefill, from ONE bulk query over every connection rather than one
    membership lookup per row.
    """
    bound_role_labels: dict[int, list[str]] = {}
    bound_has_embeddings: dict[int, bool] = {}
    bound_outcomes: dict[int, list[tuple[str, bool]]] = {}
    member_set_ids: dict[int, list[int]] = {}
    for connection_id, model_set_id in ModelSetMember.objects.filter(
            connection__in=connections).values_list("connection_id", "model_set_id"):
        member_set_ids.setdefault(connection_id, []).append(model_set_id)
    for view in role_views:
        connection = view["current_connection"]
        if connection is not None:
            bound_role_labels.setdefault(connection.pk, []).append(view["role"].label)
            # What removing this connection actually
            # does to each bound role depends on whether THAT role has an
            # explicit environment override -- `_role_view` already resolved
            # it (pure settings read, no query), so it is carried through
            # here rather than re-derived. One connection can be bound to
            # several roles with DIFFERENT outcomes, so this is a per-role
            # fact, never averaged into one.
            bound_outcomes.setdefault(connection.pk, []).append(
                (view["role"].label, view["env_override"] is not None)
            )
            if view["role"].capability == "embeddings":
                bound_has_embeddings[connection.pk] = True

    return [
        {
            "connection": connection,
            "source_label": _connection_source_label(connection, discovered, endpoint),
            "bound_role_labels": bound_role_labels.get(connection.pk, []),
            "bound_has_embeddings": bound_has_embeddings.get(connection.pk, False),
            "removal_note": _removal_note_short(bound_outcomes.get(connection.pk, [])),
            "removal_note_title": _removal_note_title(bound_outcomes.get(connection.pk, [])),
            # T2b facts <dl>: human-readable sizes computed here (the
            # template has no way to format bytes itself) -- `None` when
            # the underlying field is unset, so the template's `{% if
            # item.connection.footprint_override_bytes %}` gate already
            # decides whether either is ever rendered.
            "footprint_override_display": _human_size(connection.footprint_override_bytes),
            "measured_footprint_display": _human_size(connection.measured_footprint_bytes),
            "member_set_ids": member_set_ids.get(connection.pk, []),
        }
        for connection in connections
    ]


# --- Removal consequence copy -----------------------------------------------
#
# Removing a `ModelConnection` clears every `RoleBinding` pointing at it
# (SET_NULL). What BACKS each of those roles afterwards is not uniform: a
# role with an explicit environment override falls straight onto it and keeps
# working; a role without one is left with no model at all. Saying
# "unassigned" for both would be a flat contradiction -- the flash would
# claim a role was unassigned while the same page rendered that role's
# "explicit environment override" badge. These two helpers are the one
# place that phrasing is decided, so the pre-removal note and the post-removal
# flash cannot drift apart.
#
# `outcomes` is a list of (role label, role has an explicit env override).


def _removal_note_title(outcomes: list[tuple[str, bool]]) -> str:
    """The full-sentence consequence, for the note's `title` attribute.

    Mixed bindings name each group explicitly rather than picking a
    majority: a note that is right about one role and wrong about another
    is the same defect as before, just smaller.
    """
    if not outcomes:
        return ""
    labels = [label for label, _ in outcomes]
    overridden = [label for label, has_override in outcomes if has_override]
    unassigned = [label for label, has_override in outcomes if not has_override]

    if not overridden:
        tail = (
            "leaves them with no model assigned"
            if len(unassigned) > 1
            else "leaves it with no model assigned"
        )
    elif not unassigned:
        tail = (
            "hands them to their explicit environment override"
            if len(overridden) > 1
            else "hands it to its explicit environment override"
        )
    else:
        tail = (
            f"hands {', '.join(overridden)} to the explicit environment override "
            f"and leaves {', '.join(unassigned)} with no model assigned"
        )
    return f"in use by {', '.join(labels)} — removing {tail}"


def _removal_note_short(outcomes: list[tuple[str, bool]]) -> str:
    """The compact visible note, a one-line form for the actions
    cell; the full sentence lives in the `title` above."""
    if not outcomes:
        return ""
    overridden = [label for label, has_override in outcomes if has_override]
    unassigned = [label for label, has_override in outcomes if not has_override]
    if not overridden:
        return "removing → unassigns those roles"
    if not unassigned:
        return "removing → environment override takes over"
    return "removing → changes what backs those roles"


def _stamp_first_materialization(roles: list[RoleSpec], fingerprint: str) -> None:
    """Stamp `Materialization(role_key=role.key, fingerprint=fingerprint)`
    for every role in `roles` that has no `Materialization` row yet -- never
    overwriting an existing one.

    The shared first-materialization mechanics behind three call sites
    (`connection_add`'s update path, `connection_remove`, `role_assign`'s
    first embeddings bind/rebind): ingestion never stamps, so a role's
    first-ever fingerprint-changing action would otherwise never trip
    `is_drifted()`'s drift banner (see `models.registry.drift`) even
    though existing data was encoded under the PRE-change binding.
    `fingerprint` is always that pre-change fingerprint -- the caller's job
    is computing it (the two connection-keyed call sites always have one in
    hand; `role_assign`'s single-role call site may not, and skips calling
    this at all when it doesn't).
    """
    for role in roles:
        if not Materialization.objects.filter(role_key=role.key).exists():
            Materialization.objects.create(role_key=role.key, fingerprint=fingerprint)


def _severity_copy(dim_changed: bool) -> str:
    """The two-severity re-encode copy (embed_dim change vs same-dim swap)."""
    if dim_changed:
        return (
            "the embedding dimension changed, so this requires a full document "
            "store rebuild before results can be trusted."
        )
    return (
        "the model changed but the embedding dimension is unchanged, so "
        "documents can be re-encoded in place."
    )


def _unassign_consequence(role: RoleSpec) -> str:
    """What actually happens when a role's connection is taken away -- the
    clause spliced into the unassign gate/confirmation and removal copy.

    There is no baked default to fall back to (ADR 0010), so the honest
    answer depends on whether this operator set an
    EXPLICIT environment override for the role: with one, it takes over;
    without one, the role is left with NO model at all and everything that
    needs it reports unavailable until it is assigned again. Saying "back to
    environment defaults" (the old copy) would be a lie in the ordinary case
    -- nothing is there.

    Guarded like every other resolution on this module's POST paths: a
    settings problem degrades to the no-model wording, never a 500.
    """
    try:
        override = env_provider(role.key)
    except Exception:  # noqa: BLE001 -- never let a settings problem 500 a POST
        override = None
    if override is not None:
        return "the explicit environment override for this role takes over"
    return (
        "no model will back it and anything that needs it reports unavailable "
        "until you assign one"
    )


def _drift_severity(current: ResolvedModel, materialization: Materialization) -> str:
    """Two-severity copy for a role whose active binding has drifted from
    its last materialization.

    `embed_dim_from_fingerprint` (`models.contracts.bindings`, next to the
    `f"{engine}:{model_id}:{embed_dim}"` format it parses) recovers the
    trailing segment -- `model_id` may itself contain colons (e.g.
    "a-chat-model:7b"), so a plain `split(":")` would be wrong.
    """
    prior_embed_dim = embed_dim_from_fingerprint(materialization.fingerprint)
    dim_changed = str(current.embed_dim) != prior_embed_dim
    return _severity_copy(dim_changed)


# Adjective forms for the empty-dropdown helper copy ("No embedding models
# found ..."): only "embeddings" differs from its own capability key.
_CAPABILITY_NOUNS = {"embeddings": "embedding"}


def _role_options(
    role: RoleSpec, connections: list[ModelConnection], endpoint: str
) -> list[dict]:
    """The one "change" dropdown's options for a role: REGISTERED
    connections only (registration is an intentional act, so the registry is
    the ONLY dropdown source), filtered to the role's capability, any
    endpoint. An installed-but-unregistered model is offered nowhere here --
    its "Add to registered" button on the machine row is the way in.

    Option values are `conn:<pk>` (all facts live in the DB row); the
    template appends the `unbind` option itself while a connection is
    bound. Labels are endpoint-scoped: a connection at the
    page's scanned `endpoint` reads "— on this machine", any other names
    its endpoint explicitly.

    `connections` is expected pre-sorted in picker order
    (`ModelConnection.objects.picker_order()`, called once in
    `_build_context` -- not per role, keeping the query count flat the same
    way the rest of this page already does), so this function only filters,
    never re-sorts. When a connection carries an operator descriptor, its
    label leads with "name — descriptor" instead of the bare name, keeping
    the same endpoint-scoping tail; an undescribed connection's label is
    unchanged.
    """
    endpoint_key = norm_endpoint(endpoint)
    options = []
    for connection in connections:
        if role.capability not in connection.capabilities:
            continue
        display_name = connection.name
        if connection.descriptor:
            display_name = f"{connection.name} — {connection.descriptor}"
        if norm_endpoint(connection.endpoint) == endpoint_key:
            label = f"{display_name} ({connection.model_id}) — on this machine"
        else:
            label = f"{display_name} ({connection.model_id}) @ {connection.endpoint}"
        options.append({"value": f"conn:{connection.pk}", "label": label})
    return options


def _role_view(
    role: RoleSpec,
    picker_connections: list[ModelConnection],
    binding: RoleBinding | None,
    materialization: Materialization | None,
    installed_keys: set[tuple[str, str]],
    endpoint: str,
    discovered: list,
) -> dict:
    """Build one role's row: its current binding, the drift state
    (embeddings roles only), the "change" dropdown's options
    (`_role_options` -- registered connections only), and the
    details-disclosure data (including the env-variable names when the role
    rides an explicit environment override).

    Three honest states, not two-and-a-fallback: a role is backed by a
    registered connection, or by an operator's EXPLICIT environment
    override, or it is genuinely UNASSIGNED. There is no baked default to
    fall into, so `current` is
    None whenever nothing has been chosen and the template renders the
    "No model assigned" warning state rather than a model nobody picked.

    `env_override` is computed whether or not a connection is bound -- the
    connection wins while it exists, but the copy around unassigning has to
    say truthfully what the role falls back TO (an override, if one is set;
    nothing at all otherwise), so both facts must be in hand at once. It is
    a pure settings read (`env_provider`), no query.

    Query-efficiency absorption: `binding` and `materialization`
    are already-fetched rows from `_build_context`'s bulk queries, not
    per-role lookups. When a connection is currently bound, the displayed
    model is derived straight from `binding.connection` rather than calling
    `resolve()` a second time. When it isn't, `resolve()` is also skipped --
    its DB half (`models.registry.bindings.db_provider`) would only
    re-run the very query `binding` already answered (no row, or a row with
    a null connection), so the env half (`env_provider`) is called
    directly. This keeps the page's query count independent of how many
    roles are registered/unbound; a role using a custom
    `INFERENCE_BINDING_PROVIDER` that answers from something other than
    `RoleBinding` is out of scope for this page's own binding-derived
    display (it still resolves correctly everywhere actual inference
    happens, via the real `resolve()`).

    `picker_connections` is the SAME
    registered-connections set `_build_context` already fetched, just
    re-sorted once via `ModelConnection.objects.picker_order()` -- one
    extra query per page load, not per role -- so `_role_options` never has
    to re-sort a per-role slice.
    """
    current_connection = binding.connection if binding else None

    try:
        env_override: ResolvedModel | None = env_provider(role.key)
    except Exception:  # noqa: BLE001 -- an unresolved role shows as unassigned, not a 500
        env_override = None

    if current_connection is not None:
        current: ResolvedModel | None = resolved_from_connection(current_connection)
    else:
        current = env_override

    # Seed-migration honesty: migration 0002 binds connections seeded from an
    # operator's explicit env overrides (named "env: LLM_MODEL" / "env:
    # EMBED_MODEL") on a fresh migrate, whether or not that model is actually
    # installed -- so a role can be legitimately bound to a model the live
    # scan can't find. (With no override set, nothing is seeded at all and
    # this never fires.) Both facts are
    # cheap to determine from what's already in hand: the connection's own
    # name carries its env-seeded provenance, and `installed_keys` (built
    # once from `discover()`'s output) says whether the scan found it.
    #
    # `norm_tag()` on the connection's model_id matters here: the seed
    # migration (and hand-typed connections) can store a bare model_id
    # (e.g. "an-embedding-model") while `installed_keys` -- built from
    # `discover()`'s output -- always holds the engine's exact, tagged
    # string (e.g. "an-embedding-model:latest"). Comparing raw strings would
    # falsely report an installed model as "not found on this machine".
    #
    # Endpoint scoping matters just as much: `installed_keys` only carries
    # (engine, model_id) -- every installed row came from the ONE scanned
    # `endpoint` -- so a bound connection pointed at a DIFFERENT endpoint
    # must never be checked against it at all. A same-tag model at a
    # different, unrelated machine is not "found on this machine", but it
    # is also not honestly "not found on this machine" (it might well exist
    # there) -- so `found_on_machine` stays None (no marker rendered
    # either way) rather than being compared cross-endpoint.
    found_on_machine = None
    env_seeded = False
    if current_connection is not None:
        env_seeded = current_connection.name.startswith("env: ")
        if norm_endpoint(current_connection.endpoint) == norm_endpoint(endpoint):
            found_on_machine = (
                current_connection.engine,
                norm_tag(current_connection.model_id),
            ) in installed_keys

    drifted = False
    severity = None
    if role.capability == "embeddings" and current is not None and materialization is not None:
        drifted = current.fingerprint != materialization.fingerprint
        if drifted:
            severity = _drift_severity(current, materialization)

    # Env-override disclosure ("nothing hidden"): a role riding an
    # operator's explicit environment override shows the RESOLVED values
    # (from `current`, never re-read from settings in the template) and names
    # the variables they came from. `env_provider` only answers the two
    # shipped roles, so the variable names can be derived from the role key
    # alone; any other role that somehow resolved here would still show its
    # values, just unnamed.
    env_model_var = None
    if current_connection is None and current is not None:
        env_model_var = "EMBED_MODEL" if role.key == RAG_EMBED_ROLE else "LLM_MODEL"

    # The same source label the installed row shows for this
    # connection's model, so the role-row reflection never hardcodes
    # "manual" regardless of provenance. None when there's no connection to
    # label (env override / unassigned) -- the template branches on that.
    connection_source_label = None
    if current_connection is not None:
        connection_source_label = _connection_source_label(current_connection, discovered, endpoint)

    # "Currently: "a-chat-model:8b" (a-chat-model:8b)" would duplicate the same
    # string when an operator's connection name IS the model id (the common
    # case for anything created from the dropdown's model-pick path, whose
    # auto-generated name is the model id itself). Render it once when name
    # and model_id match case-insensitively; keep both when they differ.
    current_connection_name_matches_id = (
        current_connection is not None
        and current_connection.name.strip().lower() == current_connection.model_id.strip().lower()
    )

    # The "unassign" dropdown option's label states
    # the real consequence -- falling back to an explicit environment
    # override where one is set, or leaving the role with no model at all
    # otherwise. Computed here, next to `env_override`, so there is exactly
    # one place this copy is decided rather than each template site
    # re-deciding the same fact independently.
    # `None` (never rendered) when there is no connection bound, matching
    # the template's own `{% if view.current_connection %}` guard around
    # the option.
    unassign_option_label = None
    if current_connection is not None:
        unassign_option_label = (
            "unassign — fall back to the environment override"
            if env_override is not None
            else "unassign — leave this role with no model"
        )

    return {
        "role": role,
        "current": current,
        "env_override": env_override,
        "current_connection": current_connection,
        "current_connection_name_matches_id": current_connection_name_matches_id,
        "found_on_machine": found_on_machine,
        "env_seeded": env_seeded,
        "options": _role_options(role, picker_connections, endpoint),
        "capability_noun": _CAPABILITY_NOUNS.get(role.capability, role.capability),
        "env_model_var": env_model_var,
        "connection_source_label": connection_source_label,
        "unassign_option_label": unassign_option_label,
        "drifted": drifted,
        "severity": severity,
        "materialization": materialization,
    }


def _build_role_views(
    roles: list[RoleSpec], picker_connections: list[ModelConnection], discovered: list, endpoint: str
) -> list[dict]:
    """Fetch each role's binding/materialization in two bulk queries
    (query-efficiency absorption -- count stays flat regardless of how
    many roles are registered) and assemble the per-role view-models.

    Shared by the warm branch and the cold-start branch with
    installed models: cold start renders the SAME role rows, their
    dropdowns empty until something is registered, with the helper text
    pointing at the machine rows' Add buttons, so this is the one place
    they are built rather than two copies that could drift.

    `picker_connections`: pre-sorted via
    `ModelConnection.objects.picker_order()` -- see `_role_view`.
    """
    role_keys = [role.key for role in roles]
    bindings = {
        binding.role_key.lower(): binding
        for binding in RoleBinding.objects.select_related("connection").filter(role_key__in=role_keys)
    }
    materializations = {
        materialization.role_key.lower(): materialization
        for materialization in Materialization.objects.filter(role_key__in=role_keys)
    }
    # Normalized (norm_tag) on both this side and every comparison against
    # it: `row.model_id` is already the engine's exact tagged string for an
    # installed row, but normalizing here too keeps every comparison site
    # symmetric and idempotent rather than relying on that invariant.
    installed_keys = {(row.engine, norm_tag(row.model_id)) for row in discovered if row.installed}

    return [
        _role_view(
            role,
            picker_connections,
            bindings.get(role.key.lower()),
            materializations.get(role.key.lower()),
            installed_keys,
            endpoint,
            discovered,
        )
        for role in roles
    ]


def _engine_options(engine, endpoint: str, what: str) -> list[str]:
    """The engine's own list for one registration field, or `[]`.

    `what` is `"families"` (read via the optional `list_families` member),
    `"variants"` (the optional `list_variants` member -- ADR 0012 D-EDIT-6,
    parameterless: unlike a family, a variant is not an engine vocabulary
    intersected with anything, so no `endpoint` is passed), or an asset kind
    (`"text_encoder"`, `"vae"`). Every optional protocol member is read with
    `getattr` and every call is wrapped: an engine that is down, or an
    adapter that predates a member, costs this page an empty `<select>` and
    never a 500 -- the same tolerance `tools.vision.services.live_options`
    applies for the same reason.
    """
    try:
        if what == "families":
            reader = getattr(engine, "list_families", None)
            return [] if reader is None else list(reader(endpoint))
        if what == "variants":
            reader = getattr(engine, "list_variants", None)
            return [] if reader is None else list(reader())
        reader = getattr(engine, "list_assets", None)
        return [] if reader is None else [asset.asset_id for asset in reader(endpoint, what)]
    except Exception:  # noqa: BLE001 -- an option list is never worth a 500
        logger.debug("Listing %r for %s failed", what, endpoint, exc_info=True)
        return []


def _family_endpoint(engine, connections: list[ModelConnection], endpoint: str, override: str) -> str:
    """The address the family-declaring `engine` is ASKED its vocabulary at.

    NOT the page's endpoint, which is what `_engine_options` used to be
    handed -- and the whole of the fresh-registry onboarding gap. The
    console targets ONE address at a time (`_endpoint_context`), and with
    no connections registered that address resolves to the chat role's
    default (`_default_endpoint`). Asking the image engine there answers
    nothing whether or not it is running, so the family/companion
    `<select>`s came back empty on every console render except one
    carrying an operator's `?endpoint=` override pointed at the image
    engine itself. A brand-new operator has no scan row to click, so they
    never saw the fields at all.

    Four candidates, first non-empty wins, most specific first:

    1. `override` -- an operator who explicitly steered this page at an
       address (a scan hit's "Use this endpoint") means that address, and
       it is the only path that worked before this fix. It must keep
       working for an engine on a port the settings map has never heard of.
    2. any REGISTERED connection's endpoint for this engine -- an operator
       whose engine is not on the shipped default port already told this
       box where it lives by registering against it.
    3. this engine's own configured default
       (`settings.INFERENCE_DEFAULT_ENDPOINTS`, keyed by the adapter's own
       `.name` -- no engine name is written here).
    4. `endpoint` -- the pre-fix behaviour, as the last resort for an
       engine with no configured default and nothing registered.

    S5 note: every candidate is an address this box may already probe --
    an override that passed `_override_is_permitted`, a stored
    connection's endpoint, or a configured default (the two classes that
    allowlist itself is built from). This widens no outbound surface; it
    only stops aiming the existing probes at the wrong one of them.

    NOT `_engine_endpoints`, which builds the same raw material into a
    per-engine BUCKET for `discover()`: that caller polls every address it
    knows and unions the answers, and so has no notion of ranking (it
    appends the page's endpoint to every engine, and puts the configured
    default ahead of a registered connection's). A `<select>`'s option
    list wants one authority, and which one is exactly the question here.
    """
    if engine is None:
        return endpoint
    name = getattr(engine, "name", "")
    candidates = [
        override,
        *(connection.endpoint for connection in connections if connection.engine == name),
        settings.INFERENCE_DEFAULT_ENDPOINTS.get(name, ""),
        endpoint,
    ]
    return next((candidate for candidate in candidates if candidate), endpoint)


def _build_context(endpoint: str, override: str = "") -> dict:
    """Assemble `ConsoleView`'s full context: health, discovery, the
    "Getting models" checklist+facts (every state), and either the
    cold-start onboarding or the warm-state sections (In use / On this
    machine / Getting models / Registered connections), depending on
    state.

    `endpoint` is passed in rather than resolved here -- the
    caller decides between `_default_endpoint()` (`ConsoleView`'s normal
    GET) and an operator-chosen endpoint override (`_endpoint_context`),
    and `server_scan` reuses this same context-building for its own
    re-rendered response.

    `override` is the SECOND half of that same `_endpoint_context` pair,
    passed in for the same reason: resolving it here would mean calling
    `_default_endpoint()` again, which costs a role-resolution query and
    would break this page's flat query-count pin. It is `""` for a plain
    render, and is read by `_family_endpoint` alone -- which needs to tell
    "the operator steered this page HERE" apart from "this is just the
    default address", a distinction `endpoint` on its own cannot carry.

    Note: a `discover()` failure does NOT by itself put the page into
    cold-start -- it only empties the on-this-machine rows (and reports
    every checklist need as not yet satisfied).
    Cold-start is solely a function of `healthy`/`connections` (see below);
    a healthy engine with registered connections stays on the warm page
    even if discovery itself errors, so an operator with a working setup
    never loses their role-bindings view to an unrelated discovery hiccup.

    Query-efficiency absorption: `RoleBinding`/`Materialization`
    are each fetched once via `filter(role_key__in=[...])` instead of up to
    four queries per role, and the already-fetched `connections` list is
    passed into `discover()` instead of it re-querying `ModelConnection`.
    This keeps the page's query count flat regardless of how many roles are
    registered.
    """
    healthy = _check_health(endpoint)

    connections = list(ModelConnection.objects.all())
    # The SAME registered connections, once, re-sorted by the one
    # ordering rule every picker shares (`ModelConnection.objects.
    # picker_order()`) -- fed to role dropdowns below. A second query, not
    # a second per-role one, so the flat query-count invariant this function
    # already keeps still holds; `connections` above stays
    # in `Meta.ordering`'s plain name order for the "Registered
    # connections" / "On this machine" sections, which are not
    # reordered.
    picker_connections = list(ModelConnection.objects.picker_order())

    try:
        endpoints = _engine_endpoints(connections, endpoint)
        # C-07: the same 30s window as `_check_health`, keyed on the
        # actual endpoint VALUES this call is about, not just the engine
        # names -- an operator's `?endpoint=` override changes the set
        # `_engine_endpoints` builds without touching the registry (the
        # one thing that invalidates this cache), so a key built from
        # engine names alone would keep serving a stale override's
        # discovery answer for up to 30 seconds after switching.
        # `discover` walks every engine's discovery endpoint over HTTP and
        # is the more expensive of the two probes.
        cache_key = "discover:" + "|".join(
            sorted({url for urls in endpoints.values() for url in urls})
        )
        discovered = probe_cache.cached(cache_key, lambda _key: discover(endpoints, connections))
    except Exception:  # noqa: BLE001 -- discovery failure degrades to empty sections
        discovered = []

    # No connection registered, or nothing to reach -- there is nothing
    # useful to bind yet, so the page onboards instead of showing empty
    # role/model sections.
    cold_start = not connections or not healthy

    supported_engines = _supported_engines()

    # `all_roles()` is pure/in-memory (no query), read once here: it feeds
    # both the role rows and the "Getting models" needs checklist below.
    roles = all_roles()

    context = {
        "endpoint": endpoint,
        "healthy": healthy,
        "discovered": discovered,
        "cold_start": cold_start,
        "capabilities": sorted(CAPABILITIES),
        # The create form's engine dropdown (cold-start
        # and warm alike) and the "Supported APIs" disclosure both read
        # this same registry pass -- in context unconditionally since both
        # branches below render a create form.
        "supported_engines": supported_engines,
        # Bare names, so `_connection_edit.html` can
        # tell whether a connection's STORED engine names a registered
        # adapter at all -- when it doesn't (legacy data, a renamed/
        # deregistered engine), the edit form renders it as its own
        # selected "(not registered)" option instead of silently falling
        # through to whichever option happens to be first.
        "supported_engine_names": {entry["name"] for entry in supported_engines},
        # T2b's facts <dl> (`_registered_connection.html`) reads its
        # provenance strings straight from `_SOURCE_LABELS` -- no new
        # vocabulary alongside "manual" / "detected from the model server".
        "footprint_source_labels": _SOURCE_LABELS,
    }

    # The multi-file registration fields (ADR 0012 D-EDIT-2/3): the engine
    # that actually declares a family vocabulary at all -- today that's the
    # one adapter with a `list_families` member, found structurally rather
    # than by name so a second such engine needs no change here.
    #
    # `None` only when NO registered adapter declares one. No shipped
    # configuration is such an install: `models.contracts.engines`
    # registers the image adapter unconditionally at import, with no
    # feature or posture gate, so on a real box this is never `None` and
    # the fieldset below always renders (inside the collapsed
    # Add-a-connection disclosure). The `None` branch is for a build whose
    # adapter set was cut down, and for the tests that mock `ENGINES`.
    # Passed on unchanged either way: `_engine_options` degrades a `None`
    # engine (via `getattr`) to an empty list, `_family_endpoint` answers
    # with the page's own endpoint, and the fieldset does not render --
    # so this never needs its own guard.
    family_engine = next(
        (candidate for candidate in ENGINES.values() if getattr(candidate, "list_families", None) is not None),
        None,
    )
    # Asked at the ENGINE's own address, not the page's -- see
    # `_family_endpoint` for the gap that fixes. No new query and no extra
    # probe: the same three calls, aimed at an address that can answer
    # them (and cached below, which the page endpoint's health check
    # already was and these were not).
    family_endpoint = _family_endpoint(family_engine, connections, endpoint, override)
    # Whether the fieldset renders AT ALL -- the one bit the template used
    # to infer, wrongly, from `family_options` being non-empty. An engine
    # that is merely down is not an install where the concept does not
    # apply, and conflating the two deleted the form's family fields from
    # under an operator with no explanation (and threw away the offline
    # `variant_options` with them). True on every shipped configuration,
    # per the note above.
    context["family_fields_offered"] = family_engine is not None
    context["family_engine_name"] = getattr(family_engine, "name", "")
    context["family_endpoint"] = family_endpoint
    # C-07, extended (round-1 review M1). These three go over HTTP, and
    # they now go to an address this page does NOT health-check -- so a
    # `COMFYUI_BASE_URL` naming a blackholing host costs 3 x
    # `DISCOVERY_TIMEOUT` on EVERY console render and every `server_scan`
    # render, forever: `_object_info` memoizes successes only, never a
    # failure. `discover()` is wrapped just above for exactly this reason;
    # these were not. Keyed on the endpoint AND the kind, under their own
    # `options:` prefix, sharing `_check_health`/`discover`'s dict and
    # generation counter -- so registering a connection (which can move
    # `family_endpoint`) drops these answers on the same signal that drops
    # the others, and an operator never reads a 30-second-old option list
    # back after their own edit.
    #
    # THE TRADE, STATED (round-1 review R1). `probe_cache` caches whatever
    # the probe returned, and `_engine_options` turns a failure into `[]`
    # -- so this caches an OUTAGE for the same 30 seconds it caches a
    # successful list. That is not an oversight and it is not free: it is
    # precisely what buys the fix above, because a cache that skipped
    # failures would leave the blackholing-host case paying its three
    # timeouts on every render, which is the whole cost being removed.
    # Note it inverts `_object_info`'s own rule one layer down
    # (`models.contracts.engines.comfyui`), which deliberately memoizes
    # successes ONLY -- correctly, for an adapter that cannot know how
    # often it is called. This layer does know: it runs per page render,
    # on a page an operator reloads by hand. The operator-visible cost is
    # that starting a stopped engine is not reflected until the window
    # passes, which is why the empty state says so in as many words
    # instead of implying that "reload" is instant. Anything that CHANGES
    # the registry still invalidates immediately, via the shared
    # generation counter -- so this only ever delays news the operator
    # made elsewhere, never news they made here.
    #
    # `variants` is deliberately NOT cached: `list_variants()` takes no
    # endpoint and reads the adapter's own template package in memory.
    # There is no round trip to save, and caching it would only add a way
    # for it to be wrong.
    def _cached_options(what: str) -> list[str]:
        return probe_cache.cached(
            f"options:{what}:{norm_endpoint(family_endpoint)}",
            lambda _key: _engine_options(family_engine, family_endpoint, what),
        )

    context["family_options"] = _cached_options("families")
    context["variant_options"] = _engine_options(family_engine, family_endpoint, "variants")
    context["text_encoder_options"] = _cached_options("text_encoder")
    context["vae_options"] = _cached_options("vae")

    if cold_start:
        # When the endpoint is healthy, `discovered` (already
        # computed above from the SAME `discover()` call the warm page
        # uses) may already hold installed rows -- e.g. no connection has
        # ever been registered, but the operator's engine has models on it.
        # `cold_start` is only True while `healthy` if `connections` is
        # empty (see the boolean above: `not healthy` alone would make this
        # False if connections existed), so there is nothing yet "in use"
        # to exclude. Those installed rows lead with their "Add to
        # registered" buttons, and the SAME role rows the warm page renders
        # sit below them (dropdowns empty -- nothing registered yet -- with
        # the helper text pointing back at the Add buttons); first run
        # teaches the permanent found -> registered -> in-use pipeline.
        # When unhealthy, `discovered` naturally holds no installed rows
        # (the engine call that would report them failed), so this is a
        # no-op there; gating on `healthy` explicitly still matches the
        # brief's "when the default endpoint is healthy" and keeps the
        # never-500 contract: a `discover()` exception already degraded
        # `discovered` to `[]` above, so this can't raise either.
        if healthy:
            installed_rows = _installed_views(discovered, set(), connections, endpoint)
        else:
            installed_rows = []
        context["installed_rows"] = installed_rows
        if installed_rows:
            context["role_views"] = _build_role_views(roles, picker_connections, discovered, endpoint)
        # The "Getting models" checklist's live status reuses the
        # installed rows just built -- on an unhealthy (or empty) endpoint
        # every need honestly reads as missing.
        context["needs_checklist"] = _needs_checklist(roles, installed_rows)
        return context

    role_views = _build_role_views(roles, picker_connections, discovered, endpoint)
    context["role_views"] = role_views

    # Same tag-normalization AND endpoint-scoping concern as
    # `found_on_machine` above: a role's bound connection may hold a bare
    # model_id, and -- separately -- may point at a different endpoint
    # than the one just scanned. Every "on this machine" row is at the
    # scanned `endpoint` by construction, so a bound connection at some
    # OTHER endpoint must never be counted as "in use" here even if its
    # (engine, model_id) happens to match -- same tag, different machine,
    # not necessarily the same model.
    in_use_keys = {
        (view["current_connection"].engine, norm_tag(view["current_connection"].model_id))
        for view in role_views
        if view["current_connection"] is not None
        and norm_endpoint(view["current_connection"].endpoint) == norm_endpoint(endpoint)
    }

    installed_rows = _installed_views(discovered, in_use_keys, connections, endpoint)
    context["installed_rows"] = installed_rows

    # Live status for "Getting models", from the SAME rows the
    # "On this machine" section renders -- no extra query, no re-scan.
    context["needs_checklist"] = _needs_checklist(roles, installed_rows)

    # "Registered connections" -- every ModelConnection's one
    # editing home. Built from `connections` (already fetched above) and
    # `role_views` (already built above too) -- no new query.
    context["connection_views"] = _connection_views(connections, discovered, endpoint, role_views)
    # `_registered_connection.html`'s membership multiselect (IA-2 T14) --
    # every model SET on the box, so a connection with no memberships yet
    # still gets a full picker rather than an empty one.
    context["all_model_sets"] = list(ModelSet.objects.all())

    return context


class ConsoleView(TemplateView):
    """GET /inference/ -- the model management console.

    Never runs the scan -- `_endpoint_context` only reads an already-
    chosen `?endpoint=` (e.g. from a scan hit's "Use this endpoint" link);
    it does not probe anything itself.

    Also reads `?prefill_model_id=` / `?prefill_endpoint=` -- a
    degraded machine row (capability unknown) links to the manual form
    PREFILLED with the detected model_id and the endpoint it was found at
    (`#add-connection`), because the console never guesses a capability on
    the operator's behalf. Pure form-initial values: template-escaped,
    validated nowhere (a tampered value just prefills a form field the
    operator was going to edit anyway -- never a 400/500), and never
    persisted.
    """

    template_name = "inference/console.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        endpoint, override = _endpoint_context(self.request)
        context.update(_build_context(endpoint, override))
        context["endpoint_override"] = override
        context["scan_results"] = None
        context["prefill_model_id"] = self.request.GET.get("prefill_model_id", "").strip()
        context["prefill_endpoint"] = self.request.GET.get("prefill_endpoint", "").strip()
        return context


_UNKNOWN_ENGINE_MESSAGE = (
    "Unknown model server “{engine}” — the supported model-server "
    "APIs are listed on the console."
)
_NAME_TAKEN_MESSAGE = "A connection named “{name}” already exists."


@dataclass(frozen=True)
class _ConnectionForm:
    """`connection_add`'s POST, parsed, catalog-enriched and validated --
    everything the write step needs and nothing about HTTP.

    `instance` is the row being edited, or `None` in create mode; every
    other field is already in the type the model column wants, so
    `_write_connection` assigns rather than converts. `confirm` and
    `family_declared` are the two bits that are about the SUBMISSION
    rather than about the connection: whether the operator ticked
    "confirm rebind", and whether the family fieldset was posted at all
    (a submission that never mentions it leaves a stored config alone;
    one that posts it blank clears it -- final-review B1)."""

    instance: ModelConnection | None
    name: str
    engine: str
    endpoint: str
    model_id: str
    capabilities: list[str]
    embed_dim: int | None
    context_window: int | None
    descriptor: str
    rank: int | None
    footprint_override_bytes: int | None
    family_declared: bool
    family: str
    variant: str
    text_encoder: str
    vae: str
    confirm: bool


def _refuse_unregistered_engine(request, engine: str) -> bool:
    """True (and a flash queued) when `engine` is not a registered
    adapter (C-27).

    Both creation paths -- `connection_add` and `machine_model_add` --
    have to enforce the same support boundary against the same
    de-anchored `ENGINES.values()` read, with the same words, and
    `machine_model_add`'s own comment used to say so while copying it.
    The CALLER still decides WHEN to ask: `connection_add` asks only when
    the engine actually changed, so an unrelated edit to a row carrying
    an engine that was deregistered since does not become unsaveable.
    """
    if any(candidate.name == engine for candidate in ENGINES.values()):
        return False
    messages.error(request, _UNKNOWN_ENGINE_MESSAGE.format(engine=engine))
    return True


def _validated_connection_form(request) -> _ConnectionForm | HttpResponse:
    """`connection_add`'s parse-enrich-validate half (that view's own
    docstring carries the whole argument for every rule below).

    Returns a `_ConnectionForm` when every field is good, or the console
    redirect -- with the operator's message already queued -- for any of
    the nine refusals. WRITES NOTHING. The one database read here is the
    name-uniqueness check, which is a validation whose answer happens to
    live in a table.

    NOT here: the embeddings-rebind confirm gate. That refusal is a
    precondition of a specific SIDE EFFECT -- it depends on the row's
    current role bindings and its pre-edit fingerprint, and it does not
    exist in create mode at all -- so it lives with the write it guards,
    in `_write_connection`."""
    name = request.POST.get("name", "").strip()
    engine_raw = request.POST.get("engine", "").strip()
    endpoint = request.POST.get("endpoint", "").strip()
    model_id = request.POST.get("model_id", "").strip()
    # T9: the manual form's Capability field is a checkbox GROUP (one
    # checkbox per platform capability, `_connection_edit.html` and both
    # `console.html` manual-form copies) -- `getlist` reads every checked
    # value. A single-value post (an older caller, or a test posting a
    # bare string) still round-trips unchanged: `getlist` on one value is
    # a one-item list, exactly how every other multi-value Django form
    # field degrades. Deduped, order-preserving (`dict.fromkeys`).
    capability_list = list(
        dict.fromkeys(c.strip() for c in request.POST.getlist("capability") if c.strip())
    )
    embed_dim_raw = request.POST.get("embed_dim", "").strip()
    context_window_raw = request.POST.get("context_window", "").strip()
    descriptor = request.POST.get("descriptor", "").strip()
    rank_raw = request.POST.get("rank", "").strip()
    footprint_gb_raw = request.POST.get("footprint_gb", "").strip()
    # A multi-file model family's three declared facts (ADR 0012 D-EDIT-2/3).
    # `family` is the operator's declaration -- nothing ComfyUI serves over
    # HTTP says which family a weights file belongs to, and this platform
    # never infers one from a filename. `text_encoder` and `vae` name the
    # companion files, picked from what the engine reports having installed.
    # All three blank is the normal case: a single-file checkpoint needs
    # none of them.
    #
    # `family_declared` (final-review B1) is the "was the group posted at
    # all" bit -- both the Add form and the Edit form now render these
    # three fields together (`inference/_family_fields.html`), so a real
    # submission from either always carries `family` even when its value is
    # blank (the operator left every `<select>` on "— none —"). Only a
    # caller that never mentions the group at all -- an older template, a
    # tampered POST -- gets `False` here, and update mode reads that as
    # "keep the stored config unchanged" instead of nulling it. Blank
    # WHEN DECLARED still means "clear" (`_family_config` below), which is
    # how an operator undoes a previous declaration.
    family_declared = "family" in request.POST
    family = request.POST.get("family", "").strip()
    # `variant` (ADR 0012 D-EDIT-6/8, T13): a fourth fact in the SAME group,
    # riding `family_declared`'s existing presence check rather than getting
    # its own -- both rendered forms always post `family` alongside it once
    # the fieldset exists, so there is no case where `variant` is posted
    # without `family` also being posted.
    variant = request.POST.get("variant", "").strip()
    text_encoder = request.POST.get("text_encoder", "").strip()
    vae = request.POST.get("vae", "").strip()
    connection_id = request.POST.get("connection_id", "").strip()
    confirm = request.POST.get("confirm", "").strip().lower() == "yes"

    instance = None
    if connection_id:
        try:
            instance = ModelConnection.objects.filter(pk=connection_id).first()
        except (ValueError, TypeError):
            # A malformed id (tampered/stale form field) is not a server
            # error -- it just means "no such connection".
            instance = None
        if instance is None:
            messages.error(request, "The connection being edited no longer exists.")
            return _redirect_console(request)

    # A blank/missing `engine` field means "keep the
    # stored value" in update mode, not "fall back to ollama" -- the edit
    # form's "(not registered)" option always posts the connection's exact
    # current value, but this is belt-and-suspenders for anything that
    # somehow strips the field. Create mode has no stored value to keep,
    # so it still defaults to "ollama".
    if engine_raw:
        engine = engine_raw
    elif instance is not None:
        engine = instance.engine
    else:
        engine = "ollama"

    catalog_entry = find(model_id) if model_id else None
    if catalog_entry is not None:
        if not capability_list:
            # The catalog only ever knows one capability per entry -- a
            # blank selection fills to that single value, same as the old
            # scalar fill; it never invents a second capability the
            # catalog never claimed.
            capability_list = [catalog_entry.capability]
        if not embed_dim_raw and catalog_entry.embed_dim is not None:
            embed_dim_raw = str(catalog_entry.embed_dim)

    if not name or not model_id or not endpoint:
        messages.error(request, "Name, endpoint, and model are required to register a connection.")
        return _redirect_console(request)

    # S19: the engine name is validated against registered adapters (below)
    # and the capability against CAPABILITIES; the endpoint alone used to be
    # stored verbatim after only the required-fields check above. Not a
    # file-read primitive -- `file://` raises `UnsupportedProtocol` out of
    # httpx and every adapter catches it -- but one consumer is not httpx
    # at all (the embedder client), whose URL handling this repo does not
    # control.
    #
    # THE SYNTAX HALF ONLY, never `_override_is_permitted`: an operator
    # deliberately registering a connection is a different question from a
    # query string steering a probe, and refusing a routable engine at
    # registration would break a documented workflow.
    endpoint_ok, _host = _endpoint_is_well_formed(endpoint)
    if not endpoint_ok:
        messages.error(request, "Endpoint must be a valid http:// or https:// URL.")
        return _redirect_console(request)

    # The support boundary is the engine
    # ADAPTER, and it must be enforced at exactly that level, not just
    # communicated in copy -- a garbage engine string (or a typo) would
    # otherwise be stored on the connection as-is and only ever fail later,
    # opaquely, the first time something tried
    # to actually resolve it (`models.contracts.engines.get_engine` raising
    # deep inside the gateway). Reads `ENGINES.values()` rather than
    # `engine in ENGINES` -- the same de-anchoring `_check_health` already
    # uses, and the only form that respects a test double whose
    # `.values()` is stubbed without also faking dict `__contains__`.
    #
    # This only gates a CHANGE to an unregistered engine,
    # never an unrelated save of a connection whose STORED engine already
    # names no registered adapter (legacy data, a renamed/deregistered
    # engine) -- create has no prior value, so it's always a "change" and
    # always validated; update is exempt only while the posted value is
    # exactly the connection's existing one.
    engine_changed = instance is None or engine != instance.engine
    if engine_changed and _refuse_unregistered_engine(request, engine):
        return _redirect_console(request)

    # Never-500 grammar matching the neighboring required-fields error
    # above: an empty selection (no checkbox checked, and nothing to fill
    # from the catalog) is refused with the SAME "must be one of" copy an
    # invalid value gets -- one message covers "chose nothing" and "chose
    # garbage" alike, since both leave the connection with no honest
    # capability to register.
    if not capability_list or any(c not in CAPABILITIES for c in capability_list):
        messages.error(request, f"Capability must be one of: {', '.join(sorted(CAPABILITIES))}.")
        return _redirect_console(request)

    embed_dim = None
    if embed_dim_raw:
        try:
            embed_dim = int(embed_dim_raw)
        except ValueError:
            messages.error(request, "Embedding dimension must be a whole number.")
            return _redirect_console(request)
        # A 0 or negative dimension must be rejected here rather than
        # stored and left to fail opaquely, later, the first time something
        # tried to actually use it (e.g. `PGVectorStore.from_params
        # (embed_dim=0)`) -- same never-500 shape as every other field
        # check here (context_window and rank below both reject
        # non-positive values the same way).
        if embed_dim <= 0:
            messages.error(request, "Embedding dimension must be a positive number.")
            return _redirect_console(request)

    # Context window (the oversized-context incident): an optional
    # per-connection cap on the KV cache `models.contracts.engines.ollama`
    # asks the engine to allocate. Blank means "unset" -- the adapter's own
    # bounded `DEFAULT_CONTEXT_WINDOW` applies (`db_provider`, ollama.py).
    # A non-numeric or non-positive value is a form error, never a 500 and
    # never silently coerced.
    context_window = None
    if context_window_raw:
        try:
            context_window = int(context_window_raw)
        except ValueError:
            messages.error(request, "Context window must be a whole number of tokens.")
            return _redirect_console(request)
        if context_window <= 0:
            messages.error(request, "Context window must be a positive number of tokens.")
            return _redirect_console(request)

    # Descriptor and rank (this task, "self assign the qualifiers"): the
    # operator's own recognition aids, validated but never enriched,
    # coerced, or guessed. Blank of either is valid (unset).
    if len(descriptor) > 80:
        messages.error(request, "Descriptor must be 80 characters or fewer.")
        return _redirect_console(request)

    rank = None
    if rank_raw:
        try:
            rank = int(rank_raw)
        except ValueError:
            messages.error(request, "Rank must be a positive number.")
            return _redirect_console(request)
        if rank <= 0:
            messages.error(request, "Rank must be a positive number.")
            return _redirect_console(request)

    # Memory footprint override (T2b): entered in GB (decimals allowed --
    # unlike rank/context_window, a footprint is naturally fractional),
    # stored in bytes. Blank means "no override" -- `effective_footprint_
    # bytes` falls through to whatever the queue worker last measured, if
    # anything.
    footprint_override_bytes = None
    if footprint_gb_raw:
        try:
            footprint_gb = float(footprint_gb_raw)
        except ValueError:
            messages.error(request, "Memory footprint override must be a number of GB.")
            return _redirect_console(request)
        # `float()` happily parses "inf"/"-inf"/"nan" without raising (T4
        # review MAJOR, the same reproduced class as `tools.rag.views.
        # _upload_cap_update`'s identical `footprint_gb`-shaped
        # field: `round(inf * 1024**3)` is an uncaught OverflowError, and
        # `nan <= 0` is False, so nan would otherwise sail past the
        # positivity check below too). Neither is a usable GB figure, so
        # both are rejected with the SAME "not a number" copy an
        # unparseable string gets.
        if not math.isfinite(footprint_gb):
            messages.error(request, "Memory footprint override must be a number of GB.")
            return _redirect_console(request)
        if footprint_gb <= 0:
            messages.error(request, "Memory footprint override must be greater than zero.")
            return _redirect_console(request)
        # `foundation.format.gb_to_bytes` -- the SAME 1024 base `_human_size`
        # renders with, so a value the operator typed (e.g. 8.5) reads back
        # identically.
        #
        # M2 (Coherence Wave B review): the SAME two-part ceiling guard
        # `models.queue.views._update_budget_and_concurrency` now uses for
        # its own GB field -- `footprint_gb` itself passed `math.isfinite`
        # above, but `footprint_gb * 1024**3` can still overflow to `inf`
        # internally (e.g. `1e308`), and a merely-large-but-finite value
        # (e.g. `1e10`) can produce a byte count past `BigIntegerField`'s
        # real ceiling without ever overflowing `round()` at all -- this
        # field was outside S1's Dimension-4 enumeration and is exactly
        # the "fix could not propagate" gap S1's own argument named.
        try:
            footprint_override_bytes = gb_to_bytes(footprint_gb)
        except OverflowError:
            messages.error(request, "Memory footprint override is too large.")
            return _redirect_console(request)
        if exceeds_field_ceiling(footprint_override_bytes, max_stored=BIGINT_FIELD_MAX):
            messages.error(request, "Memory footprint override is too large.")
            return _redirect_console(request)

    name_taken = ModelConnection.objects.filter(name__iexact=name)
    if instance is not None:
        name_taken = name_taken.exclude(pk=instance.pk)
    if name_taken.exists():
        messages.error(request, _NAME_TAKEN_MESSAGE.format(name=name))
        return _redirect_console(request)
    return _ConnectionForm(
        instance=instance,
        name=name,
        engine=engine,
        endpoint=endpoint,
        model_id=model_id,
        capabilities=capability_list,
        embed_dim=embed_dim,
        context_window=context_window,
        descriptor=descriptor,
        rank=rank,
        footprint_override_bytes=footprint_override_bytes,
        family_declared=family_declared,
        family=family,
        variant=variant,
        text_encoder=text_encoder,
        vae=vae,
        confirm=confirm,
    )


def _write_connection(request, form: _ConnectionForm) -> HttpResponse:
    """`connection_add`'s write half: create the row, or update it and
    deal with what that invalidates.

    THE CONFIRM GATE LIVES HERE, not in validation, and it is the reason
    this function can still refuse. A fingerprint-altering edit
    (engine/model/dim/endpoint, compared through `norm_tag`/
    `norm_endpoint`) to a connection bound to an embeddings role
    invalidates that role's materialized data exactly like rebinding the
    role would -- so it goes through the same gate, with the same
    severity copy, and the same first-materialization stamp. That is a
    fact about the row being written, not about the form.

    Every operator sentence here uses typographic quotation marks
    (U+201C/U+201D). They are copy, not syntax; the tests assert on
    substrings that stop before them."""
    instance = form.instance
    if instance is None:
        with transaction.atomic():
            created = ModelConnection.objects.create(
                name=form.name,
                engine=form.engine,
                endpoint=form.endpoint,
                model_id=form.model_id,
                capabilities=form.capabilities,
                embed_dim=form.embed_dim,
                context_window=form.context_window,
                descriptor=form.descriptor,
                rank=form.rank,
                footprint_override_bytes=form.footprint_override_bytes,
                config=_family_config(form.family, form.variant, form.text_encoder, form.vae),
            )
            # S3 (Coherence Wave B): `ModelConnection` create/edit/delete
            # was one of the four unaudited settings surfaces the backend
            # audit named with no recorded rationale -- the recorded
            # ruling is "audit all four". `footprint_override_bytes`
            # travels in `detail` since it is the field the audit calls
            # out by name.
            audit.record(
                principal_for_request(request), actions.CONNECTION_CREATED,
                target_type="connection", target_key=created.pk, target_label=form.name,
                footprint_override_bytes=form.footprint_override_bytes,
            )
        messages.info(request, f"Registered connection “{form.name}”.")
        return _redirect_console(request)

    # Update mode. A fingerprint-altering edit (engine/model/dim/endpoint;
    # norm_tag / norm_endpoint so "an-embedding-model" -> ":latest" or a
    # trailing slash don't count as changes) to a connection bound to an
    # embeddings role invalidates that role's materialized data exactly
    # like rebinding the role would -- so it goes through the same confirm
    # gate with the same severity copy, and the same
    # first-materialization stamp: ingestion never stamps, so a fresh
    # install has no `Materialization` row, and without one `is_drifted()`
    # reports False -- a confirmed dim-changing edit would silently skip
    # the banner the identical dropdown rebind shows. The banner itself
    # still comes from the existing machinery (resolve().fingerprint vs
    # the stamp); an existing stamp is never overwritten.
    fingerprint_changed = (
        form.engine != instance.engine
        or norm_tag(form.model_id) != norm_tag(instance.model_id)
        or form.embed_dim != instance.embed_dim
        or norm_endpoint(form.endpoint) != norm_endpoint(instance.endpoint)
    )
    bound_embeddings_roles = []
    if fingerprint_changed:
        bound_embeddings_roles = [
            role
            for binding in instance.role_bindings.all()
            if (role := get_role(binding.role_key)) is not None and role.capability == "embeddings"
        ]
        if bound_embeddings_roles and not form.confirm:
            dim_changed = form.embed_dim != instance.embed_dim
            messages.warning(
                request,
                f"Editing “{instance.name}”: {_severity_copy(dim_changed)} "
                "Check “confirm rebind” and submit again to proceed.",
            )
            return _redirect_console(request)

    # The PRE-edit fingerprint -- what any already-materialized data was
    # actually encoded under -- captured before the mutation below.
    pre_fingerprint = resolved_from_connection(instance).fingerprint

    instance.name = form.name
    instance.engine = form.engine
    instance.endpoint = form.endpoint
    instance.model_id = form.model_id
    instance.capabilities = form.capabilities
    instance.embed_dim = form.embed_dim
    instance.context_window = form.context_window
    instance.descriptor = form.descriptor
    instance.rank = form.rank
    instance.footprint_override_bytes = form.footprint_override_bytes
    # Update mode ONLY (create mode has no stored config to preserve, and
    # `family_declared` is always effectively True there since there is
    # nothing to keep) -- B1: a submission that never mentions the family
    # group leaves `instance.config` exactly as it was; one that does,
    # blank or not, replaces it (`_family_config` turns "all three blank"
    # into `None`, the clear case).
    if form.family_declared:
        instance.config = _family_config(form.family, form.variant, form.text_encoder, form.vae)
    with transaction.atomic():
        instance.save()
        # S3 (Coherence Wave B): same reasoning as the create branch
        # above.
        audit.record(
            principal_for_request(request), actions.CONNECTION_UPDATED,
            target_type="connection", target_key=instance.pk, target_label=form.name,
            footprint_override_bytes=form.footprint_override_bytes,
        )

    _stamp_first_materialization(bound_embeddings_roles, pre_fingerprint)

    messages.info(request, f"Updated connection “{form.name}”.")
    return _redirect_console(request)


@require_POST
def connection_add(request):
    """POST /inference/connections/add/ -- register a `ModelConnection` by
    hand (the advanced/custom-endpoint path, e.g. a model on another
    machine), or -- with a `connection_id` field -- update an
    existing one in place, from the inline edit form on that connection's
    details disclosure. This is one of exactly two ways to create a
    connection; the other is the role rows' "change" dropdown
    (`role_assign`), and this view is the ONLY way to edit one.

    Update mode shares every validation with create; the CI-unique name
    check simply excludes the connection being edited so keeping (or
    re-casing) its own name is not a collision. If the edited connection is
    bound to an embeddings role and the change alters the fingerprint
    (engine / model / dim / endpoint), the same `confirm` gate as
    rebinding applies -- an unconfirmed submit changes nothing -- and the
    EXISTING drift machinery surfaces the re-encode banner afterwards; no
    new drift logic here. Env-fallback "rows" have no connection to edit
    (they are settings-file values; the disclosure says so).

    Catalog enrichment: if the typed `model_id` matches a
    `models.contracts.catalog.find()` entry, a blank `capability` or
    `embed_dim` is filled in from that entry before validation runs --
    saving the operator from picking a capability or looking up a
    dimension the catalog already knows. `name` is deliberately NOT
    enriched -- it is the operator's own chosen label (case-insensitively
    unique per `ModelConnection`), not spec data derived from the model
    itself, and a blank name must keep failing validation the same way it
    always has. Enrichment never rejects: a `model_id` with no catalog
    match (or one where the operator already filled the fields in) is
    completely unaffected and validates exactly as before -- the catalog
    is a suggestion list, never an allowlist of models the operator may
    run.

    Engine validation: the
    posted engine must name a registered adapter (`models.contracts.engines.
    ENGINES`) -- EXCEPT that updating a connection whose stored engine
    already names no registered adapter (legacy data, a renamed/
    deregistered engine) is allowed as long as the posted value equals that
    stored one unchanged, so an unrelated edit (e.g. a rename) can never
    silently reassign it. A blank/missing `engine` field in update mode
    keeps the stored value rather than defaulting to "ollama".

    Context window ("nothing hidden"): an optional
    positive-integer `context_window` field, blank by default (create mode
    always starts unset -- `machine_model_add` never guesses one either).
    It threads to `models.contracts.bindings.ResolvedModel.config` via
    `models.registry.bindings.db_provider` and from there to
    `models.contracts.engines.ollama.build_llm`'s `Ollama(context_window=...)`
    -- capping the KV cache the engine allocates for this connection
    instead of the adapter's own bounded default. A non-numeric or
    non-positive value is a form-level error (never a 500); it is not part
    of the embeddings fingerprint (engine/model/dim/endpoint), so changing
    it alone never triggers the re-encode confirm gate.

    Descriptor and rank let an operator self-assign the qualifiers of their
    models: `descriptor` is the
    operator's own free-text line (never auto-filled, suggested, or
    derived -- there is no catalog enrichment for it, unlike `capability`/
    `embed_dim` above); `rank` is a plain positive integer they assign by
    hand. Both are blank-valid (unset); a too-long descriptor or a
    non-numeric/non-positive rank is a form-level error, same never-500
    shape as every other field here. Neither participates in the
    embeddings fingerprint, so changing either alone never trips the
    re-encode confirm gate. `machine_model_add` never sets either --
    self-assigning them is an operator's deliberate, later act.

    Memory footprint override (T2b): an optional `footprint_gb` field, in
    GB (decimals allowed -- unlike `context_window`/`rank`, a footprint is
    naturally fractional). Blank means "no override" (`models.registry.
    bindings.footprint_for` falls through to whatever the queue worker last
    measured, if anything); a non-numeric or non-positive value is a
    form-level error, same never-500 shape as every other field here.
    Stored as `round(gb * 1024**3)` on `ModelConnection.
    footprint_override_bytes` -- the SAME 1024 base `_human_size` renders
    with, so a value the operator typed (e.g. 8.5) reads back identically.
    Never part of the embeddings fingerprint (like `context_window`/
    `descriptor`/`rank`), so changing it alone never trips the re-encode
    confirm gate.

    TWO HALVES SINCE C-48b: `_validated_connection_form` parses,
    catalog-enriches and validates (nine refusals, no write), and
    `_write_connection` creates or updates (and owns the embeddings
    confirm gate, which is a precondition of the write rather than of the
    form). This function is the seam. Every rule either half enforces is
    documented above; neither half re-argues it.
    """
    form = _validated_connection_form(request)
    if not isinstance(form, _ConnectionForm):
        return form
    return _write_connection(request, form)


@require_POST
def connection_remove(request):
    """POST /inference/connections/remove/ -- delete a registered
    `ModelConnection`: this is the found -> registered ->
    in-use pipeline's exit from "registered" -- without it, once
    registered, a connection could never be un-registered again. Mirrors
    `connection_add`'s lookup/validation/redirect/never-500 shape (the same
    `(ValueError, TypeError)`-guarded `filter(pk=...).first()`) and
    `role_assign`'s unbind confirm-gate/stamp mechanics.

    `RoleBinding.connection` is `on_delete=models.SET_NULL` (models.py) --
    that IS the honest DB behavior for "remove a connection roles use", so
    this view never blocks the delete or fights the cascade; it surfaces
    the consequence instead of hiding it:

    - bound to NO role: removes immediately, the success message names the
      connection.
    - bound to any CHAT role(s): removes immediately -- a chat rebind
      carries no drift risk -- but the template renders a visible note next
      to the button (`_removal_note_short`/`_removal_note_title`, rendered
      by `_registered_connection.html`) so the consequence is never a
      silent surprise, and the success message repeats it.

    Both the pre-removal note and the success message are per-role truthful:
    clearing a binding leaves a role with NO model
    only when that role has no explicit environment override -- with one,
    the override takes over immediately and the role keeps working. A
    connection bound to several roles can therefore have mixed outcomes,
    and the copy names each rather than averaging them. The message reuses
    `_unassign_consequence`, the same helper `role_assign`'s unassign path
    uses, so the two actions cannot describe one outcome two ways.
    - bound to an EMBEDDINGS role: an embeddings-model change is
      drift-relevant (README's re-encode guard) exactly like a rebind or an
      unbind, so an unconfirmed submit changes NOTHING -- the same
      conservative severity copy (`_severity_copy(True)`, the unbind path's
      own choice: unbinding/removing swaps the active model out from under
      the role with no "is the dimension actually changing" comparison
      available, so it always warns at the higher severity) as
      `role_assign`'s gated unbind. A confirmed removal then applies the
      SAME first-materialization stamp semantics: when no `Materialization`
      row exists yet for a bound embeddings role, the PRE-removal
      fingerprint (this connection's own facts -- it IS the currently
      active binding) is stamped so the drift banner can fire immediately
      afterward, exactly like the first confirmed unbind/rebind. An
      existing stamp is never overwritten.

    Garbage/missing `connection_id` (a tampered form, a stale page, a
    double-click after another tab already removed it): a friendly message
    and a redirect, never a 500.
    """
    connection_id = request.POST.get("connection_id", "").strip()
    confirm = request.POST.get("confirm", "").strip().lower() == "yes"

    try:
        connection = ModelConnection.objects.filter(pk=connection_id).first()
    except (ValueError, TypeError):
        # A malformed id (tampered/stale form field) is not a server error
        # -- it just means "no such connection".
        connection = None
    if connection is None:
        messages.error(request, "The connection being removed no longer exists.")
        return _redirect_console(request)

    bound_roles = [
        role
        for binding in connection.role_bindings.all()
        if (role := get_role(binding.role_key)) is not None
    ]
    bound_embeddings_roles = [role for role in bound_roles if role.capability == "embeddings"]

    if bound_embeddings_roles and not confirm:
        messages.warning(
            request,
            f"Removing “{connection.name}” (in use by "
            f"{', '.join(role.label for role in bound_roles)}): {_severity_copy(True)} "
            "Check “confirm removal” and submit again to proceed.",
        )
        return _redirect_console(request)

    # First-removal drift stamp (same rationale as role_assign's first-rebind
    # stamp and connection_add's first-edit stamp): ingestion never
    # stamps, so a role that has never drifted before has no Materialization
    # row, and without one `is_drifted()` reports False -- a confirmed
    # removal would otherwise never show the banner even though existing
    # data was encoded under the connection being removed. The PRE-removal
    # fingerprint is this connection's own facts: it IS the currently active
    # binding for every role in `bound_embeddings_roles`.
    if bound_embeddings_roles:
        pre_fingerprint = resolved_from_connection(connection).fingerprint
        _stamp_first_materialization(bound_embeddings_roles, pre_fingerprint)

    name = connection.name
    connection_pk = connection.pk
    with transaction.atomic():
        connection.delete()  # SET_NULL clears every RoleBinding above.
        # S3 (Coherence Wave B): same reasoning as `connection_add`'s
        # create/update branches. `target_key` is captured BEFORE
        # `.delete()` clears the instance's `pk`.
        audit.record(
            principal_for_request(request), actions.CONNECTION_DELETED,
            target_type="connection", target_key=connection_pk, target_label=name,
        )

    if bound_roles:
        # Per-role, never averaged. Each role's
        # binding IS now unassigned (that is the DB fact), but what backs it
        # afterwards differs -- `_unassign_consequence` is the same helper
        # `role_assign`'s unassign path uses, so the two actions describe an
        # identical outcome identically.
        clauses = [
            f"{role.label} is now unassigned: {_unassign_consequence(role)}"
            for role in bound_roles
        ]
        messages.info(request, f"Removed connection “{name}” — {'; '.join(clauses)}.")
    else:
        messages.info(request, f"Removed connection “{name}”.")
    return _redirect_console(request)


def _family_config(family: str, variant: str, text_encoder: str, vae: str) -> dict | None:
    """The connection `config` for a declared model family, or `None`.

    Blank means UNSET, and an unset key is absent rather than present-and-
    empty: a graph template asks `config.get("text_encoder")` and an empty
    string would be a filename it would then hand a loader. All four blank
    -- the single-file checkpoint case, which is every connection that
    existed before families (or variants) did -- is `None`, so no existing
    row grows keys of noise. `variant` (ADR 0012 D-EDIT-6/8, T13) rides the
    exact same rule the other three already follow: blank clears, and a
    value only ever lands here when the family group was posted at all.
    """
    declared = {"family": family, "variant": variant, "text_encoder": text_encoder, "vae": vae}
    kept = {key: value for key, value in declared.items() if value}
    return kept or None


def _unique_connection_name(model_id: str, endpoint: str) -> str | None:
    """A default operator-facing name for a connection registered from a
    machine row's "Add to registered" button (`machine_model_add`), kept
    unique against `ModelConnection.name`'s CI-unique constraint without
    asking the operator to type one.

    Never-500: a bare two-candidate version
    (`model_id`, then `model_id (endpoint)`) would raise IntegrityError -- a
    500 -- when BOTH were already taken, which an operator can reach
    without tampering just by hand-naming a connection in this same
    pattern. The candidate list therefore extends with a bounded
    numeric-suffix run ("model_id (2)" ... "(20)"); if even those are
    somehow all taken,
    returns None and the caller degrades to a graceful message instead of
    creating anything (same shape as `connection_add`'s name_taken path).
    """
    candidates = [model_id, f"{model_id} ({endpoint})"]
    candidates.extend(f"{model_id} ({n})" for n in range(2, 21))
    for candidate in candidates:
        if not ModelConnection.objects.filter(name__iexact=candidate).exists():
            return candidate
    return None


# The loader whose models need nothing else named alongside them. Compared
# against `DiscoveryRow.loader` (the engine's own node name) so the console
# reads the ENGINE's fact rather than guessing from a filename; any other
# loader means "this is half a pipeline". Not an engine-name literal: it is
# the one loader node a self-contained model is reported by, and the
# console's only job with it is inequality.
_MODEL_LOADER_SELF_CONTAINED = "CheckpointLoaderSimple"


@require_POST
def machine_model_add(request):
    """POST /inference/machine/add/ -- a machine row's "Add to registered"
    button: registration as an INTENTIONAL,
    one-click act. Creates the `ModelConnection` auto-filled entirely from
    detection -- the row's engine, the engine's exact reported model_id,
    the page's scanned endpoint (the form's hidden `endpoint` field, so an
    active `?endpoint=` override registers against the machine actually
    being viewed), the detected capability, and the detected embed_dim.
    Binds NOTHING: "in use" stays the role rows' own separate step.

    Idempotent by the same identity every other comparison on this page
    uses (norm_tag + normalized endpoint): if a matching connection already
    exists for this engine -- e.g. the seed migration's bare-model-id row,
    a hand-registered one, or a double submit from a stale page -- it is
    reported as already registered and NOTHING is created or modified
    (never a duplicate, and never a silent rewrite of operator-stored
    values).

    Never guesses (the standing never-guess rulings): the template only
    renders this button for rows with an engine-DETECTED capability, and
    the view enforces the same boundary -- a missing/garbage capability is
    refused with a pointer to the manual form (which the degraded rows
    link to, prefilled, instead of this button). An embeddings row whose
    dimension neither the engine report nor a catalog match supplies is
    likewise refused to the manual form -- never `settings.EMBED_DIM`,
    never a guessed registration.

    T9 (multi-capability registration): the row's FULL detected capability
    set rides along as one hidden `capability` input PER capability
    (`_installed_row.html`) -- `request.POST.getlist("capability")` reads
    all of them (a single-value post, from any older/other caller, still
    round-trips: `getlist` on one value is a one-item list, the same
    back-compat every other multi-value form field in Django gets for
    free). A vision-capable chat row detected as `["chat", "vision"]`
    therefore registers a connection carrying BOTH -- it then appears in
    both the chat picker and the vision (rag.extract) dropdown, the truth
    about the model; which role actually binds it is the operator's later,
    separate act. Every value is validated against `CAPABILITIES` the same
    way the old single value was; an empty selection is refused exactly
    like an unrecognized one (never a capability-less connection, never a
    500).
    """
    engine = request.POST.get("engine", "").strip()
    model_id = request.POST.get("model_id", "").strip()
    endpoint = request.POST.get("endpoint", "").strip()
    # Dedup while preserving report order (`dict.fromkeys`) -- the hidden
    # inputs are one per detected capability, never duplicated in practice,
    # but a stale/tampered form is never worth a duplicate list entry over.
    capability_list = list(
        dict.fromkeys(c.strip() for c in request.POST.getlist("capability") if c.strip())
    )
    embed_dim_raw = request.POST.get("embed_dim", "").strip()

    if not engine or not model_id or not endpoint:
        messages.error(request, "Missing model details to register.")
        return _redirect_console(request)

    # S19: this endpoint is template-filled from the page's own scanned
    # address (never operator-typed here), but it is still a second write
    # path into `ModelConnection.endpoint` and must not trust the POST any
    # more than `connection_add` does -- same syntax-only check, same
    # reasoning (see that view's comment beside its own call). A distinct
    # message from the blank-fields refusal above (review round 1) -- this
    # one names the actual problem (a tampered/stale hidden field) rather
    # than repeating "missing", which a re-scan would not explain.
    endpoint_ok, _host = _endpoint_is_well_formed(endpoint)
    if not endpoint_ok:
        messages.error(
            request,
            f"“{model_id}” was found at an endpoint that is no longer a valid "
            "http:// or https:// URL -- re-scan and try again.",
        )
        return _redirect_console(request)

    # Validation parity with `connection_add` -- the engine
    # field is hidden/template-filled, but this is still a second creation
    # path and must enforce the same support boundary (the registered
    # adapters), not trust the POST. `_refuse_unregistered_engine` is the
    # one place that owns the de-anchored `ENGINES.values()` read and the
    # operator-facing message, shared with `connection_add` (C-27).
    if _refuse_unregistered_engine(request, engine):
        return _redirect_console(request)

    if not capability_list or any(c not in CAPABILITIES for c in capability_list):
        messages.error(
            request,
            f"“{model_id}” has no detected capability -- use the manual form "
            "below to register it with an explicit capability.",
        )
        return _redirect_console(request)

    embed_dim = None
    if embed_dim_raw:
        try:
            embed_dim = int(embed_dim_raw)
        except ValueError:
            embed_dim = None

    if "embeddings" in capability_list and embed_dim is None:
        catalog_entry = find(model_id)
        if catalog_entry is not None:
            embed_dim = catalog_entry.embed_dim

    if "embeddings" in capability_list and embed_dim is None:
        messages.error(
            request,
            f"“{model_id}” has no known embedding dimension -- use the "
            "manual form below to register it with an explicit dimension.",
        )
        return _redirect_console(request)

    # Detection-only registration cannot supply a model family or its
    # companion files, and this console never guesses (the standing
    # never-guess rulings). A model reported by a DIFFUSION-MODEL loader is
    # half a pipeline; the manual form -- where the operator declares all
    # three -- is the way in. Which loader reported it is the row's own
    # `DiscoveryRow.loader`, posted by the template.
    loader = request.POST.get("loader", "").strip()
    if loader and loader != _MODEL_LOADER_SELF_CONTAINED:
        messages.error(
            request,
            f"“{model_id}” is a diffusion model, not a self-contained "
            "checkpoint -- it needs a model family, a text encoder, and a "
            "VAE named alongside it. Use the manual form below to register "
            "it with all three.",
        )
        return _redirect_console(request)

    # The reuse check: `model_id` here is the engine's exact/tagged string
    # (from the machine row), but a matching connection already in the DB
    # (the seed migration, or a hand-typed one) may hold the bare, untagged
    # form -- `norm_tag()` on both sides recognizes those as the same
    # model. Endpoint is matched too (normalized, trailing-slash-
    # insensitive): a connection for the same model_id at a DIFFERENT,
    # unrelated endpoint is a different registration and never blocks a
    # local one.
    target_key = norm_tag(model_id)
    target_endpoint = norm_endpoint(endpoint)
    existing = next(
        (
            c
            for c in ModelConnection.objects.filter(engine=engine)
            if norm_tag(c.model_id) == target_key and norm_endpoint(c.endpoint) == target_endpoint
        ),
        None,
    )
    if existing is not None:
        messages.info(request, f"Already registered as “{existing.name}”.")
        return _redirect_console(request)

    name = _unique_connection_name(model_id, endpoint)
    if name is None:
        # Every default-name candidate is taken (an operator has
        # hand-named connections in this exact pattern, 20+ deep) -- degrade
        # to the manual form, where they pick the name, instead of raising.
        messages.error(
            request,
            f"No free default name for “{model_id}” -- use the manual form "
            "below to register it with a name of your choosing.",
        )
        return _redirect_console(request)

    try:
        with transaction.atomic():
            connection = ModelConnection.objects.create(
                name=name,
                engine=engine,
                endpoint=endpoint,
                model_id=model_id,
                capabilities=capability_list,
                embed_dim=embed_dim,
            )
            # I3 (Coherence Wave B review): this is a SECOND
            # `ModelConnection` creation path, same as `connection_add`'s
            # create branch, so it carries the same `CONNECTION_CREATED`
            # audit event -- the S3 ruling covers every `ModelConnection`
            # write, not only the manual-form one. `detail` is
            # deliberately empty: unlike `connection_add`, this path sets
            # no `footprint_override_bytes` (detection never supplies
            # one), so there is nothing else to name here.
            audit.record(
                principal_for_request(request), actions.CONNECTION_CREATED,
                target_type="connection", target_key=connection.pk, target_label=name,
            )
    except IntegrityError:
        # Belt-and-suspenders for the filter-then-create race: a
        # collision the candidate check missed lands as the same graceful
        # message `connection_add`'s name_taken path uses -- never a 500.
        # The `transaction.atomic()` above rolls back the create (and
        # therefore the audit write too) on this path, so nothing is
        # recorded for a registration that did not actually happen.
        messages.error(request, _NAME_TAKEN_MESSAGE.format(name=name))
        return _redirect_console(request)
    messages.info(
        request,
        f"Registered “{connection.name}” -- it's now available in the role dropdowns above.",
    )
    return _redirect_console(request)


def _family_bind_note(role: RoleSpec, connection: ModelConnection) -> str | None:
    """An honest, NON-BLOCKING note for a bind that just succeeded but
    cannot actually do anything yet -- an owner-incident follow-up: an
    operator bound a diffusion-model connection and only discovered it
    couldn't run anything the first time a job failed.

    Three cases, all read from the engine's own facts, never guessed:

    - the connection HAS a declared family (`config["family"]`), but this
      adapter has no graph template for any operation under it yet
      (`supported_operations` -- the same registry `connection_add`'s D-
      EDIT-2 note already defers to -- answers `()`);
    - the connection HAS a declared family AND a template exists (checked
      after the no-template case above, since companions are moot for a
      family this adapter cannot run at all), but `text_encoder`/`vae`
      were left blank (finding 7) -- a template existing says nothing
      about whether THIS connection supplied the companions it needs;
      `models.contracts.engines.comfyui_workflows._fragments._declared`
      still refuses the job at run time otherwise (ADR 0012 D-EDIT-3), and
      that must not be the operator's first sign anything was wrong;
    - the connection has NO declared family, and its model is reported by a
      loader other than the self-contained one (`_MODEL_LOADER_SELF_
      CONTAINED`) -- a diffusion-model file quietly running through the
      checkpoint templates meant for a single-file model, which is exactly
      the silent-failure shape the incident was.

    Returns `None` -- nothing to say -- whenever: the connection's engine
    isn't registered; that engine has no `supported_operations` member at
    all (e.g. ollama, where this whole family concept doesn't exist);
    `supported_operations` already reports something runnable; or a
    family-less connection's model can't be found installed right now (an
    unreachable engine, or a model this adapter reports no loader for --
    same "nothing to detect against" shape `_connection_source_label` already
    has). Every engine call is wrapped: a note is never worth blocking a
    bind that has already been saved, nor a 500.
    """
    engine = next((candidate for candidate in ENGINES.values() if candidate.name == connection.engine), None)
    if engine is None:
        return None
    supported = getattr(engine, "supported_operations", None)
    if supported is None:
        return None

    family = ""
    if connection.config:
        family = connection.config.get("family", "") or ""

    try:
        operations = supported(connection.model_id, connection.endpoint, family)
    except Exception:  # noqa: BLE001 -- a note is never worth blocking a bind
        return None

    if family:
        if not operations:
            return (
                f"“{connection.name}” is now bound to {role.label}, but its declared "
                f"family “{family}” has no operations available here yet."
            )
        # A graph TEMPLATE existing for `(family, operation)` -- `operations`
        # non-empty, just proven above -- does not mean this connection can
        # actually run it (finding 7): `models.contracts.engines.
        # comfyui_workflows._fragments._declared` still refuses the JOB at
        # run time if `text_encoder`/`vae` were left blank at registration
        # (ADR 0012 D-EDIT-3), and that failure must not be the operator's
        # first sign anything was wrong. Checked only once a template
        # exists -- companions are moot for a family this adapter cannot
        # run at all, and the no-template note above is the more
        # fundamental of the two.
        config = connection.config or {}
        missing = [
            what
            for key, what in (("text_encoder", "a text encoder"), ("vae", "a VAE"))
            if not config.get(key)
        ]
        if missing:
            return (
                f"“{connection.name}” is now bound to {role.label}, but its family "
                f"“{family}” declaration is missing {' and '.join(missing)} -- edit it "
                "in Registered connections below."
            )
        return None
    if operations:
        return None

    # No family declared: a genuine single-file checkpoint needs none of
    # this, so only worth a note when the model is actually reported by a
    # non-self-contained (diffusion-model) loader -- the same fact
    # `machine_model_add`'s own refusal reads, looked up live here since a
    # `ModelConnection` carries no loader of its own.
    list_installed = getattr(engine, "list_installed", None)
    if list_installed is None:
        return None
    try:
        installed = list_installed(connection.endpoint)
    except Exception:  # noqa: BLE001 -- an unreachable engine has nothing to add
        return None
    loader = next(
        (
            getattr(model, "loader", "")
            for model in installed
            if norm_tag(getattr(model, "model_id", "")) == norm_tag(connection.model_id)
        ),
        "",
    )
    if loader and loader != _MODEL_LOADER_SELF_CONTAINED:
        return (
            f"“{connection.name}” is now bound to {role.label}, but it needs a model "
            "family declared before any operation is available here -- edit it in "
            "Registered connections below."
        )
    return None


@require_POST
def role_assign(request):
    """POST /inference/roles/assign/ -- the ONE assignment action: a role
    row's "change" dropdown posts here. The grammar is narrowed
    to REGISTERED connections only -- this view binds and
    unbinds, it never creates a `ModelConnection` (registration is
    `machine_model_add`'s / `connection_add`'s intentional act). The
    selected option's `choice` value:

    - `conn:<pk>` -- a registered `ModelConnection` (local or remote). All
      facts live in the DB row; the view just binds it.
    - `unbind` -- UNASSIGN: clear the binding's connection (without this,
      binding would be a one-way door). The role falls
      through to `env_provider` exactly as if the connection had been
      deleted (SET_NULL semantics), which with no explicit environment
      override means it resolves to NOTHING: the role is unassigned and
      says so. The gate/confirmation copy states whichever of those two
      outcomes is actually true (`_unassign_consequence`). Rendered only
      while a connection is bound.

    A stale `model:` choice (a form rendered before this restructure, or a
    tampered value) lands in the unrecognized-scheme branch: an error
    message and no change -- never a silently created connection.

    Embeddings confirmation gate: rebinding an
    "embeddings"-capability role to a *different* connection without a
    `confirm=yes` field changes nothing -- it redirects back with a
    `messages.warning` explaining the consequence (full store rebuild if
    the embed_dim is changing, re-encode in place if it isn't) and asks the
    operator to check "confirm rebind" and submit again. Declining it must
    leave no trace.

    First-rebind drift stamp: `is_drifted()` deliberately treats a role
    with no `Materialization` row as "never materialized -- not drifted"
    (see `models.registry.drift`), so the very first embeddings rebind
    would otherwise never show the drift banner even though existing data
    was encoded under the old binding. When a confirmed embeddings rebind
    is applied and no `Materialization` row exists yet, the PRE-rebind
    active fingerprint (derived from the outgoing connection where one
    exists, else `resolve()`) is stamped so the banner appears immediately.
    Confirming a rebind never runs the re-encode itself -- that's the
    banner's re-encode button (or `manage.py reencode`).

    Honest bind-time note (owner incident): a successful `conn:` bind that
    leaves the role with a connection that cannot actually run anything
    yet -- a declared family this adapter has no template for, or an
    undeclared family on a diffusion-model connection -- gets a
    `messages.warning` alongside the normal "Assigned" success message
    (`_family_bind_note`). It is purely informational: the bind itself
    already happened and is never blocked or rolled back by it.
    """
    role_key = request.POST.get("role_key", "").strip()
    choice = request.POST.get("choice", "").strip()
    confirm = request.POST.get("confirm", "").strip().lower() == "yes"

    role = get_role(role_key)
    if role is None:
        messages.error(request, f"Unknown role “{role_key}”.")
        return _redirect_console(request)

    connection = None
    is_unbind = choice == "unbind"

    if is_unbind:
        pass  # the pick IS "no connection" -- nothing to look up
    elif choice.startswith("conn:"):
        try:
            connection = ModelConnection.objects.filter(pk=choice[len("conn:"):]).first()
        except (ValueError, TypeError):
            # A malformed pk (tampered/stale option) is not a server error
            # -- it just means "no such connection".
            connection = None
        if connection is None:
            messages.error(request, "Selected connection no longer exists.")
            return _redirect_console(request)
    elif not choice:
        # The placeholder option ("choose a model…") with nothing picked.
        # The select is not `required` (a raw
        # browser validation popup would be worse), so this is a routine, silent no-op
        # -- not an operator error worth a message -- rather than the
        # scheme-mismatch path below.
        return _redirect_console(request)
    else:
        # A non-empty but unrecognized scheme (tampered/stale option value).
        messages.error(request, "Choose a model to assign.")
        return _redirect_console(request)

    binding = RoleBinding.objects.filter(role_key__iexact=role.key).first()
    if binding is None:
        binding = RoleBinding(role_key=role.key)
    current_connection = binding.connection

    if is_unbind:
        changed = current_connection is not None
    else:
        changed = current_connection is None or current_connection.pk != connection.pk

    # The confirm gate: declining it must leave no trace. It compares the
    # picked connection's own stored facts against the outgoing binding's
    # -- nothing is created or mutated on this path (nothing on
    # this VIEW creates or mutates connections at all).
    if role.capability == "embeddings" and changed and not confirm:
        if is_unbind:
            # Unassigning takes the active model away -- same drift risk as
            # any rebind; warn conservatively, and state the TRUE
            # consequence (`_unassign_consequence`), which is usually "no
            # model at all", not "back to defaults".
            messages.warning(
                request,
                f"Unassigning {role.label} — {_unassign_consequence(role)}: "
                f"{_severity_copy(True)} "
                "Check “confirm rebind” and submit again to proceed.",
            )
            return _redirect_console(request)
        dim_changed = True
        if current_connection is not None:
            dim_changed = current_connection.embed_dim != connection.embed_dim
        messages.warning(
            request,
            f"Assigning “{connection.name}” to {role.label}: {_severity_copy(dim_changed)} "
            "Check “confirm rebind” and submit again to proceed.",
        )
        return _redirect_console(request)

    if (
        role.capability == "embeddings"
        and changed
        and not Materialization.objects.filter(role_key=role.key).exists()
    ):
        # First embeddings assign ever: stamp the PRE-assign fingerprint so
        # the drift banner shows up right away -- see
        # `_stamp_first_materialization`. Unlike its other two call sites
        # (a fixed, always-resolvable fingerprint applied to a list of
        # roles), this one is single-role and its fingerprint may not
        # resolve at all (no outgoing connection AND no env override) -- in
        # that case there is nothing to stamp, so the helper is skipped
        # entirely rather than called with a None fingerprint.
        if current_connection is not None:
            pre_fingerprint = resolved_from_connection(current_connection).fingerprint
        else:
            try:
                pre_fingerprint = resolve(role.key).fingerprint
            except Exception:  # noqa: BLE001 -- unresolvable prior binding: nothing to stamp
                pre_fingerprint = None
        if pre_fingerprint is not None:
            _stamp_first_materialization([role], pre_fingerprint)

    binding.connection = connection
    with transaction.atomic():
        binding.save()
        # S3 (Coherence Wave B): `RoleBinding` assign/unbind was one of
        # the four unaudited settings surfaces the backend audit named
        # with no recorded rationale -- the recorded ruling is "audit
        # all four". Two actions, not one (`ROLE_ASSIGNED`/`ROLE_
        # UNASSIGNED`), mirroring `MODELSET_ATTACHED`/`MODELSET_
        # DETACHED` -- see that action pair's own docstring.
        if connection is not None:
            audit.record(
                principal_for_request(request), actions.ROLE_ASSIGNED,
                target_type="role_binding", target_key=binding.pk,
                target_label=role.label, role_key=role.key,
                connection_id=connection.pk, connection_label=connection.name,
            )
        else:
            audit.record(
                principal_for_request(request), actions.ROLE_UNASSIGNED,
                target_type="role_binding", target_key=binding.pk,
                target_label=role.label, role_key=role.key,
            )
    if connection is not None:
        messages.info(request, f"Assigned “{connection.name}” to {role.label}.")
        bind_note = _family_bind_note(role, connection)
        if bind_note:
            messages.warning(request, bind_note)
    else:
        messages.info(
            request, f"{role.label} is now unassigned — {_unassign_consequence(role)}."
        )
    return _redirect_console(request)


@require_POST
def role_reencode(request):
    """POST /inference/roles/reencode/ -- enqueue a `rag.reencode` job for
    a role's `rematerialize` callback instead of running it inside this
    request (T8, the execution-queue build's second job kind -- see
    `models.registry.jobs` for `plan_reencode`/`run_reencode`).

    Refuses to enqueue -- an honest `messages.error`, no job created --
    when a `rag.reencode` job is already queued or running: there is
    exactly one re-encode worth running at a time, and (since
    `models.registry.jobs.plan_reencode` declares `exclusive=True`) a
    second one would only ever sit blocked behind the first anyway, so a
    duplicate row would tell the operator nothing the first job's own row
    doesn't already say. The existence check tolerates the jobs tables
    not existing yet (a `ProgrammingError`/`OperationalError`, same
    unmigrated-window tolerance `models.registry.bindings._bound_
    connection` already shows) by treating "can't tell" as "no
    duplicate" -- the `enqueue()` call right after this would raise
    `QueueUnavailable` for the exact same reason and report it honestly
    either way.

    A role with no `rematerialize` callback configured (e.g. `rag.answer`)
    is refused the SAME WAY, before ever enqueuing -- the exact `ValueError`
    `models.registry.drift.run_rematerialize` itself would raise, checked
    here instead so a certain-to-fail job is never queued at all.

    An UNBOUND role (no `RoleBinding`/environment override resolves it --
    e.g. `rag.embed` with nothing assigned yet) is a DIFFERENT `ValueError`:
    `models.contracts.bindings.resolve()`'s own "No inference binding
    resolved", raised by `models.registry.jobs.plan_reencode`'s call to
    `resolve()` -- which runs INSIDE `enqueue()` (`models.queue.backend.
    enqueue` calls the planner directly and lets its raise propagate
    uncaught). Caught HERE, wrapped around the `enqueue()` call itself,
    rather than by a separate pre-resolve of the role beforehand: one
    resolution, at the one place that actually performs it, is simpler than
    a second up-front `resolve()` call AND closes a TOCTOU gap a pre-check
    would otherwise leave open (the binding could change between a
    pre-check and the enqueue call moments later). This is narrower than
    `tools.rag.views.AskView`'s own "resolve both roles before
    calling `enqueue()`" posture for `rag.ask` -- that view pre-checks
    because it has TWO roles and its own health-check story to run first;
    this view has exactly one role and one resolve, so catching the
    planner's own `ValueError` at the call site is the whole check.

    The run's own success/failure surface has moved to the Queue page
    (the job's stored `result["summary"]` / `error`) -- this view no
    longer calls `run_rematerialize` synchronously, nor renders a tally or
    a `RuntimeError` message itself; that copy lives on in the job row
    instead (`models.registry.jobs.run_reencode`'s docstring).

    The drift banner (`_role_row.html`) needs NO code change: it already
    reads `Materialization`, which `run_rematerialize` still stamps on a
    SUCCESSFUL run -- only now that happens on the worker, sometime after
    this request returns, rather than synchronously within it. An operator
    who clicks "Re-encode now" will see the banner stay up (correctly --
    the data hasn't been re-encoded yet) until the queued job actually
    finishes; the Queue page is where they watch that happen.

    `settings_redirect`, not `_redirect_console`, on all six ways back:
    this view has never preserved an active endpoint override and this
    round is not the one to change what it sends the operator back to.
    The narrower helper adds the assistant panel's open flag and nothing
    else, which is the whole of what the persistence round promises.
    """
    role_key = request.POST.get("role_key", "").strip()
    role = get_role(role_key)
    if role is None:
        messages.error(request, f"Unknown role “{role_key}”.")
        return settings_redirect(request, "inference-console")

    if not role.rematerialize:
        messages.error(
            request, f"Role {role.key!r} has no rematerialize callback configured"
        )
        return settings_redirect(request, "inference-console")

    try:
        duplicate = InferenceJob.objects.filter(
            kind="rag.reencode", state__in=(QUEUED, RUNNING)
        ).exists()
    except (ProgrammingError, OperationalError):
        duplicate = False

    if duplicate:
        messages.error(
            request, "A re-encode is already queued — check the Queue page."
        )
        return settings_redirect(request, "inference-console")

    try:
        enqueue(
            "rag.reencode",
            {
                "role_key": role.key,
                # WHO ASKED -- the acting principal, stamped into every
                # job payload (the acting rule). This
                # console is superuser-only, so the row is admin-visible
                # either way; stamped anyway because "every enqueuing
                # surface stamps the actor" is a rule with no exceptions
                # to remember.
                **payload_fields(principal_for_request(request)),
            },
        )
    except ValueError:
        messages.error(
            request, f"{role.label} has no model assigned — assign one before re-encoding."
        )
        return settings_redirect(request, "inference-console")
    except QueueUnavailable:
        messages.error(request, "The queue isn't ready yet — run database migrations.")
        return settings_redirect(request, "inference-console")
    except QueueQuotaExceeded as exc:
        # C-7 review, fix round 1: this route had no handler for this
        # exception -- there is no broad `except Exception` around the
        # `enqueue()` call above either, so a superuser already at their
        # own queue cap would have hit an uncaught exception (a 500).
        # Same shape as the `QueueUnavailable` catch just above: an
        # honest, never-500 message naming what actually happened.
        messages.error(request, str(exc))
        return settings_redirect(request, "inference-console")

    messages.success(request, "Re-encode queued — track it on the Queue page.")
    return settings_redirect(request, "inference-console")


@require_POST
def server_scan(request):
    """POST /inference/scan/ -- the "Scan for model servers" button.
    The ONLY call site for `models.registry.discovery.scan_for_servers`;
    nothing on this page's GET path ever reaches it, so nothing probes
    anything until an operator explicitly clicks this button.

    Re-renders the console template directly from this POST, with
    `scan_results` added to the same context `ConsoleView` builds, instead
    of redirecting-with-results: a redirect has nowhere stateless to carry
    the results (no new persisted state -- not `request.session`, not a DB
    row -- scan results are never persisted),
    and a signed-querystring round-trip would just be a more complicated
    way to do the same thing a plain re-render already does correctly.
    Works with JS disabled, same as every other form on this page.

    Scans against the SAME endpoint the console is currently showing --
    not always the env/DB default. An active `?endpoint=` override cannot
    arrive on this POST's query string (the scan forms post bare), so the
    forms round-trip it in a hidden `endpoint_override` field and
    `_endpoint_context` reads that with the same validation as the GET
    param. Re-scanning from an override page therefore
    treats the override as the already-checked endpoint -- it is skipped
    in the candidate sweep rather than reported back as a "hit".
    """
    endpoint, override = _endpoint_context(request)
    context = _build_context(endpoint, override)
    context["endpoint_override"] = override
    context["scan_results"] = scan_for_servers(endpoint)
    return render(request, "inference/console.html", context)


@require_POST
def connection_sets(request, pk):
    """POST /inference/connections/<pk>/sets/ -- set one `ModelConnection`'s
    set memberships to exactly what was submitted (spec section 6.10).

    THE EDGE AN OPERATOR EDITS WHEN A NEW MODEL ARRIVES, which is why it
    lives beside the connection on the console (`_registered_connection.
    html`) rather than on the sets page -- the sets page owns the OTHER
    edge, attaching entitlements to a set.

    404 for an unknown connection (`pk` is a URL path segment; Django's
    `<int:pk>` converter already refuses a non-numeric one before this
    function ever runs). A non-numeric SET id inside the submitted list
    is a tampered/stale form, refused with a flash rather than a 500 --
    never `int()` called on a value straight off `request.POST`. An
    id that IS numeric but names no `ModelSet` row (a set deleted in
    another tab, a hand-crafted request) is refused the same way --
    `isdecimal()` alone let a well-formed-but-unknown id reach
    `ModelSetMember.objects.create(model_set_id=...)`, a deferred FK a
    test never catches but Postgres enforces for real, reproducing as a
    500 (T14 review finding 2). Validated against what actually exists,
    the same shape `tools/rag/views.py::document_labels_bulk` checks a
    submission against `labelling_entitlements(principal)` rather than
    trusting `isdecimal()` alone.
    """
    connection = get_object_or_404(ModelConnection, pk=pk)
    raw = request.POST.getlist("sets")
    if any(not value.isdecimal() for value in raw):
        messages.error(request, "Sets are chosen from the list, not typed.")
        return _redirect_console(request)
    wanted = {int(value) for value in raw}
    known = set(ModelSet.objects.filter(pk__in=wanted).values_list("pk", flat=True))
    if wanted - known:
        messages.error(request, "One of those model sets no longer exists.")
        return _redirect_console(request)
    principal = principal_for_request(request)
    set_memberships_for(principal, connection, wanted, added_by=user_for_request(request))
    messages.info(request, f"Updated “{connection.name}”'s model sets.")
    return _redirect_console(request)


_MODEL_SET_ACTIONS = {"rename", "delete", "attach", "detach"}


def model_sets(request):
    """GET /inference/sets/ -- every model set, its members, and its
    attached entitlements; POST creates one.

    THE UNATTACHED-SET STATE IS LOUD HERE (decision 28): a set with no
    entitlement attached restricts NOBODY, and the template says so
    beside every such set -- not as an edge case, but because create the
    set -> add the models -> attach the entitlement is the ROUTINE
    workflow, and the middle of it must never read as "this is now
    locked" when it is not.
    """
    principal = principal_for_request(request)
    if request.method == "POST":
        name = request.POST.get("name", "")
        try:
            create_set(principal, name=name, description=request.POST.get("description", ""))
        except SetRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Created “{name.strip()}”.")
        return redirect("inference-model-sets")

    sets = list(
        ModelSet.objects.prefetch_related(
            "members__connection", "entitlement_attachments__entitlement"
        ).all()
    )
    # `delete_counts` STAMPED ONTO EACH ROW, not a separate `{pk: counts}`
    # context dict: a Django template cannot look a dict value up by a
    # per-loop-iteration key, and a custom filter for four lines of
    # dataclass-free counting is worse than a transient attribute the
    # template reads as `model_set.delete_counts.Models`.
    for model_set in sets:
        model_set.delete_counts = set_delete_counts(model_set)
    return render(request, "inference/model_sets.html", {
        "model_sets": sets,
        "entitlements": labelling_entitlements(principal),
    })


@require_POST
def model_set_edit(request, pk):
    """POST /inference/sets/<pk>/ -- rename, delete, attach or detach one
    model set's entitlement. Dispatched by one `action` field, the same
    shape `identity/views.py::_entitlement_action` uses: an unrecognised
    action is a flash and a redirect, `SetRefused` rendered with
    `messages.error`, redirect back to the sets list either way.

    There is no separate per-set PAGE -- every form lives inline on
    `model_sets.html` -- so this name exists only as an action target.
    `@require_POST` means a GET here (a stray bookmark, a back button) is
    refused 405 before this function body ever runs, not redirected.
    """
    model_set = get_object_or_404(ModelSet, pk=pk)
    principal = principal_for_request(request)
    action = request.POST.get("action", "")
    if action not in _MODEL_SET_ACTIONS:
        # F7's flash-and-redirect, for the reason stated in full at
        # `identity/views.py::user_edit` (N2, Wave C review).
        messages.error(request, f"{action!r} is not a recognised action.")
        return redirect("inference-model-sets")
    try:
        if action == "rename":
            rename_set(principal, model_set, request.POST.get("name", ""))
            messages.info(request, "Renamed.")
        elif action == "delete":
            counts = delete_set(principal, model_set)
            removed = ", ".join(f"{label}: {n}" for label, n in counts.items())
            messages.info(request, f"Deleted “{model_set.name}” ({removed}).")
            return redirect("inference-model-sets")
        else:
            entitlement_id = request.POST.get("entitlement", "")
            if not entitlement_id.isdecimal():
                messages.error(request, "Choose an entitlement from the list.")
                return redirect("inference-model-sets")
            entitlement_id = int(entitlement_id)
            if action == "attach":
                # VALIDATED AGAINST WHAT THE FORM OFFERED
                # (`labelling_entitlements(principal)`), not merely
                # `isdecimal()`: an id that IS numeric but names no
                # entitlement at all (deleted in another tab, a
                # hand-crafted request) would otherwise reach
                # `ModelSetEntitlement.objects.create(entitlement_id=...)`
                # and raise `IntegrityError` on the deferred FK -- a 500
                # no test exercising only well-formed ids would catch
                # (T14 review finding 2). The same shape
                # `tools/rag/views.py::document_labels_bulk` already
                # uses for the identical reason.
                offered = {offered_pk for offered_pk, _name in labelling_entitlements(principal)}
                if entitlement_id not in offered:
                    messages.error(request, "Choose an entitlement from the list.")
                    return redirect("inference-model-sets")
                attach(principal, model_set, entitlement_id,
                      attached_by=user_for_request(request))
                messages.info(request, "Attached.")
            else:
                # No matching validation needed here: `detach` only
                # DELETEs rows matching an existing filter (idempotent,
                # `models/registry/labels.py::detach`), so an unknown
                # entitlement id is a silent no-op, never an insert that
                # could violate a foreign key.
                detach(principal, model_set, entitlement_id)
                messages.info(request, "Detached.")
    except SetRefused as exc:
        messages.error(request, str(exc))
    return redirect("inference-model-sets")
