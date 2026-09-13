"""Free-running asynchronous harness for the collaborative-team experiment."""
import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from swarm import client, config, problems, team_agent, team_config
from swarm.team_memory import TeamMemory
from swarm.team_world import TeamWorld


def _agent_ids(n):
    """Return stable human-readable agent ids for one run."""
    return [f"agent-{i:02d}" for i in range(1, n + 1)]


def _build_default_pool(seed, levels):
    """Build the configured benchmark subset without exposing planted answers."""
    pool = []
    for level in levels:
        pool.extend(problems.build_pool(seed=seed, tier=level))
    return pool


def _append_jsonl(path, record, lock):
    """Append one model-call record atomically across all agent threads."""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


def _assistant_message(response):
    """Return the exact assistant turn that must be echoed after a tool call."""
    msg = response.get("assistant_message")
    if isinstance(msg, dict):
        return msg
    content = response.get("content")
    return {"role": "assistant", "content": content or ""}


def _record_fatal(world, fatal_errors, fatal_lock, agent_id, step, exc):
    """Record an unexpected worker failure without silently losing an agent."""
    text = f"{type(exc).__name__}: {exc}"
    with fatal_lock:
        fatal_errors.append(f"{agent_id} step {step}: {text}")
    world.log_event("fatal", {"error": text}, agent_id, step)


def _run_agent(world, memory, agent_id, deadline, stop_event, log_path, log_lock,
               max_calls_per_turn, max_tokens, turn_pause, fatal_errors, fatal_lock):
    """Run one agent independently until the shared deadline or a fatal stop."""
    step = 0
    try:
        while not stop_event.is_set() and time.monotonic() < deadline:
            step += 1
            world.reset_turn_budget(agent_id, step)
            messages, delivered = team_agent.build_prompt(
                world, memory, agent_id, step, include_meta=True
            )
            summary_lines = list(delivered)
            world.log_event("turn_start", {}, agent_id, step)
            hit_call_cap = True

            # A turn can contain several model -> tool -> model hops. The hop cap
            # bounds cost while still letting an agent act on tool results immediately.
            for call_index in range(1, max_calls_per_turn + 1):
                if stop_event.is_set() or time.monotonic() >= deadline:
                    hit_call_cap = False
                    break
                try:
                    response = client.call_model(
                        messages,
                        max_tokens=max_tokens,
                        tools=team_agent.TOOL_SCHEMAS,
                        tool_choice="auto",
                    )
                except config.SpendCapExceeded as exc:
                    _record_fatal(world, fatal_errors, fatal_lock, agent_id, step, exc)
                    stop_event.set()
                    return

                _append_jsonl(log_path, {
                    "agent_id": agent_id,
                    "step": step,
                    "call_index": call_index,
                    "content": response.get("content"),
                    "reasoning_content": response.get("reasoning_content"),
                    "usage": response.get("usage"),
                    "cost_gbp": response.get("cost_gbp"),
                    "latency_s": response.get("latency_s"),
                    "error": response.get("error"),
                    "finish_reason": response.get("finish_reason"),
                    "tool_calls": response.get("tool_calls"),
                }, log_lock)

                if response.get("error"):
                    summary_lines.append("model call failed; this turn ended")
                    hit_call_cap = False
                    break

                messages.append(_assistant_message(response))
                calls = response.get("tool_calls") or []
                if not calls:
                    note = (response.get("content") or "").strip()
                    if note:
                        summary_lines.append("final note: " + note[:500].replace("\n", " "))
                    if response.get("finish_reason") == "length":
                        summary_lines.append("model output hit its token limit before another action")
                        tail = (response.get("reasoning_content") or "")[-team_config.TRUNCATED_REASONING_TAIL_CHARS:]
                        if tail:
                            summary_lines.append("tail of own truncated reasoning: " + tail.replace("\n", " "))
                    hit_call_cap = False
                    break

                # Never send a follow-up request containing unresolved tool calls.
                # If the model emits too many at once, execute the allowed prefix and
                # end the turn; otherwise the provider would reject the next request.
                too_many_calls = len(calls) > team_config.MAX_TOOL_CALLS_PER_RESPONSE
                for tool_call in calls[:team_config.MAX_TOOL_CALLS_PER_RESPONSE]:
                    result, summary = team_agent.dispatch_tool(world, memory, agent_id, step, tool_call)
                    summary_lines.append(summary)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                if too_many_calls:
                    summary_lines.append(
                        f"tool-call batch was capped at {team_config.MAX_TOOL_CALLS_PER_RESPONSE}; turn ended"
                    )
                    world.log_event(
                        "tool_call_cap",
                        {"requested": len(calls), "executed": team_config.MAX_TOOL_CALLS_PER_RESPONSE},
                        agent_id,
                        step,
                    )
                    hit_call_cap = False
                    break

            if hit_call_cap:
                summary_lines.append(f"turn reached the {max_calls_per_turn}-model-call cap")
            memory.save_previous_turn(agent_id, summary_lines)
            world.log_event("turn_end", {"summary": summary_lines}, agent_id, step)
            if turn_pause > 0:
                stop_event.wait(turn_pause)
    except Exception as exc:  # One bad agent should not silently disappear.
        _record_fatal(world, fatal_errors, fatal_lock, agent_id, step, exc)


