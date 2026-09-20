"""Shared test helpers for models/registry/tests.

Plain importable module -- **not** a `conftest.py` (the repo forbids
`conftest.py` files anywhere). Each test module imports what it needs from
here explicitly, by name -- pytest discovers a fixture present in a test
module's namespace, imported or defined locally, per repo convention.

`registry_reset_fixture` (C-59) re-exports the snapshot/clear/restore
factory from `models/contracts/testing.py` -- the shared home for both
`jobkinds._JOB_KINDS` and `roles._ROLES` resets, since both registries
live in `models.contracts` (see that module's docstring for why the
factory is not in `identity/testing.py`).
"""
from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)
from models.registry import probe_cache
from models.registry.discovery import DiscoveryRow
from models.registry.models import ModelConnection, RoleBinding
from models.contracts import roles as roles_module
from models.contracts.jobkinds import JobContext
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE, get_role
from models.contracts.testing import registry_reset_fixture  # noqa: F401 -- re-exported

ENDPOINT = "http://localhost:11434"

# The ordinary, post-ruling state, stated EXPLICITLY on every view test that
# uses it rather than inherited from whatever this machine's environment
# holds: no model override anywhere. Shared by four of the
# `test_views_*.py` files (C-56a) -- moved here rather than left in any one
# of them.
NO_ENV_OVERRIDE = dict(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)

# The deliberate-operator state, equally explicit: `rag.answer` pinned from
# the environment, `rag.embed` left alone. Shared by two of the
# `test_views_*.py` files (C-56a).
CHAT_ENV_OVERRIDE = dict(LLM_MODEL="operator-chat-choice", EMBED_MODEL=None, EMBED_DIM=None)

# A per-process counter, not per-test: the default `name` below only needs
# to avoid COLLIDING with a sibling row created in the same test (the
# CI-unique constraint is per-database, not per-caller), so a monotonic
# suffix is enough and no test resets it.
_chat_names = itertools.count()


def make_chat_connection(**overrides) -> ModelConnection:
    """A registered chat-capable `ModelConnection`.

    Defaults: name="chat conn", engine="ollama", endpoint=ENDPOINT,
    model_id="llama3.1:8b", capabilities=["chat"]. Pass keyword overrides
    for a test whose point IS a specific value.

    The bare default name is "chat conn", unchanged -- existing callers
    (many, across `models/registry/tests/`) assert that literal text.
    Task 14's set tests are the first callers that need TWO unnamed
    connections in the SAME test, and the CI-unique name constraint would
    reject a second bare "chat conn" -- so a name COLLISION (checked
    against what already exists, never a call counter that would also
    change the FIRST connection's name) falls back to a numbered variant
    instead of raising. A test that cares about a specific name still
    passes `name=` explicitly.
    """
    fields = {
        "engine": "ollama",
        "endpoint": ENDPOINT,
        "model_id": "llama3.1:8b",
        "capabilities": ["chat"],
    }
    fields.update(overrides)
    if "name" not in overrides:
        name = "chat conn"
        if ModelConnection.objects.filter(name__iexact=name).exists():
            name = f"chat conn {next(_chat_names)}"
        fields["name"] = name
    return ModelConnection.objects.create(**fields)


def make_embed_connection(**overrides) -> ModelConnection:
    """A registered embeddings-capable `ModelConnection`.

    Defaults: name="embed conn", engine="ollama", endpoint=ENDPOINT,
    model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768.
    """
    fields = {
        "name": "embed conn",
        "engine": "ollama",
        "endpoint": ENDPOINT,
        "model_id": "nomic-embed-text",
        "capabilities": ["embeddings"],
        "embed_dim": 768,
    }
    fields.update(overrides)
    return ModelConnection.objects.create(**fields)


def bind(role_key: str, connection: ModelConnection) -> RoleBinding:
    """Bind `role_key` to `connection`, returning the `RoleBinding`."""
    return RoleBinding.objects.create(role_key=role_key, connection=connection)


