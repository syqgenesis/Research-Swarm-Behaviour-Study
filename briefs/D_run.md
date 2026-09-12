# Agent D — `swarm/run.py`

The round loop, the thread pool, the orchestration, and both JSONL writers.

Fan-out pattern, copied faithfully: futures keyed into a dict by input key,
`as_completed` for collection, per-worker `try/except` substituting a
**same-shape sentinel** so one failure cannot lose the round, ordering restored
afterwards by iterating the canonical input list. Add `timeout=` on
`fut.result()`.

Resolve same-round collisions on one problem by **actual completion timestamp**.
The run is round-based where the original was free-running; resolving by real
completion time is what preserves the race and the sting of being locked out.

`log(stream, record)` per the frozen signature: appends one JSON object per line
to `runs/<run_id>.<stream>.jsonl`, **flushed immediately**, so a crash at round
nine still leaves nine rounds of data.

`calls.jsonl`, one record per API call: `run_id, round, agent, ts, latency_s,
usage{...}, cost_gbp, ctx{...}, exposure{...}, reasoning_content, raw_content,
action{...}, parse_ok, error`.

`events.jsonl`, one record per world event, kinds from `config.EVENT_KINDS`. Each
carries round, timestamp, actor, recipient, problem, full text, grader verdict,
and a stable `artifact_id`.

Argparse: `--agents`, `--rounds`, `--run-id`, `--seed`.
