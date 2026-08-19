"""Live interception seams; durable facts remain in SessionLedger."""

from adaptive_harness.events import EventMode, EventSpec

AGENT_PRE_STEP = EventSpec("agent/pre-step", EventMode.WATERFALL)
AGENT_REQUEST = EventSpec("agent/request", EventMode.WATERFALL)
AGENT_TURN_STOPPING = EventSpec("agent/turn-stopping", EventMode.SERIAL)
RUN_STARTED = EventSpec("run/started", EventMode.EMIT)
RUN_STOPPED = EventSpec("run/stopped", EventMode.EMIT)