def clear_seeded_rows() -> None:
    """Body of the `_clear_seeded_rows` autouse fixture shared by
    test_discovery.py, test_db_bindings.py, and the six `test_views_*.py`
    files (C-56a).

    Migration 0002 seeds a `ModelConnection`/`RoleBinding` pair for each
    role an operator pinned from the environment, and pytest-django applies
    migrations before any test runs -- so whether any rows exist depends on
    the machine's environment (there are no baked model defaults, so
    usually none do). Clearing unconditionally makes each file's "empty
    registry"/"empty table" assumption a fact rather than a hope. (Seeding
    itself is covered by test_seed.py, which sets the settings explicitly.)
    """
    RoleBinding.objects.all().delete()
    ModelConnection.objects.all().delete()


def clean_probe_cache() -> None:
    """Body of the `_clean_probe_cache` autouse fixture shared by every
    `test_views_*.py` module (moved from `test_views.py`, C-56a).

    `probe_cache` (C-07) carries NO `connection.in_atomic_block` guard --
    deliberately, an HTTP probe has no transaction to roll back -- so
    unlike `models.registry.availability`'s cache it is NOT automatically
    inert inside a `django_db` test. Every view test shares the same
    `ENDPOINT` constant and mocks `ENGINES`/`discover` with a different
    answer per test; without this, a cached `True` from one test's healthy
    mock would silently answer the next test's unhealthy one. Each
    consuming module's local fixture calls this both before AND after, so
    a cached answer can never leak past that module either."""
    probe_cache.invalidate()


@pytest.fixture
def client():
    """A plain `django.test.Client()`, for the view test modules that need
    one (every `test_views_*.py` file, C-56a)."""
    return Client()


def _only_rag_roles():
    """`patch.dict` context manager pinning the role registry to exactly
    the two RAG roles a handful of view tests assume.

    That "exactly two roles" precondition used to be a fact of app startup
    for free (only `tools.rag` registered a role at import time). It is
    no longer one: `tools.vision` also registers `vision.generate`
    whenever the "vision" feature is enabled, which is its default
    (`FARABUNKER_FEATURES` ships "vision" on). So the precondition is made
    explicit here instead of assumed -- the specs come from the live
    registry (`get_role`) so their labels/capabilities stay the real ones,
    never hand-duplicated.
    """
    return patch.dict(
        "models.contracts.roles._ROLES",
        {RAG_ANSWER_ROLE: get_role(RAG_ANSWER_ROLE), RAG_EMBED_ROLE: get_role(RAG_EMBED_ROLE)},
        clear=True,
    )


def _mock_engine(healthy: bool) -> MagicMock:
    """A stand-in for the sole registered engine (Ollama, in every test
    that uses this) -- `.name`/`.api_description`/`.well_known_ports`/
    `.library_url` are set to Ollama's real values so `_supported_engines()`
    and `connection_add`'s "engine must be registered" check see a
    coherent stub, not bare MagicMock attributes, even
    though `ENGINES` itself is mocked away for these tests. (A MagicMock
    auto-vivifies any unset attribute rather than raising, so
    `getattr(engine, "library_url", "")`'s fallback would never actually
    trigger here if this weren't set explicitly.)

    The same auto-vivification makes the THREE optional model-family
    members a lie unless they are deleted: Ollama's real adapter declares
    no family vocabulary at all, but a bare `MagicMock` answers
    `getattr(engine, "list_families", None)` with a `Mock`, so
    `_build_context` would pick this chat stub as the page's
    family-declaring engine. That cost nothing while an unusable option
    list merely rendered an empty `<select>`; it matters now that whether
    the family fieldset renders AT ALL is a function of that same
    `getattr`. Deleted rather than stubbed -- an absent member is exactly
    what the real adapter has."""
    engine = MagicMock()
    engine.is_healthy.return_value = healthy
    engine.name = "ollama"
    engine.api_description = "the Ollama HTTP API"
    engine.well_known_ports = (11434,)
    engine.library_url = "https://ollama.com/library"
    engine.install_cmd_template = "ollama pull <model-name>"
    del engine.list_families
    del engine.list_variants
    del engine.list_assets
    return engine


def _raising_engine(exc: Exception) -> MagicMock:
    """A stub engine whose `is_healthy` blows up -- for asserting
    `_check_health`'s per-engine try/except isolation degrades to
    unhealthy rather than 500ing."""
    engine = MagicMock()
    engine.is_healthy.side_effect = exc
    return engine


