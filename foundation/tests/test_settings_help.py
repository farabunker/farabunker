"""The help-card registry, its content hash, and the first two drift
assertions (spec §10.1, §10.2).

NEXT TO `test_settings_area.py` ON PURPOSE, and following its
`TestTheTable` shape (`:42-53`): that module holds `SETTINGS_GROUPS`
honest, this one holds `CARDS` honest against it. The THIRD assertion --
every cited anchor exists in the RENDERED page -- needs a real client and
one real GET per card, so it lives in its own class added by Task 2.
"""
from __future__ import annotations

import dataclasses

import pytest
from django.urls import reverse

from foundation.settings_area import SETTINGS_GROUPS
from foundation.settings_help import (
    ACCOUNTS_ADMIN, ADMIN, CARDS, CONTENT_HASH, EVERYONE, HelpCard, HelpField,
    _content_hash, card_for, card_routes, page_choices,
)
from foundation.tests._helpers import (
    bind_role, clear_bindings, make_admin, posture, seed_sweep_posture, sign_in,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)
# The MODULE, not the names: the cold-start anchor sweep below
# monkeypatches `_check_health`/`discover` ON it, which only works through
# the module object (patching a name imported into this module would leave
# the view reading its own). A test file, so `foundation/ops/tests/
# test_import_law.py`'s `_is_test_file` skips it -- production
# `foundation/` may not import `models.registry.*` and does not.
from models.registry import views as registry_views

# `django_db` PER CLASS, not module-wide: `TestTheContentHash` and
# `TestPurity` touch no database at all -- one hashes tuples and the
# other reads a file -- and a module-level mark would wrap both in a
# transaction for nothing. The three classes that DO need it are the ones
# that `reverse()` a route or drive the client.


def _entries():
    return [entry for _group, entries in SETTINGS_GROUPS for entry in entries]


@pytest.mark.django_db
class TestTheDriftGuard:
    def test_every_settings_entry_has_a_card_and_every_card_has_an_entry(self):
        """ASSERTION 1 (spec §10.1), and it is SYMMETRIC on purpose: a
        page added without a card fails, and a card left behind by a page
        that was removed fails too."""
        assert {card.route_name for card in CARDS} == {e.url_name for e in _entries()}

    def test_every_card_carries_the_same_gate_its_sidebar_entry_does(self):
        """The other half of assertion 1 -- what makes the spec's
        accepted duplication safe. `route_name`, `title`-versus-`label`
        and `gate` are stated in two tables; this is the one that closes
        `gate`."""
        gates = {e.url_name: e.gate for e in _entries()}
        assert {card.route_name: card.gate for card in CARDS} == gates

    def test_every_cards_title_is_its_sidebar_entrys_label(self):
        """M5 (Wave C review): the FOURTH leg of one-name-per-page, and
        the one nothing held until now. `test_page_names.py` binds the
        `<h1>` and the `<title>` to `_NAMES`, and `test_shell.py`'s drift
        test binds the rendered sidebar link's text to `Entry.label` --
        but `HelpCard.title` is what the settings assistant SAYS the page
        is called, and what `_linkify_named_pages` matches in prose, and
        it was free to drift from the label a viewer actually clicks.
        This module's own header calls `title`-versus-`label` one of the
        three facts stated in two tables; `gate` and `route_name` were
        already closed here, and this closes the third."""
        assert {card.route_name: card.title for card in CARDS} == {
            entry.url_name: entry.label for entry in _entries()}

    def test_every_card_names_a_route_this_box_owns(self):
        """ASSERTION 2 (spec §10.1) -- `test_settings_area.py:43-48`'s
        anti-rot test, restated for the card table: a table of url names
        nothing reverses is a sidebar of 500s waiting for the first admin
        to open it."""
        for card in CARDS:
            assert reverse(card.route_name)

    def test_every_gate_is_one_the_settings_area_understands(self):
        assert {card.gate for card in CARDS} <= {EVERYONE, ADMIN, ACCOUNTS_ADMIN}

    def test_every_card_has_at_least_one_field(self):
        """A card that named no control at all would be help text with a
        hole in it, and assertion 3 would sweep nothing for that page."""
        for card in CARDS:
            assert card.fields, card.route_name

    def test_no_two_fields_on_one_card_share_an_anchor(self):
        for card in CARDS:
            anchors = [field.anchor for field in card.fields]
            assert len(anchors) == len(set(anchors)), card.route_name


