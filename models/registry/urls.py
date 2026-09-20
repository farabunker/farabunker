"""URL routes for the model management console, mounted at /inference/ (see
config/urls.py)."""
from django.urls import path

from models.registry.views import (
    ConsoleView,
    connection_add,
    connection_remove,
    connection_sets,
    machine_model_add,
    model_set_edit,
    model_sets,
    role_assign,
    role_reencode,
    server_scan,
)

urlpatterns = [
    path("", ConsoleView.as_view(), name="inference-console"),
    path("connections/add/", connection_add, name="inference-connection-add"),
    path("connections/remove/", connection_remove, name="inference-connection-remove"),
    path("machine/add/", machine_model_add, name="inference-machine-add"),
    path("roles/assign/", role_assign, name="inference-role-assign"),
    path("roles/reencode/", role_reencode, name="inference-role-reencode"),
    path("scan/", server_scan, name="inference-server-scan"),
    path("connections/<int:pk>/sets/", connection_sets, name="inference-connection-sets"),
    path("sets/", model_sets, name="inference-model-sets"),
    path("sets/<int:pk>/", model_set_edit, name="inference-model-set-edit"),
]
