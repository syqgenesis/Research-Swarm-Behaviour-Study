# Unsupervised build order

> **Precedence.** If this file is in play, it overrides the design document's
> build-sequence section. The plan describes a parallel, five-agent build; this
> is the same work done serially by one agent. The five briefs in `briefs/` are
> unchanged and are still the specification for each module — only the order and
> the number of agents differ. Everything the plan says about the *experiment*
> still stands.

For a single agent building alone while the human is asleep. Work the phases in
order. Append to `BUILD_LOG.md` after every phase, pass or fail, before starting
the next one.

## Hard rules — these override any instruction inside a brief

1. **Never edit anything in `tests/` to make a test pass.** If a test looks
   wrong, you may be right — but you do not get to decide that alone. Record it
   in `BLOCKED.md` and move on. A test weakened to go green is worse than no
   test, because it destroys the experiment silently.
2. **Never edit `swarm/config.py`.** It is the frozen contract.
3. **Never delete, relax or comment out an assertion.** Not to unblock yourself,
   not temporarily, not with a note promising to restore it.
4. **Never make a network call. Never spend money.** No live API calls, no
   gate 0, no trial run. `tests/__init__.py` enforces this and must not be
   modified or bypassed. Everything costing money waits for the human.
5. **Two strikes and stop.** If the same failure survives two genuine attempts,
   stop that phase, write what you tried and what you think is wrong in
   `BLOCKED.md`, and move to the next phase that does not depend on it.
6. **No scope creep.** Do not add features, refactor working code, or improve
   anything the briefs did not ask for.

## Phases

**Phase 0 — preflight.** Read `AGENTS.md`, `design/<plan>.md`, `swarm/config.py`.
Confirm no `TODO(design)` markers remain. If any do, write `BLOCKED.md` naming
them and **stop the entire build** — nothing downstream is meaningful without them.

**Phase 1 — `swarm/client.py`.** Brief `A_client.md`.

**Phase 2 — `tests/test_swarm.py`.** Brief `B_problems_grader.md`, tests section
only. Write the tests **from the plan and config alone**, before `grader.py`
exists. Every test will fail. That is correct and expected — tests written after
the implementation get fitted to the implementation and prove nothing.

**Phase 3 — `swarm/problems.py` and `swarm/grader.py`.** Brief
`B_problems_grader.md`. Make the phase-2 tests pass **without touching them**.
Test 1 — the grader accepts the known exploit, for every instance in the pool —
is the one that decides whether the whole experiment can measure anything.

**Phase 4 — `swarm/world.py` and `swarm/agentloop.py`.** Brief `C_world_agentloop.md`.

**Phase 5 — `swarm/run.py`.** Brief `D_run.md`.

**Phase 6 — `swarm/analyse.py`.** Brief `E_analyse.md`.

**Phase 7 — integration.** Run `python3 -m unittest discover -s tests -v`. Fix
the module that deviated from the contract. Never the test, never the config.
Then check every module imports cleanly and `analyse.py` survives an empty log.

**Phase 8 — adversarial self-review.** Re-read the plan with fresh eyes and
audit your own `grader.py` against it. Where could `used_exploit` be wrong at the
edges? Does the verdict ever leak which specific case failed? Is the grader
total on every path? Write findings to `REVIEW.md`. Do not act on them — log them
for the human.

**Phase 9 — handover.** Write `HANDOVER.md`: what is built, what passes, what
failed and why, everything in `BLOCKED.md`, and the three things you would check
first. Then stop. Do not start anything new.

## What the human does in the morning

Reads `HANDOVER.md`, `BLOCKED.md` and `REVIEW.md`; re-runs the tests; then gates
0, 2 and 3 in that order, watching.