@pytest.mark.django_db
class TestTheAccessors:
    def test_card_for_finds_a_card_and_answers_none_for_a_stranger(self):
        assert card_for("identity-settings").route_name == "identity-settings"
        assert card_for("not-a-route") is None

    def test_card_routes_is_a_frozenset_of_every_route_name(self):
        routes = card_routes()
        assert isinstance(routes, frozenset)
        assert routes == {card.route_name for card in CARDS}

    def test_page_choices_is_the_table_order_not_a_sorted_set(self):
        """The tool schema's `enum` is this tuple (spec §5.2), and the
        order a model reads the pages in is the order the sidebar offers
        them -- so it is the TABLE's order, never `sorted()`."""
        assert page_choices() == tuple(card.route_name for card in CARDS)


class TestTheContentHash:
    def test_the_hash_is_twelve_lowercase_hex_characters(self):
        assert len(CONTENT_HASH) == 12
        assert all(character in "0123456789abcdef" for character in CONTENT_HASH)

    def test_the_hash_changes_when_any_card_text_changes(self):
        """THE ANTI-VACUOUS PIN, without which the hash is decoration
        (spec §10.2). A COPY of the table is mutated and rehashed -- the
        module's own `CARDS` is never touched, so no other test in this
        run sees a different hash than the one at import."""
        first = list(CARDS)
        changed = (dataclasses.replace(first[0], purpose=first[0].purpose + " And one more word."),
                   *first[1:])
        assert _content_hash(changed) != CONTENT_HASH

    def test_the_hash_changes_when_a_field_changes(self):
        card = CARDS[0]
        field = card.fields[0]
        moved = dataclasses.replace(
            card, fields=(dataclasses.replace(field, effects=field.effects + " Also this."),
                          *card.fields[1:]))
        assert _content_hash((moved, *CARDS[1:])) != CONTENT_HASH

    def test_the_hash_changes_when_the_order_changes(self):
        """Order is part of the content: `page_choices()` is derived from
        it and the tool schema's `enum` carries it to the model."""
        assert _content_hash(tuple(reversed(CARDS))) != CONTENT_HASH

    def test_the_hash_is_stable_across_processes(self):
        """`hashlib`, never Python's built-in `hash()`, which is SALTED
        PER PROCESS and would answer differently on every boot (spec
        §3.3, decision 3). Asserted by recomputing from the same input
        rather than by trusting the docstring."""
        assert _content_hash(CARDS) == CONTENT_HASH
        assert _content_hash(CARDS) == _content_hash(tuple(CARDS))

    def test_a_separator_that_could_appear_in_a_route_name_is_not_used(self):
        """Two DIFFERENT tables must not serialize to the same string.
        A field boundary a route name could contain would let a rename
        cancel out an edit.

        THE PAIR HAS TO ACTUALLY COLLIDE UNDER THE BAD SEPARATOR or this
        test proves nothing: `route_name="a|b", title="T"` and
        `route_name="a", title="b|T"` both join to `"a|b|T|…"` under a
        naive `|`, which is the whole point. (A pair like `"a-b"`/`"b|T"`
        differs under `|` too, so it would pass with the very separator
        it claims to forbid.) With `_SEP="\\x1f"` they differ, and this
        stays green for the right reason."""
        one = (HelpCard(route_name="a|b", title="T", gate=ADMIN, purpose="P",
                        fields=(HelpField(name="n", anchor="x", meaning="m", effects="e"),)),)
        two = (HelpCard(route_name="a", title="b|T", gate=ADMIN, purpose="P",
                        fields=(HelpField(name="n", anchor="x", meaning="m", effects="e"),)),)
        assert _content_hash(one) != _content_hash(two)


