"""Regression tests for request-level Codex billing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from slop_code.agent_runner.agents.codex.usage import CodexUsageTracker
from slop_code.agent_runner.agents.codex.usage import reported_cost
from slop_code.agent_runner.models import AgentError
from slop_code.common.llms import APIPricing
from slop_code.common.llms import TokenUsage


@pytest.fixture
def pricing() -> APIPricing:
    return APIPricing.model_validate(
        {
            "input": 4,
            "output": 15,
            "cache_read": 0.2,
            "cache_write": 5,
            "prompt_tiers": [
                {
                    "max_input_tokens": 272_000,
                    "input": 2,
                    "output": 10,
                    "cache_read": 0.1,
                    "cache_write": 2.5,
                }
            ],
        }
    )


def _usage(
    input_tokens: int = 100_000,
    cached: int = 80_000,
    output: int = 1_000,
    writes: int = 0,
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": writes,
        "output_tokens": output,
        "reasoning_output_tokens": 100,
    }


def _request(
    thread: str,
    response: str,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread,
            "response_id": response,
            "usage": usage or _usage(),
        },
    }


def _count(
    total: dict[str, int],
    last: dict[str, int] | None = None,
    cost: float | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {"total_token_usage": total}
    if last is not None:
        info["last_token_usage"] = last
    if cost is not None:
        info["total_cost"] = cost
    return {
        "type": "event_msg",
        "payload": {"type": "token_count", "info": info},
    }


def _write(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


def test_prices_each_request_and_sums_delegated_threads(
    tmp_path: Path, pricing: APIPricing
) -> None:
    parent = _request("parent", "first")
    paths = [
        _write(tmp_path / "parent.jsonl", [parent]),
        _write(
            tmp_path / "child.jsonl",
            [parent, _request("child", "second")],
        ),
        _write(tmp_path / "last.jsonl", [_request("last", "third")]),
    ]

    cost, tokens = CodexUsageTracker().read(paths, pricing)

    assert tokens == TokenUsage(
        input=300_000, cache_read=240_000, output=3_000, reasoning=300
    )
    assert cost == pytest.approx(0.174)
    assert pricing.get_cost(tokens) == pytest.approx(0.333)


def test_mixed_tiers_use_inclusive_prompt_size(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [
            _request("root", "boundary", _usage(272_000, 250_000)),
            _request("root", "above", _usage(272_001, 250_000)),
        ],
    )

    cost, _ = CodexUsageTracker().read([path], pricing)

    expected = (
        22_000 * 2
        + 250_000 * 0.1
        + 1_000 * 10
        + 22_001 * 4
        + 250_000 * 0.2
        + 1_000 * 15
    ) / 1_000_000
    assert cost == pytest.approx(expected)


def test_cache_writes_are_preserved_and_not_charged_twice(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [_request("root", "response", _usage(writes=5_000))],
    )

    cost, tokens = CodexUsageTracker().read([path], pricing)

    assert tokens is not None
    assert tokens.input == 100_000
    assert tokens.cache_write == 5_000
    assert cost == pytest.approx(0.0605)


def test_request_records_include_compaction_missing_from_token_count(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "root"}},
            _request("root", "normal"),
            _request("root", "compaction"),
            {"type": "compacted", "payload": {}},
            _count(_usage()),
        ],
    )

    cost, tokens = CodexUsageTracker().read([path], pricing)

    assert tokens is not None
    assert tokens.input == 200_000
    assert cost == pytest.approx(0.116)


def test_repeated_workflow_reads_and_resumed_threads_only_charge_new_requests(
    tmp_path: Path, pricing: APIPricing
) -> None:
    first = _request("root", "first")
    path = _write(tmp_path / "first.jsonl", [first])
    tracker = CodexUsageTracker()
    assert tracker.read([path], pricing)[0] == pytest.approx(0.058)
    assert tracker.read([path], pricing) == (0.0, TokenUsage())

    _write(path, [first, _request("root", "resumed")])
    child = _write(
        tmp_path / "child.jsonl",
        [first, _request("child", "second-node")],
    )
    cost, tokens = tracker.read([path, child], pricing)

    assert tokens is not None
    assert tokens.input == 200_000
    assert cost == pytest.approx(0.116)
    assert tracker.read([path, child], pricing) == (0.0, TokenUsage())


def test_legacy_snapshots_use_request_deltas_and_skip_duplicates(
    tmp_path: Path, pricing: APIPricing
) -> None:
    one = _usage()
    two = {key: value * 2 for key, value in one.items()}
    three = {key: value * 3 for key, value in one.items()}
    path = _write(
        tmp_path / "legacy.jsonl",
        [_count(one, one), _count(two, one), _count(two, one)],
    )
    tracker = CodexUsageTracker()
    assert tracker.read([path], pricing)[0] == pytest.approx(0.116)
    _write(path, [_count(one, one), _count(two, one), _count(three, one)])

    cost, tokens = tracker.read([path], pricing)

    assert tokens is not None
    assert tokens.input == 100_000
    assert cost == pytest.approx(0.058)
    assert tracker.read([path], pricing) == (0.0, TokenUsage())


def test_legacy_aggregate_requires_request_detail_for_tiered_prices(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(tmp_path / "trace.jsonl", [_count(_usage(300_000))])

    with pytest.raises(AgentError, match="Cannot price tiered Codex usage"):
        CodexUsageTracker().read([path], pricing)


def test_failed_read_does_not_consume_requests(
    tmp_path: Path, pricing: APIPricing
) -> None:
    native = _write(tmp_path / "a-native.jsonl", [_request("root", "first")])
    legacy = _write(tmp_path / "z-legacy.jsonl", [_count(_usage(300_000))])
    tracker = CodexUsageTracker()
    for _ in range(2):
        with pytest.raises(AgentError, match="Cannot price tiered Codex usage"):
            tracker.read([native, legacy], pricing)
    _write(legacy, [_count(_usage(300_000), cost=0.0)])

    cost, tokens = tracker.read([native, legacy], pricing)

    assert cost == pytest.approx(0.058)
    assert tokens is not None
    assert tokens.input == 400_000


@pytest.mark.parametrize("cost", [0.0, 1.25])
def test_reported_thread_cost_overrides_estimates_and_supports_zero(
    tmp_path: Path, pricing: APIPricing, cost: float
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "root"}},
            _request("root", "first"),
            _count(_usage(), cost=cost),
        ],
    )
    tracker = CodexUsageTracker()

    assert tracker.read([path], pricing)[0] == pytest.approx(cost)
    assert tracker.read([path], pricing) == (0.0, TokenUsage())
    assert reported_cost({"total_cost": 0, "cost_usd": 99}) == 0


def test_legacy_reported_total_does_not_require_request_detail(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [_count(_usage()), _count(_usage(300_000), cost=1.25)],
    )

    cost, tokens = CodexUsageTracker().read([path], pricing)

    assert tokens is not None
    assert tokens.input == 300_000
    assert cost == 1.25


def test_stdout_reported_root_cost_preserves_other_threads(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(
        tmp_path / "trace.jsonl",
        [_request("root", "first"), _request("child", "second")],
    )

    cost, tokens = CodexUsageTracker().read(
        [path], pricing, reported_costs={"root": 1.25}
    )

    assert cost == pytest.approx(1.308)
    assert tokens is not None
    assert tokens.input == 200_000


def test_incomplete_trailing_line_does_not_discard_finished_requests(
    tmp_path: Path, pricing: APIPricing
) -> None:
    path = _write(tmp_path / "trace.jsonl", [_request("root", "first")])
    with path.open("a") as stream:
        stream.write('{"type": "token_usage_record"')

    cost, tokens = CodexUsageTracker().read([path], pricing)

    assert cost == pytest.approx(0.058)
    assert tokens is not None
    assert tokens.input == 100_000
