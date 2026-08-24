# DeepSeek Thinking Mode integration note (2026-08-24)

Primary source: [DeepSeek API — 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode).

## Request contract used by this project

For the OpenAI-compatible Chat Completions interface, DeepSeek documents two
independent fields:

```json
{
  "thinking": {"type": "enabled"},
  "reasoning_effort": "max"
}
```

With the OpenAI SDK, `thinking` is sent through `extra_body`, while
`reasoning_effort` is a top-level completion parameter. The documented effort
levels are `low`, `high`, and `max`; `max` maps to the model's maximum effort.
The default effort is `high`, so merely enabling thinking is not equivalent to
the requested `max` configuration.

The API also requires the assistant `reasoning_content` returned during a tool
turn to be preserved in subsequent requests in that turn. The pinned DeerFlow
runtime uses `PatchedChatDeepSeek`, whose request-payload hook restores this
field when replaying assistant messages.

## Harness mapping

The generated DeerFlow model profile therefore declares:

```yaml
supports_thinking: true
supports_reasoning_effort: true
when_thinking_enabled:
  reasoning_effort: max
  extra_body:
    thinking:
      type: enabled
```

The benchmark configuration separately freezes:

```yaml
thinking_enabled: enabled
thinking_effort: max
```

A pinned-container zero-model probe instantiates the actual
`PatchedChatDeepSeek` through DeerFlow's model factory and verifies that the
resulting client has `reasoning_effort == "max"` and
`extra_body.thinking.type == "enabled"`. This proves request configuration, not
model quality; quality is measured only by the subsequent Development canary.
