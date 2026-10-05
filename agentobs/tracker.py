"""
agentobs — a tiny, dependency-free instrumentation library for LLM/agent
calls.

Wrap any LLM API call (Anthropic, OpenAI, or anything else that returns a
`.usage`-shaped object, or none at all) and get one structured JSON-line
event per call: latency, token usage, success/failure, error detail. No
network calls of its own, no required third-party SDK — this only measures
and logs what you tell it about the call you're already making.

Quickstart
----------
    from agentobs import AgentTracker

    tracker = AgentTracker(log_path="events.jsonl", client_id="acme_corp", agent_name="research_agent")

    with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
        response = anthropic_client.messages.create(...)
        run.record_usage(response)   # auto-detects Anthropic/OpenAI usage shapes

    # On an exception inside the `with` block, the run is recorded as a
    # failure (error_type, error_message, latency up to the point of
    # failure) and the exception is re-raised — it never swallows errors.

Design choices worth knowing about:
  - Cost is deliberately NOT computed here. Prices change; baking a
    pricing table into a library you'd have to re-deploy to update is the
    wrong layer. Raw token counts are logged; cost is computed downstream
    in dbt from a seed table (dbt/agent_observability/seeds/model_pricing.csv)
    that's a one-line edit to update.
  - Events are appended as JSON Lines (one event per line) rather than
    written to a database directly, so the library has zero infra
    dependency — it works the same whether events are later loaded into
    Postgres, Snowflake, or just grepped by hand.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = ["AgentTracker", "AgentRun"]

_write_lock = threading.Lock()


def _extract_usage(response: Any) -> tuple[Optional[int], Optional[int]]:
    """Best-effort extraction of (input_tokens, output_tokens) from a
    provider response object or plain dict. Supports the Anthropic
    (`usage.input_tokens` / `usage.output_tokens`) and OpenAI
    (`usage.prompt_tokens` / `usage.completion_tokens`) shapes. Returns
    (None, None) if neither shape matches — callers can always pass
    explicit token counts instead (see AgentRun.record_usage)."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None, None

    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    # Anthropic shape
    in_tok = _get(usage, "input_tokens")
    out_tok = _get(usage, "output_tokens")
    if in_tok is not None or out_tok is not None:
        return in_tok, out_tok

    # OpenAI shape
    in_tok = _get(usage, "prompt_tokens")
    out_tok = _get(usage, "completion_tokens")
    return in_tok, out_tok


@dataclass
class AgentRun:
    """Represents one in-flight tracked call. Returned by
    `AgentTracker.track()` as the `with ... as run` binding."""

    client_id: Optional[str]
    agent_name: Optional[str]
    task_type: str
    model: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    success: bool = True
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    def record_usage(
        self,
        response: Any = None,
        *,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        """Call this once inside the `with` block after you get a
        response. Pass the raw response object to auto-detect token
        counts, or pass input_tokens/output_tokens explicitly (these
        always win if given) — useful for providers agentobs doesn't
        recognize, or for non-LLM tool calls you still want to cost/track."""
        if response is not None:
            auto_in, auto_out = _extract_usage(response)
            self.input_tokens = input_tokens if input_tokens is not None else auto_in
            self.output_tokens = output_tokens if output_tokens is not None else auto_out
        else:
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens

    def add_metadata(self, **kwargs) -> None:
        """Attach arbitrary extra context (e.g. prompt_version='v3',
        session_id=...). Stored as a JSON blob on the event."""
        self.metadata.update(kwargs)


class AgentTracker:
    """Create one tracker per client/agent (or one shared tracker and pass
    client_id/agent_name per-call) and use `.track()` around each LLM call
    you want observed."""

    def __init__(
        self,
        log_path: str | Path = "agent_events.jsonl",
        client_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_client_id = client_id
        self.default_agent_name = agent_name

    @contextmanager
    def track(
        self,
        task_type: str,
        model: str,
        client_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **metadata,
    ):
        run = AgentRun(
            client_id=client_id or self.default_client_id,
            agent_name=agent_name or self.default_agent_name,
            task_type=task_type,
            model=model,
            metadata=dict(metadata),
        )
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        try:
            yield run
        except Exception as exc:  # noqa: BLE001 - intentionally broad; we log then re-raise
            run.success = False
            run.error_type = type(exc).__name__
            run.error_message = str(exc)[:2000]
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            self._write_event(run, started_at, latency_ms)

    def _write_event(self, run: AgentRun, started_at: datetime, latency_ms: float) -> None:
        event = {
            "event_id": run.run_id,
            "timestamp": started_at.isoformat(),
            "client_id": run.client_id,
            "agent_name": run.agent_name,
            "task_type": run.task_type,
            "model": run.model,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "latency_ms": latency_ms,
            "success": run.success,
            "error_type": run.error_type,
            "error_message": run.error_message,
            "metadata": run.metadata or None,
        }
        line = json.dumps(event, default=str)
        with _write_lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
