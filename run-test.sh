#!/usr/bin/env bash
set -e

REPO=../exps/exp-meshctl
PROBLEM=meshctl
ENV_CONFIG=configs/environments/docker-python3.12-uv.yaml
SAVE_DIR=eval-results
MAP_FILE=../exps/exp-meshctl/ckp-mapping.json
UV_RUN=(uv run --python 3.12)
BASE_DIFF_CHECKPOINT=1
BASE_DIFF_FILENAME="diff_from_ckpt_${BASE_DIFF_CHECKPOINT}.json"
BASE_DIFF_SUMMARY_FILE="$SAVE_DIR/diff_from_ckpt_results.jsonl"
EVAL_START_CHECKPOINT=1

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

echo "Done."
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/evaluation.json"
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/diff.json"
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/$BASE_DIFF_FILENAME"
echo "  Aggregated:     $SAVE_DIR/checkpoint_results.jsonl"
echo "  Base diffs:     $BASE_DIFF_SUMMARY_FILE"
echo "  Eval start:     checkpoint_$EVAL_START_CHECKPOINT"
