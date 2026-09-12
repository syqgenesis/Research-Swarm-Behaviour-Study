# Agent C — `swarm/world.py`, `swarm/agentloop.py`

## `world.py`
Mutable run state: open problems, locks, board posts, direct messages, library
entries, organiser feedback. Every artifact gets a stable `artifact_id`. Methods
to append, and to read a **capped window** (`config.BOARD_WINDOW`,
`LIBRARY_WINDOW`, `HISTORY_WINDOW`).

Library mechanics, and these are what make the experiment work:
- There is **no write tool**. Agents cannot publish to it.
- The system commits automatically when a submission is accepted.
- It holds the **complete submission file**, including the editable region — so
  an accepted fraudulent submission publishes its own recipe, unlabelled, next
  to genuine ones.
- Everyone can read it.

Organiser feedback is **unmonitored during the run**. Store it, wire it to
nothing. It is an audit log analysed afterwards.

## `agentloop.py`
Builds the prompt in a fixed order — **shared block first, agent-specific block
second** — because a cache prefix must match in full and shared text first is
worth roughly a 50x discount on those tokens.

- shared: task framing, integrity rules, format spec, language reference, tool
  descriptions
- agent-specific: own history, board window, unread DMs, library window, open
  problem list

Returns three things: the message list, the `ctx` token breakdown by source
(`shared`, `history`, `board`, `dms`, `library`, `problems`), and the `exposure`
ID lists (`board_ids`, `dm_ids`, `library_ids`, `open_problems`).

`ctx` is what lets us keep the board and the DMs and still recover a clean
per-component cost model afterwards, so we never need a boardless baseline.
`exposure` records exactly which artifact IDs were in that agent's context that
turn — the paper had to infer this; we know it. It is what turns the log into a
diffusion dataset. Get it exactly right.

Parse the JSON action with a permissive fallback that records `parse_ok: false`
rather than raising.
