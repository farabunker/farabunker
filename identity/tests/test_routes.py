"""`ROUTE_RULES` covers every route this platform owns.

The route list is DERIVED from the resolver, never typed: a route added
to any `urls.py` without a classification fails here immediately,
naming itself.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import get_resolver

from identity.routes import ADMIN, AUTHENTICATED, PUBLIC, ROUTE_RULES, TIERS, _TIER, tier_for

# The six finer classes `ROUTE_RULES` may hold -- `identity/routes.py`'s
# own table, not retyped as test data (that duplication is exactly what
# FIX-BEFORE-MERGE 2 retired): the module's own `_TIER` map names the two
# that are not AUTHENTICATED, so the full set is those two plus the four
# `tier_for` folds into its default.
_CLASSES = set(_TIER) | {"A", "O", "L", "R"}


def _named_routes() -> dict[str, list[str]]:
    """Every URL name in the project -> its `app_names`."""
    out: dict[str, list[str]] = {}

    def walk(resolver, namespaces):
        for pattern in resolver.url_patterns:
            sub = getattr(pattern, "url_patterns", None)
            if sub is not None:
                walk(pattern, namespaces + ([pattern.app_name] if pattern.app_name else []))
            elif pattern.name:
                out[pattern.name] = namespaces

    walk(get_resolver(), [])
    return out


class TestCoverage:
    def test_every_route_this_platform_owns_is_classified(self):
        """The direction with the teeth. `/admin/`'s tree is excluded --
        it is classified wholesale by namespace (`tier_for`) -- and
        everything else must be in the table."""
        missing = sorted(
            name for name, app_names in _named_routes().items()
            if "admin" not in app_names and name not in ROUTE_RULES
        )
        assert missing == [], missing

    def test_every_rule_names_a_route_that_exists(self):
        """The direction that catches a rule left behind by a deleted
        route. `/vision/`'s names are skipped when the flag is off --
        `config/urls.py` mounts that tree conditionally.

        Task 9 mounts the last four `/identity/` names (the users page
        and the posture page), so every name in `ROUTE_RULES` now names
        a route that genuinely exists and this skip is gone outright."""
        derived = set(_named_routes())
        vision_off = "vision" not in settings.FARABUNKER_FEATURES
        stale = sorted(
            name for name in ROUTE_RULES
            if name not in derived
            and not (vision_off and name.startswith("vision-"))
        )
        assert stale == [], stale

    def test_the_admin_exclusion_is_not_swallowing_everything(self):
        """Anti-vacuous pin: at least one admin-namespaced name is
        derived, the non-admin set is non-empty, and it contains a known
        name from each mount.

        Every `/identity/` name -- the four Task 8 mounted and the four
        Task 9 mounts here -- is now checked against the LIVE RESOLVER.
        """
        routes = _named_routes()
        assert any("admin" in app_names for app_names in routes.values())
        ours = {n for n, a in routes.items() if "admin" not in a}
        assert len(ours) > 40
        for known in ("chat-index", "rag-documents", "inference-console",
                      "jobs-queue", "setup-index",
                      "identity-login", "identity-logout",
                      "identity-password-change", "identity-password-change-done",
                      "identity-users", "identity-user-create",
                      "identity-user-edit", "identity-settings"):
            assert known in ours, known
        assert "identity-login" in ROUTE_RULES


class TestTierFor:
    def test_every_value_in_the_table_is_a_real_class(self):
        """`ROUTE_RULES` now holds the finer class (FIX-BEFORE-MERGE 2:
        one table, not a tier here and the class retyped as test data in
        `test_route_matrix.py`) -- every value must be one of the six
        `identity/routes.py` documents, and every one of those six must
        still derive a real tier through `tier_for`."""
        assert set(ROUTE_RULES.values()) <= _CLASSES
        assert all(tier_for(name, []) in TIERS for name in ROUTE_RULES)

    def test_an_unclassified_name_fails_closed(self):
        """Forgetting to classify a new route must ship it CLOSED."""
        assert tier_for("a-route-nobody-classified", []) == ADMIN

    def test_the_admin_tree_is_classified_by_namespace(self):
        assert tier_for("index", ["admin"]) == ADMIN

    def test_exactly_three_routes_are_public_and_each_earns_it(self):
        """`setup-index` is the page a person needs BEFORE they can log
        in to a box whose engines are not up; `identity-login` is the
        page they log in ON; `settings-index` (UI-2) is not a page at
        all -- it renders nothing and redirects to the first settings
        section this viewer may open, which for an anonymous visitor is
        `setup-index` and nothing else. Every target keeps its OWN
        class, so the redirect opens no door its destination had shut.
        Nothing else on this platform is reachable without a session,
        and a fourth entry here is a hole."""
        assert sorted(n for n in ROUTE_RULES if tier_for(n, []) == PUBLIC) == [
            "identity-login", "settings-index", "setup-index"]
        assert tier_for("setup-index", []) == PUBLIC
        assert tier_for("identity-login", []) == PUBLIC
        assert tier_for("settings-index", []) == PUBLIC

    def test_the_console_read_is_admin_not_merely_its_mutations(self):
        """Closes ADR 0010's standing gap in full rather than half: the
        console displays an inventory of the box, which is operator
        information."""
        assert tier_for("inference-console", []) == ADMIN

    def test_the_queue_page_is_authenticated_and_its_settings_are_admin(self):
        """A queue ROW is operational and a member sees their own; the
        memory budget and retention policy are the operator's."""
        assert tier_for("jobs-queue", []) == AUTHENTICATED
        assert tier_for("jobs-settings-update", []) == ADMIN


