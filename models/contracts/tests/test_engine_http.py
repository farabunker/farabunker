"""
Unit tests for `models/contracts/engines/engine_http.py` (S11, S25).

Pure, DB-free, HTTP-free: `httpx.MockTransport` answers every request from
memory, so these tests exercise the real `httpx` streaming API (the property
under test -- that a body is read incrementally and abandoned mid-stream once
it exceeds budget) without a socket or a live server. `pytest-httpx` is not
installed in this repo and this module does not add it.
"""
from __future__ import annotations

import httpx
import pytest

from models.contracts.engines import engine_http
from models.contracts.engines.whisper import WhisperEngine


def _client_returning(body: bytes, status: int = 200) -> httpx.Client:
    """A real `httpx.Client` whose transport answers from memory -- no
    socket, no server, and the streaming API behaves exactly as it does
    against a live one, which is the property under test."""
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)))


class _ChunkStream(httpx.SyncByteStream):
    """A byte stream that yields `chunks` one at a time and raises the
    moment it is pulled more than `max_pulls` times (H11 review round 1,
    finding 5) -- the byte-by-byte proof that `get_bounded` abandons an
    over-budget (or truncated) read MID-STREAM rather than reading the
    whole body and only then measuring or trimming it. `httpx.Response(...,
    content=...)` (every other test in this module) hands `iter_bytes()`
    the whole body as one chunk, which cannot distinguish "stopped early"
    from "read everything, checked the length after" -- this can."""

    def __init__(self, chunks: list[bytes], *, max_pulls: int) -> None:
        self._chunks = chunks
        self._max_pulls = max_pulls
        self.pulls = 0

    def __iter__(self):
        for chunk in self._chunks:
            self.pulls += 1
            if self.pulls > self._max_pulls:
                raise AssertionError(
                    f"get_bounded pulled a {self.pulls}th chunk -- it must have stopped by "
                    f"chunk {self._max_pulls}"
                )
            yield chunk

    def close(self) -> None:
        pass


def _client_streaming(chunks: list[bytes], *, max_pulls: int) -> tuple[httpx.Client, "_ChunkStream"]:
    """A real `httpx.Client` whose transport answers with `_ChunkStream`,
    so `get_bounded`'s `iter_bytes()` loop pulls real, separate chunks one
    at a time instead of one pre-joined blob."""
    stream = _ChunkStream(chunks, max_pulls=max_pulls)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    return client, stream


class TestBoundedRead:
    def test_a_body_under_the_budget_is_returned_whole(self):
        with _client_returning(b"x" * 100) as client:
            assert (
                engine_http.get_bounded("http://e/view", timeout=5, max_bytes=1000, client=client)
                == b"x" * 100
            )

    def test_a_body_at_the_budget_is_returned_whole(self):
        with _client_returning(b"x" * 1000) as client:
            assert (
                len(engine_http.get_bounded("http://e/view", timeout=5, max_bytes=1000, client=client))
                == 1000
            )

    def test_an_over_budget_body_raises_an_http_error(self):
        """`httpx.HTTPError`, not a new exception type: every adapter
        already wraps these calls in `except httpx.HTTPError`, so a
        hostile engine reads as an unreachable one rather than as a 500."""
        with _client_returning(b"x" * 5000) as client:
            with pytest.raises(httpx.HTTPError):
                engine_http.get_bounded("http://e/view", timeout=5, max_bytes=1000, client=client)

    def test_the_refusal_names_the_budget_and_the_host(self):
        """The finding is memory: the read must stop at the budget rather
        than materialise the body and then complain about its size. The
        message naming the budget and the endpoint is the observable proxy
        an operator reading a log actually gets; the byte-by-byte proof is
        the `iter_bytes` accumulation loop in `get_bounded` itself, which
        raises as soon as the running total passes `max_bytes` rather than
        after the whole body is read."""
        with _client_returning(b"x" * 10_000) as client:
            with pytest.raises(httpx.HTTPError) as raised:
                engine_http.get_bounded(
                    "http://hostile-engine.example/view", timeout=5, max_bytes=100, client=client
                )
        message = str(raised.value)
        assert "100" in message
        assert "hostile-engine.example" in message

    def test_a_non_200_still_raises_for_status(self):
        with _client_returning(b"", status=500) as client:
            with pytest.raises(httpx.HTTPError):
                engine_http.get_bounded("http://e/view", timeout=5, client=client)

    def test_the_over_budget_error_is_not_a_value_error(self):
        """H11 part 2, invariant 4: `tools.vision.services.submit_job`
        classifies a bare `ValueError` as `GenerationJob.FailureKind.
        ENGINE_REJECTED` (a routine, ex-ante-knowable condition) and
        everything else as `ENGINE_FAILED`. A truncated/over-budget engine
        response is an engine FAULT, never a routine rejection, so the
        exception `get_bounded` raises for it must never ALSO be a
        `ValueError` -- if it were, it could one day be caught by that
        `ValueError` clause and misclassified. `httpx.ReadError`'s own MRO
        (`ReadError` -> `NetworkError` -> `TransportError` -> `RequestError`
        -> `HTTPError` -> `Exception`) never touches `ValueError`; this
        pins that fact so a future `httpx` upgrade or refactor cannot
        change it silently."""
        with _client_returning(b"x" * 5000) as client:
            with pytest.raises(httpx.HTTPError) as raised:
                engine_http.get_bounded("http://e/view", timeout=5, max_bytes=1000, client=client)
        assert isinstance(raised.value, httpx.HTTPError)
        assert not isinstance(raised.value, ValueError)

    def test_a_non_200_status_error_is_also_not_a_value_error(self):
        """Same pin as above, for the OTHER exception family `get_bounded`
        can raise (`raise_for_status()`'s `httpx.HTTPStatusError`) -- both
        of `get_bounded`'s failure modes must clear the same bar."""
        with _client_returning(b"", status=500) as client:
            with pytest.raises(httpx.HTTPError) as raised:
                engine_http.get_bounded("http://e/view", timeout=5, client=client)
        assert not isinstance(raised.value, ValueError)

    def test_the_read_stops_at_the_first_over_budget_chunk(self):
        """The byte-by-byte proof promised above: a 10-chunk, 1000-byte
        body over a 250-byte budget must never be pulled past its 3rd
        chunk (100 + 100 + 100 = 300, the first running total to exceed
        250) -- `_ChunkStream` raises if `get_bounded` asks for a 4th."""
        client, stream = _client_streaming([b"x" * 100] * 10, max_pulls=3)
        with client:
            with pytest.raises(httpx.HTTPError):
                engine_http.get_bounded("http://e/view", timeout=5, max_bytes=250, client=client)
        assert stream.pulls == 3