def _installed_row(model_id: str, **overrides) -> DiscoveryRow:
    """A `DiscoveryRow` for a model already installed on the machine."""
    fields = dict(
        engine="ollama",
        model_id=model_id,
        name=model_id,
        capability="chat",
        in_catalog=False,
        installed=True,
        loaded=False,
        connected=False,
        size=123456,
    )
    fields.update(overrides)
    # Real discover() output always records where an installed row's
    # capability came from; default to the common case (engine-reported)
    # unless a test overrides it.
    fields.setdefault("capability_source", "engine" if fields["capability"] else None)
    # T9: real discover() output always carries the FULL detected
    # capability set alongside the single tie-break `capability` -- default
    # to the single-capability case (`(capability,)`, or `()` when
    # `capability` is None) unless a test overrides it with its own
    # multi-capability tuple.
    fields.setdefault("capabilities", (fields["capability"],) if fields["capability"] else ())
    return DiscoveryRow(**fields)


def _catalog_only_row(model_id: str, **overrides) -> DiscoveryRow:
    """A `DiscoveryRow` for a model known to the catalog but NOT installed
    on the machine."""
    fields = dict(
        engine="ollama",
        model_id=model_id,
        name=model_id,
        capability="chat",
        in_catalog=True,
        installed=False,
        loaded=False,
        connected=False,
    )
    fields.update(overrides)
    fields.setdefault("capabilities", (fields["capability"],) if fields["capability"] else ())
    return DiscoveryRow(**fields)


def _getting_models_html(body: str) -> str:
    """The rendered "Getting models" block only -- the slice from its
    opening div to its first closing div (the block nests no other divs),
    so tests can assert what does NOT render inside it specifically."""
    start = body.index('<div class="getting-models">')
    end = body.index("</div>", start)
    return body[start:end]


@pytest.fixture
def _extra_role_registry():
    """Snapshot/restore models.contracts.roles' module-level registry
    (matching test_drift.py's `_isolated_role_registry` idiom), but without
    clearing it first -- this test needs the real rag.answer/rag.embed
    roles to stay registered, then adds a 3rd/4th role on top."""
    original = dict(roles_module._ROLES)
    yield
    roles_module._ROLES.clear()
    roles_module._ROLES.update(original)


def _create_form_engine_options(body: str) -> str:
    """The create form's engine <select> markup (the first one after the
    add-connection form opens -- the edit forms come earlier in the DOM,
    inside Registered connections)."""
    form_start = body.index('class="inline-form add-connection"')
    select_start = body.index('<select name="engine">', form_start)
    select_end = body.index("</select>", select_start)
    return body[select_start:select_end]


def make_job_ctx(**overrides) -> JobContext:
    """A `models.contracts.jobkinds.JobContext` for calling a job-kind
    handler directly in a test (T3) -- no real worker/DB behind it, just
    inert `_report`/`_checkpoint` writers so a handler that DOES call
    `report_progress`/`checkpoint` doesn't blow up mid-test for lacking
    one. Defaults: `job_id=1`, `attempt=0`, `checkpoint_state=None`, both
    writers no-ops. Pass `_report`/`_checkpoint` overrides (e.g. a
    list-appending lambda) for a test whose point IS observing a call.

    This exact shape is intentionally duplicated (not imported) across
    `models/registry/tests/_helpers.py`, `tools/rag/tests/_helpers.py`,
    and `tools/vision/tests/_helpers.py` -- one per app whose tests call
    a handler directly -- matching this codebase's established rule that a
    test module only ever imports from its OWN app's `_helpers.py` (no
    test file anywhere crosses that boundary today, including within
    the `models/` column itself), rather than introduce the first cross-app test
    import for four lines of dataclass construction.
    """
    fields = dict(
        job_id=1,
        attempt=0,
        checkpoint_state=None,
        _report=lambda progress: None,
        _checkpoint=lambda state: None,
    )
    fields.update(overrides)
    return JobContext(**fields)