class TestIA2Routes:
    def test_the_four_identity_routes_are_classified(self):
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["identity-groups"] == "S"
        assert ROUTE_RULES["identity-group-edit"] == "S"
        assert ROUTE_RULES["identity-entitlements"] == "S"

    def test_the_ia2_route_block_is_classified_as_a_block(self):
        """The IA-2 route that lives in ANOTHER column's `urls.py` still
        has its class here, which is what makes `identity/routes.py` the
        one table. It is also asserted in the task that adds it (Task 6);
        this is the block-level pin that it is not forgotten.

        `.get(name, <expected>)` rather than `[name]`, deliberately: the
        route arrives in a later task, and an assertion that cannot
        pass yet is not an assertion worth committing red.
        """
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES.get("chat-tool-entitlements", "S") == "S"   # Task 6 adds it

    def test_the_entitlement_page_is_R_not_S_so_an_owner_can_reach_it(self):
        """Spec section 11.3 marks it "S for create/rename/delete; an
        OWNER may reach the grant form (checked in the view, since the
        middleware's tiers are coarse)". `tier_for` maps S to ADMIN, and
        `IdentityGateMiddleware` refuses a non-admin there BEFORE the
        view runs -- so an owner could never reach the form. R maps to
        AUTHENTICATED, which is exactly "the middleware admits, the view
        decides", and rename/delete re-check `is_admin` inside."""
        from identity.routes import AUTHENTICATED, ROUTE_RULES, tier_for
        assert ROUTE_RULES["identity-entitlement-edit"] == "R"
        assert tier_for("identity-entitlement-edit", []) == AUTHENTICATED

    def test_the_tool_label_page_is_S(self):
        """Labelling a tool is library-wide operator policy. Unlike a
        document label it has no entitlement owner -- spec section 7.4
        names an owner's three capabilities and all three are about
        grants and documents."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["chat-tool-entitlements"] == "S"

    def test_the_agent_access_page_is_S(self):
        """Labelling an agent or a flow is the same library-wide
        operator policy as labelling a tool, and neither has an
        entitlement owner (spec sections 6.11, 9.6)."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["chat-agent-entitlements"] == "S"

    def test_the_bulk_label_route_is_R(self):
        """THE ONE label route (C-36 deleted the per-document SET twin).
        An operational ROW action applied per document: `is_admin`, or an
        owner of every entitlement being added or removed. This is the
        route the administer/read split exists for -- an administrator
        labels a document they are not cleared to read. It carries no row
        in its own URL, so it needs no `_ROW_ADDRESSED_R`/
        `_LIBRARY_MUTATIONS` entry of its own in the route matrix."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["rag-document-labels-bulk"] == "R"

    def test_the_model_set_routes_are_S(self):
        """A model connection is box inventory, not somebody's library
        row (IA-2 T14) -- an entitlement owner has no standing over it,
        so all three model-set routes are S, not R."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["inference-connection-sets"] == "S"
        assert ROUTE_RULES["inference-model-sets"] == "S"
        assert ROUTE_RULES["inference-model-set-edit"] == "S"
