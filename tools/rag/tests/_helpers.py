"""Shared test helpers for tools/rag/tests.

Plain importable module -- **not** a `conftest.py` (the repo forbids
`conftest.py` files anywhere). Each test module imports what it needs from
here explicitly, by name -- pytest discovers a fixture present in a test
module's namespace, imported or defined locally, per repo convention (see
models/registry/tests/_helpers.py).

THE VISION-FLAG RULE (T10): any test in this package that overrides
`settings.FARABUNKER_FEATURES` (directly, via the `settings` fixture, or
via `django.test.override_settings`) AND performs an actual HTTP request
or URL resolution in that same test (Django's test `Client`, `reverse()`)
MUST keep `"vision"` in the overridden set, even when the test's own point
is about `"media"` or an empty set. `config/urls.py` builds its `vision/`
mount CONDITIONALLY, ONCE, at import time, off whatever
`FARABUNKER_FEATURES` happens to be at that moment -- Django resolves the
URLconf lazily, on the first real request/`reverse()` call in the whole
process, and does not re-evaluate it afterward. `FARABUNKER_FEATURES`
ships with `"vision"` on by default, so if a test is unlucky enough to be
the FIRST one in a given run to trigger URL resolution while it has
overridden the flag to something without `"vision"`, `config.urls` gets
built without the `vision/` mount -- permanently, for the rest of that
same test process, poisoning every later test (in ANY app) that expects
`reverse("vision-create")` (or any vision URL) to resolve. See
`tools.rag.tests.test_ingest.TestIngestEventHandlerPollOnce.
test_corrupt_pdf_via_watcher_fails_honestly_not_stranded_at_pending`'s own
docstring for the fuller mechanism, and `models.registry.tests.
test_views_reencode_and_sections.TestGettingModelsChecklist.
test_media_flag_on_grows_a_real_vision_model_row_for_rag_extract`'s
docstring for the same rule documented on the console side.

A test that overrides the flag WITHOUT ever touching the Client/`reverse()`
(calling an internal function directly -- `_IngestEventHandler.poll_once`,
`stage_document`, `run_ask`, etc.) is NOT at risk and may safely use
whatever minimal set proves its own point (including an empty one) --
`test_corrupt_pdf_via_watcher_fails_honestly_not_stranded_at_pending`
itself is exactly this case, and deliberately does NOT round-trip through
the HTTP view for this very reason.
"""
from __future__ import annotations

import io
import itertools
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from models.contracts.bindings import ResolvedModel
from models.contracts.jobkinds import JobContext
from models.registry.models import ModelConnection, RoleBinding
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)

_names = itertools.count()


@pytest.fixture
def client():
    return Client()


def bind_rag_roles():
    """Bind `rag.answer` and `rag.embed` to registered connections.

    UI-1 decision 2: the shell renders the Ask and Search entries only
    when the roles behind them have a model bound, so a test about which
    entry is marked current has to make them available first. Ask needs
    BOTH roles (it retrieves before it answers); Search needs the
    embedding one.

    Shared by `test_views_ask.py`, `test_views_documents.py`, and
    `test_views_upload_and_settings.py` -- moved here (from test_views.py)
    when that file split into four (C-56b), since three of the four
    files call it.
    """
    answer = ModelConnection.objects.create(
        name="nav answer conn", engine="ollama", endpoint="http://localhost:11434",
        model_id="an-identifier", capabilities=["chat"],
    )
    embed = ModelConnection.objects.create(
        name="nav embed conn", engine="ollama", endpoint="http://localhost:11434",
        model_id="another-identifier", capabilities=["embeddings"], embed_dim=768,
    )
    RoleBinding.objects.create(role_key=RAG_ANSWER_ROLE, connection=answer)
    RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed)


def make_document(**overrides):
    """A minimal `Document` row -- the same shape
    `TestDocumentsView._make_document` (this file's own module) already
    builds per-class; a shared copy for the modules that only need one
    plain row and no per-test title/category plumbing.

    Shares a name and a purpose with `identity/tests/_helpers.py::
    make_document`, not a body -- that one resolves `rag.Document`
    through `apps.get_model` because it is scaffolding for the route
    matrix, which spans every column and may not import `tools.rag.
    models` directly; this one is already inside the `rag` column, so
    it just imports `Document`. Two row builders for two different
    contexts, not the same function duplicated with no reason to
    differ -- see that module's own comment.
    """
    from tools.rag.models import Document

    fields = dict(
        title=f"doc-{next(_names)}.txt",
        source_path="/tmp/does-not-matter.txt",
        file_hash="a" * 64,
        doc_type=Document.DocType.PROSE,
    )
    fields.update(overrides)
    return Document.objects.create(**fields)


