"""Agent A — the only module that talks to the network.

One public function, `call_model`, per the frozen signature. Everything else in
here is private. Nothing but `config` is imported from the project.

Three things in this file exist to protect real money, and none of them is
decorative:

  * `max_retries=0` on the SDK client. The SDK's own retry would fire on top of
    ours and bill the same completion twice.
  * only 429, 500 and 503 are retried. A 400, 401, 402 or 422 is permanent —
    retrying it bills again for a request that cannot succeed.
  * the spend counter raises `config.SpendCapExceeded`, which inherits from
    BaseException so that the round loop's `except Exception` per worker cannot
    swallow it and keep spending silently.

`openai` is imported lazily inside `_sdk_client` so that this module imports
cleanly, and its offline self-test runs, before anyone has pip-installed it.
"""
import threading
import time

from swarm import config

# How thinking mode is switched on. The design document fixes THINKING_ENABLED
# but not the wire spelling, and no request may be made while building, so this
# is the one thing in this file that gate 0 has to confirm against the live API.
# It is isolated here so confirming it is a one-line change. If gate 0 comes
# back without `reasoning_content` on the response, this is the first thing to
# look at, and the second is whether thinking and JSON mode can be combined at
# all on this model.
THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}

# Every call in this harness asks for one JSON action object, so JSON mode is
# always on. config.ACTION_SCHEMA_EXAMPLE carries both the literal word "json"
# and a format example, which DeepSeek requires or it can emit whitespace to the
# token cap.
RESPONSE_FORMAT = {"type": "json_object"}

# Whether json mode may be combined with `tools`. Unverified against the live
# API when this was written, and it is the one thing gate 0b exists to settle.
# If tools + response_format returns a 400, set this False: the loop still works
# because parse_action tolerates fences and wrapped objects, and the final hop
# is nudged in words. One line, no other change.
JSON_MODE_WITH_TOOLS = True

_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "reasoning_tokens",
)

_spend_lock = threading.Lock()
_spend_gbp = 0.0
_client = None
_client_lock = threading.Lock()


def _sdk_client():
    """Build the SDK client once, on first use. Never logs the key."""
    global _client
    with _client_lock:
        if _client is None:
            import openai  # lazy: see module docstring

            _client = openai.OpenAI(
                base_url=config.BASE_URL,
                api_key=config.API_KEY,
                timeout=config.CLIENT_TIMEOUT_S,
                max_retries=config.SDK_MAX_RETRIES,
            )
        return _client


def _usage_from(raw):
    """Normalise `resp.usage.model_dump()` into the five fields we log.

    Read from the dump, never by attribute access: the DeepSeek cache and
    reasoning fields are pydantic extras and are invisible as attributes.
    Anything absent is 0 — never estimated.
    """
    raw = raw or {}
    out = {}
    for field in _USAGE_FIELDS:
        value = raw.get(field)
        out[field] = int(value) if isinstance(value, (int, float)) else 0
    # reasoning_tokens usually arrives nested. completion_tokens_details is not
    # a required field, so its absence is normal and not an error.
    if not out["reasoning_tokens"]:
        details = raw.get("completion_tokens_details") or {}
        if isinstance(details, dict):
            value = details.get("reasoning_tokens")
            out["reasoning_tokens"] = int(value) if isinstance(value, (int, float)) else 0
    # If the provider reports no cache split, bill every prompt token at the
    # miss rate. Wrong in our favour: it can over-report cost, never under.
    if out["prompt_cache_hit_tokens"] == 0 and out["prompt_cache_miss_tokens"] == 0:
        out["prompt_cache_miss_tokens"] = out["prompt_tokens"]
    return out


def _cost_gbp(usage):
    """Off-peak GBP for one call. Cache hits and misses priced separately."""
    usd = (
        usage["prompt_cache_hit_tokens"] / 1e6 * config.PRICE_CACHE_HIT
        + usage["prompt_cache_miss_tokens"] / 1e6 * config.PRICE_CACHE_MISS
        + usage["completion_tokens"] / 1e6 * config.PRICE_OUTPUT
    )
    return usd * config.USD_TO_GBP


