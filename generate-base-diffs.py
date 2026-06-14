from __future__ import annotations

import argparse
from pathlib import Path

from slop_code.execution.snapshot import create_diff_from_directories


def checkpoint_sort_key(path: Path) -> tuple[int, int | str]:
    prefix = "checkpoint_"
    if path.name.startswith(prefix):
        suffix = path.name.removeprefix(prefix)
        if suffix.isdigit():
            return (0, int(suffix))
    return (1, path.name)


def generate_base_diffs(
    run_dir: Path,
    problem: str,
    base_checkpoint: int,
    output_filename: str,
) -> None:
    problem_dir = run_dir / problem
    base_snapshot_dir = (
        problem_dir / f"checkpoint_{base_checkpoint}" / "snapshot"
    )

    if not base_snapshot_dir.exists():
        print(f"Base snapshot not found, skipping: {base_snapshot_dir}")
        return

    checkpoint_dirs = sorted(
        problem_dir.glob("checkpoint_*"),
        key=checkpoint_sort_key,
    )

    for checkpoint_dir in checkpoint_dirs:
        snapshot_dir = checkpoint_dir / "snapshot"
        if not snapshot_dir.exists():
            continue

        diff = create_diff_from_directories(
            from_dir=base_snapshot_dir,
            to_dir=snapshot_dir,
        )
        diff_path = checkpoint_dir / output_filename
        diff_path.write_text(diff.model_dump_json(indent=2) + "\n")
        print(f"Wrote {diff_path} ({len(diff.file_diffs)} changed files)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate checkpoint diffs from a fixed base checkpoint.",
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("problem")
    parser.add_argument("base_checkpoint", type=int)
    parser.add_argument("output_filename")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_base_diffs(
        run_dir=args.run_dir,
        problem=args.problem,
        base_checkpoint=args.base_checkpoint,
        output_filename=args.output_filename,
    )


if __name__ == "__main__":
    main()
