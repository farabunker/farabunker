# Chat cluster — the browser smoke checklist

**What this is.** Every item the fourteen task reports flagged as *not verifiable by the test
suite*, collected in one place, each as one check with the page it happens on and the pixel
you are looking for. The suite has no browser and no JavaScript engine, so for these items a
green run proves the markup is present and proves nothing about what a reader sees.

**It does not replace the plan's own `## Smoke Checklist`**
([`2026-09-21-chat-cluster.md`](2026-09-21-chat-cluster.md)), which is the feature-level walk —
does the feature work, end to end, for each of the principals it was built for. Walk that
first; this is the per-item detail it leaves implicit.

**Where.** On this branch's own preview stack
(`scripts/preview up <branch> --port <p> --db-port <q>`), never the primary `:8000` stack, and
never against the live box. No success language until the last rung of `docs/DEV.md` §8 is
reached on the owner's own box.

**Before you start:** a conversation with at least three of your own finished messages in it,
one of them in a workstream you may upload to, and a second browser profile (or private
window) signed in as a plain member on an accounts-on box.

---

## A — the meter

- [ ] **The line renders, below the composer.** `/chat/c/<id>/` — one muted line under the
      message box reading an estimate, a ceiling and a percentage, with a bar segment
      coloured for its band. Not overlapping the composer, not clipped at phone width.
- [ ] **The disclosure opens and reads correctly.** Same page — click the small summary beside
      the line; it expands in place to the "how this is worked out" body, names the two
      exclusions a reader can act on, and does not push the composer off screen.
- [ ] **The numbers move on the poller's tick, with no reload.** Same page — send a long
      message and watch: the estimate and the percentage change on the *queued* tick and again
      on the *done* tick, and the page never flashes or scrolls to the top. This is the whole
      of the poller's context path, and the suite can only assert the script's source.
- [ ] **The truncation clause appears and the estimate plateaus.** A conversation of more than
      the replay cap's worth of turns — the line gains a clause naming how many of how many
      messages are no longer sent, and sending more messages stops moving the estimate. This
      is the pixel proof that the meter measures what is *sent*.
- [ ] **The band colours are legible in both themes.** Same line at a low, a high and a
      near-full percentage, in light and dark — all three read against the page background,
      and the near-full one is distinguishable from the high one at a glance.
- [ ] **The `full` band's own sentence arrives WITH the band, not at the next reload.**
      A conversation just under 90% of its ceiling, scripts ON — send a message long enough
      to cross it and watch the poller's tick: the bar turns its danger colour AND the line
      "Start a new conversation to keep the model's full attention." appears under it, in the
      same tick, with no F5. Then send another and confirm it does not appear twice. This is
      the one meter state whose text arrives over the wire rather than being unhidden, and
      the suite can only assert the payload and the script's source.
- [ ] **The two ceiling-free lines read as sentences.** `/chat/c/<id>/` on a conversation
      whose connection declares no window — the line reads an estimate, no percentage and no
      bar fill, ending "· no limit set for this connection". Then the same page for an agent
      whose role is not bound at all: the line reads an estimate with NO trailing clause, and
      the page's existing "unavailable" banner above it is what explains why. Confirm neither
      renders a stray "·" or an empty parenthesis where the percentage would have been.
- [ ] **The engine-default sentence renders for an administrator.** `/chat/c/<id>/` as an
      ADMINISTRATOR, on a conversation whose connection has no window of its own so the
      engine's default applies — the sentence appears under the line with a working "Models"
      link beside it. (The member half of this pair is in section B; this is the positive
      render, which nothing else walks.)

## B — the agent pages

- [ ] **The member's list renders both sections.** `/chat/agents/` as a member — "the agents I
      work on" above, and, if they own one, the read-only box-wide section below it with its
      declared sentence and **no** link on that row.
- [ ] **The administrator's library renders five columns.** `/settings/agents/` on an
      accounts-on box — name, key, reach, owner and a restriction count, under an "Agent
      library" heading and sidebar entry, in the Setup group.
- [ ] **On an open box the restriction column is GONE, not zero.** `/settings/agents/` with
      accounts off — four columns, no "Restrictions" header, no empty fifth cell, and the
      empty-state row spans exactly the columns that are there. Same check on `/chat/agents/`:
      no restriction chip anywhere, not a chip reading "0".
- [ ] **One editor, reached from both lists.** Open the same agent from `/chat/agents/` and
      from `/settings/agents/` — the same page, and Cancel returns to the list you came from,
      not always to the chat one.
