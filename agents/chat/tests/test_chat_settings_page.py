"""`/chat/settings/` -- "Chat" in the settings area (round 21).

CLASS S. The page's whole body is one administrator-only form deciding
box-wide operator policy about how a conversation's prompt is built, so
it refuses at the gate rather than rendering an empty shell -- the same
reasoning `rag-settings` records for itself.

THE ROUND TRIP IS THE POINT of most of what follows. A checkbox is the
one form control that vanishes from `request.POST` when it is off, so
"save unchecked" is the case a naive implementation silently gets wrong
by reading a missing field as "leave it alone" instead of as "off".
"""
from __future__ import annotations

import re

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_user, posture, reset_settings, seed_sweep_posture, sign_in,
)
from agents.models import ChatSettings
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db

_CHECKBOX = re.compile(r'<input[^>]*name="time_aware"[^>]*>')


def _checkbox(body: str) -> str:
    """JUST THE INPUT TAG, never the whole page.

    The settings shell ships a `input:checked + .chip` rule of its own,
    so a bare `"checked" in body` assertion is answered by a stylesheet
    rather than by the control this page renders -- the same reason
    `foundation/tests/test_shell.py` slices the nav out before asserting
    on it."""
    match = _CHECKBOX.search(body)
    assert match is not None, "the page rendered no time_aware checkbox at all"
    return match.group(0)


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheSingleton:
    def test_a_fresh_box_is_time_aware(self):
        """The owner's own words: "defaulted to on". No seed migration
        writes this row -- `get_solo()` creates it on first use, so a
        box restored from a backup taken before the table existed is
        time-aware too."""
        assert ChatSettings.objects.count() == 0
        assert ChatSettings.get_solo().time_aware is True

    def test_get_solo_is_one_row_forever(self):
        first, second = ChatSettings.get_solo(), ChatSettings.get_solo()
        assert first.pk == second.pk == 1
        assert ChatSettings.objects.count() == 1


class TestThePage:
    def test_an_admin_sees_the_toggle_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("chat-settings"))
            assert response.status_code == 200
            assert b'name="time_aware"' in response.content
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("chat-settings")).status_code == 403

    def test_the_checkbox_renders_checked_when_the_setting_is_on(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-settings")).content.decode()
        assert "checked" in _checkbox(body)

    def test_the_checkbox_renders_unchecked_when_the_setting_is_off(self, client):
        ChatSettings.objects.update_or_create(pk=1, defaults={"time_aware": False})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-settings")).content.decode()
        assert "checked" not in _checkbox(body)

    def test_saving_it_off_turns_it_off(self, client):
        """THE CASE A CHECKBOX GETS WRONG: an unchecked box is not
        submitted at all, so the absent field has to be read as OFF and
        not as "unchanged"."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("chat-settings"), {})
        assert response.status_code == 302
        assert ChatSettings.get_solo().time_aware is False

    def test_saving_it_on_turns_it_back_on(self, client):
        ChatSettings.objects.update_or_create(pk=1, defaults={"time_aware": False})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("chat-settings"), {"time_aware": "on"})
        assert response.status_code == 302
        assert ChatSettings.get_solo().time_aware is True

    def test_a_save_redirects_back_to_the_page(self, client):
        """Redirect-and-flash, the shape every mutation in this codebase
        uses: a refresh after saving must not re-post."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("chat-settings"), {"time_aware": "on"})
        assert response.headers["Location"] == reverse("chat-settings")

    def test_a_member_cannot_write_it_either(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("chat-settings"), {})
        assert response.status_code == 403
        assert ChatSettings.get_solo().time_aware is True