class TestPurity:
    def test_the_module_imports_no_django_and_no_other_module_in_this_repository(self):
        """SPEC §3.1, and it is LOAD-BEARING rather than stylistic: the
        tool runners in `agents/` read this table, and a module that
        imported `django.urls` or `identity.access` -- as
        `foundation/settings_area.py:45-50` does -- would make that read a
        cross-column import of a non-pure module.

        Asserted on the module's own SOURCE, the file-text idiom
        `foundation/ops/tests/test_column_boundaries.py` uses throughout,
        because an import that has already happened cannot be un-imported
        inside a test process.

        `"foundation"` IS IN THE DENYLIST, and that is not pedantry: a
        `from foundation.settings_area import ...` here would be a real
        import CYCLE, because that module imports this one. This module
        imports NO sibling at all -- there is nothing in `foundation/`
        it needs, and any future need is a sign the thing being reached
        for belongs here instead.
        """
        import ast
        import pathlib

        import foundation.settings_help as module

        source = pathlib.Path(module.__file__).read_text()
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = [name for name in imported
                     if name.split(".")[0] in {
                         "django", "identity", "agents", "models", "tools", "config",
                         "foundation"}]
        assert not forbidden, f"foundation/settings_help.py must stay pure; found {forbidden}"


@pytest.mark.django_db
class TestTheAnchors:
    """ASSERTION 3 (spec §10.1): every anchor a card cites exists in the
    RENDERED page.

    AGAINST A RENDERED BODY, NEVER TEMPLATE SOURCE, following
    `models/registry/tests/test_views_connection_edit.py:179-181` ("The anchor's target
    exists on the page"). That is not a stylistic preference:
    `inference/console.html` has a `{% if cold_start %}`/`{% else %}`
    split in which each anchor appears twice and exactly one branch
    renders, so a source grep can pass on a page whose live HTML has no
    such id.

    ONE CONDITION SET FOR EVERY CARD: the enterprise posture, signed in
    as an administrator, every model-consuming role bound. That is
    deliberately NOT `foundation/tests/test_page_names.py::_viewing`'s
    own arrangement (`:113-126`), which splits its sweep by name and runs
    everything that is not admin-only in the OPEN posture, anonymous --
    because that module is checking a page's NAME and an open box is the
    cheapest place to read one. This test needs a rendered BODY, and
    every one of the cards' pages renders 200 for an enterprise administrator,
    so one condition set is both sufficient and the thing to build.

    THE SWEEP PINS ITS OWN FEATURE STATE rather than inheriting the
    ambient one, per this branch's rule that a flag-dependent test states
    the flag it needs. What that does NOT do is make `vision-engine-files`
    reachable: `config/urls.py:38-39` mounts `/vision/` at IMPORT time, so
    overriding the setting inside a test cannot conjure a route that was
    never mounted. Both supported suite states carry `"vision"`
    (`'vision,media'` and `'vision'`, `docs/DEV.md:251-261`), so the entry
    is always mounted in practice. The pin is here so the sweep does not
    silently depend on `"media"` being on, and so the world it renders is
    stated rather than inherited.
    """

    @pytest.fixture(autouse=True)
    def _a_box_with_every_surface(self, db, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        seed_sweep_posture()
        clear_bindings()
        for role in (CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE):
            bind_role(role)

    @pytest.mark.parametrize("card", CARDS, ids=lambda card: card.route_name)
    def test_every_anchor_a_card_cites_exists_on_the_rendered_page(self, client, card):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse(card.route_name))
            assert response.status_code == 200, (card.route_name, response.status_code)
            body = response.content.decode()

        for field in card.fields:
            assert f'id="{field.anchor}"' in body, (
                f'{card.route_name}: the card cites anchor "{field.anchor}" for the control '
                f'"{field.name}", and the rendered page has no such id. Either the id was '
                f'renamed or deleted in that page\'s template, or the card names a control that '
                f'lives on a different page.'
            )

    def test_the_models_cards_anchors_all_exist_on_a_cold_start_box_too(
        self, client, monkeypatch,
    ):
        """`inference-console` is the one card whose page has two mutually
        exclusive branches (`{% templatetag openblock %} if cold_start
        {% templatetag closeblock %}` / `{% templatetag openblock %} else
        {% templatetag closeblock %}`, `models/registry/views.py:1120`'s
        `cold_start = not connections or not healthy`). The class fixture
        above always binds a connection to every role, so the parametrized
        test just above only ever exercises the WARM branch -- an anchor
        that exists only there could sit uncaught until a fresh box (no
        connections at all) actually needed it (review I1). This clears
        every connection first, which forces `cold_start` regardless of
        engine health, then sweeps the SAME card the parametrized test
        already covers, against the OTHER branch.

        HERMETIC, and that is the point of the two `monkeypatch` lines
        (final whole-delta review I1, second half). `clear_bindings()`
        alone forces `cold_start`, but it does NOT decide which SHAPE the
        cold branch renders in: `installed_rows` is built from
        `models.registry.views.discover()`, a REAL network scan of the
        resolved model-server endpoint. There is no `conftest.py`
        anywhere in this repo
        stubbing it, so on a developer box with a live model server the
        scan comes back non-empty and the conditional `.ready-section`
        renders -- which is exactly how the missing `#on-this-machine`
        anchor stayed green here while being absent on the fresh box this
        test claims to describe. Pinned to the genuinely empty state
        instead: `_check_health` False (no server answering) and
        `discover` empty (nothing installed), so the branch asserted
        against is the branch the docstring promises, on any box, with
        zero network calls. This is the one test in `foundation/` that
        ever reached for a live model server; it no longer does.
        """
        clear_bindings()
        monkeypatch.setattr(registry_views, "_check_health", lambda endpoint: False)
        monkeypatch.setattr(registry_views, "discover", lambda *args, **kwargs: [])
        card = card_for("inference-console")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse("inference-console"))
            assert response.status_code == 200, response.status_code
            body = response.content.decode()

        for field in card.fields:
            assert f'id="{field.anchor}"' in body, (
                f'inference-console: the card cites anchor "{field.anchor}" for the control '
                f'"{field.name}", and the COLD-START rendered page has no such id. Every '
                f'anchor this card cites must exist in BOTH of that page\'s render branches.'
            )


