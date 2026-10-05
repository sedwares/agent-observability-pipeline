"""
Example: instrumenting real Anthropic API calls with agentobs.

This is written to run two ways:
  - With a real ANTHROPIC_API_KEY set, it makes real calls and logs real
    usage/cost data.
  - Without one (e.g. CI, or just trying this out), it falls back to a
    mock client so you can see the instrumentation pattern end to end
    without any API access. Either way, `data/events/demo_run.jsonl`
    ends up with real, well-formed events.

This is the pattern to copy into your own agents: wrap the call site,
not the whole function, so retries/streaming/etc. inside the SDK call are
naturally captured in the timing.

Usage:
    python scripts/demo_instrumentation.py
"""
import os
import random
import sys
import time
from types import SimpleNamespace

sys.path.append(".")
from agentobs import AgentTracker

LOG_PATH = "data/events/demo_run.jsonl"


class _MockAnthropicResponse:
    """Stands in for anthropic.types.Message when no API key is set."""

    def __init__(self, input_tokens: int, output_tokens: int):
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.content = [SimpleNamespace(text="(mock response — set ANTHROPIC_API_KEY for a real call)")]


def call_llm_mocked(prompt: str) -> _MockAnthropicResponse:
    """Simulates latency and token usage without any network access."""
    time.sleep(random.uniform(0.05, 0.3))
    input_tokens = max(20, len(prompt.split()) * 2)
    output_tokens = random.randint(80, 400)
    if random.random() < 0.05:  # occasionally simulate a failure, same as a real API would
        raise TimeoutError("mock upstream timeout")
    return _MockAnthropicResponse(input_tokens, output_tokens)


def call_llm_real(prompt: str):
    import anthropic  # only imported if we actually have a key

    client = anthropic.Anthropic()
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )


def main():
    tracker = AgentTracker(log_path=LOG_PATH, client_id="internal_demo", agent_name="portfolio_demo_agent")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    prompts = [
        "Summarize the benefits of RevPAR as a hotel KPI in two sentences.",
        "List three risks of overbooking a hotel.",
        "Explain lead time in the context of hotel reservations.",
    ]

    for prompt in prompts:
        with tracker.track(
            task_type="summarize_doc",
            model="claude-sonnet-5",
            prompt_preview=prompt[:60],
        ) as run:
            try:
                response = call_llm_real(prompt) if has_key else call_llm_mocked(prompt)
                run.record_usage(response)
            except Exception:
                # tracker.track() already logs the failure and re-raises;
                # this outer catch just lets the demo keep going through
                # the rest of the prompt list instead of aborting.
                raise

    mode = "real Anthropic API calls" if has_key else "mocked calls (no ANTHROPIC_API_KEY set)"
    print(f"Logged {len(prompts)} events to {LOG_PATH} using {mode}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"(a call failed and was logged as a failure event — this is expected sometimes: {exc})")
