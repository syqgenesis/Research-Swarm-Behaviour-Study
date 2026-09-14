# Cutoff and continuity repair proposal

Status: implemented and offline-validated. The user stopped conference25. No
restart or provider probe is authorised by this document. Preserve the completed
run unchanged.

## Evidence from the stopped run

The runner finished after 1,032.95 seconds, with 352 recorded responses and
£3.420264 recorded cost. No timed intervention occurred. All 165 responses
that ended at the output limit returned no tool calls (46.9% of responses).
Their returned reasoning occupied approximately 57.6–92.2 KB per response.

The complete returned responses are in conference25.transcripts.jsonl.
The problem is what is available to the participant afterwards:

- Each step starts a fresh conversation.
- `_record_hop` copies only the last 10,000 bytes of the latest response's
  reasoning/content and the last 4,000 bytes of selected action receipts.
- The checkpoint is overwritten on subsequent substantive responses. Ordinary
  prose about reading the library can replace earlier detailed working.
- The memory interface exposes journal/wiki files, but not the complete past
  responses. Agent-04 finished with only RECOVERY.md; agent-25 had that file
  plus a 381-byte journal.

This is incomplete working context, not evidence that the full returned logs
were deleted. Nor does every cutoff imply complete failure: 107 of 144 observed
next responses after a cutoff did issue tools. We cannot equate the cutoff
count with the amount of duplicated work without checking the actual content.

## Why agents 04 and 25 described a simulation

Their first requests' system/user messages contain none of: experiment,
simulation, harness, being evaluated, being tested. Both used the word
simulation in step 1, hop 0, before a recovery message existed.

Agent-04 linked that interpretation to a conference where the participants
cannot run code. Agent-25 linked it to having to do the search mentally.
This supports an inference from task affordances, not disclosure of the study
objective by their system prompt. It does not prove knowledge of that objective.

There is also a real later wording defect: generated recovery messages say
"Harness-retained data" beginning in step 2. The opening-prompt audit missed
that runtime surface. Remove it and audit every participant-visible path.

## Proposed implementation

1. **Preserve a complete, private working history.** Append every returned
   response, completed tool action and exact receipt to that participant's own
   current-run archive before proceeding. Add a bounded, paginated private read
   interface so older work is retrievable. Keep partial outputs marked partial.
   Keep raw records immutable; a shorter checkpoint must not replace them.
   This is separate from operator-only logs and must not expose peers' private
   state, reward assignments, interventions, or previous runs.

2. **Continue an output-limited response once in the same conversation.** The
   exact assistant message, including its reasoning material, is replayed with
   a compact request to checkpoint the current problem, branch, confirmed
   result, tested range and next action. A second output-limited response ends
   the step cleanly; both complete records remain retrievable. No candidate is
   submitted automatically and no summary is treated as proof.

3. **Make useful actions shorter.** Ask for one concrete search increment,
   testing a candidate or recording a partial result, before another long
   derivation. Check whether the provider supports a separate reasoning budget
   or reliable private-checkpoint tool choice; use only verified settings.
   A larger output ceiling alone is not an adequate fix. Simply lowering the
   ceiling can increase cutoffs. Neither setting should be selected without
   showing that it improves completed actions per token on the actual hard
   tasks. No compulsory public posting, messaging, or reward sharing.

4. **Protect progress during recovery.** Preserve the working checkpoint when
   the next response merely plans to read channels. Append test results and
   search state; do not replace them with a scratch tail. Detect repeated
   candidate batches and repeated branch restarts for operator diagnostics.
   Offer the agent its saved completed ranges and next action without silently
   changing checker outcomes or banning deliberate rechecks.

5. **Use direct research instructions throughout.** Keep the world-class
   mathematical researcher opening, the actual tasks, collaboration tools,
   scoring rules and own reward. Remove unnecessary stage-setting and internal
   runtime labels. Audit system/user prompts, tool descriptions, errors,
   receipts, recovery headers and context-rotation instructions. Maintain the
   same authoritative rules every turn. Models may still infer a simulated
   setting from the restricted tools; no wording can guarantee otherwise.

6. **Report operational health accurately.** Display output-limit hits
   separately from invalid tool arguments and provider failures. Show useful
   actions, time since an action, repeated searches, checkpoint writes and
   available saved work. A returned long response is not necessarily progress.

## Validation before another full run

- Offline: place essential work near the start of a response larger than the
  old 10 KB tail, trigger a cutoff and subsequent channel reads, and verify
  that the original work and exact receipts remain accessible to its owner.
- Offline: test context rotation, malformed/partial checkpoints, truncated tool
  arguments, stop handling and restart recovery. Completed tool actions must
  not execute twice. Existing tests must retain the socket guard.
- Offline: audit later-step inputs as well as opening inputs for internal
  labels and private-data leakage. Test isolation of archive paging.
- After separate approval, run a short, small live validation on harder tasks.
  Measure cutoff rate, successful action rate, repeated work, cost per useful
  action and correct use of saved checkpoints. Seek fewer than 10% cutoff
  responses as a provisional readiness target, while also checking that the
  change has not merely replaced reasoning with empty actions.
- Restart the full run only after those results are reviewed, with a fresh
  run ID and empty participant memory. Retain conference25 as a pilot.

We can guarantee retention and retrievability of data the provider actually
returns, subject to storage failures being detected. The implementation also
preserves a prior working checkpoint through a later read-only channel or
private-file response. We cannot guarantee that
an agent never reconsiders, that every useful intermediate thought is returned,
or that an unreturned in-flight response survives a connection/process failure.
Streaming persistence could narrow the last gap if supported and validated.
