"""
Console model discovery.

`discover(endpoints)` merges three sources into one view-model row per
model, for the console's model management page:

- `models.contracts.catalog.CATALOG` -- models the platform knows spec
  facts for (`in_catalog`): friendly name, capability, embed_dim. Catalog
  entries enrich rows only; they are never rendered as install suggestions.
- every `models.contracts.engines.ENGINES` adapter's `list_installed(...)`
  -- models actually present right now (`installed` / `loaded`), tagged
  with whichever engine reported them (`row.engine`) and with the
  `endpoint` they were seen at. Not anchored to a single hardcoded
  `get_engine("ollama")`, nor to a single shared endpoint
  (spec §4.6 / D11): each engine is polled at ITS OWN addresses, taken
  from `discover()`'s per-engine `endpoints` map, so a second engine's
  models finally appear instead of being looked for at the first
  engine's address.
- `models.registry.models.ModelConnection` -- models an operator has
  registered as a connection (`connected`), independent of whether that
  connection's engine happens to be reachable right now.

This module also provides `scan_for_servers()` (below `discover()`) -- a SEPARATE,
user-triggered-only sweep of each engine's well-known addresses, used to
find a model server discover()'s `endpoints` map doesn't already know
about. It shares this module only because it is engine-registry code, not
because it is part of `discover()`'s per-request merge: nothing here calls
it, and nothing calls it on a page load (see `models/registry/views.py`'s
`server_scan`, its only caller).

Pure composition: the only I/O this module performs is via the engine
adapter (HTTP, mockable per repo convention -- mock at the HTTP layer, not
by patching the engine's methods away). `discover()` does not query the
DB itself -- the caller (console's
`_build_context`, which needs the same `ModelConnection` rows for its own
per-role choice lists) fetches them once and passes them in.

Tag normalization (re-exported here as `norm_tag()`;
canonical implementation lives in `models.contracts.catalog` so
`catalog.find()` can share it without core importing console):
the catalog lists bare model names (e.g. `an-embedding-model`) while Ollama
reports installed models with an explicit tag (`an-embedding-model:latest`),
so exact `(engine, model_id)` equality would never merge them into one row.
The merge key treats a bare name as equivalent to `name:latest`; a catalog
entry with an explicit *non-latest* tag (e.g. `a-chat-model:1.5b`) is
unaffected and still only matches that exact tag. `DiscoveryRow.model_id`
always ends
up holding the engine's exact reported string once a model is installed
(that string is what gets sent back to the API), and `name` prefers the
catalog's friendly name when a catalog entry matches -- `norm_tag()` is
used only for comparisons/keys, never stored. The same bare-vs-tagged
mismatch exists between a `ModelConnection.model_id` (e.g. the seed
migration's bare `an-embedding-model`, or a hand-typed one) and a
`DiscoveryRow`'s tagged one, so `norm_tag()` is exported (not just an
internal `_key()` helper) for the console view to use in its own
installed/in-use comparisons and its one-step-assign create-or-reuse
lookup (`models/registry/views.py`).

Metadata precedence: for an installed model, `capability` and
`embed_dim` come from the engine's live report ONLY -- the catalog is never
the source of truth for a model already present on the machine, so its
values are used only while a model is *not* installed (no machine truth
exists yet). If the engine reports no `capabilities` at all (an older
Ollama build), the row's `capability`/`embed_dim` are left visibly unknown
(`None`) rather than falling back to a stale catalog guess; the
role-choice UI is the designed way to recover from that case. When the
engine reports several mapped capabilities, the choice of which one becomes
`capability` is deterministic, not order-dependent: `embeddings` wins when
present (a model that can embed is presented as an embedder), `chat`
otherwise if present, else the first reported. `embedding_length` is only
ever read into `embed_dim` when the *chosen* capability is `embeddings` --
for a chat/vision model that number is a hidden size, not a vector
dimension.

Multi-capability registration (T9): `capability` above is a display/
tie-break convenience, never the whole truth about a model that reports
several -- `DiscoveryRow.capabilities` carries the FULL set (e.g. a
vision-capable chat model's `("chat", "vision")`), additively, alongside
it. Registration (`models/registry/views.py`'s `machine_model_add` and
`connection_add`) stores that whole list on the resulting
`ModelConnection.capabilities`, so the connection binds to EITHER role --
the operator's binding decides its job, the registration just tells the
truth about what the model can do.

Failure behavior for an unreachable engine, per engine: an engine adapter's `list_installed`
has no exception handling of its own -- an unreachable `endpoint` raises a
raw `httpx.HTTPError` (or whatever error a non-HTTP adapter raises), and
the adapter stays that way deliberately (raw/simple; retry/catch policy is
a caller concern, not the adapter's). `discover()` wraps EACH (engine,
endpoint) call in its own `try/except` and treats that one pair's
"installed" as empty rather than letting the exception propagate or blank
out every other engine's (or endpoint's) results: the console page must still render the catalog/DB-
connections state (cold start, engine not running yet) instead of 500ing
just because one engine is down. Catalog and DB rows are unaffected by an
unreachable engine -- only `installed` / `loaded` information becomes
unavailable, and only for the (engine, endpoint) pair(s) that failed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from models.registry.models import ModelConnection
from models.contracts.catalog import CATALOG, norm_tag
from models.contracts.engines import ENGINES
from models.contracts.engines.base import InstalledModel

# Health/discovery calls against the one configured endpoint use each
# engine's own timeout; a scan candidate gets this much shorter one
# instead -- it is probing several addresses per engine and must stay fast
# (today's worst case: 1 engine x 4 hosts x 1 port x 0.5s ~= 2s), and only
# ever runs after an explicit operator click (see `scan_for_servers`).
PROBE_TIMEOUT = 0.5

# The only hosts shared across every engine's scan candidates -- loopback
# variants plus the Docker-Desktop-style host alias. Each engine's OWN
# registry name is also tried as a compose-service hostname (see
# `_candidate_urls`), but that is per-engine, not part of this shared list.
COMMON_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "host.docker.internal")


# `norm_tag` is imported from `models.contracts.catalog` (its canonical home
# since the final fix wave -- `catalog.find()` needs it too, and
# `models/contracts/` must never import `models/registry/`) and re-exported
# here unchanged: every pre-existing
# caller (`models/registry/views.py`, tests) still imports it from this
# module, and it remains the merge-key normalizer for `discover()` below.


def norm_endpoint(endpoint: str) -> str:
    """Normalize an endpoint for equality comparison -- strip a trailing
    slash only (`http://x:11434/` == `http://x:11434`); deliberately no URL
    parsing beyond that.

    Lives here (not in the console view, which imports it directly) because
    `scan_for_servers` needs the exact same comparison to skip a
    candidate that is just the already-checked default endpoint, so it
    lives next to the scan rather than duplicating the one-liner."""
    return endpoint.rstrip("/")


@dataclass(frozen=True)
class DiscoveryRow:
    """One row of the console's model management view model.

    One row per distinct (engine, model_id) seen across the catalog, the
    live engine's installed models, and the DB's registered connections.
    """

    engine: str
    model_id: str
    name: str
    capability: str | None
    in_catalog: bool
    installed: bool
    loaded: bool
    connected: bool
    size: int | None = None
    # Bytes the loaded instance occupies in memory right now (the
    # loaded-now chip; from the engine's ps report), None while idle or
    # unreported. Read off the engine's `InstalledModel` via `getattr` so an
    # adapter predating the field degrades to None, never AttributeError.
    loaded_size: int | None = None
    embed_dim: int | None = None
    # Where `capability` (and `embed_dim`, which always travels with it --
    # the catalog supplies both, the engine supplies both, and the
    # connection fill below supplies capability only, leaving embed_dim
    # None) came from: "catalog" | "engine" | "connection" | None (unknown).
    # The "nothing hidden" requirement: the console's per-row details
    # disclosure must say whether a value was detected from the engine,
    # suggested by the catalog, or stored by an operator -- and only this
    # merge knows which stage actually set it, so the row carries it out
    # rather than the view re-deriving (ambiguously) after the fact.
    capability_source: str | None = None
    # T9 (multi-capability registration): the model's FULL reported
    # capability set, e.g. `("chat", "vision")` for a vision-capable
    # chat model -- additive alongside `capability` above, which STAYS the
    # single display/primary value driven by the same tie-break
    # (embeddings > chat > first reported) every existing reader of
    # `.capability` already relies on (the needs-checklist's dedup key, the
    # role-row/installed-row "detected" disclosure line, `_role_options`'
    # membership filter -- unchanged, still `role.capability in
    # connection.capabilities` on the CONNECTION's own stored list, not
    # this field). `capabilities` is what a multi-capability model's
    # registration should carry forward whole: a vision-capable chat
    # model registers as `["chat", "vision"]` and its resulting connection
    # then appears in BOTH the chat and vision pickers -- the truth
    # about the model is that it can do both; which role actually binds it
    # is the operator's call, not this merge's. Filled from
    # `installed.capabilities` (the engine's live report, mapped to the
    # platform vocabulary) when the model is installed, else from
    # `connection.capabilities` (the operator's own stored list) when only
    # a registered connection exists for it, else left `()` -- same
    # installed-beats-connection precedence `capability`/`capability_source`
    # already use just above. Never populated from the catalog beyond a
    # `(entry.capability,)` singleton -- the catalog only ever knows one
    # capability per entry.
    capabilities: tuple[str, ...] = ()
    # The endpoint this model was seen INSTALLED at (spec §4.6 / D11).
    # Empty for catalog-only or connection-only rows: nothing was polled to
    # produce them. The console's "Add to registered" button reads this so a
    # ComfyUI checkpoint is registered against the ComfyUI endpoint, not
    # whichever address the page happens to be viewing.
    endpoint: str = ""
    # Which of the engine's own loader nodes reported this model
    # (`InstalledModel.loader`). Empty for a catalog-only or connection-only
    # row -- nothing was polled to produce them -- and empty for an adapter
    # that does not report one. The console reads it for exactly one
    # decision: whether registering this model must also ask for the
    # companion files a multi-file family needs.
    loader: str = ""


def _unique_endpoints(raw) -> list[str]:
    """De-duplicate an engine's endpoint list by `norm_endpoint`, keeping
    first-seen order -- the default endpoint and a registered connection
    frequently name the same address with/without a trailing slash, and
    polling it twice would double the page's HTTP cost for nothing."""
    seen: set[str] = set()
    ordered: list[str] = []
    for endpoint in raw or ():
        key = norm_endpoint(endpoint)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(endpoint)
    return ordered