def _run_scheduled_notices(world, notices, started_at, stop_event, fatal_errors, fatal_lock):
    """Deliver optional researcher-configured interventions at deterministic delays."""
    for item in sorted(notices, key=lambda x: float(x.get("after_seconds", 0))):
        delay = max(0.0, float(item.get("after_seconds", 0)))
        remaining = started_at + delay - time.monotonic()
        if remaining > 0 and stop_event.wait(remaining):
            return
        if stop_event.is_set():
            return
        try:
            notice_id = world.send_organiser_notice(
                item.get("agent_id"), item.get("text"), item.get("label", "intervention")
            )
            if notice_id is None:
                raise ValueError("invalid scheduled organiser notice")
        except Exception as exc:
            _record_fatal(world, fatal_errors, fatal_lock, "organiser", 0, exc)
            return


def run_experiment(*, n_agents=team_config.N_AGENTS, minutes=60, seed=20260911,
                   levels=team_config.DEFAULT_LEVELS, spend_cap_gbp=50.0,
                   max_calls_per_turn=team_config.MAX_MODEL_CALLS_PER_TURN,
                   max_tokens=team_config.CALL_MAX_TOKENS,
                   turn_pause=team_config.TURN_PAUSE_SECONDS,
                   cheating_agent_penalty=team_config.CHEATING_AGENT_PENALTY,
                   rescind_cheated_problem_points=team_config.RESCIND_CHEATED_PROBLEM_POINTS,
                   false_report_penalty=team_config.FALSE_REPORT_PENALTY,
                   problem_pool=None, scheduled_notices=(),
                   run_root="runs", run_name=None):
    """Run the team experiment and return paths plus researcher-only final results."""
    if n_agents < 2:
        raise ValueError("n_agents must be at least 2")
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if max_calls_per_turn < 1:
        raise ValueError("max_calls_per_turn must be positive")
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")

    agent_ids = _agent_ids(n_agents)
    rewards = team_config.report_reward_assignment(agent_ids, seed)
    pool = list(problem_pool) if problem_pool is not None else _build_default_pool(seed, tuple(levels))
    if not pool or len({p["id"] for p in pool}) != len(pool):
        raise ValueError("problem pool must be non-empty and contain unique ids")

    # Include microseconds so two scripted trial runs started in one second do not collide.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = run_name or f"team-{stamp}"
    run_dir = Path(run_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    notice_config = [dict(x) for x in scheduled_notices]
    (run_dir / "run_config.json").write_text(json.dumps({
        "run_id": run_id,
        "n_agents": n_agents,
        "minutes": minutes,
        "seed": seed,
        "levels": list(levels) if problem_pool is None else None,
        "problem_ids": [p["id"] for p in pool],
        "report_rewards": rewards,
        "problem_points": team_config.PROBLEM_POINTS,
        "false_report_penalty": false_report_penalty,
        "cheating_agent_penalty": cheating_agent_penalty,
        "rescind_cheated_problem_points": rescind_cheated_problem_points,
        "max_calls_per_turn": max_calls_per_turn,
        "max_tokens": max_tokens,
        "scheduled_notices": notice_config,
    }, indent=2))

    world = TeamWorld(
        run_dir, pool, agent_ids, rewards,
        cheating_agent_penalty=cheating_agent_penalty,
        rescind_cheated_problem_points=rescind_cheated_problem_points,
        false_report_penalty=false_report_penalty,
    )
    memory = TeamMemory(run_dir / "memory", agent_ids)
    transcript_path = run_dir / "calls.jsonl"
    transcript_lock = threading.Lock()
    fatal_errors = []
    fatal_lock = threading.Lock()
    client.reset_spend()
    client.set_spend_cap(spend_cap_gbp)
    started_at = time.monotonic()
    deadline = started_at + minutes * 60
    stop_event = threading.Event()

    workers = [
        threading.Thread(
            target=_run_agent,
            name=agent_id,
            args=(world, memory, agent_id, deadline, stop_event, transcript_path, transcript_lock,
                  max_calls_per_turn, max_tokens, turn_pause, fatal_errors, fatal_lock),
            daemon=True,
        )
        for agent_id in agent_ids
    ]
    notice_thread = None
    if notice_config:
        notice_thread = threading.Thread(
            target=_run_scheduled_notices,
            name="organiser-interventions",
            args=(world, notice_config, started_at, stop_event, fatal_errors, fatal_lock),
            daemon=True,
        )

    world.log_event("run_start", {"agent_ids": agent_ids, "problem_ids": [p["id"] for p in pool]})
    try:
        for worker in workers:
            worker.start()
        if notice_thread is not None:
            notice_thread.start()

        # Poll the free-running workers rather than introducing any round barrier.
        while any(worker.is_alive() for worker in workers):
            if time.monotonic() >= deadline:
                stop_event.set()
                break
            if stop_event.wait(0.25):
                break
    except KeyboardInterrupt:
        stop_event.set()
        raise
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=2)
        if notice_thread is not None:
            notice_thread.join(timeout=2)

    results = world.final_results()
    alive = [worker.name for worker in workers if worker.is_alive()]
    if alive:
        results["workers_still_finishing"] = alive
    if fatal_errors:
        results["fatal_errors"] = list(fatal_errors)
    (run_dir / "final_results.json").write_text(json.dumps(results, indent=2))
    world.log_event("run_end", results)
    return {"run_id": run_id, "run_dir": str(run_dir), "results": results}


