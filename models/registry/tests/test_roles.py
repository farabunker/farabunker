"""Unit tests for the role registry (models/contracts/roles.py).

Pure in-memory registry — no DB, no Django. The module-level `_ROLES` dict
is reset between tests with an autouse fixture built by
`models.contracts.testing.registry_reset_fixture` (C-59) so tests don't
leak state into each other.

`reset_registry` snapshots the registry before clearing it and restores
that snapshot on teardown (matching test_drift.py's
`_isolated_role_registry` / test_views.py's `_extra_role_registry` idiom)
rather than just clearing it on both ends -- clearing without restoring
would permanently wipe whatever `tools/rag/apps.py` registered at
Django app-ready() time for every test that runs after this file in the
same process, which broke `tools/rag/tests/test_apps.py` when the two
test files shared a process (surfaced by `pytest models/registry/
tools/rag/ -q`). The same factory also builds `models/registry/tests/
test_jobkinds.py`'s and `models/queue/tests/test_backend.py`/
`test_worker.py`'s `reset_registry`, over `jobkinds._JOB_KINDS` instead.
"""
from __future__ import annotations

import pytest

from models.contracts import roles
from models.contracts.roles import CAPABILITIES, RoleSpec, all_roles, get_role, register_role
from models.registry.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported

reset_registry = registry_reset_fixture(roles, "_ROLES")


# --- register_role / all_roles ------------------------------------------


class TestRegisterRole:
    def test_register_two_roles_are_both_retrievable(self):
        answer = RoleSpec(key="rag.answer", label="RAG Answer", capability="chat")
        embed = RoleSpec(key="rag.embed", label="RAG Embed", capability="embeddings")

        register_role(answer)
        register_role(embed)

        assert get_role("rag.answer") is answer
        assert get_role("rag.embed") is embed

    def test_reregister_same_key_is_idempotent_last_wins(self):
        original = RoleSpec(key="rag.answer", label="Original", capability="chat")
        updated = RoleSpec(key="rag.answer", label="Updated", capability="chat")

        register_role(original)
        register_role(updated)

        assert all_roles() == [updated]
        assert get_role("rag.answer") is updated

    def test_all_roles_ordering_is_stable_across_reregistration(self):
        first = RoleSpec(key="rag.answer", label="Answer", capability="chat")
        second = RoleSpec(key="rag.embed", label="Embed", capability="embeddings")
        first_updated = RoleSpec(key="rag.answer", label="Answer v2", capability="chat")

        register_role(first)
        register_role(second)
        register_role(first_updated)  # re-registering rag.answer must not move it to the end

        assert all_roles() == [first_updated, second]


class TestGetRole:
    def test_miss_returns_none(self):
        assert get_role("does.not.exist") is None


class TestRematerialize:
    def test_stored_as_dotted_string_not_resolved(self):
        spec = RoleSpec(
            key="rag.answer",
            label="RAG Answer",
            capability="chat",
            rematerialize="tools.rag.index.rebuild",
        )
        register_role(spec)

        stored = get_role("rag.answer")

        assert stored.rematerialize == "tools.rag.index.rebuild"
        assert isinstance(stored.rematerialize, str)

    def test_defaults_to_none(self):
        spec = RoleSpec(key="rag.answer", label="RAG Answer", capability="chat")

        assert spec.rematerialize is None


class TestCapabilityValidation:
    def test_invalid_capability_rejected(self):
        with pytest.raises(ValueError):
            RoleSpec(key="rag.answer", label="RAG Answer", capability="text")

    def test_transcription_is_a_known_capability(self):
        assert "transcription" in CAPABILITIES

    @pytest.mark.parametrize("capability", sorted(CAPABILITIES))
    def test_valid_capabilities_accepted(self, capability):
        spec = RoleSpec(key="rag.answer", label="RAG Answer", capability=capability)

        assert spec.capability == capability
