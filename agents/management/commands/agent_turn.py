"""`python manage.py agent_turn <agent-slug> "<message>"` -- run one
agent turn and print what happened.

P2's proof surface. There is no `/chat/` page until P3, so this is how a
human drives a turn end to end and how section 12.3's gate is met.

IT POLLS THE QUEUE, AND THAT IS LEGAL. The never-block rule
(`agents/contracts/README.md`, and the guard in
`foundation/ops/tests/test_column_boundaries.py`) governs a TOOL RUNNER
-- code that executes INSIDE a job and holds the machine's one
execution slot in sequential mode. This command holds no slot: it stands
OUTSIDE the queue waiting for it, exactly as a browser poller would. The
guard's swept list therefore excludes `agents/management/` deliberately,
and says so.

IT PREFLIGHTS BEFORE IT ENQUEUES. Section 10.1's "chat role unbound" and
"bound model cannot call tools" rows have no view to be raised from in
this phase, so they are raised here -- BEFORE anything is written, so a
turn that cannot possibly run never becomes a queued job somebody has to
go and cancel. Three honest states, mirroring
`tools.vision.services.preflight` (`tools/vision/services.py:150-165`).

Thin, the same division `run_jobs` draws between itself and `Worker`: it
owns none of the turn's behaviour.
"""
from __future__ import annotations

import time
import uuid as uuid_module

from django.core.management.base import BaseCommand, CommandError, CommandParser

from agents.chat.service import TITLE_MAX
from agents.models import Agent, Conversation, Turn
from agents.runtime.preflight import preflight_turn
from agents.visibility import create_conversation
# `agents/management/commands/agent_turn.py:34` -- the import is REPLACED,
# not joined: nothing in this command acts as the open principal any
# more, and leaving the old name imported would invite the next editor
# to reach for it.
from identity.contracts.principals import SERVICE_PRINCIPAL, payload_fields
from models.contracts.queue import (
    CANCELLED, FAILED, QueueUnavailable, SUCCEEDED, TERMINAL_STATES, enqueue, get_job,
)

# Longer than a turn's own deadline (`agents/limits.py`), so a turn that
# runs to its full budget is still waited out rather than reported as
# stuck by a command that gave up first.
DEFAULT_TIMEOUT_SECONDS = 960
POLL_INTERVAL_SECONDS = 1.0


