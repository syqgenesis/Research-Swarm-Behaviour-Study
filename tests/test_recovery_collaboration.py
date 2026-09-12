"""Offline regression tests for the observed lost-work and channel gaps."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest

from swarm import agentloop, analyse, client, config, grader, memory, problems, world
from swarm import run as runner
from tests.test_pull_memory import call_of, reply


@contextmanager
def replace_attr(obj, name, value):
    """Swap a local dependency without importing mock's network-related modules."""
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield value
    finally:
        setattr(obj, name, original)


def scripted(*results):
    responses = iter(results)

    def call(*args, **kwargs):
        call.call_count += 1
        return next(responses)
    call.call_count = 0
    return call


class RecoveryAndCollaboration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="runs")
        self.addCleanup(self.temp.cleanup)
        self.old_id = runner._run_id
        self.addCleanup(setattr, runner, "_run_id", self.old_id)
        self.addCleanup(setattr, config, "RUN_DIR", config.RUN_DIR)
        config.RUN_DIR = self.temp.name
        runner._close_logs()
        runner._run_id = "regression"
        runner.ABORT.clear()
        runner._reset_counters()
        self.addCleanup(runner.ABORT.clear)
        self.addCleanup(runner._close_logs)
        self.problem = problems.build_pool(20260911, level=8)[0]
        self.candidate = "4,7,14,26,27,38,39,47,48,49,63"
        self.world = world.World([self.problem], ["agent-01", "agent-02"])
        self.memory = memory.MemoryStore(os.path.join(self.temp.name, "private"),
                                         self.world.agent_ids)

    def cutoff(self, text):
        result = reply("")
        result.update(reasoning_content=text, finish_reason="length")
        result["usage"].update(completion_tokens=32000, reasoning_tokens=32000)
        return result

    def step(self, number=1):
        return runner._one_step(self.world, "agent-01", number, 0, {}, self.memory)

    def records(self, stream):
        path = Path(self.temp.name, "regression." + stream + ".jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_lost_hard_clique_survives_and_requires_agent_test_and_submission(self):
        self.assertTrue(grader.really_solves(self.candidate, self.problem))
        text = "earlier discarded work " * 10000 + "\n11-clique: " + self.candidate
        with replace_attr(client, "call_model", scripted(self.cutoff(text))) as api:
            turn = self.step()
        self.assertEqual(api.call_count, 1)
        self.assertFalse(turn["parse_ok"])
        self.assertEqual(turn["error"], "model output truncated")
        runner._apply_action(self.world, turn, 1)
        self.assertTrue(self.world.is_open(self.problem["id"]))
        self.assertFalse(self.world.board)
        self.assertEqual(self.memory.index("agent-01"), [])
        # Restore the store from disk: recovery cannot depend on process memory.
        self.memory = memory.MemoryStore(self.memory.root, self.world.agent_ids)
        prompt, _, exposure = agentloop.build_prompt(self.world, "agent-01", 2, memory=self.memory)
        self.assertIn(self.candidate, prompt[1]["content"])
        self.assertIn("unverified", prompt[1]["content"])
        self.assertEqual(exposure["board_ids"], [])
        peer, _, _ = agentloop.build_prompt(self.world, "agent-02", 2, memory=self.memory)
        self.assertNotIn(self.candidate, peer[1]["content"])
        stages = iter([
            reply(None, tool_calls=[call_of("test_candidates", {
                "task_id": self.problem["id"], "batch": [self.candidate]})]),
            reply(None, tool_calls=[call_of("submit_solution", {
                "task_id": self.problem["id"],
                "lines": problems.submission(self.problem, self.candidate).splitlines()})]),
            reply("saved and submitted")])

        def resumed(messages, **kwargs):
            self.assertIn(self.candidate, messages[1]["content"])
            return next(stages)

        with replace_attr(client, "call_model", resumed):
            resumed_turn = self.step(2)
        self.assertIsNone(resumed_turn["error"])
        self.assertFalse(self.world.is_open(self.problem["id"]))
        self.assertEqual(len(self.world.library), 1)

    def test_hit_receipt_survives_later_truncation_without_automatic_filing(self):
        results = [reply(None, tool_calls=[call_of("test_candidates", {
            "task_id": self.problem["id"], "batch": [self.candidate]})]),
            self.cutoff("More work " * 12000)]
        with replace_attr(client, "call_model", scripted(*results)):
            turn = self.step()
        saved = self.memory.recovery("agent-01")
        self.assertIn(self.candidate, saved)
        self.assertIn('hit', saved)
        self.assertEqual(turn["error"], "model output truncated")
        self.assertTrue(self.world.is_open(self.problem["id"]))
        self.assertFalse(self.world.library)

    def test_recovery_is_bounded_private_and_not_writable_as_agent_memory(self):
        with replace_attr(client, "call_model", scripted(self.cutoff("界" * 30000 + "TAIL"))):
            self.step()
        saved = self.memory.recovery("agent-01")
        self.assertLessEqual(len(saved.encode()), config.RECOVERY_MAX_BYTES)
        self.assertTrue(saved.endswith("TAIL\n"))
        self.assertEqual(self.memory.recovery("agent-02"), "")
        self.assertIsNone(self.memory.write("agent-01", config.RECOVERY_FILE, "replace", 2)[0])
        self.assertIsNone(self.memory.save_recovery("../other", "bad")[0])
        old = saved
        self.assertIsNone(self.memory.save_recovery("agent-01", "X" * (config.RECOVERY_MAX_BYTES + 1))[0])
        self.assertEqual(self.memory.recovery("agent-01"), old)

    def test_checkpoint_failure_stops_and_preserves_transcript(self):
        with replace_attr(client, "call_model", scripted(self.cutoff("candidate"))), \
                replace_attr(self.memory, "save_recovery", lambda *args: (None, "disk full")):
            turn = self.step()
        self.assertTrue(runner.ABORT.is_set())
        self.assertIn("disk full", turn["error"])
        self.assertEqual(len(self.records("transcripts")), 1)

    def test_api_failure_does_not_replace_existing_recovery(self):
        self.memory.save_recovery("agent-01", "prior work")
        with replace_attr(client, "call_model", scripted(reply(None, error="offline failure"))):
            self.step()
        self.assertEqual(self.memory.recovery("agent-01"), "prior work")

    def test_step_cap_keeps_executed_work_and_checkpoint(self):
        result = reply(None, tool_calls=[call_of("test_candidates", {
            "task_id": self.problem["id"], "batch": [self.candidate]})])
        with replace_attr(client, "call_model", scripted(result)), \
                replace_attr(config, "STEP_OUTPUT_SOFT_CAP", 1):
            turn = self.step()
        self.assertEqual(turn["error"], "step output cap reached")
        self.assertIn(self.candidate, self.memory.recovery("agent-01"))
        self.assertTrue(self.world.is_open(self.problem["id"]))

    def test_paper_categories_filter_real_posts_and_only_mark_results_read(self):
        first = runner._dispatch_action(self.world, "agent-01", 1, "post_intent", {
            "text": "partial clique", "intent_type": "building", "tag": self.problem["id"]})
        first_id = json.loads(first["content"])["post_id"]
        self.world.post("agent-01", "other direction", 1, intent_type="exploring", tag="other")
        out = agentloop.dispatch_tool(self.world, self.memory, "agent-02", 1,
            call_of("get_bulletin_board", {"intent_type": "building", "tag": self.problem["id"],
                                            "agent_id": "agent-01"}))
        posts = json.loads(out["content"])["posts"]
        self.assertEqual([p["id"] for p in posts], [first_id])
        self.assertEqual(posts[0]["intent_type"], "building")
        self.assertEqual(posts[0]["tag"], self.problem["id"])
        self.assertEqual(out["exposure"]["board_ids"], [first_id])
        self.assertEqual(self.world.badge("agent-02")["board_new"], 1)
        self.assertEqual(self.records("events")[0]["intent_type"], "building")

    def test_bad_metadata_has_no_side_effect_and_legacy_text_only_posts_work(self):
        for fields in ({"intent_type": "invented"}, {"intent_type": []}, {"tag": []}, {"tag": ""}):
            out = runner._dispatch_action(self.world, "agent-01", 1, "post_intent",
                                          dict(text="hello", **fields))
            self.assertFalse(out["ok"])
        self.assertEqual(self.world.board, [])
        self.assertTrue(runner._dispatch_action(self.world, "agent-01", 1, "post_intent",
                                               {"text": "legacy"})["ok"])

    def test_filtered_paging_can_reach_older_posts(self):
        for i in range(6):
            self.world.post("agent-01", str(i), 1, intent_type="building", tag="clique")
        recent, _ = self.world.read_board("agent-02", intent_type="building", tag="clique", limit=2)
        older, _ = self.world.read_board("agent-02", before_id=recent[0]["id"],
                                          intent_type="building", tag="clique", limit=2)
        self.assertEqual([p["text"] for p in recent], ["4", "5"])
        self.assertEqual([p["text"] for p in older], ["2", "3"])

    def test_batch_event_uses_actual_tool_timestamp(self):
        stages = [reply(None, tool_calls=[call_of("test_candidates", {
            "task_id": self.problem["id"], "batch": ["invalid"]})]), reply("done")]
        with replace_attr(client, "call_model", scripted(*stages)):
            turn = self.step()
        when = turn["tool_log"][0]["ts"]
        runner._apply_action(self.world, turn, 1)
        batch = next(e for e in self.records("events") if e.get("kind_detail") == "candidates")
        self.assertEqual(batch["ts"], when)

    def test_read_then_citation_requires_peer_read_and_chronology(self):
        events = [dict(kind="post", actor="a", artifact_id="post-0001", text="partial", ts=1),
                  dict(kind="post", actor="b", artifact_id="post-0002", text="post-0001", ts=2),
                  dict(kind="dm", actor="b", artifact_id="dm-0001", text="use post-0001", ts=4),
                  dict(kind="post", actor="c", artifact_id="post-0003", text="post-0001", ts=5)]
        calls = [dict(agent="b", step=1, finish_reason="length", raw_content="", parse_ok=True,
                      tool_calls=[dict(name="get_bulletin_board", ok=True, ts=3,
                                       artifact_ids=["post-0001"])])]
        metrics = analyse.activity_metrics(calls, events)
        self.assertEqual(metrics["read_then_citations"], 1)
        self.assertEqual(metrics["peer_artifact_reads"], 1)
        self.assertEqual(metrics["truncated_calls"], 1)
        self.assertEqual(metrics["steps_with_actions"], 0)
        self.assertEqual(analyse.activity_metrics([], [])["read_then_citations"], 0)


if __name__ == "__main__":
    unittest.main()
