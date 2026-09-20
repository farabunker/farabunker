"""S5/S19: what the console's `?endpoint=` override will and will not
probe.

A NEW MODULE rather than an addition to one of the six
`test_views_*.py` modules the registry split produced (Global
Constraint 18).

`_requested_override` (S5) gates the console's `?endpoint=`/
`endpoint_override` override on TWO independent things: the caller must
be an admin (closes a signed-in non-admin in `personal`/`enterprise`),
and the requested host must be on the allowlist `_override_is_permitted`
computes (closes the drive-by GET case on every posture, including the
default `open` one, where `is_admin` answers True for everybody).
`_endpoint_is_well_formed` (S19) is the syntax half of that allowlist,
reused unchanged by `connection_add` and `machine_model_add` to validate
a STORED `ModelConnection.endpoint` -- a different policy question over
the same syntax.

THE BINDING INVARIANT: the override is introduced by a POST (the scan
form's hidden `endpoint_override` field, or role_assign/connection_add/
machine_model_add's own), and from then on carried by the redirect-to-GET
that follows every POST here (`_redirect_console` re-appends
`?endpoint=<override>`) -- a GET is therefore the only thing `ConsoleView`
itself (a bare `TemplateView`, GET-only) ever sees. That GET is honoured
ONLY when both halves hold: an admin caller AND an allowlisted host. A GET
carrying a non-allowlisted endpoint, or one from a non-admin, is ignored
with no probe fan-out -- `TestOverrideIsAdminOnly.
test_an_administrator_still_gets_no_override_for_a_disallowed_host` and
`TestNoProbeFromADriveByGet` below pin this directly.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from identity.contracts.postures import POSTURE_ENTERPRISE
from models.registry import views
from models.registry.models import ModelConnection
from models.registry.tests._helpers import (
    client,  # noqa: F401 -- requested by name as a fixture
    clean_probe_cache,
    clear_seeded_rows,
    make_admin,
    make_chat_connection,
    make_user,
    posture,
)


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Clear the ModelConnection/RoleBinding rows migration 0002 seeds from
    env settings, so each test starts from a known-empty registry (same
    autouse shape every `test_views_*.py` module in this package uses)."""
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """See `test_views_console_and_roles.py`'s fixture of the same name:
    `probe_cache` (C-07) has no per-test isolation of its own."""
    clean_probe_cache()
    yield
    clean_probe_cache()


@pytest.fixture
def an_admin_user(db):
    return make_admin()


@pytest.fixture
def a_member_user(db):
    return make_user()


@pytest.fixture
def a_connection_at(db):
    """Register a chat connection at `endpoint` and return it."""

    def _make(endpoint: str) -> ModelConnection:
        return make_chat_connection(endpoint=endpoint)

    return _make


def _attach_user(request, user) -> None:
    request.user = user


def _attach_anonymous(request) -> None:
    request.user = AnonymousUser()