def make_tool_ctx(**overrides):
    """An `agents.contracts.tools.ToolContext` for calling a tool runner
    directly -- no agent, no turn, no queue behind it.

    Duplicated per app, exactly as `make_job_ctx` above is (see
    `tools/rag/tests/_helpers.py`'s identical note -- the sibling copy).
    Reuses this module's own `make_job_ctx` for the `job` field.
    """
    import time

    from agents.contracts.tools import Principal, StepBudget, ToolContext

    fields = dict(
        conversation_id="00000000-0000-0000-0000-000000000000",
        principal=Principal(kind="resident_agent", key="test-agent"),
        depth=0,
        budget=StepBudget(steps=8, deadline_monotonic=time.monotonic() + 900.0),
        job=make_job_ctx(),
    )
    fields.update(overrides)
    return ToolContext(**fields)


@pytest.fixture
def isolated_tool_registry():
    """Snapshot `agents.contracts.tools._TOOLS` on entry, restore it on
    exit.

    Same shape as `tools/vision/tests/_helpers.py:26-55`'s fixture of the
    same name (CQ-4): a real `@pytest.fixture`, defined here rather than
    in a `conftest.py` (the repo forbids those), requestable by any test
    module in this package that imports it BY NAME -- directly, or via
    `pytestmark = pytest.mark.usefixtures("isolated_tool_registry")` for
    a whole module. A module-global registry surviving between tests is
    exactly the state that makes a suite pass in one collection order and
    fail in the other, which is why the repo runs both orders.
    """
    from agents.contracts import tools as tools_module

    saved = dict(tools_module._TOOLS)
    yield
    tools_module._TOOLS.clear()
    tools_module._TOOLS.update(saved)


def fake_ollama_get(tags_models, ps_models=None):
    """Build an `httpx.get` side_effect dispatching `/api/tags` vs `/api/ps`,
    for patching `models.contracts.engines.ollama.httpx.get` (repo convention:
    mock at the HTTP layer, never the engine's own methods away, so
    `list_installed`'s real parsing is exercised).

    Shared by test_engines.py and test_discovery.py. `tags_models`/
    `ps_models` are the raw per-model dicts Ollama's API returns --
    supports both the rich shape (`capabilities`, `details.embedding_length`)
    and the older minimal shape (bare `name`/`size`).
    """
    ps_models = ps_models or []

    def fake_get(url, timeout):
        response = MagicMock()
        response.raise_for_status.return_value = None
        if url.endswith("/api/tags"):
            response.json.return_value = {"models": tags_models}
        elif url.endswith("/api/ps"):
            response.json.return_value = {"models": ps_models}
        else:
            raise AssertionError(f"unexpected url: {url}")
        return response

    return fake_get


def fake_ollama_show(capabilities=None, *, status_error=False, connect_error=False,
                     omit_capabilities=False, non_dict_body=None):
    """Build an `httpx.post` side_effect for `/api/show`, for patching
    `models.contracts.engines.ollama.httpx.post` (repo convention: mock
    at the HTTP layer, never the engine's own methods away, so the
    adapter's real URL building and parsing are exercised -- the same
    rule `tools/vision/tests/_helpers.py:8-12` states for FakeComfyUI).

    Shapes observed live on 2026-08-25: a chat model reports
    `["completion", "tools"]`, an embedding model `["embedding"]`, a
    vision-capable chat model `["completion", "vision"]`.

    `capabilities` is normally a list/tuple of strings and is passed
    through as a plain list; pass a non-list/non-tuple value (e.g. an
    int) to simulate a malformed response where `capabilities` is present
    but not the expected shape.

    `non_dict_body`, when given, REPLACES the whole decoded body (e.g. a
    bare list `[1, 2, 3]`) instead of building the usual `{"capabilities":
    ...}` mapping -- valid JSON that still isn't the object shape the
    caller assumes, for `supports_tool_calling`'s own "checks the shape
    before calling `.get`" guard.
    """
    import httpx

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/show"), f"unexpected url: {url}"
        assert json and "model" in json, "the adapter must name the model in the body"
        if connect_error:
            raise httpx.ConnectError("refused")
        response = MagicMock()
        if status_error:
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock(),
            )
            return response
        response.raise_for_status.return_value = None
        if non_dict_body is not None:
            body = non_dict_body
        elif omit_capabilities:
            body = {}
        elif capabilities is None or isinstance(capabilities, (list, tuple)):
            body = {"capabilities": list(capabilities or [])}
        else:
            body = {"capabilities": capabilities}
        response.json.return_value = body
        return response

    return fake_post
