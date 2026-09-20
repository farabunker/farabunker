"""Unit tests for tools/vision/apps.py -- feature-gated registration (D9)."""
from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.test import override_settings

from models.contracts import operations as operations_module
from models.contracts import roles as roles_module
from models.contracts.jobkinds import get_job_kind
from models.contracts.operations import TXT2IMG, all_operations, get_operation
from models.contracts.roles import VISION_GENERATE_ROLE, get_role
# Imported for pytest to discover as a fixture (no conftest.py in this
# repo) -- requested explicitly below via `usefixtures`, per test.
from tools.vision.tests._helpers import isolated_tool_registry  # noqa: F401


def _run_ready_with(features):
    """Call `VisionConfig.ready()` under a feature setting, with the two
    registries it touches directly saved and restored -- app startup
    already registered the real entries and the rest of the suite depends
    on them.

    `ready()` also calls `tools.vision.tools.build_generate_spec()` (T9),
    which reads `all_operations()` WHILE `_OPERATIONS` is mid-reorder
    below (popping `TXT2IMG.key` and letting `ready()` re-`register_
    operation` it moves it to the END for the DURATION of this call -- a
    dict's re-inserted key is a NEW entry, not a restored position) and
    bakes that into a FROZEN `ToolSpec.params` tuple registered into
    `agents.contracts.tools._TOOLS` -- a THIRD registry, which this
    function does not touch itself. Every caller below requests the
    `isolated_tool_registry` fixture (`tools/vision/tests/_helpers.py`,
    imported by name -- the repo forbids `conftest.py` anywhere) instead:
    without it, `_TOOLS` would outlive this call, and `test_tools.py`'s own
    `get_tool("vision.generate")` (a DIFFERENT file, run in the SAME
    process) would read a param order this file's own probe corrupted --
    the collection-order-dependent failure the repo's two-order pytest
    matrix exists to catch.
    """
    saved_roles = dict(roles_module._ROLES)
    saved_operations = dict(operations_module._OPERATIONS)
    roles_module._ROLES.pop(VISION_GENERATE_ROLE, None)
    operations_module._OPERATIONS.pop(TXT2IMG.key, None)
    try:
        with override_settings(FARABUNKER_FEATURES=features):
            django_apps.get_app_config("vision").ready()
        return get_role(VISION_GENERATE_ROLE), get_operation(TXT2IMG.key)
    finally:
        roles_module._ROLES.clear()
        roles_module._ROLES.update(saved_roles)
        operations_module._OPERATIONS.clear()
        operations_module._OPERATIONS.update(saved_operations)


class TestVisionRegistration:
    @pytest.mark.usefixtures("isolated_tool_registry")
    def test_role_and_operation_registered_when_the_feature_is_on(self):
        role, operation = _run_ready_with(frozenset({"vision"}))

        assert role is not None
        assert role.key == "vision.generate"
        assert role.label == "Image generation"
        assert role.capability == "image-generation"
        assert role.rematerialize is None
        assert operation is TXT2IMG

    @pytest.mark.usefixtures("isolated_tool_registry")
    def test_nothing_is_registered_when_the_feature_is_off(self):
        role, operation = _run_ready_with(frozenset())

        assert role is None
        assert operation is None

    def test_the_real_startup_registered_them(self):
        """The running app registers at import time -- proof the wiring is
        live, not just callable."""
        assert get_role(VISION_GENERATE_ROLE) is not None
        assert get_operation("txt2img") is TXT2IMG

    def test_every_shipped_operation_is_registered_behind_the_feature_flag(self):
        assert {operation.key for operation in all_operations()} == {
            "txt2img", "img2img", "inpaint", "upscale", "edit",
        }

    def test_the_registered_key_and_the_modules_constant_agree(self):
        from tools.vision.jobs import JOB_KIND

        assert JOB_KIND == "vision.generate"
        assert get_job_kind(JOB_KIND).handler == "tools.vision.jobs.run_generate"
