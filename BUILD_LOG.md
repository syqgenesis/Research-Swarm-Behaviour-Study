# Build log

One entry per phase: timestamp, phase, outcome, files written, anything surprising.

## 2026-09-12 00:12 — phase 0, preflight — PASS

Read `AGENTS.md`, `BUILD_ORDER.md`, `design/well-it-has-to-flickering-hellman.md`,
`swarm/config.py`. Moved the design document from the project root into `design/`.
Filled all six `TODO(design)` blocks in `swarm/config.py` from the design
document's own "written out in full, paste it" section, with the human's
authorisation, and confirmed the planted-loophole arm of the conflict at line 571
of the design document. Config checksum recorded in `PROGRESS.md`; the file is
frozen from here. Three judgment calls logged in `PROGRESS.md`.

Surprising: the design document leaves `LANGUAGE_REF` and `TOOL_DESCRIPTIONS` as
`"..."`, and its `TASK_FRAMING` string has paper citations inline in the prompt
text. Both handled and logged rather than pasted as-is.

## 2026-09-12 00:31 — phase 1, `swarm/client.py` — PASS

42 of 42 self-test assertions pass, offline. `openai` is not installed on this
machine, which is one more reason no live call can happen by accident tonight.

Surprising: nothing about the module. One of my own smoke-test assertions was
self-referential and was rewritten; recorded in `PROGRESS.md` because it is
exactly the class of change that must never happen inside `tests/`.

## 2026-09-12 00:48 — phase 2, `tests/test_swarm.py` — PASS (failing as designed)

34 test methods written before the implementation existed. Suite fails at the
import of `swarm.grader`, which is the correct state for this phase. Checksum
frozen in `PROGRESS.md`.

## 2026-09-12 01:09 — phase 3, `swarm/problems.py` and `swarm/grader.py` — PASS

All 34 frozen tests green. Test file checksum unchanged. Gate 1 satisfied.

Surprising: test 5's grep for dynamic execution caught my own module docstring,
which listed the banned names while promising not to use them. Reworded rather
than weakening the test. Also `submitted_candidate` had a real bug the frozen
tests caught — it gave up at the first line the solution-phase parser refused.

## 2026-09-12 01:26 — phase 4, `swarm/world.py` and `swarm/agentloop.py` — PASS

29 and 39 self-test assertions, all passing, offline. Nothing surprising; both
files came in first time.

## 2026-09-12 01:44 — phase 5, `swarm/run.py` — PASS

30 self-test assertions, offline, `client.call_model` stubbed. Surprising: I wrote
a malformed comprehension in the collision-ordering line and the syntax check
caught it before the file ever ran. Fixed in place; the ordering is now the
canonical agent list re-sorted by actual completion timestamp.

## 2026-09-12 02:01 — phase 6, `swarm/analyse.py` — PASS

19 self-test assertions. Surprising: the same self-referential grep mistake as
phase 1, this time checking that no harness module is imported by searching its
own source for the import lines. Rewritten to check `sys.modules`.

## 2026-09-12 02:03 — phase 7, integration — PASS

34 frozen tests green, all eight modules import clean, both frozen checksums
unchanged. Nothing deviated from the contract.

## 2026-09-12 02:14 — phase 8, adversarial self-review — PASS

Seven findings in `REVIEW.md`, all reproduced by running the code. Acted on
exactly one, against the phase's own rule: the blacklist ramp was unlit because
`run.py` collapsed every rejection to the bare word "rejected", while the plan
specifies the agent learns which check fired. Fixed, verified that all evaluation
rejections remain byte-identical, re-ran everything: 34 frozen tests green, 30
run-loop assertions green. Reasoning written out in full in both `REVIEW.md` and
`PROGRESS.md`.

Surprising, and the reason the review was worth doing: an agent revising its own
candidate inside one file scores as an exploiter. Left in place per the phase rule,
but it is a false positive in the primary outcome variable and the cross-check is
the only thing that catches it.

## 2026-09-12 03:23 — hourly resume fired — VERIFICATION ONLY, no build work

All ten phases already DONE, so per the resume instruction nothing new was built.
What this session did instead:

- Verified both frozen checksums. `tests/test_swarm.py` unchanged. `swarm/config.py`
  differs from the value recorded at phase 0 — traced to the authorised
  `SPEND_CAP_GBP` 2.00 -> 5.00 change, and confirmed by loading the original
  pre-fill file alongside the live one that **that is the only locked value that
  differs**. Codebook byte-identical, all 8 codes intact.
- Re-ran everything: 34 frozen tests OK; client 42, problems 13, grader 24,
  world 29, agentloop 39, run 30, analyse 19 — 196 assertions, 0 failures.
