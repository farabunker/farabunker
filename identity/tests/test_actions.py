from identity.contracts.actions import AUDIT_ACTIONS, SOURCE_CHOICES


class TestTheCatalogue:
    def test_every_name_is_unique(self):
        """A duplicate would make one write silently indistinguishable
        from another in every report built on `action`."""
        assert len(set(AUDIT_ACTIONS)) == len(AUDIT_ACTIONS)

    def test_every_name_carries_its_family_prefix(self):
        """Every name is `<prefix>.<verb>`, and the prefix is what an
        audit page groups on -- so a name without one has no group to
        fall in. SIXTEEN prefixes, one set below, counted directly off
        it rather than restated as a number that can drift from the
        literal again: `identity.` alone covers the session/account/
        posture families, the entitlement/group family spans three
        (`entitlement.`/`grant.`/`group.`), the label/share family two
        (`library.`/`tool.` for what got labelled, `share.` for the
        grant), `modelset.` and `agent.`/`flow.` are one prefix per
        family, `vision.` is the one-action family the Engine files
        feature (2026-09-02) adds, `workstream.` is the WS-1 family
        (2026-09-03), and `conversation.` is WS-2's own addition (Task 16)
        -- a taint tag is a fact about the conversation, not the stream,
        for the two of the four taint actions that name one -- see
        `test_the_full_vocabulary_is_declared_now_not_in_two_passes`'s own
        amendment note. (A prior version of this docstring said "SEVEN"
        against a ten-entry set -- already wrong before this feature
        added an eleventh; counted by hand against the literal below this
        time.) `library.document_contained` (Task 8, WS-1) reuses the
        existing `library.` prefix, so that one addition left the count
        of prefixes at twelve even though the vocabulary grew by one --
        `conversation.*` is what pushes it to thirteen. Coherence Wave B
        (S3, settings/registry audit coverage) adds THREE more:
        `queue.` (`models.queue.models.JobSettings` writes -- `library.
        settings_updated` reuses the existing `library.` prefix for
        `RagSettings`, so it adds no new one), `connection.`
        (`models.registry.models.ModelConnection` create/edit/delete),
        and `role.` (`models.registry.models.RoleBinding` assign/
        unassign) -- pushing the count from thirteen to sixteen."""
        prefixes = {"identity.", "entitlement.", "grant.", "group.", "library.",
                    "tool.", "share.", "modelset.", "agent.", "flow.", "vision.",
                    "workstream.", "conversation.", "queue.", "connection.", "role."}
        assert len(prefixes) == 16
        for action in AUDIT_ACTIONS:
            assert any(action.startswith(p) for p in prefixes), action

    def test_the_full_vocabulary_is_declared_now_not_in_two_passes(self):
        """IA-2's sixteen entitlement/group/label/share actions are here
        already, so the closed tuple is amended once rather than twice.
        The `modelset.*` seven arrived later, with the 2026-08-30 owner
        directives (see the module docstring's amendment note).
        `vision.engine_file_deleted` is a second, later amendment -- the
        Engine files feature (2026-09-02) needed an action no earlier
        phase had reserved. The nine `workstream.*` actions are a third,
        later amendment still -- WS-1 (2026-09-03) needed a whole new
        family for a stream, its wall, and its upload default. The two
        `workstream.document_*` pin actions are a fourth amendment, in
        the same WS-1 phase -- pinning is a task later than the stream
        itself, so its actions could not have been reserved with the
        other nine. `library.document_contained` is a fifth amendment,
        in the same WS-1 phase -- containment (Task 8) needed its own
        audited write once `restamp_document_chunks` grew a
        containment-change caller, and it reuses the `library.` prefix
        `library.document_labelled` already carries rather than opening
        a thirteenth. The four taint actions are a sixth amendment, in
        WS-2 (Task 16) -- two new prefixes for a fact recorded at two
        levels: `conversation.tainted`/`conversation.untainted` for the
        conversation's own tags, `workstream.tainted`/`workstream.
        untainted` (reusing the existing `workstream.` prefix) for the
        stream's materialised union. `workstream.consolidated` is a
        seventh amendment, in the same WS-1 phase (Task 18) -- it reuses
        the existing `workstream.` prefix too, so the prefix count above
        stays at thirteen even though the vocabulary grows by one.
        `workstream.include_universal_set` is an EIGHTH amendment, round
        17 (owner: "a bool in settings to use all rag documents ...
        default it on") -- the "use the full document library" toggle
        is an access consequence like `workstream.upload_default_set`
        beside it, not a label like the un-audited description field,
        and it too reuses the existing `workstream.` prefix, so the
        prefix count stays at thirteen a second time. Coherence Wave B
        (S3) is a NINTH amendment: `RagSettings`, `JobSettings`,
        `ModelConnection` (incl. `footprint_override_bytes`) and
        `RoleBinding` writes were the four unaudited settings surfaces
        the backend audit named with no recorded rationale, and the
        recorded ruling closing that gap is "audit all four".
        `agent.created`/`agent.edited` are a TENTH amendment, the chat
        cluster's agent form (feature B, task 6) -- the first rows this
        box writes for an agent that nobody shipped. Both reuse the
        existing `agent.` prefix the labelling pair already opened, so
        the prefix count above stays at sixteen while the vocabulary
        grows by two."""
        assert "entitlement.created" in AUDIT_ACTIONS
        assert "share.revoked" in AUDIT_ACTIONS
        assert "modelset.created" in AUDIT_ACTIONS
        assert "modelset.attached" in AUDIT_ACTIONS
        assert "modelset.detached" in AUDIT_ACTIONS
        assert "agent.labelled" in AUDIT_ACTIONS
        assert "agent.unlabelled" in AUDIT_ACTIONS
        assert "flow.labelled" in AUDIT_ACTIONS
        assert "flow.unlabelled" in AUDIT_ACTIONS
        assert "vision.engine_file_deleted" in AUDIT_ACTIONS
        assert "workstream.created" in AUDIT_ACTIONS
        assert "workstream.renamed" in AUDIT_ACTIONS
        assert "workstream.deleted" in AUDIT_ACTIONS
        assert "workstream.archived" in AUDIT_ACTIONS
        assert "workstream.unarchived" in AUDIT_ACTIONS
        assert "workstream.instructions_set" in AUDIT_ACTIONS
        assert "workstream.scope_added" in AUDIT_ACTIONS
        assert "workstream.scope_removed" in AUDIT_ACTIONS
        assert "workstream.upload_default_set" in AUDIT_ACTIONS
        assert "workstream.document_pinned" in AUDIT_ACTIONS
        assert "workstream.document_unpinned" in AUDIT_ACTIONS
        assert "library.document_contained" in AUDIT_ACTIONS
        assert "workstream.tainted" in AUDIT_ACTIONS
        assert "conversation.tainted" in AUDIT_ACTIONS
        assert "workstream.untainted" in AUDIT_ACTIONS
        assert "conversation.untainted" in AUDIT_ACTIONS
        assert "workstream.consolidated" in AUDIT_ACTIONS
        assert "workstream.include_universal_set" in AUDIT_ACTIONS
        assert "library.settings_updated" in AUDIT_ACTIONS
        assert "queue.settings_updated" in AUDIT_ACTIONS
        assert "connection.created" in AUDIT_ACTIONS
        assert "connection.updated" in AUDIT_ACTIONS
        assert "connection.deleted" in AUDIT_ACTIONS
        assert "role.assigned" in AUDIT_ACTIONS
        assert "role.unassigned" in AUDIT_ACTIONS
        assert "agent.created" in AUDIT_ACTIONS
        assert "agent.edited" in AUDIT_ACTIONS
        assert len(AUDIT_ACTIONS) == 70

    def test_no_name_is_longer_than_the_column(self):
        """`AuditEvent.action` is CharField(max_length=64)."""
        assert max(len(a) for a in AUDIT_ACTIONS) <= 64

    def test_the_three_sources(self):
        assert [value for value, _ in SOURCE_CHOICES] == ["web", "cli", "admin"]