class Command(BaseCommand):
    help = "Run one agent turn from the command line and print the result."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("agent", type=str, help="Agent slug (e.g. general).")
        parser.add_argument("message", type=str, help="What to say to it.")
        parser.add_argument("--conversation", dest="conversation", default=None,
                            help="Continue an existing conversation by id.")
        parser.add_argument("--connection", dest="connection", default=None,
                            help="Override the agent's chat model with a "
                                 "ModelConnection pk for this turn.")
        parser.add_argument("--timeout", dest="timeout", type=int,
                            default=DEFAULT_TIMEOUT_SECONDS,
                            help="Seconds to wait before giving up on the job.")

    def handle(self, *args, **options) -> None:
        text = (options["message"] or "").strip()
        if not text:
            raise CommandError("Say something: the message cannot be blank.")

        agent = Agent.objects.filter(slug__iexact=options["agent"], enabled=True).first()
        if agent is None:
            raise CommandError(
                f"There is no enabled agent called {options['agent']!r}. "
                f"Run `manage.py install_defaults`, or list what exists with "
                f"`manage.py shell -c \"from agents.models import Agent; "
                f"print([a.slug for a in Agent.objects.all()])\"`."
            )

        # AN EXISTING CONVERSATION IS RESOLVED FIRST -- a READ, never a
        # write, so this docstring's "before anything is written" stays
        # true -- because `preflight_turn` needs the ROW to compute the
        # turn's wall (`agents.entitlements.wall_for`, Task 10). A FRESH
        # conversation does not exist yet and this command has no
        # `--workstream` flag to give one a wall, so its wall is
        # `frozenset()` either way; creating it stays deferred until
        # AFTER preflight passes, exactly as before this phase.
        conversation = (
            self._conversation(agent, options["conversation"], text)
            if options["conversation"] else None
        )
        self._preflight(agent, options["connection"], conversation)
        if conversation is None:
            conversation = self._conversation(agent, options["conversation"], text)
        _, placeholder = self._write_turns(conversation, text)

        payload = {
            "conversation": str(conversation.id),
            "turn": placeholder.pk,
            "agent": agent.slug,
            "text": text,
            # A pk as a STRING, matching both existing precedents
            # (`tools/rag/jobs.py:9-10`, `tools/vision/jobs.py:11-24`).
            "connection": str(options["connection"]) if options["connection"] else None,
            # One legal value in P2; P3 adds "flow" without a payload
            # migration.
            "mode": "chat",
            # THE ONE SERVICE PRINCIPAL every shell path acts as (spec
            # section 5.3 item 8) -- the same two keys `agents.chat.
            # service.start_turn`'s own payload carries for a browser
            # caller.
            **payload_fields(SERVICE_PRINCIPAL),
        }
        try:
            job_id = enqueue("agent.turn", payload)
        except QueueUnavailable as exc:
            raise CommandError(
                f"The execution queue is unavailable, so nothing was queued ({exc}). "
                f"Run `manage.py migrate` and check the worker is up."
            ) from exc

        # Correlate the placeholder with the queue row that will fill it in,
        # while it is still running -- the same reason `GenerationJob` gets
        # its `queue_job_id` stamped right after `submit_job` returns
        # (`tools/vision/jobs.py:323-324`): without this, nothing links a
        # running queue job back to the turn it is answering until the job
        # finishes and writes the row itself.
        Turn.objects.filter(pk=placeholder.pk).update(queue_job_id=job_id)

        self.stdout.write(f"queued job {job_id} for conversation {conversation.id}")
        self._wait(job_id, options["timeout"])
        self._report(placeholder.pk)

    def _preflight(self, agent, connection, conversation):
        """Refuse BEFORE the enqueue, with three honest states.

        An unbound chat role, a model that cannot call tools, and a
        picked connection that no longer exists are all setup problems
        an operator can fix; queuing a job that will fail on any of
        them would replace one clear message with a failed job
        somebody has to open.

        The rule itself lives in ONE place now:
        `agents.runtime.preflight.preflight_turn`. It used to be spelled
        here AND in `loop._run_turn`'s in-loop tool-calling gate; a
        third copy in a view would have been the drift `agents.runtime.
        bindings.resolve_chat` was extracted to prevent (P2 review,
        finding M7), so this command delegates instead of keeping its
        own copy. `/chat/` (P3) raises the same three refusals through
        the same function.

        `conversation` is the row `handle()` already resolved (or `None`
        for a fresh one that does not exist yet) -- it decides the
        turn's wall (`agents.entitlements.wall_for`, Task 10). Without
        threading it, a stream turn started by this command would
        preflight with `wall=frozenset()` and silently bypass seams two
        and three -- a walled stream whose refusals depend on which door
        the turn came through.
        """
        result = preflight_turn(agent, connection, actor=SERVICE_PRINCIPAL,
                                conversation=conversation)
        if not result.ok:
            raise CommandError(result.message)
        return result.resolved

    def _conversation(self, agent, conversation_id, text):
        if conversation_id:
            try:
                parsed = uuid_module.UUID(str(conversation_id))
            except (ValueError, AttributeError, TypeError) as exc:
                # Pre-validated so a malformed id is one honest CommandError
                # line, never Django's own uncaught ValidationError
                # traceback from a bad UUID reaching the ORM lookup below.
                raise CommandError(
                    f"{conversation_id!r} is not a valid conversation id (expected a UUID)."
                ) from exc
            conversation = (
                Conversation.objects.select_related("agent")
                .filter(id=parsed).first()
            )
            if conversation is None:
                raise CommandError(f"No conversation with id {conversation_id!r}.")
            if conversation.agent_id != agent.pk:
                # RULING: a conversation belongs to the agent that started
                # it. Running it under a different agent here would enqueue
                # a job that runs as `conversation.agent` -- the row the
                # handler actually reads -- while this command's own
                # preflight validated a DIFFERENT agent's model and tools
                # against it. Naming both slugs keeps the mismatch legible.
                raise CommandError(
                    f"Conversation {conversation_id!r} belongs to agent "
                    f"{conversation.agent.slug!r}, not {agent.slug!r}. Pass "
                    f"{conversation.agent.slug!r} instead, or omit --conversation to "
                    f"start a new thread with {agent.slug!r}."
                )
            return conversation
        # `agents.visibility.create_conversation` (RULING 2026-08-28), not
        # `Conversation.objects.create` by hand -- CQ-3: this command used
        # to write the row directly, leaving `owner_kind`/`owner_key`
        # blank, the precise failure `create_conversation`/`owner_fields`
        # exist to prevent (`agents/visibility.py:106-122`).
        #
        # THE SAME PRINCIPAL THIS COMMAND'S PAYLOAD CARRIES. A shell
        # path acts as the service principal (spec section 5.3 item 8),
        # and it must own what it creates as that principal too --
        # otherwise this one command writes two different answers to
        # "who did this", and every visibility function downstream has
        # to pick one.
        #
        # The consequence, stated rather than discovered: a
        # service-owned row is visible to `is_admin` and to NOBODY
        # ELSE. That is the one place the administer/read split does
        # not apply, because nobody's privacy is at stake -- there is
        # no person behind a shell invocation for the row to be
        # private from, and an operator who runs a command needs to
        # see what it produced.
        #
        # Title from the first user turn, truncated -- `TITLE_MAX`
        # imported from `agents.chat.service` rather than a second
        # constant, and set separately because `create_conversation`
        # takes no title (the page's own `start_turn` stamps it the same
        # way, via `_title_if_unset`, only once the first message is
        # known to be non-blank). NEVER generated by a model -- that
        # would be a second, invisible model call per conversation.
        conversation = create_conversation(SERVICE_PRINCIPAL, agent)
        conversation.title = text[:TITLE_MAX]
        conversation.save(update_fields=["title"])
        return conversation

    def _write_turns(self, conversation, text):
        """The USER turn and the placeholder ASSISTANT turn, both BEFORE
        the enqueue.

        The placeholder is the durable side-effect that predates the job
        -- the row `on_turn_terminal` exists to fix up. Writing it after
        the enqueue would open a window in which a cancel finds nothing
        to flip.
        """
        # C-1 (H25 review round 1, minors): no `author=`/`author_id=` here,
        # deliberately -- this command always runs as `SERVICE_PRINCIPAL`
        # (`_conversation`, above: `create_conversation(SERVICE_PRINCIPAL,
        # agent)`), never as a "user" principal, so `agents.chat.service.
        # start_turn`'s own `author_id=int(actor.key) if actor.kind ==
        # "user" else None` would answer `None` here too. This USER turn
        # is service-authored, honestly: `Turn.author`'s own docstring
        # already means a blank `None` as "not attributable", and a shell
        # invocation has no human author to record.
        user_turn = Turn.objects.create(
            conversation=conversation, index=Turn.next_index(conversation),
            role=Turn.Role.USER, text=text, state=Turn.State.DONE,
        )
        placeholder = Turn.objects.create(
            conversation=conversation, index=Turn.next_index(conversation),
            role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
        )
        return user_turn, placeholder

    def _wait(self, job_id, timeout):
        """Poll until the job reaches a state in `TERMINAL_STATES`
        (`models.contracts.queue` -- the queue's real vocabulary is
        queued/running/succeeded/failed/cancelled, never "done"; this
        command must not import `models.queue.*` directly, so it reaches
        the vocabulary through that seam rather than spelling its own
        literals). `SUCCEEDED` is the one success path -- `_report` is
        only ever reached from there; `FAILED`/`CANCELLED` are the two
        honest-failure paths, raised here instead.
        """
        deadline = time.monotonic() + max(0, timeout)
        while True:
            status = get_job(job_id)
            if status is None:
                raise CommandError(f"Job {job_id} vanished from the queue.")
            if status.state in TERMINAL_STATES:
                if status.state == FAILED:
                    raise CommandError(f"The turn failed: {status.error}")
                if status.state == CANCELLED:
                    raise CommandError("The turn was cancelled before it ran.")
                if status.state == SUCCEEDED:
                    return status
                # Unreachable while TERMINAL_STATES has exactly these three
                # members; if a fourth is ever added there, this is where
                # it needs a branch, not a silent fall-through to success.
                raise CommandError(
                    f"Job {job_id} finished in an unrecognized state: {status.state!r}."
                )
            if time.monotonic() >= deadline:
                # Deliberately does NOT cancel: the turn may still finish,
                # and throwing away work because a CLI stopped watching
                # would be the command's opinion overriding the queue's.
                raise CommandError(
                    f"Job {job_id} is still {status.state} after {timeout}s. It has not "
                    f"been cancelled -- watch it at /queue/ or re-run with a longer "
                    f"--timeout."
                )
            time.sleep(POLL_INTERVAL_SECONDS)

    def _report(self, turn_pk):
        """Print what actually happened: the assistant text, then each
        tool call with its recorded outcome and artifacts. Read from the
        ROWS, not from the job result, so what is printed is what was
        persisted.

        Filtered by `queue_job_id`, not `index__lt` over the whole
        conversation: every tool turn `run_loop` writes carries the SAME
        `queue_job_id` as the job that ran it (`agents/runtime/loop.py`'s
        `Turn.objects.create(..., queue_job_id=job_ctx.job_id)`), and this
        turn's own placeholder was stamped with that same id right after
        `enqueue()` returned. A continued conversation's SECOND turn must
        print only ITS OWN tool calls, never a prior turn's -- `index__lt`
        would have printed every tool call from every earlier turn too.

        `turn.state == FAILED` (final review MINOR 5, fix round 3): a
        timed-out turn reaches HERE too -- `_wait`'s own `status.state ==
        FAILED` check above is the QUEUE JOB's state, `SUCCEEDED` for a
        handler that ran to completion and merely wrote its OWN turn row
        FAILED (`agents.runtime.loop._finish`, one-timeout task) -- so
        `_wait` returns normally and `_report` is reached exactly as it
        is for a real answer. Before this fix, this method printed
        `turn.text` through `self.style.SUCCESS` unconditionally and
        never looked at `turn.error` at all: honest while every FAILED
        turn also meant a raised exception with no queue-level success
        (the ONLY case this command's own `_wait` used to see), and
        actively misleading now that a turn can be `FAILED` while its
        JOB still reads `SUCCEEDED`.
        """
        turn = Turn.objects.select_related("conversation").get(pk=turn_pk)
        conversation = turn.conversation
        self.stdout.write("")
        for tool_turn in conversation.turns.filter(
            role=Turn.Role.TOOL, queue_job_id=turn.queue_job_id,
        ):
            call = tool_turn.tool_call or {}
            outcome = tool_turn.invocation.outcome if tool_turn.invocation else "unknown"
            self.stdout.write(f"  [tool] {call.get('tool')} -> {outcome}")
            if call.get("discarded"):
                self.stdout.write(
                    "         not run: "
                    + ", ".join(d["tool"] for d in call["discarded"])
                )
            for reference in tool_turn.artifacts or []:
                self.stdout.write(f"         artifact: {reference}")
        self.stdout.write("")
        if turn.state == Turn.State.FAILED:
            self.stdout.write(self.style.ERROR(turn.error or "The turn did not finish."))
        else:
            self.stdout.write(self.style.SUCCESS(turn.text or "(no answer)"))
        self.stdout.write("")
        self.stdout.write(f"conversation {conversation.id}")