class TestTruncateMode:
    """`truncate=True` (H11 review round 1, finding 5): the health-probe
    shape -- return the first `max_bytes` instead of raising, so a status
    page longer than the marker-search window is still sniffed rather than
    refused outright."""

    def test_an_under_budget_body_is_returned_whole(self):
        """Truncate mode changes nothing for a body that never hits the
        budget -- same result as the default (raising) mode would give."""
        with _client_returning(b"x" * 100) as client:
            result = engine_http.get_bounded(
                "http://e/view", timeout=5, max_bytes=1000, client=client, truncate=True
            )
        assert result == b"x" * 100

    def test_an_over_budget_body_is_truncated_not_raised(self):
        with _client_returning(b"x" * 10_000) as client:
            result = engine_http.get_bounded(
                "http://e/view", timeout=5, max_bytes=100, client=client, truncate=True
            )
        assert result == b"x" * 100

    def test_a_non_200_status_still_raises_even_in_truncate_mode(self):
        """Truncate mode only changes the OVER-BUDGET behaviour -- a bad
        status is a transport-level fact `raise_for_status()` still
        reports, in either mode."""
        with _client_returning(b"", status=500) as client:
            with pytest.raises(httpx.HTTPError):
                engine_http.get_bounded("http://e/view", timeout=5, client=client, truncate=True)

    def test_the_read_stops_at_the_budget_without_pulling_further_chunks(self):
        """The chunked-stream proof for truncate mode: it must abandon the
        stream at the same point the raising mode does (chunk 3 of 10 for
        a 250-byte budget over 100-byte chunks), not read the whole body
        and trim it afterwards."""
        client, stream = _client_streaming([b"x" * 100] * 10, max_pulls=3)
        with client:
            result = engine_http.get_bounded(
                "http://e/view", timeout=5, max_bytes=250, client=client, truncate=True
            )
        assert len(result) == 250
        assert stream.pulls == 3


class TestEngineRef:
    @pytest.mark.parametrize("value", ["abc123", "a-b_c", "0"])
    def test_a_well_formed_prompt_id_passes_through(self, value):
        assert engine_http.safe_engine_ref(value) == value

    @pytest.mark.parametrize("value", ["../queue", "a?b", "a#b", "a/b", "", "a b"])
    def test_a_shaped_prompt_id_is_refused(self, value):
        with pytest.raises(ValueError):
            engine_http.safe_engine_ref(value)


class TestWhisperHealthIsBounded:
    def test_the_health_probe_reads_only_what_it_checks(self, monkeypatch):
        """It materialised the WHOLE body and only then sliced to the
        marker window, which is the finding exactly: `is_healthy` must ask
        `get_bounded` for at most `HEALTH_MARKER_SEARCH_BYTES`, never the
        default (image-sized) budget."""
        seen = {}

        def _fake(url, *, timeout, params=None, max_bytes=None, client=None, truncate=False):
            seen["max_bytes"] = max_bytes
            seen["truncate"] = truncate
            return b"whisper"

        monkeypatch.setattr("models.contracts.engines.whisper.get_bounded", _fake)
        assert WhisperEngine().is_healthy("http://e") is True
        assert seen["max_bytes"] == engine_http.HEALTH_MARKER_SEARCH_BYTES

    def test_the_health_probe_uses_truncate_mode(self, monkeypatch):
        """Review round 1, finding 5: a status page LONGER than
        `HEALTH_MARKER_SEARCH_BYTES` must still be sniffed, not refused --
        `is_healthy` must ask `get_bounded` for `truncate=True`, never the
        default (raising) mode a DATA read needs."""
        seen = {}

        def _fake(url, *, timeout, params=None, max_bytes=None, client=None, truncate=False):
            seen["truncate"] = truncate
            return b"whisper"

        monkeypatch.setattr("models.contracts.engines.whisper.get_bounded", _fake)
        assert WhisperEngine().is_healthy("http://e") is True
        assert seen["truncate"] is True
