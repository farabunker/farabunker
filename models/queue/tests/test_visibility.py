"""The queue's rows, and the difference between a row and its contents.

`_row(...)` builds a `models.queue.backend.QueueRow` directly rather
than enqueuing: `visible_rows` filters the frozen dataclasses
`queue_snapshot()` already fetched, so the dataclass IS the unit.
`visible_jobs`/`may_see_job_id` need real rows and get them from
`_job(...)`.
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from identity.contracts.postures import (
    POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from models.queue.backend import QueueRow
# MODULE-LEVEL CONSTANTS, not a `TextChoices` inner class: this app
# declares `QUEUED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED` and
# `STATE_CHOICES` at module scope (`models/queue/models.py:15-33`).
# There is no `InferenceJob.State`.
from models.queue.models import CANCELLED, FAILED, QUEUED, SUCCEEDED, InferenceJob
from models.queue.tests._helpers import (
    make_admin, make_user, posture, seed_sweep_posture, sign_in, user_principal,
)
from models.queue.visibility import (
    may_read_job_content, may_see_job_id, visible_jobs, visible_rows,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _row(**overrides) -> QueueRow:
    fields = dict(
        id=1, kind="rag.ask", payload={}, state=QUEUED, priority=100,
        exclusive=False, model_refs=[], footprint_bytes=None,
        created_at=timezone.now(), started_at=None, finished_at=None, error="",
        progress=None, not_before=None, passed_over=0,
    )
    fields.update(overrides)
    return QueueRow(**fields)


def _job(**overrides) -> InferenceJob:
    # `priority` is `PositiveIntegerField()` with NO default
    # (`models/queue/models.py:89`) -- it is resolved at enqueue time by
    # the priority chain, so a row built directly must supply one or
    # the insert raises IntegrityError.
    fields = dict(kind="rag.ask", payload={}, state=QUEUED, priority=100)
    fields.update(overrides)
    return InferenceJob.objects.create(**fields)


class TestRowsVersusContent:
    def test_an_admin_sees_every_row_with_the_content_setting_off(self):
        """A queue row is OPERATIONAL: its kind, owner, state, progress,
        priority and timings are what an administrator needs to run the
        box, and none of them is somebody's words."""
        admin = make_admin()
        rows = [_row(payload={"actor_kind": "user", "actor_key": "7"}),
                _row(id=2, payload={})]
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert len(visible_rows(user_principal(admin), rows)) == 2

    def test_and_the_payload_of_a_job_they_did_not_start_is_withheld(self):
        """A prompt is somebody's words and a retrieval answer is
        somebody's answer.

        THE OTHER PERSON'S KEY IS DERIVED FROM THIS ADMIN'S OWN, never a
        literal (Coherence Wave C fix round). It was `"7"`, which is only
        "somebody else" for as long as no admin in this module happens to
        be allocated pk 7 -- Postgres sequences are not rolled back
        between tests, so the pk this `make_admin()` gets depends on how
        many users every test that ran before it created. Adding three
        user-creating tests elsewhere in `models/queue/tests` was enough
        to walk the sequence onto 7 and turn this green assertion into a
        red one, with nothing about visibility having changed. `admin.pk
        + 1` is "not this principal" by construction, whatever the
        sequence is doing.
        """
        admin = make_admin()
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert may_read_job_content(
                user_principal(admin),
                {"actor_kind": "user", "actor_key": str(admin.pk + 1)}) is False

    def test_and_their_own_job_is_not_withheld_from_them(self):
        """Rows-versus-content is about OTHER PEOPLE's content. An
        administrator reading back their own prompt is not a
        disclosure."""
        admin = make_admin()
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert may_read_job_content(
                user_principal(admin),
                {"actor_kind": "user", "actor_key": str(admin.pk)}) is True

    def test_turning_the_setting_on_opens_the_payload_and_nothing_else(self):
        admin = make_admin()
        rows = [_row(payload={"actor_kind": "user", "actor_key": "7"})]
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            assert may_read_job_content(
                user_principal(admin), rows[0].payload) is True
            assert len(visible_rows(user_principal(admin), rows)) == 1

    def test_a_member_sees_only_jobs_they_caused(self):
        ann, bob = make_user(), make_user()
        rows = [_row(payload={"actor_kind": "user", "actor_key": str(ann.pk)}),
                _row(id=2, payload={"actor_kind": "user", "actor_key": str(bob.pk)})]
        with posture(POSTURE_PERSONAL):
            assert [r.id for r in visible_rows(user_principal(ann), rows)] == [1]

    def test_a_job_with_no_actor_keys_is_visible_to_admins_and_nobody_else(self):
        """FAIL CLOSED FOR MEMBERS, OPEN FOR ADMINISTRATORS. Every job
        enqueued before this phase is in this state, and a member must
        see nothing they did not cause."""
        member, admin = make_user(), make_admin()
        rows = [_row(payload={})]
        with posture(POSTURE_PERSONAL):
            assert visible_rows(user_principal(member), rows) == []
            assert len(visible_rows(user_principal(admin), rows)) == 1

    def test_a_non_dict_payload_is_not_a_500(self):
        """`InferenceJob.payload` is a JSONField: a bare string or a
        list survives it as easily as a dict, and the queue page is a
        never-500 surface."""
        member = make_user()
        with posture(POSTURE_PERSONAL):
            assert visible_rows(user_principal(member), [_row(payload=[1, 2])]) == []
            assert visible_rows(user_principal(member), [_row(payload="x")]) == []
            assert may_read_job_content(user_principal(member), None) is False

    def test_a_member_is_not_the_open_principal(self):
        """The one way a fail-closed rule could quietly fail open: a
        pre-phase row reads back as `("open", "box")`, and no member
        ever matches that pair."""
        member = make_user()
        rows = [_row(payload={"actor_kind": "open", "actor_key": "box"})]
        with posture(POSTURE_PERSONAL):
            assert visible_rows(user_principal(member), rows) == []