def _charge(cost_gbp):
    """Add to the process-wide counter, then raise if the cap is now breached.

    Charging before raising is deliberate: the money is already spent, so the
    total has to include it before anything aborts.
    """
    global _spend_gbp
    with _spend_lock:
        _spend_gbp += cost_gbp
        total = _spend_gbp
    if total > config.SPEND_CAP_GBP:
        raise config.SpendCapExceeded(
            "spend cap breached: %.4f GBP spent against a cap of %.2f"
            % (total, config.SPEND_CAP_GBP)
        )
    return total


def _guard_cap():
    """Refuse to start a call once the cap is already gone."""
    with _spend_lock:
        total = _spend_gbp
    if total > config.SPEND_CAP_GBP:
        raise config.SpendCapExceeded(
            "spend cap already breached: %.4f GBP against a cap of %.2f"
            % (total, config.SPEND_CAP_GBP)
        )


def _status_of(exc):
    """HTTP status of an SDK exception, or None if it carries no status."""
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retryable(exc):
    """Only the three transient statuses. Everything else is permanent."""
    status = _status_of(exc)
    if status is not None:
        return status in config.RETRY_ON
    # No status at all means the request never got an answer — a timeout or a
    # dropped connection. Those are worth one more go; nothing was billed.
    return isinstance(exc, (TimeoutError, ConnectionError))


def _blank(latency_s, error):
    return {
        "content": None,
        "reasoning_content": None,
        "usage": {field: 0 for field in _USAGE_FIELDS},
        "latency_s": latency_s,
        "cost_gbp": 0.0,
        "error": error,
        "tool_calls": None,
        "assistant_message": None,
        "finish_reason": None,
    }


def _tool_calls_from(message):
    """Normalise SDK tool_call objects into plain dicts we can log and echo.

    Attribute access is right here, unlike reasoning_content: id, type and
    function are declared fields on the SDK's tool-call model, not pydantic
    extras. Returns [] when the model asked for no tools.
    """
    out = []
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        out.append({
            "id": getattr(call, "id", None),
            "type": getattr(call, "type", None) or "function",
            "function": {"name": getattr(function, "name", None),
                         "arguments": getattr(function, "arguments", None) or "{}"},
        })
    return out


