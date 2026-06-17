#!/usr/bin/env python
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from slop_code.common import QUALITY_DIR
from slop_code.common import QUALITY_METRIC_SAVENAME
from slop_code.common import SNAPSHOT_DIR_NAME


def load_mapping(path: Path) -> dict[int, str]:
    with path.open() as f:
        raw = json.load(f)

    return {int(key): str(value) for key, value in raw.items()}


def load_helper_function(file_name: str, function_name: str) -> Any:
    script_path = Path(__file__).resolve().parent / file_name
    spec = importlib.util.spec_from_file_location(file_name, script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load helper script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)


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


def export_snapshot(repo: Path, commit: str, snapshot_dir: Path) -> None:
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(  # noqa: S603,S607
        ["git", "-C", str(repo), "archive", commit],  # noqa: S607
        check=True,
        capture_output=True,
    )
    safe_extract_tar(completed.stdout, snapshot_dir)


def delete_snapshots(problem_dir: Path) -> None:
    for snapshot_dir in problem_dir.glob("checkpoint_*/snapshot"):
        if snapshot_dir.is_dir():
            print(f"Deleting {snapshot_dir}")
            shutil.rmtree(snapshot_dir)


def ensure_quality_exists(problem_dir: Path, checkpoint_numbers: list[int]) -> None:
    missing = []
    for number in checkpoint_numbers:
        quality_file = (
            problem_dir
            / f"checkpoint_{number}"
            / QUALITY_DIR
            / QUALITY_METRIC_SAVENAME
        )
        if not quality_file.exists():
            missing.append(quality_file)

    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise SystemExit(
            "Required quality metrics are missing. "
            "This script does not recompute static analysis:\n"
            f"{formatted}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Temporarily export snapshots, rebuild one base diff, append its "
            "summary rows, then delete snapshots."
        )
    )
    parser.add_argument("--run-dir", type=Path, default=Path("eval"))
    parser.add_argument("--problem", default="meshctl")
    parser.add_argument("--repo", type=Path, default=Path("../exps/exp-meshctl"))
    parser.add_argument(
        "--map-file",
        type=Path,
        default=Path("eval/ckp-mapping.json"),
    )
    parser.add_argument("--base-checkpoint", type=int, default=4)
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=None,
        help="Defaults to RUN_DIR/diff_from_ckpt_results.jsonl.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    problem_dir = args.run_dir / args.problem
    if not problem_dir.exists():
        raise SystemExit(f"Problem directory not found: {problem_dir}")

    mapping = load_mapping(args.map_file)
    if args.base_checkpoint not in mapping:
        raise SystemExit(
            f"checkpoint_{args.base_checkpoint} is missing from {args.map_file}"
        )

    checkpoint_numbers = sorted(mapping)
    ensure_quality_exists(problem_dir, checkpoint_numbers)

    for number in checkpoint_numbers:
        commit = mapping[number]
        snapshot_dir = problem_dir / f"checkpoint_{number}" / SNAPSHOT_DIR_NAME
        print(f"Exporting checkpoint_{number} snapshot from {commit[:12]}")
        export_snapshot(args.repo, commit, snapshot_dir)

    diff_filename = f"diff_from_ckpt_{args.base_checkpoint}.json"
    generate_base_diffs = load_helper_function(
        "generate-base-diffs.py",
        "generate_base_diffs",
    )
    generate_base_diffs(
        args.run_dir,
        args.problem,
        args.base_checkpoint,
        diff_filename,
    )

    summary_file = args.summary_file or (
        args.run_dir / "diff_from_ckpt_results.jsonl"
    )
    summarize_diffs = load_helper_function(
        "summarize-diff-from-ckpt.py",
        "summarize_diffs",
    )
    summarize_diffs(
        args.run_dir,
        args.problem,
        args.base_checkpoint,
        diff_filename,
        summary_file,
        append=True,
    )

    delete_snapshots(problem_dir)
    print("Done.")
    print(f"  Base diff file: checkpoint_N/{diff_filename}")
    print(f"  Summary file:   {summary_file}")


if __name__ == "__main__":
    main()
