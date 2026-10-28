#!/usr/bin/env bash
set -euo pipefail

# Use home-directory temp space so Docker bind mounts can access it.
# export SCB_WORKSPACE_DIR="${SCB_WORKSPACE_DIR:-$HOME/.slop-workspaces}"

workflow="matspec" # openspec, artnet, synergyspec, matspec, or matspec-artnet

# Values: output name | experiment version | agent config suffix.
# Versions only label output directories; agent configs select dependencies.
declare -A workflows=(
  [openspec]="OpenSpec|1.7.0|openspec"
  [artnet]="ArtNet|1.4.4|artnet"
  [synergyspec]="SynergySpec|3.0.2|synergyspec"
  [matspec]="MatrixSpec|0.4.1-beta.3|matrixspec"
  [matspec-artnet]="MatrixSpec-ArtNet|0.4.1-beta.3-1.4.4|matrixspec-artnet"
)

if [[ -z "$workflow" || -z "${workflows[$workflow]:-}" ]]; then
  echo "Unknown workflow: $workflow" >&2
  exit 2
fi

IFS='|' read -r workflow_save_name experiment_version agent_name \
  <<< "${workflows[$workflow]}"
agent_config="configs/agents/codex-${agent_name}.yaml"
environment_config=configs/environments/docker-python3.12-uv.yaml
save_dir="outputs/${workflow_save_name}-v${experiment_version}"

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
  --model codex_auth/gpt-6.1-sol \
  --num-workers 4 \
  "${problem_args[@]}" \
  thinking=high \
  pass_policy=any-case \
  save_dir="$save_dir" \
  'save_template=${model.name}_${thinking}_${now:%Y%m%dT%H%M}'
