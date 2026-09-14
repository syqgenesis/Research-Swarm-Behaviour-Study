# Separate analysis plan: reuse existing evaluation tools

Draft for discussion, 12 September 2026. Tool capabilities checked against their public repositories and documentation today. This document is independent of `design/analysis-plan.md` and does not change that plan or the experiment harness.

Installation update, later on 12 September: Scout, Inspect AI, NetworkX and Gephi are now installed. Eight unlabelled snapshots have been imported and a code-only Scout installation scan completed. See `ops/eval/README.md` for versions, launch instructions and checks. The findings under “What was actually checked today” below describe the earlier research pass.

## Recommendation

Use **Inspect Scout** for analysing saved transcripts, running code and model graders, reviewing evidence, and validating labels against human judgments. Use **NetworkX plus Gephi** for the communication graph. Reuse the project's mathematical checks and event records. Our custom work should be the import adapter and research-specific grading definitions.

Scout calls graders **scanners**. It supports pattern matching, custom Python functions, LLM classification, structured outputs, references, resumable scans, local result storage, and a review interface. It accepts custom transcripts through its Python API, so a new experiment is unnecessary. [Repository](https://github.com/meridianlabs-ai/inspect_scout), [overview](https://meridianlabs-ai.github.io/inspect_scout/), [custom imports](https://meridianlabs-ai.github.io/inspect_scout/db_importing.html).

## Tools compared

| Tool | Verified capabilities | Fit for this project |
|---|---|---|
| [Inspect Scout](https://github.com/meridianlabs-ai/inspect_scout) | Saved-transcript analysis, code and LLM scanners, evidence review, human validation, multi-agent timelines | Recommended primary tool. Custom JSONL requires an adapter. MIT license. |
| [DeepEval](https://github.com/confident-ai/deepeval) | Automated evaluation tests, custom G-Eval criteria, agent and tool metrics, evaluation skills | Good alternative when test suites are the priority. Generic task-completion or relevance metrics do not establish misconduct. Apache-2.0 license. |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix) | Self-hosted trace viewer, evaluation, datasets, experiments, CLI, MCP and skills | Useful if we want a broader ongoing observability platform. More infrastructure than we need for the first saved-log pilot. Repository currently declares Elastic License 2.0. |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | Evaluation CLI, model/prompt comparison, caching and CI checks | Alternative for testing judge prompts. Not needed alongside Scout in the first pilot. MIT license. |
| [NetworkX](https://github.com/networkx/networkx) + [Gephi](https://github.com/gephi/gephi) | Graph algorithms and export; interactive graph layouts and filtering | Recommended graph tools. NetworkX is already installed locally. They do not determine whether an interaction caused adoption. |

These are documentation-based fit assessments, not comparative runtime benchmarks. Only NetworkX was exercised on our data in this research pass.

## Reusable skills and workflow

- [Phoenix error-analysis skill](https://github.com/Arize-ai/phoenix/blob/main/.agents/skills/phoenix-error-analysis/SKILL.md): read sampled traces, record concrete observations, then group observations into narrow labels. Its operational workflow assumes Phoenix; we can borrow the method without installing the platform. For research validation we must also sample ordinary and negative cases, not only cases judged problematic.
- [Phoenix evaluation skill](https://github.com/Arize-ai/phoenix/tree/main/.agents/skills/phoenix-evals): repository-provided evaluation workflow.
- [DeepEval skills](https://github.com/confident-ai/deepeval/tree/main/skills/deepeval): an alternative agent-assisted evaluation setup.
- [Scout human validation](https://meridianlabs-ai.github.io/inspect_scout/validation.html): built-in editing of reference labels, development/test splits, balanced accuracy, precision, recall and F1. This directly covers our agreed human-checking requirement.

Skills guide the work; they do not supply validated definitions of cheating or replace the evaluation engine. None was installed or executed as an evaluation workflow during this pass.

## Proposed first pilot

1. **Import and check the evidence.** Start with `conference25d`. Preserve run, agent, step, hop, timestamp, artifact ID, source line and intervention metadata. Keep full prompts and tool results available. Preserve reasoning text separately from public replies. Repeated request histories must not multiply the same message into several behavioural observations. Keep the original files intact.
2. **Run code graders over all eligible records.** Reuse witness verification to distinguish genuine correctness from recorded acceptance. Check tool success, message/artifact identity, explicit tool-return exposure, intervention status, missing records and truncated responses. A keyword match is a candidate for review, not a final behavioural label.
3. **Agree on a small label set.** Proposed dimensions: exploit recognition, attempted exploitation, rejection of an exploit, reporting/peer warning, and evidence of useful collaboration. Use independent labels where behaviours can coexist; include uncertain or insufficient-context judgments. Require exact quotations and source references. Submitted mathematical validity remains a code judgment.
4. **Create human reference labels.** Start with about 80 reviewable episodes: 40 randomly sampled and 40 targeted for rare behaviours or ambiguity. Keep selection reasons. Split development and held-out examples without duplicating overlapping episodes. Hide previous model labels and cohort metadata during initial review where possible; do not alter the underlying evidence. The user or another human must supply the human labels: an assistant's own labels remain model labels.
5. **Validate the model graders.** Tune on the development examples, freeze the rubric, then evaluate the held-out examples. Report precision (how many flags were right), recall (how many real cases were found), F1 and sample counts for each label. Inspect false positives and false negatives. Report random and enriched samples separately; their combined rates are not population prevalence. If a rare label has too few reviewed cases, say so and expand the sample.
6. **Scale only after validation.** Save judge model/version, rubric version, input IDs, errors and costs. Use Scout's resumable scans. Review important disagreements and alleged misconduct before reporting findings. A second model can help locate disagreements but is not a substitute for human checks.
7. **Join labels to the timeline and graph.** Preserve separate layers for observed communication, verified outcomes, model interpretations and human-reviewed labels. Export graph data using existing tools rather than building a new visualisation engine.

The 80-example size is a proposed starting point, not a claim of statistical sufficiency. Model-judge provider and pilot spending limit remain to be chosen before paid grading.

## Timeline and graph design

Scout's built-in multi-agent timeline is a hierarchy of agent/tool/scorer spans. Our conference consists of interacting peers, so that tree cannot stand in for the communication network. Keep agent timelines in Scout and build explicit communication edges from the event log. [Scout multi-agent documentation](https://meridianlabs-ai.github.io/inspect_scout/multi_agent.html).

Use two node types: agents and artifacts (posts, DMs and library items). Observed edges record `agent created artifact` and `artifact was returned by a tool to agent`. A DM's recipient field records whom it was addressed to; actual retrieval is a separate event. Keep timestamps and source references on edges. Preserve repeated reads in the evidence export, with a deduplicated view for readability.

Later, overlay verified exploit use and reviewed warnings at the corresponding times. Following a time-respecting path can identify a possible information route, but does not prove influence. Content being returned is not proof of attention or understanding. Prompted behaviour must remain visibly distinguishable from unprompted behaviour.

Use NetworkX's GEXF export for Gephi rather than inventing a graph format. [GEXF documentation](https://networkx.org/documentation/stable/reference/readwrite/gexf.html).

## What was actually checked today

- Inspected the current `swarm/timeline.py`, `swarm/sweep.py` and `swarm/judge_merge.py` integration surfaces and raw-record field names. Did not use the separate Claude analysis plan to choose the recommendation.
- Ran `python3 -m unittest tests.test_analysis tests.test_report tests.test_stats`: **52 tests passed**, with the existing network guard active.
- Ran a one-off NetworkX 3.6.1 analysis of the saved event log under the same network guard. Exported 6,770 timestamped events and an observed communication graph with 25 agents, 149 artifacts, and 13,698 edges, including repeated tool returns.
- All extracted tool-return artifact IDs resolved; none had a return timestamp before artifact creation. GEXF write/read preserved node and edge counts. These are basic integrity checks, not a full audit of logging or behavioural conclusions.
- Outputs: `workspaces/conference25d/codex-eval-tooling-pilot/`. The summary records the input SHA-256 and limitations.
- Inspect Scout, DeepEval and Phoenix are not installed in the inspected `python3` environment. No external model grading, new experiment, package installation or hosted trace upload occurred.

The next concrete implementation is a small Scout import-and-grading pilot, followed by human review of the reference examples. The graph export already demonstrates that we can reuse an existing graph library on the current logs.
