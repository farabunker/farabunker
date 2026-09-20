"""The console page's shared nav and its cold/warm state rendering, plus
role_assign's connection picks, its honest bind-time family note, and
unbind (including the env-override case).

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

import pytest
from django.test import override_settings
from django.urls import reverse

from identity.contracts import actions
from identity.models import AuditEvent
from models.registry.models import Materialization, ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    CHAT_ENV_OVERRIDE,
    ENDPOINT,
    NO_ENV_OVERRIDE,
    _catalog_only_row,
    _extra_role_registry,  # noqa: F401 -- requested by name as a fixture
    _installed_row,
    _mock_engine,
    _only_rag_roles,
    _raising_engine,
    bind,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
    make_chat_connection,
    make_embed_connection,
)
from models.contracts.roles import (
    RAG_ANSWER_ROLE,
    RAG_EMBED_ROLE,
    VISION_GENERATE_ROLE,
    RoleSpec,
    register_role,
)


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


# --- ConsoleView: shared nav -------------------------------------------------


@pytest.mark.django_db
class TestConsoleViewNav:
    """The console extends the shared shell (foundation/templates/_shell.html), which
    owns the one app bar rendered on every page -- this asserts the console
    renders it with itself marked current."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_renders_shared_nav_with_console_marked_current(
        self, mock_engines, mock_discover, client
    ):
        # The Ask entry is availability-gated since UI-1, and this test
        # asserts it is present and NOT current -- so it has to be
        # available. The console's own entry needs no binding: Models is
        # one of the surfaces an operator fixes "nothing is bound" WITH,
        # and is always shown for exactly that reason. Since UI-2 that
        # entry lives in the settings sidebar rather than in the bar,
        # and the `current` mark is on it.
        bind(RAG_ANSWER_ROLE, make_chat_connection(name="nav answer conn"))
        bind(RAG_EMBED_ROLE, make_embed_connection(name="nav embed conn"))
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f'href="{reverse("rag-ask-page")}"' in body
        assert f'href="{reverse("rag-documents")}"' in body
        assert f'href="{reverse("rag-history")}"' in body
        assert f'href="{reverse("inference-console")}"' in body
        assert f'href="{reverse("inference-console")}" class="current"' in body
        assert f'href="{reverse("rag-ask-page")}" class="current"' not in body
        assert f'href="{reverse("rag-documents")}" class="current"' not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_the_operator_surfaces_are_shown_with_nothing_bound_at_all(
        self, mock_engines, mock_discover, client
    ):
        """The other half of UI-1 decision 2, and the important half for
        THIS page: the bar's Queue and Settings, and the settings
        sidebar's Models, Library and Install guides, all render on a box
        with no binding whatsoever. They are what an operator fixes that
        state with; gating them on availability would lock the door from
        the inside."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        body = client.get(reverse("inference-console")).content.decode()

        assert ">Queue</a>" in body
        assert ">Settings</a>" in body
        assert ">Models</a>" in body
        assert ">Library</a>" in body
        assert ">Install guides</a>" in body
        assert ">Ask</a>" not in body


# --- ConsoleView: cold start ------------------------------------------------


@pytest.mark.django_db
class TestConsoleViewColdStart:
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_no_connections_shows_getting_models_guidance(self, mock_engines, mock_discover, client):
        """Cold start renders the "Getting models" block -- needs
        checklist (all unsatisfied here: nothing installed) plus the
        adapter-derived server facts -- and NEVER a pinned model card. The
        catalog's model ids must not appear anywhere in the output."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is True
        body = response.content.decode()
        assert "No model connected yet" in body
        assert "Getting models" in body
        assert "missing — install one from the library below" in body
        # The old catalog cards (and every pinned model id) are gone.
        for model_id in ("qwen2.5:7b", "llama3.1:8b", "phi3:mini", "nomic-embed-text", "mxbai-embed-large"):
            assert model_id not in body
        assert "ollama pull qwen2.5:7b" not in body
        assert "https://ollama.com/library/qwen2.5" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_shows_needs_internet_and_posture_note(
        self, mock_engines, mock_discover, client
    ):
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "internet access once" in body
        assert "air-gapped" in body
        assert "gated-sync window" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unhealthy_engine_is_cold_start_even_with_a_connection(
        self, mock_engines, mock_discover, client
    ):
        ModelConnection.objects.create(
            name="local llama", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=False)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is True
        assert "No model connected yet" in response.content.decode()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_health_check_failure_never_500s(self, mock_engines, mock_discover, client):
        mock_engines.values.return_value = [_raising_engine(RuntimeError("engine registry blew up"))]
        mock_discover.side_effect = RuntimeError("discovery blew up")

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is True

    # --- Cold-start detects & offers installed models -----------------------

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_healthy_with_installed_models_leads_with_ready_to_use_section(
        self, mock_engines, mock_discover, client
    ):
        """A reachable, model-stocked engine with no connection
        registered yet leads with the SAME machine rows the warm page uses,
        each with its "Add to registered" button, above the SAME role rows
        -- whose dropdowns are EMPTY (nothing registered yet), showing the
        helper that points back at the machine list. First run teaches the
        permanent found -> registered -> in-use pipeline."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [
            _installed_row("llama3.1:8b", capability="chat"),
            _installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768),
            _catalog_only_row("qwen2.5:7b"),
        ]

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is True
        installed = response.context["installed_rows"]
        assert {item["row"].model_id for item in installed} == {
            "llama3.1:8b",
            "nomic-embed-text:latest",
        }

        # Registered connections are the ONLY dropdown source -- none exist
        # yet, so every role's dropdown is empty (no `model:` options).
        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["options"] == []
        assert role_views["rag.embed"]["options"] == []

        body = " ".join(response.content.decode().split())
        assert "Found on your machine — ready to use" in body
        # One Add button per detected machine row, each posting the row's
        # detected facts to machine_model_add.
        assert body.count(">Add to registered<") == 2
        assert f'action="{reverse("inference-machine-add")}"' in body
        assert 'name="model_id" value="llama3.1:8b"' in body
        assert 'name="model_id" value="nomic-embed-text:latest"' in body
        assert 'name="embed_dim" value="768"' in body
        assert "model:ollama" not in body
        # The role rows render with the empty-dropdown helper (no select,
        # no Apply) until something is registered.
        assert body.count(">Apply<") == 0
        assert "No registered chat models" in body
        assert "No registered embedding models" in body
        assert 'href="#on-this-machine"' in body
        assert 'id="on-this-machine"' in body
        # Both needs are met by the installed rows -- the
        # "Getting models" checklist shows live satisfied status; the
        # not-installed catalog row renders nowhere (no model cards).
        assert "✓ installed at your model server" in body
        assert "qwen2.5:7b" not in body
        assert "no models are installed yet" not in body
        assert "No model server could be reached" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_healthy_zero_installed_shows_install_guidance_no_ready_section(
        self, mock_engines, mock_discover, client
    ):
        """A reachable model server with nothing installed genuinely needs
        the operator to pull something first -- the "Getting models" block
        (every need unsatisfied) is the guidance, with no ready-to-use
        section and no model cards."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_catalog_only_row("qwen2.5:7b")]

        response = client.get(reverse("inference-console"))

        assert response.context["installed_rows"] == []
        assert all(not item["satisfied"] for item in response.context["needs_checklist"])

        body = " ".join(response.content.decode().split())
        assert "Found on your machine — ready to use" not in body
        assert "no models are installed yet" in body
        assert "Getting models" in body
        assert "missing — install one from the library below" in body
        assert "qwen2.5:7b" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unhealthy_shows_unreachable_copy_no_ready_section_no_healthy_copy(
        self, mock_engines, mock_discover, client
    ):
        """Live-finding fix: the page used to say "or the engine isn't
        reachable" directly under a passing health check. The unhealthy
        copy must only ever show when the health check actually failed, and
        must never claim reachability."""
        mock_engines.values.return_value = [_mock_engine(healthy=False)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["healthy"] is False
        assert response.context["installed_rows"] == []
        body = " ".join(response.content.decode().split())
        assert "No model server could be reached at" in body
        assert f"<code>{ENDPOINT}</code>" in body
        assert "Found on your machine — ready to use" not in body
        assert "is reachable" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_discover_exception_on_healthy_cold_start_degrades_to_pull_guidance_never_500(
        self, mock_engines, mock_discover, client
    ):
        """Per-engine failure isolation and the never-500 contract must
        hold on the cold branch too: a `discover()` exception (the engine
        itself is fine -- something else in discovery blew up) must degrade
        to the current pull-guidance page, never a 500 and never a fake
        ready-to-use section."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.side_effect = RuntimeError("discovery blew up")

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is True
        assert response.context["installed_rows"] == []
        assert all(not item["satisfied"] for item in response.context["needs_checklist"])
        body = response.content.decode()
        assert "No model connected yet" in body
        assert "Getting models" in body


# --- ConsoleView: warm state -------------------------------------------------


@pytest.mark.django_db
class TestConsoleViewWarm:
    def _make_chat_connection(self):
        return make_chat_connection()

    def _make_embed_connection(self, embed_dim=768):
        return make_embed_connection(embed_dim=embed_dim)

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_discover_failure_with_healthy_engine_and_connections_stays_warm(
        self, mock_engines, mock_discover, client
    ):
        """RULED: discovery failing is not itself a cold-start trigger -- a
        healthy engine with a registered connection stays on the warm page
        (role bindings visible) even if discover() blows up; only the
        discovered-models table goes empty."""
        self._make_chat_connection()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.side_effect = RuntimeError("discovery blew up")

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is False
        assert response.context["discovered"] == []
        assert "role_views" in response.context

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_lists_roles_with_current_bindings(self, mock_engines, mock_discover, client):
        chat = self._make_chat_connection()
        RoleBinding.objects.create(role_key="rag.answer", connection=chat)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.status_code == 200
        assert response.context["cold_start"] is False
        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert "rag.answer" in role_views
        assert "rag.embed" in role_views
        assert role_views["rag.answer"]["current_connection"] == chat

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_options_are_capability_filtered(self, mock_engines, mock_discover, client):
        chat = self._make_chat_connection()
        embed = self._make_embed_connection()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert [o["value"] for o in role_views["rag.answer"]["options"]] == [f"conn:{chat.pk}"]
        assert [o["value"] for o in role_views["rag.embed"]["options"]] == [f"conn:{embed.pk}"]

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_drifted_embeddings_role_shows_banner_with_severity_copy(
        self, mock_engines, mock_discover, client
    ):
        embed = self._make_embed_connection(embed_dim=768)
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:old-model:1024")
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["drifted"] is True
        assert "full document store rebuild" in role_views["rag.embed"]["severity"]
        assert "full document store rebuild" in response.content.decode()

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_never_materialized_role_is_not_drifted(self, mock_engines, mock_discover, client):
        embed = self._make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["drifted"] is False

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_materialized_note_has_no_double_period(self, mock_engines, mock_discover, client):
        """Django's "p.m." already ends in a period, so the template must
        not append its own sentence-ending period on top (that produced a
        double period: "2:31 p.m..")."""
        embed = self._make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:nomic-embed-text:768")
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "Last re-encoded" in body
        note = re.search(r'<p class="materialized-note">(.*?)</p>', body).group(1)
        assert ".." not in note

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_reencode_control_renders_for_a_materialized_non_drifted_embeddings_role(
        self, mock_engines, mock_discover, client
    ):
        """W5 review MAJOR 2: the ONLY form posting to `role_reencode`
        used to live inside `{% if view.drifted %}` -- so an operator
        whose embeddings role was materialized but NOT drifted (the
        ordinary case right after flipping `RagSettings.hybrid_search`,
        which changes nothing about the live table until a re-encode) had
        no way to reach it from this page at all, even though the enable-
        copy chain (flash message, history.html, README, ADR 0014 §18)
        all point here. Fingerprint matches the materialization exactly,
        so `drifted` is False -- the same non-drifted setup
        `test_materialized_note_has_no_double_period` above uses."""
        embed = self._make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:nomic-embed-text:768")
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["drifted"] is False
        body = response.content.decode()
        assert "Re-encode index" in body
        assert "Rebuilds every chunk" in body
        assert 'action="%s"' % reverse("inference-role-reencode") in body
        # Neutral wrapper for the non-drift case (fix: no amber warning
        # styling on a control that isn't reporting a problem). The page's
        # embedded stylesheet always defines `.drift-banner` (shared by
        # every role row), so this checks the rendered element's class,
        # not the CSS rule text.
        assert 'class="role-action"' in body
        assert 'class="drift-banner"' not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_reencode_control_does_not_render_for_a_chat_role(
        self, mock_engines, mock_discover, client
    ):
        """A chat role (`capability != "embeddings"`) never gets a
        `Materialization` row at all (only `rag.embed`'s rematerialize
        callback ever stamps one) -- the hoisted control's own guard
        (`view.role.capability == "embeddings" and view.current`, T-
        reencode-gate review) must keep it off `rag.answer`'s row even
        with a connection BOUND (`view.current` is truthy) and no
        drift/materialization anywhere on the page -- the capability check
        excludes it regardless of binding state."""
        connection = self._make_chat_connection()
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["materialization"] is None
        assert role_views["rag.answer"]["current"] is not None
        body = response.content.decode()
        assert "Re-encode index" not in body
        assert "Re-encode now" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_reencode_control_renders_for_a_bound_unmaterialized_embeddings_role(
        self, mock_engines, mock_discover, client
    ):
        """T-reencode-gate review (live evidence): the owner's live
        console had `rag.embed` bound (env: `EMBED_MODEL`) but with NO
        `Materialization` row at all -- the old gate
        (`view.role.capability == "embeddings" and view.materialization`)
        hid the control in exactly this state, so an operator enabling
        hybrid search there had no way to reach a first rebuild from the
        UI (the MAJOR the W5 fix was meant to close, reopened by the
        materialization-only gate). Here a `ModelConnection`/`RoleBinding`
        is created (`view.current_connection` bound, so `view.current` is
        truthy) with deliberately NO `Materialization` row -- the same
        never-materialized setup `test_never_materialized_role_is_not_
        drifted` uses. The control must render, with the unmaterialized
        caption (not the "Rebuilds every chunk" one, which implies a
        prior encode to redo)."""
        embed = self._make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["materialization"] is None
        assert role_views["rag.embed"]["current"] is not None
        body = response.content.decode()
        assert "Re-encode index" in body
        assert "Builds the chunk index from every stored document." in body
        assert "Rebuilds every chunk" not in body
        assert 'action="%s"' % reverse("inference-role-reencode") in body
        assert 'class="role-action"' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    @override_settings(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)
    def test_reencode_control_does_not_render_for_an_unbound_embeddings_role(
        self, mock_engines, mock_discover, client
    ):
        """The other side of the T-reencode-gate fix: a genuinely UNBOUND
        `rag.embed` (no connection, no environment override -- explicit
        settings override on top of `_clear_seeded_rows`) has `view.current`
        `None`, so the control must NOT render even though the role's
        capability is "embeddings" -- gating on "bound" must not regress
        into gating on "capability alone". This is also the state
        `role_reencode` itself would refuse (its own `except ValueError`
        around `resolve()`), so hiding the control here keeps the page
        from offering a control that's certain to be refused.

        A registered chat connection keeps the page warm (`_build_context`
        treats zero registered connections as cold start) while leaving
        `rag.embed` itself with nothing bound -- the one thing this test
        actually needs unbound."""
        self._make_chat_connection()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["current"] is None
        assert role_views["rag.embed"]["materialization"] is None
        body = response.content.decode()
        assert "Re-encode index" not in body
        assert "Re-encode now" not in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_drifted_role_still_shows_the_drift_copy_and_re_encode_now_label(
        self, mock_engines, mock_discover, client
    ):
        """W5 review MAJOR 2: the drift case is UNCHANGED by the hoist --
        still the drift sentence (not the new caption), still the "Re-
        encode now" button label (not "Re-encode index")."""
        embed = self._make_embed_connection(embed_dim=768)
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        Materialization.objects.create(role_key="rag.embed", fingerprint="ollama:old-model:1024")
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "This role's materialized data has drifted from its current binding" in body
        assert "Re-encode now" in body
        assert "Re-encode index" not in body
        assert "Rebuilds every chunk" not in body
        # Drift case keeps the amber warning wrapper (fix only touched the
        # non-drift branch).
        assert 'class="drift-banner"' in body
        assert 'class="role-action"' not in body


# --- role_assign: connection picks -------------------------------------------


@pytest.mark.django_db
class TestRoleAssignConnectionPick:
    """The role rows' one "change" dropdown posts EVERY pick to
    `role_assign`, and a registered connection arrives as a `conn:<pk>`
    choice: covers the embeddings confirmation gate, the first-rebind drift
    stamp, and tampered-id handling. Unbind lives on in
    `TestRoleUnbind` as the dropdown's `choice=unbind` option (deleting the bound
    connection still unbinds via SET_NULL)."""

    def _pick(self, client, role_key, connection, **extra):
        return client.post(
            reverse("inference-role-assign"),
            data={"role_key": role_key, "choice": f"conn:{connection.id}", **extra},
        )

    def _make_chat_connection(self):
        return make_chat_connection()

    def _make_old_embed(self):
        return make_embed_connection(name="old embed")

    def _make_new_embed(self, *, embed_dim=1024, name="new embed", model_id="mxbai-embed-large"):
        return make_embed_connection(embed_dim=embed_dim, name=name, model_id=model_id)

    def test_binding_a_chat_role_applies_immediately_no_confirm_needed(self, client):
        connection = self._make_chat_connection()

        response = self._pick(client, "rag.answer", connection)

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection == connection

    def test_a_valid_assign_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): `RoleBinding` assign/unbind was one of
        the four unaudited settings surfaces the backend audit named
        with no recorded rationale -- the recorded ruling is "audit all
        four"."""
        connection = self._make_chat_connection()

        self._pick(client, "rag.answer", connection)

        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert AuditEvent.objects.filter(action=actions.ROLE_ASSIGNED).count() == 1
        event = AuditEvent.objects.get(action=actions.ROLE_ASSIGNED)
        assert event.actor_kind == "open"
        assert event.target_key == str(binding.pk)
        assert event.detail["role_key"] == "rag.answer"
        assert event.detail["connection_id"] == connection.pk

    def test_embeddings_rebind_without_confirm_does_not_change_binding(self, client):
        old = self._make_old_embed()
        new = self._make_new_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=old)

        response = self._pick(client, "rag.embed", new)

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection == old  # unchanged

        followed = client.get(response.url)
        body = followed.content.decode()
        assert "full document store rebuild" in body  # embed_dim changed -> the more severe copy
        assert AuditEvent.objects.count() == 0

    def test_embeddings_rebind_same_dim_warns_with_reencode_in_place_copy(self, client):
        old = self._make_old_embed()
        new = self._make_new_embed(
            embed_dim=768, name="new embed same dim", model_id="other-embed-model"
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=old)

        response = self._pick(client, "rag.embed", new)

        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection == old

        followed = client.get(response.url)
        body = followed.content.decode()
        assert "re-encoded in place" in body

    def test_embeddings_rebind_with_confirm_applies(self, client):
        old = self._make_old_embed()
        new = self._make_new_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=old)

        response = self._pick(client, "rag.embed", new, confirm="yes")

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection == new

    def test_rebinding_to_the_same_connection_does_not_require_confirm(self, client):
        embed = self._make_old_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)

        response = self._pick(client, "rag.embed", embed)

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "full document store rebuild" not in followed.content.decode()

    def test_first_confirmed_embeddings_rebind_stamps_pre_rebind_fingerprint(self, client):
        """RULED: with no Materialization row, `is_drifted()` reports False
        (that semantic stands), so the FIRST embeddings rebind would never
        show the drift banner. Applying a confirmed embeddings rebind with
        no row stamps the PRE-rebind active fingerprint (the old
        connection's) so the banner appears immediately after the rebind."""
        old = self._make_old_embed()
        new = self._make_new_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=old)
        assert not Materialization.objects.filter(role_key="rag.embed").exists()

        response = self._pick(client, "rag.embed", new, confirm="yes")

        assert response.status_code == 302
        materialization = Materialization.objects.get(role_key="rag.embed")
        # The OLD binding's fingerprint (pre-rebind), not the new one's --
        # that's what the existing data was encoded under.
        assert materialization.fingerprint == "ollama:nomic-embed-text:768"

    def test_confirmed_embeddings_rebind_does_not_overwrite_an_existing_stamp(self, client):
        old = self._make_old_embed()
        new = self._make_new_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=old)
        Materialization.objects.create(
            role_key="rag.embed", fingerprint="ollama:even-older-model:512"
        )

        self._pick(client, "rag.embed", new, confirm="yes")

        # The existing stamp records what the data was actually last
        # materialized against; a rebind must not touch it.
        assert Materialization.objects.filter(role_key="rag.embed").count() == 1
        assert (
            Materialization.objects.get(role_key="rag.embed").fingerprint
            == "ollama:even-older-model:512"
        )

    def test_binding_a_chat_role_does_not_stamp_materialization(self, client):
        connection = self._make_chat_connection()

        self._pick(client, "rag.answer", connection)

        assert not Materialization.objects.filter(role_key="rag.answer").exists()

    def test_rebinding_embeddings_to_the_same_connection_does_not_stamp(self, client):
        embed = self._make_old_embed()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)

        self._pick(client, "rag.embed", embed)

        # No actual change -- nothing drifted, nothing to stamp.
        assert not Materialization.objects.filter(role_key="rag.embed").exists()

    def test_nonnumeric_connection_pk_does_not_500(self, client):
        """A tampered/stale option value (non-numeric pk) must be handled as
        "no such connection", not raise ValueError out of the ORM lookup."""
        response = client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": "conn:not-a-number"},
        )

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "Selected connection no longer exists" in followed.content.decode()
        assert RoleBinding.objects.filter(role_key__iexact="rag.answer").count() == 0

    def test_nonexistent_connection_pk_errors(self, client):
        response = client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": "conn:999999"},
        )

        assert response.status_code == 302
        followed = client.get(response.url)
        assert "Selected connection no longer exists" in followed.content.decode()
        assert RoleBinding.objects.filter(role_key__iexact="rag.answer").count() == 0

    def test_empty_choice_is_a_silent_noop(self, client):
        """The select is not `required`, so clicking Apply
        with nothing chosen (the placeholder option, choice="") is a
        routine no-op -- no binding, no connection, no 500, and no error
        message (that would scold an operator for a normal, harmless
        click)."""
        response = client.post(
            reverse("inference-role-assign"), data={"role_key": "rag.answer", "choice": ""}
        )

        assert response.status_code == 302
        assert RoleBinding.objects.filter(role_key__iexact="rag.answer").count() == 0
        assert not ModelConnection.objects.exists()
        followed = client.get(response.url)
        assert "Choose a model to assign" not in followed.content.decode()

    def test_unrecognized_choice_scheme_errors(self, client):
        response = client.post(
            reverse("inference-role-assign"),
            data={"role_key": "rag.answer", "choice": "garbage:whatever"},
        )

        assert response.status_code == 302
        assert RoleBinding.objects.filter(role_key__iexact="rag.answer").count() == 0
        followed = client.get(response.url)
        assert "Choose a model to assign" in followed.content.decode()

    def test_stale_model_choice_is_an_unrecognized_scheme_and_creates_nothing(self, client):
        """`role_assign` has no `model:` branch: a stale form
        (rendered before a page restructure) or a tampered value posting the
        old `model:<engine>:<dim>:<id>` scheme must land in the
        unrecognized-scheme error -- and must
        NEVER silently create a ModelConnection as a side effect."""
        response = client.post(
            reverse("inference-role-assign"),
            data={
                "role_key": "rag.answer",
                "choice": "model:ollama::llama3.1:8b",
                "endpoint": ENDPOINT,
            },
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()
        assert RoleBinding.objects.filter(role_key__iexact="rag.answer").count() == 0
        followed = client.get(response.url)
        assert "Choose a model to assign" in followed.content.decode()

    def test_get_is_not_allowed(self, client):
        response = client.get(reverse("inference-role-assign"))
        assert response.status_code == 405

    def test_unknown_role_key_errors(self, client):
        connection = self._make_chat_connection()

        response = self._pick(client, "does.not.exist", connection)

        assert response.status_code == 302
        assert not RoleBinding.objects.exists()
        followed = client.get(response.url)
        assert "Unknown role" in followed.content.decode()

    def test_conn_pick_does_not_mutate_the_connection(self, client):
        """A conn-pick binds; it never tops up the connection's own
        capabilities/embed_dim (that top-up is the model-pick path's, fed by
        live engine facts). Even a tampered cross-capability pick must not
        rewrite the stored connection."""
        embed = self._make_old_embed()

        self._pick(client, "rag.answer", embed)  # tampered: chat role, embeddings conn

        embed.refresh_from_db()
        assert embed.capabilities == ["embeddings"]
        assert embed.embed_dim == 768

    def test_confirm_gate_falls_back_to_conservative_dim_changed_when_unresolvable(
        self, client, _extra_role_registry
    ):
        """A freshly-registered embeddings role with no DB binding and no
        env-provider fallback (`env_provider` only knows rag.answer/
        rag.embed) has no current connection to compare dimensions against
        -- the gate must default to the more severe copy rather than 500."""
        register_role(
            RoleSpec(key="test.unresolvable-embed", label="Unresolvable Embed", capability="embeddings")
        )
        connection = self._make_new_embed()

        response = self._pick(client, "test.unresolvable-embed", connection)

        assert response.status_code == 302
        assert RoleBinding.objects.filter(role_key__iexact="test.unresolvable-embed").count() == 0
        followed = client.get(response.url)
        assert "full document store rebuild" in followed.content.decode()

    def test_first_rebind_stamp_falls_back_to_no_stamp_when_resolve_raises(
        self, client, _extra_role_registry
    ):
        """Same unresolvable-binding role, confirmed this time: the
        first-rebind stamp's own `resolve()` call raises -- the
        `except Exception` there must default `pre_fingerprint=None` (skip
        the stamp, not 500), while the bind itself still applies."""
        register_role(
            RoleSpec(key="test.unresolvable-embed", label="Unresolvable Embed", capability="embeddings")
        )
        connection = self._make_new_embed()

        response = self._pick(client, "test.unresolvable-embed", connection, confirm="yes")

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="test.unresolvable-embed")
        assert binding.connection == connection
        assert not Materialization.objects.filter(role_key="test.unresolvable-embed").exists()


