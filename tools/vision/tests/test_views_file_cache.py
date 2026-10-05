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

from identity.contracts.actions import SOURCE_WEB
from identity.contracts.postures import POSTURE_OPEN, POSTURE_PERSONAL
from identity.contracts.retention import KIND_VISION_JOB
from identity.retention import delete_content, restore_content
from tools.vision.models import JobInput
from tools.vision.tests._helpers import (
    PNG, clear_bindings, make_user, posture, sign_in, stored_output, user_principal,
)

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


def _owned_output_route(tmp_path, owner):
    """An owned `GeneratedOutput`, a real file on disk: (job, its
    `vision-output-file` URL)."""
    output = stored_output(tmp_path)
    output.job.owner_kind = "user"
    output.job.owner_key = str(owner.pk)
    output.job.save(update_fields=["owner_kind", "owner_key"])
    return output.job, reverse("vision-output-file", args=[output.id])


def _owned_input_route(tmp_path, owner):
    """An owned `JobInput`, a real file on disk: (job, its
    `vision-input-file` URL)."""
    output = stored_output(tmp_path)
    output.job.owner_kind = "user"
    output.job.owner_key = str(owner.pk)
    output.job.save(update_fields=["owner_kind", "owner_key"])
    source = tmp_path / "init_image-beach.png"
    source.write_bytes(PNG)
    job_input = JobInput.objects.create(
        job=output.job, param_key="init_image", path=str(source), media_type="image/png",
    )
    return output.job, reverse("vision-input-file", args=[job_input.id])


_OWNED_ROUTES = {"output": _owned_output_route, "input": _owned_input_route}


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["output", "input"])
class TestATicketedJobsFileIsNotFetchableByItsDirectURL:
    """`output_file` and `input_file` resolve their owner check through
    `may_read_job`, on the row they already loaded by primary key --
    never through `visible_jobs`, which is what excludes a ticketed job
    from the gallery and the create page's Recent list. Without a
    matching check inside `may_read_job` itself, a deleted image would
    stay fetchable forever by anybody who already had its direct URL."""

    def test_the_owner_is_refused_after_delete_and_served_again_after_restore(
        self, tmp_path, client, route,
    ):
        owner = make_user()
        job, url = _OWNED_ROUTES[route](tmp_path, owner)
        with posture(POSTURE_PERSONAL):
            sign_in(client, owner)
            assert client.get(url).status_code == 200

            ticket = delete_content(
                user_principal(owner), kind=KIND_VISION_JOB, key=str(job.pk),
                owner=job, source=SOURCE_WEB,
            )
            assert client.get(url).status_code == 404

            restore_content(user_principal(owner), ticket, source=SOURCE_WEB)
            assert client.get(url).status_code == 200

    def test_another_principal_on_an_open_box_is_refused_too(
        self, tmp_path, client, route,
    ):
        """`sees_all_content` answers True for every principal on an
        open box -- the posture most boxes run -- so this is the case
        that proves the ticket check runs BEFORE that branch, not only
        on the restricted leg: a check bolted onto the owner-only leg
        alone would leave the file servable exactly here."""
        owner, another = make_user(), make_user()
        job, url = _OWNED_ROUTES[route](tmp_path, owner)
        with posture(POSTURE_PERSONAL):
            delete_content(
                user_principal(owner), kind=KIND_VISION_JOB, key=str(job.pk),
                owner=job, source=SOURCE_WEB,
            )
        with posture(POSTURE_OPEN):
            sign_in(client, another)
            assert client.get(url).status_code == 404

    def test_an_unticketed_jobs_file_still_serves(self, tmp_path, client, route):
        owner = make_user()
        _job, url = _OWNED_ROUTES[route](tmp_path, owner)
        with posture(POSTURE_PERSONAL):
            sign_in(client, owner)
            assert client.get(url).status_code == 200
