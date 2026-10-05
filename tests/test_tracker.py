"""
Offline tests for agentobs — no network, no API keys required. Mocks both
the Anthropic and OpenAI response shapes, plus a failure path, and asserts
the JSONL output is well-formed. This is what actually gets run in CI.

Usage:
    python -m pytest tests/test_tracker.py -v
    (or just: python tests/test_tracker.py)
"""
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.append(".")
from agentobs import AgentTracker


def _read_events(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_anthropic_shaped_response(tmp_path):
    log_path = tmp_path / "events.jsonl"
    tracker = AgentTracker(log_path=log_path, client_id="acme_corp", agent_name="research_agent")

    fake_response = SimpleNamespace(usage=SimpleNamespace(input_tokens=1200, output_tokens=340))
    with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
        time.sleep(0.01)
        run.record_usage(fake_response)

    events = _read_events(log_path)
    assert len(events) == 1
    e = events[0]
    assert e["client_id"] == "acme_corp"
    assert e["agent_name"] == "research_agent"
    assert e["input_tokens"] == 1200
    assert e["output_tokens"] == 340
    assert e["success"] is True
    assert e["latency_ms"] > 0
    print("PASS: anthropic-shaped response")


def test_openai_shaped_response(tmp_path):
    log_path = tmp_path / "events.jsonl"
    tracker = AgentTracker(log_path=log_path)

    fake_response = {"usage": {"prompt_tokens": 500, "completion_tokens": 150}}
    with tracker.track(task_type="classify", model="gpt-4o-mini", client_id="beta_llc") as run:
        run.record_usage(fake_response)

    events = _read_events(log_path)
    assert events[0]["input_tokens"] == 500
    assert events[0]["output_tokens"] == 150
    assert events[0]["client_id"] == "beta_llc"
    print("PASS: openai-shaped response")


def test_explicit_token_override(tmp_path):
    log_path = tmp_path / "events.jsonl"
    tracker = AgentTracker(log_path=log_path, client_id="acme_corp")

    with tracker.track(task_type="tool_call", model="claude-haiku-4-5") as run:
        run.record_usage(input_tokens=42, output_tokens=7)

    events = _read_events(log_path)
    assert events[0]["input_tokens"] == 42
    assert events[0]["output_tokens"] == 7
    print("PASS: explicit token override")


def test_failure_is_logged_and_reraised(tmp_path):
    log_path = tmp_path / "events.jsonl"
    tracker = AgentTracker(log_path=log_path, client_id="acme_corp")

    raised = False
    try:
        with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
            raise TimeoutError("upstream timed out after 30s")
    except TimeoutError:
        raised = True

    assert raised, "exception must propagate, not be swallowed"
    events = _read_events(log_path)
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "TimeoutError"
    assert "timed out" in events[0]["error_message"]
    print("PASS: failure logged and re-raised")


def test_metadata_and_multiple_events_append(tmp_path):
    log_path = tmp_path / "events.jsonl"
    tracker = AgentTracker(log_path=log_path, client_id="acme_corp", agent_name="research_agent")

    with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
        run.add_metadata(prompt_version="v3")
        run.record_usage(input_tokens=100, output_tokens=20)

    with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
        run.record_usage(input_tokens=110, output_tokens=25)

    events = _read_events(log_path)
    assert len(events) == 2, "second call must append, not overwrite"
    assert events[0]["metadata"] == {"prompt_version": "v3"}
    print("PASS: metadata + append-only writes")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_anthropic_shaped_response(tmp / "t1")
        test_openai_shaped_response(tmp / "t2")
        test_explicit_token_override(tmp / "t3")
        test_failure_is_logged_and_reraised(tmp / "t4")
        test_metadata_and_multiple_events_append(tmp / "t5")
    print("\nAll agentobs tests passed.")