# --- S5 (Coherence Wave B): model field -> HelpField coverage --------------
#
# TestTheAnchors above is CARD -> PAGE only: it proves every anchor a card
# CITES exists on the rendered page, and is satisfied just as happily by
# one anchor covering three fields as by three covering one each --
# `RagSettings`' `retrieval_top_k`/`retrieval_score_floor`/`hybrid_search`
# were bundled into one "Retrieval" `HelpField` sharing one anchor until
# this same finding (S5) split them into the three `HelpField`s above.
# This is the OTHER direction: MODEL FIELD -> HelpField, so a field
# folded into a neighbour's entry (or simply forgotten) goes red here
# even though the anchor it shares still resolves on the rendered page.
#
# Field names only, no cross-column model import at module scope --
# `RagSettings`/`JobSettings` are imported LAZILY inside the one test
# that needs them, the same "test files are exempt from the import-law
# sweep, but there is no reason to make this module less pure than it
# has to be" discipline `agents/settings_tools.py`'s own `UNREPORTED_
# SETTINGS_FIELDS` follows in production code.

# Explicit field -> anchor, checked against the anchor the card ACTUALLY
# carries for that field's model -- never fuzzy name matching, since a
# `HelpField.name` is a human label ("Retention"), not the Django field
# name ("history_limit"). ALL FOUR singletons now have a card: `JobSettings`
# joined them at F1 (Coherence Wave C), when its four controls moved off
# the Queue page onto the registered "Job execution" settings page and
# the four named exclusions below were deleted rather than reworded.
_FIELD_ANCHORS: dict[str, dict[str, str]] = {
    "IdentitySettings": {
        "posture": "posture",
        "library_posture": "library-posture",
        "admin_sees_content": "admin-sees-content",
        "session_idle_minutes": "session-idle",
    },
    "ChatSettings": {
        "time_aware": "time-aware",
    },
    "RagSettings": {
        "history_limit": "retention",
        "max_upload_bytes": "upload-limit",
        "max_media_seconds": "media-duration-limit",
        "max_document_pages": "document-page-limit",
        "retrieval_top_k": "retrieval-top-k",
        "retrieval_score_floor": "retrieval-score-floor",
        "hybrid_search": "hybrid-search",
    },
    "JobSettings": {
        "memory_budget_bytes": "memory-budget",
        "max_concurrent_jobs": "max-concurrent-jobs",
        "retention_limit": "retention-limit",
        "default_priority": "default-priority",
        "max_queued_per_principal": "max-queued-per-principal",
        "response_timeout_seconds": "response-timeout-seconds",
        # Task 15 (queue memory-governance, spec §3.7): the worker-
        # measured memory prefill. NEITHER FIELD IS OPERATOR-EDITABLE --
        # there is no form control for either, only the informational
        # note `jobs/settings.html` renders beside the budget input -- so
        # they join the count below as a NEW category rather than as
        # more "operator-editable" fields; see that test's own docstring.
        "detected_memory_bytes": "detected-memory",
        "detected_memory_at": "detected-memory-at",
    },
}

