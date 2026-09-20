"""Unit tests for foundation/uploads.py (S9): a request-body cap, a file-count
cap, and the upload settings this platform actually has stated in one place.

`RequestBodyLimitMiddleware` is exercised through the real middleware stack
(`django_db` for the `/` requests below -- the landing page's context
processors read `IdentitySettings.get_solo()`, which needs a real
connection), not called directly, because ordering in `settings.MIDDLEWARE`
is exactly what this closes: a middleware that works when called by hand but
sits after `CsrfViewMiddleware` in the list would still let an oversize body
reach the upload handlers first.

`CONTENT_LENGTH=`, not `HTTP_CONTENT_LENGTH=`, throughout -- see the class
docstring below. Getting this wrong makes every test here pass for the wrong
reason both before and after the fix.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


class TestRequestBodyLimit:
    """`Content-Length` is a WSGI environ key in its own right, not an
    `HTTP_`-prefixed one, and Django's test client merges `**extra` into the
    environ verbatim without translating it -- `RequestFactory.generic()`
    sets `CONTENT_LENGTH` from the payload and *then* applies `extra`, so an
    override under this exact name takes. `HTTP_CONTENT_LENGTH="2048"` would
    land at `request.META["HTTP_CONTENT_LENGTH"]` while the real declared
    length stayed whatever the payload actually was."""

    def test_a_declared_oversize_body_is_refused_with_413(self, client, settings):
        settings.MAX_REQUEST_BODY_BYTES = 1024
        response = client.post(
            "/rag/documents/upload/", data=b"x" * 16,
            content_type="application/octet-stream", CONTENT_LENGTH="2048",
        )
        assert response.status_code == 413

    def test_a_body_at_the_cap_is_allowed_through(self, client, settings):
        settings.MAX_REQUEST_BODY_BYTES = 4096
        response = client.get("/", CONTENT_LENGTH="4096")
        assert response.status_code == 200

    def test_a_get_with_no_content_length_is_untouched(self, client):
        assert client.get("/").status_code == 200

    def test_a_malformed_content_length_is_not_a_500(self, client):
        """Never-500 is a hard rule for every rendered view here, and an
        attacker chooses this header. A malformed declaration falls through
        to Django's own handlers -- for this GET, the ordinary 200."""
        assert client.get("/", CONTENT_LENGTH="not-a-number").status_code == 200

    def test_the_refusal_costs_nothing(self, client, settings, django_assert_num_queries):
        """The finding is resource exhaustion: a refusal that still read
        the body, or hit the database, would be the same DoS with an
        error page on it."""
        settings.MAX_REQUEST_BODY_BYTES = 1
        with django_assert_num_queries(0):
            assert client.post(
                "/rag/documents/upload/", data=b"xx",
                content_type="application/octet-stream", CONTENT_LENGTH="999999",
            ).status_code == 413

    def test_the_middleware_is_installed_before_anything_reads_the_body(self, settings):
        entry = "foundation.uploads.RequestBodyLimitMiddleware"
        assert entry in settings.MIDDLEWARE
        assert settings.MIDDLEWARE.index(entry) < \
               settings.MIDDLEWARE.index("django.middleware.csrf.CsrfViewMiddleware")


class TestDjangoUploadCaps:
    def test_the_file_count_cap_is_set(self, settings):
        """`request.FILES.getlist("files")` was unbounded in count, so N
        files each just under the per-file cap were all accepted. Django
        5.2 already defaults this to 100; stated lower here, explicitly,
        as a fact about this box rather than an inherited default."""
        assert settings.DATA_UPLOAD_MAX_NUMBER_FILES == 50

    def test_the_non_file_post_cap_is_stated_explicitly(self, settings):
        assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == 2 * 1024 * 1024
