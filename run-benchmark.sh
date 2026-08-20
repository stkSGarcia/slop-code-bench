#!/usr/bin/env bash
set -euo pipefail

# Use home-directory temp space so Docker bind mounts can access it.
# export SCB_WORKSPACE_DIR="${SCB_WORKSPACE_DIR:-$HOME/.slop-workspaces}"

workflow="artnet" # openspec, artnet, or synergyspec
version="1.3.2"

# Configure the output-directory name for each workflow here.
declare -A workflow_save_names=(
  [openspec]=OpenSpec
  [artnet]=ArtNet
  [synergyspec]=SynergySpec
)

case "$workflow" in
  openspec)
    agent_config=configs/agents/codex-openspec.yaml
    environment_config=docker-python3.12-uv
    ;;
  artnet)
    agent_config=configs/agents/codex-artnet.yaml
    environment_config=docker-python3.12-uv-artnet
    ;;
  synergyspec)
    agent_config=configs/agents/codex-synergyspec.yaml
    environment_config=docker-python3.12-uv
    ;;
  *)
    echo "workflow must be openspec, artnet, or synergyspec" >&2
    exit 2
    ;;
esac

save_dir="outputs/${workflow_save_names[$workflow]}-v${version}"

problems=(
  # Easy
  cfgpipe
  code_search
  env_manager
  execution_server
  forge

  # Medium
  circuit_eval
  database_migration
  file_query_tool
  mvvault
  trajectory_api

  # Hard
  eve_industry
  meshctl
  mocked_http
  recli
  test_translator
  rejector
)

problem_args=()
for problem in "${problems[@]}"; do
  problem_args+=(--problem "$problem")
done

exec uv run slop-code run \
  --agent "$agent_config" \
  --environment "$environment_config" \
  --prompt checkpoint-only \
  --model codex_auth/gpt-5.5 \
  --num-workers 4 \
  "${problem_args[@]}" \
  thinking=high \
  pass_policy=any-case \
  save_dir="$save_dir" \
  'save_template=${model.name}_${thinking}_${now:%Y%m%dT%H%M}'
