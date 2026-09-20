"""Unit tests for tools/rag/apps.py (role + job-kind registration).

Covers RAG role registration at app startup: rag.answer and rag.embed, and
the `rag.ask` job kind (T5) -- registration already happened when the app
loaded (once, at test-session startup), so these tests just read back the
registries `tools/rag/apps.py::ready()` populated, matching
`models/queue/tests/test_worker.py`'s own registration-test convention.

`TestMediaRoleRegistration` covers the "media"-gated roles (media-into-RAG
plan, T1; ADR 0014), which are NOT registered by the real startup
(the "media" feature isn't in the default `FARABUNKER_FEATURES`) -- so
those tests re-run `RagConfig.ready()` under an explicit override, mirroring
`tools/vision/tests/test_apps.py::_run_ready_with`'s save/restore idiom.
"""
from __future__ import annotations

from django.apps import apps as django_apps
from django.test import override_settings

from agents.contracts.artifacts import file_resolver_for
from models.contracts.jobkinds import get_job_kind
from models.contracts import roles as roles_module
from models.contracts.roles import RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE, get_role


class TestRagRoleRegistration:
    def test_rag_answer_role_registered(self):
        """rag.answer role is registered with 'chat' capability."""
        role = get_role("rag.answer")
        assert role is not None
        assert role.key == "rag.answer"
        assert role.label == "RAG answer"
        assert role.capability == "chat"
        assert role.rematerialize is None

    def test_rag_embed_role_registered(self):
        """rag.embed role is registered with 'embeddings' capability and rematerialize."""
        role = get_role("rag.embed")
        assert role is not None
        assert role.key == "rag.embed"
        assert role.label == "RAG embeddings"
        assert role.capability == "embeddings"
        assert role.rematerialize == "tools.rag.services.reencode_all"


def _run_ready_with(features):
    """Call `RagConfig.ready()` under a feature setting, with the role
    registry saved and restored -- app startup already registered the real
    entries and the rest of the suite depends on them.

    Also snapshots/restores `agents.contracts.tools._TOOLS`, inline here
    rather than through the `isolated_tool_registry` fixture (`tools/rag/
    tests/_helpers.py`): `ready()` re-imports `tools/rag/tools.py`, which
    re-registers `rag.search`/`rag.ask`/`rag.ingest` at the end of the
    module-level dict every time it runs. Left unsnapshotted, each call
    here would reorder those three entries relative to every other app's
    tools for the rest of the process -- exactly the "passes in one
    collection order, fails in the other" drift that fixture exists to
    prevent. A fixture cannot wrap the save/restore of BOTH registries
    around ONE `override_settings` block the way this function's own
    `try`/`finally` does, so it saves and restores `_TOOLS` by hand, the
    same way it already does for `_ROLES`.
    """
    from agents.contracts import tools as tools_module

    saved_roles = dict(roles_module._ROLES)
    saved_tools = dict(tools_module._TOOLS)
    roles_module._ROLES.pop(RAG_TRANSCRIBE_ROLE, None)
    roles_module._ROLES.pop(RAG_EXTRACT_ROLE, None)
    try:
        with override_settings(FARABUNKER_FEATURES=features):
            django_apps.get_app_config("rag").ready()
        return get_role(RAG_TRANSCRIBE_ROLE), get_role(RAG_EXTRACT_ROLE)
    finally:
        roles_module._ROLES.clear()
        roles_module._ROLES.update(saved_roles)
        tools_module._TOOLS.clear()
        tools_module._TOOLS.update(saved_tools)


class TestMediaRoleRegistration:
    def test_both_roles_registered_when_the_feature_is_on(self):
        transcribe, extract = _run_ready_with(frozenset({"media"}))

        assert transcribe is not None
        assert transcribe.key == "rag.transcribe"
        assert transcribe.label == "RAG transcription"
        assert transcribe.capability == "transcription"
        assert transcribe.rematerialize is None

        assert extract is not None
        assert extract.key == "rag.extract"
        assert extract.label == "RAG image text extraction"
        assert extract.capability == "vision"
        assert extract.rematerialize is None

    def test_neither_role_registered_when_the_feature_is_off(self):
        transcribe, extract = _run_ready_with(frozenset())

        assert transcribe is None
        assert extract is None

    def test_neither_role_registered_when_only_other_features_are_on(self):
        """"media" specifically gates these roles -- "vision" alone must
        not turn them on."""
        transcribe, extract = _run_ready_with(frozenset({"vision"}))

        assert transcribe is None
        assert extract is None