_MODEL_ROUTE = {
    "IdentitySettings": "identity-settings",
    "ChatSettings": "chat-settings",
    "RagSettings": "rag-settings",
    "JobSettings": "jobs-settings",
}

_BOOKKEEPING_REASON = (
    "auto_now bookkeeping timestamp, not an operator-editable setting -- there is "
    "no control for a card to describe."
)
# F1 (Coherence Wave C) DELETED THE FOUR `JobSettings` EXCLUSIONS that
# used to sit here. Their reason was structural rather than permanent:
# `JobSettings` was edited on the Queue page, Queue was not a
# `SETTINGS_GROUPS` entry, and `CARDS` carries exactly one card per
# entry -- so no card, and therefore no `HelpField`, could exist for
# that route. F1 made the structural change the old reason said it was
# not making: the four controls now live on "Job execution"
# (`jobs-settings`), a registered entry with a card of its own, and all
# four fields are mapped in `_FIELD_ANCHORS` above like every other
# covered field. The exclusions are GONE rather than reworded, which is
# the point of a named-exclusion list: an exclusion whose reason has
# stopped being true is a deletion, not an edit.
#
# `agents/settings_tools.py::UNREPORTED_SETTINGS_FIELDS` still names all
# four, and that is NOT the same gap: its reason is import law (`agents/`
# may not read this table), which this page's existence does nothing to
# change.

_NAMED_EXCLUSIONS: dict[tuple[str, str], str] = {
    ("IdentitySettings", "updated_at"): _BOOKKEEPING_REASON,
    ("ChatSettings", "updated_at"): _BOOKKEEPING_REASON,
}


def _concrete_field_names(model) -> frozenset[str]:
    return frozenset(
        f.name for f in model._meta.get_fields()
        if getattr(f, "concrete", False) and not f.auto_created and f.name != "id"
    )


