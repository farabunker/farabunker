"""The seed-migration not-found-on-machine case, `machine_model_add`'s
found -> registered pipeline step, the console's query-count budget and
its `?endpoint` override, `server_scan`'s GET/POST boundary and its POST
round-trip with the override, and the role dropdown's
registered-connections-only options.

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

from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from django.urls import reverse

from identity.contracts import actions
from identity.models import AuditEvent
from models.registry import probe_cache
from models.registry import views as views_module
from models.registry.discovery import ScanHit, ScanResult
from models.registry.models import Materialization, ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    ENDPOINT,
    _extra_role_registry,  # noqa: F401 -- requested by name as a fixture
    _installed_row,
    _mock_engine,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
    make_embed_connection,
)
from models.contracts.roles import RoleSpec, register_role


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


@pytest.mark.django_db
class TestConsoleViewNotFoundOnMachine:
    """Seed-migration honesty: migration 0002 binds connections seeded from
    an operator's explicit env overrides (named "env: LLM_MODEL" / "env:
    EMBED_MODEL") whether or not the model is actually installed, so a role
    can legitimately be bound to a model the live scan can't find."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_env_seeded_binding_absent_from_scan_shows_provenance_and_marker(
        self, mock_engines, mock_discover, client
    ):
        connection = ModelConnection.objects.create(
            name="env: LLM_MODEL", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []  # nothing installed

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["found_on_machine"] is False
        assert role_views["rag.answer"]["env_seeded"] is True
        body = response.content.decode()
        assert (
            "registered from an environment override — not found on this machine" in body
        )

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_operator_registered_binding_absent_from_scan_shows_plain_marker(
        self, mock_engines, mock_discover, client
    ):
        connection = ModelConnection.objects.create(
            name="my custom conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["found_on_machine"] is False
        assert role_views["rag.answer"]["env_seeded"] is False
        body = response.content.decode()
        assert "— not found on this machine" in body
        assert "from an environment override" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_binding_found_on_machine_shows_no_marker(self, mock_engines, mock_discover, client):
        connection = ModelConnection.objects.create(
            name="env: LLM_MODEL", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b")]

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["found_on_machine"] is True
        body = response.content.decode()
        assert "not found on this machine" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_bare_id_binding_matches_tagged_installed_row(self, mock_engines, mock_discover, client):
        """C1 regression: the seed migration (and hand-typed connections)
        can store a bare model_id ("nomic-embed-text") while `discover()`
        always reports the engine's exact tagged string
        ("nomic-embed-text:latest") for an installed row. Comparing those
        raw strings falsely marked an installed model "not found on this
        machine" and offered "use for..." on its On-this-machine row
        instead of "in use" -- live-confirmed on the preview stack against
        the real env-seeded "nomic-embed-text" connection."""
        connection = ModelConnection.objects.create(
            name="env: EMBED_MODEL", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row(
                "nomic-embed-text:latest", capability="embeddings", embed_dim=768, size=274290000
            )
        ]

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["found_on_machine"] is True
        body = response.content.decode()
        assert "not found on this machine" not in body

        installed_rows = response.context["installed_rows"]
        assert len(installed_rows) == 1
        assert installed_rows[0]["in_use"] is True
        assert '<span class="badge healthy">in use</span>' in body
        # Dedup: the installed model matches the bound connection
        # by norm_tag+endpoint, so the rag.embed dropdown offers it ONCE,
        # as the connection -- never as a second raw model: option.
        role_views = {v["role"].key: v for v in response.context["role_views"]}
        values = [o["value"] for o in role_views["rag.embed"]["options"]]
        assert values == [f"conn:{connection.pk}"]
        assert "model:ollama:768:nomic-embed-text:latest" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_bound_connection_at_a_different_endpoint_gets_no_marker_and_no_in_use(
        self, mock_engines, mock_discover, client
    ):
        """`norm_tag()` alone would widen `found_on_machine`
        / `in_use` into falsely matching a bound connection at an UNRELATED
        endpoint that just happens to share (engine, model_id) with a
        locally installed row -- exactly the shape a hand-typed or
        env-seeded connection can take. That connection must get NEITHER
        marker: not "in use"/"found on this machine" (it might not even be
        the same actual model at that other endpoint), and not "not found
        on this machine" either (that phrasing would be false too --
        it may well exist at its own endpoint)."""
        remote = ModelConnection.objects.create(
            name="remote embed", engine="ollama", endpoint="http://other-host:11434",
            model_id="foo", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=remote)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("foo:latest", capability="embeddings", embed_dim=768)
        ]

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["found_on_machine"] is None
        body = response.content.decode()
        assert "not found on this machine" not in body

        installed_rows = response.context["installed_rows"]
        assert len(installed_rows) == 1
        assert installed_rows[0]["in_use"] is False
        assert '<span class="in-use-tag">' not in body
        # The dropdown lists REGISTERED
        # connections only -- just the remote one, labeled with its
        # endpoint. The locally installed model is not registered (the
        # remote connection is a different registration -- no cross-endpoint
        # match), so its machine row carries the "Add to registered"
        # button instead.
        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.embed"]["options"]
        assert [o["value"] for o in options] == [f"conn:{remote.pk}"]
        assert options[0]["label"].endswith("@ http://other-host:11434")
        assert installed_rows[0]["connection"] is None
        assert ">Add to registered<" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_trailing_slash_endpoint_still_matches(self, mock_engines, mock_discover, client):
        """A connection's endpoint with a trailing slash
        ("http://localhost:11434/") must still match the scanned endpoint
        without one -- `norm_endpoint()` only strips the slash, no URL
        parsing."""
        connection = ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=f"{ENDPOINT}/",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)
        ]

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["found_on_machine"] is True
        assert response.context["installed_rows"][0]["in_use"] is True


# --- machine_model_add: the "Add to registered" button -----------------------