class TestTheQuerysetAndTheIdLookup:
    def test_the_queryset_and_the_row_filter_agree(self):
        """One predicate, two callers. A page that showed a row the
        cancel route then refused -- or the reverse -- is the bug this
        assertion exists to prevent."""
        ann = make_user()
        mine = _job(payload={"actor_kind": "user", "actor_key": str(ann.pk)})
        theirs = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_PERSONAL):
            principal = user_principal(ann)
            assert list(visible_jobs(principal).values_list("pk", flat=True)) == [mine.pk]
            assert may_see_job_id(principal, mine.pk) is True
            assert may_see_job_id(principal, theirs.pk) is False

    def test_an_admin_may_see_any_id_including_one_that_does_not_exist(self):
        """`is_admin` short-circuits before the query, which is what
        keeps the two poll routes from paying a lookup they do not
        need. An unknown id is then the VIEW's 404, not this
        function's."""
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            assert may_see_job_id(user_principal(admin), 99999999) is True

    def test_an_open_box_returns_everything_unfiltered(self):
        _job(payload={})
        with posture(POSTURE_OPEN):
            sql = str(visible_jobs(OPEN_PRINCIPAL).query)
        assert "actor_key" not in sql


class TestTheCancelRoute:
    def test_cancel_is_administer_so_an_admin_may_cancel_anything(self, client):
        """A stuck queue is an operational fact and clearing it is the
        operator's job. There is no posture, and no setting, in which
        the operator cannot clear their own queue."""
        admin = make_admin()
        job = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            response = client.post(reverse("jobs-queue-cancel", args=[job.pk]))
        assert response.status_code == 302
        job.refresh_from_db()
        assert job.state == CANCELLED

    def test_a_member_cancelling_someone_elses_job_gets_404_not_403(self, client):
        """403 on a row-addressed URL confirms the row exists."""
        member = make_user()
        job = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("jobs-queue-cancel", args=[job.pk]))
        assert response.status_code == 404
        job.refresh_from_db()
        assert job.state == QUEUED

    def test_a_member_may_cancel_their_own(self, client):
        member = make_user()
        job = _job(payload={"actor_kind": "user", "actor_key": str(member.pk)})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            assert client.post(
                reverse("jobs-queue-cancel", args=[job.pk])).status_code == 302


