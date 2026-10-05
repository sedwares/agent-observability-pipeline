"""agentobs — lightweight instrumentation for LLM/agent calls.

    from agentobs import AgentTracker

See tracker.py's module docstring for the full quickstart.
"""
from .tracker import AgentRun, AgentTracker

__all__ = ["AgentTracker", "AgentRun"]
__version__ = "0.1.0"