@pytest.mark.django_db
class TestMachineModelAdd:
    """The machine
    rows' "Add to registered" button posts the row's DETECTED facts to
    `machine_model_add`, which registers a connection -- intentionally,
    one click, never as a side effect of assignment -- and binds NOTHING.
    Registration uses create-or-reuse identity (norm_tag + normalized
    endpoint) and refuse-don't-guess rules (unknown capability, unknown
    embedding dimension)."""

    def _post(self, client, **data):
        return client.post(reverse("inference-machine-add"), data=data)

    def _detected(self, **overrides):
        data = {
            "engine": "ollama",
            "model_id": "llama3.1:8b",
            "endpoint": ENDPOINT,
            "capability": "chat",
            "embed_dim": "",
        }
        data.update(overrides)
        return data

    def test_creates_connection_with_all_detected_fields(self, client):
        response = self._post(
            client,
            **self._detected(
                model_id="nomic-embed-text:latest", capability="embeddings", embed_dim="768"
            ),
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(model_id="nomic-embed-text:latest")
        assert connection.engine == "ollama"
        assert connection.endpoint == ENDPOINT
        assert connection.capabilities == ["embeddings"]
        assert connection.embed_dim == 768
        # The default name is the model id, as the old assign flow named
        # its auto-created connections (`_unique_connection_name`).
        assert connection.name == "nomic-embed-text:latest"
        followed = client.get(response.url)
        assert "Registered" in followed.content.decode()

    def test_a_valid_registration_is_audited_exactly_once(self, client):
        """I3 (Coherence Wave B review): a SECOND `ModelConnection`
        creation path, same as `connection_add`'s create branch --
        S3's ruling covers every `ModelConnection` write, not only the
        manual-form one. `detail` is empty: this path sets no
        `footprint_override_bytes` at all."""
        self._post(client, **self._detected())

        connection = ModelConnection.objects.get(model_id="llama3.1:8b")
        assert AuditEvent.objects.filter(action=actions.CONNECTION_CREATED).count() == 1
        event = AuditEvent.objects.get(action=actions.CONNECTION_CREATED)
        assert event.actor_kind == "open"
        assert event.target_key == str(connection.pk)
        assert event.target_label == connection.name
        assert event.detail == {}

    def test_created_connection_has_no_descriptor_or_rank(self, client):
        """`machine_model_add` registers strictly from detection --
        self-assigning a descriptor/rank is an operator's own later,
        deliberate act in the one edit home, never guessed here."""
        self._post(client, **self._detected())

        connection = ModelConnection.objects.get(model_id="llama3.1:8b")
        assert connection.descriptor == ""
        assert connection.rank is None

    def test_registering_binds_no_role(self, client):
        """Registration and assignment are separate pipeline steps: the Add
        button must never touch a RoleBinding -- "in use" is the role
        dropdowns' own act."""
        self._post(client, **self._detected())

        assert ModelConnection.objects.count() == 1
        assert not RoleBinding.objects.exists()

    def test_embeddings_add_needs_no_confirm_and_stamps_nothing(self, client):
        """The confirm gate and the pre-rebind stamp protect BINDINGS
        (materialized data); registering a connection changes no binding,
        so an embeddings Add applies without `confirm` and stamps no
        Materialization."""
        response = self._post(
            client,
            **self._detected(
                model_id="mxbai-embed-large:latest", capability="embeddings", embed_dim="1024"
            ),
        )

        assert response.status_code == 302
        assert ModelConnection.objects.filter(model_id="mxbai-embed-large:latest").exists()
        assert not Materialization.objects.exists()

    def test_second_click_reuses_never_duplicates(self, client):
        self._post(client, **self._detected())
        response = self._post(client, **self._detected())

        assert response.status_code == 302
        assert ModelConnection.objects.count() == 1
        followed = client.get(response.url)
        assert "Already registered as" in followed.content.decode()

    def test_reuses_bare_id_connection_for_a_tagged_detected_model_id(self, client):
        """C1 regression, carried over: an env-seeded (or hand-typed)
        connection can hold a bare model_id ("nomic-embed-text") while the
        machine row always posts the engine's exact tagged string
        ("nomic-embed-text:latest") -- the same model, recognized via
        `norm_tag()`, is reported as already registered rather than
        duplicated beside the existing row. The existing connection is not
        modified either -- reuse never rewrites operator-stored values."""
        existing = ModelConnection.objects.create(
            name="env: EMBED_MODEL", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._post(
            client,
            **self._detected(
                model_id="nomic-embed-text:latest", capability="embeddings", embed_dim="768"
            ),
        )

        assert response.status_code == 302
        assert ModelConnection.objects.count() == 1
        existing.refresh_from_db()
        assert existing.model_id == "nomic-embed-text"  # untouched
        followed = client.get(response.url)
        assert "Already registered as" in followed.content.decode()
        assert "env: EMBED_MODEL" in followed.content.decode()

    def test_does_not_reuse_a_connection_registered_at_a_different_endpoint(self, client):
        """A connection sharing (engine, model_id) but registered at a
        DIFFERENT endpoint is a different registration -- it must never
        block (or stand in for) registering the locally found model."""
        ModelConnection.objects.create(
            name="remote embed", engine="ollama", endpoint="http://other-host:11434",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._post(
            client,
            **self._detected(
                model_id="nomic-embed-text:latest", capability="embeddings", embed_dim="768"
            ),
        )

        assert response.status_code == 302
        assert ModelConnection.objects.count() == 2  # remote untouched, new local one created
        local = ModelConnection.objects.get(endpoint=ENDPOINT)
        assert local.model_id == "nomic-embed-text:latest"
        # The remote connection's NAME ("remote embed") doesn't collide, so
        # the default model-id-as-name applies unsuffixed.
        assert local.name == "nomic-embed-text:latest"

    def test_reuses_connection_across_a_trailing_slash_endpoint_mismatch(self, client):
        """`norm_endpoint()` strips only a trailing slash -- a stored
        endpoint of "http://localhost:11434/" is the same machine as the
        posted "http://localhost:11434"."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=f"{ENDPOINT}/",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._post(
            client,
            **self._detected(
                model_id="nomic-embed-text:latest", capability="embeddings", embed_dim="768"
            ),
        )

        assert response.status_code == 302
        assert ModelConnection.objects.count() == 1

    def test_name_collision_falls_back_to_the_endpoint_suffixed_name(self, client):
        """A DIFFERENT model whose id happens to collide with an existing
        connection NAME still registers, under the endpoint-suffixed
        fallback (`_unique_connection_name`, CI-unique)."""
        ModelConnection.objects.create(
            name="llama3.1:8b", engine="ollama", endpoint="http://other-host:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._detected())

        assert response.status_code == 302
        local = ModelConnection.objects.get(endpoint=ENDPOINT)
        assert local.name == f"llama3.1:8b ({ENDPOINT})"

    def test_unknown_capability_is_refused_to_the_manual_form(self, client):
        """Never-guess: the template renders the manual-form link (not this
        button) for a degraded row, and the view enforces the same boundary
        against stale/tampered posts -- nothing is created."""
        response = self._post(client, **self._detected(capability=""))

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "no detected capability" in body
        assert "manual form" in body

    def test_garbage_capability_is_refused_too(self, client):
        response = self._post(client, **self._detected(capability="not-a-capability"))

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()

    # --- T9: multi-capability registration -----------------------------

    def test_full_capability_list_registers_via_one_click(self, client):
        """A llava/qwen-vl-class row detected with BOTH "chat" and "vision"
        posts every capability as its own hidden `capability` input
        (`_installed_row.html`) -- `machine_model_add` reads the whole set
        via `getlist` and stores it whole, so the resulting connection
        appears in both the chat picker and the vision (rag.extract)
        dropdown; the operator's later binding decides which job it does."""
        response = self._post(
            client,
            **self._detected(model_id="llava:13b", capability=["chat", "vision"]),
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(model_id="llava:13b")
        assert connection.capabilities == ["chat", "vision"]

    def test_a_single_value_capability_post_still_round_trips(self, client):
        """Back-compat: `getlist("capability")` on a single posted value is
        a one-item list, same as before this task -- an ordinary
        single-capability row (e.g. transcription) keeps registering
        exactly one capability."""
        response = self._post(
            client,
            **self._detected(model_id="whisper:latest", capability="transcription"),
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(model_id="whisper:latest")
        assert connection.capabilities == ["transcription"]

    def test_empty_capability_selection_is_refused(self, client):
        """A row's capability field posted entirely absent (never even an
        empty string) -- `getlist` on a missing key is `[]` -- is refused
        exactly like an unrecognized single value, never a
        capability-less connection."""
        data = self._detected()
        del data["capability"]

        response = self._post(client, **data)

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "no detected capability" in body
        assert "manual form" in body

    def test_embed_dim_rule_applies_when_embeddings_is_anywhere_in_the_list(self, client):
        """The refuse-don't-guess embed-dim rule keys off "embeddings"
        being IN the full capability list, not off it being the sole/first
        entry -- a chat+embeddings row with no known dimension is still
        refused to the manual form."""
        response = self._post(
            client,
            **self._detected(
                model_id="dual-capability:latest", capability=["chat", "embeddings"], embed_dim="",
            ),
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        assert "no known embedding dimension" in followed.content.decode()

    def test_chat_and_embeddings_combo_registers_with_the_supplied_dimension(self, client):
        response = self._post(
            client,
            **self._detected(
                model_id="dual-capability:latest",
                capability=["chat", "embeddings"],
                embed_dim="999",
            ),
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(model_id="dual-capability:latest")
        assert connection.capabilities == ["chat", "embeddings"]
        assert connection.embed_dim == 999

    def test_unregistered_engine_is_rejected_cleanly(self, client):
        """Review I1: validation parity with `connection_add` -- the engine
        field is hidden/template-filled, but this is a second creation path
        and must enforce the same support boundary. A garbage engine (stale
        or tampered POST) stores nothing and lands as the same friendly
        message, never a 500 and never a garbage row."""
        response = self._post(client, **self._detected(engine="totally-not-an-engine"))

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "Unknown model server" in body
        assert "totally-not-an-engine" in body

    def test_double_name_collision_falls_back_to_a_numbered_name(self, client):
        """Review I2 (never-500): an operator can hand-name connections in
        the default-name pattern, taking BOTH original candidates
        (`model_id` and `model_id (endpoint)`); the old two-candidate
        helper then raised IntegrityError -- a 500. The bounded numeric
        suffix now registers under "model_id (2)" instead. The colliding
        connections point at other models/endpoints so the norm_tag +
        endpoint reuse check never fires -- this is purely a NAME clash."""
        ModelConnection.objects.create(
            name="llama3.1:8b", engine="ollama", endpoint="http://other-host:11434",
            model_id="some-other-model", capabilities=["chat"],
        )
        ModelConnection.objects.create(
            name=f"llama3.1:8b ({ENDPOINT})", engine="ollama",
            endpoint="http://other-host:11434", model_id="another-model",
            capabilities=["chat"],
        )

        response = self._post(client, **self._detected())

        assert response.status_code == 302
        created = ModelConnection.objects.get(model_id="llama3.1:8b")
        assert created.name == "llama3.1:8b (2)"
        followed = client.get(response.url)
        assert "Registered" in followed.content.decode()

    def test_integrity_error_on_create_degrades_to_a_message_not_a_500(self, client):
        """Review I2 belt: a collision the candidate check missed (the
        filter-then-create race, or anything else the DB-level CI-unique
        constraint catches) must land as `connection_add`'s name_taken-style
        message, never a 500."""
        from django.db import IntegrityError

        with patch(
            "models.registry.views.ModelConnection.objects.create",
            side_effect=IntegrityError("duplicate key"),
        ):
            response = self._post(client, **self._detected())

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        assert "already exists" in followed.content.decode()

    def test_an_integrity_error_is_not_audited(self, client):
        """I3 (Coherence Wave B review): the `transaction.atomic()` block
        wrapping the create rolls the audit write back along with the
        row when the DB-level constraint fires, mirroring `TestConnectionAdd::
        test_an_invalid_create_is_not_audited`."""
        from django.db import IntegrityError

        with patch(
            "models.registry.views.ModelConnection.objects.create",
            side_effect=IntegrityError("duplicate key"),
        ):
            self._post(client, **self._detected())

        assert AuditEvent.objects.count() == 0

    def test_embeddings_with_no_known_dimension_refuses_and_points_to_manual_form(self, client):
        """No dimension from the engine (posted blank) and no catalog match
        -- must refuse outright, never guess, never fall back to
        settings.EMBED_DIM (the standing ruling, carried from the old
        model-pick path)."""
        response = self._post(
            client,
            **self._detected(
                model_id="some-custom-embedder:latest", capability="embeddings", embed_dim=""
            ),
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "no known embedding dimension" in body
        assert "manual form" in body

    def test_embeddings_dimension_falls_back_to_the_catalog(self, client):
        """F1 regression, carried over: a blank engine-reported dimension
        with a TAGGED id matching a bare catalog entry gets the catalog's
        dimension instead of a refusal."""
        response = self._post(
            client,
            **self._detected(
                model_id="nomic-embed-text:latest", capability="embeddings", embed_dim=""
            ),
        )

        assert response.status_code == 302
        connection = ModelConnection.objects.get(model_id="nomic-embed-text:latest")
        assert connection.embed_dim == 768  # from the bare catalog entry

    @pytest.mark.parametrize("missing_field", ["engine", "model_id", "endpoint"])
    def test_missing_detected_field_errors(self, client, missing_field):
        """A stripped/tampered POST missing a detection fact registers
        nothing."""
        response = self._post(client, **self._detected(**{missing_field: ""}))

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        assert "Missing model details" in followed.content.decode()

    def test_get_is_not_allowed(self, client):
        response = client.get(reverse("inference-machine-add"))
        assert response.status_code == 405

    def test_redirect_preserves_an_active_endpoint_override(self, client):
        """The Add form carries the hidden `endpoint_override` like every
        other form on the page (F2), and registers against the OVERRIDE
        endpoint -- the machine actually being viewed."""
        override = "http://elsewhere:11434"
        response = self._post(
            client,
            **self._detected(endpoint=override, endpoint_override=override),
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('inference-console')}?endpoint={quote(override)}"
        connection = ModelConnection.objects.get(model_id="llama3.1:8b")
        assert connection.endpoint == override

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_pipeline_register_then_assign_turns_the_page_warm(
        self, mock_engines, mock_discover, client
    ):
        """The full pipeline from an actual cold-start console. FOUND: the
        cold page offers the detected row's Add button and an empty role
        dropdown. REGISTERED: posting the Add creates the connection (page
        still binds nothing). IN USE: the role dropdown now offers
        `conn:<pk>` and assigning it binds; the SAME console then renders
        warm."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        cold_response = client.get(reverse("inference-console"))
        assert cold_response.context["cold_start"] is True
        assert not ModelConnection.objects.exists()
        cold_views = {v["role"].key: v for v in cold_response.context["role_views"]}
        assert cold_views["rag.answer"]["options"] == []
        assert ">Add to registered<" in cold_response.content.decode()

        # Step 1 -- FOUND -> REGISTERED: the Add button's post.
        add_response = self._post(client, **self._detected())
        assert add_response.status_code == 302
        connection = ModelConnection.objects.get(model_id="llama3.1:8b")
        assert not RoleBinding.objects.exists()

        # The page is warm now (a connection exists + healthy) and the
        # dropdown offers exactly that registered connection.
        registered_response = client.get(reverse("inference-console"))
        assert registered_response.context["cold_start"] is False
        registered_views = {
            v["role"].key: v for v in registered_response.context["role_views"]
        }
        assert [o["value"] for o in registered_views["rag.answer"]["options"]] == [
            f"conn:{connection.pk}"
        ]
        # The machine row now shows the registered chip, not the button.
        registered_body = registered_response.content.decode()
        assert "registered ✓" in registered_body
        assert ">Add to registered<" not in registered_body

        # Step 2 -- REGISTERED -> IN USE: the role dropdown's conn pick.
        assign_response = client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": f"conn:{connection.pk}"},
        )
        assert assign_response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection == connection

        warm_response = client.get(reverse("inference-console"))
        role_views = {v["role"].key: v for v in warm_response.context["role_views"]}
        assert role_views["rag.answer"]["current_connection"] == connection
        assert '<span class="badge healthy">in use</span>' in warm_response.content.decode()

    # --- diffusion-model files need companions detection can't supply ------

    def test_one_click_registration_refuses_a_model_that_needs_companions(self, client):
        """`machine_model_add` registers ONLY from detection, and detection
        cannot supply a family or its companion files -- so a diffusion-model
        row is sent to the manual form instead of being registered half-
        configured."""
        response = self._post(
            client,
            engine="comfyui",
            model_id="weights-Q4_K_S.gguf",
            endpoint="http://comfy.test:8188",
            capability="image-generation",
            embed_dim="",
            loader="UnetLoaderGGUF",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(model_id="weights-Q4_K_S.gguf").exists()
        followed = client.get(response.url)
        assert "manual form" in followed.content.decode()

    def test_a_self_contained_checkpoint_loader_registers_without_companions(self, client):
        """The one loader whose models need nothing else named alongside
        them (`_MODEL_LOADER_SELF_CONTAINED`) must never be caught by the
        diffusion-model refusal -- a plain checkpoint keeps working through
        the one-click button exactly as before."""
        response = self._post(
            client,
            engine="comfyui",
            model_id="sdxl.safetensors",
            endpoint="http://comfy.test:8188",
            capability="image-generation",
            embed_dim="",
            loader="CheckpointLoaderSimple",
        )

        assert response.status_code == 302
        assert ModelConnection.objects.filter(model_id="sdxl.safetensors").exists()

    def test_a_blank_loader_registers_without_companions(self, client):
        """An older/stub row (or an ollama-detected model, which reports no
        loader at all) posts an empty `loader` field -- never refused, same
        as before this task."""
        response = self._post(client, **self._detected())

        assert response.status_code == 302
        assert ModelConnection.objects.filter(model_id="llama3.1:8b").exists()


# --- ConsoleView: query-efficiency absorption ---------------------------------


@pytest.mark.django_db
class TestConsoleViewQueryCount:
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_query_count_does_not_grow_with_more_registered_roles(
        self, mock_engines, mock_discover, client, django_assert_num_queries, _extra_role_registry
    ):
        # 10, not 5, and every one of them is FLAT -- none grows with the
        # number of roles, which is the invariant this test exists to
        # check and the only thing the constant's size has ever meant.
        # `_build_context` fetches `connections` (Meta's plain name order,
        # feeding "Registered connections") and `picker_connections`
        # (`ModelConnection.objects.picker_order()`, feeding role
        # dropdowns) as two separate queries -- still ONE each, not one
        # per role. The sixth is `IdentityGateMiddleware`'s one
        # `accounts_on()` read of the posture singleton, on every request
        # regardless of route -- primed below so its one-time
        # row-creation cost (SELECT + INSERT) isn't counted inside the
        # assertion window. The seventh and eighth (IA-2 T14) are
        # `all_model_sets` (every `ModelSet` row, for
        # `_registered_connection.html`'s membership multiselect) and
        # `_connection_views`'s own bulk `ModelSetMember` read (every
        # connection's current memberships, in ONE query, not one per
        # connection or per role). The ninth (UI-1) is
        # `models.registry.context_processors.availability`'s
        # `values_list("role_key")` over `RoleBinding`, which the SHELL
        # runs on every rendered page to decide which use-surface entries
        # to show -- in production it is served from
        # `models.registry.availability`'s process cache and costs
        # nothing on the warm path; it runs live here only because a
        # `django_db` test is inside an atomic block, where that module
        # deliberately refuses to cache (see its docstring). THE TENTH
        # (Task 8, spec §11's `+1`) is `agents.chat.context_processors.
        # settings_assistant` -- `inference-console` ("Models") is one of
        # the settings-area card routes, so the processor does not
        # early-out on it the way it does off the settings area; the one
        # query is the agent row `visible_agents` reads for the
        # COLLAPSED panel (open-posture `client`, so `is_admin` and
        # `sees_all_content` both cost zero and the panel takes its cheap
        # branch). The same `+1` lands on every settings page; the only
        # absolute pins on it are this one and its override sibling,
        # `test_console_override_query_count.py`, which asserts the same
        # 10 for a permitted loopback `?endpoint=` GET.
        from identity.models import IdentitySettings
        IdentitySettings.get_solo()
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        with django_assert_num_queries(10):
            client.get(reverse("inference-console"))

        register_role(RoleSpec(key="test.role3", label="Role 3", capability="chat"))
        register_role(RoleSpec(key="test.role4", label="Role 4", capability="vision"))

        with django_assert_num_queries(10):
            client.get(reverse("inference-console"))

    def test_a_second_console_get_inside_the_ttl_does_not_re_probe(self, client):
        """C-07 half A, at the surface. Two GETs in one process, inside
        the 30s window, must cost ONE health probe -- not two."""
        probe_cache.invalidate()
        engine = _mock_engine(healthy=True)
        with patch.dict(views_module.ENGINES, {"stub": engine}, clear=True), \
                patch.object(views_module, "discover", return_value=[]):
            client.get(reverse("inference-console"))
            client.get(reverse("inference-console"))
        assert engine.is_healthy.call_count == 1

    @pytest.mark.django_db(transaction=True)
    def test_a_mutating_post_invalidates_the_probe_cache(self, client):
        """The other direction. An operator who registers a connection and
        lands back on the console must see a fresh probe, not a 30s-old
        one -- which is what makes the TTL safe to have at all.

        `transaction=True`, overriding the class's plain `django_db`: the
        invalidation runs through `transaction.on_commit` (probe_cache.
        invalidate_after_commit), which never fires inside an ordinary
        `django_db` test's rolled-back transaction -- the same reason
        `test_availability.py::TestTheProcessCache` runs
        `transaction=True` for its own signal-wiring tests."""
        probe_cache.invalidate()
        engine = _mock_engine(healthy=True)
        with patch.dict(views_module.ENGINES, {"stub": engine}, clear=True), \
                patch.object(views_module, "discover", return_value=[]):
            client.get(reverse("inference-console"))
            response = client.post(
                reverse("inference-connection-add"),
                data={
                    "name": "probe cache conn",
                    "engine": "ollama",
                    "endpoint": ENDPOINT,
                    "model_id": "llama3.1:8b",
                    "capability": "chat",
                },
            )
            assert response.status_code == 302
            assert ModelConnection.objects.filter(name="probe cache conn").exists()
            client.get(reverse("inference-console"))
        assert engine.is_healthy.call_count >= 2


# --- ConsoleView: ?endpoint override ------------------------------------------


@pytest.mark.django_db
class TestConsoleViewEndpointOverride:
    """A scan hit's "Use this endpoint" link reloads the console with
    `?endpoint=<url>` -- validated http/https AND allowlisted (H8, S5),
    malformed or disallowed silently falls back to the default
    (`_requested_override`). The allowlist (`_override_is_permitted`)
    admits only the box's own loopback interface, or the exact
    scheme+host+port of a `ModelConnection` already registered here or a
    configured default endpoint -- an arbitrary address is refused
    regardless of scheme; loopback values are used below so these tests
    exercise the override mechanism itself, not the allowlist (that has
    its own module, `test_endpoint_override.py`)."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_valid_http_endpoint_overrides_the_default(self, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        # H8 (S5): the override must be an allowlisted host (loopback here)
        # for `_requested_override` to honour it.
        response = client.get(reverse("inference-console"), {"endpoint": "http://127.0.0.1:11434"})

        assert response.context["endpoint"] == "http://127.0.0.1:11434"
        assert "http://127.0.0.1:11434" in mock_discover.call_args[0][0]["ollama"]

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_https_endpoint_is_also_accepted(self, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        # H8 (S5): loopback again -- an arbitrary host is refused regardless
        # of scheme.
        response = client.get(reverse("inference-console"), {"endpoint": "https://127.0.0.1:8443"})

        assert response.context["endpoint"] == "https://127.0.0.1:8443"

    @pytest.mark.parametrize(
        "malformed",
        ["not-a-url", "ftp://elsewhere:11434", "javascript:alert(1)", "//elsewhere:11434", ""],
    )
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_malformed_or_non_http_endpoint_falls_back_to_default(
        self, mock_engines, mock_discover, client, malformed
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"), {"endpoint": malformed})

        assert response.context["endpoint"] == ENDPOINT

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_no_endpoint_param_uses_default(self, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["endpoint"] == ENDPOINT


# --- server_scan: never on GET ------------------------------------------------


@pytest.mark.django_db
class TestServerScanNeverRunsOnGet:
    """The console's own GET path must NEVER trigger a scan -- cold or
    warm -- only the explicit POST button does."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_warm_console_get_never_calls_scan(self, mock_scan, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        client.get(reverse("inference-console"))

        mock_scan.assert_not_called()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_cold_start_console_get_never_calls_scan(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=False)]
        mock_discover.return_value = []

        client.get(reverse("inference-console"))

        mock_scan.assert_not_called()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_endpoint_override_get_never_calls_scan(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        client.get(reverse("inference-console"), {"endpoint": "http://elsewhere:11434"})

        mock_scan.assert_not_called()


# --- server_scan: the POST endpoint -------------------------------------------


@pytest.mark.django_db
class TestServerScan:
    def test_get_is_not_allowed(self, client):
        response = client.get(reverse("inference-server-scan"))
        assert response.status_code == 405

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_post_runs_the_scan_exactly_once(self, mock_scan, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(hits=[], checked=[])

        client.post(reverse("inference-server-scan"))

        mock_scan.assert_called_once()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_post_renders_the_console_directly_with_a_hit(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(
            hits=[ScanHit(engine="ollama", endpoint="http://host.docker.internal:11434", model_count=3)],
            checked=["http://localhost:11434", "http://host.docker.internal:11434"],
        )

        response = client.post(reverse("inference-server-scan"))

        assert response.status_code == 200  # re-rendered directly, not a redirect
        body = response.content.decode()
        assert "Found" in body
        assert "ollama" in body
        assert "http://host.docker.internal:11434" in body
        assert "3 models" in body
        assert "Use this endpoint" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_hit_use_this_endpoint_link_carries_the_found_endpoint(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(
            hits=[ScanHit(engine="ollama", endpoint="http://host.docker.internal:11434", model_count=1)],
            checked=[],
        )

        response = client.post(reverse("inference-server-scan"))

        body = response.content.decode()
        assert f'{reverse("inference-console")}?endpoint=http%3A//host.docker.internal%3A11434' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_nothing_found_shows_the_checked_addresses_hint(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(
            hits=[], checked=["http://localhost:11434", "http://127.0.0.1:11434"]
        )

        response = client.post(reverse("inference-server-scan"))

        body = response.content.decode()
        assert "No additional model servers found" in body
        assert "http://localhost:11434" in body
        assert "http://127.0.0.1:11434" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_scan_targets_the_currently_resolved_endpoint(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        """Honors an already-applied `?endpoint=` override (e.g. re-
        scanning after a prior "Use this endpoint" click) rather than
        always the env/DB default."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(hits=[], checked=[])

        client.post(reverse("inference-server-scan"))
        mock_scan.assert_called_once_with(ENDPOINT)

        # H8 (S5): the override must be an allowlisted host (loopback here)
        # for `_requested_override` to honour it.
        mock_scan.reset_mock()
        client.post(f"{reverse('inference-server-scan')}?endpoint=http://127.0.0.1:11434")
        mock_scan.assert_called_once_with("http://127.0.0.1:11434")


# --- Final-review F2: the override survives POST round-trips -----------------


@pytest.mark.django_db
class TestEndpointOverridePostRoundTrip:
    """Final-review F2: the `?endpoint=` override lives only in the URL, so
    the page's plain forms used to drop it on every POST -- the scan and
    assign/bind forms now round-trip it in a hidden `endpoint_override`
    field (same validation as the GET param), and the redirect-after-POST
    re-appends `?endpoint=<override>` while one is active."""

    # H8 (S5): must be an allowlisted host (loopback here) for
    # `_requested_override` to honour it -- an arbitrary address like the
    # old "http://elsewhere:11434" is refused regardless of who asks.
    OVERRIDE = "http://127.0.0.1:9999"

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_override_page_scan_form_carries_the_hidden_override(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"), {"endpoint": self.OVERRIDE})

        assert f'name="endpoint_override" value="{self.OVERRIDE}"' in response.content.decode()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_default_page_renders_no_hidden_override_field(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert "endpoint_override" not in response.content.decode()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_ready_section_add_form_carries_the_hidden_override(
        self, mock_engines, mock_discover, client
    ):
        """The cold-start ready-to-use section's "Add to registered" form must
        carry the active override the same way every other form does -- and
        must register against the OVERRIDE endpoint, the machine actually
        being viewed."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        response = client.get(reverse("inference-console"), {"endpoint": self.OVERRIDE})

        assert response.context["cold_start"] is True
        assert response.context["installed_rows"]
        body = response.content.decode()
        # At least twice: the scan form and the machine row's Add form.
        assert body.count(f'name="endpoint_override" value="{self.OVERRIDE}"') >= 2
        # The Add form's hidden endpoint is the override -- the scanned
        # machine the row was actually found on.
        form_start = body.index('class="inline-form add-to-registered"')
        form_end = body.index("</form>", form_start)
        add_form = body[form_start:form_end]
        assert f'name="endpoint" value="{self.OVERRIDE}"' in add_form
        assert f'name="endpoint_override" value="{self.OVERRIDE}"' in add_form

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_scan_post_with_posted_override_scans_the_override(
        self, mock_scan, mock_engines, mock_discover, client
    ):
        """The scan form posts bare URLs -- the hidden field is the ONLY
        thing carrying the override into `server_scan`."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(hits=[], checked=[])

        client.post(reverse("inference-server-scan"), data={"endpoint_override": self.OVERRIDE})

        mock_scan.assert_called_once_with(self.OVERRIDE)

    @pytest.mark.parametrize(
        "malformed",
        ["not-a-url", "ftp://elsewhere:11434", "javascript:alert(1)", "//elsewhere:11434", ""],
    )
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @patch("models.registry.views.scan_for_servers")
    def test_malformed_posted_override_is_ignored_default_used(
        self, mock_scan, mock_engines, mock_discover, client, malformed
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []
        mock_scan.return_value = ScanResult(hits=[], checked=[])

        client.post(reverse("inference-server-scan"), data={"endpoint_override": malformed})

        mock_scan.assert_called_once_with(ENDPOINT)

    @patch("models.registry.views.discover")
    @patch("models.registry.discovery.ENGINES")
    @patch("models.registry.views.ENGINES")
    def test_scan_treats_the_active_override_as_already_checked_not_a_hit(
        self, mock_view_engines, mock_scan_engines, mock_discover, client
    ):
        """The override the console is showing must be skipped by the sweep
        (already checked on this page load) rather than probed and reported
        back as a 'hit' -- exactly what `server_scan`'s docstring promises."""
        stub = MagicMock()
        stub.name = "ollama"
        stub.well_known_ports = [11434]
        stub.is_healthy.return_value = True
        stub.list_installed.return_value = []
        mock_view_engines.values.return_value = [stub]
        mock_scan_engines.values.return_value = [stub]
        mock_discover.return_value = []

        # An override that IS in the scan's candidate list (COMMON_HOSTS)
        # and, since H8 review round 1 dropped `host.docker.internal`
        # from the allowlist's automatic set, must also be loopback for
        # `_requested_override` to honour it -- "127.0.0.1" is both.
        override = "http://127.0.0.1:11434"
        response = client.post(
            reverse("inference-server-scan"), data={"endpoint_override": override}
        )

        scan = response.context["scan_results"]
        assert override not in scan.checked
        assert all(hit.endpoint != override for hit in scan.hits)

    def _make_override_embed(self):
        """A registered embeddings connection at the override endpoint --
        the dropdown only ever posts `conn:<pk>` picks, so the
        override round-trip is exercised through one (binding rag.embed
        never moves `_default_endpoint()`, which resolves rag.answer, so
        the override stays active)."""
        return make_embed_connection(
            name="override embed", endpoint=self.OVERRIDE,
            model_id="mxbai-embed-large", embed_dim=1024,
        )

    def _assign_data(self, connection, **extra):
        data = {
            "role_key": "rag.embed",
            "choice": f"conn:{connection.pk}",
            "endpoint_override": self.OVERRIDE,
        }
        data.update(extra)
        return data

    def test_assign_confirm_gate_redirect_preserves_the_override(self, client):
        """The confirm-gate bounce must land back on the override page --
        the operator was just told to resubmit on a row only that page
        shows."""
        connection = self._make_override_embed()

        response = client.post(
            reverse("inference-role-assign"), data=self._assign_data(connection)
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('inference-console')}?endpoint={quote(self.OVERRIDE)}"
        # And it really was the gate, not an applied assignment.
        assert not RoleBinding.objects.filter(
            role_key__iexact="rag.embed", connection__isnull=False
        ).exists()

    def test_successful_assign_redirect_preserves_the_override(self, client):
        connection = self._make_override_embed()

        response = client.post(
            reverse("inference-role-assign"),
            data=self._assign_data(connection, confirm="yes"),
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('inference-console')}?endpoint={quote(self.OVERRIDE)}"
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection == connection

    def test_successful_connection_pick_redirect_preserves_the_override(self, client):
        # A connection on the DEFAULT machine: binding it does not move the
        # default endpoint, so the override stays active and is preserved.
        connection = ModelConnection.objects.create(
            name="local llama",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capabilities=["chat"],
        )

        response = client.post(
            reverse("inference-role-assign"),
            data={
                "role_key": "rag.answer",
                "choice": f"conn:{connection.id}",
                "endpoint_override": self.OVERRIDE,
            },
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('inference-console')}?endpoint={quote(self.OVERRIDE)}"
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection == connection

    def test_connection_pick_that_moves_the_default_to_the_override_collapses_it(self, client):
        """Binding rag.answer to a connection AT the override endpoint moves
        `_default_endpoint()` (which resolves rag.answer) to the override --
        it no longer differs from the default, so the redirect drops the
        now-redundant `?endpoint=`; the plain console URL already shows
        that endpoint."""
        connection = ModelConnection.objects.create(
            name="elsewhere llama",
            engine="ollama",
            endpoint=self.OVERRIDE,
            model_id="llama3.1:8b",
            capabilities=["chat"],
        )

        response = client.post(
            reverse("inference-role-assign"),
            data={
                "role_key": "rag.answer",
                "choice": f"conn:{connection.id}",
                "endpoint_override": self.OVERRIDE,
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("inference-console")
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection == connection

    def test_assign_without_override_redirects_to_the_plain_console(self, client):
        connection = self._make_override_embed()
        data = self._assign_data(connection, confirm="yes")
        del data["endpoint_override"]

        response = client.post(reverse("inference-role-assign"), data=data)

        assert response.status_code == 302
        assert response.url == reverse("inference-console")


# --- The role dropdown (registered connections ONLY) --------------------------


@pytest.mark.django_db
class TestRoleDropdownOptions:
    """Each role row's "change" dropdown lists REGISTERED
    connections only (capability-filtered) plus unbind -- no installed-model
    union and no `model:` option values. An
    installed-but-unregistered model reaches the dropdown by being
    registered first, from its machine row's "Add to registered" button."""

    def _get(self, client, discovered, connections=()):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = discovered
            return client.get(reverse("inference-console"))

    def test_installed_model_with_matching_connection_appears_once_as_the_connection(self, client):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(
            client,
            [_installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)],
        )

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.embed"]["options"]
        assert [o["value"] for o in options] == [f"conn:{connection.pk}"]
        assert options[0]["label"] == "embed conn (nomic-embed-text) — on this machine"

    # --- descriptor label / rank ordering -----------------------------------

    def test_descriptor_leads_the_option_label_when_set(self, client):
        connection = ModelConnection.objects.create(
            name="workstation chat", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], descriptor="light + fast",
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.answer"]["options"]
        assert options[0]["label"] == "workstation chat — light + fast (llama3.1:8b) — on this machine"

    def test_option_label_is_bare_name_when_no_descriptor(self, client):
        ModelConnection.objects.create(
            name="workstation chat", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.answer"]["options"]
        assert options[0]["label"] == "workstation chat (llama3.1:8b) — on this machine"

    def test_descriptor_labels_remote_connections_too(self, client):
        """Descriptor leads the label; the existing endpoint-scoping tail
        is unchanged either way."""
        ModelConnection.objects.create(
            name="mistral", engine="ollama", endpoint="http://192.168.1.50:11434",
            model_id="mistral:7b", capabilities=["chat"], descriptor="deep reasoning — heavy",
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.answer"]["options"]
        assert options[0]["label"] == "mistral — deep reasoning — heavy (mistral:7b) @ http://192.168.1.50:11434"

    def test_options_order_ranked_connections_first_lowest_rank_first(self, client):
        ModelConnection.objects.create(
            name="second", engine="ollama", endpoint=ENDPOINT, model_id="a:1b",
            capabilities=["chat"], rank=2,
        )
        ModelConnection.objects.create(
            name="first", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=1,
        )
        ModelConnection.objects.create(
            name="zzz unranked", engine="ollama", endpoint=ENDPOINT, model_id="c:1b",
            capabilities=["chat"],
        )
        ModelConnection.objects.create(
            name="aaa unranked", engine="ollama", endpoint=ENDPOINT, model_id="d:1b",
            capabilities=["chat"],
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        names = [o["label"].split(" (")[0] for o in role_views["rag.answer"]["options"]]
        assert names == ["first", "second", "aaa unranked", "zzz unranked"]

    def test_options_break_ties_by_name_case_insensitively(self, client):
        """Same rank: broken by name, case-insensitively -- duplicate
        ranks are allowed, never validated for uniqueness."""
        ModelConnection.objects.create(
            name="Bravo", engine="ollama", endpoint=ENDPOINT, model_id="a:1b",
            capabilities=["chat"], rank=1,
        )
        ModelConnection.objects.create(
            name="alpha", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=1,
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        names = [o["label"].split(" (")[0] for o in role_views["rag.answer"]["options"]]
        assert names == ["alpha", "Bravo"]

    def test_installed_unregistered_model_is_not_offered(self, client):
        """An
        installed model with NO registered connection appears in no
        dropdown -- its machine row's Add button is the way in, and the
        empty rag.embed dropdown says exactly that."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(
            client,
            [_installed_row("mxbai-embed-large:latest", capability="embeddings", embed_dim=1024)],
        )

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["options"] == []
        body = response.content.decode()
        assert "model:ollama" not in body
        assert "No registered embedding models" in body
        assert ">Add to registered<" in body

    def test_remote_connection_is_labeled_with_its_endpoint(self, client):
        remote = ModelConnection.objects.create(
            name="mistral", engine="ollama", endpoint="http://192.168.1.50:11434",
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        options = role_views["rag.answer"]["options"]
        assert [o["value"] for o in options] == [f"conn:{remote.pk}"]
        assert options[0]["label"] == "mistral (mistral:7b) @ http://192.168.1.50:11434"

    def test_capability_unknown_row_is_offered_to_no_role(self, client):
        """A degraded row (no engine-reported
        capability) appears in NO dropdown -- the never-guess boundary
        lives at registration, where its machine row links to the manual
        form and the operator states the capability explicitly."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(
            client, [_installed_row("mystery:latest", capability=None, capability_source=None)]
        )

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        for role_key in ("rag.answer", "rag.embed"):
            assert all(
                not o["value"].startswith("model:") for o in role_views[role_key]["options"]
            )
        assert "model:ollama::mystery:latest" not in response.content.decode()

    def test_empty_dropdown_explains_itself_with_a_machine_list_anchor(self, client):
        """A role with
        no matching REGISTERED connection renders helper text pointing at
        the machine list, where the Add buttons live, instead of a
        dead-end empty dropdown."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["options"] == []
        body = response.content.decode()
        assert "No registered embedding models" in body
        assert "add one from the machine list below" in body
        assert 'href="#on-this-machine"' in body
        assert 'aria-label="Change model for RAG embeddings"' not in body
        # The anchor's target exists on the page.
        assert 'id="on-this-machine"' in body

    # --- T9: multi-capability registration ---------------------------------

    def test_dual_capability_connection_appears_in_both_role_dropdowns(
        self, client, _extra_role_registry
    ):
        """The desired side effect this task names: a ["chat", "vision"]
        connection appears in BOTH the chat picker and the vision
        (rag.extract-shaped) dropdown -- the truth about the model; the
        operator's binding decides its job. A dedicated "test.vision" role
        is registered here (rather than relying on the ambient
        `RAG_EXTRACT_ROLE`, which only exists when "media" is in
        `FARABUNKER_FEATURES`) so this holds under either feature-flag
        state."""
        register_role(RoleSpec(key="test.vision", label="Vision test role", capability="vision"))
        connection = ModelConnection.objects.create(
            name="vlm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llava:13b", capabilities=["chat", "vision"],
        )

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        chat_values = [o["value"] for o in role_views["rag.answer"]["options"]]
        vision_values = [o["value"] for o in role_views["test.vision"]["options"]]
        assert f"conn:{connection.pk}" in chat_values
        assert f"conn:{connection.pk}" in vision_values


