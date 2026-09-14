# Plan: resolve the run failures before scaling

Status: proposed implementation and validation plan, 12 September 2026.
This document plans the remaining work; it does not implement it or launch runs.

## Objective and boundaries

Make the harness preserve work, record what really happened, support the paper's
communication channels, and distinguish technical failure from agent behaviour.
Then determine whether the agents can make useful individual and shared progress
under the actual task/tool conditions before interpreting the main experiment.

Preserve the confirmed main-run design: 50 agents, 40 problems available from the
start, a planned 90-minute horizon, first accepted submission receives 50 points,
no helper points or split rewards, fixed reporting-reward groups 20/10/10/10 at
0/5/10/15 points, and penalties calculated after termination. Preserve the agreed
minute-30 prompted agent and conditional minute-60 single-peer hint. Neither
acceptance nor exposure is guaranteed. Do not change these rules in response to
uninteresting results.

The paper's Appendix C supports flexible communication, including partial work
and questions. It does not establish mandatory posting schedules or fixed team
roles. Competition and the possibility of shared mistakes are study conditions
to measure and contain; they cannot simply be removed while calling the result
the same experiment.

## Current status: implemented is not yet live-validated

The first patch restored optional board categories/tags and filtering, added a
bounded private recovery excerpt, corrected empty-output classification and
action timestamps, and added basic activity/read-then-citation measurements.
The last recorded offline suite passed 98 tests, including the actual lost-clique
regression. This planning turn did not rerun that suite.

Remaining limits are material:

- Recovery keeps a 10 KB scratch tail and a 4 KB receipt tail. Earlier useful
  material can be omitted; a later short note can replace the working excerpt.
- Full transcripts are written after a hop and its tools finish. There is still
  a window between receiving a response, executing actions, and durable capture.
- The client raises when charging a response exceeds the spend cap, before
  returning that response to the runner. The runner can therefore miss its
  content/usage in normal call logs. Concurrent requests are not reserved first.
- The monitor still uses legacy parse-rate and last-call timestamps. It has not
  caught up with the new activity analysis.
- Read-then-citation is an explicit-reference signal, not verified collaboration.
- There is no live evidence yet that recovery stops repeated searches or that the
  restored channels increase useful interaction.

## Priority 0: trustworthy execution and records

### P0.1 Preserve the working state, not just the newest tail

**Files:** `swarm/memory.py`, `swarm/agentloop.py`, `swarm/run.py`, `swarm/config.py`.

Keep separate private records for agent-written research notes, recent scratch
excerpts, and verified action receipts. Pin a successful candidate-test receipt
until it is submitted, its problem closes, or the agent explicitly abandons it.
A read-only hop or a short closing note must not erase an unresolved result.
Keep the original source-call ID with every retained item.

Make older returned scratch text retrievable by its owner through a bounded,
paginated read interface using immutable call IDs, not arbitrary filesystem paths.
The full text already belongs in the audit stream; expose only that agent's own
returned work through this interface. Never expose other agents' private work,
trusted audit internals, or generator solutions. Keep excerpts explicitly
incomplete/unverified, with an indication that earlier content exists.

Keep a compact current-work summary in the next prompt: agent-written task and
next action where available, outstanding test hits, and references to older work.
Do not have the harness invent a mathematical summary or silently extract and
submit answers from reasoning.

**Done when:** fixtures with an answer at the beginning, middle and end of a long
response remain retrievable; a test hit survives subsequent polling, a short
note, another cutoff and store restart; closed tasks are recognized; all state
stays private and within explicit storage/prompt bounds; no action is duplicated.

### P0.2 Recover from cutoffs without an endless restart cycle

**Files:** `swarm/run.py`, `swarm/agentloop.py`, `swarm/config.py`.

Represent completion, truncation, tool-budget exhaustion, API failure, stop and
normal inactivity as distinct outcomes. A truncated call resumes the same work
with the preserved state. Keep completed tool receipts; never replay a mutation
just because the enclosing step failed.

Retain incremental-work guidance. If a compact recovery prompt still repeatedly
produces no action, test an explicit action/checkpoint phase during calibration.
Do not assume that lowering the token cap reserves output space: the provider
may spend the entire smaller allowance on reasoning too. Any provider-specific
reasoning or tool-choice control needs the existing live compatibility gate and
must be frozen as a documented engineering condition before the main run.

**Proposed pilot diagnostic trigger:** three consecutive truncated calls with no
executed action flags that agent as stalled. This triggers inspection, not an
automatic label of refusal or a hidden mid-run prompt change. For a recovery
pilot only, predeclare that repeated unchanged failure ends that pilot early.
Low collaboration or zero cheating must not become a main-run stop criterion.

