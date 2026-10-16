"""Request-level Codex usage accounting, including delegated threads."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from slop_code.agent_runner.models import AgentError
from slop_code.common.llms import APIPricing
from slop_code.common.llms import TokenUsage


def parse_usage(usage: dict[str, Any]) -> TokenUsage:
    """Keep Codex's inclusive input and output counts intact."""
    return TokenUsage(
        input=int(usage.get("input_tokens") or 0),
        output=int(usage.get("output_tokens") or 0),
        cache_read=int(usage.get("cached_input_tokens") or 0),
        cache_write=int(usage.get("cache_write_input_tokens") or 0),
        reasoning=int(usage.get("reasoning_output_tokens") or 0),
    )


def reported_cost(info: dict[str, Any]) -> float | None:
    """A reported zero is an authoritative cost, too."""
    for key in ("total_cost", "cost_usd"):
        value = info.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


@dataclass
class _Trace:
    thread: str
    requests: list[dict[str, Any]] = field(default_factory=list)
    counts: list[dict[str, Any]] = field(default_factory=list)


def _read_trace(path: Path) -> _Trace:
    trace = _Trace(thread=str(path))
    has_metadata = False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A timed-out process can leave an unfinished final line.
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("type") == "session_meta" and not has_metadata:
                # Forked rollouts can also contain their parent's metadata.
                trace.thread = str(payload.get("id") or path)
                has_metadata = True
            elif event.get("type") == "token_usage_record":
                if (
                    payload.get("thread_id")
                    and payload.get("response_id")
                    and isinstance(payload.get("usage"), dict)
                ):
                    trace.requests.append(payload)
            elif (
                event.get("type") == "event_msg"
                and payload.get("type") == "token_count"
            ):
                info = payload.get("info")
                if isinstance(info, dict) and isinstance(
                    info.get("total_token_usage"), dict
                ):
                    trace.counts.append(info)
    return trace


class CodexUsageTracker:
    """Consume each request once across workflow nodes and resumed calls.

    Native request records include compaction and avoid inherited-history
    double counting. Older rollouts fall back to cumulative token deltas;
    tiered pricing requires those deltas to represent individual requests.
    """

    def __init__(self) -> None:
        self._responses: set[tuple[str, str]] = set()
        self._tokens: dict[str, TokenUsage] = {}
        self._costs: dict[str, float] = {}

    def read(
        self,
        paths: Iterable[Path],
        pricing: APIPricing | None,
        *,
        reported_costs: dict[str, float] | None = None,
    ) -> tuple[float | None, TokenUsage | None]:
        traces = [_read_trace(path) for path in sorted(paths)]
        native_threads = {
            record["thread_id"] for trace in traces for record in trace.requests
        }
        total_tokens = TokenUsage()
        costs: dict[str, float] = {}
        responses = set(self._responses)
        consumed_tokens = dict(self._tokens)
        found = False
        thread_reported_costs = dict(reported_costs or {})
        for trace in traces:
            cost = reported_cost(trace.counts[-1]) if trace.counts else None
            if cost is not None:
                thread_reported_costs.setdefault(trace.thread, cost)
        for trace in traces:
            for record in trace.requests:
                found = True
                thread = record["thread_id"]
                key = (thread, record["response_id"])
                if key in responses:
                    continue
                responses.add(key)
                tokens = parse_usage(record["usage"])
                total_tokens += tokens
                cost = (
                    pricing.get_cost(tokens, cache_write_in_input=True)
                    if pricing is not None
                    else 0.0
                )
                costs[thread] = costs.get(thread, 0.0) + cost
            if trace.thread not in native_threads:
                last_cost = thread_reported_costs.get(trace.thread)
                for info in trace.counts:
                    found = True
                    tokens, cost = self._legacy_delta(
                        trace.thread,
                        info,
                        pricing if last_cost is None else None,
                        consumed_tokens,
                    )
                    total_tokens += tokens
                    costs[trace.thread] = costs.get(trace.thread, 0.0) + cost

        # Prefer a provider-reported thread total over local estimates.
        # Subagents have their own totals; never replace the whole invocation
        # with just one thread's reported cost or tokens.
        if not found:
            return None, None
        for thread, cost in thread_reported_costs.items():
            costs[thread] = cost - self._costs.get(thread, 0.0)
        for thread, cost in costs.items():
            self._costs[thread] = self._costs.get(thread, 0.0) + cost
        self._responses = responses
        self._tokens = consumed_tokens
        return sum(costs.values()), total_tokens

    @staticmethod
    def _legacy_delta(
        thread: str,
        info: dict[str, Any],
        pricing: APIPricing | None,
        consumed_tokens: dict[str, TokenUsage],
    ) -> tuple[TokenUsage, float]:
        current = parse_usage(info["total_token_usage"])
        previous = consumed_tokens.get(thread, TokenUsage())
        if any(
            value < getattr(previous, key)
            for key, value in current.model_dump().items()
        ):
            # Ignore an older snapshot copied into another rollout.
            return TokenUsage(), 0.0
        delta = TokenUsage(
            **{
                key: value - getattr(previous, key)
                for key, value in current.model_dump().items()
            }
        )
        consumed_tokens[thread] = current
        if delta == TokenUsage() or pricing is None:
            return delta, 0.0
        last = info.get("last_token_usage")
        if (
            pricing.prompt_tiers
            and reported_cost(info) is None
            and (not isinstance(last, dict) or parse_usage(last) != delta)
        ):
            raise AgentError(
                "Cannot price tiered Codex usage without per-request "
                "records, matching last_token_usage, or a reported cost"
            )
        return delta, pricing.get_cost(delta, cache_write_in_input=True)
