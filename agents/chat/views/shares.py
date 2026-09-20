"""`POST /chat/c/<uuid>/share/` -- extend one thread to somebody else,
and take it back.

ONE ROUTE, TWO ACTIONS. Unsharing keys on a `share_id` in the same body
rather than living at a second URL, so there is one place that decides
who may change this thread's share list.

CLASS O, so every refusal here is 404 rather than 403: the URL names a
row, and a 403 on a row-addressed URL confirms the row exists.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from agents.chat.service import visible_conversation_or_404
from agents.models import Share
from agents.visibility import revoke_share, share_conversation
from identity import audit
from identity.contracts import actions
from identity.request import principal_for_request

_ACTIONS = ("share", "revoke")


@require_POST
def conversation_share(request, conversation_id):
    """Add or remove one `Share` row on this conversation.

    The conversation is resolved through `visible_conversations` first,
    so a caller who cannot see the thread gets the same 404 they get
    everywhere else -- and it is resolved BEFORE the action is checked,
    which is what lets an unrecognised action redirect to this thread
    rather than answering a raw 400 (M4, Wave C review); `share_conversation`/`revoke_share` then answer None
    for a caller who can SEE it but may not SHARE it -- a recipient -- and
    that becomes a 404 too. Owner or `sees_all_content` only: a recipient
    passing a thread on would make the owner's own list of who is reading
    it wrong.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    action = request.POST.get("action", "")
    if action not in _ACTIONS:
        # F7's flash-and-redirect (M4, Wave C review), for the reason
        # stated in full at `identity/views.py::user_edit`. The share
        # panel is a plain form on a conversation page, and this module
        # already commits to a refusal grammar of its own (404, never
        # 403) -- a raw 400 for a stale form was the odd one out.
        messages.error(request, f"{action!r} is not a recognised action.")
        return redirect("chat-conversation", conversation_id=conversation.pk)

    if action == "revoke":
        revoked = revoke_share(principal, conversation, request.POST.get("share", ""))
        if revoked is None:
            raise Http404("no such share")
        # `revoked` carries the deleted row's own subject/level (review
        # finding, 2026-08-31) -- the row is gone by the time this runs
        # and this module may not query `Share` itself to reconstruct
        # who it named, so without it the audit trail could never
        # answer "whose access was removed".
        audit.record(principal, actions.SHARE_REVOKED, target_type="conversation",
                     target_key=conversation.pk, level=revoked.level,
                     subject=revoked.subject, subject_key=revoked.subject_key)
        messages.info(request, "Removed that share.")
        return redirect("chat-conversation", conversation_id=conversation.pk)

    kind, _, raw = request.POST.get("subject", "").partition(":")
    # `isdecimal()`, NOT `isdigit()` (review finding, 2026-08-31):
    # `isdigit()` admits characters like "²" ("2") that `int()`
    # itself rejects with `ValueError` -- a 500 on a never-500 surface,
    # reachable by anyone who can merely SEE the thread, before the
    # owner check even runs. `isdecimal()` is exactly the set `int()`
    # can parse.
    if kind not in ("user", "group") or not raw.isdecimal():
        # A hand-made body, or a stale form. 404 rather than 400 for the
        # same reason the rest of this view answers 404: the caller is
        # not being told anything about which subjects exist.
        raise Http404("no such subject")
    level = request.POST.get("level", Share.Level.VIEW)
    subject = _subject(kind, int(raw))
    row = share_conversation(principal, conversation,
                             user=subject if kind == "user" else None,
                             group=subject if kind == "group" else None, level=level)
    if row is None:
        raise Http404("cannot share this conversation")
    audit.record(principal, actions.SHARE_ADDED, target_type="conversation",
                 target_key=conversation.pk, level=level, subject=kind,
                 subject_key=subject.pk)
    messages.info(request, "Shared.")
    return redirect("chat-conversation", conversation_id=conversation.pk)


def _subject(kind: str, pk: int):
    """The account or group this share names, or a 404.

    `django.contrib.auth`'s own models, reached through Django rather
    than through `identity.models` -- which no column outside `identity/`
    may import (import-law rule 2). `get_user_model()` is exactly what
    `AUTH_USER_MODEL` exists for.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    if kind == "user":
        return get_object_or_404(get_user_model(), pk=pk, is_active=True)
    return get_object_or_404(Group, pk=pk)
