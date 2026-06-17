from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from slop_code.common import FILES_QUALITY_SAVENAME
from slop_code.common import QUALITY_DIR
from slop_code.common import QUALITY_METRIC_SAVENAME
from slop_code.metrics.checkpoint.delta import safe_delta_pct
from slop_code.metrics.checkpoint.delta import safe_ratio
from slop_code.metrics.checkpoint.loaders import load_file_metrics
from slop_code.metrics.checkpoint.loaders import load_snapshot_metrics

CHECKPOINT_RE = re.compile(r"^checkpoint_(\d+)$")


def checkpoint_number(path: Path) -> int | None:
    match = CHECKPOINT_RE.match(path.name)
    if match is None:
        return None
    return int(match.group(1))


def discover_checkpoint_dirs(problem_dir: Path) -> list[tuple[int, Path]]:
    checkpoints: list[tuple[int, Path]] = []
    for child in problem_dir.iterdir():
        if not child.is_dir():
            continue
        number = checkpoint_number(child)
        if number is not None:
            checkpoints.append((number, child))
    return sorted(checkpoints)


def empty_counts() -> dict[str, int]:
    return {
        "changed_files": 0,
        "created": 0,
        "modified": 0,
        "deleted": 0,
        "lines_added": 0,
        "lines_removed": 0,
    }


def add_file_diff(counts: dict[str, int], file_diff: dict[str, Any]) -> None:
    change_type = file_diff.get("change_type")
    if change_type == "created":
        counts["created"] += 1
    elif change_type == "modified":
        counts["modified"] += 1
    elif change_type == "deleted":
        counts["deleted"] += 1

    counts["changed_files"] += 1
    counts["lines_added"] += int(file_diff.get("lines_added") or 0)
    counts["lines_removed"] += int(file_diff.get("lines_removed") or 0)


def summarize_analyzed_diff(
    checkpoint_dir: Path,
    file_diffs: dict[str, Any],
) -> dict[str, int]:
    """Match checkpoint metric extraction's file-metrics-filtered logic."""
    file_metrics_path = (
        checkpoint_dir / QUALITY_DIR / FILES_QUALITY_SAVENAME
    )
    if not file_metrics_path.exists():
        print(
            "File metrics not found, original-style counts will be zero: "
            f"{file_metrics_path}"
        )

    counts = empty_counts()
    for file_metric in load_file_metrics(checkpoint_dir):
        file_path = file_metric.get("file_path")
        if not isinstance(file_path, str):
            continue

        file_diff = file_diffs.get(file_path)
        if isinstance(file_diff, dict):
            add_file_diff(counts, file_diff)

    return counts


def load_quality_delta_inputs(checkpoint_dir: Path) -> dict[str, int]:
    quality_path = checkpoint_dir / QUALITY_DIR / QUALITY_METRIC_SAVENAME
    snapshot = load_snapshot_metrics(checkpoint_dir)
    if snapshot is None:
        raise SystemExit(f"Quality metrics not found: {quality_path}")

    try:
        return {
            "total_lines": int(snapshot["lines"]["total_lines"]),
            "ast_grep_violations": int(
                snapshot["ast_grep"]["violations"]
            ),
        }
    except KeyError as e:
        raise SystemExit(
            f"Invalid quality metrics in {quality_path}: missing {e}"
        ) from e


def summarize_diff(
    *,
    problem: str,
    checkpoint_num: int,
    checkpoint_dir: Path,
    base_checkpoint: int,
    base_quality: dict[str, int],
    diff_filename: str,
) -> dict[str, Any] | None:
    diff_path = checkpoint_dir / diff_filename
    if not diff_path.exists():
        print(f"Diff file not found, skipping: {diff_path}")
        return None

    with diff_path.open() as f:
        diff = json.load(f)

    file_diffs = diff.get("file_diffs", {})
    if not isinstance(file_diffs, dict):
        raise ValueError(f"Invalid file_diffs in {diff_path}")

    analyzed_counts = summarize_analyzed_diff(checkpoint_dir, file_diffs)
    current_quality = load_quality_delta_inputs(checkpoint_dir)
    churn = (
        analyzed_counts["lines_added"] + analyzed_counts["lines_removed"]
    )

    return {
        "problem": problem,
        "checkpoint": checkpoint_dir.name,
        "checkpoint_num": checkpoint_num,
        "base_checkpoint": base_checkpoint,
        "diff_file": str(diff_path),
        "from_checksum": diff.get("from_checksum"),
        "to_checksum": diff.get("to_checksum"),
        "changed_files": analyzed_counts["changed_files"],
        "created": analyzed_counts["created"],
        "modified": analyzed_counts["modified"],
        "deleted": analyzed_counts["deleted"],
        "lines_added": analyzed_counts["lines_added"],
        "lines_removed": analyzed_counts["lines_removed"],
        "delta.churn_ratio": safe_ratio(
            churn,
            base_quality["total_lines"],
        ),
        "delta.ast_grep_violations": safe_delta_pct(
            base_quality["ast_grep_violations"],
            current_quality["ast_grep_violations"],
        ),
    }


def summarize_diffs(
    run_dir: Path,
    problem: str,
    base_checkpoint: int,
    diff_filename: str,
    output_jsonl: Path,
    *,
    append: bool = False,
) -> None:
    problem_dir = run_dir / problem
    if not problem_dir.exists():
        raise SystemExit(f"Problem directory not found: {problem_dir}")

    base_checkpoint_dir = problem_dir / f"checkpoint_{base_checkpoint}"
    base_quality = load_quality_delta_inputs(base_checkpoint_dir)

    rows = []
    for checkpoint_num, checkpoint_dir in discover_checkpoint_dirs(problem_dir):
        if checkpoint_num <= base_checkpoint:
            continue

        row = summarize_diff(
            problem=problem,
            checkpoint_num=checkpoint_num,
            checkpoint_dir=checkpoint_dir,
            base_checkpoint=base_checkpoint,
            base_quality=base_quality,
            diff_filename=diff_filename,
        )
        if row is not None:
            rows.append(row)

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with output_jsonl.open(mode) as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    action = "Appended to" if append else "Wrote"
    print(f"{action} {output_jsonl} ({len(rows)} rows)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize checkpoint-to-base diff files into JSONL.",
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("problem")
    parser.add_argument("base_checkpoint", type=int)
    parser.add_argument("diff_filename")
    parser.add_argument("output_jsonl", type=Path)
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows to output_jsonl instead of overwriting it.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summarize_diffs(
        run_dir=args.run_dir,
        problem=args.problem,
        base_checkpoint=args.base_checkpoint,
        diff_filename=args.diff_filename,
        output_jsonl=args.output_jsonl,
        append=args.append,
    )


if __name__ == "__main__":
    main()