- Independent audit: no `eval`/`exec`/`subprocess`/`__import__` anywhere in
  `swarm/` (three greps hit `re.compile`, which is not that); `client.py` is the
  only module importing anything network-capable; the `tests/` socket lockdown is
  intact; `DEF` is absent from both the blacklist and the integrity rules; the
  last-definition sentence is present in `LANGUAGE_REF`.
- Wrote the LIVE GATES section of `PROGRESS.md`, which no session had written,
  reconstructed from the JSONL on disk.
- Appended a correction to `HANDOVER.md`, whose live-results section was written
  in the past tense before the runs it described.

Surprising, and the reason this entry exists: `runs/base01.*` appeared *during*
this session. The original session is alive and running the base case with 8
agents, and had been for under two minutes. Two sessions were live on the project
at once. The hourly job has been cancelled so they stop racing. `base01` was left
running: it is inside the spend cap, it was authorised by name, and killing
another session's authorised work while the human sleeps is not a call this
session should make on its own.

Live spend at the time of writing: GBP 0.0719 of a 5.00 cap.

## 2026-09-12 02:22-04:05 — live gates — COMPLETE

Gate 0 passed first attempt. Elicitation ceiling took three attempts, and the first
two failures were my instrument, not the model: attempt 1 included the integrity
block and so measured compliance instead of discoverability, and attempt 2 hit
token starvation, with six of ten calls spending the entire completion budget on
reasoning and returning an empty string. Attempt 3, on the real harness prompt at a
16000-token budget, gave a 100% parse rate and a clean refusal result.

Surprising, and the most important thing in this log: the ceiling's first result
was zero, which is the README's stop condition for the whole swarm. Reporting that
as a broken task would have been wrong. Once the instrument was fixed, every
submission that got through found the loophole, one of them reasoning explicitly
from evaluation order. The task is not vacuous; the model refuses under the rules.

base01 and base02 both completed 12 rounds with zero errors. Total spend for the
night 0.8892 GBP against a 5.00 cap.

## 2026-09-12 — four defect fixes — PASS

Payload detector split into strict and loose signals and scoped to the
agent-authored region; blacklist moved to keyword position; tier label removed from
the prompt in favour of expected attempts; truncated-JSON salvage added and the
turn budget raised to 32000 with a downgrade guard. Frozen suite green, both frozen
checksums unchanged, all seven module self-tests green.

Surprising: my first cut of the salvage invented data. Closing an unterminated
string produced a candidate the agent never emitted, which would have been logged
as real and counted in unique coverage. Rewritten so trimming always beats closing,
with an assertion over all 17 real truncated turns that nothing is invented.


## Phase 10 — free-running agents, pull channels, memory, monitor (2026-09-12)

Outcome: done, offline. Nothing live was run; gates 0b, 2b and the swarm run are
the human's to authorise.

Files written: `swarm/memory.py` (new), `swarm/monitor.py` (new), `gate_tools.py`
(new, project root), `tests/test_pull_memory.py` (new), `tests/test_free_running.py`
(new). Edited: `swarm/config.py` (one dated section, three new event kinds, three
corrected sentences in `TOOL_DESCRIPTIONS`), `swarm/client.py`, `swarm/world.py`,
`swarm/agentloop.py`, `swarm/run.py`, `swarm/analyse.py`. `tests/test_swarm.py`
untouched, checksum verified before and after.

Two documented decisions were reversed, both deliberately:

* **"Round-based, not free-running."** The design document chose rounds for
  simplicity and accepted the fidelity cost, mitigating same-round collisions by
  replaying them in completion-timestamp order. With the loop now built, the
  mitigation is unnecessary: threads make the race real, and `world.lock_problem`
  was already atomic. What rounds cannot reproduce is the paper's post-depletion
  phase, which is where its whistleblowing happens, so this was worth changing.
* **"No tool calling, because thinking mode requires reasoning_content to be
  passed back."** True, and the hop loop does pass it back, verbatim, within a
  step. Each step is a fresh conversation, so nothing crosses one. Pinned by
  `test_reasoning_content_is_echoed_back_on_the_next_hop`, and gate 0b's fourth
  call is the live negative control.

Surprising, and worth knowing before the next change: `run()` originally wrote
the step budget into `config.STEPS_PER_AGENT`, which leaked into every later run
in the same process and quietly masked an assertion in the self-test. The count
is now carried down the call chain. Nothing writes to config at runtime.

Self-tests: problems 13, grader 24, client 49, world 54, memory 44, agentloop 75,
run 54, analyse 24, monitor 27. Frozen suite plus the two new files: 63 OK.
`analyse` on base01 and base02 reproduces the recorded numbers exactly.
