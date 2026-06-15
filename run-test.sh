#!/usr/bin/env bash
set -e

# Customize these variables as needed for your experiment setup
PROBLEM=meshctl
REPO=../exps/exp-meshctl
MAP_FILE=../exps/exp-meshctl/ckp-mapping.json
SAVE_DIR=eval-results
EVAL_START_CHECKPOINT=5
BASE_DIFF_CHECKPOINT=1
DELETE_SNAPSHOT=1

BASE_DIFF_FILENAME="diff_from_ckpt_${BASE_DIFF_CHECKPOINT}.json"
BASE_DIFF_SUMMARY_FILE="$SAVE_DIR/diff_from_ckpt_results.jsonl"
UV_RUN=(uv run --python 3.12)
ENV_CONFIG=configs/environments/docker-python3.12-uv.yaml

mkdir -p "$SAVE_DIR/$PROBLEM"

# Config required by slop-code eval
cat > "$SAVE_DIR/config.yaml" <<EOF
problems:
  - $PROBLEM
model:
  name: codex-gpt5.5
thinking: high
prompt_path: external
agent:
  type: external
EOF

# Step 1: populate snapshot/ dirs from git commits
for checkpoint_num in $(jq -r 'keys[]' "$MAP_FILE" | sort -n); do
  commit_id=$(jq -r --arg k "$checkpoint_num" '.[$k]' "$MAP_FILE")
  snapshot_dir="$SAVE_DIR/$PROBLEM/checkpoint_${checkpoint_num}/snapshot"

  echo "=== Export checkpoint $checkpoint_num ($commit_id) ==="
  rm -rf "$snapshot_dir"
  mkdir -p "$snapshot_dir"

  git -C "$REPO" archive "$commit_id" | tar -x -C "$snapshot_dir"
done

# Step 2: generate diff.json files used for churn metrics
echo "=== Regenerating diff.json ==="
"${UV_RUN[@]}" slop-code utils repopulate-diffs "$SAVE_DIR" -p "$PROBLEM"

# Also generate diffs from checkpoint 1 for cross-checkpoint comparison.
echo "=== Regenerating $BASE_DIFF_FILENAME ==="
"${UV_RUN[@]}" python generate-base-diffs.py "$SAVE_DIR" "$PROBLEM" \
  "$BASE_DIFF_CHECKPOINT" "$BASE_DIFF_FILENAME"

# Step 3: full evaluation — tests + quality + verbosity/erosion + delta
echo "=== Running full evaluation ==="
"${UV_RUN[@]}" slop-code eval "$SAVE_DIR" \
  --problem "$PROBLEM" \
  --start-checkpoint "$EVAL_START_CHECKPOINT" \
  -e "$ENV_CONFIG"

# Step 4: summarize checkpoint-to-base diffs into a run-level JSONL file
echo "=== Summarizing $BASE_DIFF_FILENAME ==="
"${UV_RUN[@]}" python summarize-diff-from-ckpt.py "$SAVE_DIR" "$PROBLEM" \
  "$BASE_DIFF_CHECKPOINT" "$BASE_DIFF_FILENAME" "$BASE_DIFF_SUMMARY_FILE"

if [[ "$DELETE_SNAPSHOT" -eq 1 ]]; then
  echo "=== Deleting checkpoint snapshot directories ==="
  shopt -s nullglob
  snapshot_dirs=("$SAVE_DIR/$PROBLEM"/checkpoint_*/snapshot)
  shopt -u nullglob

  for snapshot_dir in "${snapshot_dirs[@]}"; do
    if [[ -d "$snapshot_dir" ]]; then
      echo "Deleting $snapshot_dir"
      rm -rf "$snapshot_dir"
    fi
  done
fi

echo "Done."
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/evaluation.json"
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/diff.json"
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/$BASE_DIFF_FILENAME"
echo "  Aggregated:     $SAVE_DIR/checkpoint_results.jsonl"
echo "  Base diffs:     $BASE_DIFF_SUMMARY_FILE"
echo "  Eval start:     checkpoint_$EVAL_START_CHECKPOINT"
