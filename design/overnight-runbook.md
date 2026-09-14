# Overnight analysis runbook

What to do once a real run's logs exist. Everything below is offline and
free except the judging step, which spends subscription usage (a Claude
subagent) or a small DeepSeek amount if we fall back. Nothing here starts
a live swarm run.

## 1. Deterministic sweep (free, seconds, run first on every run)

```python
from swarm import sweep
result = sweep.write_sweep("main-run-01")   # substitute the real run id
```

Writes, next to the run's own logs:

- `runs/<run>.sweep.json` — codebook hit counts, the full work-loss list
  (correct candidates found in reasoning but never filed), the exposure
  table, and how many items were queued for judging
- `runs/<run>.sweep.md` — short human-readable version of the same
- `runs/<run>.judge_queue.jsonl` — every hop selected for judging
- `runs/<run>.judge_batches/batch-0001.json` ... — the queue chunked into
  batches of 25, ready to hand to a judge

Read `<run>.sweep.md` first. The work-loss and exposure sections are
often enough on their own to answer Q1, Q3 and Q8 from the plan without
spending anything on a judge.

## 2. Hand-label a calibration set (do this once, before trusting any judge)

Pick ~50-100 items from `<run>.judge_queue.jsonl` (or from an existing
pilot run's queue tonight, before the real data lands) and label them by
hand against `design/judge-rubric.md`'s codebook. Save as JSONL:

```json
{"item_id": "...", "labels": {"bluff": true, "exploit_intent": false}}
```

to `runs/<run>.gold.jsonl`. Only codes you actually hand-checked need to
appear per item.

## 3. Judge the batches

For each `runs/<run>.judge_batches/batch-NNNN.json`:

1. Open the file, take its `items` array and the rubric in
   `design/judge-rubric.md`.
2. Run it through a Sonnet subagent at low effort with the rubric as the
   prompt and the batch's items as the input, asking for exactly the
   output schema the rubric specifies.
3. Save the returned JSON as `runs/<run>.judge_batches/batch-NNNN.result.json`
   next to the input file.

Check what's left with:

```python
from swarm import judge_merge
judge_merge.judge_status("main-run-01")   # -> {"pending": [...], "done": [...]}
```

If Sonnet usage runs low, the remaining batches can go through DeepSeek
flash instead via the existing `swarm.client` — same batch files, same
output schema, a different judge.

## 4. Merge and calibrate

```python
from swarm import judge_merge
summary = judge_merge.merge_batches("main-run-01", judge_name="sonnet-low")
print(summary)   # annotated_items, missing_batches, batches_with_errors

report = judge_merge.calibration_report(
    "runs/main-run-01.gold.jsonl", summary["annotations_path"])
for code, stats in report.items():
    if stats["n_shared"]:
        print(code, stats)
```

`kappa` below about 0.5 for a code means that code isn't trustworthy yet
for this run — report it as exploratory only, don't lean on it for a
frozen comparison.

## 5. Human-label the two primary binaries (before touching real numbers)

`design/codebook.md` — two labellers, all 24 primary agents, both
binaries (`recognised_exploit`, `warned_or_sanctioned`). These feed the
cohort rule directly and are never left to the LLM judge. Save each
labeller's raw output, adjudicate disagreements together, and compute
kappa with `swarm.judge_merge.cohens_kappa` before trusting the
adjudicated result.

## 6. Build the outcome table and render — blinded first

```python
from swarm import timeline, outcomes, report

tl = timeline.build("main-run-01")
table = outcomes.build_outcome_table(tl, annotations=None)   # unblind defaults False
result = report.render("main-run-01", unblind=False, n_perm=20000, seed=20260913)
print(result)   # {"report_path", "evalcard_path", "blinded": True, "primary_p": {...}}
```

Read `<run>.report.md` while it is still blinded. Every table, every
number, is real — only the `reward_group` column is a reproducible
scramble. Fix anything that looks wrong in the pipeline now, while it
cannot be influenced by knowing which group is which.

## 7. Unblind — once, by hand

After the blinded pipeline has run cleanly and `design/preregistration.md`,
`design/codebook.md` and this runbook are committed:

```python
from swarm import report
result = report.render("main-run-01", unblind=True, n_perm=20000, seed=20260913)
```

The report's banner switches from `**BLINDED**` to `**UNBLINDED**`. This
is the one unblinding event — do not re-run with `unblind=False` after
this point and call it a fresh look; the deviations log in
`design/preregistration.md` is where any change made after unblinding
gets recorded, not silently folded back in.

## 8. Rehearsal, already done before any real data existed

`python3 -m swarm.mockrun --rehearse --effect 0` and `--effect 0.15`
were run on synthetic logs before the main run, proving the primary test
never exceeds its nominal false-positive rate and recovers an injected
effect (numbers in `design/preregistration.md` section 9, raw output in
`runs/rehearsal-null.json` / `runs/rehearsal-effect.json`). Nothing to do
here tonight beyond citing those numbers in the report's limitations.

## 9. What's still open

Social-layer graphs (message/read network centrality, cascade geometry),
memory-file version reconstruction, and the Docent/Inspect exports from
`design/analysis-plan.md` layers 4-6 are not built. They are exploratory
extensions, not needed for the frozen primary comparisons in
`design/preregistration.md`, and are worth doing only once the frozen
result is in hand and there is time left before the deadline.
