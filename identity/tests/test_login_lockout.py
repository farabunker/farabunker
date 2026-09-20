"""Login lockout (S7): a per-username window over the audit rows the
login page already writes on every failed attempt.

Two things are pinned here, deliberately kept in one module rather than
split between `identity/tests/test_throttle.py` and an extension of
`identity/tests/test_login.py` (orchestrator ruling, H9 addendum): the
POLICY (`identity/throttle.py`'s `locked_out`/`seconds_remaining`) and
the VIEW WIRING (`identity.views.LoginView.post`) that refuses a
locked-out sign-in before Django's own `AuthenticationForm` ever runs.

The request-level refusal test does not wrap `client.post` in
`django_assert_num_queries` (the brief's original shape) -- a bound
`AuthenticationForm` touches the database for reasons that have nothing
to do with the lockout (session, CSRF, the eventual re-render), so a
query-count assertion there would pass or fail on unrelated churn and
would have to be "fixed" by loosening whenever it did. What the refusal
actually promises is that no password hash is computed, so that is what
is pinned: `django.contrib.auth.base_user.check_password` (the name
`AbstractBaseUser.check_password` calls) is mocked and asserted never
called. The query-count tool is kept for `throttle.locked_out` itself,
where every query the function issues is one this test controls.
"""
from __future__ import annotations

import unicodedata
from datetime import timedelta
from unittest import mock

import pytest
from django.urls import reverse

from identity import throttle
from identity.contracts.actions import LOGIN_FAILED
from identity.models import AuditEvent
from identity.tests._helpers import _record_failures, make_user

pytestmark = pytest.mark.django_db

A_KNOWN_PASSWORD = "correct-horse-battery-staple-9"


def _fullwidth(text: str) -> str:
    """An NFKC-equivalent SPELLING of `text` -- every ASCII printable
    character shifted into the Unicode "fullwidth" block (U+FF01-FF5E),
    which `unicodedata.normalize("NFKC", ...)` folds straight back to
    the ordinary character it stands in for. Used to prove the
    canonicaliser really does fold Unicode variants, not only
    whitespace."""
    return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)


@pytest.fixture
def a_user():
    return make_user()


@pytest.fixture
def another_user():
    return make_user()


class TestCanonicalUsername:
    """`throttle.canonical_username` is the one key the audit write and
    the throttle read now share (review round 1) -- these pin the
    function in isolation before the view-level regression tests below
    exercise it end to end."""

    def test_strips_surrounding_whitespace(self):
        assert throttle.canonical_username(" alice ") == "alice"

    def test_folds_an_nfkc_equivalent_spelling_to_the_ordinary_one(self):
        assert throttle.canonical_username(_fullwidth("alice")) == "alice"

    def test_is_exactly_strip_then_nfkc(self):
        """Pinned against the stdlib call directly, so this test would
        catch a drift from `UsernameField.to_python`'s own pipeline
        rather than only from whatever `_fullwidth` happens to cover."""
        raw = "  " + _fullwidth("Alice") + "  "
        assert throttle.canonical_username(raw) == unicodedata.normalize("NFKC", raw.strip())


class TestLockoutPolicy:
    def test_below_the_threshold_is_not_locked_out(self, db, a_user):
        _record_failures(a_user.username, 4)
        assert throttle.locked_out(a_user.username) is False

    def test_at_the_threshold_is_locked_out(self, db, a_user):
        _record_failures(a_user.username, throttle.FAILURE_THRESHOLD)
        assert throttle.locked_out(a_user.username) is True

    def test_failures_outside_the_window_do_not_count(self, db, a_user):
        _record_failures(a_user.username, throttle.FAILURE_THRESHOLD,
                          age=throttle.WINDOW + timedelta(seconds=1))
        assert throttle.locked_out(a_user.username) is False

    def test_another_accounts_failures_do_not_count(self, db, a_user, another_user):
        _record_failures(another_user.username, throttle.FAILURE_THRESHOLD)
        assert throttle.locked_out(a_user.username) is False

    def test_a_username_that_does_not_exist_locks_out_identically(self, db):
        """No enumeration oracle: the login page's generic error is the
        one thing that keeps wrong-password, no-such-user and inactive
        indistinguishable, and a lockout that only fired for real
        accounts would undo it."""
        _record_failures("no-such-person", throttle.FAILURE_THRESHOLD)
        assert throttle.locked_out("no-such-person") is True

    def test_the_username_match_is_case_insensitive(self, db, a_user):
        _record_failures(a_user.username.upper(), throttle.FAILURE_THRESHOLD)
        assert throttle.locked_out(a_user.username.lower()) is True

    def test_seconds_remaining_is_the_whole_window_while_locked_out(self, db, a_user):
        """An UPPER BOUND, deliberately -- see the function's docstring:
        naming the exact second an attacker is freed is a small oracle
        about their own timing."""
        _record_failures(a_user.username, throttle.FAILURE_THRESHOLD)
        assert throttle.seconds_remaining(a_user.username) == int(throttle.WINDOW.total_seconds())

    def test_seconds_remaining_is_zero_when_not_locked_out(self, db, a_user):
        assert throttle.seconds_remaining(a_user.username) == 0

    def test_the_lookup_is_one_query(self, db, a_user, django_assert_num_queries):
        with django_assert_num_queries(1):
            throttle.locked_out(a_user.username)


