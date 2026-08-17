#!/usr/bin/env bash
set -euo pipefail

# Use home-directory temp space so Docker bind mounts can access it.
# export SCB_WORKSPACE_DIR="${SCB_WORKSPACE_DIR:-$HOME/.slop-workspaces}"

workflow=openspec # openspec, artnet, or synergyspec
case "$workflow" in
  openspec)
    agent_config=configs/agents/codex-openspec.yaml
    environment_config=docker-python3.12-uv
    save_dir=outputs/codex_openspec
    ;;
  artnet)
    agent_config=configs/agents/codex-artnet.yaml
    environment_config=docker-python3.12-uv-artnet
    save_dir=outputs/codex_artnet
    ;;
  synergyspec)
    agent_config=configs/agents/codex-synergyspec.yaml
    environment_config=docker-python3.12-uv
    save_dir=outputs/codex_synergyspec
    ;;
  *)
    echo "workflow must be openspec, artnet, or synergyspec" >&2
    exit 2
    ;;
esac

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
