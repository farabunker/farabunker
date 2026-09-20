"""Login lockout: the hook `identity/views.py::LoginView.form_invalid`
has carried a comment about since IA-1 ("login lockout is deferred and
this is its hook"), now filled in.

NO NEW DEPENDENCY, deliberately. `django-axes` is a fine package and the
wrong fit here: its own models, migrations, admin registration,
middleware and settings surface, on a box whose premise is a small
auditable dependency set -- to do what six lines of policy over rows this
codebase ALREADY WRITES on every failed attempt can do.

THE TRADE-OFF, STATED RATHER THAN HIDDEN: a per-USERNAME window lets
somebody who knows a username lock its owner out for the window. That is
inherent to username lockout, and the alternative (per-IP) is worse on a
LAN where every browser can share one NAT address. The window is short,
the box is LAN-only, and every attempt is in the audit log either way.

NO ENUMERATION ORACLE. `locked_out` answers identically for a username
that exists and one that does not -- it counts audit rows, and a failed
attempt against a non-existent username writes one exactly like any
other. That is load-bearing: Django's generic `AuthenticationForm` error
is the one thing keeping wrong-password, no-such-user and inactive
indistinguishable on this page, and a lockout that fired only for real
accounts would hand back the oracle.

ONE KEY, WRITE AND READ (review round 1). The audit row used to be
written under the raw submitted string while a read normalised it
first -- so a run of failures submitted as `" alice "`, or under any
other Unicode spelling that collapses to the same name, silently never
counted toward `alice`'s own lockout. `canonical_username` is the one
normalisation both sides now share: `identity.views.LoginView.
form_invalid` canonicalises before writing the audit row, and
`locked_out` canonicalises before reading it back, so the row on disk
and the value being matched against it are the same string.
"""
from __future__ import annotations

import unicodedata
from datetime import timedelta

from django.utils import timezone

from identity import audit

# Five attempts in fifteen minutes. Generous enough that a person who
# genuinely mistypes a password does not trip it (the fifth attempt is
# already unusual), tight enough that an online guessing run gets 5
# attempts per 15 minutes per username instead of thousands per second.
FAILURE_THRESHOLD = 5
WINDOW = timedelta(minutes=15)


def canonical_username(username: str) -> str:
    """The one key both the audit write and the throttle read compare
    on: `str.strip()` then Unicode NFKC normalisation -- exactly the
    pipeline `django.contrib.auth.forms.UsernameField.to_python` already
    runs the SAME string through on its way into `AuthenticationForm`
    (minus that field's max-length short-circuit, which exists to dodge
    a slow normalisation on adversarial input a bound *field* must
    accept).

    NEVER TRUNCATE BEFORE CALLING THIS (whole-branch review, final
    wave). Both callers used to truncate to 255 chars first and
    canonicalise second -- see `locked_out` and `identity.views.
    LoginView.form_invalid` for why that was wrong, not merely
    stylistically different from truncating after.

    CASEFOLDING STAYS THE QUERY'S JOB
    (`identity.audit.failed_logins_since`'s `target_label__iexact`).
    Folding case here too would make the CANONICAL key diverge from what
    the audit row displays -- and the row exists to show what was
    actually typed, `iexact` is what makes `Alice`/`alice` count as one
    attacker regardless.
    """
    return unicodedata.normalize("NFKC", (username or "").strip())


def locked_out(username: str, *, now=None) -> bool:
    """Whether sign-in for `username` is refused right now.

    ONE QUERY, and a counted one rather than a fetch: the row bodies are
    not needed and an attack run can leave a great many of them.

    CANONICALISE, THEN TRUNCATE TO 255 (whole-branch review, final wave;
    was the other way around). Truncating a raw, unstripped string to
    255 chars FIRST breaks on a username with more than 255 chars of
    leading whitespace ahead of the real content: slicing the first 255
    chars off `" " * 300 + "alice"` yields 255 spaces with `"alice"`
    never reached, which `canonical_username`'s own `.strip()` then
    collapses to `""` -- while canonicalising first strips the whole
    string down to `"alice"` before the (now moot) truncation. The write
    side (`identity.views.LoginView.form_invalid`) hit exactly that case
    for the SAME input and used to land on a different key
    (`""` on write, `"alice"` on this read) -- the "one key, both sides"
    property this module's own docstring promises, silently broken past
    that boundary. Both sides now canonicalise the FULL string first and
    truncate the RESULT, so they agree regardless of how much leading
    whitespace precedes the real username.
    """
    username = canonical_username(username or "")[:255]
    if not username:
        return False
    now = now or timezone.now()
    return audit.failed_logins_since(username, now - WINDOW) >= FAILURE_THRESHOLD


def seconds_remaining(username: str, *, now=None) -> int:
    """Whole seconds until `username` may try again; 0 when not locked
    out.

    An UPPER BOUND rather than an exact countdown -- it reports the whole
    window, not the time until the oldest counted failure ages out.
    Telling the caller precisely which second frees them is a small
    oracle about the attack's own timing, and "try again in a few
    minutes" is the honest operator-facing answer either way.
    """
    return int(WINDOW.total_seconds()) if locked_out(username, now=now) else 0