# --- role_assign: honest bind-time note (owner incident) --------------------


@pytest.mark.django_db
class TestRoleAssignFamilyBindNote:
    """Binding a role to a connection that cannot actually run any
    operation yet -- a declared family with no graph template, or an
    undeclared family on a diffusion-model connection -- gets an honest,
    NON-BLOCKING note alongside the normal "Assigned" message
    (`_family_bind_note`). The bind itself always applies; nothing here
    gates it. Mocked at the `ENGINES` registry, matching this file's own
    convention (see the module docstring) since `role_assign` reaches the
    engine adapter directly, not over HTTP."""

    def _comfyui_engine(self):
        engine = MagicMock()
        engine.name = "comfyui"
        engine.supported_operations = MagicMock(return_value=())
        engine.list_installed = MagicMock(return_value=[])
        return engine

    def test_declared_family_with_no_template_gets_an_honest_note(self, client):
        connection = ModelConnection.objects.create(
            name="flux2 conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2"},
        )
        engine = self._comfyui_engine()

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact=VISION_GENERATE_ROLE)
        assert binding.connection == connection
        engine.supported_operations.assert_called_once_with("weights.gguf", ENDPOINT, "flux2")
        body = client.get(response.url).content.decode()
        assert "Assigned" in body
        assert "flux2" in body
        assert "no operations" in body

    def test_undeclared_family_on_a_diffusion_model_connection_gets_an_honest_note(self, client):
        connection = ModelConnection.objects.create(
            name="raw gguf conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
        )
        engine = self._comfyui_engine()
        engine.list_installed.return_value = [MagicMock(model_id="weights.gguf", loader="UnetLoaderGGUF")]

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        assert response.status_code == 302
        body = client.get(response.url).content.decode()
        assert "needs a model family declared" in body

    def test_a_genuine_checkpoint_with_no_family_gets_no_note(self, client):
        """The self-contained loader must never trip the note -- a plain
        checkpoint needs no family and none of this applies to it."""
        connection = ModelConnection.objects.create(
            name="checkpoint conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="sdxl.safetensors",
            capabilities=["image-generation"],
        )
        engine = self._comfyui_engine()
        engine.list_installed.return_value = [
            MagicMock(model_id="sdxl.safetensors", loader="CheckpointLoaderSimple")
        ]

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        assert response.status_code == 302
        body = client.get(response.url).content.decode()
        assert "Assigned" in body
        assert "needs a model family" not in body
        assert "has no operations" not in body

    def test_a_family_declared_without_its_companions_gets_an_honest_note(self, client):
        """Finding 7: a graph TEMPLATE existing for `(family, operation)`
        (`supported_operations` non-empty) does not mean the connection can
        actually run it -- `_fragments._declared` (models/contracts/engines/
        comfyui_workflows/_fragments.py) still refuses the JOB at run time
        if `text_encoder`/`vae` were left blank at registration (ADR 0012
        D-EDIT-3). The bind-time note must catch this BEFORE a job ever
        reaches the engine, the same honesty the no-template case already
        gets."""
        connection = ModelConnection.objects.create(
            name="half-declared flux2 conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2"},  # text_encoder/vae left blank
        )
        engine = self._comfyui_engine()
        # A template DOES exist for this family -- proving the note isn't
        # just re-testing the no-template case.
        engine.supported_operations.return_value = ("edit",)

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact=VISION_GENERATE_ROLE)
        assert binding.connection == connection
        body = client.get(response.url).content.decode()
        assert "Assigned" in body
        assert "a text encoder" in body
        assert "a VAE" in body

    def test_a_family_declared_with_only_one_companion_missing_names_just_that_one(self, client):
        connection = ModelConnection.objects.create(
            name="one-companion-missing conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf"},  # vae left blank
        )
        engine = self._comfyui_engine()
        engine.supported_operations.return_value = ("edit",)

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        body = client.get(response.url).content.decode()
        assert "a VAE" in body
        assert "a text encoder" not in body

    def test_a_family_declared_with_both_companions_gets_no_missing_companion_note(self, client):
        """The no-template note (already covered above) and this one must
        not both fire, and a fully-declared family gets neither."""
        connection = ModelConnection.objects.create(
            name="fully-declared flux2 conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )
        engine = self._comfyui_engine()
        engine.supported_operations.return_value = ("edit",)

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        body = client.get(response.url).content.decode()
        assert "Assigned" in body
        assert "a text encoder" not in body
        assert "a VAE" not in body
        assert "no operations" not in body

    def test_note_computation_never_blocks_the_bind_when_the_engine_errors(self, client):
        """A note is never worth blocking a bind that already succeeded --
        an engine call that raises must still leave the bind applied and
        the page must never 500."""
        connection = ModelConnection.objects.create(
            name="flaky conn",
            engine="comfyui",
            endpoint=ENDPOINT,
            model_id="weights.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2"},
        )
        engine = self._comfyui_engine()
        engine.supported_operations.side_effect = RuntimeError("engine unreachable")

        with patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": VISION_GENERATE_ROLE, "choice": f"conn:{connection.pk}"},
            )

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact=VISION_GENERATE_ROLE)
        assert binding.connection == connection


