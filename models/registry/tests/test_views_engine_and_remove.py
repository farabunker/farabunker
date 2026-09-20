"""The Capability checkbox group across all three form locations, the
create form's context-window label scoping, the supported-engines boundary
and engine dropdowns, engine-profile endpoint autofill, model-server
terminology, `connection_remove` and its per-role consequence copy,
`_default_endpoint`'s three-tier fallback, and the machine table's row
layout.

One of six files `models/registry/tests/test_views.py` split into (C-56):
`test_views_console_and_roles.py`, `test_views_reencode_and_sections.py`,
`test_views_machine_add_and_dropdowns.py`, `test_views_connection_edit.py`,
`test_views_engine_and_remove.py`, `test_views_tables_and_picker.py`. The
original was 7,704 lines and 43 classes; `tools/vision/tests/` is the
in-repo precedent for splitting one `views.py` module's tests by feature
area. Cut at class boundaries, on the file's own `# ---` dividers, with
no assertion changed.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from django.test import override_settings
from django.urls import reverse

from identity.contracts import actions
from identity.models import AuditEvent
from models.registry import views as views_module
from models.registry.models import Materialization, ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    CHAT_ENV_OVERRIDE,
    ENDPOINT,
    NO_ENV_OVERRIDE,
    _create_form_engine_options,
    _installed_row,
    _mock_engine,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
    make_chat_connection,
)
from models.contracts.bindings import ResolvedModel
from models.contracts.roles import CAPABILITIES


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Clear the ModelConnection/RoleBinding rows migration 0002 seeds from
    env settings, so each test starts from a known-empty registry."""
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """`probe_cache` (C-07) carries NO `connection.in_atomic_block` guard
    -- deliberately, an HTTP probe has no transaction to roll back -- so
    unlike `models.registry.availability`'s cache it is NOT automatically
    inert inside this file's `django_db` tests. Every test in this module
    shares the same `ENDPOINT` constant and mocks `ENGINES`/`discover`
    with a different answer per test; without this, a cached `True` from
    one test's healthy mock would silently answer the next test's
    unhealthy one. Invalidated before AND after so it can never leak past
    this module either."""
    clean_probe_cache()
    yield
    clean_probe_cache()


# --- T9: the Capability field is a checkbox group in all three form locations -


