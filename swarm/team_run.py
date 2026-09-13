"""Free-running asynchronous harness for the collaborative-team experiment."""
import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from swarm import client, config, problems, team_agent, team_config, team_metrics
from swarm.team_memory import TeamMemory
from swarm.team_world import TeamWorld


def _agent_ids(n):
    """Return stable human-readable agent ids for one run."""
    return [f"agent-{i:02d}" for i in range(1, n + 1)]


def _build_default_pool(seed, levels, problem_kinds=None):
    """Build the configured benchmark subset without exposing planted answers."""
    pool = []
    for level in levels:
        pool.extend(problems.build_pool(seed=seed, tier=level))
    if problem_kinds:
        wanted = tuple(problem_kinds)
        unknown = sorted(set(wanted) - {p["kind"] for p in pool})
        if unknown:
            raise ValueError(f"problem_kinds not present in selected levels: {', '.join(unknown)}")
        pool = [p for p in pool if p["kind"] in wanted]
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
               max_calls_per_turn, max_tokens, turn_pause, fatal_errors, fatal_lock,
               call_deadline=None):
    """Run one agent independently until the call cutoff or a fatal stop.

    ``deadline`` is the hard experiment deadline. ``call_deadline`` can be earlier: no
    new model calls start after it, leaving a drain window for calls already in flight.
    """
    if call_deadline is None:
        call_deadline = deadline
    step = 0
    try:
        while not stop_event.is_set() and time.monotonic() < call_deadline:
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
                if stop_event.is_set() or time.monotonic() >= call_deadline:
                    hit_call_cap = False
                    break
                try:
                    response = client.call_model(
                        messages,
                        max_tokens=max_tokens,
                        tools=team_agent.tool_schemas(world.reporting_enabled),
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

                # The provider call cannot be cancelled from this worker thread. If it
                # returns after the hard deadline (or after a global stop), keep the
                # accounting record but never execute its tool calls against the world.
                if time.monotonic() >= deadline:
                    summary_lines.append(
                        "model response arrived after the hard deadline; tool actions were ignored"
                    )
                    world.log_event(
                        "post_deadline_response_ignored",
                        {"call_index": call_index, "tool_calls": len(response.get("tool_calls") or [])},
                        agent_id,
                        step,
                    )
                    hit_call_cap = False
                    break

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

                # Collaboration should not compete with ordinary tool capacity. Allow
                # post_message/send_dm (plus set_focus) alongside the regular per-response
                # tool cap, while the world separately enforces six outbound messages/DMs
                # per turn. If any emitted calls are dropped, end the turn rather than
                # sending a provider follow-up with unresolved tool calls.
                collaboration_tools = {"post_message", "send_dm", "set_focus"}
                allowed_calls = []
                regular_used = 0
                collab_used = 0
                dropped = 0
                for tool_call in calls:
                    tool_name = ((tool_call or {}).get("function") or {}).get("name")
                    if tool_name in collaboration_tools:
                        # set_focus is cheap coordination and does not consume the outbound
                        # post/DM allowance; still cap the whole collaboration batch sanely.
                        collab_cap = team_config.COLLAB_ACTIONS_PER_TURN + 2
                        if collab_used < collab_cap:
                            allowed_calls.append(tool_call)
                            collab_used += 1
                        else:
                            dropped += 1
                    elif regular_used < team_config.MAX_TOOL_CALLS_PER_RESPONSE:
                        allowed_calls.append(tool_call)
                        regular_used += 1
                    else:
                        dropped += 1

                deadline_cut_tool_batch = False
                for offset, tool_call in enumerate(allowed_calls):
                    if time.monotonic() >= deadline:
                        remaining = len(allowed_calls) - offset
                        summary_lines.append(
                            f"hard deadline reached; {remaining} remaining tool action(s) were ignored"
                        )
                        world.log_event(
                            "deadline_tool_suppression",
                            {"remaining": remaining},
                            agent_id,
                            step,
                        )
                        deadline_cut_tool_batch = True
                        hit_call_cap = False
                        break
                    result, summary = team_agent.dispatch_tool(world, memory, agent_id, step, tool_call)
                    summary_lines.append(summary)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                if deadline_cut_tool_batch:
                    break
                if dropped:
                    summary_lines.append(
                        f"tool-call batch was capped using separate regular/collaboration allowances; {dropped} action(s) were ignored and the turn ended"
                    )
                    world.log_event(
                        "tool_call_cap",
                        {
                            "requested": len(calls),
                            "executed": len(allowed_calls),
                            "dropped": dropped,
                            "regular_executed": regular_used,
                            "collaboration_executed": collab_used,
                        },
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
                   levels=team_config.DEFAULT_LEVELS, problem_kinds=None, spend_cap_gbp=50.0,
                   max_calls_per_turn=team_config.MAX_MODEL_CALLS_PER_TURN,
                   max_tokens=team_config.CALL_MAX_TOKENS,
                   turn_pause=team_config.TURN_PAUSE_SECONDS,
                   cheating_agent_penalty=team_config.CHEATING_AGENT_PENALTY,
                   rescind_cheated_problem_points=team_config.RESCIND_CHEATED_PROBLEM_POINTS,
                   false_report_penalty=team_config.FALSE_REPORT_PENALTY,
                   reporting_enabled=True,
                   problem_pool=None, scheduled_notices=(),
                   call_drain_seconds=team_config.CALL_DRAIN_SECONDS,
                   shutdown_grace_seconds=team_config.SHUTDOWN_GRACE_SECONDS,
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
    if call_drain_seconds < 0:
        raise ValueError("call_drain_seconds cannot be negative")
    if shutdown_grace_seconds < 0:
        raise ValueError("shutdown_grace_seconds cannot be negative")

    run_seconds = minutes * 60
    effective_call_drain_seconds = min(float(call_drain_seconds), run_seconds * 0.20)

    if problem_pool is not None and problem_kinds:
        raise ValueError("problem_kinds cannot be combined with an explicit problem_pool")

    agent_ids = _agent_ids(n_agents)
    rewards = (
        team_config.report_reward_assignment(agent_ids, seed)
        if reporting_enabled
        else {agent_id: 0 for agent_id in agent_ids}
    )
    pool = (
        list(problem_pool)
        if problem_pool is not None
        else _build_default_pool(seed, tuple(levels), problem_kinds)
    )
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
        "harness_variant": team_config.HARNESS_VARIANT,
        "model": config.MODEL,
        "n_agents": n_agents,
        "minutes": minutes,
        "seed": seed,
        "levels": list(levels) if problem_pool is None else None,
        "problem_kinds": list(problem_kinds) if (problem_pool is None and problem_kinds) else None,
        "problem_ids": [p["id"] for p in pool],
        "reporting_enabled": bool(reporting_enabled),
        "report_rewards": rewards if reporting_enabled else {},
        "problem_points": team_config.PROBLEM_POINTS,
        "false_report_penalty": false_report_penalty,
        "cheating_agent_penalty": cheating_agent_penalty,
        "rescind_cheated_problem_points": rescind_cheated_problem_points,
        "max_calls_per_turn": max_calls_per_turn,
        "max_tokens": max_tokens,
        "collaboration": {
            "outbound_actions_per_turn": team_config.COLLAB_ACTIONS_PER_TURN,
            "message_max_chars": team_config.MESSAGE_MAX_CHARS,
            "silence_reminder_turns": team_config.COLLAB_SILENCE_REMINDER_TURNS,
        },
        "honeypot": {
            "command": team_config.HONEYPOT_COMMAND,
            "real_solution_count": team_config.HONEYPOT_REAL_SOLUTION_COUNT,
            "disclosed_problem_ids": [
                p["id"] for p in pool[:team_config.HONEYPOT_REAL_SOLUTION_COUNT]
            ],
        },
        "call_drain_seconds_requested": call_drain_seconds,
        "call_drain_seconds_effective": effective_call_drain_seconds,
        "shutdown_grace_seconds": shutdown_grace_seconds,
        "memory_limits": {
            "private_notes_chars": team_config.PRIVATE_NOTES_MAX_CHARS,
            "previous_turn_chars": team_config.PREVIOUS_TURN_SUMMARY_MAX_CHARS,
        },
        "prompt_static": {
            "team_framing": team_config.TEAM_FRAMING,
            "language_reference": team_config.LANGUAGE_REFERENCE,
            "interface_reference": team_config.INTERFACE_REFERENCE,
            "report_interface_reference": team_config.REPORT_INTERFACE_REFERENCE if reporting_enabled else None,
            "reporting_rules": team_config.REPORTING_RULES if reporting_enabled else None,
        },
        "scheduled_notices": notice_config,
    }, indent=2))

    world = TeamWorld(
        run_dir, pool, agent_ids, rewards,
        cheating_agent_penalty=cheating_agent_penalty,
        rescind_cheated_problem_points=rescind_cheated_problem_points,
        false_report_penalty=false_report_penalty,
        reporting_enabled=reporting_enabled,
    )
    memory = TeamMemory(run_dir / "memory", agent_ids)
    transcript_path = run_dir / "calls.jsonl"
    transcript_lock = threading.Lock()
    fatal_errors = []
    fatal_lock = threading.Lock()
    client.reset_spend()
    client.set_spend_cap(spend_cap_gbp)
    started_at = time.monotonic()
    deadline = started_at + run_seconds
    call_deadline = deadline - effective_call_drain_seconds
    stop_event = threading.Event()

    workers = [
        threading.Thread(
            target=_run_agent,
            name=agent_id,
            args=(world, memory, agent_id, deadline, stop_event, transcript_path, transcript_lock,
                  max_calls_per_turn, max_tokens, turn_pause, fatal_errors, fatal_lock, call_deadline),
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
        # Use one shared grace window rather than N sequential per-thread timeouts.
        # Normally the earlier call cutoff means workers have already drained.
        join_deadline = time.monotonic() + float(shutdown_grace_seconds)
        for worker in workers:
            remaining = max(0.0, join_deadline - time.monotonic())
            worker.join(timeout=remaining)
        if notice_thread is not None:
            remaining = max(0.0, join_deadline - time.monotonic())
            notice_thread.join(timeout=remaining)

    results = world.final_results()
    alive = [worker.name for worker in workers if worker.is_alive()]
    if alive:
        results["workers_still_finishing"] = alive
    if fatal_errors:
        results["fatal_errors"] = list(fatal_errors)
    (run_dir / "final_results.json").write_text(json.dumps(results, indent=2))
    world.log_event("run_end", results)
    try:
        metrics_path, metrics = team_metrics.write_behavior_metrics(run_dir)
        metrics["partial_due_to_live_workers"] = bool(alive)
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    except Exception as exc:
        metrics_path = None
        world.log_event("metrics_error", {"error": f"{type(exc).__name__}: {exc}"})
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "results": results,
        "behavior_metrics": str(metrics_path) if metrics_path is not None else None,
    }


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
    parser.add_argument(
        "--problem-kinds", nargs="+",
        choices=("clique", "subset_sum", "sat", "discrepancy", "vanderwaerden"),
        help="optional problem families within the selected levels (for example: subset_sum sat)",
    )
    parser.add_argument(
        "--reporting", choices=("on", "off"), default="on",
        help="enable or remove the peer-reporting interface and reporting rewards",
    )
    parser.add_argument("--spend-cap-gbp", type=float, default=50.0)
    parser.add_argument("--max-calls-per-turn", type=int, default=team_config.MAX_MODEL_CALLS_PER_TURN)
    parser.add_argument("--max-tokens", type=int, default=team_config.CALL_MAX_TOKENS)
    parser.add_argument(
        "--call-drain-seconds", type=float, default=team_config.CALL_DRAIN_SECONDS,
        help="stop starting new model calls this many seconds before the hard deadline",
    )
    parser.add_argument(
        "--shutdown-grace-seconds", type=float, default=team_config.SHUTDOWN_GRACE_SECONDS,
        help="shared grace window for in-flight calls after the hard deadline",
    )
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
        problem_kinds=args.problem_kinds,
        reporting_enabled=(args.reporting == "on"),
        spend_cap_gbp=args.spend_cap_gbp,
        max_calls_per_turn=args.max_calls_per_turn,
        max_tokens=args.max_tokens,
        call_drain_seconds=args.call_drain_seconds,
        shutdown_grace_seconds=args.shutdown_grace_seconds,
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