class TestTheQueuePage:
    def test_it_renders_a_withheld_marker_not_a_blank(self, client):
        """A blank summary would read as a job that carried nothing.
        The row must say that its content is hidden."""
        admin = make_admin()
        _job(kind="rag.ask",
             payload={"question": "a private question",
                      "actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("jobs-queue")).content.decode()
        assert "a private question" not in body
        assert "Content hidden" in body
        # MINOR (fix round 1): the row does not just say its content is
        # hidden -- it says WHY and WHERE to change it, so an operator
        # who wants to see it is not left guessing at a setting they
        # have to go find on their own.
        assert "admin_sees_content" in body
        assert reverse("identity-settings") in body

    def test_and_the_row_itself_is_still_there(self, client):
        """The whole of rows-versus-content, on one page: the operator
        can see that something is queued and act on it, without reading
        it. The row's kind, state and cancel control are all present in
        the same response that withheld the question."""
        admin = make_admin()
        job = _job(kind="rag.ask",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("jobs-queue")).content.decode()
        assert "rag.ask" in body or "Ask" in body
        assert reverse("jobs-queue-cancel", args=[job.pk]) in body

    def test_a_withheld_poll_hides_the_question_as_well_as_the_answer(self, client):
        """`JobStatus.summary` is `summarize_job`'s output, which for
        `rag.ask` IS the question. Hiding the answer and leaving the
        question is not hiding anything."""
        admin = make_admin()
        job = _job(kind="rag.ask",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("rag-ask-status", args=[job.pk])).json()
        assert "a private question" not in str(body)
        assert body["content_hidden"] is True
        assert "result" not in body and "answer" not in body

    def test_a_withheld_failed_job_hides_its_error_on_the_queue_page(self, client):
        """A failure message is content too -- it can quote the payload
        back -- so it follows `summary` behind the same content-hidden
        gate rather than leaking through as the field the gate forgot."""
        admin = make_admin()
        job = _job(kind="rag.ask", state=FAILED, error="could not answer: a private question",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": "99999999"})
        job.finished_at = timezone.now()
        job.save(update_fields=["finished_at"])
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("jobs-queue")).content.decode()
        assert "could not answer" not in body
        assert "a private question" not in body

    def test_and_shows_it_once_content_is_visible(self, client):
        admin = make_admin()
        job = _job(kind="rag.ask", state=FAILED, error="could not answer: a private question",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": "99999999"})
        job.finished_at = timezone.now()
        job.save(update_fields=["finished_at"])
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            body = client.get(reverse("jobs-queue")).content.decode()
        assert "could not answer" in body

    def test_a_withheld_failed_poll_hides_the_error(self, client):
        admin = make_admin()
        job = _job(kind="rag.ask", state=FAILED, error="could not answer: a private question",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("rag-ask-status", args=[job.pk])).json()
        assert body["content_hidden"] is True
        assert body["error"] == ""
        assert "setup_url" in body

    def test_and_an_unhidden_failed_poll_still_shows_the_error(self, client):
        admin = make_admin()
        job = _job(kind="rag.ask", state=FAILED, error="could not answer: a private question",
                   payload={"question": "a private question",
                            "actor_kind": "user", "actor_key": str(admin.pk)})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("rag-ask-status", args=[job.pk])).json()
        assert body.get("content_hidden") is not True
        assert body["error"] == "could not answer: a private question"


class TestTheCountsAndPositionsAgreeWithVisibility:
    """Fix round 1, IMPORTANT 4/5: filtering the queue page's three
    lists to what a principal may see must not corrupt two facts that
    are about the FULL, box-wide queue -- a row's true place in line,
    and how many finished jobs THIS principal can actually see."""

    def test_finished_count_reflects_only_visible_rows_not_the_whole_box(self, client):
        member = make_user()
        _job(state=SUCCEEDED, payload={"actor_kind": "user", "actor_key": "99999999"})
        _job(state=SUCCEEDED, payload={"actor_kind": "user", "actor_key": str(member.pk)})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("jobs-queue"))
        assert response.context["finished_count"] == 1
        assert len(response.context["finished_rows"]) == 1

    def test_a_members_own_waiting_job_keeps_its_true_position(self, client):
        """Six jobs ahead of a member's own -- none of them the
        member's -- and the member's is 7th. Filtering the list down to
        what the member may see must not relabel their own row #1."""
        member = make_user()
        for _ in range(6):
            _job(priority=100, payload={"actor_kind": "user", "actor_key": "99999999"})
        mine = _job(priority=100, payload={"actor_kind": "user", "actor_key": str(member.pk)})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("jobs-queue"))
        rows = response.context["waiting_rows"]
        assert len(rows) == 1
        assert rows[0]["id"] == mine.pk
        assert rows[0]["position"] == 7

    def test_an_admin_sees_the_true_box_wide_count_and_positions_unchanged(self, client):
        """Anti-vacuous pin: an admin's own view of the SAME setup above
        is untouched -- `visible_rows` is a no-op for `is_admin`."""
        admin = make_admin()
        for _ in range(2):
            _job(priority=100, payload={"actor_kind": "user", "actor_key": "99999999"})
        _job(state=SUCCEEDED, payload={"actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            response = client.get(reverse("jobs-queue"))
        assert response.context["finished_count"] == 1
        assert [row["position"] for row in response.context["waiting_rows"]] == [1, 2]


def test_live_job_for_finds_a_queued_job_and_ignores_terminal_ones():
    from models.queue.models import CANCELLED, QUEUED, InferenceJob
    from models.queue.visibility import live_job_for

    InferenceJob.objects.create(kind="rag.consolidate", priority=200, state=CANCELLED,
                                payload={"conversation": "abc"})
    assert live_job_for("rag.consolidate", conversation="abc") is None

    live = InferenceJob.objects.create(kind="rag.consolidate", priority=200, state=QUEUED,
                                       payload={"conversation": "abc"})
    assert live_job_for("rag.consolidate", conversation="abc") == live.pk
    # Keyed on the payload, so another conversation's job is not this one.
    assert live_job_for("rag.consolidate", conversation="def") is None
    # And keyed on the kind, so an unrelated job of another kind is not.
    assert live_job_for("rag.ingest", conversation="abc") is None
