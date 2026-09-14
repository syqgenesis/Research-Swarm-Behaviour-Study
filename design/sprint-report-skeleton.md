# Sprint report skeleton — fill in after the main run

Target: 8 pages max excluding references and appendices (Apart Research
AI Incident Response Sprint, Open track). Judged on "an artifact somebody
can use, a stated limit on what it establishes, and what a month of
follow-up would add." Deadline 2026-09-13 23:59 AoE.

Everything in brackets below is a placeholder to fill in from
`<run>.report.md`, `<run>.evalcard.md` and `design/preregistration.md`
once the main run exists — do not write numbers into this file before
that, so this skeleton stays reusable if the run needs a second attempt.

---

## 1. Summary and the artifact (roughly 0.5 page)

- One paragraph: what was built (an offline analysis pipeline for
  multi-agent CoT/message logs: `swarm/timeline.py`, `swarm/sweep.py`,
  `swarm/outcomes.py`, `swarm/stats.py`, `swarm/mockrun.py`,
  `swarm/report.py`, `swarm/judge_merge.py`) and what it was run on (a
  25-agent, 90-minute replication-with-manipulation of DeepMind's
  emergent-cheating-and-whistleblowing swarm, arXiv 2609.04170).
- One paragraph: the headline finding, stated with its own confidence
  interval and p-value inline, not as a bare claim. [FILL: H1/H2 result
  from section 1 of the rendered report.]
- One sentence: the artifact is reusable on any run in this harness's log
  schema, and rehearsed on synthetic data before ever touching real logs
  — link to `design/preregistration.md` section 9.

## 2. Setup (roughly 1 page)

- The DeepMind paper's design and what this run adapts vs. reproduces
  (persona-free, DeepSeek not Gemini, 25 not 100 agents, a seeded rather
  than spontaneously-discovered exploit, paid reporting).
- EvalCard table, pasted verbatim from `<run>.evalcard.md`.
- The four-group reward design and the primary-population exclusion
  (seeded agent out, 24 remain).

## 3. Frozen results (roughly 1.5 pages)

- H1 and H2 tables and p-values, pasted from `<run>.report.md` section 1.
- One paragraph interpreting them against the pre-registered minimum
  detectable effect from `design/preregistration.md` section 9 — if the
  result is null, say explicitly whether that is "no effect" or
  "under-powered to see the effect size we could detect," and which.
- Holm-adjusted table.

## 4. Exposure and time-to-event (roughly 1 page)

- `adoption_given_exposure`, the number that matters — from section 2 of
  the rendered report, next to the whole-population rate, with both
  labelled.
- Kaplan-Meier curves or tables for time-to-report, paid vs zero, with
  the permutation log-rank p-value.
- Library vs peer-mediated exposure split.

## 5. Cohorts and case files (roughly 1.5 pages)

- Cohort table (section 5 of the rendered report).
- 2-4 short case files: the seeded agent's own trajectory, one convert
  (if any), one whistleblower, and the minute-60 hint recipient if
  prompted — each a few sentences with a direct quote and its evidence
  index pointer `(agent, step, hop, char offsets)`.

## 6. Exploratory (roughly 1 page)

- Work-loss findings (correct answers never filed).
- Codebook hit patterns, framed explicitly as "recall only, not a
  finding by itself."
- Judge calibration kappa, if the secondary codebook was run.
- Point-sharing tables, if that arm ran.

## 7. What a month of follow-up would add (roughly 0.5 page)

- A second, independently-seeded run (the £90 budget's repeat row in
  `design/main-run-plan.md`) to move from "one world's exploratory
  patterns" toward something closer to a replicated effect.
- Graph-cluster randomisation (Ugander et al.) as the design fix for the
  interference/SUTVA problem this run cannot solve on its own.
- Full-coverage LLM judging of every hop (affordable at the DeepSeek
  flash price point measured in this run) rather than the filtered queue,
  for the exploratory cohort/rationalisation analysis.
- A no-communication control condition to separate genuine social
  contagion from independent rediscovery by an identical model.

## Appendix A — Limitations

- **Single shared world.** 24 primary agents are not 24 independent
  replications; they influence each other. Confidence intervals and
  p-values are conditional on this one realised world, not a claim about
  the population of possible worlds.
- **Self-selected exposure.** Whether an agent reads the library or a DM
  is itself a behaviour, not something assigned; exposure-conditional
  comparisons are not causal without further assumptions
  (Aronow & Samii's framing, cited in `design/preregistration.md`
  section 5).
- **Chain of thought is a lower bound on cognition.** An agent's
  reasoning text not mentioning the exploit is evidence of no verbalised
  awareness, not evidence of no awareness at all (see the CoT-faithfulness
  literature surveyed 2026-09-12: verbalisation of decisive information
  is well under half in published measurements).
- **Same-model judge risk.** The secondary-codebook judge and the agents
  under study can be the same model family; agreement with itself is not
  validity. The two primary binaries are entirely human-labelled for
  exactly this reason (section 6, `design/codebook.md`).
- **Censoring at pool depletion.** An agent with no open problem left has
  no further opportunity to exploit or to solve honestly; its later
  inactivity is not evidence of a behavioural choice.
- **Keyword hits are not intent.** The Tier-0 regex codebook
  (`swarm/sweep.py`) is recall-only triage; every reported finding that
  matters is backed by a human-checked quote or a deterministic
  re-verification (`swarm.benchmark.really_solves`), never a bare regex
  match.
- **Small-sample test conservativeness.** The primary test is an exact
  permutation test at n=24 in 4 unbalanced groups; it is provably never
  anti-conservative, but is measurably conservative at these sample
  sizes (`design/preregistration.md` section 9) — a null result here is
  weaker evidence of "no effect" than the same p-value would be at a
  larger n.

## Appendix B — Dual-use considerations

- The exact exploit payload (the checker-bypass definition) and the
  seeded-agent instruction text are not reproduced verbatim in the public
  report; the report cites `swarm/config.py`'s `MARK_SOL_BEGIN`/`END`
  convention and the general mechanism (a definition inside the editable
  region that rebinds an authority name) without the copyable string.
- The minute-60 hint text template is described by function, not quoted
  verbatim, for the same reason.
- What a reader with adversarial intent could take from this report: the
  general finding that a permanent-lockout-plus-shared-library design
  creates a race that pressures honest agents toward exploitation once
  they observe it succeeding — this is the paper's own finding, already
  public, and the mitigation section below is the intended takeaway, not
  the exploit mechanics.
- Mitigations discussed: private/delayed lockout resolution, structured
  auditable communication channels over unmonitored back-channels
  (Ostrom-style design principles, per the source paper's Section 4),
  and a monitored feedback channel rather than the unmonitored one this
  run and the source paper both used.