class TestOverrideAllowlist:
    @pytest.mark.parametrize("raw", [
        "http://127.0.0.1:11434",
        "http://localhost:8188",
        "http://[::1]:8080",
        "http://127.5.5.5:9999",  # anywhere in 127.0.0.0/8, not just .1
    ])
    def test_a_loopback_host_is_permitted(self, raw):
        assert views._override_is_permitted(raw) is True

    @pytest.mark.parametrize("raw", [
        "http://example.com/",
        "http://8.8.8.8:53",
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "https://attacker.example:443",
        "ftp://127.0.0.1:21",
        "file:///etc/passwd",
        "http://",
        "",
        # REVIEW ROUND 1 (orchestrator ruling): private-but-non-loopback
        # and the `host.docker.internal` special case are BOTH dropped --
        # neither is loopback, and none is registered here, so all four
        # are refused exactly like a public address. `10.0.0.5:11434` is
        # this suite's version of S5's own drive-by example
        # (`http://10.0.0.5:22`, pinned end-to-end below).
        "http://host.docker.internal:11434",
        "http://10.0.0.5:11434",
        "http://192.168.1.50:11434",
        "http://172.16.4.4:11434",
    ])
    def test_everything_else_is_refused(self, raw, db):
        assert views._override_is_permitted(raw) is False

    def test_a_registered_connection_host_is_permitted(self, db, a_connection_at):
        """The escape hatch for an engine on a routable OR private-LAN
        name: register it (which an operator must do anyway before a
        role can bind to it) and its exact scheme+host+port is permitted
        from then on. This is why no env allowlist setting exists, and
        why `host.docker.internal` needs no hardcoded special case either
        -- an operator who actually points an engine there sets it as
        `OLLAMA_BASE_URL`/etc., which lands in
        `INFERENCE_DEFAULT_ENDPOINTS` and is covered the same way."""
        a_connection_at("http://gpu-box.example:11434")
        assert views._override_is_permitted("http://gpu-box.example:11434") is True

    def test_a_registered_connection_does_not_permit_a_different_scheme_or_port(
        self, db, a_connection_at
    ):
        """Full-triple comparison (review round 1 ruling): a registered
        `http://gpu-box.example:11434` must not accidentally permit a
        DIFFERENT port at the same host -- a host-string-only comparison
        would let `https://gpu-box.example:22` (ssh, wrong scheme too)
        through just because the hostname matches something registered."""
        a_connection_at("http://gpu-box.example:11434")
        assert views._override_is_permitted("https://gpu-box.example:22") is False
        assert views._override_is_permitted("http://gpu-box.example:22") is False
        assert views._override_is_permitted("https://gpu-box.example:11434") is False

    def test_a_configured_default_endpoint_host_is_permitted(self, db, settings):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {"ollama": "http://engines.example:11434"}
        assert views._override_is_permitted("http://engines.example:11434") is True

    def test_default_port_is_normalised_so_an_implicit_and_explicit_port_match(
        self, db, settings
    ):
        """`http://host` and `http://host:80` must compare equal -- an
        exact-string port comparison would treat the implicit default as
        a different endpoint than the same address spelled with it
        explicit."""
        settings.INFERENCE_DEFAULT_ENDPOINTS = {"ollama": "http://engines.example"}
        assert views._override_is_permitted("http://engines.example:80") is True

    def test_a_malformed_stored_endpoint_is_skipped_not_a_500(self, db, a_connection_at):
        """A `ModelConnection.endpoint` may predate this task's own S19
        validation, or be edited by hand -- the known-set comprehension
        must not let a bad stored value raise past it (review round 1:
        `_endpoint_triple` reuses `_endpoint_is_well_formed`, the one
        parser, for exactly this reason). `"http://host:port"` is chosen
        deliberately over a merely-unparseable string: its non-numeric
        port makes `urlsplit(...).port` itself raise `ValueError`, the
        exact exception `_endpoint_triple` catches -- a value that failed
        only the scheme/netloc check would never exercise that except
        clause at all."""
        a_connection_at("http://host:port")
        assert views._override_is_permitted("http://127.0.0.1:11434") is True  # loopback, unaffected
        assert views._override_is_permitted("http://10.0.0.5:11434") is False  # still refused, no 500

    @pytest.mark.parametrize("raw", ["http://[::1", "http://[gg::1]:80"])
    def test_a_malformed_ipv6_literal_is_refused_not_a_500(self, raw):
        """`urlsplit(...).hostname` RAISES ValueError for these, and an
        attacker chooses this string."""
        assert views._override_is_permitted(raw) is False


