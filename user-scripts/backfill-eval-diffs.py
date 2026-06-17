#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from slop_code.common import CHECKPOINT_RESULTS_FILENAME
from slop_code.common import CONFIG_FILENAME
from slop_code.common import SNAPSHOT_DIR_NAME
from slop_code.entrypoints.commands.repopulate_diffs import _process_problem
from slop_code.entrypoints.evaluation.metrics import create_problem_reports
from slop_code.entrypoints.evaluation.metrics import update_results_jsonl
from slop_code.entrypoints.utils import display_and_save_summary
from slop_code.evaluation import ProblemConfig
from slop_code.metrics import measure_snapshot_quality
from slop_code.metrics.quality_io import save_quality_metrics

CHECKPOINT_RE = re.compile(r"^checkpoint_(\d+)$")


def checkpoint_number(name: str) -> int | None:
    match = CHECKPOINT_RE.match(name)
    if match is None:
        return None
    return int(match.group(1))


def checkpoint_name(number: int) -> str:
    return f"checkpoint_{number}"


def load_mapping(path: Path) -> dict[int, str]:
    with path.open() as f:
        raw = json.load(f)

    mapping: dict[int, str] = {}
    for key, value in raw.items():
        mapping[int(key)] = str(value)
    return mapping


def discover_checkpoint_numbers(problem_dir: Path) -> list[int]:
    numbers = []
    for child in problem_dir.iterdir():
        if not child.is_dir():
            continue
        number = checkpoint_number(child.name)
        if number is not None:
            numbers.append(number)
    return sorted(numbers)


def discover_evaluated_numbers(problem_dir: Path) -> set[int]:
    numbers = set()
    for checkpoint_dir in problem_dir.glob("checkpoint_*"):
        number = checkpoint_number(checkpoint_dir.name)
        if number is None:
            continue
        if (checkpoint_dir / "evaluation.json").exists():
            numbers.add(number)
    return numbers


def infer_start_checkpoint(
    run_dir: Path,
    problem: str,
    problem_dir: Path,
) -> int:
    results_path = run_dir / CHECKPOINT_RESULTS_FILENAME
    if results_path.exists():
        numbers = []
        with results_path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("problem") != problem:
                    continue
                number = checkpoint_number(str(row.get("checkpoint", "")))
                if number is not None:
                    numbers.append(number)
        if numbers:
            return min(numbers)

    numbers = []
    for checkpoint_dir in problem_dir.glob("checkpoint_*"):
        number = checkpoint_number(checkpoint_dir.name)
        if number is None:
            continue
        if (checkpoint_dir / "evaluation.json").exists():
            numbers.append(number)
    if numbers:
        return min(numbers)

    discovered = discover_checkpoint_numbers(problem_dir)
    if not discovered:
        raise SystemExit(f"No checkpoint directories found in {problem_dir}")
    return min(discovered)


def safe_extract_tar(data: bytes, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            target_path = (target_dir / member.name).resolve()
            if target_root not in (target_path, *target_path.parents):
                raise SystemExit(f"Unsafe archive member: {member.name}")
            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            target_path.write_bytes(source.read())


def export_snapshot(
    repo: Path,
    commit: str,
    snapshot_dir: Path,
    *,
    overwrite_snapshots: bool,
) -> bool:
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        if not overwrite_snapshots:
            return False
        for child in snapshot_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(  # noqa: S603,S607
        ["git", "-C", str(repo), "archive", commit],  # noqa: S607
        check=True,
        capture_output=True,
    )
    safe_extract_tar(completed.stdout, snapshot_dir)
    return True


def load_helper_function(file_name: str, function_name: str) -> Any:
    import importlib.util

    script_path = Path(__file__).resolve().parent / file_name
    spec = importlib.util.spec_from_file_location(file_name, script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load helper script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)


def rebuild_checkpoint_results(
    run_dir: Path,
    problem_dir: Path,
    problem_name: str,
    start_checkpoint: int,
    evaluated_numbers: set[int],
) -> list[dict[str, Any]]:
    problem = ProblemConfig.from_yaml(problem_dir / "problem.yaml")
    reports, errors = create_problem_reports(
        problem_dir,
        problem,
        start_checkpoint=checkpoint_name(start_checkpoint),
    )
    reports = [
        report
        for report in reports
        if (
            checkpoint_number(str(report.get("checkpoint", "")))
            in evaluated_numbers
        )
    ]

    report_file = run_dir / CHECKPOINT_RESULTS_FILENAME
    update_results_jsonl(report_file, reports, replace_problems={problem_name})

    config_path = run_dir / CONFIG_FILENAME
    if config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f)
        display_and_save_summary(
            report_file,
            run_dir,
            config,
            Console(),
            expected_checkpoints=len(reports),
        )

    if errors:
        print("Report warnings:")
        for checkpoint, error in errors:
            print(f"  - {checkpoint}: {error}")

    return reports