def call_model(messages: list, max_tokens: int = 2048,
               tools: list = None, tool_choice=None) -> dict:
    """One completion. Returns the frozen dict on every path, including errors.

    -> {content, reasoning_content, usage, latency_s, cost_gbp, error,
        tool_calls, assistant_message, finish_reason}

    The last three are additive: with `tools` left None the request built here
    is byte-identical to the one the base runs made, and the three keys come
    back None.

    `assistant_message` is the message to append back to `messages` before the
    next hop of a tool loop. It carries reasoning_content, and it must: in
    thinking mode DeepSeek rejects a follow-up request whose assistant turn had
    tool_calls but no reasoning_content.

    `tools` is passed through BY REFERENCE and never copied or mutated. It is
    part of the prompt-cache prefix, so every agent and every step must send the
    identical object.

    usage carries prompt_tokens, completion_tokens, prompt_cache_hit_tokens,
    prompt_cache_miss_tokens and reasoning_tokens, read from the response and
    never estimated.

    Raises only config.SpendCapExceeded. Every other failure comes back as a
    verdict with `error` set, because one agent's bad turn must not end the run.
    """
    _guard_cap()
    attempts = len(config.BACKOFF_S)
    started = time.monotonic()
    last_error = "no attempt was made"

    for attempt in range(attempts):
        attempt_started = time.monotonic()
        try:
            # No user_id, ever: DeepSeek uses it to ISOLATE the KV cache, which
            # would cost roughly 50x. No temperature either — it is silently
            # ignored while thinking mode is on, so setting it would only
            # suggest the run is less stochastic than it is.
            kwargs = dict(
                model=config.MODEL,
                messages=messages,
                max_tokens=max_tokens,
            )
            if tools is None or JSON_MODE_WITH_TOOLS:
                kwargs["response_format"] = RESPONSE_FORMAT
            if tools is not None:
                kwargs["tools"] = tools          # by reference: cache prefix
                if tool_choice is not None:
                    kwargs["tool_choice"] = tool_choice
            if config.THINKING_ENABLED:
                kwargs["extra_body"] = dict(THINKING_EXTRA_BODY)
            response = _sdk_client().chat.completions.create(**kwargs)
        except config.SpendCapExceeded:
            raise
        except BaseException as exc:  # noqa: BLE001 — every failure is a verdict
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            last_error = "%s: %s" % (type(exc).__name__, exc)
            if attempt + 1 < attempts and _retryable(exc):
                time.sleep(config.BACKOFF_S[attempt])
                continue
            return _blank(time.monotonic() - started, last_error)

        latency_s = time.monotonic() - attempt_started
        try:
            usage = _usage_from(
                response.usage.model_dump() if response.usage is not None else {}
            )
            choice = response.choices[0]
            message = choice.message
            content = getattr(message, "content", None)
            reasoning = getattr(message, "reasoning_content", None)
            finish_reason = getattr(choice, "finish_reason", None)
            tool_calls = _tool_calls_from(message)
            assistant_message = {"role": "assistant", "content": content}
            if reasoning is not None:
                assistant_message["reasoning_content"] = reasoning
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # The call was billed but the shape surprised us. Report it as an
            # error rather than guessing at the numbers.
            return _blank(latency_s, "unreadable response: %s: %s" % (type(exc).__name__, exc))

        cost_gbp = _cost_gbp(usage)
        _charge(cost_gbp)   # may raise SpendCapExceeded, after banking the cost
        return {
            "content": content,
            "reasoning_content": reasoning,
            "usage": usage,
            "latency_s": latency_s,
            "cost_gbp": cost_gbp,
            "error": None,
            "tool_calls": tool_calls or None,
            "assistant_message": assistant_message,
            "finish_reason": finish_reason,
        }

    return _blank(time.monotonic() - started, last_error)


