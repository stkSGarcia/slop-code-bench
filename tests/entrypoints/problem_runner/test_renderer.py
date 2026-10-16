"""Check spending across checkpoint completion and failure boundaries."""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from slop_code.agent_runner import AgentStateEnum
from slop_code.agent_runner import MetricsTracker
from slop_code.agent_runner import UsageTracker
from slop_code.entrypoints.problem_runner.renderer import (
    ProblemProgressRenderer,
)
from slop_code.entrypoints.problem_runner.state import ProblemStateTracker


@pytest.mark.parametrize(
    "state",
    [AgentStateEnum.RUNNING, AgentStateEnum.COMPLETED, AgentStateEnum.ERROR],
)
@pytest.mark.parametrize("included", [False, True])
def test_spend_counts_current_checkpoint_once(
    state: AgentStateEnum, *, included: bool
) -> None:
    now = datetime.now()
    metrics = MetricsTracker(
        state=state,
        current_checkpoint="checkpoint_1",
        usage=UsageTracker(cost=10),
        started=now,
        checkpoint_started=now,
    )
    current = UsageTracker(cost=3)
    tracker = ProblemStateTracker(["problem"])
    renderer = ProblemProgressRenderer(tracker, Path("run"), now, 1, 1)
    if included:
        metrics.finish_checkpoint(current)
    tracker.handle_update("problem", current, metrics)
    stream = StringIO()
    Console(file=stream, width=160, color_system=None).print(
        renderer._build_header()
    )

    assert "Spend: $13.00" in stream.getvalue()
    assert tracker["problem"].net_cost == 13
    assert tracker["problem"].prior_cost == 10
    assert tracker["problem"].get_simple_state()["total_cost"] == 13

    metrics.checkpoint_usage_included = False
    metrics.current_checkpoint = "checkpoint_2"
    tracker.handle_update("problem", UsageTracker(cost=2), metrics)
    assert tracker["problem"].net_cost == (15 if included else 12)
