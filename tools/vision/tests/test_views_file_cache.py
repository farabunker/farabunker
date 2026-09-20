"""Unit tests for the B-8 cache-header hardening (round-3, cache half) on
`_serve_stored_file` -- shared by `output_file` (`GET /vision/outputs/<id>/
file/`) and `input_file` (`GET /vision/inputs/<id>/file/`).

A NEW module, vision-steward-granted (see the H35 addendum): the ONLY
change under `tools/vision/**` this task may make is one call to
`foundation.http.mark_private` plus its import in `views.py::
_serve_stored_file`, set on the response object before `return` -- the
same placement H23's own `X-Content-Type-Options: nosniff` uses. This
module asserts, on BOTH routes: the two new cache headers, that H23's
nosniff and the forced-attachment-outside-the-raster-set behaviour are
unchanged, and that the missing-file 404's shape (status AND body) is
byte-identical to before -- `mark_private` is set only on
`_serve_stored_file`'s success return, never on the `raise
Http404(...)` branches above it, so the 404 case is a pin, not a
red/green pair.

Both routes are driven off one parametrized `route` fixture factory
(`_ROUTES`) rather than two near-identical classes, so the two can never
silently drift apart in what they're asserted against.
"""
from __future__ import annotations

import os

import pytest
from django.test import Client
from django.urls import reverse

from tools.vision.models import JobInput
from tools.vision.tests._helpers import PNG, clear_bindings, stored_output

_PRIVATE_NO_STORE = "private, no-store, max-age=0"

# pytest-django's own `django_debug_mode` ini setting (unset here, so it
# defaults to `False`) forces `settings.DEBUG = False` for the whole test
# session regardless of the `DEBUG` env var this repo's `.env` would
# otherwise set -- so every `raise Http404(...)` this suite triggers
# renders Django's plain built-in 404 page (there is no `404.html`
# template in this repo to override it), NOT the message string the view
# passed in. That message is still real -- it reaches the request log
# (`django.request` at WARNING) -- but the response BODY a client sees is
# this fixed string no matter which sentence raised it, which is exactly
# what makes it the right byte-identical pin.
_GENERIC_404_BODY = (
    b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Not Found</title>\n'
    b"</head>\n<body>\n  <h1>Not Found</h1><p>The requested resource was not "
    b"found on this server.</p>\n</body>\n</html>\n"
)


def _output_route(tmp_path, media_type="image/png"):
    """A stored `GeneratedOutput`: (file path, its `vision-output-file` URL)."""
    output = stored_output(tmp_path)
    if media_type != "image/png":
        output.media_type = media_type
        output.save(update_fields=["media_type"])
    return output.path, reverse("vision-output-file", args=[output.id])


def _input_route(tmp_path, media_type="image/png"):
    """A stored `JobInput`: (file path, its `vision-input-file` URL)."""
    output = stored_output(tmp_path)
    source = tmp_path / "init_image-beach.png"
    source.write_bytes(PNG)
    job_input = JobInput.objects.create(
        job=output.job, param_key="init_image", path=str(source), media_type=media_type,
    )
    return job_input.path, reverse("vision-input-file", args=[job_input.id])


_ROUTES = {"output": _output_route, "input": _input_route}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["output", "input"])
class TestServedFileAnswersPrivateNoStore:
    def test_carries_cache_control_and_vary_cookie(self, tmp_path, client, route):
        _path, url = _ROUTES[route](tmp_path)

        response = client.get(url)

        assert response.status_code == 200
        assert response["Cache-Control"] == _PRIVATE_NO_STORE
        assert "Cookie" in response["Vary"]

    def test_nosniff_is_unchanged(self, tmp_path, client, route):
        _path, url = _ROUTES[route](tmp_path)

        response = client.get(url)

        assert response["X-Content-Type-Options"] == "nosniff"

    def test_the_forced_attachment_outside_the_raster_set_is_unchanged(self, tmp_path, client, route):
        """B-1's clamp: a non-raster media type is still served as an
        attachment whatever `?download=` says, cache headers or not."""
        _path, url = _ROUTES[route](tmp_path, media_type="image/svg+xml")

        response = client.get(url)

        assert response["Content-Type"] == "application/octet-stream"
        assert response["Content-Disposition"].startswith("attachment")
        assert response["Cache-Control"] == _PRIVATE_NO_STORE

    def test_the_missing_file_404_shape_is_unchanged(self, tmp_path, client, route):
        path, url = _ROUTES[route](tmp_path)
        os.remove(path)

        response = client.get(url)

        assert response.status_code == 404
        assert response.content == _GENERIC_404_BODY

    def test_the_missing_file_404_carries_no_cache_headers(self, tmp_path, client, route):
        """The other half of the pin above, on the headers rather than the
        body. `mark_private` is set on `_serve_stored_file`'s SUCCESS
        return only, never on the `raise Http404(...)` branches before it
        -- a 404 that started answering `private, no-store`, or that grew
        a `Vary: Cookie`, would mean the marker had drifted onto a path
        that serves no file at all. Nothing asked for that; this is where
        it would show up."""
        path, url = _ROUTES[route](tmp_path)
        os.remove(path)

        response = client.get(url)

        assert response.status_code == 404
        assert not response.has_header("Cache-Control")
        assert "Cookie" not in response.get("Vary", "")
