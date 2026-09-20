"""Unit tests for foundation/http.py's `mark_private` (B-8, cache half,
round-3 hardening).

Needs a configured Django project (an `HttpResponse` instance, and
`django.utils.cache.patch_vary_headers`) but no database -- collected the
same way as any other column's tests: `foundation` is one of `pytest.ini`'s
`testpaths` entries (`tools models foundation agents scripts`), so the bare
`pytest -q` and the reversed `pytest -q scripts agents foundation models
tools` both sweep these tests in like any other, with no special-casing
needed.
"""
from __future__ import annotations

from django.http import HttpResponse

from foundation.http import mark_private


class TestMarkPrivate:
    def test_sets_cache_control(self):
        response = mark_private(HttpResponse())
        assert response["Cache-Control"] == "private, no-store, max-age=0"

    def test_adds_cookie_to_vary(self):
        response = mark_private(HttpResponse())
        assert "Cookie" in response["Vary"]

    def test_returns_the_response_for_chaining(self):
        response = HttpResponse()
        assert mark_private(response) is response

    def test_vary_is_patched_additively_not_assigned_wholesale(self):
        """A view that already set its own `Vary` (for its own reasons)
        keeps what it set -- `mark_private` only ever ADDS `Cookie`, via
        `django.utils.cache.patch_vary_headers`, never replaces the
        header outright."""
        response = HttpResponse()
        response["Vary"] = "Accept-Encoding"
        mark_private(response)
        vary_values = {v.strip() for v in response["Vary"].split(",")}
        assert vary_values == {"Accept-Encoding", "Cookie"}

    def test_idempotent(self):
        """Calling it twice leaves `Cookie` listed exactly once and
        `Cache-Control` unchanged."""
        response = HttpResponse()
        mark_private(response)
        mark_private(response)
        assert response["Cache-Control"] == "private, no-store, max-age=0"
        vary_values = [v.strip() for v in response["Vary"].split(",")]
        assert vary_values.count("Cookie") == 1
