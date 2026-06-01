#!/usr/bin/env bash
set -e

REPO=../exp-meshctl
PROBLEM=meshctl
ENV_CONFIG=configs/environments/docker-python3.12-uv.yaml
SAVE_DIR=eval-results
MAP_FILE=../meshctl-ckps.json

mkdir -p "$SAVE_DIR/$PROBLEM"

# Config required by slop-code eval
cat > "$SAVE_DIR/config.yaml" <<EOF
problems:
  - $PROBLEM
model:
  name: claude-sonnet-4-6
thinking: high
prompt_path: external
agent:
  type: external
EOF

# Step 1: populate snapshot/ dirs from git commits
for checkpoint_num in $(jq -r 'keys[]' "$MAP_FILE" | sort -n); do
  commit_id=$(jq -r --arg k "$checkpoint_num" '.[$k]' "$MAP_FILE")
  snapshot_dir="$SAVE_DIR/$PROBLEM/checkpoint_${checkpoint_num}/snapshot"

  echo "=== Checkout checkpoint $checkpoint_num ($commit_id) ==="
  mkdir -p "$snapshot_dir"

  worktree_dir=$(mktemp -d)
  git -C "$REPO" worktree add --detach "$worktree_dir" "$commit_id"
  cp -r "$worktree_dir/." "$snapshot_dir/"
  git -C "$REPO" worktree remove --force "$worktree_dir"
done

# Step 2: full evaluation — tests + quality + verbosity/erosion + delta
echo "=== Running full evaluation ==="
uv run slop-code eval "$SAVE_DIR" \
  --problem "$PROBLEM" \
  -e "$ENV_CONFIG"

echo "Done."
echo "  Per-checkpoint: $SAVE_DIR/$PROBLEM/checkpoint_N/evaluation.json"
echo "  Aggregated:     $SAVE_DIR/checkpoint_results.jsonl"
