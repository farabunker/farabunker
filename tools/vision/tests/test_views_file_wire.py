"""B-1's wire regression, end to end through the real routes.

`tools/vision/tests/test_views_gallery.py` already pins B-1's fix at the
PRIVATE clamp function -- `test_an_svg_media_type_is_never_named_in_a_
content_type_header` calls `views._serve_stored_file` directly. That call
bypasses `input_file`/`output_file` entirely, so it never proved the route
plumbing (the URL, the row lookup, the response headers a real client
receives) reaches the same clamp. The audit's own probe (report §9) drove a
`.svg` upload through `POST /vision/generate/` with the multipart part's
own `Content-Type` forced to `image/svg+xml` and read `Content-Type:
image/svg+xml` straight back off `GET /vision/inputs/<id>/file/`. Nothing
in the suite pinned that PAIR -- store-time derivation
(`store.media_type_for_upload`) and serve-time clamp
(`views._serve_stored_file`), through the real route -- which is the thing
that can silently regress if either half drifts independently. This module
is that pin, plus the two additions the vision steward's review of the
fix (`7351807`) asked for: a response-local `X-Content-Type-Options:
nosniff`, and a stored row that actually carries a media-type PARAMETER
(`"image/svg+xml; charset=utf-8"`, exactly what a client-set multipart
header produces) rather than only the bare type.

The test database's posture defaults to `IdentitySettings.posture`'s own
field default, `POSTURE_OPEN` -- under which `is_admin`/`sees_all_content`
answer True for every principal, anonymous included. Neither `input_file`
nor `output_file` needs a signed-in principal to serve a job under that
posture, and this module's routes never touch anything posture-sensitive
beyond ordinary job-read visibility -- exactly the assumption
`test_views_gallery.py`'s own `TestInputFile`/`TestOutputFile` classes
already make, with no `sign_in` call anywhere in either. This module
follows the same convention rather than inventing a second one.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from tools.vision import store
from tools.vision.models import JobInput
from tools.vision.tests._helpers import PNG, stored_output


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
class TestB1TheServedMediaTypeIsNeverTheClients:
    """Both halves of the fix are already in the tree (`7351807`); this
    class pins the PAIR, which is the thing that can regress."""

    def test_an_svg_uploaded_as_image_svg_xml_is_stored_with_no_media_type(self):
        uploaded = SimpleUploadedFile(
            "payload.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>',
            content_type="image/svg+xml",
        )
        assert store.media_type_for_upload(uploaded) == ""

    def test_the_input_file_route_serves_it_as_an_octet_stream_attachment(self, client, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "payload.svg"
        source.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>')
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source),
            media_type="image/svg+xml",
        )

        response = client.get(reverse("vision-input-file", args=[job_input.id]))

        assert response["Content-Type"] == "application/octet-stream"
        assert response["Content-Disposition"].startswith("attachment")

    def test_a_real_raster_type_still_renders_inline(self, client, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "ok.png"
        source.write_bytes(PNG)
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png",
        )

        response = client.get(reverse("vision-input-file", args=[job_input.id]))

        assert response["Content-Type"] == "image/png"
        assert response["Content-Disposition"].startswith("inline")

    def test_the_output_route_clamps_identically(self, client, tmp_path):
        """The engine adapter guesses an output's media type from the
        filename (`models/contracts/engines/comfyui.py::_media_type`), so
        an engine answering with an `.svg` filename reaches the same
        clamp through the OUTPUT route, not only the input route."""
        output = stored_output(tmp_path)
        output.media_type = "image/svg+xml"
        output.save(update_fields=["media_type"])

        response = client.get(reverse("vision-output-file", args=[output.id]))

        assert response["Content-Type"] == "application/octet-stream"

    def test_a_stored_parametered_media_type_is_clamped_end_to_end(self, client, tmp_path):
        """Steward addition (b). The clamp lowercases and splits on ';'
        (`views.py`), but nothing tested a row that actually CARRIES a
        parameter, through the real route rather than the private
        function -- exactly what a client that sets its own multipart
        header produces."""
        output = stored_output(tmp_path)
        source = tmp_path / "p.svg"
        source.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source),
            media_type="image/svg+xml; charset=utf-8",
        )

        response = client.get(reverse("vision-input-file", args=[job_input.id]))

        assert response["Content-Type"] == "application/octet-stream"
        assert response["Content-Disposition"].startswith("attachment")
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_the_response_carries_nosniff_of_its_own(self, client, tmp_path):
        """Steward addition (a). The site-wide middleware sets it today;
        setting it on this response makes the guarantee local to the
        route that needs it, so a middleware change cannot quietly
        remove it from the one place it is load-bearing."""
        output = stored_output(tmp_path)
        source = tmp_path / "ok.png"
        source.write_bytes(PNG)
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png",
        )

        response = client.get(reverse("vision-input-file", args=[job_input.id]))

        assert response["X-Content-Type-Options"] == "nosniff"
