"""URL routes for the identity pages, mounted at /identity/ (config/urls.py).

UNGATED BY ANY FEATURE FLAG, exactly like `/chat/` and `/rag/`: accounts
are not optional machinery, and a third `FARABUNKER_FEATURES` token
would leave this whole surface untested in one of the two supported
suite states.
"""
from django.urls import path

from identity.views import (
    LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView,
    entitlement_edit, entitlements, group_edit, groups, settings_page,
    user_create, user_edit, users,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="identity-login"),
    path("logout/", LogoutView.as_view(), name="identity-logout"),
    path("password/", PasswordChangeView.as_view(), name="identity-password-change"),
    path("password/done/", PasswordChangeDoneView.as_view(),
         name="identity-password-change-done"),
    path("users/", users, name="identity-users"),
    path("users/create/", user_create, name="identity-user-create"),
    path("users/<int:pk>/edit/", user_edit, name="identity-user-edit"),
    path("settings/", settings_page, name="identity-settings"),
    path("groups/", groups, name="identity-groups"),
    path("groups/<int:pk>/edit/", group_edit, name="identity-group-edit"),
    path("entitlements/", entitlements, name="identity-entitlements"),
    path("entitlements/<int:pk>/", entitlement_edit, name="identity-entitlement-edit"),
]
