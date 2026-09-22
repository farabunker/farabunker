"""The help-card registry -- what the settings assistant knows about
this box's settings pages (spec §3).

PURE, IN THE RULE-1 SENSE, AND THAT IS LOAD-BEARING RATHER THAN A STYLE
PREFERENCE. No Django import of any kind, no database, no import of any
non-pure module. The tool runners that read this table live in
`agents/settings_tools.py`, and a module that imported `django.urls` or
`identity.access` -- as `foundation/settings_area.py` does -- would make
that read a cross-column import of a NON-PURE module, which ADR 0015's
import law forbids. Pure, it is importable by any column exactly as
`foundation/format.py` and `foundation/files.py` already are.

TWO TABLES, ONE TRUTH. `foundation/settings_area.py::SETTINGS_GROUPS`
decides what the settings area IS -- the order, the gates, the labels.
This table says what each of those pages is FOR and what every control on
it means. `route_name`, `title`-versus-`label` and `gate` are therefore
stated twice, which is the identical shape `SETTINGS_GROUPS` and
`_settings.html` already have, and it is closed the identical way: the
drift test in `foundation/tests/test_settings_help.py` asserts the two
tables name EXACTLY the same routes with EQUAL gates.

FOUR RULES NO TEST CAN CATCH, so they are written here and in
`docs/EXTENDING.md`'s "Adding a settings page" recipe:

  1. A CARD DESCRIBES THE CONTROLS ITS OWN PAGE RENDERS, AND NO OTHERS.
     The drift tests only ask whether the anchor exists on the page the
     card names, so a card that claimed a neighbouring page's control
     would pass every one of them and misroute every answer about it.
     The worked case is the one this feature is judged by: library
     posture is an `IdentitySettings` column edited ONLY on Identity &
     security, so the `identity-settings` card carries it and the
     `rag-settings` card must not -- that page owns the library's
     numeric limits, none of which is a posture.

  2. A FIELD WHOSE CONTROL ONLY RENDERS IN SOME POSTURES SAYS SO IN ITS
     `meaning`. The rendered-body assertion drives ONE posture and cannot
     see the others. Library posture is the worked case again: a real
     select on an open and an enterprise box, a hidden input with an
     explanation on a personal one. Deep links are unaffected either way
     -- the section wrapper that carries the anchor renders in all three
     postures and only its contents branch -- so this is a rule about
     what the card SAYS, never about whether the link lands.

  3. A `title` IS A LIVE TEXT MATCHER OVER EVERY ASSISTANT ANSWER'S
     PROSE, so a SHORT, GENERIC, ONE-WORD title is a live-linking
     liability no drift test catches. `agents.chat.context_processors::
     _linkify_named_pages` wraps the FIRST occurrence of a card's own
     `title` or `route_name` spelling, found anywhere in the model's
     rendered answer, in a link to that page; `_named_page_links` adds a
     page-level strip link besides. A ONE-WORD title ("Models",
     "Library", "Chat", "Accounts", "Groups", "Entitlements") reads as
     ordinary English the way a multi-word one ("Identity & security",
     "Tool access") almost never does, so `_requires_exact_case` narrows
     -- never eliminates -- the collision: a one-word title links only
     on the registry's own exact capitalisation, or case-insensitively
     when its own card was `settings.card`-fetched THIS turn (the Q7
     battery finding: an answer about library ACCESS POSTURE
     auto-linked the stray word "library" to the numeric-limits page,
     because that page's own title happens to be that word). An
     exact-case occurrence of a one-word title anywhere in any answer
     still links, whatever the sentence is actually about, so PREFER A
     MULTI-WORD OR OTHERWISE DISTINCTIVE `title` for a new card where the
     obvious human name is a common English word -- there is no rule
     forbidding a one-word title, and none of the existing ones were
     renamed for this, but a new card is the one place this cost
     is still cheap to avoid by choice rather than by narrowing the
     matcher further.

  4. IF A CONTROL ONLY TAKES EFFECT WHILE A `FARABUNKER_FEATURES` TOKEN
     IS ON, SAY SO IN ITS `meaning`. A control can be honest about what
     it does and still mislead an administrator on a box that has the
     relevant feature off. The worked case is `rag-settings`'s "Maximum
     media duration" and "Maximum document pages" fields: both describe
     caps that have nothing to enforce until "media" is enabled, for two
     DIFFERENT reasons the rule deliberately does not distinguish
     between. The media cap's own file types are not even accepted for
     upload with the flag off (`tools.rag.ingest.supported_exts`
     adds `AV_EXTS`/`IMAGE_EXTS` only under the flag); the page cap's
     ARE (`PROSE_EXTS` is accepted unconditionally) -- what is gated
     there is the vision-extraction branch the cap sits on
     (`tools.rag.ingest._check_document_pages`). Either way the control
     is inert with the flag off, which is the fact the `meaning` owes an
     administrator. This is a
     narrower case of rule 2 above -- a flag is just another condition a
     control's real effect depends on -- stated separately because a
     flag's absence is easy to miss: nothing about the page itself
     changes shape the way a posture branch does, so there is no
     rendered branch to remind a reader the caveat is missing.

THE GATE CONSTANTS LIVE HERE, and `foundation/settings_area.py` imports
them (an intra-column import, one line). ONE definition, so "admin"
cannot come to mean two things. `HelpCard.gate` is carried as DATA THE
MODEL READS, never as a filter this module applies: the gate-evaluation
logic (`_may_see`) stays in `settings_area.py` and is not duplicated
here. It has NO runtime consumer in v1 -- the drift test is its only
reader -- and that is worth saying so a reviewer does not go hunting for
one. It is carried because a card that did not say who may open its page
would be help text with a hole in it, and because a member-facing variant
would need it on day one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

EVERYONE = "everyone"
ADMIN = "admin"
ACCOUNTS_ADMIN = "accounts-admin"


@dataclass(frozen=True)
class HelpField:
    """One control on a settings page, as the assistant describes it."""

    name: str        # what the page calls it, verbatim
    anchor: str      # the id= on that control's own section, in that page's template
    meaning: str     # what it is, in one or two sentences
    effects: str     # what changes on this box when it changes


@dataclass(frozen=True)
class HelpCard:
    """One settings page, as the assistant describes it."""

    route_name: str              # the SAME url_name `SETTINGS_GROUPS` names
    title: str                   # help title; may be longer than the sidebar label
    gate: str                    # EVERYONE / ADMIN / ACCOUNTS_ADMIN -- data, not a filter
    purpose: str                 # why this page exists, in two or three sentences
    fields: tuple[HelpField, ...]


# ONE CARD PER `SETTINGS_GROUPS` ENTRY, IN THE SAME ORDER. The drift test
# is symmetric, so this table and that one add and retire pages together.
CARDS: tuple[HelpCard, ...] = (
    # --- Setup --------------------------------------------------------
    HelpCard(
        route_name="inference-console",
        title="Models",
        gate=ADMIN,
        purpose=(
            "Where this box learns about the models it can use and decides which one answers "
            "each job. A connection is one reachable model on one model server; a role is a job "
            "this platform needs a model for -- conversation, library answers, library "
            "embeddings, image generation. Nothing on this box runs until at least one role has "
            "a connection assigned to it."
        ),
        fields=(
            HelpField(
                name="In use",
                anchor="in-use",
                meaning=(
                    "Every role this box registers, and which registered connection currently "
                    "answers it. Each row picks a connection for one role."
                ),
                effects=(
                    "Assigning a connection to a role is what makes that surface work: with the "
                    "conversation role unassigned there is no chat, with the embedding role "
                    "unassigned nothing can be added to the library. Changing an assignment "
                    "takes effect on the next job; it never re-runs an old one."
                ),
            ),
            HelpField(
                name="On this machine",
                anchor="on-this-machine",
                meaning=(
                    "Models a supported model server running on this machine already has "
                    "installed, detected automatically -- discovered by talking to the server "
                    "at its default endpoint, or by pressing Scan for model servers to check a "
                    "short list of common addresses when none answers there."
                ),
                effects=(
                    "Pressing Add to registered on a detected model registers it as a connection "
                    "with every setting the model server reported already filled in -- no manual "
                    "form needed. Detection alone changes nothing: a model stays unregistered, "
                    "and unusable by any role, until you add it."
                ),
            ),
            HelpField(
                name="Getting models",
                anchor="getting-models",
                meaning=(
                    "What each role needs from a model, and how to install one on a supported "
                    "model server. This section explains rather than changes anything."
                ),
                effects=(
                    "Nothing. It is the checklist that tells you which capability a role wants "
                    "before you go and install something that has it."
                ),
            ),
            HelpField(
                name="Registered connections",
                anchor="registered-connections",
                meaning=(
                    "Every connection this box knows about, whether or not a role currently uses "
                    "it -- the one editing home for each: name, model ID, endpoint, capability, "
                    "embedding dimension, and a link to the model sets it belongs to."
                ),
                effects=(
                    "Editing a connection here changes it everywhere it is used, immediately. "
                    "Removing a connection that a role is bound to unassigns that role -- the "
                    "role goes back to unassigned rather than falling back to anything."
                ),
            ),
            HelpField(
                name="Memory footprint override",
                anchor="footprint-override",
                meaning=(
                    "An optional, per-connection GB value on that connection's own Edit "
                    "disclosure, overriding this box's own memory measurement for it."
                ),
                effects=(
                    "The execution queue uses this figure, when set, to decide what else can run "
                    "at the same time as this model. Left blank, the queue falls back to "
                    "whatever the engine last measured after actually running it -- which is "
                    "accurate but only exists after a first run, so a model that has never run "
                    "yet plans with no memory estimate at all until either happens."
                ),
            ),
            HelpField(
                name="Model sets",
                anchor="model-sets",
                meaning=(
                    "Named groups of connections, managed on their own page linked from here. A "
                    "set lets an agent or a role be pointed at a curated group rather than one "
                    "fixed connection."
                ),
                effects=(
                    "Changes made on the model sets page take effect wherever that set is "
                    "referenced; nothing on this page writes to a set directly."
                ),
            ),
            HelpField(
                name="Add a connection manually",
                anchor="add-connection",
                meaning=(
                    "Registers a model that is running somewhere this box cannot detect on its "
                    "own -- another machine, or one whose model server does not report its "
                    "capability or embedding dimension."
                ),
                effects=(
                    "Adds a row to Registered connections. It does not assign the connection to "
                    "any role: it becomes available in the role pickers, and stays unused until "
                    "you pick it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="rag-settings",
        title="Library",
        gate=ADMIN,
        purpose=(
            "The limits this box puts on its own document library: how much history it keeps, "
            "how large an upload may be, how long a media file may run, how many pages a "
            "document may have, and how retrieval searches. It sets no permissions -- who may "
            "READ an unlabelled document is decided on Identity & security, not here."
        ),
        fields=(
            HelpField(
                name="Retention",
                anchor="retention",
                meaning="How many recent question-and-answer entries the library keeps.",
                effects=(
                    "Older entries beyond the limit stop being listed. It never deletes a "
                    "document or its chunks -- only the history of what was asked."
                ),
            ),
            HelpField(
                name="Maximum upload size (GB)",
                anchor="upload-limit",
                meaning="The largest single file this box accepts into the library.",
                effects=(
                    "A larger file is refused at upload with an honest message. Files already "
                    "ingested are untouched."
                ),
            ),
            HelpField(
                name="Maximum media duration (minutes)",
                anchor="media-duration-limit",
                meaning=(
                    "The longest audio or video file this box will transcribe. Media is "
                    "transcribed before it becomes searchable text. This limit only has "
                    "anything to enforce while the media feature is enabled -- with it off, "
                    "audio and video are not accepted into the library at all."
                ),
                effects=(
                    "A longer file is refused before any transcription job is queued, so it "
                    "cannot occupy the machine's one execution slot for hours."
                ),
            ),
            HelpField(
                name="Maximum document pages",
                anchor="document-page-limit",
                meaning=(
                    "The largest page count this box will extract text from. In practice this "
                    "only ever caps a scanned or image PDF, and only while the media feature is "
                    "enabled -- an ordinary text PDF is never subject to it, flag on or off."
                ),
                effects=(
                    "A longer document is refused at ingest rather than part-way through, so the "
                    "library never holds half a book."
                ),
            ),
            # S5 (Coherence Wave B): these three used to be ONE bundled
            # `HelpField` ("Retrieval", anchor `retrieval`) sharing one
            # anchor -- the settings-backend audit's own proof that the
            # card->page anchor guard is one-directional and cannot see a
            # field get folded into a neighbour's entry
            # (`test_settings_help.py::TestTheModelFieldCoverage`'s
            # model-field->HelpField assertion is what catches it now).
            # Split into three, each with its own real anchor
            # (`rag/templates/rag/settings.html`'s three `<form id=...>`
            # tags inside the shared `#retrieval` section -- the
            # empty-`<span>` pattern `docs/EXTENDING.md`'s anchor step
            # describes is for a control with no element of its own to
            # carry the id; each of these three already has one, its own
            # `<form>`), so a deep link can land on any one of them
            # individually instead of only on the section as a whole.
            HelpField(
                name="Chunks retrieved per question (top_k)",
                anchor="retrieval-top-k",
                meaning=(
                    "How many chunks are retrieved from the library for each question, 1-50."
                ),
                effects=(
                    "A larger value can surface more context but costs more of the chat "
                    "model's context window -- a value too large for the currently-bound "
                    "model's context window is rejected at save time."
                ),
            ),
            HelpField(
                name="Minimum cosine similarity (score floor)",
                anchor="retrieval-score-floor",
                meaning=(
                    "The minimum similarity a retrieved chunk must reach to be used at all. "
                    "0 means off -- every retrieved chunk is kept regardless of similarity."
                ),
                effects=(
                    "Raising it drops weakly-matched chunks before an answer is generated; "
                    "if every retrieved chunk falls below the floor, the question is answered "
                    "with an honest \"nothing matched\" message and no citations, without "
                    "calling the model."
                ),
            ),
            HelpField(
                name="Hybrid keyword + vector search",
                anchor="hybrid-search",
                meaning=(
                    "Whether Postgres keyword matching runs alongside semantic search, "
                    "concatenating and deduplicating both result sets rather than replacing "
                    "one with the other."
                ),
                effects=(
                    "Flipping this saves the setting immediately, but changes nothing about "
                    "what is actually searched until the index is re-encoded (Models -> "
                    "rag.embed -> Re-encode), which rebuilds the chunk table and re-embeds "
                    "every chunk in the library."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="chat-settings",
        title="Chat",
        gate=ADMIN,
        purpose=(
            "How a conversation's prompt is built on this box. One box-wide operator policy, not "
            "a per-person preference -- it applies to every conversation and every agent, "
            "including delegated ones."
        ),
        fields=(
            HelpField(
                name="Tell the model the date and time",
                anchor="time-aware",
                meaning=(
                    "Adds one line to every conversation's prompt naming the current day, date, "
                    "time and UTC offset from this box's own clock, and stamps each earlier "
                    "message with when it was sent."
                ),
                effects=(
                    "With it off, a model falls back on whatever its training implies \"now\" is "
                    "and can report the current year as a set of future dates. Turning it off "
                    "removes both the date line and the message stamps. The clock reads in this "
                    "box's configured time zone."
                ),
            ),
        ),
    ),
    # F1 (Coherence Wave C). "Job execution", not "Queue": the Queue
    # page is a different, unregistered ACTIVITY surface that still
    # exists under that name, and rule 3 above wants a distinctive
    # title besides -- a one-word "Queue" here would auto-link every
    # exact-case occurrence of the word in any answer, including the
    # many that are about today's jobs rather than about this page.
    HelpCard(
        route_name="jobs-settings",
        title="Job execution",
        gate=ADMIN,
        purpose=(
            "How much of this machine the execution queue may use at once, and what it keeps "
            "afterwards. Five of these six values are obeyed by every queued job on this box, "
            "whatever the job is; the response timeout is different -- it bounds one agent or "
            "chat response, and a document-ingest or image-generation job never reads it at "
            "all. None of the six is a per-job setting and nothing here is about one job. What "
            "is running, waiting or finished right now is on the Queue page, which these "
            "settings govern but which is not part of the settings area."
        ),
        fields=(
            HelpField(
                name="Memory budget (GB)",
                anchor="memory-budget",
                meaning=(
                    "The total memory this box may hold in models at once. Left BLANK it is "
                    "unset, which is not \"unlimited\" -- it means sequential mode: one job at a "
                    "time, whatever its size."
                ),
                effects=(
                    "With a budget set, jobs whose measured footprints fit within it start "
                    "together and anything larger waits its turn. A job whose own footprint has "
                    "never been measured always runs alone. Changing it affects what starts "
                    "next; nothing already running is stopped or re-evaluated."
                ),
            ),
            HelpField(
                name="Max concurrent jobs",
                anchor="max-concurrent-jobs",
                meaning=(
                    "A hard cap on how many jobs may run at the same time, applied on top of "
                    "the memory budget rather than instead of it."
                ),
                effects=(
                    "The queue never starts more than this many at once however small their "
                    "footprints are, so the lower of this and the budget is what actually "
                    "decides. With the budget unset it is the budget, not this, that holds jobs "
                    "to one at a time."
                ),
            ),
            HelpField(
                name="Keep the most recent (finished jobs)",
                anchor="retention-limit",
                meaning="How many finished job records this box keeps.",
                effects=(
                    "Older records beyond the limit are deleted when the next job finishes -- "
                    "not on the spot when the limit is lowered -- and deleted records cannot be "
                    "recovered. It never touches a job that is still running or waiting."
                ),
            ),
            HelpField(
                name="Default priority",
                anchor="default-priority",
                meaning=(
                    "The priority a submission gets when neither it nor its job kind asks for "
                    "one. Lower numbers run first."
                ),
                effects=(
                    "It applies to jobs submitted from now on; nothing already queued is "
                    "renumbered."
                ),
            ),
            HelpField(
                name="Per-principal queue cap",
                anchor="max-queued-per-principal",
                meaning=(
                    "How many queued-or-running jobs one account may hold at once, across "
                    "every job kind. Left BLANK there is no cap."
                ),
                effects=(
                    "A submission that would take an account past the cap is refused at the "
                    "moment it is made, with a message naming the cap -- nothing already "
                    "queued is cancelled, and lowering it never removes a job that is "
                    "already waiting."
                ),
            ),
            HelpField(
                name="Response timeout (seconds)",
                anchor="response-timeout-seconds",
                meaning=(
                    "How long a single agent or chat response may take end to end, from 60 "
                    "to 7200 seconds (1 minute to 2 hours). Default 1800 (30 minutes)."
                ),
                effects=(
                    "This is the ONLY timeout that ends a turn -- the chat model's own inner "
                    "request timeout is built from this same value, so raising it here also "
                    "raises how long the model itself is allowed to take before the platform "
                    "gives up on a response. It applies to the next turn that starts; a turn "
                    "already running keeps whatever limit it started with. Worst case, a turn "
                    "can run up to roughly TWICE this value: the deadline is only checked "
                    "between steps, never during one already in flight, so a single request "
                    "that starts just before the deadline may still run the full length again "
                    "before it is caught."
                ),
            ),
        ),
    ),
    # Chat cluster, feature B. "Agent library", not "Agents": the Access
    # group's own "Agent access" card below is a DIFFERENT page about a
    # different job (which entitlements label a row), and rule 3 above
    # wants a distinctive title besides -- a one-word "Agents" here would
    # auto-link every exact-case occurrence of the word in any answer,
    # and most of them are about agents rather than about this page.
    HelpCard(
        route_name="settings-agents",
        title="Agent library",
        gate=ADMIN,
        purpose=(
            "Every agent on this box in one list -- box-wide ones and every person's own -- "
            "with who can reach it, who owns it, and how many entitlements restrict it. It is "
            "the administrator's view of the same editor a person reaches from their own agent "
            "list; opening a row here opens that one editor, not a second one."
        ),
        fields=(
            HelpField(
                name="Every agent on this box",
                anchor="agent-library",
                meaning=(
                    "A read-only table: name, key, reach (everyone on this box, or only the "
                    "people it is given to), and owner. Nothing is saved on this page -- each "
                    "name is a link into the agent's own editor, which is where reach, the "
                    "prompt and the labels are changed. A fifth column counts the entitlements "
                    "restricting each row, and it appears ONLY once accounts are turned on: "
                    "with accounts off there is nobody for an entitlement to restrict, so the "
                    "count is not asked rather than answered as zero."
                ),
                effects=(
                    "Opening a row from here returns here when it is saved or cancelled. The "
                    "list shows every agent whatever the administrator-content setting says, "
                    "because administering the box's agents is not reading somebody's content: "
                    "an administrator who could not see a row could not turn a runaway agent "
                    "off."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="vision-engine-files",
        title="Engine files",
        gate=ADMIN,
        purpose=(
            "The files the image engine has left on disk -- what it was given to work from, and "
            "what it produced. A housekeeping page: it removes files, and changes no setting."
        ),
        fields=(
            HelpField(
                name="Output folder",
                anchor="output-folder",
                meaning="Images the engine has generated, newest first, with their sizes.",
                effects=(
                    "Selecting files and confirming the delete removes them from disk "
                    "permanently. A generated image that was saved into the library is a "
                    "separate copy and is not affected."
                ),
            ),
            HelpField(
                name="Input folder",
                anchor="input-folder",
                meaning="Files handed to the engine as inputs -- source images for an edit.",
                effects=(
                    "The same permanent delete. Removing an input does not change any image "
                    "already generated from it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="setup-index",
        title="Install guides",
        gate=EVERYONE,
        purpose=(
            "The one page in the settings area that everybody can open, signed in or not, "
            "because it is what a person needs BEFORE they can sign in to a box whose engines "
            "are not running. It explains rather than configures: there is no control on it and "
            "nothing on it writes anything."
        ),
        fields=(
            HelpField(
                name="How models reach farabunker",
                anchor="how-models-reach-farabunker",
                meaning=(
                    "What a model server is, how this box talks to one, and what has to be true "
                    "before a model can be assigned to a role."
                ),
                effects="Nothing. It is reading.",
            ),
            HelpField(
                name="What each feature needs",
                anchor="what-each-feature-needs",
                meaning=(
                    "Per-surface checklist: which role each part of the box needs bound before "
                    "it will work. Shown to administrators only, on a page anybody may open."
                ),
                effects=(
                    "Nothing. It tells you which role is missing; Models is where you assign it."
                ),
            ),
        ),
    ),
    # --- Access -------------------------------------------------------
    HelpCard(
        route_name="identity-users",
        title="Accounts",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The people who may sign in to this box. This whole group of pages exists only once "
            "the box has left the open posture: with no accounts there is nobody to administer, "
            "so the sidebar does not offer it at all."
        ),
        fields=(
            HelpField(
                name="Create an account",
                anchor="create-account",
                meaning=(
                    "Adds one account with a username and a password, optionally an "
                    "administrator. The administrator checkbox is not offered in the personal "
                    "posture, where every account is an administrator already."
                ),
                effects=(
                    "The account can sign in immediately. It owns nothing until it creates "
                    "something, and it holds no entitlements until it is granted some."
                ),
            ),
            HelpField(
                name="The accounts table",
                anchor="account-list",
                meaning=(
                    "Every account, with the controls that act on one: deactivate or reactivate, "
                    "promote to administrator or demote, and set a new password."
                ),
                effects=(
                    "Deactivating an account ends its ability to sign in and leaves everything it "
                    "owns in place. Demoting removes administrator surfaces from it; it does not "
                    "remove what it can read, which entitlements decide."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="identity-groups",
        title="Groups",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "Named sets of accounts, so an entitlement or a share can be given to several people "
            "at once instead of one at a time. A group grants nothing by itself -- it is who, not "
            "what."
        ),
        fields=(
            HelpField(
                name="New group",
                anchor="new-group",
                meaning="Creates an empty group with a name.",
                effects="Nothing changes for anybody until the group has members and a grant.",
            ),
            HelpField(
                name="A group's own controls",
                anchor="group-list",
                meaning=(
                    "For each group: add a member, remove a member, and delete the group. Every "
                    "existing group is listed here, so on a box with no groups yet this section "
                    "is present but empty."
                ),
                effects=(
                    "Adding a member gives that account everything the group holds. Deleting a "
                    "group removes the grants that named it; it never deletes the accounts in it."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="identity-entitlements",
        title="Entitlements",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The labels this box uses to decide who may read what. An entitlement is a name; "
            "labelling a document, a tool or an agent with it narrows that thing to the people "
            "who hold it. Granting one to an account or a group is done from that entitlement's "
            "own page."
        ),
        fields=(
            HelpField(
                name="New entitlement",
                anchor="new-entitlement",
                meaning="Creates a label with a name and a description.",
                effects=(
                    "Nothing is restricted by creating one. It starts mattering when something "
                    "is labelled with it."
                ),
            ),
            HelpField(
                name="The entitlements table",
                anchor="entitlement-list",
                meaning="Every entitlement on this box, each linking to its own page.",
                effects="Reading only. The per-entitlement page is where grants are edited.",
            ),
        ),
    ),
    HelpCard(
        route_name="chat-tool-entitlements",
        title="Tool access",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "Which entitlements a tool requires. An agent declares the tools it wants; this page "
            "decides which of them a given person actually gets. A tool with no entitlement is "
            "callable by everyone signed in."
        ),
        fields=(
            HelpField(
                name="A tool's labels",
                anchor="tool-labels",
                meaning=(
                    "One row per registered tool, each with the set of entitlements that tool "
                    "requires. Saving a row replaces that tool's labels."
                ),
                effects=(
                    "A labelled tool disappears from every agent run by somebody who holds none "
                    "of its entitlements -- and from the watcher and the command line entirely, "
                    "because automated callers hold no entitlements on this box."
                ),
            ),
        ),
    ),
    HelpCard(
        route_name="chat-agent-entitlements",
        title="Agent access",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "The same labelling, one level up: which entitlements an agent or a saved flow "
            "requires before a person may run it at all. Tool access narrows what an agent can "
            "do; this narrows who can start one."
        ),
        fields=(
            HelpField(
                name="An agent's labels",
                anchor="agent-labels",
                meaning="One row per agent on this box, each with the entitlements it requires.",
                effects=(
                    "A labelled agent stops appearing in the picker for somebody who holds none "
                    "of its entitlements -- including the account that created it. Conversations "
                    "they already started stay readable."
                ),
            ),
            HelpField(
                name="A flow's labels",
                anchor="flow-labels",
                meaning="The same, for each saved flow.",
                effects=(
                    "A labelled flow is no longer offered to, or runnable by, somebody who holds "
                    "none of its entitlements."
                ),
            ),
        ),
    ),
    # --- Box ----------------------------------------------------------
    HelpCard(
        route_name="identity-settings",
        title="Identity & security",
        gate=ACCOUNTS_ADMIN,
        purpose=(
            "This box's own security posture: whether it has accounts at all, who may read a "
            "document carrying no label, whether an administrator may read other people's "
            "content, and how long a signed-in session may sit idle. Every one of these is "
            "box-wide, and the posture is the one that decides whether the rest of the Access "
            "group exists."
        ),
        fields=(
            HelpField(
                name="Posture",
                anchor="posture",
                meaning=(
                    "Open, personal or enterprise. Open means no accounts: everybody at the "
                    "keyboard is an administrator and there is nobody for anything to be hidden "
                    "from. Personal means accounts, all of them administrators. Enterprise means "
                    "accounts with ordinary members among them."
                ),
                effects=(
                    "Leaving open is what makes the Accounts, Groups, Entitlements, Tool access "
                    "and Agent access pages appear in the sidebar. Moving to personal resets "
                    "Library posture to open, because there is nobody to lock the library "
                    "against."
                ),
            ),
            HelpField(
                name="Library posture",
                anchor="library-posture",
                meaning=(
                    "Who may READ a document that carries no entitlement label: everybody signed "
                    "in while this is open, and only an administrator with content access while "
                    "it is locked. A labelled document is never affected -- the people who hold "
                    "its entitlements read it either way. THIS IS THE CONTROL THAT MAKES THE "
                    "LIBRARY ADMINISTRATOR-ONLY, and it lives here rather than on the Library "
                    "page, which owns the library's numeric limits and no permission at all. "
                    "How it is OFFERED depends on the posture: it is a real selector on an open "
                    "and on an enterprise box, and on a personal box it is not offered as a "
                    "choice at all -- the page shows it fixed at open, with its own explanation, "
                    "because every account there is an administrator already."
                ),
                effects=(
                    "Setting it to locked stops every ordinary member reading unlabelled "
                    "documents, immediately, on the next request. On an OPEN box it has no "
                    "effect at all -- everybody there already reads everything, so a lock has "
                    "nobody to lock out; it starts meaning something the moment the box leaves "
                    "the open posture."
                ),
            ),
            HelpField(
                name="Administrators may read other users' conversations and files",
                anchor="admin-sees-content",
                meaning=(
                    "Whether an administrator may read other people's CONTENT -- their "
                    "conversations, generated images, document bytes and job payloads -- as "
                    "opposed to merely administering the rows."
                ),
                effects=(
                    "Off by default, and that default is the decision: an administrator already "
                    "sees every row they need to run the box without it. Turning it on is "
                    "audited, and takes effect on the next request without a restart."
                ),
            ),
            HelpField(
                name="Session idle window (minutes)",
                anchor="session-idle",
                meaning=(
                    "How long a signed-in session may sit idle before it expires. Rolling: every "
                    "request resets the clock."
                ),
                effects=(
                    "Zero means the session ends when the browser closes. A change applies from "
                    "the next request; it does not sign anybody out retroactively."
                ),
            ),
        ),
    ),
)


# The separator is a control character, so it cannot appear in a route
# name, a title, a gate, a prose field, or anything else a card holds --
# which is what makes the serialization below UNAMBIGUOUS. A separator a
# field could contain would let one edit cancel another out.
_SEP = "\x1f"


def _content_hash(cards: tuple[HelpCard, ...] | None = None) -> str:
    """`sha256` over a DETERMINISTIC CANONICAL SERIALIZATION of `cards`,
    truncated to 12 hex characters.

    NEVER Python's built-in `hash()`, which is salted per process and
    would answer differently on every boot -- the one mistake that would
    make every job below silently useless.

    Takes the table as an argument, defaulting to `CARDS`, for exactly
    one reason: the anti-vacuous test mutates a COPY and rehashes it. No
    production caller passes anything.

    Twelve hex characters, because this is a CHANGE DETECTOR, not a
    signature. Its three jobs (spec §3.3): the assistant can cite its
    context version, so an answer in a transcript can be tied to the
    content that produced it; it is the key any future cached or derived
    artifact compares against before trusting itself; and a test pins
    that it CHANGES when any card changes, which is what makes the other
    two worth anything.
    """
    if cards is None:
        cards = CARDS
    parts: list[str] = []
    for card in cards:
        parts += [card.route_name, card.title, card.gate, card.purpose]
        for field in card.fields:
            parts += [field.name, field.anchor, field.meaning, field.effects]
        # A per-card terminator, so two adjacent cards cannot be
        # re-partitioned into a different pair with the same joined text.
        parts.append("")
    digest = hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


CONTENT_HASH: str = _content_hash()

# Every route this registry covers, built ONCE at import (final review,
# m3). `card_routes()` used to rebuild this `frozenset` from `CARDS` on
# every call -- one string comparison per card, nothing in practice, but this
# module's own `card_routes()` docstring says membership "must cost
# nothing", and rebuilding a frozenset is not nothing: `settings_assistant`
# (`agents/chat/context_processors.py`) calls `card_routes()` on every
# request in the box, and `_links` calls it again. Hoisted beside
# `CONTENT_HASH`, the module's other precomputed constant, for the same
# reason: `CARDS` does not change at runtime, so nothing is lost by
# computing its derived views once.
_CARD_ROUTES: frozenset[str] = frozenset(card.route_name for card in CARDS)


def card_for(route_name: str) -> HelpCard | None:
    """The card for `route_name`, or `None`. A linear scan over the
    table's frozen rows -- no index, because building one would be a second copy
    of the same table to keep honest."""
    for card in CARDS:
        if card.route_name == route_name:
            return card
    return None


def card_routes() -> frozenset[str]:
    """Every route this registry covers.

    A `frozenset` because its hottest caller is a MEMBERSHIP TEST that
    runs on every page in the box: the panel's context processor checks
    `request.resolver_match.url_name not in card_routes()` before it
    reads anything at all, and that test must cost nothing -- which is
    why this returns the module-level `_CARD_ROUTES` built once at
    import, rather than rebuilding the same frozenset on every call.
    """
    return _CARD_ROUTES


def page_choices() -> tuple[str, ...]:
    """The route names the `settings.card` tool schema enumerates, IN
    TABLE ORDER -- which is sidebar order, not alphabetical. It becomes
    an `enum` in the JSON schema both wire adapters build, so a page name
    a model invents comes back as a parameter error rather than as a
    confident answer about a page that does not exist."""
    return tuple(card.route_name for card in CARDS)