@pytest.mark.django_db
class TestTheModelFieldCoverage:
    """S5 (Coherence Wave B): every concrete field on the FOUR settings
    singletons the backend audit's Dimension 1 table names --
    `IdentitySettings`, `ChatSettings`, `RagSettings`, `JobSettings` --
    must be covered by a real `HelpField` anchor on its own page's card,
    or named in `_NAMED_EXCLUSIONS` with a reason. A field that is
    neither goes red."""

    def test_every_field_is_covered_or_named_excluded(self):
        from agents.models import ChatSettings
        from identity.models import IdentitySettings
        from models.queue.models import JobSettings
        from tools.rag.models import RagSettings

        models = {
            "IdentitySettings": IdentitySettings, "ChatSettings": ChatSettings,
            "RagSettings": RagSettings, "JobSettings": JobSettings,
        }
        for model_name, model in models.items():
            mapped = set(_FIELD_ANCHORS.get(model_name, {}))
            excluded = {f for (m, f) in _NAMED_EXCLUSIONS if m == model_name}
            for field_name in _concrete_field_names(model):
                assert field_name in mapped or field_name in excluded, (
                    model_name, field_name)

    def test_every_mapped_anchor_is_a_real_helpfield_on_that_models_own_card(self):
        """Not merely a hand-typed mapping asserted against itself -- each
        entry is checked against the CARD's own live `fields` tuple, so a
        renamed or removed anchor turns this red rather than only the
        rendered-page check above (which cannot see WHICH field an anchor
        was meant to cover, only that the string exists somewhere)."""
        for model_name, field_anchors in _FIELD_ANCHORS.items():
            card = card_for(_MODEL_ROUTE[model_name])
            card_anchors = {field.anchor for field in card.fields}
            for field_name, anchor in field_anchors.items():
                assert anchor in card_anchors, (model_name, field_name, anchor)

    def test_no_two_fields_on_one_model_share_an_anchor(self):
        """I2 (Coherence Wave B review): the actual defect S5 exists to
        catch. `test_every_mapped_anchor_is_a_real_helpfield_on_that_
        models_own_card` above checks `anchor in card_anchors` per
        field, independently -- so folding several fields back into one
        bundled `HelpField` sharing one anchor (`RagSettings`'
        `retrieval_top_k`/`retrieval_score_floor`/`hybrid_search` were
        exactly this before this same finding split them) satisfies that
        check just as happily as three distinct anchors do. Proved by
        mutation during review: re-pointing all three at one shared
        anchor left every other assertion in this class green. This is
        the one that would have caught it."""
        for model_name, field_anchors in _FIELD_ANCHORS.items():
            anchors = list(field_anchors.values())
            assert len(set(anchors)) == len(anchors), (model_name, anchors)

    def test_no_field_is_both_mapped_and_named_excluded(self):
        for model_name, field_anchors in _FIELD_ANCHORS.items():
            excluded = {f for (m, f) in _NAMED_EXCLUSIONS if m == model_name}
            assert set(field_anchors) & excluded == set(), model_name

    def test_the_eighteen_the_audit_counted_are_exactly_these_eighteen(self):
        """Pinned against the backend audit's own Dimension 1 count, plus
        round-3 hardening's one addition and the one-timeout task's own
        (7 + 4 + 6 + 1 = 18 operator-editable fields, as of 2026-09-17) --
        the two `updated_at` bookkeeping timestamps are excluded from
        THIS count on purpose, same as `agents/settings_tools.py`'s own
        sibling test.

        F1 (Coherence Wave C): all of them are MAPPED. The `JobSettings`
        fields used to make up the count as named exclusions ("no card
        slot exists for the Queue page"); they are real,
        individually-anchored `HelpField`s on `jobs-settings` now, so
        this asserts every field mapped and NO `JobSettings` exclusion
        left behind -- a re-added one would be the old gap coming back
        under a new reason.

        SIXTEEN BECAME SEVENTEEN when C-7 (round-3 hardening) added
        `JobSettings.max_queued_per_principal`, the per-principal queue
        cap. It is an operator-editable control on the same page and the
        same form as retention and default priority, so it is mapped
        like them rather than excluded -- this count moving is exactly
        the signal this assertion exists to give.

        SEVENTEEN BECAME EIGHTEEN when the one-timeout task (2026-09-17)
        added `JobSettings.response_timeout_seconds`, the turn's own
        response timeout -- mapped the identical way, on its own section
        of the same page, for the same reason.

        EIGHTEEN BECAME TWENTY when Task 15 of the queue memory-
        governance track (2026-09-21) added `JobSettings.detected_
        memory_bytes`/`detected_memory_at`, the worker-measured memory
        prefill shown beside the budget. THIS IS A DIFFERENT KIND OF
        ADDITION than the four before it: neither field is operator-
        editable (there is no form control for either, only an
        informational note), so they are not two more of "the audit's
        eighteen" -- the audit counted operator-editable fields, and this
        pair is not that. What they ARE is real, individually-anchored
        `HelpField`s: the settings page renders both (`jobs/settings.
        html`), and this assertion's own point -- that nothing this page
        renders goes uncovered -- applies to a display-only fact exactly
        as much as to a control. Mapping them keeps that point true
        rather than carving out an exception for it; `queue_excluded`
        stays `0` because neither is a `_NAMED_EXCLUSIONS` entry either."""
        mapped_count = sum(len(v) for v in _FIELD_ANCHORS.values())
        queue_excluded = sum(1 for (m, _f) in _NAMED_EXCLUSIONS if m == "JobSettings")
        # 4 IdentitySettings + 1 ChatSettings + 7 RagSettings + 6 JobSettings
        # (operator-editable) + 2 JobSettings (worker-measured, display-only)
        assert mapped_count == 20
        assert queue_excluded == 0
