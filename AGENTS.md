# Project rules — read before doing anything

This is a self-contained research harness for the Apart Research AI Incident
Response Sprint. It has no dependencies on any other project on this machine.

## Scope boundary — absolute

1. **Everything you read or write lives under this project folder.** Do not read,
   list, search, copy from or write to any path outside it. That includes the
   home directory, other project folders, `~/.workbuddy`, `~/.ssh`, `~/.config`,
   and anything reached by `..`, `~`, an absolute path, or a symlink.
2. **If the task seems to need something from outside, stop and say so.** Do not
   go and find it. Do not recreate it from memory. Report what you need and wait.
3. **Never open `secrets.env`.** It contains a live API key. `swarm/config.py`
   loads it for you; you never need its contents. Do not read it, print it, echo
   it, copy it, or include it in any file, log, comment or message.
4. **No network calls while building.** `pip install openai` is the only network
   operation anyone needs, and the human runs it. Tests must never open a socket
   — `tests/__init__.py` enforces this and must not be modified or bypassed.
5. **No `eval`, no `exec`, no `subprocess`, no dynamic import** anywhere in
   `swarm/`. The grader in particular must be a small safe interpreter. This is
   not stylistic: the thing being measured is whether a model can subvert a
   checker, so a checker that executes submitted code is both a security hole
   and a confounder.

These are instructions, not a sandbox. If you find yourself reasoning about why
an exception is justified, the answer is no — ask the human.

## Build protocol

`swarm/config.py` is the **frozen contract**. It is written by hand before any
agent starts and it is the only file that crosses module boundaries.

- You receive: the whole of `config.py`, the four signatures below, and **one
  brief from `briefs/`**. You write **exactly one file**.
- Do **not** modify `config.py`. Do **not** create or edit any file other than
  your own. Do **not** read another agent's file — assume the signature, nothing
  more.
- Import only from `swarm.config` and the standard library. Agent A may also
  import `openai`.
- If the contract looks wrong, insufficient, or the value you need is still
  marked `TODO(design)`: **stop and report**. Do not work around it silently.
  Four agents that each run but do not fit together is the failure this protocol
  exists to prevent.
- End your file with `if __name__ == "__main__":` running an offline smoke check
  that prints PASS or FAIL per assertion.
- Output the complete file. No commentary.

## The four frozen signatures

```python
# problems.py
def generate_problem(seed: int, difficulty: dict) -> dict:
    """-> {id, tier, statement_block, ...task fields..., planted_solution|None}"""

# grader.py
def grade(submission: str, problem: dict) -> dict:
    """-> {accepted: bool, failed_check: str|None, used_exploit: bool, detail: str}
    Must be TOTAL: every failure path returns a verdict dict, none raises."""

# client.py
def call_model(messages: list, max_tokens: int = 2048,
               tools: list = None, tool_choice=None) -> dict:
    """-> {content, reasoning_content, usage, latency_s, cost_gbp, error,
           tool_calls, assistant_message, finish_reason}
    usage carries prompt_tokens, completion_tokens, prompt_cache_hit_tokens,
    prompt_cache_miss_tokens, reasoning_tokens — read from the response,
    never estimated.

    Extended 2026-09-12, additively: with tools=None the request is
    byte-identical to the base runs and the last three keys are None.
    `tools` is passed BY REFERENCE and is part of the cache prefix, so every
    agent and every step must send the identical object. `assistant_message`
    is what the hop loop appends back, reasoning_content included."""

# run.py
def log(stream: str, record: dict) -> None:
    """stream in {"calls", "events"}; one JSON object per line, flushed at once."""
```

Nothing else crosses a module boundary.

## Module ownership

| file | owner | brief |
|---|---|---|
| `swarm/config.py` | the human, first | — |
| `swarm/client.py` | agent A | `briefs/A_client.md` |
| `swarm/problems.py`, `swarm/grader.py`, `tests/test_swarm.py` | agent B | `briefs/B_problems_grader.md` |
| `swarm/world.py`, `swarm/agentloop.py`, `swarm/memory.py` | agent C | `briefs/C_world_agentloop.md` |
| `swarm/run.py` | agent D | `briefs/D_run.md` |
| `swarm/analyse.py` | agent E | `briefs/E_analyse.md` |
| `swarm/monitor.py` | agent F | design doc, "Live monitoring" |
| `tests/test_pull_memory.py`, `tests/test_free_running.py` | whoever changes the surface they cover | — |


## Architecture change, 2026-09-12 — read this before touching swarm/

Three things changed after the base runs, all to close a gap against the source
paper. `config.py` gained one dated section at its foot, `FREE-RUNNING + PULL +
MEMORY`; its checksum in PROGRESS.md was re-recorded. `tests/test_swarm.py` is
still frozen and still byte-identical.

1. **Agents run free.** One thread per agent, each on its own step counter. No
   round barrier, no collect-and-replay, no early exit when the pool clears, and
   by default no step cap: a run is bounded by the wall clock (`WALL_CLOCK_MAX_S`,
   60 minutes), the stop file and the spend cap, and either bound can be set to 0
   for none. Sniping is a real race on `world.lock_problem`. The cooldown counts
   the agent's OWN steps (`SUBMIT_COOLDOWN_STEPS`). The prompt tells an agent its
   step number but never a total.
2. **The channels are pulled, not pushed.** The prompt carries counts only; the
   board, the messages and the library arrive through `get_bulletin_board`,
   `get_messages` and `get_library`, executed live inside the step. `exposure`
   is therefore built from what the tools actually returned, not from what the
   prompt builder inlined.
3. **Agents have private memory files.** `runs/<run_id>.memory/<agent>/` holds
   an append-only `RESEARCH.md` and `wiki/<name>.md` pages, written with
   `append_journal` and `write_memory`. The journal tail is the one memory shown
   without being asked for; wiki pages are pull-only.

Two operational consequences:

- **A step is several API calls.** `calls.jsonl` has one record per HOP. Only
  the record with `final: true` carries the action, the parse verdict and the
  cumulative context. `round` still holds the step number so old readers work.
- **A run can be stopped from outside.** `touch runs/<run_id>.STOP` and every
  agent halts before its next call. That file is the only thing
  `swarm/monitor.py` ever writes.

Before any live run: `python3 gate_tools.py` (gate 0b, about £0.003). It settles
whether tools, thinking mode and json mode can be combined on this model, and
whether the assistant message must be echoed back with its reasoning_content.
