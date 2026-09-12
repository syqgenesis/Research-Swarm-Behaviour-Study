"""Offline tests for the pull channels, the memory files and the hop loop.

tests/__init__.py has already blocked sockets for the whole package, so nothing
here can reach the network even by mistake. Every test that needs a model reply
patches swarm.client.call_model with a scripted fake.

tests/test_swarm.py stays frozen; this is the new surface only.
"""
import json
import os
import shutil
import tempfile
import unittest

from swarm import agentloop
from swarm import client
from swarm import config
from swarm import memory as memory_module
from swarm import problems as problems_module
from swarm import run as run_module
from swarm import world as world_module


def build_pool(seed=555000):
    pool, s = [], seed
    for tier, count, kwargs in config.POOL:
        for _ in range(count):
            pool.append(problems_module.generate_problem(s, dict(kwargs, tier=tier)))
            s += 1
    return pool


def reply(content, tool_calls=None, cost=0.0001, error=None):
    """A call_model-shaped dict, including the three keys tool calling added."""
    message = {"role": "assistant", "content": content, "reasoning_content": "R"}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"content": content, "reasoning_content": "R",
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100,
                      "reasoning_tokens": 5},
            "latency_s": 0.01, "cost_gbp": cost, "error": error,
            "tool_calls": tool_calls, "assistant_message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop"}


def call_of(name, arguments=None, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {})}}