def _workstream(**overrides):
    """A `Workstream` row for a `tools/rag` test that needs one.

    THE `tools/rag` TWIN of `agents/tests/_helpers.py::_workstream` (spec
    section 17.9). Duplicated per column, never imported across, exactly as
    `isolated_tool_registry` is and for the same reason -- consolidating
    ACROSS apps would make one app's test scaffolding load-bearing for
    another's. Reached through `apps.get_model`, so this module still
    imports nothing of `agents/` at module scope.
    """
    from django.apps import apps

    model = apps.get_model("agents", "Workstream")
    fields = {"name": f"stream-{next(_names)}"}
    fields.update(overrides)
    return model.objects.create(**fields)


def make_agent(**overrides):
    """A minimal `Agent` row, through `apps.get_model`, so this module
    imports nothing of `agents.models` -- the same rule `_workstream`
    above follows and for the same reason."""
    from django.apps import apps

    model = apps.get_model("agents", "Agent")
    fields = {"slug": f"agent-{next(_names)}", "name": "Test agent",
              "system_prompt": "Be useful.", "tool_keys": []}
    fields.update(overrides)
    return model.objects.create(**fields)


def make_conversation(**overrides):
    """A minimal `Conversation` row, through `apps.get_model` -- the
    SAME reason `_workstream`/`make_agent` above give, for any
    `tools/rag` test needing a conversation to attach a document to
    (e.g. the chat attach form's `conversation` field). `agent`
    defaults to a fresh `make_agent()` row, matching `agents/tests/
    _helpers.py::make_conversation`'s own default -- this is that
    module's `tools/rag` twin, not an import of it."""
    from django.apps import apps

    model = apps.get_model("agents", "Conversation")
    fields = dict(agent=overrides.pop("agent", None) or make_agent())
    fields.update(overrides)
    return model.objects.create(**fields)


def model_available():
    """Body of the `_model_available` autouse fixture shared by
    `TestAskView`, `TestAskEnqueue`, and `TestAskViewConnectionOverride` in
    test_views_ask.py (T6: `AskView` only enqueues -- these tests
    exercise its pre-check + enqueue call, never a real answer).

    Stubs `resolve()` (the name bound in tools.rag.views) and `get_engine()`
    (bound in tools.rag.messages -- C-15's shared endpoint-dedup health-check
    probe, `unreachable_endpoints`, is what `AskView`'s pre-check calls
    into now) so every test in the class transits `AskView`'s pre-check
    without touching the network -- matching this file's "never hit Ollama"
    convention -- resolving to one healthy ollama endpoint unless a test
    layers its own `@patch` on top, which takes precedence for the
    duration of that test.
    """
    with patch("tools.rag.views.resolve") as mock_resolve, patch(
        "tools.rag.messages.get_engine"
    ) as mock_get_engine:
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434"
        )
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        yield


