"""Unit tests for the universal setup page (foundation/setup/views.py).

Engine-agnostic by construction: the assertions read the SAME registries the
page does, and one test registers a throwaway engine to prove a new adapter
appears with no change to the view or its template.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from foundation.setup import views
from models.contracts.engines import ENGINES
from models.contracts.engines.base import SetupGuide, SetupStep
from models.contracts.roles import RAG_TRANSCRIBE_ROLE, RoleSpec, all_roles
from models.contracts.roles import _ROLES as _ROLE_REGISTRY
from models.contracts.roles import register_role


def _guided_engine(name="stubsetup", healthy=True, capabilities=("chat",)):
    engine = MagicMock()
    engine.name = name
    engine.api_description = "a stub HTTP API"
    engine.library_url = "https://example.invalid/library"
    engine.install_cmd_template = "stub pull <model-name>"
    engine.well_known_ports = (9999,)
    engine.serves_capabilities = capabilities
    engine.is_healthy.return_value = healthy
    engine.setup_guide = SetupGuide(
        summary="A stub engine used only in tests.",
        platforms={"macos": (SetupStep("Install it", "Somehow.", "stub install"),)},
        network_note="Bind all interfaces.",
        models_note="Place your own model files.",
        verify_url_path="/stub-health",
    )
    return engine


def _bare_engine(name="bareengine"):
    engine = MagicMock(spec=["name", "api_description", "well_known_ports", "is_healthy", "install_cmd_template", "library_url"])
    engine.name = name
    engine.api_description = "a bare HTTP API"
    engine.install_cmd_template = "bare pull <model-name>"
    engine.library_url = ""
    engine.well_known_ports = (8888,)
    engine.is_healthy.return_value = False
    return engine


@pytest.mark.django_db
class TestSetupPageStructure:
    def test_it_answers_and_marks_itself_current_in_the_settings_sidebar(self):
        """"Install guides" since UI-2, and it marks its SIDEBAR entry:
        this page moved into the settings area, so the app bar's own
        `Settings` entry is marked once by the settings shell for every
        page in it."""
        body = Client().get(reverse("setup-index")).content.decode()
        assert f'<a href="{reverse("setup-index")}" class="current">Install guides</a>' in body

    def test_it_explains_the_register_then_bind_flow_and_links_to_the_console(self):
        body = Client().get(reverse("setup-index")).content.decode()
        assert "How models reach farabunker" in body
        assert reverse("inference-console") in body

    def test_every_registered_engine_gets_its_own_anchored_section(self):
        body = Client().get(reverse("setup-index")).content.decode()
        for engine in ENGINES.values():
            assert f'id="engine-{engine.name}"' in body

    def test_every_registered_role_appears_in_the_needs_table(self):
        body = Client().get(reverse("setup-index")).content.decode()
        for role in all_roles():
            assert role.label in body
            assert role.capability in body


@pytest.mark.django_db
class TestAccountsLink:
    """T9 review round 1 IMPORTANT: the posture page
    (`/identity/settings/`) was linked from nowhere. Setup is the one
    PUBLIC, operator-facing page every visitor reaches before anything
    else, so a box in the open posture -- the shipped default -- offers
    the discovery path for turning accounts on here."""

    def test_it_offers_turn_accounts_on_in_the_open_posture(self):
        from identity.contracts.postures import POSTURE_OPEN
        from foundation.setup.tests._helpers import posture, seed_sweep_posture

        seed_sweep_posture()
        with posture(POSTURE_OPEN):
            response = Client().get(reverse("setup-index"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Turn accounts on" in body
        assert reverse("identity-settings") in body

    def test_it_says_nothing_once_accounts_are_on(self):
        """Once a posture has been chosen, the ongoing settings surface
        belongs to the shared nav's admin-only Settings link, not to
        the one-time discovery sentence on Setup."""
        from identity.contracts.postures import POSTURE_ENTERPRISE
        from foundation.setup.tests._helpers import (
            make_admin, posture, seed_sweep_posture, sign_in,
        )

        seed_sweep_posture()
        with posture(POSTURE_ENTERPRISE):
            client = Client()
            sign_in(client, make_admin())
            response = client.get(reverse("setup-index"))
        assert response.status_code == 200
        assert "Turn accounts on" not in response.content.decode()


@pytest.mark.django_db
class TestInventoryGatedOnAdmin:
    """Final fix wave, IMPORTANT 1: the setup page is PUBLIC (install
    guidance is public), but the endpoint line, the reachability probe,
    and the "What each feature needs" bindings table are box inventory --
    gated on `is_admin` inside the view so an anonymous caller on a box
    with accounts cannot read them, and so the probe never fires for one
    (no anonymous amplification)."""

    def test_anonymous_in_personal_posture_sees_guidance_not_inventory(self):
        from identity.contracts.postures import POSTURE_PERSONAL
        from foundation.setup.tests._helpers import posture, seed_sweep_posture

        engine = _guided_engine()
        seed_sweep_posture()
        with posture(POSTURE_PERSONAL), patch.dict(
            ENGINES, {engine.name: engine}, clear=True
        ), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            response = Client().get(reverse("setup-index"))
        assert response.status_code == 200
        body = response.content.decode()
        # Install guidance: still there.
        assert "A stub engine used only in tests." in body
        assert "stub install" in body
        # Inventory: withheld.
        assert "http://stub:9999" not in body
        assert "Verify:" not in body
        assert "Reachable" not in body and "Not reachable" not in body
        assert "What each feature needs" not in body
        engine.is_healthy.assert_not_called()

    def test_admin_in_personal_posture_sees_everything(self):
        from identity.contracts.postures import POSTURE_PERSONAL
        from foundation.setup.tests._helpers import (
            make_admin, posture, seed_sweep_posture, sign_in,
        )

        engine = _guided_engine()
        seed_sweep_posture()
        with posture(POSTURE_PERSONAL), patch.dict(
            ENGINES, {engine.name: engine}, clear=True
        ), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            client = Client()
            sign_in(client, make_admin())
            body = client.get(reverse("setup-index")).content.decode()
        assert "http://stub:9999/stub-health" in body
        assert "Verify:" in body
        assert "What each feature needs" in body

    def test_open_posture_renders_the_same_markup_as_before(self):
        """The open box's page must not change: `is_admin` is True at
        zero extra query cost in open posture, so this asserts the
        inventory is present exactly as it always was."""
        from identity.contracts.postures import POSTURE_OPEN
        from foundation.setup.tests._helpers import posture, seed_sweep_posture

        engine = _guided_engine()
        seed_sweep_posture()
        with posture(POSTURE_OPEN), patch.dict(
            ENGINES, {engine.name: engine}, clear=True
        ), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            body = Client().get(reverse("setup-index")).content.decode()
        assert "http://stub:9999/stub-health" in body
        assert "Verify:" in body
        assert "What each feature needs" in body
        engine.is_healthy.assert_called_once_with("http://stub:9999")

    def test_a_member_in_personal_posture_also_sees_no_inventory(self):
        """Not just anonymous -- a signed-in NON-admin member gets the
        same install-only page, since `is_admin` (not "signed in") is
        the gate."""
        from identity.contracts.postures import POSTURE_PERSONAL
        from foundation.setup.tests._helpers import (
            make_user, posture, seed_sweep_posture, sign_in,
        )

        engine = _guided_engine()
        seed_sweep_posture()
        with posture(POSTURE_PERSONAL), patch.dict(
            ENGINES, {engine.name: engine}, clear=True
        ), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            client = Client()
            sign_in(client, make_user())
            body = client.get(reverse("setup-index")).content.decode()
        assert "http://stub:9999" not in body
        assert "What each feature needs" not in body


@pytest.mark.django_db
class TestEngineSections:
    def test_a_guided_engine_renders_its_own_steps_notes_and_verify_url(self):
        engine = _guided_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            body = Client().get(reverse("setup-index")).content.decode()

        assert "A stub engine used only in tests." in body
        assert "stub install" in body
        assert "Bind all interfaces." in body
        assert "Place your own model files." in body
        assert "http://stub:9999/stub-health" in body

    def test_health_is_reported_honestly(self):
        for healthy, expected in ((True, "Reachable"), (False, "Not reachable")):
            engine = _guided_engine(healthy=healthy)
            with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
                "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
                {"stubsetup": "http://stub:9999"},
                clear=True,
            ):
                body = Client().get(reverse("setup-index")).content.decode()
            assert expected in body

    def test_a_health_check_that_blows_up_reads_as_not_reachable(self):
        engine = _guided_engine()
        engine.is_healthy.side_effect = RuntimeError("boom")
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            response = Client().get(reverse("setup-index"))

        assert response.status_code == 200
        assert "Not reachable" in response.content.decode()

    def test_an_engine_with_no_default_endpoint_is_not_checked(self):
        engine = _guided_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS", {}, clear=True
        ):
            body = Client().get(reverse("setup-index")).content.decode()

        engine.is_healthy.assert_not_called()
        assert "not checked" in body

    def test_an_engine_without_a_guide_still_gets_a_minimal_card(self):
        engine = _bare_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            body = Client().get(reverse("setup-index")).content.decode()

        assert 'id="engine-bareengine"' in body
        assert "a bare HTTP API" in body
        assert "bare pull &lt;model-name&gt;" in body
        assert "8888" in body

    def test_platform_sections_are_ordered_with_macos_open(self):
        engine = _guided_engine()
        engine.setup_guide = SetupGuide(
            summary="s",
            platforms={
                "linux": (SetupStep("L", "", "l"),),
                "macos": (SetupStep("M", "", "m"),),
                "windows": (SetupStep("W", "", "w"),),
            },
            network_note="n",
            models_note="mm",
            verify_url_path="/h",
        )
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            platforms = views._platform_views(engine.setup_guide)

        assert [p["key"] for p in platforms] == ["macos", "windows", "linux"]
        assert [p["label"] for p in platforms] == ["macOS", "Windows", "Linux"]
        assert platforms[0]["open"] is True
        assert platforms[1]["open"] is False


@pytest.mark.django_db
class TestNeedsTable:
    def test_it_names_the_engines_that_can_serve_each_capability(self):
        engine = _guided_engine(capabilities=("image-generation",))
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            rows = views._role_views(is_admin=True)

        by_capability = {row["capability"]: row for row in rows}
        assert by_capability["image-generation"]["engines"] == ["stubsetup"]
        assert by_capability["chat"]["engines"] == []

    def test_an_unbound_role_reads_as_not_yet_assigned(self):
        from models.registry.models import ModelConnection, RoleBinding

        RoleBinding.objects.all().delete()
        ModelConnection.objects.all().delete()

        assert all(row["bound"] is False for row in views._role_views(is_admin=True))

    def test_a_bound_role_reads_as_assigned(self):
        from models.registry.models import ModelConnection, RoleBinding

        RoleBinding.objects.all().delete()
        ModelConnection.objects.all().delete()
        connection = ModelConnection.objects.create(
            name="c", engine="comfyui", endpoint="http://x:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key="vision.generate", connection=connection)

        rows = {row["capability"]: row for row in views._role_views(is_admin=True)}
        assert rows["image-generation"]["bound"] is True


@pytest.mark.django_db
class TestWhisperAdapterAppearsUnprompted:
    """One real-adapter assertion (media-into-RAG plan T6): a newly
    registered adapter (`WhisperEngine`) needs no template/view change to
    appear here, the same guarantee `TestSetupPageStructure.
    test_every_registered_engine_gets_its_own_anchored_section` already
    covers generically -- this pins it concretely for whisper, plus the
    "Served by" column, which needs the "rag.transcribe" role registered
    (normally gated behind the "media" feature flag -- see
    `tools.rag.tests.test_apps.TestMediaRoleRegistration`) to have
    anything to show at all.
    """

    def test_whisper_section_renders_and_is_named_as_serving_transcription(self):
        saved_roles = dict(_ROLE_REGISTRY)
        register_role(RoleSpec(RAG_TRANSCRIBE_ROLE, "RAG transcription", "transcription"))
        try:
            body = Client().get(reverse("setup-index")).content.decode()
            rows = {row["capability"]: row for row in views._role_views(is_admin=True)}
        finally:
            _ROLE_REGISTRY.clear()
            _ROLE_REGISTRY.update(saved_roles)

        assert 'id="engine-whisper"' in body
        assert "whisper-server -m" in body
        assert rows["transcription"]["engines"] == ["whisper"]


@pytest.mark.django_db
class TestStatusColoursUseTheSharedTokens:
    def test_the_setup_page_reads_the_status_colours_from_tokens(self):
        """`setup/index.html` extends `_shell.html`, whose inline `<style>`
        carries the token declarations -- so each hex must appear exactly
        once (that declaration) and never as a property value here."""
        body = Client().get(reverse("setup-index")).content.decode()

        assert body.count("#1b7f4f") == 1
        assert body.count("#b3261e") == 1
        assert "color: var(--ok)" in body
        assert "color: var(--danger)" in body