class PullChannels(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool()
        self.world = world_module.World(self.pool, ["agent-01", "agent-02", "agent-03"])
        self.tmp = tempfile.mkdtemp()
        self.memory = memory_module.MemoryStore(self.tmp, self.world.agent_ids)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dispatch(self, name, agent="agent-01", step=1, **args):
        return agentloop.dispatch_tool(self.world, self.memory, agent, step,
                                       call_of(name, args))

    def test_tool_schemas_are_identical_for_every_agent_and_step(self):
        """They are part of the DeepSeek cache prefix. One agent-specific byte
        and the prefix fragments, costing ~50x on those tokens."""
        first, _, _ = agentloop.build_prompt(self.world, "agent-01", 1, memory=self.memory)
        second, _, _ = agentloop.build_prompt(self.world, "agent-02", 7, memory=self.memory)
        self.assertEqual(first[0]["content"], second[0]["content"])
        self.assertIs(agentloop.tool_schemas(), agentloop.tool_schemas())
        self.assertEqual([t["function"]["name"] for t in agentloop.tool_schemas()],
                         list(config.TOOL_NAMES))

    def test_the_prompt_carries_counts_and_never_the_content(self):
        self.world.post("agent-02", "TAKING THE THIRTEEN BIT SET", 1)
        self.world.send_dm("agent-03", "agent-01", "SECRET MESSAGE TEXT", 1)
        self.world.commit_to_library("agent-02", self.pool[0]["id"], "DEF solved(x) = 1", 1)
        messages, _, exposure = agentloop.build_prompt(self.world, "agent-01", 2,
                                                       memory=self.memory)
        body = messages[1]["content"]
        for secret in ("TAKING THE THIRTEEN BIT SET", "SECRET MESSAGE TEXT",
                       "DEF solved(x) = 1"):
            self.assertNotIn(secret, body)
        self.assertIn("1 new post by other researchers", body)
        self.assertIn("1 unread", body)
        self.assertIn("1 new accepted submission", body)
        self.assertEqual(exposure["board_ids"], [])
        self.assertEqual(exposure["dm_ids"], [])
        self.assertEqual(exposure["library_ids"], [])
        # Building a prompt must have no side effect on the world at all.
        self.assertEqual(self.world.badge("agent-01")["dm_unread"], 1)

    def test_messages_are_marked_read_at_pull_time_and_only_once(self):
        self.world.send_dm("agent-02", "agent-01", "hello", 1)
        first = json.loads(self.dispatch("get_messages")["content"])
        self.assertEqual(len(first["messages"]), 1)
        self.assertEqual(self.world.badge("agent-01")["dm_unread"], 0)
        second = json.loads(self.dispatch("get_messages")["content"])
        self.assertEqual(second["messages"], [])
        again = json.loads(self.dispatch("get_messages", unread_only=False)["content"])
        self.assertEqual(len(again["messages"]), 1)

    def test_the_board_badge_is_exact_under_every_filter(self):
        for i in range(1, 6):
            self.world.post("agent-02", "post %d" % i, i)
        self.world.post("agent-01", "my own", 6)
        self.assertEqual(self.world.badge("agent-01")["board_new"], 5)

        limited = json.loads(self.dispatch("get_bulletin_board", limit=2)["content"])
        self.assertEqual([p["text"] for p in limited["posts"]], ["post 5", "my own"])
        self.assertEqual(limited["omitted_older"], 4)
        self.assertEqual(self.world.badge("agent-01")["board_new"], 4)

        clamped = json.loads(self.dispatch("get_bulletin_board", limit=10 ** 6)["content"])
        self.assertLessEqual(len(clamped["posts"]), config.BOARD_PULL_MAX)

        older = json.loads(self.dispatch("get_bulletin_board", before_id="post-0005",
                                         limit=2)["content"])
        self.assertEqual([p["text"] for p in older["posts"]], ["post 3", "post 4"])

        filtered = json.loads(self.dispatch("get_bulletin_board", agent_id="agent-01",
                                            agent="agent-03")["content"])
        self.assertEqual([p["agent"] for p in filtered["posts"]], ["agent-01"])

        bad = self.dispatch("get_bulletin_board", since_id="not-an-id")
        self.assertFalse(bad["ok"])
        self.assertIn("post-0007", bad["error"])

    def test_the_library_reads_newest_first_and_pages(self):
        for i in range(1, 5):
            self.world.commit_to_library("agent-02", self.pool[i]["id"], "file %d" % i, i)
        newest = json.loads(self.dispatch("get_library", limit=2)["content"])
        self.assertEqual([e["text"] for e in newest["entries"]], ["file 4", "file 3"])
        self.assertEqual(self.world.badge("agent-01")["library_new"], 2)
        older = json.loads(self.dispatch("get_library", before_id="lib-0003")["content"])
        self.assertEqual([e["text"] for e in older["entries"]], ["file 2", "file 1"])
        self.assertEqual(self.world.badge("agent-01")["library_new"], 0)
        # Another agent's watermark is its own.
        self.assertEqual(self.world.badge("agent-03")["library_new"], 4)

    def test_exposure_is_exactly_what_the_tools_returned(self):
        self.world.post("agent-02", "a", 1)
        self.world.post("agent-02", "b", 1)
        self.world.post("agent-02", "c", 1)
        self.world.commit_to_library("agent-02", self.pool[0]["id"], "f", 1)
        board = self.dispatch("get_bulletin_board", limit=2)
        library = self.dispatch("get_library")
        merged = agentloop.merge_exposure(agentloop.empty_exposure(), board["exposure"])
        agentloop.merge_exposure(merged, library["exposure"])
        self.assertEqual(merged["board_ids"], ["post-0002", "post-0003"])
        self.assertEqual(merged["library_ids"], ["lib-0001"])
        self.assertEqual(merged["dm_ids"], [])

    def test_a_bad_tool_call_is_an_answer_not_an_exception(self):
        self.assertFalse(self.dispatch("get_everything")["ok"])
        broken = agentloop.dispatch_tool(
            self.world, self.memory, "agent-01", 1,
            {"id": "c", "function": {"name": "get_messages", "arguments": "not json"}})
        self.assertFalse(broken["ok"])
        self.assertIn("json object", broken["error"])
        # And every failure still produces content the model can read.
        self.assertIn("error", json.loads(broken["content"]))


class MemoryFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = memory_module.MemoryStore(self.tmp, ["agent-01"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_path_whitelist_confines_an_agent_to_its_own_directory(self):
        for bad in ("../escape.md", "/etc/passwd", "wiki/../RESEARCH.md", "wiki/A.md",
                    "wiki/a.txt", "wiki/" + "a" * 41 + ".md", "wiki/a b.md",
                    "wiki\\a.md", "wiki/sub/a.md", "", None, 7):
            written, why = self.store.write("agent-01", bad, "x", 1)
            self.assertIsNone(written, "%r should have been refused" % (bad,))
            self.assertTrue(why)
        self.assertIsNotNone(self.store.write("agent-01", "wiki/plan_v2-1.md", "ok", 1)[0])
        on_disk = [os.path.relpath(os.path.join(base, name), self.tmp)
                   for base, _dirs, files in os.walk(self.tmp) for name in files]
        self.assertTrue(all(p.startswith("agent-01" + os.sep) for p in on_disk), on_disk)

    def test_the_journal_is_append_only(self):
        self.store.append_journal("agent-01", "first", 1)
        self.store.append_journal("agent-01", "second", 2)
        body = self.store.read("agent-01", config.MEMORY_JOURNAL)[0]
        self.assertIn("## step 1", body)
        self.assertIn("first", body)
        self.assertIn("second", body)
        written, why = self.store.write("agent-01", config.MEMORY_JOURNAL, "wiped", 3)
        self.assertIsNone(written)
        self.assertIn("append-only", why)
        self.assertIn("first", self.store.read("agent-01", config.MEMORY_JOURNAL)[0])

    def test_every_cap_refuses_without_destroying_what_was_there(self):
        self.store.write("agent-01", "wiki/p.md", "keep me", 1)
        self.assertIsNone(self.store.write("agent-01", "wiki/p.md",
                                           "x" * (config.MEMORY_FILE_MAX_BYTES + 1), 2)[0])
        self.assertEqual(self.store.read("agent-01", "wiki/p.md")[0], "keep me")
        self.assertIsNone(self.store.append_journal(
            "agent-01", "y" * config.MEMORY_JOURNAL_ENTRY_MAX_BYTES, 2)[0])
        for i in range(config.MEMORY_MAX_WIKI_PAGES + 3):
            self.store.write("agent-01", "wiki/n%d.md" % i, "n", 1)
        self.assertLessEqual(len(self.store._pages("agent-01")),
                             config.MEMORY_MAX_WIKI_PAGES)

    def test_the_journal_tail_is_cut_on_an_entry_boundary(self):
        for i in range(1, 40):
            self.store.append_journal("agent-01", "entry %d %s" % (i, "w" * 60), i)
        tail = self.store.journal_tail("agent-01")
        self.assertIn("earlier bytes are not shown", tail)
        self.assertTrue(tail.split("]\n", 1)[1].startswith("## step "))
        self.assertIn("entry 39", tail)
        self.assertNotIn("entry 1 ", tail)
        self.assertIn("entry 1 ", self.store.read("agent-01", config.MEMORY_JOURNAL)[0])


class HopLoop(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool()
        self.world = world_module.World(self.pool, ["agent-01"])
        self.tmp = tempfile.mkdtemp()
        self.memory = memory_module.MemoryStore(self.tmp, ["agent-01"])
        self.real = client.call_model
        self.requests = []
        run_module.ABORT.clear()

    def tearDown(self):
        client.call_model = self.real
        run_module.ABORT.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, responder):
        def call_model(messages, max_tokens=2048, tools=None, tool_choice=None):
            self.requests.append({"messages": [dict(m) for m in messages],
                                  "tools": tools, "tool_choice": tool_choice})
            return responder(len(self.requests), tool_choice)
        client.call_model = call_model

    def test_reasoning_content_is_echoed_back_on_the_next_hop(self):
        """Thinking mode 400s if the assistant turn that carried tool_calls comes
        back without its reasoning_content. That risk is why the design document
        ruled tool calling out, so it is pinned by a test."""
        self.world.post("agent-02", "hello", 1)

        def responder(n, choice):
            if n == 1:
                return reply(None, tool_calls=[call_of("get_bulletin_board", {}, "c1"),
                                               call_of("list_memory", {}, "c2")])
            return reply(json.dumps({"think": "done"}))

        self.patch(responder)
        turn = run_module._one_step(self.world, "agent-01", 1, 12, {}, self.memory)
        second = self.requests[1]["messages"]
        self.assertEqual(second[:2], self.requests[0]["messages"][:2])
        self.assertEqual(second[2]["role"], "assistant")
        self.assertEqual(second[2]["reasoning_content"], "R")
        self.assertEqual([m["tool_call_id"] for m in second if m["role"] == "tool"],
                         ["c1", "c2"])
        self.assertIs(self.requests[1]["tools"], config.TOOL_SCHEMAS)
        self.assertEqual(turn["n_calls"], 2)
        self.assertEqual(turn["exposure"]["board_ids"], ["post-0001"])

    def test_the_hop_budget_forces_a_final_answer(self):
        def responder(n, choice):
            if choice == "none":
                return reply(json.dumps({"think": "fine"}))
            return reply(None, tool_calls=[call_of("list_memory")])

        self.patch(responder)
        turn = run_module._one_step(self.world, "agent-01", 1, 12, {}, self.memory)
        self.assertEqual(len(self.requests), config.MAX_TOOL_HOPS + 1)
        self.assertEqual(self.requests[-1]["tool_choice"], "none")
        self.assertEqual(self.requests[-1]["messages"][-1]["content"], config.FINAL_NUDGE)
        self.assertIs(self.requests[-1]["tools"], config.TOOL_SCHEMAS)
        self.assertTrue(turn["parse_ok"])
        self.assertEqual(turn["action"]["think"], "fine")

    def test_an_api_error_mid_loop_keeps_what_was_already_read(self):
        self.world.post("agent-02", "hello", 1)

        def responder(n, choice):
            if n == 1:
                return reply(None, tool_calls=[call_of("get_bulletin_board")])
            return reply(None, error="503 upstream")

        self.patch(responder)
        turn = run_module._one_step(self.world, "agent-01", 1, 12, {}, self.memory)
        self.assertEqual(turn["error"], "503 upstream")
        self.assertEqual(turn["exposure"]["board_ids"], ["post-0001"])
        self.assertFalse(turn["parse_ok"])
        self.assertEqual(turn["n_calls"], 2)

    def test_the_spend_cap_mid_loop_aborts_and_keeps_the_billed_hops(self):
        def responder(n, choice):
            if n == 1:
                return reply(None, tool_calls=[call_of("list_memory")])
            raise config.SpendCapExceeded("cap")

        self.patch(responder)
        turn = run_module._one_step(self.world, "agent-01", 1, 12, {}, self.memory)
        self.assertTrue(run_module.ABORT.is_set())
        self.assertIn("spend cap", turn["error"])
        self.assertEqual(turn["n_calls"], 1)

    def test_memory_written_in_one_step_is_in_the_next_prompt(self):
        def responder(n, choice):
            if n == 1:
                return reply(None, tool_calls=[
                    call_of("append_journal", {"text": "CARRIED FORWARD"}, "j1"),
                    call_of("write_memory", {"path": "wiki/p.md", "text": "PRIVATE PAGE"}, "w1")])
            return reply(json.dumps({"think": "saved"}))

        self.patch(responder)
        run_module._one_step(self.world, "agent-01", 1, 12, {}, self.memory)
        messages, ctx, _ = agentloop.build_prompt(self.world, "agent-01", 2,
                                                  memory=self.memory)
        body = messages[1]["content"]
        self.assertIn("CARRIED FORWARD", body)          # the journal tail is auto-shown
        self.assertIn("wiki/p.md", body)                # the page is listed
        self.assertNotIn("PRIVATE PAGE", body)          # but not opened
        self.assertGreater(ctx["memory"], 0)
        on_disk = os.path.join(self.tmp, "agent-01", config.MEMORY_JOURNAL)
        self.assertIn("## step 1", open(on_disk).read())


class ClientContract(unittest.TestCase):
    def test_call_model_extension_is_additive(self):
        blank = client._blank(0.0, "x")
        self.assertEqual(set(blank),
                         {"content", "reasoning_content", "usage", "latency_s", "cost_gbp",
                          "error", "tool_calls", "assistant_message", "finish_reason"})

    def test_tool_calls_are_normalised_to_plain_dicts(self):
        function = type("F", (), {"name": "get_library", "arguments": '{"limit": 3}'})()
        message = type("M", (), {"tool_calls": [type("T", (), {
            "id": "abc", "type": "function", "function": function})()]})()
        self.assertEqual(client._tool_calls_from(message),
                         [{"id": "abc", "type": "function",
                           "function": {"name": "get_library", "arguments": '{"limit": 3}'}}])
        self.assertEqual(client._tool_calls_from(type("M", (), {})()), [])


if __name__ == "__main__":
    unittest.main()
