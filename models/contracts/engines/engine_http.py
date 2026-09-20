"""
Bounded reads for inference-engine HTTP responses (S11, S25).

Every adapter in this package speaks HTTP to a process the platform does not
control: ComfyUI, whisper-server, Ollama. Before this module existed, two of
those calls (`comfyui.ComfyUIGenerator.fetch_outputs`'s `/view` read and
`whisper.WhisperEngine.is_healthy`'s body read) materialised the ENTIRE
response body in memory before doing anything with it -- a generated image's
worth of bytes in one case, a health-check page's worth in the other, with no
upper bound on either. A compromised or merely hostile engine (or an SSRF
target reached via S5's endpoint override) can hold the connection open and
stream for the full request timeout -- up to `DEFAULT_REQUEST_TIMEOUT`
(300s) -- turning one request into gigabytes on a box whose unified memory is
already the binding constraint, inside the queue worker process.

`get_bounded` reads with `httpx.stream(...)`, counting bytes as they arrive
and stopping the moment the running total passes `max_bytes`, so the worst a
hostile engine can do is spend `max_bytes` of memory, never more. By default
it then raises `httpx.HTTPError` -- not a new exception type -- because every
existing `except httpx.HTTPError` around an adapter call already treats the
engine as unreachable; a hostile stream should read exactly like a dead
server, not like a 500. `truncate=True` changes only what happens at that
point: it returns the first `max_bytes` read instead of raising, for the one
kind of caller that wants to look at a prefix of a body it doesn't control
the length of (a health-probe marker sniff) rather than needing the whole
thing to be meaningful.

This module also holds `safe_engine_ref` (S25): `comfyui.py`'s `_history`
interpolates an engine-supplied `prompt_id` straight into a request path
(`f"{self.endpoint}/history/{engine_ref}"`) with no shape check. A malicious
engine that returns a `prompt_id` containing `../`, `?`, or `#` can retarget
that poll at a different path on the SAME engine (there is no redirect
following and the netloc is fixed, so this cannot reach a different host --
but it can still read whatever else the engine exposes). `safe_engine_ref`
refuses anything that is not a bare token before it ever reaches an f-string.

Named `engine_http.py`, not `http.py`: a module named `http` inside a
package that `httpx` itself imports from would shadow the standard-library
`http` package for anything below it on `sys.path` -- a footgun with no
upside.
"""
from __future__ import annotations

import re

import httpx

# A generated image is single-digit megabytes; a transcription health page
# is a few kilobytes. 256 MiB is two orders of magnitude of headroom over
# the largest legitimate body any adapter call in this package expects, and
# still three orders of magnitude below what a hostile stream could send
# across the full 300-second request timeout if nothing stopped it early.
MAX_RESPONSE_BYTES = 256 * 1024 * 1024

# How much of a health-probe response body is worth reading at all: a
# health check exists to answer one yes/no question ("does this look like
# the engine we expect"), never to hold the whole page in memory. Moved
# here from `models/contracts/engines/whisper.py` (it lived beside
# `_HEALTH_MARKER` as `_HEALTH_MARKER_SEARCH_BYTES`) so `comfyui.py` and any
# future adapter share the one constant instead of each adapter defining
# its own 4096 under a different name.
HEALTH_MARKER_SEARCH_BYTES = 4096

# An engine-supplied identifier (ComfyUI's `prompt_id`) that is about to be
# interpolated into a request path must be a bare token: letters, digits,
# `-` and `_` only, 1-128 characters. No `/`, `?`, `#`, or whitespace --
# nothing that could change which path segment the request actually hits.
ENGINE_REF_RE = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def get_bounded(
    url: str,
    *,
    timeout: float,
    params: dict | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    client: httpx.Client | None = None,
    truncate: bool = False,
) -> bytes:
    """`GET url`, never holding more than `max_bytes` of the response body
    in memory at once.

    Raises for a non-2xx status the same way `response.raise_for_status()`
    does (an `httpx.HTTPStatusError`, itself an `httpx.HTTPError`).

    The default, `truncate=False`, is for DATA reads -- a caller that needs
    the WHOLE body to mean anything (an image, a JSON document): the
    moment the running total of bytes read passes `max_bytes`, this raises
    `httpx.ReadError` (also an `httpx.HTTPError`) before the rest of the
    body is ever read off the socket, so callers that already wrap adapter
    calls in `except httpx.HTTPError` need no changes -- a hostile or
    over-budget engine reads exactly like an unreachable one. A body
    silently truncated here would be a worse bug than a refusal: a
    truncated image or a truncated JSON document is not a smaller version
    of the real thing, it is a corrupt one.

    `truncate=True` is for PROBE reads -- a caller that only wants to LOOK
    at the first `max_bytes` of a body whose real length it doesn't
    control and doesn't care about (a health-probe marker sniff): instead
    of raising, the read stops the moment `max_bytes` is reached and
    returns exactly that many bytes, no more, no exception. A status page
    that happens to run long must still be sniffed, not refused -- refusing
    would turn "the engine's status page grew a few bytes" into "this
    engine looks unreachable", which is a worse failure mode than reading
    less of a body nobody was going to read in full anyway.

    Either mode still stops reading at the first byte over the budget --
    the difference is only what happens next (raise vs. return what was
    read), never how much of an over-budget body is pulled off the wire.

    `client` is the test seam. `None` (every real caller) opens a one-shot
    `httpx.Client` for this call and closes it before returning; a caller
    may pass one built on `httpx.MockTransport` to answer from memory with
    no socket and no server -- `httpx.stream(...)` as a bare module-level
    call has no other way to be stubbed that isn't monkeypatching this
    module itself, which would test less than the real streaming path.
    """
    owns_client = client is None
    active_client = httpx.Client() if owns_client else client
    try:
        with active_client.stream("GET", url, params=params, timeout=timeout) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    if truncate:
                        # Trim the overshoot so the return value is capped
                        # at EXACTLY max_bytes, then stop reading -- the
                        # rest of the body is abandoned, same as the
                        # raising branch, just without raising.
                        overshoot = total - max_bytes
                        chunks.append(chunk[: len(chunk) - overshoot])
                        break
                    raise httpx.ReadError(
                        f"engine response from {url!r} exceeded the {max_bytes}-byte budget",
                        request=response.request,
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    finally:
        if owns_client:
            active_client.close()


def safe_engine_ref(value: str) -> str:
    """`value`, unchanged, if it is safe to interpolate into a request path
    (a bare token: letters, digits, `-`, `_`, 1-128 characters). Raises
    `ValueError` -- an honest refusal, not a silent skip -- for anything
    else, including the empty string, whitespace, and any of `/`, `?`, `#`.

    Call this on every engine-supplied identifier BEFORE it reaches an
    f-string that builds a request path (S25): an engine that returns
    `../queue` or `a?b` as its own job id must not be able to redirect the
    next poll at a different path on itself.
    """
    if not ENGINE_REF_RE.match(value):
        raise ValueError(f"refusing to use {value!r} as an engine reference: not a bare token")
    return value
