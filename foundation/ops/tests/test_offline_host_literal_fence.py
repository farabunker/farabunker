"""The permanent gate on new non-local host literals in production code.

WHY THIS EXISTS. `docs/ARCHITECTURE.md` §2 states, as design, that nothing
reaches the public internet unless a posture profile opens a gated window.
That property is TRUE today -- a read-only investigation confirmed no
shipped runtime path reaches a public host: every module that imports an
HTTP client (`models/contracts/engines/{comfyui,engine_http,ollama,
whisper}.py`) talks to an engine endpoint that is either loopback, the
container host, or a value supplied at runtime (an env var or a
`ModelConnection` row), never a public address baked into the source; the
discovery scan (`models/registry/discovery.py`) only probes
`COMMON_HOSTS` and each engine's own compose hostname; and the public URLs
that DO appear in tracked code are DISPLAY STRINGS -- `library_url` and
setup-guide text the `/setup/` console prints verbatim for an operator to
read and copy, never fetched by this platform. But that property is
upheld by policy and review today, not by anything the box enforces
(`README.md`'s "Design principles" §1; `posture/README.md`; the box builds
no ISOLATION layer yet). This gate is the review half of that sentence,
made mechanical: it fails the build the moment a new non-local host
literal lands in tracked production code, so drift has to be a deliberate
EXEMPT entry with a stated reason, not something nobody notices.

THE HONEST LIMIT, stated plainly because it is most of the truth of what
this gate is worth: it sees only STRING LITERALS in tracked `.py` code. It
cannot see an endpoint typed into a `ModelConnection` row by an operator,
an environment variable (`OLLAMA_BASE_URL` and kin), a host assembled at
runtime from an f-string or concatenation (`models/registry/discovery.py`
builds its probe URLs this way, from `COMMON_HOSTS` and `engine.name` --
literal, local values, but the *mechanism* is invisible to this gate on
any other host too), or anything else configured after the process
starts -- which is where the real exposure would live if this claim were
ever false. This gate stops literal drift; it does not enforce the
offline claim, and nothing here should be read as doing so.

Two more blind spots worth naming plainly: non-Python tracked files --
templates, JavaScript, compose files -- are not scanned at all (today's
templates carry only `host.docker.internal` placeholder text, so the
vector is clean, but it is a gap in this gate, not a closed door). And a
schemeless host literal -- a bare `"api.example.com"` with no `http(s)://`
in front -- cannot be scanned without drowning in dotted-path false
positives (module paths, version strings, anything else with a dot in
it), so it is documented here rather than chased.

SCOPE. Tracked `.py` files only, and only the STRING LITERALS the program
actually uses as values -- not comments (those are not source text this
walk visits at all) and not DOCSTRINGS (the first bare string statement of
a module, class, or function). A docstring is prose ABOUT the code, never
a value the program reads or sends anywhere, so an illustrative address in
one -- `models/registry/views.py`'s attack-scenario walkthrough quotes
`box.lan` and the RFC 2606 reserved `gpu-box.example` domain to explain
`_override_is_permitted`'s allowlist -- is not the hazard this gate exists
for. That exclusion is a drift-model judgment, not a claim that a
docstring cannot carry a live value -- `module.__doc__` plus a split is a
working fetch target, in principle -- and it does not lower the
adversarial bar: a plain f-string is a cheaper hiding place than a
docstring, and is already admitted above as invisible to this gate. Test
modules are excluded the same way `test_engine_endpoint_fence.py`
excludes them: this gate polices what SHIPS, not test fixtures.

WHAT COUNTS AS LOCAL. The literal loopback/any-address forms
(`localhost`, `127.0.0.1`, `0.0.0.0`, `::1`), `host.docker.internal` (how
the farabunker web container reaches the operator's own machine -- see
every adapter's `setup_guide.network_note`), a bare hostname with no dot
(a Docker Compose service name like `ollama` or `comfyui`, the same shape
`models.registry.discovery.COMMON_HOSTS` and `engine.name` resolve), and
any private or loopback IP address (RFC 1918/4193 -- an operator's own
LAN, not the public internet). Anything else is flagged.

Lives beside `test_engine_endpoint_fence.py` and `test_import_law.py` for
the same reason those do -- pure file text (an AST parse plus a scan), no
Django ORM, no database.
"""
from __future__ import annotations

import ast
import ipaddress
import re
import subprocess

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file

_URL_RE = re.compile(r"(?:https?|wss?)://([^\s\"'/]+)")

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"})

