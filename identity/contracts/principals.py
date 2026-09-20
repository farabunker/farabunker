"""WHO is acting -- the platform's base identity type.

MOVED here verbatim from `agents/contracts/tools.py`, where it was an
agents-column detail. After Identity & Auth it is the type every column
reads, so it lives in a rule-1 pure leaf that any column may import in
any direction (spec section 4.2). NO DJANGO, NO DATABASE, NO I/O --
pinned by `identity/tests/test_purity.py`, a subprocess with no settings
module configured at all.

This module and `identity/request.py` are the ONLY two files in the
codebase that may CONSTRUCT a `Principal`, enforced by an AST guard in
`foundation/ops/tests/test_import_law.py`. A principal's kind is the
field a grants table joins on, so a third place minting one is a third
opinion about who is acting.
"""
from __future__ import annotations

from dataclasses import dataclass

# The kinds of caller a grant can attach to.
#   "open"    -- an UNAUTHENTICATED box: one `Principal("open", "box")`.
#   "user"    -- key = the user's PRIMARY KEY as a string. Never the
#                username: a username is renameable, and a rename must
#                not orphan every row a person owns. Display resolves
#                pk -> username at render time.
#   "service" -- a named machine caller. ONE constant, not one per
#                caller: the watcher, `manage.py ingest`, `manage.py ask`
#                and `manage.py agent_turn` all act as it, and WHICH of
#                them acted is recorded in `AuditEvent.detail
#                ["source_command"]` rather than in a key that would
#                quietly turn "the shell" into four grantable subjects.
#                `api_client` is folded into this kind: nothing has ever
#                constructed one, so there is no stored data to preserve,
#                and two names meaning "a machine holding a credential"
#                would make a future grants join test two values where
#                one fact exists.
#
# HISTORICAL, below. Nothing constructs these after IA-1 -- the acting
# rule (spec section 5.3) makes a turn run as the USER -- but
# `agents.models.ToolInvocation` rows written before it carry them, and
# a closed vocabulary that cannot describe its own stored data is a
# vocabulary that lies.
#   "resident_agent" -- a code-declared agent.
#   "user_agent"     -- an Agent row somebody wrote.
PRINCIPAL_KINDS = ("open", "user", "service", "resident_agent", "user_agent")


@dataclass(frozen=True)
class Principal:
    """WHO is calling. Two strings, deliberately: the same shape serves
    an in-process agent turn, a signed-in browser request, and an
    external tool call, so nothing downstream needs a second field or a
    second code path.

    Frozen: hashable (so a later grant cache can key on it) and
    un-repointable (so nothing downstream can quietly change who a
    half-finished turn is acting as).
    """

    kind: str
    key: str

    def __post_init__(self) -> None:
        if self.kind not in PRINCIPAL_KINDS:
            raise ValueError(
                f"Unknown principal kind {self.kind!r}; must be one of "
                f"{list(PRINCIPAL_KINDS)}"
            )
        if not self.key:
            raise ValueError(f"Principal({self.kind!r}) needs a non-blank key")


# The principal an UNAUTHENTICATED box acts as. ONE instance, shared, so
# there is never a second spelling of who this box is.
OPEN_PRINCIPAL = Principal("open", "box")

# The principal EVERY shell path acts as (spec section 5.3, item 8).
SERVICE_PRINCIPAL = Principal("service", "local")


@dataclass(frozen=True)
class _Anonymous:
    """Nobody. A SEPARATE TYPE from `Principal`, not a fifth kind.

    It carries `.kind`/`.key` so the access functions read it with the
    same two attribute lookups they use for a real principal and need no
    isinstance branch -- but `"anonymous"` is deliberately NOT in
    PRINCIPAL_KINDS, so `Principal("anonymous", ...)` raises, and the
    type is structurally incapable of reaching a grants join or an owner
    column: it is never passed to `owner_fields` (the gate refuses the
    request first), and every join in this platform is on a user id,
    never on a kind string. Making it a kind would mean the first person
    to write `Q(owner_kind="anonymous")` gave "nobody" rows to own.
    """

    kind: str = "anonymous"
    key: str = ""


ANONYMOUS = _Anonymous()

# The two keys every job payload carries so the queue page, the runtime
# and the audit trail all agree on who asked for the work.
ACTOR_KIND_KEY = "actor_kind"
ACTOR_KEY_KEY = "actor_key"


def payload_fields(principal) -> dict:
    """The two keys to merge into a job payload for `principal`.

    A dict rather than two arguments, for the same reason
    `identity.access.owner_fields` is one: a call site cannot pass one
    and forget the other.
    """
    return {ACTOR_KIND_KEY: principal.kind, ACTOR_KEY_KEY: principal.key}


def principal_from_payload(payload) -> Principal:
    """The principal a job payload was enqueued by.

    NEVER RAISES, and that is the whole design of this function. It is
    called from a job handler on the worker -- where a raise is a failed
    job -- and from the queue page -- where a raise is a 500 on a
    never-500 surface. A payload with no actor keys is every job
    enqueued before this phase, and the honest answer for a row written
    by a box that had no users is the open principal. A payload with a
    kind outside the vocabulary, or one that is not a dict at all, is a
    hand-edited or corrupt row and gets the same answer.

    `OPEN_PRINCIPAL` IS NOT UNIFORMLY THE LEAST PRIVILEGED READING --
    it depends which predicate reads it. `models.queue.visibility`
    matches a member on their own kind AND key against the PAYLOAD's
    actor fields, and no member is `("open", "box")`, so there this
    fallback matches only a row with no actor (or a corrupt one) and
    nothing a real member caused: the narrowest reading available.
    `tools.vision.visibility.visible_jobs` instead filters on the
    OWNER COLUMN (`owner_kind`/`owner_key`, stamped once at creation and
    never revisited by this function), so `OPEN_PRINCIPAL` there matches
    EVERY row the open box ever created and nobody has since adopted --
    the widest reading, not the narrowest, because `owner_key` is the
    fixed string `"box"` rather than a per-caller value. A payload with
    no actor keys therefore always resolves as the open box; this is
    precisely why every enqueuer stamps a real actor (`payload_fields`,
    above) rather than leaving a job to fall through to this default.
    """
    if not isinstance(payload, dict):
        return OPEN_PRINCIPAL
    kind = payload.get(ACTOR_KIND_KEY)
    key = payload.get(ACTOR_KEY_KEY)
    if not isinstance(kind, str) or not isinstance(key, str):
        return OPEN_PRINCIPAL
    try:
        return Principal(kind, key)
    except ValueError:
        return OPEN_PRINCIPAL
