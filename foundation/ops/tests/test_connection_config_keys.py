"""`ModelConnection.config`'s key list cannot drift from the code (S7).

`ModelConnection.config` is the platform's fourth settings tier and its
only SCHEMALESS one: a JSON blob per registered connection, seeded into
`models.contracts.bindings.ResolvedModel.config` and splatted into an
engine adapter's builder as `**config`. No migration declares a key, no
form renders one, no validator refuses one, and no help card describes
one -- which is precisely what the settings-backend audit's S7 named.

The DISCOVERABILITY half of S7 is closed by `docs/EXTENDING.md`'s "Where
a setting lives" rule (d), which lists every key any code reads. THIS
FILE is what stops that list being prose that rots: it sweeps every
production module for the spellings a config key is actually read by and
compares that set against the documented one, BOTH DIRECTIONS. Code that
reads a key the table does not name is red; a table row nothing reads any
more is red.

The VALIDATION half is deliberately NOT built -- runtime schema
validation for this tier is a recorded owner-call (ADR 0018, G13). This
file asserts nothing about a key's VALUE, only that the set of keys is
written down where an author adding the next one will find it.

WHY A REGEX SWEEP AND NOT AN IMPORT. A `foundation/` module may not
import `models.*` (import law rule 3), and the question being asked is
about source text anyway -- "which string literals does this codebase use
as config keys" is not a runtime fact any object can be interrogated for.
Every spelling the alternation below recognises is one that exists in the
tree today, each named in its own comment; a NEW spelling nothing here
matches would make the sweep miss a key, which is why
`test_the_sweep_is_not_vacuous` pins the shape of what it finds rather
than trusting it.

NO FILE LIST. The sweep runs over every tracked production module in
every column, not a hand-maintained set of "the files that consume a
config" -- such a list is itself a thing that goes stale, and the
alternation is specific enough to produce zero false positives across the
whole tree today (10 modules, 8 keys, 2 columns at the time of writing).

WHAT GUARDS OVER-MATCHING, precisely -- an earlier version of this
docstring claimed `test_the_sweep_is_not_vacuous` did, and it does not
(closing-wave re-verify, C2(b)): that test asserts FLOORS, and a floor
cannot notice an increase. The real protection is
`test_every_key_production_reads_is_in_the_documented_list`. A false
positive only matters if it extracts a string this table does not name,
and that test goes red on exactly that, naming the bogus key and the
module it came from -- which is also how a reader learns the regex
over-matched rather than that somebody added a knob. A false positive
that happens to extract an ALREADY documented key is inert: it adds a
module to that key's "read by" set, which nothing asserts on.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from foundation.ops.tests._helpers import REPO_ROOT, _is_test_file

_EXTENDING = REPO_ROOT / "docs" / "EXTENDING.md"

# The documented table is fenced by HTML comments rather than located by
# heading text: a heading is prose somebody will reword, and this parse
# must survive that. Invisible in rendered Markdown.
_TABLE_BEGIN = "<!-- CONNECTION-CONFIG-KEYS:BEGIN -->"
_TABLE_END = "<!-- CONNECTION-CONFIG-KEYS:END -->"

# `| `key` | description |` -- the first cell of each body row.
_DOC_ROW_RE = re.compile(r"^\s*\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

_DOCSTRING_RE = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')
_COMMENT_RE = re.compile(r"#[^\n]*")

# Every spelling in the tree by which a `ModelConnection.config` key is
# read, and nothing else. Stripped of docstrings and comments first, so a
# key merely NAMED in prose (several are, correctly) is not mistaken for
# a key the code reads.
_CONFIG_KEY_RE = re.compile(
    r"""(?:
          \bcfg\s*(?:\.get|\.setdefault)?\s*[(\[]     # the `**cfg` an engine builder receives
        | \bself\.config\s*(?:\.get)?\s*[(\[]         # an adapter that stored the dict on itself
        | \bconfig\s*(?:\.get)?\s*[(\[]               # a workflow template's `config` parameter
        | \(\s*config\s+or\s+\{\}\s*\)\s*\.get\(      # the shared `config_family` normalisation
        | _declared\(\s*config\s*,\s*                 # the fragments' declare-or-refuse helper
      )\s*["']([a-z_]+)["']""",
    re.VERBOSE,
)

_COLUMNS = ("models", "tools", "agents", "identity", "foundation", "config", "scripts")


def _documented_keys() -> set[str]:
    text = _EXTENDING.read_text()
    start = text.index(_TABLE_BEGIN) + len(_TABLE_BEGIN)
    end = text.index(_TABLE_END)
    return set(_DOC_ROW_RE.findall(text[start:end]))


def _production_modules() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--"] + list(_COLUMNS),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [r for r in out if r.endswith(".py") and not _is_test_file(r)]


def keys_read_in(source: str) -> set[str]:
    """Every config key `source` READS -- module-level so the non-vacuity
    test below can run the same extractor over a synthetic snippet
    instead of trusting that the tree-wide sweep would have caught a new
    one."""
    stripped = _COMMENT_RE.sub("", _DOCSTRING_RE.sub("", source))
    return {m.group(1) for m in _CONFIG_KEY_RE.finditer(stripped)}


def _keys_by_module() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for relative in _production_modules():
        keys = keys_read_in((REPO_ROOT / relative).read_text())
        if keys:
            found[relative] = keys
    return found


def test_every_key_production_reads_is_in_the_documented_list():
    """Direction 1: code cannot read a key the author of the next one
    would never find. The failure message names the key AND the module,
    so the fix is "add this row", not "go looking"."""
    documented = _documented_keys()
    undocumented = {
        key: sorted(mods)
        for key, mods in _invert(_keys_by_module()).items()
        if key not in documented
    }
    assert not undocumented, (
        f"these `ModelConnection.config` keys are read by production code but are not in "
        f"`docs/EXTENDING.md`'s \"Where a setting lives\" rule (d) table: {undocumented}. "
        f"A new config key must be added to that list in the same commit that reads it -- "
        f"it is the only record this schemaless tier has."
    )


def test_every_documented_key_is_actually_read():
    """Direction 2, and the one a one-directional guard would miss: a row
    left behind by code that stopped reading it teaches a future author
    about a knob that does nothing."""
    read = set(_invert(_keys_by_module()))
    unread = sorted(_documented_keys() - read)
    assert not unread, (
        f"`docs/EXTENDING.md`'s rule (d) table names these `ModelConnection.config` keys, and "
        f"no production module reads any of them: {unread}. Either the code that read them was "
        f"removed and the row should go, or the key is read by a spelling this file's "
        f"`_CONFIG_KEY_RE` does not recognise -- in which case widen the regex, do not delete "
        f"the row."
    )


def test_the_sweep_is_not_vacuous():
    """A regex that silently stopped matching would make BOTH assertions
    above pass trivially (an empty set is a subset of anything, and the
    doc-side one would then fail loudly -- but only because the doc list
    is non-empty, which is not a property this file should rely on).
    Pins the shape of what the sweep finds: several distinct keys, spread
    across several modules, in more than one column.

    A FLOOR, AND ONLY A FLOOR. These assertions catch a regex that
    stopped matching; they cannot catch one that STARTED over-matching,
    because `>=` says nothing about an increase (closing-wave re-verify,
    C2(b) -- this docstring used to claim otherwise). Over-matching is
    caught by `test_every_key_production_reads_is_in_the_documented_list`
    instead: a false positive matters exactly when it extracts a string
    the table does not name, and that is what goes red there. The floors
    were not tightened to equality deliberately -- the module count
    legitimately grows the day a new engine adapter reads `family`, and a
    pin that reddened on that would be reddening on the sweep working,
    not on it drifting."""
    by_module = _keys_by_module()
    assert len(by_module) >= 6, sorted(by_module)
    assert len(_invert(by_module)) >= 6, sorted(_invert(by_module))
    assert len({module.split("/")[0] for module in by_module}) >= 2, sorted(by_module)
    assert _documented_keys(), "the documented table parsed empty -- check the fence markers"


def test_the_extractor_catches_a_key_in_every_spelling_the_tree_uses():
    """NON-VACUITY, positive control: the extractor is run over a
    synthetic module written in each of the five spellings
    `_CONFIG_KEY_RE` claims to recognise, each carrying a key that
    appears nowhere in the real tree. If the regex were broken, the real
    sweep above would quietly return nothing and this returns nothing
    too -- which is the whole point of asserting it here rather than
    inferring it from a green suite.

    The synthetic source ALSO carries the same invented key inside a
    docstring and a comment, which must NOT be extracted: a key named in
    prose is not a key the code reads, and several real modules name keys
    in prose correctly."""
    synthetic = '''
"""A docstring naming config.get("prose_only_key") -- must not count."""
# A comment naming cfg.get("comment_only_key") -- must not count either.
def build(**cfg):
    cfg.setdefault("spelling_cfg_setdefault", 1)
    a = cfg.get("spelling_cfg_get")
    b = cfg["spelling_cfg_item"]
    return a, b

class Adapter:
    def run(self):
        c = self.config.get("spelling_self_config_get")
        d = self.config["spelling_self_config_item"]
        return c, d

def template(config):
    e = config.get("spelling_config_get")
    f = _declared(config, "spelling_declared", "a thing")
    return e, f

def normalise(config):
    return (config or {}).get("spelling_or_empty")
'''
    found = keys_read_in(synthetic)
    assert found == {
        "spelling_cfg_setdefault",
        "spelling_cfg_get",
        "spelling_cfg_item",
        "spelling_self_config_get",
        "spelling_self_config_item",
        "spelling_config_get",
        "spelling_declared",
        "spelling_or_empty",
    }, sorted(found)
    assert "prose_only_key" not in found
    assert "comment_only_key" not in found


def test_an_undocumented_key_would_fail_the_first_assertion():
    """The mutation the first test's failure mode actually needs: a
    module that reads a key the table does not name. Done against the
    extractor and the parsed table in memory -- no repo file is touched
    -- so this proves the comparison, not merely the regex."""
    documented = _documented_keys()
    invented = keys_read_in('def f(**cfg): return cfg.get("an_undocumented_knob")')
    assert invented == {"an_undocumented_knob"}
    assert not invented <= documented


def _invert(by_module: dict[str, set[str]]) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for module, module_keys in by_module.items():
        for key in module_keys:
            keys.setdefault(key, set()).add(module)
    return keys