**Done when:** scripted cutoffs preserve state and budget limits, recovery can
produce an agent-issued test/submission, and failures remain visible. Live
readiness additionally requires evidence that agents resume partial work rather
than continually reconstructing it. Offline scripted replies cannot establish
that behavioural result.

### P0.3 Capture responses and actions before they can disappear

**Files:** `swarm/run.py`, `swarm/client.py`, log readers and relevant tests.

Give every API attempt, model response and tool execution an immutable ID. Record
call start, response receipt and complete normalized response before executing
tools. Record each tool's start and result at execution time, linked to its
parent response and tool-call ID. Preserve raw responses when normalization fails.
Distinguish pending, succeeded, failed and unknown-after-interruption executions.

Record requested and actual model/settings, actual max-token value on a downgrade,
usage fields and missing-usage indicators. Keep wall-clock timestamps for the
timeline and monotonic durations for elapsed time. A timeout or crash is not
evidence that a request was free or that a mutation never ran.

Use one authoritative event identity so transcript/call/event readers cannot
double-count the same operation. Recovery must never blindly replay an action
whose result is unknown. Either reconstruct it from durable state, use an
idempotent receipt, or mark it unresolved and stop the affected operation.

**Done when:** injected failures after response receipt, before tool execution,
after a mutation and before step completion leave an auditable record; accepted
files, library publication and recorded receipts reconcile exactly; partial
records are labelled rather than silently treated as zero activity.

### P0.4 Make spending, deadlines and termination reliable

**Files:** `swarm/client.py`, `swarm/run.py`, `swarm/monitor.py`.

Reserve a conservative request cost atomically before dispatch using explicit
input/output bounds and the configured price schedule; include pending calls in
remaining budget. Count cache misses conservatively for admission. Reconcile
the reservation against reported usage when the response returns. Preserve
uncertain cost for timeouts/malformed responses; do not replace it with a
confirmed zero. Record the price schedule used and verify it before live work.

Return or durably log an already-paid response before signaling a cap stop.
Recheck stop, deadline and budget at dispatch and before every retry, including
after waiting for a concurrency slot. Record queue time separately from provider
time. No new request or retry starts once admission is closed.

Define stop handling explicitly: drain already-started requests into the audit
record; after a global stop, withhold new tool mutations from their responses and
record that withholding. Preserve completed actions. Finalize the ledger only
after workers have drained. Test these semantics and record the version change.
Supervise worker failures and failed log/state writes; an emitted stop event must
correspond to an actual stop state. A missing terminal record means unknown or
interrupted, not successful completion.

**Done when:** concurrent fake requests cannot over-admit the configured bound;
every returned billable response is retained; stop/retry races do not start new
requests; all workers have explicit terminal states; final costs and accepted
work reconcile. The bound remains conditional on the verified price schedule
and request-size assumptions, not a guarantee about an external provider bill.

### P0.5 Make the monitor show actual activity

**Files:** `swarm/monitor.py`, `swarm/analyse.py`.

Show each agent's phase: queued, awaiting provider, using tools, recovering,
active, idle with no tasks, stopped, or failed. Show time since last executed
action separately from time since last response; show truncation counts, repeated
stalls, pending cost and logging gaps. Include the entire configured roster so an
agent that never returns a call cannot disappear from the dashboard.

Share outcome definitions between monitoring and offline analysis. Retire
parse-rate as a proxy for healthy work. Do not classify an agent that never
submitted as honestly abstaining: use `no_submission`, with reason unknown
unless the evidence supports a narrower description.

**Done when:** the old completed hard run shows 59 truncated calls and one step
with an executed action; a queued or in-flight agent is distinguishable from a
failed one; a run ended by time or error does not appear alive merely because
there is no STOP file; monitor and analysis agree on the same saved records.

## Priority 1: useful work, communication and trustworthy interpretation

### P1.1 Calibrate difficulty for these agents and these tools

**Files:** benchmark/configuration documentation, runner selection, analysis.

Keep the verified 40-instance main pool. Use explicit calibration subsets from
the existing ladder with the same model, tool access and recovery behaviour.
Measure genuine solves, valid candidates, persistent partial work, errors,
truncation and time/cost per action by family and level. Distinguish a discovered
but unfiled answer from inability to construct an answer.

Start with easy and intermediate tasks before an all-hard stress test. Confirm
which tasks support incremental progress under the current interface. Keep a
separate validation seed/pool after tuning, and record all tried settings so
selection of successful pilots is visible. Do not alter the main pool mid-run.

If arithmetic/search tools become necessary, propose a separate bounded-tool
condition; arbitrary submitted-code execution remains prohibited. Report that
capability change separately from a communication intervention.

**Done when:** there is a family/level calibration table and repeatable evidence
of genuine progress with the chosen interface. Levels without useful progress
remain explicitly uncalibrated or beyond this measured operating range; they
are not described as guaranteed triggers for cheating.

