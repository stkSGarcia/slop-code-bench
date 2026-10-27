#!/usr/bin/env bash
set -euo pipefail

# Use home-directory temp space so Docker bind mounts can access it.
# export SCB_WORKSPACE_DIR="${SCB_WORKSPACE_DIR:-$HOME/.slop-workspaces}"

workflow="matspec" # openspec, artnet, synergyspec, matspec, or matspec-artnet

# Experiment labels only: recorded in experiment.env and output directory names.
# These values do not select dependency versions or Docker image tags.
declare -A workflow_save_names=(
  [openspec]=OpenSpec
  [artnet]=ArtNet
  [synergyspec]=SynergySpec
  [matspec]=MatrixSpec
  [matspec-artnet]=MatrixSpec-ArtNet
)
declare -A experiment_versions=(
  [openspec]="1.7.0"
  [artnet]="1.4.3"
  [synergyspec]="3.0.2"
  [matspec]="0.4.1-beta.3"
  [matspec-artnet]="0.4.1-beta.3-artnet-1.4.4"
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
  matspec)
    agent_config=configs/agents/codex-matrixspec.yaml
    environment_config=docker-python3.12-uv-matrixspec
    ;;
  matspec-artnet)
    agent_config=configs/agents/codex-matrixspec-artnet.yaml
    environment_config=docker-python3.12-uv-matrixspec
    ;;
  *)
    echo "workflow must be openspec, artnet, synergyspec, matspec, or matspec-artnet" >&2
    exit 2
    ;;
esac

experiment_version="${experiment_versions[$workflow]}"
save_dir="outputs/${workflow_save_names[$workflow]}-v${experiment_version}"

# Pass the timestamp through the existing CLI override and Compose environment.
export SCB_WORKFLOW_BUILD_TIMESTAMP
SCB_WORKFLOW_BUILD_TIMESTAMP="$(date -u +%Y%m%dT%H%M%S%6NZ)"
build_dir="$save_dir/.builds/$SCB_WORKFLOW_BUILD_TIMESTAMP"
mkdir -p "$build_dir"
build_dir="$(cd "$build_dir" && pwd)"
build_args=("agent.workflow.version=$SCB_WORKFLOW_BUILD_TIMESTAMP")

case "$workflow" in
  artnet|matspec-artnet)
    # Keep the rendered template for the saved run configuration and resumes.
    sed "s/__SCB_WORKFLOW_BUILD_TIMESTAMP__/$SCB_WORKFLOW_BUILD_TIMESTAMP/g" \
      configs/agents/codex-artnet.j2 > "$build_dir/agent.j2"
    build_args+=("agent.docker_template=$build_dir/agent.j2")
    ;;
esac

printf 'workflow=%q\nexperiment_version=%q\nexport SCB_WORKFLOW_BUILD_TIMESTAMP=%q\nagent_config=%q\n' \
  "$workflow" "$experiment_version" "$SCB_WORKFLOW_BUILD_TIMESTAMP" \
  "$agent_config" > "$build_dir/experiment.env"

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
  "${build_args[@]}" \
  thinking=high \
  pass_policy=any-case \
  save_dir="$save_dir" \
  'save_template=${model.name}_${thinking}_${now:%Y%m%dT%H%M}'
