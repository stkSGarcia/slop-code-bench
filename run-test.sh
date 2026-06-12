#!/usr/bin/env bash
set -e

REPO=../exps/exp-meshctl
PROBLEM=meshctl
ENV_CONFIG=configs/environments/docker-python3.12-uv.yaml
SAVE_DIR=eval-results
MAP_FILE=../exps/exp-meshctl/ckp-mapping.json

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
uv run slop-code utils repopulate-diffs "$SAVE_DIR" -p "$PROBLEM"

# Step 3: full evaluation — tests + quality + verbosity/erosion + delta
echo "=== Running full evaluation ==="
uv run slop-code eval "$SAVE_DIR" \
  --problem "$PROBLEM" \
  -e "$ENV_CONFIG"

echo "Done."
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/evaluation.json"
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/diff.json"
echo "  Aggregated:     $SAVE_DIR/checkpoint_results.jsonl"