class TestLoginLockoutView:
    def test_a_locked_out_account_is_refused_before_the_password_is_checked(
            self, client, db, a_user):
        """Refused BEFORE authentication: a lockout that still hashed the
        password would leave the timing oracle and the CPU cost intact.
        `check_password` not being called is what pins that. The second
        assertion pins the other half: a bound form here would ALSO show
        Django's generic error, so the page would say two contradictory
        things at once."""
        _record_failures(a_user.username, throttle.FAILURE_THRESHOLD)
        with mock.patch("django.contrib.auth.base_user.check_password") as check:
            response = client.post(reverse("identity-login"),
                                    {"username": a_user.username,
                                     "password": "the-correct-one"})
        check.assert_not_called()
        assert response.status_code == 200
        assert b"Too many sign-in attempts" in response.content
        assert b"about 15 minutes" in response.content
        assert b"Please enter a correct" not in response.content
        assert response.wsgi_request.user.is_anonymous

    def test_a_refused_attempt_does_not_extend_the_lockout(self, db, client, a_user):
        """Or an attacker hammering a username locks its owner out forever
        rather than for the window.

        `seconds_remaining` cannot pin this on its own (review round 1
        finding): it reports the same fixed WINDOW for as long as the
        threshold is met, refused POST or not, so it would read
        identical before and after even if the refusal HAD been wrongly
        audited as a new failure. What would actually grow in that case
        is the `login_failed` row count, so that is what this counts."""
        _record_failures(a_user.username, throttle.FAILURE_THRESHOLD)
        canonical = throttle.canonical_username(a_user.username)
        before = AuditEvent.objects.filter(action=LOGIN_FAILED, target_label=canonical).count()
        client.post(reverse("identity-login"), {"username": a_user.username, "password": "x"})
        after = AuditEvent.objects.filter(action=LOGIN_FAILED, target_label=canonical).count()
        assert after == before

    def test_the_refusal_reads_the_same_for_a_username_that_does_not_exist(self, db, client):
        _record_failures("no-such-person", throttle.FAILURE_THRESHOLD)
        body = client.post(reverse("identity-login"),
                            {"username": "no-such-person", "password": "x"}).content
        assert b"Too many sign-in attempts" in body
        assert b"about 15 minutes" in body

    def test_failures_submitted_with_stray_whitespace_still_count_at_the_canonical_username(
            self, client, db, a_user):
        """Regression (review round 1, CRITICAL): the audit write used
        to key on the RAW submitted username while the throttle read
        stripped it before matching with `iexact` -- so a run of
        failures submitted as `" alice "` would never trip `alice`'s own
        lockout. Both sides now share `throttle.canonical_username`."""
        for _ in range(throttle.FAILURE_THRESHOLD):
            client.post(reverse("identity-login"),
                        {"username": f"  {a_user.username}  ", "password": "wrong"})
        response = client.post(reverse("identity-login"),
                                {"username": a_user.username, "password": "the-correct-one"})
        assert b"Too many sign-in attempts" in response.content

    def test_a_username_with_over_255_chars_of_leading_whitespace_still_counts(
            self, client, db, a_user):
        """Regression (whole-branch review, final wave): the write side
        (`LoginView.form_invalid`) used to truncate the RAW submitted
        string to 255 chars before canonicalising it, and the read side
        (`throttle.locked_out`) did the same -- symmetric with each
        other, but each individually wrong the same way. A username with
        more than 255 chars of leading whitespace ahead of the real
        content truncates, on THAT pipeline, to a string of nothing but
        spaces, which `canonical_username`'s own `.strip()` then
        collapses to `""` -- so a run of failures submitted that way
        keyed on `""`, not on the real username underneath the padding,
        and never counted toward it. Canonicalising the FULL string
        first (strip + NFKC), THEN truncating the result to 255, is what
        this test pins: the padded and unpadded spellings must resolve
        to the SAME lockout."""
        padded = (" " * 300) + a_user.username
        for _ in range(throttle.FAILURE_THRESHOLD):
            client.post(reverse("identity-login"),
                        {"username": padded, "password": "wrong"})
        response = client.post(reverse("identity-login"),
                                {"username": a_user.username, "password": "the-correct-one"})
        assert b"Too many sign-in attempts" in response.content

    def test_an_nfkc_equivalent_spelling_counts_against_the_canonical_username(
            self, client, db, a_user):
        """Regression (review round 1, CRITICAL): alternating between two
        Unicode spellings of the same name (here, a fullwidth Latin
        variant that NFKC-normalises to the ordinary one) must not
        defeat the threshold, exactly like alternating case already
        could not."""
        variant = _fullwidth(a_user.username)
        for _ in range(throttle.FAILURE_THRESHOLD):
            client.post(reverse("identity-login"), {"username": variant, "password": "wrong"})
        response = client.post(reverse("identity-login"),
                                {"username": a_user.username, "password": "the-correct-one"})
        assert b"Too many sign-in attempts" in response.content

    def test_an_unthrottled_login_still_works(self, db, client):
        """The regression guard: a lockout that broke ordinary sign-in
        would be a denial of service with a security rationale."""
        user = make_user(password=A_KNOWN_PASSWORD)
        response = client.post(reverse("identity-login"),
                                {"username": user.username, "password": A_KNOWN_PASSWORD},
                                follow=True)
        assert response.wsgi_request.user.is_authenticated