@pytest.mark.django_db
class TestCapabilityCheckboxGroups:
    """`_connection_edit.html` and BOTH `console.html` manual-form copies
    (cold-start and warm) render Capability as a checkbox GROUP -- one
    `<input type="checkbox" name="capability" value="...">` per platform
    capability -- instead of the old single-select `<select>`, which could
    only ever submit one value (THE silent-loss bug this task fixes)."""

    def _checkbox_inputs(self, html: str) -> list[str]:
        return re.findall(r'<input type="checkbox" name="capability" value="([^"]+)"', html)

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_warm_manual_form_renders_a_checkbox_per_capability(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is False
        body = response.content.decode()
        assert '<fieldset class="capability-group">' in body
        form_start = body.index('class="inline-form add-connection"')
        form_end = body.index("</form>", form_start)
        add_form = body[form_start:form_end]
        assert set(self._checkbox_inputs(add_form)) == set(sorted(CAPABILITIES))
        # A CREATE form: nothing is pre-checked (the legend's own "leave
        # every box unchecked" copy is not itself a checked box).
        assert not re.search(r'<input type="checkbox"[^>]*\schecked', add_form)
        assert "<select name=\"capability\">" not in add_form

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_manual_form_renders_a_checkbox_per_capability(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is True
        body = response.content.decode()
        form_start = body.index('class="inline-form add-connection"')
        form_end = body.index("</form>", form_start)
        add_form = body[form_start:form_end]
        assert set(self._checkbox_inputs(add_form)) == set(sorted(CAPABILITIES))
        assert "checked" not in add_form

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_edit_form_checks_every_one_of_a_connections_capabilities(
        self, mock_engines, mock_discover, client
    ):
        """The round-trip half of THE bug fix: a two-capability connection's
        edit form must show BOTH boxes checked, not just its first/primary
        one -- otherwise an operator saving any unrelated field would see
        (and silently keep) only one."""
        connection = ModelConnection.objects.create(
            name="vlm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llava:13b", capabilities=["chat", "vision"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        form_start = body.index(f'id="conn-{connection.pk}"')
        form_end = body.index("</form>", form_start)
        edit_form = body[form_start:form_end]
        assert 'name="capability" value="chat" checked' in edit_form
        assert 'name="capability" value="vision" checked' in edit_form
        assert 'name="capability" value="embeddings" checked' not in edit_form
        assert "<select name=\"capability\">" not in edit_form


# --- Context window label matches the edit form's chat-models-only scoping ----


@pytest.mark.django_db
class TestCreateFormContextWindowLabelScoping:
    """The edit form's Context window label (`_connection_edit.html`) reads
    "(tokens, optional — chat models only)" -- the two CREATE forms (warm
    "Add a connection manually" and the cold-start variant) must say the
    same thing, not the field's old unscoped "(tokens, optional)" copy.
    The field itself stays visible either way (owner's nothing-hidden
    ruling) -- this only pins the label text."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_warm_create_form_scopes_context_window_to_chat_models(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "Context window (tokens, optional — chat models only)" in body
        assert "Context window (tokens, optional)<input" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_create_form_scopes_context_window_to_chat_models(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is True
        body = response.content.decode()
        assert "Context window (tokens, optional — chat models only)" in body
        assert "Context window (tokens, optional)<input" not in body


# --- The support boundary is the engine adapter -------------------------------


class TestSupportedEnginesHelper:
    """`_supported_engines()` derives its whole listing from the registry
    itself -- name, one-phrase API description, the adapter's own module
    file path, and its well-known ports -- with nothing about a specific
    engine hardcoded in the console layer. A pure function, no Django
    client/DB needed; exercised against the REAL `models.contracts.engines`
    registry."""

    def test_derives_name_description_path_and_ports_from_the_real_registry(self):
        from models.registry.views import _supported_engines

        entries = {entry["name"]: entry for entry in _supported_engines()}

        assert "ollama" in entries
        ollama = entries["ollama"]
        assert ollama["description"] == "the Ollama HTTP API"
        assert ollama["source_path"] == "models/contracts/engines/ollama.py"
        assert ollama["ports"] == "default port 11434"


@pytest.mark.django_db
class TestEngineDropdownsAndSupportedAPIs:
    """The create form and every per-connection edit form offer a
    `<select>` over registered engines, not free text -- the support
    boundary is the engine ADAPTER, so the form can only ever submit one
    that actually exists. The "Supported APIs" disclosure lists the same
    registry, one line per engine, entirely derived."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_engine_field_is_a_select_of_registered_engines(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert '<select name="engine">' in body
        assert '<option value="ollama"' in body
        # No more free-text engine input anywhere on the page.
        assert 'name="engine" value=' not in body
        assert 'type="text" name="engine"' not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_supported_apis_block_lists_name_description_and_port(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "Supported APIs" in body
        assert "<code>ollama</code>" in body
        assert "the Ollama HTTP API" in body
        assert "default port 11434" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_second_registered_engine_appears_automatically_with_no_template_change(
        self, mock_engines, mock_discover, client
    ):
        """Reuses test_discovery.py's stub-engine idiom (a fake
        `InferenceEngine` with just the fields the caller reads set): a
        second registered adapter must show up in BOTH the dropdown and
        the "Supported APIs" list without any template/view change --
        proof that nothing about "ollama" specifically is hardcoded in
        either place."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        second = MagicMock()
        second.name = "second-engine"
        second.is_healthy.return_value = False
        second.api_description = "a made-up test API"
        second.well_known_ports = (9999,)
        second.library_url = "https://example.test/library"
        second.install_cmd_template = "second-cli fetch <model-name>"
        mock_engines.values.return_value = [_mock_engine(healthy=True), second]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert '<option value="second-engine"' in body
        assert "a made-up test API" in body
        assert "default port 9999" in body
        # The second adapter brings its own "Getting models"
        # facts line -- library, usual address, install shape -- and its
        # dropdown option carries its own data-endpoint for the autofill.
        # Nothing about a specific engine is hardcoded anywhere.
        assert "https://example.test/library" in body
        assert "usually at <code>http://localhost:9999</code>" in body
        assert "second-cli fetch &lt;model-name&gt;" in body
        assert 'data-endpoint="http://localhost:9999"' in body

    def test_edit_form_preselects_the_connections_current_engine(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert '<option value="ollama" title="unittest.mock" data-endpoint="http://localhost:11434" selected>ollama</option>' in body

    def test_edit_form_shows_an_unregistered_stored_engine_as_its_own_selected_option(
        self, client
    ):
        """A connection whose stored engine names no
        registered adapter must not leave the <select> with nothing
        selected (which the browser would silently resolve to the first
        real option) -- it gets its own selected option, labeled "(not
        registered)", that still posts the exact stored value."""
        connection = ModelConnection.objects.create(
            name="legacy conn", engine="mistral-server", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert '<option value="mistral-server" selected>mistral-server (not registered)</option>' in body
        # This connection's own edit form must have exactly one selected
        # engine option (its stored, unregistered one) -- not also the
        # registered "ollama" entry alongside it in the SAME <select>.
        form_start = body.index(f'name="connection_id" value="{connection.id}"')
        select_start = body.index('<select name="engine">', form_start)
        select_end = body.index("</select>", select_start)
        engine_select_html = body[select_start:select_end]
        assert engine_select_html.count(" selected") == 1


# --- Engine-profile endpoint autofill -----------------------------------------


@pytest.mark.django_db
class TestEngineProfileAutofill:
    """Choosing a model server auto-defaults the endpoint: the create
    form's endpoint is server-rendered with the
    best available fact -- the page's live, health-checked endpoint when
    one resolved, else the DEFAULT engine's usual address (works with JS
    disabled either way) -- and every engine option carries
    `data-endpoint` for the progressive-enhancement swap. The distinct
    well-known port (9990) proves the profile values derive from the
    adapter itself, not from the page's endpoint."""

    def _get(self, client, engines):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = engines
            mock_discover.return_value = []
            return client.get(reverse("inference-console"))

    def _profiled_engine(self, name="ollama", port=9990):
        engine = _mock_engine(healthy=True)
        engine.name = name
        engine.well_known_ports = (port,)
        return engine

    def test_create_form_endpoint_prefills_the_pages_live_endpoint(self, client):
        """The page's resolved endpoint (here `OLLAMA_BASE_URL`'s default,
        http://localhost:11434 -- the one default the no-model-defaults
        ruling keeps, since a server location is deploy convention) is
        strictly more specific evidence than the adapter's well-known-port
        guess (9990) -- it wins."""
        response = self._get(client, [self._profiled_engine()])

        body = response.content.decode()
        assert f'name="endpoint" value="{ENDPOINT}"' in body
        assert 'name="endpoint" value="http://localhost:9990"' not in body

    def test_create_form_falls_back_to_the_engines_profile_url(self, client):
        """No resolvable page endpoint (defense-in-depth cold start): the
        default engine's own profile URL is the prefill."""
        with patch("models.registry.views._default_endpoint", return_value=""):
            response = self._get(client, [self._profiled_engine()])

        body = response.content.decode()
        assert 'name="endpoint" value="http://localhost:9990"' in body

    def test_every_engine_option_carries_its_own_data_endpoint(self, client):
        second = MagicMock()
        second.name = "second-engine"
        second.is_healthy.return_value = False
        second.api_description = "a made-up test API"
        second.well_known_ports = (9999,)
        second.library_url = ""
        second.install_cmd_template = ""
        response = self._get(client, [self._profiled_engine(), second])

        options = _create_form_engine_options(response.content.decode())
        assert 'data-endpoint="http://localhost:9990"' in options
        assert 'data-endpoint="http://localhost:9999"' in options

    def test_edit_form_options_carry_data_endpoint_but_value_stays_stored(self, client):
        connection = ModelConnection.objects.create(
            name="remote conn", engine="ollama", endpoint="http://elsewhere:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        response = self._get(client, [self._profiled_engine()])

        body = response.content.decode()
        form_start = body.index(f'name="connection_id" value="{connection.id}"')
        form_end = body.index("</form>", form_start)
        edit_form = body[form_start:form_end]
        assert 'data-endpoint="http://localhost:9990"' in edit_form
        # The stored endpoint is what renders (and posts) -- the autofill
        # is enhancement only and never rewrites the server-rendered value.
        assert 'name="endpoint" value="http://elsewhere:11434"' in edit_form

    def test_autofill_script_is_inline_and_guarded(self, client):
        """Offline UI: the enhancement ships as one inline script (no
        external assets) whose dirty check treats the server-rendered
        value as the clean baseline and an operator keystroke as the
        permanent opt-out."""
        response = self._get(client, [self._profiled_engine()])

        body = response.content.decode()
        assert "<script>" in body
        # Anchored on the autofill script's own distinctive marker (its
        # lead comment) rather than "the first <script> tag in the page":
        # the settings-assistant panel (`chat/_assistant_panel.html`) now
        # legitimately renders its own inline poll script on this page too
        # in open posture, ahead of this one in document order, so a naive
        # `body.split("<script>")[1]` no longer names this script.
        marker = "Engine-profile endpoint autofill"
        marker_at = body.index(marker)
        script_start = body.rindex("<script>", 0, marker_at) + len("<script>")
        script_end = body.index("</script>", marker_at)
        script = body[script_start:script_end]
        assert 'src="' not in script
        assert "data-endpoint" in script
        assert "pristine" in script
        assert '"input"' in script  # the keystroke listener that marks dirty

    def test_engine_dropdown_renders_even_with_a_single_engine(self, client):
        """User overrule: built to scale -- one registered engine still
        renders as a real <select>, never plain text."""
        response = self._get(client, [self._profiled_engine()])

        options = _create_form_engine_options(response.content.decode())
        assert "<option" in options

    def test_engine_options_are_exactly_the_registered_engines(self, client):
        """User-directed: the dropdown IS the support boundary -- its
        option set equals exactly the registered engine names. No
        placeholder, example, or coming-soon entries."""
        import re

        second = MagicMock()
        second.name = "second-engine"
        second.is_healthy.return_value = False
        second.api_description = "a made-up test API"
        second.well_known_ports = (9999,)
        second.library_url = ""
        second.install_cmd_template = ""
        response = self._get(client, [self._profiled_engine(), second])

        options = _create_form_engine_options(response.content.decode())
        values = re.findall(r'<option value="([^"]*)"', options)
        assert values == ["ollama", "second-engine"]


@pytest.mark.django_db
class TestModelServerTerminology:
    """"Model server" is the one human-facing
    term for the thing this console connects to; "engine" survives only as
    the internal/code term (form field names, option values) and the
    technical label in Supported APIs."""

    def _get(self, client):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            return client.get(reverse("inference-console"))

    def test_forms_label_the_engine_field_model_server(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        response = self._get(client)

        body = response.content.decode()
        assert "<label>Model server" in body
        assert "<label>Engine" not in body
        # The code term stays on the wire unchanged.
        assert '<select name="engine">' in body

    def test_getting_models_copy_says_model_server(self, client):
        response = self._get(client)

        body = response.content.decode()
        assert "model server" in body
        # The Supported APIs heading survives, reframed as model-server
        # APIs (warm page only; cold start has no Supported APIs block).
        assert "Getting models" in body


# --- connection_remove -- closing the pipeline's one-way door -----------------


@pytest.mark.django_db
class TestConnectionRemove:
    """`connection_remove` deletes a registered `ModelConnection`.
    `RoleBinding.connection` is `on_delete=SET_NULL` (models.py) -- that IS
    the DB behavior for "remove a connection roles use", surfaced rather
    than fought: unbound removes immediately, chat-bound removes
    immediately with a visible note, embeddings-bound reuses
    `role_assign`'s unbind confirm gate and first-materialization stamp."""

    def _remove(self, client, connection_id, **extra):
        return client.post(
            reverse("inference-connection-remove"),
            data={"connection_id": str(connection_id), **extra},
        )

    def test_get_is_not_allowed(self, client):
        response = client.get(reverse("inference-connection-remove"))
        assert response.status_code == 405

    def test_removes_unbound_connection_with_success_message(self, client):
        connection = ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._remove(client, connection.id)

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(pk=connection.id).exists()
        followed = client.get(response.url)
        assert "Removed connection “spare conn”" in followed.content.decode()

    def test_a_valid_removal_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): `target_key` is captured BEFORE
        `.delete()` clears the instance's own pk -- if it weren't, this
        would record `target_key=None` for every deletion."""
        connection = ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        connection_id = connection.id

        self._remove(client, connection.id)

        assert AuditEvent.objects.filter(action=actions.CONNECTION_DELETED).count() == 1
        event = AuditEvent.objects.get(action=actions.CONNECTION_DELETED)
        assert event.actor_kind == "open"
        assert event.target_key == str(connection_id)
        assert event.target_label == "spare conn"

    def test_a_removal_of_a_missing_connection_is_not_audited(self, client):
        self._remove(client, 999999)

        assert AuditEvent.objects.count() == 0

    @override_settings(**NO_ENV_OVERRIDE)
    def test_removes_chat_bound_connection_leaves_those_roles_unassigned(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._remove(client, connection.id)

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(pk=connection.id).exists()
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection is None  # SET_NULL -- the role is now unassigned
        followed = client.get(response.url)
        body = followed.content.decode()
        assert (
            "Removed connection “chat conn” — RAG answer is now unassigned: no model "
            "will back it and anything that needs it reports unavailable until you "
            "assign one." in body
        )

    @override_settings(**NO_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_chat_bound_row_renders_the_in_use_note_before_removal(
        self, mock_engines, mock_discover, client
    ):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert (
            "in use by RAG answer — removing leaves it with no model assigned"
            in body
        )
        # Chat is not drift-relevant -- no confirm checkbox on the remove form.
        assert "confirm-remove" not in body

    def test_embeddings_bound_without_confirm_refuses_nothing_deleted(self, client):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._remove(client, connection.id)

        assert response.status_code == 302
        assert ModelConnection.objects.filter(pk=connection.id).exists()
        assert not Materialization.objects.exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "full document store rebuild" in body  # conservative severity copy
        assert "confirm removal" in body

    def test_embeddings_bound_with_confirm_removes_and_stamps_pre_change_fingerprint(
        self, client
    ):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)
        assert not Materialization.objects.filter(role_key="rag.embed").exists()

        response = self._remove(client, connection.id, confirm="yes")

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(pk=connection.id).exists()
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection is None
        materialization = Materialization.objects.get(role_key="rag.embed")
        assert materialization.fingerprint == "ollama:nomic-embed-text:768"

    def test_confirmed_removal_never_overwrites_an_existing_stamp(self, client):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)
        Materialization.objects.create(
            role_key="rag.embed", fingerprint="ollama:some-other-model:512"
        )

        response = self._remove(client, connection.id, confirm="yes")

        assert response.status_code == 302
        assert Materialization.objects.filter(role_key="rag.embed").count() == 1
        assert (
            Materialization.objects.get(role_key="rag.embed").fingerprint
            == "ollama:some-other-model:512"
        )

    def test_garbage_connection_id_does_not_500(self, client):
        response = self._remove(client, "not-a-number")

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "no longer exists" in followed.content.decode()

    def test_missing_connection_id_does_not_500(self, client):
        response = client.post(reverse("inference-connection-remove"), data={})

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "no longer exists" in followed.content.decode()

    def test_nonexistent_connection_id_does_not_500(self, client):
        response = self._remove(client, 999999)

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "no longer exists" in followed.content.decode()

    def test_redirect_preserves_an_active_endpoint_override(self, client):
        # H8 (S5): the override must be an allowlisted host -- loopback here,
        # not an arbitrary address -- for `_requested_override` to honour it.
        override = "http://127.0.0.1:9999"
        connection = ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._remove(client, connection.id, endpoint_override=override)

        assert response.status_code == 302
        assert response.url == f"{reverse('inference-console')}?endpoint={quote(override)}"

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_full_circle_add_assign_unbind_remove_add_button_reappears(
        self, mock_engines, mock_discover, client
    ):
        """The whole pipeline, round-trip: found -> registered -> in use ->
        (unbind) registered, unbound -> removed -> found again (the Add
        button is back, exactly as if nothing had ever been registered)."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        # FOUND -> REGISTERED
        add_response = client.post(
            reverse("inference-machine-add"),
            data={
                "engine": "ollama", "model_id": "llama3.1:8b", "endpoint": ENDPOINT,
                "capability": "chat", "embed_dim": "",
            },
        )
        assert add_response.status_code == 302
        connection = ModelConnection.objects.get(model_id="llama3.1:8b")

        # REGISTERED -> IN USE
        client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": f"conn:{connection.pk}"},
        )
        assert RoleBinding.objects.get(role_key__iexact="rag.answer").connection == connection

        # IN USE -> back to REGISTERED (unbind)
        client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": "unbind"},
        )
        assert RoleBinding.objects.get(role_key__iexact="rag.answer").connection is None

        # REGISTERED -> gone (remove)
        remove_response = client.post(
            reverse("inference-connection-remove"),
            data={"connection_id": str(connection.pk)},
        )
        assert remove_response.status_code == 302
        assert not ModelConnection.objects.filter(pk=connection.pk).exists()

        # The machine row is FOUND again -- the Add button is back.
        final = client.get(reverse("inference-console"))
        body = final.content.decode()
        assert ">Add to registered<" in body
        assert "registered ✓" not in body


# --- connection_remove: per-role truthful consequence ------------------------


@pytest.mark.django_db
class TestConnectionRemoveConsequenceCopy:
    """Removing a connection clears every
    `RoleBinding` pointing at it (SET_NULL) -- but what BACKS each of those
    roles afterwards is not uniform, so the copy must not claim it is.

    A role with an explicit environment override falls straight onto it and
    keeps working; a role without one is left with no model at all -- the
    copy must not say "unassigned" for both, since that would render the
    flash "RAG answer are now unassigned" directly above that same role's
    "explicit environment override" badge. Both the pre-removal note and
    the post-removal message are branched per role, and a connection
    bound to roles with DIFFERENT outcomes names each one.

    Every state here is set EXPLICITLY (`override_settings`), never
    inherited from this machine's environment."""

    def _remove(self, client, connection_id, **extra):
        return client.post(
            reverse("inference-connection-remove"),
            data={"connection_id": str(connection_id), **extra},
        )

    def _chat_conn(self):
        return make_chat_connection()

    @override_settings(**CHAT_ENV_OVERRIDE)
    def test_removal_message_says_the_override_takes_over(self, client):
        connection = self._chat_conn()
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._remove(client, connection.id)

        assert response.status_code == 302
        assert RoleBinding.objects.get(role_key__iexact="rag.answer").connection is None
        followed = client.get(response.url)
        body = followed.content.decode()
        assert (
            "Removed connection “chat conn” — RAG answer is now unassigned: "
            "the explicit environment override for this role takes over." in body
        )
        # ...and the page does NOT simultaneously claim nothing backs it.
        assert "no model will back it" not in body
        role_views = {v["role"].key: v for v in followed.context["role_views"]}
        assert role_views["rag.answer"]["current"].model_id == "operator-chat-choice"

    @override_settings(**CHAT_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_pre_removal_note_says_the_override_takes_over(
        self, mock_engines, mock_discover, client
    ):
        connection = self._chat_conn()
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert (
            'title="in use by RAG answer — removing hands it to its explicit '
            'environment override"' in body
        )
        assert ">removing → environment override takes over<" in body
        assert "removing → unassigns those roles" not in body

    @override_settings(**NO_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_pre_removal_note_says_unassigned_without_an_override(
        self, mock_engines, mock_discover, client
    ):
        connection = self._chat_conn()
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert (
            'title="in use by RAG answer — removing leaves it with no model '
            'assigned"' in body
        )
        assert ">removing → unassigns those roles<" in body

    @override_settings(**CHAT_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_mixed_outcomes_name_each_role_rather_than_averaging(
        self, mock_engines, mock_discover, client
    ):
        """One connection, two roles, DIFFERENT outcomes: `rag.answer` has an
        explicit override (`CHAT_ENV_OVERRIDE` sets `LLM_MODEL` only) and
        `rag.embed` has none. Neither the note nor the message may pick one
        answer and apply it to both."""
        connection = ModelConnection.objects.create(
            name="dual conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["chat", "embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        page = client.get(reverse("inference-console"))
        body = page.content.decode()
        assert (
            'title="in use by RAG answer, RAG embeddings — removing hands RAG '
            'answer to the explicit environment override and leaves RAG embeddings '
            'with no model assigned"' in body
        )
        assert ">removing → changes what backs those roles<" in body

        # An embeddings role is bound, so removal is gated -- confirm it.
        response = self._remove(client, connection.id, confirm="yes")

        assert response.status_code == 302
        followed = client.get(response.url)
        message_body = followed.content.decode()
        assert (
            "Removed connection “dual conn” — RAG answer is now unassigned: "
            "the explicit environment override for this role takes over; "
            "RAG embeddings is now unassigned: no model will back it and anything "
            "that needs it reports unavailable until you assign one." in message_body
        )


# --- _default_endpoint's three-tier fallback ----------------------------------


class TestDefaultEndpointFallback:
    """`_default_endpoint()` answers "which address does this page health-check
    and scan?" -- and it must answer on a box where NOTHING is assigned, or
    first run would have no machine list to register from.

    Three tiers, each pinned here: the third tier must not be skipped, since
    its absence would be a 500 on the whole console the moment nothing is
    assigned. No DB needed: both
    resolution halves are patched at the names bound in the view module.

    `settings.OLLAMA_BASE_URL` as the floor is deliberate, not a relapse into
    defaults: it is a server *location*, deploy convention (ADR 0006), and the
    one default this platform keeps. Nothing about a MODEL is
    presumed by it -- the roles still read "No model assigned"."""

    def test_tier_one_uses_the_resolved_binding_endpoint(self):
        with patch(
            "models.registry.views.resolve",
            return_value=ResolvedModel("ollama", "m", "http://resolved.local:11434"),
        ):
            assert views_module._default_endpoint() == "http://resolved.local:11434"

    @override_settings(OLLAMA_BASE_URL="http://settings.local:11434")
    def test_tier_two_falls_back_to_the_env_provider_when_resolve_raises(self):
        """A broken `INFERENCE_BINDING_PROVIDER` (or any other resolution
        blowup) must not cost an operator their explicit env override."""
        with (
            patch("models.registry.views.resolve", side_effect=ValueError("boom")),
            patch(
                "models.registry.views.env_provider",
                return_value=ResolvedModel("ollama", "m", "http://override.local:11434"),
            ),
        ):
            assert views_module._default_endpoint() == "http://override.local:11434"

    @override_settings(OLLAMA_BASE_URL="http://settings.local:11434")
    def test_tier_three_falls_back_to_the_configured_server_location(self):
        """The fresh-box state: nothing resolves, `env_provider` has no
        opinion. The page still gets an address instead of a 500."""
        with (
            patch("models.registry.views.resolve", side_effect=ValueError("unbound")),
            patch("models.registry.views.env_provider", return_value=None),
        ):
            assert views_module._default_endpoint() == "http://settings.local:11434"

    @override_settings(OLLAMA_BASE_URL="http://settings.local:11434")
    def test_an_env_provider_that_itself_raises_still_never_500s(self):
        with (
            patch("models.registry.views.resolve", side_effect=ValueError("unbound")),
            patch("models.registry.views.env_provider", side_effect=RuntimeError("boom")),
        ):
            assert views_module._default_endpoint() == "http://settings.local:11434"


# --- Tabular row layout -------------------------------------------------------


@pytest.mark.django_db
class TestMachineTableLayout:
    """"On this machine" rows are a real aligned-column grid (the
    needs-table's `display: contents` idiom): Model | Capability | On disk
    | Runtime | Status | Action."""

    def _get(self, client, discovered, connections=()):
        for kwargs in connections:
            ModelConnection.objects.create(**kwargs)
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = discovered
            return client.get(reverse("inference-console"))

    def test_column_headers_render(self, client):
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat")],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        assert 'class="machine-table" role="table"' in body
        assert '<span class="machine-cell" role="columnheader">Model</span>' in body
        assert '<span class="machine-cell" role="columnheader">Capability</span>' in body
        assert '<span class="machine-cell" role="columnheader">On disk</span>' in body
        assert '<span class="machine-cell" role="columnheader">Runtime</span>' in body
        assert '<span class="machine-cell" role="columnheader">Status</span>' in body
        assert '<span class="machine-cell" role="columnheader">Action</span>' in body

    def test_unregistered_row_cells_land_in_the_grid_action_shows_add_button(self, client):
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat", size=4_700_000_000)],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        assert 'role="row"' in body
        # Exactly 6 cells for this row (one per column).
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)
        row_html = body[row_start:row_end]
        assert row_html.count('class="machine-cell"') == 6
        assert "llama3.1:8b" in row_html
        assert "chat" in row_html
        # The ON DISK cell is the bare size, the RUNTIME cell is
        # bare "idle" -- the column header and section intro already carry
        # the rest of the meaning. (The size's `title` attribute still says
        # "Size on disk" as a hover explanation -- that's not the visible
        # per-row repetition being killed here.)
        assert ">4.4 GB<" in row_html
        assert "GB on disk" not in row_html
        assert ">idle<" in row_html
        assert "loads on demand" not in row_html
        assert ">Add to registered<" in row_html

    def test_registered_row_action_column_shows_no_add_button(self, client):
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(
            client,
            [_installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)],
        )

        body = response.content.decode()
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)
        row_html = body[row_start:row_end]
        assert "registered ✓" in row_html
        assert ">Add to registered<" not in row_html

    # --- Presentation cleanup ------------------------------------------------

    def test_status_chips_carry_nowrap_styling_class(self, client):
        """Fix 2: "registered ✓ — in use" used to wrap mid-phrase in the
        Status column. It now renders as two `.badge` pill chips -- the
        same nowrap chip styling the "explicit environment override" chip
        elsewhere on the page already uses -- each individually
        non-wrapping, with wrapping only ever allowed BETWEEN chips."""
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._get(
            client,
            [_installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)],
        )

        body = response.content.decode()
        assert '<span class="badge chip"' in body
        assert '<span class="badge healthy">in use</span>' in body
        # The `.badge` rule itself carries the nowrap declaration.
        assert "white-space: nowrap;" in body

    def test_on_disk_cell_shows_bare_size_not_repeated_on_disk(self, client):
        """Fix 3: the ON DISK column header already says "on disk" -- the
        cell itself is just the humanized size, e.g. "4.4 GB", never
        "4.4 GB on disk" repeated per row."""
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat", size=4_700_000_000)],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)
        row_html = body[row_start:row_end]
        assert ">4.4 GB<" in row_html
        assert "GB on disk" not in row_html

    def test_on_disk_cell_is_empty_not_an_em_dash_when_size_unknown(self, client):
        """No em-dash placeholders: the ON DISK cell must not fall back to a
        "—" filler when the engine doesn't report a size. It renders
        empty instead, same no-filler rule as Status/Action."""
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat", size=None)],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)
        row_html = body[row_start:row_end]
        # Scope to the On disk cell (the 3rd of the 6 `.machine-cell`s) --
        # the Model cell's nested disclosure legitimately uses em dashes in
        # its own prose ("— sent to the API as-is" etc.), which isn't what
        # this fix concerns.
        on_disk_cell = row_html.split('<span class="machine-cell" role="cell">')[3]
        assert "—" not in on_disk_cell
        assert "GB" not in on_disk_cell

    def test_runtime_cell_says_idle_without_the_parenthetical(self, client):
        """Fix 3: the load-on-demand explanation already lives in the
        section intro -- the RUNTIME cell for an idle model is just
        "idle", not "idle (loads on demand)"."""
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat")],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        assert ">idle<" in body
        assert "loads on demand" not in body

    def test_empty_action_cell_renders_no_em_dash_placeholder(self, client):
        """Fix 4: a registered row has nothing to do in the Action column
        -- the Status cell already says so -- so the cell renders empty,
        never an em-dash filler."""
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(
            client,
            [_installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)],
        )

        body = response.content.decode()
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)
        row_html = body[row_start:row_end]
        assert ">Add to registered<" not in row_html
        # Scope to the Action cell (the last of the 6 `.machine-cell`s) --
        # the Model cell's nested disclosure legitimately uses em dashes in
        # its own prose ("— sent to the API as-is" etc.), which isn't what
        # this fix concerns.
        action_cell = row_html[row_html.rindex('<span class="machine-cell" role="cell">'):]
        assert "—" not in action_cell

    def test_connection_details_disclosure_nests_inside_the_model_cell(self, client):
        """Fix 1: the disclosure used to render as its own full-width row
        below the six cells, doubling the table's rendered height even
        collapsed. It now lives inside the Model cell, beneath the model
        name -- so it's INSIDE `.installed-row`'s own closing tag, not a
        sibling that follows it."""
        response = self._get(
            client,
            [_installed_row("llama3.1:8b", capability="chat")],
            connections=[
                dict(
                    name="warm conn", engine="ollama", endpoint=ENDPOINT,
                    model_id="warm-model:latest", capabilities=["chat"],
                )
            ],
        )

        body = response.content.decode()
        row_start = body.index('<div class="installed-row"')
        row_end = body.index("</div>", row_start)  # closes .installed-row itself
        name_idx = body.index('<span class="name">llama3.1:8b</span>', row_start)
        details_idx = body.index('<details class="conn-details">', name_idx)
        assert row_start < name_idx < details_idx < row_end