def recompute_quality_metrics(
    problem_dir: Path,
    entry_file: str,
    checkpoint_numbers: list[int],
) -> None:
    for number in checkpoint_numbers:
        checkpoint_dir = problem_dir / checkpoint_name(number)
        snapshot_dir = checkpoint_dir / SNAPSHOT_DIR_NAME
        if not snapshot_dir.exists():
            raise SystemExit(
                f"Snapshot not found for quality metrics: {snapshot_dir}"
            )

        quality_result, file_metrics = measure_snapshot_quality(
            entry_file,
            snapshot_dir,
        )
        save_quality_metrics(checkpoint_dir, quality_result, file_metrics)
        print(f"Recomputed quality metrics for checkpoint_{number}")


def delete_snapshots(problem_dir: Path) -> None:
    for snapshot_dir in problem_dir.glob("checkpoint_*/snapshot"):
        if snapshot_dir.is_dir():
            print(f"Deleting {snapshot_dir}")
            shutil.rmtree(snapshot_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill previous-checkpoint diffs, base-checkpoint diffs, and "
            "checkpoint_results.jsonl for an existing eval result directory."
        )
    )
    parser.add_argument("run_dir", type=Path, help="Run directory, e.g. eval-results")
    parser.add_argument("problem", help="Problem name under the run directory")
    parser.add_argument("repo", type=Path, help="Git repo containing checkpoint commits")
    parser.add_argument("map_file", type=Path, help="JSON mapping checkpoint number to commit")
    parser.add_argument(
        "--base-checkpoint",
        type=int,
        action="append",
        default=None,
        help="Base checkpoint number for diff_from_ckpt_N.json",
    )
    parser.add_argument(
        "--start-checkpoint",
        type=int,
        default=None,
        help=(
            "First evaluated checkpoint to keep in checkpoint_results.jsonl. "
            "Defaults to the existing checkpoint_results.jsonl minimum checkpoint, "
            "or the minimum checkpoint with evaluation.json."
        ),
    )
    parser.add_argument(
        "--base-diff-filename",
        default=None,
        help="Output filename for base diffs. Defaults to diff_from_ckpt_<base>.json.",
    )
    parser.add_argument(
        "--base-summary-file",
        type=Path,
        default=None,
        help="Output JSONL for base diff summaries. Defaults to RUN_DIR/diff_from_ckpt_results.jsonl.",
    )
    parser.add_argument(
        "--keep-existing-snapshots",
        action="store_true",
        help=(
            "Do not re-export snapshots that already have files. By default, "
            "snapshots are overwritten from the checkpoint mapping."
        ),
    )
    parser.add_argument(
        "--delete-snapshots",
        action="store_true",
        help="Delete checkpoint snapshot directories after backfill completes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir
    problem_dir = run_dir / args.problem
    if not problem_dir.exists():
        raise SystemExit(f"Problem directory not found: {problem_dir}")

    mapping = load_mapping(args.map_file)
    start_checkpoint = args.start_checkpoint or infer_start_checkpoint(
        run_dir,
        args.problem,
        problem_dir,
    )
    base_checkpoints = args.base_checkpoint or [1]
    if args.base_diff_filename is not None and len(base_checkpoints) > 1:
        raise SystemExit(
            "--base-diff-filename can only be used with a single "
            "--base-checkpoint"
        )

    base_diff_filenames = {
        base_checkpoint: args.base_diff_filename
        or f"diff_from_ckpt_{base_checkpoint}.json"
        for base_checkpoint in base_checkpoints
    }
    base_summary_file = (
        args.base_summary_file
        or run_dir / "diff_from_ckpt_results.jsonl"
    )

    mapped_numbers = sorted(mapping)
    missing_base_checkpoints = [
        base_checkpoint
        for base_checkpoint in base_checkpoints
        if base_checkpoint not in mapping
    ]
    if missing_base_checkpoints:
        formatted = ", ".join(
            f"checkpoint_{base_checkpoint}"
            for base_checkpoint in missing_base_checkpoints
        )
        raise SystemExit(
            f"Base checkpoint(s) missing from {args.map_file}: {formatted}"
        )

    print(
        "Backfilling "
        f"{run_dir}/{args.problem}: start=checkpoint_{start_checkpoint}, "
        f"base={', '.join(f'checkpoint_{n}' for n in base_checkpoints)}"
    )
    print(
        "Mapped checkpoints: "
        f"{', '.join(f'checkpoint_{n}' for n in mapped_numbers)}"
    )

    for number in mapped_numbers:
        commit = mapping[number]
        snapshot_dir = problem_dir / checkpoint_name(number) / SNAPSHOT_DIR_NAME
        exported = export_snapshot(
            args.repo,
            commit,
            snapshot_dir,
            overwrite_snapshots=not args.keep_existing_snapshots,
        )
        if exported:
            print(f"Exported checkpoint_{number} snapshot from {commit[:12]}")

    print("Regenerating previous-checkpoint diff.json files...")
    _process_problem(problem_dir)

    print("Recomputing static quality metrics for all mapped checkpoints...")
    problem_config = ProblemConfig.from_yaml(problem_dir / "problem.yaml")
    recompute_quality_metrics(
        problem_dir,
        problem_config.entry_file,
        mapped_numbers,
    )

    print("Generating base diff files...")
    generate_base_diffs = load_helper_function(
        "generate-base-diffs.py",
        "generate_base_diffs",
    )
    for base_checkpoint in base_checkpoints:
        base_diff_filename = base_diff_filenames[base_checkpoint]
        print(f"Generating {base_diff_filename} files...")
        generate_base_diffs(
            run_dir,
            args.problem,
            base_checkpoint,
            base_diff_filename,
        )

    print("Rebuilding checkpoint_results.jsonl...")
    reports = rebuild_checkpoint_results(
        run_dir,
        problem_dir,
        args.problem,
        start_checkpoint,
        {
            number
            for number in discover_evaluated_numbers(problem_dir)
            if number >= start_checkpoint
        },
    )

    print(f"Writing base diff summary: {base_summary_file}")
    summarize_diffs = load_helper_function(
        "summarize-diff-from-ckpt.py",
        "summarize_diffs",
    )
    base_summary_file.parent.mkdir(parents=True, exist_ok=True)
    base_summary_file.write_text("")
    for base_checkpoint in base_checkpoints:
        summarize_diffs(
            run_dir,
            args.problem,
            base_checkpoint,
            base_diff_filenames[base_checkpoint],
            base_summary_file,
            append=True,
        )

    if args.delete_snapshots:
        print("Deleting checkpoint snapshot directories...")
        delete_snapshots(problem_dir)

    print("Done.")
    print(f"  Reports: {run_dir / CHECKPOINT_RESULTS_FILENAME} ({len(reports)} rows)")
    print(f"  Previous diffs: {problem_dir}/checkpoint_N/diff.json")
    for base_checkpoint in base_checkpoints:
        print(
            "  Base diffs: "
            f"{problem_dir}/checkpoint_N/{base_diff_filenames[base_checkpoint]}"
        )
    print(f"  Base summary: {base_summary_file}")


if __name__ == "__main__":
    main()
