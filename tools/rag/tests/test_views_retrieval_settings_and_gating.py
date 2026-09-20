"""Unit tests for the RagSettings field-update views and the
administration/gating behaviour of the library and upload views in
tools/rag/views.py.

One of four modules `tools/rag/tests/test_views.py` split into by feature
area (C-56b): this one, `test_views_ask.py`,
`test_views_documents.py`, and `test_views_upload_and_settings.py`.
Unlike the registry module's split (C-56a), this file's original had
exactly one `# ---` divider, so the cut follows class boundaries rather
than dividers.

Orchestrator ruling: `TestEverySettingsFieldSharesThreeBehaviours`
(C-57's consolidation) and all seven `*SettingsUpdate` classes stay
contiguous in this one file -- the settings file -- rather than being
split across this file and `test_views_upload_and_settings.py`, since
the consolidated class's `_SETTINGS_FIELDS` table parametrizes over
every field regardless of which page renders it. The five
`TestTheLibrary*`/`TestTheUploadDoor*` gating classes, which test who
may reach the mutation/settings/upload forms, follow in original order.
"""
import contextlib
import logging
import math
import re
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.utils import OperationalError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.models import AuditEvent
from models.contracts.bindings import ResolvedModel
# The MODULE, not the names: `TestTheSingleSettingsReadPerRequest`'s
# non-vacuity mutation monkeypatches `_top_k_context_window_fit_check` ON
# it, which only works through the module object.
from tools.rag import views as rag_views
from tools.rag.models import Category, Document, DocumentEntitlement, RagSettings
from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    client, grant, make_admin, make_document, make_entitlement, make_user, posture, sign_in,
)


# (POST field, RagSettings attribute, a valid input, what it stores, a
# bad input, the words the refusal must contain). ONE ROW PER FIELD
# (C-57): the three behaviours below are the ones all seven
# `*SettingsUpdate` classes tested identically -- a valid value updates
# and redirects, the settings row is created if absent, and a bad value
# is a clean redirect that changes nothing and says something specific.
#
# S2 (Coherence Wave C): every row posts to the SAME `rag-settings-update`
# endpoint now (the seven per-field URLs are retired) -- `field` IS the
# hidden dispatch input `library_settings_update` reads, so the column
# that used to carry a distinct url_name per row is gone; every POST
# below carries `{"field": field, field: <value>}` instead.
_SETTINGS_FIELDS = [
    ("history_limit", "history_limit", "5", 5, "abc", "whole number"),
    ("max_document_pages", "max_document_pages", "250", 250, "abc", "whole number"),
    ("max_upload_gb", "max_upload_bytes", "5", round(5 * 1024**3), "abc", "must be a number of GB"),
    ("max_media_minutes", "max_media_seconds", "90", round(90 * 60), "abc", "must be a number of minutes"),
    # `retrieval_top_k`'s own refusal copy is "must be a whole number.",
    # like `history_limit`/`max_document_pages` above -- NOT "must be a
    # number" (that's `retrieval_score_floor`'s float-field copy below).
    ("retrieval_top_k", "retrieval_top_k", "8", 8, "abc", "whole number"),
    ("retrieval_score_floor", "retrieval_score_floor", "0.6", 0.6, "abc", "must be a number"),
]
# `hybrid_search` is deliberately absent: it is a checkbox, not a bounded
# number, it does not go through `_ragsettings_field_update` at all, and
# its own class says so.


@contextlib.contextmanager
def _patched(field):
    """The `resolve`-patch context `retrieval_top_k` needs (its `extra_
    check` is the context-window fit check, which calls `resolve()`) so a
    valid POST never tries to resolve a real model binding -- a no-op for
    every other table row above, none of which has a fit check at all."""
    if field == "retrieval_top_k":
        with patch("tools.rag.views.resolve", side_effect=ValueError("unbound")):
            yield
    else:
        yield