# Each entry: (repo-relative file, why every non-local host literal in it
# is a DISPLAY constant -- printed for an operator to read and copy, never
# fetched -- rather than a real runtime dependency on a public host. Only
# the three adapters whose `library_url`/`setup_guide` carry real public
# addresses are here; `engine_http.py`, the fourth module that imports an
# HTTP client, carries none and needs no exemption.
EXEMPT = (
    ("models/contracts/engines/comfyui.py",
     "`library_url` and the setup guide's install commands (github.com, "
     "download.pytorch.org) are DISPLAY constants the /setup/ console "
     "prints verbatim for an operator to read and copy -- this platform "
     "never fetches them (see the adapter's own `library_url` and "
     "`setup_guide` docstrings, and `models/contracts/engines/base.py`'s "
     "`library_url` field docstring)."),
    ("models/contracts/engines/ollama.py",
     "`library_url` and the setup guide's text (ollama.com) are DISPLAY "
     "constants the /setup/ console prints verbatim for an operator to "
     "read and copy, never fetched by this platform."),
    ("models/contracts/engines/whisper.py",
     "`library_url` and the setup guide's text (github.com, "
     "huggingface.co) are DISPLAY constants the /setup/ console prints "
     "verbatim for an operator to read and copy, never fetched by this "
     "platform."),
)


def _tracked_production_python_files() -> list[str]:
    """Every `.py` file GIT TRACKS that is not a test module -- production
    code, DERIVED from the git index rather than `Path.glob` for the same
    reason `test_engine_endpoint_fence._tracked_test_files` gives: a
    module that has never been `git add`ed is invisible to `git ls-files`,
    so a literal typed into a brand-new file is unpoliced until it joins
    the index -- before any commit, review, or merge can carry it
    anywhere."""
    out = subprocess.run(["git", "ls-files", "--", "*.py"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p and not _is_test_file(p)]


def _split_host_port(host: str) -> str:
    """`host` with a trailing `:<digits>` port stripped, if it has one."""
    maybe_host, sep, maybe_port = host.rpartition(":")
    if sep and maybe_port.isdigit():
        return maybe_host
    return host


def _is_local_host(host: str) -> bool:
    """Whether `host` is local by any shape this codebase already uses for
    one -- see the module docstring's "WHAT COUNTS AS LOCAL" section."""
    if host in _LOCAL_HOSTS:
        return True
    if "." not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every `ast.Constant` that IS a docstring -- the first
    statement of a module, class, or function body, when it is a bare
    string expression. See the module docstring's "SCOPE" section for why
    these are excluded rather than scanned."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            ids.add(id(body[0].value))
    return ids


def _offending_hosts(relative: str) -> list[tuple[int, str]]:
    """Every `(lineno, host)` pair in `relative` where a string literal
    the program actually uses as a value -- not a docstring -- spells out
    a non-local `http(s)://` host."""
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative)
    doc_ids = _docstring_constant_ids(tree)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in doc_ids:
            continue
        for match in _URL_RE.finditer(node.value):
            host = _split_host_port(match.group(1))
            if not _is_local_host(host):
                offenders.append((node.lineno, host))
    return offenders


def test_no_new_non_local_host_literal_in_tracked_production_code():
    exempt_files = {relative for relative, _reason in EXEMPT}
    offenders: dict[str, list[tuple[int, str]]] = {}
    for relative in _tracked_production_python_files():
        if relative in exempt_files:
            continue
        hits = _offending_hosts(relative)
        if hits:
            offenders[relative] = hits

    assert not offenders, (
        "these files spell out a non-local host in a tracked-code string literal. "
        "Either it is a genuine DISPLAY string an operator reads and copies -- add "
        "the file to EXEMPT above with a reason -- or it is a real runtime dependency "
        "on a public host, which 'offline by default' (AGENTS.md non-negotiable 5) "
        "forbids: %r" % (offenders,)
    )


def test_the_gate_is_not_vacuous_it_catches_the_display_urls_it_exempts():
    """Pin on the gate's own reach: with EXEMPT bypassed, the scan must
    still find exactly the display hosts these three adapters carry today
    -- so a broken regex, a broken docstring filter, or an
    accidentally-narrowed file list goes red HERE, rather than the main
    gate quietly passing because it inspects nothing.

    One residual this pin does not close: it pins the host SET per exempt
    file, so a NEW USE of an ALREADY-PINNED host in the same file -- a
    genuinely fetched `github.com` URL added to the ComfyUI adapter, say
    -- passes both the main gate (the file is exempt) and this pin (the
    set is unchanged). No literal scan can tell a fetch from a display
    string; that case is carried by review, not by this test."""
    found = {
        relative: {host for _lineno, host in _offending_hosts(relative)}
        for relative, _reason in EXEMPT
    }
    assert found == {
        "models/contracts/engines/comfyui.py": {"github.com", "download.pytorch.org"},
        "models/contracts/engines/ollama.py": {"ollama.com"},
        "models/contracts/engines/whisper.py": {"github.com", "huggingface.co"},
    }