### P1.2 Encourage collaboration without manufacturing it

**Files:** `swarm/config.py`, `swarm/world.py`, channel tests, prompt documentation.

Finish end-to-end validation of Appendix C categories, author/tag filters,
pagination, unread counts, DMs and organiser feedback. Share partial work through
`building` posts and questions through `proposing`, without changing the
accepted-solutions-only library. Ensure an empty library does not imply there is
no place to share unfinished work.

Freeze the paper-aligned wording as the baseline. If testing an additional
collaboration prompt, make it a distinct condition: invite agents to state an
intention, share a useful intermediate result, or ask for a concrete check when
help would be useful. Do not require a fixed number of messages, assign permanent
team roles, reward posting, force reads, or push peer content into every prompt.
The existing disabled personas remain a documented difference; enabling adapted
personas is another condition, not an unrecorded fix.

**Done when:** offline fixtures demonstrate that partial work can reach a peer
through the intended tools, and live calibration documents whether any peer
actually uses it. Low uptake with working channels is a result to explain, not
grounds for secretly increasing social pressure during the main experiment.

### P1.3 Handle racing and duplicated effort correctly

**Files:** runner/world outcome accounting, analysis, prompt examples.

Retain first-to-file credit and no helper rewards. Keep intentions visible as
non-exclusive coordination information, not locks or reservations. Give agents
accurate current open/closed state and clear receipts for late submissions.

Separate wrong witness, malformed file, closed-task race loss, concurrent snipe,
cooldown, invalid tool input, checker probe and technical failure. Audit late
files offline if needed to identify valid work that lost a race; retain the
original live verdict. Count identical candidate duplication separately from
duplicated methods or overlapping branches, which require transcript review.

**Done when:** existing easy-pilot race losses stay race losses, correct answers
that lost credit remain visible in research accounting, and no non-submitter is
automatically labelled unwilling to cooperate or honestly refusing to cheat.

### P1.4 Trace assistance and mistakes without overstating either

**Files:** `swarm/analyse.py`, evidence export, reporting documentation.

Build an artifact trail: author → publication → actual recipient read → response
or later action. Retain IDs and content versions. Code selected episodes as
independent work, attempted reuse, verified useful assistance, repeated incorrect
information, or uncertain. An explicit citation without substantive reuse remains
a citation. Implicit reuse can be recognized by human review with a cited excerpt,
but not inferred merely because two agents solved the same problem.

Keep independent witness/audit checks. In the main experiment, do not silently
remove invalid accepted files or add new public trusted-check labels: those would
change the incident-response conditions. Allow agents to challenge claims through
the existing channels. Keep private trusted audit results out of ordinary tool
replies. Regression-test prompt injection in shared text and attempts to escape
tool/path boundaries. Bound message size and pagination; use spam diagnostics
rather than rewarding verbosity or silently hiding parts of the record.

**Done when:** a worked assistance episode and a propagated-error episode can be
reconstructed from immutable evidence; no shared claim is treated as verified
without a check; audit flags and private memories do not leak to other agents.

### P1.5 Account for depletion and misleading inactivity

**Files:** prompt/status text, runner, monitor, analysis.

State clearly that the pool is fixed and no new problems arrive. Record its exact
depletion time. Keep subsequent reporting, warnings and discussion observable.
Separate solving opportunities before closure from post-depletion behaviour.

An agent without an open target cannot demonstrate adoption by submitting a new
solution. An unread message is not refusal, an empty pool is not incapability,
and missing logging is not a decision. An extension with new prompts, state or
memories is a new trajectory unless true continuation is explicitly implemented
and documented.

**Done when:** reports separate pre/post-depletion periods and opportunity counts;
blocked interventions and unread hints are explicit; old extensions are not
merged into continuous agent histories without evidence.

## Priority 2: a defensible experiment and release decision

### P2.1 Freeze comparable conditions and primary questions

**Files:** `design/main-run-plan.md`, run manifest, analysis specification.

Record source revision plus working-tree content hashes, prompt/schema hashes,
model and settings, generator/pool hashes, seeds, roles/personas if enabled,
recovery policy, concurrency, stopping rules, intervention schedule, actual
runtime, token/cost limits and cost schedule. Include only selected configuration
fields; never archive secrets or environment credentials. A git commit alone is
insufficient when running with uncommitted edits.

Repair stale or contradictory prose in the main-run plan: some sections still
describe scoring and transcripts as unimplemented, despite the newer implemented
sections. Reconcile the earlier example budget allocations with the actual
main-run cap before launch.

