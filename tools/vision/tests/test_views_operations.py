"""Unit tests for `GET /vision/operations/` -- the schema-discovery endpoint
(ADR 0012's payload-contract section): `services.operation_catalog()` as
JSON, for an agent caller or the upcoming UI to read before it submits.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from models.registry.models import ModelConnection
from models.contracts.engines import ENGINES
from tools.vision.tests._helpers import StubEngine, clear_bindings


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


@pytest.mark.django_db
class TestVisionOperations:
    def test_it_returns_the_catalog_wrapped_under_operations(self):
        client = Client()
        response = client.get(reverse("vision-operations"))

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        body = json.loads(response.content)
        assert set(body.keys()) == {"connection", "operations"}
        assert isinstance(body["operations"], list)
        assert body["operations"]

    def test_every_entry_carries_key_label_and_params(self):
        client = Client()
        response = client.get(reverse("vision-operations"))
        body = json.loads(response.content)

        for entry in body["operations"]:
            assert "key" in entry
            assert "label" in entry
            assert "params" in entry
            assert isinstance(entry["params"], list)

    def test_the_body_is_json_safe(self):
        """`json.loads` above already proves it decodes; this asserts the
        endpoint's `operations` value is exactly `services.
        operation_catalog()` -- no view-layer reshaping that could silently
        drift from the seam `tools.vision.services.operation_catalog`
        documents. `connection` is this view's own addition (final-review
        finding 3), not part of that seam."""
        from tools.vision import services

        client = Client()
        response = client.get(reverse("vision-operations"))
        body = json.loads(response.content)

        assert body["operations"] == services.operation_catalog()
        # Nothing bound, nothing picked: an honestly empty identity.
        assert body["connection"] == {"id": None, "name": None}

    def test_unbound_role_is_still_200(self):
        """No 503: an unbound role is a legitimate state the catalog
        already degrades for (empty option lists, same shape) -- this
        endpoint must not invent a new failure mode on top of it."""
        client = Client()
        response = client.get(reverse("vision-operations"))

        assert response.status_code == 200

    def test_the_catalog_lists_every_registered_mode_supported_or_not(self):
        """Contract change (ADR 0012 D-EDIT-13): the array is no longer
        "modes I can run" -- it is every registered mode, and a caller
        filters on `supported`. Saying WHY a mode is unavailable is more
        useful than silence."""
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = Client().get(
                reverse("vision-operations"), {"connection": str(connection.pk)}
            )

        body = response.json()
        entries = {entry["key"]: entry for entry in body["operations"]}
        assert set(entries) == {"txt2img", "img2img", "inpaint", "upscale", "edit"}
        assert entries["edit"]["supported"] is True
        assert entries["edit"]["unsupported_reason"] == ""
        assert entries["inpaint"]["supported"] is False
        assert entries["inpaint"]["unsupported_reason"] == (
            "No inpaint graph for the flux2 family."
        )
        assert body["connection"] == {"id": connection.pk, "name": "picked"}

    def test_every_param_carries_an_ignored_key_null_when_honoured(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="flux2.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        # The stub is MANDATORY, not a convenience: `operation_catalog`
        # calls `live_options` for EVERY operation whatever `preflight`
        # said (there is no readiness guard on that path), so a real
        # `comfyui` endpoint here would mean roughly ten httpx attempts at
        # `DISCOVERY_TIMEOUT` (5 s) each. Same `clear=True` idiom
        # `TestLiveDefaults` uses.
        class _Flux2(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("txt2img", "edit")

            def ignored_params(self, operation_key, config=None):
                if operation_key != "txt2img":
                    return {}
                return {
                    "negative_prompt": "This model has no negative-prompt input.",
                    "scheduler": (
                        "This model's own noise schedule has no separate "
                        "setting to choose."
                    ),
                }

            def list_choices(self, endpoint, key):
                return ()

        with patch.dict(ENGINES, {"stubengine": _Flux2()}, clear=True):
            response = Client().get(
                reverse("vision-operations"), {"connection": str(connection.pk)}
            )

        txt2img = next(
            entry for entry in response.json()["operations"] if entry["key"] == "txt2img"
        )
        ignored = {param["key"]: param["ignored"] for param in txt2img["params"]}
        assert set(ignored) >= {"prompt", "negative_prompt", "scheduler"}
        assert ignored["prompt"] is None
        assert ignored["negative_prompt"]
        assert "no separate setting to choose" in ignored["scheduler"]

    def test_an_unbound_catalog_still_carries_both_new_keys(self):
        """Nothing bound is "no opinion", not "supports nothing": every
        entry is supported, every reason blank, every `ignored` null."""
        body = Client().get(reverse("vision-operations")).json()

        for entry in body["operations"]:
            assert entry["supported"] is True
            assert entry["unsupported_reason"] == ""
            assert all(param["ignored"] is None for param in entry["params"])

    def test_the_role_binding_is_echoed_when_nothing_is_picked(self):
        """The `connection` field must name whichever connection actually
        answered -- the role binding, when nothing was explicitly picked --
        not just describe the picker's own state."""
        from models.registry.models import RoleBinding
        from models.contracts.roles import VISION_GENERATE_ROLE

        connection = ModelConnection.objects.create(
            name="bound conn", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key=VISION_GENERATE_ROLE, defaults={"connection": connection}
        )

        client = Client()
        response = client.get(reverse("vision-operations"))

        assert response.json()["connection"] == {"id": connection.pk, "name": "bound conn"}

    def test_an_unusable_connection_is_a_404_with_a_json_error(self):
        """Final-review finding 3: this used to fall back to the role
        binding's catalog in silence -- an agent that named a pk explicitly
        would get an ANSWER for a different model with no sign anything
        was wrong. A pk that no longer names a usable image-generation
        connection is now refused outright, the same honesty the create
        page's own POST path (`_picker_failure_response`) already gives an
        operator who picks a model that is gone."""
        client = Client()
        response = client.get(reverse("vision-operations"), {"connection": "999999"})

        assert response.status_code == 404
        assert response["Content-Type"] == "application/json"
        body = response.json()
        assert "error" in body
        assert "no longer registered" in body["error"]


@pytest.mark.django_db
class TestPickedConnection:
    def test_an_unresolvable_pk_is_reported_unusable(self):
        """Pins the contract: `usable` is `False`, not just `resolved is
        None` -- the one bit that will let Task 11's POST path tell a
        stale/deleted pick apart from 'nothing picked at all'."""
        from tools.vision import views

        request = RequestFactory().get("/vision/operations/", {"connection": "999999"})
        assert views.picked_connection(request) == (None, "999999", False)
