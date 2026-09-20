"""The image-generation module (spec §4.7; see tools/vision/README.md)."""
from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings

FEATURE = "vision"


class VisionConfig(AppConfig):
    """Registers the `vision.generate` role and the operations this app
    serves -- but ONLY while the "vision" feature is enabled (D9), so an
    operator who opted out sees no role in the console and no `/vision/`
    page."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tools.vision"
    label = "vision"

    def ready(self) -> None:
        """Register the `vision.generate` role, the operations this app
        serves, and the `vision.generate` job kind -- only while the
        feature is enabled (D9).

        Imports are local so app import stays light (no DB, no HTTP at
        startup), matching `tools/rag/apps.py` -- registration only
        stores the dotted-path strings below, it never imports
        `tools.vision.jobs` itself (see that module's docstring: its
        `planner`/`handler`/`summarizer` are resolved lazily, only when a
        job is actually enqueued/run). The operation is DEFINED in
        `models.contracts` and merely registered here, so `all_operations()`
        lists only what an enabled feature can actually run.
        """
        if FEATURE not in settings.FARABUNKER_FEATURES:
            return

        from models.contracts.jobkinds import JobKind, register_job_kind
        from models.contracts.operations import (
            EDIT, IMG2IMG, INPAINT, TXT2IMG, UPSCALE, register_operation,
        )
        from models.contracts.roles import VISION_GENERATE_ROLE, RoleSpec, register_role

        register_role(RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation"))
        register_operation(TXT2IMG)
        register_operation(IMG2IMG)
        register_operation(INPAINT)
        register_operation(UPSCALE)
        register_operation(EDIT)
        register_job_kind(
            JobKind(
                # The literal, not `tools.vision.jobs.JOB_KIND`: this
                # method imports no handler module by design (see the
                # docstring above), which is also why the paths below are
                # strings. `jobs.JOB_KIND` is the same string, and
                # `test_apps.py` asserts they agree.
                key="vision.generate",
                label="Generate an image",
                planner="tools.vision.jobs.plan_generate",
                handler="tools.vision.jobs.run_generate",
                summarizer="tools.vision.jobs.summarize_generate",
                default_priority=200,
            )
        )

        # Tool registration (spec section 4.3). AFTER the early feature
        # exit above and AFTER the five register_operation calls: one
        # gate, one behaviour -- with the flag off there is no vision
        # role, no operations, no job kind, and no tools. And
        # `build_generate_spec()` reads `all_operations()` to fill its
        # `operation` choices, so it must run after they are registered,
        # which is why it is a function and not a module constant.
        from agents.contracts.tools import register_tool
        from tools.vision.tools import VISION_OPERATIONS, build_generate_spec

        register_tool(VISION_OPERATIONS)
        register_tool(build_generate_spec())

        # The one owned table in this column, so `manage.py
        # adopt_open_rows` and `manage.py reassign_owner` can walk it
        # without `identity/` importing `tools` (import-law rule 4).
        # INSIDE the feature gate above: a box with "vision" off carries
        # no registration naming a model whose app registered nothing
        # else either.
        from identity.contracts.ownership import OwnedRows, register_owned_rows

        register_owned_rows(
            OwnedRows("vision.generationjob", "Generated images", "vision.GenerationJob"))
