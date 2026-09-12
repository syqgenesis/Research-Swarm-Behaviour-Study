# Agent A — `swarm/client.py`

Write exactly one file: `swarm/client.py`. Implement `call_model` per the frozen
signature in AGENTS.md.

Wrap `openai.OpenAI(base_url=config.BASE_URL, api_key=config.API_KEY)`.

Must:
- Read every usage field via `resp.usage.model_dump()`, **not** attribute access.
  The DeepSeek-specific fields are extras on the SDK's pydantic model and are
  invisible to attribute access.
- Reach the trace with `getattr(msg, "reasoning_content", None)`. It may be
  absent; that is not an error.
- Guard for `completion_tokens_details` being absent — it is not a required field.
- Set `timeout=config.CLIENT_TIMEOUT_S`. The server can hold a connection for up
  to ten minutes before inference starts.
- Set `max_retries=config.SDK_MAX_RETRIES` on the client and implement your own
  backoff over `config.BACKOFF_S`, retrying only `config.RETRY_ON`. Never retry
  400, 401, 402 or 422 — they are permanent and retrying them bills twice.
- Compute `cost_gbp` from the off-peak price constants and `USD_TO_GBP`, using
  the cache-hit and cache-miss token counts separately.
- Maintain a process-wide spend counter that **raises** past
  `config.SPEND_CAP_GBP`. Not a warning. A raise.
- Return the full dict from the signature on every path, including errors.

Must not:
- Pass `user_id`. DeepSeek uses it to isolate the KV cache — setting it per agent
  turns a 50x discount into full price on every token.
- Set `temperature` and expect it to apply. It is silently ignored in thinking mode.
- Read, print or log `config.API_KEY`.

Target ~120 lines.
