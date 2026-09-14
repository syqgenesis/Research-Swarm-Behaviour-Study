"""Normalises the three JSONL streams a run writes into one in-memory shape.

Pure offline reader, stdlib only, no imports from any other swarm module
except `config` — same discipline as `swarm/analyse.py`, kept independent of
it on purpose so either can be pointed at a crashed/partial run on its own.

Every read tolerates a truncated final line, a missing file, an empty file
and a record with fields absent. This module does not judge anything; it
only reshapes calls.jsonl / events.jsonl into agent- and time-indexed lists
that swarm/sweep.py (and, later, an LLM judge) can consume without each
re-parsing JSONL themselves.
"""
import json
import os

from swarm import config


def _validate_run_id(run_id):
    """Run ids are filename stems, never paths. Duplicated from analyse.py
    on purpose (see module docstring): each pure reader stays import-free."""
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (not isinstance(run_id, str) or not 1 <= len(run_id) <= 80
            or run_id in (".", "..") or not run_id[0].isascii()
            or not run_id[0].isalnum() or any(ch not in allowed for ch in run_id)):
        raise ValueError("run id must be a bare ASCII name")
    return run_id


def read_jsonl(path):
    """Every well-formed line. A truncated final line is skipped, not fatal."""
    records = []
    if not os.path.exists(path):
        return records
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _run_paths(run_id, run_dir=None):
    run_id = _validate_run_id(run_id)
    base = run_dir or config.RUN_DIR
    return {
        "calls": os.path.join(base, run_id + ".calls.jsonl"),
        "events": os.path.join(base, run_id + ".events.jsonl"),
        "transcripts": os.path.join(base, run_id + ".transcripts.jsonl"),
        "memory": os.path.join(base, run_id + ".memory"),
    }


def _hop_row(call):
    usage = call.get("usage") or {}
    exposure = call.get("exposure") or {}
    return {
        "run_id": call.get("run_id"),
        "agent": call.get("agent"),
        "step": call.get("round"),
        "hop": call.get("hop"),
        "final": bool(call.get("final")),
        "ts": call.get("ts"),
        "finish_reason": call.get("finish_reason"),
        "truncated": bool(call.get("truncated")) or call.get("finish_reason") == "length",
        "has_tool_actions": bool(call.get("has_tool_actions")) or bool(call.get("tool_calls")),
        "parse_ok": call.get("parse_ok"),
        "error": call.get("error"),
        "reasoning": call.get("reasoning_content") or "",
        "reply": call.get("raw_content") or "",
        "action": call.get("action") or {},
        "tool_calls": call.get("tool_calls") or [],
        "cost_gbp": call.get("cost_gbp") or 0.0,
        "tokens": {
            "prompt": usage.get("prompt_tokens") or 0,
            "completion": usage.get("completion_tokens") or 0,
            "reasoning": usage.get("reasoning_tokens") or 0,
            "cache_hit": usage.get("prompt_cache_hit_tokens") or 0,
            "cache_miss": usage.get("prompt_cache_miss_tokens") or 0,
        },
        "exposure": {
            "board_ids": exposure.get("board_ids") or [],
            "dm_ids": exposure.get("dm_ids") or [],
            "library_ids": exposure.get("library_ids") or [],
            "open_problems": exposure.get("open_problems") or [],
        },
    }


def _event_row(ev):
    return {
        "kind": ev.get("kind"),
        "kind_detail": ev.get("kind_detail"),
        "actor": ev.get("actor"),
        "recipient": ev.get("recipient"),
        "step": ev.get("step"),
        "seq": ev.get("seq"),
        "ts": ev.get("ts"),
        "artifact_id": ev.get("artifact_id"),
        "artifact_ids": ev.get("artifact_ids") or [],
        "problem": ev.get("problem"),
        "text": ev.get("text"),
        "verdict": ev.get("verdict"),
        "intervention": ev.get("intervention"),
    }


# Event kinds that create a readable, citable artifact (as opposed to a bare
# action like `lock` or `stop`, which have no content another agent can read).
_ARTIFACT_KINDS = {"post", "dm", "submit", "accept", "library_commit", "feedback"}
# Event kinds that mean "this agent's tools handed it some content", joined
# against `artifact_ids` to build the read ledger.
_READ_KINDS = {"tool_call"}
_READ_DETAILS = {"get_bulletin_board", "get_messages", "get_library"}


