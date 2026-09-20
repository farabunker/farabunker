"""One request-level bound on how many bytes a caller may send.

S9: Django has NO built-in cap on total FILE upload size --
`DATA_UPLOAD_MAX_MEMORY_SIZE` covers non-file POST data only -- and the
cap this platform does have (`RagSettings.max_upload_bytes`) is PER FILE
and is checked in `stage_and_enqueue_one` AFTER the upload handler has
already spilled the bytes to `FILE_UPLOAD_TEMP_DIR`. That directory is
`DATA_DIR/tmp`: the same host-mounted volume as the document store and
the Postgres data directory's sibling. Filling it takes the box down, not
just the upload. There is no reverse proxy in front to apply a
`client_max_body_size` -- `deploy/` is a README.

WHAT THIS DOES NOT CLOSE, said plainly rather than left to be assumed: a
caller who LIES in `Content-Length`, or sends a chunked body with none,
is not caught here. Enforcing the real byte count needs the server or a
proxy to do it while reading, and this box has no reverse proxy in front
of it today (`deploy/` is a README). This is the cheap first cut that
costs a legitimate upload nothing; `docs/OPERATIONS.md` §"The
request-body cap" says what the complete answer (a proxy) would need to
do.

`foundation/`, because it is the base layer every column may import and
which imports nothing of the project's. It deliberately does NOT read
`RagSettings.max_upload_bytes` -- `foundation` may not import `tools.rag`
(import-law rule 2), and a request-body bound is a deployment fact rather
than a library policy in any case.
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse


class RequestBodyLimitMiddleware:
    """Refuse a request whose declared `Content-Length` exceeds
    `settings.MAX_REQUEST_BODY_BYTES`, before anything reads the body.

    PLACED BEFORE `CsrfViewMiddleware` in `MIDDLEWARE`, which is what
    makes "before anything reads the body" true: CSRF reads POST data for
    a form-encoded request, and reading POST data is what triggers the
    upload handlers.

    `413`, with a plain-text body. Not a rendered page: a caller sending
    four gigabytes is not reading prose, and rendering a template here
    would spend a template load on every refusal.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        declared = request.META.get("CONTENT_LENGTH") or ""
        try:
            length = int(declared)
        except (TypeError, ValueError):
            # An attacker chooses this header. A malformed value is not a
            # 500; the request simply carries no usable declaration and
            # falls through to the handlers' own limits.
            length = 0
        if length > settings.MAX_REQUEST_BODY_BYTES:
            return HttpResponse(
                "The request body is larger than this box accepts.\n",
                status=413, content_type="text/plain; charset=utf-8",
            )
        return self.get_response(request)
