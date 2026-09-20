"""URL routes for the RAG module, mounted at /rag/ (see config/urls.py)."""
from django.urls import path

from tools.rag.views import (
    AskJobStatusView,
    AskPageView,
    AskView,
    DocumentsView,
    HistoryView,
    LibrarySettingsView,
    SearchView,
    category_delete,
    category_rename,
    document_delete,
    document_file,
    document_labels_bulk,
    document_reingest,
    document_transcript,
    document_upload,
    library_settings_update,
    workstream_pin,
)

urlpatterns = [
    path("", AskPageView.as_view(), name="rag-ask-page"),
    # C-55: `AskView`/`AskJobStatusView` are plain functions now, not DRF
    # `APIView` classes -- no `.as_view()`. Names kept (orchestrator
    # ruling on C-55: renaming would need a 33-file by-name sweep for no
    # behavioural gain).
    path("ask/", AskView, name="rag-ask"),
    path("ask/jobs/<int:job_id>/", AskJobStatusView, name="rag-ask-status"),
    path("search/", SearchView.as_view(), name="rag-search"),
    path("documents/", DocumentsView.as_view(), name="rag-documents"),
    path("documents/upload/", document_upload, name="rag-document-upload"),
    path("documents/<int:doc_id>/file/", document_file, name="rag-document-file"),
    path("documents/<int:doc_id>/transcript/", document_transcript, name="rag-document-transcript"),
    path("documents/<int:doc_id>/delete/", document_delete, name="rag-document-delete"),
    path("documents/<int:doc_id>/reingest/", document_reingest, name="rag-document-reingest"),
    # THE ONE LABEL WRITER. It ADDS or REMOVES across many documents
    # (never "set to exactly this") -- decision 29's ambiguity rule, now
    # trivially satisfied because there is only one route left to be
    # ambiguous about. The per-document SET route that used to sit above
    # this one was deleted (C-36): no template ever reversed it.
    path("documents/labels/", document_labels_bulk, name="rag-document-labels-bulk"),
    path("workstreams/<int:ws_id>/pin/", workstream_pin, name="rag-workstream-pin"),
    path("categories/<int:cat_id>/rename/", category_rename, name="rag-category-rename"),
    path("categories/<int:cat_id>/delete/", category_delete, name="rag-category-delete"),
    path("history/", HistoryView.as_view(), name="rag-history"),
    # "Library" (UI-1 as "Library settings"; renamed in UI-2): the page
    # that renders the seven settings forms, and the ONE endpoint they
    # all post to.
    #
    # S2 (Coherence Wave C): the seven per-field URLs this used to carry
    # (`rag-history-settings` and its six siblings, under
    # `/rag/history/settings/...`) are RETIRED -- seven paths, seven
    # `ROUTE_RULES` classes and seven route-matrix drivers for seven
    # writes to one table on one page, growing by three touch points per
    # field added. One dispatched endpoint replaces them, the shape
    # `models/queue/urls.py` already had and the one `docs/EXTENDING.md`
    # now prescribes; `tools/rag/views.py::library_settings_update`
    # reads the hidden `field` input each form carries.
    path("settings/", LibrarySettingsView.as_view(), name="rag-settings"),
    path("settings/update/", library_settings_update, name="rag-settings-update"),
]
