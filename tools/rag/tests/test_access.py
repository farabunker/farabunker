"""`tools.rag.access` -- Ask history is content, not a row.

`_ask_record(...)` builds the minimum `AskRecord` row these tests need,
local to this module -- the same "no shared factory layer" convention
`tools/rag/tests/test_views_documents.py`'s own per-class `_make_document`
already follows.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.access import owner_fields
from identity.contracts.postures import POSTURE_PERSONAL
from identity.contracts.principals import SERVICE_PRINCIPAL
from tools.rag.access import visible_ask_records
from tools.rag.models import AskRecord
from tools.rag.tests._helpers import (
    make_admin, make_user, posture, seed_sweep_posture, sign_in, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _ask_record(**overrides) -> AskRecord:
    fields = dict(question="q", connection_name="conn", model_id="model", answer="a")
    fields.update(overrides)
    return AskRecord.objects.create(**fields)


class TestAskHistoryIsContent:
    def test_a_member_sees_only_their_own_records(self):
        ann, bob = make_user(), make_user()
        mine = _ask_record(**owner_fields(user_principal(ann)))
        theirs = _ask_record(**owner_fields(user_principal(bob)))
        with posture(POSTURE_PERSONAL):
            pks = {r.pk for r in visible_ask_records(user_principal(ann))}
        assert mine.pk in pks and theirs.pk not in pks

    def test_an_admin_with_the_setting_off_sees_only_their_own(self):
        """An Ask record holds a QUESTION and an ANSWER, so it is
        content, not an operational row."""
        admin, bob = make_admin(), make_user()
        theirs = _ask_record(**owner_fields(user_principal(bob)))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert theirs.pk not in {r.pk for r in
                                     visible_ask_records(user_principal(admin))}

    def test_the_count_comes_off_the_same_function_as_the_rows(self, client):
        """A count that disagreed with the rows it describes would leak
        exactly the fact the filter exists to hide -- and the count is
        the easiest one to forget, because it renders no record.

        The two now render on DIFFERENT pages: since UI-1 the count sits
        beside the retention field it explains, on Library,
        while the rows stay on Ask history. That is precisely why this
        pin still earns its place -- two pages are easier to let drift
        apart than two lines of one template. Asserted as an ADMIN WITH
        THE CONTENT SETTING OFF: the one principal for whom a bare
        `AskRecord.objects.count()` would disagree with the rows, and the
        only kind who can reach the settings page at all."""
        admin, bob = make_admin(), make_user()
        _ask_record(**owner_fields(user_principal(bob)))
        _ask_record(**owner_fields(user_principal(admin)))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            sign_in(client, admin)
            history = client.get(reverse("rag-history"))
            library_settings = client.get(reverse("rag-settings"))
        assert len(history.context["records"]) == 1
        assert library_settings.context["record_count"] == 1

    def test_a_pre_phase_record_with_blank_owner_columns_is_nobodys(self):
        """`("", "")` is what a record written before this phase
        carries. It belongs to nobody until `adopt_open_rows` claims
        it, and until then a member must not see it."""
        _ask_record(owner_kind="", owner_key="")
        with posture(POSTURE_PERSONAL):
            assert visible_ask_records(user_principal(make_user())).count() == 0

    def test_an_admin_sees_a_record_a_shell_path_made(self):
        """MINOR (fix round 1): the service-row clause
        `visible_ask_records` carries -- `manage.py ask` writes no
        `AskRecord` today, but `rag.ask` enqueued from a command would,
        and a row nobody can see is a row nobody can prune."""
        shell_made = _ask_record(**owner_fields(SERVICE_PRINCIPAL))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert shell_made.pk in {r.pk for r in
                                     visible_ask_records(user_principal(make_admin()))}

    def test_a_member_does_not_see_it(self):
        shell_made = _ask_record(**owner_fields(SERVICE_PRINCIPAL))
        with posture(POSTURE_PERSONAL):
            assert shell_made.pk not in {r.pk for r in
                                         visible_ask_records(user_principal(make_user()))}
