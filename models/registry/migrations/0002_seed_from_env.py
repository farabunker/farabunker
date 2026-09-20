"""Seed `ModelConnection`/`RoleBinding` rows from an operator's EXPLICIT env
overrides at migrate time.

`INFERENCE_BINDING_PROVIDER` defaults to `models.registry.bindings.db_provider`
(see that module's docstring), so an existing install that pinned its models
from the environment must keep resolving to the same LLM/embed models after
upgrading -- otherwise `resolve()` would suddenly answer from empty DB tables
instead of `LLM_MODEL`/`EMBED_MODEL`. This migration seeds the DB from those
same env-derived settings once, so resolution flows through `db_provider`
from here on rather than falling back to `env_provider`.

NOTHING is seeded for a role whose env override is unset (owner ruling, ADR
0010 third amendment: there are no baked model defaults, so an unset variable
is not a model choice waiting to be materialized -- it is the absence of one).
A fresh install therefore migrates into a genuinely empty registry, and the
console's cold-start onboarding -- register a model, then assign it to a role
-- is the real first-run path. Each half is independent: an operator who
pinned only `LLM_MODEL` gets only the chat connection and its binding.

Idempotent forward (`get_or_create`, keyed the same way the models themselves
are made unique -- CI on `name`/`role_key`): running `migrate` again after
rows already exist, or after an operator has since edited them, is a no-op
for existing rows. Reverse is conservative -- it removes only the two role
bindings this migration can create, leaving the `ModelConnection` rows in
place (an operator may have since pointed other bindings at them), mirroring
the irreversible-`noop` idiom in `tools/rag/migrations/0006_*` for the case
where the safe partial-undo still can't fully restore pre-migration state.
"""
from __future__ import annotations

from django.conf import settings
from django.db import migrations

LLM_CONNECTION_NAME = "env: LLM_MODEL"
EMBED_CONNECTION_NAME = "env: EMBED_MODEL"


def seed_from_env(apps, schema_editor):
    ModelConnection = apps.get_model("inference", "ModelConnection")
    RoleBinding = apps.get_model("inference", "RoleBinding")

    if settings.LLM_MODEL:
        llm_connection, _ = ModelConnection.objects.get_or_create(
            name=LLM_CONNECTION_NAME,
            defaults={
                "engine": "ollama",
                "endpoint": settings.OLLAMA_BASE_URL,
                "model_id": settings.LLM_MODEL,
                "capabilities": ["chat"],
            },
        )
        RoleBinding.objects.get_or_create(
            role_key="rag.answer", defaults={"connection": llm_connection}
        )

    if settings.EMBED_MODEL:
        embed_connection, _ = ModelConnection.objects.get_or_create(
            name=EMBED_CONNECTION_NAME,
            defaults={
                "engine": "ollama",
                "endpoint": settings.OLLAMA_BASE_URL,
                "model_id": settings.EMBED_MODEL,
                "capabilities": ["embeddings"],
                "embed_dim": settings.EMBED_DIM,
            },
        )
        RoleBinding.objects.get_or_create(
            role_key="rag.embed", defaults={"connection": embed_connection}
        )


def remove_seeded_bindings(apps, schema_editor):
    # Conservative reverse: drop only the two RoleBinding rows this migration
    # can have created, keyed by role_key the same way forward's
    # get_or_create is. The ModelConnection rows are left in place -- an
    # operator may have since re-pointed other bindings at them, and
    # deleting a connection un-binds any role via SET_NULL rather than
    # raising, so there is no safety reason to remove them too.
    RoleBinding = apps.get_model("inference", "RoleBinding")
    RoleBinding.objects.filter(role_key__in=["rag.answer", "rag.embed"]).delete()


class Migration(migrations.Migration):

    dependencies = [("inference", "0001_initial")]

    operations = [migrations.RunPython(seed_from_env, remove_seeded_bindings)]