class TestRagAskJobKindRegistration:
    def test_rag_ask_job_kind_registered(self):
        """rag.ask is registered with its planner/handler/summarizer dotted
        paths (mirrors TestRagRoleRegistration's rematerialize assertion:
        proves the STRING is right, not that it resolves -- resolution is
        covered by tools/rag/tests/test_jobs.py)."""
        kind = get_job_kind("rag.ask")
        assert kind.key == "rag.ask"
        assert kind.label == "Ask a question"
        assert kind.planner == "tools.rag.jobs.plan_ask"
        assert kind.handler == "tools.rag.jobs.run_ask"
        assert kind.summarizer == "tools.rag.jobs.summarize_ask"
        assert kind.default_priority is None


class TestRagIngestJobKindRegistration:
    def test_rag_ingest_job_kind_registered(self):
        """rag.ingest (T2) is registered unconditionally -- NOT behind the
        "media" feature flag (`TestMediaRoleRegistration` above), since
        plain prose/tabular document ingestion moves onto the queue for
        every deployment. `default_priority=200` is a higher number (lower
        priority -- see `models/queue/models.py`) than rag.ask's `None` ->
        the queue-wide default of 100: ingest queues behind an operator's
        own interactive questions."""
        kind = get_job_kind("rag.ingest")
        assert kind.key == "rag.ingest"
        assert kind.label == "Ingest a document"
        assert kind.planner == "tools.rag.jobs.plan_ingest"
        assert kind.handler == "tools.rag.jobs.run_ingest"
        assert kind.summarizer == "tools.rag.jobs.summarize_ingest"
        assert kind.default_priority == 200


class TestReadyRegistersTheDocumentLabelsCascade:
    """IA-2 T10 review finding: the `register_entitlement_cascade` call in
    `RagConfig.ready()` was untested production wiring -- deleting the
    whole block, or typo-ing the handler's dotted path, left every test in
    this repository green (`identity/tests/test_cascades.py`'s anti-vacuous
    pin only asserts the KEY is present, never the handler string). Same
    anti-vacuous shape as `agents/tests/test_apps.py::
    TestReadyRegistersTheToolLabelsCascade`: pop the key first, so passing
    proves `ready()` put it back -- with the RIGHT handler -- rather than
    some earlier registration this process already ran.
    """

    def test_ready_registers_the_document_labels_cascade(self):
        from identity.contracts import cascades as cascades_module

        django_apps.get_app_config("rag").ready()
        cascades_module._CASCADES.pop("rag.document_labels", None)
        registered = {spec.key for spec in cascades_module.all_entitlement_cascades()}
        assert "rag.document_labels" not in registered

        django_apps.get_app_config("rag").ready()
        by_key = {
            spec.key: spec for spec in cascades_module.all_entitlement_cascades()
        }
        assert "rag.document_labels" in by_key
        assert by_key["rag.document_labels"].handler == "tools.rag.labels.unlabel_all_for_entitlement"


class TestArtifactRegistrations:
    """`tools/rag/apps.py::ready()` already ran at test-session startup;
    these read the registries back, the convention this module's own
    docstring states."""

    def test_the_document_kinds_file_resolver_is_registered(self):
        assert file_resolver_for("document") == "tools.rag.access.artifact_file_for"

    def test_the_registered_path_actually_resolves(self):
        """REGISTERED IN THE SAME COMMIT AS THE HANDLER -- a registration
        naming a function that does not exist fails only at the moment a
        real tool call needs it, which is the worst possible moment."""
        from models.contracts.jobkinds import resolve_dotted_path

        assert callable(resolve_dotted_path(file_resolver_for("document")))