def build(run_id, run_dir=None):
    """Returns a plain-dict timeline. JSON-serialisable, no custom classes,
    so it can be dumped/loaded independently of this module's version."""
    paths = _run_paths(run_id, run_dir)
    calls = read_jsonl(paths["calls"])
    events = read_jsonl(paths["events"])
    return from_records(run_id, calls, events)


def from_records(run_id, calls, events):
    """Same shape as `build`, but from already-loaded records rather than
    files on disk. Lets an in-memory generator (swarm/mockrun.py) or a
    rehearsal loop build a timeline without a write/read round trip; `build`
    is exactly this called on freshly-read JSONL, so the two never drift."""
    hops = [_hop_row(c) for c in calls]
    hops.sort(key=lambda h: (h["ts"] or 0, h["agent"] or "", h["step"] or 0, h["hop"] or 0))

    ev_rows = [_event_row(e) for e in events]
    ev_rows.sort(key=lambda e: (e["seq"] if e["seq"] is not None else 0))

    artifacts = []
    for e in ev_rows:
        if e["kind"] in _ARTIFACT_KINDS and e["artifact_id"]:
            artifacts.append({
                "artifact_id": e["artifact_id"], "kind": e["kind"],
                "author": e["actor"], "ts": e["ts"], "step": e["step"],
                "text": e["text"] or "", "problem": e["problem"],
                "recipient": e["recipient"],
            })

    reads = []
    for e in ev_rows:
        if e["kind"] in _READ_KINDS and e["kind_detail"] in _READ_DETAILS and e["artifact_ids"]:
            for aid in e["artifact_ids"]:
                reads.append({
                    "agent": e["actor"], "artifact_id": aid,
                    "channel": e["kind_detail"], "step": e["step"], "ts": e["ts"],
                })

    memory_writes = [e for e in ev_rows if e["kind"] == "memory_write"]
    ledger = [e for e in ev_rows if e["kind"] == "score"]
    reports = [e for e in ev_rows if e["kind"] == "report"]
    interventions = [e for e in ev_rows if e["kind"] == "intervention"]

    # run_start/run_end are singleton events whose completeness matters more
    # than the per-hop trimming _event_row applies to everything else (they
    # carry report_rewards, seeded_agent, hint_peer, point_sharing_enabled,
    # final_ledger, ...) — kept as the raw dict rather than the artifact-
    # shaped ev_row.
    run_start = next((dict(e) for e in events if e.get("kind") == "run_start"), None)
    run_end = next((dict(e) for e in events if e.get("kind") == "run_end"), None)
    problems = (run_start or {}).get("problems") or []

    agents = sorted({h["agent"] for h in hops if h["agent"]})
    timestamps = [h["ts"] for h in hops if h["ts"]]

    return {
        "run_id": run_id,
        "agents": agents,
        "span_s": (max(timestamps) - min(timestamps)) if timestamps else 0.0,
        "problems": problems,
        "hops": hops,
        "events": ev_rows,
        "artifacts": artifacts,
        "reads": reads,
        "memory_writes": memory_writes,
        "ledger": ledger,
        "reports": reports,
        "interventions": interventions,
        "run_start": run_start,
        "run_end": run_end,
        "complete": run_end is not None,
    }


def artifacts_by_id(timeline):
    return {a["artifact_id"]: a for a in timeline["artifacts"]}


def hops_by_agent(timeline):
    out = {}
    for h in timeline["hops"]:
        out.setdefault(h["agent"], []).append(h)
    return out


if __name__ == "__main__":
    import sys
    checks = {}
    tl = build("nonexistent-run")
    checks["missing run yields empty, not an exception"] = (
        tl["hops"] == [] and tl["events"] == [] and tl["agents"] == [])
    try:
        build("../etc/passwd")
        checks["path traversal in run id is rejected"] = False
    except ValueError:
        checks["path traversal in run id is rejected"] = True

    tl2 = from_records("x", [{"run_id": "x", "agent": "a", "round": 1, "hop": 0,
                               "final": True, "ts": 1.0}],
                        [{"kind": "run_start", "actor": "harness", "step": 0, "seq": 1,
                          "ts": 0.0, "problems": [{"id": "p1"}], "seeded_agent": "a"}])
    checks["from_records preserves run_start extra fields"] = (
        tl2["run_start"]["seeded_agent"] == "a" and tl2["problems"] == [{"id": "p1"}])

    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    raise SystemExit(0 if all(checks.values()) else 1)
