"""Offline tests for the free-running loop: threads, races, budgets and stops.

The point of these is the properties that only exist once the round barrier is
gone. Sockets are already blocked package-wide by tests/__init__.py; every model
reply here is scripted.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from swarm import client
from swarm import config
from swarm import problems as problems_module
from swarm import run as run_module

from tests.test_pull_memory import build_pool, call_of, reply


class FreeRunning(unittest.TestCase):
    def setUp(self):
        self.real_call = client.call_model
        self.real_dir = config.RUN_DIR
        self.tmp = tempfile.mkdtemp(dir="runs")
        config.RUN_DIR = self.tmp
        run_module.ABORT.clear()
        run_module._close_logs()
        run_module._reset_counters()
        self.pool = build_pool(777000)
        self.easy = self.pool[0]
        self.honest = (self.easy["statement_block"].split("\n") + ["", config.MARK_SOL_BEGIN]
                       + ['DEF CANDIDATE = "%s"' % self.easy["planted_solution"],
                          config.MARK_SOL_END])

    def tearDown(self):
        client.call_model = self.real_call
        run_module._close_logs()
        run_module.ABORT.clear()
        config.RUN_DIR = self.real_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def records(self, run_id, stream):
        path = os.path.join(self.tmp, "%s.%s.jsonl" % (run_id, stream))
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def answer(self, tool_calls=None, delay=0.0):
        """A stub that plays one round of tool calls, then ends the step.

        Every action is a tool call now, so a step is: optionally call tools,
        then reply with a note and no tool call.
        """
        state = {}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            if delay:
                time.sleep(delay)
            key = threading.current_thread().name, len([m for m in messages
                                                        if m.get("role") == "assistant"])
            if tool_calls and key[1] == 0:
                return reply(None, tool_calls=list(tool_calls))
            return reply("done")
        client.call_model = call_model

    # ------------------------------------------------------------------ tests
    def test_two_agents_racing_one_instance_produce_exactly_one_winner(self):
        """With agents free-running this is a real race on world.lock_problem
        rather than a replay sorted by timestamp, so it is worth pinning."""
        self.answer([call_of("submit_solution",
                             {"task_id": self.easy["id"], "lines": self.honest}, "s1")])
        run_module.run("race", 4, 1, 777000, minutes=1)
        events = self.records("race", "events")
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds.count("accept"), 1)
        self.assertEqual(kinds.count("lock"), 1)
        self.assertEqual(kinds.count("library_commit"), 1)
        self.assertEqual(len([e for e in events if e["kind"] == "reject"]), 3)
        self.assertTrue(all((e.get("verdict") or {}).get("reason") in ("sniped", "locked")
                            for e in events if e["kind"] == "reject"))

    def test_a_slow_agent_does_not_hold_up_a_fast_one(self):
        """The whole point of dropping the round barrier: no agent waits."""
        order = []
        lock = threading.Lock()

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            who = threading.current_thread().name
            if who == "agent-01":
                time.sleep(0.25)
            with lock:
                order.append(who)
            return reply("done")

        client.call_model = call_model
        world = run_module.run("pace", 2, 3, 777000, minutes=1)
        self.assertEqual(world.steps["agent-01"], 3)
        self.assertEqual(world.steps["agent-02"], 3)
        # The fast agent got through several steps before the slow one finished
        # its first, which could not happen under a round barrier.
        self.assertGreater(order.index("agent-01"), 1)

    def test_each_agent_stops_at_its_own_step_budget(self):
        self.answer()
        world = run_module.run("budget", 3, 4, 777000, minutes=1)
        self.assertEqual(set(world.steps.values()), {4})
        steps = {(c["agent"], c["step"]) for c in self.records("budget", "calls")}
        self.assertEqual(len(steps), 12)
        self.assertEqual(max(s for _, s in steps), 4)

    def test_the_stop_file_halts_every_agent_and_is_recorded_once(self):
        seen = {"n": 0}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            seen["n"] += 1
            if seen["n"] == 2:
                open(os.path.join(self.tmp, "stopme" + config.STOP_FILE_SUFFIX), "w").close()
            time.sleep(0.02)
            return reply("working")

        client.call_model = call_model
        run_module.run("stopme", 3, 10, 777000, minutes=1)
        events = self.records("stopme", "events")
        self.assertTrue(run_module.ABORT.is_set())
        self.assertEqual(len([e for e in events if e["kind"] == "stop"]), 1)
        self.assertLess(max(c["step"] for c in self.records("stopme", "calls")), 10)

    def test_the_wall_clock_ceiling_halts_the_run_and_keeps_its_data(self):
        self.answer(delay=0.12)
        started = time.time()
        run_module.run("clock", 2, 200, 777000, minutes=1.0 / 60)      # a one-second run
        elapsed = time.time() - started
        self.assertLess(elapsed, 20)
        self.assertTrue(run_module.ABORT.is_set())
        calls = self.records("clock", "calls")
        self.assertTrue(calls, "steps completed before the ceiling must still be logged")
        self.assertIn("stop", [e["kind"] for e in self.records("clock", "events")])

    def test_agents_keep_going_after_the_pool_is_empty(self):
        """The paper spends half its length on what happens AFTER the last
        problem is claimed. A loop that exits at depletion cannot see it."""
        pool_ids = [p["id"] for p in self.pool]
        state = {"n": 0}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            if any(m.get("role") == "assistant" for m in messages):
                return reply("done")
            state["n"] += 1
            if state["n"] <= len(pool_ids):
                problem = self.pool[state["n"] - 1]
                lines = (problem["statement_block"].split("\n") + ["", config.MARK_SOL_BEGIN]
                         + ['DEF CANDIDATE = "%s"' % problem["planted_solution"],
                            config.MARK_SOL_END])
                return reply(None, tool_calls=[call_of(
                    "submit_solution", {"task_id": problem["id"], "lines": lines}, "s1")])
            return reply(None, tool_calls=[call_of(
                "post_intent", {"text": "nothing left to claim; this is unfair"}, "p1")])

        client.call_model = call_model
        # One scripted claim per step, then two opportunities after depletion.
        # A fixed 16 total steps no longer clears a forty-task pool.
        steps = len(pool_ids) + 2
        world = run_module.run("after", 1, steps, 777000, minutes=1)
        self.assertEqual(set(world.steps.values()), {steps})
        self.assertEqual(world.open_problems(), [])
        posts = [e for e in self.records("after", "events") if e["kind"] == "post"]
        self.assertTrue(posts, "agents must still be able to act once the pool is gone")

    def test_the_cooldown_counts_the_agents_own_steps(self):
        self.answer([call_of("submit_solution",
                             {"task_id": self.pool[3]["id"], "lines": ["nonsense"]}, "s1")])
        run_module.run("cool", 1, 4, 777000, minutes=1)
        events = self.records("cool", "events")
        cooled = [e for e in events
                  if e["kind"] == "reject" and (e.get("verdict") or {}).get("reason") == "cooldown"]
        self.assertEqual(len(cooled), config.SUBMIT_COOLDOWN_STEPS - 1)

    def test_a_submission_verdict_arrives_inside_the_step(self):
        """It used to reach the agent in the NEXT prompt. Now the tool answers."""
        seen = {}
        lock = threading.Lock()

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            who = threading.current_thread().name
            for message in messages:
                if message.get("role") == "tool" and "accepted" in (message.get("content") or ""):
                    with lock:
                        seen.setdefault(who, []).append(json.loads(message["content"]))
            if who == "agent-01" and not any(m.get("role") == "assistant" for m in messages):
                return reply(None, tool_calls=[call_of(
                    "submit_solution", {"task_id": self.easy["id"], "lines": self.honest}, "s1")])
            return reply("watching")

        client.call_model = call_model
        run_module.run("verdict", 2, 1, 777000, minutes=1)
        self.assertTrue(seen["agent-01"][0]["accepted"])
        self.assertEqual(set(seen["agent-01"][0]) , {"accepted", "verdict", "problem"})
        self.assertNotIn("agent-02", seen)

    def test_each_agents_history_carries_only_its_own_actions(self):
        prompts = {}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            who = threading.current_thread().name
            prompts.setdefault(who, []).append(messages[1]["content"])
            if who == "agent-01" and not any(m.get("role") == "assistant" for m in messages):
                return reply(None, tool_calls=[call_of(
                    "post_intent", {"text": "mine alone"}, "p1")])
            return reply("watching")

        client.call_model = call_model
        run_module.run("history", 2, 2, 777000, minutes=1)
        step2 = {who: [p for p in seen if "This is your step 2." in p]
                 for who, seen in prompts.items()}
        self.assertIn("posted to the board", step2["agent-01"][0])
        self.assertNotIn("posted to the board", step2["agent-02"][0])

    def test_every_record_is_ordered_and_one_final_closes_each_step(self):
        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            if len(messages) == 2:
                return reply(None, tool_calls=[call_of("list_memory")])
            return reply("done")

        client.call_model = call_model
        run_module.run("order", 3, 2, 777000, minutes=1)
        calls = self.records("order", "calls")
        seqs = [c["seq"] for c in calls]
        self.assertEqual(len(set(seqs)), len(seqs))
        self.assertEqual(seqs, sorted(seqs))
        for agent in ("agent-01", "agent-02", "agent-03"):
            for step in (1, 2):
                own = [c for c in calls if c["agent"] == agent and c["step"] == step]
                self.assertEqual(len([c for c in own if c["final"]]), 1)
                self.assertEqual(len(own), 2)
        self.assertIn("tool_call", [e["kind"] for e in self.records("order", "events")])

    def test_transcript_flushes_exact_requests_responses_and_tool_replies(self):
        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            if len(messages) == 2:
                return reply(None, tool_calls=[call_of("get_messages", {}, "m1")])
            return reply("finished")

        client.call_model = call_model
        run_module.run("transcript", 1, 1, 777000, minutes=1)
        records = self.records("transcript", "transcripts")
        self.assertEqual(len(records), 2)
        first, second = records
        self.assertEqual(first["response"]["reasoning_content"], "R")
        self.assertEqual(first["response"]["assistant_message"]["tool_calls"][0]["id"], "m1")
        self.assertEqual(first["tool_results"][0]["content"],
                         second["request"]["messages"][-1]["content"])
        self.assertEqual(second["response"]["content"], "finished")
        self.assertEqual(first["request"]["tools"], config.TOOL_SCHEMAS)

    def test_a_memory_directory_is_created_per_run_and_per_agent(self):
        self.answer()
        run_module.run("mem", 2, 1, 777000, minutes=1)
        root = os.path.join(self.tmp, "mem" + config.MEMORY_DIR_SUFFIX)
        self.assertTrue(os.path.isdir(os.path.join(root, "agent-01", "wiki")))
        self.assertTrue(os.path.isdir(os.path.join(root, "agent-02", "wiki")))

    def test_no_step_cap_runs_until_the_stop_file(self):
        """The paper's run had no step budget; it ended when its operators ended
        it. steps=0 is that mode, and the stop file is the operator."""
        seen = {"n": 0}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            seen["n"] += 1
            if seen["n"] == 7:
                open(os.path.join(self.tmp, "forever" + config.STOP_FILE_SUFFIX), "w").close()
            time.sleep(0.02)
            return reply("on and on")

        client.call_model = call_model
        world = run_module.run("forever", 2, 0, 777000, minutes=1)
        self.assertGreaterEqual(max(world.steps.values()), 3)
        self.assertIn("stop", [e["kind"] for e in self.records("forever", "events")])

    def test_no_time_ceiling_leaves_the_step_cap_in_charge(self):
        self.answer()
        world = run_module.run("untimed", 2, 3, 777000, minutes=0)
        self.assertEqual(set(world.steps.values()), {3})
        self.assertNotIn("stop", [e["kind"] for e in self.records("untimed", "events")])

    def test_the_prompt_never_states_a_step_horizon(self):
        prompts = []

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            prompts.append(messages[1]["content"])
            return reply("done")

        client.call_model = call_model
        run_module.run("horizon", 1, 2, 777000, minutes=1)
        self.assertIn("This is your step 2.", prompts[1])
        self.assertNotIn(" of 2.", prompts[1])
        self.assertNotIn("of 12", prompts[1])

    def test_a_live_batch_is_checked_mid_step_and_shares_the_budget(self):
        """Try, learn, try again inside one step: the tool answers at once, and
        the per-step budget is one pot across the tool and the json field."""
        easy = self.easy
        state = {"n": 0}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            state["n"] += 1
            if state["n"] == 1:
                return reply(None, tool_calls=[call_of(
                    "test_candidates", {"task_id": easy["id"], "batch": ["a", "b", "c"]}, "t1")])
            if state["n"] == 2:
                last = messages[-1]["content"]
                assert '"tested": 3' in last and '"budget_remaining"' in last, last
                return reply(None, tool_calls=[call_of(
                    "test_candidates",
                    {"task_id": easy["id"], "batch": ["d"] * 200}, "t2")])
            if state["n"] == 3:
                assert '"budget_remaining": 0' in messages[-1]["content"], messages[-1]
                return reply(None, tool_calls=[call_of(
                    "test_candidates", {"task_id": easy["id"], "batch": ["late"]}, "t3")])
            return reply("done")

        client.call_model = call_model
        run_module.run("live", 1, 1, 777000, minutes=1)
        events = self.records("live", "events")
        batches = [e for e in events if e["kind"] == "submit"
                   and e.get("kind_detail") == "candidates"]
        tested = [e["verdict"]["tested"] for e in batches]
        self.assertEqual(sum(tested), config.CANDIDATE_CAP)
        self.assertTrue(all(e["verdict"].get("live") for e in batches))
        self.assertEqual(len([e for e in events if e["kind"] == "tool_call"
                              and e["kind_detail"] == "test_candidates"]), 3)
        self.assertEqual(len([e for e in events if e["kind"] == "tool_call"
                              and e["kind_detail"] == "test_candidates"
                              and e["verdict"]["ok"]]), 3)

    def test_a_live_hit_can_be_claimed_in_the_same_step(self):
        easy = self.easy
        state = {"n": 0}

        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            state["n"] += 1
            if state["n"] == 1:
                return reply(None, tool_calls=[call_of(
                    "test_candidates", {"task_id": easy["id"],
                                        "batch": ["nope", easy["planted_solution"]]}, "t1")])
            payload = json.loads(messages[-1]["content"])
            if not payload.get("hit"):
                return reply("nothing")
            lines = (easy["statement_block"].split("\n") + ["", config.MARK_SOL_BEGIN]
                     + ['DEF CANDIDATE = "%s"' % payload["hit"], config.MARK_SOL_END])
            return reply(None, tool_calls=[call_of(
                "submit_solution", {"task_id": easy["id"], "lines": lines}, "s1")])

        client.call_model = call_model
        world = run_module.run("claim", 1, 1, 777000, minutes=1)
        self.assertFalse(world.is_open(easy["id"]))
        kinds = [e["kind"] for e in self.records("claim", "events")]
        self.assertIn("accept", kinds)
        self.assertIn("library_commit", kinds)

    def test_a_stale_stop_file_does_not_kill_the_next_run(self):
        open(os.path.join(self.tmp, "fresh" + config.STOP_FILE_SUFFIX), "w").close()
        self.answer()
        world = run_module.run("fresh", 1, 2, 777000, minutes=1)
        self.assertEqual(world.steps["agent-01"], 2)


if __name__ == "__main__":
    unittest.main()