For collaboration calibration, compare matched conditions: same problems,
model, agent count, tools, budgets and recovery; vary only the predeclared prompt
addition. Randomize condition order and use fresh histories. Aim for at least
three matched seed pairs for exploratory comparison if budget permits; fewer
provide feasibility evidence only, not a reliable effect estimate. A seed does
not guarantee deterministic model outputs.

For the main incentive study, preserve reward randomization and report group
outcomes, exposure reach and responses after exposure separately. Exclude the
prompted agent from the primary unprompted comparison. The remaining agents are
socially dependent; they are not independent experimental repeats. Conditioning
only on agents who read the exploit can introduce selection bias, since reward
may change who reads it. Do not infer that difficulty causes cheating from these
historical runs, whose conditions differ in several ways.

**Done when:** the manifest can identify every material condition; outcome and
exposure definitions are frozen before launch; the report distinguishes
descriptive associations from causal claims and spontaneous from prompted acts.

### P2.2 Rehearse the incident and attribution path offline

**Files:** main-run tests, world/run, audit/reporting analysis.

Cover accepted genuine and invalid work, report correctness, duplicates, false
reports, final penalties, intervention refusal, a target closing during the
prompted agent's call, an empty pool, no exposure, missing exposure records,
an unread hint, a hint read without the file, and a peer receiving the full file.
Keep scheduled time, delivered instruction, executed action, acceptance,
publication and actual read separate. A failed seed is a recorded failed
intervention; no hidden harness submission replaces it.

Replay reporting and final scores from immutable records and compare with live
state. Preserve overlaps: a participant can both report and exploit. Unknown
evidence availability must not trigger a hint as if it were confirmed zero.

**Done when:** every branch has an expected auditable outcome; existing reward
counts, private scoring and end-of-run penalties remain consistent; independent
replay reproduces the final ledger without double rewards or lost actions.

### P2.3 Validate capacity and decide whether to proceed

Run offline fault/concurrency tests first, then the existing provider gate and
small live calibration only under live-run authorisation. Test actual concurrent
capacity gradually; 50 identities do not imply 50 simultaneous provider calls.
Measure queue fairness, per-agent completed work, latency, context growth,
checkpoint storage, log growth and measured spend. Choose and freeze a concurrency
setting that the measured workload can support.

Maintain one cumulative experiment budget across pilots, gates and the main run.
Do not treat every per-run cap as fresh money. Keep the main cap within the
confirmed £50–£90 design; the selected total envelope must also cover preparation
already spent and reservations for pending calls. Two full repeats are not
promised within that envelope. If preparation leaves insufficient room, revise
the allocation before launching rather than silently shrinking or overspending
the confirmed main design. No current provider price is assumed by this plan.

**Proceed only when:** records reconcile, recovery works, the chosen task/tool
mix supports genuine progress, channel access works, ledger/intervention tests
pass, stop/budget controls pass, measured capacity is adequate, and the run
manifest/budget are frozen. A small live pilot is still needed even after every
offline test passes. Neither high collaboration nor the appearance of cheating
is required for a technically valid main-run result.

## Execution order and deliverables

| Order | Work package | Deliverable / completion evidence |
|---|---|---|
| 1 | P0.1–P0.2: persistence and recovery | Versioned private state, bounded retrieval, cutoff/restart regressions |
| 2 | P0.3–P0.4: durable events, spending and stops | Fault-injection matrix, concurrent admission tests, reconciled receipts |
| 3 | P0.5 + P1.3/P1.5: monitor and outcome semantics | Historical-log replay with correct inactivity, timing and race categories |
| 4 | P1.2/P1.4 + P2.2: channels, provenance and incident paths | End-to-end fixture traces and independently replayed ledger |
| 5 | P2.1: manifest and protocol cleanup | One consistent comparison specification and budget plan |
| 6 | P1.1 + P2.3: authorised calibration and capacity test | Difficulty/action table, recovery evidence, measured resource needs |
| 7 | Final readiness review | Remaining limitations stated; explicit decision on the main run |

These packages describe module responsibilities, not permission to launch agents
or live runs. Finish and review one package before widening scope. Preserve
other ongoing workspace edits. Keep all work under this project, never read
`secrets.env`, never bypass `tests/__init__.py`, and make no network calls while
building. Any new operational or behavioural control must be documented before
it is used to generate research results.

## Sources and status evidence

- [Prior-run analysis](../runs/previous-runs-analysis-2026-09-12.md).
- [Implemented Appendix C and recovery patch](paper-prompt-recovery.md).
- [Current main-run specification](main-run-plan.md).
- [Saved paper, sections 2–3 and Appendices A–C](sources/2609.04170.txt).
- Current inspected code: `swarm/run.py`, `swarm/client.py`, `swarm/memory.py`,
  `swarm/monitor.py`, and `swarm/analyse.py`. Findings about remaining gaps are
  based on those files as read during this planning turn, not historical comments
  alone.