class TestOverrideIsAdminOnly:
    def test_an_anonymous_caller_in_an_accounts_on_posture_gets_no_override(self, rf, db):
        with posture(POSTURE_ENTERPRISE):
            request = rf.get(reverse("inference-console"), {"endpoint": "http://127.0.0.1:9999"})
            _attach_anonymous(request)
            assert views._requested_override(request) is None

    def test_an_open_box_still_refuses_a_host_outside_the_allowlist(self, rf, db):
        """The open box has NO caller to gate -- `is_admin` is True for
        OPEN_PRINCIPAL by design -- so the ALLOWLIST is the only thing
        standing between an `<img src=...>` and an outbound probe. That
        is the whole S5 chain on a default install."""
        request = rf.get(reverse("inference-console"), {"endpoint": "http://8.8.8.8:53"})
        _attach_anonymous(request)
        assert views._requested_override(request) is None

    def test_an_administrator_gets_the_override(self, rf, db, an_admin_user):
        """Wrapped in an accounts-on posture, deliberately: under the
        default OPEN posture `is_admin` answers True for anybody (see the
        drive-by test above), so an unwrapped admin-vs-member pair here
        would pass for the wrong reason."""
        with posture(POSTURE_ENTERPRISE):
            request = rf.get(reverse("inference-console"), {"endpoint": "http://127.0.0.1:9999"})
            _attach_user(request, an_admin_user)
            assert views._requested_override(request) == "http://127.0.0.1:9999"

    def test_a_signed_in_non_admin_gets_no_override(self, rf, db, a_member_user):
        with posture(POSTURE_ENTERPRISE):
            request = rf.get(reverse("inference-console"), {"endpoint": "http://127.0.0.1:9999"})
            _attach_user(request, a_member_user)
            assert views._requested_override(request) is None

    def test_an_administrator_still_gets_no_override_for_a_disallowed_host(
        self, rf, db, an_admin_user
    ):
        """The binding invariant: the override is introduced by a GET
        carrying it (re-appended by every redirect-after-POST) and the
        allowlist and the admin gate are INDEPENDENT -- passing one never
        waives the other. An admin's GET for a host outside the allowlist
        is ignored exactly like a non-admin's or an anonymous caller's
        (see `TestOverrideIsAdminOnly` above and `TestNoProbeFromADriveByGet`
        below): no probe fan-out, ever, for a non-allowlisted host,
        regardless of who is asking."""
        request = rf.get(reverse("inference-console"), {"endpoint": "http://8.8.8.8:53"})
        _attach_user(request, an_admin_user)
        assert views._requested_override(request) is None


class TestNoProbeFromADriveByGet:
    def test_a_disallowed_host_is_never_probed(self, client, db, monkeypatch, an_admin_user):
        """The whole finding, end to end: one GET fanned out to ~10
        outbound probes at an attacker-chosen host. Pins EVERY probe
        surface `_build_context` touches -- `_check_health`, `discover`,
        and `_engine_options` (the family/variant/text_encoder/vae calls)
        -- to an address this box already knows; the disallowed host
        reaches none of them.

        RE-PINNED by the fresh-registry family-fields fix: the four
        `_engine_options` calls used to be asserted at the resolved
        default endpoint literally, because that is the only address they
        were ever handed. They now go to the family-declaring engine's OWN
        address (`views._family_endpoint`), which on a box with nothing
        registered is that engine's configured default -- still one of the
        two classes the allowlist is built from, never a caller-supplied
        host. The assertion below therefore states the invariant this test
        exists for (nothing the caller named is probed, and every probed
        address is one the box itself configured) rather than the single
        address that satisfied it before."""
        default = views._default_endpoint()
        configured = set(settings.INFERENCE_DEFAULT_ENDPOINTS.values()) | {default}
        probed_health: list[str] = []
        probed_options: list[str] = []
        monkeypatch.setattr(
            views, "_check_health",
            lambda endpoint: probed_health.append(endpoint) or False,
        )
        monkeypatch.setattr(
            views, "_engine_options",
            lambda engine, endpoint, what: probed_options.append(endpoint) or [],
        )
        client.force_login(an_admin_user)
        with patch.object(views, "discover") as mock_discover:
            mock_discover.return_value = []
            client.get(reverse("inference-console"), {"endpoint": "http://169.254.169.254"})

        assert probed_health == [default]
        assert len(probed_options) == 4
        assert set(probed_options) <= configured
        assert not any("169.254.169.254" in endpoint for endpoint in probed_options)
        # All four are ONE address by construction (`_family_endpoint` is
        # called once and its answer reused), so pinning that restores most
        # of the address-identity specificity the `== [default] * 4` form
        # used to carry: a regression that aimed, say, the VAE probe at a
        # different configured engine's default would still be caught.
        assert len(set(probed_options)) == 1
        engine_map = mock_discover.call_args[0][0]
        assert all(
            "http://169.254.169.254" not in endpoints for endpoints in engine_map.values()
        )

    def test_the_drive_by_example_is_refused_and_never_probed(
        self, client, db, monkeypatch, an_admin_user
    ):
        """S5's own drive-by example, verbatim: `http://10.0.0.5:22` is a
        private, unregistered LAN address, refused exactly like a public
        one since review round 1 dropped the private-range clause the
        first pass had (which would have let this exact address
        through).

        Pinned the same way as `test_a_disallowed_host_is_never_probed`
        above (Minor, whole-branch review): `discover` and
        `_engine_options` are mocked too, not only `_check_health`, so
        this test never falls through to a live probe of the box's own
        resolved default endpoint."""
        probed: list[str] = []
        monkeypatch.setattr(views, "_check_health",
                            lambda endpoint: probed.append(endpoint) or False)
        monkeypatch.setattr(views, "_engine_options", lambda engine, endpoint, what: [])
        client.force_login(an_admin_user)
        with patch.object(views, "discover") as mock_discover:
            mock_discover.return_value = []
            client.get(reverse("inference-console"), {"endpoint": "http://10.0.0.5:22"})
        assert "http://10.0.0.5:22" not in probed