- [ ] **The label panel renders as a real two-pane transfer panel.** The agent editor as a
      principal who owns an entitlement — two panes side by side, each a fixed height with its
      own scroll, each with a working type-to-filter box, and the panel's own Save **below the
      field form's Save**, visibly a separate control rather than a nested one.
- [ ] **The transfer panel looks identical on all five consumers.** Its CSS was *moved* from
      the settings shell to the page shell this branch; compare, side by side, an entitlement's
      own page, the **entitlements list page** (whose search box is the one consumer that
      shares only the promoted `.filter-input` rule — so a half-worked promotion shows there
      first), `/chat/tools/`, `/chat/access/`, and the agent editor. Same card chrome, same
      pane height, same filter box.
- [ ] **The reach control is absent from a member's page source.** View source as a member on
      the agent editor — "everyone on this box" appears nowhere in the HTML, not merely hidden
      by CSS. Same for the engine-default sentence on the thread page.
- [ ] **The box-wide section's ADMINISTRATOR half.** `/chat/agents/` as an administrator, on a
      default OPEN box — the same read-only section a member sees renders a DIFFERENT declared
      sentence and a working link into the editor on each of those rows. On an open box the
      administrator is that section's only reader, so this is the common case, and the member
      half above is the one that was walked.
- [ ] **A save from the agent editor, from both mounts.** Open an agent from `/chat/agents/`,
      change its description, Save — the flash names the agent, the list shows the change, and
      you land back on the list you came from. Repeat from `/settings/agents/` and confirm you
      land back THERE. Then add and remove a label through the panel's own Save and confirm the
      same, including the count on the list.
- [ ] **Creating an agent, end to end.** `/chat/agents/new/` — the form renders with no label
      panel (there is no row to label yet), submit it empty and confirm the refusal sentences
      render beside the fields with nothing you typed lost; fill it in, Save, and confirm the
      new row is in the list and usable as a conversation's agent.
- [ ] **The two refusals are refusals, not tracebacks.** With the browser's dev tools, POST an
      unknown `action` to the agent editor — a plain 400 page, not a 500. And POST anything at
      all to `/settings/agents/` — a 405; that page has no write path by design.

## C — editing a past prompt

- [ ] **The disclosure works with JavaScript OFF.** `/chat/c/<id>/` with scripts disabled —
      open "Edit and carry on from here" on one of your own finished messages, edit the text,
      attach a file, submit, and land on the branch. Native `<details>` behaviour, and the
      form legible inside the message bubble at phone width.
- [ ] **Each edit form's "+ Add files" opens ITS OWN picker.** A thread with several editable
      messages — open the **second** disclosure, press its "+ Add files", choose a file, and
      confirm the chip appears in *that* form, not in the composer at the bottom. While there,
      confirm the composer's own drag-and-drop still works and has not latched onto an edit
      form.
- [ ] **"Edit this message" focuses that form's box.** Same thread — open the **third**
      disclosure and click its label; the caret lands in the textarea beside it, not in the
      first one on the page.
- [ ] **A polled `done` swap keeps the picker's selection.** Load a thread with an explicit
      `?connection=<pk>`, send a message, let the poller swap the finished exchange in, then
      open the swapped bubble's disclosure and confirm its hidden connection field carries the
      picked value — not empty. Confirm the swapped disclosure also shows the lead sentence
      and, in a workstream, the three-value placement chooser plus the remember control.
- [ ] **The edit control disappears while an answer is running**, on every message in the
      thread, and comes back when it finishes.
- [ ] **The provenance banner survives a poll cycle.** On a branch — send a message and watch
      the queued → running → done ticks swap the exchange in; the "Branched from …" banner at
      the top never flickers, never duplicates, and never ends up below the thread. On a
      branch of a branch, confirm it names the **immediate** parent.
- [ ] **The provenance banner's two fallback sentences.** Take a branch, then DELETE its
      parent conversation, and reload the branch — the banner reads "Branched from an earlier
      conversation", a whole sentence with no gap where the title was, no link, and NO message
      number. Then, as a second principal who cannot see the parent, open a branch you were
      given: the identical sentence, and the parent's title nowhere in the page source. Both
      are the paths where the banner has the least to say and the most to get wrong.
- [ ] **The banner's number is the one you can count to.** On a branch taken from the third
      message of a thread that had used a tool — the banner reads "at your message 3", not the
      row index, and clicking the link lands on the parent where you can count the bubbles and
      confirm it.
- [ ] **A branch looks like an ordinary thread.** The copied history above, the edited message
      as the newest turn, the answer arriving into it, tool cards rendered **and no audit link
      on the branch**, and the original unchanged when you open it again.
