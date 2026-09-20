"""Each machine row's found -> registered -> in-use pipeline state, the
prefilled-manual-form link, the orphan-connection label, the per-row
settings transparency disclosure, `connection_add`'s update (inline edit)
mode, and the collapsed manual create form.

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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from identity.contracts import actions
from identity.models import AuditEvent
from models.registry.models import Materialization, ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    ENDPOINT,
    _installed_row,
    _mock_engine,
    _only_rag_roles,
    bind,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
    make_chat_connection,
    make_embed_connection,
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


# --- Machine-row pipeline state + the loaded-now chip -------------------------


def _manual_add_href(body: str) -> str:
    """The degraded row's "Add to registered manually" href, sliced out
    of a rendered console so a test asserts what the LINK carries rather
    than what the page happens to contain somewhere (the assistant
    panel's own closed bubble already writes `?assistant=1` into every
    settings page, which is exactly what a bare substring check would
    answer to)."""
    match = re.search(r'<a class="manual-add-link" href="([^"]+)"', body)
    assert match is not None, "no manual-add link rendered"
    return match.group(1)


@pytest.mark.django_db
class TestMachineRowPipelineState:
    """Each "On this machine" row renders its found -> registered -> in-use
    state: the "Add to registered" button (unregistered, capability
    detected), the prefilled-manual-form link (unregistered, capability
    unknown -- never a guessed registration), or the "registered" chip
    (plus an "in use" chip while bound) -- and the loaded-now truth from
    the already-fetched /api/ps report. Both chips are non-wrapping
    `.badge` pills, not prose that could break mid-phrase."""

    def _get(self, client, discovered, query=""):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = discovered
            return client.get(reverse("inference-console") + query)

    def _make_warm_connection(self):
        """Any registered connection keeps the page warm without matching
        the rows under test."""
        return make_chat_connection(name="warm conn", model_id="warm-model:latest")

    def test_unregistered_detected_row_renders_the_add_form_with_detected_fields(self, client):
        self._make_warm_connection()

        response = self._get(
            client,
            [_installed_row("mxbai-embed-large:latest", capability="embeddings", embed_dim=1024)],
        )

        body = response.content.decode()
        assert ">Add to registered<" in body
        assert f'action="{reverse("inference-machine-add")}"' in body
        form_start = body.index('class="inline-form add-to-registered"')
        form_end = body.index("</form>", form_start)
        add_form = body[form_start:form_end]
        assert 'name="engine" value="ollama"' in add_form
        assert 'name="model_id" value="mxbai-embed-large:latest"' in add_form
        assert f'name="endpoint" value="{ENDPOINT}"' in add_form
        assert 'name="capability" value="embeddings"' in add_form
        assert 'name="embed_dim" value="1024"' in add_form

    # --- T9: multi-capability registration ---------------------------------

    def test_multi_capability_row_posts_one_hidden_input_per_capability(self, client):
        """A llava/qwen-vl-class row detected as chat+vision posts BOTH as
        separate hidden `capability` inputs, all named "capability" -- the
        server reads the whole set back with `getlist`."""
        self._make_warm_connection()

        response = self._get(
            client,
            [_installed_row("llava:13b", capability="chat", capabilities=("chat", "vision"))],
        )

        body = response.content.decode()
        form_start = body.index('class="inline-form add-to-registered"')
        form_end = body.index("</form>", form_start)
        add_form = body[form_start:form_end]
        assert add_form.count('name="capability"') == 2
        assert 'name="capability" value="chat"' in add_form
        assert 'name="capability" value="vision"' in add_form

    def test_capability_cell_renders_the_joined_list(self, client):
        """Scan/discovery display: the Capability cell renders every
        detected capability, joined -- "chat, vision" -- the truth about
        the model, not just whichever one the tie-break chose as primary."""
        self._make_warm_connection()

        response = self._get(
            client,
            [_installed_row("llava:13b", capability="chat", capabilities=("chat", "vision"))],
        )

        body = response.content.decode()
        assert "chat, vision" in body

    def test_capability_cell_has_no_leaked_template_comment(self, client):
        """Regression: `_installed_row.html`'s T9 capability-cell comment
        used a `{# ... #}` spanning multiple lines. Django's `{# #}` is
        single-line only, so anything past the first line rendered as
        literal text in the Capability column. Assert the raw comment
        markers never leak into the rendered page, and that the comment's
        own wording -- unique to this row-level comment, unlike the
        unrelated "T9:" CSS comment elsewhere on the page -- doesn't
        either."""
        self._make_warm_connection()

        response = self._get(
            client,
            [_installed_row("llava:13b", capability="chat", capabilities=("chat", "vision"))],
        )

        body = response.content.decode()
        assert "{#" not in body
        assert "#}" not in body
        assert "tie-break chose as primary" not in body
        assert "FULL reported capability set, joined" not in body

    def test_capability_unknown_row_links_to_the_prefilled_manual_form_instead(self, client):
        self._make_warm_connection()

        response = self._get(
            client, [_installed_row("mystery:latest", capability=None, capability_source=None)]
        )

        body = response.content.decode()
        assert ">Add to registered<" not in body
        assert "Add to registered manually" in body
        assert "prefill_model_id=mystery%3Alatest" in body
        assert "prefill_endpoint=http%3A//localhost%3A11434" in body
        assert "#add-connection" in body
        # The anchor's target exists on the page.
        assert 'id="add-connection"' in body

    def test_the_manual_form_link_keeps_the_assistant_panel_open(self, client):
        """REVIEW ROUND 1, M1. This anchor navigates console -> console,
        and `inference-console` IS a settings page -- so it is a settings
        link like any in the sidebar, and it was dropping the assistant
        panel's open flag on a page that could hold it.

        `&amp;assistant=1`, not the `{{ assistant.open_query }}` suffix
        every settings FORM writes: this href already carries a query
        (the two prefill parameters), so the joiner is `&`, and it has to
        land BEFORE the `#add-connection` fragment or no query parser
        would ever see it -- the same rule `foundation.settings_area.
        with_assistant_flag` follows in Python for the redirects.
        """
        self._make_warm_connection()

        response = self._get(
            client, [_installed_row("mystery:latest", capability=None, capability_source=None)],
            query="?assistant=1",
        )

        href = _manual_add_href(response.content.decode())
        assert href.endswith("&amp;assistant=1#add-connection"), href
        assert "prefill_model_id=mystery%3Alatest" in href

    def test_the_manual_form_link_is_bare_with_the_panel_shut(self, client):
        self._make_warm_connection()

        response = self._get(
            client, [_installed_row("mystery:latest", capability=None, capability_source=None)]
        )

        href = _manual_add_href(response.content.decode())
        assert "assistant" not in href, href
        assert href.endswith("#add-connection"), href

    def test_manual_form_link_prefills_the_rows_own_endpoint(self, client):
        """D11: a row discovered at a second engine's address prefills THAT
        address -- the same value the "Add to registered" form posts. The
        link used to hand the manual form whichever endpoint the page
        happened to be viewing, quietly registering the model at the wrong
        address."""
        self._make_warm_connection()

        response = self._get(
            client,
            [
                _installed_row(
                    "mystery:latest",
                    capability=None,
                    capability_source=None,
                    endpoint="http://box:8188",
                )
            ],
        )

        body = response.content.decode()
        assert "Add to registered manually" in body
        assert "prefill_endpoint=http%3A//box%3A8188" in body
        assert "prefill_endpoint=http%3A//localhost%3A11434" not in body

    def test_installed_row_connection_details_shows_the_rows_own_endpoint(self, client):
        """D11 fix-wave item 1: the Connection-details disclosure used to
        render the PAGE's endpoint (`endpoint`) instead of the row's own
        (`item.endpoint`, D11) -- so a row discovered at a second engine's
        address quietly showed the page's address in its own disclosure,
        contradicting the manual-form-link fix above (lines 112/118)."""
        self._make_warm_connection()

        response = self._get(
            client,
            [
                _installed_row(
                    "mystery:latest",
                    capability=None,
                    capability_source=None,
                    endpoint="http://box:8188",
                )
            ],
        )

        body = response.content.decode()
        # Anchor on the row's own Model ID line inside its disclosure --
        # "Connection details" alone also matches a page-CSS comment and
        # (were one bound) a role row's own disclosure, so this scopes
        # strictly to this row's `<dl>` (Model ID immediately precedes
        # Endpoint, lines 63-64 of _installed_row.html).
        marker_pos = body.index("<code>mystery:latest</code>")
        window = body[marker_pos:marker_pos + 500]
        assert "http://box:8188" in window
        assert "localhost:11434" not in window

    def test_registered_row_shows_the_chip_not_the_button(self, client):
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(
            client,
            [_installed_row("nomic-embed-text:latest", capability="embeddings", embed_dim=768)],
        )

        body = response.content.decode()
        assert "registered ✓" in body
        assert ">Add to registered<" not in body
        assert "Add to registered manually" not in body
        # Registered but unbound: no "in use" chip yet.
        assert '<span class="badge healthy">in use</span>' not in body

    def test_registered_and_bound_row_shows_both_chip_and_in_use(self, client):
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
        assert "registered ✓" in body
        assert '<span class="badge healthy">in use</span>' in body
        assert ">Add to registered<" not in body

    def test_loaded_row_shows_the_memory_chip_with_humanized_size(self, client):
        """The 44GB-invisible gap, closed: a loaded model renders "loaded ·
        X GB" straight from the already-fetched /api/ps truth
        (`DiscoveryRow.loaded_size`) -- humanized the same way the on-disk
        size is. The section intro already explains what
        "loaded" means, so the cell itself is just the fact -- no bullet,
        no "in memory"."""
        self._make_warm_connection()

        response = self._get(
            client,
            [
                _installed_row(
                    "qwen2.5:72b", capability="chat",
                    loaded=True, loaded_size=47_200_000_000, size=47_200_000_000,
                )
            ],
        )

        item = next(
            i
            for i in response.context["installed_rows"]
            if i["row"].model_id == "qwen2.5:72b"
        )
        assert item["loaded_size_display"] == "44.0 GB"
        body = response.content.decode()
        assert "loaded · 44.0 GB" in body
        assert "idle" not in body.split("qwen2.5:72b", 1)[1].split("</div>")[0]

    def test_loaded_row_without_a_reported_size_still_shows_loaded(self, client):
        self._make_warm_connection()

        response = self._get(
            client,
            [_installed_row("qwen2.5:72b", capability="chat", loaded=True, loaded_size=None)],
        )

        body = response.content.decode()
        assert '<span class="loaded-tag"' in body
        assert ">loaded<" in body
        assert "loaded · " not in body

    def test_idle_row_says_idle(self, client):
        """The section intro already carries the loads-on-demand meaning;
        the cell itself is just "idle"."""
        self._make_warm_connection()

        response = self._get(client, [_installed_row("llama3.1:8b", capability="chat")])

        body = response.content.decode()
        assert ">idle<" in body
        assert "loads on demand" not in body
        assert '<span class="loaded-tag"' not in body

    def test_lifecycle_sentence_renders_in_the_machine_section_header(self, client):
        """The intro is one
        line, with no quoted-term glossary clause, that carries the
        load-on-first-use/unload-when-idle meaning."""
        self._make_warm_connection()

        response = self._get(client, [_installed_row("llama3.1:8b", capability="chat")])

        body = " ".join(response.content.decode().split())
        assert "reload to rescan. Models load on first use and unload when idle." in body
        assert "“loaded” means in memory right now" not in body


@pytest.mark.django_db
class TestManualFormPrefill:
    """The degraded rows' manual-form link: `?prefill_model_id=` /
    `?prefill_endpoint=` prefill the manual form's fields and pop it open,
    so the operator only supplies what detection could not -- the
    capability (and dimension)."""

    def _get(self, client, params=None):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            return client.get(reverse("inference-console"), params or {})

    def test_prefill_params_populate_the_form_and_open_the_details(self, client):
        ModelConnection.objects.create(
            name="warm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="warm-model:latest", capabilities=["chat"],
        )

        response = self._get(
            client,
            {"prefill_model_id": "mystery:latest", "prefill_endpoint": ENDPOINT},
        )

        body = response.content.decode()
        assert '<details class="add-connection-details" open>' in body
        assert 'name="model_id" value="mystery:latest"' in body
        assert f'name="endpoint" value="{ENDPOINT}"' in body

    def test_without_prefill_the_form_stays_collapsed_and_blank(self, client):
        ModelConnection.objects.create(
            name="warm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="warm-model:latest", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert '<details class="add-connection-details">' in body
        assert '<details class="add-connection-details" open>' not in body
        assert 'name="model_id" value=""' in body

    def test_prefill_works_on_the_cold_start_form_too(self, client):
        response = self._get(
            client,
            {"prefill_model_id": "mystery:latest", "prefill_endpoint": ENDPOINT},
        )

        assert response.context["cold_start"] is True
        body = response.content.decode()
        assert '<details class="add-connection-details" open>' in body
        assert 'name="model_id" value="mystery:latest"' in body

    def test_prefill_values_are_escaped_never_executed(self, client):
        """A tampered prefill is inert: template-escaped into the value
        attribute, never a 500."""
        response = self._get(
            client, {"prefill_model_id": '"><script>alert(1)</script>'}
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "<script>alert(1)</script>" not in body


@pytest.mark.django_db
class TestOrphanConnectionLabel:
    """The inverse of the existing "used by: X" note -- a
    registered connection no role uses says "not used by any role", so an
    orphan registration is never a mystery record."""

    def _get(self, client):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            return client.get(reverse("inference-console"))

    def test_unbound_connection_is_labeled_not_used_by_any_role(self, client):
        ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert "not used by any role" in body
        assert "used by: " not in body

    def test_bound_connection_keeps_the_used_by_note_and_no_orphan_label(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._get(client)

        body = response.content.decode()
        # Each bound role label is its own non-wrapping chip,
        # not a plain comma-joined string.
        assert "used by:" in body
        assert '<span class="badge chip">RAG answer</span>' in body
        assert "not used by any role" not in body


# --- Per-row settings transparency (details disclosures) ---------------------


@pytest.mark.django_db
class TestConnectionDetailsDisclosure:
    """"Nothing hidden": every dynamic value used to
    connect -- endpoint, engine, exact model id, embedding dimension -- and
    the SOURCE of each must be visible, behind a native `<details>`.

    "One source of truth": the role-row and
    installed-row disclosures are READ-ONLY reflections -- no edit form
    lives on either any more. A reflection of a REGISTERED connection ends
    with a plain anchor link to that connection's one edit form in the
    "Registered connections" section (`#conn-<pk>`); env-fallback and
    installed-but-unregistered reflections have nothing to edit, so they
    get no link.
    """

    def _get(self, client, discovered):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = discovered
            return client.get(reverse("inference-console"))

    def test_registered_connection_role_row_is_a_read_only_reflection_with_anchor_link(
        self, client
    ):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._get(client, [])

        body = response.content.decode()
        assert "Connection details" in body
        assert "<code>nomic-embed-text</code>" in body
        assert "sent to the API as-is" in body
        assert f"<code>{ENDPOINT}</code>" in body
        assert "“embed conn”, registered" in body
        assert f'href="#conn-{connection.pk}"' in body
        assert "Edit in Registered connections ↓" in body
        # Exactly ONE edit form for this connection anywhere on the page --
        # its one home in "Registered connections" -- not on the role row.
        # (The remove form carries the same `connection_id` field
        # too, so this is scoped to the edit form's own class rather than
        # a page-wide count of that hidden field.)
        assert body.count('class="inline-form conn-edit"') == 1

    def test_role_row_source_label_reflects_engine_detection_not_hardcoded_manual(
        self, client
    ):
        """Deferred fix (same pass, per the brief): the role-row reflection
        used to hardcode "manual (stored on this connection)" for
        capability/dim even when the value was actually engine-detected.
        It must now derive the source through the same logic the installed
        row uses."""
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._get(
            client,
            [
                _installed_row(
                    "nomic-embed-text:latest", capability="embeddings", embed_dim=768,
                    capability_source="engine",
                )
            ],
        )

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["connection_source_label"] == "detected from the model server"
        body = response.content.decode()
        assert "detected from the model server" in body
        assert "manual (stored on this connection)" not in body

    def test_registered_connection_role_row_falls_back_to_manual_when_not_installed(
        self, client
    ):
        """The other half of the same fix: when the connection's model
        ISN'T actually found on this machine, there is nothing to detect
        against -- the label stays "manual", not a false "detected from
        engine"."""
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["connection_source_label"] == "manual"
        assert "manual" in response.content.decode()

    def test_installed_only_row_shows_engine_detected_source(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(
            client,
            [_installed_row("mxbai-embed-large:latest", capability="embeddings", embed_dim=1024)],
        )

        body = response.content.decode()
        assert "detected from the model server" in body
        assert "1024" in body
        assert "<code>mxbai-embed-large:latest</code>" in body
        # Not registered: no connection line, no anchor link, no edit form
        # on this row.
        item = response.context["installed_rows"][0]
        assert item["connection"] is None
        assert item["source_label"] == "detected from the model server"

    def test_installed_row_with_unreported_capability_says_so(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(
            client, [_installed_row("mystery:latest", capability=None, capability_source=None)]
        )

        body = response.content.decode()
        assert "the model server didn't report one" in body

    def test_registered_and_installed_connection_has_exactly_one_edit_form_on_the_page(
        self, client
    ):
        """A connection bound to a role AND installed locally is still the
        SAME record -- there must be exactly
        one edit form, in "Registered connections"; both the role row's and
        the installed row's disclosures link to it rather than each
        rendering their own."""
        connection = ModelConnection.objects.create(
            name="llama conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._get(client, [_installed_row("llama3.1:8b", capability="chat")])

        body = response.content.decode()
        assert response.context["installed_rows"][0]["connection"] == connection
        # The remove form also carries `connection_id` (a distinct
        # action, not a second edit form), so this counts the edit form's
        # own class rather than the raw hidden field.
        assert body.count('class="inline-form conn-edit"') == 1
        assert body.count(f'id="conn-{connection.id}"') == 1
        # Both reflections (role row + installed row) point at that one form.
        assert body.count(f'href="#conn-{connection.id}"') == 2

    def test_remote_endpoint_connection_previously_homeless_gets_an_edit_form(self, client):
        """A connection registered at a DIFFERENT endpoint than the one
        being viewed, bound to no role, matches no installed row either --
        it still needs its own representation on the page (no
        role row, no installed row to carry it otherwise)."""
        remote = ModelConnection.objects.create(
            name="remote embed", engine="ollama", endpoint="http://other-host:11434",
            model_id="foo", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(client, [])

        connection_views = {item["connection"].pk: item for item in response.context["connection_views"]}
        assert remote.pk in connection_views
        body = response.content.decode()
        assert f'id="conn-{remote.pk}"' in body
        assert f'name="connection_id" value="{remote.id}"' in body
        assert "remote embed" in body

    def test_registered_connections_section_shows_bound_roles_note(self, client):
        bound = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=bound)
        unbound = ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._get(client, [])

        connection_views = {item["connection"].pk: item for item in response.context["connection_views"]}
        assert connection_views[bound.pk]["bound_role_labels"] == ["RAG answer"]
        assert connection_views[unbound.pk]["bound_role_labels"] == []
        body = response.content.decode()
        assert "used by:" in body
        assert '<span class="badge chip">RAG answer</span>' in body

    @override_settings(
        LLM_MODEL=None, EMBED_MODEL="operator-embed-choice", EMBED_DIM=1024
    )
    def test_env_override_role_names_the_variables_and_shows_resolved_values(self, client):
        """rag.embed rides an EXPLICIT environment override (no binding, the
        operator deliberately pinned `EMBED_MODEL`/`EMBED_DIM`): the
        disclosure shows the RESOLVED values and names the variables,
        labeled as the operator's override rather than as "defaults" (Task
        27 -- there are none) -- and offers no edit form and no anchor link
        (settings-file values are not editable here, on this row or
        anywhere else).

        The override is set HERE, explicitly, not inherited from the
        machine's environment: what it resolves to is part of what this
        test asserts."""
        chat = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=chat)

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["current_connection"] is None
        assert role_views["rag.embed"]["env_model_var"] == "EMBED_MODEL"
        assert role_views["rag.answer"]["env_model_var"] is None  # bound, not env

        body = response.content.decode()
        assert "explicit environment override" in body
        assert "environment defaults" not in body
        assert "EMBED_MODEL" in body
        assert "OLLAMA_BASE_URL" in body
        assert "EMBED_DIM" in body
        assert "<code>operator-embed-choice</code>" in body
        assert "1024" in body
        # Exactly ONE edit form on the page: the bound chat connection's,
        # in "Registered connections". The env-fallback row renders no
        # form and no anchor link -- nothing to edit. (The remove
        # form also carries a `connection_id` field, so this is scoped to
        # the edit form's own class.)
        assert body.count('class="inline-form conn-edit"') == 1
        # Only one reflection (the bound chat role row) links to it --
        # nothing installed, so no installed-row reflection either.
        assert body.count("Edit in Registered connections ↓") == 1

    def test_currently_line_renders_name_once_when_it_equals_the_model_id(self, client):
        """A connection whose (operator-editable) name IS the
        model id, case-insensitively, must render just once, not as
        "Currently: "llama3.1:8b" (llama3.1:8b)" duplicating the same
        string."""
        connection = ModelConnection.objects.create(
            name="Llama3.1:8B", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.answer"]["current_connection_name_matches_id"] is True
        body = response.content.decode()
        assert "“Llama3.1:8B”" in body
        assert "“Llama3.1:8B” (llama3.1:8b)" not in body

    def test_currently_line_renders_both_name_and_id_when_they_differ(self, client):
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        response = self._get(client, [])

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["current_connection_name_matches_id"] is False
        body = response.content.decode()
        assert "“embed conn” (nomic-embed-text)" in body

    @override_settings(
        LLM_MODEL=None,
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=1024,
        OLLAMA_BASE_URL=ENDPOINT,
    )
    def test_env_override_currently_line_shows_a_badge_not_the_long_sentence(self, client):
        """The embeddings env-override row's "Currently:" summary line shows
        just the model plus a short badge, matching the bound chat row's
        shape; the endpoint and the fuller sentence live in the
        Connection details disclosure instead. The badge reads as an
        operator's deliberate choice, never a "default"."""
        # cold_start only renders role rows when installed_rows is
        # non-empty, so a bound chat connection keeps the page warm
        # (cold_start False) while rag.embed rides the explicit env override
        # unbound -- the same shape the sibling override test uses.
        chat = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=chat)

        response = self._get(client, [])

        body = response.content.decode()
        assert "Currently: operator-embed-choice" in " ".join(body.split())
        assert '<span class="badge chip">explicit environment override</span>' in body
        assert "not a registered connection" not in body
        # The endpoint is gone from the summary line but still present in
        # the details disclosure, alongside the fuller explanation.
        assert f"<code>{ENDPOINT}</code>" in body
        assert "no registered connection backs this role" in body

    @override_settings(LLM_MODEL=None, EMBED_MODEL=None, EMBED_DIM=None)
    def test_unassigned_role_renders_the_warning_state_with_actionable_copy(self, client):
        """No connection AND no explicit env
        override is not "unbound" in passing -- it is UNASSIGNED, said out
        loud in the warning style, with copy that points at the actual
        pipeline (register from the machine list, then choose it here).

        Pinned to exactly the two RAG roles (`_only_rag_roles()`): the
        "exactly one unassigned role" assertion below is about this pair,
        not a claim that no other role can ever be registered -- see that
        helper's docstring.
        """
        chat = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=chat)

        with _only_rag_roles():
            response = self._get(client, [])

            role_views = {v["role"].key: v for v in response.context["role_views"]}
            assert role_views["rag.embed"]["current"] is None
            assert role_views["rag.embed"]["env_override"] is None

            body = response.content.decode()
            assert '<span class="unassigned-tag">No model assigned</span>' in body
            assert '<p class="unassigned-banner">' in body
            assert (
                "Nothing backs this role yet — anything that needs it reports unavailable."
                in body
            )
            assert '<a href="#on-this-machine">Register one from the machine list below</a>' in body
            assert "then choose it here." in body
            # The disclosure states the same fact at length, and never suggests
            # a default exists.
            assert (
                "No model assigned — no registered connection and no environment "
                "override backs this role. There is no default: the platform never "
                "presumes a model on your behalf." in body
            )
            assert "environment defaults" not in body
            # The bound chat role is unaffected -- exactly one unassigned role.
            assert body.count('class="unassigned-banner"') == 1

    def test_installed_but_unregistered_row_has_no_edit_link(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client, [_installed_row("mistral:7b", capability="chat")])

        item = response.context["installed_rows"][0]
        assert item["connection"] is None
        body = response.content.decode()
        # The only registered connection here is unbound and not installed
        # under this scan -- the sole edit link on the page is its own, in
        # "Registered connections" itself, not on the unregistered row.
        assert body.count("Edit in Registered connections ↓") == 0


# --- connection_add update mode (inline edit) ---------------------------------


@pytest.mark.django_db
class TestConnectionEdit:
    """`connection_add` with a `connection_id` field updates in place: same
    validation as create, CI-unique name excludes self, and a
    fingerprint-changing edit to an embeddings-bound connection requires
    the same confirm gate as rebinding."""

    def _post(self, client, **data):
        return client.post(reverse("inference-connection-add"), data=data)

    def _edit_data(self, connection, **overrides):
        data = {
            "connection_id": str(connection.id),
            "name": connection.name,
            "engine": connection.engine,
            "endpoint": connection.endpoint,
            "model_id": connection.model_id,
            "capability": connection.capabilities[0],
            "embed_dim": "" if connection.embed_dim is None else str(connection.embed_dim),
        }
        data.update(overrides)
        return data

    def _make_embed(self, name="embed conn", bound=True):
        connection = make_embed_connection(name=name)
        if bound:
            bind("rag.embed", connection)
        return connection

    def test_updates_in_place_no_duplicate_row(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, name="renamed conn"))

        assert response.status_code == 302
        assert ModelConnection.objects.count() == 1
        connection.refresh_from_db()
        assert connection.name == "renamed conn"
        followed = client.get(response.url)
        assert "Updated connection" in followed.content.decode()

    def test_a_valid_edit_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): the update branch is a SEPARATE code
        path from `TestConnectionAdd`'s create branch (`instance.save()`,
        not `.objects.create()`) -- pinned separately."""
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        self._post(client, **self._edit_data(connection, name="renamed conn"))

        assert AuditEvent.objects.filter(action=actions.CONNECTION_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.CONNECTION_UPDATED)
        assert event.actor_kind == "open"
        assert event.target_key == str(connection.pk)
        assert event.target_label == "renamed conn"

    # --- final-review B1: the Edit form declares family + companions ------

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_edit_form_renders_family_fields_preselected_from_config(
        self, mock_engines, mock_discover, client
    ):
        """Before this fix, only the machine-list registration path
        (Task 4) collected `family`/`text_encoder`/`vae` -- an
        already-registered multi-file connection had no route to declare
        or change them except remove-and-re-add. The Edit form now renders
        the SAME `inference/_family_fields.html` partial the Add form
        does, preselected from the connection's own stored `config`."""
        engine = MagicMock()
        engine.is_healthy.return_value = True
        engine.name = "comfyui"
        engine.api_description = "the ComfyUI HTTP API"
        engine.well_known_ports = (8188,)
        engine.library_url = "https://example.test"
        engine.install_cmd_template = "n/a"
        engine.list_families.return_value = ["flux2", "qwen_image"]

        def _list_assets(endpoint, kind):
            if kind == "text_encoder":
                return [SimpleNamespace(asset_id="enc-a.gguf")]
            return [SimpleNamespace(asset_id="vae-a.safetensors")]

        engine.list_assets.side_effect = _list_assets
        mock_engines.values.return_value = [engine]
        mock_discover.return_value = []

        connection = ModelConnection.objects.create(
            name="flux conn",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        prefix = f"edit-{connection.pk}-"
        assert f'id="{prefix}family"' in body
        assert f'id="{prefix}text_encoder"' in body
        assert f'id="{prefix}vae"' in body
        assert '<option value="flux2" selected>flux2</option>' in body
        assert '<option value="enc-a.gguf" selected>enc-a.gguf</option>' in body
        assert '<option value="vae-a.safetensors" selected>vae-a.safetensors</option>' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_edit_form_family_fields_post_back_to_connection_add(
        self, mock_engines, mock_discover, client
    ):
        """The Edit form's family fields must post to the SAME
        `connection_add` action, under the SAME field names, as the Add
        form -- proven end-to-end: render the page, then submit the
        rendered form's own `connection_id` plus a NEW family, and confirm
        the stored config changed."""
        engine = MagicMock()
        engine.is_healthy.return_value = True
        engine.name = "comfyui"
        engine.list_families.return_value = ["flux2", "qwen_image"]
        engine.list_assets.return_value = []
        mock_engines.values.return_value = [engine]
        mock_discover.return_value = []

        connection = ModelConnection.objects.create(
            name="flux conn",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf",
            capabilities=["image-generation"],
        )

        response = self._post(
            client,
            **self._edit_data(
                connection, family="qwen_image", text_encoder="", vae=""
            ),
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.config == {"family": "qwen_image"}

    # --- T13: the variant field joins the same fieldset (ADR 0012 D-EDIT-8) -

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_edit_form_offers_the_variant_field_preselected_from_config(
        self, mock_engines, mock_discover, client
    ):
        """The Variant `<select>` rides the SAME `_family_fields.html`
        partial the family/text_encoder/vae fields do -- one more field, no
        new rule."""
        engine = MagicMock()
        engine.is_healthy.return_value = True
        engine.name = "comfyui"
        engine.api_description = "the ComfyUI HTTP API"
        engine.well_known_ports = (8188,)
        engine.library_url = "https://example.test"
        engine.install_cmd_template = "n/a"
        engine.list_families.return_value = ["flux2", "qwen_image"]
        engine.list_variants.return_value = ["distilled"]
        engine.list_assets.return_value = []
        mock_engines.values.return_value = [engine]
        mock_discover.return_value = []

        connection = ModelConnection.objects.create(
            name="distilled conn",
            engine="comfyui",
            endpoint="http://comfy.test:8188",
            model_id="weights-Q8_0.gguf",
            capabilities=["image-generation"],
            config={"family": "flux2", "variant": "distilled",
                    "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        prefix = f"edit-{connection.pk}-"
        assert f'id="{prefix}variant"' in body
        assert 'name="variant"' in body
        assert '<option value="distilled" selected>distilled</option>' in body

    # --- T9: multi-capability registration (THE silent-loss bug) ----------

    def test_two_capabilities_survive_a_descriptor_only_edit(self, client):
        """THE bug this task fixes: the edit form's Capability field used
        to be a single `<select>`, which can only ever submit ONE value --
        so saving a two-capability connection through it, even to change an
        unrelated field like the descriptor, silently dropped the second
        capability on every save. The checkbox group posts every checked
        box; this proves a descriptor-only edit (posting BOTH capabilities
        checked, exactly as the fixed template renders them) keeps both."""
        connection = ModelConnection.objects.create(
            name="vlm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llava:13b", capabilities=["chat", "vision"],
        )

        response = self._post(
            client,
            **self._edit_data(
                connection, capability=["chat", "vision"], descriptor="handles both",
            ),
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.capabilities == ["chat", "vision"]
        assert connection.descriptor == "handles both"

    def test_edit_can_drop_down_to_a_single_capability_deliberately(self, client):
        """The reverse must also work -- an operator UNCHECKING a box is a
        deliberate choice, not the silent bug: posting only "chat" for a
        connection that used to be chat+vision narrows it on purpose."""
        connection = ModelConnection.objects.create(
            name="vlm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llava:13b", capabilities=["chat", "vision"],
        )

        response = self._post(
            client, **self._edit_data(connection, capability=["chat"]),
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.capabilities == ["chat"]

    def test_edit_with_every_box_unchecked_is_refused(self, client):
        connection = ModelConnection.objects.create(
            name="vlm conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llava:13b", capabilities=["chat", "vision"],
        )
        data = self._edit_data(connection)
        del data["capability"]

        response = self._post(client, **data)

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.capabilities == ["chat", "vision"]  # unchanged
        followed = client.get(response.url)
        assert "Capability must be one of" in followed.content.decode()

    def test_recasing_own_name_is_not_a_collision(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, name="Chat Conn"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "Chat Conn"

    def test_name_collision_with_another_connection_is_rejected(self, client):
        ModelConnection.objects.create(
            name="taken", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, name="Taken"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "chat conn"  # unchanged
        followed = client.get(response.url)
        assert "already exists" in followed.content.decode()

    def test_embeddings_bound_fingerprint_edit_without_confirm_refuses(self, client):
        connection = self._make_embed()

        response = self._post(client, **self._edit_data(connection, embed_dim="1024"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.embed_dim == 768  # unchanged
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "full document store rebuild" in body  # dim change -> severe copy
        assert "confirm rebind" in body

    def test_embeddings_bound_fingerprint_edit_with_confirm_applies(self, client):
        connection = self._make_embed()

        response = self._post(
            client, **self._edit_data(connection, embed_dim="1024", confirm="yes")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.embed_dim == 1024

    def test_embeddings_bound_name_only_edit_needs_no_confirm(self, client):
        connection = self._make_embed()

        response = self._post(client, **self._edit_data(connection, name="renamed embed"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "renamed embed"
        assert connection.embed_dim == 768

    def test_unbound_embeddings_fingerprint_edit_needs_no_confirm(self, client):
        connection = self._make_embed(bound=False)

        response = self._post(client, **self._edit_data(connection, embed_dim="1024"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.embed_dim == 1024
        # Nothing bound -> no role's data is invalidated -> no stamp (C1).
        assert not Materialization.objects.exists()

    def test_tagging_the_model_id_is_not_a_fingerprint_change(self, client):
        """"nomic-embed-text" -> "nomic-embed-text:latest" is the same
        model (norm_tag) -- no confirm gate for it."""
        connection = self._make_embed()

        response = self._post(
            client, **self._edit_data(connection, model_id="nomic-embed-text:latest")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.model_id == "nomic-embed-text:latest"

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_confirmed_edit_surfaces_drift_via_the_existing_machinery(
        self, mock_engines, mock_discover, client
    ):
        """No new drift logic: after a confirmed dim-changing edit, the
        existing fingerprint comparison (resolve() vs the Materialization
        stamp) raises the banner on the next page load by itself."""
        connection = self._make_embed()
        Materialization.objects.create(
            role_key="rag.embed", fingerprint="ollama:nomic-embed-text:768"
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        self._post(client, **self._edit_data(connection, embed_dim="1024", confirm="yes"))
        response = client.get(reverse("inference-console"))

        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["drifted"] is True
        assert "full document store rebuild" in response.content.decode()
        # C1 fix must never overwrite an existing stamp -- it records what
        # the data was actually last materialized against.
        assert Materialization.objects.filter(role_key="rag.embed").count() == 1
        assert (
            Materialization.objects.get(role_key="rag.embed").fingerprint
            == "ollama:nomic-embed-text:768"
        )

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_confirmed_fingerprint_edit_with_no_prior_stamp_stamps_pre_edit_fingerprint(
        self, mock_engines, mock_discover, client
    ):
        """Review C1: ingestion never stamps, so a fresh install has no
        Materialization row -- and without one `is_drifted()` reports
        False. A confirmed dim-changing edit of a bound embeddings
        connection must stamp the PRE-edit fingerprint (exactly like the
        dropdown's first-rebind stamp) so the drift banner appears on the
        next page load instead of a silent store mismatch."""
        connection = self._make_embed()
        assert not Materialization.objects.filter(role_key="rag.embed").exists()
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        self._post(client, **self._edit_data(connection, embed_dim="1024", confirm="yes"))

        materialization = Materialization.objects.get(role_key="rag.embed")
        # The PRE-edit fingerprint -- what any existing data was encoded
        # under -- not the post-edit one.
        assert materialization.fingerprint == "ollama:nomic-embed-text:768"

        response = client.get(reverse("inference-console"))
        role_views = {v["role"].key: v for v in response.context["role_views"]}
        assert role_views["rag.embed"]["drifted"] is True
        assert "full document store rebuild" in response.content.decode()

    def test_nonexistent_connection_id_errors(self, client):
        response = self._post(
            client,
            connection_id="999999",
            name="whatever",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()  # no create fallback either
        followed = client.get(response.url)
        assert "no longer exists" in followed.content.decode()

    def test_malformed_connection_id_does_not_500(self, client):
        response = self._post(
            client,
            connection_id="not-a-number",
            name="whatever",
            endpoint=ENDPOINT,
            model_id="llama3.1:8b",
            capability="chat",
        )

        assert response.status_code == 302
        assert not ModelConnection.objects.exists()

    def test_blank_name_in_update_mode_is_rejected(self, client):
        connection = self._make_embed(bound=False)

        response = self._post(client, **self._edit_data(connection, name=""))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "embed conn"  # unchanged

    def test_context_window_can_be_set_on_update(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(
            client, **self._edit_data(connection, context_window="16384")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.context_window == 16384

    def test_context_window_can_be_cleared_back_to_blank(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], context_window=16384,
        )

        response = self._post(
            client, **self._edit_data(connection, context_window="")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.context_window is None

    def test_non_positive_context_window_is_rejected_on_update_leaving_value_unchanged(
        self, client
    ):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], context_window=16384,
        )

        response = self._post(
            client, **self._edit_data(connection, context_window="-5")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.context_window == 16384  # unchanged
        followed = client.get(response.url)
        assert "Context window must be a positive number of tokens." in followed.content.decode()

    # --- footprint_gb on update (T2b) -------------------------------------

    def test_footprint_gb_can_be_set_on_update(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, footprint_gb="8.5"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.footprint_override_bytes == round(8.5 * 1024**3)
        assert connection.footprint_override_gb == "8.5"

    def test_footprint_gb_can_be_cleared_back_to_blank(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            footprint_override_bytes=round(8.5 * 1024**3),
        )

        response = self._post(client, **self._edit_data(connection, footprint_gb=""))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.footprint_override_bytes is None

    def test_non_positive_footprint_gb_is_rejected_on_update_leaving_value_unchanged(
        self, client
    ):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            footprint_override_bytes=round(8.5 * 1024**3),
        )

        response = self._post(client, **self._edit_data(connection, footprint_gb="-1"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.footprint_override_bytes == round(8.5 * 1024**3)  # unchanged
        followed = client.get(response.url)
        assert "Memory footprint override must be greater than zero." in followed.content.decode()

    @pytest.mark.parametrize("bad_value", ["0", "-5"])
    def test_non_positive_embed_dim_is_rejected_on_update_leaving_value_unchanged(
        self, client, bad_value
    ):
        """Mirrors the context_window/
        rank checks above -- embed_dim rejects non-positive values on
        the update path too, same never-500 clean-error shape."""
        connection = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._post(client, **self._edit_data(connection, embed_dim=bad_value))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.embed_dim == 768  # unchanged
        followed = client.get(response.url)
        assert "Embedding dimension must be a positive number." in followed.content.decode()

    # --- descriptor / rank round-trip ---------------------------------------

    def test_descriptor_and_rank_can_be_set_on_update(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(
            client, **self._edit_data(connection, descriptor="deep reasoning — heavy", rank="1")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.descriptor == "deep reasoning — heavy"
        assert connection.rank == 1

    def test_descriptor_and_rank_can_be_cleared_back_to_blank(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], descriptor="light + fast", rank=3,
        )

        response = self._post(
            client, **self._edit_data(connection, descriptor="", rank="")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.descriptor == ""
        assert connection.rank is None

    def test_bad_rank_is_rejected_on_update_leaving_value_unchanged(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], rank=5,
        )

        response = self._post(client, **self._edit_data(connection, rank="0"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.rank == 5  # unchanged
        followed = client.get(response.url)
        assert "Rank must be a positive number." in followed.content.decode()

    def test_unknown_engine_is_rejected_cleanly_on_update(self, client):
        """A garbage engine string must not be silently stored, only
        to fail later, opaquely, the first time something tries to resolve
        it. Applies to update just as much as create: an edit can retype
        the engine field too."""
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, engine="not-a-real-engine"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.engine == "ollama"  # unchanged
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "Unknown model server" in body
        assert "not-a-real-engine" in body

    def test_unrelated_edit_preserves_an_unregistered_stored_engine(self, client):
        """A connection whose STORED engine names no
        registered adapter (legacy data, a renamed/deregistered engine)
        must survive an UNRELATED edit -- e.g. a rename -- with that
        engine untouched. The edit form's "(not registered)" option always
        reposts the connection's exact current value unchanged, and the
        registry check only gates a CHANGE, never this."""
        connection = ModelConnection.objects.create(
            name="legacy conn", engine="mistral-server", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(
            client, **self._edit_data(connection, name="renamed legacy conn")
        )

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.name == "renamed legacy conn"
        assert connection.engine == "mistral-server"  # unchanged, never rejected
        followed = client.get(response.url)
        assert "Unknown model server" not in followed.content.decode()

    @patch("models.registry.views.ENGINES")
    def test_normal_change_between_two_registered_engines_still_works(
        self, mock_engines, client
    ):
        """The registry check must not become so conservative that a
        genuine engine change between two REGISTERED adapters stops
        working."""
        second = MagicMock()
        second.name = "second-engine"
        mock_engines.values.return_value = [_mock_engine(healthy=True), second]
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._post(client, **self._edit_data(connection, engine="second-engine"))

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.engine == "second-engine"


# --- The manual create form does not read as a step ---------------------------


@pytest.mark.django_db
class TestManualCreateFormCollapsed:
    """A visually-open, empty create form instructed operators to fill it
    in even when every model they had was already assignable from "In use"
    -- collapsing it behind a native `<details>`, plus a sentence stating
    the negative explicitly, kills that false "available models ->
    transcribe here" inference."""

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_create_form_is_collapsed_by_default_with_the_never_need_this_sentence(
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
        assert "<details class=\"add-connection-details\">" in body
        assert "<details class=\"add-connection-details\" open" not in body
        # The negative sentence points at the Add buttons.
        assert (
            "Models found on this machine with a detected capability never "
            "need this form — add them with their “Add to registered” "
            "button above." in body
        )
        assert "Add a connection manually" in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_cold_start_create_form_is_also_collapsed_by_default(
        self, mock_engines, mock_discover, client
    ):
        """The cold-start branch's create form must get the same
        <details> wrap as the warm branch's -- otherwise, in the
        healthy+installed sub-case, a plain always-open form sits
        directly under the role-dropdown rows, inviting exactly the
        "available models -> transcribe here" false inference. Zero
        connections (cold_start=True) with a healthy, populated scan
        reproduces that sub-case."""
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = [_installed_row("llama3.1:8b", capability="chat")]

        response = client.get(reverse("inference-console"))

        assert response.context["cold_start"] is True
        assert response.context["installed_rows"]  # the risky sub-case
        body = response.content.decode()
        assert "<details class=\"add-connection-details\">" in body
        assert "<details class=\"add-connection-details\" open" not in body
        assert (
            "Models found on this machine with a detected capability never need this form"
            in body
        )


# --- the fresh-registry onboarding gap: family fields without ?endpoint= ---

# The image engine's CONFIGURED default, not a literal: `COMFYUI_BASE_URL`
# is env-overridable (`config/settings.py`), and a hardcoded address would
# fail this whole class on any box that sets it -- while also silently
# ceasing to test rank 3 of `_family_endpoint`, which is the rank the
# fresh-registry case actually exercises.
COMFY_DEFAULT = settings.INFERENCE_DEFAULT_ENDPOINTS["comfyui"]


def _family_declaring_engine(reachable_at: str = COMFY_DEFAULT) -> MagicMock:
    """A stand-in for the one registered adapter that declares a family
    vocabulary, reachable at exactly ONE address.

    `list_families`/`list_assets` raise for any other address -- which is
    what the real adapter does (`_combo_values` propagates an unreachable
    server) and the whole point of these tests: asking the right engine at
    the wrong address is indistinguishable, from the console's side, from
    an engine that is down. `list_variants` takes no endpoint and answers
    offline, exactly as the real one does.
    """
    engine = MagicMock()
    engine.is_healthy.side_effect = lambda ep: ep == reachable_at
    engine.name = "comfyui"
    engine.api_description = "the ComfyUI HTTP API"
    engine.well_known_ports = (8188,)
    engine.library_url = "https://example.test"
    engine.install_cmd_template = "n/a"

    def _families(ep):
        if ep != reachable_at:
            raise RuntimeError("unreachable")
        return ("flux2", "qwen_image")

    def _assets(ep, kind):
        if ep != reachable_at:
            raise RuntimeError("unreachable")
        return [SimpleNamespace(asset_id=f"{kind}-a.safetensors")]

    engine.list_families.side_effect = _families
    engine.list_variants.return_value = ("distilled",)
    engine.list_assets.side_effect = _assets
    return engine


class TestFamilyFieldsOnAFreshRegistry:
    """The onboarding gap a brand-new operator hits first: the manual
    add-a-connection form dropped its whole model-family fieldset unless
    the console happened to be pointed at the image engine's own address
    with `?endpoint=`.

    The cause was never the prefill. `_engine_options` asked the
    family-declaring engine at the PAGE's one endpoint, which with no
    connections registered resolves to the chat engine's default address.
    A running image engine on its own configured address was never asked,
    `family_options` came back empty, and the template gated the entire
    fieldset on that one list -- discarding even the offline variant
    vocabulary. A fresh registry is only the most visible case; any
    console render whose endpoint is not the image engine's hit it.

    `_mock_engine` stands in for the chat adapter here, and is honest
    about having no family vocabulary at all (it deletes the three
    optional members a bare `MagicMock` would auto-vivify) -- without
    that, the console would find a `Mock` on `getattr(engine,
    "list_families", None)` and treat the chat engine as the
    family-declaring one.
    """

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_fresh_registry_offers_the_family_fields_with_no_prefill(
        self, mock_engines, mock_discover, client
    ):
        """THE GAP. No connections, no `?endpoint=`: the console targets
        the chat engine's default address, and the family-declaring engine
        is nonetheless asked at its OWN configured default."""
        mock_engines.values.return_value = [
            _mock_engine(healthy=True), _family_declaring_engine()
        ]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.context["cold_start"] is True
        assert response.context["endpoint"] != COMFY_DEFAULT
        assert response.context["family_options"] == ["flux2", "qwen_image"]
        assert response.context["text_encoder_options"] == ["text_encoder-a.safetensors"]
        assert response.context["vae_options"] == ["vae-a.safetensors"]
        assert 'class="family-fields"' in body
        assert 'id="add-family"' in body
        assert '<option value="flux2">flux2</option>' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_warm_registry_with_only_chat_connections_offers_them_too(
        self, mock_engines, mock_discover, client
    ):
        """Same cause, warm page: one chat connection registered is enough
        to leave cold-start, and the add form still has to offer the
        fields."""
        make_chat_connection()
        mock_engines.values.return_value = [
            _mock_engine(healthy=True), _family_declaring_engine()
        ]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.context["cold_start"] is False
        assert response.context["family_options"] == ["flux2", "qwen_image"]
        assert 'id="add-family"' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_an_endpoint_override_still_wins_over_the_configured_default(
        self, mock_engines, mock_discover, client
    ):
        """No regression on the path that used to be the ONLY working one:
        a scan hit's "Use this endpoint" link points the console at an
        address the settings map has never heard of, and that address is
        what the family engine is asked at."""
        elsewhere = "http://127.0.0.1:8189"
        mock_engines.values.return_value = [
            _mock_engine(healthy=True), _family_declaring_engine(reachable_at=elsewhere)
        ]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"), {"endpoint": elsewhere})

        assert response.context["endpoint"] == elsewhere
        assert response.context["family_options"] == ["flux2", "qwen_image"]

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_registered_connections_address_beats_the_configured_default(
        self, mock_engines, mock_discover, client
    ):
        """An operator whose image engine does not sit on the shipped
        default port already told this box where it lives -- by registering
        a connection against it. That address is asked before the settings
        map's."""
        elsewhere = "http://127.0.0.1:9188"
        ModelConnection.objects.create(
            name="image conn", engine="comfyui", endpoint=elsewhere,
            model_id="weights.gguf", capabilities=["image-generation"],
        )
        mock_engines.values.return_value = [
            _mock_engine(healthy=True), _family_declaring_engine(reachable_at=elsewhere)
        ]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))

        assert response.context["family_options"] == ["flux2", "qwen_image"]

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_an_unreachable_family_engine_still_renders_the_fieldset_and_says_why(
        self, mock_engines, mock_discover, client
    ):
        """Honest empty state, not a vanished form: the operator whose
        image engine is simply not running sees the fields, the offline
        variant vocabulary, and a sentence naming the engine and the
        address it was asked at -- instead of a form with no sign the
        concept exists."""
        mock_engines.values.return_value = [
            _mock_engine(healthy=True),
            _family_declaring_engine(reachable_at="http://nowhere.test:1"),
        ]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.context["family_options"] == []
        assert response.context["variant_options"] == ["distilled"]
        assert 'class="family-fields"' in body
        assert 'id="add-family"' in body
        assert 'id="add-variant"' in body
        assert "comfyui" in body
        assert COMFY_DEFAULT in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_an_adapterless_build_renders_no_fieldset(
        self, mock_engines, mock_discover, client
    ):
        """The gate's OTHER branch, pinned -- and pinned for what it
        really is.

        This is NOT "a chat-only box". `models.contracts.engines` registers
        the image adapter unconditionally at import, with no feature or
        posture gate, so `family_fields_offered` is True on every shipped
        configuration and a chat-only operator DOES see this fieldset (one
        disclosure deep, inside the collapsed Add-a-connection details --
        see `test_a_chat_only_install_still_renders_the_fieldset` below,
        which pins that). The branch this test covers is a build whose
        adapter set was cut down to adapters that declare no family
        vocabulary; `ENGINES` is mocked to produce one, because nothing
        else can.
        """
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.context["family_fields_offered"] is False
        assert response.context["family_options"] == []
        assert 'class="family-fields"' not in body
        assert 'id="add-family"' not in body

    @patch("models.registry.views._engine_options", return_value=[])
    @patch("models.registry.views._check_health", return_value=False)
    @patch("models.registry.views.discover", return_value=[])
    def test_a_chat_only_install_still_renders_the_fieldset(
        self, _mock_discover, _mock_health, _mock_options, client
    ):
        """The consequence the round-1 review asked to be surfaced rather
        than asserted away: with the REAL engine registry -- `ENGINES` is
        deliberately NOT mocked here -- an operator who only wants chat
        still gets the multi-file fieldset, because the image adapter is
        always registered.

        The three patches remove only the network (health, discovery and
        the option probes), never the registry: `family_engine` is looked
        up in the real `ENGINES`, which is the whole point. Pinned so the
        claims in `views._build_context`, in the partial's own comment and
        in the README stay honest, and so that anyone who later makes
        adapter registration conditional finds this test rather than a
        stale comment. Also pins the mitigation the design leans on: the
        fieldset renders INSIDE the collapsed disclosure, not on first
        paint.
        """
        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.status_code == 200
        assert response.context["family_fields_offered"] is True
        assert response.context["family_engine_name"]  # a real adapter answered
        assert 'class="family-fields"' in body
        # ... and INSIDE the disclosure, not in the operator's face. Sliced
        # open-tag-to-close-tag (this file's idiom, e.g. the
        # add-to-registered form above) rather than compared by index: an
        # index comparison also passes for a fieldset that renders AFTER
        # `</details>`, which is the arrangement this assertion exists to
        # rule out.
        details_start = body.index('<details class="add-connection-details">')
        details_end = body.index("</details>", details_start)
        assert 'class="family-fields"' in body[details_start:details_end]

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_stored_declaration_survives_an_unreachable_engine(
        self, mock_engines, mock_discover, client
    ):
        """The data-loss edge the always-rendered fieldset opens, closed.

        While the fieldset was gated on a live option list, an Edit form
        rendered against a down engine posted no `family` at all and
        `family_declared` left the stored config alone. Now that it always
        renders, a save from that page WOULD replace the config with
        whatever the selects hold -- so each select carries the
        connection's own stored value as its selected option when the live
        list does not contain it, the same way the engine `<select>`
        carries an unregistered stored engine."""
        mock_engines.values.return_value = [
            _mock_engine(healthy=True),
            _family_declaring_engine(reachable_at="http://nowhere.test:1"),
        ]
        mock_discover.return_value = []

        ModelConnection.objects.create(
            name="image conn", engine="comfyui", endpoint=COMFY_DEFAULT,
            model_id="weights.gguf", capabilities=["image-generation"],
            config={"family": "flux2", "variant": "distilled",
                    "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )

        response = client.get(reverse("inference-console"))
        body = response.content.decode()

        assert response.context["family_options"] == []
        assert '<option value="flux2" selected>flux2</option>' in body
        assert '<option value="distilled" selected>distilled</option>' in body
        assert '<option value="enc-a.gguf" selected>enc-a.gguf</option>' in body
        assert '<option value="vae-a.safetensors" selected>vae-a.safetensors</option>' in body

    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_an_unchanged_edit_save_against_a_down_engine_keeps_the_declaration(
        self, mock_engines, mock_discover, client
    ):
        """The same guard, pinned end to end instead of in the markup.

        The HTML assertion above proves the options are OFFERED; it does
        not prove the declaration survives a save, which is the invariant
        that actually matters. So: render the Edit form against a down
        engine, harvest the four values that rendered form would submit,
        POST them alongside a genuinely changed field (the descriptor),
        and assert `config` came back identical. Before the guard, all
        four selects would have fallen back to "— none —", posted blank,
        and `_family_config` would have stored `None`.

        This is also the only assertion here that exercises the guard on
        all four fields: `variant` is read offline, so `distilled` is in
        `variant_options` and reaches the markup through the ordinary
        option loop rather than through the guard.
        """
        mock_engines.values.return_value = [
            _mock_engine(healthy=True),
            _family_declaring_engine(reachable_at="http://nowhere.test:1"),
        ]
        mock_discover.return_value = []

        declared = {"family": "flux2", "variant": "distilled",
                    "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"}
        connection = ModelConnection.objects.create(
            name="image conn", engine="comfyui", endpoint=COMFY_DEFAULT,
            model_id="weights.gguf", capabilities=["image-generation"],
            config=dict(declared),
        )

        body = client.get(reverse("inference-console")).content.decode()

        # What the rendered Edit form would actually submit, harvested from
        # the markup rather than assumed -- a guard that stopped emitting
        # `selected` is caught here instead of silently passing.
        harvested = {}
        for field in ("family", "variant", "text_encoder", "vae"):
            select = re.search(
                rf'<select id="edit-{connection.pk}-{field}" name="{field}">(.*?)</select>',
                body, re.S,
            )
            assert select, f"no {field} select rendered on the Edit form"
            chosen = re.search(r'<option value="([^"]*)" selected>', select.group(1))
            harvested[field] = chosen.group(1) if chosen else ""
        assert harvested == declared

        response = client.post(reverse("inference-connection-add"), data={
            "connection_id": str(connection.pk),
            "name": connection.name,
            "engine": connection.engine,
            "endpoint": connection.endpoint,
            "model_id": connection.model_id,
            "capability": connection.capabilities[0],
            "embed_dim": "",
            "descriptor": "edited for an unrelated reason",
            **harvested,
        })

        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.descriptor == "edited for an unrelated reason"
        assert connection.config == declared

