"""The chat column's settings page -- `/chat/settings/`, "Chat" in the
settings area's Setup group (`foundation/settings_area.py`).

ONE ROW, ONE FORM, ONE ROUTE. `agents.models.ChatSettings` is the
singleton this page edits, and it holds the policies that govern how a
CONVERSATION'S PROMPT IS BUILT -- which is why the page exists at all
rather than the toggle being hung on "Identity & security" (posture,
sessions, admin content access) or on "Library" (retention, upload caps,
retrieval). Neither of those pages is about the prompt, and a settings
row belongs to the column that reads it.

GET renders, POST saves, both on the SAME url name -- the shape
`identity.views.settings_page` already uses for its own singleton.
`rag/settings.html` reaches the same destination from the other end:
it carries seven independent policies with genuinely different
validation, and S2 (Coherence Wave C) collapsed its seven per-field
endpoints onto ONE dispatched write (`tools.rag.views.
library_settings_update`, a hidden `field` input saying which form
submitted) -- the sanctioned topology, which `models.queue.views.
queue_settings_update` had already settled. This page carries a single
checkbox, so it does not need even the hidden field: GET/POST on one
url name is the simpler end of the same shape, not a different one.

CLASS S (`identity/routes.py`): a page whose entire body is an
administrator-only form has nothing to show anybody else, exactly the
reasoning `rag-settings`' own entry records. The template mirrors the
gate with `identity_is_admin` so an OPEN box -- where everybody is an
administrator -- renders and gates the same way.

NO AUDIT ROW, and that is a decision rather than an omission: this is
operator policy about prompt text, the same kind of thing every
`RagSettings` toggle is (none of which audits either), not a security
posture. `identity.services.set_posture`'s audit exists because
changing a posture changes who may read what; changing this changes
whether the model is told what day it is.
"""
from __future__ import annotations

from django import forms
from django.contrib import messages
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from agents.models import ChatSettings
from foundation.settings_area import settings_redirect


class ChatSettingsForm(forms.Form):
    """The one field, as a plain `Form` rather than a `ModelForm`.

    `required=False` is what makes a CHECKBOX round-trip correctly: an
    unchecked HTML checkbox is not submitted at all, so a required
    `BooleanField` would reject "off" as a missing field instead of
    reading it as the answer it is. `cleaned_data["time_aware"]` is
    therefore `False` for an absent field and `True` only when the box
    was actually checked -- which is also why this view never needs
    `rag`'s own `"on"/""/"off"` raw-string check: that endpoint parses
    `request.POST` by hand, and this one lets the form do it.
    """

    time_aware = forms.BooleanField(required=False)


# R7 (audit 2, S22): the method vocabulary is DECLARED, not implied by
# a branch in the body. GET renders, POST mutates, and anything else is
# a 405 from Django before this function runs -- so the `request.method`
# test below chooses between methods this view really serves rather than
# silently treating a PUT or a DELETE as a read.
#
# HEAD IS LISTED EXPLICITLY (audit-2 confirm, observation 7). Django
# serves HEAD by running the GET path and dropping the body, which is
# what this view did before it declared anything; `require_http_methods`
# admits only what it is given, so omitting HEAD would have turned a
# previously-working request into a 405 for anything that HEADs a chat
# URL. Declaring the vocabulary was the point -- narrowing it was not.
@require_http_methods(["GET", "HEAD", "POST"])
def chat_settings(request):
    """GET/POST /chat/settings/ -- "Chat" in the settings area.

    The redirect-and-flash shape every mutation in this codebase uses:
    a successful save redirects back here so a refresh cannot re-post,
    and says so with `messages.info`. There is nothing here that can
    refuse -- a checkbox has two legal states and both are legal
    settings -- so there is no error branch to write, and none is
    invented to look symmetrical with pages that have one.
    """
    row = ChatSettings.get_solo()
    if request.method == "POST":
        form = ChatSettingsForm(request.POST)
        if form.is_valid():
            row.time_aware = form.cleaned_data["time_aware"]
            row.save(update_fields=["time_aware", "updated_at"])
            messages.info(request, "Chat settings saved.")
            return settings_redirect(request, "chat-settings")
    else:
        form = ChatSettingsForm(initial={"time_aware": row.time_aware})
    return render(request, "chat/settings.html", {"form": form})
