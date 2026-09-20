"""Tests for the seed-from-env data migration
(models/registry/migrations/0002_seed_from_env.py).

There are no baked model defaults, so these tests must never depend on the
ambient environment happening to have a value set -- that would only be a
fact about this machine, not about the code. The seed function is instead
called DIRECTLY with the settings made EXPLICIT per test
(`override_settings`), against a registry each test clears first.
Both directions are covered: an unset override seeds NOTHING (a fresh install
migrates into a genuinely empty registry and the console's cold-start
onboarding is the real first-run path), an explicit override seeds exactly
what the operator pinned. The migration's own idempotence is unchanged and
still pinned here.

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
"""
from __future__ import annotations

import importlib

import pytest
from django.apps import apps
from django.test import override_settings

from models.registry.models import ModelConnection, RoleBinding
from models.registry.tests._helpers import clear_seeded_rows
from models.contracts.bindings import resolve

ENDPOINT = "http://ollama.test:11434"

seed_module = importlib.import_module("models.registry.migrations.0002_seed_from_env")


def _seed():
    """Run the migration's forward operation against the live models.

    The module name starts with a digit, so it can't be a normal `import`
    statement -- `import_module` handles that fine. `apps` (the real
    registry) stands in for the historical one a migration is handed; the
    two models involved are unchanged since 0001, so they are identical.
    """
    seed_module.seed_from_env(apps, schema_editor=None)


@pytest.mark.django_db
class TestSeedsNothingWithoutAnExplicitOverride:
    """The ruling: "there should be no default in the env files as there
    isn't a default to setup". An unset variable is the ABSENCE of a model
    choice, not a choice waiting to be materialized -- so the migration
    creates no connection and no binding for it."""

    @pytest.fixture(autouse=True)
    def _clear_seeded_rows(self):
        clear_seeded_rows()

    @override_settings(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)
    def test_both_unset_seeds_an_entirely_empty_registry(self):
        _seed()

        assert not ModelConnection.objects.exists()
        assert not RoleBinding.objects.exists()

    @override_settings(
        LLM_MODEL="operator-chat-choice",
        EMBED_MODEL=None,
        EMBED_DIM=None,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_each_half_is_independent(self):
        """Only `LLM_MODEL` pinned: the chat side seeds, the embeddings side
        stays untouched (not a half-built row with a null model id)."""
        _seed()

        assert ModelConnection.objects.filter(name="env: LLM_MODEL").count() == 1
        assert not ModelConnection.objects.filter(name="env: EMBED_MODEL").exists()
        assert RoleBinding.objects.filter(role_key="rag.answer").count() == 1
        assert not RoleBinding.objects.filter(role_key="rag.embed").exists()

    @override_settings(
        LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=1024, OLLAMA_BASE_URL=ENDPOINT
    )
    def test_a_dimension_without_a_model_seeds_nothing(self):
        """`EMBED_DIM` is a property of a model choice, not a choice in
        itself -- a half-set override must not materialize a connection
        with no model id."""
        _seed()

        assert not ModelConnection.objects.exists()
        assert not RoleBinding.objects.exists()

    @override_settings(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)
    def test_an_unseeded_role_is_genuinely_unresolvable(self):
        """The point of seeding nothing: `resolve()` raises rather than
        answering with a model nobody chose."""
        _seed()

        with pytest.raises(ValueError, match="rag.answer"):
            resolve("rag.answer")


@pytest.mark.django_db
class TestSeedsExactlyWhatWasPinned:
    """An operator who DID set the variables keeps their setup across the
    upgrade -- the original reason this migration exists."""

    @pytest.fixture(autouse=True)
    def _clear_seeded_rows(self):
        clear_seeded_rows()

    @override_settings(
        LLM_MODEL="operator-chat-choice",
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=1024,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_seeds_connections_and_bindings_from_the_explicit_values(self):
        _seed()

        llm = ModelConnection.objects.get(name="env: LLM_MODEL")
        embed = ModelConnection.objects.get(name="env: EMBED_MODEL")
        assert llm.model_id == "operator-chat-choice"
        assert llm.capabilities == ["chat"]
        assert llm.endpoint == ENDPOINT
        assert embed.model_id == "operator-embed-choice"
        assert embed.capabilities == ["embeddings"]
        assert embed.embed_dim == 1024
        assert embed.endpoint == ENDPOINT

        assert RoleBinding.objects.get(role_key="rag.answer").connection == llm
        assert RoleBinding.objects.get(role_key="rag.embed").connection == embed

    @override_settings(
        LLM_MODEL="operator-chat-choice",
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=1024,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_resolution_routes_through_db_provider_not_the_env_fallback(self):
        """Once seeded, `resolve()` answers from the DB rows -- the whole
        reason the seeding exists (otherwise `db_provider` would answer from
        empty tables and the operator's setup would vanish on upgrade)."""
        _seed()

        assert resolve("rag.answer").model_id == "operator-chat-choice"
        assert resolve("rag.embed").model_id == "operator-embed-choice"
        assert resolve("rag.embed").embed_dim == 1024

    @override_settings(
        LLM_MODEL="operator-chat-choice",
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=1024,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_migration_forward_is_idempotent(self):
        # Re-running the seed against rows that already exist must not
        # raise (unique constraints) or duplicate rows.
        _seed()
        _seed()

        assert ModelConnection.objects.filter(name="env: LLM_MODEL").count() == 1
        assert ModelConnection.objects.filter(name="env: EMBED_MODEL").count() == 1
        assert RoleBinding.objects.filter(role_key="rag.answer").count() == 1
        assert RoleBinding.objects.filter(role_key="rag.embed").count() == 1

    @override_settings(
        LLM_MODEL="operator-chat-choice",
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=1024,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_reverse_removes_only_the_two_role_bindings(self):
        _seed()

        seed_module.remove_seeded_bindings(apps, schema_editor=None)

        assert not RoleBinding.objects.filter(
            role_key__in=["rag.answer", "rag.embed"]
        ).exists()
        # Conservative reverse: the connections stay (an operator may have
        # since pointed other bindings at them).
        assert ModelConnection.objects.filter(name="env: LLM_MODEL").exists()
        assert ModelConnection.objects.filter(name="env: EMBED_MODEL").exists()
