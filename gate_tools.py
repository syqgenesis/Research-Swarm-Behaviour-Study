"""Gate 0b — the one live check that settles whether tool calling works here.

Four calls, a fraction of a penny. Run it before gate 2b and before any swarm
run, because everything the free-running loop does rests on the answers.

  python3 gate_tools.py

What each call settles:

  1. tools + thinking + json mode are accepted together, and the model returns
     tool_calls with reasoning_content beside them.
  2. a second hop works when the assistant message is appended back verbatim,
     with a tool result answering the call id. This is the hop loop.
  3. tool_choice="none" makes it answer instead of asking for another tool.
     This is what the hop budget relies on.
  4. NEGATIVE CONTROL: the same second hop with reasoning_content stripped.
     A 400 here confirms why the assistant message must be echoed whole, which
     is the risk the design document cited when it ruled tool calling out.

If call 1 fails with a 400 naming response_format, set
swarm/client.JSON_MODE_WITH_TOOLS = False and run this again. If it fails naming
thinking, stop: turning thinking off during tool hops is a different experiment
and is the human's call, not the harness's.
"""
import json

from swarm import client
from swarm import config

SYSTEM = ("You are a test harness for a research conference platform. When you have "
          "what you need, answer with a single json object and nothing else.")
USER = ("Call get_bulletin_board with no arguments to see what has been posted, then "
        "reply with the json object {\"posts\": <the number of posts you were shown>}.")
TOOL_RESULT = json.dumps({"posts": [], "total_posts": 0, "omitted_older": 0,
                          "oldest_id": None, "latest_id": None})


def show(label, result):
    usage = result.get("usage") or {}
    print("  error            : %s" % result.get("error"))
    print("  finish_reason    : %s" % result.get("finish_reason"))
    print("  tool_calls       : %s" % json.dumps(result.get("tool_calls")))
    print("  reasoning_content: %s" % ((result.get("reasoning_content") or "")[:120] or None))
    print("  content          : %s" % ((result.get("content") or "")[:200] or None))
    print("  tokens           : %d prompt (%d cached), %d completion"
          % (usage.get("prompt_tokens", 0), usage.get("prompt_cache_hit_tokens", 0),
             usage.get("completion_tokens", 0)))
    print("  cost             : %.6f GBP" % (result.get("cost_gbp") or 0))


def main():
    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))
        print("%s  %s" % ("PASS" if condition else "FAIL", name))

    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]

    print("\n--- 1. tools + thinking + json mode ---")
    first = client.call_model(messages, max_tokens=2000, tools=config.TOOL_SCHEMAS)
    show("first", first)
    check("the call was accepted", first["error"] is None)
    calls = first.get("tool_calls") or []
    check("the model asked for a tool", bool(calls))
    check("it asked for the board", calls and calls[0]["function"]["name"] == "get_bulletin_board")
    if not calls:
        print("\nno tool call to follow up; stopping here.")
        return 1

    print("\n--- 2. the second hop, assistant message echoed back whole ---")
    second_messages = messages + [
        first["assistant_message"],
        {"role": "tool", "tool_call_id": calls[0]["id"], "content": TOOL_RESULT},
    ]
    second = client.call_model(second_messages, max_tokens=2000, tools=config.TOOL_SCHEMAS)
    show("second", second)
    check("the second hop was accepted", second["error"] is None)
    # A tool-only hop may omit reasoning even in thinking mode. The recorded
    # unit is the complete step, which includes the answer after the tool result.
    check("reasoning_content returned during the tool step",
          bool(first.get("reasoning_content") or second.get("reasoning_content")))
    check("it answered instead of asking again", not (second.get("tool_calls") or []))
    try:
        parsed = json.loads(second.get("content") or "")
        check("the answer is a json object", isinstance(parsed, dict))
    except ValueError:
        check("the answer is a json object", False)
    hit = (second.get("usage") or {}).get("prompt_cache_hit_tokens") or 0
    print("  (cache hit tokens on the second hop: %d — informational)" % hit)

    print("\n--- 3. tool_choice=none forces an answer ---")
    forced = client.call_model(messages + [{"role": "user", "content": "Answer now with the json object and no tool calls."}],
                               max_tokens=2000, tools=config.TOOL_SCHEMAS, tool_choice="none")
    show("forced", forced)
    check("the forced call was accepted", forced["error"] is None)
    check("it did not ask for a tool", not (forced.get("tool_calls") or []))

    print("\n--- 4. negative control: reasoning_content stripped ---")
    stripped = dict(first["assistant_message"])
    stripped.pop("reasoning_content", None)
    control = client.call_model(
        messages + [stripped, {"role": "tool", "tool_call_id": calls[0]["id"],
                               "content": TOOL_RESULT}],
        max_tokens=200, tools=config.TOOL_SCHEMAS)
    print("  error: %s" % control.get("error"))
    if control.get("error"):
        print("  -> as expected: the echo is required, which is what the loop does.")
    else:
        print("  -> it was accepted. Harmless; the loop echoes it anyway.")

    failed = [name for name, ok in checks if not ok]
    print("\n%d of %d checks passed" % (len(checks) - len(failed), len(checks)))
    if failed:
        print("failed: %s" % "; ".join(failed))
        print("\nIf a 400 named response_format, set client.JSON_MODE_WITH_TOOLS = False")
        print("and run this again. If it named thinking, stop and decide first.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