def make_job_ctx(**overrides) -> JobContext:
    """A `models.contracts.jobkinds.JobContext` for calling `run_ask`/
    `run_ingest` directly (T3) -- no real worker/DB behind it, just inert
    `_report`/`_checkpoint` writers. See `models/registry/tests/
    _helpers.py`'s identical function for why this is duplicated per app
    rather than imported cross-app."""
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
    `models/registry/tests/_helpers.py`'s identical note). Reuses this
    module's own `make_job_ctx` for the `job` field.
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
    module in this package that imports it BY NAME, e.g. `from
    tools.rag.tests._helpers import isolated_tool_registry  # noqa: F401`
    -- directly, or via `pytestmark = pytest.mark.usefixtures(
    "isolated_tool_registry")` for a whole module. A module-global
    registry surviving between tests is exactly the state that makes a
    suite pass in one collection order and fail in the other, which is
    why the repo runs both orders.
    """
    from agents.contracts import tools as tools_module

    saved = dict(tools_module._TOOLS)
    yield
    tools_module._TOOLS.clear()
    tools_module._TOOLS.update(saved)


def post_ask(client, payload):
    """POST a question payload to `/rag/ask/` as JSON, shared by every
    `TestAskView*` class in test_views_ask.py."""
    return client.post(
        reverse("rag-ask"), data=json.dumps(payload), content_type="application/json"
    )


def fake_whisper_get(*, status_code: int = 200, body: str = "whisper.cpp server ready"):
    """Build a `get_bounded`-compatible double for patching
    `models.contracts.engines.whisper.get_bounded` (H11 review round 1 --
    `is_healthy` reads through `get_bounded`, not `httpx.get`, since S11
    landed; a patch on `httpx.get` is never called and a unit test that
    still used one would silently fall through to a REAL network call).
    Mirrors `models.registry.tests._helpers.fake_ollama_get`'s shape (repo
    convention: mock at the reader layer, never the engine's own methods
    away, so `WhisperEngine.is_healthy`'s real marker-sniff runs for
    real): any NON-2xx `status_code` raises `httpx.HTTPStatusError`,
    exactly as a real `get_bounded` does via `raise_for_status()` --
    `Response.is_success` (200-299) is the only thing that short-circuits
    it, so a 3xx redirect raises exactly like a 4xx/5xx, never only
    `>= 400`; otherwise returns `body` encoded as bytes, matching
    `get_bounded`'s real return type.

    Used by `TestIsHealthy` in test_whisper_engine.py -- `status_code`/`body`
    are the only two facts `is_healthy` reads off the reader's result.
    """

    def fake_get_bounded(url, *, timeout=None, params=None, max_bytes=None, client=None, truncate=False):
        if not (200 <= status_code < 300):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=request, response=httpx.Response(status_code, request=request)
            )
        return body.encode()

    return fake_get_bounded


def fake_whisper_post(*, status_code: int = 200, payload=None, text: str = ""):
    """Build an `httpx.post` side_effect for patching
    `models.contracts.engines.whisper.httpx.post` -- `WhisperTranscriber.
    transcribe` (unlike `is_healthy` above) still calls `httpx.post`
    directly, so this one doubles the real HTTP layer, not `get_bounded`.
    Used by `TestTranscribe` in test_whisper_transcriber.py to double
    `POST /inference` without ever hitting a real server.
    """

    def fake_post(url, files=None, data=None, timeout=None):
        response = MagicMock()
        response.status_code = status_code
        response.text = text
        response.json.return_value = payload if payload is not None else {}
        return response

    return fake_post


def make_pdf_bytes(page_texts: list[str | None]) -> bytes:
    """Hand-assemble a minimal valid PDF with one page per entry in
    `page_texts`, each page's content stream a single `Tj` text-showing
    operator -- enough for `pypdf.PdfReader.extract_text()` to recover the
    literal text, without pulling in a PDF-authoring dependency this repo
    doesn't otherwise need (reportlab/fpdf2 are not in requirements.txt).

    An entry of `None` (rather than `""`) yields a page whose content
    stream is entirely EMPTY -- no `BT`/`Tj`/`ET` operators at all, the
    closer proxy for a scanned/image-only page's PDF structure, as opposed
    to a text page that merely shows an empty string.

    ONE COPY (C-61). This function was written out identically in five
    modules of this package, and `test_transcode.py`'s own copy called
    itself "a standalone copy, not a cross-module import" -- which is the
    sanctioned-duplication reasoning for crossing an APP boundary, applied
    to two files in the same package, where this module is the documented
    answer. Renamed from `_make_pdf_bytes` to match every other builder
    exported from here.
    """
    n_pages = len(page_texts)
    font_num = 3 + 2 * n_pages

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(n_pages))}] "
            f"/Count {n_pages} >>"
        ).encode(),
        font_num: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, text in enumerate(page_texts):
        page_num = 3 + 2 * i
        content_num = page_num + 1
        objects[page_num] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/Contents {content_num} 0 R >>"
        ).encode()
        stream = b"" if text is None else f"BT /F1 18 Tf 10 100 Td ({text}) Tj ET".encode()
        objects[content_num] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n".encode())
        buf.write(objects[num])
        buf.write(b"\nendobj\n")

    xref_offset = buf.tell()
    count = len(objects) + 1
    buf.write(f"xref\n0 {count}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for num in sorted(objects):
        buf.write(f"{offsets[num]:010d} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())
    return buf.getvalue()
