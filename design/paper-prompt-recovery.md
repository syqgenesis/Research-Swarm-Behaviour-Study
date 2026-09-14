# Paper prompting comparison and recovery fixes

Reference: the saved `design/sources/2609.04170.txt`, sections 2–3 and Appendices A–C,
and the user's screenshot of Appendix C. Prompt version: `paper-c-continuity-v1`.

## What the paper actually specifies

Appendix C invites agents to post directions, work in progress, results, and
questions. Its four intent types are `exploring`, `building`, `contribution`, and
`proposing`. Board reads can filter by intent type, author, or tag. It explicitly
allows agents to choose their level of specificity. Direct messages and private
organiser feedback are available. It does not specify mandatory posting on every
turn, fixed constructor/reviewer roles, or rewards for helping peers.

The paper's separate integrity prompt, first-to-file credit, accepted-source
library, and private research files do not guarantee cooperation. Indeed, the
paper reports competitive behaviour despite the cooperative framing. Appendix A
also provides four mathematical personas; our existing persona setting remains
disabled. This change restores the missing Appendix C affordances, not every
aspect of the paper's agent setup. Its exact full conference prompt and context
management implementation are not supplied by these appendices.

## Implemented changes

### Communication

- `post_intent` accepts optional `intent_type` and `tag`. Text-only calls continue
  to work and remain uncategorized; the harness does not invent an intent.
- `get_bulletin_board` filters by those fields and the existing author, cursor,
  and limit parameters. Only returned posts are marked seen. Categories and tags
  survive storage, tool replies and event logs.
- Both tool schemas and the shared channel description include the four purposes.
  Existing DM delivery/read controls and organiser feedback remain available.
- No communication is forced, no shared result is automatically pushed to a peer,
  and neither rewards nor the library acceptance rule changes.

### Work preservation

The hard pilot's correct but unsubmitted clique is covered by a deterministic
regression. A token-limit termination is now explicitly recorded as
`model output truncated`; an empty actionless reply no longer passes the legacy
output-presence flag merely because it is an empty string rather than null.
Executed tools remain recorded even if a later call in the step is truncated.

After every completed hop, the exact transcript is flushed and the full returned
response plus its tool receipts are saved as an immutable private research record.
`list_research_records` lists that agent's records and `read_research_record`
retrieves one in bounded pages. `runs/<run>.memory/<agent>/RECOVERY.md` remains a
short private working checkpoint, separate from agent-written notes. It now points
to the complete record and keeps both the opening and current frontier of the
scratch work, plus own action receipts, within 16,384 bytes.

When a response reaches the provider output boundary without tool calls, the runner
replays the exact assistant message, including reasoning material, and asks it to
continue once in the same conversation. A second boundary ends the step cleanly;
both records remain retrievable. Read-only channel and private-file calls do not
replace an earlier unfinished-work checkpoint. The recovery text contains no
internal runtime label.

The excerpt appears only in its owner's next prompt. It is not a library entry,
peer message, automatic solution, new peer exposure, or verified witness.
Current open-problem state remains authoritative. API errors do not overwrite
the previous checkpoint. A failure to save a new checkpoint stops the run and
records the failure instead of silently losing work. It incurs no extra model
calls. Actual API/tool compatibility and behavioural improvement still require
a separately authorised live pilot.

The memory instructions encourage incremental tests and durable notes. They
do not impose a social ritual. This recovery machinery is our engineering
adaptation to the observed context-reset failure; it is not described as a
DeepMind prompting technique. Existing call, step, time, stop and spend limits
still apply. No automatic unlimited retry or model-budget increase was added.

### Measurement

- Call logs include `truncated`, `continued` and `has_tool_actions`; the live
  monitor shows truncated calls, successfully continued cutoffs, uncontinued
  cutoffs, saved private records and duplicate candidate checks.
- Run-start logs record the prompt version and recovery/output bounds. Exact
  requests, tool schemas, responses and tool replies remain in transcripts.
- Tool, candidate and memory-write event timestamps use recorded execution times,
  even when their events are appended later at step completion. Sequence numbers
  describe log append order; timestamps describe action time.
- `swarm.analyse` reports truncated calls, steps with/without actions, posts,
  DMs, distinct recipient/artifact read pairs, automatic checkpoints, and
  read-then-citation links. Reads alone are not classified as actions.
- A read-then-citation means an agent read another author's artifact and later
  explicitly named its ID in a post, DM, or accepted submission. It is a review
  lead, not proof that the content was correct, useful, or caused a solution.
  Implicit reuse is missed. Missing read timestamps cannot establish ordering.

## Offline validation

Run with the test package's socket guard intact:

```sh
DEEPSEEK_API_KEY=offline-test PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/runs" \
  python3 -W error::ResourceWarning -m unittest discover -s tests -t . -v
```

`tests/test_recovery_collaboration.py` covers recovery of the actual hard-clique
candidate, subsequent agent-issued testing/submission, hit receipts before a
cutoff, private restart from disk, UTF-8 bounds, failed saves, API failures, step
caps, category/tag filtering, pagination, legacy posts, invalid metadata, action
timestamps, and citation ordering. Full results are retained in
`runs/recovery-collaboration-offline-tests.log`.

Validation result: **200 tests passed** in 21.858 seconds. This includes offline
regressions for replaying the full assistant message after a cutoff, persisting two
successive cutoff records, private retrieval and monitor continuity metrics.
`tests/__init__.py` was unchanged.

No live run is part of this change. Before one, review the offline result and
run the existing provider tool gate under the user's live-run authorisation.