def discover(endpoints: dict[str, list[str]], connections: list[ModelConnection]) -> list[DiscoveryRow]:
    """Merge catalog + live installed models + DB connections into rows.

    Args:
        endpoints: Per-engine endpoint lists (spec §4.6 / D11) -- each
            registered engine is polled at ITS OWN addresses, built by
            `models.registry.views._engine_endpoints` from
            `settings.INFERENCE_DEFAULT_ENDPOINTS` plus that engine's
            registered-connection endpoints plus the endpoint currently
            being viewed. An engine absent from the map is not polled at
            all (its catalog/connection rows still appear). Before this,
            every engine was polled at the single Ollama endpoint, so a
            ComfyUI checkpoint could never show up.
        connections: The registered `ModelConnection` rows to merge in --
            fetched once by the caller rather than queried here.

    Returns:
        One `DiscoveryRow` per distinct (engine, normalized model_id):
        catalog entries first (in catalog order), then installed-only or
        connection-only models in first-seen order. Rows are still keyed by
        (engine, model_id) and NOT by endpoint -- the same model id at two
        endpoints of one engine merges into one row carrying the FIRST
        endpoint it was seen at (and that sighting's size/capability/
        embed_dim); only `loaded`/`loaded_size` are unioned across the
        later sightings, since a model loaded at any one of its endpoints
        is loaded right now. See the module docstring for the
        unreachable-engine failure behavior, which now isolates per
        (engine, endpoint) pair.
    """
    rows: dict[tuple[str, str], DiscoveryRow] = {}
    order: list[tuple[str, str]] = []

    def _key(engine: str, model_id: str) -> tuple[str, str]:
        key = (engine, norm_tag(model_id))
        if key not in rows:
            rows[key] = DiscoveryRow(
                engine=engine,
                model_id=model_id,
                name=model_id,
                capability=None,
                in_catalog=False,
                installed=False,
                loaded=False,
                connected=False,
            )
            order.append(key)
        return key

    for entry in CATALOG:
        key = (entry.engine, norm_tag(entry.model_id))
        if key not in rows:
            order.append(key)
        rows[key] = DiscoveryRow(
            engine=entry.engine,
            model_id=entry.model_id,
            name=entry.name,
            capability=entry.capability,
            in_catalog=True,
            installed=False,
            loaded=False,
            connected=False,
            embed_dim=entry.embed_dim,
            capability_source="catalog",
            capabilities=(entry.capability,),
        )

    # Iterates every registered engine instead of a
    # hardcoded `get_engine("ollama")` -- each engine's models are tagged
    # with its own `.name`, and one engine raising (unreachable, or any
    # other failure) is isolated to that engine so it never hides another's
    # results. With only Ollama registered this produces byte-identical
    # output to the old hardcoded call.
    installed_by_engine: list[tuple[str, str, InstalledModel]] = []
    for engine in ENGINES.values():
        for endpoint in _unique_endpoints(endpoints.get(engine.name)):
            try:
                for installed in engine.list_installed(endpoint):
                    installed_by_engine.append((engine.name, endpoint, installed))
            except Exception:  # noqa: BLE001 -- one dead (engine, endpoint) must not blank the page
                continue

    for engine_name, engine_endpoint, installed in installed_by_engine:
        key = _key(engine_name, installed.model_id)
        row = rows[key]

        # FIRST sighting wins (D11): one model id can be installed at
        # several endpoints of the same engine, and they all merge into
        # this single row -- so a later sighting must not retarget the
        # row's `endpoint` (nor restate its size/capability) at whichever
        # address happened to be polled last, because `endpoint` is the
        # address "Add to registered" registers the model against. The one
        # exception is `loaded`: a model loaded at ANY of its endpoints IS
        # loaded right now, which is exactly what the console's chip
        # claims, so that (and the loaded size reported alongside it) is
        # unioned in from the later sighting.
        if row.installed:
            if installed.loaded and not row.loaded:
                rows[key] = replace(
                    row, loaded=True, loaded_size=getattr(installed, "loaded_size", None)
                )
            continue

        # An installed model's capability/embed_dim come from the engine's
        # live report ONLY -- the catalog is never the source of truth for
        # a model you already have. If the engine reported no capabilities
        # (older Ollama build omitting the field), both are left visibly
        # unknown (None) rather than falling back to stale catalog data;
        # the role-choice path is the designed way to recover from that.
        capability = None
        embed_dim = None
        if installed.capabilities:
            # Deterministic, not order-dependent: a model that can embed is
            # presented as an embedder regardless of where "embedding" fell
            # in Ollama's reported list; chat is the commoner default
            # otherwise (e.g. vision-only falls through to its own value).
            if "embeddings" in installed.capabilities:
                capability = "embeddings"
            elif "chat" in installed.capabilities:
                capability = "chat"
            else:
                capability = installed.capabilities[0]
            # `embedding_length` is only a vector dimension when the CHOSEN
            # capability is `embeddings`; for chat/vision it's a hidden size
            # and must never be surfaced as `embed_dim`.
            embed_dim = installed.embedding_length if capability == "embeddings" else None

        rows[key] = replace(
            row,
            model_id=installed.model_id,  # the engine's exact string, for API calls
            installed=True,
            endpoint=engine_endpoint,
            loader=getattr(installed, "loader", ""),
            loaded=installed.loaded,
            loaded_size=getattr(installed, "loaded_size", None),
            size=installed.size,
            capability=capability,
            embed_dim=embed_dim,
            capability_source="engine" if capability is not None else None,
            # T9: the engine's FULL reported capability set, additive
            # alongside the single `capability` tie-break above -- a
            # vision-capable chat model reporting `["completion",
            # "vision"]` carries the whole mapped tuple `("chat",
            # "vision")` here, not just whichever one the tie-break chose
            # as primary/display.
            capabilities=installed.capabilities,
        )

    for connection in connections:
        key = _key(connection.engine, connection.model_id)
        row = rows[key]
        capability = row.capability
        capability_source = row.capability_source
        if capability is None and connection.capabilities:
            capability = connection.capabilities[0]
            capability_source = "connection"
        # T9: same installed-beats-connection precedence as `capability`
        # just above -- an installed row's own engine-reported capability
        # SET (richer, live truth) is never overwritten by the
        # connection's stored one; only a row with no capabilities yet
        # (not installed anywhere `discover()` scanned) inherits the
        # operator's own registered list.
        capabilities = row.capabilities
        if not capabilities and connection.capabilities:
            capabilities = tuple(connection.capabilities)
        rows[key] = replace(
            row,
            connected=True,
            capability=capability,
            capability_source=capability_source,
            capabilities=capabilities,
        )

    return [rows[key] for key in order]