@pytest.mark.django_db
class TestEverySettingsFieldSharesThreeBehaviours:
    """C-57. Seven `*SettingsUpdate` classes, ~1,050 lines, each opening
    with the same three tests. Those three are here, once, over a table;
    everything each field does that the others do not stays in that
    field's own class below -- including every regression pin that names
    a numbered review item, because a de-duplication that loses one of
    those has removed a guard against a 500 somebody actually hit."""

    @pytest.mark.parametrize(
        ("field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_a_valid_value_updates_the_setting_and_redirects(
            self, client, field, attr, valid, stored, bad, words):
        with _patched(field):
            response = client.post(reverse("rag-settings-update"), {"field": field, field: valid})
        assert response.status_code == 302
        assert response.url == reverse("rag-settings")
        assert getattr(RagSettings.get_solo(), attr) == stored

    @pytest.mark.parametrize(
        ("field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_the_settings_row_is_created_if_it_does_not_exist_yet(
            self, client, field, attr, valid, stored, bad, words):
        assert RagSettings.objects.count() == 0
        with _patched(field):
            client.post(reverse("rag-settings-update"), {"field": field, field: valid})
        assert RagSettings.objects.count() == 1

    @pytest.mark.parametrize(
        ("field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_a_bad_value_changes_nothing_and_says_what_is_wrong(
            self, client, field, attr, valid, stored, bad, words):
        RagSettings.objects.create(pk=1, **{attr: stored})
        with _patched(field):
            response = client.post(
                reverse("rag-settings-update"), {"field": field, field: bad}, follow=True)
        assert response.status_code == 200
        assert getattr(RagSettings.get_solo(), attr) == stored
        assert words in response.content.decode()

    @pytest.mark.parametrize(
        ("field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_a_valid_write_is_audited_exactly_once(
            self, client, field, attr, valid, stored, bad, words):
        """S3 (Coherence Wave B): every one of these six fields writes
        the SAME `LIBRARY_SETTINGS_UPDATED` action (one action per
        settings DOMAIN, `detail` carrying which field changed) --
        `_history_limit_update` and `_ragsettings_field_update`'s five
        other callers all say so in their own docstrings, which is why
        this is a table-driven pin here rather than six near-identical
        tests repeated per class."""
        with _patched(field):
            client.post(reverse("rag-settings-update"), {"field": field, field: valid})
        assert AuditEvent.objects.filter(action=actions.LIBRARY_SETTINGS_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.LIBRARY_SETTINGS_UPDATED)
        assert event.actor_kind == "open"
        # The audit `field` names the MODEL attribute written (`attr`),
        # not the POST field name that dispatched here -- they diverge
        # for `max_upload_gb` -> `max_upload_bytes` and
        # `max_media_minutes` -> `max_media_seconds`, exactly the two
        # `_ragsettings_field_update` callers whose `raw_key` and `field`
        # kwargs differ (see that function's own callers).
        assert event.detail == {"field": attr, "to": stored}

    @pytest.mark.parametrize(
        ("field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_an_invalid_write_is_not_audited(
            self, client, field, attr, valid, stored, bad, words):
        with _patched(field):
            client.post(reverse("rag-settings-update"), {"field": field, field: bad})
        assert AuditEvent.objects.count() == 0


@pytest.mark.django_db
class TestHistorySettingsUpdate:
    """`field=history_limit` on POST /rag/settings/update/. The
    valid-update/row-created/generic-bad-value/audit behaviours this
    field shares with every other `*SettingsUpdate` class are covered
    once, table-driven, by `TestEverySettingsFieldSharesThreeBehaviours`
    above."""

    @pytest.mark.parametrize("bad_value", ["", "0", "-5", "abc", "1.5"])
    def test_invalid_limit_is_a_clean_error_and_does_not_change_the_setting(
        self, client, bad_value
    ):
        RagSettings.objects.create(pk=1, history_limit=100)

        response = client.post(reverse("rag-settings-update"), {"field": "history_limit", "history_limit": bad_value})

        assert response.status_code == 302
        assert RagSettings.get_solo().history_limit == 100

    def test_invalid_limit_shows_error_message_on_redirect(self, client):
        response = client.post(
            reverse("rag-settings-update"), {"field": "history_limit", "history_limit": "-1"}, follow=True
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "positive" in body.lower()

    def test_blank_limit_shows_whole_number_error(self, client):
        response = client.post(reverse("rag-settings-update"), {"field": "history_limit", "history_limit": ""}, follow=True)

        assert response.status_code == 200
        assert "whole number" in response.content.decode().lower()

    def test_astronomically_large_limit_is_rejected_cleanly_never_500s(self, client):
        """S1 (Coherence Wave B): `history_limit` is a `PositiveIntegerField`
        (Postgres `integer`, max 2**31-1) -- the same column type as
        `max_document_pages` (`TestEverySettingsFieldSharesThreeBehaviours`'s
        own table), but this view historically had NO ceiling check at all
        (T10 review MINOR 5 only fixed the three `_ragsettings_field_update`
        callers, never this hand-rolled one in the same module). `int()`
        parses a 40-digit whole-number string without raising, so this used
        to reach `settings_row.save()` unchecked and 500 as an uncaught
        `django.db.utils.DataError` -- now caught by `foundation.
        settings_bounds.exceeds_field_ceiling`, same as every other integer
        settings field."""
        RagSettings.objects.create(pk=1, history_limit=100)
        astronomically_large = "9" + "0" * 39  # 9e39, as a plain digit string

        response = client.post(
            reverse("rag-settings-update"),
            {"field": "history_limit", "history_limit": astronomically_large}, follow=True,
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().history_limit == 100
        assert "too large" in response.content.decode()


@pytest.mark.django_db
class TestUploadCapSettingsUpdate:
    """`field=max_upload_gb` (T4): `RagSettings.
    max_upload_bytes`, entered/rendered in GB -- the sibling form to
    `TestHistorySettingsUpdate` above, mirroring `models.registry.views.
    connection_add`'s own `footprint_gb` validation copy shape exactly.
    The valid-update/row-created/generic-bad-value behaviours this field
    shares with every other `*SettingsUpdate` class are covered once,
    table-driven, by `TestEverySettingsFieldSharesThreeBehaviours`
    above."""

    def test_fractional_gb_is_accepted(self, client):
        response = client.post(reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": "0.5"})

        assert response.status_code == 302
        assert RagSettings.get_solo().max_upload_bytes == round(0.5 * 1024**3)

    def test_gb_round_trip_is_exact_to_one_decimal(self, client):
        """The pinned round-trip (T4): an operator-typed "8.5" survives
        store-as-bytes-then-read-back-as-GB at the SAME 1024**3 base
        `_human_bytes` renders with -- not just "close", exactly "8.5"
        once formatted to one decimal, the same precision the form itself
        displays (`HistoryView`'s `max_upload_gb` context)."""
        client.post(reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": "8.5"})

        gb_back = RagSettings.get_solo().max_upload_bytes / 1024**3
        assert f"{gb_back:.1f}" == "8.5"

    @pytest.mark.parametrize("bad_value", ["0", "-5", "-0.1"])
    def test_non_positive_is_a_clean_error_and_does_not_change_the_setting(self, client, bad_value):
        RagSettings.objects.create(pk=1, max_upload_bytes=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": bad_value}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_upload_bytes == 999
        assert "greater than zero" in response.content.decode()

    @pytest.mark.parametrize("bad_value", ["inf", "-inf", "nan", "infinity"])
    def test_non_finite_gb_is_rejected_cleanly_never_500s(self, client, bad_value):
        """T4 review MAJOR, reproduced live: `float("inf")` parses without
        raising, so `round(gb * 1024**3)` was an uncaught OverflowError (a
        500), and `float("nan") <= 0` is False, so nan sailed past the
        positivity check too. Both are rejected with the SAME "not a
        number" copy an unparseable string gets -- the same fix as
        `models.registry.views.connection_add`'s identically-shaped
        `footprint_gb` field."""
        RagSettings.objects.create(pk=1, max_upload_bytes=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": bad_value}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_upload_bytes == 999
        assert "must be a number of GB" in response.content.decode()

    def test_astronomically_large_gb_is_rejected_cleanly_never_500s(self, client):
        """T10 review MINOR 5: `1e308` is itself a FINITE float (it passes
        `math.isfinite`), but `gb_to_bytes(1e308)` is `round(1e308 *
        1024**3)` -- and `1e308 * 1024**3` overflows to `inf` as a float,
        so the un-guarded `round(inf)` raised an uncaught `OverflowError`
        (a 500) despite the value looking perfectly finite going in."""
        RagSettings.objects.create(pk=1, max_upload_bytes=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": "1e308"}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_upload_bytes == 999
        assert "too large" in response.content.decode()

    def test_positive_gb_that_rounds_to_zero_bytes_is_rejected_cleanly(self, client):
        """T10 re-review MINOR 4: `1e-15` GB is itself a positive `float`
        (it passes the `value <= 0` check), but `gb_to_bytes(1e-15)` is
        `round(1e-15 * 1024**3)` == `round(1.073741824e-06)` == `0` --
        rounds all the way down to a `0`-byte cap. `0` is never TOO
        LARGE, so it used to sail past `stored > max_stored` and get
        silently accepted/saved as an effectively-zero upload cap, the
        same "must be greater than zero." case a bare `0` input already
        gets rejected for -- just discovered one rounding step later."""
        RagSettings.objects.create(pk=1, max_upload_bytes=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_upload_gb", "max_upload_gb": "1e-15"}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_upload_bytes == 999
        assert "greater than zero" in response.content.decode()


@pytest.mark.django_db
class TestMediaDurationSettingsUpdate:
    """`field=max_media_minutes` (T7): `RagSettings.
    max_media_seconds`, entered/rendered in MINUTES -- the duration sibling
    of `TestUploadCapSettingsUpdate` above, same validation copy shape
    (float grammar, `math.isfinite` guard), 60x base instead of 1024**3.
    The valid-update/row-created/generic-bad-value behaviours this field
    shares with every other `*SettingsUpdate` class are covered once,
    table-driven, by `TestEverySettingsFieldSharesThreeBehaviours`
    above."""

    def test_fractional_minutes_is_accepted(self, client):
        response = client.post(reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": "0.5"})

        assert response.status_code == 302
        assert RagSettings.get_solo().max_media_seconds == round(0.5 * 60)

    def test_minutes_round_trip_is_exact_to_one_decimal(self, client):
        """The pinned round-trip (T7): an operator-typed "90.5" survives
        store-as-seconds-then-read-back-as-minutes at the SAME 60x base
        `HistoryView`'s `max_media_minutes` context divides by."""
        client.post(reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": "90.5"})

        minutes_back = RagSettings.get_solo().max_media_seconds / 60
        assert f"{minutes_back:.1f}" == "90.5"

    @pytest.mark.parametrize("bad_value", ["0", "-5", "-0.1"])
    def test_non_positive_is_a_clean_error_and_does_not_change_the_setting(self, client, bad_value):
        RagSettings.objects.create(pk=1, max_media_seconds=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": bad_value}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_media_seconds == 999
        assert "greater than zero" in response.content.decode()

    @pytest.mark.parametrize("bad_value", ["inf", "-inf", "nan", "infinity"])
    def test_non_finite_minutes_is_rejected_cleanly_never_500s(self, client, bad_value):
        RagSettings.objects.create(pk=1, max_media_seconds=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": bad_value}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_media_seconds == 999
        assert "must be a number of minutes" in response.content.decode()

    def test_astronomically_large_minutes_is_rejected_cleanly_never_500s(self, client):
        """T10 review MINOR 5: the same `round()`-overflow shape
        `TestUploadCapSettingsUpdate.
        test_astronomically_large_gb_is_rejected_cleanly_never_500s`
        covers for the GB field -- `1e308 * 60` overflows to `inf` as a
        float, so `round(minutes * 60)` raised an uncaught
        `OverflowError`."""
        RagSettings.objects.create(pk=1, max_media_seconds=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": "1e308"}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_media_seconds == 999
        assert "too large" in response.content.decode()

    def test_positive_minutes_that_rounds_to_zero_seconds_is_rejected_cleanly(self, client):
        """T10 re-review MINOR 4: the same "positive but rounds to a
        stored 0" gap `TestUploadCapSettingsUpdate.
        test_positive_gb_that_rounds_to_zero_bytes_is_rejected_cleanly`
        covers for the GB field -- `1e-15` minutes is positive, but
        `round(1e-15 * 60)` == `round(6e-14)` == `0` seconds, which used
        to be silently accepted as a `0`-second cap."""
        RagSettings.objects.create(pk=1, max_media_seconds=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": "1e-15"}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_media_seconds == 999
        assert "greater than zero" in response.content.decode()

    def test_finite_but_column_busting_minutes_is_rejected_cleanly_never_500s(self, client):
        """T10 review MINOR 5, the OTHER failure mode: `max_media_seconds`
        is a `PositiveIntegerField` (Postgres `integer`, max 2**31-1).
        `1e10` minutes is a perfectly finite float whose `round(minutes *
        60)` is a perfectly finite Python int (`6e11`) -- no
        `OverflowError` anywhere -- but it's still far too big for the
        column, which used to reach `settings_row.save()` unchecked and
        500 as an uncaught `django.db.utils.DataError`."""
        RagSettings.objects.create(pk=1, max_media_seconds=999)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_media_minutes", "max_media_minutes": "1e10"}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_media_seconds == 999
        assert "too large" in response.content.decode()


@pytest.mark.django_db
class TestDocumentPagesSettingsUpdate:
    """`field=max_document_pages` (T8 review minor 4):
    `RagSettings.max_document_pages` -- a plain whole-number page count,
    so this gets `TestHistorySettingsUpdate`'s own int grammar, not the
    float grammar `TestMediaDurationSettingsUpdate`/
    `TestUploadCapSettingsUpdate` use for their naturally-fractional
    fields. The valid-update/row-created/generic-bad-value behaviours this
    field shares with every other `*SettingsUpdate` class are covered
    once, table-driven, by `TestEverySettingsFieldSharesThreeBehaviours`
    above."""

    @pytest.mark.parametrize("bad_value", ["0", "-5"])
    def test_non_positive_is_a_clean_error_and_does_not_change_the_setting(self, client, bad_value):
        RagSettings.objects.create(pk=1, max_document_pages=500)

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_document_pages", "max_document_pages": bad_value}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_document_pages == 500
        assert "greater than zero" in response.content.decode()

    def test_astronomically_large_pages_is_rejected_cleanly_never_500s(self, client):
        """T10 review MINOR 5's own reproduction: `max_document_pages` is
        a `PositiveIntegerField` (Postgres `integer`, max 2**31-1); `int()`
        parses a 40-digit whole-number string (arbitrary-precision Python
        ints have no trouble with it, and this field's `cast=int` never
        rejects it as "not a number") without raising, so the value used
        to reach `settings_row.save()` unchecked and 500 as an uncaught
        `django.db.utils.DataError`."""
        RagSettings.objects.create(pk=1, max_document_pages=500)
        astronomically_large = "9" + "0" * 39  # 9e39, as a plain digit string

        response = client.post(
            reverse("rag-settings-update"), {"field": "max_document_pages", "max_document_pages": astronomically_large}, follow=True
        )

        assert response.status_code == 200
        assert RagSettings.get_solo().max_document_pages == 500
        assert "too large" in response.content.decode()


@pytest.mark.django_db
class TestRetrievalTopKSettingsUpdate:
    """`field=retrieval_top_k` (W4, ADR 0014 §14):
    `RagSettings.retrieval_top_k` -- bounded 1..50, plus a context-window
    fit check the other RagSettings int fields don't have. Every test that
    doesn't care about the fit check stubs `resolve` to raise (unbound rag.answer
    -- "unknown window", accepted unconditionally) so the fit check never
    accidentally participates in a bounds-only assertion. The valid-
    update/row-created/generic-bad-value behaviours this field shares
    with every other `*SettingsUpdate` class are covered once, table-
    driven, by `TestEverySettingsFieldSharesThreeBehaviours` above."""

    def _post(self, client, value):
        with patch("tools.rag.views.resolve", side_effect=ValueError("unbound")):
            return client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": value})

    def test_below_minimum_is_rejected(self, client):
        RagSettings.objects.create(pk=1, retrieval_top_k=5)

        self._post(client, "0")
        response = client.get(reverse("rag-settings"))

        assert RagSettings.get_solo().retrieval_top_k == 5
        assert "at least 1" in response.content.decode()

    def test_above_maximum_is_rejected(self, client):
        RagSettings.objects.create(pk=1, retrieval_top_k=5)

        self._post(client, "51")
        response = client.get(reverse("rag-settings"))

        assert RagSettings.get_solo().retrieval_top_k == 5
        # Not the apostrophe-bearing "can't" substring -- Django autoescapes
        # the message into `can&#x27;t` in the rendered HTML.
        assert "more than 50" in response.content.decode()

    def test_minimum_and_maximum_bounds_themselves_are_accepted(self, client):
        response = self._post(client, "1")
        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 1

        response = self._post(client, "50")
        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 50

    @patch("tools.rag.views.resolve")
    def test_unbound_rag_answer_means_unknown_window_and_is_accepted(self, mock_resolve, client):
        mock_resolve.side_effect = ValueError("No inference binding resolved for role 'rag.answer'")

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "40"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 40

    @patch("tools.rag.views.resolve")
    def test_bound_connection_with_no_context_window_override_is_unknown_and_accepted(
        self, mock_resolve, client
    ):
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "40"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 40

    @patch("tools.rag.views.resolve")
    def test_top_k_that_fits_the_known_context_window_is_accepted(self, mock_resolve, client):
        # 4 * 1024 (CHUNK_TOKENS) + 2048 (RESPONSE_RESERVE) = 6144 <= 8192.
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "4"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 4

    @patch("tools.rag.views.resolve")
    def test_top_k_at_exactly_the_context_window_boundary_is_accepted(self, mock_resolve, client):
        # 6 * 1024 + 2048 == 8192 -- fits exactly, "exceeds" is strict.
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "6"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 6

    @patch("tools.rag.views.resolve")
    def test_top_k_that_does_not_fit_the_known_context_window_is_rejected_naming_the_numbers(
        self, mock_resolve, client
    ):
        """W5 review m3: a non-hybrid install's refusal must name the
        plain `top_k=N` the operator actually typed -- never "effective_k
        =N", a term that appears nowhere in the UI and would misleadingly
        suggest doubling happened when `effective_k == top_k` here."""
        # 10 * 1024 + 2048 = 12288 > 8192.
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        with patch("tools.rag.views.rag_index.live_store_shape", return_value=None):
            response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "10"})
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 5
        body = follow_up.content.decode()
        assert "top_k=10 would need about 12288 tokens" in body
        assert "12288" in body
        assert "8192" in body
        assert "effective_k" not in body
        assert "×2" not in body

    @patch("tools.rag.views.resolve")
    def test_unexpected_resolve_error_logs_a_warning_and_the_save_still_succeeds(
        self, mock_resolve, client, caplog
    ):
        """W4 review MINOR 6: an error resolving `rag.answer` that is NOT
        the documented "unbound" `ValueError` (e.g. an `OperationalError`
        out of a DB-backed `resolve()`) must not 500, must not block the
        save -- it degrades to "unknown window" the same as the documented
        case -- but IS logged loudly (`logger.warning(..., exc_info=True)`),
        unlike the documented case's quiet `debug` log."""
        mock_resolve.side_effect = OperationalError("could not connect to server")

        with caplog.at_level(logging.WARNING, logger="tools.rag.views"):
            response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "40"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 40
        assert any(
            record.levelno == logging.WARNING and record.exc_info is not None
            for record in caplog.records
        )

    @patch("tools.rag.views.resolve")
    def test_string_context_window_is_cast_to_int_and_the_fit_check_still_runs(
        self, mock_resolve, client
    ):
        """W4 review MINOR 6: a `context_window` override typed/stored as a
        numeric STRING (never written that way today, but nothing enforces
        it) must not `TypeError` out of the `required_tokens > context_
        window` comparison -- it's `int()`-cast first, and the fit check
        still actually runs (rejects a top_k that doesn't fit) rather than
        silently degrading to "unknown window" just because the type was
        unexpected."""
        # 10 * 1024 + 2048 = 12288 > 8192 -- same arithmetic as the sibling
        # int-context_window rejection test above.
        RagSettings.objects.create(pk=1, retrieval_top_k=5)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": "8192"}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "10"})
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 5
        body = follow_up.content.decode()
        assert "12288" in body
        assert "8192" in body

    @patch("tools.rag.views.resolve")
    def test_non_castable_context_window_logs_a_warning_not_the_unbound_debug_line(
        self, mock_resolve, client, caplog
    ):
        """W4 spot-check review item 2: a `context_window` override that
        can't `int()`-cast (e.g. `"wide"`) used to be caught by the SAME
        `except ValueError` that wraps `resolve()` itself, so it wrongly
        logged the "no binding" debug line even though `resolve()`
        succeeded and the value it returned simply isn't an integer. The
        save still succeeds (degrades to "unknown window"), but now logs
        exactly one WARNING with `exc_info=True`, and never the misleading
        unbound-debug message."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": "wide"}
        )

        with caplog.at_level(logging.DEBUG, logger="tools.rag.views"):
            response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "10"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 10

        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert "is not an integer" in warnings[0].getMessage()
        assert not any(
            "no binding, or a binding with no context_window override" in record.getMessage()
            for record in caplog.records
        )

    @patch("tools.rag.views.resolve")
    def test_top_k_validation_doubles_the_budget_while_hybrid_is_on(self, mock_resolve, client):
        """W5 review S7 (the other half of the shared `_effective_k_fit_
        check` helper `TestHybridSearchSettingsUpdate` exercises from the
        toggle's side): with `hybrid_search` already ON, saving a bare
        `top_k=10` must be checked as `effective_k=20`
        (20*1024+2048=22528 > 8192), not as `top_k=10` alone
        (10*1024+2048=12288, which would fit). W5 review m3: the refusal
        names BOTH numbers ("top_k=10 (×2 for hybrid = 20 chunks)"), not
        the bare "effective_k=20" an operator never typed."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=True)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "10"})
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 5
        body = follow_up.content.decode()
        assert "top_k=10 (×2 for hybrid = 20 chunks)" in body
        assert "22528" in body

    @patch("tools.rag.views.resolve")
    def test_top_k_validation_doubles_when_the_live_store_is_hybrid_even_with_the_toggle_off(
        self, mock_resolve, client
    ):
        """W5 review m2: `hybrid_search=False` alone must NOT be read as
        "definitely not hybrid" -- an operator who disabled the toggle but
        hasn't re-encoded yet is still querying the LIVE table, which is
        still hybrid (composition rule 2 keys off `rag_index.
        live_store_shape()`, never the toggle) until that re-encode
        actually rebuilds it. `top_k=10` must still be checked doubled
        here, the same as the toggle-on case above."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        with patch(
            "tools.rag.views.rag_index.live_store_shape",
            return_value={"hybrid": True, "jsonb": True},
        ):
            response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "10"})
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 5
        body = follow_up.content.decode()
        assert "top_k=10 (×2 for hybrid = 20 chunks)" in body
        assert "22528" in body

    @patch("tools.rag.views.resolve")
    def test_top_k_validation_not_doubled_when_toggle_off_and_no_live_table_yet(
        self, mock_resolve, client
    ):
        """The sibling of the m2 fix above: `live_store_shape() is None`
        (nothing prose has ever been ingested -- no live table to be
        hybrid) must fall back to the toggle alone, not be treated as
        "hybrid" by default. `top_k=4` singly (4*1024+2048=6144 <= 8192)
        fits when NOT doubled; pinned explicitly with a mocked `None`
        rather than relying on there being no real table in the test
        database, so this keeps testing the right thing even if a prior
        test in the same run left one behind."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        with patch("tools.rag.views.rag_index.live_store_shape", return_value=None):
            response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "4"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 4

    @patch("tools.rag.views.resolve")
    def test_live_store_shape_error_logs_a_warning_and_the_save_still_succeeds(
        self, mock_resolve, client, caplog
    ):
        """`live_store_shape()`'s single `pg_attribute` lookup must not turn
        a settings save into a 500: a `ProgrammingError`/`OperationalError`
        out of that read (e.g. the table not migrated yet in some
        deployment order) degrades to the toggle-only doubling rule, the
        same fallback the documented `None` case already uses, but logged
        loudly (`logger.warning(..., exc_info=True)`) since it is not that
        documented shape."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        with patch(
            "tools.rag.views.rag_index.live_store_shape",
            side_effect=OperationalError("could not connect to server"),
        ):
            with caplog.at_level(logging.WARNING, logger="tools.rag.views"):
                response = client.post(
                    reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "4"}
                )

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 4
        assert any(
            record.levelno == logging.WARNING and record.exc_info is not None
            for record in caplog.records
        )

    @patch("tools.rag.views.resolve")
    def test_top_k_validation_is_not_doubled_while_hybrid_is_off(self, mock_resolve, client):
        """The sibling case: the SAME `top_k=10` against the SAME 8192
        window is accepted when `hybrid_search` is off, since
        `effective_k` then equals `top_k` itself
        (10*1024+2048=12288 > 8192 would actually still reject here --
        use a value that only fits singly to prove the non-doubled path:
        4*1024+2048=6144 <= 8192)."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "retrieval_top_k", "retrieval_top_k": "4"})

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_top_k == 4


@pytest.mark.django_db
class TestRetrievalScoreFloorSettingsUpdate:
    """`field=retrieval_score_floor` (W4, ADR 0014 §14):
    `RagSettings.retrieval_score_floor` -- a float bounded [0.0, 1.0],
    `0.0` (OFF) a valid, intentional value. Routed through
    `_ragsettings_field_update` with `min_stored=0` (W4 review item 6) so
    it skips that shared helper's "must be greater than zero" grammar,
    which would otherwise wrongly reject `0.0`. The valid-update/row-
    created/generic-bad-value behaviours this field shares with every
    other `*SettingsUpdate` class are covered once, table-driven, by
    `TestEverySettingsFieldSharesThreeBehaviours` above."""

    def test_zero_is_a_valid_off_value(self, client):
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.5)

        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "0"}
        )

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_score_floor == 0.0

    def test_one_is_a_valid_maximum_value(self, client):
        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "1"}
        )

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_score_floor == 1.0

    @pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf"])
    def test_nan_and_inf_are_rejected_cleanly_never_500s(self, client, bad_value):
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.4)

        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": bad_value}
        )
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().retrieval_score_floor == 0.4
        assert "must be a number" in follow_up.content.decode().lower()

    @pytest.mark.parametrize("bad_value", ["-0.1", "1.1", "2"])
    def test_out_of_range_is_rejected(self, client, bad_value):
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.4)

        client.post(reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": bad_value})
        response = client.get(reverse("rag-settings"))

        assert RagSettings.get_solo().retrieval_score_floor == 0.4
        assert "between 0.0 and 1.0" in response.content.decode()

    @pytest.mark.parametrize("bad_value", ["-0.001", "1.004"])
    def test_out_of_range_raw_value_is_rejected_even_when_rounding_would_land_it_in_range(
        self, client, bad_value
    ):
        """W4 spot-check review item 1: the bounds check used to run on
        `stored = _rounded_score_floor(value)`, AFTER rounding -- so a raw,
        out-of-range input that happens to round back INTO [0.0, 1.0]
        (`-0.001` rounds to `-0.0`, normalized to `0.0`; `1.004` rounds to
        `1.0`) slipped through unrejected. `raw_min`/`raw_max` now validate
        the RAW value first, before `to_stored` ever runs, using the same
        "must be between 0.0 and 1.0" copy as any other out-of-range
        rejection on this field."""
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.4)

        client.post(reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": bad_value})
        response = client.get(reverse("rag-settings"))

        assert RagSettings.get_solo().retrieval_score_floor == 0.4
        assert "between 0.0 and 1.0" in response.content.decode()

    def test_raw_value_within_range_near_the_boundary_is_still_accepted(self, client):
        """0.995 is itself within [0.0, 1.0] -- the new raw-value bounds
        check must not reject it. (`round(0.995, 2)` is `0.99`, not `1.0`
        -- `0.995` isn't exactly representable as a float, so it's really
        stored as very slightly under `0.995` -- but that's `_rounded_
        score_floor`'s existing, unrelated rounding behavior; the point
        here is only that the new raw-bounds check doesn't reject an
        in-range input.)"""
        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "0.995"}, follow=True
        )

        assert RagSettings.get_solo().retrieval_score_floor == 0.99
        assert "Retrieval score floor set to 0.99." in response.content.decode()

    def test_saving_rounds_the_floor_to_two_decimal_places(self, client):
        """W4 review MINOR 4/5: stored via `round(floor, 2)`, matching the
        form's own `step="0.01"` -- an operator-typed `0.005` (one step
        finer than the UI can represent) is stored as `0.01`, not the raw,
        never-displayable `0.005`."""
        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "0.005"}, follow=True
        )

        assert RagSettings.get_solo().retrieval_score_floor == 0.01
        assert "Retrieval score floor set to 0.01." in response.content.decode()

    def test_re_saving_the_rendered_value_is_a_no_op(self, client):
        """W4 review MINOR 4/5: the exact value `HistoryView` renders back
        for an already-rounded floor, POSTed unchanged, must save to the
        SAME stored value again -- an untouched re-save never drifts."""
        client.post(reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "0.005"})
        assert RagSettings.get_solo().retrieval_score_floor == 0.01

        rendered = client.get(reverse("rag-settings")).content.decode()
        assert 'value="0.01"' in rendered

        client.post(reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "0.01"})

        assert RagSettings.get_solo().retrieval_score_floor == 0.01

    def test_negative_zero_normalizes_to_positive_zero(self, client):
        """W4 review MINOR 4/5: floating point's signed zero (`round(-0.0,
        2) == -0.0`) must not sail through to storage/display as the
        confusing "-0.00" -- it normalizes to plain `0.0`, matching every
        other "off" value on this field."""
        response = client.post(
            reverse("rag-settings-update"), {"field": "retrieval_score_floor", "retrieval_score_floor": "-0.0"}, follow=True
        )

        stored = RagSettings.get_solo().retrieval_score_floor
        assert stored == 0.0
        assert math.copysign(1.0, stored) == 1.0  # not -0.0
        assert "Retrieval score floor set to 0.00." in response.content.decode()


@pytest.mark.django_db
class TestHybridSearchSettingsUpdate:
    """`field=hybrid_search` (W5, ADR 0014 §18):
    `RagSettings.hybrid_search` -- a plain boolean checkbox, not a bounded
    number, so it does NOT go through `_ragsettings_field_update`. Enabling
    re-runs the context-window fit check against DOUBLE the stored
    `retrieval_top_k` (`_effective_k_fit_check`, shared with
    `_retrieval_top_k_update`); disabling never re-checks anything.
    Every test that doesn't care about the fit check stubs `resolve` to
    raise (unbound `rag.answer` -- "unknown window", accepted
    unconditionally), matching `TestRetrievalTopKSettingsUpdate`'s own
    convention.
    """

    def _post(self, client, *, checked, resolve_side_effect=ValueError("unbound")):
        data = {"field": "hybrid_search"}
        if checked:
            data["hybrid_search"] = "on"
        with patch("tools.rag.views.resolve", side_effect=resolve_side_effect):
            return client.post(reverse("rag-settings-update"), data)

    def test_checked_box_enables_the_setting(self, client):
        response = self._post(client, checked=True)

        assert response.status_code == 302
        assert response.url == reverse("rag-settings")
        assert RagSettings.get_solo().hybrid_search is True

    def test_unchecked_box_disables_the_setting(self, client):
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = self._post(client, checked=False)

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False

    def test_creates_the_settings_row_if_it_does_not_exist_yet(self, client):
        assert RagSettings.objects.count() == 0

        self._post(client, checked=True)

        assert RagSettings.objects.count() == 1
        assert RagSettings.get_solo().hybrid_search is True

    def test_a_valid_write_is_audited_exactly_once(self, client):
        """S3 (Coherence Wave B): this view has its own hand-rolled body
        too (its own docstring: "does NOT go through `_ragsettings_
        field_update`"), so it needs its own audit pin, distinct from
        `_history_limit_update`'s and the shared helper's."""
        self._post(client, checked=True)

        assert AuditEvent.objects.filter(action=actions.LIBRARY_SETTINGS_UPDATED).count() == 1
        event = AuditEvent.objects.get(action=actions.LIBRARY_SETTINGS_UPDATED)
        assert event.actor_kind == "open"
        assert event.detail == {"field": "hybrid_search", "to": True}

    def test_enable_flash_names_the_rebuild_and_the_suspended_floor(self, client):
        response = self._post(client, checked=True)
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        body = follow_up.content.decode()
        assert "Hybrid search enabled" in body
        assert "Re-encode the index" in body
        # W5 review R2: the floor is still applied UNTIL the rebuild
        # completes (composition rule 2 keys off the LIVE store's shape,
        # not this toggle) -- the enable copy must not claim it stops now.
        assert "After the rebuild, the score floor is no longer applied." in body

    def test_disable_flash_names_both_the_keyword_matching_and_the_floor(self, client):
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = self._post(client, checked=False)
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        body = follow_up.content.decode()
        assert "Hybrid search disabled" in body
        assert "keyword matching, and the suspended score floor, both stay as they are" in body

    @patch("tools.rag.views.resolve")
    def test_enabling_rechecks_the_context_window_against_double_top_k(self, mock_resolve, client):
        """S7: `retrieval_top_k=5` alone fits an 8192 window
        (5*1024+2048=7168), but doubled to 10 it doesn't
        (10*1024+2048=12288 > 8192) -- enabling hybrid must check the
        DOUBLED number, not the bare stored top_k."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "on"})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False

    @patch("tools.rag.views.resolve")
    def test_the_refusal_message_names_the_doubled_token_count_not_the_bare_top_k(
        self, mock_resolve, client
    ):
        """N5(a)/W5 review m3: the refusal must print the doubled product
        (12288) and BOTH `top_k` numbers ("top_k=5 (×2 for hybrid = 10
        chunks)"), never just the bare stored top_k (5) as though it were
        what was rejected, and never the undocumented "effective_k" term
        either."""
        RagSettings.objects.create(pk=1, retrieval_top_k=5, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "on"})
        follow_up = client.get(reverse("rag-settings"))

        body = follow_up.content.decode()
        assert "top_k=5 (×2 for hybrid = 10 chunks)" in body
        assert "12288" in body
        assert "8192" in body

    @patch("tools.rag.views.resolve")
    def test_enabling_is_accepted_when_the_context_window_is_unknown(self, mock_resolve, client):
        """N5(b): `rag.answer` unbound (or bound with no `context_window`
        override) degrades to "accept, no fit check" -- the toggle SAVES.
        This pins the degrade as intended behavior, not a gap to "fix"
        into a refusal later."""
        RagSettings.objects.create(pk=1, retrieval_top_k=50, hybrid_search=False)
        mock_resolve.side_effect = ValueError("No inference binding resolved for role 'rag.answer'")

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "on"})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is True

    @patch("tools.rag.views.resolve")
    def test_top_k_that_fits_doubled_is_accepted(self, mock_resolve, client):
        # 2 * 1024 + 2048 = 4096 <= 8192.
        RagSettings.objects.create(pk=1, retrieval_top_k=2, hybrid_search=False)
        mock_resolve.return_value = ResolvedModel(
            "ollama", "llama3.1:8b", "http://localhost:11434", config={"context_window": 8192}
        )

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "on"})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is True

    @patch("tools.rag.views.resolve")
    def test_disabling_never_reruns_the_fit_check(self, mock_resolve, client):
        """Dropping to dense-only only ever SHRINKS the token budget the
        already-saved top_k passed when hybrid was enabled -- disabling
        must not even resolve `rag.answer`."""
        RagSettings.objects.create(pk=1, retrieval_top_k=50, hybrid_search=True)

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", })

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False
        mock_resolve.assert_not_called()

    def test_empty_string_value_disables_the_setting(self, client):
        """W5 review m4: `""` (the field present but empty) is one of the
        two explicitly-accepted "off" spellings, alongside an absent
        field entirely."""
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": ""})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False

    def test_off_value_disables_the_setting(self, client):
        """W5 review m4: `"off"` is the other explicitly-accepted "off"
        spelling."""
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "off"})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False

    def test_unparseable_value_is_rejected_with_an_honest_error_never_defaulted_to_off(self, client):
        """W5 review m4: every OTHER value (e.g. a hand-built request's
        `"true"`) used to be silently treated as "off" by a bare
        `== "on"` comparison -- now it's a clean form error, matching the
        never-500 "reject what can't be honestly parsed" shape every
        sibling settings field on this page already uses. The setting
        must not change either way."""
        RagSettings.objects.create(pk=1, hybrid_search=False)

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "true"})
        follow_up = client.get(reverse("rag-settings"))

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is False
        assert "Invalid hybrid_search value" in follow_up.content.decode()

    def test_unparseable_value_does_not_change_an_already_enabled_setting(self, client):
        """The same rejection, from the OTHER starting state -- an
        unparseable value must not be able to accidentally disable an
        already-enabled toggle either."""
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = client.post(reverse("rag-settings-update"), {"field": "hybrid_search", "hybrid_search": "1"})

        assert response.status_code == 302
        assert RagSettings.get_solo().hybrid_search is True


@pytest.mark.django_db
class TestTheLibraryMutationsAreAdministration:
    """IA-1 Step 6b: `rag-document-delete`/`rag-document-reingest` are
    class R (`identity/routes.py`), enforced only as AUTHENTICATED by the
    middleware -- their real rule lives in the view, and until this task
    nothing wrote it. Document labels are IA-2's; the `is_admin` half is
    the half that closes the hole today."""

    @pytest.mark.parametrize("name", ["rag-document-delete", "rag-document-reingest"])
    def test_a_member_is_refused(self, client, name):
        """THE HOLE THIS CLOSES: without it, any signed-in account
        could delete every document on the box."""
        document = make_document()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse(name, args=[document.pk]))
        assert response.status_code == 403
        assert Document.objects.filter(pk=document.pk).exists()

    @pytest.mark.parametrize("name", ["rag-document-delete", "rag-document-reingest"])
    def test_an_admin_with_the_content_setting_off_is_allowed(self, client, name):
        """Administering is not reading: an administrator prunes a
        library they are not cleared to read. That is the whole reason
        this is `is_admin` and not `sees_all_content`."""
        document = make_document()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, make_admin())
            assert client.post(
                reverse(name, args=[document.pk])).status_code in (302, 200)

    def test_an_open_box_is_unchanged(self, client):
        """`is_admin(OPEN_PRINCIPAL)` is True, so the household box
        behaves exactly as it does today."""
        document = make_document()
        with posture(POSTURE_OPEN):
            assert client.post(reverse("rag-document-delete",
                                       args=[document.pk])).status_code in (302, 200)


@pytest.mark.django_db
class TestTheUploadDoorObeysItsToolLabel:
    """The library's own door, and the exact mirror of the vision one:
    same predicate shape, same 403, same first-thing-in-the-view position.

    `rag.ingest` is `mutates=True`, so no AGENT can ever be granted it --
    which is a different question from whether a PERSON reaches the upload
    form, and spec section 22.35 is where the two are told apart.
    """

    def test_a_non_holder_is_refused_403_and_nothing_reaches_the_inbox(
            self, client, tmp_path, settings):
        """ASSERTED ON THE DIRECTORY, not only on the response: the whole
        point of gating first is that no byte is staged."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": ""})
        assert response.status_code == 403
        assert b"Traceback" not in response.content
        assert list(tmp_path.rglob("*")) == []
        assert Document.objects.count() == 0

    def test_a_holder_is_not_refused_by_this_gate(self, client, tmp_path, settings):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": ""})
        assert response.status_code != 403

    def test_an_unlabelled_tool_refuses_nobody(self, client, tmp_path, settings):
        """T13 review finding 4: the OLD version of this test created a
        `ToolEntitlement` row for `rag.ingest` and posted anonymously --
        which tests the open-box short-circuit (below), never the
        unlabelled case its own name claimed, since a labelled tool with
        no signed-in caller never reaches `ToolAccess.required` at all
        under the open posture. This is the real unlabelled case: NO
        `ToolEntitlement` row, a signed-in member, enterprise posture."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        settings.INGEST_INBOX_DIR = str(tmp_path)
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": ""})
        assert response.status_code != 403

    def test_an_open_box_is_unchanged(self, client, tmp_path, settings):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        assert client.post(reverse("rag-document-upload"),
                           {"files": [upload], "category": ""}).status_code != 403


@pytest.mark.django_db
class TestTheLibraryRenderGatesTheUploadForm:
    def test_a_non_holder_sees_no_upload_form_but_still_sees_the_library(self, client):
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        make_document(title="a document")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() not in body
        assert b"a document" in body          # the listing is untouched

    def test_a_holder_sees_it(self, client):
        from agents.models import ToolEntitlement
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() in body


# --- IA-2 T17: render-vs-gate -- settings/mutation forms are gated on the page ---


@pytest.mark.django_db
class TestTheLibrarySettingsFormsAreAdminOnly:
    """IA-2 T17's render-vs-gate pin, restated for the page these seven
    forms live on since UI-1 -- all seven posting to the one endpoint
    asserted below since S2, Coherence Wave C.

    THE ROUTE ITSELF IS THE REFUSAL NOW. `rag-settings` is class S -- a
    page whose entire body is administrator-only forms has nothing to
    show anybody else -- so a member never reaches it at all, which is
    why the member case below asserts a 403 rather than an admin-free
    render. The template keeps its own `{% if identity_is_admin %}` as
    the second half of the same rule; that is what governs an OPEN box,
    where the gate returns before it ever looks at the route.
    """

    # S2 (Coherence Wave C): ONE endpoint, where seven per-field URLs
    # used to be. Still a tuple, so the sweep below reads unchanged if a
    # second settings endpoint ever joins it.
    _NAMES = ("rag-settings-update",)

    def test_a_member_never_reaches_the_page(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("rag-settings"))
        assert response.status_code == 403

    def test_an_admin_is_shown_the_forms(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-settings")).content
        for name in self._NAMES:
            assert reverse(name).encode() in body

    def test_an_open_box_shows_the_forms(self, client):
        """The open posture has one principal and it is an administrator,
        so the template gate opens and the route gate never runs."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("rag-settings")).content
        for name in self._NAMES:
            assert reverse(name).encode() in body


@pytest.mark.django_db
class TestTheOneDispatchedLibrarySettingsEndpoint:
    """S2 (Coherence Wave C): the seven per-field URLs collapsed into one
    endpoint that dispatches on a hidden `field` input. The per-field
    behaviour is pinned by the seven classes above, unchanged; this class
    is about the DISPATCH itself -- the part that did not exist before.
    """

    def test_every_form_on_the_page_posts_to_the_one_endpoint(self, client):
        body = client.get(reverse("rag-settings")).content.decode()

        assert body.count(f'action="{reverse("rag-settings-update")}"') == 7
        for field in ("history_limit", "max_upload_gb", "max_media_minutes",
                      "max_document_pages", "retrieval_top_k",
                      "retrieval_score_floor", "hybrid_search"):
            assert f'name="field" value="{field}"' in body, field

    def test_every_dispatch_value_the_page_sends_is_one_the_endpoint_knows(self, client):
        """ANTI-VACUOUS, in the direction that actually breaks: a hidden
        `field` the dispatch table has no entry for would flash "not a
        recognised settings form" at an operator who did nothing wrong,
        and every per-field test above would stay green because each of
        them posts its own hand-written value."""
        from tools.rag.views import _LIBRARY_SETTINGS_FIELDS

        body = client.get(reverse("rag-settings")).content.decode()
        sent = set(re.findall(r'name="field" value="([^"]+)"', body))

        assert sent == set(_LIBRARY_SETTINGS_FIELDS)

    @pytest.mark.parametrize("field", ["", "   ", "history-limit", "explode",
                                       "posture", "max_upload_bytes"])
    def test_an_unrecognised_field_flashes_and_saves_nothing(self, client, field):
        """Never a 500 and never a raw 400 -- and never a silent no-op
        either, which is what an unchanged page after a submit reads as.
        `max_upload_bytes` is in the list on purpose: it is the real MODEL
        field name, which is exactly the near-miss a hand-built request
        would send."""
        RagSettings.objects.create(pk=1, history_limit=20)

        response = client.post(
            reverse("rag-settings-update"),
            {"field": field, "history_limit": "99", "max_upload_gb": "99"},
            follow=True,
        )

        assert response.status_code == 200
        assert "is not a recognised settings form." in response.content.decode()
        assert RagSettings.get_solo().history_limit == 20

    def test_a_missing_field_is_the_same_refusal(self, client):
        RagSettings.objects.create(pk=1, history_limit=20)

        response = client.post(
            reverse("rag-settings-update"), {"history_limit": "99"}, follow=True)

        assert response.status_code == 200
        assert "is not a recognised settings form." in response.content.decode()
        assert RagSettings.get_solo().history_limit == 20

    def test_an_unrecognised_field_writes_no_audit_row(self, client):
        """The refusal happens before any handler runs, so there is
        nothing to audit -- asserted rather than assumed, since an audit
        row for a write that did not happen is a worse record than none."""
        client.post(reverse("rag-settings-update"), {"field": "explode"})

        assert not AuditEvent.objects.filter(
            action=actions.LIBRARY_SETTINGS_UPDATED).exists()

    def test_a_get_is_refused_before_the_dispatch_runs(self, client):
        assert client.get(reverse("rag-settings-update")).status_code == 405

    def test_a_recognised_field_still_saves_and_redirects_home(self, client):
        """The dispatch's happy path, once -- proof the refusal branch
        above is not the only branch this endpoint has."""
        response = client.post(
            reverse("rag-settings-update"),
            {"field": "history_limit", "history_limit": "42"},
        )

        assert response.status_code == 302
        assert response.url == reverse("rag-settings")
        assert RagSettings.get_solo().history_limit == 42


@pytest.mark.django_db
class TestTheLibraryHidesActionsAMemberCannotTake:
    def test_reingest_delete_and_the_category_forms_are_hidden_from_a_member(self, client):
        member = make_user()
        document = make_document(title="a document")
        category = Category.objects.create(name="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert b"a document" in body            # the ROW is still listed
        assert reverse("rag-document-reingest", args=[document.id]).encode() not in body
        assert reverse("rag-document-delete", args=[document.id]).encode() not in body
        assert reverse("rag-category-rename", args=[category.id]).encode() not in body
        assert reverse("rag-category-delete", args=[category.id]).encode() not in body

    def test_an_entitlement_owner_IS_shown_them_on_their_own_document(self, client):
        """The WIDENED predicate (`row.may_label`), not `identity_is_admin`:
        IA-2 gives an entitlement owner those two actions, and gating the
        rendering on admin-ness would hide from an owner a control they
        may actually use."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role="owner")
        document = make_document(title="a document")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-delete", args=[document.id]).encode() in body

    def test_the_upload_form_is_still_offered_to_a_member(self, client):
        """Upload is class A: every signed-in caller may upload, and the
        POST does not 403. Hiding it would REMOVE a capability rather than
        stop advertising a refused one."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() in body


# --- S6: one settings read per request ----------------------------------------


@pytest.mark.django_db
class TestTheSingleSettingsReadPerRequest:
    """S6 (settings-backend audit, closed by the final whole-delta
    review's I2 ruling): this module's views read `RagSettings` through
    ONE request-scoped helper, `tools.rag.views.settings_row_for`, not
    through an independent `get_solo()` each.

    THE PATTERN IS `identity.request.settings_row_for`'s, copied rather
    than invented -- down to the name. The one difference is where the
    row comes from: identity's gate middleware has already stashed it on
    the request by the time a view asks, so its helper reads the stash
    and falls back to a fetch; nothing stashes `RagSettings`, so this
    one fetches on first ask and stashes it itself. Same contract to a
    caller either way -- "the row for THIS request", named once instead
    of at each call site.

    COUNTED AGAINST `rag_ragsettings` SPECIFICALLY rather than the
    request's total query count, exactly as
    `identity/tests/test_middleware.py::TestTheSingleRowRead` counts
    `identity_identitysettings`: these pins must not drift every time a
    page's own unrelated queries change.

    SCOPE, stated so a later reader does not mistake these pins for more
    than they are: the audit counted fifteen `get_solo()` sites across
    `tools/rag`, and this closes the SEVEN that live in `views.py`.
    `ingest.py`, `media.py`, `index.py` and `services.py`'s prune read
    keep their own reads -- those run on the watcher and in job handlers,
    where there is no request to scope anything to, and `retrieval.py`
    already threads a row through its own call chain.

    ONE UNTOUCHED SITE IS ON A REQUEST PATH, and is named here rather
    than swept under that sentence (closing-wave re-verify, C1):
    `services.stage_turn_attachments` (`services.py:639`) is reached by
    three chat POST handlers via `agents.chat.service.start_turn`. It is
    left alone because collapsing it means threading a row through
    `start_turn`'s cross-column signature -- a design change, not a
    query-count one -- so it is a bounded exception with its own reason,
    not part of the watcher/job-handler class above.

    That scope is also why the pins below use the settings page rather
    than the upload path: an upload runs the staging pipeline, whose
    reads are not this helper's to collapse.
    """

    @staticmethod
    def _settings_reads(context) -> int:
        """READS of the row, matched on `FROM "rag_ragsettings"` rather
        than on the table name anywhere in the statement. The narrower
        match is load-bearing HERE in a way it is not for
        `models/queue/tests/test_worker.py`'s sibling pin: every POST
        below SAVES the row, and `get_solo()` on a box that has never
        stored one INSERTs it -- both statements name the table, and
        neither is a read. Counting them would make these pins measure
        "how many statements touched the settings table", which is not
        the property S6 is about."""
        return sum(
            1 for q in context.captured_queries
            if 'FROM "rag_ragsettings"' in q["sql"]
        )

    def test_the_settings_page_reads_the_row_once(self, client):
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(reverse("rag-settings")).status_code == 200
        assert self._settings_reads(ctx) == 1

    def test_the_top_k_write_reads_the_row_once(self, client):
        """THE COLLAPSE THIS FINDING IS ABOUT. `field=retrieval_top_k` is
        the one write on this page with a second reader:
        `_ragsettings_field_update` reads the row to save onto, and the
        `extra_check` it runs first (`_top_k_context_window_fit_check`)
        used to read its OWN copy for `hybrid_search`. Two reads for one
        POST, on the same row, microseconds apart. One now."""
        with CaptureQueriesContext(connection) as ctx:
            response = client.post(
                reverse("rag-settings-update"),
                {"field": "retrieval_top_k", "retrieval_top_k": "5"},
            )
        assert response.status_code == 302
        assert self._settings_reads(ctx) == 1
        assert RagSettings.get_solo().retrieval_top_k == 5

    def test_the_hybrid_write_reads_the_row_once(self, client):
        with CaptureQueriesContext(connection) as ctx:
            response = client.post(
                reverse("rag-settings-update"),
                {"field": "hybrid_search", "hybrid_search": "on"},
            )
        assert response.status_code == 302
        assert self._settings_reads(ctx) == 1

    def test_the_pin_is_not_vacuous_a_view_that_re_reads_is_red(self, client, monkeypatch):
        """NON-VACUITY, by mutation and not by assertion (no repo file is
        touched): put back the independent `get_solo()` the fit check used
        to make, and the top_k pin above goes from one read to two. A pin
        reading `== 1` that could not do this would be pinning nothing --
        it would stay green through a re-read reintroduced anywhere in the
        request."""
        real_check = rag_views._top_k_context_window_fit_check

        def check_with_its_own_read(top_k, settings_row):
            RagSettings.get_solo()  # the read the helper removed
            return real_check(top_k, settings_row)

        monkeypatch.setattr(
            rag_views, "_top_k_context_window_fit_check", check_with_its_own_read
        )

        with CaptureQueriesContext(connection) as ctx:
            response = client.post(
                reverse("rag-settings-update"),
                {"field": "retrieval_top_k", "retrieval_top_k": "5"},
            )
        assert response.status_code == 302
        assert self._settings_reads(ctx) == 2

    def test_the_helper_fetches_once_and_answers_from_the_request_after(self, rf):
        """The helper's own contract, directly: first ask queries, every
        later ask on the SAME request object does not, and a DIFFERENT
        request gets its own fetch -- the memo is request-scoped, never
        process-scoped. (A process-wide cache would serve an operator the
        value from before their own save.)"""
        request = rf.get("/rag/settings/")
        with CaptureQueriesContext(connection) as first:
            row = rag_views.settings_row_for(request)
        assert self._settings_reads(first) == 1

        with CaptureQueriesContext(connection) as again:
            assert rag_views.settings_row_for(request) is row
        assert self._settings_reads(again) == 0

        with CaptureQueriesContext(connection) as other:
            rag_views.settings_row_for(rf.get("/rag/settings/"))
        assert self._settings_reads(other) == 1
