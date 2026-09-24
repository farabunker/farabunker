"""The registered-connections table's row layout,
`ModelConnection.objects.picker_order()`, the `_engine_endpoints` map, and
the needs-checklist phrasing for image generation and transcription.

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
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from models.registry import views
from models.registry.models import ModelConnection, RoleBinding
from models.registry.tests._helpers import (
    ENDPOINT,
    NO_ENV_OVERRIDE,
    _mock_engine,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
)
from models.contracts.roles import RoleSpec


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
class TestRegisteredTableLayout:
    """"Registered connections"' fact block is a real aligned-column grid
    too: Name | Model server | Model ID | Endpoint | Capability | Dim |
    Created | Used by -- the edit form (and the new remove form) render
    below each connection's own row."""

    def _get(self, client):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            return client.get(reverse("inference-console"))

    def test_column_headers_render(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert 'class="registered-table" role="table"' in body
        for header in [
            "Name", "Model server", "Model ID", "Endpoint", "Capability",
            "Dim", "Created", "Used by", "Actions",
        ]:
            assert f'<span class="registered-cell" role="columnheader">{header}</span>' in body

    def test_row_cells_land_in_the_grid(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index(f'id="conn-{connection.pk}"')
        row_end = body.index("</form>", row_start)  # through the edit form
        row_html = body[row_start:row_end]
        # The actions cell (Edit disclosure + Remove cluster) is
        # the 9th cell of this SAME fact row, not a separate block --
        # the endpoint cell also carries `cell-endpoint` (the overflow-wrap
        # guard) and the actions cell is a `<div>` rather than a `<span>`,
        # so this counts cells by their shared `role="cell"` marker rather
        # than the exact `class="registered-cell"` string.
        assert row_html.count('role="cell"') == 9
        assert row_html.count('class="registered-cell') == 9
        assert "chat conn" in row_html
        assert "ollama" in row_html
        assert "llama3.1:8b" in row_html
        assert ENDPOINT in row_html

    def test_action_column_has_a_remove_button_per_connection(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert 'class="inline-form conn-remove"' in body
        assert f'action="{reverse("inference-connection-remove")}"' in body
        assert ">Remove<" in body

    def test_endpoint_cell_carries_the_overflow_wrap_guard_class(self, client):
        """Live-review fix: an unwrappable monospace endpoint URL
        ("http://host.docker.internal:11434") overflowed its grid track and
        painted into Capability/Dim. The Endpoint cell (only) carries
        `cell-endpoint`, and the stylesheet gives it `overflow-wrap:
        anywhere` -- paint itself can't be unit-tested, but the guard's
        presence can."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama",
            endpoint="http://host.docker.internal:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert 'class="registered-cell cell-endpoint"' in body
        assert "http://host.docker.internal:11434" in body
        # The grid-blowout guards land in the stylesheet: every cell gets
        # `min-width: 0` (a grid item's default `min-width: auto` is what
        # let the overflowing text win over its track), and the endpoint
        # cell specifically gets the aggressive break.
        assert ".registered-cell {" in body
        assert "min-width: 0;" in body
        assert ".registered-cell.cell-endpoint {" in body
        assert "overflow-wrap: anywhere;" in body

    # --- No em-dash placeholders, mirrored onto this grid --------------------

    def test_dim_cell_has_no_em_dash_placeholder_when_unset(self, client):
        """An unset embedding dimension must not render an
        em-dash filler in the Dim cell -- it renders empty instead,
        same as the machine table's empty Status/Action cells. (Both Dim
        and Capability are bare cells, so neither
        carries an inline em dash.)"""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index('id="conn-')
        # The actions cell is a `<div>` nested inside
        # `.registered-row` (with its own nested `</div>`s), so the first
        # bare `</div>` no longer closes `.registered-row` itself -- anchor
        # on the `.row-rule` sliver that follows the row instead.
        row_end = body.index('<span class="row-rule"', row_start)
        row_html = body[row_start:row_end]
        # Scope to the Dim cell (the 6th of the 8 `.registered-cell`
        # *spans* -- the actions cell is a `<div>` and doesn't match this
        # split delimiter, so the index is unaffected by its addition).
        cells = row_html.split('<span class="registered-cell')
        dim_cell = cells[6]
        assert "—" not in dim_cell
        # Empty: nothing renders between the cell's own opening and
        # closing tag when there's no embed_dim to show.
        assert dim_cell.split(">", 1)[1].startswith("</span>")

    def test_capability_and_dim_cells_are_bare_values(self, client):
        """A `title` attribute is
        mouse-hover-only and invisible to keyboard users. Both Capability and
        Dim render clean bare values with no inline source note and no
        `title` tooltip; the source lives in the keyboard-accessible
        disclosure below instead (see test_source_note_disclosure_... below)."""
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(client)

        body = response.content.decode()
        assert '<span class="registered-cell" role="cell">embeddings</span>' in body
        assert '<span class="registered-cell" role="cell">768</span>' in body
        assert 'title="manual"' not in body
        assert 'title="detected from the model server"' not in body

    def test_source_note_folds_into_the_one_edit_disclosure(self, client):
        """The Capability/Dim source note and the edit form share ONE
        closed-by-default disclosure ("Edit"), not two separate blocks per
        connection: opening it reveals the source note first, then the
        edit form.
        `Tab` still reaches the `<summary>`, `Enter`/`Space` still toggles
        it, no mouse required -- now for the single disclosure that also
        gates the form."""
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )

        response = self._get(client)

        body = response.content.decode()
        assert '<details class="conn-edit-details">' in body
        # Closed by default: no bare `open` attribute on the tag.
        assert '<details class="conn-edit-details" open' not in body
        assert "<summary>Edit</summary>" in body
        assert "<summary>Where Capability/Dim come from</summary>" not in body
        assert "<dt>Capability</dt><dd>manual</dd>" in body
        assert "<dt>Embedding dimension</dt><dd>manual</dd>" in body
        # The source note renders BEFORE the edit form inside the same
        # disclosure -- not a second disclosure, not after the form.
        details_start = body.index('<details class="conn-edit-details">')
        details_end = body.index("</details>", details_start)
        disclosure_html = body[details_start:details_end]
        source_idx = disclosure_html.index("<dt>Capability</dt>")
        form_idx = disclosure_html.index('class="inline-form conn-edit"')
        assert source_idx < form_idx
        assert disclosure_html.count("<details") == 1

    def test_source_note_disclosure_omits_embedding_dimension_row_when_unset(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        # Scope to THIS connection's own disclosure -- an env-fallback role
        # row elsewhere on the page legitimately has its own unrelated
        # "Embedding dimension" line.
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Capability</dt><dd>manual</dd>" in disclosure_html
        assert "<dt>Embedding dimension</dt>" not in disclosure_html

    def test_context_window_renders_in_the_edit_disclosure_when_set(self, client):
        """This task, the qwen3:30b-rag incident: a registered connection
        with an explicit context window shows it in the same facts area as
        Capability/Embedding dimension."""
        connection = ModelConnection.objects.create(
            name="capped conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], context_window=16384,
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Context window</dt><dd>16384 tokens</dd>" in disclosure_html

    def test_context_window_omitted_from_the_edit_disclosure_when_unset(self, client):
        connection = ModelConnection.objects.create(
            name="uncapped conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Context window</dt>" not in disclosure_html
        # The edit form's own field is still there, just blank.
        assert 'name="context_window"' in disclosure_html

    # --- descriptor / rank facts --------------------------------------------

    def test_descriptor_renders_in_the_row_without_opening_edit(self, client):
        """The descriptor is how the operator recognizes a model at a
        glance -- it renders in the row itself, not tucked behind the Edit
        disclosure the way Rank/Context window are."""
        connection = ModelConnection.objects.create(
            name="workstation llama", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], descriptor="light + fast",
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index(f'id="conn-{connection.pk}"')
        row_end = body.index('<div class="registered-cell registered-actions"', row_start)
        row_html = body[row_start:row_end]
        assert '<span class="descriptor-note">— light + fast</span>' in row_html

    def test_descriptor_omitted_from_the_row_when_unset(self, client):
        connection = ModelConnection.objects.create(
            name="plain conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index(f'id="conn-{connection.pk}"')
        row_end = body.index('<div class="registered-cell registered-actions"', row_start)
        row_html = body[row_start:row_end]
        assert "descriptor-note" not in row_html

    def test_rank_renders_in_the_edit_disclosure_when_set(self, client):
        connection = ModelConnection.objects.create(
            name="ranked conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"], rank=1,
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Rank</dt><dd>1</dd>" in disclosure_html

    def test_rank_omitted_from_the_edit_disclosure_when_unset(self, client):
        connection = ModelConnection.objects.create(
            name="unranked conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Rank</dt>" not in disclosure_html
        # The edit form's own fields are still there, just blank.
        assert 'name="descriptor"' in disclosure_html
        assert 'name="rank"' in disclosure_html

    # --- memory footprint facts (T2b) --------------------------------------

    def test_footprint_neither_fact_set_renders_no_rows(self, client):
        connection = ModelConnection.objects.create(
            name="plain conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Memory footprint</dt>" not in disclosure_html
        assert "<dt>Last measured</dt>" not in disclosure_html

    def test_footprint_override_only_renders_one_manual_row(self, client):
        connection = ModelConnection.objects.create(
            name="overridden conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            footprint_override_bytes=round(8.5 * 1024**3),
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Memory footprint</dt><dd>8.5 GB — set by the operator</dd>" in disclosure_html
        assert "<dt>Last measured</dt>" not in disclosure_html

    def test_footprint_measured_only_renders_one_detected_row(self, client):
        measured_at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = ModelConnection.objects.create(
            name="measured conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            measured_footprint_bytes=round(4.7 * 1024**3),
            measured_footprint_at=measured_at,
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert (
            "<dt>Memory footprint</dt><dd>4.7 GB — measured after a run "
            "on March 4, 2026</dd>" in disclosure_html
        )
        assert "<dt>Last measured</dt>" not in disclosure_html

    def test_footprint_override_and_measured_renders_both_rows(self, client):
        """Override wins as the "Memory footprint" fact, but the last real
        measurement stays visible in its own "Last measured" row rather
        than disappearing the moment an operator overrides it."""
        measured_at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = ModelConnection.objects.create(
            name="both conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            footprint_override_bytes=round(8.5 * 1024**3),
            measured_footprint_bytes=round(4.7 * 1024**3),
            measured_footprint_at=measured_at,
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert "<dt>Memory footprint</dt><dd>8.5 GB — set by the operator</dd>" in disclosure_html
        assert (
            "<dt>Last measured</dt><dd>4.7 GB — measured after a run "
            "on March 4, 2026</dd>" in disclosure_html
        )

    def test_footprint_override_gb_prefills_the_edit_form(self, client):
        connection = ModelConnection.objects.create(
            name="overridden conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
            footprint_override_bytes=round(8.5 * 1024**3),
        )

        response = self._get(client)

        body = response.content.decode()
        disclosure_start = body.index(f'id="conn-{connection.pk}"')
        disclosure_end = body.index("</details>", disclosure_start)
        disclosure_html = body[disclosure_start:disclosure_end]
        assert 'name="footprint_gb"' in disclosure_html
        assert 'value="8.5"' in disclosure_html

    def test_used_by_cell_renders_role_labels_as_nowrap_chips(self, client):
        """Fix 2, mirrored: a multi-role "used by" list used to be a
        plain comma-joined string that could wrap mid-name -- each bound
        role label is now its own non-wrapping `.badge` chip."""
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        response = self._get(client)

        body = response.content.decode()
        assert '<span class="badge chip">RAG answer</span>' in body

    def test_row_rule_replaces_fragmented_per_cell_borders(self, client):
        """Fix 5, mirrored: the facts row's per-cell `border-bottom`s
        fragmented into a broken-looking rule across the grid's column
        gaps -- a single full-width `.row-rule` sliver now draws one
        continuous line per row instead."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert '<span class="row-rule" aria-hidden="true"></span>' in body
        assert ".row-rule {" in body
        assert "grid-column: 1 / -1;" in body

    # --- One consistent actions row per connection --------------------------
    # Every connection renders one closed-by-
    # default "Edit" disclosure (form + folded-in source note) and one
    # Remove cluster, both inside a single right-aligned actions row,
    # structurally identical across every connection regardless of state.

    def test_edit_form_lives_inside_exactly_one_closed_details_per_connection(self, client):
        connection = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index(f'id="conn-{connection.pk}"')
        details_start = body.index('<details class="conn-edit-details">', row_start)
        details_end = body.index("</details>", details_start)
        disclosure_html = body[details_start:details_end]
        # Closed by default (no JS): the tag carries no `open` attribute.
        assert body[details_start:details_start + len(
            '<details class="conn-edit-details">'
        )] == '<details class="conn-edit-details">'
        # Exactly one details element wraps the form -- not nested inside
        # (or followed by) a second one.
        assert disclosure_html.count("<details") == 1
        assert disclosure_html.count('class="inline-form conn-edit"') == 1

    def test_actions_cluster_renders_with_consistent_classes_per_connection(self, client):
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        # The actions cluster is the grid's 9th cell -- a
        # `<div class="registered-cell registered-actions" role="cell">`,
        # not a bare `<div class="registered-actions">` floating below the
        # row -- and it holds exactly the same two things.
        assert body.count(
            '<div class="registered-cell registered-actions" role="cell">'
        ) == 2
        assert body.count('<details class="conn-edit-details">') == 2
        assert body.count("<summary>Edit</summary>") == 2
        assert body.count('<div class="conn-remove-cluster">') == 2
        assert body.count('class="inline-form conn-remove"') == 2

    def test_actions_cell_nests_inside_the_fact_row_as_its_last_cell(self, client):
        """The actions cluster must not
        render as a SEPARATE full-width band below the facts -- one grid
        row of its own, mostly empty -- doubling every connection's
        rendered height. It is the 9th `role="cell"` of the SAME
        `.registered-row`, alongside Name..Used by, not a sibling of that
        row. This asserts the nesting directly: the actions cell opens
        BEFORE `.registered-row`'s own closing tag, and no bare
        `.registered-actions` div (a floating-band class, without
        `registered-cell` alongside it) remains anywhere on the page."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        row_start = body.index('<div class="registered-row" role="row">')
        actions_start = body.index(
            '<div class="registered-cell registered-actions" role="cell">',
            row_start,
        )
        # `.row-rule` is always the very next sibling AFTER `.registered-row`
        # closes (its template line immediately follows the row's, see
        # `_registered_connection.html`) -- so anything found between the
        # row's own opening tag and that sliver is necessarily nested
        # INSIDE the row, not a sibling band that follows it.
        row_rule_start = body.index('<span class="row-rule"', row_start)
        assert row_start < actions_start < row_rule_start
        # No separate full-width actions band class remains: every
        # `registered-actions` occurrence on the page is paired with
        # `registered-cell` on the same element.
        for match in re.finditer(r'<div class="([^"]*registered-actions[^"]*)"', body):
            classes = match.group(1).split()
            assert "registered-cell" in classes
            assert "registered-actions" in classes

    def test_no_has_support_fallback_keeps_actions_full_width(self, client):
        """A browser with no
        `:has()` support silently drops the `:has(.conn-edit-details[open])`
        expand-on-open rule -- not "never collapses", but "never expands
        either", so opening Edit crams the whole disclosure + form into the
        narrow last-column track. Measured on a live no-`:has()` browser:
        the actions cell ballooned to 1826px inside that track, document
        scrollWidth hit 3174px against a 2056px viewport, "Save changes"
        rendered entirely off-screen, and the squeezed grid re-wrapped
        every endpoint -- controls-unusable, unacceptable for an offline
        appliance that can't assume an evergreen browser. `@supports not
        selector(:has(a))` gives non-supporting browsers the
        known-good always-full-width band instead (paint itself can't be
        unit-tested; this is a presence check that the fallback rule
        exists and unconditionally sets the full span)."""
        ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )

        response = self._get(client)

        body = response.content.decode()
        assert (
            "@supports not selector(:has(a)) {\n"
            "    .registered-cell.registered-actions {\n"
            "      grid-column: 1 / -1;\n"
            "    }\n"
            "  }"
        ) in body

    @override_settings(**NO_ENV_OVERRIDE)
    def test_removal_warning_renders_only_when_bound(self, client):
        # `ModelConnection.Meta.ordering = ["name"]` -- names chosen so the
        # unbound connection ("aaa spare") always renders before the bound
        # one ("zzz chat"), regardless of creation order.
        unbound = ModelConnection.objects.create(
            name="aaa spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        bound = ModelConnection.objects.create(
            name="zzz chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=bound)

        response = self._get(client)

        body = response.content.decode()
        unbound_start = body.index(f'id="conn-{unbound.pk}"')
        bound_start = body.index(f'id="conn-{bound.pk}"')
        assert unbound_start < bound_start
        unbound_html = body[unbound_start:bound_start]
        bound_html = body[bound_start:]
        assert "remove-note" not in unbound_html
        assert 'class="remove-note"' in bound_html
        # The warning compresses to a short muted note in the
        # actions cell -- NOT a full-width sentence -- with the complete
        # consequence still available via `title` for anyone who
        # hovers/inspects it.
        note_start = bound_html.index('class="remove-note"')
        note_end = bound_html.index("</p>", note_start)
        note_html = bound_html[note_start:note_end]
        assert (
            'title="in use by RAG answer — removing leaves it with no model assigned"'
            in note_html
        )
        # The VISIBLE text is the compact form, not the loose sentence.
        visible_text = note_html.split(">", 1)[1]
        assert visible_text == "removing → unassigns those roles"
        assert "in use by RAG answer" not in visible_text

    def test_confirm_removal_checkbox_renders_only_for_embeddings_bound_connection(self, client):
        chat_bound = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=chat_bound)
        embed_bound = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=embed_bound)

        response = self._get(client)

        body = response.content.decode()
        chat_start = body.index(f'id="conn-{chat_bound.pk}"')
        embed_start = body.index(f'id="conn-{embed_bound.pk}"')
        assert chat_start < embed_start
        chat_html = body[chat_start:embed_start]
        embed_html = body[embed_start:]
        assert "confirm-remove" not in chat_html
        assert f'id="confirm-remove-{embed_bound.id}"' in embed_html
        assert "Confirm removal" in embed_html

    def test_three_connections_in_different_states_render_the_identical_skeleton(
        self, client
    ):
        """The audit requirement, literally: unbound / chat-bound /
        embeddings-bound connections must each produce the same actions
        markup, varying only in the optional pieces their state legitimately
        changes (the remove-note and the confirm checkbox) -- not three
        differently-shaped rows."""
        ModelConnection.objects.create(
            name="spare conn", engine="ollama", endpoint=ENDPOINT,
            model_id="mistral:7b", capabilities=["chat"],
        )
        chat_bound = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint=ENDPOINT,
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=chat_bound)
        embed_bound = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint=ENDPOINT,
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=embed_bound)

        response = self._get(client)

        body = response.content.decode()
        assert len(response.context["connection_views"]) == 3
        assert body.count('<div class="registered-connection"') == 3
        # The actions cluster is the 9th `role="cell"` of each fact row
        # (`registered-cell registered-actions`), not a bare
        # full-width `<div class="registered-actions">` below it.
        assert body.count(
            '<div class="registered-cell registered-actions" role="cell">'
        ) == 3
        assert body.count('<details class="conn-edit-details">') == 3
        assert body.count("<summary>Edit</summary>") == 3
        assert body.count('<dl class="conn-source">') == 3
        assert body.count('class="inline-form conn-edit"') == 3
        assert body.count('<div class="conn-remove-cluster">') == 3
        assert body.count('class="inline-form conn-remove"') == 3
        assert body.count(">Remove<") == 3
        # Every connection's fact row is exactly 9 cells (8 facts + the
        # actions cell) -- the identical skeleton, regardless of bound/
        # embeddings-bound state. Scoped to "Registered connections" (the
        # page's last section) since "Getting models"/"On this machine"
        # above it use `role="cell"` too.
        registered_table_start = body.index('<div class="registered-table"')
        assert body.count('role="cell"', registered_table_start) == 27


# --- ModelConnection.objects.picker_order() -----------------------------------


@pytest.mark.django_db
class TestModelConnectionPickerOrder:
    """Direct unit coverage of the ONE ordering rule every model picker
    shares (role dropdowns here; the Ask-time picker reuses this same
    method in a later task) -- independent of any view/HTTP layer."""

    def test_ranked_connections_come_before_unranked(self):
        unranked = ModelConnection.objects.create(
            name="unranked", engine="ollama", endpoint=ENDPOINT, model_id="a:1b", capabilities=["chat"],
        )
        ranked = ModelConnection.objects.create(
            name="ranked", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=5,
        )

        ordered = list(ModelConnection.objects.picker_order())

        assert ordered == [ranked, unranked]

    def test_lower_rank_sorts_first(self):
        second = ModelConnection.objects.create(
            name="second", engine="ollama", endpoint=ENDPOINT, model_id="a:1b",
            capabilities=["chat"], rank=2,
        )
        first = ModelConnection.objects.create(
            name="first", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=1,
        )

        ordered = list(ModelConnection.objects.picker_order())

        assert ordered == [first, second]

    def test_ties_break_by_name_case_insensitively(self):
        bravo = ModelConnection.objects.create(
            name="Bravo", engine="ollama", endpoint=ENDPOINT, model_id="a:1b",
            capabilities=["chat"], rank=1,
        )
        alpha = ModelConnection.objects.create(
            name="alpha", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=1,
        )

        ordered = list(ModelConnection.objects.picker_order())

        assert ordered == [alpha, bravo]

    def test_unranked_ties_break_by_name_case_insensitively_too(self):
        zulu = ModelConnection.objects.create(
            name="Zulu", engine="ollama", endpoint=ENDPOINT, model_id="a:1b", capabilities=["chat"],
        )
        alpha = ModelConnection.objects.create(
            name="alpha", engine="ollama", endpoint=ENDPOINT, model_id="b:1b", capabilities=["chat"],
        )

        ordered = list(ModelConnection.objects.picker_order())

        assert ordered == [alpha, zulu]

    def test_duplicate_ranks_are_allowed_no_uniqueness_enforced(self):
        one = ModelConnection.objects.create(
            name="one", engine="ollama", endpoint=ENDPOINT, model_id="a:1b",
            capabilities=["chat"], rank=1,
        )
        two = ModelConnection.objects.create(
            name="two", engine="ollama", endpoint=ENDPOINT, model_id="b:1b",
            capabilities=["chat"], rank=1,
        )

        ordered = list(ModelConnection.objects.picker_order())

        assert ordered == [one, two]  # stable, name-broken, no error raised


@pytest.mark.django_db
class TestEngineEndpointsMap:
    """`_engine_endpoints` builds `discover()`'s per-engine map: each
    engine's default endpoint, plus its registered connections' endpoints,
    plus the endpoint the page is currently viewing (so an operator's
    `?endpoint=` override is still scanned for every engine)."""

    def test_defaults_connections_and_the_viewed_endpoint_are_unioned(self):
        ModelConnection.objects.create(
            name="remote comfy", engine="comfyui", endpoint="http://box:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )
        engine_map = views._engine_endpoints(
            list(ModelConnection.objects.all()), "http://elsewhere:11434"
        )

        assert settings.INFERENCE_DEFAULT_ENDPOINTS["comfyui"] in engine_map["comfyui"]
        assert "http://box:8188" in engine_map["comfyui"]
        assert "http://elsewhere:11434" in engine_map["ollama"]

    def test_endpoints_are_deduplicated_by_normalized_form(self):
        engine_map = views._engine_endpoints([], settings.OLLAMA_BASE_URL + "/")
        assert len(engine_map["ollama"]) == 1


class TestImageGenerationNeedPhrase:
    def test_checklist_names_the_image_generation_need(self):
        role = RoleSpec("vision.generate", "Image generation", "image-generation")
        checklist = views._needs_checklist([role], [])
        assert checklist[0]["requirement"] == "an image-generation model"
        assert checklist[0]["used_by"] == "Image generation"
        assert checklist[0]["satisfied"] is False


class TestTranscriptionNeedPhrase:
    def test_checklist_names_the_transcription_need(self):
        role = RoleSpec("rag.transcribe", "RAG transcription", "transcription")
        checklist = views._needs_checklist([role], [])
        assert checklist[0]["requirement"] == "a speech-to-text model"
        assert checklist[0]["used_by"] == "RAG transcription"
        assert checklist[0]["satisfied"] is False


def _refusal_body(client, url, engine):
    """POST an otherwise-valid payload for either creation path with an
    unregistered `engine`, follow the redirect, and return just the flash
    text (not the whole page -- the CSRF token embedded elsewhere on the
    page is randomly re-masked per request and would never compare equal).
    `connection_add` reads name/model_id/endpoint/capability;
    `machine_model_add` reads engine/model_id/endpoint/capability and
    ignores the extra `name` -- one payload shape satisfies both."""
    response = client.post(
        url,
        data={
            "name": "candidate",
            "engine": engine,
            "model_id": "llama3.1:8b",
            "endpoint": ENDPOINT,
            "capability": "chat",
        },
    )
    followed = client.get(response.url)
    match = re.search(
        r'<ul class="messages">(.*?)</ul>', followed.content.decode(), re.DOTALL
    )
    assert match, "expected a flash message on the redirected page"
    return match.group(1)


@pytest.mark.django_db
def test_both_creation_paths_refuse_an_unregistered_engine_with_the_same_words(client):
    """C-27. `machine_model_add`'s own comment says it keeps "validation
    parity with `connection_add`" -- by copying it. Two copies of an
    operator-facing refusal are two things to keep in step by hand. This
    pin is what makes the parity a fact rather than an intention."""
    from_connection = _refusal_body(client, reverse("inference-connection-add"), engine="nope")
    from_machine = _refusal_body(client, reverse("inference-machine-add"), engine="nope")
    assert "Unknown model server" in from_connection
    assert from_connection == from_machine