@dataclass(frozen=True)
class ScanHit:
    """One reachable model server `scan_for_servers` found, beyond the
    already-checked default endpoint."""

    engine: str
    endpoint: str
    model_count: int


@dataclass(frozen=True)
class ScanResult:
    """The full outcome of one `scan_for_servers` run: any hits, plus every
    candidate URL actually probed (so a "nothing found" console page can
    tell the operator exactly what was ruled out instead of leaving them to
    guess -- the "checked-addresses hint")."""

    hits: list[ScanHit]
    checked: list[str]


def _candidate_urls(engine) -> list[str]:
    """Every address `scan_for_servers` tries for one engine: `COMMON_HOSTS`
    crossed with the engine's own `well_known_ports`, plus the engine's
    registry `.name` used as a compose-service hostname (e.g.
    `http://ollama:11434`) for each of those same ports. Zero engine-name
    literals -- every value here comes off the `engine` object itself or
    the shared `COMMON_HOSTS` list."""
    urls = [
        f"http://{host}:{port}" for host in COMMON_HOSTS for port in engine.well_known_ports
    ]
    urls.extend(f"http://{engine.name}:{port}" for port in engine.well_known_ports)
    return urls


def scan_for_servers(default_endpoint: str) -> ScanResult:
    """A user-triggered sweep of each registered engine's
    well-known addresses -- finds a model server at a DIFFERENT address
    than the one the console already checks by default. Engine-agnostic by
    construction: this loop names no engine, no port, no path -- it only
    reads `ENGINES` and each engine's own declared `well_known_ports` /
    `is_healthy` / `list_installed`. Registering a second engine (even a
    test stub) makes it discoverable here with NO change to this function.

    NEVER called on a page load -- `models/registry/views.py`'s
    `server_scan` (a POST-only view) is this function's only caller.
    Sequential, no threads, no retries, no caching: each candidate gets
    `PROBE_TIMEOUT` (0.5s), so an engine with `COMMON_HOSTS` plus its own
    compose hostname and N ports costs at most `(len(COMMON_HOSTS) + 1) *
    N * PROBE_TIMEOUT` seconds -- and only after an explicit operator click.

    The candidate identical (via `norm_endpoint`) to `default_endpoint` is
    skipped -- the console already health-checked that address on this same
    page load, re-probing it here would be pure waste. `checked` records
    every OTHER candidate actually probed, hit or not, for the console's
    "nothing found -- here's what was ruled out" hint.

    A hit's `model_count` costs one extra call (`list_installed`) -- only
    paid for actual hits, which are rare; a `list_installed` failure right
    after a successful `is_healthy` (a narrow race) still counts as a hit,
    just with `model_count=0` rather than dropping it.
    """
    default_norm = norm_endpoint(default_endpoint)
    hits: list[ScanHit] = []
    checked: list[str] = []

    for engine in ENGINES.values():
        for url in _candidate_urls(engine):
            if norm_endpoint(url) == default_norm:
                continue
            checked.append(url)
            try:
                reachable = engine.is_healthy(url, timeout=PROBE_TIMEOUT)
            except Exception:  # noqa: BLE001 -- one broken engine must not stop the rest of the scan
                continue
            if not reachable:
                continue
            try:
                model_count = len(engine.list_installed(url))
            except Exception:  # noqa: BLE001 -- reachable but listing failed: still a hit, just uncounted
                model_count = 0
            hits.append(ScanHit(engine=engine.name, endpoint=url, model_count=model_count))

    return ScanResult(hits=hits, checked=checked)
