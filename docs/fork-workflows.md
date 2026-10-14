# Running fork workflow experiments

Run `bash run-benchmark.sh` from the repository root. Set `workflow` near the
top of the script to one of these four conditions:

| Workflow | Agent configuration | Environment | Output directory |
| --- | --- | --- | --- |
| `openspec` | `configs/agents/codex-openspec.yaml` | `docker-python3.12-uv` | `outputs/OpenSpec-v1.7.0/` |
| `artnet` | `configs/agents/codex-artnet.yaml` | `docker-python3.12-uv-artnet` | `outputs/ArtNet-v1.4.3/` |
| `synergyspec` | `configs/agents/codex-synergyspec.yaml` | `docker-python3.12-uv` | `outputs/SynergySpec-v3.0.2/` |
| `matspec` | `configs/agents/codex-matrixspec.yaml` | `docker-python3.12-uv-matrixspec` | `outputs/MatrixSpec-v0.4.1-beta.3/` |

All four use Codex CLI `0.146.1`. The script currently selects
`codex_auth/gpt-5.5`, `thinking=high`, 16 problems, and four workers. Edit the
`problems` array and `--num-workers` to change the batch. Output naming is
controlled by `workflow_save_names` and `workflow_versions`; keep those values
consistent with the selected agent configuration.

## Common prerequisites

Install uv and Docker, and ensure your account can access the Docker daemon.
Use Python 3.12 for this fork's locked dependencies:

```bash
uv python install 3.12
uv sync --locked --python 3.12
docker info
```

The `codex_auth` provider reads `~/.codex/auth.json`. Supply that file on the
benchmark host before running. The separate `OPENROUTER_API_KEY` below is
needed only by ArtNet.

For comparable experiments, use the same problem catalog release:

```bash
uv run slop-code sync v1.0
uv run slop-code problems ls
```

The harness runs Docker containers as the invoking host user's UID/GID. This
keeps bind-mounted workspaces writable when the host user is not UID 1000.
After updating from older images, force a rebuild of each condition you use:

```bash
uv run slop-code docker build-agent \
  configs/agents/codex-openspec.yaml \
  configs/environments/docker-python3.12-uv.yaml \
  --force-build --force-build-base
```

Use the corresponding agent and environment files from the table for the
other conditions. The explicit rebuild applies the updated permissions to
cached agent images. Subsequent runs build missing images automatically.

## OpenSpec and SynergySpec

OpenSpec `1.7.0` and SynergySpec Self-Evolving `3.0.2` install inside their
agent images. They require no additional host Node.js installation or
database. Both execute Propose, Apply, Sync, and Archive for each checkpoint,
with a separate `codex exec` invocation per node.

SynergySpec's configuration deliberately skips its gen-tests, run-tests,
verify, and learn stages. The archive prompt includes the benchmark policy
for those skipped evidence gates.

## ArtNet

Place the ArtNet `1.4.3` source next to this repository:

```text
parent/
├── slop-code-bench/
└── artnet/
```

`configs/agents/codex-artnet.compose.yaml` builds the `runtime-base` target
from ArtNet's Dockerfile as `slop-artnet:1.4.3`. The agent image copies the
resulting CLI and installs OpenSpec `1.7.0`. Node.js package installation and
the ArtNet build run in Docker.

Export the API key in the shell that launches the benchmark. For interactive
Bash sessions, this can be set in `~/.bashrc` and loaded with
`source ~/.bashrc`:

```bash
export OPENROUTER_API_KEY="<your-api-key>"
```

Docker Compose must be available as `docker compose`. The harness starts one
Neo4j service per problem, waits for its health check, and attaches the agent
to that problem's private network. The agent connects to `bolt://neo4j:7687`;
HTTP and Bolt host ports are allocated dynamically on loopback. Workers do
not need a preconfigured Neo4j URI pool.

After each problem, Compose removes its containers and network, retaining
named volumes. The output's `workflow-compose.json` records the project name
for resuming and manual cleanup. If cleanup fails, use the command printed in
the log. ArtNet executes Propose, Apply, and Archive for each checkpoint.

## MatrixSpec

MatrixSpec currently runs from a read-only host checkout mounted at
`/opt/matrixspec`. Before running on another machine, edit the host path in
`configs/environments/docker-python3.12-uv-matrixspec.yaml`:

```yaml
extra_mounts:
  /absolute/path/to/MatrixSpec:
    bind: /opt/matrixspec
    mode: ro
```

Replace the existing `/home/stk/projects/MatrixSpec` key with your checkout's
absolute path, for example `/home/Samuel/projects/MatrixSpec`. Relative mount
paths resolve against the temporary problem workspace, so `../MatrixSpec`
does not refer to the repository's sibling directory.

Use a `0.4.1-beta.3` checkout containing `bin/matspec.js`, its source and
templates, and installed dependencies in `node_modules`. Install those
dependencies in that checkout if needed:

```bash
cd /absolute/path/to/MatrixSpec
npm ci
```

This local-checkout condition needs its dependencies prepared before the
read-only mount is used. Codex and the `matspec` executable run inside Docker.
The agent configuration links `matspec` into the container's npm bin directory
and runs the complete Light lifecycle in one workflow node per checkpoint.

## Running and inspecting results

After setting the condition and completing its prerequisites:

```bash
bash run-benchmark.sh
```

Each run is saved under
`outputs/<Condition>-v<version>/<model>_<thinking>_<timestamp>/`. The
`checkpoint-only` prompt supplies the checkpoint specification to the
workflow. Inspect `run_info.yaml`, `result.json`, and the logs to distinguish
run completion from passing every evaluation case. Interrupting a run can
leave completed checkpoint artifacts alongside an incomplete problem run.

To reevaluate a saved run, use `uv run slop-code eval outputs/<run-dir>`.

## Archiving results

Archive only after the benchmark has stopped writing, and check the saved
`agent.workflow.type` and `agent.workflow.version` in each run's configuration
before grouping results under one condition/version name:

```bash
cd outputs
tar -I 'zstd -T0 -10' -cf ArtNet-v1.4.3.tar.zst ArtNet-v1.4.3
zstd --test ArtNet-v1.4.3.tar.zst
tar --zstd -tf ArtNet-v1.4.3.tar.zst >/dev/null
```

The script already creates versioned output directories. Keep the original
until the archive has been verified and copied to its storage destination.
Restore with `tar --zstd -xf ArtNet-v1.4.3.tar.zst`.
