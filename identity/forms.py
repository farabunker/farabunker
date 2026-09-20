"""The two identity forms.

PLAIN `Form`, NOT `ModelForm`, deliberately: every write on this column
goes through `identity.services`, where the last-admin guard, the three
posture refusals and the audit rows live. A `ModelForm` would carry a
`save()` that reaches the model directly -- a second door to exactly the
writes that have guards.
"""
from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password

from identity.contracts.postures import LIBRARY_CHOICES, POSTURE_CHOICES
from identity.models import User


class UserCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    # Rendered only in the organisation posture: in `personal` every
    # account is an administrator, so the page offers no choice.
    is_superuser = forms.BooleanField(required=False)

    def clean_password(self):
        """Django's own validators, not ours -- length, commonness,
        similarity to the username, all-numeric.

        `user=User(username=...)`, UNSAVED, purely so
        `UserAttributeSimilarityValidator` has a username to compare
        against -- exactly what Django's own `UserCreationForm` does.
        Without it, `validate_password`'s `user` argument stays `None`
        and the validator returns immediately, silently skipping the
        similarity check this account-creation path promises. `self.data`,
        not `self.cleaned_data`, because `username` may not have cleaned
        yet when field errors are processed out of declaration order --
        `self.data.get(...)` never raises, worst case comparing against
        an empty string.
        """
        value = self.cleaned_data["password"]
        validate_password(value, user=User(username=self.data.get("username", "")))
        return value


class PostureForm(forms.Form):
    posture = forms.ChoiceField(choices=POSTURE_CHOICES)
    library_posture = forms.ChoiceField(choices=LIBRARY_CHOICES)
    admin_sees_content = forms.BooleanField(required=False)
    session_idle_minutes = forms.IntegerField(min_value=0, max_value=60 * 24 * 30)


class NameForm(forms.Form):
    """One name field, for a group or an entitlement.

    A plain `Form`, like every other form on this column: the write goes
    through `identity.services`, where the refusals and the audit rows
    live, and a `ModelForm`'s `save()` would be a second door to exactly
    the writes that have guards.
    """
    name = forms.CharField(max_length=255)
    description = forms.CharField(max_length=2000, required=False,
                                  widget=forms.Textarea(attrs={"rows": 2}))


class GrantForm(forms.Form):
    """Who to grant to, and in what role.

    `subject` is ONE field carrying `"user:<pk>"` or `"group:<pk>"`,
    rather than two optional fields the view has to reconcile: the XOR
    is then unrepresentable in the form rather than merely refused after
    it, and a form that cannot express an invalid state cannot submit
    one.
    """
    subject = forms.RegexField(regex=r"^(user|group):[0-9]+$")
    role = forms.ChoiceField(choices=(("member", "Member"), ("owner", "Owner")))

    def clean_subject(self):
        kind, _, raw = self.cleaned_data["subject"].partition(":")
        return kind, int(raw)


class SetPasswordForm(forms.Form):
    """The users page's own password-RESET field.

    `identity.services.set_password` runs no validation at all -- it is
    documented as a plain reset, not a policy check -- and
    `AbstractBaseUser.set_password("")` happily produces a USABLE,
    EMPTY password. Without this form, posting `action=set_password`
    with no `password` field locked an account's password to the empty
    string. `clean_password` runs the same validators `UserCreateForm`
    does, and `target_user` (an already-fetched `User`, set by the
    view before calling `is_valid()`) lets the similarity-to-account
    validator compare against the account being reset rather than
    nothing.
    """
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, target_user=None, **kwargs):
        self._target_user = target_user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        value = self.cleaned_data["password"]
        validate_password(value, user=self._target_user)
        return value