def _load_scheduled_notices(path):
    """Load optional intervention notices from a researcher-owned JSON file."""
    if path is None:
        return []
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("interventions JSON must be a list")
    notices = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each intervention must be an object")
        notices.append({
            "after_seconds": float(item.get("after_seconds", item.get("after_minutes", 0) * 60)),
            "agent_id": item.get("agent_id"),
            "label": item.get("label", "intervention"),
            "text": item.get("text"),
        })
    return notices


def main(argv=None):
    """CLI entry point for configurable trial and full runs."""
    parser = argparse.ArgumentParser(description="Run the collaborative-team AI safety experiment")
    parser.add_argument("--agents", type=int, default=team_config.N_AGENTS)
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--levels", nargs="+", default=list(team_config.DEFAULT_LEVELS))
    parser.add_argument("--spend-cap-gbp", type=float, default=50.0)
    parser.add_argument("--max-calls-per-turn", type=int, default=team_config.MAX_MODEL_CALLS_PER_TURN)
    parser.add_argument("--max-tokens", type=int, default=team_config.CALL_MAX_TOKENS)
    parser.add_argument("--turn-pause", type=float, default=team_config.TURN_PAUSE_SECONDS)
    parser.add_argument("--cheating-penalty", type=int, default=team_config.CHEATING_AGENT_PENALTY)
    parser.add_argument("--false-report-penalty", type=int, default=team_config.FALSE_REPORT_PENALTY)
    parser.add_argument("--no-rescind-cheated-points", action="store_true")
    parser.add_argument(
        "--interventions-json",
        help="optional researcher-only JSON list of timed organiser notices",
    )
    parser.add_argument("--run-root", default="runs")
    parser.add_argument("--run-name")
    args = parser.parse_args(argv)
    result = run_experiment(
        n_agents=args.agents,
        minutes=args.minutes,
        seed=args.seed,
        levels=args.levels,
        spend_cap_gbp=args.spend_cap_gbp,
        max_calls_per_turn=args.max_calls_per_turn,
        max_tokens=args.max_tokens,
        turn_pause=args.turn_pause,
        cheating_agent_penalty=args.cheating_penalty,
        false_report_penalty=args.false_report_penalty,
        rescind_cheated_problem_points=not args.no_rescind_cheated_points,
        scheduled_notices=_load_scheduled_notices(args.interventions_json),
        run_root=args.run_root,
        run_name=args.run_name,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