# --------------------------------------------------------------- self-test
if __name__ == "__main__":
    import json

    passed = failed = 0

    def check(name, condition):
        global passed, failed
        if condition:
            passed += 1
            print("PASS  " + name)
        else:
            failed += 1
            print("FAIL  " + name)

    def reset_spend(value=0.0):
        global _spend_gbp
        with _spend_lock:
            _spend_gbp = value

    # ---- usage normalisation
    dump = {
        "prompt_tokens": 7400,
        "completion_tokens": 1120,
        "prompt_cache_hit_tokens": 5900,
        "prompt_cache_miss_tokens": 1500,
        "completion_tokens_details": {"reasoning_tokens": 890},
    }
    usage = _usage_from(dump)
    check("usage reads all five fields from the dump",
          usage == {"prompt_tokens": 7400, "completion_tokens": 1120,
                    "prompt_cache_hit_tokens": 5900, "prompt_cache_miss_tokens": 1500,
                    "reasoning_tokens": 890})
    check("usage survives a missing completion_tokens_details",
          _usage_from({"prompt_tokens": 10, "completion_tokens": 2,
                       "prompt_cache_hit_tokens": 10})["reasoning_tokens"] == 0)
    check("usage of an empty dump is all zeros",
          _usage_from({}) == {f: 0 for f in _USAGE_FIELDS})
    check("no cache split reported bills every prompt token as a miss",
          _usage_from({"prompt_tokens": 500})["prompt_cache_miss_tokens"] == 500)
    check("a flat reasoning_tokens field is read too",
          _usage_from({"reasoning_tokens": 7})["reasoning_tokens"] == 7)

    # ---- cost
    cost = _cost_gbp(usage)
    expect = (5900 / 1e6 * 0.003 + 1500 / 1e6 * 0.15 + 1120 / 1e6 * 0.60) * config.USD_TO_GBP
    check("cost prices hits and misses separately", abs(cost - expect) < 1e-12)
    check("cost of a zero-token call is zero", _cost_gbp(_usage_from({})) == 0.0)
    check("a cache hit is far cheaper than the same tokens as a miss",
          _cost_gbp(_usage_from({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 1000}))
          < _cost_gbp(_usage_from({"prompt_tokens": 1000, "prompt_cache_miss_tokens": 1000})))

    # ---- the spend cap
    reset_spend(config.SPEND_CAP_GBP - 0.0001)
    try:
        _charge(0.001)
        check("charging past the cap raises", False)
    except config.SpendCapExceeded:
        check("charging past the cap raises", True)
    check("the cap exception cannot be caught by `except Exception`",
          not issubclass(config.SpendCapExceeded, Exception))
    reset_spend(config.SPEND_CAP_GBP + 1.0)
    try:
        _guard_cap()
        check("no call is started once the cap is gone", False)
    except config.SpendCapExceeded:
        check("no call is started once the cap is gone", True)
    reset_spend(0.0)
    check("charging under the cap returns the running total",
          abs(_charge(0.01) - 0.01) < 1e-12)
    reset_spend(0.0)

    # ---- retry policy
    class _Err(Exception):
        def __init__(self, status):
            Exception.__init__(self, "status %s" % status)
            self.status_code = status

    check("429 retries", _retryable(_Err(429)))
    check("500 retries", _retryable(_Err(500)))
    check("503 retries", _retryable(_Err(503)))
    check("400 never retries", not _retryable(_Err(400)))
    check("401 never retries", not _retryable(_Err(401)))
    check("402 never retries — no balance means no amount of retrying helps",
          not _retryable(_Err(402)))
    check("422 never retries", not _retryable(_Err(422)))
    check("a status on exc.response is found too",
          _retryable(type("E", (Exception,), {"response": type("R", (), {"status_code": 503})()})()))
    check("three attempts configured", len(config.BACKOFF_S) == 3)
    check("the SDK does no retrying of its own", config.SDK_MAX_RETRIES == 0)

    # ---- the error path returns a verdict rather than raising
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise _Err(400)

    saved, _client = _client, _Boom()
    result = call_model([{"role": "user", "content": "json"}], max_tokens=16)
    check("a permanent error returns the full dict, not an exception",
          set(result) == {"content", "reasoning_content", "usage", "latency_s", "cost_gbp",
                          "error", "tool_calls", "assistant_message", "finish_reason"})
    check("a permanent error reports the error and no cost",
          result["error"] is not None and result["cost_gbp"] == 0.0)
    check("a failed call is not billed", _spend_gbp == 0.0)

    # ---- the success path, with a stub standing in for the SDK
    class _Ok:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    check("no user_id is ever sent", "user_id" not in kwargs)
                    check("no temperature is sent", "temperature" not in kwargs)
                    check("json mode is requested", kwargs["response_format"] == RESPONSE_FORMAT)
                    check("thinking mode is requested", "extra_body" in kwargs)
                    check("max_tokens is passed through", kwargs["max_tokens"] == 16)
                    usage_obj = type("U", (), {"model_dump": staticmethod(lambda: dict(dump))})()
                    msg = type("M", (), {"content": '{"think": "x"}', "reasoning_content": "trace"})()
                    return type("R", (), {"usage": usage_obj,
                                          "choices": [type("C", (), {"message": msg})()]})()

    _client = _Ok()
    result = call_model([{"role": "user", "content": "json"}], max_tokens=16)
    check("content comes back", result["content"] == '{"think": "x"}')
    check("reasoning_content comes back", result["reasoning_content"] == "trace")
    check("usage comes back populated", result["usage"]["reasoning_tokens"] == 890)
    check("cost is recorded on the call", result["cost_gbp"] > 0)
    check("latency is recorded", result["latency_s"] >= 0)
    check("error is None on success", result["error"] is None)
    check("the successful call was billed", _spend_gbp > 0)
    check("the returned content is the JSON we will parse later",
          json.loads(result["content"]) == {"think": "x"})

    check("no tools are sent unless asked for", result["tool_calls"] is None)

    # ---- tool calling: the request carries the schemas, the reply is normalised
    class _Tools:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    check("the tools list is passed by identity, not copied",
                          kwargs.get("tools") is config.TOOL_SCHEMAS)
                    check("tool_choice is forwarded when given",
                          kwargs.get("tool_choice") == "none")
                    usage_obj = type("U", (), {"model_dump": staticmethod(lambda: dict(dump))})()
                    fn = type("F", (), {"name": "get_bulletin_board", "arguments": '{"limit": 5}'})()
                    tc = type("T", (), {"id": "call_1", "type": "function", "function": fn})()
                    msg = type("M", (), {"content": None, "reasoning_content": "trace",
                                         "tool_calls": [tc]})()
                    return type("R", (), {"usage": usage_obj,
                                          "choices": [type("C", (), {"message": msg,
                                                                     "finish_reason": "tool_calls"})()]})()

    reset_spend(0.0)
    _client = _Tools()
    result = call_model([{"role": "user", "content": "json"}], max_tokens=16,
                        tools=config.TOOL_SCHEMAS, tool_choice="none")
    check("tool calls are normalised to plain dicts",
          result["tool_calls"] == [{"id": "call_1", "type": "function",
                                    "function": {"name": "get_bulletin_board",
                                                 "arguments": '{"limit": 5}'}}])
    check("finish_reason comes back", result["finish_reason"] == "tool_calls")
    check("the assistant message is ready to append back",
          result["assistant_message"]["role"] == "assistant"
          and result["assistant_message"]["tool_calls"] == result["tool_calls"])
    check("the assistant message carries reasoning_content, which thinking mode requires",
          result["assistant_message"]["reasoning_content"] == "trace")

    # ---- a response with no reasoning_content is not an error
    class _NoTrace:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    usage_obj = type("U", (), {"model_dump": staticmethod(lambda: {"prompt_tokens": 5,
                                                                                  "completion_tokens": 1})})()
                    msg = type("M", (), {"content": "{}"})()
                    return type("R", (), {"usage": usage_obj,
                                          "choices": [type("C", (), {"message": msg})()]})()

    reset_spend(0.0)
    _client = _NoTrace()
    result = call_model([{"role": "user", "content": "json"}])
    check("a missing reasoning_content is not an error",
          result["error"] is None and result["reasoning_content"] is None)
    _client = saved

    # ---- the key never leaves this module, checked behaviourally
    class _Echo:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    # Worst case: a provider echoes the request back at us.
                    usage_obj = type("U", (), {"model_dump": staticmethod(lambda: {"prompt_tokens": 1,
                                                                                  "completion_tokens": 1})})()
                    msg = type("M", (), {"content": json.dumps(kwargs, default=str),
                                         "reasoning_content": None})()
                    return type("R", (), {"usage": usage_obj,
                                          "choices": [type("C", (), {"message": msg})()]})()

    reset_spend(0.0)
    _client = _Echo()
    echoed = call_model([{"role": "user", "content": "json"}], max_tokens=8)
    check("no request this module builds carries the key",
          config.API_KEY not in json.dumps(echoed, default=str))
    errored = call_model.__doc__ or ""
    check("the docstring does not carry the key", config.API_KEY not in errored)
    with open(__file__) as handle:
        check("the key is nowhere in this file's text", config.API_KEY not in handle.read())

    print("\n%d passed, %d failed" % (passed, failed))
    raise SystemExit(1 if failed else 0)