class TestRedirectNeverCarriesADisallowedOverride:
    def test_a_posted_disallowed_override_is_dropped_not_re_appended(self, client, db):
        """The POST negative case: `endpoint_override` carrying a
        disallowed host is dropped by `_requested_override` exactly like a
        malformed one, so `_endpoint_context` has no ACTIVE override for
        `_redirect_console` to re-append -- the redirect lands on the
        plain console URL, not `?endpoint=http://10.0.0.5:22`."""
        response = client.post(
            reverse("inference-connection-add"),
            data={
                "name": "round1 conn",
                "engine": "ollama",
                "endpoint": "http://127.0.0.1:11434",
                "model_id": "llama3.1:8b",
                "capability": "chat",
                "endpoint_override": "http://10.0.0.5:22",
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("inference-console")
        assert "endpoint=" not in response.url


# --- S19: a STORED endpoint is validated on both write paths -----------------


@pytest.mark.django_db
class TestConnectionAddEndpointValidation:
    """`connection_add`'s create path used to store `endpoint` verbatim
    once it was merely non-empty. `_endpoint_is_well_formed` -- the SAME
    parser the allowlist above builds on -- is now enforced too."""

    def _post(self, client, **overrides):
        data = {
            "name": "s19 conn",
            "engine": "ollama",
            "endpoint": "http://127.0.0.1:11434",
            "model_id": "llama3.1:8b",
            "capability": "chat",
        }
        data.update(overrides)
        return client.post(reverse("inference-connection-add"), data=data)

    def test_a_well_formed_endpoint_is_stored(self, client):
        response = self._post(client)

        assert response.status_code == 302
        connection = ModelConnection.objects.get(name="s19 conn")
        assert connection.endpoint == "http://127.0.0.1:11434"

    @pytest.mark.parametrize("bad", ["file:///etc/passwd", "not-a-url", "ftp://host/"])
    def test_a_malformed_endpoint_is_refused_no_row_created(self, client, bad):
        response = self._post(client, endpoint=bad, name=f"s19 refused {bad}")

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name=f"s19 refused {bad}").exists()
        followed = client.get(response.url)
        assert "valid http" in followed.content.decode().lower()


@pytest.mark.django_db
class TestMachineModelAddEndpointValidation:
    """`machine_model_add`'s endpoint is template-filled, never
    operator-typed, but it is still a second write path into
    `ModelConnection.endpoint` and gets the same syntax check."""

    def _post(self, client, **overrides):
        data = {
            "engine": "ollama",
            "model_id": "llama3.1:8b",
            "endpoint": "http://127.0.0.1:11434",
            "capability": "chat",
        }
        data.update(overrides)
        return client.post(reverse("inference-machine-add"), data=data)

    def test_a_well_formed_endpoint_is_stored(self, client):
        response = self._post(client)

        assert response.status_code == 302
        connection = ModelConnection.objects.get(engine="ollama", model_id="llama3.1:8b")
        assert connection.endpoint == "http://127.0.0.1:11434"

    @pytest.mark.parametrize("bad", ["file:///etc/passwd", "not-a-url", "ftp://host/"])
    def test_a_malformed_endpoint_is_refused_no_row_created(self, client, bad):
        response = self._post(client, endpoint=bad, model_id=f"model-{bad}")

        assert response.status_code == 302
        assert not ModelConnection.objects.filter(model_id=f"model-{bad}").exists()
        # Review round 1: a message distinct from the blank-fields refusal
        # -- this one names a tampered/stale hidden field, not a blank
        # form, and points at re-scanning rather than re-typing.
        followed = client.get(response.url)
        assert "re-scan and try again" in followed.content.decode()