# --- role_assign: unbind (I1 -- binding must not be a one-way door) ----------


@pytest.mark.django_db
class TestRoleUnbind:
    """Review I1: the dropdown carries an UNASSIGN option (`choice=unbind`)
    while a connection is bound -- restoring the old `role_bind` unbind
    path, including its embeddings confirm gate, in the new grammar.
    Without it, binding was a one-way door.

    "Unbind" does not mean "back to environment
    defaults" -- there are none. The class-level `override_settings` makes
    the ordinary state EXPLICIT rather than inherited from whatever this
    machine's environment happens to hold: no override set, so unassigning
    leaves the role with NO model. `TestRoleUnbindWithAnEnvOverride` below
    covers the other case, equally explicitly."""

    def _unbind(self, client, role_key, **extra):
        return client.post(
            reverse("inference-role-assign"),
            data={"role_key": role_key, "choice": "unbind", **extra},
        )

    def _make_chat_bound(self):
        connection = make_chat_connection()
        bind("rag.answer", connection)
        return connection

    def _make_embed_bound(self):
        connection = make_embed_connection()
        bind("rag.embed", connection)
        return connection

    @override_settings(**NO_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unbind_clears_binding_and_leaves_the_role_unassigned(
        self, mock_engines, mock_discover, client
    ):
        """With no env override set, unassigning leaves NOTHING backing the
        role -- and the page says exactly that, warning-styled, instead of
        claiming a default took over."""
        self._make_chat_bound()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = self._unbind(client, "rag.answer")

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.answer")
        assert binding.connection is None

        followed = client.get(response.url)
        body = followed.content.decode()
        assert (
            "RAG answer is now unassigned — no model will back it and anything "
            "that needs it reports unavailable until you assign one." in body
        )
        role_views = {v["role"].key: v for v in followed.context["role_views"]}
        assert role_views["rag.answer"]["current_connection"] is None
        assert role_views["rag.answer"]["current"] is None  # nothing resolves it
        assert role_views["rag.answer"]["env_override"] is None
        assert '<span class="unassigned-tag">No model assigned</span>' in body
        assert "environment defaults" not in body
        assert AuditEvent.objects.filter(action=actions.ROLE_UNASSIGNED).count() == 1
        event = AuditEvent.objects.get(action=actions.ROLE_UNASSIGNED)
        assert event.actor_kind == "open"
        assert event.target_key == str(binding.pk)
        assert event.detail["role_key"] == "rag.answer"

    @override_settings(**NO_ENV_OVERRIDE)
    def test_embeddings_unbind_without_confirm_refuses(self, client):
        """The old role_bind's gated-unbind behavior, restored: unassigning
        an embeddings role takes the active model away -- same drift risk as
        any rebind, warned conservatively -- and the gate copy now states
        the true consequence."""
        connection = self._make_embed_bound()

        response = self._unbind(client, "rag.embed")

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection == connection  # unchanged

        followed = client.get(response.url)
        body = followed.content.decode()
        assert (
            "Unassigning RAG embeddings — no model will back it and anything "
            "that needs it reports unavailable until you assign one" in body
        )
        assert "full document store rebuild" in body  # conservative severity

    @override_settings(**NO_ENV_OVERRIDE)
    def test_embeddings_unbind_with_confirm_applies_and_stamps(self, client):
        connection = self._make_embed_bound()
        assert not Materialization.objects.filter(role_key="rag.embed").exists()

        response = self._unbind(client, "rag.embed", confirm="yes")

        assert response.status_code == 302
        binding = RoleBinding.objects.get(role_key__iexact="rag.embed")
        assert binding.connection is None
        # Same first-rebind stamp as any other confirmed embeddings change:
        # the PRE-unbind fingerprint, so the banner appears immediately.
        materialization = Materialization.objects.get(role_key="rag.embed")
        assert materialization.fingerprint == "ollama:nomic-embed-text:768"

    @override_settings(**NO_ENV_OVERRIDE)
    def test_unbind_when_already_unbound_is_a_no_op(self, client):
        """No change -> no gate, no stamp -- even for embeddings, even
        without confirm (a tampered/stale unbind post on an unbound role
        must not warn or 500)."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._unbind(client, "rag.embed")

        assert response.status_code == 302
        assert not Materialization.objects.exists()
        followed = client.get(response.url)
        assert "full document store rebuild" not in followed.content.decode()

    @override_settings(**NO_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unbind_option_renders_only_while_bound(
        self, mock_engines, mock_discover, client
    ):
        """rag.answer bound, rag.embed unassigned: exactly one unassign
        option on the page -- an unassigned role has nothing to unassign."""
        self._make_chat_bound()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert body.count('value="unbind"') == 1
        # Its label states the real consequence, and the bound row's
        # disclosure says the same thing at length.
        assert "unassign — leave this role with no model" in body
        assert (
            "This connection is the only thing backing this role — unassigning "
            "it leaves the role with no model at all, not a default." in body
        )


@pytest.mark.django_db
class TestRoleUnbindWithAnEnvOverride:
    """The other half of the same honesty rule: an operator who
    DELIBERATELY pinned a role from the environment gets copy that says the
    override takes over -- because for them, it does. The override is set
    EXPLICITLY here, never inherited from the ambient environment."""

    def _make_chat_bound(self):
        connection = make_chat_connection()
        bind("rag.answer", connection)
        return connection

    @override_settings(**CHAT_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unassign_option_and_disclosure_name_the_override(
        self, mock_engines, mock_discover, client
    ):
        self._make_chat_bound()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        body = response.content.decode()
        assert "unassign — fall back to the environment override" in body
        assert (
            "This role also has an explicit environment override, not in effect "
            "while this connection is assigned" in body
        )
        assert "environment defaults" not in body

    @override_settings(**CHAT_ENV_OVERRIDE)
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_unassign_message_says_the_override_takes_over(
        self, mock_engines, mock_discover, client
    ):
        self._make_chat_bound()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        # Pinned to exactly the two RAG roles (see `_only_rag_roles`): the
        # "exactly one warning banner" assertion below is about THIS pair,
        # not a claim that no other role can ever be registered.
        with _only_rag_roles():
            response = client.post(
                reverse("inference-role-assign"),
                data={"role_key": "rag.answer", "choice": "unbind"},
            )
            followed = client.get(response.url)

            body = followed.content.decode()
            assert (
                "RAG answer is now unassigned — the explicit environment override "
                "for this role takes over." in body
            )
            # And the role row now shows the override, labeled as the operator's
            # deliberate choice -- never as a "default".
            assert '<span class="badge chip">explicit environment override</span>' in body
            role_views = {v["role"].key: v for v in followed.context["role_views"]}
            assert role_views["rag.answer"]["current"].model_id == "operator-chat-choice"
            # rag.embed, which this operator did NOT pin, is the unassigned one
            # -- exactly one warning banner on the page, and it isn't this role's.
            assert role_views["rag.embed"]["current"] is None
            assert body.count('class="unassigned-banner"') == 1


